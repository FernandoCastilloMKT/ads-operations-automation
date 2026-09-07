import argparse
import json
import logging
import os
import re
import sys
import time
import unicodedata
from calendar import monthrange
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, timedelta
from pathlib import Path

import gspread
from dotenv import load_dotenv
from gspread.exceptions import WorksheetNotFound
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.oauth2.service_account import Credentials

from runtime_config import load_consumption_runtime_config

BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR / ".env"
sheets_credentials_path = BASE_DIR / "credentials" / "google-sheets-service-account.json"
vendor_path = BASE_DIR / ".vendor"

if vendor_path.exists():
    sys.path.insert(0, str(vendor_path))

try:
    import truststore

    if hasattr(truststore, "inject_into_ssl"):
        truststore.inject_into_ssl()
except ImportError:
    pass

# True = simula sin escribir.
# False = escribe en Google Sheets.
DRY_RUN = False
MAX_GOOGLE_ADS_RETRIES = 3
RETRY_DELAY_SECONDS = 5
DEFAULT_PARALLEL_WORKERS = 10
DEFAULT_QUICK_LOOKBACK_DAYS = 7
EURO_NUMBER_FORMAT_PATTERN = "#,##0.00\\ [$€-1]"
FAILED_ACCOUNT_QUERIES = []
ACCOUNT_METADATA = {}

ACCOUNT_STATUS_LABELS = {
    "ENABLED": "",
    "SUSPENDED": "Suspendida",
    "CANCELED": "Cancelada",
    "CANCELLED": "Cancelada",
    "CLOSED": "Cerrada",
    "PAUSED": "Pausada",
    "REMOVED": "Eliminada",
    "DISABLED": "Desactivada",
    "DRAFT": "Borrador",
    "HIDDEN": "Oculta",
    "FINALIZED": "Finalizada",
    "UNKNOWN": "Estado desconocido",
    "UNSPECIFIED": "Sin especificar",
}

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

load_dotenv(env_path, override=True)

# La libreria de Google Ads escribe en stderr cada excepcion de cuentas
# desactivadas aunque luego la capturemos. Dejamos nuestro resumen propio para
# que los logs de Windows/GitHub no parezcan fallos reales.
logging.getLogger("google.ads.googleads").setLevel(logging.CRITICAL)

developer_token = os.getenv("GOOGLE_ADS_DEVELOPER_TOKEN")
client_id = os.getenv("GOOGLE_ADS_CLIENT_ID")
client_secret = os.getenv("GOOGLE_ADS_CLIENT_SECRET")
refresh_token = os.getenv("GOOGLE_ADS_REFRESH_TOKEN")


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


def normalize_run_mode(value):
    normalized = normalize_match_text(value)

    if normalized in {"completo", "complete", "full"}:
        return "completo"

    if normalized in {"rapido", "quick", "fast"}:
        return "rapido"

    raise SystemExit("Modo no valido. Usa --modo completo o --modo rapido.")


def normalize_match_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


def client_matches_filters(cliente, filters):
    if not filters:
        return cliente.get("activo") is True

    client_values = [
        cliente.get("nombre", ""),
        cliente.get("mcc_id", ""),
    ]
    client_values.extend(cliente.get("sub_mcc_ids", []))

    for source in cliente.get("manager_sources", []):
        client_values.extend([
            source.get("nombre", ""),
            source.get("mcc_id", ""),
        ])
        client_values.extend(source.get("sub_mcc_ids", []))

    normalized_values = [
        normalize_match_text(value)
        for value in client_values
    ]

    for raw_filter in filters:
        normalized_filter = normalize_match_text(raw_filter)

        if not normalized_filter:
            continue

        if any(
            normalized_filter in value or value in normalized_filter
            for value in normalized_values
            if value
        ):
            return True

    return False


def select_clients(config, filters):
    """
    Sin filtro, procesa solo clientes activos.
    Con --cliente, permite aislar un cliente concreto aunque este marcado
    como inactivo; asi podemos probar MCC por MCC sin tocar los demas.
    """
    clientes = config.get("clientes", [])
    selected = [
        cliente for cliente in clientes
        if client_matches_filters(cliente, filters)
    ]

    if not selected:
        available = ", ".join(
            cliente.get("nombre", "(sin nombre)")
            for cliente in clientes
        )
        raise SystemExit(
            "No hay clientes que coincidan con el filtro indicado. "
            f"Clientes disponibles: {available}"
        )

    return selected


def get_month_dates():
    today = date.today()
    start_date = today.replace(day=1)
    return start_date.isoformat(), today.isoformat()


def get_full_month_dates_for_day(day):
    start_date = day.replace(day=1)
    end_date = day.replace(day=monthrange(day.year, day.month)[1])
    return start_date.isoformat(), end_date.isoformat()


def get_quick_dates(lookback_days):
    today = date.today()
    month_start = today.replace(day=1)
    days = max(1, int(lookback_days))
    quick_start = max(month_start, today - timedelta(days=days - 1))
    return quick_start.isoformat(), today.isoformat()


def parse_month_key(month_key):
    year, month = month_key.split("-")
    return int(year), int(month)


def get_month_start_date(month_key):
    year, month = parse_month_key(month_key)
    return date(year, month, 1)


def get_month_key_from_day(day):
    return str(day)[:7]


def parse_sheet_number(value):
    if isinstance(value, (int, float)):
        return float(value)

    clean_value = (
        str(value)
        .replace("€", "")
        .replace("â‚¬", "")
        .replace("\xa0", "")
        .replace(" ", "")
        .strip()
    )

    if not clean_value:
        return 0

    if "," in clean_value and "." in clean_value:
        clean_value = clean_value.replace(".", "").replace(",", ".")
    elif "," in clean_value:
        clean_value = clean_value.replace(",", ".")

    try:
        return float(clean_value)
    except ValueError:
        return 0


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


def get_month_keys_between(start_month_key, end_day):
    """
    Devuelve meses en formato YYYY-MM desde el mes inicial hasta el mes actual.
    Se usa para rellenar la hoja historica sin tocar meses futuros.
    """
    year, month = parse_month_key(start_month_key)
    current = date(year, month, 1)
    end_month = end_day.replace(day=1)
    month_keys = []

    while current <= end_month:
        month_keys.append(f"{current.year}-{current.month:02d}")

        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)

    return month_keys


def get_monthly_period(cliente):
    today = date.today()
    start_month_key = get_monthly_start_month(cliente, today)
    start_date = get_month_start_date(start_month_key)
    month_keys = get_month_keys_between(start_month_key, today)
    return start_date.isoformat(), today.isoformat(), month_keys


def get_month_abbr(month):
    month_abbrs = {
        1: "Ene",
        2: "Feb",
        3: "Mar",
        4: "Abr",
        5: "May",
        6: "Jun",
        7: "Jul",
        8: "Ago",
        9: "Sep",
        10: "Oct",
        11: "Nov",
        12: "Dic",
    }
    return month_abbrs[month]


