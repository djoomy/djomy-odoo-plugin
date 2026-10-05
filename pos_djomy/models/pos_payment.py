# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import api, models, _
from odoo.exceptions import ValidationError


class PosPayment(models.Model):
    _inherit = 'pos.payment'

    @api.constrains('transaction_id', 'payment_method_id')
    def _check_djomy_transaction_unique(self):
        """Un paiement Djomy ne peut etre encaisse qu'une fois.

        Le rattachement manuel (QR statique) copie un `transactionId` Djomy
        sur une ligne de paiement : deux commandes ne doivent jamais se
        partager le meme argent, quelle que soit la session.
        """
        for payment in self:
            if not payment.transaction_id or payment.payment_method_id.use_payment_terminal != 'djomy':
                continue
            duplicate = self.search([
                ('id', '!=', payment.id),
                ('transaction_id', '=', payment.transaction_id),
                ('payment_method_id.use_payment_terminal', '=', 'djomy'),
                ('company_id', '=', payment.company_id.id),
            ], limit=1)
            if duplicate:
                raise ValidationError(_(
                    "Le paiement Djomy %(tx)s est deja encaisse sur la commande %(order)s.",
                    tx=payment.transaction_id, order=duplicate.pos_order_id.name,
                ))
