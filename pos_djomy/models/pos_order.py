# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import models


class PosOrder(models.Model):
    _inherit = 'pos.order'

    def action_djomy_match_payment(self):
        """Ouvre le wizard de rattachement d'un paiement Djomy sur cette commande."""
        self.ensure_one()
        return self.env['djomy.payment.match.wizard']._action_open(
            'pos.order', self.id, self.amount_total - self.amount_paid, self.currency_id,
            link_reference=self.config_id.djomy_static_link_reference,
        )
