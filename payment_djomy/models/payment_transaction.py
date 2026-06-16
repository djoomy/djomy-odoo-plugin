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

    # === UX : nettoyage des transactions zombies ===========================

    @api.model_create_multi
    def create(self, vals_list):
        """Override : à la création d'une nouvelle tx Djomy, annule les
        précédentes `draft`/`pending` rattachées aux mêmes SO/factures.

        Le besoin métier : si le client a abandonné un précédent paiement
        (browser fermé, timeout réseau, IPN perdu côté Djomy…), la tx
        précédente reste accrochée à la commande/facture et bloque l'UX
        du portail. À chaque nouvelle tentative on fait table rase des
        zombies pour qu'il n'y ait au plus qu'une tx active par périmètre.

        Sont préservés : tous les états finaux (`done`/`cancel`/`error`) et
        toutes les tx d'autres providers (scope strict `provider_code='djomy'`).
        """
        records = super().create(vals_list)
        records.filtered(lambda t: t.provider_code == 'djomy')._djomy_cancel_stale_siblings()
        return records

    def _djomy_cancel_stale_siblings(self):
        """Annule les autres tx Djomy `draft`/`pending` sur les mêmes SO /
        factures que `self`, ainsi que les `account.payment` draft associés.
        """
        PT = self.sudo()
        for tx in self:
            so_ids = tx.sale_order_ids.ids if 'sale_order_ids' in tx._fields else []
            inv_ids = tx.invoice_ids.ids if 'invoice_ids' in tx._fields else []
            if not (so_ids or inv_ids):
                continue
            domain = [
                ('id', '!=', tx.id),
                ('provider_code', '=', 'djomy'),
                ('state', 'in', ('draft', 'pending')),
            ]
            stale = PT.browse()
            if so_ids:
                stale |= PT.search(domain + [('sale_order_ids', 'in', so_ids)])
            if inv_ids:
                stale |= PT.search(domain + [('invoice_ids', 'in', inv_ids)])
            if not stale:
                continue
            _logger.info(
                "[DJOMY] tx %s supersedes %d stale tx(s) %s — auto-cancel",
                tx.reference, len(stale), stale.mapped('reference'),
            )
            # Note interne en chatter SO/facture — pas dans `state_message`
            # qui serait rendu côté portail client (payment_templates).
            note = _(
                "Transaction Djomy %(old)s annulée automatiquement — "
                "remplacée par la nouvelle tentative %(new)s.",
                old=', '.join(stale.mapped('reference')), new=tx.reference,
            )
            if 'sale_order_ids' in tx._fields:
                for so in tx.sale_order_ids:
                    so.message_post(body=note)
            if 'invoice_ids' in tx._fields:
                for inv in tx.invoice_ids:
                    inv.message_post(body=note)

            for s in stale:
                s._set_canceled()
                # Annule aussi les account.payment draft liés (sinon la
                # facture reste polluée par un brouillon orphelin).
                stale_payments = self.env['account.payment'].sudo().search([
                    ('payment_transaction_id', '=', s.id),
                    ('state', '=', 'draft'),
                ])
                for p in stale_payments:
                    try:
                        p.action_cancel()
                    except Exception as exc:
                        _logger.warning(
                            "[DJOMY] could not cancel stale account.payment "
                            "%d (tx %s): %s", p.id, s.reference, exc,
                        )
