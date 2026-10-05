# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import hashlib
import hmac
import io
import re
from datetime import timedelta

import requests

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError
from odoo.tools.urls import urljoin as url_join

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_djomy import const, tools


_logger = get_payment_logger(__name__)

try:
    import qrcode
    QRCODE_AVAILABLE = True
except ImportError:  # pragma: no cover - l'image de base fournit qrcode
    QRCODE_AVAILABLE = False


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('djomy', "Djomy")], ondelete={'djomy': 'set default'}
    )
    djomy_client_id = fields.Char(
        string="Djomy Client ID",
        help="The client ID provided by Djomy.",
        required_if_provider='djomy',
        copy=False,
    )
    djomy_client_secret = fields.Char(
        string="Djomy Client Secret",
        required_if_provider='djomy',
        copy=False,
        groups='base.group_system',
    )
    djomy_access_token = fields.Char(
        string="Djomy Access Token",
        copy=False,
        groups='base.group_system',
    )
    djomy_access_token_expiry = fields.Datetime(
        string="Djomy Access Token Expiry",
        copy=False,
        groups='base.group_system',
        help="Expiration du jeton en cache. Le jeton est renouvele avant cette date.",
    )
    djomy_partner_domain = fields.Char(
        string="Partner Domain",
        help="Your domain registered and validated by Djomy. Optional in Test mode, required in Production.",
        copy=False,
    )
    djomy_link_expiry_minutes = fields.Integer(
        string="Validite des liens (minutes)",
        default=const.DEFAULT_LINK_EXPIRY_MINUTES,
        help="Duree de validite d'un lien de paiement genere depuis le portail ou "
             "la boutique en ligne. Un lien non paye expire de lui-meme chez Djomy.",
    )
    djomy_allowed_methods = fields.Char(
        string="Moyens de paiement proposes",
        help="Codes Djomy separes par des virgules (OM, MOMO, SOUTRA_MONEY, PAYCARD, "
             "CARD). Vide : Djomy affiche tous les moyens disponibles.",
    )
    djomy_static_link_reference = fields.Char(
        string="Lien statique (reference)",
        copy=False,
        help="Reference du lien de paiement a usage multiple affiche en QR code dans "
             "le commerce. Les paiements recus par ce lien ne sont lies a aucune "
             "commande : ils apparaissent dans la liste des paiements Djomy, avec "
             "ceux des autres liens, et se rattachent depuis la commande ou la facture.",
    )
    djomy_static_link_url = fields.Char(
        string="Lien statique (URL)", copy=False, readonly=True,
    )
    djomy_static_link_qr = fields.Binary(
        string="QR code du lien statique",
        compute='_compute_djomy_static_link_qr',
    )

    # === COMPUTE METHODS === #

    @api.constrains('state', 'djomy_partner_domain')
    def _check_djomy_partner_domain(self):
        for provider in self.filtered(lambda p: p.code == 'djomy'):
            if provider.state == 'enabled' and not provider.djomy_partner_domain:
                raise ValidationError(_(
                    "Djomy: Partner Domain is required in Production mode. "
                    "Please configure your domain registered with Djomy."
                ))

    @api.constrains('djomy_allowed_methods')
    def _check_djomy_allowed_methods(self):
        for provider in self.filtered(lambda p: p.code == 'djomy'):
            unknown = [
                code for code in provider._djomy_get_allowed_methods()
                if code not in const.PAYMENT_METHOD_LABELS
            ]
            if unknown:
                raise ValidationError(_(
                    "Djomy : moyen(s) de paiement inconnu(s) : %s. Codes acceptes : %s.",
                    ', '.join(unknown), ', '.join(const.PAYMENT_METHOD_LABELS),
                ))

    @api.constrains('djomy_link_expiry_minutes')
    def _check_djomy_link_expiry_minutes(self):
        for provider in self.filtered(lambda p: p.code == 'djomy'):
            if provider.djomy_link_expiry_minutes <= 0:
                raise ValidationError(_(
                    "Djomy : la validite des liens doit etre d'au moins une minute."
                ))

    @api.depends('djomy_static_link_url')
    def _compute_djomy_static_link_qr(self):
        for provider in self:
            png = provider._djomy_qr_code_png(provider.djomy_static_link_url) \
                if provider.djomy_static_link_url else None
            provider.djomy_static_link_qr = base64.b64encode(png) if png else False

    def _compute_feature_support_fields(self):
        """Override of `payment` to enable additional features."""
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'djomy').update({
            'support_tokenization': False,
            'support_express_checkout': False,
            'support_refund': False,
        })

    def _get_supported_currencies(self):
        """Override of `payment` to return the supported currencies."""
        supported_currencies = super()._get_supported_currencies()
        if self.code == 'djomy':
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name in const.SUPPORTED_CURRENCIES
            )
        return supported_currencies

    # === CRUD METHODS === #

    def _get_default_payment_method_codes(self):
        """Override of `payment` to return the default payment method codes."""
        self.ensure_one()
        if self.code != 'djomy':
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    # === HELPERS DE CONFIGURATION === #

    def _djomy_get_allowed_methods(self):
        """Liste des codes de `djomy_allowed_methods`, nettoyee ; vide = tous."""
        self.ensure_one()
        raw = self.djomy_allowed_methods or ''
        return [code.strip().upper() for code in raw.split(',') if code.strip()]

    def _djomy_get_country_code(self, partner=None):
        """Code pays ISO a envoyer a Djomy : celui du partenaire, sinon de la societe."""
        self.ensure_one()
        country = (partner and partner.country_id) or self.company_id.country_id
        return (country and country.code) or const.DEFAULT_COUNTRY_CODE

    def _djomy_get_phone_code(self, partner=None):
        self.ensure_one()
        country = (partner and partner.country_id) or self.company_id.country_id
        return (country and country.phone_code) or const.DEFAULT_PHONE_CODE

    def _djomy_get_api_url(self):
        """Return the API URL based on the provider state."""
        self.ensure_one()
        if self.state == 'enabled':
            return const.API_URLS['production']
        return const.API_URLS['test']

    # === AUTHENTIFICATION === #

    def _djomy_generate_signature(self):
        """Generate the HMAC-SHA256 signature for X-API-KEY header."""
        self.ensure_one()
        signature = hmac.new(
            self.djomy_client_secret.encode('utf-8'),
            self.djomy_client_id.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return f"{self.djomy_client_id}:{signature}"

    def _djomy_token_is_valid(self):
        """Le jeton en cache est-il encore utilisable (avec une marge de securite) ?

        Un jeton sans date d'expiration connue (installations anterieures a
        l'ajout du champ) est considere valide : le retry sur 401 rattrape
        le cas ou il serait perime.
        """
        self.ensure_one()
        if not self.djomy_access_token:
            return False
        if not self.djomy_access_token_expiry:
            return True
        margin = timedelta(seconds=const.TOKEN_REFRESH_MARGIN_SECONDS)
        return fields.Datetime.now() + margin < self.djomy_access_token_expiry

    def _djomy_fetch_access_token(self):
        """Fetch a new access token from Djomy API."""
        self.ensure_one()
        # Clear existing token before fetching new one
        self.djomy_access_token = False
        response = self._send_api_request(
            'POST', 'auth', json={}, skip_auth=True
        )
        # _parse_response_content already extracts 'data' from the response
        token = response.get('accessToken')
        try:
            ttl = int(response.get('expiresIn') or const.DEFAULT_TOKEN_TTL_SECONDS)
        except (TypeError, ValueError):
            ttl = const.DEFAULT_TOKEN_TTL_SECONDS
        self.write({
            'djomy_access_token': token,
            'djomy_access_token_expiry': fields.Datetime.now() + timedelta(seconds=ttl),
        })
        return token

    def _djomy_send_request_with_retry(self, method, endpoint, **kwargs):
        """Send API request with automatic token refresh on auth failure.

        If the request fails due to an expired/invalid token (401 or token error),
        the token is refreshed and the request is retried once.
        """
        self.ensure_one()
        try:
            return self._send_api_request(method, endpoint, **kwargs)
        except Exception as e:
            error_msg = str(e).lower()
            # Check if it's an authentication error
            if '401' in error_msg or 'token' in error_msg or 'unauthorized' in error_msg:
                _logger.info("Djomy: Token expired or invalid, refreshing...")
                self._djomy_fetch_access_token()
                # Retry the request with new token
                return self._send_api_request(method, endpoint, **kwargs)
            raise

    # === CLIENT API DJOMY === #
    #
    # Toutes les operations metier passent par ces methodes : ni le module
    # POS ni les controleurs ne construisent de payload eux-memes. Chaque
    # methode renvoie le `data` brut de la reponse Djomy (deja extrait par
    # `_parse_response_content`) et laisse remonter la ValidationError du
    # framework en cas d'echec HTTP.

    def _djomy_create_link(
        self, amount, merchant_reference, *, country_code=None, link_name=None,
        description=None, usage_type='UNIQUE', expires_at=None, phone_number=None,
        send_sms=False, return_url=None, cancel_url=None, allowed_methods=None,
        metadata=None, skip_status_page=None, usage_limit=None,
    ):
        """`POST /links` : cree un lien de paiement et renvoie sa description.

        :param amount: montant attendu ; ``None``/0 laisse le payeur saisir
            (lien statique a usage multiple)
        :param merchant_reference: reference marchand propagee sur chaque
            paiement (``merchantPaymentReference`` du webhook)
        :return: dict avec au moins ``paymentPageUrl`` et ``paymentLinkReference``
        """
        self.ensure_one()
        payload = {
            'countryCode': country_code or self._djomy_get_country_code(),
            'usageType': usage_type,
            'merchantReference': merchant_reference,
            'linkName': link_name or merchant_reference,
        }
        if amount:
            payload['amountToPay'] = tools.api_amount(amount)
        if description:
            payload['description'] = description[:255]
        if expires_at:
            payload['expiresAt'] = tools.to_api_datetime(expires_at)
        if phone_number:
            payload['phoneNumber'] = phone_number
            payload['sendSms'] = bool(send_sms)
        if return_url:
            payload['returnUrl'] = return_url
        if cancel_url:
            payload['cancelUrl'] = cancel_url
        methods = allowed_methods if allowed_methods is not None else self._djomy_get_allowed_methods()
        if methods:
            payload['allowedPaymentMethods'] = methods
        if metadata:
            payload['metadata'] = metadata
        if skip_status_page is not None:
            payload['skipDjomyStatusPage'] = bool(skip_status_page)
        if usage_limit:
            payload['usageLimit'] = int(usage_limit)
        return self._djomy_send_request_with_retry('POST', 'links', json=payload)

    def _djomy_get_link(self, reference):
        """`GET /links/{reference}` : statut du lien et paiements recus."""
        self.ensure_one()
        return self._djomy_send_request_with_retry('GET', f'links/{reference}')

    @staticmethod
    def _djomy_pagination_params(page=0, size=100, sort_by='createdAt', sort_direction='desc'):
        """Parametres de pagination de `GET /payments`.

        La doc Djomy declare un objet ``paginationRequest`` en query string
        sans en montrer la serialisation. L'API est du Spring (page
        ``content``/``totalPages``/``last``) : on envoie ``page``/``size``/
        ``sortBy``/``sortDirection`` a plat, la convention Spring. A ajuster
        ici, et seulement ici, si l'API attend un prefixe
        (``paginationRequest.page``).
        """
        return {
            'page': int(page),
            'size': int(size),
            'sortBy': sort_by,
            'sortDirection': sort_direction,
        }

    @staticmethod
    def _djomy_payments_period(start_date, end_date):
        """Bornes au jour de `GET /payments` : les deux dates, ou aucune.

        L'API refuse une periode a moitie renseignee. Une seule date fournie :
        on complete l'autre (debut -> aujourd'hui, fin -> premier jour de son
        mois, le defaut de Djomy quand rien n'est fourni).
        """
        if not start_date and not end_date:
            return None, None
        if start_date and not end_date:
            end_date = fields.Datetime.now()
        if end_date and not start_date:
            start_date = end_date.replace(day=1)
        return tools.to_api_date(start_date), tools.to_api_date(end_date)

    def _djomy_list_payments(self, page=0, size=100, start_date=None, end_date=None, statuses=None):
        """`GET /payments` : une page des paiements du marchand sur une periode.

        Tous les paiements, quelle que soit leur origine (lien Odoo, QR
        statique, lien cree a la main chez Djomy) : c'est la source du
        rattachement manuel. Sans ``statuses``, Djomy renvoie les paiements
        ``SUCCESS`` et ``PENDING`` ; sans dates, le mois courant.

        :return: dict brut Djomy (page Spring) ; les paiements sont sous
            ``content`` : utiliser :meth:`_djomy_page_items`.
        """
        self.ensure_one()
        params = self._djomy_pagination_params(page, size)
        start, end = self._djomy_payments_period(start_date, end_date)
        if start:
            params['startDate'] = start
            params['endDate'] = end
        if statuses:
            params['statuses'] = ','.join(statuses) if not isinstance(statuses, str) else statuses
        return self._djomy_send_request_with_retry('GET', 'payments', params=params)

    def _djomy_iter_payments(self, start_date=None, end_date=None, *, max_pages=5, size=100, statuses=None):
        """Enchaine les pages de `GET /payments` et renvoie les paiements, les plus recents d'abord.

        S'arrete a la derniere page (``last``), sur une page vide ou
        incomplete, et au plus apres ``max_pages`` pages : un rattachement
        manuel n'a pas besoin de tout l'historique.
        """
        self.ensure_one()
        payments = []
        for page in range(max(1, int(max_pages or 1))):
            page_data = self._djomy_list_payments(
                page=page, size=size, start_date=start_date, end_date=end_date, statuses=statuses,
            )
            items = [p for p in self._djomy_page_items(page_data) if isinstance(p, dict)]
            payments.extend(items)
            if not items or len(items) < size:
                break
            if isinstance(page_data, dict) and page_data.get('last') is True:
                break
        return payments

    @staticmethod
    def _djomy_page_items(page_data):
        """Extrait les elements d'une liste Djomy quel que soit le nom de la cle."""
        if isinstance(page_data, list):
            return page_data
        if not isinstance(page_data, dict):
            return []
        for key in ('content', 'items', 'links', 'payments', 'data'):
            value = page_data.get(key)
            if isinstance(value, list):
                return value
        return []

    @staticmethod
    def _djomy_link_payments(link_data):
        """Paiements d'un lien (`GET /links/{ref}`), liste vide si absents."""
        if not isinstance(link_data, dict):
            return []
        payments = link_data.get('payments')
        if isinstance(payments, dict):
            payments = PaymentProvider._djomy_page_items(payments)
        return [p for p in (payments or []) if isinstance(p, dict)]

    @staticmethod
    def _djomy_pick_link_payment(link_data):
        """Le paiement a retenir pour un lien a usage unique.

        Un lien UNIQUE ne porte qu'un paiement abouti, mais peut cumuler des
        tentatives echouees avant : on prefere le paiement reussi, sinon le
        plus recent (pour refleter un echec/annulation en cours).
        """
        payments = PaymentProvider._djomy_link_payments(link_data)
        if not payments:
            # La sandbox ne detaille pas les paiements d'un lien : seul
            # `numberOfUsage` bouge. Pour un lien a usage unique, un usage,
            # c'est le paiement attendu - sans transactionId ni montant.
            if not isinstance(link_data, dict):
                return None
            try:
                usage = int(link_data.get('numberOfUsage') or 0)
            except (TypeError, ValueError):
                usage = 0
            if usage >= 1 and str(link_data.get('usageType') or '').upper() == 'UNIQUE':
                return {
                    'status': 'SUCCESS',
                    'paymentLinkReference': link_data.get('paymentLinkReference'),
                    'inferredFromUsage': True,
                }
            return None
        for payment in payments:
            if str(payment.get('status', '')).upper() in const.PAYMENT_STATUS_MAPPING['done']:
                return payment
        return sorted(
            payments, key=lambda p: str(p.get('createdAt') or ''), reverse=True,
        )[0]

    def _djomy_create_direct_payment(
        self, method, payer_identifier, amount, merchant_reference, *,
        country_code=None, description=None, return_url=None, cancel_url=None,
        metadata=None,
    ):
        """`POST /payments` : initie un encaissement sans redirection.

        OM/MOMO : le payeur confirme sur son telephone. KULU : la reponse
        contient une URL a ouvrir. PAYCARD : un OTP est ensuite attendu
        (:meth:`_djomy_confirm_otp`). CARD est refuse par Djomy sur cet
        endpoint.
        """
        self.ensure_one()
        method = (method or '').upper()
        if method not in const.PAYMENT_METHOD_LABELS:
            raise UserError(_("Djomy : moyen de paiement inconnu (%s).", method))
        if not payer_identifier:
            raise UserError(_("Djomy : le numero du payeur est obligatoire."))
        payload = {
            'paymentMethod': method,
            'payerIdentifier': payer_identifier,
            'amount': tools.api_amount(amount),
            'countryCode': country_code or self._djomy_get_country_code(),
            'merchantPaymentReference': merchant_reference,
        }
        if description:
            payload['description'] = description[:255]
        if return_url:
            payload['returnUrl'] = return_url
        if cancel_url:
            payload['cancelUrl'] = cancel_url
        if metadata:
            payload['metadata'] = metadata
        return self._djomy_send_request_with_retry('POST', 'payments', json=payload)

    def _djomy_confirm_otp(self, transaction_reference, one_time_pin):
        """`POST /payments/{ref}/confirmOTP` : transmet l'OTP saisi par le payeur."""
        self.ensure_one()
        pin = re.sub(r'\D', '', str(one_time_pin or ''))
        if not re.fullmatch(r'\d{4,6}', pin):
            raise UserError(_("Djomy : le code OTP doit comporter 4 a 6 chiffres."))
        return self._djomy_send_request_with_retry(
            'POST', f'payments/{transaction_reference}/confirmOTP',
            json={'oneTimePin': pin},
        )

    def _djomy_get_payment_status(self, transaction_id):
        """`GET /payments/{id}/status` : statut officiel d'un paiement."""
        self.ensure_one()
        return self._djomy_send_request_with_retry(
            'GET', f'payments/{transaction_id}/status'
        )

    # === QR CODES === #

    @api.model
    def _djomy_qr_code_png(self, data):
        """PNG (bytes) du QR encodant ``data``, ``None`` si qrcode est absent."""
        if not QRCODE_AVAILABLE or not data:
            return None
        qr = qrcode.QRCode(
            version=1,
            error_correction=qrcode.constants.ERROR_CORRECT_M,
            box_size=10,
            border=4,
        )
        qr.add_data(data)
        qr.make(fit=True)
        img = qr.make_image(fill_color="black", back_color="white")
        buffer = io.BytesIO()
        img.save(buffer, format='PNG')
        return buffer.getvalue()

    @api.model
    def _djomy_qr_code_base64(self, data):
        """Data-URI PNG du QR, directement affichable dans un ``<img>``."""
        png = self._djomy_qr_code_png(data)
        if not png:
            return None
        return "data:image/png;base64," + base64.b64encode(png).decode('utf-8')

    # === LIEN STATIQUE === #

    def _djomy_create_static_link(self, reference, *, link_name, description, metadata=None):
        """Cree un lien a usage multiple, sans montant ni expiration, avec un
        `usageLimit` tres grand (sans lui Djomy ferme le lien au premier
        paiement). Partage par le QR global (fournisseur) et les QR par
        boutique (`pos.config`)."""
        self.ensure_one()
        try:
            return self._djomy_create_link(
                None, reference,
                link_name=link_name,
                description=description,
                usage_type='MULTIPLE',
                usage_limit=const.STATIC_LINK_USAGE_LIMIT,
                metadata=metadata,
            )
        except ValidationError as error:
            raise UserError(_("Djomy n'a pas pu creer le lien statique : %s", error)) from error

    @api.model
    def _djomy_provider_for(self, company):
        """Le fournisseur Djomy actif d'une societe (sudo), ou un recordset vide."""
        return self.sudo().search([
            ('code', '=', 'djomy'), ('company_id', '=', company.id), ('state', '!=', 'disabled'),
        ], limit=1)

    def action_djomy_generate_static_link(self):
        """Cree (ou remplace) le lien a usage multiple affiche en QR dans le commerce.

        Sans montant ni expiration : le payeur saisit ce qu'il doit, le lien
        vit tant qu'on ne le remplace pas. Avec un `usageLimit` tres grand :
        sans lui, Djomy ferme le lien au premier paiement (constate en
        production). Les paiements recus se rattachent
        ensuite aux commandes/factures via le wizard de rapprochement.
        """
        self.ensure_one()
        if self.code != 'djomy':
            return False
        reference = f"STATIC-{self.company_id.id}-{fields.Datetime.now():%Y%m%d%H%M%S}"
        link = self._djomy_create_static_link(
            reference,
            link_name=_("QR %s", self.company_id.name),
            description=_("Paiement libre - %s", self.company_id.name),
            metadata={'odoo_static_link': True, 'odoo_company': self.company_id.id},
        )
        self.write({
            'djomy_static_link_reference': link.get('paymentLinkReference'),
            'djomy_static_link_url': link.get('paymentPageUrl'),
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _("Lien statique Djomy cree : %s", self.djomy_static_link_reference),
                'next': {'type': 'ir.actions.act_window_close'},
            },
        }

    # === REQUEST HELPERS === #

    def _build_request_url(self, endpoint, **kwargs):
        """Override of `payment` to build the request URL."""
        if self.code != 'djomy':
            return super()._build_request_url(endpoint, **kwargs)
        return url_join(self._djomy_get_api_url(), endpoint)

    def _build_request_headers(self, *args, skip_auth=False, **kwargs):
        """Override of `payment` to build the request headers."""
        if self.code != 'djomy':
            return super()._build_request_headers(*args, **kwargs)

        headers = {
            'Content-Type': 'application/json',
            'X-API-KEY': self._djomy_generate_signature(),
        }
        if self.djomy_partner_domain:
            headers['X-PARTNER-DOMAIN'] = self.djomy_partner_domain
        if not skip_auth:
            if not self._djomy_token_is_valid():
                self._djomy_fetch_access_token()
            headers['Authorization'] = f'Bearer {self.djomy_access_token}'
        return headers

    def _parse_response_error(self, response):
        """Override of `payment` to parse the error message."""
        if self.code != 'djomy':
            return super()._parse_response_error(response)
        try:
            body = response.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            return response.text or _("Djomy API error (HTTP %s)", response.status_code)
        # Enveloppe Djomy : `message` au niveau racine, detail dans `error`
        error = body.get('error') if isinstance(body, dict) else None
        detail = ''
        if isinstance(error, dict):
            detail = error.get('details') or error.get('message') or ''
        message = (body.get('message') if isinstance(body, dict) else '') or ''
        if detail and detail != message:
            message = f"{message} - {detail}" if message else detail
        return message or _("Djomy API error (HTTP %s)", response.status_code)

    def _parse_response_content(self, response, **kwargs):
        """Override of `payment` to parse the response content."""
        if self.code != 'djomy':
            return super()._parse_response_content(response, **kwargs)
        try:
            json_response = response.json()
        except (ValueError, requests.exceptions.JSONDecodeError):
            raise ValidationError(_("Djomy: Invalid API response (HTTP %s)", response.status_code))
        if isinstance(json_response, dict) and json_response.get('success'):
            data = json_response.get('data', json_response)
            return data if data is not None else {}
        return json_response
