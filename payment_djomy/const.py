# Part of Odoo. See LICENSE file for full copyright and licensing details.

# Currencies supported by Djomy
SUPPORTED_CURRENCIES = [
    'GNF',  # Guinean Franc
    'XOF',  # West African CFA franc
    'EUR',
    'USD',
]

# Mapping of Djomy payment statuses to Odoo transaction states
PAYMENT_STATUS_MAPPING = {
    'pending': ['PENDING', 'INITIATED', 'PROCESSING', 'CREATED'],
    'done': ['SUCCESS', 'SUCCESSFUL'],
    'cancel': ['CANCELLED'],
    'error': ['FAILED', 'ERROR'],
}

# Default payment method codes
DEFAULT_PAYMENT_METHOD_CODES = {
    'djomy',
}

# API URLs
API_URLS = {
    'production': 'https://api.djomy.africa/v1/',
    'test': 'https://sandbox-api.djomy.africa/v1/',
}
