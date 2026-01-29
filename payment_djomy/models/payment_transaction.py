# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import _, api, models
from odoo.exceptions import ValidationError
from odoo.tools import urls

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_djomy import const
from odoo.addons.payment_djomy.controllers.main import DjomyController


_logger = get_payment_logger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    def _get_specific_rendering_values(self, processing_values):
        """Override of payment to return Djomy-specific rendering values.

        For Djomy, we use a direct flow where the JS captures the phone number
        and calls _djomy_create_payment via a custom route. So we don't initiate
        the payment here, just return the reference for the JS to use.

        Note: self.ensure_one() from `_get_processing_values`
        """
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'djomy':
            return res

        return {
            'reference': self.reference,
        }

    def _djomy_create_payment(self):
        """Create payment on Djomy API and return the redirect URL.

        This method is called from the controller after the phone number
        has been captured from the inline form.

        :return: The redirect URL to the Djomy gateway, or None on failure.
        :rtype: str or None
        """
        self.ensure_one()

        base_url = self.provider_id.get_base_url()
        return_url = urls.urljoin(base_url, DjomyController._return_url)
        cancel_url = urls.urljoin(base_url, DjomyController._cancel_url)

        _logger.info(
            "Djomy: Creating payment for %s, amount=%s, phone=%s",
            self.reference, self.amount, self.partner_phone
        )

        payload = {
            'amount': int(self.amount),
            'countryCode': self.partner_country_id.code or 'GN',
            'payerNumber': self.partner_phone or '',
            'description': f"Payment for {self.reference}",
            'merchantPaymentReference': self.reference,
            'returnUrl': return_url,
            'cancelUrl': cancel_url,
        }

        try:
            # Use provider's retry method for automatic token refresh on 401
            payment_data = self.provider_id._djomy_send_request_with_retry(
                'POST', 'payments/gateway', json=payload
            )
        except ValidationError as error:
            _logger.error("Djomy: API error for %s: %s", self.reference, error)
            self._set_error(str(error))
            return None

        # Save the transaction ID for later reference
        transaction_id = payment_data.get('transactionId')
        if transaction_id:
            self.provider_reference = transaction_id

        # Extract and return the redirect URL
        redirect_url = payment_data.get('redirectUrl') or payment_data.get('link')
        _logger.info("Djomy: Payment created, redirect URL: %s", redirect_url)

        return redirect_url

    @api.model
    def _extract_reference(self, provider_code, payment_data):
        """Override of `payment` to extract the reference from the payment data."""
        if provider_code != 'djomy':
            return super()._extract_reference(provider_code, payment_data)
        return (
            payment_data.get('merchantPaymentReference')
            or payment_data.get('data', {}).get('merchantPaymentReference')
        )

    def _extract_amount_data(self, payment_data):
        """Override of `payment` to extract the amount and currency."""
        if self.provider_code != 'djomy':
            return super()._extract_amount_data(payment_data)

        data = payment_data.get('data', payment_data)
        return {
            'amount': float(data.get('paidAmount', data.get('amount', 0))),
            'currency_code': data.get('currency', 'GNF'),
        }

    def _apply_updates(self, payment_data):
        """Override of `payment` to update the transaction based on payment data."""
        if self.provider_code != 'djomy':
            return super()._apply_updates(payment_data)

        data = payment_data.get('data', payment_data)

        # Update the provider reference
        self.provider_reference = data.get('transactionId')

        # Update the payment state
        payment_status = data.get('status', '').upper()

        if payment_status in const.PAYMENT_STATUS_MAPPING['pending']:
            self._set_pending()
        elif payment_status in const.PAYMENT_STATUS_MAPPING['done']:
            self._set_done()
        elif payment_status in const.PAYMENT_STATUS_MAPPING['cancel']:
            self._set_canceled()
        elif payment_status in const.PAYMENT_STATUS_MAPPING['error']:
            self._set_error(_(
                "An error occurred during the processing of your payment (status %s).",
                payment_status
            ))
        else:
            _logger.warning(
                "Received data with invalid payment status (%s) for transaction %s.",
                payment_status, self.reference
            )
            self._set_error(_("Unknown payment status: %s", payment_status))
