"""SEM: service."""

from .account_cells import (
    find_active_insertion_index,
    find_vista_global_marker_row,
    locate_vista_global_sections,
    plan_next_vista_global_status_move,
    reconcile_vista_global_status_blocks,
    update_account_status_cell,
    update_and_log_account_statuses,
    update_configured_account_statuses,
    vista_global_row_value,
)

from .account_status import (
    aggregate_account_operational_status,
    campaign_is_finished,
    classify_account_operational_status,
    customer_status_display_label,
)

from .annual_control import (
    annual_control_period_bounds,
    build_annual_control_block,
    create_annual_control_historical_block,
    extract_year,
    find_annual_control_historical_block,
    find_annual_control_live_block,
    find_annual_control_marker_row,
    find_annual_control_total_row,
    is_annual_control_total_cell,
    queue_annual_control_status_rules,
    update_annual_control_block,
    upsert_annual_control_historical_year,
)

from .balance import (
    apply_continuous_contract_months,
    build_border,
    build_saldo_target,
    contract_month_context,
    find_month_column,
    find_monthly_real_spend_cell,
    find_saldo_targets,
    saldo_month_start_day,
    select_saldo_period_targets,
    update_closed_real_spend_during_pause,
    update_current_saldo_month_marker,
    update_monthly_real_spend,
    validate_saldo_period_targets,
)

from .campaign_blocks import (
    apply_campaign_value_formats,
    apply_existing_total_currency_formats,
    apply_historical_side_band,
    apply_live_block_borders,
    apply_single_campaign_historical_borders,
    apply_total_value_formats,
    archive_live_block_if_needed,
    build_block_from_header,
    build_compatible_historical_block,
    canonical_period_label,
    clean_orphan_historical_metric_tails,
    clear_obsolete_live_columns,
    compact_historical_total_spacing,
    copy_range_to_rows,
    count_campaign_rows,
    create_historical_period_block_from_live,
    ensure_historical_marker_row,
    ensure_historical_total_row,
    ensure_rows_before_total,
    existing_period_label_updates,
    extract_campaign_rows_from_block,
    extract_preserved_historical_campaign_rows,
    find_columns_for_total_row,
    find_first_campaign_header,
    find_header_columns,
    find_historical_append_row,
    find_historical_block_end_row,
    find_historical_marker_row,
    find_historical_period_block,
    find_live_block,
    find_missing_historical_total_blocks,
    find_next_period_row,
    find_obsolete_header_columns,
    find_total_row,
    historical_copy_end_row,
    historical_period_exists,
    historical_period_start_for_order,
    infer_historical_month_year,
    is_legacy_conversion_value_header,
    iter_period_cells,
    looks_like_campaign_header,
    normalize_enabled_conditional_format_colors,
    normalize_existing_standard_block_formats,
    normalize_legacy_historical_historical_totals,
    normalize_live_total_label,
    normalized_header_lookup,
    queue_legacy_historical_historical_total,
    queue_total_row_format_copy,
    read_external_current_cost,
    repair_missing_historical_totals,
    resolve_historical_side_band_end,
    standardize_live_block_columns,
    update_live_block,
    update_single_campaign_historical_block,
    update_total_formulas,
    upsert_historical_period,
)

from .configuration import (
    get_sem_account_client_configs,
    get_sem_customer_ids,
    load_client_config_by_mcc,
    resolve_sem_client_selection,
    select_sem_client,
)

from .errors import (
    SaldoPeriodValidationError,
)

from .external_annual import (
    ensure_worksheet_columns,
    external_annual_unmerge_request,
    find_external_annual_area,
    find_microsoft_annual_area,
    find_openai_annual_area,
    microsoft_annual_columns,
    microsoft_archive_start_col,
    queue_external_annual_table,
    unmerge_external_annual_area,
    update_external_annual_control,
    update_microsoft_annual_control,
    update_openai_annual_control,
)

from .google_ads import (
    apply_exact_impression_shares,
    build_campaign_rows_from_daily_aggregates,
    create_google_ads_client,
    fetch_account_operational_status,
    fetch_annual_control_annual_rows,
    fetch_campaign_periods,
    fetch_campaign_rows,
    fetch_customer_status,
    fetch_exact_impression_shares,
    get_developer_token_for_client,
    get_google_ads_retry_delay,
    get_thread_google_ads_client,
    is_retryable_google_ads_error,
    print_google_ads_error,
    run_google_ads_read_with_retries,
)

from .metrics import (
    build_output_matrix,
    build_standard_header_slice,
    campaign_costs_by_source,
    campaign_sort_key,
    combine_google_ads_account_results,
    format_number,
    format_share,
    has_external_ads_sources,
    optional_currency_format_for_value,
    optional_decimal_format_for_value,
    optional_number_format_for_value,
    optional_percent_format_for_value,
    parse_sheet_number,
    sort_campaign_rows,
    summarize_campaign_metrics,
)

from .periods import (
    add_calendar_months,
    add_one_contract_month,
    build_named_date_range,
    date_range_start_month,
    extract_explicit_year,
    get_month_dates,
    google_sheets_date_serial,
    is_month_current_cell,
    is_period_cell,
    is_previous_months_cell,
    is_total_cost_cell,
    is_uncertain_period,
    month_key_for_day,
    month_label_for_day,
    normalize_year,
    parse_date_range,
    parse_month_name,
    parse_named_date_range_parts,
    parse_partial_month_period,
    parse_period_identity,
    parse_period_month,
    parse_period_month_number,
    parse_saldo_year_label,
    parse_sequence_number,
    parse_sheet_date,
    period_context_for_calendar_month,
    period_context_for_month,
    period_context_for_range,
    range_label_for_days,
    today_in_spain,
)