def get_daily_sheet_name(cliente, target_day=None):
    template = cliente.get("daily_worksheet_name_template")

    if template:
        today = target_day or date.today()
        return template.format(
            month_abbr=get_month_abbr(today.month),
            year=today.year,
            year_2digit=f"{today.year % 100:02d}",
            month=today.month,
            month_2digit=f"{today.month:02d}",
        )

    return cliente.get("daily_worksheet_name") or cliente.get("worksheet_name")


def get_monthly_sheet_name(cliente, target_day=None):
    target_day = target_day or date.today()
    template = cliente.get("monthly_worksheet_name_template")

    if template:
        return template.format(
            year=target_day.year,
            year_2digit=f"{target_day.year % 100:02d}",
        )

    return cliente.get("monthly_worksheet_name")


def get_monthly_start_month(cliente, target_day=None):
    target_day = target_day or date.today()
    first_start_month = cliente.get("monthly_start_month", f"{target_day.year}-01")
    first_year, _ = parse_month_key(first_start_month)

    if target_day.year <= first_year:
        return first_start_month

    return f"{target_day.year}-01"


def add_months(day, month_delta):
    month_index = day.month - 1 + month_delta
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = monthrange(year, month)[1]
    return day.replace(year=year, month=month, day=min(day.day, last_day))


def get_previous_month_day(day):
    return add_months(day.replace(day=1), -1)


def parse_daily_google_ads_sheet_month(sheet_name):
    normalized = normalize_sheet_text(sheet_name)
    match = re.fullmatch(
        (
            r"(?:consumos)?"
            r"(ene|enero|feb|febrero|mar|marzo|abr|abril|may|mayo|"
            r"jun|junio|jul|julio|ago|agosto|sep|septiembre|"
            r"oct|octubre|nov|noviembre|dic|diciembre)"
            r"(\d{2}|\d{4})gads"
        ),
        normalized,
    )

    if not match:
        return None

    month_aliases = {
        "ene": 1,
        "enero": 1,
        "feb": 2,
        "febrero": 2,
        "mar": 3,
        "marzo": 3,
        "abr": 4,
        "abril": 4,
        "may": 5,
        "mayo": 5,
        "jun": 6,
        "junio": 6,
        "jul": 7,
        "julio": 7,
        "ago": 8,
        "agosto": 8,
        "sep": 9,
        "septiembre": 9,
        "oct": 10,
        "octubre": 10,
        "nov": 11,
        "noviembre": 11,
        "dic": 12,
        "diciembre": 12,
    }
    raw_year = int(match.group(2))
    year = raw_year if raw_year >= 1000 else 2000 + raw_year
    return date(year, month_aliases[match.group(1)], 1)


def plan_hidden_daily_sheet_order(worksheets):
    ordered = sorted(
        worksheets,
        key=lambda worksheet: int(worksheet._properties.get("index", 0)),
    )
    hidden_positions = []
    hidden_daily_sheets = []

    for position, worksheet in enumerate(ordered):
        sheet_month = parse_daily_google_ads_sheet_month(worksheet.title)
        if not sheet_month or not worksheet._properties.get("hidden", False):
            continue
        hidden_positions.append(position)
        hidden_daily_sheets.append(worksheet)

    hidden_daily_sheets.sort(
        key=lambda worksheet: (
            parse_daily_google_ads_sheet_month(worksheet.title),
            normalize_sheet_text(worksheet.title),
        )
    )
    desired = list(ordered)
    for position, worksheet in zip(hidden_positions, hidden_daily_sheets):
        desired[position] = worksheet
    return desired


def build_sheet_reorder_requests(current, desired):
    working = list(current)
    requests = []

    for target_index, worksheet in enumerate(desired):
        current_index = working.index(worksheet)
        if current_index == target_index:
            continue
        requests.append({
            "updateSheetProperties": {
                "properties": {
                    "sheetId": worksheet.id,
                    "index": target_index,
                },
                "fields": "index",
            }
        })
        working.pop(current_index)
        working.insert(target_index, worksheet)

    return requests


def maintain_hidden_daily_sheet_order(
    spreadsheet,
    target_day=None,
    force=False,
):
    target_day = target_day or date.today()
    if target_day.day != 1 and not force:
        return []

    current = sorted(
        spreadsheet.worksheets(),
        key=lambda worksheet: int(worksheet._properties.get("index", 0)),
    )
    desired = plan_hidden_daily_sheet_order(current)
    requests = build_sheet_reorder_requests(current, desired)

    if requests:
        spreadsheet.batch_update({"requests": requests})
        print(
            "Pestanas mensuales ocultas ordenadas cronologicamente: "
            f"{len(requests)} movimientos."
        )
    else:
        print("Orden mensual oculto: ya estaba correcto.")

    return [
        worksheet.title
        for worksheet in desired
        if worksheet._properties.get("hidden", False)
        and parse_daily_google_ads_sheet_month(worksheet.title)
    ]


def plan_daily_sheet_visibility(worksheets, target_day, visible_months=3):
    keep_from = add_months(target_day.replace(day=1), -(visible_months - 1))
    to_hide = []

    for worksheet in worksheets:
        sheet_month = parse_daily_google_ads_sheet_month(worksheet.title)

        if not sheet_month or sheet_month >= keep_from:
            continue

        if not worksheet._properties.get("hidden", False):
            to_hide.append(worksheet)

    return to_hide


def maintain_daily_sheet_visibility(
    spreadsheet,
    target_day=None,
    force=False,
    visible_months=3,
):
    target_day = target_day or date.today()

    if target_day.day != 1 and not force:
        return []

    worksheets = spreadsheet.worksheets()
    to_hide = plan_daily_sheet_visibility(
        worksheets,
        target_day,
        visible_months,
    )

    if not to_hide:
        print("Visibilidad mensual: no hay hojas diarias antiguas que ocultar.")
        return []

    spreadsheet.batch_update({
        "requests": [{
            "updateSheetProperties": {
                "properties": {
                    "sheetId": worksheet.id,
                    "hidden": True,
                },
                "fields": "hidden",
            }
        } for worksheet in to_hide]
    })

    print("Hojas diarias antiguas ocultadas:")
    for worksheet in to_hide:
        print(f"  - {worksheet.title}")

    return [worksheet.title for worksheet in to_hide]


def get_month_delta(start_day, end_day):
    return (end_day.year - start_day.year) * 12 + end_day.month - start_day.month


def get_current_month_labels():
    today = date.today()
    year = today.year
    month = today.month

    return [
        f"{year}|{month:02d}",
        f"{year}/{month:02d}",
        f"{year}-{month:02d}",
        f"{year}-{month:02d}-01",
        f"{year}{month:02d}",
    ]


def get_month_header_labels(month_key):
    year, month = parse_month_key(month_key)

    return [
        f"{year}|{month:02d}",
        f"{year}/{month:02d}",
        f"{year}-{month:02d}",
        f"{year}-{month:02d}-01",
        f"{year}{month:02d}",
    ]


