# Part of Odoo. See LICENSE file for full copyright and licensing details.

{
    'name': 'Payment Provider: Djomy',
    'version': '19.0.1.6.0',
    'category': 'Accounting/Payment Providers',
    'sequence': 350,
    'summary': "Agregateur de paiement guineen : Orange Money, MTN Mobile Money, Kulu, PayCard, carte.",
    'description': """
Paiement Djomy pour le portail et la boutique en ligne : lien de paiement a
duree configurable, retour et webhook (V1/V2), synchronisation manuelle et
cron de reconciliation, rattachement manuel aux factures clients des
paiements recus hors Odoo (QR statique, lien paye apres coup), listes par
GET /payments.
""",
    'depends': ['payment', 'account_payment'],
    'data': [
        'security/ir.model.access.csv',
        'views/payment_djomy_templates.xml',
        'views/payment_provider_views.xml',
        'wizard/djomy_payment_match_views.xml',
        'data/payment_provider_data.xml',
        'data/ir_config_parameter.xml',
        'data/ir_cron.xml',
    ],
    'assets': {
        'web.assets_frontend': [
            'payment_djomy/static/src/img/*',
            'payment_djomy/static/src/js/payment_form.js',
        ],
    },
    'author': 'Dookonect',
    'post_init_hook': 'post_init_hook',
    'uninstall_hook': 'uninstall_hook',
    'license': 'LGPL-3',
}
