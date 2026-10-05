# Part of Odoo. See LICENSE file for full copyright and licensing details.

# Devises acceptees par Djomy
SUPPORTED_CURRENCIES = [
    'GNF',  # Franc guineen
    'XOF',  # Franc CFA (UEMOA)
    'EUR',
    'USD',
]

# Correspondance statut Djomy -> etat de la payment.transaction Odoo.
# `TIMEOUT` (evenement `payment.timeout`) et `EXPIRED` (lien perime) sont
# des fins de vie sans paiement : on les range avec l'annulation, pas avec
# l'erreur, pour que le portail propose simplement de retenter.
PAYMENT_STATUS_MAPPING = {
    'pending': [
        'PENDING', 'INITIATED', 'PROCESSING', 'CREATED', 'REDIRECTED',
        'OTP_REQUIRED', 'WAITING_OTP', 'AWAITING_OTP',
    ],
    'done': ['SUCCESS', 'SUCCESSFUL'],
    'cancel': ['CANCELLED', 'CANCELED', 'TIMEOUT', 'EXPIRED', 'REVOKED', 'DISABLED'],
    'error': ['FAILED', 'ERROR'],
    # Rembourse cote Djomy : on ne sait pas le refleter (support_refund=False),
    # on le journalise sans toucher a l'etat.
    'refunded': ['REFUNDED'],
}

# Statuts qui signalent qu'un OTP est attendu du payeur (PayCard).
OTP_PENDING_STATUSES = ['OTP_REQUIRED', 'WAITING_OTP', 'AWAITING_OTP']

# Codes des moyens de paiement Djomy (cf. doc API v1.0).
PAYMENT_METHODS = [
    ('OM', 'Orange Money'),
    ('MOMO', 'MTN Mobile Money'),
    ('KULU', 'Kulu'),
    ('SOUTRA_MONEY', 'Soutra Money'),
    ('PAYCARD', 'PayCard'),
    ('YMO', 'Ymo'),
    ('CARD', 'Carte bancaire (VISA / MasterCard)'),
]
PAYMENT_METHOD_LABELS = dict(PAYMENT_METHODS)

# Moyens proposes en caisse pour l'encaissement direct (`POST /payments`) :
# le caissier choisit l'operateur, saisit le numero, Djomy pousse une demande
# (OM, MOMO) ou envoie un OTP au client (PAYCARD). Kulu n'y est pas : il ne
# pousse rien, il renvoie une URL a ouvrir - inutilisable au comptoir. Djomy
# annonce PAYCARD "bientot" sur cet endpoint : son refus eventuel remonte au
# caissier tel quel. SOUTRA_MONEY et YMO : a ajouter ici quand ils ouvrent.
DIRECT_PAYMENT_METHODS = ['OM', 'MOMO', 'PAYCARD']

# Moyens affichables sur la page d'un lien de paiement (`allowedPaymentMethods`).
LINK_PAYMENT_METHODS = ['OM', 'MOMO', 'SOUTRA_MONEY', 'PAYCARD', 'CARD']

# Moyens qui exigent un OTP saisi par le payeur apres l'initiation.
OTP_METHODS = {'PAYCARD'}

# Moyens dont le `payerIdentifier` est un numero de telephone (a normaliser
# en 00<indicatif>...). Pour PAYCARD, c'est un numero de compte : tel quel.
MOBILE_MONEY_METHODS = {'OM', 'MOMO', 'SOUTRA_MONEY', 'YMO'}

# Moyens pour lesquels `POST /payments` renvoie une URL a ouvrir (app ou web).
REDIRECT_METHODS = {'KULU'}

# Evenements webhook lies aux paiements entrants. Les `payout.*` (transferts
# sortants, webhook V2) ne nous concernent pas : ils sont ignores.
WEBHOOK_PAYMENT_EVENTS = [
    'payment.created',
    'payment.redirected',
    'payment.pending',
    'payment.cancelled',
    'payment.timeout',
    'payment.success',
    'payment.failed',
    'payment.refunded',
]

# Statuts d'un lien de paiement. La sandbox repond `ENABLED` pour un lien
# actif (la doc parle d'ACTIVE) : on accepte les deux.
LINK_STATUS_ACTIVE = 'ACTIVE'
LINK_STATUS_REVOKED = 'REVOKED'
LINK_STATUS_EXPIRED = 'EXPIRED'
LINK_ACTIVE_STATUSES = ['ACTIVE', 'ENABLED']
LINK_PAID_STATUSES = ['PAID']  # lien a usage unique consomme (observe en sandbox)
LINK_FINAL_STATUSES = [LINK_STATUS_REVOKED, LINK_STATUS_EXPIRED, 'DISABLED']

# Nombre de paiements autorises sur un lien a usage multiple. Constate en
# production le 1er octobre 2026 : un lien MULTIPLE *sans* `usageLimit` passe
# `PAID` et se ferme au premier paiement reussi, comme un lien UNIQUE. Il
# faut donc toujours en poser un.
BILL_LINK_USAGE_LIMIT = 50          # note partagee : autant de convives que de parts
STATIC_LINK_USAGE_LIMIT = 100000    # QR affiche en boutique : « sans limite »

# Defauts de configuration
DEFAULT_LINK_EXPIRY_MINUTES = 60      # lien web (portail / eCommerce)
DEFAULT_BILL_LINK_EXPIRY_MINUTES = 180  # QR imprime sur la note
DEFAULT_TOKEN_TTL_SECONDS = 3600      # si Djomy ne renvoie pas `expiresIn`
TOKEN_REFRESH_MARGIN_SECONDS = 60
DEFAULT_COUNTRY_CODE = 'GN'
DEFAULT_PHONE_CODE = 224

# Codes des methodes de paiement Odoo par defaut
DEFAULT_PAYMENT_METHOD_CODES = {
    'djomy',
}

# URLs de l'API
API_URLS = {
    'production': 'https://api.djomy.africa/v1/',
    'test': 'https://sandbox-api.djomy.africa/v1/',
}