def get_month_name_labels(month_key):
    _, month = parse_month_key(month_key)
    month_names = {
        1: ["enero", "ene", "jan", "january"],
        2: ["febrero", "feb", "february"],
        3: ["marzo", "mar", "march"],
        4: ["abril", "abr", "apr", "april"],
        5: ["mayo", "may"],
        6: ["junio", "jun", "june"],
        7: ["julio", "jul", "july"],
        8: ["agosto", "ago", "aug", "august"],
        9: ["septiembre", "setiembre", "sep", "september"],
        10: ["octubre", "oct", "october"],
        11: ["noviembre", "nov", "november"],
        12: ["diciembre", "dic", "dec", "december"],
    }
    return month_names[month]


def normalize_sheet_text(value):
    return normalize_match_text(value)


def get_header_aliases(labels):
    return {
        normalize_match_text(label)
        for label in labels
    }


def get_developer_token_for_client(cliente):
    developer_token_env = cliente.get(
        "developer_token_env",
        "GOOGLE_ADS_DEVELOPER_TOKEN"
    )
    token = os.getenv(developer_token_env)

    if not token:
        raise SystemExit(
            f"Falta la variable {developer_token_env} para {cliente['nombre']}"
        )

    return token


def get_manager_sources(cliente):
    configured_sources = cliente.get("manager_sources")

    if not configured_sources:
        configured_sources = [{
            "nombre": cliente.get("nombre", ""),
            "mcc_id": cliente.get("mcc_id", ""),
            "sub_mcc_ids": cliente.get("sub_mcc_ids", []),
        }]

    sources = []
    seen_mcc_ids = set()

    for source in configured_sources:
        mcc_id = normalize_customer_id(source.get("mcc_id", ""))

        if not mcc_id:
            raise ValueError(
                f"Fuente MCC sin mcc_id valido para {cliente.get('nombre', '')}"
            )

        if mcc_id in seen_mcc_ids:
            continue

        sources.append({
            "nombre": source.get("nombre") or mcc_id,
            "mcc_id": mcc_id,
            "sub_mcc_ids": [
                normalize_customer_id(item)
                for item in source.get("sub_mcc_ids", [])
                if normalize_customer_id(item)
            ],
        })
        seen_mcc_ids.add(mcc_id)

    return sources


def create_google_ads_client(cliente, login_customer_id=None):
    google_ads_config = {
        "developer_token": get_developer_token_for_client(cliente),
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "login_customer_id": (
            login_customer_id
            or cliente["mcc_id"]
        ),
        "use_proto_plus": True,
    }
    return GoogleAdsClient.load_from_dict(google_ads_config)


def merge_unique_daily_account_results(groups):
    merged = []
    seen_ids = set()
    duplicate_ids = set()

    for accounts in groups:
        for account in accounts:
            account_id = normalize_customer_id(account.get("id", ""))

            if not account_id or account_id in seen_ids:
                if account_id:
                    duplicate_ids.add(account_id)
                continue

            merged.append(account)
            seen_ids.add(account_id)

    return merged, duplicate_ids


def create_sheets_client():
    credentials = Credentials.from_service_account_file(
        sheets_credentials_path,
        scopes=SCOPES,
    )
    return gspread.authorize(credentials)


def normalize_customer_id(value):
    return "".join(ch for ch in str(value) if ch.isdigit())


def is_retryable_error(error):
    error_text = str(error).lower()
    return (
        "503" in error_text
        or "500" in error_text
        or "429" in error_text
        or "unavailable" in error_text
        or "backend unavailable" in error_text
        or "internal error" in error_text
        or "resource has been exhausted" in error_text
        or "too many requests" in error_text
    )


def is_skippable_account_error(error):
    """
    Algunas cuentas canceladas/deactivadas aparecen en customer_client pero
    Google Ads ya no permite consultarlas. Las omitimos sin borrar sus filas
    historicas y sin hacer fallar toda la automatizacion.
    """
    error_text = str(error).lower()
    return (
        "not yet enabled or has been deactivated" in error_text
        or "customer_not_enabled" in error_text
    )


def get_retry_delay_seconds(error, attempt):
    error_text = str(error).lower()
    retry_match = re.search(r"retry in (\d+) seconds", error_text)

    if retry_match:
        return int(retry_match.group(1))

    return RETRY_DELAY_SECONDS * attempt


def get_account_daily_spend_with_retries(
    google_ads_client,
    account,
    start_date,
    end_date,
    context
):
    account_id = account["id"]

    for attempt in range(1, MAX_GOOGLE_ADS_RETRIES + 1):
        try:
            return get_account_daily_spend(
                google_ads_client,
                account_id,
                start_date,
                end_date
            )
        except GoogleAdsException as ex:
            if is_retryable_error(ex) and attempt < MAX_GOOGLE_ADS_RETRIES:
                retry_delay = get_retry_delay_seconds(ex, attempt)
                print(
                    f"  Error temporal consultando {account_id} "
                    f"({context}). Reintento {attempt + 1}/{MAX_GOOGLE_ADS_RETRIES} "
                    f"en {retry_delay} segundos..."
                )
                time.sleep(retry_delay)
                continue

            if is_skippable_account_error(ex):
                FAILED_ACCOUNT_QUERIES.append({
                    "context": context,
                    "id": account_id,
                    "name": account.get("name", ""),
                    "error": "Cuenta no consultable: no habilitada o desactivada",
                    "fatal": False,
                })
                print(
                    f"  Omito cuenta no consultable {account_id} "
                    f"({account.get('name', '')})."
                )
                return None, {}, 0

            FAILED_ACCOUNT_QUERIES.append({
                "context": context,
                "id": account_id,
                "name": account.get("name", ""),
                "error": f"GoogleAdsException Request ID: {ex.request_id}",
                "fatal": True,
            })
            print(f"  No se pudo consultar la cuenta {account_id}. Request ID: {ex.request_id}")
            return None, {}, 0
        except Exception as ex:
            if is_retryable_error(ex) and attempt < MAX_GOOGLE_ADS_RETRIES:
                retry_delay = get_retry_delay_seconds(ex, attempt)
                print(
                    f"  Error temporal consultando {account_id} "
                    f"({context}). Reintento {attempt + 1}/{MAX_GOOGLE_ADS_RETRIES} "
                    f"en {retry_delay} segundos..."
                )
                time.sleep(retry_delay)
                continue

            FAILED_ACCOUNT_QUERIES.append({
                "context": context,
                "id": account_id,
                "name": account.get("name", ""),
                "error": str(ex),
                "fatal": True,
            })
            print(f"  Error consultando cuenta {account_id}: {ex}")
            return None, {}, 0


