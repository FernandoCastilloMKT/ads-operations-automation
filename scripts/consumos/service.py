"""Consumption: service."""

from .accounts import (
    format_account_display_name,
    normalize_account_status,
    sort_account_matrix,
    split_known_status_suffix,
)

from .aggregation import (
    build_accounts_for_daily_sheet,
    build_accounts_for_monthly_sheet,
    build_monthly_costs,
    filter_daily_costs_by_period,
    filter_monthly_account_results,
    merge_monthly_account_results,
)

from .failures import (
    get_failed_account_ids,
    print_failed_account_queries,
)

from .google_ads import (
    build_spend_result_for_account,
    create_google_ads_client,
    fetch_client_daily_spend,
    get_account_daily_spend,
    get_account_daily_spend_with_retries,
    get_accounts_with_daily_spend,
    get_accounts_with_daily_spend_for_manager,
    get_accounts_with_monthly_spend,
    get_accounts_with_monthly_spend_for_manager,
    get_child_accounts,
    get_developer_token_for_client,
    get_manager_sources,
    get_retry_delay_seconds,
    is_retryable_error,
    is_skippable_account_error,
    merge_unique_daily_account_results,
)

from .periods import (
    add_months,
    get_current_month_labels,
    get_daily_sheet_name,
    get_full_month_dates_for_day,
    get_historical_backfill_period,
    get_month_abbr,
    get_month_dates,
    get_month_delta,
    get_month_header_labels,
    get_month_key_from_day,
    get_month_keys_between,
    get_month_name_labels,
    get_month_start_date,
    get_monthly_period,
    get_monthly_sheet_name,
    get_monthly_start_month,
    get_previous_month_day,
    get_quick_dates,
    get_reporting_start_date,
    limit_reporting_month_keys,
    parse_daily_google_ads_sheet_month,
    parse_month_key,
)

from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from datetime import date
from datetime import timedelta
from dotenv import load_dotenv
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.oauth2.service_account import Credentials
from gspread.exceptions import WorksheetNotFound
from pathlib import Path
from runtime_config import load_consumption_runtime_config
import argparse
import gspread
import json
import logging
import os
import re
import sys
import time
import unicodedata
from .runtime import (
    ACCOUNT_METADATA,
    BASE_DIR,
    DRY_RUN,
    FAILED_ACCOUNT_QUERIES,
    client_id,
    client_secret,
    developer_token,
    env_path,
    refresh_token,
    sheets_credentials_path,
    vendor_path,
)
from .constants import (
    ACCOUNT_STATUS_LABELS,
    DEFAULT_PARALLEL_WORKERS,
    DEFAULT_QUICK_LOOKBACK_DAYS,
    EURO_NUMBER_FORMAT_PATTERN,
    MAX_GOOGLE_ADS_RETRIES,
    RETRY_DELAY_SECONDS,
    SCOPES,
)

from .selection import (
    client_matches_filters,
    normalize_run_mode,
    select_clients,
)

from .sheets import (
    apply_daily_euro_formats,
    apply_euro_formats,
    apply_monthly_euro_formats,
    build_daily_summary_formula_updates,
    build_daily_total_title,
    build_euro_format_requests,
    build_id_to_row,
    build_restore_daily_column_requests,
    clear_daily_sheet_data,
    clear_monthly_sheet_data,
    clear_out_of_month_daily_cells,
    create_sheets_client,
    find_column,
    find_first_empty_row,
    find_header_row,
    find_month_column,
    find_month_columns,
    get_date_columns,
    get_header_aliases,
    get_or_create_daily_worksheet,
    get_or_create_monthly_worksheet,
    get_preserved_failed_daily_accounts,
    group_contiguous_columns,
    update_daily_sheet_date_headers,
    update_monthly_sheet_year_headers,
)

from .text import (
    normalize_customer_id,
    normalize_match_text,
    normalize_sheet_text,
    parse_sheet_number,
)

from .visibility import (
    build_sheet_reorder_requests,
    maintain_daily_sheet_visibility,
    maintain_hidden_daily_sheet_order,
    plan_daily_sheet_visibility,
    plan_daily_sheet_visibility_changes,
    plan_hidden_daily_sheet_order,
)


