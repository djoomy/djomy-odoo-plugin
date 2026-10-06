# Part of Odoo. See LICENSE file for full copyright and licensing details.

from datetime import timedelta

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools import urls

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_djomy import const, tools
from odoo.addons.payment_djomy.controllers.main import DjomyController


_logger = get_payment_logger(__name__)


class PaymentTransaction(models.Model):
    _inherit = 'payment.transaction'

    # `provider_reference` reste reserve au `transactionId` Djomy, qui n'existe
    # qu'une fois le payeur passe a l'acte. Avant cela, la seule cle dont on
    # dispose est la reference du lien de paiement.
    djomy_payment_link_reference = fields.Char(
        string="Lien de paiement Djomy", copy=False, index='btree_not_null',
    )
    djomy_payment_link_url = fields.Char(
        string="URL du lien Djomy", copy=False,
    )
    djomy_payment_method = fields.Char(
        string="Moyen de paiement Djomy", copy=False,
        help="Canal utilise par le payeur (OM, MOMO, KULU...), tel que rapporte par Djomy.",
    )

    # === LECTURE DES PAYLOADS DJOMY ==========================================

    @api.model
    def _djomy_payment_data(self, payload):
        """Aplatit un payload Djomy en dict de paiement, quelle que soit sa forme.

        Trois formes arrivent ici : la reponse d'un appel API (deja reduite a
        `data` par le provider), un webhook V1 (`data` = le paiement) et un
        webhook V2 (`data.payment`). On renvoie toujours le dict du paiement,
        vide si le payload est inexploitable.
        """
        if not isinstance(payload, dict):
            return {}
        data = payload.get('data', payload)
        if isinstance(data, dict) and isinstance(data.get('payment'), dict):
            data = data['payment']
        return data if isinstance(data, dict) else {}

    @api.model
    def _djomy_status(self, payload):
        return str(self._djomy_payment_data(payload).get('status') or '').upper()

    # === CREATION DU LIEN (flux portail / eCommerce) ========================

    def _get_specific_rendering_values(self, processing_values):
        """Override of payment to return Djomy-specific rendering values.

        For Djomy, we use a direct flow where the JS captures the phone number
        and calls _djomy_create_payment_link via a custom route. So we don't
        initiate the payment here, just return the reference for the JS to use.

        Note: self.ensure_one() from `_get_processing_values`
        """
        res = super()._get_specific_rendering_values(processing_values)
        if self.provider_code != 'djomy':
            return res

        return {
            'reference': self.reference,
        }

    def _djomy_create_payment_link(self):
        """Cree le lien de paiement Djomy de la transaction et renvoie son URL.

        Pourquoi un lien et non `payments/gateway` : la session de la gateway
        meurt au bout d'une heure, alors qu'un lien vit le temps configure
        sur le provider. Un client qui revient l'apres-midi sur le lien recu
        le matin peut encore payer.

        :return: l'URL de la page de paiement, ou None si Djomy a refuse.
        :rtype: str or None
        """
        self.ensure_one()
        provider = self.provider_id

        base_url = provider.get_base_url()
        return_url = urls.urljoin(base_url, f"{DjomyController._return_url}/{self.reference}")
        cancel_url = urls.urljoin(base_url, f"{DjomyController._cancel_url}/{self.reference}")
        if not base_url.lower().startswith('https://'):
            # Djomy exige des URLs de retour en https (400 sinon). En http
            # (dev local), on n'en envoie pas : le client reste sur la page
            # de statut Djomy et Odoo se met a jour par sync/webhook.
            _logger.warning(
                "Djomy: base URL %s is not https, return/cancel URLs not sent for %s",
                base_url, self.reference,
            )
            return_url = cancel_url = None
        expiry_minutes = provider.djomy_link_expiry_minutes or const.DEFAULT_LINK_EXPIRY_MINUTES
        expires_at = fields.Datetime.now() + timedelta(minutes=expiry_minutes)
        phone = tools.format_phone(
            self.partner_phone, provider._djomy_get_phone_code(self.partner_id),
        )

        _logger.info(
            "Djomy: creating payment link for %s, amount=%s, phone=%s",
            self.reference, self.amount, phone,
        )
        try:
            link = provider._djomy_create_link(
                self.amount, self.reference,
                country_code=provider._djomy_get_country_code(self.partner_id),
                link_name=self.reference,
                description=_("Paiement %s", self.reference),
                usage_type='UNIQUE',
                expires_at=expires_at,
                phone_number=phone,
                send_sms=bool(phone),
                return_url=return_url,
                cancel_url=cancel_url,
                skip_status_page=True,
                metadata={'odoo_reference': self.reference, 'odoo_db': self.env.cr.dbname},
            )
        except ValidationError as error:
            _logger.error("Djomy: API error for %s: %s", self.reference, error)
            self._set_error(str(error))
            return None

        url = link.get('paymentPageUrl') or link.get('link')
        self.write({
            'djomy_payment_link_reference': link.get('paymentLinkReference'),
            'djomy_payment_link_url': url,
        })
        _logger.info("Djomy: payment link %s created for %s", self.djomy_payment_link_reference, self.reference)
        return url

    # === RATTACHEMENT D'UN PAYLOAD A UNE TRANSACTION ========================

    @api.model
    def _extract_reference(self, provider_code, payment_data):
        """Override of `payment` to extract the reference from the payment data."""
        if provider_code != 'djomy':
            return super()._extract_reference(provider_code, payment_data)
        data = self._djomy_payment_data(payment_data)
        return (
            data.get('merchantPaymentReference')
            or (payment_data.get('merchantPaymentReference') if isinstance(payment_data, dict) else None)
        )

    @api.model
    def _search_by_reference(self, provider_code, payment_data):
        """Override : retombe sur le lien de paiement puis sur le transactionId.

        Un webhook peut arriver sans `merchantPaymentReference` exploitable
        (paiement initie hors Odoo sur un lien que nous avons cree) : la
        `paymentLinkReference` portee par l'enveloppe suffit alors a
        retrouver la transaction.
        """
        if provider_code != 'djomy':
            return super()._search_by_reference(provider_code, payment_data)
        tx = super()._search_by_reference(provider_code, payment_data)
        if tx:
            return tx
        data = self._djomy_payment_data(payment_data)
        link_reference = (
            (payment_data.get('paymentLinkReference') if isinstance(payment_data, dict) else None)
            or data.get('paymentLinkReference')
        )
        if link_reference:
            tx = self.search([
                ('provider_code', '=', 'djomy'),
                ('djomy_payment_link_reference', '=', link_reference),
            ], limit=1)
        if not tx and data.get('transactionId'):
            tx = self.search([
                ('provider_code', '=', 'djomy'),
                ('provider_reference', '=', data['transactionId']),
            ], limit=1)
        return tx

    def _extract_amount_data(self, payment_data):
        """Override of `payment` to extract the amount and currency.

        Renvoie None (validation sautee) quand Djomy n'a envoye aucun
        montant : c'est le cas d'un lien expire sans paiement, ou d'un
        evenement intermediaire. Le montant est controle des qu'il est la.
        """
        if self.provider_code != 'djomy':
            return super()._extract_amount_data(payment_data)

        data = self._djomy_payment_data(payment_data)
        raw_amount = data.get('paidAmount', data.get('amount'))
        if raw_amount in (None, ''):
            return None
        return {
            'amount': float(raw_amount),
            'currency_code': data.get('currency') or self.currency_id.name or 'GNF',
        }

    def _apply_updates(self, payment_data):
        """Override of `payment` to update the transaction based on payment data."""
        if self.provider_code != 'djomy':
            return super()._apply_updates(payment_data)

        data = self._djomy_payment_data(payment_data)

        vals = {}
        if data.get('transactionId'):
            vals['provider_reference'] = data['transactionId']
        if data.get('paymentMethod'):
            vals['djomy_payment_method'] = str(data['paymentMethod']).upper()
        link_reference = (
            payment_data.get('paymentLinkReference') if isinstance(payment_data, dict) else None
        ) or data.get('paymentLinkReference')
        if link_reference and not self.djomy_payment_link_reference:
            vals['djomy_payment_link_reference'] = link_reference
        if vals:
            self.write(vals)

        payment_status = str(data.get('status') or '').upper()

        if data.get('inferredFromUsage'):
            _logger.warning(
                "Djomy: %s marked paid from link usage count only (no transactionId "
                "returned by Djomy); reconcile the transactionId manually if needed.",
                self.reference,
            )

        if payment_status in const.PAYMENT_STATUS_MAPPING['pending']:
            self._set_pending()
        elif payment_status in const.PAYMENT_STATUS_MAPPING['done']:
            # `cancel` autorise : un client qui abandonne la page puis paie
            # plus tard via le SMS a bel et bien paye - l'argent est la.
            self._set_done(extra_allowed_states=('cancel',))
        elif payment_status in const.PAYMENT_STATUS_MAPPING['cancel']:
            self._set_canceled(state_message=_(
                "Paiement Djomy termine sans encaissement (statut %s).", payment_status,
            ))
        elif payment_status in const.PAYMENT_STATUS_MAPPING['error']:
            self._set_error(_(
                "An error occurred during the processing of your payment (status %s).",
                payment_status
            ))
        elif payment_status in const.PAYMENT_STATUS_MAPPING['refunded']:
            # Pas de remboursement gere cote Odoo : on trace, on ne change rien.
            _logger.warning(
                "Djomy: transaction %s reported as REFUNDED; refunds are not handled "
                "automatically, please record it manually.", self.reference,
            )
            self._log_message_on_linked_documents(_(
                "Djomy signale ce paiement comme rembourse (transaction %s). "
                "Le remboursement est a enregistrer manuellement.",
                self.provider_reference or self.reference,
            ))
        else:
            _logger.warning(
                "Received data with invalid payment status (%s) for transaction %s.",
                payment_status, self.reference
            )
            self._set_error(_("Unknown payment status: %s", payment_status))

    # === SYNCHRONISATION MANUELLE / CRON ====================================

    def _djomy_fetch_official_status(self, transaction_id=None):
        """Interroge Djomy et renvoie un payload de paiement, ou None.

        Ordre des sources : le `transactionId` connu (le notre, ou celui
        passe en parametre par un retour/webhook, apres controle qu'il
        correspond bien a cette transaction), puis le lien de paiement.
        Un lien mort sans paiement produit un payload minimal au statut du
        lien (EXPIRED/REVOKED) pour que la transaction soit cloturee.
        """
        self.ensure_one()
        provider = self.provider_id
        candidate = self.provider_reference or transaction_id
        if candidate:
            data = self._djomy_payment_data(provider._djomy_get_payment_status(candidate))
            merchant_ref = data.get('merchantPaymentReference')
            if self.provider_reference or not merchant_ref or merchant_ref == self.reference:
                data.setdefault('transactionId', candidate)
                data.setdefault('merchantPaymentReference', self.reference)
                return data
            _logger.warning(
                "Djomy: transactionId %s belongs to %s, not to %s; ignoring it",
                candidate, merchant_ref, self.reference,
            )
        if self.djomy_payment_link_reference:
            link = provider._djomy_get_link(self.djomy_payment_link_reference)
            payment = provider._djomy_pick_link_payment(link)
            if payment:
                payment = dict(payment)
                payment.setdefault('merchantPaymentReference', self.reference)
                payment['paymentLinkReference'] = self.djomy_payment_link_reference
                return payment
            link_status = str(link.get('status') or '').upper()
            if link_status in const.LINK_FINAL_STATUSES:
                return {
                    'status': link_status,
                    'merchantPaymentReference': self.reference,
                    'paymentLinkReference': self.djomy_payment_link_reference,
                }
        return None

    def _djomy_sync_status(self, transaction_id=None):
        """Realigne l'etat de la transaction sur le statut officiel Djomy.

        :return: True si un statut a ete applique, False sinon (rien a
            interroger, ou Djomy injoignable - deja journalise).
        """
        self.ensure_one()
        if self.provider_code != 'djomy':
            return False
        try:
            data = self._djomy_fetch_official_status(transaction_id=transaction_id)
        except ValidationError as error:
            _logger.warning("Djomy: could not fetch status for %s: %s", self.reference, error)
            return False
        if not data:
            return False
        self._process('djomy', data)
        if self.state == 'done' and not self.is_post_processed:
            try:
                self._post_process()
            except Exception as exc:  # noqa: BLE001 - un echec comptable ne doit pas masquer le paiement
                _logger.warning("Djomy: _post_process failed for %s: %s", self.reference, exc)
        return True

    def action_djomy_sync_status(self):
        """Bouton "Synchroniser" du formulaire de transaction."""
        for tx in self.filtered(lambda t: t.provider_code == 'djomy'):
            before = tx.state
            if not tx._djomy_sync_status():
                raise ValidationError(_(
                    "Impossible de recuperer le statut Djomy de %s : aucune reference "
                    "connue ou Djomy injoignable (voir le journal).", tx.reference,
                ))
            _logger.info("Djomy: manual sync of %s: %s -> %s", tx.reference, before, tx.state)
        return True

    @api.model
    def _cron_djomy_sync_pending(self, batch_limit=50):
        """Rattrape les webhooks perdus : resynchronise les tx encore ouvertes.

        Fenetre `djomy.sync_pending_max_hours` (defaut 24 h) : au-dela, le
        lien est expire depuis longtemps et le cron d'annulation a fait son
        travail. Chaque transaction est traitee dans son propre savepoint
        pour qu'une erreur isolee ne bloque pas le lot.
        """
        hours = int(self.env['ir.config_parameter'].sudo().get_param(
            'djomy.sync_pending_max_hours', '24',
        ))
        if hours <= 0:
            return 0
        cutoff = fields.Datetime.now() - timedelta(hours=hours)
        candidates = self.sudo().search([
            ('provider_code', '=', 'djomy'),
            ('state', 'in', ('draft', 'pending')),
            ('create_date', '>=', cutoff),
            '|',
            ('provider_reference', '!=', False),
            ('djomy_payment_link_reference', '!=', False),
        ], limit=batch_limit, order='create_date asc')
        synced = 0
        for tx in candidates:
            try:
                with self.env.cr.savepoint():
                    if tx._djomy_sync_status():
                        synced += 1
            except Exception as exc:  # noqa: BLE001 - journalise et continue
                _logger.warning("Djomy: cron sync failed for %s: %s", tx.reference, exc)
        return synced

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
                "Transaction Djomy %(old)s annulée automatiquement : "
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

    # === CANCEL FORCÉ — débloque le portail quand Djomy reste PENDING ========

    @api.model
    def _cron_djomy_cancel_stale_pending(self, batch_limit=100):
        """Annule de force les tx Djomy `pending` trop anciennes.

        Sur Odoo v16+, le portail masque le bouton "Payer" tant qu'une
        `payment.transaction` en `pending` est rattachée à la facture.
        Si Djomy garde une session en `PENDING` de son côté (bug provider,
        timeout non signalé, coupure serveur Djomy), le client reste
        bloqué : ni bouton Payer, ni bouton Annuler. Ce cron annule
        de force les tx qui dépassent le délai configurable
        `djomy.pending_auto_cancel_minutes` (défaut 120 min = 2h) — le
        portail redevient utilisable et le zombie cleanup nettoie la
        nouvelle tentative.

        Avant d'annuler, une derniere synchronisation est tentee : un
        paiement reellement abouti dont le webhook s'est perdu passe
        `done` au lieu d'etre annule a tort.

        Mettre le paramètre à `0` désactive le cancel.
        """
        minutes = int(self.env['ir.config_parameter'].sudo().get_param(
            'djomy.pending_auto_cancel_minutes', '120',
        ))
        if minutes <= 0:
            return 0
        cutoff = fields.Datetime.now() - timedelta(minutes=minutes)
        # Filter on `create_date` (invariant), not `write_date`: any
        # `write` on a still-`pending` tx (audit log, resync amount,
        # etc.) would push `write_date` forward and the cron would
        # never cancel it. A Djomy tx moves to `pending` seconds after
        # creation (via `_set_pending`), so `create_date` is a reliable
        # proxy for the moment the wait started.
        stale = self.sudo().search([
            ('provider_code', '=', 'djomy'),
            ('state', '=', 'pending'),
            ('create_date', '<', cutoff),
        ], limit=batch_limit, order='create_date asc')
        cancelled = 0
        for tx in stale:
            try:
                with self.env.cr.savepoint():
                    tx._djomy_sync_status()
            except Exception as exc:  # noqa: BLE001
                _logger.warning("Djomy: last-chance sync failed for %s: %s", tx.reference, exc)
            if tx.state != 'pending':
                _logger.info(
                    "Djomy: stale tx=%s resolved by sync (%s), not cancelled",
                    tx.reference, tx.state,
                )
                continue
            _logger.info(
                "Djomy: auto-cancel stale pending tx=%s (pending since %s)",
                tx.reference, tx.create_date,
            )
            tx._set_canceled(state_message=_(
                "Transaction annulée automatiquement : en attente "
                "depuis plus de %s minute(s).",
            ) % minutes)
            cancelled += 1
        return cancelled