def get_child_accounts(google_ads_client, manager_customer_id):
    """
    Devuelve todas las cuentas finales visibles dentro de un MCC/sub-MCC.
    Importante: NO filtramos solo ENABLED, porque hay cuentas suspendidas
    que sí tuvieron gasto en el mes y deben aparecer en el histórico.
    """
    google_ads_service = google_ads_client.get_service("GoogleAdsService")

    # No filtramos por status ni por hidden. Primero traemos todas las
    # cuentas finales que devuelve el sub-MCC y despues solo descartamos
    # las que no tengan coste en el periodo consultado.
    query = """
        SELECT
          customer_client.id,
          customer_client.descriptive_name,
          customer_client.manager,
          customer_client.status,
          customer_client.hidden,
          customer_client.level
        FROM customer_client
        WHERE
          customer_client.manager = FALSE
        ORDER BY
          customer_client.level,
          customer_client.descriptive_name
    """

    for attempt in range(1, MAX_GOOGLE_ADS_RETRIES + 1):
        try:
            response = google_ads_service.search_stream(
                customer_id=manager_customer_id,
                query=query
            )

            accounts = []

            for batch in response:
                for row in batch.results:
                    accounts.append({
                        "id": str(row.customer_client.id),
                        "name": row.customer_client.descriptive_name,
                        "status": row.customer_client.status.name,
                        "hidden": bool(row.customer_client.hidden),
                        "level": int(row.customer_client.level),
                    })

            return accounts
        except (GoogleAdsException, Exception) as ex:
            if is_retryable_error(ex) and attempt < MAX_GOOGLE_ADS_RETRIES:
                retry_delay = get_retry_delay_seconds(ex, attempt)
                print(
                    f"  Error temporal listando cuentas de {manager_customer_id}. "
                    f"Reintento {attempt + 1}/{MAX_GOOGLE_ADS_RETRIES} "
                    f"en {retry_delay} segundos..."
                )
                time.sleep(retry_delay)
                continue

            raise


def get_account_daily_spend(google_ads_client, customer_id, start_date, end_date):
    google_ads_service = google_ads_client.get_service("GoogleAdsService")

    query = f"""
        SELECT
          customer.id,
          customer.descriptive_name,
          segments.date,
          metrics.cost_micros
        FROM customer
        WHERE
          segments.date BETWEEN '{start_date}' AND '{end_date}'
        ORDER BY
          segments.date
    """

    response = google_ads_service.search_stream(
        customer_id=customer_id,
        query=query
    )

    account_name = None
    daily_costs = {}

    for batch in response:
        for row in batch.results:
            account_name = row.customer.descriptive_name
            day = str(row.segments.date)
            cost = row.metrics.cost_micros / 1_000_000

            if cost > 0:
                daily_costs[day] = round(daily_costs.get(day, 0) + cost, 2)

    total_cost = round(sum(daily_costs.values()), 2)

    return account_name, daily_costs, total_cost


def build_monthly_costs(daily_costs, month_keys):
    monthly_costs = {month_key: 0 for month_key in month_keys}

    for day, cost in daily_costs.items():
        month_key = get_month_key_from_day(day)

        if month_key in monthly_costs:
            monthly_costs[month_key] = round(monthly_costs[month_key] + cost, 2)

    return {
        month_key: cost
        for month_key, cost in monthly_costs.items()
        if cost > 0
    }


def build_spend_result_for_account(
    google_ads_client,
    account,
    start_date,
    end_date,
    context
):
    account_id = account["id"]

    account_name, daily_costs, total_cost = get_account_daily_spend_with_retries(
        google_ads_client,
        account,
        start_date,
        end_date,
        context
    )

    if total_cost <= 0:
        return None

    return {
        "name": account_name or account["name"],
        "id": account_id,
        "status": account["status"],
        "hidden": account.get("hidden", False),
        "level": account.get("level"),
        "daily_costs": daily_costs,
        "total_cost": total_cost,
    }


def get_accounts_with_daily_spend_for_manager(
    google_ads_client,
    manager_customer_id,
    start_date,
    end_date,
    context="hoja diaria",
    parallel_workers=1
):
    child_accounts = get_child_accounts(google_ads_client, manager_customer_id)

    for account in child_accounts:
        account_id = normalize_customer_id(account.get("id", ""))

        if account_id:
            ACCOUNT_METADATA[account_id] = {
                "name": account.get("name", ""),
                "status": account.get("status", ""),
                "hidden": account.get("hidden", False),
            }

    print(f"  Cuentas finales encontradas en {manager_customer_id}: {len(child_accounts)}")

    enabled_count = sum(1 for account in child_accounts if account["status"] == "ENABLED")
    suspended_count = sum(1 for account in child_accounts if account["status"] == "SUSPENDED")
    canceled_count = sum(1 for account in child_accounts if account["status"] == "CANCELED")
    hidden_count = sum(1 for account in child_accounts if account.get("hidden") is True)

    print(f"  ENABLED: {enabled_count}")
    print(f"  SUSPENDED: {suspended_count}")
    print(f"  CANCELED: {canceled_count}")
    print(f"  HIDDEN: {hidden_count}")

    if not child_accounts:
        return []

    workers = max(1, int(parallel_workers or 1))
    workers = min(workers, len(child_accounts))

    if workers == 1:
        results = []

        for index, account in enumerate(child_accounts):
            result = build_spend_result_for_account(
                google_ads_client,
                account,
                start_date,
                end_date,
                context
            )

            if result:
                results.append((index, result))

        return [result for _, result in results]

    print(f"  Consultas de gasto en paralelo: {workers} hilos")

    results = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_index = {
            executor.submit(
                build_spend_result_for_account,
                google_ads_client,
                account,
                start_date,
                end_date,
                context
            ): index
            for index, account in enumerate(child_accounts)
        }

        for future in as_completed(future_to_index):
            index = future_to_index[future]
            result = future.result()

            if result:
                results.append((index, result))

    results.sort(key=lambda item: item[0])
    return [result for _, result in results]


def get_accounts_with_daily_spend(
    google_ads_client,
    cliente,
    start_date,
    end_date,
    context="hoja diaria",
    parallel_workers=1
):
    mcc_id = cliente["mcc_id"]
    sub_mcc_ids = cliente.get("sub_mcc_ids", [])

    all_results = []
    seen_ids = set()

    if sub_mcc_ids:
        print("Sub-MCCs configurados. Solo se consultarán estos:")
        for sub_mcc_id in sub_mcc_ids:
            print(f"  - {sub_mcc_id}")

        for sub_mcc_id in sub_mcc_ids:
            print(f"\nConsultando sub-MCC {sub_mcc_id}...")
            sub_results = get_accounts_with_daily_spend_for_manager(
                google_ads_client,
                sub_mcc_id,
                start_date,
                end_date,
                context,
                parallel_workers
            )

            for item in sub_results:
                account_id = normalize_customer_id(item["id"])

                if account_id not in seen_ids:
                    all_results.append(item)
                    seen_ids.add(account_id)

    else:
        print("No hay sub-MCCs configurados. Se consultará el MCC principal completo.")
        all_results = get_accounts_with_daily_spend_for_manager(
            google_ads_client,
            mcc_id,
            start_date,
            end_date,
            context,
            parallel_workers
        )

    return all_results