def parse_args():
    parser = argparse.ArgumentParser(
        description="Actualiza consumos de Google Ads en Google Sheets."
    )
    parser.add_argument(
        "--cliente",
        action="append",
        default=[],
        help=(
            "Procesa solo el cliente indicado. Se puede usar varias veces. "
            "Acepta nombre, clave publica o MCC ID."
        ),
    )
    parser.add_argument(
        "--modo",
        default=os.getenv("ACTUALIZAR_CONSUMOS_MODO", "completo"),
        help="Modo de ejecucion: completo/full o rapido/quick.",
    )
    parser.add_argument(
        "--quick-lookback-days",
        type=int,
        default=int(os.getenv("QUICK_LOOKBACK_DAYS", DEFAULT_QUICK_LOOKBACK_DAYS)),
        help="Dias recientes a recalcular en modo rapido.",
    )
    parser.add_argument(
        "--parallel-workers",
        type=int,
        default=int(os.getenv("GOOGLE_ADS_PARALLEL_WORKERS", DEFAULT_PARALLEL_WORKERS)),
        help="Numero maximo de cuentas de Google Ads consultadas en paralelo.",
    )
    parser.add_argument(
        "--backfill-year",
        type=int,
        help=(
            "Rellena la hoja historica anual desde enero hasta hoy para el "
            "ano indicado. No actualiza hojas diarias."
        ),
    )
    return parser.parse_args()


