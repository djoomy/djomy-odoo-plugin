# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""Facade RPC du POS vers le client API Djomy.

Toute la logique d'appel est sur `payment.provider` (module payment_djomy) ;
ici on ne fait que verifier les droits, choisir le fournisseur de la societe,
et renvoyer au JS de la caisse des dicts plats et sans exception : le
caissier doit toujours recevoir ``{'success': False, 'error': ...}`` plutot
qu'une trace.
"""

import json
import re
from datetime import timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, AccessError, ValidationError

from odoo.addons.payment_djomy import const, tools


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    DJOMY_PAYMENT_METHODS = [
        (code, label) for code, label in const.PAYMENT_METHODS
        if code in const.DIRECT_PAYMENT_METHODS
    ]

    def _get_payment_terminal_selection(self):
        return super()._get_payment_terminal_selection() + [('djomy', 'Djomy')]

    djomy_payment_method = fields.Selection(
        selection=DJOMY_PAYMENT_METHODS,
        string='Operateur par defaut',
        default='OM',
        help="Operateur preselectionne en caisse ; le caissier peut en choisir un autre.",
    )
    djomy_methods_json = fields.Char(compute='_compute_djomy_methods_json')

    @api.depends('use_payment_terminal')
    def _compute_djomy_methods_json(self):
        methods = [
            {'code': code, 'label': label, 'otp': code in const.OTP_METHODS,
             'redirect': code in const.REDIRECT_METHODS}
            for code, label in const.PAYMENT_METHODS
            if code in const.DIRECT_PAYMENT_METHODS
        ]
        for method in self:
            method.djomy_methods_json = json.dumps(methods)

    @api.model
    def _load_pos_data_fields(self, config):
        params = super()._load_pos_data_fields(config)
        params += ['djomy_payment_method', 'djomy_methods_json']
        return params

    # --- Helpers ------------------------------------------------------------

    def _get_djomy_payment_provider(self):
        """Get the configured Djomy payment provider for the current company."""
        company = self.company_id or self.env.company
        djomy_payment_provider = self.env['payment.provider'].sudo().search([
            ('code', '=', 'djomy'),
            ('company_id', '=', company.id),
        ], limit=1)

        if not djomy_payment_provider:
            raise UserError(_("Djomy payment provider for company %s is missing", company.name))

        return djomy_payment_provider

    def _djomy_check_access(self):
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_("Do not have access to Djomy payments"))

    @api.model
    def _djomy_status_flags(self, status):
        status = str(status or '').upper()
        return {
            'status': status,
            'isPending': status in const.PAYMENT_STATUS_MAPPING['pending'],
            'isDone': status in const.PAYMENT_STATUS_MAPPING['done'],
            'isFailed': status in const.PAYMENT_STATUS_MAPPING['error'],
            'isCancelled': status in const.PAYMENT_STATUS_MAPPING['cancel'],
            'otpRequired': status in const.OTP_PENDING_STATUSES,
        }

    @api.model
    def _djomy_bill_expiry_minutes(self):
        """Validite du QR imprime sur la note (`djomy.bill_link_expiry_minutes`)."""
        default = const.DEFAULT_BILL_LINK_EXPIRY_MINUTES
        try:
            minutes = int(self.env['ir.config_parameter'].sudo().get_param(
                'djomy.bill_link_expiry_minutes', default,
            ))
        except (TypeError, ValueError):
            minutes = default
        return minutes if minutes > 0 else default

    def _djomy_phone(self, phone_number):
        provider = self._get_djomy_payment_provider()
        return tools.format_phone(phone_number, provider._djomy_get_phone_code())

    def _djomy_payer_identifier(self, method, identifier):
        """Identifiant payeur selon l'operateur : telephone normalise pour le
        mobile money, numero de compte tel que saisi (chiffres) pour PayCard."""
        if method in const.MOBILE_MONEY_METHODS:
            return self._djomy_phone(identifier)
        return re.sub(r'\D', '', str(identifier or '')) or None

    # --- Lien de paiement : QR imprime sur la note --------------------------

    @api.model
    def djomy_create_payment_link(self, payment_method_id, amount, reference, phone_number=None,
                                  purpose='bill'):
        """Cree le lien dont le QR est imprime sur la note.

        Lien a usage MULTIPLE **sans montant impose** : plusieurs convives
        scannent la meme note et paient chacun leur part ; la caisse
        additionne ce qu'elle recoit (:meth:`djomy_check_link_status` avec le
        montant attendu). Le montant du est rappele dans le libelle Djomy, sur
        la page de paiement, et sur la note.

        C'est le seul usage d'un lien de paiement en caisse : l'encaissement au
        comptoir passe par `djomy_create_payment` (operateur + numero).
        """
        self._djomy_check_access()
        payment_method = self.browse(payment_method_id)
        try:
            provider = payment_method._get_djomy_payment_provider()
            phone = payment_method._djomy_phone(phone_number)
            minutes = self._djomy_bill_expiry_minutes()
            expires_at = fields.Datetime.now() + timedelta(minutes=minutes)
            api_amount = tools.api_amount(amount)
            currency = provider.company_id.currency_id.name or ''
            link = provider._djomy_create_link(
                None, reference,
                link_name=f"POS-{reference}",
                description=_(
                    "Note %(ref)s : %(amount)s %(currency)s a regler (plusieurs paiements possibles)",
                    ref=reference, amount=api_amount, currency=currency,
                ),
                usage_type='MULTIPLE',
                usage_limit=const.BILL_LINK_USAGE_LIMIT,
                expires_at=expires_at,
                phone_number=phone,
                send_sms=bool(phone),
                metadata={'odoo_pos_order': reference, 'odoo_purpose': purpose, 'odoo_amount': api_amount},
            )
        except (UserError, ValidationError) as e:
            return {'success': False, 'error': str(e)}

        payment_page_url = link.get('paymentPageUrl')
        return {
            'success': True,
            'amount': api_amount,
            'paymentLink': payment_page_url,
            'qrCodeBase64': provider._djomy_qr_code_base64(payment_page_url) if payment_page_url else None,
            'paymentLinkReference': link.get('paymentLinkReference'),
            'expiresAt': tools.to_api_datetime(expires_at),
            'smsSent': bool(phone),
            'data': link,
        }

    @api.model
    def djomy_check_link_status(self, payment_link_reference, expected_amount=None):
        """Statut d'un lien et des paiements qu'il a recus.

        Sans ``expected_amount`` : un lien a payeur unique, abouti des qu'un
        paiement est ``SUCCESS``. Avec ``expected_amount`` (note partagee) :
        abouti quand la **somme** des paiements ``SUCCESS`` couvre le montant ;
        ``isPartial`` tant qu'il en manque ; un echec d'un convive
        (``FAILED``) n'arrete pas l'attente, les autres peuvent encore payer.
        ``receivedPayments`` detaille chaque paiement abouti, pour une ligne
        de caisse par paiement.
        """
        self._djomy_check_access()
        try:
            provider = self._get_djomy_payment_provider()
            link = provider._djomy_get_link(payment_link_reference)
        except (UserError, ValidationError) as e:
            return {'success': False, 'error': str(e)}

        link_status = str(link.get('status') or '').upper()
        payments = provider._djomy_link_payments(link)
        received = []
        for p in payments:
            transaction_id = p.get('transactionId') or p.get('id')
            if not transaction_id or str(p.get('status') or '').upper() not in const.PAYMENT_STATUS_MAPPING['done']:
                continue
            received.append({
                'transactionId': str(transaction_id),
                'paidAmount': float(p.get('paidAmount', p.get('amount')) or 0),
                'paymentMethod': str(p.get('paymentMethod') or '').upper(),
                'payerIdentifier': p.get('payerIdentifier') or '',
                'createdAt': p.get('createdAt'),
            })
        received.sort(key=lambda r: str(r['createdAt'] or ''))
        paid_total = sum(r['paidAmount'] for r in received)
        payment = provider._djomy_pick_link_payment(link) or {}
        payment_status = str(payment.get('status') or '').upper()
        shared = expected_amount is not None
        if shared:
            expected = float(expected_amount or 0)
            is_done = bool(received) and paid_total + 0.005 >= expected
            is_failed = False
        else:
            is_done = payment_status in const.PAYMENT_STATUS_MAPPING['done']
            is_failed = bool(payment) and payment_status in const.PAYMENT_STATUS_MAPPING['error']
        first = received[0] if received else {}
        return {
            'success': True,
            'linkStatus': link_status,
            'isPending': link_status in const.LINK_ACTIVE_STATUSES and not is_done,
            'isDone': is_done,
            'isPartial': shared and bool(received) and not is_done,
            'isFailed': is_failed,
            'isCancelled': link_status in (const.LINK_STATUS_REVOKED, 'DISABLED') and not is_done,
            'isExpired': link_status == const.LINK_STATUS_EXPIRED and not is_done,
            'inferredFromUsage': bool(payment.get('inferredFromUsage')),
            'transactionId': (first.get('transactionId') or payment.get('transactionId')) if is_done else None,
            'paidAmount': (first.get('paidAmount') or payment.get('paidAmount')) if is_done else None,
            'paymentMethod': (first.get('paymentMethod') or payment.get('paymentMethod')) if is_done else None,
            'paidTotal': paid_total,
            'expectedAmount': float(expected_amount) if shared else None,
            'receivedPayments': received,
            'payments': payments,
            'data': link,
        }

    # --- Encaissement direct ------------------------------------------------

    @api.model
    def djomy_create_payment(self, payment_method_id, amount, phone_number, reference, djomy_method=None):
        """Initie un encaissement direct (`POST /payments`).

        OM/MOMO : le client confirme sur son telephone, on renvoie
        ``transactionId`` a surveiller. PayCard : ``otpRequired`` invite le
        caissier a saisir l'OTP recu par le client. Si Djomy renvoyait une
        ``redirectUrl``, elle est transmise (affichee en QR par la caisse).
        """
        self._djomy_check_access()
        payment_method = self.browse(payment_method_id)
        try:
            provider = payment_method._get_djomy_payment_provider()
            method = (djomy_method or payment_method.djomy_payment_method or 'OM').upper()
            if method not in const.DIRECT_PAYMENT_METHODS:
                raise UserError(_(
                    "%s n'est pas encore disponible en encaissement direct.",
                    const.PAYMENT_METHOD_LABELS.get(method, method),
                ))
            payer = payment_method._djomy_payer_identifier(method, phone_number)
            if not payer:
                raise UserError(_("Le numero du client est obligatoire pour un encaissement direct."))
            response = provider._djomy_create_direct_payment(
                method, payer, amount, reference,
                description=_("Paiement caisse %s", reference),
                metadata={'odoo_pos_order': reference, 'odoo_method': method},
            )
        except (UserError, ValidationError) as e:
            return {'success': False, 'error': str(e)}

        status = str(response.get('status') or 'PENDING').upper()
        flags = self._djomy_status_flags(status)
        redirect_url = response.get('redirectUrl') or response.get('link') or response.get('paymentPageUrl')
        # `flags` en premier : `otpRequired` doit garder la valeur calculee ici
        # (PayCard exige un OTP meme si le statut initial ne le dit pas).
        return {
            **flags,
            'success': True,
            'transactionId': response.get('transactionId'),
            'paymentMethod': method,
            'redirectUrl': redirect_url,
            'qrCodeBase64': provider._djomy_qr_code_base64(redirect_url) if redirect_url else None,
            'otpRequired': flags['otpRequired'] or bool(response.get('otpRequired')) or method in const.OTP_METHODS,
            'data': response,
        }

    @api.model
    def djomy_confirm_otp(self, transaction_id, one_time_pin):
        """Transmet l'OTP saisi par le client (PayCard)."""
        self._djomy_check_access()
        try:
            provider = self._get_djomy_payment_provider()
            response = provider._djomy_confirm_otp(transaction_id, one_time_pin)
        except (UserError, ValidationError) as e:
            return {'success': False, 'error': str(e)}
        status = response.get('status') if isinstance(response, dict) else None
        return {'success': True, 'data': response, **self._djomy_status_flags(status or 'PENDING')}

    @api.model
    def djomy_check_payment_status(self, transaction_id):
        """Statut officiel d'un paiement."""
        self._djomy_check_access()
        try:
            provider = self._get_djomy_payment_provider()
            response = provider._djomy_get_payment_status(transaction_id)
        except (UserError, ValidationError) as e:
            return {'success': False, 'error': str(e)}
        flags = self._djomy_status_flags(response.get('status'))
        return {
            'success': True,
            'transactionId': response.get('transactionId') or transaction_id,
            'paidAmount': response.get('paidAmount'),
            'paymentMethod': response.get('paymentMethod'),
            'data': response,
            **flags,
        }

    # --- Rattachement d'un paiement deja recu --------------------------------

    @api.model
    def djomy_list_recent_payments(self, payment_method_id, hours=24, config_id=None):
        """Paiements recus par le marchand sur les dernieres heures.

        `GET /payments` liste tout ce que Djomy a encaisse pour le compte,
        quel que soit le lien d'origine (QR de la boutique, QR global, QR de
        la note paye apres coup, lien cree a la main) : la caisse y retrouve
        le paiement d'un client qui a « deja paye ». Chaque ligne dit d'ou
        vient le paiement (``source`` : ``shop`` = QR de cette caisse,
        ``global`` = QR du fournisseur, vide sinon) ; ceux de la boutique
        passent en premier, puis les plus recents.
        """
        self._djomy_check_access()
        payment_method = self.browse(payment_method_id)
        cutoff = fields.Datetime.now() - timedelta(hours=max(1, int(hours or 24)))
        try:
            provider = payment_method._get_djomy_payment_provider()
            payments = provider._djomy_iter_payments(cutoff, fields.Datetime.now(), max_pages=3)
        except (UserError, ValidationError) as e:
            return {'success': False, 'error': str(e)}

        config = self.env['pos.config'].browse(config_id).exists() if config_id else self.env['pos.config']
        shop_ref = config.djomy_static_link_reference or None
        global_ref = provider.djomy_static_link_reference or None
        Wizard = self.env['djomy.payment.match.wizard']
        rows = []
        for payment in payments:
            transaction_id = payment.get('transactionId') or payment.get('id')
            if not transaction_id:
                continue
            paid_at = tools.from_api_datetime(payment.get('createdAt') or payment.get('paidAt'))
            if paid_at and paid_at < cutoff:
                continue
            status = str(payment.get('status') or '').upper()
            method = str(payment.get('paymentMethod') or '').upper()
            link_ref = payment.get('paymentLinkReference') or ''
            source = 'shop' if shop_ref and link_ref == shop_ref else 'global' if global_ref and link_ref == global_ref else ''
            rows.append({
                'source': source,
                'transactionId': str(transaction_id),
                'status': status,
                'isDone': status in const.PAYMENT_STATUS_MAPPING['done'],
                'paidAmount': float(payment.get('paidAmount', payment.get('amount')) or 0),
                'currency': payment.get('currency') or '',
                'paymentMethod': method,
                'paymentMethodLabel': const.PAYMENT_METHOD_LABELS.get(method, method),
                'payerIdentifier': payment.get('payerIdentifier') or '',
                'paidAt': fields.Datetime.to_string(paid_at) if paid_at else '',
                'linkReference': payment.get('paymentLinkReference') or '',
                'merchantReference': payment.get('merchantPaymentReference') or '',
                'matchedOn': Wizard._djomy_find_existing_match(str(transaction_id)) or None,
            })
        # boutique d'abord ; a source egale, les plus recents en tete
        rows.sort(key=lambda r: r['paidAt'], reverse=True)
        rows.sort(key=lambda r: r['source'] != 'shop')
        return {'success': True, 'shopLinkReference': shop_ref, 'globalLinkReference': global_ref, 'payments': rows}

    @api.model
    def djomy_verify_payment(self, transaction_id):
        """Re-verifie un paiement avant rattachement en caisse."""
        self._djomy_check_access()
        try:
            provider = self._get_djomy_payment_provider()
            response = provider._djomy_get_payment_status(transaction_id)
        except (UserError, ValidationError) as e:
            return {'success': False, 'error': str(e)}
        flags = self._djomy_status_flags(response.get('status'))
        matched_on = self.env['djomy.payment.match.wizard']._djomy_find_existing_match(str(transaction_id))
        return {
            'success': True,
            'transactionId': response.get('transactionId') or transaction_id,
            'paidAmount': float(response.get('paidAmount', response.get('amount')) or 0),
            'currency': response.get('currency') or '',
            'paymentMethod': str(response.get('paymentMethod') or '').upper(),
            'payerIdentifier': response.get('payerIdentifier') or '',
            'matchedOn': matched_on or None,
            'data': response,
            **flags,
        }

    def action_djomy_config(self):
        """Open the Djomy payment provider configuration."""
        res_id = self._get_djomy_payment_provider().id
        return {
            'name': _('Djomy Configuration'),
            'res_model': 'payment.provider',
            'type': 'ir.actions.act_window',
            'view_mode': 'form',
            'res_id': res_id,
        }