def fetch_client_daily_spend(
    cliente,
    start_date,
    end_date,
    context,
    parallel_workers,
):
    manager_sources = get_manager_sources(cliente)
    source_result_groups = []

    print("Fuentes MCC configuradas:")
    for source in manager_sources:
        print(f"  - {source['nombre']}: {source['mcc_id']}")

    for source in manager_sources:
        print(
            f"\nConsultando fuente MCC {source['nombre']} "
            f"({source['mcc_id']})..."
        )
        google_ads_client = create_google_ads_client(
            cliente,
            login_customer_id=source["mcc_id"],
        )
        source_client = dict(cliente)
        source_client["mcc_id"] = source["mcc_id"]
        source_client["sub_mcc_ids"] = source["sub_mcc_ids"]

        source_result_groups.append(get_accounts_with_daily_spend(
            google_ads_client,
            source_client,
            start_date,
            end_date,
            context,
            parallel_workers,
        ))

    accounts, duplicate_ids = merge_unique_daily_account_results(
        source_result_groups
    )

    if duplicate_ids:
        print(
            f"Cuentas visibles desde varias fuentes MCC y deduplicadas: "
            f"{len(duplicate_ids)}"
        )

    return accounts


def get_accounts_with_monthly_spend_for_manager(
    google_ads_client,
    manager_customer_id,
    start_date,
    end_date,
    month_keys
):
    child_accounts = get_child_accounts(google_ads_client, manager_customer_id)
    results = []

    print(f"  Cuentas finales encontradas en {manager_customer_id}: {len(child_accounts)}")

    for account in child_accounts:
        account_id = account["id"]

        account_name, daily_costs, total_cost = get_account_daily_spend_with_retries(
            google_ads_client,
            account,
            start_date,
            end_date,
            "hoja historica mensual"
        )

        if total_cost > 0:
            monthly_costs = build_monthly_costs(daily_costs, month_keys)

            results.append({
                "name": account_name or account["name"],
                "id": account_id,
                "status": account["status"],
                "monthly_costs": monthly_costs,
                "total_cost": round(sum(monthly_costs.values()), 2),
            })

    return results


def get_accounts_with_monthly_spend(
    google_ads_client,
    cliente,
    start_date,
    end_date,
    month_keys
):
    mcc_id = cliente["mcc_id"]
    sub_mcc_ids = cliente.get("sub_mcc_ids", [])

    all_results = []
    seen_ids = set()

    if sub_mcc_ids:
        print("Sub-MCCs configurados para historico mensual. Solo se consultaran estos:")
        for sub_mcc_id in sub_mcc_ids:
            print(f"  - {sub_mcc_id}")

        for sub_mcc_id in sub_mcc_ids:
            print(f"\nConsultando historico mensual en sub-MCC {sub_mcc_id}...")
            sub_results = get_accounts_with_monthly_spend_for_manager(
                google_ads_client,
                sub_mcc_id,
                start_date,
                end_date,
                month_keys
            )

            for item in sub_results:
                account_id = normalize_customer_id(item["id"])

                if account_id not in seen_ids:
                    all_results.append(item)
                    seen_ids.add(account_id)

    else:
        print("No hay sub-MCCs configurados. Se consultara el MCC principal completo.")
        all_results = get_accounts_with_monthly_spend_for_manager(
            google_ads_client,
            mcc_id,
            start_date,
            end_date,
            month_keys
        )

    return all_results


def filter_daily_costs_by_period(daily_costs, start_date, end_date):
    return {
        day: cost
        for day, cost in daily_costs.items()
        if start_date <= day <= end_date
    }


def build_accounts_for_daily_sheet(accounts_with_period_spend, start_date, end_date):
    """
    Prepara la hoja diaria desde la lectura unica de Google Ads.
    Asi la hoja diaria y la mensual parten de la misma foto de datos.
    """
    results = []

    for account in accounts_with_period_spend:
        daily_costs = filter_daily_costs_by_period(
            account["daily_costs"],
            start_date,
            end_date
        )
        total_cost = round(sum(daily_costs.values()), 2)

        if total_cost <= 0:
            continue

        results.append({
            "name": account["name"],
            "id": account["id"],
            "status": account.get("status", ""),
            "hidden": account.get("hidden", False),
            "level": account.get("level"),
            "daily_costs": daily_costs,
            "total_cost": total_cost,
        })

    return results


def build_accounts_for_monthly_sheet(accounts_with_period_spend, month_keys):
    """
    Prepara la hoja mensual desde la misma lectura usada por la hoja diaria.
    Evita que Google Ads devuelva centimos distintos entre dos consultas.
    """
    results = []

    for account in accounts_with_period_spend:
        monthly_costs = build_monthly_costs(account["daily_costs"], month_keys)
        total_cost = round(sum(monthly_costs.values()), 2)

        if total_cost <= 0:
            continue

        results.append({
            "name": account["name"],
            "id": account["id"],
            "status": account.get("status", ""),
            "hidden": account.get("hidden", False),
            "level": account.get("level"),
            "monthly_costs": monthly_costs,
            "total_cost": total_cost,
        })

    return results


def merge_monthly_account_results(accounts):
    merged = {}
    order = []

    for account in accounts:
        account_id = normalize_customer_id(account["id"])

        if not account_id:
            continue

        if account_id not in merged:
            merged[account_id] = {
                "name": account["name"],
                "id": account_id,
                "status": account.get("status", ""),
                "hidden": account.get("hidden", False),
                "monthly_costs": {},
                "total_cost": 0,
            }
            order.append(account_id)

        item = merged[account_id]

        if not item["name"] and account.get("name"):
            item["name"] = account["name"]

        if not item["status"] and account.get("status"):
            item["status"] = account["status"]

        if account.get("hidden"):
            item["hidden"] = True

        for month_key, cost in account.get("monthly_costs", {}).items():
            item["monthly_costs"][month_key] = round(
                item["monthly_costs"].get(month_key, 0) + cost,
                2
            )

    results = []

    for account_id in order:
        item = merged[account_id]
        item["total_cost"] = round(sum(item["monthly_costs"].values()), 2)

        if item["total_cost"] > 0:
            results.append(item)

    return results


def filter_monthly_account_results(accounts, month_keys):
    wanted_month_keys = set(month_keys)
    results = []

    for account in accounts:
        monthly_costs = {
            month_key: cost
            for month_key, cost in account.get("monthly_costs", {}).items()
            if month_key in wanted_month_keys
        }
        total_cost = round(sum(monthly_costs.values()), 2)

        if total_cost <= 0:
            continue

        results.append({
            "name": account["name"],
            "id": account["id"],
            "status": account.get("status", ""),
            "hidden": account.get("hidden", False),
            "monthly_costs": monthly_costs,
            "total_cost": total_cost,
        })

    return results


def find_header_row(values):
    account_headers = get_header_aliases([
        "Cuenta",
        "Nombre de cuenta",
        "Account",
        "Account name",
    ])
    id_headers = get_header_aliases([
        "ID de cuenta",
        "ID cuenta",
        "Customer ID",
        "Account ID",
        "ID",
    ])

    for index, row in enumerate(values, start=1):
        normalized = [normalize_match_text(cell) for cell in row]

        has_account = any(
            cell in account_headers
            for cell in normalized
        )

        has_id = any(
            cell in id_headers
            for cell in normalized
        )

        if has_account and has_id:
            return index, row

    raise ValueError("No encontré la fila de encabezados con cuenta e ID de cuenta.")