def process_daily_sheet_for_client(
    sheets_client,
    cliente,
    accounts_with_spend,
    replace_start_date=None,
    replace_end_date=None,
    target_day=None,
    create_if_missing=True,
    spreadsheet=None,
):
    target_day = target_day or date.today()
    reporting_start = get_reporting_start_date(cliente)
    if reporting_start and target_day < reporting_start:
        print("Hoja diaria anterior al inicio configurado: se conserva sin cambios.")
        return []
    daily_sheet_name = get_daily_sheet_name(cliente, target_day)

    if not daily_sheet_name:
        print("No hay hoja diaria configurada. Salto hoja diaria.")
        return []

    if spreadsheet is None:
        spreadsheet = sheets_client.open_by_key(cliente["spreadsheet_id"])

    try:
        worksheet = get_or_create_daily_worksheet(
            spreadsheet,
            cliente,
            target_day,
            create_if_missing and not DRY_RUN,
        )
    except WorksheetNotFound as exc:
        if DRY_RUN and create_if_missing:
            print(
                f"DRY_RUN=True: se crearia la hoja diaria "
                f"'{daily_sheet_name}', pero no se modifica el Spreadsheet."
            )
            return []
        raise RuntimeError(
            f"No existe la hoja diaria '{daily_sheet_name}'. "
            "Se detiene este proceso antes de actualizar el resumen mensual "
            "para conservar los importes existentes. Restaura la pestana o "
            "corrige su nombre en la configuracion y vuelve a ejecutar."
        ) from exc

    daily_sheet_name = worksheet.title
    if not DRY_RUN:
        update_daily_sheet_date_headers(worksheet, target_day, target_day)

    values = worksheet.get_all_values()

    header_row_index, header_row = find_header_row(values)

    total_col = find_column(header_row, ["Total (cuentas)", "Total", "Total cuentas"])
    cuenta_col = find_column(header_row, ["Cuenta", "Nombre de cuenta", "Account"])
    id_col = find_column(header_row, ["ID de cuenta", "ID cuenta", "Customer ID", "ID"])
    estado_col = find_column(
        header_row,
        ["Estado de servicio", "Estado", "Serving status", "Status"]
    )
    date_columns = get_date_columns(header_row)

    if not total_col:
        total_col = 1

    if not cuenta_col or not id_col:
        raise ValueError("No encontré columnas obligatorias en hoja diaria: Cuenta / ID de cuenta.")

    if not date_columns:
        raise ValueError("No encontré columnas de fechas en la hoja diaria.")

    target_month_key = target_day.strftime("%Y-%m")
    target_month_dates = [
        day
        for day in date_columns
        if day.startswith(target_month_key)
    ]

    if not target_month_dates:
        raise ValueError(
            f"La hoja diaria '{daily_sheet_name}' no tiene columnas de fecha "
            f"para {target_month_key}. No escribo para evitar tocar una hoja de otro mes."
        )

    replace_start_date = replace_start_date or min(target_month_dates)
    replace_end_date = replace_end_date or max(target_month_dates)
    replace_dates = [
        day
        for day in target_month_dates
        if replace_start_date <= day <= replace_end_date
    ]

    if not replace_dates:
        raise ValueError(
            f"No hay columnas de fecha entre {replace_start_date} y "
            f"{replace_end_date} en la hoja diaria '{daily_sheet_name}'."
        )

    last_date_col = max(date_columns.values())
    last_col = max(total_col, cuenta_col, id_col, estado_col or 0, last_date_col)

    print("\n--- Hoja diaria ---")
    print(f"Hoja: {daily_sheet_name}")
    print(f"Fila de encabezados: {header_row_index}")
    print(f"Fechas detectadas: {len(date_columns)}")
    print(f"Primera fecha: {min(date_columns.keys())}")
    print(f"Última fecha: {max(date_columns.keys())}")
    print(f"Fechas a reemplazar: {min(replace_dates)} a {max(replace_dates)}")

    id_to_row = build_id_to_row(values, header_row_index, id_col)

    existing_accounts = []
    new_accounts = []

    for account in accounts_with_spend:
        account_id = normalize_customer_id(account["id"])

        if account_id in id_to_row:
            existing_accounts.append(account)
        else:
            new_accounts.append(account)

    total_general = round(sum(account["total_cost"] for account in accounts_with_spend), 2)

    print(f"Cuentas a actualizar: {len(existing_accounts)}")
    print(f"Cuentas nuevas a añadir: {len(new_accounts)}")
    print(f"Total gasto Google Ads detectado en consulta: {total_general:.2f} €")

    if DRY_RUN:
        print("DRY_RUN=True: no se escribe hoja diaria.")
        return []

    id_to_row = build_id_to_row(values, header_row_index, id_col)
    next_row = find_first_empty_row(values, header_row_index, cuenta_col, id_col)
    result_ids = {
        normalize_customer_id(account["id"])
        for account in accounts_with_spend
    }
    failed_ids = get_failed_account_ids()
    unavailable_ids = failed_ids | (set(id_to_row) - set(ACCOUNT_METADATA) - result_ids)

    ordered_accounts = []

    for account in accounts_with_spend:
        account_id = normalize_customer_id(account["id"])

        if account_id in id_to_row:
            ordered_accounts.append((id_to_row[account_id], account))

    for account in accounts_with_spend:
        account_id = normalize_customer_id(account["id"])

        if account_id not in id_to_row:
            ordered_accounts.append((next_row, account))
            next_row += 1

    for account_id, row_number in id_to_row.items():
        if account_id in result_ids or account_id in unavailable_ids:
            continue

        row = values[row_number - 1] if len(values) >= row_number else []
        name = row[cuenta_col - 1] if len(row) >= cuenta_col else ""
        metadata = ACCOUNT_METADATA.get(account_id, {})
        status = metadata.get("status", "")

        if not status and estado_col and len(row) >= estado_col:
            status = row[estado_col - 1]

        ordered_accounts.append((
            row_number,
            {
                "name": name,
                "id": account_id,
                "status": status,
                "hidden": metadata.get("hidden", False),
                "daily_costs": {},
                "total_cost": 0,
            }
        ))

    if not ordered_accounts:
        print("No hay datos para escribir en hoja diaria.")
        return []

    account_row_numbers = (
        [row for row, _ in ordered_accounts]
        + list(id_to_row.values())
    )
    min_row = min(account_row_numbers)
    max_row = max(account_row_numbers)

    height = max_row - min_row + 1
    matrix = [["" for _ in range(last_col)] for _ in range(height)]

    for row_number in range(min_row, max_row + 1):
        source_row = (
            values[row_number - 1]
            if len(values) >= row_number
            else []
        )
        matrix_index = row_number - min_row
        for column_index, value in enumerate(source_row[:last_col]):
            matrix[matrix_index][column_index] = value

    for row_number, account in ordered_accounts:
        matrix_index = row_number - min_row

        matrix[matrix_index][cuenta_col - 1] = account["name"]
        matrix[matrix_index][id_col - 1] = normalize_customer_id(account["id"])

        if estado_col:
            matrix[matrix_index][estado_col - 1] = account.get("status", "")

        for day in replace_dates:
            col = date_columns[day]
            matrix[matrix_index][col - 1] = ""

        for day, cost in account["daily_costs"].items():
            if day in replace_dates:
                col = date_columns[day]
                matrix[matrix_index][col - 1] = cost

        target_month_total = 0

        for day in target_month_dates:
            col = date_columns[day]
            target_month_total += parse_sheet_number(matrix[matrix_index][col - 1])

        matrix[matrix_index][total_col - 1] = round(target_month_total, 2)

    status_by_id = {
        account_id: metadata.get("status", "")
        for account_id, metadata in ACCOUNT_METADATA.items()
        if account_id in id_to_row
    }
    status_by_id.update({
        normalize_customer_id(account["id"]): account.get("status", "")
        for _, account in ordered_accounts
    })
    hidden_by_id = {
        account_id: metadata.get("hidden", False)
        for account_id, metadata in ACCOUNT_METADATA.items()
        if account_id in id_to_row
    }
    hidden_by_id.update({
        normalize_customer_id(account["id"]): account.get("hidden", False)
        for _, account in ordered_accounts
    })

    if estado_col:
        for account_id, row_number in id_to_row.items():
            row = values[row_number - 1] if len(values) >= row_number else []
            existing_status = (
                row[estado_col - 1]
                if len(row) >= estado_col
                else ""
            )
            status_by_id.setdefault(account_id, existing_status)

    matrix = sort_account_matrix(
        matrix,
        cuenta_col,
        id_col,
        [date_columns[day] for day in target_month_dates],
        status_by_id,
        hidden_by_id,
    )

    start_cell = gspread.utils.rowcol_to_a1(min_row, 1)
    end_cell = gspread.utils.rowcol_to_a1(max_row, last_col)

    worksheet.update(
        range_name=f"{start_cell}:{end_cell}",
        values=matrix,
        value_input_option="USER_ENTERED"
    )

    format_request_count = apply_daily_euro_formats(
        worksheet,
        values,
        header_row_index,
        max_row,
        total_col,
        date_columns,
    )

    print(
        "Hoja diaria actualizada correctamente. "
        f"Formato EUR aplicado en {format_request_count} rangos."
    )

    daily_month_accounts = []

    for row in matrix:
        account_id = normalize_customer_id(
            row[id_col - 1] if len(row) >= id_col else ""
        )
        target_month_total = parse_sheet_number(
            row[total_col - 1] if len(row) >= total_col else ""
        )

        if not account_id or target_month_total <= 0:
            continue

        daily_month_accounts.append({
            "name": row[cuenta_col - 1],
            "id": account_id,
            "status": status_by_id.get(account_id, ""),
            "hidden": hidden_by_id.get(account_id, False),
            "monthly_costs": {
                target_month_key: round(target_month_total, 2)
            },
            "total_cost": round(target_month_total, 2),
        })

    preserved_accounts = [
        account
        for account in daily_month_accounts
        if account["id"] in unavailable_ids
    ]

    final_sheet_total = round(
        sum(account["total_cost"] for account in daily_month_accounts),
        2
    )

    if preserved_accounts:
        preserved_total = round(
            sum(account["total_cost"] for account in preserved_accounts),
            2
        )
        print(
            f"Cuentas no consultables con gasto preservado: "
            f"{len(preserved_accounts)} ({preserved_total:.2f} EUR)"
        )

    print(
        f"Total final de la hoja diaria {target_month_key}: "
        f"{final_sheet_total:.2f} EUR"
    )

    return daily_month_accounts


