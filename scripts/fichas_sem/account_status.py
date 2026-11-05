"""SEM: account status."""

from datetime import date
from datetime import datetime
import re
from .constants import (
    ACCOUNT_STATUS_ENABLED,
    ACCOUNT_STATUS_FINISHED,
    ACCOUNT_STATUS_PAUSED,
)

from .text import (
    normalize_text,
)

STATUS_ALIASES = {
    "active": "enabled", "activo": "enabled", "activa": "enabled",
    "pausado": "paused", "pausada": "paused", "inactive": "paused",
    "inactivo": "paused", "inactiva": "paused",
    "cancelled": "canceled", "cancelado": "canceled", "cancelada": "canceled",
    "suspendido": "suspended", "suspendida": "suspended",
    "cerrado": "closed", "cerrada": "closed",
    "ended": "finished", "finalizada": "finished", "finalizado": "finished",
    "completed": "finished", "completado": "finished",
    "eliminado": "removed", "eliminada": "removed", "deleted": "removed",
    "pendiente": "pending", "enrevision": "inreview",
    "rechazado": "disapproved", "rechazada": "disapproved",
    "archivado": "archived", "archivada": "archived",
    "borrador": "draft", "desconocido": "unknown",
    "sinespecificar": "unspecified", "limitado": "limited",
}

STATUS_LABELS_ES = {
    "enabled": "Activo", "paused": "Pausado", "finished": "Finalizado",
    "canceled": "Cancelado", "suspended": "Suspendido", "closed": "Cerrado",
    "removed": "Eliminado", "pending": "Pendiente", "inreview": "En revisi\u00f3n",
    "disapproved": "Rechazado", "archived": "Archivado", "draft": "Borrador",
    "unknown": "Desconocido", "unspecified": "Sin especificar", "limited": "Limitado",
}


def canonical_status_key(value):
    """Accept provider codes and previously translated sheet values alike."""
    key = normalize_text(value)
    return STATUS_ALIASES.get(key, key)


def status_display_label(value):
    """Translate at the Sheets boundary; preserve source labels and unknown codes."""
    return STATUS_LABELS_ES.get(canonical_status_key(value), str(value or ""))


def translate_status_condition(condition):
    """Translate exact status literals, never identifiers or arbitrary formula text."""
    result = dict(condition)
    values = []
    for entry in condition.get("values", []):
        translated = dict(entry)
        if "userEnteredValue" not in entry:
            values.append(translated)
            continue
        text = entry.get("userEnteredValue", "")
        if condition.get("type") == "CUSTOM_FORMULA":
            def replace_literal(match):
                literal = match.group(1).replace('""', '"')
                if canonical_status_key(literal) not in STATUS_LABELS_ES:
                    return match.group(0)
                label = status_display_label(literal)
                return '"' + label.replace('"', '""') + '"'
            text = re.sub(r'"((?:[^"]|"")*)"', replace_literal, text)
        elif condition.get("type") in {
            "TEXT_EQ", "TEXT_NOT_EQ", "TEXT_CONTAINS", "TEXT_NOT_CONTAINS",
        }:
            text = status_display_label(text)
        translated["userEnteredValue"] = text
        values.append(translated)
    if "values" in condition:
        result["values"] = values
    return result


def customer_status_display_label(customer_status):
    normalized = canonical_status_key(customer_status)
    labels = {
        "enabled": ACCOUNT_STATUS_ENABLED,
        "paused": ACCOUNT_STATUS_PAUSED,
        "canceled": "Canceled",
        "suspended": "Suspended",
        "closed": "Closed",
        "unknown": "Unknown",
        "unspecified": "Unspecified",
    }
    return labels.get(normalized, str(customer_status or "Unknown").strip().title())


def campaign_is_finished(campaign, operational_day):
    if normalize_text(campaign.get("primary_status")) == "ended":
        return True

    end_date_text = str(
        campaign.get("end_date_time")
        or campaign.get("end_date")
        or ""
    ).strip()
    if not end_date_text:
        return False

    try:
        if re.match(r"^\d{4}-\d{2}-\d{2}", end_date_text):
            end_day = date.fromisoformat(end_date_text[:10])
        elif re.match(r"^\d{8}", end_date_text):
            end_day = datetime.strptime(
                end_date_text[:8],
                "%Y%m%d",
            ).date()
        else:
            return False
    except ValueError:
        return False
    return end_day < operational_day


def classify_account_operational_status(
    customer_status,
    campaigns,
    operational_day,
):
    """Resume si una cuenta puede seguir consumiendo en Google Ads."""
    if normalize_text(customer_status) != "enabled":
        return customer_status_display_label(customer_status)

    enabled_campaigns = [
        campaign
        for campaign in campaigns
        if normalize_text(campaign.get("status")) == "enabled"
    ]
    if not enabled_campaigns:
        return ACCOUNT_STATUS_PAUSED

    if all(
        campaign_is_finished(campaign, operational_day)
        for campaign in enabled_campaigns
    ):
        return ACCOUNT_STATUS_FINISHED

    return ACCOUNT_STATUS_ENABLED


def aggregate_account_operational_status(
    account_statuses,
    customer_ids=None,
    require_all_enabled=False,
):
    """Resume varias cuentas con la política operativa configurada."""
    customer_ids = customer_ids or list(account_statuses)
    statuses = [
        account_statuses.get(customer_id, "Unknown")
        for customer_id in customer_ids
    ]
    normalized = [canonical_status_key(status) for status in statuses]

    if require_all_enabled:
        if normalized and all(status == "enabled" for status in normalized):
            return ACCOUNT_STATUS_ENABLED
        for status, normalized_status in zip(statuses, normalized):
            if normalized_status != "enabled":
                return customer_status_display_label(status)

    if "enabled" in normalized:
        return ACCOUNT_STATUS_ENABLED
    if "finished" in normalized:
        return ACCOUNT_STATUS_FINISHED
    if "paused" in normalized:
        return ACCOUNT_STATUS_PAUSED
    return customer_status_display_label(statuses[0] if statuses else "Unknown")
