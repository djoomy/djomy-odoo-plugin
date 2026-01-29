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


_logger = get_payment_logger(__name__)


class DjomyController(http.Controller):
    _return_url = '/payment/djomy/return'
    _cancel_url = '/payment/djomy/cancel'
    _webhook_url = '/payment/djomy/webhook'
    _process_url = '/payment/djomy/process'

    @http.route(_process_url, type='json', auth='public')
    def djomy_process_payment(self, reference, phone):
        """Process Djomy payment with phone number from inline form.

        :param str reference: The transaction reference.
        :param str phone: The payer's phone number.
        :return: The redirect URL or error message.
        :rtype: dict
        """
        _logger.info("Processing Djomy payment for reference %s with phone %s", reference, phone)

        tx_sudo = request.env['payment.transaction'].sudo().search([
            ('reference', '=', reference),
            ('provider_code', '=', 'djomy'),
        ], limit=1)

        if not tx_sudo:
            _logger.warning("Djomy: Transaction not found for reference %s", reference)
            return {'error': 'Transaction non trouvee'}

        # Update the phone on the transaction
        tx_sudo.partner_phone = phone

        # Create payment on Djomy and get redirect URL
        redirect_url = tx_sudo._djomy_create_payment()

        if not redirect_url:
            _logger.error("Djomy: Failed to create payment for reference %s", reference)
            return {'error': 'Erreur lors de la creation du paiement'}

        return {'redirect_url': redirect_url}

    @http.route(_return_url, type='http', methods=['GET'], auth='public')
    def djomy_return_from_checkout(self, **data):
        """Process the payment data sent by Djomy after redirection.

        Djomy should add ?transactionId=<uuid>&status=SUCCESS|FAILED|CANCELLED
        But sometimes Djomy doesn't send these parameters, so we fallback to
        finding the most recent pending transaction.
        """
        _logger.info("Handling redirection from Djomy with data:\n%s", pprint.pformat(data))

        transaction_id = data.get('transactionId')
        url_status = data.get('status', '').upper()

        tx_sudo = None

        if transaction_id:
            # Find transaction by provider reference (transactionId from Djomy)
            tx_sudo = request.env['payment.transaction'].sudo().search([
                ('provider_code', '=', 'djomy'),
                ('provider_reference', '=', transaction_id),
            ], limit=1)

        if not tx_sudo:
            # Fallback: find the most recent draft/pending djomy transaction
            tx_sudo = request.env['payment.transaction'].sudo().search([
                ('provider_code', '=', 'djomy'),
                ('state', 'in', ['draft', 'pending']),
            ], order='create_date desc', limit=1)
            _logger.info("Djomy: No transactionId in URL, found fallback tx: %s", tx_sudo.reference if tx_sudo else None)

        if tx_sudo:
            # Get transaction_id from the transaction if not in URL
            if not transaction_id:
                transaction_id = tx_sudo.provider_reference

            if transaction_id:
                # Query API to get actual status
                try:
                    api_data = tx_sudo._send_api_request(
                        'GET', f'payments/{transaction_id}/status'
                    )
                    # Use API status if URL status not available
                    api_status = api_data.get('status', '').upper()
                    final_status = url_status if url_status else api_status

                    payment_data = {
                        'transactionId': transaction_id,
                        'status': final_status,
                        'merchantPaymentReference': tx_sudo.reference,
                    }
                    payment_data.update(api_data)
                    payment_data['status'] = final_status

                    _logger.info("Djomy: Processing payment with status: %s", final_status)
                    tx_sudo._process('djomy', payment_data)

                except ValidationError as e:
                    _logger.warning("Could not fetch payment details from Djomy API: %s", e)
                    # Still try to process with what we have
                    if url_status:
                        payment_data = {
                            'transactionId': transaction_id,
                            'status': url_status,
                            'merchantPaymentReference': tx_sudo.reference,
                        }
                        tx_sudo._process('djomy', payment_data)
            else:
                _logger.warning("Djomy: No transactionId available to verify payment status")

        return request.redirect('/payment/status')

    @http.route(_cancel_url, type='http', methods=['GET'], auth='public')
    def djomy_cancel_from_checkout(self, **data):
        """Handle payment cancellation."""
        _logger.info("Payment cancelled from Djomy with data:\n%s", pprint.pformat(data))
        return request.redirect('/payment/status')

    @http.route(_webhook_url, type='http', methods=['GET', 'POST'], auth='public', csrf=False)
    def djomy_webhook(self):
        """Process the webhook notification from Djomy.

        GET: Validation/health check for webhook registration.
        POST: Process actual webhook notifications.
        Signature header: X-Webhook-Signature: v1:<signature>
        """
        # Handle GET requests for webhook validation
        if request.httprequest.method == 'GET':
            _logger.info("Webhook validation request from Djomy")
            return request.make_json_response({'status': 'ok'})

        data = request.get_json_data()
        _logger.info("Webhook notification from Djomy:\n%s", pprint.pformat(data))

        event_type = data.get('eventType', '')

        if event_type in [
            'payment.success',
            'payment.failed',
            'payment.cancelled',
            'payment.pending',
        ]:
            # Find the transaction
            tx_sudo = request.env['payment.transaction'].sudo()._search_by_reference(
                'djomy', data
            )

            if tx_sudo:
                # Verify webhook signature
                signature = request.httprequest.headers.get('X-Webhook-Signature', '')
                self._verify_webhook_signature(signature, data, tx_sudo)

                # Process the transaction
                tx_sudo._process('djomy', data)

        return request.make_json_response({'status': 'ok'})

    @staticmethod
    def _verify_webhook_signature(received_signature, payload, tx_sudo):
        """Verify the webhook signature.

        Format: v1:<HMAC-SHA256(payload, clientSecret)>
        """
        if not received_signature:
            _logger.warning("Received webhook without signature.")
            raise Forbidden()

        # Extract signature from "v1:signature" format
        if ':' in received_signature:
            _, signature = received_signature.split(':', 1)
        else:
            signature = received_signature

        # Compute expected signature
        payload_str = json.dumps(payload, separators=(',', ':'))
        expected_signature = hmac.new(
            tx_sudo.provider_id.djomy_client_secret.encode('utf-8'),
            payload_str.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()

        if not hmac.compare_digest(signature, expected_signature):
            _logger.warning("Received webhook with invalid signature.")
            raise Forbidden()

