# -*- coding: utf-8 -*-
"""Chantier 4 : synchronisation manuelle et crons de reconciliation/annulation."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from odoo.addons.payment_djomy.tests.common import DjomyCase


@tagged('post_install', '-at_install')
class TestDjomySync(DjomyCase):

    def _age(self, tx, **delta):
        """Vieillit `create_date` (le cron filtre dessus)."""
        self.env.cr.execute(
            "UPDATE payment_transaction SET create_date = %s WHERE id = %s",
            (fields.Datetime.now() - timedelta(**delta), tx.id),
        )
        tx.invalidate_recordset()

    # --- _djomy_sync_status -------------------------------------------------

    def test_sync_by_provider_reference(self):
        tx = self._tx('SYNC-1', state='pending', provider_reference='TX-1')
        with self._mock_api({('GET', 'payments/TX-1/status'): self._payment(tx)}):
            self.assertTrue(tx._djomy_sync_status())
        self.assertEqual(tx.state, 'done')
        self.assertTrue(tx.is_post_processed)

    def test_sync_by_link(self):
        tx = self._tx('SYNC-2', state='pending', djomy_payment_link_reference='LNK-2')
        link = self._link('LNK-2', payments=[self._payment(tx, transaction_id='TX-2', merchant_reference='')])
        with self._mock_api({('GET', 'links/LNK-2'): link}) as api:
            self.assertTrue(tx._djomy_sync_status())
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.provider_reference, 'TX-2')
        self.assertFalse(api.calls_to('payments/TX-2/status'))

    def test_sync_by_link_usage_count_only(self):
        """Reponse sandbox : ENABLED + numberOfUsage, sans detail des paiements."""
        tx = self._tx('SYNC-2b', state='pending', djomy_payment_link_reference='LNK-2b')
        link = {'paymentLinkReference': 'LNK-2b', 'status': 'ENABLED', 'usageType': 'UNIQUE', 'numberOfUsage': 1}
        with self._mock_api({('GET', 'links/LNK-2b'): link}):
            self.assertTrue(tx._djomy_sync_status())
        self.assertEqual(tx.state, 'done')
        self.assertFalse(tx.provider_reference, "aucun transactionId connu")

    def test_sync_enabled_link_unused_stays_pending(self):
        tx = self._tx('SYNC-2c', state='pending', djomy_payment_link_reference='LNK-2c')
        link = {'paymentLinkReference': 'LNK-2c', 'status': 'ENABLED', 'usageType': 'UNIQUE', 'numberOfUsage': 0}
        with self._mock_api({('GET', 'links/LNK-2c'): link}):
            self.assertFalse(tx._djomy_sync_status())
        self.assertEqual(tx.state, 'pending')

    def test_sync_expired_link_without_payment_cancels(self):
        tx = self._tx('SYNC-3', state='pending', djomy_payment_link_reference='LNK-3')
        with self._mock_api({('GET', 'links/LNK-3'): self._link('LNK-3', status='EXPIRED')}):
            self.assertTrue(tx._djomy_sync_status())
        self.assertEqual(tx.state, 'cancel')

    def test_sync_active_link_without_payment_does_nothing(self):
        tx = self._tx('SYNC-4', state='pending', djomy_payment_link_reference='LNK-4')
        with self._mock_api({('GET', 'links/LNK-4'): self._link('LNK-4')}):
            self.assertFalse(tx._djomy_sync_status())
        self.assertEqual(tx.state, 'pending')

    def test_sync_without_any_reference(self):
        tx = self._tx('SYNC-5', state='pending')
        with self._mock_api({}):
            self.assertFalse(tx._djomy_sync_status())

    def test_sync_foreign_transaction_id_is_ignored(self):
        """Un transactionId qui appartient a une autre reference ne doit pas
        valider cette transaction (URL de retour forgee ou melangee)."""
        tx = self._tx('SYNC-6', state='pending', djomy_payment_link_reference='LNK-6')
        with self._mock_api({
            ('GET', 'payments/EVIL/status'): self._payment(tx, transaction_id='EVIL', merchant_reference='SOMEONE-ELSE'),
            ('GET', 'links/LNK-6'): self._link('LNK-6'),
        }):
            self.assertFalse(tx._djomy_sync_status(transaction_id='EVIL'))
        self.assertEqual(tx.state, 'pending')
        self.assertFalse(tx.provider_reference)

    def test_sync_given_transaction_id_is_trusted_when_it_matches(self):
        tx = self._tx('SYNC-7', state='pending', djomy_payment_link_reference='LNK-7')
        with self._mock_api({('GET', 'payments/TX-7/status'): self._payment(tx, transaction_id='TX-7')}):
            self.assertTrue(tx._djomy_sync_status(transaction_id='TX-7'))
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.provider_reference, 'TX-7')

    def test_sync_unreachable_api(self):
        tx = self._tx('SYNC-8', state='pending', provider_reference='TX-8')
        with self._mock_api({('GET', 'payments/TX-8/status'): ValidationError("down")}):
            self.assertFalse(tx._djomy_sync_status())
        self.assertEqual(tx.state, 'pending')

    def test_action_sync_raises_when_nothing_to_sync(self):
        tx = self._tx('SYNC-9', state='pending')
        with self._mock_api({}):
            with self.assertRaises(ValidationError):
                tx.action_djomy_sync_status()

    def test_action_sync_applies_status(self):
        tx = self._tx('SYNC-10', state='pending', provider_reference='TX-10')
        with self._mock_api({('GET', 'payments/TX-10/status'): self._payment(tx, transaction_id='TX-10', status='FAILED')}):
            tx.action_djomy_sync_status()
        self.assertEqual(tx.state, 'error')

    # --- Cron de synchronisation -------------------------------------------

    def test_cron_sync_pending(self):
        paid = self._tx('CRON-A', state='pending', djomy_payment_link_reference='LNK-A')
        no_ref = self._tx('CRON-B', state='draft')
        still = self._tx('CRON-C', state='pending', provider_reference='TX-C')
        old = self._tx('CRON-D', state='pending', provider_reference='TX-D')
        self._age(old, days=2)
        done_already = self._tx('CRON-E', state='done', provider_reference='TX-E')
        routes = {
            ('GET', 'links/LNK-A'): self._link('LNK-A', payments=[self._payment(paid, transaction_id='TX-A')]),
            ('GET', 'payments/TX-C/status'): self._payment(still, transaction_id='TX-C', status='PENDING'),
        }
        with self._mock_api(routes) as api:
            synced = self.env['payment.transaction']._cron_djomy_sync_pending()
        self.assertEqual(synced, 2)
        self.assertEqual(paid.state, 'done')
        self.assertEqual(no_ref.state, 'draft')
        self.assertEqual(still.state, 'pending')
        self.assertEqual(old.state, 'pending', "hors fenetre : pas interrogee")
        self.assertEqual(done_already.state, 'done')
        self.assertFalse(api.calls_to('payments/TX-D/status'))
        self.assertFalse(api.calls_to('payments/TX-E/status'))

    def test_cron_sync_survives_one_failure(self):
        broken = self._tx('CRON-F', state='pending', provider_reference='TX-F')
        fine = self._tx('CRON-G', state='pending', provider_reference='TX-G')
        with self._mock_api({
            ('GET', 'payments/TX-F/status'): RuntimeError("unexpected"),
            ('GET', 'payments/TX-G/status'): self._payment(fine, transaction_id='TX-G'),
        }):
            synced = self.env['payment.transaction']._cron_djomy_sync_pending()
        self.assertEqual(synced, 1)
        self.assertEqual(broken.state, 'pending')
        self.assertEqual(fine.state, 'done')

    def test_cron_sync_disabled_by_parameter(self):
        self.env['ir.config_parameter'].sudo().set_param('djomy.sync_pending_max_hours', '0')
        tx = self._tx('CRON-H', state='pending', provider_reference='TX-H')
        with self._mock_api({}) as api:
            self.assertEqual(self.env['payment.transaction']._cron_djomy_sync_pending(), 0)
        self.assertFalse(api.calls)
        self.assertEqual(tx.state, 'pending')

    # --- Cron d'annulation --------------------------------------------------

    def test_cancel_cron_keeps_transactions_paid_meanwhile(self):
        paid = self._tx('STALE-A', state='pending', djomy_payment_link_reference='LNK-SA')
        unpaid = self._tx('STALE-B', state='pending', provider_reference='TX-SB')
        unreachable = self._tx('STALE-C', state='pending', provider_reference='TX-SC')
        recent = self._tx('STALE-D', state='pending', provider_reference='TX-SD')
        for tx in (paid, unpaid, unreachable):
            self._age(tx, hours=3)
        with self._mock_api({
            ('GET', 'links/LNK-SA'): self._link('LNK-SA', payments=[self._payment(paid, transaction_id='TX-SA')]),
            ('GET', 'payments/TX-SB/status'): self._payment(unpaid, transaction_id='TX-SB', status='PENDING'),
            ('GET', 'payments/TX-SC/status'): ValidationError("down"),
        }):
            cancelled = self.env['payment.transaction']._cron_djomy_cancel_stale_pending()
        self.assertEqual(cancelled, 2)
        self.assertEqual(paid.state, 'done', "paye entre-temps : ne doit pas etre annule")
        self.assertEqual(unpaid.state, 'cancel')
        self.assertEqual(unreachable.state, 'cancel', "Djomy injoignable : le portail doit etre debloque")
        self.assertEqual(recent.state, 'pending')
