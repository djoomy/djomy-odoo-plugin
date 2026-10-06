# Payment Provider: Djomy

Fournisseur de paiement Odoo 19 pour [Djomy](https://djomy.africa), agrégateur de
paiement mobile en Afrique de l'Ouest (Orange Money, MTN Mobile Money, Kulu, PayCard,
carte bancaire).

## Ce que fait le module

- **Lien de paiement** pour le portail client et la boutique en ligne : à chaque
  paiement, un lien Djomy à usage unique est créé (`POST /v1/links`) avec une durée de
  validité configurable (60 min par défaut). Contrairement à la session de la gateway,
  un lien envoyé par SMS le matin est encore payable l'après-midi.
- **Retour et webhook** : le retour du client et les notifications Djomy (payloads V1
  et V2, événements `payment.*`) mettent à jour la transaction, après vérification de
  la signature HMAC et re-lecture du statut officiel auprès de Djomy.
- **Synchronisation** : bouton « Synchroniser avec Djomy » sur la transaction, cron de
  réconciliation toutes les 10 min pour rattraper un webhook perdu, et cron
  d'annulation des transactions restées en attente (après une dernière vérification).
- **Rattachement manuel** : wizard « Rattacher un paiement Djomy » sur les factures
  clients, qui liste **tous les paiements reçus par le marchand** sur une période
  (`GET /v1/payments`, quel que soit le lien d'origine) ou ceux d'un lien précis, et
  lie celui qu'on désigne à la facture, après re-vérification du statut et avec
  garde-fou anti-doublon.
- **QR statique** (optionnel) : génération d'un lien à usage multiple, sans montant,
  à imprimer et afficher en boutique ; ses paiements se rattachent comme les autres.
- **Client API unique** sur `payment.provider` (`_djomy_create_link`, `_djomy_get_link`,
  `_djomy_list_payments`/`_djomy_iter_payments`, `_djomy_create_direct_payment`,
  `_djomy_confirm_otp`, `_djomy_get_payment_status`), réutilisé par `pos_djomy`.

## Installation

1. Le module est dans `odoo/addons/` (monté dans le conteneur).
2. Mettre à jour la liste des applications et installer « Payment Provider: Djomy »
   (ou `./scripts/update_tenant.sh <db> --modules payment_djomy`).

`qrcode` est fourni par l'image de base Odoo : rien à ajouter.

## Configuration

**Facturation › Configuration › Fournisseurs de paiement › Djomy**

| Champ | Rôle |
|---|---|
| Client ID / Client Secret | identifiants de l'espace marchand Djomy |
| Partner Domain | domaine validé par Djomy (obligatoire en production) |
| Validité des liens (minutes) | durée de vie d'un lien de paiement web (défaut 60) |
| Moyens de paiement proposés | codes Djomy séparés par des virgules (`OM, MOMO, CARD`) ; vide = tous |
| Lien statique | bouton « Générer le lien statique » → référence, URL et QR à imprimer (optionnel) |

Mode **Test** = sandbox (`https://sandbox-api.djomy.africa/v1/`), **Activé** =
production (`https://api.djomy.africa/v1/`).

### Paramètres système

| Clé | Défaut | Effet |
|---|---|---|
| `djomy.webhook_verify_signature` | `True` | `False` ignore la signature HMAC (le statut est toujours re-vérifié auprès de Djomy) |
| `djomy.pending_auto_cancel_minutes` | `120` | délai avant annulation d'une transaction restée en attente ; `0` désactive |
| `djomy.sync_pending_max_hours` | `24` | fenêtre du cron de réconciliation ; `0` désactive |

### Webhook

À déclarer dans l'espace développeur Djomy :

```
https://<domaine-du-tenant>/payment/djomy/webhook
```

En-tête attendu : `X-Webhook-Signature: v1:<HMAC-SHA256(corps brut, clientSecret)>`.

## Parcours de paiement (portail)

1. Le client choisit Djomy, saisit éventuellement son numéro (le lien lui est alors
   aussi envoyé par SMS).
2. Odoo crée le lien (`POST /v1/links`, `merchantReference` = référence de la
   transaction) et redirige le client vers la page de paiement Djomy.
3. Le client paie ; Djomy le renvoie sur `/payment/djomy/return/<référence>` et envoie
   le webhook. Dans les deux cas Odoo relit le statut officiel (`GET
   /v1/payments/{id}/status`, ou `GET /v1/links/{ref}` tant que le `transactionId`
   est inconnu) avant de passer la transaction en `done`.
4. Le cron de réconciliation rattrape les cas où ni retour ni webhook n'est arrivé.

## Rattacher un paiement reçu hors Odoo

Sur une facture client validée et non soldée : **« Rattacher un paiement Djomy »**.
Le wizard charge les paiements de la période (7 derniers jours par défaut, `GET
/v1/payments` paginé par 100), ou ceux d'un lien précis (`GET /v1/links/{ref}`,
prérempli avec le lien statique). Au clic sur « Rattacher », le statut est relu
(`GET /v1/payments/{id}/status`), puis une transaction `offline` est créée et
comptabilisée sur la facture. Refusés : paiement non abouti, déjà rattaché (facture
ou commande POS), devise différente, montant supérieur au restant dû.

## Endpoints Djomy utilisés

| Endpoint | Usage |
|---|---|
| `POST /v1/auth` | jeton Bearer (mis en cache avec son expiration) |
| `POST /v1/links` | lien de paiement (web, POS, QR statique) |
| `GET /v1/links/{reference}` | statut d'un lien et paiements reçus |
| `GET /v1/payments` | tous les paiements du marchand sur une période (wizard de rattachement, caisse « Déjà payé ») |
| `GET /v1/payments/{id}/status` | statut officiel d'un paiement |
| `POST /v1/payments` | encaissement direct (via `pos_djomy`) |
| `POST /v1/payments/{ref}/confirmOTP` | OTP PayCard (via `pos_djomy`) |

## Tests

```bash
set -a && source .env && set +a
docker exec -e PGPASSWORD="$POSTGRES_PASSWORD" odoo \
  odoo -d _test_djomy --db_host=postgres --db_user=odoo --db_password="$POSTGRES_PASSWORD" \
  -i payment_djomy,pos_djomy,sale --test-enable --test-tags /payment_djomy,/pos_djomy \
  --without-demo --workers=0 --http-port=8079 --db-filter='^_test_djomy$' --stop-after-init
```

L'API Djomy est simulée (`tests/common.py`) : aucun test ne sort sur le réseau.

## Devises

GNF, XOF, EUR, USD.