def process_monthly_sheet_for_client(
    sheets_client,
    cliente,
    accounts_with_monthly_spend,
    month_keys,
    target_day=None,
    spreadsheet=None,
):
    target_day = target_day or date.today()
    month_keys = limit_reporting_month_keys(cliente, month_keys)
    if not month_keys:
        print("Historico anterior al inicio configurado: se conserva sin cambios.")
        return
    accounts_with_monthly_spend = filter_monthly_account_results(
        accounts_with_monthly_spend, month_keys,
    )
    monthly_sheet_name = get_monthly_sheet_name(cliente, target_day)

    if not monthly_sheet_name:
        print("No hay hoja histórica mensual configurada. Salto hoja mensual.")
        return

    if spreadsheet is None:
        spreadsheet = sheets_client.open_by_key(cliente["spreadsheet_id"])
    if DRY_RUN:
        try:
            worksheet = spreadsheet.worksheet(monthly_sheet_name)
        except WorksheetNotFound:
            print(
                f"DRY_RUN=True: se crearia la hoja mensual "
                f"'{monthly_sheet_name}', pero no se modifica el Spreadsheet."
            )
            return
    else:
        worksheet = get_or_create_monthly_worksheet(
            spreadsheet,
            cliente,
            target_day,
        )
    monthly_sheet_name = worksheet.title

    values = worksheet.get_all_values()

    header_row_index, header_row = find_header_row(values)

    cuenta_col = find_column(header_row, ["Cuenta", "Nombre de cuenta", "Account"])
    id_col = find_column(header_row, ["ID de cuenta", "ID cuenta", "Customer ID", "ID"])
    total_col = find_column(header_row, ["Total", "Total (cuentas)", "Total cuentas"])

    month_columns = find_month_columns(values, header_row_index, header_row, month_keys)
    missing_month_keys = [
        month_key
        for month_key in month_keys
        if month_key not in month_columns
    ]

    if not cuenta_col or not id_col:
        raise ValueError("No encontré columnas obligatorias en hoja mensual: Cuenta / ID de cuenta.")

    if not month_columns:
        print("\n--- Hoja mensual ---")
        print(f"Hoja: {monthly_sheet_name}")
        print("NO ESCRIBO: no encontre ninguna columna mensual dentro del periodo configurado.")
        print(f"Meses buscados: {', '.join(month_keys)}")
        return

    last_month_col = max(item["index"] for item in month_columns.values())
    last_col = max(cuenta_col, id_col, total_col or 0, last_month_col)

    print("\n--- Hoja histórica mensual ---")
    print(f"Hoja: {monthly_sheet_name}")
    print(f"Fila de encabezados: {header_row_index}")
    print(f"Columna Cuenta: {cuenta_col}")
    print(f"Columna ID de cuenta: {id_col}")
    print(f"Columna Total: {total_col}")
    print("Columnas mensuales a actualizar:")
    for month_key in month_keys:
        if month_key in month_columns:
            month_info = month_columns[month_key]
            print(
                f"  {month_key}: columna {month_info['index']} "
                f"({month_info['label']}, fila {month_info['source_row']})"
            )

    if missing_month_keys:
        print("Meses sin columna en la hoja; se omiten:")
        for month_key in missing_month_keys:
            print(f"  - {month_key}")

    id_to_row = build_id_to_row(values, header_row_index, id_col)

    existing_accounts = []
    new_accounts = []

    for account in accounts_with_monthly_spend:
        account_id = normalize_customer_id(account["id"])

        if account_id in id_to_row:
            existing_accounts.append(account)
        else:
            new_accounts.append(account)

    total_general = round(sum(account["total_cost"] for account in accounts_with_monthly_spend), 2)

    print(f"Cuentas a actualizar en historico mensual: {len(existing_accounts)}")
    print(f"Cuentas nuevas a añadir: {len(new_accounts)}")
    print(f"Total periodo historico detectado: {total_general:.2f} €")

    print("\nPrimeras cuentas para hoja mensual:")
    for account in accounts_with_monthly_spend[:20]:
        print(
            f"{account['name']} | {account['id']} | "
            f"{account.get('status', '')} | {account['total_cost']:.2f} €"
        )

    if len(accounts_with_monthly_spend) > 20:
        print(f"... y {len(accounts_with_monthly_spend) - 20} cuentas más")

    if DRY_RUN:
        print("DRY_RUN=True: no se escribe hoja mensual.")
        return

    id_to_row = build_id_to_row(values, header_row_index, id_col)
    next_row = find_first_empty_row(values, header_row_index, cuenta_col, id_col)
    result_ids = {
        normalize_customer_id(account["id"])
        for account in accounts_with_monthly_spend
    }
    failed_ids = get_failed_account_ids()
    unavailable_ids = failed_ids | (set(id_to_row) - set(ACCOUNT_METADATA))
    existing_account_rows = [
        row_number
        for row_number, row in enumerate(values[header_row_index:], header_row_index + 1)
        if len(row) >= id_col and normalize_customer_id(row[id_col - 1]) in id_to_row
    ]

    ordered_accounts = []

    for account in accounts_with_monthly_spend:
        account_id = normalize_customer_id(account["id"])

        if account_id in id_to_row:
            ordered_accounts.append((id_to_row[account_id], account))

    for account in accounts_with_monthly_spend:
        account_id = normalize_customer_id(account["id"])

        if account_id not in id_to_row:
            ordered_accounts.append((next_row, account))
            next_row += 1

    for account_id, row_number in id_to_row.items():
        if account_id in result_ids or account_id in unavailable_ids:
            continue

        row = values[row_number - 1] if len(values) >= row_number else []
        name = row[cuenta_col - 1] if len(row) >= cuenta_col else ""
        metadata = ACCOUNT_METADATA.get(account_id, {})

        ordered_accounts.append((
            row_number,
            {
                "name": name,
                "id": account_id,
                "status": metadata.get("status", ""),
                "hidden": metadata.get("hidden", False),
                "monthly_costs": {},
                "total_cost": 0,
            }
        ))

    if not ordered_accounts:
        print("No hay datos para escribir en hoja mensual.")
        return

    account_row_numbers = (
        [row for row, _ in ordered_accounts]
        + existing_account_rows
    )
    min_row = min(account_row_numbers)
    max_row = max(account_row_numbers)

    height = max_row - min_row + 1
    matrix = [["" for _ in range(last_col)] for _ in range(height)]

    for row_number in range(min_row, max_row + 1):
        source_row = (
            values[row_number - 1]
            if len(values) >= row_number
            else []
        )
        matrix_index = row_number - min_row
        for column_index, value in enumerate(source_row[:last_col]):
            matrix[matrix_index][column_index] = value

    modified_rows = set()
    # Historical duplicates keep their rows; a successful refresh replaces
    # the target months on every copy before writing that account once.
    for matrix_index, row in enumerate(matrix):
        account_id = normalize_customer_id(row[id_col - 1])
        if account_id and account_id not in unavailable_ids:
            for info in month_columns.values():
                row[info["index"] - 1] = ""
            modified_rows.add(matrix_index)

    for row_number, account in ordered_accounts:
        matrix_index = row_number - min_row
        modified_rows.add(matrix_index)

        matrix[matrix_index][cuenta_col - 1] = account["name"]
        matrix[matrix_index][id_col - 1] = normalize_customer_id(account["id"])

        # Solo escribimos los meses configurados y existentes en la hoja.
        # Los meses anteriores, futuros o no detectados se quedan intactos.
        for month_key, month_info in month_columns.items():
            if (normalize_customer_id(account["id"]) not in unavailable_ids
                    or month_key in account["monthly_costs"]):
                matrix[matrix_index][month_info["index"] - 1] = ""

        for month_key, cost in account["monthly_costs"].items():
            if month_key in month_columns:
                month_col = month_columns[month_key]["index"]
                matrix[matrix_index][month_col - 1] = cost

    if total_col:
        for matrix_index in modified_rows:
            old_month_values = []

            for col_index, value in enumerate(matrix[matrix_index], start=1):
                if col_index in [cuenta_col, id_col, total_col]:
                    continue

                old_month_values.append(parse_sheet_number(value))

            matrix[matrix_index][total_col - 1] = round(sum(old_month_values), 2)

    status_by_id = {
        account_id: metadata.get("status", "")
        for account_id, metadata in ACCOUNT_METADATA.items()
        if account_id in id_to_row
    }
    status_by_id.update({
        normalize_customer_id(account["id"]): account.get("status", "")
        for _, account in ordered_accounts
    })
    hidden_by_id = {
        account_id: metadata.get("hidden", False)
        for account_id, metadata in ACCOUNT_METADATA.items()
        if account_id in id_to_row
    }
    hidden_by_id.update({
        normalize_customer_id(account["id"]): account.get("hidden", False)
        for _, account in ordered_accounts
    })
    all_year_month_keys = [
        f"{target_day.year}-{month:02d}"
        for month in range(1, 13)
    ]
    sorting_month_columns = find_month_columns(
        values,
        header_row_index,
        header_row,
        all_year_month_keys,
    )
    all_month_columns = sorted({
        item["index"]
        for item in sorting_month_columns.values()
    })
    sorting_columns = [
        column
        for column in all_month_columns
        if column <= last_col
    ]
    matrix = sort_account_matrix(
        matrix,
        cuenta_col,
        id_col,
        sorting_columns,
        status_by_id,
        hidden_by_id,
    )

    start_cell = gspread.utils.rowcol_to_a1(min_row, 1)
    end_cell = gspread.utils.rowcol_to_a1(max_row, last_col)

    worksheet.update(
        range_name=f"{start_cell}:{end_cell}",
        values=matrix,
        value_input_option="USER_ENTERED"
    )

    format_request_count = apply_monthly_euro_formats(
        worksheet,
        values,
        header_row_index,
        max_row,
        all_month_columns,
        total_col,
    )

    print(
        "Hoja histórica mensual actualizada correctamente. "
        f"Formato EUR aplicado en {format_request_count} rangos."
    )


