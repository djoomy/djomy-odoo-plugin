# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'POS Djomy',
    'version': '1.0',
    'category': 'Sales/Point of Sale',
    'sequence': 6,
    'summary': 'Accept mobile money payments in POS via Djomy (Orange Money, MTN MoMo, Kulu)',
    'description': """
        This module integrates Djomy mobile money payments with the Odoo Point of Sale.

        Flow:
        1. Cashier clicks JOMI payment method
        2. Popup to enter/confirm payment amount
        3. QR code is generated and displayed
        4. Customer scans QR code to pay via Djomy (Orange Money, MTN MoMo, Kulu)
        5. Payment is confirmed automatically via polling
    """,
    'depends': ['point_of_sale', 'payment_djomy'],
    'external_dependencies': {
        'python': ['qrcode'],
    },
    'data': [
        'views/pos_payment_method_views.xml',
    ],
    'assets': {
        'point_of_sale._assets_pos': [
            'pos_djomy/static/src/app/**/*',
        ],
    },
    'installable': True,
    'author': 'Dookonect',
    'license': 'LGPL-3',
}