from .renewals import (
    find_contract_date_range,
    has_monthly_renewal_without_end,
    monthly_renewal_note,
    plan_monthly_budget_carry_forward,
    plan_monthly_renewal_updates,
)

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from datetime import date
from datetime import datetime
from dotenv import load_dotenv
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.oauth2.service_account import Credentials
from gspread.http_client import BackOffHTTPClient
from pathlib import Path
from runtime_config import load_consumption_runtime_config
from runtime_config import load_sem_runtime_config
from runtime_config import normalize_config_key
from zoneinfo import ZoneInfo
import argparse
import calendar
import gspread
import json
import os
import re
import sys
import threading
import time
import unicodedata
from .constants import (
    ACCOUNT_STATUS_DISABLED_COLOR,
    ACCOUNT_STATUS_ENABLED,
    ACCOUNT_STATUS_ENABLED_COLOR,
    ACCOUNT_STATUS_FINISHED,
    ACCOUNT_STATUS_PAUSED,
    ALL_SEM_CLIENTS_VALUE,
    ANNUAL_CONTROL_HANDLER,
    ANNUAL_CONTROL_HEADERS,
    DATE_RANGE_PATTERN,
    EXPECTED_HEADERS,
    EXTERNAL_ADS_SOURCE_IDS,
    GOOGLE_SHEETS_DATE_EPOCH,
    HEADER_ALIASES,
    HISTORICAL_SIDE_BAND_COLOR,
    INTEGER_CURRENCY_FORMAT,
    INTEGER_NUMBER_FORMAT,
    INTEGER_PERCENT_FORMAT,
    MAX_ADS_WORKERS,
    MAX_GOOGLE_ADS_RETRIES,
    MICROSOFT_ANNUAL_ARCHIVE_COLUMN_STEP,
    MICROSOFT_ANNUAL_ARCHIVE_FIRST_COLUMN,
    MICROSOFT_ANNUAL_ARCHIVE_KEY_PREFIX,
    MICROSOFT_ANNUAL_BASE_YEAR,
    MICROSOFT_ANNUAL_PERIOD_KEY,
    MONTHLY_RENEWAL_NOTE_PREFIX,
    MONTHLY_RENEWAL_TEXT,
    MONTHLY_RENEWAL_TEXTS,
    MONTH_ALIASES_ES,
    MONTH_NAMES_ES,
    MONTH_NUMBERS_BY_NAME,
    OBSOLETE_HEADER_ALIASES,
    OPENAI_ANNUAL_ARCHIVE_KEY_PREFIX,
    OPENAI_ANNUAL_PERIOD_KEY,
    OPTIONAL_CURRENCY_FORMAT,
    OPTIONAL_DECIMAL_FORMAT,
    OPTIONAL_PERCENT_FORMAT,
    PARTIAL_MONTH_END_PATTERN,
    PARTIAL_MONTH_START_PATTERN,
    RETRY_DELAY_SECONDS,
    SCOPES,
    SHEET_WRITE_BATCH_CLIENTS,
    SPAIN_TIMEZONE,
    SPAIN_TIMEZONE_NAME,
    STANDARD_HEADER_BY_KEY,
    TOTAL_CURRENCY_FORMAT,
    VISTA_GLOBAL_CLIENT_COLUMN,
    VISTA_GLOBAL_MONTHLY_BUDGET_COLUMN,
    VISTA_GLOBAL_NATALIA_MARKER,
    VISTA_GLOBAL_SIGNED_MARKER,
    VISTA_GLOBAL_STANDBY_MARKER,
    VISTA_GLOBAL_STATUS_COLUMN,
)
from .runtime import (
    BASE_DIR,
    CONTROL_SEM_SPREADSHEET_ID,
    EXCLUDED_SEM_CLIENTS,
    MONTHLY_RENEWAL_NOTE_PREFIXES,
    SEM_CLIENTS,
    SEM_ONLY_MCC_CONFIGS,
    SEM_RUNTIME_CONFIG,
    VISTA_GLOBAL_WORKSHEET_NAME,
    _google_ads_thread_local,
    env_path,
    sheets_credentials_path,
    vendor_path,
)

from .sheets_io import (
    SpreadsheetMutationBatch,
    create_sheets_client,
    get_worksheet_values,
    invalidate_worksheet_values_cache,
    prime_monthly_renewal_notes,
    prime_worksheet_values_cache,
    queue_cell_note,
    queue_format_requests,
    queue_monthly_renewal,
    queue_values_update,
    set_worksheet_values_cache,
)

from .text import (
    fold_text,
    normalize_customer_id,
    normalize_sem_client_selector,
    normalize_text,
)
from .social_ads import SOCIAL_HANDLER, fetch_social_campaign_periods


def parse_args():
    parser = argparse.ArgumentParser(
        description="Actualiza fichas SEM del sheet de control."
    )
    parser.add_argument(
        "--cliente",
        required=True,
        help=(
            "Cliente SEM a procesar. Use el nombre de una ficha configurada "
            f"o {ALL_SEM_CLIENTS_VALUE} para procesarlas todas."
        ),
    )
    parser.add_argument(
        "--fecha-operativa",
        help=(
            "Fecha YYYY-MM-DD para reparaciones controladas. Si se omite, "
            "se usa la fecha actual de Europe/Madrid."
        ),
    )
    parser.add_argument(
        "--normalizar-formatos-historicos",
        action="store_true",
        help=(
            "Reaplica una sola vez los formatos numericos y los colores de "
            "estado a todos los bloques historicos reconocidos."
        ),
    )
    return parser.parse_args()


def prepare_annual_control_client(sem_client, worksheet, target_day):
    if sem_client.get("google_ads_mcc_by_customer_id"):
        raise ValueError("Los gestores por cuenta requieren una ficha SEM estandar.")
    ads_client_config = load_client_config_by_mcc(sem_client["mcc_id"])
    block = find_annual_control_live_block(worksheet)
    period_value = block["values"][block["period_row"] - 1][block["period_col"] - 1]
    live_year = extract_year(period_value)
    campaign_count = 0
    for row_index in range(block["data_start_row"], block["total_row"]):
        row = block["values"][row_index - 1]
        value = row[block["start_col"] - 1] if len(row) >= block["start_col"] else ""
        if not str(value).strip():
            break
        campaign_count += 1

    if live_year is None and campaign_count:
        raise ValueError(
            "la ficha anual tiene campanas en MES ACTUAL pero no se puede leer el ano."
        )
    if live_year and live_year > target_day.year:
        raise ValueError(
            f"El bloque vivo de la ficha anual esta en {live_year}, posterior a "
            f"{target_day.year}."
        )

    years = {target_day.year}
    if live_year and live_year < target_day.year:
        years.update(range(live_year, target_day.year))
    periods = {
        year: annual_control_period_bounds(sem_client, year, target_day)
        for year in sorted(years)
    }
    return {
        "sem_client": sem_client,
        "worksheet": worksheet,
        "block": block,
        "ads_client_config": ads_client_config,
        "mcc_id": normalize_customer_id(ads_client_config["mcc_id"]),
        "customer_ids": get_sem_customer_ids(sem_client),
        "target_day": target_day,
        "live_year": live_year,
        "annual_periods": periods,
    }


def fetch_annual_control_client_data(prepared):
    started_at = time.perf_counter()
    google_ads_client = get_thread_google_ads_client(
        prepared["ads_client_config"]
    )
    customer_id = prepared["customer_ids"][0]
    rows_by_year = {
        year: fetch_annual_control_annual_rows(
            google_ads_client,
            customer_id,
            period,
        )
        for year, period in prepared["annual_periods"].items()
    }
    technical_status = fetch_customer_status(
        google_ads_client,
        customer_id,
    )
    account_status = fetch_account_operational_status(
        google_ads_client,
        customer_id,
        technical_status,
        prepared["target_day"],
    )
    return {
        "rows_by_year": rows_by_year,
        "account_statuses": {customer_id: account_status},
        "technical_account_statuses": {
            customer_id: technical_status,
        },
        "elapsed_seconds": time.perf_counter() - started_at,
    }


