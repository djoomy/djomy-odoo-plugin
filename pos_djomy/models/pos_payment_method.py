# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64
import io
from datetime import datetime, timedelta

from odoo import api, fields, models, _
from odoo.exceptions import UserError, AccessError

try:
    import qrcode
    QRCODE_AVAILABLE = True
except ImportError:
    QRCODE_AVAILABLE = False


class PosPaymentMethod(models.Model):
    _inherit = 'pos.payment.method'

    DJOMY_PAYMENT_METHODS = [
        ('OM', 'Orange Money'),
        ('MOMO', 'MTN Mobile Money'),
        ('KULU', 'Kulu'),
    ]

    def _get_payment_terminal_selection(self):
        return super()._get_payment_terminal_selection() + [('djomy', 'Djomy')]

    djomy_payment_method = fields.Selection(
        selection=DJOMY_PAYMENT_METHODS,
        string='Djomy Payment Method',
        default='OM',
        help='The mobile money provider to use for payments',
    )

    @api.model
    def _load_pos_data_fields(self, config):
        params = super()._load_pos_data_fields(config)
        params += ['djomy_payment_method']
        return params

    def _get_djomy_payment_provider(self):
        """Get the configured Djomy payment provider for the current company."""
        djomy_payment_provider = self.env['payment.provider'].search([
            ('code', '=', 'djomy'),
            ('company_id', '=', self.env.company.id),
        ], limit=1)

        if not djomy_payment_provider:
            raise UserError(_("Djomy payment provider for company %s is missing", self.env.company.name))

        return djomy_payment_provider

    def _generate_qr_code_base64(self, data):
        """Generate a QR code image as base64 string.

        Args:
            data: The data to encode in the QR code (URL or text)

        Returns:
            str: Base64 encoded PNG image with data URI prefix
        """
        if not QRCODE_AVAILABLE:
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
        buffer.seek(0)

        img_base64 = base64.b64encode(buffer.getvalue()).decode('utf-8')
        return f"data:image/png;base64,{img_base64}"

    @api.model
    def djomy_create_payment(self, payment_method_id, amount, phone_number, reference, djomy_method=None):
        """Create a Djomy payment request.

        Args:
            payment_method_id: ID of the pos.payment.method
            amount: Payment amount
            phone_number: Customer phone number in international format (00224XXXXXXXXX)
            reference: Merchant payment reference (POS order reference)
            djomy_method: Payment method selected in popup (OM, MOMO, KULU)

        Returns:
            dict: API response with transactionId and status
        """
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_("Do not have access to create Djomy payments"))

        payment_method = self.browse(payment_method_id)
        provider = payment_method.sudo()._get_djomy_payment_provider()

        # Get currency and country from company
        currency = payment_method.journal_id.currency_id or payment_method.company_id.currency_id
        country_code = payment_method.company_id.country_id.code or 'GN'

        # Use the payment method from popup, fallback to configured default
        selected_method = djomy_method or payment_method.djomy_payment_method or 'OM'

        payload = {
            'paymentMethod': selected_method,
            'payerIdentifier': phone_number,
            'amount': int(amount),
            'countryCode': country_code,
            'description': f'POS Payment - {reference}',
            'merchantPaymentReference': reference,
        }

        try:
            response = provider._djomy_send_request_with_retry('POST', 'payments', json=payload)
            return {
                'success': True,
                'transactionId': response.get('transactionId'),
                'status': response.get('status', 'PENDING'),
                'data': response,
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
            }

    @api.model
    def djomy_create_payment_link(self, payment_method_id, amount, reference, phone_number=None):
        """Create a Djomy payment link for QR code display.

        Args:
            payment_method_id: ID of the pos.payment.method
            amount: Payment amount
            reference: Merchant payment reference (POS order reference)
            phone_number: Optional phone number to send SMS with payment link

        Returns:
            dict: API response with paymentLink URL and linkId
        """
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_("Do not have access to create Djomy payment links"))

        payment_method = self.browse(payment_method_id)
        provider = payment_method.sudo()._get_djomy_payment_provider()

        # Get country from company
        country_code = payment_method.company_id.country_id.code or 'GN'

        # Calculate expiration (15 minutes from now)
        expires_at = (datetime.utcnow() + timedelta(minutes=15)).strftime('%Y-%m-%dT%H:%M:%SZ')

        payload = {
            'amountToPay': int(amount),
            'linkName': f'POS-{reference}',
            'countryCode': country_code,
            'description': f'POS Payment - {reference}',
            'merchantReference': reference,
            'usageType': 'UNIQUE',
            'expiresAt': expires_at,
        }

        # If phone number provided, send SMS with payment link
        if phone_number:
            payload['phoneNumber'] = phone_number
            payload['sendSms'] = True

        try:
            response = provider._djomy_send_request_with_retry('POST', 'links', json=payload)
            payment_page_url = response.get('paymentPageUrl')

            # Generate QR code image as base64
            qr_code_base64 = None
            if payment_page_url:
                qr_code_base64 = payment_method._generate_qr_code_base64(payment_page_url)

            return {
                'success': True,
                'paymentLink': payment_page_url,
                'qrCodeBase64': qr_code_base64,
                'paymentLinkReference': response.get('paymentLinkReference'),
                'expiresAt': expires_at,
                'smsSent': bool(phone_number),
                'data': response,
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
            }

    @api.model
    def djomy_check_payment_status(self, transaction_id):
        """Check the status of a Djomy payment.

        Args:
            transaction_id: The Djomy transaction ID

        Returns:
            dict: Status information
        """
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_("Do not have access to check Djomy payment status"))

        provider = self.sudo()._get_djomy_payment_provider()

        try:
            response = provider._djomy_send_request_with_retry('GET', f'payments/{transaction_id}/status')
            status = response.get('status', '').upper()

            return {
                'success': True,
                'status': status,
                'isPending': status in ['PENDING', 'INITIATED', 'PROCESSING', 'CREATED'],
                'isDone': status in ['SUCCESS', 'SUCCESSFUL'],
                'isFailed': status in ['FAILED', 'ERROR'],
                'isCancelled': status in ['CANCELLED'],
                'data': response,
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
            }

    @api.model
    def djomy_check_link_status(self, payment_link_reference):
        """Check the status of a Djomy payment link.

        Args:
            payment_link_reference: The Djomy payment link reference

        Returns:
            dict: Status information including payment details if paid
        """
        if not self.env.user.has_group('point_of_sale.group_pos_user'):
            raise AccessError(_("Do not have access to check Djomy payment status"))

        provider = self.sudo()._get_djomy_payment_provider()

        try:
            response = provider._djomy_send_request_with_retry('GET', f'links/{payment_link_reference}')
            link_status = response.get('status', '').upper()
            payments = response.get('payments', [])

            # Check if any payment succeeded
            successful_payment = next(
                (p for p in payments if p.get('status', '').upper() == 'SUCCESS'),
                None
            )

            return {
                'success': True,
                'linkStatus': link_status,
                'isPending': link_status == 'ACTIVE' and not successful_payment,
                'isDone': bool(successful_payment),
                'isFailed': any(p.get('status', '').upper() == 'FAILED' for p in payments),
                'isCancelled': link_status == 'REVOKED',
                'isExpired': link_status == 'EXPIRED',
                'transactionId': successful_payment.get('transactionId') if successful_payment else None,
                'payments': payments,
                'data': response,
            }
        except Exception as e:
            return {
                'success': False,
                'error': str(e),
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