def find_column(header_row, possible_names):
    possible_names = get_header_aliases(possible_names)

    for index, value in enumerate(header_row, start=1):
        if normalize_match_text(value) in possible_names:
            return index

    return None


def get_date_columns(header_row):
    date_columns = {}

    for index, value in enumerate(header_row, start=1):
        value = value.strip()

        if len(value) == 10 and value.count("-") == 2:
            date_columns[value] = index

    return date_columns


def find_month_column(header_row):
    labels = get_current_month_labels()
    normalized_labels = [label.strip().lower() for label in labels]

    for index, value in enumerate(header_row, start=1):
        clean_value = value.strip().lower()

        if clean_value in normalized_labels:
            return index, value.strip()

    today = date.today()
    month_names = {
        1: ["enero", "jan", "january"],
        2: ["febrero", "feb", "february"],
        3: ["marzo", "mar", "march"],
        4: ["abril", "apr", "april"],
        5: ["mayo", "may"],
        6: ["junio", "jun", "june"],
        7: ["julio", "jul", "july"],
        8: ["agosto", "aug", "august"],
        9: ["septiembre", "sep", "september"],
        10: ["octubre", "oct", "october"],
        11: ["noviembre", "nov", "november"],
        12: ["diciembre", "dec", "december"],
    }

    allowed_month_names = month_names[today.month]

    for index, value in enumerate(header_row, start=1):
        clean_value = value.strip().lower()

        if clean_value in allowed_month_names:
            return index, value.strip()

    return None, None


def find_month_columns(values, header_row_index, header_row, month_keys):
    month_columns = {}
    rows_to_scan = [(header_row_index, header_row)]

    for row_index, row in enumerate(values[:header_row_index - 1], start=1):
        rows_to_scan.append((row_index, row))

    for month_key in month_keys:
        labels = {
            normalize_sheet_text(label)
            for label in get_month_header_labels(month_key)
            + get_month_name_labels(month_key)
        }

        for row_index, row in rows_to_scan:
            for index, value in enumerate(row, start=1):
                if normalize_sheet_text(value) in labels:
                    month_columns[month_key] = {
                        "index": index,
                        "label": str(value).strip(),
                        "source_row": row_index,
                    }
                    break

            if month_key in month_columns:
                break

    return month_columns


def print_failed_account_queries():
    if not FAILED_ACCOUNT_QUERIES:
        print("\nResumen consultas Google Ads: sin cuentas fallidas.")
        return

    fatal_items = [
        item for item in FAILED_ACCOUNT_QUERIES
        if item.get("fatal", True)
    ]
    skipped_items = [
        item for item in FAILED_ACCOUNT_QUERIES
        if not item.get("fatal", True)
    ]

    if skipped_items:
        print("\nResumen consultas Google Ads: cuentas omitidas sin borrar historico.")
        for item in skipped_items:
            print(
                f"  {item['context']} | {item['id']} | "
                f"{item['name']} | {item['error']}"
            )

    if not fatal_items:
        return

    print("\nResumen consultas Google Ads: cuentas fallidas tras reintentos.")
    for item in fatal_items:
        print(
            f"  {item['context']} | {item['id']} | "
            f"{item['name']} | {item['error']}"
        )


def get_failed_account_ids(context=None):
    return {
        normalize_customer_id(item["id"])
        for item in FAILED_ACCOUNT_QUERIES
        if context is None or item["context"] == context
    }


def find_first_empty_row(values, header_row_index, cuenta_col, id_col):
    for row_index, row in enumerate(values, start=1):
        if row_index <= header_row_index:
            continue

        cuenta = row[cuenta_col - 1] if len(row) >= cuenta_col else ""
        account_id = row[id_col - 1] if len(row) >= id_col else ""

        if not str(cuenta).strip() and not str(account_id).strip():
            return row_index

    return len(values) + 1


def group_contiguous_columns(columns):
    ordered = sorted({int(column) for column in columns if column})

    if not ordered:
        return []

    groups = []
    start = previous = ordered[0]

    for column in ordered[1:]:
        if column == previous + 1:
            previous = column
            continue

        groups.append((start, previous))
        start = previous = column

    groups.append((start, previous))
    return groups


def build_euro_format_requests(sheet_id, ranges):
    """Crea formatos EUR sin modificar valores, formulas ni otros estilos."""
    requests = []

    for start_row, end_row, start_col, end_col in ranges:
        if end_row < start_row or end_col < start_col:
            continue

        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": sheet_id,
                    "startRowIndex": start_row - 1,
                    "endRowIndex": end_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {
                            "type": "CURRENCY",
                            "pattern": EURO_NUMBER_FORMAT_PATTERN,
                        }
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        })

    return requests


def apply_euro_formats(worksheet, ranges):
    requests = build_euro_format_requests(worksheet.id, ranges)

    if requests:
        worksheet.spreadsheet.batch_update({"requests": requests})

    return len(requests)


def apply_daily_euro_formats(
    worksheet,
    values,
    header_row_index,
    last_data_row,
    total_col,
    date_columns,
):
    ranges = []
    currency_columns = [total_col, *date_columns.values()]

    for start_col, end_col in group_contiguous_columns(currency_columns):
        ranges.append((header_row_index + 1, last_data_row, start_col, end_col))

    if date_columns:
        first_date_col = min(date_columns.values())
        last_date_col = max(date_columns.values())
        summary_start_col = max(1, first_date_col - 1)

        for row_index, row in enumerate(
            values[:header_row_index - 1],
            start=1,
        ):
            if any(
                normalize_match_text(value).startswith("googleadstotal")
                for value in row
            ):
                ranges.append((
                    row_index,
                    row_index,
                    summary_start_col,
                    last_date_col,
                ))
                break

    return apply_euro_formats(worksheet, ranges)


def apply_monthly_euro_formats(
    worksheet,
    values,
    header_row_index,
    last_data_row,
    month_columns,
    total_col=None,
):
    ranges = []
    currency_columns = [total_col, *month_columns]

    for start_col, end_col in group_contiguous_columns(currency_columns):
        ranges.append((header_row_index + 1, last_data_row, start_col, end_col))

    if month_columns and header_row_index > 1:
        first_month_col = min(month_columns)
        last_month_col = max(month_columns)
        summary_start_col = first_month_col
        summary_header_row = values[header_row_index - 3]
        total_header_col = first_month_col - 1

        if (
            total_header_col >= 1
            and len(summary_header_row) >= total_header_col
            and normalize_match_text(
                summary_header_row[total_header_col - 1]
            ) == "total"
        ):
            summary_start_col = total_header_col

        ranges.append((
            header_row_index - 1,
            header_row_index - 1,
            summary_start_col,
            last_month_col,
        ))

    return apply_euro_formats(worksheet, ranges)


