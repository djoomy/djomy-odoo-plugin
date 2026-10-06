# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import models


class AccountMove(models.Model):
    _inherit = 'account.move'

    def action_djomy_match_payment(self):
        """Ouvre le wizard de rattachement d'un paiement Djomy sur cette facture."""
        self.ensure_one()
        return self.env['djomy.payment.match.wizard']._action_open(
            'account.move', self.id, self.amount_residual, self.currency_id,
        )
