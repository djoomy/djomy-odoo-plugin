# POS Djomy

Encaissement mobile money dans le point de vente Odoo 19 via
[Djomy](https://djomy.africa). Dépend de `payment_djomy`, qui porte la
configuration et le client API.

## Parcours en caisse

Au clic sur le mode de paiement Djomy, le caissier choisit l'**opérateur du client**
(Orange Money, MTN MoMo, PayCard), saisit son **numéro** (prérempli depuis la fiche
client) et valide :

| Opérateur | Ce qui se passe |
|---|---|
| Orange Money, MTN MoMo | Djomy pousse une demande de paiement sur le téléphone du client ; la caisse interroge Djomy toutes les 3 s jusqu'à confirmation |
| PayCard | Djomy envoie un OTP au client ; le caissier le saisit dans la caisse, puis attend la confirmation |

Pas de QR à scanner ni de lien envoyé par SMS au comptoir. Kulu n'est pas proposé en
caisse (il ne pousse rien, il renvoie une URL à ouvrir) ; il reste disponible sur le
portail web.

Si une note avec QR a été imprimée avant l'encaissement, la caisse propose aussi
**QR de la note** : attendre les paiements de ce lien. La note est **partagée** :
plusieurs convives scannent le même QR et paient chacun leur part ; la caisse
additionne ce qu'elle reçoit, crée **une ligne de paiement par paiement Djomy** et
termine quand le montant dû est couvert. Le caissier peut aussi « Encaisser ce qui est
reçu » et compléter en espèces. Un trop-perçu reste chez Djomy et lui est signalé.

**Déjà payé (QR / lien Djomy)** : le client a payé en scannant le QR statique affiché
en boutique, ou le QR de sa note après un autre encaissement. La caisse liste les
paiements reçus par le marchand sur les dernières heures (`GET /v1/payments`), le
caissier choisit celui du client ; son statut est relu, la ligne prend le montant payé
et le `transactionId`. Un paiement déjà encaissé est grisé et refusé.

## QR sur la note

Quand une commande non réglée est imprimée (note restaurant, ticket avant paiement),
un lien de paiement **à usage multiple, sans montant imposé** est créé et son QR
imprimé sur la note avec le montant dû (chaque convive saisit sa part sur la page
Djomy). Si Djomy est injoignable, la note s'imprime sans QR.

## Rattachement depuis le back-office

Sur une commande POS encore en brouillon, le bouton « Rattacher un paiement Djomy »
ouvre le même wizard que sur les factures (`payment_djomy`) : tous les paiements de la
période ou ceux d'un lien précis, re-vérification, puis `pos.payment` sur le mode Djomy
de la caisse. Un `transactionId` Djomy ne peut être encaissé qu'une fois : contrainte
sur `pos.payment`.

## Configuration

1. Configurer le fournisseur Djomy (`payment_djomy`) ; générer le **lien statique**
   global si le commerce veut un QR commun (optionnel).
1b. Par caisse, **Paramètres › bloc Paiement › « Générer le QR de la boutique »** : un QR
   propre à la boutique, dont les paiements sont montrés en premier dans « Déjà payé »
   (badge « QR boutique »).
2. **Point de vente › Configuration › Modes de paiement** : créer un mode avec
   *Terminal de paiement* = Djomy et l'**opérateur par défaut** (présélectionné en caisse).
3. Ajouter ce mode au point de vente.

### Paramètres système

| Clé | Défaut | Effet |
|---|---|---|
| `djomy.bill_link_expiry_minutes` | `180` | validité du QR imprimé sur la note |

## Appels serveur (`pos.payment.method`)

Tous réservés au groupe *Utilisateur PdV*, tous renvoient `{'success': False, 'error': ...}`
plutôt qu'une exception :

`djomy_create_payment_link`, `djomy_check_link_status`, `djomy_create_payment`,
`djomy_confirm_otp`, `djomy_check_payment_status`, `djomy_list_recent_payments`,
`djomy_verify_payment`.

## Tests

Voir `payment_djomy/README.md` (même commande, tags `/pos_djomy`).
