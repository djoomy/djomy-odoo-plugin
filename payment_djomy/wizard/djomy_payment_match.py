# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""Rapprochement manuel d'un paiement Djomy avec un document Odoo.

Cas d'usage : le commerce affiche un QR code statique (lien Djomy a usage
multiple), ou un client paie le QR de sa note apres que la caisse a encaisse
autrement. Djomy a l'argent, mais aucune commande ni facture Odoo n'est
marquee payee, puisque le paiement n'est lie a rien. Ce wizard liste les
paiements recus par le marchand sur une periode (`GET /payments`, quel que
soit le lien d'origine) - ou ceux d'un lien precis - et rattache celui que
designe le caissier au document ouvert, apres avoir re-verifie son statut
aupres de Djomy.

Le rattachement lui-meme depend du document cible : ce module sait traiter
les factures clients (`account.move`) ; `pos_djomy` etend
:meth:`DjomyPaymentMatchWizard._djomy_apply_match` pour les commandes POS.
"""

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import UserError, ValidationError

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_djomy import const, tools


_logger = get_payment_logger(__name__)


class DjomyPaymentMatchWizard(models.TransientModel):
    _name = 'djomy.payment.match.wizard'
    _description = "Rattacher un paiement Djomy"

    res_model = fields.Char(string="Modele cible", readonly=True, required=True)
    res_id = fields.Integer(string="Document cible", readonly=True, required=True)
    target_name = fields.Char(string="Document", compute='_compute_target_name')
    company_id = fields.Many2one(
        'res.company', required=True, default=lambda self: self.env.company,
    )
    currency_id = fields.Many2one('res.currency', readonly=True)
    expected_amount = fields.Monetary(
        string="Restant du", currency_field='currency_id', readonly=True,
    )
    provider_name = fields.Char(string="Fournisseur", compute='_compute_provider_name')
    source = fields.Selection([
        ('all', "Tous les paiements de la periode"),
        ('link', "Un lien precis"),
    ], string="Chercher dans", default='all', required=True)
    link_reference = fields.Char(
        string="Reference du lien",
        help="Reference Djomy du lien de paiement a inspecter. Prerempli avec le "
             "lien statique du fournisseur s'il existe.",
    )
    date_from = fields.Datetime(
        string="Du", default=lambda self: fields.Datetime.now() - timedelta(days=7),
    )
    date_to = fields.Datetime(string="Au")
    max_pages = fields.Integer(
        string="Pages max", default=5,
        help="Nombre maximal de pages de paiements parcourues (100 paiements par page).",
    )
    line_ids = fields.One2many(
        'djomy.payment.match.line', 'wizard_id', string="Paiements Djomy",
    )
    note = fields.Char(readonly=True)

    # --- Ouverture ----------------------------------------------------------

    @api.model
    def _action_open(self, res_model, res_id, expected_amount, currency, link_reference=None):
        """Action de fenetre du wizard pour un document donne.

        :param link_reference: lien prerempli pour la source « un lien precis »
            (le QR de la boutique depuis une commande POS) ; sinon le QR global.
        """
        provider = self._djomy_provider(self.env.company)
        return {
            'name': _("Rattacher un paiement Djomy"),
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'view_mode': 'form',
            'target': 'new',
            'context': {
                'default_res_model': res_model,
                'default_res_id': res_id,
                'default_expected_amount': expected_amount,
                'default_currency_id': currency.id if currency else False,
                'default_link_reference': link_reference or (provider.djomy_static_link_reference if provider else False),
                'default_source': 'all',
            },
        }

    @api.model
    def _djomy_provider(self, company=None):
        """Le fournisseur Djomy actif de la societe, en sudo (ACL group_system)."""
        company = company or self.env.company
        return self.env['payment.provider'].sudo().search([
            ('code', '=', 'djomy'),
            ('company_id', '=', company.id),
            ('state', '!=', 'disabled'),
        ], limit=1)

    def _djomy_require_provider(self):
        self.ensure_one()
        provider = self._djomy_provider(self.company_id)
        if not provider:
            raise UserError(_(
                "Aucun fournisseur de paiement Djomy actif pour %s.", self.company_id.name,
            ))
        return provider

    @api.depends('company_id')
    def _compute_provider_name(self):
        for wizard in self:
            provider = wizard._djomy_provider(wizard.company_id)
            wizard.provider_name = provider.name if provider else _("(aucun)")

    @api.depends('res_model', 'res_id')
    def _compute_target_name(self):
        for wizard in self:
            record = wizard._djomy_target()
            wizard.target_name = record.display_name if record else ''

    def _djomy_target(self):
        self.ensure_one()
        if not self.res_model or not self.res_id or self.res_model not in self.env:
            return None
        return self.env[self.res_model].browse(self.res_id).exists() or None

    @api.onchange('source')
    def _onchange_source(self):
        if self.source == 'link' and not self.link_reference:
            provider = self._djomy_provider(self.company_id)
            self.link_reference = provider.djomy_static_link_reference if provider else False

    # --- Chargement des paiements ------------------------------------------

    def action_refresh(self):
        """Recharge les lignes depuis Djomy et rouvre le wizard."""
        self.ensure_one()
        payments = self._djomy_collect_payments()
        self.line_ids.unlink()
        vals = [self._djomy_line_vals(p, link_ref) for p, link_ref in payments]
        vals = [v for v in vals if v]
        self.line_ids = [(0, 0, v) for v in vals]
        self.note = _("%s paiement(s) trouve(s).", len(vals)) if vals else _(
            "Aucun paiement sur cette periode."
        )
        return self._reopen()

    def _reopen(self):
        return {
            'type': 'ir.actions.act_window',
            'res_model': self._name,
            'res_id': self.id,
            'view_mode': 'form',
            'target': 'new',
        }

    def _djomy_collect_payments(self):
        """Renvoie une liste de ``(paiement, reference_du_lien)`` depuis Djomy.

        ``all`` : `GET /payments`, tous les paiements du marchand sur la
        periode, pagines (``max_pages`` x 100). ``link`` : les paiements d'un
        lien precis (`GET /links/{ref}`), par defaut le lien statique.
        """
        self.ensure_one()
        provider = self._djomy_require_provider()
        Provider = provider.__class__
        try:
            if self.source == 'link':
                reference = self.link_reference or provider.djomy_static_link_reference
                if not reference:
                    raise UserError(_(
                        "Aucun lien a inspecter : indiquez une reference de lien, ou "
                        "generez le lien statique sur le fournisseur Djomy."
                    ))
                link = provider._djomy_get_link(reference)
                collected = [(p, reference) for p in Provider._djomy_link_payments(link)]
            else:
                payments = provider._djomy_iter_payments(
                    self.date_from, self.date_to or fields.Datetime.now(),
                    max_pages=self.max_pages,
                )
                collected = [(p, p.get('paymentLinkReference')) for p in payments]
        except ValidationError as error:
            raise UserError(_("Djomy n'a pas repondu : %s", error)) from error
        return self._djomy_filter_by_period(collected)

    def _djomy_filter_by_period(self, payments):
        """Ne garde que les paiements dates dans [date_from, date_to] (les non dates passent)."""
        kept = []
        for payment, link_ref in payments:
            paid_at = tools.from_api_datetime(payment.get('createdAt') or payment.get('paidAt'))
            if paid_at:
                if self.date_from and paid_at < self.date_from:
                    continue
                if self.date_to and paid_at > self.date_to:
                    continue
            kept.append((payment, link_ref))
        kept.sort(key=lambda item: str(item[0].get('createdAt') or ''), reverse=True)
        return kept

    def _djomy_line_vals(self, payment, link_reference):
        transaction_id = payment.get('transactionId') or payment.get('id')
        if not transaction_id:
            return None
        amount = payment.get('paidAmount', payment.get('amount'))
        return {
            'transaction_id': str(transaction_id),
            'link_reference': link_reference or payment.get('paymentLinkReference'),
            'status': str(payment.get('status') or '').upper(),
            'paid_amount': float(amount or 0),
            'currency_code': payment.get('currency') or '',
            'payment_method': str(payment.get('paymentMethod') or '').upper(),
            'payer_identifier': payment.get('payerIdentifier') or '',
            'paid_at': tools.from_api_datetime(payment.get('createdAt') or payment.get('paidAt')),
            'merchant_reference': payment.get('merchantPaymentReference') or '',
            'matched_on': self._djomy_find_existing_match(str(transaction_id)),
        }

    @api.model
    def _djomy_find_existing_match(self, transaction_id):
        """Nom du document deja rattache a ce transactionId, ou False.

        Etendu par pos_djomy pour les paiements de caisse.
        """
        tx = self.env['payment.transaction'].sudo().search([
            ('provider_code', '=', 'djomy'),
            ('provider_reference', '=', transaction_id),
            ('state', '=', 'done'),
        ], limit=1)
        if tx:
            docs = tx.invoice_ids if 'invoice_ids' in tx._fields else tx.browse()
            return ', '.join(docs.mapped('name')) or tx.reference
        return False

    # --- Rattachement -------------------------------------------------------

    def _djomy_apply_match(self, payment):
        """Rattache un paiement Djomy verifie au document cible.

        :param dict payment: donnees du paiement telles que confirmees par
            `GET /payments/{id}/status` (statut abouti, montant present).
        """
        self.ensure_one()
        if self.res_model == 'account.move':
            return self._djomy_match_account_move(payment)
        raise UserError(_(
            "Le rattachement d'un paiement Djomy n'est pas pris en charge pour %s.",
            self.res_model,
        ))

    def _djomy_payment_amount(self, payment):
        amount = payment.get('paidAmount', payment.get('amount'))
        try:
            amount = float(amount)
        except (TypeError, ValueError):
            amount = 0.0
        if amount <= 0:
            raise UserError(_("Djomy ne renvoie aucun montant pour ce paiement."))
        return amount

    def _djomy_match_account_move(self, payment):
        move = self.env['account.move'].browse(self.res_id).exists()
        if not move:
            raise UserError(_("La facture n'existe plus."))
        if move.move_type not in ('out_invoice', 'out_receipt') or move.state != 'posted':
            raise UserError(_("Seule une facture client validee peut recevoir un paiement Djomy."))
        if move.payment_state not in ('not_paid', 'partial'):
            raise UserError(_("La facture %s est deja reglee.", move.name))

        amount = self._djomy_payment_amount(payment)
        currency_code = payment.get('currency') or move.currency_id.name
        if currency_code != move.currency_id.name:
            raise UserError(_(
                "Devise du paiement (%s) differente de celle de la facture (%s).",
                currency_code, move.currency_id.name,
            ))
        if move.currency_id.compare_amounts(amount, move.amount_residual) > 0:
            raise UserError(_(
                "Le paiement (%(paid)s) depasse le restant du de la facture (%(due)s). "
                "Enregistrez-le manuellement pour traiter le trop-percu.",
                paid=amount, due=move.amount_residual,
            ))

        provider = self._djomy_require_provider()
        PaymentTransaction = self.env['payment.transaction'].sudo()
        tx = PaymentTransaction.create({
            'provider_id': provider.id,
            'payment_method_id': provider.payment_method_ids[:1].id,
            'reference': PaymentTransaction._compute_reference('djomy', prefix=f"{move.name}-DJOMY"),
            'amount': amount,
            'currency_id': move.currency_id.id,
            'partner_id': move.partner_id.commercial_partner_id.id,
            'operation': 'offline',
            'invoice_ids': [(6, 0, [move.id])],
            'provider_reference': payment['transactionId'],
            'djomy_payment_method': str(payment.get('paymentMethod') or '').upper() or False,
        })
        tx._process('djomy', {**payment, 'merchantPaymentReference': tx.reference})
        if tx.state != 'done':
            raise UserError(_(
                "Le paiement n'a pas pu etre valide (etat %s : %s).",
                tx.state, tx.state_message or '-',
            ))
        try:
            tx._post_process()
        except Exception as error:  # noqa: BLE001 - message actionnable pour l'utilisateur
            raise UserError(_(
                "Paiement valide mais non comptabilise : %s\nVerifiez le journal "
                "du fournisseur de paiement Djomy.", error,
            )) from error
        move.message_post(body=_(
            "Paiement Djomy %(tx)s rattache manuellement : %(amount)s %(currency)s "
            "par %(method)s (%(payer)s).",
            tx=payment['transactionId'], amount=amount, currency=currency_code,
            method=const.PAYMENT_METHOD_LABELS.get(tx.djomy_payment_method, tx.djomy_payment_method or '?'),
            payer=payment.get('payerIdentifier') or '?',
        ))
        return tx


class DjomyPaymentMatchLine(models.TransientModel):
    _name = 'djomy.payment.match.line'
    _description = "Paiement Djomy candidat au rattachement"
    _order = 'paid_at desc, id desc'

    wizard_id = fields.Many2one(
        'djomy.payment.match.wizard', required=True, ondelete='cascade',
    )
    transaction_id = fields.Char(string="Transaction Djomy", required=True, readonly=True)
    link_reference = fields.Char(string="Lien", readonly=True)
    status = fields.Char(string="Statut", readonly=True)
    is_success = fields.Boolean(compute='_compute_is_success')
    paid_amount = fields.Float(string="Montant", readonly=True)
    currency_code = fields.Char(string="Devise", readonly=True)
    payment_method = fields.Char(string="Moyen", readonly=True)
    payment_method_label = fields.Char(compute='_compute_payment_method_label')
    payer_identifier = fields.Char(string="Payeur", readonly=True)
    paid_at = fields.Datetime(string="Date", readonly=True)
    merchant_reference = fields.Char(string="Ref. marchand", readonly=True)
    matched_on = fields.Char(string="Deja rattache a", readonly=True)

    @api.depends('status')
    def _compute_is_success(self):
        for line in self:
            line.is_success = line.status in const.PAYMENT_STATUS_MAPPING['done']

    @api.depends('payment_method')
    def _compute_payment_method_label(self):
        for line in self:
            line.payment_method_label = const.PAYMENT_METHOD_LABELS.get(
                line.payment_method, line.payment_method,
            )

    def action_match(self):
        """Re-verifie le paiement aupres de Djomy puis le rattache au document."""
        self.ensure_one()
        wizard = self.wizard_id
        if self.matched_on:
            raise UserError(_(
                "Ce paiement est deja rattache a %s.", self.matched_on,
            ))
        existing = wizard._djomy_find_existing_match(self.transaction_id)
        if existing:
            raise UserError(_("Ce paiement est deja rattache a %s.", existing))

        provider = wizard._djomy_require_provider()
        try:
            data = provider._djomy_get_payment_status(self.transaction_id)
        except ValidationError as error:
            raise UserError(_("Djomy n'a pas confirme ce paiement : %s", error)) from error
        payment = self.env['payment.transaction']._djomy_payment_data(data)
        payment.setdefault('transactionId', self.transaction_id)
        status = str(payment.get('status') or '').upper()
        if status not in const.PAYMENT_STATUS_MAPPING['done']:
            raise UserError(_(
                "Ce paiement n'est pas abouti chez Djomy (statut %s).", status or '?',
            ))
        if not payment.get('paidAmount') and not payment.get('amount'):
            payment['paidAmount'] = self.paid_amount
        if not payment.get('paymentMethod') and self.payment_method:
            payment['paymentMethod'] = self.payment_method
        if not payment.get('payerIdentifier') and self.payer_identifier:
            payment['payerIdentifier'] = self.payer_identifier

        wizard._djomy_apply_match(payment)
        _logger.info(
            "Djomy: payment %s matched on %s,%s by uid %s",
            self.transaction_id, wizard.res_model, wizard.res_id, self.env.uid,
        )
        return {'type': 'ir.actions.client', 'tag': 'soft_reload'}