def process_annual_control_client(prepared, ads_data, mutation_batch):
    worksheet = prepared["worksheet"]
    target_year = prepared["target_day"].year
    print(f"Pestana procesada: {worksheet.title}")
    print(f"Cuenta Google Ads consultada: {prepared['customer_ids'][0]}")
    print(f"MCC usado: {prepared['mcc_id']}")
    print(
        "Lectura Google Ads completada en: "
        f"{ads_data['elapsed_seconds']:.2f} s"
    )
    update_and_log_account_statuses(
        worksheet,
        prepared["sem_client"],
        ads_data["account_statuses"],
        mutation_batch,
    )

    live_block = find_annual_control_live_block(worksheet)
    current_campaign_rows = ads_data["rows_by_year"][target_year]
    current_capacity = live_block["total_row"] - live_block["data_start_row"]
    required_capacity = len(current_campaign_rows) + 1
    reserved_rows = max(0, required_capacity - current_capacity)
    if reserved_rows:
        worksheet.insert_rows(
            [["" for _ in range(worksheet.col_count)] for _ in range(reserved_rows)],
            row=live_block["total_row"],
            value_input_option="USER_ENTERED",
            inherit_from_before=True,
        )
        invalidate_worksheet_values_cache(worksheet)
        live_block = find_annual_control_live_block(worksheet)

    live_year = prepared.get("live_year")
    if live_year and live_year < target_year:
        for year in range(live_year, target_year):
            period = prepared["annual_periods"][year]
            historical_result = upsert_annual_control_historical_year(
                worksheet,
                live_block,
                ads_data["rows_by_year"][year],
                period,
                mutation_batch,
            )
            action = "creado" if historical_result["created"] else "actualizado"
            print(
                f"Historico anual {year} {action}: "
                f"{historical_result['campaign_count']} campanas"
            )

    live_block = find_annual_control_live_block(worksheet)
    current_period = prepared["annual_periods"][target_year]
    campaign_rows = current_campaign_rows
    write_result = update_annual_control_block(
        worksheet,
        live_block,
        campaign_rows,
        current_period,
        mutation_batch,
        apply_live_status_rules=True,
    )
    marker_row = live_block["marker_row"] + write_result["inserted_rows"]
    queue_values_update(
        worksheet,
        f"B{marker_row}",
        [["A\u00d1OS ANTERIORES (Ordenados desde el primer a\u00f1o hasta el \u00faltimo)"]],
        mutation_batch,
    )

    total_cost = sum(row["cost"] for row in campaign_rows)
    total_budget = sum(row["total_budget"] for row in campaign_rows)
    print(
        "Periodo anual consultado: "
        f"{current_period['start_day'].isoformat()} a "
        f"{current_period['query_end_day'].isoformat()}"
    )
    print(f"Campanas encontradas: {len(campaign_rows)}")
    print(f"Total coste detectado: {total_cost:.2f} EUR")
    print(f"Presupuesto total detectado: {total_budget:.2f} EUR")
    total_inserted_rows = reserved_rows + write_result["inserted_rows"]
    print(f"Filas insertadas antes del total: {total_inserted_rows}")
    print(f"Rango limpiado: {write_result['clear_range']}")
    print(f"Rango escrito: {write_result['write_range']}")
    print(f"Fila de totales: {write_result['total_row']}")
    print("Ficha anual la ficha anual preparada para escritura.")
    return {"mode": "annual", "worksheet": worksheet.title}


def prepare_sem_client(sem_client, worksheet, target_day):
    worksheet._sem_platform_display = bool(sem_client.get('campaign_platform_display'))
    if sem_client.get("handler") == "calendar_year_campaigns":
        from .calendar_year import prepare
        return prepare(sem_client, worksheet, target_day)
    if sem_client.get("handler") == ANNUAL_CONTROL_HANDLER:
        return prepare_annual_control_client(
            sem_client,
            worksheet,
            target_day,
        )

    social_only = sem_client.get('handler') == SOCIAL_HANDLER
    ads_client_config = (
        None if social_only else load_client_config_by_mcc(sem_client["mcc_id"])
    )
    customer_ids = [] if social_only else get_sem_customer_ids(sem_client)
    account_client_configs = {} if social_only else get_sem_account_client_configs(sem_client)
    block = find_live_block(worksheet, target_day)
    try:
        saldo_targets = find_saldo_targets(
            worksheet,
            target_day,
            continuous_contract=sem_client.get(
                "continuous_contract_months",
                False,
            ),
            carry_forward_monthly_budget=sem_client.get(
                "carry_forward_monthly_budget",
                False,
            ),
        )
    except SaldoPeriodValidationError as exc:
        print(f"ERROR: {exc}")
        print("No se ha modificado el Sheet.")
        raise SystemExit(1) from exc

    period_contexts = {}
    if saldo_targets["mode"] == "active":
        period_contexts["current"] = saldo_targets["period_context"]
        previous_target = saldo_targets.get("previous_target")
        if previous_target:
            period_contexts["previous"] = previous_target["period_context"]
    else:
        closed_target = saldo_targets.get("previous_target")
        if closed_target:
            period_contexts["closed"] = closed_target["period_context"]

    prepared = {
        "sem_client": sem_client,
        "worksheet": worksheet,
        "block": block,
        "saldo_targets": saldo_targets,
        "ads_client_config": ads_client_config,
        "mcc_id": normalize_customer_id(ads_client_config["mcc_id"]) if ads_client_config else '',
        "customer_ids": customer_ids,
        "account_client_configs": account_client_configs,
        "period_contexts": period_contexts,
        "target_day": target_day,
    }

    if sem_client.get('meta_ads_contract_summary'):
        from .meta_contract import prepare_meta_contract_summary
        prepared['meta_contract'] = prepare_meta_contract_summary(
            worksheet, sem_client['meta_ads_contract_summary'],
        )

    if sem_client.get('microsoft_ads_contract_summary'):
        from .microsoft_contract import prepare_microsoft_contract_summary
        prepared['microsoft_contract'] = prepare_microsoft_contract_summary(
            worksheet, sem_client['microsoft_ads_contract_summary'],
        )

    if sem_client.get("microsoft_ads_account_id"):
        values = get_worksheet_values(worksheet)
        annual_area = find_microsoft_annual_area(values)
        title_value = values[annual_area["title_row"] - 1][
            annual_area["start_col"] - 1
        ]
        live_year = extract_explicit_year(title_value) or target_day.year
        annual_period_contexts = {
            MICROSOFT_ANNUAL_PERIOD_KEY: {
                "query_start_day": date(target_day.year, 1, 1),
                "query_end_day": target_day,
            }
        }
        archive_years = []
        if live_year < target_day.year:
            archive_years = list(range(live_year, target_day.year))
            for archive_year in archive_years:
                annual_period_contexts[
                    f"{MICROSOFT_ANNUAL_ARCHIVE_KEY_PREFIX}{archive_year}"
                ] = {
                    "query_start_day": date(archive_year, 1, 1),
                    "query_end_day": date(archive_year, 12, 31),
                }

        prepared["microsoft_annual"] = {
            **annual_area,
            "live_year": live_year,
            "target_year": target_day.year,
            "archive_years": archive_years,
            "period_contexts": annual_period_contexts,
        }

    if (
        sem_client.get("openai_ads_account_id")
        and sem_client.get("openai_ads_annual_control")
    ):
        values = get_worksheet_values(worksheet)
        annual_area = find_openai_annual_area(values)
        title_value = ""
        if not annual_area["needs_creation"]:
            title_value = values[annual_area["title_row"] - 1][
                annual_area["start_col"] - 1
            ]
        live_year = extract_explicit_year(title_value) or target_day.year
        annual_period_contexts = {
            OPENAI_ANNUAL_PERIOD_KEY: {
                "query_start_day": date(target_day.year, 1, 1),
                "query_end_day": target_day,
            }
        }
        archive_years = []
        if live_year < target_day.year:
            archive_years = list(range(live_year, target_day.year))
            for archive_year in archive_years:
                annual_period_contexts[
                    f"{OPENAI_ANNUAL_ARCHIVE_KEY_PREFIX}{archive_year}"
                ] = {
                    "query_start_day": date(archive_year, 1, 1),
                    "query_end_day": date(archive_year, 12, 31),
                }
        prepared["openai_annual"] = {
            **annual_area,
            "live_year": live_year,
            "target_year": target_day.year,
            "archive_years": archive_years,
            "period_contexts": annual_period_contexts,
        }

    return prepared


