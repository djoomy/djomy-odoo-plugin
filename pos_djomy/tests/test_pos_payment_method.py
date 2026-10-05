# -*- coding: utf-8 -*-
"""Chantier 2 : la facade RPC de la caisse (QR de la note, encaissement direct, OTP, paiements deja recus)."""

import base64
from datetime import timedelta

from odoo import fields
from odoo.exceptions import AccessError, ValidationError
from odoo.tests.common import tagged

from odoo.addons.payment_djomy import const
from odoo.addons.payment_djomy.tests.common import DjomyCase


@tagged('post_install', '-at_install')
class TestPosDjomyPaymentMethod(DjomyCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.pm = cls.env['pos.payment.method'].create({
            'name': 'Djomy',
            'use_payment_terminal': 'djomy',
            'payment_method_type': 'terminal',
            'djomy_payment_method': 'OM',
            'company_id': cls.env.company.id,
        })
        cls.PM = cls.env['pos.payment.method']

    def _expires_in_minutes(self, result):
        expires = fields.Datetime.from_string(result['expiresAt'].replace('T', ' ').rstrip('Z'))
        return (expires - fields.Datetime.now()).total_seconds() / 60

    # --- Donnees chargees en caisse -----------------------------------------

    def test_pos_data_fields_and_methods_json(self):
        fields_loaded = self.PM._load_pos_data_fields(None)
        for name in ('djomy_payment_method', 'djomy_methods_json'):
            self.assertIn(name, fields_loaded)
        self.assertNotIn('djomy_flow', fields_loaded)
        import json
        methods = json.loads(self.pm.djomy_methods_json)
        self.assertEqual([m['code'] for m in methods], ['OM', 'MOMO', 'PAYCARD'],
                         "Kulu n'est pas propose en caisse : il ne pousse rien")
        self.assertTrue(next(m for m in methods if m['code'] == 'PAYCARD')['otp'])
        self.assertFalse(next(m for m in methods if m['code'] == 'OM')['otp'])

    # --- QR imprime sur la note -------------------------------------------------

    def test_bill_link_returns_qr(self):
        with self._mock_api({('POST', 'links'): self._link('LNK-BILL')}) as api:
            result = self.PM.djomy_create_payment_link(self.pm.id, 30000, 'Order 0002')
        self.assertTrue(result['success'])
        self.assertEqual(result['paymentLinkReference'], 'LNK-BILL')
        self.assertTrue(result['qrCodeBase64'].startswith('data:image/png;base64,'))
        self.assertFalse(result['smsSent'])
        payload = api.payload('links')
        self.assertEqual(payload['usageType'], 'MULTIPLE', "note partagee : plusieurs convives payent leur part")
        self.assertNotIn('amountToPay', payload, "pas de montant impose : chacun saisit sa part")
        self.assertEqual(payload['usageLimit'], const.BILL_LINK_USAGE_LIMIT,
                         "sans usageLimit, Djomy ferme le lien au premier paiement")
        self.assertIn('30000', payload['description'], "le montant du est rappele dans le libelle")
        self.assertNotIn('phoneNumber', payload)
        self.assertEqual(payload['linkName'], 'POS-Order 0002')
        self.assertEqual(payload['metadata']['odoo_purpose'], 'bill')
        self.assertEqual(payload['metadata']['odoo_amount'], 30000)
        self.assertEqual(result['amount'], 30000)
        self.assertAlmostEqual(self._expires_in_minutes(result), 180, delta=2)

    def test_bill_link_expiry_parameter(self):
        self.env['ir.config_parameter'].sudo().set_param('djomy.bill_link_expiry_minutes', '45')
        with self._mock_api({('POST', 'links'): self._link()}):
            result = self.PM.djomy_create_payment_link(self.pm.id, 100, 'R')
        self.assertAlmostEqual(self._expires_in_minutes(result), 45, delta=2)

    def test_create_payment_link_api_error(self):
        with self._mock_api({('POST', 'links'): ValidationError("KO")}):
            result = self.PM.djomy_create_payment_link(self.pm.id, 100, 'R')
        self.assertFalse(result['success'])
        self.assertIn('KO', result['error'])

    def test_check_link_status(self):
        paid = {'transactionId': 'T-OK', 'status': 'SUCCESS', 'paidAmount': 100, 'paymentMethod': 'MOMO'}
        sandbox_unpaid = {'paymentLinkReference': 'D', 'status': 'ENABLED', 'usageType': 'UNIQUE', 'numberOfUsage': 0}
        sandbox_paid = {**sandbox_unpaid, 'paymentLinkReference': 'E', 'numberOfUsage': 1}
        with self._mock_api({
            ('GET', 'links/A'): self._link('A', payments=[{'transactionId': 'T-KO', 'status': 'FAILED'}, paid]),
            ('GET', 'links/B'): self._link('B', status='EXPIRED'),
            ('GET', 'links/C'): self._link('C'),
            ('GET', 'links/D'): sandbox_unpaid,
            ('GET', 'links/E'): sandbox_paid,
        }):
            done = self.PM.djomy_check_link_status('A')
            expired = self.PM.djomy_check_link_status('B')
            waiting = self.PM.djomy_check_link_status('C')
            enabled = self.PM.djomy_check_link_status('D')
            used = self.PM.djomy_check_link_status('E')
        self.assertTrue(enabled['isPending'], "ENABLED (sandbox) vaut ACTIVE")
        self.assertFalse(enabled['isDone'])
        self.assertTrue(used['isDone'])
        self.assertTrue(used['inferredFromUsage'])
        self.assertIsNone(used['transactionId'])
        self.assertTrue(done['isDone'])
        self.assertEqual(done['transactionId'], 'T-OK')
        self.assertEqual(done['paymentMethod'], 'MOMO')
        self.assertFalse(done['isPending'])
        self.assertTrue(expired['isExpired'])
        self.assertFalse(expired['isDone'])
        self.assertTrue(waiting['isPending'])
        self.assertIsNone(waiting['transactionId'])

    def test_check_link_status_shared_bill(self):
        """Note partagee : la somme des paiements aboutis fait foi, un echec n'arrete rien."""
        payments = [
            {'transactionId': 'T-2', 'status': 'SUCCESS', 'paidAmount': 400, 'paymentMethod': 'OM',
             'createdAt': '2026-10-01T20:02:00Z'},
            {'transactionId': 'T-KO', 'status': 'FAILED', 'paidAmount': 100, 'createdAt': '2026-10-01T20:03:00Z'},
            {'transactionId': 'T-1', 'status': 'SUCCESS', 'paidAmount': 600, 'paymentMethod': 'MOMO',
             'createdAt': '2026-10-01T20:01:00Z'},
        ]
        with self._mock_api({('GET', 'links/S'): self._link('S', status='ENABLED', payments=payments)}):
            complete = self.PM.djomy_check_link_status('S', 1000)
            partial = self.PM.djomy_check_link_status('S', 1500)
            single = self.PM.djomy_check_link_status('S')
        self.assertTrue(complete['isDone'])
        self.assertFalse(complete['isPartial'])
        self.assertEqual(complete['paidTotal'], 1000)
        self.assertEqual([p['transactionId'] for p in complete['receivedPayments']], ['T-1', 'T-2'],
                         "paiements aboutis seulement, dans l'ordre de reception")
        self.assertEqual(complete['transactionId'], 'T-1')
        self.assertFalse(partial['isDone'])
        self.assertTrue(partial['isPartial'])
        self.assertTrue(partial['isPending'])
        self.assertFalse(partial['isFailed'], "l'echec d'un convive n'arrete pas l'attente")
        self.assertEqual(partial['expectedAmount'], 1500)
        self.assertTrue(single['isDone'], "sans montant attendu : un paiement abouti suffit")
        self.assertEqual(single['paidTotal'], 1000)

    def test_check_link_status_shared_bill_nothing_yet(self):
        with self._mock_api({('GET', 'links/S'): self._link('S', status='ENABLED')}):
            result = self.PM.djomy_check_link_status('S', 1000)
        self.assertFalse(result['isDone'])
        self.assertFalse(result['isPartial'])
        self.assertTrue(result['isPending'])
        self.assertEqual(result['receivedPayments'], [])

    # --- Encaissement direct --------------------------------------------------

    def test_create_payment_om(self):
        with self._mock_api({('POST', 'payments'): {'transactionId': 'T-OM', 'status': 'PENDING'}}) as api:
            result = self.PM.djomy_create_payment(self.pm.id, 2500, '+224 622 00 00 01', 'Order 0003', 'OM')
        self.assertTrue(result['success'])
        self.assertEqual(result['transactionId'], 'T-OM')
        self.assertTrue(result['isPending'])
        self.assertFalse(result['otpRequired'])
        self.assertIsNone(result['redirectUrl'])
        payload = api.payload('payments')
        self.assertEqual(payload['paymentMethod'], 'OM')
        self.assertEqual(payload['payerIdentifier'], '00224622000001')
        self.assertEqual(payload['merchantPaymentReference'], 'Order 0003')

    def test_create_payment_uses_default_channel(self):
        self.pm.djomy_payment_method = 'MOMO'
        with self._mock_api({('POST', 'payments'): {'transactionId': 'T', 'status': 'PENDING'}}) as api:
            result = self.PM.djomy_create_payment(self.pm.id, 100, '622000001', 'R')
        self.assertEqual(result['paymentMethod'], 'MOMO')
        self.assertEqual(api.payload('payments')['paymentMethod'], 'MOMO')

    def test_create_payment_kulu_refused_in_pos(self):
        with self._mock_api({}) as api:
            result = self.PM.djomy_create_payment(self.pm.id, 100, '622000001', 'R', 'KULU')
        self.assertFalse(result['success'])
        self.assertIn('Kulu', result['error'])
        self.assertFalse(api.calls)

    def test_create_payment_paycard_requires_otp(self):
        """PayCard : Djomy envoie un OTP au client, la caisse doit le saisir.
        L'identifiant est un numero de compte, envoye tel quel (pas 00224...)."""
        with self._mock_api({('POST', 'payments'): {'transactionId': 'T-PC', 'status': 'PENDING'}}) as api:
            result = self.PM.djomy_create_payment(self.pm.id, 100, '537-417-414', 'R', 'PAYCARD')
        self.assertTrue(result['success'])
        self.assertTrue(result['otpRequired'])
        payload = api.payload('payments')
        self.assertEqual(payload['paymentMethod'], 'PAYCARD')
        self.assertEqual(payload['payerIdentifier'], '537417414')

    def test_create_payment_redirect_url_is_forwarded(self):
        """Si Djomy renvoyait une URL a ouvrir, la caisse la recoit avec son QR."""
        response = {'transactionId': 'T-R', 'status': 'INITIATED', 'redirectUrl': 'https://pay.example/x'}
        with self._mock_api({('POST', 'payments'): response}):
            result = self.PM.djomy_create_payment(self.pm.id, 100, '622000001', 'R', 'OM')
        self.assertEqual(result['redirectUrl'], 'https://pay.example/x')
        self.assertTrue(result['qrCodeBase64'].startswith('data:image/png;base64,'))

    def test_create_payment_otp_flag(self):
        with self._mock_api({('POST', 'payments'): {'transactionId': 'T', 'status': 'OTP_REQUIRED'}}):
            result = self.PM.djomy_create_payment(self.pm.id, 100, '622000001', 'R', 'OM')
        self.assertTrue(result['otpRequired'])
        self.assertTrue(result['isPending'])

    def test_create_payment_unavailable_channel(self):
        with self._mock_api({}) as api:
            result = self.PM.djomy_create_payment(self.pm.id, 100, '622000001', 'R', 'SOUTRA_MONEY')
        self.assertFalse(result['success'])
        self.assertIn('Soutra Money', result['error'])
        self.assertFalse(api.calls)

    def test_create_payment_requires_phone(self):
        with self._mock_api({}) as api:
            result = self.PM.djomy_create_payment(self.pm.id, 100, '', 'R', 'OM')
        self.assertFalse(result['success'])
        self.assertFalse(api.calls)

    def test_confirm_otp(self):
        with self._mock_api({('POST', 'payments/T/confirmOTP'): {'status': 'PROCESSING'}}):
            ok = self.PM.djomy_confirm_otp('T', '123456')
            bad = self.PM.djomy_confirm_otp('T', '12')
        self.assertTrue(ok['success'])
        self.assertTrue(ok['isPending'])
        self.assertFalse(bad['success'])

    def test_check_payment_status(self):
        with self._mock_api({
            ('GET', 'payments/T1/status'): {'status': 'SUCCESS', 'paidAmount': 100, 'paymentMethod': 'OM'},
            ('GET', 'payments/T2/status'): {'status': 'CANCELLED'},
            ('GET', 'payments/T3/status'): ValidationError("down"),
        }):
            done = self.PM.djomy_check_payment_status('T1')
            cancelled = self.PM.djomy_check_payment_status('T2')
            down = self.PM.djomy_check_payment_status('T3')
        self.assertTrue(done['isDone'])
        self.assertEqual(done['transactionId'], 'T1')
        self.assertEqual(done['paidAmount'], 100)
        self.assertTrue(cancelled['isCancelled'])
        self.assertFalse(down['success'])

    # --- Paiements deja recus (GET /payments) ----------------------------------

    def test_list_recent_payments(self):
        recent = fields.Datetime.now() - timedelta(hours=1)
        old = fields.Datetime.now() - timedelta(days=3)
        payments = [
            {'transactionId': 'T-NEW', 'status': 'SUCCESS', 'paidAmount': 5000, 'currency': 'GNF',
             'paymentMethod': 'OM', 'payerIdentifier': '00224', 'paymentLinkReference': 'LNK-STATIC',
             'merchantPaymentReference': 'STATIC', 'createdAt': recent.strftime('%Y-%m-%dT%H:%M:%SZ')},
            {'transactionId': 'T-OLD', 'status': 'SUCCESS', 'paidAmount': 100,
             'createdAt': old.strftime('%Y-%m-%dT%H:%M:%SZ')},
            {'transactionId': 'T-KO', 'status': 'FAILED', 'paidAmount': 100,
             'createdAt': recent.strftime('%Y-%m-%dT%H:%M:%SZ')},
        ]
        with self._mock_api({('GET', 'payments'): {'content': payments, 'last': True}}) as api:
            result = self.PM.djomy_list_recent_payments(self.pm.id, 24)
        self.assertTrue(result['success'])
        params = api.params('payments')
        self.assertEqual(params['startDate'], (fields.Datetime.now() - timedelta(hours=24)).strftime('%Y-%m-%d'))
        self.assertEqual(params['endDate'], fields.Datetime.now().strftime('%Y-%m-%d'))
        ids = [row['transactionId'] for row in result['payments']]
        self.assertEqual(ids, ['T-NEW', 'T-KO'], "le vieux paiement est filtre a l'heure cote Odoo")
        row = result['payments'][0]
        self.assertTrue(row['isDone'])
        self.assertEqual(row['paymentMethodLabel'], 'Orange Money')
        self.assertEqual(row['linkReference'], 'LNK-STATIC')
        self.assertEqual(row['merchantReference'], 'STATIC')
        self.assertIsNone(row['matchedOn'])

    def test_list_recent_payments_flags_shop_and_global_links(self):
        """Le QR de la boutique passe en premier, le QR global est nomme, le reste suit."""
        config = self.env['pos.config'].create({'name': 'Boutique test'})
        config.djomy_static_link_reference = 'LNK-SHOP'
        self.provider.djomy_static_link_reference = 'LNK-GLOBAL'
        now = fields.Datetime.now()
        def pay(tx, ref, minutes_ago):
            return {'transactionId': tx, 'status': 'SUCCESS', 'paidAmount': 100, 'paymentLinkReference': ref,
                    'createdAt': (now - timedelta(minutes=minutes_ago)).strftime('%Y-%m-%dT%H:%M:%SZ')}
        payments = [pay('T-OTHER', 'LNK-BILL', 1), pay('T-GLOBAL', 'LNK-GLOBAL', 2),
                    pay('T-SHOP-OLD', 'LNK-SHOP', 30), pay('T-SHOP-NEW', 'LNK-SHOP', 5)]
        with self._mock_api({('GET', 'payments'): {'content': payments, 'last': True}}):
            result = self.PM.djomy_list_recent_payments(self.pm.id, 24, config.id)
        self.assertEqual([r['transactionId'] for r in result['payments']],
                         ['T-SHOP-NEW', 'T-SHOP-OLD', 'T-OTHER', 'T-GLOBAL'])
        self.assertEqual([r['source'] for r in result['payments']], ['shop', 'shop', '', 'global'])
        self.assertEqual(result['shopLinkReference'], 'LNK-SHOP')
        self.assertEqual(result['globalLinkReference'], 'LNK-GLOBAL')

    def test_pos_config_static_link(self):
        config = self.env['pos.config'].create({'name': 'Boutique QR'})
        with self._mock_api({('POST', 'links'): self._link('LNK-SHOP')}) as api:
            action = config.action_djomy_generate_static_link()
        payload = api.payload('links')
        self.assertEqual(payload['usageType'], 'MULTIPLE')
        self.assertEqual(payload['usageLimit'], const.STATIC_LINK_USAGE_LIMIT)
        self.assertNotIn('amountToPay', payload)
        self.assertEqual(payload['linkName'], 'QR Boutique QR')
        self.assertTrue(payload['merchantReference'].startswith(f'STATIC-{self.env.company.id}-POS{config.id}-'))
        self.assertEqual(payload['metadata']['odoo_pos_config'], config.id)
        self.assertEqual(config.djomy_static_link_reference, 'LNK-SHOP')
        self.assertTrue(base64.b64decode(config.djomy_static_link_qr).startswith(b'\x89PNG'))
        self.assertEqual(action['tag'], 'display_notification')

    def test_list_recent_payments_marks_matched(self):
        self._tx('MATCHED-1', state='done', provider_reference='T-DONE')
        payments = [{'transactionId': 'T-DONE', 'status': 'SUCCESS', 'paidAmount': 5000}]
        with self._mock_api({('GET', 'payments'): {'content': payments, 'last': True}}):
            result = self.PM.djomy_list_recent_payments(self.pm.id)
        self.assertEqual(result['payments'][0]['matchedOn'], 'MATCHED-1')

    def test_list_recent_payments_api_down(self):
        with self._mock_api({('GET', 'payments'): ValidationError("down")}):
            result = self.PM.djomy_list_recent_payments(self.pm.id)
        self.assertFalse(result['success'])
        self.assertIn('down', result['error'])

    def test_verify_payment(self):
        self._tx('MATCHED-2', state='done', provider_reference='T-USED')
        with self._mock_api({
            ('GET', 'payments/T-FREE/status'): {'status': 'SUCCESS', 'paidAmount': 700, 'paymentMethod': 'MOMO', 'currency': 'GNF'},
            ('GET', 'payments/T-USED/status'): {'status': 'SUCCESS', 'paidAmount': 5000},
        }):
            free = self.PM.djomy_verify_payment('T-FREE')
            used = self.PM.djomy_verify_payment('T-USED')
        self.assertTrue(free['isDone'])
        self.assertEqual(free['paidAmount'], 700)
        self.assertIsNone(free['matchedOn'])
        self.assertEqual(used['matchedOn'], 'MATCHED-2')

    # --- Droits ------------------------------------------------------------------

    def test_requires_pos_user_group(self):
        user = self.env['res.users'].create({
            'name': 'Sans caisse', 'login': 'sans_caisse',
            'group_ids': [(6, 0, [self.env.ref('base.group_user').id])],
        })
        with self._mock_api({}):
            for call in (
                lambda: self.PM.with_user(user).djomy_check_payment_status('T'),
                lambda: self.PM.with_user(user).djomy_create_payment_link(self.pm.id, 1, 'R'),
                lambda: self.PM.with_user(user).djomy_create_payment(self.pm.id, 1, '6', 'R'),
                lambda: self.PM.with_user(user).djomy_confirm_otp('T', '1234'),
                lambda: self.PM.with_user(user).djomy_list_recent_payments(self.pm.id),
                lambda: self.PM.with_user(user).djomy_verify_payment('T'),
            ):
                with self.assertRaises(AccessError):
                    call()
