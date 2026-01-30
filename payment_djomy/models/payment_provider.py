# Part of Odoo. See LICENSE file for full copyright and licensing details.

import hmac
import hashlib

from odoo import _, api, fields, models
from odoo.exceptions import ValidationError
from odoo.tools.urls import urljoin as url_join

from odoo.addons.payment.logging import get_payment_logger
from odoo.addons.payment_djomy import const


_logger = get_payment_logger(__name__)


class PaymentProvider(models.Model):
    _inherit = 'payment.provider'

    code = fields.Selection(
        selection_add=[('djomy', "Djomy")], ondelete={'djomy': 'set default'}
    )
    djomy_client_id = fields.Char(
        string="Djomy Client ID",
        help="The client ID provided by Djomy.",
        required_if_provider='djomy',
        copy=False,
    )
    djomy_client_secret = fields.Char(
        string="Djomy Client Secret",
        required_if_provider='djomy',
        copy=False,
        groups='base.group_system',
    )
    djomy_access_token = fields.Char(
        string="Djomy Access Token",
        copy=False,
        groups='base.group_system',
    )
    djomy_partner_domain = fields.Char(
        string="Partner Domain",
        help="Your domain registered and validated by Djomy. Optional in Test mode, required in Production.",
        copy=False,
    )

    # === COMPUTE METHODS === #

    def _compute_feature_support_fields(self):
        """Override of `payment` to enable additional features."""
        super()._compute_feature_support_fields()
        self.filtered(lambda p: p.code == 'djomy').update({
            'support_tokenization': False,
            'support_express_checkout': False,
            'support_refund': False,
        })

    def _get_supported_currencies(self):
        """Override of `payment` to return the supported currencies."""
        supported_currencies = super()._get_supported_currencies()
        if self.code == 'djomy':
            supported_currencies = supported_currencies.filtered(
                lambda c: c.name in const.SUPPORTED_CURRENCIES
            )
        return supported_currencies

    # === CRUD METHODS === #

    def _get_default_payment_method_codes(self):
        """Override of `payment` to return the default payment method codes."""
        self.ensure_one()
        if self.code != 'djomy':
            return super()._get_default_payment_method_codes()
        return const.DEFAULT_PAYMENT_METHOD_CODES

    # === BUSINESS METHODS === #

    def _djomy_get_api_url(self):
        """Return the API URL based on the provider state."""
        self.ensure_one()
        if self.state == 'enabled':
            return const.API_URLS['production']
        return const.API_URLS['test']

    def _djomy_generate_signature(self):
        """Generate the HMAC-SHA256 signature for X-API-KEY header."""
        self.ensure_one()
        signature = hmac.new(
            self.djomy_client_secret.encode('utf-8'),
            self.djomy_client_id.encode('utf-8'),
            hashlib.sha256
        ).hexdigest()
        return f"{self.djomy_client_id}:{signature}"

    def _djomy_fetch_access_token(self):
        """Fetch a new access token from Djomy API."""
        self.ensure_one()
        # Clear existing token before fetching new one
        self.djomy_access_token = False
        response = self._send_api_request(
            'POST', 'auth', json={}, skip_auth=True
        )
        # _parse_response_content already extracts 'data' from the response
        self.djomy_access_token = response.get('accessToken')
        return self.djomy_access_token

    def _djomy_send_request_with_retry(self, method, endpoint, **kwargs):
        """Send API request with automatic token refresh on auth failure.

        If the request fails due to an expired/invalid token (401 or token error),
        the token is refreshed and the request is retried once.
        """
        self.ensure_one()
        try:
            return self._send_api_request(method, endpoint, **kwargs)
        except Exception as e:
            error_msg = str(e).lower()
            # Check if it's an authentication error
            if '401' in error_msg or 'token' in error_msg or 'unauthorized' in error_msg:
                _logger.info("Djomy: Token expired or invalid, refreshing...")
                self._djomy_fetch_access_token()
                # Retry the request with new token
                return self._send_api_request(method, endpoint, **kwargs)
            raise

    # === REQUEST HELPERS === #

    def _build_request_url(self, endpoint, **kwargs):
        """Override of `payment` to build the request URL."""
        if self.code != 'djomy':
            return super()._build_request_url(endpoint, **kwargs)
        return url_join(self._djomy_get_api_url(), endpoint)

    def _build_request_headers(self, *args, skip_auth=False, **kwargs):
        """Override of `payment` to build the request headers."""
        if self.code != 'djomy':
            return super()._build_request_headers(*args, **kwargs)

        headers = {
            'Content-Type': 'application/json',
            'X-API-KEY': self._djomy_generate_signature(),
        }
        if self.djomy_partner_domain:
            headers['X-PARTNER-DOMAIN'] = self.djomy_partner_domain
        if not skip_auth:
            if not self.djomy_access_token:
                self._djomy_fetch_access_token()
            headers['Authorization'] = f'Bearer {self.djomy_access_token}'
        return headers

    def _parse_response_error(self, response):
        """Override of `payment` to parse the error message."""
        if self.code != 'djomy':
            return super()._parse_response_error(response)
        return response.json().get('message', '')

    def _parse_response_content(self, response, **kwargs):
        """Override of `payment` to parse the response content."""
        if self.code != 'djomy':
            return super()._parse_response_content(response, **kwargs)
        json_response = response.json()
        if json_response.get('success'):
            return json_response.get('data', json_response)
        return json_response
