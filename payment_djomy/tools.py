# Part of Odoo. See LICENSE file for full copyright and licensing details.
"""Petits utilitaires partages par payment_djomy et pos_djomy."""

import re
from datetime import datetime

from odoo.addons.payment_djomy import const


def format_phone(phone, phone_code=None):
    """Normalise un numero au format attendu par Djomy : ``00<indicatif><numero>``.

    Djomy veut un numero international prefixe par ``00`` (ex. ``00224623707722``).
    On accepte ce que les gens tapent vraiment : ``+224 623 70 77 22``,
    ``623707722``, ``00224623707722``, avec espaces, points ou tirets.

    :param str phone: le numero saisi
    :param int phone_code: indicatif a prefixer quand le numero est local
        (defaut : Guinee, 224)
    :return: le numero normalise, ou ``None`` si rien d'exploitable
    """
    if not phone:
        return None
    cleaned = re.sub(r'[^\d+]', '', str(phone))
    if cleaned.startswith('+'):
        cleaned = '00' + cleaned[1:]
    if not cleaned:
        return None
    if cleaned.startswith('00'):
        return cleaned
    code = str(phone_code or const.DEFAULT_PHONE_CODE)
    # "224623707722" : indicatif deja la, sans les 00
    if cleaned.startswith(code) and len(cleaned) > len(code) + 6:
        return '00' + cleaned
    return '00' + code + cleaned


def to_api_datetime(value):
    """Formate un datetime (UTC naif, convention Odoo) pour l'API : ``2025-07-13T10:30:00Z``."""
    if isinstance(value, str):
        return value
    return value.strftime('%Y-%m-%dT%H:%M:%SZ')


def to_api_date(value):
    """Formate une date (ou un datetime naif UTC) pour les filtres au jour de l'API : ``2025-07-13``.

    `GET /payments` filtre par ``startDate``/``endDate`` au format ``date``,
    la ou les autres endpoints parlent en ``date-time``.
    """
    if isinstance(value, str):
        return value
    return value.strftime('%Y-%m-%d')


def from_api_datetime(value):
    """Parse un datetime ISO renvoye par Djomy (``2025-07-13T10:30:00.000Z``) en datetime naif UTC.

    Renvoie ``None`` si la valeur est absente ou illisible : un horodatage
    manquant ne doit jamais faire echouer un rapprochement.
    """
    if not value or not isinstance(value, str):
        return None
    text = value.strip()
    if text.endswith('Z'):
        text = text[:-1] + '+00:00'
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = (parsed - parsed.utcoffset()).replace(tzinfo=None)
    return parsed


def api_amount(amount):
    """Montant tel que Djomy l'attend : entier quand il n'y a pas de decimales (GNF)."""
    amount = float(amount or 0)
    return int(amount) if amount.is_integer() else round(amount, 2)