def run_historical_backfill(
    sheets_client,
    clientes,
    backfill_year,
    today,
    parallel_workers,
):
    try:
        start_day, end_day, month_keys = get_historical_backfill_period(
            backfill_year,
            today,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc

    print("=" * 70)
    print(f"BACKFILL HISTORICO {backfill_year}")
    print("=" * 70)
    print(f"Periodo: {start_day.isoformat()} a {end_day.isoformat()}")
    print(f"Meses: {', '.join(month_keys)}")
    print("No se actualizaran hojas diarias.\n")

    for cliente in clientes:
        print("=" * 70)
        print(f"Procesando backfill: {cliente['nombre']}")
        print("=" * 70)
        client_month_keys = limit_reporting_month_keys(cliente, month_keys)
        if not client_month_keys:
            print("Periodo anterior al inicio configurado: no se consulta ni escribe.")
            continue
        client_start_day = max(start_day, get_month_start_date(client_month_keys[0]))
        accounts_with_period_spend = fetch_client_daily_spend(
            cliente,
            client_start_day.isoformat(),
            end_day.isoformat(),
            f"backfill historico {backfill_year}",
            parallel_workers,
        )
        accounts_with_monthly_spend = build_accounts_for_monthly_sheet(
            accounts_with_period_spend,
            client_month_keys,
        )
        print(
            f"Cuentas con gasto en el backfill: "
            f"{len(accounts_with_monthly_spend)}"
        )
        process_monthly_sheet_for_client(
            sheets_client,
            cliente,
            accounts_with_monthly_spend,
            client_month_keys,
            end_day,
        )
        print("")


def main():
    args = parse_args()
    run_mode = normalize_run_mode(args.modo)
    parallel_workers = max(1, int(args.parallel_workers))
    quick_lookback_days = max(1, int(args.quick_lookback_days))
    FAILED_ACCOUNT_QUERIES.clear()
    ACCOUNT_METADATA.clear()

    config = load_consumption_runtime_config()

    clientes_activos = select_clients(config, args.cliente)

    if not clientes_activos:
        raise SystemExit("No hay clientes activos en config_clientes.json")

    today = date.today()
    sheets_client = create_sheets_client()

    if args.backfill_year:
        run_historical_backfill(
            sheets_client,
            clientes_activos,
            args.backfill_year,
            today,
            parallel_workers,
        )
        print_failed_account_queries()

        if any(item.get("fatal", True) for item in FAILED_ACCOUNT_QUERIES):
            raise SystemExit(1)

        return

    daily_start_date, daily_end_date = get_month_dates()
    previous_month_day = get_previous_month_day(today)
    previous_daily_start_date, previous_daily_end_date = get_full_month_dates_for_day(
        previous_month_day
    )
    current_month_key = today.strftime("%Y-%m")
    previous_month_key = previous_month_day.strftime("%Y-%m")
    monthly_month_keys = list(dict.fromkeys([previous_month_key, current_month_key]))
    monthly_start_date = previous_daily_start_date
    monthly_end_date = daily_end_date

    if run_mode == "rapido":
        replace_start_date, replace_end_date = get_quick_dates(quick_lookback_days)
    else:
        replace_start_date, replace_end_date = daily_start_date, daily_end_date

    print(f"Periodo diario Google Ads: {daily_start_date} a {daily_end_date}")
    print(
        f"Periodo diario mes anterior: "
        f"{previous_daily_start_date} a {previous_daily_end_date}"
    )
    print(
        f"Meses historicos a actualizar: "
        f"{', '.join(monthly_month_keys)}"
    )
    print(f"Modo ejecucion: {run_mode}")
    print(f"Consultas paralelas Google Ads: {parallel_workers}")

    if run_mode == "rapido":
        print(
            f"Modo rapido: se recalculan fechas recientes "
            f"{replace_start_date} a {replace_end_date}"
        )
    print(f"Modo simulación DRY_RUN={DRY_RUN}\n")

    if args.cliente:
        print("Filtro manual de clientes:")
        for cliente_filter in args.cliente:
            print(f"  - {cliente_filter}")
        print("")

    for cliente in clientes_activos:
        print("=" * 70)
        print(f"Procesando cliente: {cliente['nombre']}")
        print("=" * 70)

        reporting_start = get_reporting_start_date(cliente)
        if reporting_start and today < reporting_start:
            print("Inicio de reporting futuro: no se consulta ni escribe.")
            continue

        if run_mode == "rapido":
            current_daily_start_date = replace_start_date
            current_daily_end_date = replace_end_date
            query_start_date = previous_daily_start_date
            query_end_date = replace_end_date
        else:
            current_daily_start_date = daily_start_date
            current_daily_end_date = daily_end_date
            query_start_date = previous_daily_start_date
            query_end_date = daily_end_date

        if reporting_start:
            query_start_date = max(query_start_date, reporting_start.isoformat())

        print(
            f"Periodo historico mensual Google Ads: "
            f"{monthly_start_date} a {monthly_end_date}"
        )
        print(
            f"Lectura unica Google Ads para ambas hojas: "
            f"{query_start_date} a {query_end_date}"
        )

        accounts_with_period_spend = fetch_client_daily_spend(
            cliente,
            query_start_date,
            query_end_date,
            "lectura unica diaria/mensual",
            parallel_workers,
        )
        spreadsheet = sheets_client.open_by_key(cliente["spreadsheet_id"])

        current_daily_accounts = build_accounts_for_daily_sheet(
            accounts_with_period_spend,
            current_daily_start_date,
            current_daily_end_date
        )

        print(
            f"\nCuentas con gasto encontradas en diario actual "
            f"({current_month_key}): {len(current_daily_accounts)}"
        )

        current_daily_month_accounts = process_daily_sheet_for_client(
            sheets_client,
            cliente,
            current_daily_accounts,
            replace_start_date,
            replace_end_date,
            today,
            True,
            spreadsheet,
        )

        previous_daily_accounts = build_accounts_for_daily_sheet(
            accounts_with_period_spend,
            previous_daily_start_date,
            previous_daily_end_date
        )

        print(
            f"\nCuentas con gasto encontradas en diario anterior "
            f"({previous_month_key}): {len(previous_daily_accounts)}"
        )

        previous_daily_month_accounts = process_daily_sheet_for_client(
            sheets_client,
            cliente,
            previous_daily_accounts,
            previous_daily_start_date,
            previous_daily_end_date,
            previous_month_day,
            False,
            spreadsheet,
        )

        accounts_with_monthly_spend = merge_monthly_account_results(
            previous_daily_month_accounts + current_daily_month_accounts
        )

        print(
            f"\nCuentas con gasto historico encontradas: "
            f"{len(accounts_with_monthly_spend)}"
        )

        if previous_month_day.year == today.year:
            process_monthly_sheet_for_client(
                sheets_client,
                cliente,
                accounts_with_monthly_spend,
                monthly_month_keys,
                today,
                spreadsheet,
            )
        else:
            previous_year_accounts = filter_monthly_account_results(
                accounts_with_monthly_spend,
                [previous_month_key]
            )
            current_year_accounts = filter_monthly_account_results(
                accounts_with_monthly_spend,
                [current_month_key]
            )

            process_monthly_sheet_for_client(
                sheets_client,
                cliente,
                previous_year_accounts,
                [previous_month_key],
                previous_month_day,
                spreadsheet,
            )
            process_monthly_sheet_for_client(
                sheets_client,
                cliente,
                current_year_accounts,
                [current_month_key],
                today,
                spreadsheet,
            )

        if today.day == 1 and not DRY_RUN:
            maintain_daily_sheet_visibility(
                spreadsheet,
                today,
            )
            maintain_hidden_daily_sheet_order(
                spreadsheet,
                today,
            )

        print("")

    print_failed_account_queries()

    if any(item.get("fatal", True) for item in FAILED_ACCOUNT_QUERIES):
        raise SystemExit(1)
