"""SEM: metrics."""

import re
from decimal import Decimal, ROUND_HALF_UP
from .account_status import canonical_status_key, status_display_label
from .constants import (
    EXTERNAL_ADS_SOURCE_IDS,
    INTEGER_CURRENCY_FORMAT,
    INTEGER_NUMBER_FORMAT,
    INTEGER_PERCENT_FORMAT,
    OPTIONAL_CURRENCY_FORMAT,
    OPTIONAL_DECIMAL_FORMAT,
    OPTIONAL_PERCENT_FORMAT,
    STANDARD_HEADER_BY_KEY,
)

from .text import (
    normalize_text,
)


def format_number(value, decimals=2):
    if value is None:
        return ""

    rounded = round(float(value), decimals)

    if rounded == 0:
        return 0

    return rounded


def parse_sheet_number(value):
    """Convierte numeros de Sheets con formato espanol a float."""
    if isinstance(value, (int, float)):
        return float(value)

    text = str(value or "").strip()
    if not text:
        return 0.0

    text = (
        text.replace("\u00a0", "")
        .replace(" ", "")
        .replace("\u20ac", "")
        .replace("%", "")
    )
    text = re.sub(r"[^0-9,.+\-]", "", text)
    if not text:
        return 0.0

    if "," in text and "." in text:
        if text.rfind(",") > text.rfind("."):
            text = text.replace(".", "").replace(",", ".")
        else:
            text = text.replace(",", "")
    elif "," in text:
        text = text.replace(",", ".")
    elif re.fullmatch(r"[+\-]?[1-9]\d{0,2}(?:\.\d{3})+", text):
        text = text.replace(".", "")
    elif text.count(".") > 1:
        parts = text.split(".")
        text = "".join(parts[:-1]) + "." + parts[-1]

    try:
        return float(text)
    except ValueError:
        return 0.0


def optional_number_format_for_value(
    value,
    decimal_pattern=OPTIONAL_DECIMAL_FORMAT,
    integer_pattern=INTEGER_NUMBER_FORMAT,
):
    """Dos decimales fijos, salvo enteros tras el redondeo visible de Sheets."""
    number = parse_sheet_number(value)
    visible_number = Decimal(str(number)).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
    if visible_number == visible_number.to_integral_value():
        return integer_pattern
    return decimal_pattern


def optional_decimal_format_for_value(value):
    """Evita que Sheets deje una coma final en valores enteros."""
    return optional_number_format_for_value(value)


def optional_currency_format_for_value(value):
    return optional_number_format_for_value(
        value,
        decimal_pattern=OPTIONAL_CURRENCY_FORMAT,
        integer_pattern=INTEGER_CURRENCY_FORMAT,
    )


def optional_percent_format_for_value(value):
    return optional_number_format_for_value(
        parse_sheet_number(value) * 100,
        decimal_pattern=OPTIONAL_PERCENT_FORMAT,
        integer_pattern=INTEGER_PERCENT_FORMAT,
    )


def campaign_sort_key(campaign):
    is_enabled = canonical_status_key(campaign.get("campaign_status")) == "enabled"
    cost = parse_sheet_number(
        campaign.get("raw_cost", campaign.get("cost", 0))
    )
    return (
        0 if is_enabled else 1,
        -cost,
        normalize_text(campaign.get("campaign_name")),
        str(campaign.get("campaign_id", "")),
    )


def sort_campaign_rows(campaign_rows):
    return sorted(campaign_rows, key=campaign_sort_key)


def has_external_ads_sources(sem_client):
    return bool(
        sem_client.get("microsoft_ads_account_id")
        or sem_client.get("openai_ads_account_id")
        or sem_client.get('handler') == 'social_campaigns'
    )


def campaign_costs_by_source(campaign_rows):
    costs = {
        "google": 0.0,
        "microsoft": 0.0,
        "openai": 0.0,
    }
    for row in campaign_rows:
        source = row.get("source_customer_id")
        bucket = source if source in EXTERNAL_ADS_SOURCE_IDS else "google"
        costs[bucket] = costs.get(bucket, 0.0) + parse_sheet_number(row.get("raw_cost", 0))
    return {key: round(value, 2) for key, value in costs.items()}


def summarize_campaign_metrics(campaign_rows):
    clicks = sum(parse_sheet_number(row.get("clicks", 0)) for row in campaign_rows)
    impressions = sum(
        parse_sheet_number(row.get("impressions", 0))
        for row in campaign_rows
    )
    cost = sum(
        parse_sheet_number(row.get("raw_cost", row.get("cost", 0)))
        for row in campaign_rows
    )
    conversions = sum(
        parse_sheet_number(row.get("conversions", 0))
        for row in campaign_rows
    )
    return {
        "clicks": clicks,
        "ctr": clicks / impressions if impressions else 0,
        "average_cpc": cost / clicks if clicks else 0,
        "cost": cost,
        "conversions": conversions,
        "cost_per_conversion": cost / conversions if conversions else 0,
        "impressions": impressions,
    }


def format_share(value):
    if value is None:
        return ""

    return round(float(value), 2)


def combine_google_ads_account_results(account_results):
    """Une cuentas sin mezclar campanas ni promediar metricas no aditivas."""
    account_statuses = {}
    technical_account_statuses = {}
    period_rows = {}

    for customer_id, result in account_results.items():
        account_statuses[customer_id] = result["account_status"]
        technical_account_statuses[customer_id] = result.get(
            "technical_account_status",
            result["account_status"],
        )
        for period_key, rows in result["period_rows"].items():
            combined_rows = period_rows.setdefault(period_key, [])
            for row in rows:
                combined_row = dict(row)
                combined_row["source_customer_id"] = customer_id
                combined_row["campaign_id"] = (
                    f"{customer_id}:{row['campaign_id']}"
                )
                combined_rows.append(combined_row)

    for period_key, rows in period_rows.items():
        period_rows[period_key] = sort_campaign_rows(rows)

    unique_statuses = sorted(set(account_statuses.values()))
    if len(unique_statuses) == 1:
        account_status = unique_statuses[0]
    else:
        account_status = "mixed (" + ", ".join(unique_statuses) + ")"

    return {
        "account_status": account_status,
        "account_statuses": account_statuses,
        "technical_account_statuses": technical_account_statuses,
        "period_rows": period_rows,
    }


def build_output_matrix(campaign_rows, columns, start_col, end_col):
    width = end_col - start_col + 1
    matrix = []

    for campaign in campaign_rows:
        row = ["" for _ in range(width)]

        for key, col_index in columns.items():
            row[col_index - start_col] = (
                campaign.get('display_platform', status_display_label(campaign[key]))
                if key == "campaign_status" else campaign[key]
            )

        matrix.append(row)

    return matrix


def build_standard_header_slice(columns, start_col, end_col, platform_display=False):
    header = ["" for _ in range(end_col - start_col + 1)]

    for key, label in STANDARD_HEADER_BY_KEY.items():
        if key not in columns:
            continue

        header[columns[key] - start_col] = (
            'Plataforma' if key == 'campaign_status' and platform_display else label
        )

    return header
