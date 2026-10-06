# -*- coding: utf-8 -*-
"""Chantier 0 : le client API centralise sur payment.provider."""

import base64
import types
from datetime import timedelta

from odoo import fields
from odoo.exceptions import UserError, ValidationError
from odoo.tests.common import tagged

from odoo.addons.payment_djomy import const, tools
from odoo.addons.payment_djomy.tests.common import DjomyCase


@tagged('post_install', '-at_install')
class TestDjomyProviderClient(DjomyCase):

    # --- Liens de paiement --------------------------------------------------

    def test_create_link_payload(self):
        expires = fields.Datetime.now() + timedelta(minutes=30)
        with self._mock_api({('POST', 'links'): self._link('LNK-A')}) as api:
            result = self.provider._djomy_create_link(
                5000, 'REF-1', country_code='GN', link_name='Ma boutique',
                description='Paiement REF-1', expires_at=expires,
                phone_number='00224622000001', send_sms=True,
                return_url='https://shop/return', cancel_url='https://shop/cancel',
                allowed_methods=['OM', 'CARD'], metadata={'order': 'REF-1'},
                skip_status_page=True,
            )
        self.assertEqual(result['paymentLinkReference'], 'LNK-A')
        payload = api.payload('links')
        self.assertEqual(payload['amountToPay'], 5000)
        self.assertIsInstance(payload['amountToPay'], int, "GNF : montant entier")
        self.assertEqual(payload['merchantReference'], 'REF-1')
        self.assertEqual(payload['usageType'], 'UNIQUE')
        self.assertEqual(payload['countryCode'], 'GN')
        self.assertEqual(payload['linkName'], 'Ma boutique')
        self.assertEqual(payload['expiresAt'], tools.to_api_datetime(expires))
        self.assertEqual(payload['phoneNumber'], '00224622000001')
        self.assertTrue(payload['sendSms'])
        self.assertEqual(payload['returnUrl'], 'https://shop/return')
        self.assertEqual(payload['cancelUrl'], 'https://shop/cancel')
        self.assertEqual(payload['allowedPaymentMethods'], ['OM', 'CARD'])
        self.assertEqual(payload['metadata'], {'order': 'REF-1'})
        self.assertTrue(payload['skipDjomyStatusPage'])

    def test_create_link_without_amount_is_free_amount(self):
        with self._mock_api({('POST', 'links'): self._link()}) as api:
            self.provider._djomy_create_link(None, 'STATIC', usage_type='MULTIPLE')
        payload = api.payload('links')
        self.assertNotIn('amountToPay', payload)
        self.assertNotIn('expiresAt', payload)
        self.assertNotIn('phoneNumber', payload)
        self.assertEqual(payload['usageType'], 'MULTIPLE')

    def test_create_link_uses_provider_allowed_methods(self):
        self.provider.djomy_allowed_methods = ' om, momo '
        with self._mock_api({('POST', 'links'): self._link()}) as api:
            self.provider._djomy_create_link(100, 'REF')
        self.assertEqual(api.payload('links')['allowedPaymentMethods'], ['OM', 'MOMO'])

    def test_allowed_methods_validated(self):
        with self.assertRaises(ValidationError):
            self.provider.djomy_allowed_methods = 'OM, BITCOIN'

    def test_link_expiry_must_be_positive(self):
        with self.assertRaises(ValidationError):
            self.provider.djomy_link_expiry_minutes = 0

    def test_get_link(self):
        with self._mock_api({('GET', 'links/LNK-A'): self._link('LNK-A')}) as api:
            link = self.provider._djomy_get_link('LNK-A')
        self.assertEqual(link['paymentLinkReference'], 'LNK-A')
        self.assertEqual(len(api.calls_to('links/LNK-A', 'GET')), 1)

    # --- GET /payments : tous les paiements du marchand -----------------------

    def test_list_payments_params(self):
        start = fields.Datetime.now() - timedelta(days=3)
        end = fields.Datetime.now()
        with self._mock_api({('GET', 'payments'): {'content': [], 'last': True}}) as api:
            page = self.provider._djomy_list_payments(
                page=2, size=50, start_date=start, end_date=end, statuses=['SUCCESS', 'FAILED'],
            )
        self.assertEqual(self.provider._djomy_page_items(page), [])
        params = api.params('payments')
        self.assertEqual(params['page'], 2)
        self.assertEqual(params['size'], 50)
        self.assertEqual(params['sortBy'], 'createdAt')
        self.assertEqual(params['sortDirection'], 'desc')
        self.assertEqual(params['startDate'], start.strftime('%Y-%m-%d'), "filtre au jour, pas a l'heure")
        self.assertEqual(params['endDate'], end.strftime('%Y-%m-%d'))
        self.assertEqual(params['statuses'], 'SUCCESS,FAILED')

    def test_list_payments_defaults(self):
        """Sans dates ni statuts : rien d'envoye, Djomy applique ses defauts (mois courant, SUCCESS+PENDING)."""
        with self._mock_api({('GET', 'payments'): {'content': []}}) as api:
            self.provider._djomy_list_payments()
        params = api.params('payments')
        self.assertEqual(params['page'], 0)
        self.assertEqual(params['size'], 100)
        for key in ('startDate', 'endDate', 'statuses'):
            self.assertNotIn(key, params)

    def test_list_payments_completes_missing_date(self):
        """L'API veut les deux dates ou aucune : une seule fournie, l'autre est completee."""
        start = fields.Datetime.now() - timedelta(days=2)
        with self._mock_api({('GET', 'payments'): {'content': []}}) as api:
            self.provider._djomy_list_payments(start_date=start)
            only_start = api.params('payments')
            end = fields.Datetime.now().replace(day=15)
            self.provider._djomy_list_payments(end_date=end)
            only_end = api.params('payments')
        self.assertEqual(only_start['startDate'], start.strftime('%Y-%m-%d'))
        self.assertEqual(only_start['endDate'], fields.Datetime.now().strftime('%Y-%m-%d'))
        self.assertEqual(only_end['endDate'], end.strftime('%Y-%m-%d'))
        self.assertEqual(only_end['startDate'], end.replace(day=1).strftime('%Y-%m-%d'))

    def test_iter_payments_follows_pages_until_last(self):
        pages = {
            0: {'content': [{'transactionId': f'T-{i}'} for i in range(3)], 'last': False},
            1: {'content': [{'transactionId': 'T-3'}, {'transactionId': 'T-4'}], 'last': False},
            2: {'content': [{'transactionId': 'T-5'}], 'last': True},
        }
        with self._mock_api({('GET', 'payments'): lambda params, **kw: pages[params['page']]}) as api:
            payments = self.provider._djomy_iter_payments(max_pages=5, size=3)
        self.assertEqual([p['transactionId'] for p in payments], ['T-0', 'T-1', 'T-2', 'T-3', 'T-4'],
                         "page 1 incomplete : c'est la derniere, la page 2 n'est pas demandee")
        self.assertEqual(len(api.calls_to('payments', 'GET')), 2)

    def test_iter_payments_stops_on_last_flag_and_max_pages(self):
        full = {'content': [{'transactionId': f'T-{i}'} for i in range(2)], 'last': False}
        with self._mock_api({('GET', 'payments'): full}) as api:
            payments = self.provider._djomy_iter_payments(max_pages=3, size=2)
        self.assertEqual(len(payments), 6)
        self.assertEqual(len(api.calls_to('payments', 'GET')), 3, "borne par max_pages")
        with self._mock_api({('GET', 'payments'): {**full, 'last': True}}) as api:
            payments = self.provider._djomy_iter_payments(max_pages=3, size=2)
        self.assertEqual(len(payments), 2)
        self.assertEqual(len(api.calls_to('payments', 'GET')), 1, "`last` : on s'arrete")

    def test_page_items_accepts_several_shapes(self):
        Provider = self.provider.__class__
        self.assertEqual(Provider._djomy_page_items([1, 2]), [1, 2])
        self.assertEqual(Provider._djomy_page_items({'items': [1]}), [1])
        self.assertEqual(Provider._djomy_page_items({'links': [1]}), [1])
        self.assertEqual(Provider._djomy_page_items({'foo': 'bar'}), [])
        self.assertEqual(Provider._djomy_page_items(None), [])

    def test_pick_link_payment_prefers_success(self):
        Provider = self.provider.__class__
        link = self._link(payments=[
            {'transactionId': 'F', 'status': 'FAILED', 'createdAt': '2026-09-07T10:00:00Z'},
            {'transactionId': 'S', 'status': 'SUCCESS', 'createdAt': '2026-09-07T09:00:00Z'},
        ])
        self.assertEqual(Provider._djomy_pick_link_payment(link)['transactionId'], 'S')
        link = self._link(payments=[
            {'transactionId': 'OLD', 'status': 'FAILED', 'createdAt': '2026-09-07T09:00:00Z'},
            {'transactionId': 'NEW', 'status': 'PENDING', 'createdAt': '2026-09-07T10:00:00Z'},
        ])
        self.assertEqual(Provider._djomy_pick_link_payment(link)['transactionId'], 'NEW',
                         "sans succes, le plus recent reflete l'etat courant")
        self.assertIsNone(Provider._djomy_pick_link_payment(self._link()))
        self.assertIsNone(Provider._djomy_pick_link_payment(None))

    def test_pick_link_payment_infers_from_usage_count(self):
        """Sandbox : pas de `payments[]`, seul `numberOfUsage` bouge."""
        Provider = self.provider.__class__
        link = {'paymentLinkReference': 'L', 'status': 'ENABLED', 'usageType': 'UNIQUE', 'numberOfUsage': 1}
        payment = Provider._djomy_pick_link_payment(link)
        self.assertEqual(payment['status'], 'SUCCESS')
        self.assertTrue(payment['inferredFromUsage'])
        self.assertIsNone(Provider._djomy_pick_link_payment({**link, 'numberOfUsage': 0}))
        self.assertIsNone(Provider._djomy_pick_link_payment({**link, 'usageType': 'MULTIPLE'}),
                          "usage multiple : impossible de savoir quel paiement")

    # --- Encaissement direct ------------------------------------------------

    def test_create_direct_payment_payload(self):
        with self._mock_api({('POST', 'payments'): {'transactionId': 'T1', 'status': 'PENDING'}}) as api:
            result = self.provider._djomy_create_direct_payment(
                'om', '00224622000001', 2500, 'POS-1', description='Caisse',
                metadata={'a': 1},
            )
        self.assertEqual(result['transactionId'], 'T1')
        payload = api.payload('payments')
        self.assertEqual(payload['paymentMethod'], 'OM')
        self.assertEqual(payload['payerIdentifier'], '00224622000001')
        self.assertEqual(payload['amount'], 2500)
        self.assertEqual(payload['merchantPaymentReference'], 'POS-1')
        self.assertEqual(payload['countryCode'], self.provider._djomy_get_country_code())
        self.assertEqual(payload['metadata'], {'a': 1})

    def test_create_direct_payment_rejects_bad_input(self):
        with self._mock_api({}):
            with self.assertRaises(UserError):
                self.provider._djomy_create_direct_payment('BITCOIN', '00224622000001', 10, 'R')
            with self.assertRaises(UserError):
                self.provider._djomy_create_direct_payment('OM', '', 10, 'R')

    def test_confirm_otp(self):
        with self._mock_api({('POST', 'payments/T1/confirmOTP'): {'status': 'PROCESSING'}}) as api:
            result = self.provider._djomy_confirm_otp('T1', ' 12 34 ')
        self.assertEqual(result['status'], 'PROCESSING')
        self.assertEqual(api.payload('payments/T1/confirmOTP'), {'oneTimePin': '1234'})
        with self._mock_api({}):
            with self.assertRaises(UserError):
                self.provider._djomy_confirm_otp('T1', '12')
            with self.assertRaises(UserError):
                self.provider._djomy_confirm_otp('T1', '1234567')

    def test_get_payment_status(self):
        with self._mock_api({('GET', 'payments/T1/status'): {'status': 'SUCCESS'}}) as api:
            self.assertEqual(self.provider._djomy_get_payment_status('T1')['status'], 'SUCCESS')
        self.assertEqual(len(api.calls_to('payments/T1/status', 'GET')), 1)

    # --- QR ------------------------------------------------------------------

    def test_qr_code_base64_is_png_data_uri(self):
        data_uri = self.provider._djomy_qr_code_base64('https://pay.djomy.africa/l/x')
        self.assertTrue(data_uri.startswith('data:image/png;base64,'))
        png = base64.b64decode(data_uri.split(',', 1)[1])
        self.assertTrue(png.startswith(b'\x89PNG'))
        self.assertIsNone(self.provider._djomy_qr_code_base64(''))

    # --- Jeton ---------------------------------------------------------------

    def test_token_validity(self):
        self.assertTrue(self.provider._djomy_token_is_valid())
        self.provider.djomy_access_token_expiry = fields.Datetime.now() + timedelta(seconds=30)
        self.assertFalse(self.provider._djomy_token_is_valid(), "moins d'une minute : a renouveler")
        self.provider.djomy_access_token_expiry = False
        self.assertTrue(self.provider._djomy_token_is_valid(), "expiration inconnue : on tente")
        self.provider.djomy_access_token = False
        self.assertFalse(self.provider._djomy_token_is_valid())

    def test_headers_refresh_expired_token(self):
        self.provider.djomy_access_token_expiry = fields.Datetime.now() - timedelta(minutes=1)
        with self._mock_api({('POST', 'auth'): {'accessToken': 'fresh', 'expiresIn': 7200}}) as api:
            headers = self.provider._build_request_headers('GET', 'links', None)
        self.assertEqual(len(api.calls_to('auth', 'POST')), 1)
        self.assertTrue(api.calls_to('auth')[0][2].get('skip_auth'))
        self.assertEqual(headers['Authorization'], 'Bearer fresh')
        self.assertEqual(headers['X-API-KEY'].split(':')[0], 'ci_test')
        self.assertEqual(headers['X-PARTNER-DOMAIN'], 'test.example.com')
        delta = self.provider.djomy_access_token_expiry - fields.Datetime.now()
        self.assertAlmostEqual(delta.total_seconds(), 7200, delta=60)

    def test_headers_keep_valid_token(self):
        with self._mock_api({}) as api:
            headers = self.provider._build_request_headers('GET', 'links', None)
        self.assertEqual(headers['Authorization'], 'Bearer tok_test')
        self.assertFalse(api.calls)

    def test_headers_skip_auth(self):
        with self._mock_api({}):
            headers = self.provider._build_request_headers('POST', 'auth', {}, skip_auth=True)
        self.assertNotIn('Authorization', headers)

    def test_retry_once_on_401(self):
        attempts = []

        def flaky(**kwargs):
            attempts.append(1)
            if len(attempts) == 1:
                raise ValidationError("The payment provider rejected the request.\n401 Unauthorized")
            return {'status': 'SUCCESS'}

        with self._mock_api({
            ('GET', 'payments/T1/status'): flaky,
            ('POST', 'auth'): {'accessToken': 'fresh'},
        }) as api:
            result = self.provider._djomy_send_request_with_retry('GET', 'payments/T1/status')
        self.assertEqual(result['status'], 'SUCCESS')
        self.assertEqual([c[1] for c in api.calls], ['payments/T1/status', 'auth', 'payments/T1/status'])
        self.assertEqual(self.provider.djomy_access_token, 'fresh')

    def test_no_retry_on_other_errors(self):
        with self._mock_api({('GET', 'payments/T1/status'): ValidationError("500 boom")}) as api:
            with self.assertRaises(ValidationError):
                self.provider._djomy_send_request_with_retry('GET', 'payments/T1/status')
        self.assertEqual(len(api.calls), 1)

    # --- Reponses ------------------------------------------------------------

    def test_parse_response_error_reads_envelope(self):
        response = types.SimpleNamespace(
            status_code=422, text='raw',
            json=lambda: {'success': False, 'message': 'Donnees non traitables',
                          'error': {'code': 422, 'details': 'countryCode invalide'}},
        )
        self.assertEqual(
            self.provider._parse_response_error(response),
            'Donnees non traitables - countryCode invalide',
        )
        response = types.SimpleNamespace(status_code=500, text='oops', json=lambda: (_ for _ in ()).throw(ValueError()))
        self.assertEqual(self.provider._parse_response_error(response), 'oops')

    def test_parse_response_content_unwraps_data(self):
        response = types.SimpleNamespace(status_code=201, json=lambda: {'success': True, 'data': {'a': 1}})
        self.assertEqual(self.provider._parse_response_content(response), {'a': 1})
        response = types.SimpleNamespace(status_code=200, json=lambda: {'success': False, 'message': 'x'})
        self.assertEqual(self.provider._parse_response_content(response)['message'], 'x')

    # --- Lien statique -------------------------------------------------------

    def test_generate_static_link(self):
        with self._mock_api({('POST', 'links'): self._link('LNK-STATIC')}) as api:
            action = self.provider.action_djomy_generate_static_link()
        payload = api.payload('links')
        self.assertEqual(payload['usageType'], 'MULTIPLE')
        self.assertEqual(payload['usageLimit'], const.STATIC_LINK_USAGE_LIMIT)
        self.assertNotIn('amountToPay', payload)
        self.assertNotIn('expiresAt', payload)
        self.assertEqual(self.provider.djomy_static_link_reference, 'LNK-STATIC')
        self.assertEqual(self.provider.djomy_static_link_url, 'https://pay.djomy.africa/l/LNK-STATIC')
        self.assertTrue(base64.b64decode(self.provider.djomy_static_link_qr).startswith(b'\x89PNG'))
        self.assertEqual(action['tag'], 'display_notification')

    def test_generate_static_link_api_error_is_user_error(self):
        with self._mock_api({('POST', 'links'): ValidationError("boom")}):
            with self.assertRaises(UserError):
                self.provider.action_djomy_generate_static_link()

    # --- Outils --------------------------------------------------------------

    def test_country_and_phone_code_fallbacks(self):
        self.assertEqual(self.provider._djomy_get_country_code(self.partner), 'GN')
        self.assertEqual(self.provider._djomy_get_phone_code(self.partner), 224)
        self.assertEqual(tools.format_phone('+224 622 00 00 01'), '00224622000001')
        self.assertEqual(tools.format_phone('622000001', 224), '00224622000001')
        self.assertIsNone(tools.format_phone(''))
        self.assertEqual(const.PAYMENT_METHOD_LABELS['OM'], 'Orange Money')
