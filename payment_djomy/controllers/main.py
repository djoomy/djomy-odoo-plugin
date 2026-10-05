# Part of Odoo. See LICENSE file for full copyright and licensing details.

import hmac
import hashlib
import json
import pprint

from werkzeug.exceptions import Forbidden

from odoo import http
from odoo.exceptions import ValidationError
from odoo.http import request

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_djomy import const


_logger = get_payment_logger(__name__)


class DjomyController(http.Controller):
    _return_url = '/payment/djomy/return'
    _cancel_url = '/payment/djomy/cancel'
    _webhook_url = '/payment/djomy/webhook'
    _process_url = '/payment/djomy/process'

    @http.route(_process_url, type='jsonrpc', auth='public')
    def djomy_process_payment(self, reference, phone=None):
        """Cree le lien de paiement Djomy de la transaction et renvoie son URL.

        :param str reference: reference de la transaction Odoo
        :param str phone: numero du payeur (optionnel : s'il est la, Djomy
            envoie aussi le lien par SMS)
        :return: ``{'redirect_url': ...}`` ou ``{'error': ...}``
        """
        _logger.info("Processing Djomy payment for reference %s with phone %s", reference, phone)

        tx_sudo = request.env['payment.transaction'].sudo().search([
            ('reference', '=', reference),
            ('provider_code', '=', 'djomy'),
        ], limit=1)

        if not tx_sudo:
            _logger.warning("Djomy: Transaction not found for reference %s", reference)
            return {'error': 'Transaction non trouvée'}
        if tx_sudo.state not in ('draft', 'pending'):
            return {'error': 'Cette transaction n\'est plus modifiable'}

        if phone:
            tx_sudo.partner_phone = phone

        redirect_url = tx_sudo._djomy_create_payment_link()

        if not redirect_url:
            _logger.error("Djomy: Failed to create payment link for reference %s", reference)
            return {'error': 'Erreur lors de la création du lien de paiement'}

        return {'redirect_url': redirect_url}

    # --- Retour du payeur ---------------------------------------------------

    @staticmethod
    def _find_tx(reference=None, transaction_id=None):
        """Retrouve la transaction du retour : par reference, sinon par transactionId.

        Plus aucun fallback sur "la derniere transaction en attente" : avec
        deux clients simultanes, l'un validait la transaction de l'autre.
        """
        PaymentTransaction = request.env['payment.transaction'].sudo()
        tx = PaymentTransaction
        if reference:
            tx = PaymentTransaction.search([
                ('provider_code', '=', 'djomy'), ('reference', '=', reference),
            ], limit=1)
        if not tx and transaction_id:
            tx = PaymentTransaction.search([
                ('provider_code', '=', 'djomy'), ('provider_reference', '=', transaction_id),
            ], limit=1)
        return tx

    @http.route([_return_url, f'{_return_url}/<path:reference>'],
                type='http', methods=['GET'], auth='public')
    def djomy_return_from_checkout(self, reference=None, **data):
        """Retour du payeur depuis la page Djomy.

        Djomy ajoute ``?transactionId=<uuid>&status=SUCCESS|...`` a l'URL de
        retour. Le statut de l'URL n'est jamais applique tel quel : on
        interroge Djomy (par transactionId, sinon par lien) et on applique
        ce que l'API repond. L'URL ne sert qu'a retrouver la transaction.
        """
        _logger.info("Handling redirection from Djomy (%s) with data:\n%s", reference, pprint.pformat(data))
        transaction_id = data.get('transactionId')
        tx_sudo = self._find_tx(reference, transaction_id)
        if not tx_sudo:
            _logger.warning("Djomy: no transaction for return reference=%s transactionId=%s", reference, transaction_id)
            return request.redirect('/payment/status')

        if not tx_sudo._djomy_sync_status(transaction_id=transaction_id):
            # Djomy injoignable : on ne fait confiance a l'URL que pour un
            # echec/annulation, jamais pour un succes.
            url_status = str(data.get('status') or '').upper()
            if url_status in const.PAYMENT_STATUS_MAPPING['cancel'] + const.PAYMENT_STATUS_MAPPING['error']:
                tx_sudo._process('djomy', {
                    'transactionId': transaction_id,
                    'status': url_status,
                    'merchantPaymentReference': tx_sudo.reference,
                })
        return request.redirect('/payment/status')

    @http.route([_cancel_url, f'{_cancel_url}/<path:reference>'],
                type='http', methods=['GET'], auth='public')
    def djomy_cancel_from_checkout(self, reference=None, **data):
        """Le payeur a quitte la page Djomy sans payer.

        On resynchronise d'abord (il a peut-etre paye avant de cliquer),
        puis on annule ce qui est encore ouvert pour que le portail
        propose a nouveau le bouton Payer. Le lien Djomy reste valide : un
        paiement tardif dessus reveillera la transaction via le webhook.
        """
        _logger.info("Payment cancelled from Djomy (%s) with data:\n%s", reference, pprint.pformat(data))
        tx_sudo = self._find_tx(reference, data.get('transactionId'))
        if tx_sudo:
            tx_sudo._djomy_sync_status(transaction_id=data.get('transactionId'))
            if tx_sudo.state in ('draft', 'pending'):
                tx_sudo._set_canceled(state_message="Paiement abandonne par le client sur la page Djomy.")
        return request.redirect('/payment/status')

    # --- Webhook ------------------------------------------------------------

    @http.route(_webhook_url, type='http', methods=['GET', 'POST'], auth='public', csrf=False)
    def djomy_webhook(self):
        """Process the webhook notification from Djomy.

        GET: Validation/health check for webhook registration.
        POST: Process actual webhook notifications (payload V1 or V2).
        Signature header: X-Webhook-Signature: v1:<signature>

        HMAC verification can be disabled via the system parameter
        ``djomy.webhook_verify_signature`` (see ``_verify_webhook_signature``).
        Whether the signature is checked or not, the controller ALWAYS
        re-fetches the official status from Djomy before transitioning the
        transaction state — this prevents a caller from forging a
        ``{status: SUCCESS}`` payload when the signature check is off.
        """
        # Handle GET requests for webhook validation
        if request.httprequest.method == 'GET':
            _logger.info("Webhook validation request from Djomy")
            return request.make_json_response({'status': 'ok'})

        # Read the RAW body: the HMAC must be computed on the exact bytes
        # Djomy signed, never a re-serialized payload.
        raw_body = request.httprequest.get_data() or b''
        try:
            data = json.loads(raw_body.decode('utf-8')) if raw_body else {}
        except (ValueError, UnicodeDecodeError):
            _logger.warning("Djomy webhook: invalid JSON body")
            return request.make_json_response(
                {'status': 'error', 'reason': 'bad_payload'}
            )
        if not isinstance(data, dict):
            return request.make_json_response({'status': 'error', 'reason': 'bad_payload'})
        _logger.info("Webhook notification from Djomy:\n%s", pprint.pformat(data))

        event_type = data.get('eventType', '')
        if event_type not in const.WEBHOOK_PAYMENT_EVENTS:
            # payout.* et evenements inconnus : rien a faire, on accuse reception
            return request.make_json_response({'status': 'ok', 'reason': 'ignored_event'})

        PaymentTransaction = request.env['payment.transaction'].sudo()
        tx_sudo = PaymentTransaction._search_by_reference('djomy', data)
        if not tx_sudo:
            # Paiement sur un lien statique, ou reference inconnue : il n'y a
            # rien a mettre a jour ici, le rapprochement se fait a la main.
            return request.make_json_response({'status': 'ok', 'reason': 'unknown_transaction'})

        # Verify webhook signature (before touching anything)
        signature = request.httprequest.headers.get('X-Webhook-Signature', '')
        self._verify_webhook_signature(signature, raw_body, tx_sudo)

        # Re-fetch the official status from Djomy to prevent a forged
        # payload from moving the transaction to `done` when signature
        # verification is disabled.
        payment = PaymentTransaction._djomy_payment_data(data)
        transaction_id = payment.get('transactionId') or tx_sudo.provider_reference
        if not tx_sudo._djomy_sync_status(transaction_id=transaction_id):
            _logger.warning(
                "Djomy webhook: could not confirm official status for tx=%s", tx_sudo.reference,
            )
            return request.make_json_response(
                {'status': 'error', 'reason': 'api_unreachable'}
            )
        return request.make_json_response({'status': 'ok'})

    @staticmethod
    def _verify_webhook_signature(received_signature, raw_body, tx_sudo):
        """Verify the webhook signature.

        Format: v1:<HMAC-SHA256(raw_body, clientSecret)>

        Can be disabled via the system parameter
        ``djomy.webhook_verify_signature`` (default ``True``). When set
        to ``False``, a warning is logged and the check is skipped —
        useful when Djomy is not (yet) sending a proper
        ``X-Webhook-Signature`` header. In that case the caller of this
        method MUST compensate by re-fetching the official payment
        status from Djomy before transitioning the transaction (see
        ``djomy_webhook``).
        """
        verify = request.env['ir.config_parameter'].sudo().get_param(
            'djomy.webhook_verify_signature', 'True',
        )
        if str(verify).lower() not in ('true', '1', 'yes'):
            _logger.warning(
                "Djomy webhook: HMAC verification DISABLED via "
                "djomy.webhook_verify_signature=False"
            )
            return

        if not received_signature:
            _logger.warning("Received webhook without signature.")
            raise Forbidden()

        # Extract signature from "v1:signature" format
        if ':' in received_signature:
            _, signature = received_signature.split(':', 1)
        else:
            signature = received_signature

        # Compute expected signature on the RAW body bytes
        if isinstance(raw_body, str):
            raw_body = raw_body.encode('utf-8')
        expected_signature = hmac.new(
            tx_sudo.provider_id.djomy_client_secret.encode('utf-8'),
            raw_body,
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            _logger.warning("Received webhook with invalid signature.")
            raise Forbidden()
