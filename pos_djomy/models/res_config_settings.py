# Part of Odoo. See LICENSE file for full copyright and licensing details.

from odoo import fields, models


class ResConfigSettings(models.TransientModel):
    _inherit = 'res.config.settings'

    pos_djomy_static_link_reference = fields.Char(related='pos_config_id.djomy_static_link_reference')
    pos_djomy_static_link_url = fields.Char(related='pos_config_id.djomy_static_link_url')
    pos_djomy_static_link_qr = fields.Binary(related='pos_config_id.djomy_static_link_qr')

    def pos_djomy_generate_static_link(self):
        """Bouton des parametres du point de vente : delegue a la caisse choisie."""
        self.ensure_one()
        if not self.pos_config_id:
            return False
        return self.pos_config_id.action_djomy_generate_static_link()
