# Part of Odoo. See LICENSE file for full copyright and licensing details.
{
    'name': 'POS Djomy',
    'version': '19.0.2.4.0',
    'category': 'Sales/Point of Sale',
    'sequence': 6,
    'summary': 'Encaissement mobile money en caisse via Djomy : operateur + numero du client, OTP PayCard, QR sur la note, rattachement des paiements recus',
    'description': """
Paiement Djomy dans le point de vente :

- encaissement direct : le caissier choisit l'operateur du client (Orange
  Money, MTN MoMo, PayCard) et saisit son numero ; le client confirme sur son
  telephone, ou communique l'OTP recu (PayCard) ;
- QR de paiement imprime sur la note avant encaissement, que la caisse
  attend ensuite ;
- rattachement d'un paiement deja recu (QR statique du commerce, lien de
  note paye apres coup) a une commande, depuis la caisse ou le back-office ;
- QR Djomy par boutique (en plus du QR global du fournisseur), dont les
  paiements sont montres en premier dans « Deja paye ».
    """,
    'depends': ['point_of_sale', 'payment_djomy'],
    'data': [
        'security/ir.model.access.csv',
        'views/pos_payment_method_views.xml',
        'views/res_config_settings_views.xml',
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