def fetch_prepared_sem_client_data(prepared):
    if prepared["sem_client"].get("handler") == "calendar_year_campaigns":
        from .calendar_year import fetch
        return fetch(prepared)
    if prepared["sem_client"].get("handler") == ANNUAL_CONTROL_HANDLER:
        return fetch_annual_control_client_data(prepared)

    started_at = time.perf_counter()
    if prepared['sem_client'].get('handler') == SOCIAL_HANDLER:
        result = fetch_social_campaign_periods(
            prepared['sem_client'], prepared['period_contexts'], prepared['target_day'],
        )
        result['elapsed_seconds'] = time.perf_counter() - started_at
        return result
    account_results = {}
    for customer_id in prepared["customer_ids"]:
        account_config = prepared.get("account_client_configs", {}).get(
            customer_id, prepared["ads_client_config"],
        )
        google_ads_client = get_thread_google_ads_client(account_config)
        account_results[customer_id] = fetch_campaign_periods(
            google_ads_client,
            customer_id,
            prepared["period_contexts"],
            prepared["target_day"],
        )
    result = combine_google_ads_account_results(account_results)
    sem_client = prepared["sem_client"]
    microsoft_account_id = sem_client.get("microsoft_ads_account_id")
    if microsoft_account_id:
        from microsoft_ads.provider import fetch_microsoft_campaign_periods

        microsoft_period_contexts = dict(prepared["period_contexts"])
        microsoft_annual = prepared.get("microsoft_annual")
        if microsoft_annual:
            microsoft_period_contexts.update(
                microsoft_annual["period_contexts"]
            )

        microsoft_result = fetch_microsoft_campaign_periods(
            account_id=microsoft_account_id,
            customer_id=sem_client["microsoft_ads_customer_id"],
            period_contexts=microsoft_period_contexts,
            campaign_prefix=sem_client.get(
                "microsoft_ads_campaign_prefix",
                "MS Ads - ",
            ),
            source_currency=sem_client.get("microsoft_ads_currency"),
        )
        for period_key in prepared["period_contexts"]:
            microsoft_rows = microsoft_result["period_rows"].get(period_key, [])
            result["period_rows"].setdefault(period_key, []).extend(
                microsoft_rows
            )
            result["period_rows"][period_key] = sort_campaign_rows(
                result["period_rows"][period_key]
            )
        result["microsoft_ads"] = {
            "account_id": microsoft_account_id,
            "source_currency": (
                microsoft_result.get("source_currency")
                or sem_client.get("microsoft_ads_currency")
            ),
            "currency": (
                microsoft_result.get("currency")
                or sem_client.get("microsoft_ads_currency")
            ),
            "exchange_rate_source": microsoft_result.get(
                "exchange_rate_source"
            ),
            "annual_current_rows": sort_campaign_rows(
                microsoft_result["period_rows"].get(
                    MICROSOFT_ANNUAL_PERIOD_KEY,
                    [],
                )
            ),
            "annual_archive_rows": {
                year: sort_campaign_rows(
                    microsoft_result["period_rows"].get(
                        f"{MICROSOFT_ANNUAL_ARCHIVE_KEY_PREFIX}{year}",
                        [],
                    )
                )
                for year in (
                    microsoft_annual.get("archive_years", [])
                    if microsoft_annual else []
                )
            },
        }
    openai_account_id = sem_client.get("openai_ads_account_id")
    if openai_account_id:
        from openai_ads.provider import fetch_openai_campaign_periods

        openai_period_contexts = dict(prepared["period_contexts"])
        openai_annual = prepared.get("openai_annual")
        if openai_annual:
            openai_period_contexts.update(openai_annual["period_contexts"])
        openai_result = fetch_openai_campaign_periods(
            account_id=openai_account_id,
            period_contexts=openai_period_contexts,
            campaign_prefix=sem_client.get(
                "openai_ads_campaign_prefix",
                "ChatGPT Ads - ",
            ),
            as_of_day=prepared["target_day"],
        )
        for period_key in prepared["period_contexts"]:
            openai_rows = openai_result["period_rows"].get(period_key, [])
            result["period_rows"].setdefault(period_key, []).extend(
                openai_rows
            )
            result["period_rows"][period_key] = sort_campaign_rows(
                result["period_rows"][period_key]
            )
        result["openai_ads"] = {
            "account_id": openai_result["account_id"],
            "currency": openai_result["currency"],
            "annual_current_rows": sort_campaign_rows(
                openai_result["period_rows"].get(
                    OPENAI_ANNUAL_PERIOD_KEY,
                    [],
                )
            ),
            "annual_archive_rows": {
                year: sort_campaign_rows(
                    openai_result["period_rows"].get(
                        f"{OPENAI_ANNUAL_ARCHIVE_KEY_PREFIX}{year}",
                        [],
                    )
                )
                for year in (
                    openai_annual.get("archive_years", [])
                    if openai_annual else []
                )
            },
        }
    if sem_client.get('microsoft_ads_contract_summary'):
        from microsoft_ads.provider import fetch_microsoft_contract_summary
        config = sem_client['microsoft_ads_contract_summary']
        summary = prepared['microsoft_contract']
        result['microsoft_contract'] = fetch_microsoft_contract_summary(
            config['account_id'], config['customer_id'], config['account_number'],
            summary['start_day'], summary['end_day'], prepared['target_day'],
        )
    if sem_client.get('meta_ads_contract_summary'):
        from meta_ads.contract_provider import fetch_meta_contract_summary
        summary = prepared['meta_contract']
        result['meta_contract'] = fetch_meta_contract_summary(
            sem_client['meta_ads_contract_summary'], summary['start_day'],
            summary['end_day'], prepared['target_day'],
        )
    if sem_client.get('campaign_platform_display'):
        from .platform_display import platform_campaign
        for rows in result['period_rows'].values():
            rows[:] = [platform_campaign(row) for row in rows]
        for provider in ('microsoft_ads', 'openai_ads'):
            if provider in result:
                data = result[provider]
                data['annual_current_rows'] = [platform_campaign(row) for row in data['annual_current_rows']]
                for year, rows in data['annual_archive_rows'].items():
                    data['annual_archive_rows'][year] = [platform_campaign(row) for row in rows]
    result["elapsed_seconds"] = time.perf_counter() - started_at
    return result


def update_sem_account_statuses(worksheet, sem_client, account_statuses, mutation_batch):
    if sem_client.get('handler') == SOCIAL_HANDLER:
        status = aggregate_account_operational_status(account_statuses)
        update_account_status_cell(worksheet, sem_client, status, mutation_batch)
        print(f'Estado operativo social: {status}')
        return
    update_and_log_account_statuses(worksheet, sem_client, account_statuses, mutation_batch)