def build_id_to_row(values, header_row_index, id_col):
    id_to_row = {}

    for row_index, row in enumerate(values, start=1):
        if row_index <= header_row_index:
            continue

        if len(row) >= id_col:
            customer_id = normalize_customer_id(row[id_col - 1])

            if customer_id:
                id_to_row[customer_id] = row_index

    return id_to_row


def build_daily_total_title(value, target_day):
    normalized = normalize_match_text(value)
    if not normalized.startswith(("googleadstotal", "googletotal")):
        return None

    raw_value = str(value).strip()
    total_match = re.search(r"(?i)\btotal\b", raw_value)
    prefix = (
        raw_value[:total_match.end()].strip()
        if total_match
        else "Google Ads TOTAL"
    )
    return f"{prefix} {get_month_abbr(target_day.month)} {target_day.year}"


def clear_out_of_month_daily_cells(worksheet, values, target_day):
    header_row_index, header_row = find_header_row(values)
    date_columns = get_date_columns(header_row)
    if not date_columns:
        return []

    first_date_col = min(date_columns.values())
    days_in_target_month = monthrange(target_day.year, target_day.month)[1]
    first_extra_col = first_date_col + days_in_target_month
    last_calendar_col = min(first_date_col + 30, worksheet.col_count)
    if first_extra_col > last_calendar_col:
        return []

    last_used_row = max(len(values), header_row_index)
    start_cell = gspread.utils.rowcol_to_a1(1, first_extra_col)
    end_cell = gspread.utils.rowcol_to_a1(last_used_row, last_calendar_col)
    clear_range = f"{start_cell}:{end_cell}"
    worksheet.batch_clear([clear_range])
    return [clear_range]


def update_daily_sheet_date_headers(worksheet, source_day, target_day):
    values = worksheet.get_all_values()
    header_row_index, header_row = find_header_row(values)
    date_columns = get_date_columns(header_row)

    if not date_columns:
        print("No se encontraron fechas que actualizar en la nueva hoja diaria.")
        return

    col_to_date = {
        col: day
        for day, col in date_columns.items()
    }
    min_col = min(col_to_date)
    max_col = max(col_to_date)
    days_in_target_month = monthrange(target_day.year, target_day.month)[1]
    max_col = max(max_col, min_col + days_in_target_month - 1)

    if max_col > worksheet.col_count:
        worksheet.add_cols(max_col - worksheet.col_count)

    updated_header_slice = []

    for col in range(min_col, max_col + 1):
        value = header_row[col - 1] if len(header_row) >= col else ""
        day_number = col - min_col + 1

        if day_number <= days_in_target_month:
            value = date(target_day.year, target_day.month, day_number).isoformat()
        elif col in col_to_date:
            value = ""

        updated_header_slice.append(value)

    start_cell = gspread.utils.rowcol_to_a1(header_row_index, min_col)
    end_cell = gspread.utils.rowcol_to_a1(header_row_index, max_col)

    updates = [{
        "range": f"{start_cell}:{end_cell}",
        "values": [updated_header_slice],
    }]

    # La hoja se copia desde el mes anterior. Actualizamos tambien el titulo
    # decorativo para que no siga mostrando, por ejemplo, "Jun" en julio.
    title_updated = False

    for row_index, row in enumerate(values[:header_row_index - 1], start=1):
        for col_index, value in enumerate(row, start=1):
            updated_title = build_daily_total_title(value, target_day)
            if updated_title:
                title_cell = gspread.utils.rowcol_to_a1(row_index, col_index)
                updates.append({
                    "range": title_cell,
                    "values": [[updated_title]],
                })
                title_updated = True
                break

        if title_updated:
            break

    worksheet.batch_update(
        updates,
        value_input_option="USER_ENTERED"
    )
    clear_out_of_month_daily_cells(worksheet, values, target_day)


def get_preserved_failed_daily_accounts(
    values,
    id_to_row,
    failed_ids,
    cuenta_col,
    id_col,
    estado_col,
    date_columns,
    target_month_dates,
    target_month_key
):
    """
    Recupera de la hoja el gasto ya conocido de cuentas que Google Ads ha
    dejado de permitir consultar. Asi no se borra gasto real historico y el
    total informado por el script coincide con el total final de la hoja.
    """
    preserved_accounts = []

    for account_id in sorted(failed_ids):
        row_number = id_to_row.get(account_id)

        if not row_number or len(values) < row_number:
            continue

        row = values[row_number - 1]
        month_total = 0

        for day in target_month_dates:
            col = date_columns[day]
            value = row[col - 1] if len(row) >= col else ""
            month_total += parse_sheet_number(value)

        month_total = round(month_total, 2)

        if month_total <= 0:
            continue

        name = row[cuenta_col - 1] if len(row) >= cuenta_col else ""
        status = (
            row[estado_col - 1]
            if estado_col and len(row) >= estado_col
            else ""
        )

        preserved_accounts.append({
            "name": name,
            "id": account_id,
            "status": status,
            "monthly_costs": {target_month_key: month_total},
            "total_cost": month_total,
        })

    return preserved_accounts


def clear_daily_sheet_data(worksheet):
    values = worksheet.get_all_values()
    header_row_index, header_row = find_header_row(values)

    if len(values) <= header_row_index:
        return

    total_col = find_column(header_row, ["Total (cuentas)", "Total", "Total cuentas"]) or 1
    cuenta_col = find_column(header_row, ["Cuenta", "Nombre de cuenta", "Account"])
    id_col = find_column(header_row, ["ID de cuenta", "ID cuenta", "Customer ID", "ID"])
    estado_col = find_column(
        header_row,
        ["Estado de servicio", "Estado", "Serving status", "Status"]
    )
    date_columns = get_date_columns(header_row)

    last_date_col = max(date_columns.values()) if date_columns else 0
    last_col = max(total_col, cuenta_col or 0, id_col or 0, estado_col or 0, last_date_col)

    if not last_col:
        return

    start_cell = gspread.utils.rowcol_to_a1(header_row_index + 1, 1)
    end_cell = gspread.utils.rowcol_to_a1(len(values), last_col)
    worksheet.batch_clear([f"{start_cell}:{end_cell}"])


def get_or_create_daily_worksheet(
    spreadsheet,
    cliente,
    target_day=None,
    create_if_missing=True
):
    target_day = target_day or date.today()
    target_sheet_name = get_daily_sheet_name(cliente, target_day)

    try:
        return spreadsheet.worksheet(target_sheet_name)
    except WorksheetNotFound:
        if not create_if_missing:
            raise

        if not cliente.get("daily_create_from_previous_month", True):
            raise

    source_day = get_previous_month_day(target_day)
    source_sheet_name = get_daily_sheet_name(cliente, source_day)

    print(
        f"No existe la hoja diaria '{target_sheet_name}'. "
        f"Creo una copia desde '{source_sheet_name}'."
    )

    try:
        source_worksheet = spreadsheet.worksheet(source_sheet_name)
    except WorksheetNotFound as exc:
        raise WorksheetNotFound(
            f"No existe la hoja diaria '{target_sheet_name}' y tampoco "
            f"la hoja del mes anterior '{source_sheet_name}' para copiarla."
        ) from exc

    worksheet = spreadsheet.duplicate_sheet(
        source_worksheet.id,
        new_sheet_name=target_sheet_name,
    )

    update_daily_sheet_date_headers(worksheet, source_day, target_day)
    clear_daily_sheet_data(worksheet)

    print(f"Hoja diaria creada: {target_sheet_name}")
    return worksheet


