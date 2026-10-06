# Part of Odoo. See LICENSE file for full copyright and licensing details.

import base64

from odoo import _, api, fields, models
from odoo.exceptions import UserError


class PosConfig(models.Model):
    _inherit = 'pos.config'

    djomy_static_link_reference = fields.Char(
        string="QR Djomy de la boutique (reference)", copy=False,
        help="Lien de paiement a usage multiple propre a cette caisse, affiche en QR au "
             "comptoir. Les paiements recus portent sa reference : la caisse les montre en "
             "premier dans « Deja paye ». S'ajoute au QR global du fournisseur Djomy.",
    )
    djomy_static_link_url = fields.Char(string="QR Djomy de la boutique (URL)", copy=False, readonly=True)
    djomy_static_link_qr = fields.Binary(string="QR Djomy de la boutique", compute='_compute_djomy_static_link_qr')

    @api.depends('djomy_static_link_url')
    def _compute_djomy_static_link_qr(self):
        Provider = self.env['payment.provider'].sudo()
        for config in self:
            png = Provider._djomy_qr_code_png(config.djomy_static_link_url) if config.djomy_static_link_url else None
            config.djomy_static_link_qr = base64.b64encode(png) if png else False

    def action_djomy_generate_static_link(self):
        """Cree (ou remplace) le QR a usage multiple de cette boutique."""
        self.ensure_one()
        provider = self.env['payment.provider']._djomy_provider_for(self.company_id)
        if not provider:
            raise UserError(_("Aucun fournisseur de paiement Djomy actif pour %s.", self.company_id.name))
        reference = f"STATIC-{self.company_id.id}-POS{self.id}-{fields.Datetime.now():%Y%m%d%H%M%S}"
        link = provider._djomy_create_static_link(
            reference,
            link_name=_("QR %s", self.name),
            description=_("Paiement libre - %s", self.name),
            metadata={'odoo_static_link': True, 'odoo_company': self.company_id.id, 'odoo_pos_config': self.id},
        )
        self.write({
            'djomy_static_link_reference': link.get('paymentLinkReference'),
            'djomy_static_link_url': link.get('paymentPageUrl'),
        })
        return {
            'type': 'ir.actions.client',
            'tag': 'display_notification',
            'params': {
                'type': 'success',
                'message': _("QR Djomy de la boutique %(shop)s cree : %(ref)s",
                             shop=self.name, ref=self.djomy_static_link_reference),
                'next': {'type': 'ir.actions.client', 'tag': 'soft_reload'},
            },
        }