def process_sem_client(prepared, ads_data, mutation_batch):
    from .vista_scope import navigation_request
    if "navigation_target" in prepared:
        worksheet = prepared["worksheet"]
        mutation_batch.add_requests([navigation_request(worksheet.id, *prepared["navigation_target"])])
    sem_client = prepared["sem_client"]
    if sem_client.get("handler") == "calendar_year_campaigns":
        from .calendar_year import process
        return process(prepared, ads_data, mutation_batch)
    if sem_client.get("handler") == ANNUAL_CONTROL_HANDLER:
        return process_annual_control_client(
            prepared,
            ads_data,
            mutation_batch,
        )

    worksheet = prepared["worksheet"]
    block = prepared["block"]
    saldo_targets = prepared["saldo_targets"]
    target_day = prepared["target_day"]
    uses_legacy_historical_totals = bool(
        sem_client.get("legacy_historical_totals")
    )

    from .summary_borders import repair_summary_borders
    repair_summary_borders(worksheet, mutation_batch)

    if prepared.get('meta_contract'):
        from .meta_contract import write_meta_contract_summary
        write_meta_contract_summary(worksheet, prepared['meta_contract'],
                                    ads_data['meta_contract'], mutation_batch)

    if prepared.get('microsoft_contract'):
        from .microsoft_contract import write_microsoft_contract_summary
        write_microsoft_contract_summary(worksheet, prepared['microsoft_contract'],
                                         ads_data['microsoft_contract'], mutation_batch)

    for update in saldo_targets.get("continuous_contract_updates", []):
        queue_values_update(
            worksheet,
            update["cell"],
            [[update["value"]]],
            mutation_batch,
        )
    if saldo_targets.get("continuous_contract_updates"):
        print(
            "Meses intermedios del contrato continuo completados: "
            + ", ".join(
                update["cell"]
                for update in saldo_targets["continuous_contract_updates"]
            )
        )

    budget_carry_forward = saldo_targets.get(
        "budget_carry_forward",
        {},
    )
    if budget_carry_forward.get("applied"):
        budget_update = budget_carry_forward["value_update"]
        queue_values_update(
            worksheet,
            budget_update["cell"],
            [[budget_update["value"]]],
            mutation_batch,
        )
        print(
            "Presupuesto mensual arrastrado: "
            f"{budget_update['source_cell']} -> {budget_update['cell']} "
            f"({budget_carry_forward['period_label']})."
        )

    monthly_renewal = saldo_targets.get("monthly_renewal", {})
    queue_monthly_renewal(worksheet, monthly_renewal, mutation_batch)
    if monthly_renewal.get("applied"):
        copied_budget = next(
            (
                update
                for update in monthly_renewal["value_updates"]
                if update["kind"] == "monthly_budget"
            ),
            None,
        )
        contract_end = next(
            update
            for update in monthly_renewal["value_updates"]
            if update["kind"] == "contract_end"
        )
        print(
            (
                "Renovacion mensual recuperada: "
                if monthly_renewal.get("catch_up")
                else "Renovacion mensual aplicada: "
            )
            +
            f"{monthly_renewal['period_label']}; "
            + (
                f"presupuesto copiado en {copied_budget['cell']}; "
                if copied_budget
                else "presupuesto mensual ya informado; "
            )
            + (
                "Fecha Fin ampliada a "
                f"{contract_end['date'].isoformat()} "
                f"en {contract_end['cell']}."
            )
        )

    microsoft_ads = ads_data.get("microsoft_ads")
    openai_ads = ads_data.get("openai_ads")
    annual_result = None
    openai_annual_result = None
    if microsoft_ads and prepared.get("microsoft_annual"):
        annual_result = update_microsoft_annual_control(
            worksheet,
            prepared["microsoft_annual"],
            microsoft_ads,
            mutation_batch,
        )
        block = find_live_block(worksheet, target_day)
        prepared["block"] = block
    if openai_ads and prepared.get("openai_annual"):
        openai_annual_result = update_openai_annual_control(
            worksheet,
            prepared["openai_annual"],
            openai_ads,
            mutation_batch,
        )
        block = find_live_block(worksheet, target_day)
        prepared["block"] = block

    historical_spacing = compact_historical_total_spacing(
        worksheet,
        min(block["columns"].values()),
        max(block["columns"].values()),
        mutation_batch,
    )
    if historical_spacing["deleted_rows"]:
        block["values"] = get_worksheet_values(worksheet, refresh=True)
    repaired_historical_totals = repair_missing_historical_totals(
        worksheet,
        block,
        mutation_batch,
    )
    orphan_historical_ranges = clean_orphan_historical_metric_tails(
        worksheet,
        block,
        mutation_batch,
    )

    print(f"Pestana procesada: {worksheet.title}")
    print(
        "Bloque vivo detectado: "
        f"periodo fila {block['period_row']}, "
        f"cabeceras fila {block['header_row']}"
    )
    if historical_spacing["deleted_rows"]:
        print(
            "Historicos compactados: "
            f"{historical_spacing['blocks_compacted']} bloques, "
            f"{historical_spacing['deleted_rows']} filas eliminadas."
        )
    if repaired_historical_totals:
        print(
            "Totales historicos reparados: "
            + ", ".join(
                f"{item['period']} ({item['campaign_count']} campanas, "
                f"fila {item['total_row']})"
                for item in repaired_historical_totals
            )
        )
    if orphan_historical_ranges:
        print(
            "Restos historicos aislados limpiados: "
            + ", ".join(orphan_historical_ranges)
        )

    if saldo_targets.get("ignored_period_cells"):
        print(
            "Periodos con fecha incierta ignorados: "
            + ", ".join(saldo_targets["ignored_period_cells"])
        )

    customer_ids = prepared["customer_ids"]
    customer_label = (
        "Cuenta Google Ads consultada"
        if len(customer_ids) == 1
        else "Cuentas Google Ads consultadas"
    )
    if customer_ids:
        print(f"{customer_label}: {', '.join(customer_ids)}")
        account_configs = prepared.get("account_client_configs", {})
        if len({config["mcc_id"] for config in account_configs.values()}) > 1:
            print("MCC por cuenta: " + ", ".join(
                f"{cid} -> {account_configs[cid]['mcc_id']}" for cid in customer_ids
            ))
        else:
            print(f"MCC usado: {prepared['mcc_id']}")
    else:
        print('Fuentes consultadas: Meta Ads y LinkedIn Ads; sin Google Ads.')
    account_statuses = ads_data["account_statuses"]
    technical_account_statuses = ads_data.get(
        "technical_account_statuses",
        {},
    )
    for customer_id in customer_ids:
        technical_status = technical_account_statuses.get(
            customer_id,
            "unknown",
        )
        print(
            "Estado operativo de cuenta Google Ads: "
            f"{customer_id} = {account_statuses[customer_id]} "
            f"(estado tecnico: {technical_status})"
        )
    print(
        ('Lectura social completada en: ' if not customer_ids else 'Lectura Google Ads completada en: ')
        + f"{ads_data['elapsed_seconds']:.2f} s"
    )
    if microsoft_ads:
        print(
            "Cuenta Microsoft Ads consultada: "
            f"{microsoft_ads['account_id']} "
            f"({microsoft_ads['source_currency']} convertido a "
            f"{microsoft_ads['currency']} con "
            f"{microsoft_ads['exchange_rate_source']})"
        )
        if annual_result:
            print(
                "Control anual Microsoft Ads actualizado: "
                f"{annual_result['year']} / "
                f"{annual_result['campaign_count']} campanas / "
                f"{annual_result['total_cost']:.2f} EUR / "
                f"{annual_result['range']}"
            )
            if annual_result["archives"]:
                print(
                    "Anos Microsoft Ads archivados: "
                    + ", ".join(
                        f"{item['year']} ({item['range']})"
                        for item in annual_result["archives"]
                    )
                )

    if openai_ads:
        print(
            "Cuenta ChatGPT Ads consultada: "
            f"{openai_ads['account_id']} ({openai_ads['currency']})"
        )
        if openai_annual_result:
            print(
                "Control anual ChatGPT Ads actualizado: "
                f"{openai_annual_result['year']} / "
                f"{openai_annual_result['campaign_count']} campanas / "
                f"{openai_annual_result['total_cost']:.2f} EUR / "
                f"{openai_annual_result['range']}"
            )
            if openai_annual_result["archives"]:
                print(
                    "Anos ChatGPT Ads archivados: "
                    + ", ".join(
                        f"{item['year']} ({item['range']})"
                        for item in openai_annual_result["archives"]
                    )
                )

    if saldo_targets["mode"] == "pause":
        closed_target = saldo_targets.get("previous_target")
        next_target = saldo_targets.get("next_target")
        print(
            "No hay un periodo de saldo vigente para "
            f"{target_day.isoformat()}. La tabla MES ACTUAL no se modificara."
        )

        if next_target:
            next_period = next_target["period_context"]
            print(
                "Proximo periodo configurado: "
                f"{next_period['label']} "
                f"({next_period['start_day'].isoformat()} a "
                f"{next_period['end_day'].isoformat()})"
            )
        else:
            print("No hay un periodo futuro configurado en el bloque de saldo.")

        if not closed_target:
            update_sem_account_statuses(
                worksheet,
                sem_client,
                account_statuses,
                mutation_batch,
            )
            total_label_result = normalize_live_total_label(
                worksheet,
                block,
                mutation_batch,
            )
            if total_label_result["changed"]:
                print(
                    "Etiqueta del total normalizada en: "
                    f"{total_label_result['cell']}"
                )
            update_current_saldo_month_marker(
                worksheet,
                saldo_targets,
                mutation_batch,
            )
            if uses_legacy_historical_totals:
                normalized_rows = normalize_legacy_historical_historical_totals(
                    worksheet,
                    mutation_batch,
                )
                print(
                    "Totales historicos de la ficha con totales historicos heredados normalizados: "
                    + ", ".join(map(str, normalized_rows))
                )
            currency_rows = apply_existing_total_currency_formats(
                worksheet,
                mutation_batch,
            )
            print(
                "Unidad EUR aplicada a totales existentes: "
                + ", ".join(map(str, currency_rows))
            )
            print("No hay un periodo cerrado anterior que consolidar.")
            print("Marcador 'Mes en curso' retirado.")
            print("Ficha SEM en pausa preparada para escritura.")
            return {"mode": "pause", "worksheet": worksheet.title}

        closed_period = closed_target["period_context"]
        closed_start_day = closed_period["query_start_day"]
        closed_end_day = closed_period["query_end_day"]
        print(
            "Ultimo periodo cerrado consultado: "
            f"{closed_start_day.isoformat()} a {closed_end_day.isoformat()}"
        )
        closed_campaign_rows = list(ads_data["period_rows"]["closed"])
        preserved_closed_rows = extract_preserved_historical_campaign_rows(
            worksheet,
            sem_client,
            closed_period,
            block,
        )
        closed_campaign_rows.extend(preserved_closed_rows)
        closed_total_cost = round(
            sum(row["raw_cost"] for row in closed_campaign_rows),
            2,
        )
        print(f"Campanas encontradas: {len(closed_campaign_rows)}")
        if preserved_closed_rows:
            print(
                "Filas externas conservadas en historico: "
                f"{len(preserved_closed_rows)}"
            )
        print(
            "Total coste del ultimo periodo cerrado detectado: "
            f"{closed_total_cost:.2f} EUR"
        )

        update_sem_account_statuses(
            worksheet,
            sem_client,
            account_statuses,
            mutation_batch,
        )
        total_label_result = normalize_live_total_label(
            worksheet,
            block,
            mutation_batch,
        )
        if total_label_result["changed"]:
            print(
                "Etiqueta del total normalizada en: "
                f"{total_label_result['cell']}"
            )

        historical_result = upsert_historical_period(
            worksheet,
            block,
            closed_campaign_rows,
            closed_period,
            mutation_batch,
            force_formats=has_external_ads_sources(sem_client),
        )
        history_action = "creado" if historical_result["created"] else "actualizado"
        print(
            f"Historico del ultimo periodo cerrado {history_action}: "
            f"{historical_result['period']} en "
            f"{historical_result['write']['period_cell']}:"
            f"{gspread.utils.rowcol_to_a1(historical_result['write']['end_row'], max(block['columns'].values()))}"
        )
        if uses_legacy_historical_totals:
            normalized_rows = normalize_legacy_historical_historical_totals(
                worksheet,
                mutation_batch,
                force_total_rows={historical_result["write"]["total_row"]},
            )
            print(
                "Totales historicos de la ficha con totales historicos heredados normalizados: "
                + ", ".join(map(str, normalized_rows))
            )
        closed_result = update_closed_real_spend_during_pause(
            worksheet,
            closed_total_cost,
            saldo_targets,
            mutation_batch,
        )
        print(
            "Gasto real del ultimo periodo cerrado escrito: "
            f"{closed_result['monthly_cost']:.2f} EUR en "
            f"{closed_result['cell']} "
            f"({closed_result['group_label']} / "
            f"{closed_result['month_label']})"
        )
        currency_rows = apply_existing_total_currency_formats(
            worksheet,
            mutation_batch,
            metric_totals_by_row=(
                {
                    historical_result["write"]["total_row"]:
                        summarize_campaign_metrics(closed_campaign_rows)
                }
                if historical_result["write"].get("total_row")
                else {}
            ),
        )
        print(
            "Unidad EUR aplicada a totales existentes: "
            + ", ".join(map(str, currency_rows))
        )
        print("Marcador 'Mes en curso' retirado.")
        print("Ficha SEM en pausa preparada para escritura.")
        return {"mode": "pause", "worksheet": worksheet.title}

    period_context = saldo_targets["period_context"]
    start_day = period_context["query_start_day"]
    end_day = period_context["query_end_day"]
    print(
        "Periodo detectado desde saldo: "
        f"{period_context['label']} "
        f"({period_context['start_day'].isoformat()} a "
        f"{period_context['end_day'].isoformat()})"
    )

    previous_target = saldo_targets.get("previous_target")
    previous_campaign_rows = None
    previous_total_cost = None
    if previous_target:
        previous_period = previous_target["period_context"]
        previous_start_day = previous_period["query_start_day"]
        previous_end_day = previous_period["query_end_day"]
        print(
            "Periodo anterior consultado: "
            f"{previous_start_day.isoformat()} a "
            f"{previous_end_day.isoformat()}"
        )
        previous_campaign_rows = list(ads_data["period_rows"]["previous"])
        preserved_previous_rows = extract_preserved_historical_campaign_rows(
            worksheet,
            sem_client,
            previous_period,
            block,
        )
        previous_campaign_rows.extend(preserved_previous_rows)
        previous_total_cost = round(
            sum(row["raw_cost"] for row in previous_campaign_rows),
            2,
        )
        if preserved_previous_rows:
            print(
                "Filas externas conservadas en el periodo anterior: "
                f"{len(preserved_previous_rows)}"
            )
        print(
            "Total coste del periodo anterior detectado: "
            f"{previous_total_cost:.2f} EUR"
        )

    else:
        print("Periodo anterior no disponible en el bloque de saldo.")

    print(f"Periodo actual consultado: {start_day.isoformat()} a {end_day.isoformat()}")
    campaign_rows = ads_data["period_rows"]["current"]
    source_costs = campaign_costs_by_source(campaign_rows)
    google_total_cost = source_costs["google"]
    microsoft_current_cost = source_costs["microsoft"]
    openai_current_cost = source_costs["openai"]
    total_cost = round(sum(source_costs.values()), 2)
    print(f"Campanas encontradas: {len(campaign_rows)}")
    if customer_ids:
        print(f"Total coste Google Ads detectado: {google_total_cost:.2f} EUR")
    else:
        print(f"Coste Meta Ads: {source_costs.get('meta', 0):.2f} EUR")
        print(f"Coste LinkedIn Ads: {source_costs.get('linkedin', 0):.2f} EUR")
        print(f"Total combinado para saldo: {total_cost:.2f} EUR")
    if microsoft_ads:
        print(
            "Coste Microsoft Ads incluido en filas y saldo: "
            f"{microsoft_current_cost:.2f} {microsoft_ads['currency']}"
        )
        print(f"Total combinado para saldo: {total_cost:.2f} EUR")
    if openai_ads:
        print(
            "Coste ChatGPT Ads incluido en filas y saldo: "
            f"{openai_current_cost:.2f} {openai_ads['currency']}"
        )
        print(f"Total combinado para saldo: {total_cost:.2f} EUR")

    existing_period_label = ""
    if len(block["values"]) >= block["period_row"]:
        period_row_values = block["values"][block["period_row"] - 1]
        if len(period_row_values) >= block["period_col"]:
            existing_period_label = period_row_values[block["period_col"] - 1]
    update_saldo_marker = (
        normalize_text(existing_period_label)
        != normalize_text(period_context["label"])
    )

    # Se prepara primero el bloque vivo. Si necesita filas nuevas, cualquier
    # historico situado debajo se detecta despues con sus coordenadas reales.
    write_result = update_live_block(
        worksheet,
        campaign_rows,
        block,
        period_context,
        mutation_batch,
    )

    if previous_target:
        historical_result = upsert_historical_period(
            worksheet,
            block,
            previous_campaign_rows,
            previous_period,
            mutation_batch,
            force_formats=has_external_ads_sources(sem_client),
        )
        history_action = "creado" if historical_result["created"] else "actualizado"
        print(
            f"Historico del periodo anterior {history_action}: "
            f"{historical_result['period']} en "
            f"{historical_result['write']['period_cell']}:"
            f"{gspread.utils.rowcol_to_a1(historical_result['write']['end_row'], max(block['columns'].values()))}"
        )

    if uses_legacy_historical_totals:
        normalized_rows = normalize_legacy_historical_historical_totals(
            worksheet,
            mutation_batch,
            force_total_rows=(
                {historical_result["write"]["total_row"]}
                if previous_target
                and historical_result["write"].get("total_row")
                else set()
            ),
        )
        print(
            "Totales historicos de la ficha con totales historicos heredados normalizados: "
            + ", ".join(map(str, normalized_rows))
        )

    update_sem_account_statuses(
        worksheet,
        sem_client,
        account_statuses,
        mutation_batch,
    )
    saldo_result = update_monthly_real_spend(
        worksheet,
        target_day,
        total_cost,
        previous_total_cost=previous_total_cost,
        saldo_targets=saldo_targets,
        mutation_batch=mutation_batch,
        update_marker=update_saldo_marker,
    )

    print(f"Periodo escrito en: {write_result['period_cell']}")
    print(f"Texto periodo: {write_result['period_label']}")
    print(f"Filas insertadas antes del total: {write_result['inserted_rows']}")
    print(f"Filas eliminadas antes del total: {write_result['deleted_rows']}")
    print(f"Rango limpiado: {write_result['clear_range']}")
    if write_result["obsolete_clear_ranges"]:
        print(
            "Columnas de metricas retiradas y limpiadas: "
            + ", ".join(write_result["obsolete_clear_ranges"])
        )
    print(f"Rango escrito: {write_result['write_range']}")
    print(
        "Gasto real mensual escrito: "
        f"{saldo_result['monthly_cost']:.2f} EUR en {saldo_result['cell']} "
        f"({saldo_result['group_label']} / {saldo_result['month_label']})"
    )
    if saldo_result["previous"]:
        previous_result = saldo_result["previous"]
        print(
            "Gasto real del periodo anterior escrito: "
            f"{previous_result['monthly_cost']:.2f} EUR en "
            f"{previous_result['cell']} "
            f"({previous_result['group_label']} / "
            f"{previous_result['month_label']})"
        )
    total_metric_overrides = {
        write_result["total_row"]: summarize_campaign_metrics(campaign_rows),
    }
    if previous_target and historical_result["write"].get("total_row"):
        total_metric_overrides[historical_result["write"]["total_row"]] = (
            summarize_campaign_metrics(previous_campaign_rows)
        )
    currency_rows = apply_existing_total_currency_formats(
        worksheet,
        mutation_batch,
        metric_totals_by_row=total_metric_overrides,
    )
    print(
        "Unidad EUR aplicada a totales existentes: "
        + ", ".join(map(str, currency_rows))
    )
    print("Ficha SEM preparada para escritura.")
    return {"mode": "active", "worksheet": worksheet.title}