def update_monthly_sheet_year_headers(worksheet, target_day):
    values = worksheet.get_all_values()
    header_row_index, header_row = find_header_row(values)
    cuenta_col = find_column(header_row, ["Cuenta", "Nombre de cuenta", "Account"])
    id_col = find_column(header_row, ["ID de cuenta", "ID cuenta", "Customer ID", "ID"])

    if not cuenta_col or not id_col:
        raise ValueError("No encontré columnas Cuenta / ID de cuenta en la hoja mensual copiada.")

    start_col = max(cuenta_col, id_col) + 1
    month_labels = [
        get_month_name_labels(f"{target_day.year}-{month:02d}")[0].capitalize()
        for month in range(1, 13)
    ]
    month_keys = [
        f"{target_day.year}|{month:02d}"
        for month in range(1, 13)
    ]
    end_col = start_col + 11

    if end_col > worksheet.col_count:
        worksheet.add_cols(end_col - worksheet.col_count)

    worksheet.update(
        range_name="A1",
        values=[[str(target_day.year)]],
        value_input_option="USER_ENTERED"
    )
    worksheet.update(
        range_name=(
            f"{gspread.utils.rowcol_to_a1(1, start_col)}:"
            f"{gspread.utils.rowcol_to_a1(1, end_col)}"
        ),
        values=[month_labels],
        value_input_option="USER_ENTERED"
    )
    worksheet.update(
        range_name=(
            f"{gspread.utils.rowcol_to_a1(header_row_index, start_col)}:"
            f"{gspread.utils.rowcol_to_a1(header_row_index, end_col)}"
        ),
        values=[month_keys],
        value_input_option="USER_ENTERED"
    )


def clear_monthly_sheet_data(worksheet):
    values = worksheet.get_all_values()
    header_row_index, header_row = find_header_row(values)

    if len(values) <= header_row_index:
        return

    cuenta_col = find_column(header_row, ["Cuenta", "Nombre de cuenta", "Account"])
    id_col = find_column(header_row, ["ID de cuenta", "ID cuenta", "Customer ID", "ID"])
    month_columns = find_month_columns(
        values,
        header_row_index,
        header_row,
        [f"{date.today().year}-{month:02d}" for month in range(1, 13)]
    )
    last_month_col = max(
        [item["index"] for item in month_columns.values()] or [0]
    )
    last_col = max(cuenta_col or 0, id_col or 0, last_month_col)

    if not last_col:
        return

    start_cell = gspread.utils.rowcol_to_a1(header_row_index + 1, 1)
    end_cell = gspread.utils.rowcol_to_a1(len(values), last_col)
    worksheet.batch_clear([f"{start_cell}:{end_cell}"])


def get_or_create_monthly_worksheet(spreadsheet, cliente, target_day=None):
    target_day = target_day or date.today()
    target_sheet_name = get_monthly_sheet_name(cliente, target_day)

    try:
        return spreadsheet.worksheet(target_sheet_name)
    except WorksheetNotFound:
        if not cliente.get("monthly_create_from_previous_year", True):
            raise

    source_day = target_day.replace(year=target_day.year - 1, day=1)
    source_sheet_name = get_monthly_sheet_name(cliente, source_day)

    print(
        f"No existe la hoja mensual '{target_sheet_name}'. "
        f"Creo una copia desde '{source_sheet_name}'."
    )

    try:
        source_worksheet = spreadsheet.worksheet(source_sheet_name)
    except WorksheetNotFound as exc:
        raise WorksheetNotFound(
            f"No existe la hoja mensual '{target_sheet_name}' y tampoco "
            f"la hoja del año anterior '{source_sheet_name}' para copiarla."
        ) from exc

    worksheet = spreadsheet.duplicate_sheet(
        source_worksheet.id,
        new_sheet_name=target_sheet_name,
    )

    update_monthly_sheet_year_headers(worksheet, target_day)
    clear_monthly_sheet_data(worksheet)

    print(f"Hoja mensual creada: {target_sheet_name}")
    return worksheet


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
            create_if_missing
        )
    except WorksheetNotFound:
        print(
            f"No existe la hoja diaria '{daily_sheet_name}'. "
            "Salto esta hoja para no crear meses antiguos automaticamente."
        )
        return []

    daily_sheet_name = worksheet.title
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
        if account_id in result_ids or account_id in failed_ids:
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
        if account["id"] in failed_ids
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
    monthly_sheet_name = get_monthly_sheet_name(cliente, target_day)

    if not monthly_sheet_name:
        print("No hay hoja histórica mensual configurada. Salto hoja mensual.")
        return

    if spreadsheet is None:
        spreadsheet = sheets_client.open_by_key(cliente["spreadsheet_id"])
    worksheet = get_or_create_monthly_worksheet(spreadsheet, cliente, target_day)
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
        if account_id in result_ids or account_id in failed_ids:
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

        # Solo escribimos los meses configurados y existentes en la hoja.
        # Los meses anteriores, futuros o no detectados se quedan intactos.
        for month_info in month_columns.values():
            matrix[matrix_index][month_info["index"] - 1] = ""

        for month_key, cost in account["monthly_costs"].items():
            if month_key in month_columns:
                month_col = month_columns[month_key]["index"]
                matrix[matrix_index][month_col - 1] = cost

        if total_col:
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


def get_historical_backfill_period(backfill_year, today):
    if backfill_year > today.year:
        raise ValueError("No se puede rellenar un ano futuro.")

    start_day = date(backfill_year, 1, 1)
    end_day = (
        today
        if backfill_year == today.year
        else date(backfill_year, 12, 31)
    )
    month_keys = get_month_keys_between(
        f"{backfill_year}-01",
        end_day,
    )
    return start_day, end_day, month_keys


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
        accounts_with_period_spend = fetch_client_daily_spend(
            cliente,
            start_day.isoformat(),
            end_day.isoformat(),
            f"backfill historico {backfill_year}",
            parallel_workers,
        )
        accounts_with_monthly_spend = build_accounts_for_monthly_sheet(
            accounts_with_period_spend,
            month_keys,
        )
        print(
            f"Cuentas con gasto en el backfill: "
            f"{len(accounts_with_monthly_spend)}"
        )
        process_monthly_sheet_for_client(
            sheets_client,
            cliente,
            accounts_with_monthly_spend,
            month_keys,
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

        if today.day == 1:
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


if __name__ == "__main__":
    main()
