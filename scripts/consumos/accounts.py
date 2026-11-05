"""Consumption: accounts."""

import re
from .constants import (
    ACCOUNT_STATUS_LABELS,
)

from .text import (
    normalize_customer_id,
    normalize_match_text,
    parse_sheet_number,
)


def normalize_account_status(value):
    normalized = normalize_match_text(value)
    aliases = {
        "enabled": "ENABLED",
        "activa": "ENABLED",
        "activo": "ENABLED",
        "suspended": "SUSPENDED",
        "suspendida": "SUSPENDED",
        "suspendido": "SUSPENDED",
        "canceled": "CANCELED",
        "cancelled": "CANCELED",
        "cancelada": "CANCELED",
        "cancelado": "CANCELED",
        "closed": "CLOSED",
        "cerrada": "CLOSED",
        "cerrado": "CLOSED",
        "paused": "PAUSED",
        "pausada": "PAUSED",
        "pausado": "PAUSED",
        "removed": "REMOVED",
        "eliminada": "REMOVED",
        "eliminado": "REMOVED",
        "disabled": "DISABLED",
        "desactivada": "DISABLED",
        "desactivado": "DISABLED",
        "draft": "DRAFT",
        "borrador": "DRAFT",
        "hidden": "HIDDEN",
        "oculta": "HIDDEN",
        "oculto": "HIDDEN",
        "finalized": "FINALIZED",
        "finalizada": "FINALIZED",
        "finalizado": "FINALIZED",
        "unknown": "UNKNOWN",
        "estadodesconocido": "UNKNOWN",
        "unspecified": "UNSPECIFIED",
        "sinespecificar": "UNSPECIFIED",
    }
    return aliases.get(normalized, str(value or "").strip().upper())


def split_known_status_suffix(account_name):
    name = str(account_name or "").strip()
    match = re.match(r"^(.*?)\s*\(([^()]*)\)\s*$", name)

    if not match:
        return name, ""

    suffix_status = normalize_account_status(match.group(2))

    if suffix_status not in ACCOUNT_STATUS_LABELS:
        return name, ""

    return match.group(1).strip(), suffix_status


def format_account_display_name(account_name, status="", hidden=False):
    """
    Muestra el estado junto al nombre solo cuando la cuenta no esta activa
    de forma normal. Tambien traduce sufijos antiguos escritos en ingles.
    """
    clean_name, suffix_status = split_known_status_suffix(account_name)
    normalized_status = normalize_account_status(status) or suffix_status

    if hidden and normalized_status in {"", "ENABLED"}:
        normalized_status = "HIDDEN"

    if not normalized_status or normalized_status == "ENABLED":
        return clean_name

    label = ACCOUNT_STATUS_LABELS.get(normalized_status)

    if label is None:
        label = normalized_status.replace("_", " ").capitalize()

    return f"{clean_name} ({label})"


def sort_account_matrix(
    matrix,
    cuenta_col,
    id_col,
    total_columns,
    status_by_id=None,
    hidden_by_id=None,
):
    """
    Ordena filas completas de mayor a menor gasto y deja huecos al final.
    Los desempates son estables por nombre e ID para mantener idempotencia.
    """
    status_by_id = status_by_id or {}
    hidden_by_id = hidden_by_id or {}
    populated_rows = []
    empty_rows = []

    for row in matrix:
        account_id = normalize_customer_id(
            row[id_col - 1] if len(row) >= id_col else ""
        )

        if not account_id:
            empty_rows.append(row)
            continue

        account_name = row[cuenta_col - 1] if len(row) >= cuenta_col else ""
        row[cuenta_col - 1] = format_account_display_name(
            account_name,
            status_by_id.get(account_id, ""),
            hidden_by_id.get(account_id, False),
        )
        total_cost = round(sum(
            parse_sheet_number(row[col - 1])
            for col in total_columns
            if len(row) >= col
        ), 2)
        populated_rows.append((total_cost, account_id, row))

    populated_rows.sort(
        key=lambda item: (
            -item[0],
            normalize_match_text(item[2][cuenta_col - 1]),
            item[1],
        )
    )
    return [item[2] for item in populated_rows] + empty_rows
