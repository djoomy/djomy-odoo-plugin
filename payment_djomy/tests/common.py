# -*- coding: utf-8 -*-
"""Socle des tests payment_djomy : fournisseur de test et API Djomy simulee.

Aucun test ne parle au reseau : `_send_api_request` est remplace par un
faux qui sert des reponses par ``(METHODE, endpoint)`` et enregistre les
appels, pour verifier a la fois ce qu'on envoie a Djomy et ce qu'on fait de
ses reponses.
"""

from contextlib import contextmanager
from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests.common import TransactionCase


class DjomyApiMock:
    """Sert des reponses par route et memorise les appels.

    ``routes`` : ``{('GET', 'links/LNK'): reponse}``. La reponse peut etre un
    dict (renvoye tel quel), une exception (levee), ou un callable
    (``handler(method=, endpoint=, **kwargs)``). Une route dont l'endpoint
    finit par ``*`` sert de prefixe.
    """

    def __init__(self, routes):
        self.routes = dict(routes)
        self.calls = []

    def handle(self, provider, method, endpoint, **kwargs):
        method = method.upper()
        self.calls.append((method, endpoint, kwargs))
        handler = self._resolve(method, endpoint)
        if handler is None:
            raise AssertionError(f"Appel Djomy inattendu : {method} {endpoint}")
        if isinstance(handler, Exception):
            raise handler
        if callable(handler):
            return handler(method=method, endpoint=endpoint, **kwargs)
        return handler

    def _resolve(self, method, endpoint):
        if (method, endpoint) in self.routes:
            return self.routes[(method, endpoint)]
        for (route_method, pattern), handler in self.routes.items():
            if route_method == method and pattern.endswith('*') and endpoint.startswith(pattern[:-1]):
                return handler
        return None

    def calls_to(self, endpoint, method=None):
        return [
            call for call in self.calls
            if call[1] == endpoint and (method is None or call[0] == method.upper())
        ]

    def payload(self, endpoint, method='POST'):
        """Le corps JSON du dernier appel a cet endpoint."""
        calls = self.calls_to(endpoint, method)
        assert calls, f"aucun appel {method} {endpoint}"
        return calls[-1][2].get('json')

    def params(self, endpoint, method='GET'):
        calls = self.calls_to(endpoint, method)
        assert calls, f"aucun appel {method} {endpoint}"
        return calls[-1][2].get('params')


class DjomyMockMixin:

    @contextmanager
    def _mock_api(self, routes):
        mock = DjomyApiMock(routes)

        def fake_send_api_request(provider, method, endpoint, **kwargs):
            return mock.handle(provider, method, endpoint, **kwargs)

        Provider = self.env['payment.provider'].__class__
        with patch.object(Provider, '_send_api_request', fake_send_api_request):
            yield mock


def configure_djomy_provider(env, company=None, **extra):
    """Fournisseur Djomy pret a l'emploi pour ``company`` (cree s'il manque).

    Les tests comptables et POS travaillent dans une societe creee a la
    volee, ou le fournisseur livre par les donnees du module n'existe pas.
    """
    company = company or env.company
    Provider = env['payment.provider'].sudo()
    provider = Provider.search([('code', '=', 'djomy'), ('company_id', '=', company.id)], limit=1)
    method = env.ref('payment_djomy.payment_method_djomy')
    values = {
        'djomy_client_id': 'ci_test',
        'djomy_client_secret': 'sec_test',
        'djomy_access_token': 'tok_test',
        'djomy_access_token_expiry': fields.Datetime.now() + timedelta(hours=1),
        'djomy_partner_domain': 'test.example.com',
        'djomy_link_expiry_minutes': 60,
        'djomy_allowed_methods': False,
        'state': 'test',
        **extra,
    }
    if provider:
        provider.write(values)
    else:
        provider = Provider.create({
            'name': 'Djomy',
            'code': 'djomy',
            'company_id': company.id,
            'payment_method_ids': [(6, 0, [method.id])],
            **values,
        })
    return provider


class DjomyCase(DjomyMockMixin, TransactionCase):
    """TransactionCase avec un fournisseur Djomy configure en mode test."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        # Les tenants sont guineens : indicatif 224 pour les numeros locaux
        cls.env.company.country_id = cls.env.ref('base.gn')
        cls.provider = configure_djomy_provider(cls.env)
        cls.method_djomy = cls.env.ref('payment_djomy.payment_method_djomy')
        cls.currency = cls.env.company.currency_id
        cls.partner = cls.env['res.partner'].create({
            'name': 'Client Test',
            'phone': '+224 622 00 00 01',
            'country_id': cls.env.ref('base.gn').id,
        })

    def _tx(self, reference, amount=5000, state='draft', **vals):
        values = {
            'reference': reference,
            'amount': amount,
            'currency_id': self.currency.id,
            'provider_id': self.provider.id,
            'payment_method_id': self.method_djomy.id,
            'partner_id': self.partner.id,
            'partner_phone': self.partner.phone,
            'state': state,
        }
        values.update(vals)
        return self.env['payment.transaction'].create(values)

    def _payment(self, tx, status='SUCCESS', transaction_id='TX-1', amount=None, method='OM',
                 merchant_reference=None, **extra):
        """Un paiement tel que Djomy le decrit (reponse API ou webhook V1)."""
        data = {
            'transactionId': transaction_id,
            'status': status,
            'paidAmount': tx.amount if amount is None else amount,
            'currency': tx.currency_id.name,
            'paymentMethod': method,
            'merchantPaymentReference': tx.reference if merchant_reference is None else merchant_reference,
            'payerIdentifier': '00224622000001',
            'createdAt': '2026-09-07T10:30:00.000Z',
        }
        data.update(extra)
        return data

    def _link(self, reference='LNK-1', status='ACTIVE', payments=None):
        return {
            'paymentLinkReference': reference,
            'paymentPageUrl': f'https://pay.djomy.africa/l/{reference}',
            'status': status,
            'payments': payments or [],
        }
