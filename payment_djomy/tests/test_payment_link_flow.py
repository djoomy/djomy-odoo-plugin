# -*- coding: utf-8 -*-
"""Chantier 1 : le portail cree un lien de paiement, plus une session gateway."""

from datetime import timedelta

from odoo import fields
from odoo.exceptions import ValidationError
from odoo.tests.common import tagged

from odoo.addons.payment_djomy.tests.common import DjomyCase


@tagged('post_install', '-at_install')
class TestDjomyPaymentLinkFlow(DjomyCase):

    def test_create_payment_link_calls_links_not_gateway(self):
        self.env['ir.config_parameter'].sudo().set_param('web.base.url', 'https://shop.example.com')
        tx = self._tx('S00042-1')
        with self._mock_api({('POST', 'links'): self._link('LNK-42')}) as api:
            url = tx._djomy_create_payment_link()
        self.assertEqual(url, 'https://pay.djomy.africa/l/LNK-42')
        self.assertEqual(tx.djomy_payment_link_reference, 'LNK-42')
        self.assertEqual(tx.djomy_payment_link_url, url)
        self.assertFalse(tx.provider_reference, "pas de transactionId avant paiement")
        self.assertFalse(api.calls_to('payments/gateway'))
        payload = api.payload('links')
        self.assertEqual(payload['usageType'], 'UNIQUE')
        self.assertEqual(payload['merchantReference'], tx.reference)
        self.assertEqual(payload['amountToPay'], 5000)
        self.assertEqual(payload['phoneNumber'], '00224622000001')
        self.assertTrue(payload['sendSms'])
        self.assertTrue(payload['skipDjomyStatusPage'])
        self.assertEqual(payload['returnUrl'], 'https://shop.example.com/payment/djomy/return/' + tx.reference)
        self.assertEqual(payload['cancelUrl'], 'https://shop.example.com/payment/djomy/cancel/' + tx.reference)
        self.assertEqual(payload['metadata']['odoo_reference'], tx.reference)
        expires = fields.Datetime.from_string(payload['expiresAt'].replace('T', ' ').rstrip('Z'))
        delta = expires - fields.Datetime.now()
        self.assertAlmostEqual(delta.total_seconds() / 60, 60, delta=2)

    def test_http_base_url_sends_no_return_urls(self):
        """Djomy refuse les URLs de retour en http : en dev local on ne les envoie pas."""
        self.env['ir.config_parameter'].sudo().set_param('web.base.url', 'http://djomy-demo.odookonect.local:8090')
        tx = self._tx('S00046-1')
        with self._mock_api({('POST', 'links'): self._link('LNK-46')}) as api:
            self.assertTrue(tx._djomy_create_payment_link())
        payload = api.payload('links')
        self.assertNotIn('returnUrl', payload)
        self.assertNotIn('cancelUrl', payload)

    def test_link_expiry_follows_provider_setting(self):
        self.provider.djomy_link_expiry_minutes = 240
        tx = self._tx('S00043-1')
        with self._mock_api({('POST', 'links'): self._link()}) as api:
            tx._djomy_create_payment_link()
        expires = fields.Datetime.from_string(api.payload('links')['expiresAt'].replace('T', ' ').rstrip('Z'))
        self.assertAlmostEqual((expires - fields.Datetime.now()).total_seconds() / 60, 240, delta=2)

    def test_link_without_phone_sends_no_sms(self):
        silent = self.env['res.partner'].create({'name': 'Sans telephone'})
        tx = self._tx('S00044-1', partner_id=silent.id, partner_phone=False)
        with self._mock_api({('POST', 'links'): self._link()}) as api:
            tx._djomy_create_payment_link()
        payload = api.payload('links')
        self.assertNotIn('phoneNumber', payload)
        self.assertNotIn('sendSms', payload)

    def test_link_api_error_sets_tx_in_error(self):
        tx = self._tx('S00045-1')
        with self._mock_api({('POST', 'links'): ValidationError("Djomy KO")}):
            self.assertIsNone(tx._djomy_create_payment_link())
        self.assertEqual(tx.state, 'error')
        self.assertIn('Djomy KO', tx.state_message)

    # --- Lecture des payloads ---------------------------------------------

    def test_payment_data_flattens_v1_and_v2(self):
        PT = self.env['payment.transaction']
        v1 = {'eventType': 'payment.success', 'data': {'transactionId': 'T', 'status': 'SUCCESS'}}
        v2 = {'eventType': 'payment.success', 'data': {'payment': {'transactionId': 'T', 'status': 'SUCCESS'}}}
        api = {'transactionId': 'T', 'status': 'SUCCESS'}
        for payload in (v1, v2, api):
            self.assertEqual(PT._djomy_payment_data(payload)['transactionId'], 'T')
            self.assertEqual(PT._djomy_status(payload), 'SUCCESS')
        self.assertEqual(PT._djomy_payment_data('garbage'), {})
        self.assertEqual(PT._djomy_payment_data({'data': 'garbage'}), {})

    def test_extract_reference_v1_v2(self):
        PT = self.env['payment.transaction']
        self.assertEqual(PT._extract_reference('djomy', {'data': {'merchantPaymentReference': 'R1'}}), 'R1')
        self.assertEqual(PT._extract_reference('djomy', {'data': {'payment': {'merchantPaymentReference': 'R2'}}}), 'R2')
        self.assertEqual(PT._extract_reference('djomy', {'merchantPaymentReference': 'R3'}), 'R3')
        self.assertFalse(PT._extract_reference('djomy', {'data': {}}))

    def test_search_by_reference_falls_back_on_link_then_transaction_id(self):
        PT = self.env['payment.transaction']
        by_link = self._tx('BY-LINK', djomy_payment_link_reference='LNK-X')
        by_tx = self._tx('BY-TX', provider_reference='TX-X')
        self.assertEqual(PT._search_by_reference('djomy', {'merchantPaymentReference': 'BY-LINK'}), by_link)
        self.assertEqual(PT._search_by_reference('djomy', {
            'paymentLinkReference': 'LNK-X', 'data': {'transactionId': 'other'},
        }), by_link)
        self.assertEqual(PT._search_by_reference('djomy', {'data': {'transactionId': 'TX-X'}}), by_tx)
        self.assertFalse(PT._search_by_reference('djomy', {'data': {'transactionId': 'nope'}}))

    # --- Application des statuts ------------------------------------------

    def test_success_moves_to_done_and_records_details(self):
        tx = self._tx('OK-1', djomy_payment_link_reference='LNK-1')
        tx._process('djomy', {'paymentLinkReference': 'LNK-1', 'data': self._payment(tx, method='momo')})
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.provider_reference, 'TX-1')
        self.assertEqual(tx.djomy_payment_method, 'MOMO')

    def test_success_revives_cancelled_transaction(self):
        tx = self._tx('LATE-1', state='cancel', djomy_payment_link_reference='LNK-L')
        tx._process('djomy', self._payment(tx))
        self.assertEqual(tx.state, 'done', "un client qui paie tard via le SMS a bel et bien paye")

    def test_timeout_and_expired_cancel(self):
        tx = self._tx('TO-1', state='pending')
        tx._process('djomy', self._payment(tx, status='TIMEOUT'))
        self.assertEqual(tx.state, 'cancel')
        tx = self._tx('EXP-1', state='pending', djomy_payment_link_reference='LNK-E')
        tx._process('djomy', {'status': 'EXPIRED', 'merchantPaymentReference': tx.reference})
        self.assertEqual(tx.state, 'cancel', "lien expire sans montant : annule, pas en erreur")

    def test_failed_and_unknown_are_errors(self):
        tx = self._tx('KO-1', state='pending')
        tx._process('djomy', self._payment(tx, status='FAILED'))
        self.assertEqual(tx.state, 'error')
        tx = self._tx('WTF-1', state='pending')
        tx._process('djomy', self._payment(tx, status='SOMETHING_NEW'))
        self.assertEqual(tx.state, 'error')

    def test_pending_stays_pending(self):
        tx = self._tx('PEND-1')
        tx._process('djomy', self._payment(tx, status='PROCESSING'))
        self.assertEqual(tx.state, 'pending')

    def test_refunded_is_logged_not_applied(self):
        tx = self._tx('REF-1', state='done')
        tx._process('djomy', self._payment(tx, status='REFUNDED'))
        self.assertEqual(tx.state, 'done')

    def test_amount_mismatch_is_error(self):
        tx = self._tx('AMT-1', state='pending')
        tx._process('djomy', self._payment(tx, amount=4999))
        self.assertEqual(tx.state, 'error')

    def test_currency_mismatch_is_error(self):
        tx = self._tx('CUR-1', state='pending')
        tx._process('djomy', self._payment(tx, currency='XXX'))
        self.assertEqual(tx.state, 'error')

    def test_link_reference_learned_from_webhook(self):
        tx = self._tx('LEARN-1')
        tx._process('djomy', {'paymentLinkReference': 'LNK-W', 'data': self._payment(tx)})
        self.assertEqual(tx.djomy_payment_link_reference, 'LNK-W')
