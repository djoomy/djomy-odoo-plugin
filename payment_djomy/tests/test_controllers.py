# -*- coding: utf-8 -*-
"""Routes HTTP : creation du lien, retour du payeur, annulation, webhook."""

import hashlib
import hmac
import json

from odoo.tests.common import HttpCase, tagged

from odoo.addons.payment_djomy.tests.common import DjomyMockMixin, configure_djomy_provider


@tagged('post_install', '-at_install')
class TestDjomyControllers(DjomyMockMixin, HttpCase):

    def setUp(self):
        super().setUp()
        self.provider = configure_djomy_provider(self.env)
        self.method_djomy = self.env.ref('payment_djomy.payment_method_djomy')
        self.currency = self.env.company.currency_id
        self.partner = self.env['res.partner'].create({
            'name': 'Client HTTP', 'phone': '+224622000002',
            'country_id': self.env.ref('base.gn').id,
        })

    # --- Helpers --------------------------------------------------------------

    def _tx(self, reference, state='draft', **vals):
        values = {
            'reference': reference, 'amount': 5000,
            'currency_id': self.currency.id,
            'provider_id': self.provider.id,
            'payment_method_id': self.method_djomy.id,
            'partner_id': self.partner.id,
            'partner_phone': self.partner.phone,
            'state': state,
        }
        values.update(vals)
        return self.env['payment.transaction'].create(values)

    def _payment(self, tx, status='SUCCESS', transaction_id='TX-1', amount=None, **extra):
        data = {
            'transactionId': transaction_id, 'status': status,
            'paidAmount': tx.amount if amount is None else amount,
            'currency': tx.currency_id.name, 'paymentMethod': 'OM',
            'merchantPaymentReference': tx.reference,
            'payerIdentifier': '00224622000002',
        }
        data.update(extra)
        return data

    def _link(self, reference, status='ACTIVE', payments=None):
        return {
            'paymentLinkReference': reference,
            'paymentPageUrl': f'https://pay.djomy.africa/l/{reference}',
            'status': status, 'payments': payments or [],
        }

    def _json_call(self, url, params):
        response = self.url_open(
            url,
            data=json.dumps({'jsonrpc': '2.0', 'method': 'call', 'params': params, 'id': 1}),
            headers={'Content-Type': 'application/json'},
        )
        return response.json().get('result')

    def _webhook(self, payload, sign=True, secret=None):
        body = json.dumps(payload).encode('utf-8')
        headers = {'Content-Type': 'application/json'}
        if sign:
            digest = hmac.new(
                (secret or self.provider.djomy_client_secret).encode('utf-8'), body, hashlib.sha256,
            ).hexdigest()
            headers['X-Webhook-Signature'] = f'v1:{digest}'
        return self.url_open('/payment/djomy/webhook', data=body, headers=headers)

    def _v1(self, tx, event='payment.success', link_reference=None, **payment):
        return {
            'message': 'Statut du paiement', 'eventType': event, 'eventId': 'evt-1',
            'data': self._payment(tx, **payment),
            'paymentLinkReference': link_reference or tx.djomy_payment_link_reference or '',
            'timestamp': '2026-09-07T10:31:00.000Z',
        }

    def _v2(self, tx, event='payment.success', **payment):
        envelope = self._v1(tx, event=event, **payment)
        envelope['data'] = {'payment': envelope['data']}
        return envelope

    # --- /payment/djomy/process --------------------------------------------

    def test_process_creates_link(self):
        tx = self._tx('HTTP-PROC-1')
        with self._mock_api({('POST', 'links'): self._link('LNK-P1')}) as api:
            result = self._json_call('/payment/djomy/process', {'reference': tx.reference, 'phone': '622000002'})
        self.assertEqual(result['redirect_url'], 'https://pay.djomy.africa/l/LNK-P1')
        tx.invalidate_recordset()
        self.assertEqual(tx.djomy_payment_link_reference, 'LNK-P1')
        self.assertEqual(tx.partner_phone, '622000002')
        self.assertEqual(api.payload('links')['phoneNumber'], '00224622000002')

    def test_process_without_phone(self):
        silent = self.env['res.partner'].create({'name': 'Sans telephone'})
        tx = self._tx('HTTP-PROC-2', partner_id=silent.id, partner_phone=False)
        with self._mock_api({('POST', 'links'): self._link('LNK-P2')}) as api:
            result = self._json_call('/payment/djomy/process', {'reference': tx.reference})
        self.assertIn('redirect_url', result)
        self.assertNotIn('phoneNumber', api.payload('links'))

    def test_process_unknown_reference(self):
        with self._mock_api({}):
            result = self._json_call('/payment/djomy/process', {'reference': 'NOPE'})
        self.assertIn('error', result)

    def test_process_refuses_closed_transaction(self):
        tx = self._tx('HTTP-PROC-3', state='done')
        with self._mock_api({}) as api:
            result = self._json_call('/payment/djomy/process', {'reference': tx.reference})
        self.assertIn('error', result)
        self.assertFalse(api.calls)

    # --- Retour du payeur ------------------------------------------------------

    def test_return_trusts_api_over_url_status(self):
        tx = self._tx('HTTP-RET-1', state='pending', djomy_payment_link_reference='LNK-R1')
        with self._mock_api({('GET', 'payments/TX-R1/status'): self._payment(tx, transaction_id='TX-R1')}):
            response = self.url_open(
                f'/payment/djomy/return/{tx.reference}?transactionId=TX-R1&status=FAILED',
                allow_redirects=False,
            )
        self.assertIn(response.status_code, (302, 303))
        self.assertIn('/payment/status', response.headers.get('Location', ''))
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'done', "le statut de l'URL n'est jamais applique tel quel")
        self.assertEqual(tx.provider_reference, 'TX-R1')

    def test_return_without_transaction_id_uses_link(self):
        tx = self._tx('HTTP-RET-2', state='pending', djomy_payment_link_reference='LNK-R2')
        link = self._link('LNK-R2', payments=[self._payment(tx, transaction_id='TX-R2')])
        with self._mock_api({('GET', 'links/LNK-R2'): link}):
            self.url_open(f'/payment/djomy/return/{tx.reference}', allow_redirects=False)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.provider_reference, 'TX-R2')

    def test_return_never_falls_back_on_latest_pending(self):
        """Deux clients en meme temps : le retour de l'un ne valide pas l'autre."""
        other = self._tx('HTTP-RET-OTHER', state='pending', djomy_payment_link_reference='LNK-O')
        with self._mock_api({}) as api:
            self.url_open('/payment/djomy/return?status=SUCCESS', allow_redirects=False)
        other.invalidate_recordset()
        self.assertEqual(other.state, 'pending')
        self.assertFalse(api.calls)

    def test_return_legacy_route_by_transaction_id(self):
        tx = self._tx('HTTP-RET-3', state='pending', provider_reference='TX-R3')
        with self._mock_api({('GET', 'payments/TX-R3/status'): self._payment(tx, transaction_id='TX-R3')}):
            self.url_open('/payment/djomy/return?transactionId=TX-R3&status=SUCCESS', allow_redirects=False)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'done')

    def test_return_api_down_only_applies_negative_url_status(self):
        tx = self._tx('HTTP-RET-4', state='pending', djomy_payment_link_reference='LNK-R4')
        boom = {('GET', 'payments/*'): __import__('odoo.exceptions', fromlist=['ValidationError']).ValidationError("down"),
                ('GET', 'links/*'): __import__('odoo.exceptions', fromlist=['ValidationError']).ValidationError("down")}
        with self._mock_api(boom):
            self.url_open(f'/payment/djomy/return/{tx.reference}?transactionId=TX-R4&status=SUCCESS', allow_redirects=False)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'pending', "SUCCESS non confirme par l'API : rien n'est applique")
        with self._mock_api(boom):
            self.url_open(f'/payment/djomy/return/{tx.reference}?transactionId=TX-R4&status=CANCELLED', allow_redirects=False)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'cancel')

    def test_cancel_route(self):
        tx = self._tx('HTTP-CAN-1', state='pending', djomy_payment_link_reference='LNK-C1')
        with self._mock_api({('GET', 'links/LNK-C1'): self._link('LNK-C1')}):
            self.url_open(f'/payment/djomy/cancel/{tx.reference}', allow_redirects=False)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'cancel')

    def test_cancel_route_keeps_paid_transaction(self):
        tx = self._tx('HTTP-CAN-2', state='pending', djomy_payment_link_reference='LNK-C2')
        link = self._link('LNK-C2', payments=[self._payment(tx, transaction_id='TX-C2')])
        with self._mock_api({('GET', 'links/LNK-C2'): link}):
            self.url_open(f'/payment/djomy/cancel/{tx.reference}', allow_redirects=False)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'done', "le client a paye avant de cliquer Annuler")

    # --- Webhook ---------------------------------------------------------------

    def test_webhook_health_check(self):
        response = self.url_open('/payment/djomy/webhook')
        self.assertEqual(response.json(), {'status': 'ok'})

    def test_webhook_v1_success(self):
        tx = self._tx('HTTP-WH-1', state='pending', djomy_payment_link_reference='LNK-W1')
        with self._mock_api({('GET', 'payments/TX-1/status'): self._payment(tx)}) as api:
            response = self._webhook(self._v1(tx))
        self.assertEqual(response.json()['status'], 'ok')
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'done')
        self.assertTrue(tx.is_post_processed)
        self.assertEqual(len(api.calls_to('payments/TX-1/status')), 1, "statut officiel re-verifie")

    def test_webhook_v2_success_found_by_link(self):
        tx = self._tx('HTTP-WH-2', state='pending', djomy_payment_link_reference='LNK-W2')
        payload = self._v2(tx, transaction_id='TX-2')
        payload['data']['payment']['merchantPaymentReference'] = ''
        with self._mock_api({('GET', 'payments/TX-2/status'): self._payment(tx, transaction_id='TX-2')}):
            response = self._webhook(payload)
        self.assertEqual(response.json()['status'], 'ok')
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'done')
        self.assertEqual(tx.provider_reference, 'TX-2')

    def test_webhook_bad_signature_is_forbidden(self):
        tx = self._tx('HTTP-WH-3', state='pending', djomy_payment_link_reference='LNK-W3')
        with self._mock_api({}) as api:
            response = self._webhook(self._v1(tx), secret='wrong')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(api.calls)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'pending')

    def test_webhook_missing_signature_is_forbidden(self):
        tx = self._tx('HTTP-WH-4', state='pending', djomy_payment_link_reference='LNK-W4')
        with self._mock_api({}):
            response = self._webhook(self._v1(tx), sign=False)
        self.assertEqual(response.status_code, 403)

    def test_webhook_signature_check_disabled_still_refetches(self):
        self.env['ir.config_parameter'].sudo().set_param('djomy.webhook_verify_signature', 'False')
        tx = self._tx('HTTP-WH-5', state='pending', djomy_payment_link_reference='LNK-W5')
        with self._mock_api({('GET', 'payments/TX-1/status'): self._payment(tx, status='PENDING')}) as api:
            response = self._webhook(self._v1(tx), sign=False)
        self.assertEqual(response.json()['status'], 'ok')
        self.assertEqual(len(api.calls_to('payments/TX-1/status')), 1)
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'pending', "un payload SUCCESS forge ne suffit pas")

    def test_webhook_timeout_cancels(self):
        tx = self._tx('HTTP-WH-6', state='pending', djomy_payment_link_reference='LNK-W6')
        with self._mock_api({('GET', 'payments/TX-1/status'): self._payment(tx, status='TIMEOUT')}):
            self._webhook(self._v1(tx, event='payment.timeout', status='TIMEOUT'))
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'cancel')

    def test_webhook_ignores_payout_events(self):
        with self._mock_api({}) as api:
            response = self._webhook({'eventType': 'payout.success', 'data': {'payout': {'id': 'p'}}})
        self.assertEqual(response.json()['reason'], 'ignored_event')
        self.assertFalse(api.calls)

    def test_webhook_unknown_transaction_is_acknowledged(self):
        with self._mock_api({}) as api:
            response = self._webhook({
                'eventType': 'payment.success', 'paymentLinkReference': 'LNK-STATIC',
                'data': {'transactionId': 'T-STATIC', 'status': 'SUCCESS', 'merchantPaymentReference': ''},
            })
        self.assertEqual(response.json()['reason'], 'unknown_transaction')
        self.assertFalse(api.calls)

    def test_webhook_bad_json(self):
        response = self.url_open('/payment/djomy/webhook', data=b'{not json', headers={'Content-Type': 'application/json'})
        self.assertEqual(response.json()['reason'], 'bad_payload')

    def test_webhook_api_unreachable_reports_error(self):
        tx = self._tx('HTTP-WH-7', state='pending', djomy_payment_link_reference='LNK-W7')
        ValidationError = __import__('odoo.exceptions', fromlist=['ValidationError']).ValidationError
        with self._mock_api({('GET', 'payments/*'): ValidationError("down"), ('GET', 'links/*'): ValidationError("down")}):
            response = self._webhook(self._v1(tx))
        self.assertEqual(response.json(), {'status': 'error', 'reason': 'api_unreachable'})
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'pending')

    def test_webhook_is_idempotent(self):
        tx = self._tx('HTTP-WH-8', state='pending', djomy_payment_link_reference='LNK-W8')
        with self._mock_api({('GET', 'payments/TX-1/status'): self._payment(tx)}):
            self._webhook(self._v1(tx))
            self._webhook(self._v1(tx))
        tx.invalidate_recordset()
        self.assertEqual(tx.state, 'done')
