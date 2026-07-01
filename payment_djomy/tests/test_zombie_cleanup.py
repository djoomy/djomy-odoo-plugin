# -*- coding: utf-8 -*-
"""Tests du cleanup auto des transactions zombies (`draft`/`pending`).

À chaque nouvelle tx Djomy sur une SO/facture, on annule automatiquement
les précédentes tx Djomy `draft`/`pending` du même périmètre — sans toucher
aux états finaux ni aux autres providers.
"""
from unittest.mock import MagicMock, patch

from odoo.tests.common import TransactionCase, tagged


@tagged('post_install', '-at_install')
class TestDjomyZombieCleanup(TransactionCase):

    def setUp(self):
        super().setUp()
        self.provider = self.env.ref('payment_djomy.payment_provider_djomy')
        self.provider.write({
            'djomy_client_id': 'ci_test',
            'djomy_client_secret': 'sec_test',
            'state': 'test',
        })
        self.method_djomy = self.env.ref('payment_djomy.payment_method_djomy')
        self.partner = self.env['res.partner'].create({
            'name': 'Client Test', 'phone': '+224622000001',
        })
        Product = self.env['product.template']
        self.product = Product.create({
            'name': 'Forfait Test', 'type': 'service',
            'list_price': 5000,
        })

    # --- Helpers --------------------------------------------------------

    def _so(self, name='S-TEST'):
        return self.env['sale.order'].create({
            'partner_id': self.partner.id,
            'order_line': [(0, 0, {
                'product_id': self.product.product_variant_id.id,
                'product_uom_qty': 1,
            })],
        })

    def _tx(self, ref, so=None, state='draft', provider=None, method=None):
        provider = provider or self.provider
        method = method or provider.payment_method_ids[:1] or self.method_djomy
        vals = {
            'reference': ref, 'amount': 5000,
            'currency_id': self.env.company.currency_id.id,
            'provider_id': provider.id,
            'payment_method_id': method.id,
            'partner_id': self.partner.id,
            'state': state,
        }
        if so:
            vals['sale_order_ids'] = [(6, 0, [so.id])]
        return self.env['payment.transaction'].create(vals)

    # --- Cas nominal : la nouvelle tx supersede les anciennes -----------

    def test_new_tx_cancels_previous_draft_on_same_so(self):
        so = self._so()
        old = self._tx('OLD-DRAFT', so=so, state='draft')
        self.assertEqual(old.state, 'draft')
        # Création d'une nouvelle tx sur la même SO → l'ancienne doit passer cancel.
        new = self._tx('NEW-TX', so=so, state='draft')
        old.invalidate_recordset()
        self.assertEqual(old.state, 'cancel')
        self.assertEqual(new.state, 'draft')

    def test_new_tx_cancels_previous_pending_on_same_so(self):
        so = self._so()
        old = self._tx('OLD-PENDING', so=so, state='pending')
        new = self._tx('NEW-TX', so=so, state='draft')
        old.invalidate_recordset()
        self.assertEqual(old.state, 'cancel')
        self.assertEqual(new.state, 'draft')

    # --- Garde-fous : ne touche pas aux états finaux --------------------

    def test_done_tx_is_never_cancelled(self):
        so = self._so()
        done = self._tx('DONE-TX', so=so, state='done')
        new = self._tx('NEW-TX', so=so, state='draft')
        done.invalidate_recordset()
        self.assertEqual(done.state, 'done')

    def test_already_cancelled_tx_is_left_alone(self):
        so = self._so()
        cancelled = self._tx('CANCELLED-TX', so=so, state='cancel')
        new = self._tx('NEW-TX', so=so, state='draft')
        cancelled.invalidate_recordset()
        self.assertEqual(cancelled.state, 'cancel')

    def test_error_tx_is_left_alone(self):
        so = self._so()
        errored = self._tx('ERROR-TX', so=so, state='error')
        new = self._tx('NEW-TX', so=so, state='draft')
        errored.invalidate_recordset()
        self.assertEqual(errored.state, 'error')

    # --- Isolation : autres SO et autres providers ----------------------

    def test_tx_on_other_so_is_not_touched(self):
        so_a = self._so('SO-A')
        so_b = self._so('SO-B')
        unrelated = self._tx('OTHER-SO-DRAFT', so=so_a, state='draft')
        new = self._tx('NEW-TX', so=so_b, state='draft')
        unrelated.invalidate_recordset()
        self.assertEqual(unrelated.state, 'draft',
                         "tx d'une autre SO ne doit jamais être impactée")

    def test_non_djomy_tx_is_not_touched(self):
        so = self._so()
        # Provider natif Odoo `transfer` (toujours présent en base).
        transfer = self.env.ref('payment.payment_provider_transfer')
        other_provider = self._tx(
            'TRANSFER-DRAFT', so=so, state='draft', provider=transfer,
        )
        new = self._tx('NEW-DJOMY-TX', so=so, state='draft')
        other_provider.invalidate_recordset()
        self.assertEqual(other_provider.state, 'draft',
                         "tx d'un autre provider ne doit jamais être impactée")

    # --- account.payment associé est cancel lui aussi -------------------

    def test_stale_account_payment_is_cancelled(self):
        """L'`account.payment` draft attaché à une tx superseded doit recevoir
        `action_cancel()`. On mocke la recherche pour éviter la config
        comptable lourde (journal + outstanding accounts) requise par
        ``account.payment.create()`` en Odoo 19.
        """
        so = self._so()
        old = self._tx('OLD-WITH-PAYMENT', so=so, state='pending')
        fake_payment = MagicMock()
        fake_payments = [fake_payment]

        AccountPayment = self.env['account.payment'].__class__
        original_search = AccountPayment.search

        def fake_search(self_model, domain, *args, **kwargs):
            if any(isinstance(d, (list, tuple)) and d
                   and d[0] == 'payment_transaction_id' for d in domain):
                return fake_payments
            return original_search(self_model, domain, *args, **kwargs)

        with patch.object(AccountPayment, 'search', fake_search):
            self._tx('NEW-TX', so=so, state='draft')

        old.invalidate_recordset()
        self.assertEqual(old.state, 'cancel')
        fake_payment.action_cancel.assert_called_once()
