# -*- coding: utf-8 -*-
"""Chantier 3 : rattacher a une facture client un paiement recu hors Odoo (liste par `GET /payments`)."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError
from odoo.tests.common import tagged

from odoo.addons.account.tests.common import AccountTestInvoicingCommon
from odoo.addons.payment_djomy.tests.common import DjomyMockMixin, configure_djomy_provider


@tagged('post_install', '-at_install')
class TestDjomyMatchWizard(DjomyMockMixin, AccountTestInvoicingCommon):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.provider = configure_djomy_provider(
            cls.env, cls.env.company,
            djomy_static_link_reference='LNK-STATIC',
            djomy_static_link_url='https://pay.djomy.africa/l/LNK-STATIC',
        )
        # Meme preparation comptable qu'AccountPaymentCommon : un journal
        # banque et un compte d'attente sur la ligne de methode du fournisseur.
        if not cls.provider.journal_id:
            cls.provider.journal_id = cls.company_data['default_journal_bank']
        line = cls.provider.journal_id.inbound_payment_method_line_ids.filtered(
            lambda l: l.payment_provider_id == cls.provider
        )
        line.payment_account_id = cls.inbound_payment_method_line.payment_account_id
        cls.Wizard = cls.env['djomy.payment.match.wizard']

    # --- Helpers --------------------------------------------------------------

    def _invoice(self, amount=100.0, post=True):
        return self.init_invoice(
            'out_invoice', partner=self.partner_a, amounts=[amount],
            taxes=self.env['account.tax'], post=post,
        )

    def _wizard(self, move, **vals):
        context = move.action_djomy_match_payment()['context']
        return self.Wizard.with_context(context).create(vals)

    def _payment(self, transaction_id, amount, status='SUCCESS', currency=None, **extra):
        data = {
            'transactionId': transaction_id, 'status': status, 'paidAmount': amount,
            'currency': currency or self.env.company.currency_id.name,
            'paymentMethod': 'OM', 'payerIdentifier': '00224622000001',
            'createdAt': fields.Datetime.now().strftime('%Y-%m-%dT%H:%M:%SZ'),
        }
        data.update(extra)
        return data

    def _link(self, reference, payments):
        return {'paymentLinkReference': reference, 'status': 'ACTIVE', 'payments': payments}

    def _page(self, payments, last=True):
        """Une page Spring de `GET /payments`."""
        return {'content': payments, 'last': last, 'totalPages': 1 if last else 2}

    # --- Ouverture et chargement -----------------------------------------------

    def test_action_open_defaults(self):
        move = self._invoice(100.0)
        action = move.action_djomy_match_payment()
        context = action['context']
        self.assertEqual(action['res_model'], 'djomy.payment.match.wizard')
        self.assertEqual(context['default_res_model'], 'account.move')
        self.assertEqual(context['default_res_id'], move.id)
        self.assertEqual(context['default_expected_amount'], move.amount_residual)
        self.assertEqual(context['default_link_reference'], 'LNK-STATIC')
        self.assertEqual(context['default_source'], 'all')
        wizard = self._wizard(move)
        self.assertEqual(wizard.target_name, move.display_name)
        self.assertEqual(wizard.provider_name, self.provider.name)

    def test_refresh_all_payments(self):
        move = self._invoice(100.0)
        wizard = self._wizard(move)
        payments = [
            self._payment('T-A', 100.0, paymentLinkReference='LNK-STATIC'),
            self._payment('T-B', 50.0, status='FAILED'),
        ]
        with self._mock_api({('GET', 'payments'): self._page(payments)}) as api:
            action = wizard.action_refresh()
        params = api.params('payments')
        self.assertEqual(params['startDate'], wizard.date_from.strftime('%Y-%m-%d'))
        self.assertEqual(params['endDate'], fields.Datetime.now().strftime('%Y-%m-%d'))
        self.assertEqual(action['res_id'], wizard.id)
        self.assertEqual(len(wizard.line_ids), 2)
        done = wizard.line_ids.filtered(lambda l: l.transaction_id == 'T-A')
        self.assertTrue(done.is_success)
        self.assertEqual(done.paid_amount, 100.0)
        self.assertEqual(done.payment_method_label, 'Orange Money')
        self.assertEqual(done.link_reference, 'LNK-STATIC')
        self.assertFalse(done.matched_on)
        self.assertFalse(wizard.line_ids.filtered(lambda l: l.transaction_id == 'T-B').is_success)
        self.assertIn('2', wizard.note)

    def test_refresh_period_filter(self):
        move = self._invoice(100.0)
        wizard = self._wizard(move, date_from=fields.Datetime.now() - timedelta(hours=2))
        old = (fields.Datetime.now() - timedelta(days=2)).strftime('%Y-%m-%dT%H:%M:%SZ')
        payments = [self._payment('T-NEW', 100.0), self._payment('T-OLD', 100.0, createdAt=old)]
        with self._mock_api({('GET', 'payments'): self._page(payments)}):
            wizard.action_refresh()
        self.assertEqual(wizard.line_ids.mapped('transaction_id'), ['T-NEW'],
                         "l'API filtre au jour, le wizard a l'heure")

    def test_refresh_all_payments_paginates(self):
        move = self._invoice(100.0)
        wizard = self._wizard(move, max_pages=3)
        pages = {
            0: self._page([self._payment(f'T-{i}', 1.0) for i in range(100)], last=False),
            1: self._page([self._payment('T-100', 100.0)], last=True),
        }
        with self._mock_api({('GET', 'payments'): lambda params, **kw: pages[params['page']]}) as api:
            wizard.action_refresh()
        self.assertEqual(len(wizard.line_ids), 101)
        self.assertEqual(len(api.calls_to('payments', 'GET')), 2, "page pleine : on demande la suivante")
        self.assertEqual(api.params('payments')['page'], 1)

    def test_refresh_all_payments_respects_max_pages(self):
        move = self._invoice(100.0)
        wizard = self._wizard(move, max_pages=2)
        full = self._page([self._payment(f'T-{i}', 1.0) for i in range(100)], last=False)
        with self._mock_api({('GET', 'payments'): lambda params, **kw: {
            **full, 'content': [dict(p, transactionId=f"{p['transactionId']}-P{params['page']}") for p in full['content']],
        }}) as api:
            wizard.action_refresh()
        self.assertEqual(len(wizard.line_ids), 200)
        self.assertEqual(len(api.calls_to('payments', 'GET')), 2, "borne par max_pages")

    def test_refresh_link_source(self):
        """Un lien precis : lecture par `GET /links/{ref}`, prerempli avec le lien statique."""
        move = self._invoice(100.0)
        wizard = self._wizard(move, source='link')
        self.assertEqual(wizard.link_reference, 'LNK-STATIC')
        payments = [self._payment('T-L', 100.0)]
        with self._mock_api({('GET', 'links/LNK-STATIC'): self._link('LNK-STATIC', payments)}) as api:
            wizard.action_refresh()
        self.assertEqual(wizard.line_ids.mapped('transaction_id'), ['T-L'])
        self.assertEqual(wizard.line_ids.link_reference, 'LNK-STATIC')
        self.assertFalse(api.calls_to('payments', 'GET'))

    def test_refresh_link_source_without_reference(self):
        self.provider.djomy_static_link_reference = False
        move = self._invoice(100.0)
        wizard = self._wizard(move, source='link', link_reference=False)
        with self._mock_api({}):
            with self.assertRaises(UserError):
                wizard.action_refresh()

    def test_refresh_api_down_is_user_error(self):
        from odoo.exceptions import ValidationError
        move = self._invoice(100.0)
        wizard = self._wizard(move)
        with self._mock_api({('GET', 'payments'): ValidationError("down")}):
            with self.assertRaises(UserError):
                wizard.action_refresh()

    # --- Rattachement ----------------------------------------------------------

    def _load_and_match(self, wizard, transaction_id, amount, verify=None):
        payments = [self._payment(transaction_id, amount)]
        with self._mock_api({('GET', 'payments'): self._page(payments)}):
            wizard.action_refresh()
        line = wizard.line_ids.filtered(lambda l: l.transaction_id == transaction_id)
        routes = {('GET', f'payments/{transaction_id}/status'): verify or self._payment(transaction_id, amount)}
        with self._mock_api(routes) as api:
            result = line.action_match()
        return line, result, api

    def test_match_full_payment_pays_invoice(self):
        move = self._invoice(100.0)
        residual = move.amount_residual
        wizard = self._wizard(move)
        line, result, api = self._load_and_match(wizard, 'T-FULL', residual)
        self.assertEqual(result['tag'], 'soft_reload')
        self.assertEqual(len(api.calls_to('payments/T-FULL/status')), 1, "re-verification avant rattachement")
        tx = self.env['payment.transaction'].search([('provider_reference', '=', 'T-FULL')])
        self.assertEqual(len(tx), 1)
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.amount, residual)
        self.assertEqual(tx.operation, 'offline')
        self.assertEqual(tx.djomy_payment_method, 'OM')
        self.assertIn(move, tx.invoice_ids)
        self.assertTrue(tx.payment_id, "un account.payment a ete genere")
        move.invalidate_recordset()
        self.assertEqual(move.amount_residual, 0.0)
        self.assertIn(move.payment_state, ('paid', 'in_payment'))
        self.assertTrue(any('T-FULL' in (m.body or '') for m in move.message_ids))

    def test_match_partial_payment(self):
        move = self._invoice(100.0)
        wizard = self._wizard(move)
        self._load_and_match(wizard, 'T-PART', 40.0)
        move.invalidate_recordset()
        self.assertEqual(move.payment_state, 'partial')
        self.assertAlmostEqual(move.amount_residual, 60.0)

    def test_match_refuses_already_matched(self):
        move = self._invoice(100.0)
        self._load_and_match(self._wizard(move), 'T-TWICE', 40.0)
        other = self._invoice(100.0)
        wizard = self._wizard(other)
        payments = [self._payment('T-TWICE', 40.0)]
        with self._mock_api({('GET', 'payments'): self._page(payments)}):
            wizard.action_refresh()
        line = wizard.line_ids
        self.assertEqual(line.matched_on, move.name)
        with self._mock_api({}):
            with self.assertRaises(UserError):
                line.action_match()

    def test_match_refuses_over_residual(self):
        move = self._invoice(100.0)
        wizard = self._wizard(move)
        with self.assertRaises(UserError):
            self._load_and_match(wizard, 'T-BIG', move.amount_residual + 1)
        self.assertFalse(self.env['payment.transaction'].search([('provider_reference', '=', 'T-BIG')]))

    def test_match_refuses_not_successful(self):
        move = self._invoice(100.0)
        wizard = self._wizard(move)
        with self.assertRaises(UserError):
            self._load_and_match(wizard, 'T-PEND', 100.0, verify=self._payment('T-PEND', 100.0, status='PENDING'))

    def test_match_refuses_currency_mismatch(self):
        move = self._invoice(100.0)
        wizard = self._wizard(move)
        with self.assertRaises(UserError):
            self._load_and_match(wizard, 'T-CUR', 100.0, verify=self._payment('T-CUR', 100.0, currency='XXX'))

    def test_match_refuses_draft_invoice(self):
        move = self._invoice(100.0, post=False)
        wizard = self.Wizard.with_context(
            default_res_model='account.move', default_res_id=move.id,
            default_expected_amount=100.0, default_currency_id=move.currency_id.id,
            default_link_reference='LNK-STATIC',
        ).create({})
        with self.assertRaises(UserError):
            self._load_and_match(wizard, 'T-DRAFT', 100.0)

    def test_match_api_unreachable(self):
        from odoo.exceptions import ValidationError
        move = self._invoice(100.0)
        wizard = self._wizard(move)
        with self.assertRaises(UserError):
            self._load_and_match(wizard, 'T-DOWN', 100.0, verify=ValidationError("down"))
