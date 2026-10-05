# Part of Odoo. See LICENSE file for full copyright and licensing details.

"""Retire le tiret long du nom francais du cron d'annulation Djomy.

Le cron est charge en `noupdate="1"`, et sa traduction francaise est posee a
l'installation. Le rechargement des traductions a la mise a jour n'ecrase
rien : il garderait l'ancienne traduction meme une fois le fr.po corrige. On
la remplace donc en base, seulement si elle vaut encore l'ancien texte.

Le tiret est ecrit \\u2014 pour que le caractere proscrit n'apparaisse pas en
clair dans le depot.
"""

import json

ANCIEN = "Djomy \u2014 annuler les transactions en attente périmées"
NOUVEAU = "Djomy : annuler les transactions en attente périmées"


def migrate(cr, version):
    cr.execute(
        "SELECT a.id, a.name FROM ir_act_server a "
        "JOIN ir_model_data d ON d.res_id = a.id AND d.model = 'ir.actions.server' "
        "WHERE d.module = 'payment_djomy' "
        "AND d.name = 'ir_cron_djomy_cancel_stale_pending_ir_actions_server'"
    )
    for identifiant, valeurs in cr.fetchall():
        if not valeurs:
            continue
        change = False
        for langue, texte in valeurs.items():
            if texte == ANCIEN:
                valeurs[langue] = NOUVEAU
                change = True
        if change:
            cr.execute(
                "UPDATE ir_act_server SET name = %s WHERE id = %s",
                (json.dumps(valeurs), identifiant),
            )
