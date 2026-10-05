# -*- coding: utf-8 -*-
"""Chantier 3 (caisse) : rattacher a une commande POS un paiement recu hors Odoo (`GET /payments`)."""

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from odoo.addons.point_of_sale.tests.common import TestPoSCommon
from odoo.addons.payment_djomy.tests.common import DjomyMockMixin, configure_djomy_provider


@tagged('post_install', '-at_install')
class TestDjomyMatchPosOrder(DjomyMockMixin, TestPoSCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Pas `_create_basic_config()` : il recree un journal `CSH <id>` que
        # `pos.config.create` a deja genere, et heurte la contrainte d'unicite.
        cls.config = cls.env['pos.config'].create({'name': 'Caisse Djomy'})
        cls.provider = configure_djomy_provider(
            cls.env, cls.env.company, djomy_static_link_reference='LNK-STATIC',
        )
        cls.djomy_pm = cls.env['pos.payment.method'].create({
            'name': 'Djomy',
            'use_payment_terminal': 'djomy',
            'payment_method_type': 'terminal',
            'journal_id': cls.company_data['default_journal_bank'].id,
            'receivable_account_id': cls.pos_receivable_bank.id,
            'company_id': cls.env.company.id,
        })
        cls.config.write({'payment_method_ids': [(4, cls.djomy_pm.id)]})
        cls.config.open_ui()
        cls.session = cls.config.current_session_id
        cls.product = cls.env['product.product'].create({
            'name': 'Plat du jour', 'type': 'consu', 'list_price': 100.0,
            'available_in_pos': True, 'taxes_id': [(6, 0, [])],
        })
        cls.client = cls.env['res.partner'].create({'name': 'Client caisse'})
        cls.Wizard = cls.env['djomy.payment.match.wizard']

    # --- Helpers --------------------------------------------------------------

    def _order(self, amount=100.0):
        return self.env['pos.order'].create({
            'company_id': self.env.company.id,
            'session_id': self.session.id,
            'partner_id': self.client.id,
            'lines': [(0, 0, {
                'name': 'Plat du jour', 'product_id': self.product.id,
                'price_unit': amount, 'qty': 1, 'discount': 0,
                'price_subtotal': amount, 'price_subtotal_incl': amount,
                'tax_ids': [(6, 0, [])],
            })],
            'amount_tax': 0, 'amount_total': amount, 'amount_paid': 0, 'amount_return': 0,
        })

    def _wizard(self, order, **vals):
        context = order.action_djomy_match_payment()['context']
        return self.Wizard.with_context(context).create(vals)

    def _payment(self, transaction_id, amount, status='SUCCESS', currency=None, **extra):
        data = {
            'transactionId': transaction_id, 'status': status, 'paidAmount': amount,
            'currency': currency or self.env.company.currency_id.name,
            'paymentMethod': 'MOMO', 'payerIdentifier': '00224622000001',
            'merchantPaymentReference': 'STATIC',
            'createdAt': fields.Datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
        }
        data.update(extra)
        return data

    def _page(self, payments):
        return {'content': payments, 'last': True}

    def _load_and_match(self, wizard, transaction_id, amount, verify=None):
        with self._mock_api({('GET', 'payments'): self._page([self._payment(transaction_id, amount)])}):
            wizard.action_refresh()
        line = wizard.line_ids.filtered(lambda l: l.transaction_id == transaction_id)
        with self._mock_api({('GET', f'payments/{transaction_id}/status'): verify or self._payment(transaction_id, amount)}):
            return line.action_match()

    # --- Tests ------------------------------------------------------------------

    def test_action_open_defaults(self):
        order = self._order(100.0)
        context = order.action_djomy_match_payment()['context']
        self.assertEqual(context['default_res_model'], 'pos.order')
        self.assertEqual(context['default_res_id'], order.id)
        self.assertEqual(context['default_expected_amount'], 100.0)
        self.assertEqual(context['default_link_reference'], 'LNK-STATIC', "sans QR de boutique : le QR global")
        self.config.djomy_static_link_reference = 'LNK-SHOP'
        context = order.action_djomy_match_payment()['context']
        self.assertEqual(context['default_link_reference'], 'LNK-SHOP', "le QR de la boutique de la commande")

    def test_match_full_payment_marks_order_paid(self):
        order = self._order(100.0)
        self._load_and_match(self._wizard(order), 'T-POS-1', 100.0)
        self.assertEqual(order.state, 'paid')
        self.assertEqual(order.amount_paid, 100.0)
        payment = order.payment_ids
        self.assertEqual(len(payment), 1)
        self.assertEqual(payment.payment_method_id, self.djomy_pm)
        self.assertEqual(payment.transaction_id, 'T-POS-1')
        self.assertEqual(payment.payment_status, 'done')
        self.assertEqual(payment.payment_method_payment_mode, 'MOMO')
        self.assertEqual(payment.payment_ref_no, 'STATIC')
        self.assertTrue(any('T-POS-1' in (m.body or '') for m in order.message_ids))

    def test_match_partial_keeps_order_draft(self):
        order = self._order(100.0)
        self._load_and_match(self._wizard(order), 'T-POS-2', 40.0)
        self.assertEqual(order.state, 'draft')
        self.assertEqual(order.amount_paid, 40.0)

    def test_duplicate_transaction_refused(self):
        first = self._order(100.0)
        self._load_and_match(self._wizard(first), 'T-DUP', 100.0)
        second = self._order(100.0)
        wizard = self._wizard(second)
        with self._mock_api({('GET', 'payments'): self._page([self._payment('T-DUP', 100.0)])}):
            wizard.action_refresh()
        self.assertEqual(wizard.line_ids.matched_on, first.name)
        with self._mock_api({}):
            with self.assertRaises(UserError):
                wizard.line_ids.action_match()
        # Et la contrainte de modele bloque aussi une creation directe.
        with self.assertRaises(ValidationError):
            second.add_payment({
                'pos_order_id': second.id, 'payment_method_id': self.djomy_pm.id,
                'amount': 100.0, 'transaction_id': 'T-DUP',
            })
        self.assertEqual(second.amount_paid, 0.0)

    def test_over_remaining_refused(self):
        order = self._order(100.0)
        with self.assertRaises(UserError):
            self._load_and_match(self._wizard(order), 'T-BIG', 101.0)
        self.assertFalse(order.payment_ids)

    def test_not_successful_refused(self):
        order = self._order(100.0)
        with self.assertRaises(UserError):
            self._load_and_match(self._wizard(order), 'T-PEND', 100.0,
                                 verify=self._payment('T-PEND', 100.0, status='PENDING'))

    def test_paid_order_refused(self):
        order = self._order(100.0)
        self._load_and_match(self._wizard(order), 'T-ONE', 100.0)
        with self.assertRaises(UserError):
            self._load_and_match(self._wizard(order), 'T-TWO', 100.0)

    def test_no_djomy_method_in_config_refused(self):
        """Caisse sans mode Djomy : refus explicite, Odoo n'accepterait pas le paiement."""
        other_config = self.env['pos.config'].create({'name': 'Caisse sans Djomy'})
        # une caisse neuve herite des modes de la societe : on retire Djomy avant d'ouvrir
        other_config.write({'payment_method_ids': [(3, self.djomy_pm.id)]})
        other_config.open_ui()
        order = self._order(100.0)
        order.session_id = other_config.current_session_id
        with self.assertRaises(UserError):
            self._load_and_match(self._wizard(order), 'T-FALL', 100.0)
        self.assertFalse(order.payment_ids)