def main():
    started_at = time.perf_counter()
    args = parse_args()
    target_day = (
        date.fromisoformat(args.fecha_operativa)
        if args.fecha_operativa
        else today_in_spain()
    )
    print(
        f"Fecha operativa ({SPAIN_TIMEZONE_NAME}): "
        f"{target_day.isoformat()}"
    )

    process_all, selected_clients = resolve_sem_client_selection(args.cliente)
    if process_all:
        print(f"Ejecucion general solicitada: {len(selected_clients)} fichas.")

    print("Abriendo Sheet de control SEM...")
    sheets_client = create_sheets_client()
    spreadsheet = sheets_client.open_by_key(CONTROL_SEM_SPREADSHEET_ID)
    print(f"Sheet abierto: {spreadsheet.title}")
    worksheets_by_title = {
        worksheet.title: worksheet
        for worksheet in spreadsheet.worksheets()
    }
    from .vista_scope import read_scope, filter_clients, check_scope_unchanged
    scope_clients = list(SEM_CLIENTS.values())
    execution_scope = read_scope(spreadsheet, worksheets_by_title, scope_clients)
    selected_clients, excluded_clients = filter_clients(selected_clients, worksheets_by_title, execution_scope)
    for client in excluded_clients:
        print(f"Ficha excluida por Vista Global: {client['worksheet_name']}")
    if not process_all and excluded_clients:
        raise SystemExit("Ficha excluida: solo se ejecutan el bloque principal y Natalia de Vista Global.")
    if not selected_clients:
        print("No hay fichas elegibles en el bloque principal ni en Natalia.")
        return
    failures = []
    prepared_clients = []

    selected_worksheets = []
    for sem_client in selected_clients:
        worksheet = worksheets_by_title.get(sem_client["worksheet_name"])
        if worksheet is None:
            error = "No existe la pestana configurada."
            if not process_all:
                raise SystemExit(error)
            failures.append((sem_client["worksheet_name"], error))
            continue
        selected_worksheets.append(worksheet)
        worksheet._sem_platform_display = bool(sem_client.get('campaign_platform_display'))

    from .dimensions import protect_dimensions, verify_dimensions
    dimension_worksheets = selected_worksheets + [worksheets_by_title[VISTA_GLOBAL_WORKSHEET_NAME]]
    protect_dimensions(spreadsheet, dimension_worksheets)
    print(
        "Precargando pestanas de Sheets en una sola lectura: "
        f"{len(selected_worksheets)}"
    )
    prime_worksheet_values_cache(spreadsheet, selected_worksheets)
    prime_monthly_renewal_notes(
        spreadsheet,
        selected_worksheets,
        target_day,
    )

    if args.normalizar_formatos_historicos:
        print(
            "Normalizando formatos historicos existentes "
            "(operacion puntual)..."
        )
        format_batch = SpreadsheetMutationBatch(spreadsheet)
        pending_format_names = []
        normalized_totals = {
            "worksheets": 0,
            "blocks": 0,
            "campaigns": 0,
            "totals": 0,
            "sorted_blocks": 0,
            "period_labels": 0,
        }

        for sem_client in selected_clients:
            if sem_client.get("handler") in (ANNUAL_CONTROL_HANDLER, "calendar_year_campaigns"):
                continue
            worksheet = worksheets_by_title.get(sem_client["worksheet_name"])
            if worksheet is None:
                continue

            checkpoint = format_batch.checkpoint()
            try:
                normalized = normalize_existing_standard_block_formats(
                    worksheet,
                    format_batch,
                )
            except (Exception, SystemExit) as exc:
                format_batch.rollback(checkpoint)
                if not process_all:
                    raise
                failures.append((
                    sem_client["worksheet_name"],
                    f"Normalizacion historica: {exc}",
                ))
                print(
                    "ERROR normalizando formatos historicos de "
                    f"{sem_client['worksheet_name']}: {exc}"
                )
                continue

            format_batch.mark_client()
            pending_format_names.append(sem_client["worksheet_name"])
            normalized_totals["worksheets"] += 1
            for key in (
                "blocks",
                "campaigns",
                "totals",
                "sorted_blocks",
                "period_labels",
            ):
                normalized_totals[key] += normalized[key]

            # Los historicos largos generan muchas operaciones de formato.
            # Se limita cada envio a tres fichas para mantener peticiones
            # pequenas y recuperables.
            if len(pending_format_names) >= 3:
                check_scope_unchanged(spreadsheet, worksheets_by_title, scope_clients, execution_scope)
                flush_result = format_batch.flush()
                print(
                    "Formatos historicos normalizados: "
                    f"{', '.join(pending_format_names)} "
                    f"({flush_result['format_requests']} operaciones)."
                )
                pending_format_names = []

        if pending_format_names:
            check_scope_unchanged(spreadsheet, worksheets_by_title, scope_clients, execution_scope)
            flush_result = format_batch.flush()
            print(
                "Formatos historicos normalizados: "
                f"{', '.join(pending_format_names)} "
                f"({flush_result['format_requests']} operaciones)."
            )

        conditional_batch = SpreadsheetMutationBatch(spreadsheet)
        changed_conditional_rules = (
            normalize_enabled_conditional_format_colors(
                spreadsheet,
                selected_worksheets,
                conditional_batch,
            )
        )
        check_scope_unchanged(spreadsheet, worksheets_by_title, scope_clients, execution_scope)
        conditional_batch.flush()
        if changed_conditional_rules:
            changed_rule_names = sorted({
                item["worksheet"]
                for item in changed_conditional_rules
            })
            print(
                "Reglas condicionales `enabled` corregidas: "
                f"{len(changed_conditional_rules)} en "
                f"{', '.join(changed_rule_names)}."
            )

        print(
            "Normalizacion historica completada: "
            f"{normalized_totals['worksheets']} fichas, "
            f"{normalized_totals['blocks']} bloques, "
            f"{normalized_totals['campaigns']} campanas y "
            f"{normalized_totals['totals']} totales; "
            f"{normalized_totals['sorted_blocks']} bloques reordenados y "
            f"{normalized_totals['period_labels']} periodos renombrados."
        )

    for sem_client in selected_clients:
        worksheet = worksheets_by_title.get(sem_client["worksheet_name"])
        if worksheet is None:
            continue
        try:
            prepared = prepare_sem_client(sem_client, worksheet, target_day)
            prepared["navigation_target"] = (spreadsheet.id, worksheets_by_title[VISTA_GLOBAL_WORKSHEET_NAME].id)
            prepared_clients.append(prepared)
        except (Exception, SystemExit) as exc:
            if not process_all:
                raise
            failures.append((sem_client["worksheet_name"], str(exc)))
            print(
                "ERROR preparando ficha "
                f"{sem_client['worksheet_name']}: {exc}"
            )

    ads_results = {}
    if prepared_clients:
        worker_count = min(MAX_ADS_WORKERS, len(prepared_clients))
        print(
            "Consultando Google Ads en paralelo: "
            f"{worker_count} trabajadores, "
            f"{len(prepared_clients)} cuentas."
        )
        with ThreadPoolExecutor(max_workers=worker_count) as executor:
            futures = {
                executor.submit(
                    fetch_prepared_sem_client_data,
                    prepared,
                ): prepared
                for prepared in prepared_clients
            }
            completed = 0
            for future in as_completed(futures):
                prepared = futures[future]
                worksheet_name = prepared["worksheet"].title
                completed += 1
                try:
                    ads_results[worksheet_name] = future.result()
                    print(
                        "Lectura Ads completada "
                        f"{completed}/{len(prepared_clients)}: "
                        f"{worksheet_name}"
                    )
                except (Exception, SystemExit) as exc:
                    if not process_all:
                        raise
                    failures.append((worksheet_name, str(exc)))
                    print(
                        f"ERROR leyendo Google Ads para {worksheet_name}: "
                        f"{exc}"
                    )

    mutation_batch = SpreadsheetMutationBatch(spreadsheet)
    check_scope_unchanged(spreadsheet, worksheets_by_title, scope_clients, execution_scope)
    pending_batch_names = []
    processed_count = 0

    for prepared in prepared_clients:
        worksheet_name = prepared["worksheet"].title
        ads_data = ads_results.get(worksheet_name)
        if ads_data is None:
            continue
        processed_count += 1
        print()
        print("=" * 72)
        print(
            f"Ficha {processed_count}/{len(ads_results)}: "
            f"{worksheet_name}"
        )
        print("=" * 72)

        checkpoint = mutation_batch.checkpoint()
        try:
            process_sem_client(
                prepared,
                ads_data,
                mutation_batch,
            )
            mutation_batch.mark_client()
            pending_batch_names.append(worksheet_name)
        except (Exception, SystemExit) as exc:
            mutation_batch.rollback(checkpoint)
            if not process_all:
                raise
            failures.append((worksheet_name, str(exc)))
            print(
                "ERROR procesando ficha "
                f"{worksheet_name}: {exc}"
            )

        if mutation_batch.should_flush():
            check_scope_unchanged(spreadsheet, worksheets_by_title, scope_clients, execution_scope)
            flush_result = mutation_batch.flush()
            print(
                "Lote escrito correctamente: "
                f"{len(pending_batch_names)} fichas, "
                f"{flush_result['value_ranges']} rangos de valores y "
                f"{flush_result['format_requests']} operaciones de formato."
            )
            pending_batch_names = []

    if pending_batch_names:
        check_scope_unchanged(spreadsheet, worksheets_by_title, scope_clients, execution_scope)
        flush_result = mutation_batch.flush()
        print(
            "Lote final escrito correctamente: "
            f"{len(pending_batch_names)} fichas, "
            f"{flush_result['value_ranges']} rangos de valores y "
            f"{flush_result['format_requests']} operaciones de formato."
        )

    verify_dimensions(spreadsheet, dimension_worksheets)
    if failures:
        print()
        print(f"Ejecucion general incompleta: {len(failures)} errores.")
        for worksheet_name, error in failures:
            print(f"  - {worksheet_name}: {error}")
        raise SystemExit(1)

    vista_moves = reconcile_vista_global_status_blocks(spreadsheet)
    if not vista_moves:
        print("Vista Global: clientes activos y Stand By ya estaban correctos.")

    if process_all:
        print()
        print(
            "Ejecucion general completada correctamente: "
            f"{len(selected_clients)} fichas."
        )

    elapsed_seconds = time.perf_counter() - started_at
    print(
        "Duracion total: "
        f"{elapsed_seconds:.1f} segundos "
        f"({elapsed_seconds / 60:.2f} minutos)."
    )
