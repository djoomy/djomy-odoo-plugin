# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, fields, models, _
from odoo.exceptions import UserError

from odoo.addons.payment_djomy import const


class DjomyPaymentMatchWizard(models.TransientModel):
    _inherit = 'djomy.payment.match.wizard'

    @api.model
    def _djomy_find_existing_match(self, transaction_id):
        """Etend la detection de doublon aux paiements de caisse."""
        existing = super()._djomy_find_existing_match(transaction_id)
        if existing:
            return existing
        payment = self.env['pos.payment'].sudo().search([
            ('transaction_id', '=', transaction_id),
            ('payment_method_id.use_payment_terminal', '=', 'djomy'),
        ], limit=1)
        return payment.pos_order_id.name if payment else False

    def _djomy_apply_match(self, payment):
        if self.res_model == 'pos.order':
            return self._djomy_match_pos_order(payment)
        return super()._djomy_apply_match(payment)

    def _djomy_pos_payment_method(self, order):
        """Le mode de paiement Djomy de la caisse de la commande.

        Pas de repli sur un autre mode de la societe : Odoo refuse un
        paiement dont le mode n'est pas autorise dans la config de la session.
        """
        methods = order.config_id.payment_method_ids.filtered(
            lambda m: m.use_payment_terminal == 'djomy'
        )
        if not methods:
            raise UserError(_(
                "Aucun mode de paiement Djomy n'est configure pour le point de vente %s : "
                "ajoutez-le a la caisse avant de rattacher ce paiement.",
                order.config_id.name,
            ))
        return methods[0]

    def _djomy_match_pos_order(self, payment):
        order = self.env['pos.order'].browse(self.res_id).exists()
        if not order:
            raise UserError(_("La commande n'existe plus."))
        if order.state != 'draft':
            raise UserError(_("La commande %s n'est plus en attente de paiement.", order.name))

        amount = self._djomy_payment_amount(payment)
        currency_code = payment.get('currency') or order.currency_id.name
        if currency_code != order.currency_id.name:
            raise UserError(_(
                "Devise du paiement (%s) differente de celle de la commande (%s).",
                currency_code, order.currency_id.name,
            ))
        remaining = order.amount_total - order.amount_paid
        if order.currency_id.compare_amounts(amount, remaining) > 0:
            raise UserError(_(
                "Le paiement (%(paid)s) depasse le restant du de la commande (%(due)s).",
                paid=amount, due=remaining,
            ))

        method = self._djomy_pos_payment_method(order)
        djomy_method = str(payment.get('paymentMethod') or '').upper()
        order.add_payment({
            'pos_order_id': order.id,
            'payment_method_id': method.id,
            'amount': amount,
            'name': _("Djomy %s", djomy_method or ''),
            'transaction_id': payment['transactionId'],
            'payment_status': 'done',
            'payment_method_payment_mode': djomy_method or False,
            'payment_ref_no': payment.get('merchantPaymentReference') or False,
        })
        if order._is_pos_order_paid():
            order.action_pos_order_paid()
        order.message_post(body=_(
            "Paiement Djomy %(tx)s rattache manuellement : %(amount)s %(currency)s "
            "par %(method)s (%(payer)s).",
            tx=payment['transactionId'], amount=amount, currency=currency_code,
            method=const.PAYMENT_METHOD_LABELS.get(djomy_method, djomy_method or '?'),
            payer=payment.get('payerIdentifier') or '?',
        ))
        return order
