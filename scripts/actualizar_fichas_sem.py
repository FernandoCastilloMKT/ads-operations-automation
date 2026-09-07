import argparse
import calendar
import json
import os
import re
import sys
import threading
import time
import unicodedata
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
from dotenv import load_dotenv
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.oauth2.service_account import Credentials
from gspread.http_client import BackOffHTTPClient

from runtime_config import (
    load_consumption_runtime_config,
    load_sem_runtime_config,
    normalize_config_key,
)

BASE_DIR = Path(__file__).resolve().parent.parent
env_path = BASE_DIR / ".env"
load_dotenv(env_path, override=True)
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

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

ALL_SEM_CLIENTS_VALUE = "TODAS"
SPAIN_TIMEZONE_NAME = "Europe/Madrid"
SPAIN_TIMEZONE = ZoneInfo(SPAIN_TIMEZONE_NAME)
MAX_GOOGLE_ADS_RETRIES = 3
RETRY_DELAY_SECONDS = 5
MAX_ADS_WORKERS = 5
SHEET_WRITE_BATCH_CLIENTS = 8
HISTORICAL_SIDE_BAND_COLOR = {"red": 0.4, "green": 0.4, "blue": 0.4}
MICROSOFT_ANNUAL_PERIOD_KEY = "microsoft_annual_current"
MICROSOFT_ANNUAL_ARCHIVE_KEY_PREFIX = "microsoft_annual_archive_"
MICROSOFT_ANNUAL_ARCHIVE_FIRST_COLUMN = 18  # R
MICROSOFT_ANNUAL_ARCHIVE_COLUMN_STEP = 13
MICROSOFT_ANNUAL_BASE_YEAR = 2026
ACCOUNT_STATUS_ENABLED = "Enabled"
ACCOUNT_STATUS_PAUSED = "Paused"
ACCOUNT_STATUS_FINISHED = "Finalizada"
ACCOUNT_STATUS_ENABLED_COLOR = {
    "red": 0,
    "green": 1,
    "blue": 0,
}
ACCOUNT_STATUS_DISABLED_COLOR = {"red": 1, "green": 0, "blue": 0}
VISTA_GLOBAL_SIGNED_MARKER = "Clientes firmados sin comenzar"
VISTA_GLOBAL_STANDBY_MARKER = "Stand By"
VISTA_GLOBAL_NATALIA_MARKER = "Natalia"
VISTA_GLOBAL_STATUS_COLUMN = 0
VISTA_GLOBAL_CLIENT_COLUMN = 2
VISTA_GLOBAL_MONTHLY_BUDGET_COLUMN = 7
MONTHLY_RENEWAL_TEXT = "Renueva mes a mes sin fecha fin"
MONTHLY_RENEWAL_TEXTS = (
    MONTHLY_RENEWAL_TEXT,
    "Renueva mes a mes hasta nueva orden",
)
MONTHLY_RENEWAL_NOTE_PREFIX = (
    "SEM - ultima renovacion mensual: "
)
OPTIONAL_DECIMAL_FORMAT = "#,##0.##"
OPTIONAL_PERCENT_FORMAT = "0.##%"
OPTIONAL_CURRENCY_FORMAT = "[$\u20ac]#,##0.##"
INTEGER_CURRENCY_FORMAT = "[$\u20ac]#,##0"
TOTAL_CURRENCY_FORMAT = OPTIONAL_CURRENCY_FORMAT
INTEGER_NUMBER_FORMAT = "#,##0"
INTEGER_PERCENT_FORMAT = "0%"
GOOGLE_SHEETS_DATE_EPOCH = date(1899, 12, 30)


_google_ads_thread_local = threading.local()


class SaldoPeriodValidationError(ValueError):
    pass


class SpreadsheetMutationBatch:
    """Agrupa valores y formatos de varias fichas en pocas llamadas API."""

    def __init__(self, spreadsheet):
        self.spreadsheet = spreadsheet
        self.value_updates = []
        self.format_requests = []
        self.client_count = 0

    def checkpoint(self):
        return (
            len(self.value_updates),
            len(self.format_requests),
            self.client_count,
        )

    def rollback(self, checkpoint):
        value_count, request_count, client_count = checkpoint
        del self.value_updates[value_count:]
        del self.format_requests[request_count:]
        self.client_count = client_count

    def add_values(self, worksheet, range_name, values):
        self.value_updates.append({
            "range": gspread.utils.absolute_range_name(
                worksheet.title,
                range_name,
            ),
            "values": values,
        })

    def add_requests(self, requests):
        self.format_requests.extend(requests)

    def mark_client(self):
        self.client_count += 1

    def should_flush(self):
        return self.client_count >= SHEET_WRITE_BATCH_CLIENTS

    def flush(self):
        if not self.value_updates and not self.format_requests:
            self.client_count = 0
            return {"value_ranges": 0, "format_requests": 0}

        value_count = len(self.value_updates)
        request_count = len(self.format_requests)

        if self.value_updates:
            self.spreadsheet.values_batch_update({
                "valueInputOption": "USER_ENTERED",
                "data": self.value_updates,
            })

        if self.format_requests:
            self.spreadsheet.batch_update({
                "requests": self.format_requests,
            })

        self.value_updates = []
        self.format_requests = []
        self.client_count = 0
        return {
            "value_ranges": value_count,
            "format_requests": request_count,
        }

MONTH_NAMES_ES = {
    1: "enero",
    2: "febrero",
    3: "marzo",
    4: "abril",
    5: "mayo",
    6: "junio",
    7: "julio",
    8: "agosto",
    9: "septiembre",
    10: "octubre",
    11: "noviembre",
    12: "diciembre",
}

MONTH_ALIASES_ES = {
    1: ("enero", "ene"),
    2: ("febrero", "feb"),
    3: ("marzo", "mar"),
    4: ("abril", "abr"),
    5: ("mayo", "may"),
    6: ("junio", "jun"),
    7: ("julio", "jul"),
    8: ("agosto", "ago"),
    9: ("septiembre", "sep", "sept"),
    10: ("octubre", "oct"),
    11: ("noviembre", "nov"),
    12: ("diciembre", "dic"),
}

MONTH_NUMBERS_BY_NAME = {
    alias: month
    for month, aliases in MONTH_ALIASES_ES.items()
    for alias in aliases
}

SEM_RUNTIME_CONFIG = load_sem_runtime_config()
CONTROL_SEM_SPREADSHEET_ID = SEM_RUNTIME_CONFIG["control_sem_spreadsheet_id"]
VISTA_GLOBAL_WORKSHEET_NAME = SEM_RUNTIME_CONFIG.get(
    "control_sem_worksheet_name",
    "Vista Global",
)
SEM_CLIENTS = SEM_RUNTIME_CONFIG["clients"]
SEM_ONLY_MCC_CONFIGS = SEM_RUNTIME_CONFIG.get("sem_only_mcc_configs", {})
EXCLUDED_SEM_CLIENTS = {
    normalize_config_key(value)
    for value in SEM_RUNTIME_CONFIG.get("excluded_sem_clients", [])
}
MONTHLY_RENEWAL_NOTE_PREFIXES = (
    MONTHLY_RENEWAL_NOTE_PREFIX,
    *SEM_RUNTIME_CONFIG.get("legacy_renewal_note_prefixes", []),
)

EXPECTED_HEADERS = [
    "Nombre de la campana",
    "Estado de la campana",
    "Clics",
    "CTR",
    "CPC",
    "Costo",
    "Conversiones",
    "Costo por conversion",
    "Impresiones",
    "Porcentaje de impresiones en la parte superior",
    "Porcentaje de impresiones en la parte superior absoluta",
    "Presupuesto diario",
]

STANDARD_HEADER_BY_KEY = {
    "campaign_name": "Nombre de la campana",
    "campaign_status": "Estado de la campana",
    "clicks": "Clics",
    "ctr": "CTR",
    "average_cpc": "CPC",
    "cost": "Costo",
    "conversions": "Conversiones",
    "cost_per_conversion": "Costo por conversion",
    "impressions": "Impresiones",
    "top_impression_share": "Porcentaje de impresiones en la parte superior",
    "absolute_top_impression_share": (
        "Porcentaje de impresiones en la parte superior absoluta"
    ),
    "daily_budget": "Presupuesto diario",
}

ANNUAL_CONTROL_HEADERS = [
    "Campa\u00f1a/Medio",
    "Ubicaci\u00f3n",
    "Estado",
    "Inicio campa\u00f1a",
    "Fin campa\u00f1a",
    "Clics",
    "Impresiones",
    "CTR %",
    "Coste",
    "CPC",
    "CPM",
    "Presupuesto diario",
    "Presupuesto total",
]

ANNUAL_CONTROL_HANDLER = "annual_campaign_control"

HEADER_ALIASES = {
    "campaign_name": [
        "Nombre de la campana",
        "Nombre de la campaña",
        "Campaign name",
    ],
    "campaign_status": [
        "Estado de la campana",
        "Estado de la campaña",
        "Campaign status",
    ],
    "clicks": ["Clics", "Clicks"],
    "ctr": ["CTR"],
    "average_cpc": ["CPC"],
    "cost": ["Costo", "Coste", "Cost"],
    "conversions": [
        "Conversiones",
        "Conversions",
        "Todas las conversiones",
        "All conversions",
    ],
    "cost_per_conversion": [
        "Costo por conversion",
        "Costo por conversión",
        "Cost per conversion",
        "Valor total de conversion",
        "Valor total de conversión",
        "Valor de todas las conversiones",
        "All conversions value",
    ],
    "impressions": ["Impresiones", "Impressions"],
    "top_impression_share": [
        "Porcentaje de impresiones en la parte superior",
        "Top impression percentage",
    ],
    "absolute_top_impression_share": [
        "Porcentaje de impresiones en la parte superior absoluta",
        "Absolute top impression percentage",
    ],
    "daily_budget": ["Presupuesto diario", "Presupuesto", "Daily budget", "Budget"],
}

# Estas metricas formaron parte temporalmente del bloque vivo. Se conservan
# solo como aliases de migracion para poder limpiar sus antiguas columnas.
OBSOLETE_HEADER_ALIASES = {
    "average_cpm": ["CPM"],
    "conversion_value": [
        "Valor de conversion",
        "Valor de conversión",
        "Valor de conversiones",
        "Valor total de conversion",
        "Valor total de conversión",
        "Conversion value",
    ],
    "all_conversions": [
        "Todas las conversiones",
        "All conversions",
    ],
    "all_conversions_value": [
        "Valor de todas las conversiones",
        "All conversions value",
    ],
}

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


def normalize_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


def normalize_sem_client_selector(value):
    raw_value = str(value or "")
    if not raw_value.strip():
        raise SystemExit("El cliente SEM no puede estar vacio.")
    if any(unicodedata.category(char).startswith("C") for char in raw_value):
        raise SystemExit(
            "El cliente SEM contiene caracteres de control no permitidos."
        )

    normalized = unicodedata.normalize("NFKD", raw_value)
    without_accents = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    return " ".join(without_accents.casefold().split())


def normalize_customer_id(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def get_sem_customer_ids(sem_client):
    configured_ids = sem_client.get("google_ads_customer_ids")
    if configured_ids is None:
        configured_ids = [sem_client.get("google_ads_customer_id")]

    customer_ids = []
    seen_ids = set()
    for value in configured_ids:
        customer_id = normalize_customer_id(value)
        if customer_id and customer_id not in seen_ids:
            customer_ids.append(customer_id)
            seen_ids.add(customer_id)

    if not customer_ids:
        raise ValueError(
            f"La ficha {sem_client['nombre']} no tiene cuentas Google Ads."
        )
    return customer_ids


def parse_month_name(value):
    exact_match = MONTH_NUMBERS_BY_NAME.get(normalize_text(value))
    if exact_match:
        return exact_match

    for token in re.findall(r"[a-z]+", fold_text(value)):
        month_number = MONTH_NUMBERS_BY_NAME.get(token)
        if month_number:
            return month_number

    return None


def is_total_cost_cell(value):
    # La variante `campa?as` quedo escrita por una conversion de codificacion
    # antigua. Se reconoce para poder repararla al actualizar la fila.
    return normalize_text(value) in {
        "totalcostecampanas",
        "totalcostecampaas",
        "totales",
    }


def select_sem_client(cliente_filter):
    normalized_filter = normalize_sem_client_selector(cliente_filter)

    if normalize_text(normalized_filter) in EXCLUDED_SEM_CLIENTS:
        raise SystemExit(
            "Cliente SEM excluido de momento por estructura especial: "
            f"{cliente_filter}"
        )

    for internal_key, client_config in SEM_CLIENTS.items():
        candidate_names = [
            internal_key,
            client_config["public_key"],
            client_config["nombre"],
            client_config["worksheet_name"],
            *client_config.get("aliases", []),
        ]

        for candidate_name in candidate_names:
            normalized_name = normalize_sem_client_selector(candidate_name)
            if normalized_filter == normalized_name:
                return client_config

    available = ", ".join(item["nombre"] for item in SEM_CLIENTS.values())
    raise SystemExit(
        "Cliente SEM no soportado en este piloto. "
        f"Disponibles: {available}"
    )


def resolve_sem_client_selection(cliente_filter):
    normalized_filter = normalize_sem_client_selector(cliente_filter)
    process_all = normalized_filter == normalize_sem_client_selector(
        ALL_SEM_CLIENTS_VALUE
    )
    if process_all:
        return True, list(SEM_CLIENTS.values())
    return False, [select_sem_client(cliente_filter)]


def load_client_config_by_mcc(mcc_id):
    config = load_consumption_runtime_config()

    wanted_mcc_id = normalize_customer_id(mcc_id)

    for cliente in config.get("clientes", []):
        if normalize_customer_id(cliente.get("mcc_id")) == wanted_mcc_id:
            return cliente

    sem_only_config = SEM_ONLY_MCC_CONFIGS.get(wanted_mcc_id)
    if sem_only_config:
        return sem_only_config

    raise SystemExit(
        f"No se encontro MCC {mcc_id} en config_clientes.json ni en la "
        "configuracion exclusiva de fichas SEM."
    )


def get_developer_token_for_client(cliente):
    developer_token_env = cliente.get(
        "developer_token_env",
        "GOOGLE_ADS_DEVELOPER_TOKEN",
    )
    token = os.getenv(developer_token_env)

    if not token:
        raise SystemExit(
            f"Falta la variable {developer_token_env} para {cliente['nombre']}"
        )

    return token


def create_google_ads_client(cliente):
    required = {
        "client_id": os.getenv("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": os.getenv("GOOGLE_ADS_REFRESH_TOKEN"),
    }
    missing = [name for name, value in required.items() if not value]

    if missing:
        raise SystemExit(
            "Faltan variables OAuth de Google Ads: " + ", ".join(missing)
        )

    google_ads_config = {
        "developer_token": get_developer_token_for_client(cliente),
        "client_id": required["client_id"],
        "client_secret": required["client_secret"],
        "refresh_token": required["refresh_token"],
        "login_customer_id": normalize_customer_id(cliente["mcc_id"]),
        "use_proto_plus": True,
    }
    return GoogleAdsClient.load_from_dict(google_ads_config)


def get_thread_google_ads_client(cliente):
    clients = getattr(_google_ads_thread_local, "clients", None)
    if clients is None:
        clients = {}
        _google_ads_thread_local.clients = clients

    mcc_id = normalize_customer_id(cliente["mcc_id"])
    if mcc_id not in clients:
        clients[mcc_id] = create_google_ads_client(cliente)
    return clients[mcc_id]


def create_sheets_client():
    credentials = Credentials.from_service_account_file(
        sheets_credentials_path,
        scopes=SCOPES,
    )
    # Sheets limita las escrituras por usuario y minuto. El cliente con
    # backoff espera y reintenta 429/5xx sin abandonar una ficha a medias.
    return gspread.authorize(
        credentials,
        http_client=BackOffHTTPClient,
    )


def set_worksheet_values_cache(worksheet, values):
    worksheet._sem_values_cache = values
    return values


def invalidate_worksheet_values_cache(worksheet):
    worksheet._sem_values_cache = None


def get_worksheet_values(worksheet, refresh=False):
    cached_values = getattr(worksheet, "_sem_values_cache", None)
    if refresh or cached_values is None:
        cached_values = worksheet.get_all_values()
        set_worksheet_values_cache(worksheet, cached_values)
    return cached_values


def prime_worksheet_values_cache(spreadsheet, worksheets):
    if not worksheets:
        return

    ranges = [
        gspread.utils.absolute_range_name(worksheet.title)
        for worksheet in worksheets
    ]
    response = spreadsheet.values_batch_get(
        ranges,
        params={"valueRenderOption": "FORMATTED_VALUE"},
    )
    value_ranges = response.get("valueRanges", [])

    if len(value_ranges) != len(worksheets):
        raise RuntimeError(
            "Google Sheets no devolvio todas las pestanas solicitadas "
            f"({len(value_ranges)}/{len(worksheets)})."
        )

    for worksheet, value_range in zip(worksheets, value_ranges):
        set_worksheet_values_cache(
            worksheet,
            value_range.get("values", []),
        )


def prime_monthly_renewal_notes(spreadsheet, worksheets, target_day):
    """Precarga en una llamada las marcas de renovación del día 1."""
    if target_day.day != 1:
        return

    worksheets_by_title = {}
    ranges = []
    for worksheet in worksheets:
        values = get_worksheet_values(worksheet)
        if not has_monthly_renewal_without_end(values):
            continue

        try:
            contract_range = find_contract_date_range(values)
        except SaldoPeriodValidationError:
            # La preparacion individual mostrara el error sin detener al resto.
            continue
        worksheet._sem_monthly_renewal_note = ""
        worksheets_by_title[worksheet.title] = worksheet
        ranges.append(
            gspread.utils.absolute_range_name(
                worksheet.title,
                contract_range["end_cell"],
            )
        )

    if not ranges:
        return

    try:
        metadata = spreadsheet.fetch_sheet_metadata(params={
            "includeGridData": "true",
            "ranges": ranges,
            "fields": (
                "sheets(properties(title),"
                "data(rowData(values(note))))"
            ),
        })
    except Exception:
        # Si falla la lectura agrupada, cada ficha leera su nota al prepararse.
        for worksheet in worksheets_by_title.values():
            delattr(worksheet, "_sem_monthly_renewal_note")
        return
    for sheet_data in metadata.get("sheets", []):
        title = sheet_data.get("properties", {}).get("title")
        worksheet = worksheets_by_title.get(title)
        if worksheet is None:
            continue

        note = ""
        data = sheet_data.get("data", [])
        if data:
            row_data = data[0].get("rowData", [])
            if row_data:
                cell_values = row_data[0].get("values", [])
                if cell_values:
                    note = cell_values[0].get("note", "")
        worksheet._sem_monthly_renewal_note = note


def queue_values_update(
    worksheet,
    range_name,
    values,
    mutation_batch=None,
):
    if mutation_batch is not None:
        mutation_batch.add_values(worksheet, range_name, values)
        return

    worksheet.update(
        range_name=range_name,
        values=values,
        value_input_option="USER_ENTERED",
    )


def queue_format_requests(worksheet, requests, mutation_batch=None):
    if not requests:
        return

    if mutation_batch is not None:
        mutation_batch.add_requests(requests)
        return

    worksheet.spreadsheet.batch_update({"requests": requests})


def queue_cell_note(worksheet, cell, note, mutation_batch=None):
    row, col = gspread.utils.a1_to_rowcol(cell)
    queue_format_requests(
        worksheet,
        [{
            "updateCells": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": col - 1,
                    "endColumnIndex": col,
                },
                "rows": [{"values": [{"note": note}]}],
                "fields": "note",
            }
        }],
        mutation_batch,
    )


def today_in_spain():
    return datetime.now(SPAIN_TIMEZONE).date()


def get_month_dates():
    today = today_in_spain()
    return today.replace(day=1), today


DATE_RANGE_PATTERN = re.compile(
    r"(?P<start_day>\d{1,2})\s*/\s*(?P<start_month>\d{1,2})"
    r"(?:\s*/\s*(?P<start_year>\d{2,4}))?"
    r"\s*(?:-|–|a|al)\s*"
    r"(?P<end_day>\d{1,2})\s*/\s*(?P<end_month>\d{1,2})"
    r"(?:\s*/\s*(?P<end_year>\d{2,4}))?",
    re.IGNORECASE,
)

PARTIAL_MONTH_END_PATTERN = re.compile(
    r"^\s*(?P<month>[a-z]+)\s*(?:-|\u2013|a|al)\s*"
    r"(?P<day>\d{1,2})\s*/\s*(?P<numeric_month>\d{1,2})\s*\??\s*$"
)
PARTIAL_MONTH_START_PATTERN = re.compile(
    r"^\s*(?P<day>\d{1,2})\s*/\s*(?P<numeric_month>\d{1,2})\s*"
    r"(?:-|\u2013|a|al)\s*(?P<month>[a-z]+)\s*\??\s*$"
)


def fold_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    ).lower()


def parse_partial_month_period(value):
    text = fold_text(value)

    for mode, pattern in (
        ("month_start_to_date", PARTIAL_MONTH_END_PATTERN),
        ("date_to_month_end", PARTIAL_MONTH_START_PATTERN),
    ):
        match = pattern.match(text)
        if not match:
            continue

        month_number = MONTH_NUMBERS_BY_NAME.get(match.group("month"))
        numeric_month = int(match.group("numeric_month"))
        day_number = int(match.group("day"))

        if not month_number or numeric_month != month_number:
            return None

        if day_number < 1 or day_number > calendar.monthrange(2000, month_number)[1]:
            return None

        return {
            "month_number": month_number,
            "start_day_number": 1 if mode == "month_start_to_date" else day_number,
            "end_day_number": day_number if mode == "month_start_to_date" else None,
        }

    return None


def parse_named_date_range_parts(value):
    text = fold_text(value).strip()

    day_span = re.fullmatch(
        r"\(?\s*(?P<start_day>\d{1,2})\s*-\s*"
        r"(?P<end_day>\d{1,2})\s*\)?\s*(?P<month>[a-z]+)",
        text,
    )
    if day_span:
        month_number = MONTH_NUMBERS_BY_NAME.get(day_span.group("month"))
        if month_number:
            return {
                "start_day_number": int(day_span.group("start_day")),
                "start_month": month_number,
                "end_day_number": int(day_span.group("end_day")),
                "end_month": month_number,
            }

    month_to_numeric = re.fullmatch(
        r"(?P<month>[a-z]+)\s*-\s*(?P<end_day>\d{1,2})\s*/\s*"
        r"(?P<end_month>\d{1,2})",
        text,
    )
    if month_to_numeric:
        month_number = MONTH_NUMBERS_BY_NAME.get(
            month_to_numeric.group("month")
        )
        if month_number:
            return {
                "start_day_number": 1,
                "start_month": month_number,
                "end_day_number": int(month_to_numeric.group("end_day")),
                "end_month": int(month_to_numeric.group("end_month")),
            }

    numeric_to_month = re.fullmatch(
        r"(?P<start_day>\d{1,2})\s*[/\-]\s*"
        r"(?P<start_month>\d{1,2})\s+-\s+"
        r"(?:(?P<end_day>\d{1,2})\s*/\s*)?"
        r"(?P<end_month>[a-z]+)",
        text,
    )
    if numeric_to_month:
        end_month = MONTH_NUMBERS_BY_NAME.get(
            numeric_to_month.group("end_month")
        )
        if end_month:
            end_day = numeric_to_month.group("end_day")
            return {
                "start_day_number": int(numeric_to_month.group("start_day")),
                "start_month": int(numeric_to_month.group("start_month")),
                "end_day_number": int(end_day) if end_day else None,
                "end_month": end_month,
            }

    return None


def build_named_date_range(parts, start_year):
    if not parts or not start_year:
        return None

    end_year = start_year
    if parts["end_month"] < parts["start_month"]:
        end_year += 1

    end_day_number = parts["end_day_number"] or calendar.monthrange(
        end_year,
        parts["end_month"],
    )[1]

    try:
        return {
            "start_day": date(
                start_year,
                parts["start_month"],
                parts["start_day_number"],
            ),
            "end_day": date(
                end_year,
                parts["end_month"],
                end_day_number,
            ),
        }
    except ValueError:
        return None


def is_uncertain_period(value):
    return str(value or "").strip().endswith("?")


def normalize_year(value, base_year=None):
    if not value:
        return base_year

    year = int(value)

    if year < 100:
        century = (base_year or today_in_spain().year) // 100 * 100
        year = century + year

    return year


def extract_explicit_year(value):
    match = re.search(r"\b(20\d{2})\b", str(value or ""))
    return int(match.group(1)) if match else None


def parse_date_range(
    value,
    reference_day=None,
    require_contains=False,
    default_start_year=None,
):
    text = str(value or "")
    match = DATE_RANGE_PATTERN.search(text)

    if not match:
        return None

    reference_day = reference_day or today_in_spain()
    explicit_year = extract_explicit_year(text)
    start_day = int(match.group("start_day"))
    start_month = int(match.group("start_month"))
    end_day = int(match.group("end_day"))
    end_month = int(match.group("end_month"))
    start_year_text = match.group("start_year")
    end_year_text = match.group("end_year")
    candidate_start_years = []

    if start_year_text:
        candidate_start_years.append(normalize_year(start_year_text, reference_day.year))
    elif explicit_year:
        candidate_start_years.append(explicit_year)
    elif default_start_year:
        candidate_start_years.append(default_start_year)
    else:
        candidate_start_years.extend([
            reference_day.year - 1,
            reference_day.year,
            reference_day.year + 1,
        ])

    candidates = []

    for start_year in candidate_start_years:
        if end_year_text:
            end_year = normalize_year(end_year_text, start_year)
        else:
            end_year = start_year
            if (end_month, end_day) < (start_month, start_day):
                end_year += 1

        try:
            start = date(start_year, start_month, start_day)
            end = date(end_year, end_month, end_day)
        except ValueError:
            continue

        candidates.append({
            "start_day": start,
            "end_day": end,
            "contains_reference": start <= reference_day <= end,
        })

    if not candidates:
        return None

    containing = [
        candidate
        for candidate in candidates
        if candidate["contains_reference"]
    ]

    if require_contains and not containing:
        return None

    if containing:
        return containing[0]

    return min(
        candidates,
        key=lambda candidate: abs((candidate["start_day"] - reference_day).days),
    )


def month_label_for_day(day):
    month_name = MONTH_NAMES_ES[day.month].capitalize()
    return f"Periodo seleccionado: {month_name} {day.year}"


def range_label_for_days(start_day, end_day):
    if start_day.year == end_day.year:
        return (
            "Periodo seleccionado: "
            f"{start_day:%d/%m} - {end_day:%d/%m} {end_day.year}"
        )
    return (
        "Periodo seleccionado: "
        f"{start_day:%d/%m/%Y} - {end_day:%d/%m/%Y}"
    )


def period_context_for_calendar_month(month_day, target_day):
    start_day = month_day.replace(day=1)
    end_day = date(
        start_day.year,
        start_day.month,
        calendar.monthrange(start_day.year, start_day.month)[1],
    )

    return {
        "mode": "month",
        "start_day": start_day,
        "end_day": end_day,
        "query_start_day": start_day,
        "query_end_day": min(end_day, target_day),
        "key": month_key_for_day(start_day),
        "label": month_label_for_day(start_day),
    }


def period_context_for_month(target_day):
    return period_context_for_calendar_month(target_day, target_day)


def period_context_for_range(range_info, target_day):
    query_end_day = min(range_info["end_day"], target_day)

    return {
        "mode": "range",
        "start_day": range_info["start_day"],
        "end_day": range_info["end_day"],
        "query_start_day": range_info["start_day"],
        "query_end_day": query_end_day,
        "key": (
            f"{range_info['start_day'].isoformat()}:"
            f"{range_info['end_day'].isoformat()}"
        ),
        "label": range_label_for_days(
            range_info["start_day"],
            range_info["end_day"],
        ),
    }


def month_key_for_day(day):
    return f"{day.year}-{day.month:02d}"


def parse_period_identity(value, reference_day=None):
    range_info = parse_date_range(value, reference_day)

    if range_info:
        return {
            "mode": "range",
            "start_day": range_info["start_day"],
            "end_day": range_info["end_day"],
            "key": (
                f"{range_info['start_day'].isoformat()}:"
                f"{range_info['end_day'].isoformat()}"
            ),
            "label": range_label_for_days(
                range_info["start_day"],
                range_info["end_day"],
            ),
        }

    period_month = parse_period_month(value)

    if not period_month:
        return None

    return {
        "mode": "month",
        "start_day": period_month,
        "key": month_key_for_day(period_month),
        "label": month_key_for_day(period_month),
    }


def parse_period_month(value):
    text = str(value or "").strip()

    if not text:
        return None

    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    ).lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    normalized = re.sub(r"^(periodo|perido) seleccionado", "", normalized).strip()

    month_lookup = {
        normalize_text(name): month
        for month, name in MONTH_NAMES_ES.items()
    }
    month_lookup.update({
        "ene": 1,
        "feb": 2,
        "mar": 3,
        "abr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "ago": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dic": 12,
    })

    year = None
    month = None

    for token in normalized.split():
        if re.fullmatch(r"20\d{2}", token):
            year = int(token)
            continue

        token_month = month_lookup.get(normalize_text(token))
        if token_month:
            month = token_month

    if not year or not month:
        return None

    return date(year, month, 1)


def parse_period_month_number(value):
    if not is_period_cell(value):
        return None

    normalized = fold_text(value)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    normalized = re.sub(
        r"^(periodo|perido) seleccionado",
        "",
        normalized,
    ).strip()

    for token in normalized.split():
        month_number = MONTH_NUMBERS_BY_NAME.get(token)
        if month_number:
            return month_number

    return None


def is_period_cell(value):
    normalized = normalize_text(value)
    return normalized.startswith("periodoseleccionado") or normalized.startswith(
        "peridoseleccionado"
    )


def is_month_current_cell(value):
    return normalize_text(value) == "mesactual"


def is_previous_months_cell(value):
    normalized = normalize_text(value)
    return normalized.startswith("mesesanteriores")


def normalized_header_lookup(row):
    return {
        normalize_text(value): index
        for index, value in enumerate(row, start=1)
        if str(value).strip()
    }


def find_header_columns(header_row):
    lookup = normalized_header_lookup(header_row)
    columns = {}

    for key, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            normalized_alias = normalize_text(alias)
            if normalized_alias in lookup:
                columns[key] = lookup[normalized_alias]
                break

    missing = [key for key in HEADER_ALIASES if key not in columns]

    if missing:
        raise ValueError(
            "Faltan cabeceras requeridas en la tabla SEM: " + ", ".join(missing)
        )

    return columns


def find_obsolete_header_columns(header_row):
    lookup = normalized_header_lookup(header_row)
    columns = {}

    for key, aliases in OBSOLETE_HEADER_ALIASES.items():
        for alias in aliases:
            normalized_alias = normalize_text(alias)
            if normalized_alias in lookup:
                columns[key] = lookup[normalized_alias]
                break

    return columns


def is_legacy_conversion_value_header(value):
    legacy_aliases = {
        normalize_text(alias)
        for key in ("conversion_value", "all_conversions_value")
        for alias in OBSOLETE_HEADER_ALIASES[key]
    }
    return normalize_text(value) in legacy_aliases


def looks_like_campaign_header(row):
    normalized_values = {normalize_text(value) for value in row}
    return (
        any(
            normalize_text(alias) in normalized_values
            for alias in HEADER_ALIASES["campaign_name"]
        )
        and any(
            normalize_text(alias) in normalized_values
            for alias in HEADER_ALIASES["campaign_status"]
        )
        and any(
            normalize_text(alias) in normalized_values
            for alias in HEADER_ALIASES["cost"]
        )
    )


def build_block_from_header(
    values,
    period_row,
    period_col,
    header_row_index,
    header_values=None,
    header_template_row=None,
    historical_marker_row=None,
):
    header_row = header_values or values[header_row_index - 1]
    columns = find_header_columns(header_row)
    required_column_indexes = set(columns.values())
    obsolete_columns = {
        key: col_index
        for key, col_index in find_obsolete_header_columns(header_row).items()
        if col_index not in required_column_indexes
    }

    return {
        "values": values,
        "period_row": period_row,
        "period_col": period_col,
        "header_row": header_row_index,
        "header_values": header_row,
        "header_template_row": header_template_row or header_row_index,
        "historical_marker_row": historical_marker_row,
        "columns": columns,
        "obsolete_columns": obsolete_columns,
    }


def find_first_campaign_header(values, start_row=1):
    for row_index in range(start_row, len(values) + 1):
        row = values[row_index - 1]
        if looks_like_campaign_header(row):
            return row_index, row

    raise ValueError("No se encontro ninguna cabecera de campanas en la pestana.")


def find_live_block(worksheet, target_day):
    values = get_worksheet_values(worksheet)
    mes_actual_rows = []

    for row_index, row in enumerate(values, start=1):
        if any(is_month_current_cell(value) for value in row):
            mes_actual_rows.append(row_index)

    if mes_actual_rows:
        mes_actual_row = min(mes_actual_rows)
        search_start = mes_actual_row + 1
        try:
            historical_marker_row = find_historical_marker_row(values)
        except ValueError:
            historical_marker_row = min(len(values), search_start + 30)
        search_end = min(len(values), historical_marker_row - 1)

        period_row = None
        period_col = None

        for row_index in range(search_start, search_end + 1):
            row = values[row_index - 1]

            for col_index, value in enumerate(row, start=1):
                if is_period_cell(value):
                    period_row = row_index
                    period_col = col_index
                    break

            if period_row:
                break

        for row_index in range(search_start, search_end + 1):
            row = values[row_index - 1]
            if looks_like_campaign_header(row):
                columns = find_header_columns(row)
                period_row = period_row or row_index - 1
                period_col = period_col or min(columns.values())
                return build_block_from_header(
                    values,
                    period_row,
                    period_col,
                    row_index,
                    historical_marker_row=historical_marker_row,
                )

        template_header_row, template_header = find_first_campaign_header(
            values,
            historical_marker_row + 1,
        )
        template_columns = find_header_columns(template_header)
        period_row = period_row or search_start
        period_col = period_col or min(template_columns.values())
        header_row_index = period_row + 1

        return build_block_from_header(
            values,
            period_row,
            period_col,
            header_row_index,
            header_values=template_header,
            header_template_row=template_header_row,
            historical_marker_row=historical_marker_row,
        )

    wanted_period = normalize_text(month_label_for_day(target_day))

    for row_index, row in enumerate(values, start=1):
        for col_index, value in enumerate(row, start=1):
            if is_period_cell(value) and normalize_text(value) == wanted_period:
                return build_block_from_header(
                    values,
                    row_index,
                    col_index,
                    row_index + 1,
                )

    for row_index, row in enumerate(values, start=1):
        if looks_like_campaign_header(row):
            columns = find_header_columns(row)
            period_row = row_index - 1
            period_col = min(columns.values())
            return build_block_from_header(
                values,
                period_row,
                period_col,
                row_index,
            )

    raise ValueError(
        "No se encontro el bloque vivo: falta 'Periodo seleccionado' "
        "y no se pudo localizar la cabecera de campanas."
    )


def find_total_row(values, header_row, start_col, end_col, stop_row=None):
    last_row = min(stop_row or len(values), len(values))

    for row_index in range(header_row + 1, last_row + 1):
        row = values[row_index - 1]
        visible = row[start_col - 1:end_col]

        if any(is_total_cost_cell(value) for value in visible):
            return row_index

        if row_index > header_row + 100:
            break

    raise ValueError("No se encontro la fila 'Total coste campanas'.")


def find_next_period_row(values, start_row):
    """Devuelve el siguiente periodo posterior a ``start_row``, si existe."""
    for row_index in range(start_row + 1, len(values) + 1):
        if any(is_period_cell(value) for value in values[row_index - 1]):
            return row_index

    return None


def normalize_live_total_label(worksheet, block, mutation_batch=None):
    """Corrige la etiqueta del total sin alterar los datos del bloque vivo."""
    columns = block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    total_row = find_total_row(
        block["values"],
        block["header_row"],
        start_col,
        end_col,
        stop_row=block.get("historical_marker_row"),
    )
    label_col = columns["ctr"]
    label_cell = gspread.utils.rowcol_to_a1(total_row, label_col)
    row_values = block["values"][total_row - 1]
    current_label = (
        row_values[label_col - 1]
        if len(row_values) >= label_col
        else ""
    )
    expected_label = "Total coste campa\u00f1as:"

    if current_label == expected_label:
        return {"cell": label_cell, "changed": False}

    queue_values_update(
        worksheet,
        label_cell,
        [[expected_label]],
        mutation_batch,
    )
    return {"cell": label_cell, "changed": True}


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
    """Usa un formato entero cuando no hay decimales que mostrar."""
    number = parse_sheet_number(value)
    visible_number = round(number, 2)
    if abs(visible_number - round(visible_number)) < 1e-9:
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
        value,
        decimal_pattern=OPTIONAL_PERCENT_FORMAT,
        integer_pattern=INTEGER_PERCENT_FORMAT,
    )


def campaign_sort_key(campaign):
    is_enabled = normalize_text(campaign.get("campaign_status")) == "enabled"
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


def is_retryable_google_ads_error(error):
    error_text = str(error).lower()
    return any(
        marker in error_text
        for marker in (
            "429",
            "500",
            "502",
            "503",
            "504",
            "unavailable",
            "backend unavailable",
            "deadline exceeded",
            "internal error",
            "resource has been exhausted",
            "resource_exhausted",
            "too many requests",
        )
    )


def get_google_ads_retry_delay(error, attempt):
    retry_match = re.search(r"retry in (\d+) seconds", str(error).lower())
    if retry_match:
        return int(retry_match.group(1))
    return RETRY_DELAY_SECONDS * attempt


def print_google_ads_error(error, context):
    print(f"Error de Google Ads {context}.")
    if isinstance(error, GoogleAdsException):
        print(f"Request ID: {error.request_id}")
        for failure_error in error.failure.errors:
            print(f"  {failure_error.message}")
    else:
        print(f"  {type(error).__name__}: {error}")


def run_google_ads_read_with_retries(operation, context):
    for attempt in range(1, MAX_GOOGLE_ADS_RETRIES + 1):
        try:
            return operation()
        except Exception as error:
            can_retry = (
                is_retryable_google_ads_error(error)
                and attempt < MAX_GOOGLE_ADS_RETRIES
            )
            if not can_retry:
                print_google_ads_error(error, context)
                raise

            retry_delay = get_google_ads_retry_delay(error, attempt)
            print(
                f"Error temporal de Google Ads {context}. "
                f"Reintento {attempt + 1}/{MAX_GOOGLE_ADS_RETRIES} "
                f"en {retry_delay} segundos..."
            )
            time.sleep(retry_delay)

    raise RuntimeError("La operacion de Google Ads termino sin resultado.")


def fetch_customer_status(google_ads_client, customer_id):
    google_ads_service = google_ads_client.get_service("GoogleAdsService")
    query = """
        SELECT
          customer.status
        FROM customer
        LIMIT 1
    """

    def read_status():
        response = google_ads_service.search_stream(
            customer_id=normalize_customer_id(customer_id),
            query=query,
        )
        for batch in response:
            for row in batch.results:
                return row.customer.status.name.lower()
        return "unknown"

    return run_google_ads_read_with_retries(
        read_status,
        "consultando el estado de la cuenta",
    )


def customer_status_display_label(customer_status):
    normalized = normalize_text(customer_status)
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


def fetch_account_operational_status(
    google_ads_client,
    customer_id,
    customer_status=None,
    operational_day=None,
):
    customer_status = (
        customer_status
        or fetch_customer_status(google_ads_client, customer_id)
    )
    operational_day = operational_day or today_in_spain()
    if normalize_text(customer_status) != "enabled":
        return customer_status_display_label(customer_status)

    google_ads_service = google_ads_client.get_service("GoogleAdsService")
    query = """
        SELECT
          campaign.id,
          campaign.status,
          campaign.primary_status,
          campaign.end_date_time
        FROM campaign
    """

    def read_campaign_states():
        campaigns = []
        response = google_ads_service.search_stream(
            customer_id=normalize_customer_id(customer_id),
            query=query,
        )
        for batch in response:
            for row in batch.results:
                campaigns.append({
                    "campaign_id": str(row.campaign.id),
                    "status": row.campaign.status.name.lower(),
                    "primary_status": (
                        row.campaign.primary_status.name.lower()
                    ),
                    "end_date_time": str(
                        row.campaign.end_date_time or ""
                    ),
                })
        return campaigns

    campaigns = run_google_ads_read_with_retries(
        read_campaign_states,
        "consultando el estado operativo de las campanas",
    )
    return classify_account_operational_status(
        customer_status,
        campaigns,
        operational_day,
    )


def build_campaign_rows_from_daily_aggregates(campaigns):
    rows = []

    for campaign_id, campaign in campaigns.items():
        clicks = campaign["clicks"]
        impressions = campaign["impressions"]
        cost = campaign["cost_micros"] / 1_000_000
        conversions = campaign["conversions"]
        average_cpc = cost / clicks if clicks else 0
        ctr = (clicks / impressions * 100) if impressions else 0
        cost_per_conversion = cost / conversions if conversions else ""
        top_share = (
            campaign["top_share_weighted"] / campaign["top_share_weight"]
            if campaign["top_share_weight"]
            else None
        )
        absolute_top_share = (
            campaign["absolute_top_share_weighted"]
            / campaign["absolute_top_share_weight"]
            if campaign["absolute_top_share_weight"]
            else None
        )
        has_activity = any([
            clicks,
            impressions,
            cost,
            conversions,
        ])

        if not has_activity:
            continue

        rows.append({
            "campaign_id": campaign_id,
            "campaign_name": campaign["campaign_name"],
            "campaign_status": campaign["campaign_status"],
            "clicks": int(clicks),
            "ctr": format_number(ctr, 2),
            "average_cpc": format_number(average_cpc, 2),
            "cost": round(cost, 6),
            "conversions": format_number(conversions, 2),
            "cost_per_conversion": (
                format_number(cost_per_conversion, 2)
                if cost_per_conversion != ""
                else ""
            ),
            "impressions": int(impressions),
            "top_impression_share": format_share(top_share),
            "absolute_top_impression_share": format_share(
                absolute_top_share
            ),
            "daily_budget": format_number(
                campaign["daily_budget_micros"] / 1_000_000,
                2,
            ),
            "raw_cost": cost,
        })

    return sort_campaign_rows(rows)


def apply_exact_impression_shares(period_rows, exact_shares):
    for period_key, rows in period_rows.items():
        shares_by_campaign = exact_shares.get(period_key, {})
        for row in rows:
            shares = shares_by_campaign.get(row["campaign_id"])
            if shares is None:
                continue
            row["top_impression_share"] = format_share(shares["top"])
            row["absolute_top_impression_share"] = format_share(
                shares["absolute_top"]
            )


def fetch_exact_impression_shares(
    google_ads_service,
    customer_id,
    period_ranges,
):
    exact_shares = {}

    for period_key, (start_day, end_day) in period_ranges.items():
        query = f"""
            SELECT
              campaign.id,
              metrics.search_top_impression_share,
              metrics.search_absolute_top_impression_share
            FROM campaign
            WHERE
              segments.date BETWEEN '{start_day.isoformat()}' AND '{end_day.isoformat()}'
        """

        def read_shares():
            shares = {}
            response = google_ads_service.search_stream(
                customer_id=normalize_customer_id(customer_id),
                query=query,
            )
            for batch in response:
                for row in batch.results:
                    shares[str(row.campaign.id)] = {
                        "top": float(
                            row.metrics.search_top_impression_share or 0
                        ),
                        "absolute_top": float(
                            row.metrics.search_absolute_top_impression_share
                            or 0
                        ),
                    }
            return shares

        exact_shares[period_key] = run_google_ads_read_with_retries(
            read_shares,
            (
                "consultando porcentajes de impresion exactos del periodo "
                f"{start_day.isoformat()} a {end_day.isoformat()}"
            ),
        )

    return exact_shares


def fetch_campaign_periods(
    google_ads_client,
    customer_id,
    period_contexts,
    operational_day=None,
):
    if not period_contexts:
        technical_status = fetch_customer_status(
            google_ads_client,
            customer_id,
        )
        return {
            "account_status": fetch_account_operational_status(
                google_ads_client,
                customer_id,
                technical_status,
                operational_day,
            ),
            "technical_account_status": technical_status,
            "period_rows": {},
        }

    period_ranges = {
        key: (
            context["query_start_day"],
            context["query_end_day"],
        )
        for key, context in period_contexts.items()
    }
    query_start_day = min(start for start, _ in period_ranges.values())
    query_end_day = max(end for _, end in period_ranges.values())
    google_ads_service = google_ads_client.get_service("GoogleAdsService")
    query = f"""
        SELECT
          customer.status,
          segments.date,
          campaign.id,
          campaign.name,
          campaign.status,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions,
          metrics.impressions,
          metrics.search_top_impression_share,
          metrics.search_absolute_top_impression_share,
          campaign_budget.amount_micros
        FROM campaign
        WHERE
          segments.date BETWEEN '{query_start_day.isoformat()}' AND '{query_end_day.isoformat()}'
        ORDER BY
          segments.date,
          campaign.id
    """

    def read_campaigns():
        account_status = None
        aggregates = {key: {} for key in period_contexts}
        response = google_ads_service.search_stream(
            customer_id=normalize_customer_id(customer_id),
            query=query,
        )

        for batch in response:
            for row in batch.results:
                account_status = row.customer.status.name.lower()
                row_day = date.fromisoformat(str(row.segments.date))
                period_key = next(
                    (
                        key
                        for key, (start_day, end_day) in period_ranges.items()
                        if start_day <= row_day <= end_day
                    ),
                    None,
                )
                if period_key is None:
                    continue

                campaign_id = str(row.campaign.id)
                campaigns = aggregates[period_key]
                campaign = campaigns.setdefault(campaign_id, {
                    "campaign_name": row.campaign.name,
                    "campaign_status": row.campaign.status.name.lower(),
                    "latest_day": row_day,
                    "clicks": 0,
                    "cost_micros": 0,
                    "conversions": 0.0,
                    "impressions": 0,
                    "daily_budget_micros": 0,
                    "top_share_weighted": 0.0,
                    "top_share_weight": 0,
                    "absolute_top_share_weighted": 0.0,
                    "absolute_top_share_weight": 0,
                })

                if row_day >= campaign["latest_day"]:
                    campaign["latest_day"] = row_day
                    campaign["campaign_name"] = row.campaign.name
                    campaign["campaign_status"] = (
                        row.campaign.status.name.lower()
                    )
                    campaign["daily_budget_micros"] = int(
                        row.campaign_budget.amount_micros or 0
                    )

                impressions = int(row.metrics.impressions or 0)
                campaign["clicks"] += int(row.metrics.clicks or 0)
                campaign["cost_micros"] += int(
                    row.metrics.cost_micros or 0
                )
                campaign["conversions"] += float(
                    row.metrics.conversions or 0
                )
                campaign["impressions"] += impressions

                top_share = float(
                    row.metrics.search_top_impression_share or 0
                )
                if top_share and impressions:
                    campaign["top_share_weighted"] += (
                        top_share * impressions
                    )
                    campaign["top_share_weight"] += impressions

                absolute_top_share = float(
                    row.metrics.search_absolute_top_impression_share or 0
                )
                if absolute_top_share and impressions:
                    campaign["absolute_top_share_weighted"] += (
                        absolute_top_share * impressions
                    )
                    campaign["absolute_top_share_weight"] += impressions

        return {
            "account_status": account_status,
            "period_rows": {
                key: build_campaign_rows_from_daily_aggregates(campaigns)
                for key, campaigns in aggregates.items()
            },
        }

    result = run_google_ads_read_with_retries(
        read_campaigns,
        (
            "consultando campanas agrupadas del periodo "
            f"{query_start_day.isoformat()} a {query_end_day.isoformat()}"
        ),
    )
    if not result["account_status"]:
        result["account_status"] = fetch_customer_status(
            google_ads_client,
            customer_id,
        )
    technical_status = result["account_status"]
    result["technical_account_status"] = technical_status
    result["account_status"] = fetch_account_operational_status(
        google_ads_client,
        customer_id,
        technical_status,
        operational_day,
    )
    exact_shares = fetch_exact_impression_shares(
        google_ads_service,
        customer_id,
        period_ranges,
    )
    apply_exact_impression_shares(result["period_rows"], exact_shares)
    return result


def fetch_campaign_rows(google_ads_client, customer_id, start_day, end_day):
    context = {
        "query_start_day": start_day,
        "query_end_day": end_day,
    }
    return fetch_campaign_periods(
        google_ads_client,
        customer_id,
        {"single": context},
    )["period_rows"]["single"]


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
            row[col_index - start_col] = campaign[key]

        matrix.append(row)

    return matrix


def build_standard_header_slice(columns, start_col, end_col):
    header = ["" for _ in range(end_col - start_col + 1)]

    for key, label in STANDARD_HEADER_BY_KEY.items():
        if key not in columns:
            continue

        header[columns[key] - start_col] = label

    return header


def clear_obsolete_live_columns(
    worksheet,
    block,
    total_row,
    mutation_batch=None,
):
    obsolete_columns = sorted(set(block.get("obsolete_columns", {}).values()))

    if not obsolete_columns:
        return []

    clear_ranges = [
        (
            f"{gspread.utils.rowcol_to_a1(block['header_row'], col_index)}:"
            f"{gspread.utils.rowcol_to_a1(total_row, col_index)}"
        )
        for col_index in obsolete_columns
    ]
    requests = []
    for col_index in obsolete_columns:
        queue_values_update(
            worksheet,
            (
                f"{gspread.utils.rowcol_to_a1(block['header_row'], col_index)}:"
                f"{gspread.utils.rowcol_to_a1(total_row, col_index)}"
            ),
            [[""] for _ in range(total_row - block["header_row"] + 1)],
            mutation_batch,
        )
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": block["header_row"] - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": col_index - 1,
                    "endColumnIndex": col_index,
                },
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat",
            }
        })

    queue_format_requests(worksheet, requests, mutation_batch)
    return clear_ranges


def standardize_live_block_columns(worksheet, target_day=None):
    block = find_live_block(worksheet, target_day or today_in_spain())
    columns = block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    marker_row = block.get("historical_marker_row")

    try:
        total_row = find_total_row(
            block["values"],
            block["header_row"],
            start_col,
            end_col,
            marker_row - 1 if marker_row else None,
        )
    except ValueError:
        if not marker_row:
            raise
        total_row = max(block["header_row"] + 1, marker_row - 2)

    header_slice = build_standard_header_slice(columns, start_col, end_col)
    header_range = (
        f"{gspread.utils.rowcol_to_a1(block['header_row'], start_col)}:"
        f"{gspread.utils.rowcol_to_a1(block['header_row'], end_col)}"
    )
    worksheet.update(
        range_name=header_range,
        values=[header_slice],
        value_input_option="USER_ENTERED",
    )
    cleared_ranges = clear_obsolete_live_columns(worksheet, block, total_row)

    return {
        "header_range": header_range,
        "cleared_ranges": cleared_ranges,
        "standard_columns": len(columns),
    }


def count_campaign_rows(values, data_start_row, total_row, campaign_col):
    count = 0

    for row_index in range(data_start_row, total_row):
        row = values[row_index - 1] if len(values) >= row_index else []
        campaign_name = row[campaign_col - 1] if len(row) >= campaign_col else ""

        if not str(campaign_name).strip():
            break

        count += 1

    return count


def find_historical_marker_row(values):
    for row_index, row in enumerate(values, start=1):
        if any(is_previous_months_cell(value) for value in row):
            return row_index

    raise ValueError("No se encontro la fila 'MESES ANTERIORES'.")


def ensure_historical_marker_row(worksheet, live_block):
    values = get_worksheet_values(worksheet)
    try:
        return find_historical_marker_row(values)
    except ValueError:
        pass

    columns = live_block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    total_row = find_total_row(
        values,
        live_block["header_row"],
        start_col,
        end_col,
    )
    marker_row = total_row + 2
    existing_row = values[marker_row - 1] if len(values) >= marker_row else []
    occupied = any(
        str(value).strip()
        for value in existing_row[start_col - 1:end_col]
    )
    if occupied:
        worksheet.insert_rows(
            [["" for _ in range(worksheet.col_count)]],
            row=marker_row,
            value_input_option="USER_ENTERED",
            inherit_from_before=True,
        )
        invalidate_worksheet_values_cache(worksheet)

    marker_cell = gspread.utils.rowcol_to_a1(marker_row, start_col)
    worksheet.update(
        range_name=marker_cell,
        values=[["MESES ANTERIORES (Ordenados desde el primer mes hasta el \u00faltimo)"]],
        value_input_option="USER_ENTERED",
    )
    worksheet.spreadsheet.batch_update({
        "requests": [
            {
                "mergeCells": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": marker_row - 1,
                        "endRowIndex": marker_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "mergeType": "MERGE_ALL",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": marker_row - 1,
                        "endRowIndex": marker_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {
                                "red": 0.4,
                                "green": 0.4,
                                "blue": 0.4,
                            },
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                            "textFormat": {
                                "bold": True,
                                "foregroundColor": {
                                    "red": 1,
                                    "green": 1,
                                    "blue": 1,
                                },
                            },
                        }
                    },
                    "fields": "userEnteredFormat",
                }
            },
        ]
    })
    invalidate_worksheet_values_cache(worksheet)
    get_worksheet_values(worksheet, refresh=True)
    return marker_row


def iter_period_cells(values, start_row=1, reference_day=None):
    for row_index, row in enumerate(values, start=1):
        if row_index < start_row:
            continue

        for col_index, value in enumerate(row, start=1):
            if is_period_cell(value):
                period_identity = parse_period_identity(value, reference_day)
                if period_identity:
                    yield row_index, col_index, value, period_identity


def historical_period_start_for_order(value, target_start_day):
    range_match = DATE_RANGE_PATTERN.search(str(value or ""))
    range_has_year = bool(
        range_match
        and (
            range_match.group("start_year")
            or range_match.group("end_year")
        )
    )

    if range_match and not range_has_year:
        start_month_day = (
            int(range_match.group("start_month")),
            int(range_match.group("start_day")),
        )
        target_month_day = (
            target_start_day.month,
            target_start_day.day,
        )
        # Un rango historico sin ano se interpreta como la ocurrencia mas
        # reciente que no empiece despues del periodo que se va a archivar.
        # Asi 01/12 - 19/12 antes de julio de 2026 es diciembre de 2025.
        default_start_year = target_start_day.year
        if start_month_day > target_month_day:
            default_start_year -= 1
        range_info = parse_date_range(
            value,
            target_start_day,
            default_start_year=default_start_year,
        )
        if range_info:
            return range_info["start_day"]

    identity = parse_period_identity(value, target_start_day)
    if identity:
        return identity["start_day"]

    month_number = parse_period_month_number(value)
    if not month_number:
        return None

    # Los historicos heredados suelen omitir el ano. Para decidir si el nuevo
    # periodo puede anexarse, se interpreta el mes como la ocurrencia mas
    # reciente que no sea posterior al periodo objetivo.
    year = (
        target_start_day.year
        if month_number <= target_start_day.month
        else target_start_day.year - 1
    )
    return date(year, month_number, 1)


def find_historical_block_end_row(
    values,
    period_row,
    start_col,
    end_col,
):
    next_period_row = None
    for row_index in range(period_row + 1, len(values) + 1):
        if any(is_period_cell(value) for value in values[row_index - 1]):
            next_period_row = row_index
            break

    stop_row = next_period_row - 1 if next_period_row else len(values)
    header_row = period_row + 1

    if header_row <= len(values) and looks_like_campaign_header(
        values[header_row - 1]
    ):
        try:
            return find_total_row(
                values,
                header_row,
                start_col,
                end_col,
                stop_row,
            )
        except ValueError:
            pass

        header_lookup = normalized_header_lookup(values[header_row - 1])
        campaign_col = next(
            header_lookup[normalize_text(alias)]
            for alias in HEADER_ALIASES["campaign_name"]
            if normalize_text(alias) in header_lookup
        )
        last_campaign_row = header_row
        for row_index in range(header_row + 1, stop_row + 1):
            row = values[row_index - 1]
            campaign_name = (
                row[campaign_col - 1]
                if len(row) >= campaign_col
                else ""
            )
            if not str(campaign_name).strip():
                break
            last_campaign_row = row_index

        return last_campaign_row

    # Compatibilidad defensiva con cabeceras muy antiguas: el bloque termina
    # tras dos filas completamente vacias consecutivas. Asi se ignoran restos
    # aislados que puedan existir mucho mas abajo en otras columnas.
    last_content_row = period_row
    blank_rows = 0
    for row_index in range(period_row + 1, stop_row + 1):
        row = values[row_index - 1]
        visible = row[start_col - 1:end_col]
        if any(str(value).strip() for value in visible):
            last_content_row = row_index
            blank_rows = 0
            continue

        blank_rows += 1
        if blank_rows >= 2:
            break

    return last_content_row


def clean_orphan_historical_metric_tails(
    worksheet,
    live_block,
    mutation_batch=None,
):
    """Limpia restos K:M que no pertenecen a ningun bloque historico."""
    values = get_worksheet_values(worksheet)
    columns = live_block["columns"]
    tail_keys = (
        "top_impression_share",
        "absolute_top_impression_share",
        "daily_budget",
    )
    if not all(key in columns for key in tail_keys):
        return []

    marker_row = find_historical_marker_row(values)
    start_col = min(columns.values())
    end_col = max(columns.values())
    tail_start_col = min(columns[key] for key in tail_keys)
    covered_rows = set()

    period_rows = [
        row_index
        for row_index in range(marker_row + 1, len(values) + 1)
        if any(is_period_cell(value) for value in values[row_index - 1])
    ]
    for period_row in period_rows:
        block_end_row = find_historical_block_end_row(
            values,
            period_row,
            start_col,
            end_col,
        )
        covered_rows.update(range(period_row, block_end_row + 1))

    orphan_rows = []
    for row_index in range(marker_row + 1, len(values) + 1):
        if row_index in covered_rows:
            continue
        row = values[row_index - 1]
        leading_values = row[start_col - 1:tail_start_col - 1]
        tail_values = row[tail_start_col - 1:end_col]
        if (
            not any(str(value).strip() for value in leading_values)
            and any(str(value).strip() for value in tail_values)
        ):
            orphan_rows.append(row_index)

    ranges = []
    for row_index in orphan_rows:
        if ranges and row_index == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], row_index)
        else:
            ranges.append((row_index, row_index))

    cleared_ranges = []
    width = end_col - tail_start_col + 1
    for first_row, last_row in ranges:
        range_name = (
            f"{gspread.utils.rowcol_to_a1(first_row, tail_start_col)}:"
            f"{gspread.utils.rowcol_to_a1(last_row, end_col)}"
        )
        queue_values_update(
            worksheet,
            range_name,
            [["" for _ in range(width)] for _ in range(last_row - first_row + 1)],
            mutation_batch,
        )
        queue_format_requests(
            worksheet,
            [{
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": first_row - 1,
                        "endRowIndex": last_row,
                        "startColumnIndex": tail_start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "cell": {"note": "", "userEnteredFormat": {}},
                    "fields": "note,userEnteredFormat",
                }
            }],
            mutation_batch,
        )
        cleared_ranges.append(range_name)

    return cleared_ranges


def resolve_historical_side_band_end(
    historical_block,
    target_end_row,
    inserted_rows=0,
    deleted_rows=0,
):
    """Calcula el final global aunque el periodo actualizado no sea el último."""
    values = historical_block["values"]
    columns = historical_block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    marker_row = find_historical_marker_row(values)
    period_rows = [
        row_index
        for row_index in range(marker_row + 1, len(values) + 1)
        if any(is_period_cell(value) for value in values[row_index - 1])
    ]

    if not period_rows or period_rows[-1] == historical_block["period_row"]:
        return target_end_row

    last_end_row = find_historical_block_end_row(
        values,
        period_rows[-1],
        start_col,
        end_col,
    )
    if period_rows[-1] > historical_block["period_row"]:
        last_end_row += inserted_rows - deleted_rows

    return max(target_end_row, last_end_row)


def find_historical_append_row(
    values,
    marker_row,
    start_col,
    end_col,
    period_context,
):
    period_cells = []

    for row_index in range(marker_row + 1, len(values) + 1):
        row = values[row_index - 1]
        for value in row[start_col - 1:end_col]:
            if is_period_cell(value):
                period_cells.append((row_index, value))
                break

    if not period_cells:
        return marker_row + 2

    target_start_day = period_context["start_day"]
    later_periods = []
    for row_index, value in period_cells:
        existing_start = historical_period_start_for_order(
            value,
            target_start_day,
        )
        if existing_start and existing_start > target_start_day:
            later_periods.append((row_index, str(value).strip()))

    if later_periods:
        row_index, value = later_periods[0]
        raise ValueError(
            "No se puede anadir el periodo historico al final porque ya existe "
            f"un periodo posterior en la fila {row_index}: {value}."
        )

    last_period_row = period_cells[-1][0]
    last_block_end_row = find_historical_block_end_row(
        values,
        last_period_row,
        start_col,
        end_col,
    )
    return last_block_end_row + 2


def historical_period_exists(values, marker_row, period_context):
    wanted_key = period_context["key"]

    for _, _, _, historical_period in iter_period_cells(
        values,
        marker_row + 1,
        period_context["start_day"],
    ):
        if historical_period["key"] == wanted_key:
            return True

    return False


def copy_range_to_rows(worksheet, source_start_row, source_end_row, start_col,
                       end_col, destination_start_row):
    height = source_end_row - source_start_row + 1
    destination_end_row = destination_start_row + height - 1

    worksheet.spreadsheet.batch_update({
        "requests": [
            {
                "insertDimension": {
                    "range": {
                        "sheetId": worksheet.id,
                        "dimension": "ROWS",
                        "startIndex": destination_start_row - 1,
                        "endIndex": destination_end_row,
                    },
                    "inheritFromBefore": True,
                }
            },
            {
                "copyPaste": {
                    "source": {
                        "sheetId": worksheet.id,
                        "startRowIndex": source_start_row - 1,
                        "endRowIndex": source_end_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "destination": {
                        "sheetId": worksheet.id,
                        "startRowIndex": destination_start_row - 1,
                        "endRowIndex": destination_end_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "pasteType": "PASTE_NORMAL",
                    "pasteOrientation": "NORMAL",
                }
            },
        ]
    })
    invalidate_worksheet_values_cache(worksheet)

    return destination_end_row


def historical_copy_end_row(block, total_row, campaign_count):
    """Una campana se archiva sin la fila de total redundante."""
    if campaign_count == 1:
        return block["header_row"] + 1

    return total_row


def archive_live_block_if_needed(worksheet, block, period_context):
    values = block["values"]
    columns = block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    data_start_row = block["header_row"] + 1
    marker_row = block.get("historical_marker_row") or find_historical_marker_row(values)

    try:
        total_row = find_total_row(
            values,
            block["header_row"],
            start_col,
            end_col,
            marker_row - 1,
        )
    except ValueError:
        total_row = max(block["header_row"] + 1, marker_row - 2)

    period_value = ""

    if len(values) >= block["period_row"]:
        period_row_values = values[block["period_row"] - 1]
        if len(period_row_values) >= block["period_col"]:
            period_value = period_row_values[block["period_col"] - 1]

    live_period = parse_period_identity(
        period_value,
        period_context["start_day"],
    )
    campaign_count = count_campaign_rows(
        values,
        data_start_row,
        total_row,
        columns["campaign_name"],
    )

    if not live_period:
        if campaign_count:
            raise SystemExit(
                "El bloque vivo tiene campanas pero no se pudo leer su periodo. "
                "No archivo ni sobrescribo para no perder historico."
            )

        return {
            "archived": False,
            "reason": "bloque vivo sin periodo y sin campanas",
        }

    if live_period["key"] == period_context["key"]:
        return {
            "archived": False,
            "reason": f"el bloque vivo ya es {period_context['label']}",
        }

    reference_day = period_context["query_end_day"]
    same_active_range = (
        live_period["mode"] == "range"
        and period_context["mode"] == "range"
        and live_period["start_day"] <= reference_day <= live_period["end_day"]
        and period_context["start_day"] <= reference_day <= period_context["end_day"]
    )

    if same_active_range:
        return {
            "archived": False,
            "reason": (
                "el rango vigente se ha redefinido y ambos rangos contienen "
                f"{reference_day.isoformat()}"
            ),
        }

    if live_period["start_day"] > period_context["start_day"]:
        raise SystemExit(
            "El periodo del bloque vivo es posterior al periodo actual "
            f"({live_period['label']} > {period_context['label']}). "
            "No continuo para evitar sobrescribir datos."
        )

    if historical_period_exists(values, marker_row, live_period):
        return {
            "archived": False,
            "reason": f"{live_period['label']} ya existe en historico",
        }

    destination_start_row = find_historical_append_row(
        values,
        marker_row,
        start_col,
        end_col,
        live_period,
    )
    source_start_row = block["period_row"]
    # En historico, una sola campana ya representa el total. Se copia solo
    # periodo, cabecera y campana para no repetir la misma cifra en otra fila.
    source_end_row = historical_copy_end_row(
        block,
        total_row,
        campaign_count,
    )
    destination_end_row = copy_range_to_rows(
        worksheet,
        source_start_row,
        source_end_row,
        start_col,
        end_col,
        destination_start_row,
    )
    destination_header_row = destination_start_row + 1
    destination_data_start_row = destination_header_row + 1
    if campaign_count != 1:
        archived_campaign_rows = extract_campaign_rows_from_block(
            block["values"],
            columns,
            data_start_row,
            data_start_row + campaign_count - 1,
        )
        destination_total_row = destination_end_row
        update_total_formulas(
            worksheet,
            columns,
            start_col,
            end_col,
            destination_data_start_row,
            campaign_count,
            destination_total_row,
            conversion_total=sum(
                parse_sheet_number(
                    block["values"][block["header_row"] + offset][
                        columns["conversions"] - 1
                    ]
                    if (
                        len(block["values"]) > block["header_row"] + offset
                        and len(
                            block["values"][block["header_row"] + offset]
                        ) >= columns["conversions"]
                    )
                    else 0
                )
                for offset in range(campaign_count)
            ),
            metric_totals=summarize_campaign_metrics(
                archived_campaign_rows
            ),
        )
        apply_live_block_borders(
            worksheet,
            columns,
            start_col,
            end_col,
            destination_header_row,
            destination_data_start_row,
            campaign_count,
            destination_total_row,
        )

    apply_historical_side_band(
        worksheet,
        marker_row,
        destination_end_row,
    )

    return {
        "archived": True,
        "period": live_period["label"],
        "range": (
            f"{gspread.utils.rowcol_to_a1(destination_start_row, start_col)}:"
            f"{gspread.utils.rowcol_to_a1(destination_end_row, end_col)}"
        ),
        "rows_inserted": source_end_row - source_start_row + 1,
    }


def ensure_rows_before_total(worksheet, data_start_row, total_row, campaign_count):
    # Entre las campañas y el total debe quedar exactamente una fila vacía.
    required_rows_before_total = campaign_count + 1
    current_rows_before_total = total_row - data_start_row

    if current_rows_before_total == required_rows_before_total:
        return total_row, 0, 0

    if current_rows_before_total > required_rows_before_total:
        desired_total_row = data_start_row + required_rows_before_total
        delete_start_row = desired_total_row
        delete_end_row = total_row - 1
        rows_to_delete = delete_end_row - delete_start_row + 1
        worksheet.delete_rows(delete_start_row, delete_end_row)
        invalidate_worksheet_values_cache(worksheet)
        return desired_total_row, 0, rows_to_delete

    rows_to_insert = required_rows_before_total - current_rows_before_total
    blank_rows = [
        ["" for _ in range(worksheet.col_count)]
        for _ in range(rows_to_insert)
    ]

    worksheet.insert_rows(
        blank_rows,
        row=total_row,
        value_input_option="USER_ENTERED",
        inherit_from_before=True,
    )
    invalidate_worksheet_values_cache(worksheet)

    return total_row + rows_to_insert, rows_to_insert, 0


def compact_historical_total_spacing(
    worksheet,
    start_col=None,
    end_col=None,
    mutation_batch=None,
):
    """Deja una unica fila vacia antes de cada total historico heredado."""
    values = get_worksheet_values(worksheet)
    marker_row = find_historical_marker_row(values)
    period_rows = [
        row_index
        for row_index in range(marker_row + 1, len(values) + 1)
        if any(is_period_cell(value) for value in values[row_index - 1])
    ]
    deletions = []

    for index, period_row in enumerate(period_rows):
        header_row = period_row + 1
        if header_row > len(values) or not looks_like_campaign_header(
            values[header_row - 1]
        ):
            continue

        next_period_row = (
            period_rows[index + 1]
            if index + 1 < len(period_rows)
            else None
        )
        stop_row = next_period_row - 1 if next_period_row else len(values)
        header_lookup = normalized_header_lookup(values[header_row - 1])
        campaign_col = next(
            (
                header_lookup[normalize_text(alias)]
                for alias in HEADER_ALIASES["campaign_name"]
                if normalize_text(alias) in header_lookup
            ),
            None,
        )
        if campaign_col is None:
            continue

        try:
            total_row = find_total_row(
                values,
                header_row,
                campaign_col,
                len(values[header_row - 1]),
                stop_row,
            )
        except ValueError:
            continue

        campaign_count = 0
        for row_index in range(header_row + 1, total_row):
            row = values[row_index - 1]
            campaign_name = (
                row[campaign_col - 1]
                if len(row) >= campaign_col
                else ""
            )
            if not str(campaign_name).strip():
                break
            campaign_count += 1

        desired_total_row = header_row + campaign_count + 2
        if total_row > desired_total_row:
            deletions.append((desired_total_row, total_row - 1))

    deleted_rows = 0
    for start_row, end_row in reversed(deletions):
        worksheet.delete_rows(start_row, end_row)
        deleted_rows += end_row - start_row + 1

    if deleted_rows:
        invalidate_worksheet_values_cache(worksheet)
        refreshed_values = get_worksheet_values(worksheet, refresh=True)
        refreshed_period_rows = [
            row_index
            for row_index in range(marker_row + 1, len(refreshed_values) + 1)
            if any(
                is_period_cell(value)
                for value in refreshed_values[row_index - 1]
            )
        ]
        if refreshed_period_rows and start_col is not None and end_col is not None:
            side_band_end_row = find_historical_block_end_row(
                refreshed_values,
                refreshed_period_rows[-1],
                start_col,
                end_col,
            )
            apply_historical_side_band(
                worksheet,
                marker_row,
                side_band_end_row,
                mutation_batch,
            )

    return {
        "blocks_compacted": len(deletions),
        "deleted_rows": deleted_rows,
    }


def find_missing_historical_total_blocks(values):
    """Localiza historicos heredados con varias campanas y sin total."""
    marker_row = find_historical_marker_row(values)
    period_rows = [
        row_index
        for row_index in range(marker_row + 1, len(values) + 1)
        if any(is_period_cell(value) for value in values[row_index - 1])
    ]
    missing_blocks = []

    for index, period_row in enumerate(period_rows):
        header_row = period_row + 1
        if header_row > len(values) or not looks_like_campaign_header(
            values[header_row - 1]
        ):
            continue

        header_lookup = normalized_header_lookup(values[header_row - 1])
        campaign_col = next(
            (
                header_lookup[normalize_text(alias)]
                for alias in HEADER_ALIASES["campaign_name"]
                if normalize_text(alias) in header_lookup
            ),
            None,
        )
        if campaign_col is None:
            continue

        next_period_row = (
            period_rows[index + 1]
            if index + 1 < len(period_rows)
            else None
        )
        stop_row = next_period_row - 1 if next_period_row else len(values)
        campaign_count = 0
        for row_index in range(header_row + 1, stop_row + 1):
            row = values[row_index - 1]
            campaign_name = (
                row[campaign_col - 1]
                if len(row) >= campaign_col
                else ""
            )
            if not str(campaign_name).strip():
                break
            campaign_count += 1

        if campaign_count < 2:
            continue

        try:
            find_total_row(
                values,
                header_row,
                1,
                max(1, len(values[header_row - 1])),
                stop_row,
            )
            continue
        except ValueError:
            pass

        period_col = next(
            col_index
            for col_index, value in enumerate(
                values[period_row - 1],
                start=1,
            )
            if is_period_cell(value)
        )
        period_label = str(
            values[period_row - 1][period_col - 1]
        ).strip()
        missing_blocks.append({
            "period_row": period_row,
            "period_col": period_col,
            "header_row": header_row,
            "campaign_count": campaign_count,
            "period_label": period_label,
        })

    return missing_blocks


def queue_total_row_format_copy(
    worksheet,
    live_block,
    historical_block,
    total_row,
    mutation_batch=None,
):
    """Copia solo el formato de la fila total viva al total historico."""
    live_columns = live_block["columns"]
    historical_columns = historical_block["columns"]
    live_total_row = find_total_row(
        live_block["values"],
        live_block["header_row"],
        min(live_columns.values()),
        max(live_columns.values()),
        live_block.get("historical_marker_row"),
    )
    start_col = min(historical_columns.values())
    end_col = max(historical_columns.values())
    queue_format_requests(
        worksheet,
        [{
            "copyPaste": {
                "source": {
                    "sheetId": worksheet.id,
                    "startRowIndex": live_total_row - 1,
                    "endRowIndex": live_total_row,
                    "startColumnIndex": min(live_columns.values()) - 1,
                    "endColumnIndex": max(live_columns.values()),
                },
                "destination": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "pasteType": "PASTE_FORMAT",
                "pasteOrientation": "NORMAL",
            }
        }],
        mutation_batch,
    )


def repair_missing_historical_totals(
    worksheet,
    live_block,
    mutation_batch=None,
):
    """Repara todos los totales historicos omitidos, no solo el mes anterior."""
    repaired = []

    while True:
        values = get_worksheet_values(worksheet, refresh=bool(repaired))
        missing_blocks = find_missing_historical_total_blocks(values)
        if not missing_blocks:
            break

        # Se procesa de arriba abajo: las inserciones posteriores no desplazan
        # los rangos de formulas que ya quedaron preparados en filas anteriores.
        missing = missing_blocks[0]
        historical_block = build_compatible_historical_block(
            worksheet,
            values,
            missing["period_row"],
            missing["period_col"],
            missing["header_row"],
            live_block,
        )
        historical_block = ensure_historical_total_row(
            worksheet,
            historical_block,
        )
        columns = historical_block["columns"]
        start_col = min(columns.values())
        end_col = max(columns.values())
        total_row = find_total_row(
            historical_block["values"],
            historical_block["header_row"],
            start_col,
            end_col,
            find_next_period_row(
                historical_block["values"],
                historical_block["period_row"],
            ),
        )
        data_start_row = historical_block["header_row"] + 1

        queue_total_row_format_copy(
            worksheet,
            live_block,
            historical_block,
            total_row,
            mutation_batch,
        )
        repaired_campaign_rows = extract_campaign_rows_from_block(
            values,
            columns,
            data_start_row,
            data_start_row + missing["campaign_count"] - 1,
        )
        update_total_formulas(
            worksheet,
            columns,
            start_col,
            end_col,
            data_start_row,
            missing["campaign_count"],
            total_row,
            mutation_batch,
            conversion_total=sum(
                parse_sheet_number(
                    values[row_index - 1][columns["conversions"] - 1]
                    if len(values[row_index - 1]) >= columns["conversions"]
                    else 0
                )
                for row_index in range(
                    data_start_row,
                    data_start_row + missing["campaign_count"],
                )
            ),
            metric_totals=summarize_campaign_metrics(
                repaired_campaign_rows
            ),
        )
        apply_live_block_borders(
            worksheet,
            columns,
            start_col,
            end_col,
            historical_block["header_row"],
            data_start_row,
            missing["campaign_count"],
            total_row,
            mutation_batch,
        )
        repaired.append({
            "period": missing["period_label"],
            "campaign_count": missing["campaign_count"],
            "total_row": total_row,
        })

    if repaired:
        values = get_worksheet_values(worksheet, refresh=True)
        marker_row = find_historical_marker_row(values)
        period_rows = [
            row_index
            for row_index in range(marker_row + 1, len(values) + 1)
            if any(is_period_cell(value) for value in values[row_index - 1])
        ]
        if period_rows:
            columns = live_block["columns"]
            side_band_end_row = find_historical_block_end_row(
                values,
                period_rows[-1],
                min(columns.values()),
                max(columns.values()),
            )
            apply_historical_side_band(
                worksheet,
                marker_row,
                side_band_end_row,
                mutation_batch,
            )

    return repaired


def update_total_formulas(worksheet, columns, start_col, end_col, data_start_row,
                          campaign_count, total_row, mutation_batch=None,
                          apply_formats=True, conversion_total=None,
                          metric_totals=None):
    width = end_col - start_col + 1
    total_row_values = ["" for _ in range(width)]

    sum_end_row = max(data_start_row, data_start_row + campaign_count - 1)
    def col_letter(column_key):
        return gspread.utils.rowcol_to_a1(1, columns[column_key]).rstrip("1")

    def set_sum(column_key):
        if column_key not in columns:
            return

        letter = col_letter(column_key)
        total_row_values[columns[column_key] - start_col] = (
            f"=SUM({letter}{data_start_row}:{letter}{sum_end_row})"
        )

    clicks_col = col_letter("clicks")
    cost_col = col_letter("cost")
    conversions_col = col_letter("conversions")

    total_row_values[columns["clicks"] - start_col] = (
        f"=SUM({clicks_col}{data_start_row}:{clicks_col}{sum_end_row})"
    )
    total_row_values[columns["ctr"] - start_col] = "Total coste campa\u00f1as:"
    total_row_values[columns["cost"] - start_col] = (
        f"=SUM({cost_col}{data_start_row}:{cost_col}{sum_end_row})"
    )
    total_row_values[columns["conversions"] - start_col] = (
        f"=SUM({conversions_col}{data_start_row}:{conversions_col}{sum_end_row})"
    )
    total_row_values[columns["cost_per_conversion"] - start_col] = (
        f'=IFERROR({cost_col}{total_row}/{conversions_col}{total_row};"")'
    )

    set_sum("impressions")

    queue_values_update(
        worksheet,
        (
            f"{gspread.utils.rowcol_to_a1(total_row, start_col)}:"
            f"{gspread.utils.rowcol_to_a1(total_row, end_col)}"
        ),
        [total_row_values],
        mutation_batch,
    )
    if apply_formats:
        apply_total_value_formats(
            worksheet,
            columns,
            total_row,
            mutation_batch,
            conversion_value=conversion_total,
            metric_values=metric_totals,
        )

    return col_letter("cost_per_conversion")


def apply_total_value_formats(
    worksheet,
    columns,
    total_row,
    mutation_batch=None,
    conversion_value=None,
    metric_values=None,
):
    metric_values = dict(metric_values or {})
    if conversion_value is not None:
        metric_values.setdefault("conversions", conversion_value)

    number_format_by_key = {
        "clicks": ("NUMBER", "#,##0"),
        "average_cpc": (
            "NUMBER",
            optional_decimal_format_for_value(
                metric_values.get("average_cpc", 0)
            ),
        ),
        "cost": (
            "CURRENCY",
            optional_currency_format_for_value(metric_values.get("cost", 0)),
        ),
        "conversions": (
            "NUMBER",
            optional_decimal_format_for_value(
                metric_values.get("conversions", 0)
            ),
        ),
        "cost_per_conversion": (
            "CURRENCY",
            optional_currency_format_for_value(
                metric_values.get("cost_per_conversion", 0)
            ),
        ),
        "impressions": ("NUMBER", "#,##0"),
        "daily_budget": (
            "NUMBER",
            optional_decimal_format_for_value(
                metric_values.get("daily_budget", 0)
            ),
        ),
    }
    requests = []

    for key, (format_type, pattern) in number_format_by_key.items():
        if key not in columns:
            continue

        col_index = columns[key]
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": total_row - 1,
                        "endRowIndex": total_row,
                        "startColumnIndex": col_index - 1,
                        "endColumnIndex": col_index,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {
                                "type": format_type,
                                "pattern": pattern,
                            }
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            }
        )

    queue_format_requests(worksheet, requests, mutation_batch)


def find_columns_for_total_row(values, total_row):
    """Busca la cabecera mas cercana asociada a una fila de total."""
    required = {"cost", "cost_per_conversion"}
    for row_index in range(total_row - 1, 0, -1):
        try:
            columns = find_header_columns(values[row_index - 1])
        except ValueError:
            continue
        if required.issubset(columns):
            return columns
    return None


def apply_existing_total_currency_formats(
    worksheet,
    mutation_batch=None,
    metric_totals_by_row=None,
):
    """Anade la unidad EUR a todos los totales vivos e historicos existentes."""
    values = get_worksheet_values(worksheet)
    requests = []
    formatted_rows = []
    metric_totals_by_row = metric_totals_by_row or {}

    for total_row, row in enumerate(values, start=1):
        if not any(is_total_cost_cell(value) for value in row):
            continue

        columns = find_columns_for_total_row(values, total_row)
        if not columns:
            continue

        for key in ("cost", "cost_per_conversion"):
            col_index = columns[key]
            value = metric_totals_by_row.get(total_row, {}).get(
                key,
                row[col_index - 1] if len(row) >= col_index else 0,
            )
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": total_row - 1,
                        "endRowIndex": total_row,
                        "startColumnIndex": col_index - 1,
                        "endColumnIndex": col_index,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {
                                "type": "CURRENCY",
                                "pattern": optional_currency_format_for_value(
                                    value
                                ),
                            }
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            })
        formatted_rows.append(total_row)

    queue_format_requests(worksheet, requests, mutation_batch)
    return formatted_rows


def extract_campaign_rows_from_block(
    values,
    columns,
    data_start_row,
    block_stop_row,
):
    campaign_rows = []
    optional_keys = {
        "cost_per_conversion",
        "top_impression_share",
        "absolute_top_impression_share",
    }
    header_row = values[data_start_row - 2] if data_start_row >= 2 else []
    cost_per_conversion_col = columns["cost_per_conversion"]
    cost_per_conversion_header = (
        header_row[cost_per_conversion_col - 1]
        if len(header_row) >= cost_per_conversion_col
        else ""
    )
    migrate_conversion_value = is_legacy_conversion_value_header(
        cost_per_conversion_header
    )

    for row_index in range(data_start_row, block_stop_row + 1):
        row = values[row_index - 1]
        campaign_name_col = columns["campaign_name"]
        campaign_name = (
            row[campaign_name_col - 1]
            if len(row) >= campaign_name_col
            else ""
        )
        if not str(campaign_name).strip():
            break
        if any(is_total_cost_cell(value) for value in row):
            break

        campaign = {
            "campaign_id": f"sheet-row:{row_index}",
            "campaign_name": str(campaign_name).strip(),
        }
        for key, col_index in columns.items():
            if key == "campaign_name":
                continue
            value = row[col_index - 1] if len(row) >= col_index else ""
            if key == "campaign_status":
                campaign[key] = str(value).strip()
            elif key in optional_keys and not str(value).strip():
                campaign[key] = ""
            else:
                campaign[key] = parse_sheet_number(value)

        campaign["raw_cost"] = parse_sheet_number(campaign.get("cost", 0))
        if migrate_conversion_value:
            conversions = parse_sheet_number(campaign.get("conversions", 0))
            campaign["cost_per_conversion"] = (
                campaign["raw_cost"] / conversions
                if conversions
                else ""
            )
        campaign_rows.append(campaign)

    return campaign_rows


def canonical_period_label(period_identity):
    if period_identity["mode"] == "range":
        return range_label_for_days(
            period_identity["start_day"],
            period_identity["end_day"],
        )
    return month_label_for_day(period_identity["start_day"])


def existing_period_label_updates(values):
    entries = []
    for row_index, row in enumerate(values, start=1):
        for col_index, value in enumerate(row, start=1):
            if not is_period_cell(value):
                continue
            start_month = (
                parse_period_month_number(value)
                or date_range_start_month(value)
            )
            entries.append({
                "row": row_index,
                "col": col_index,
                "value": value,
                "month": start_month,
                "explicit_start": (
                    parse_period_identity(value, today_in_spain())
                    if extract_explicit_year(value) is not None
                    else None
                ),
            })
            break

    historical_marker_row = None
    try:
        historical_marker_row = find_historical_marker_row(values)
    except ValueError:
        pass

    historical_entries = [
        entry
        for entry in entries
        if historical_marker_row is not None
        and entry["row"] > historical_marker_row
    ]
    updates = []
    for entry in entries:
        identity = entry["explicit_start"]
        if identity is None and entry in historical_entries and entry["month"]:
            inferred_year = infer_historical_month_year(
                historical_entries,
                entry["row"],
            )
            if inferred_year:
                reference_day = date(
                    inferred_year,
                    entry["month"],
                    1,
                )
                identity = parse_period_identity(
                    entry["value"],
                    reference_day,
                )
                if identity is None:
                    identity = {
                        "mode": "month",
                        "start_day": reference_day,
                    }

        if identity is None:
            continue
        label = canonical_period_label(identity)
        if str(entry["value"]).strip() == label:
            continue
        updates.append({
            "cell": gspread.utils.rowcol_to_a1(entry["row"], entry["col"]),
            "value": label,
        })

    return updates


def normalize_existing_standard_block_formats(
    worksheet,
    mutation_batch=None,
):
    """Normaliza formatos de todos los bloques estandar ya archivados."""
    values = get_worksheet_values(worksheet)
    header_rows = [
        row_index
        for row_index, row in enumerate(values, start=1)
        if looks_like_campaign_header(row)
    ]
    result = {
        "blocks": 0,
        "campaigns": 0,
        "totals": 0,
        "sorted_blocks": 0,
        "period_labels": 0,
    }

    period_updates = existing_period_label_updates(values)
    for update in period_updates:
        queue_values_update(
            worksheet,
            update["cell"],
            [[update["value"]]],
            mutation_batch,
        )
    result["period_labels"] = len(period_updates)

    for header_position, header_row in enumerate(header_rows):
        try:
            columns = find_header_columns(values[header_row - 1])
        except ValueError:
            continue

        start_col = min(columns.values())
        end_col = max(columns.values())
        standard_header = build_standard_header_slice(
            columns,
            start_col,
            end_col,
        )
        current_header = values[header_row - 1][start_col - 1:end_col]
        cost_per_conversion_header = values[header_row - 1][
            columns["cost_per_conversion"] - 1
        ]
        requires_metric_migration = is_legacy_conversion_value_header(
            cost_per_conversion_header
        )
        if current_header != standard_header:
            queue_values_update(
                worksheet,
                (
                    f"{gspread.utils.rowcol_to_a1(header_row, start_col)}:"
                    f"{gspread.utils.rowcol_to_a1(header_row, end_col)}"
                ),
                [standard_header],
                mutation_batch,
            )

        next_header_row = (
            header_rows[header_position + 1]
            if header_position + 1 < len(header_rows)
            else len(values) + 1
        )
        next_period_row = find_next_period_row(values, header_row)
        block_stop_row = min(
            next_header_row,
            next_period_row or len(values) + 1,
        ) - 1
        data_start_row = header_row + 1
        campaign_rows = extract_campaign_rows_from_block(
            values,
            columns,
            data_start_row,
            block_stop_row,
        )

        if not campaign_rows:
            continue

        ordered_rows = sort_campaign_rows(campaign_rows)
        order_changed = [row["campaign_id"] for row in ordered_rows] != [
            row["campaign_id"] for row in campaign_rows
        ]
        if order_changed or requires_metric_migration:
            queue_values_update(
                worksheet,
                (
                    f"{gspread.utils.rowcol_to_a1(data_start_row, start_col)}:"
                    f"{gspread.utils.rowcol_to_a1(data_start_row + len(ordered_rows) - 1, end_col)}"
                ),
                build_output_matrix(
                    ordered_rows,
                    columns,
                    start_col,
                    end_col,
                ),
                mutation_batch,
            )
            if order_changed:
                result["sorted_blocks"] += 1
        campaign_rows = ordered_rows

        data_end_row = data_start_row + len(campaign_rows) - 1
        apply_campaign_value_formats(
            worksheet,
            columns,
            data_start_row,
            campaign_rows,
            data_end_row,
            mutation_batch,
            apply_number_formats=True,
            apply_status_formats=True,
        )
        result["blocks"] += 1
        result["campaigns"] += len(campaign_rows)

        try:
            total_row = find_total_row(
                values,
                header_row,
                min(columns.values()),
                max(columns.values()),
                stop_row=block_stop_row,
            )
        except ValueError:
            continue

        total_values = values[total_row - 1]
        is_legacy_totals_row = any(
            normalize_text(value) == "totales"
            for value in total_values[start_col - 1:end_col]
        )
        if is_legacy_totals_row:
            apply_total_value_formats(
                worksheet,
                columns,
                total_row,
                mutation_batch,
                metric_values={
                    key: (
                        total_values[col_index - 1]
                        if len(total_values) >= col_index
                        else 0
                    )
                    for key, col_index in columns.items()
                    if key not in {"campaign_name", "campaign_status"}
                },
            )
        else:
            update_total_formulas(
                worksheet,
                columns,
                start_col,
                end_col,
                data_start_row,
                len(campaign_rows),
                total_row,
                mutation_batch,
                metric_totals=summarize_campaign_metrics(campaign_rows),
            )
        result["totals"] += 1

    return result


def normalize_enabled_conditional_format_colors(
    spreadsheet,
    selected_worksheets,
    mutation_batch=None,
):
    """Unifica en verde vivo las reglas condicionales de texto `enabled`."""
    selected_sheet_ids = {worksheet.id for worksheet in selected_worksheets}
    metadata = spreadsheet.fetch_sheet_metadata(params={
        "includeGridData": "false",
        "fields": "sheets(properties(sheetId,title),conditionalFormats)",
    })
    requests = []
    changed = []

    for sheet in metadata.get("sheets", []):
        properties = sheet.get("properties", {})
        sheet_id = properties.get("sheetId")
        if sheet_id not in selected_sheet_ids:
            continue

        for rule_index, rule in enumerate(sheet.get("conditionalFormats", [])):
            boolean_rule = rule.get("booleanRule", {})
            condition = boolean_rule.get("condition", {})
            if condition.get("type") != "TEXT_CONTAINS":
                continue
            condition_values = [
                normalize_text(value.get("userEnteredValue"))
                for value in condition.get("values", [])
            ]
            if "enabled" not in condition_values:
                continue

            format_config = boolean_rule.get("format", {})
            current_color = (
                format_config.get("backgroundColorStyle", {})
                .get("rgbColor")
                or format_config.get("backgroundColor", {})
            )
            expected_color = ACCOUNT_STATUS_ENABLED_COLOR
            if all(
                abs(current_color.get(channel, 0) - expected_color[channel])
                < 0.000001
                for channel in ("red", "green", "blue")
            ):
                continue

            updated_rule = json.loads(json.dumps(rule))
            updated_format = updated_rule["booleanRule"].setdefault(
                "format",
                {},
            )
            updated_format["backgroundColor"] = dict(expected_color)
            updated_format["backgroundColorStyle"] = {
                "rgbColor": dict(expected_color),
            }
            requests.append({
                "updateConditionalFormatRule": {
                    "sheetId": sheet_id,
                    "index": rule_index,
                    "rule": updated_rule,
                }
            })
            changed.append({
                "worksheet": properties.get("title", ""),
                "rule_index": rule_index,
            })

    if mutation_batch is not None:
        mutation_batch.add_requests(requests)
    elif requests:
        spreadsheet.batch_update({"requests": requests})
    return changed


def apply_live_block_borders(
    worksheet,
    columns,
    start_col,
    end_col,
    header_row,
    data_start_row,
    campaign_count,
    total_row,
    mutation_batch=None,
):
    border = {
        "style": "SOLID",
        "width": 1,
        "color": {
            "red": 0,
            "green": 0,
            "blue": 0,
        },
    }
    no_border = {"style": "NONE"}
    last_campaign_row = max(header_row, data_start_row + campaign_count - 1)
    total_start_col = columns["clicks"]
    total_formula_keys = [
        "clicks",
        "cost",
        "conversions",
        "cost_per_conversion",
        "impressions",
    ]
    total_end_col = max(
        columns[key]
        for key in total_formula_keys
        if key in columns
    )

    queue_format_requests(
        worksheet,
        [
            {
                "updateBorders": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": header_row - 1,
                        "endRowIndex": total_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "top": no_border,
                    "bottom": no_border,
                    "left": no_border,
                    "right": no_border,
                    "innerHorizontal": no_border,
                    "innerVertical": no_border,
                }
            },
            {
                "updateBorders": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": header_row - 1,
                        "endRowIndex": last_campaign_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "top": border,
                    "bottom": border,
                    "left": border,
                    "right": border,
                    "innerHorizontal": border,
                    "innerVertical": border,
                }
            },
            {
                "updateBorders": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": total_row - 1,
                        "endRowIndex": total_row,
                        "startColumnIndex": total_start_col - 1,
                        "endColumnIndex": total_end_col,
                    },
                    "top": border,
                    "bottom": border,
                    "left": border,
                    "right": border,
                    "innerVertical": border,
                }
            },
        ],
        mutation_batch,
    )


def apply_single_campaign_historical_borders(
    worksheet,
    start_col,
    end_col,
    header_row,
    campaign_row,
    clear_end_row,
    mutation_batch=None,
):
    """Deja bordes solo en cabecera y campana, nunca en una fila de total."""
    border = {
        "style": "SOLID",
        "width": 1,
        "color": {"red": 0, "green": 0, "blue": 0},
    }
    no_border = {"style": "NONE"}
    requests = [
        {
            "updateBorders": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": header_row - 1,
                    "endRowIndex": clear_end_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "top": no_border,
                "bottom": no_border,
                "left": no_border,
                "right": no_border,
                "innerHorizontal": no_border,
                "innerVertical": no_border,
            }
        },
        {
            "updateBorders": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": header_row - 1,
                    "endRowIndex": campaign_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "top": border,
                "bottom": border,
                "left": border,
                "right": border,
                "innerHorizontal": border,
                "innerVertical": border,
            }
        },
    ]

    if clear_end_row > campaign_row:
        requests.insert(0, {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": campaign_row,
                    "endRowIndex": clear_end_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat",
            }
        })

    queue_format_requests(worksheet, requests, mutation_batch)


def apply_historical_side_band(
    worksheet,
    marker_row,
    end_row,
    mutation_batch=None,
):
    """Mantiene la banda gris continua hasta el último histórico real."""
    requests = [{
        "repeatCell": {
            "range": {
                "sheetId": worksheet.id,
                "startRowIndex": marker_row - 1,
                "endRowIndex": end_row,
                "startColumnIndex": 0,
                "endColumnIndex": 1,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": HISTORICAL_SIDE_BAND_COLOR,
                }
            },
            "fields": "userEnteredFormat.backgroundColor",
        }
    }]

    if end_row < worksheet.row_count:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": end_row,
                    "endRowIndex": worksheet.row_count,
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {}
                },
                "fields": (
                    "userEnteredFormat.backgroundColor,"
                    "userEnteredFormat.backgroundColorStyle"
                ),
            }
        })

    queue_format_requests(worksheet, requests, mutation_batch)


def apply_campaign_value_formats(
    worksheet,
    columns,
    data_start_row,
    campaign_rows,
    clear_end_row,
    mutation_batch=None,
    apply_number_formats=True,
    apply_status_formats=True,
):
    campaign_count = len(campaign_rows)
    if not apply_number_formats and not apply_status_formats:
        return

    status_col = columns["campaign_status"]
    metric_keys = (
        "clicks",
        "ctr",
        "average_cpc",
        "cost",
        "conversions",
        "cost_per_conversion",
        "impressions",
        "top_impression_share",
        "absolute_top_impression_share",
        "daily_budget",
    )
    requests = []

    if apply_status_formats:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": data_start_row - 1,
                    "endRowIndex": clear_end_row,
                    "startColumnIndex": status_col - 1,
                    "endColumnIndex": status_col,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {
                            "red": 1,
                            "green": 1,
                            "blue": 1,
                        },
                        "textFormat": {
                            "bold": False,
                            "foregroundColor": {
                                "red": 0,
                                "green": 0,
                                "blue": 0,
                            },
                        },
                    }
                },
                "fields": (
                    "userEnteredFormat.backgroundColor,"
                    "userEnteredFormat.textFormat.bold,"
                    "userEnteredFormat.textFormat.foregroundColor"
                ),
            }
        })

    if apply_number_formats and campaign_count > 0:
        for key in metric_keys:
            if key not in columns:
                continue

            col_index = columns[key]
            patterns = [
                (
                    INTEGER_NUMBER_FORMAT
                    if key in {"clicks", "impressions"}
                    else optional_decimal_format_for_value(
                        campaign.get(key, 0)
                    )
                )
                for campaign in campaign_rows
            ]
            run_start = 0
            for index in range(1, campaign_count + 1):
                run_ended = (
                    index == campaign_count
                    or patterns[index] != patterns[run_start]
                )
                if not run_ended:
                    continue

                requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": worksheet.id,
                            "startRowIndex": data_start_row - 1 + run_start,
                            "endRowIndex": data_start_row - 1 + index,
                            "startColumnIndex": col_index - 1,
                            "endColumnIndex": col_index,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "numberFormat": {
                                    "type": "NUMBER",
                                    "pattern": patterns[run_start],
                                }
                            }
                        },
                        "fields": "userEnteredFormat.numberFormat",
                    }
                })
                run_start = index

    if apply_status_formats:
        for index, campaign in enumerate(campaign_rows):
            # En la ficha con totales historicos heredados estas filas indican la fuente, no un estado Ads.
            # Se dejan con el formato neutro aplicado al rango completo.
            if campaign.get("source_customer_id") == "external":
                continue

            is_enabled = (
                normalize_text(campaign["campaign_status"]) == "enabled"
            )
            background_color = (
                ACCOUNT_STATUS_ENABLED_COLOR
                if is_enabled
                else {"red": 1, "green": 0, "blue": 0}
            )
            text_color = (
                {"red": 0, "green": 0, "blue": 0}
                if is_enabled
                else {"red": 1, "green": 1, "blue": 1}
            )
            row_index = data_start_row + index

            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": row_index - 1,
                        "endRowIndex": row_index,
                        "startColumnIndex": status_col - 1,
                        "endColumnIndex": status_col,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": background_color,
                            "textFormat": {
                                "bold": False,
                                "foregroundColor": text_color,
                            },
                        }
                    },
                    "fields": (
                        "userEnteredFormat.backgroundColor,"
                        "userEnteredFormat.textFormat.bold,"
                        "userEnteredFormat.textFormat.foregroundColor"
                    ),
                }
            })

    queue_format_requests(worksheet, requests, mutation_batch)


def update_live_block(
    worksheet,
    campaign_rows,
    block,
    period_context,
    mutation_batch=None,
    force_formats=False,
):
    campaign_rows = sort_campaign_rows(list(campaign_rows))
    values = block["values"]
    columns = block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    data_start_row = block["header_row"] + 1
    marker_row = block.get("historical_marker_row")

    total_missing = False
    try:
        total_row = find_total_row(
            values,
            block["header_row"],
            start_col,
            end_col,
            marker_row - 1 if marker_row else None,
        )
    except ValueError:
        if not marker_row:
            raise

        total_missing = True
        total_row = max(block["header_row"] + 1, marker_row - 2)

    existing_campaign_count = count_campaign_rows(
        values,
        data_start_row,
        total_row,
        columns["campaign_name"],
    )
    existing_statuses = []
    for row_index in range(
        data_start_row,
        data_start_row + existing_campaign_count,
    ):
        row = values[row_index - 1] if len(values) >= row_index else []
        status_col = columns["campaign_status"]
        existing_statuses.append(
            row[status_col - 1] if len(row) >= status_col else ""
        )

    total_row, inserted_rows, deleted_rows = ensure_rows_before_total(
        worksheet,
        data_start_row,
        total_row,
        len(campaign_rows),
    )
    clear_end_row = total_row - 1
    obsolete_clear_ranges = clear_obsolete_live_columns(
        worksheet,
        block,
        total_row,
        mutation_batch,
    )

    period_label = period_context["label"]
    period_cell = gspread.utils.rowcol_to_a1(
        block["period_row"],
        block["period_col"],
    )

    clear_start = gspread.utils.rowcol_to_a1(data_start_row, start_col)
    clear_end = gspread.utils.rowcol_to_a1(clear_end_row, end_col)
    clear_range = f"{clear_start}:{clear_end}"

    output_matrix = build_output_matrix(
        campaign_rows,
        columns,
        start_col,
        end_col,
    )
    header_slice = build_standard_header_slice(columns, start_col, end_col)
    current_header = (
        values[block["header_row"] - 1][start_col - 1:end_col]
        if len(values) >= block["header_row"]
        else []
    )
    header_changed = [normalize_text(value) for value in current_header] != [
        normalize_text(value) for value in header_slice
    ]
    structure_changed = any([
        total_missing,
        inserted_rows,
        deleted_rows,
        existing_campaign_count != len(campaign_rows),
        header_changed,
        obsolete_clear_ranges,
    ])
    new_statuses = [row["campaign_status"] for row in campaign_rows]
    statuses_changed = structure_changed or (
        [normalize_text(value) for value in existing_statuses]
        != [normalize_text(value) for value in new_statuses]
    )

    queue_values_update(
        worksheet,
        period_cell,
        [[period_label]],
        mutation_batch,
    )
    queue_values_update(
        worksheet,
        (
            f"{gspread.utils.rowcol_to_a1(block['header_row'], start_col)}:"
            f"{gspread.utils.rowcol_to_a1(block['header_row'], end_col)}"
        ),
        [header_slice],
        mutation_batch,
    )

    width = end_col - start_col + 1
    data_matrix = [
        ["" for _ in range(width)]
        for _ in range(clear_end_row - data_start_row + 1)
    ]
    for index, output_row in enumerate(output_matrix):
        data_matrix[index] = output_row
    queue_values_update(
        worksheet,
        clear_range,
        data_matrix,
        mutation_batch,
    )

    if output_matrix:
        write_start = gspread.utils.rowcol_to_a1(data_start_row, start_col)
        write_end = gspread.utils.rowcol_to_a1(
            data_start_row + len(output_matrix) - 1,
            end_col,
        )
        write_range = f"{write_start}:{write_end}"
    else:
        write_range = "(sin campanas que escribir)"

    update_total_formulas(
        worksheet,
        columns,
        start_col,
        end_col,
        data_start_row,
        len(campaign_rows),
        total_row,
        mutation_batch,
        apply_formats=True,
        conversion_total=sum(
            parse_sheet_number(row.get("conversions", 0))
            for row in campaign_rows
        ),
        metric_totals=summarize_campaign_metrics(campaign_rows),
    )
    if structure_changed or force_formats:
        apply_live_block_borders(
            worksheet,
            columns,
            start_col,
            end_col,
            block["header_row"],
            data_start_row,
            len(campaign_rows),
            total_row,
            mutation_batch,
        )
    apply_campaign_value_formats(
        worksheet,
        columns,
        data_start_row,
        campaign_rows,
        clear_end_row,
        mutation_batch,
        apply_number_formats=True,
        apply_status_formats=True,
    )

    return {
        "period_cell": period_cell,
        "period_label": period_label,
        "clear_range": clear_range,
        "write_range": write_range,
        "header_row": block["header_row"],
        "period_row": block["period_row"],
        "total_row": total_row,
        "inserted_rows": inserted_rows,
        "deleted_rows": deleted_rows,
        "obsolete_clear_ranges": obsolete_clear_ranges,
        "formats_updated": True,
    }


def build_compatible_historical_block(
    worksheet,
    values,
    period_row,
    period_col,
    header_row,
    live_block,
):
    try:
        historical_block = build_block_from_header(
            values,
            period_row,
            period_col,
            header_row,
        )
    except ValueError:
        columns = live_block["columns"]
        header_slice = build_standard_header_slice(
            columns,
            min(columns.values()),
            max(columns.values()),
        )
        return build_block_from_header(
            values,
            period_row,
            period_col,
            header_row,
            header_values=header_slice,
            header_template_row=live_block.get("header_template_row"),
        )

    live_columns = live_block["columns"]
    historical_columns = historical_block["columns"]
    misplaced = [
        key
        for key, live_col in live_columns.items()
        if historical_columns.get(key) != live_col
    ]
    if misplaced:
        details = ", ".join(
            f"{key}={historical_columns.get(key)}->{live_columns[key]}"
            for key in misplaced
        )
        raise ValueError(
            "El bloque historico no esta alineado horizontalmente con "
            f"MES ACTUAL ({details}). No se actualiza para evitar desplazar "
            "metricas."
        )

    return historical_block


def find_historical_period_block(worksheet, period_context, live_block):
    values = get_worksheet_values(worksheet)
    marker_row = find_historical_marker_row(values)
    month_fallbacks = []
    start_col = min(live_block["columns"].values())
    end_col = max(live_block["columns"].values())
    historical_month_entries = []

    for period_row in range(marker_row + 1, len(values) + 1):
        row = values[period_row - 1]
        for period_col in range(start_col, min(end_col, len(row)) + 1):
            value = row[period_col - 1]
            if not is_period_cell(value):
                continue
            if (
                period_row >= len(values)
                or not looks_like_campaign_header(values[period_row])
            ):
                continue
            historical_month_entries.append({
                "row": period_row,
                "col": period_col,
                "value": value,
                "month": parse_period_month_number(value),
                "explicit_start": (
                    parse_period_identity(value, period_context["start_day"])
                    if extract_explicit_year(value) is not None
                    else None
                ),
            })
            break

    for period_row in range(marker_row + 1, len(values) + 1):
        row = values[period_row - 1]
        for period_col, value in enumerate(row, start=1):
            if not is_period_cell(value):
                continue

            identity = parse_period_identity(
                value,
                period_context["start_day"],
            )
            if identity and identity["key"] == period_context["key"]:
                header_row = period_row + 1
                if header_row > len(values) or not looks_like_campaign_header(
                    values[header_row - 1]
                ):
                    raise ValueError(
                        "El periodo historico existe, pero no tiene una cabecera "
                        f"de campanas valida en la fila {header_row}."
                    )

                return build_compatible_historical_block(
                    worksheet,
                    values,
                    period_row,
                    period_col,
                    header_row,
                    live_block,
                )

            if (
                period_context["mode"] == "month"
                and extract_explicit_year(value) is None
                and parse_period_month_number(value)
                == period_context["start_day"].month
            ):
                month_fallbacks.append((period_row, period_col, value))

    if month_fallbacks:
        matching_fallbacks = []
        wanted_year = period_context["start_day"].year
        for period_row, period_col, value in month_fallbacks:
            inferred_year = infer_historical_month_year(
                historical_month_entries,
                period_row,
            )
            if inferred_year == wanted_year:
                matching_fallbacks.append((period_row, period_col, value))

        if not matching_fallbacks:
            return None

        period_row, period_col, _ = matching_fallbacks[-1]
        header_row = period_row + 1
        if header_row > len(values) or not looks_like_campaign_header(
            values[header_row - 1]
        ):
            raise ValueError(
                "El periodo historico sin ano existe, pero no tiene una cabecera "
                f"de campanas valida en la fila {header_row}."
            )

        return build_compatible_historical_block(
            worksheet,
            values,
            period_row,
            period_col,
            header_row,
            live_block,
        )

    return None


def infer_historical_month_year(entries, target_row):
    """Deduce el ano de un mes heredado sin sobrescribir otro ejercicio."""
    target_index = next(
        (
            index
            for index, entry in enumerate(entries)
            if entry["row"] == target_row
        ),
        None,
    )
    if target_index is None or not entries[target_index]["month"]:
        return None

    inferred_years = []

    for anchor_index in range(target_index - 1, -1, -1):
        anchor = entries[anchor_index]
        if not anchor["explicit_start"]:
            continue
        year = anchor["explicit_start"]["start_day"].year
        previous_month = anchor["explicit_start"]["start_day"].month
        for entry in entries[anchor_index + 1:target_index + 1]:
            month = entry["month"]
            if not month:
                continue
            if month < previous_month:
                year += 1
            previous_month = month
        inferred_years.append(year)
        break

    for anchor_index in range(target_index + 1, len(entries)):
        anchor = entries[anchor_index]
        if not anchor["explicit_start"]:
            continue
        year = anchor["explicit_start"]["start_day"].year
        next_month = anchor["explicit_start"]["start_day"].month
        for entry in reversed(entries[target_index:anchor_index]):
            month = entry["month"]
            if not month:
                continue
            if month > next_month:
                year -= 1
            next_month = month
        inferred_years.append(year)
        break

    if not inferred_years or len(set(inferred_years)) != 1:
        return None
    return inferred_years[0]


def extract_preserved_historical_campaign_rows(
    worksheet,
    sem_client,
    period_context,
    live_block,
):
    """Lee filas de fuentes externas que Google Ads no puede reconstruir."""
    preserved_statuses = {
        normalize_text(status)
        for status in sem_client.get("preserve_historical_statuses", [])
    }
    if not preserved_statuses:
        return []

    historical_block = find_historical_period_block(
        worksheet,
        period_context,
        live_block,
    )
    if historical_block is None:
        return []

    values = historical_block["values"]
    columns = historical_block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    next_period_row = find_next_period_row(
        values,
        historical_block["header_row"],
    )
    try:
        campaign_stop_row = find_total_row(
            values,
            historical_block["header_row"],
            start_col,
            end_col,
            next_period_row - 1 if next_period_row else None,
        )
    except ValueError:
        campaign_stop_row = find_historical_block_end_row(
            values,
            historical_block["period_row"],
            start_col,
            end_col,
        ) + 1

    def cell_value(row, key):
        col_index = columns[key]
        return row[col_index - 1] if len(row) >= col_index else ""

    preserved_rows = []
    for row_index in range(
        historical_block["header_row"] + 1,
        campaign_stop_row,
    ):
        row = values[row_index - 1] if len(values) >= row_index else []
        campaign_name = str(cell_value(row, "campaign_name")).strip()
        campaign_status = str(cell_value(row, "campaign_status")).strip()
        if not campaign_name or normalize_text(campaign_status) not in preserved_statuses:
            continue

        cost = parse_sheet_number(cell_value(row, "cost"))
        optional_keys = {
            "cost_per_conversion",
            "top_impression_share",
            "absolute_top_impression_share",
        }
        numeric_values = {}
        for key in STANDARD_HEADER_BY_KEY:
            if key in {"campaign_name", "campaign_status"}:
                continue
            raw_value = cell_value(row, key)
            numeric_values[key] = (
                ""
                if key in optional_keys and not str(raw_value).strip()
                else parse_sheet_number(raw_value)
            )

        for integer_key in ("clicks", "impressions"):
            numeric_values[integer_key] = int(
                round(float(numeric_values[integer_key] or 0))
            )

        preserved_rows.append({
            "campaign_id": f"external:{period_context['key']}:{row_index}",
            "campaign_name": campaign_name,
            "campaign_status": campaign_status,
            **numeric_values,
            "cost": cost,
            "raw_cost": cost,
            "source_customer_id": "external",
        })

    return preserved_rows


def read_external_current_cost(worksheet, sem_client):
    cell = sem_client.get("external_current_cost_cell")
    if not cell:
        return 0.0

    row_index, col_index = gspread.utils.a1_to_rowcol(cell)
    values = get_worksheet_values(worksheet)
    row = values[row_index - 1] if len(values) >= row_index else []
    value = row[col_index - 1] if len(row) >= col_index else ""
    return parse_sheet_number(value)


def create_historical_period_block_from_live(
    worksheet,
    live_block,
    period_context,
    campaign_count,
):
    values = get_worksheet_values(worksheet)
    columns = live_block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    marker_row = find_historical_marker_row(values)

    try:
        live_total_row = find_total_row(
            values,
            live_block["header_row"],
            start_col,
            end_col,
            marker_row - 1,
        )
    except ValueError:
        live_total_row = max(live_block["header_row"] + 1, marker_row - 2)

    source_end_row = historical_copy_end_row(
        live_block,
        live_total_row,
        campaign_count,
    )
    destination_start_row = find_historical_append_row(
        values,
        marker_row,
        start_col,
        end_col,
        period_context,
    )
    destination_end_row = copy_range_to_rows(
        worksheet,
        live_block["period_row"],
        source_end_row,
        start_col,
        end_col,
        destination_start_row,
    )

    refreshed_values = get_worksheet_values(worksheet, refresh=True)
    historical_block = build_block_from_header(
        refreshed_values,
        destination_start_row,
        start_col,
        destination_start_row + 1,
    )

    return historical_block, {
        "range": (
            f"{gspread.utils.rowcol_to_a1(destination_start_row, start_col)}:"
            f"{gspread.utils.rowcol_to_a1(destination_end_row, end_col)}"
        ),
        "rows_inserted": destination_end_row - destination_start_row + 1,
    }


def update_single_campaign_historical_block(
    worksheet,
    campaign_rows,
    historical_block,
    period_context,
    mutation_batch=None,
):
    """Actualiza un historico de una campana sin duplicar sus totales."""
    if len(campaign_rows) != 1:
        raise ValueError(
            "La actualizacion historica sin total requiere una sola campana."
        )

    values = historical_block["values"]
    columns = historical_block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    data_start_row = historical_block["header_row"] + 1
    block_end_row = find_historical_block_end_row(
        values,
        historical_block["period_row"],
        start_col,
        end_col,
    )
    clear_end_row = max(data_start_row, block_end_row)
    obsolete_clear_ranges = clear_obsolete_live_columns(
        worksheet,
        historical_block,
        clear_end_row,
        mutation_batch,
    )

    period_cell = gspread.utils.rowcol_to_a1(
        historical_block["period_row"],
        historical_block["period_col"],
    )
    header_range = (
        f"{gspread.utils.rowcol_to_a1(historical_block['header_row'], start_col)}:"
        f"{gspread.utils.rowcol_to_a1(historical_block['header_row'], end_col)}"
    )
    clear_range = (
        f"{gspread.utils.rowcol_to_a1(data_start_row, start_col)}:"
        f"{gspread.utils.rowcol_to_a1(clear_end_row, end_col)}"
    )
    output_row = build_output_matrix(
        campaign_rows,
        columns,
        start_col,
        end_col,
    )[0]
    width = end_col - start_col + 1
    data_matrix = [output_row]
    data_matrix.extend(
        ["" for _ in range(width)]
        for _ in range(clear_end_row - data_start_row)
    )

    queue_values_update(
        worksheet,
        period_cell,
        [[period_context["label"]]],
        mutation_batch,
    )
    queue_values_update(
        worksheet,
        header_range,
        [build_standard_header_slice(columns, start_col, end_col)],
        mutation_batch,
    )
    queue_values_update(
        worksheet,
        clear_range,
        data_matrix,
        mutation_batch,
    )

    apply_single_campaign_historical_borders(
        worksheet,
        start_col,
        end_col,
        historical_block["header_row"],
        data_start_row,
        clear_end_row,
        mutation_batch,
    )
    apply_campaign_value_formats(
        worksheet,
        columns,
        data_start_row,
        campaign_rows,
        data_start_row,
        mutation_batch,
        apply_number_formats=True,
        apply_status_formats=True,
    )
    side_band_end_row = resolve_historical_side_band_end(
        historical_block,
        data_start_row,
    )
    apply_historical_side_band(
        worksheet,
        find_historical_marker_row(values),
        side_band_end_row,
        mutation_batch,
    )

    write_range = (
        f"{gspread.utils.rowcol_to_a1(data_start_row, start_col)}:"
        f"{gspread.utils.rowcol_to_a1(data_start_row, end_col)}"
    )
    return {
        "period_cell": period_cell,
        "period_label": period_context["label"],
        "clear_range": clear_range,
        "write_range": write_range,
        "header_row": historical_block["header_row"],
        "period_row": historical_block["period_row"],
        "total_row": None,
        "end_row": data_start_row,
        "inserted_rows": 0,
        "deleted_rows": 0,
        "obsolete_clear_ranges": obsolete_clear_ranges,
        "formats_updated": True,
        "includes_total": False,
    }


def ensure_historical_total_row(worksheet, historical_block):
    # Algunos historicos de Supermetrics terminan tras las campanas y no tienen total.
    values = historical_block["values"]
    columns = historical_block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    period_row = historical_block["period_row"]
    header_row = historical_block["header_row"]
    next_period_row = None

    for row_index in range(header_row + 1, len(values) + 1):
        if any(is_period_cell(value) for value in values[row_index - 1]):
            next_period_row = row_index
            break

    try:
        find_total_row(
            values,
            header_row,
            start_col,
            end_col,
            next_period_row - 1 if next_period_row else None,
        )
        return historical_block
    except ValueError:
        pass

    data_start_row = header_row + 1
    campaign_col = columns["campaign_name"]
    campaign_count = 0

    for row_index in range(data_start_row, len(values) + 1):
        if next_period_row and row_index >= next_period_row:
            break

        row = values[row_index - 1]
        campaign_name = (
            row[campaign_col - 1]
            if len(row) >= campaign_col
            else ""
        )
        if not str(campaign_name).strip():
            break

        campaign_count += 1

    total_row = data_start_row + campaign_count + 1
    if next_period_row and total_row >= next_period_row:
        rows_to_insert = total_row - next_period_row + 1
        worksheet.insert_rows(
            [["" for _ in range(worksheet.col_count)] for _ in range(rows_to_insert)],
            row=next_period_row,
            value_input_option="USER_ENTERED",
            inherit_from_before=True,
        )
        invalidate_worksheet_values_cache(worksheet)

    total_label_cell = gspread.utils.rowcol_to_a1(
        total_row,
        columns["ctr"],
    )
    worksheet.update(
        range_name=total_label_cell,
        values=[["Total coste campa\u00f1as:"]],
        value_input_option="USER_ENTERED",
    )
    invalidate_worksheet_values_cache(worksheet)

    refreshed_values = get_worksheet_values(worksheet, refresh=True)
    return build_block_from_header(
        refreshed_values,
        period_row,
        historical_block["period_col"],
        header_row,
    )


def upsert_historical_period(
    worksheet,
    live_block,
    campaign_rows,
    period_context,
    mutation_batch=None,
    force_formats=False,
):
    ensure_historical_marker_row(worksheet, live_block)
    historical_block = find_historical_period_block(
        worksheet,
        period_context,
        live_block,
    )
    created = historical_block is None
    creation_result = None

    if created:
        historical_block, creation_result = create_historical_period_block_from_live(
            worksheet,
            live_block,
            period_context,
            len(campaign_rows),
        )

    if len(campaign_rows) == 1:
        write_result = update_single_campaign_historical_block(
            worksheet,
            campaign_rows,
            historical_block,
            period_context,
            mutation_batch,
        )
    else:
        historical_block = ensure_historical_total_row(
            worksheet,
            historical_block,
        )
        write_result = update_live_block(
            worksheet,
            campaign_rows,
            historical_block,
            period_context,
            mutation_batch,
            force_formats=force_formats,
        )
        write_result["end_row"] = write_result["total_row"]
        write_result["includes_total"] = True
        side_band_end_row = resolve_historical_side_band_end(
            historical_block,
            write_result["end_row"],
            write_result["inserted_rows"],
            write_result["deleted_rows"],
        )
        apply_historical_side_band(
            worksheet,
            find_historical_marker_row(historical_block["values"]),
            side_band_end_row,
            mutation_batch,
        )

    return {
        "created": created,
        "creation": creation_result,
        "write": write_result,
        "period": period_context["label"],
    }


def queue_legacy_historical_historical_total(
    worksheet,
    columns,
    start_col,
    end_col,
    data_start_row,
    campaign_count,
    total_row,
    mutation_batch=None,
    conversion_total=None,
    metric_totals=None,
):
    """Restaura la fila TOTALES historica propia de la ficha con totales historicos heredados."""
    width = end_col - start_col + 1
    values = ["" for _ in range(width)]
    data_end_row = data_start_row + campaign_count - 1

    def letter(key):
        return gspread.utils.rowcol_to_a1(1, columns[key]).rstrip("1")

    def set_value(key, value):
        values[columns[key] - start_col] = value

    clicks = letter("clicks")
    cost = letter("cost")
    conversions = letter("conversions")
    impressions = letter("impressions")

    set_value("campaign_status", "TOTALES")
    set_value("clicks", f"=SUM({clicks}{data_start_row}:{clicks}{data_end_row})")
    set_value("ctr", f'=IFERROR({clicks}{total_row}/{impressions}{total_row};"")')
    set_value("average_cpc", f'=IFERROR({cost}{total_row}/{clicks}{total_row};"")')
    set_value("cost", f"=SUM({cost}{data_start_row}:{cost}{data_end_row})")
    set_value(
        "conversions",
        f"=SUM({conversions}{data_start_row}:{conversions}{data_end_row})",
    )
    set_value(
        "cost_per_conversion",
        f'=IFERROR({cost}{total_row}/{conversions}{total_row};"")',
    )
    set_value(
        "impressions",
        f"=SUM({impressions}{data_start_row}:{impressions}{data_end_row})",
    )

    total_range = (
        f"{gspread.utils.rowcol_to_a1(total_row, start_col)}:"
        f"{gspread.utils.rowcol_to_a1(total_row, end_col)}"
    )
    queue_values_update(
        worksheet,
        total_range,
        [values],
        mutation_batch,
    )

    border = {
        "style": "SOLID",
        "width": 1,
        "color": {"red": 0, "green": 0, "blue": 0},
    }
    no_border = {"style": "NONE"}
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": columns["campaign_status"] - 1,
                    "endColumnIndex": columns["campaign_status"],
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {
                            "red": 0.9764706,
                            "green": 0.79607844,
                            "blue": 0.6117647,
                        },
                        "textFormat": {
                            "fontFamily": "Arial",
                            "bold": True,
                            "italic": True,
                        },
                    }
                },
                "fields": "userEnteredFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": columns["clicks"] - 1,
                    "endColumnIndex": columns["impressions"],
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 1, "green": 1, "blue": 1},
                        "horizontalAlignment": "RIGHT",
                        "textFormat": {"fontFamily": "Arial"},
                    }
                },
                "fields": (
                    "userEnteredFormat.backgroundColor,"
                    "userEnteredFormat.horizontalAlignment,"
                    "userEnteredFormat.textFormat.fontFamily"
                ),
            }
        },
        {
            "updateBorders": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "top": no_border,
                "bottom": no_border,
                "left": no_border,
                "right": no_border,
                "innerVertical": no_border,
            }
        },
        {
            "updateBorders": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": columns["campaign_status"] - 1,
                    "endColumnIndex": columns["impressions"],
                },
                "top": border,
                "bottom": border,
                "left": border,
                "right": border,
                "innerVertical": border,
            }
        },
    ]

    metric_totals = dict(metric_totals or {})
    if conversion_total is not None:
        metric_totals.setdefault("conversions", conversion_total)
    number_formats = {
        "clicks": ("NUMBER", "#,##0"),
        "ctr": (
            "PERCENT",
            optional_percent_format_for_value(metric_totals.get("ctr", 0)),
        ),
        "average_cpc": (
            "CURRENCY",
            optional_currency_format_for_value(
                metric_totals.get("average_cpc", 0)
            ),
        ),
        "cost": (
            "CURRENCY",
            optional_currency_format_for_value(metric_totals.get("cost", 0)),
        ),
        "conversions": (
            "NUMBER",
            optional_decimal_format_for_value(
                metric_totals.get("conversions", 0)
            ),
        ),
        "cost_per_conversion": (
            "CURRENCY",
            optional_currency_format_for_value(
                metric_totals.get("cost_per_conversion", 0)
            ),
        ),
        "impressions": ("NUMBER", "#,##0"),
    }
    for key, (format_type, pattern) in number_formats.items():
        col_index = columns[key]
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": col_index - 1,
                    "endColumnIndex": col_index,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {
                            "type": format_type,
                            "pattern": pattern,
                        }
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        })

    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": worksheet.id,
                "startRowIndex": total_row - 1,
                "endRowIndex": total_row,
                "startColumnIndex": columns["cost"] - 1,
                "endColumnIndex": columns["cost"],
            },
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"bold": True},
                }
            },
            "fields": "userEnteredFormat.textFormat.bold",
        }
    })
    queue_format_requests(worksheet, requests, mutation_batch)


def normalize_legacy_historical_historical_totals(
    worksheet,
    mutation_batch=None,
    force_total_rows=None,
):
    """Normaliza todas las filas de total del historico mensual de la ficha con totales historicos heredados."""
    values = get_worksheet_values(worksheet, refresh=True)
    marker_row = find_historical_marker_row(values)
    normalized = []
    force_total_rows = set(force_total_rows or [])

    for period_row in range(marker_row + 1, len(values) + 1):
        if not any(is_period_cell(value) for value in values[period_row - 1]):
            continue

        header_row = period_row + 1
        if header_row > len(values) or not looks_like_campaign_header(
            values[header_row - 1]
        ):
            continue

        columns = find_header_columns(values[header_row - 1])
        start_col = min(columns.values())
        end_col = max(columns.values())
        next_period_row = find_next_period_row(values, period_row)
        try:
            total_row = find_total_row(
                values,
                header_row,
                start_col,
                end_col,
                next_period_row - 1 if next_period_row else None,
            )
        except ValueError:
            continue

        data_start_row = header_row + 1
        campaign_count = count_campaign_rows(
            values,
            data_start_row,
            total_row,
            columns["campaign_name"],
        )
        if campaign_count <= 1:
            continue

        campaign_rows = extract_campaign_rows_from_block(
            values,
            columns,
            data_start_row,
            data_start_row + campaign_count - 1,
        )

        queue_legacy_historical_historical_total(
            worksheet,
            columns,
            start_col,
            end_col,
            data_start_row,
            campaign_count,
            total_row,
            mutation_batch,
            conversion_total=sum(
                parse_sheet_number(
                    values[row_index - 1][columns["conversions"] - 1]
                    if len(values[row_index - 1]) >= columns["conversions"]
                    else 0
                )
                for row_index in range(
                    data_start_row,
                    data_start_row + campaign_count,
                )
            ),
            metric_totals=summarize_campaign_metrics(campaign_rows),
        )
        normalized.append(total_row)

    return normalized


def update_account_status_cell(
    worksheet,
    sem_client,
    account_status,
    mutation_batch=None,
):
    status_cell = sem_client.get("account_status_cell", "A2")
    status_row, status_col = gspread.utils.a1_to_rowcol(status_cell)
    is_enabled = normalize_text(account_status) == "enabled"
    background_color = (
        ACCOUNT_STATUS_ENABLED_COLOR
        if is_enabled
        else ACCOUNT_STATUS_DISABLED_COLOR
    )
    text_color = (
        {"red": 0, "green": 0, "blue": 0}
        if is_enabled
        else {"red": 1, "green": 1, "blue": 1}
    )

    queue_values_update(
        worksheet,
        status_cell,
        [[account_status]],
        mutation_batch,
    )
    queue_format_requests(
        worksheet,
        [{
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": status_row - 1,
                    "endRowIndex": status_row,
                    "startColumnIndex": status_col - 1,
                    "endColumnIndex": status_col,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": background_color,
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": text_color,
                        },
                    }
                },
                "fields": (
                    "userEnteredFormat.backgroundColor,"
                    "userEnteredFormat.textFormat.bold,"
                    "userEnteredFormat.textFormat.foregroundColor"
                ),
            }
        }],
        mutation_batch,
    )
    return status_cell


def update_configured_account_statuses(
    worksheet,
    sem_client,
    account_statuses,
    mutation_batch=None,
):
    customer_ids = get_sem_customer_ids(sem_client)
    configured_cells = {
        normalize_customer_id(customer_id): cell
        for customer_id, cell in sem_client.get(
            "account_status_cells",
            {},
        ).items()
    }
    write_individual_statuses = sem_client.get(
        "write_individual_account_statuses",
        True,
    )

    if (
        len(customer_ids) > 1
        and write_individual_statuses
        and not configured_cells
    ):
        raise ValueError(
            f"La ficha multicuenta {sem_client['nombre']} necesita "
            "account_status_cells."
        )

    updates = []
    start_index = 0
    if len(customer_ids) > 1:
        aggregate_status = aggregate_account_operational_status(
            account_statuses,
            customer_ids,
            require_all_enabled=sem_client.get(
                "require_all_accounts_enabled",
                False,
            ),
        )
        primary_customer_id = customer_ids[0]
        aggregate_cell = configured_cells.get(
            primary_customer_id,
            sem_client.get("account_status_cell", "A2"),
        )
        update_account_status_cell(
            worksheet,
            {"account_status_cell": aggregate_cell},
            aggregate_status,
            mutation_batch,
        )
        updates.append({
            "customer_id": "GLOBAL",
            "status": aggregate_status,
            "cell": aggregate_cell,
        })
        start_index = 1

    individual_customer_ids = (
        customer_ids[start_index:]
        if write_individual_statuses
        else []
    )
    for customer_id in individual_customer_ids:
        status = account_statuses.get(customer_id, "unknown")
        status_config = {
            "account_status_cell": configured_cells.get(
                customer_id,
                sem_client.get("account_status_cell", "A2"),
            )
        }
        cell = update_account_status_cell(
            worksheet,
            status_config,
            status,
            mutation_batch,
        )
        updates.append({
            "customer_id": customer_id,
            "status": status,
            "cell": cell,
        })

    for status_cell in sem_client.get("clear_account_status_cells", []):
        status_row, status_col = gspread.utils.a1_to_rowcol(status_cell)
        queue_values_update(
            worksheet,
            status_cell,
            [[""]],
            mutation_batch,
        )
        queue_format_requests(
            worksheet,
            [{
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": status_row - 1,
                        "endRowIndex": status_row,
                        "startColumnIndex": status_col - 1,
                        "endColumnIndex": status_col,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {
                                "red": 1,
                                "green": 1,
                                "blue": 1,
                            },
                            "textFormat": {
                                "bold": False,
                                "foregroundColor": {
                                    "red": 0,
                                    "green": 0,
                                    "blue": 0,
                                },
                            },
                        }
                    },
                    "fields": (
                        "userEnteredFormat.backgroundColor,"
                        "userEnteredFormat.textFormat.bold,"
                        "userEnteredFormat.textFormat.foregroundColor"
                    ),
                }
            }],
            mutation_batch,
        )
    return updates


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
    normalized = [normalize_text(status) for status in statuses]

    if require_all_enabled:
        if normalized and all(status == "enabled" for status in normalized):
            return ACCOUNT_STATUS_ENABLED
        for status, normalized_status in zip(statuses, normalized):
            if normalized_status != "enabled":
                return customer_status_display_label(status)

    if "enabled" in normalized:
        return ACCOUNT_STATUS_ENABLED
    if "finalizada" in normalized:
        return ACCOUNT_STATUS_FINISHED
    if "paused" in normalized:
        return ACCOUNT_STATUS_PAUSED
    return customer_status_display_label(statuses[0] if statuses else "Unknown")


def update_and_log_account_statuses(
    worksheet,
    sem_client,
    account_statuses,
    mutation_batch=None,
):
    updates = update_configured_account_statuses(
        worksheet,
        sem_client,
        account_statuses,
        mutation_batch,
    )
    for update in updates:
        print(
            "Estado escrito: "
            f"cuenta {update['customer_id']} = {update['status']} "
            f"en {update['cell']}"
        )
    return updates


def find_vista_global_marker_row(values, marker):
    marker_key = normalize_text(marker)
    for row_index, row in enumerate(values):
        if any(normalize_text(value) == marker_key for value in row[:3]):
            return row_index
    raise ValueError(
        f"No se encontro el marcador '{marker}' en Vista Global."
    )


def locate_vista_global_sections(values):
    signed_row = find_vista_global_marker_row(
        values,
        VISTA_GLOBAL_SIGNED_MARKER,
    )
    standby_row = find_vista_global_marker_row(
        values,
        VISTA_GLOBAL_STANDBY_MARKER,
    )
    natalia_row = find_vista_global_marker_row(
        values,
        VISTA_GLOBAL_NATALIA_MARKER,
    )
    if not (1 < signed_row < standby_row < natalia_row):
        raise ValueError(
            "Los bloques de Vista Global no conservan el orden esperado: "
            "activos, firmados sin comenzar, Stand By y Natalia."
        )
    return {
        "active_start": 1,
        "signed_row": signed_row,
        "standby_row": standby_row,
        "natalia_row": natalia_row,
    }


def vista_global_row_value(row, column_index):
    return row[column_index] if column_index < len(row) else ""


def find_active_insertion_index(values, sections, monthly_budget):
    for row_index in range(
        sections["active_start"],
        sections["signed_row"],
    ):
        row = values[row_index]
        client_name = vista_global_row_value(
            row,
            VISTA_GLOBAL_CLIENT_COLUMN,
        )
        if not str(client_name).strip():
            continue
        existing_budget = parse_sheet_number(
            vista_global_row_value(
                row,
                VISTA_GLOBAL_MONTHLY_BUDGET_COLUMN,
            )
        )
        if existing_budget < monthly_budget:
            return row_index
    return sections["signed_row"]


def plan_next_vista_global_status_move(values):
    sections = locate_vista_global_sections(values)

    # Las bajas hacia Stand By son una decision manual. El script solo conserva
    # la ayuda inversa para devolver al bloque activo una fila ya reactivada.
    for row_index in range(
        sections["standby_row"] + 1,
        sections["natalia_row"],
    ):
        row = values[row_index]
        client_name = vista_global_row_value(
            row,
            VISTA_GLOBAL_CLIENT_COLUMN,
        )
        status = vista_global_row_value(
            row,
            VISTA_GLOBAL_STATUS_COLUMN,
        )
        if (
            str(client_name).strip()
            and normalize_text(status) == "enabled"
        ):
            monthly_budget = parse_sheet_number(
                vista_global_row_value(
                    row,
                    VISTA_GLOBAL_MONTHLY_BUDGET_COLUMN,
                )
            )
            return {
                "direction": "active",
                "client": str(client_name).strip(),
                "status": str(status).strip(),
                "source_index": row_index,
                "destination_index": find_active_insertion_index(
                    values,
                    sections,
                    monthly_budget,
                ),
            }
    return None


def reconcile_vista_global_status_blocks(spreadsheet):
    worksheet = spreadsheet.worksheet(VISTA_GLOBAL_WORKSHEET_NAME)
    moved = []

    for _ in range(100):
        values = worksheet.get_all_values()
        move = plan_next_vista_global_status_move(values)
        if move is None:
            break
        spreadsheet.batch_update({
            "requests": [{
                "moveDimension": {
                    "source": {
                        "sheetId": worksheet.id,
                        "dimension": "ROWS",
                        "startIndex": move["source_index"],
                        "endIndex": move["source_index"] + 1,
                    },
                    "destinationIndex": move["destination_index"],
                }
            }]
        })
        moved.append(move)
        destination = (
            "Stand By"
            if move["direction"] == "standby"
            else "clientes activos"
        )
        print(
            "Vista Global: "
            f"{move['client']} movido a {destination} "
            f"(estado {move['status']})."
        )
    else:
        raise RuntimeError(
            "Vista Global supero el limite seguro de 100 movimientos."
        )

    return moved


def parse_saldo_year_label(value):
    match = re.search(r"(20\d{2})(?:\D+(\d{2,4}))?", str(value or ""))

    if not match:
        return None

    start_year = int(match.group(1))
    end_year = None

    if match.group(2):
        end_text = match.group(2)
        end_year = int(end_text)

        if len(end_text) == 2:
            end_year = (start_year // 100) * 100 + end_year
            if end_year < start_year:
                end_year += 100

    return {
        "start_year": start_year,
        "end_year": end_year,
    }


def parse_sequence_number(value):
    text = str(value or "").strip().replace(",", ".")

    if not re.fullmatch(r"\d+(?:\.0+)?", text):
        return None

    return int(float(text))


def date_range_start_month(value):
    match = DATE_RANGE_PATTERN.search(str(value or ""))

    if not match:
        return None

    return int(match.group("start_month"))


def parse_sheet_date(value):
    """Interpreta fechas ISO, numericas o con meses abreviados en espanol."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value or "").strip()
    if not text:
        return None

    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass

    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    match = re.fullmatch(
        r"\s*(\d{1,2})\s*[-/. ]\s*([a-z]+|\d{1,2})"
        r"\s*[-/. ]\s*(\d{4})\s*",
        normalized,
    )
    if not match:
        return None

    day_number = int(match.group(1))
    month_token = match.group(2)
    month_number = (
        int(month_token)
        if month_token.isdigit()
        else MONTH_NUMBERS_BY_NAME.get(month_token)
    )
    if not month_number:
        return None

    try:
        return date(int(match.group(3)), month_number, day_number)
    except ValueError:
        return None


def find_contract_date_range(values):
    """Localiza Fecha inicio/Fecha Fin y devuelve el contrato continuo."""
    for row_index, row in enumerate(values):
        normalized = [normalize_text(value) for value in row]
        if "fechainicio" not in normalized or "fechafin" not in normalized:
            continue

        if row_index + 1 >= len(values):
            break

        start_col = normalized.index("fechainicio")
        end_col = normalized.index("fechafin")
        value_row = values[row_index + 1]
        start_value = value_row[start_col] if len(value_row) > start_col else ""
        end_value = value_row[end_col] if len(value_row) > end_col else ""
        start_day = parse_sheet_date(start_value)
        end_day = parse_sheet_date(end_value)

        if not start_day or not end_day:
            raise SaldoPeriodValidationError(
                "Fecha inicio o Fecha Fin no tiene un formato reconocido."
            )
        if end_day < start_day:
            raise SaldoPeriodValidationError(
                "Fecha Fin es anterior a Fecha inicio."
            )
        return {
            "start_day": start_day,
            "end_day": end_day,
            "start_cell": gspread.utils.rowcol_to_a1(
                row_index + 2,
                start_col + 1,
            ),
            "end_cell": gspread.utils.rowcol_to_a1(
                row_index + 2,
                end_col + 1,
            ),
        }

    raise SaldoPeriodValidationError(
        "No se encontraron Fecha inicio y Fecha Fin para el contrato continuo."
    )


def add_calendar_months(month_day, offset):
    month_index = month_day.year * 12 + month_day.month - 1 + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


def add_one_contract_month(day):
    next_month = add_calendar_months(day.replace(day=1), 1)
    next_month_last_day = calendar.monthrange(
        next_month.year,
        next_month.month,
    )[1]
    current_month_last_day = calendar.monthrange(day.year, day.month)[1]
    target_day = (
        next_month_last_day
        if day.day == current_month_last_day
        else min(day.day, next_month_last_day)
    )
    return date(next_month.year, next_month.month, target_day)


def google_sheets_date_serial(day):
    return (day - GOOGLE_SHEETS_DATE_EPOCH).days


def has_monthly_renewal_without_end(values):
    renewal_keys = [normalize_text(value) for value in MONTHLY_RENEWAL_TEXTS]
    return any(
        any(renewal_key in normalize_text(value) for renewal_key in renewal_keys)
        for row in values
        for value in row
    )


def monthly_renewal_note(existing_note, renewal_month):
    marker = MONTHLY_RENEWAL_NOTE_PREFIX + renewal_month
    kept_lines = [
        line
        for line in str(existing_note or "").splitlines()
        if not any(
            line.startswith(prefix)
            for prefix in MONTHLY_RENEWAL_NOTE_PREFIXES
        )
    ]
    kept_lines.append(marker)
    return "\n".join(line for line in kept_lines if line).strip()


def plan_monthly_renewal_updates(
    worksheet,
    values,
    period_targets,
    target_day,
):
    result = {
        "enabled": has_monthly_renewal_without_end(values),
        "applied": False,
        "catch_up": False,
        "value_updates": [],
        "note_update": None,
        "period_label": None,
        "reason": None,
    }
    if not result["enabled"]:
        return result

    contract_range = find_contract_date_range(values)
    active_targets = [
        target
        for target in period_targets
        if target["period_context"]["start_day"]
        <= target_day
        <= target["period_context"]["end_day"]
    ]
    if len(active_targets) != 1:
        result["reason"] = "No hay un unico periodo escrito vigente para hoy."
        return result

    # Una ficha puede cambiar de meses naturales a rangos personalizados en
    # otro ano. Solo el periodo vigente decide que regla de renovacion usar;
    # los rangos futuros no deben reclasificar los meses actuales.
    current_month_target = active_targets[0]
    uses_custom_ranges = (
        current_month_target["period_context"]["mode"] == "range"
    )

    if uses_custom_ranges:
        range_start = current_month_target["period_context"]["start_day"]
        if (range_start.year, range_start.month) != (
            target_day.year,
            target_day.month,
        ):
            result["reason"] = (
                "El rango vigente empezo en un mes anterior y todavia no ha "
                "comenzado el siguiente periodo."
            )
            return result
        result["catch_up"] = target_day != range_start
    else:
        current_month_end = date(
            target_day.year,
            target_day.month,
            calendar.monthrange(target_day.year, target_day.month)[1],
        )
        current_month_start = target_day.replace(day=1)
        result["catch_up"] = target_day.day != 1
        if (
            result["catch_up"]
            and contract_range["end_day"] >= current_month_start
        ):
            result["reason"] = (
                "Fuera del dia 1 solo se recuperan renovaciones cuya Fecha Fin "
                "es anterior al mes actual."
            )
            return result

        if (
            current_month_target["period_context"]["start_day"].year,
            current_month_target["period_context"]["start_day"].month,
        ) != (target_day.year, target_day.month):
            result["reason"] = (
                "No hay un mes escrito que empiece en el mes actual."
            )
            return result

    previous_targets = [
        target
        for target in period_targets
        if target["period_context"]["end_day"]
        < current_month_target["period_context"]["start_day"]
    ]
    if not previous_targets:
        raise SaldoPeriodValidationError(
            "La renovacion mensual no encontro el periodo anterior."
        )
    current_budget_cell = current_month_target.get("monthly_budget_cell")
    if not current_budget_cell:
        raise SaldoPeriodValidationError(
            "La renovacion mensual no encontro la columna 'Presup mes'."
        )

    renewal_start = current_month_target["period_context"]["start_day"]
    renewal_month = f"{renewal_start.year:04d}-{renewal_start.month:02d}"
    existing_note = getattr(
        worksheet,
        "_sem_monthly_renewal_note",
        None,
    )
    if existing_note is None:
        existing_note = worksheet.get_note(contract_range["end_cell"])
    if any(
        prefix + renewal_month in str(existing_note or "")
        for prefix in MONTHLY_RENEWAL_NOTE_PREFIXES
    ):
        result["reason"] = (
            f"La renovacion {renewal_month} ya estaba aplicada."
        )
        return result

    current_budget_row, current_budget_col = (
        gspread.utils.a1_to_rowcol(current_budget_cell)
    )
    current_budget = (
        values[current_budget_row - 1][current_budget_col - 1]
        if len(values) >= current_budget_row
        and len(values[current_budget_row - 1]) >= current_budget_col
        else ""
    )

    previous_budget = ""
    previous_budget_cell = None
    for previous_target in reversed(previous_targets):
        candidate_cell = previous_target.get("monthly_budget_cell")
        if not candidate_cell:
            continue
        candidate_row, candidate_col = gspread.utils.a1_to_rowcol(
            candidate_cell
        )
        candidate_value = (
            values[candidate_row - 1][candidate_col - 1]
            if len(values) >= candidate_row
            and len(values[candidate_row - 1]) >= candidate_col
            else ""
        )
        if str(candidate_value).strip():
            previous_budget = candidate_value
            previous_budget_cell = candidate_cell
            break

    if previous_budget_cell is None:
        raise SaldoPeriodValidationError(
            "La renovacion mensual no encontro ningun presupuesto anterior "
            "informado."
        )

    if not str(current_budget).strip():
        result["value_updates"].append({
            "cell": current_budget_cell,
            "value": previous_budget,
            "kind": "monthly_budget",
            "source_cell": previous_budget_cell,
        })

    next_contract_end = add_one_contract_month(contract_range["end_day"])
    if uses_custom_ranges:
        next_contract_end = max(
            next_contract_end,
            current_month_target["period_context"]["end_day"],
        )
    else:
        next_contract_end = max(next_contract_end, current_month_end)
    result["value_updates"].append({
        "cell": contract_range["end_cell"],
        "value": google_sheets_date_serial(next_contract_end),
        "kind": "contract_end",
        "date": next_contract_end,
    })
    result["note_update"] = {
        "cell": contract_range["end_cell"],
        "note": monthly_renewal_note(existing_note, renewal_month),
    }
    result["period_label"] = current_month_target["month_label"]
    result["applied"] = True
    return result


def plan_monthly_budget_carry_forward(
    values,
    period_targets,
    target_day,
    enabled=False,
):
    """Copia el ultimo presupuesto al periodo vigente sin ampliar contrato."""
    result = {
        "enabled": bool(enabled),
        "applied": False,
        "value_update": None,
        "period_label": None,
        "reason": None,
    }
    if not result["enabled"]:
        return result

    active_targets = [
        target
        for target in period_targets
        if target["period_context"]["start_day"]
        <= target_day
        <= target["period_context"]["end_day"]
    ]
    if len(active_targets) != 1:
        result["reason"] = "No hay un unico periodo vigente para copiar presupuesto."
        return result

    current_target = active_targets[0]
    current_cell = current_target.get("monthly_budget_cell")
    if not current_cell:
        result["reason"] = "El periodo vigente no tiene columna 'Presup mes'."
        return result

    current_row, current_col = gspread.utils.a1_to_rowcol(current_cell)
    current_value = (
        values[current_row - 1][current_col - 1]
        if len(values) >= current_row
        and len(values[current_row - 1]) >= current_col
        else ""
    )
    if str(current_value).strip():
        result["reason"] = "El presupuesto vigente ya esta informado."
        return result

    previous_targets = [
        target
        for target in period_targets
        if target["period_context"]["end_day"]
        < current_target["period_context"]["start_day"]
    ]
    for previous_target in reversed(previous_targets):
        source_cell = previous_target.get("monthly_budget_cell")
        if not source_cell:
            continue
        source_row, source_col = gspread.utils.a1_to_rowcol(source_cell)
        source_value = (
            values[source_row - 1][source_col - 1]
            if len(values) >= source_row
            and len(values[source_row - 1]) >= source_col
            else ""
        )
        if not str(source_value).strip():
            continue

        result["value_update"] = {
            "cell": current_cell,
            "value": source_value,
            "kind": "monthly_budget_carry_forward",
            "source_cell": source_cell,
        }
        result["period_label"] = current_target["month_label"]
        result["applied"] = True
        return result

    result["reason"] = "No existe un presupuesto anterior informado."
    return result


def contract_month_context(month_day, contract_range, target_day):
    month_end = date(
        month_day.year,
        month_day.month,
        calendar.monthrange(month_day.year, month_day.month)[1],
    )
    start_day = max(month_day, contract_range["start_day"])
    end_day = min(month_end, contract_range["end_day"])
    if start_day == month_day and end_day == month_end:
        return period_context_for_calendar_month(month_day, target_day)
    return period_context_for_range(
        {"start_day": start_day, "end_day": end_day},
        target_day,
    )


def apply_continuous_contract_months(
    values,
    period_targets,
    contract_slots,
    contract_range,
    target_day,
):
    """Completa los meses comprendidos entre el inicio y el fin del contrato."""
    if not contract_slots:
        raise SaldoPeriodValidationError(
            "El contrato continuo no tiene filas numeradas disponibles."
        )

    slots_by_sequence = {}
    for slot in contract_slots:
        slots_by_sequence.setdefault(slot["sequence_number"], slot)

    first_sequence = min(slots_by_sequence)
    first_month = contract_range["start_day"].replace(day=1)
    last_month = contract_range["end_day"].replace(day=1)
    month_count = (
        (last_month.year - first_month.year) * 12
        + last_month.month
        - first_month.month
        + 1
    )
    explicit_by_row = {target["row"]: target for target in period_targets}
    generated_targets = []
    updates = []

    for offset in range(month_count):
        month_day = add_calendar_months(first_month, offset)
        sequence_number = first_sequence + offset
        slot = slots_by_sequence.get(sequence_number)
        if not slot:
            raise SaldoPeriodValidationError(
                "No hay una fila numerada para el mes continuo "
                f"{MONTH_NAMES_ES[month_day.month]} {month_day.year}."
            )

        context = contract_month_context(month_day, contract_range, target_day)
        explicit = explicit_by_row.get(slot["row"])
        if explicit:
            explicit_start = explicit["period_context"]["start_day"]
            if (
                explicit_start.year,
                explicit_start.month,
            ) != (month_day.year, month_day.month):
                raise SaldoPeriodValidationError(
                    f"{slot['period_cell']}: el mes escrito no coincide con "
                    "la secuencia continua de Fecha inicio/Fecha Fin."
                )
            target = {**explicit, "period_context": context}
        else:
            expected_label = MONTH_NAMES_ES[month_day.month].capitalize()
            row_values = values[slot["row"] - 1]
            while len(row_values) < slot["period_col"]:
                row_values.append("")
            row_values[slot["period_col"] - 1] = expected_label
            updates.append({
                "cell": slot["period_cell"],
                "value": expected_label,
            })
            target = {
                "cell": slot["real_spend_cell"],
                "row": slot["row"],
                "col": slot["real_spend_col"],
                "group_label": slot["group_label"],
                "month_label": expected_label,
                "sequence_cell": slot["sequence_cell"],
                "period_context": context,
            }
        generated_targets.append(target)

    return generated_targets, updates


def find_month_column(values, header_row, group_start_col, group_end_col,
                      real_spend_col):
    best_col = None
    best_count = 0
    last_candidate_col = min(group_end_col, real_spend_col - 1)

    for col_index in range(group_start_col, last_candidate_col + 1):
        month_count = 0

        for row_index in range(header_row + 1, min(len(values), header_row + 20) + 1):
            row = values[row_index - 1]
            value = row[col_index - 1] if len(row) >= col_index else ""

            if parse_month_name(value):
                month_count += 1

        if month_count > best_count:
            best_col = col_index
            best_count = month_count

    if best_col:
        return best_col

    return group_start_col + 1


def saldo_month_start_day(
    group_label,
    first_month,
    month_number,
    group_start_year=None,
):
    parsed_year = parse_saldo_year_label(group_label)

    if not first_month or not month_number:
        return None

    year = group_start_year or (
        parsed_year["start_year"] if parsed_year else None
    )
    if not year:
        return None

    if month_number < first_month:
        year += 1

    return date(year, month_number, 1)


def build_saldo_target(
    item,
    real_spend_col,
    sequence_col,
    group_label,
    first_month,
    target_day,
    group_start_year=None,
):
    if item.get("range_info"):
        period_context = period_context_for_range(item["range_info"], target_day)
    else:
        month_day = saldo_month_start_day(
            group_label,
            first_month,
            item.get("month_number"),
            group_start_year,
        )
        if not month_day:
            return None

        partial_period = item.get("partial_period")
        if partial_period:
            end_day_number = partial_period.get("end_day_number") or calendar.monthrange(
                month_day.year,
                month_day.month,
            )[1]
            try:
                partial_range = {
                    "start_day": date(
                        month_day.year,
                        month_day.month,
                        partial_period["start_day_number"],
                    ),
                    "end_day": date(
                        month_day.year,
                        month_day.month,
                        end_day_number,
                    ),
                }
            except ValueError as exc:
                raise SaldoPeriodValidationError(
                    f"Periodo parcial no valido: {item['month_label']!r}."
                ) from exc
            period_context = period_context_for_range(partial_range, target_day)
        else:
            period_context = period_context_for_calendar_month(month_day, target_day)

    return {
        "cell": gspread.utils.rowcol_to_a1(item["row"], real_spend_col),
        "row": item["row"],
        "col": real_spend_col,
        "group_label": group_label,
        "month_label": item["month_label"],
        "sequence_cell": gspread.utils.rowcol_to_a1(
            item["row"],
            sequence_col,
        ),
        "period_context": period_context,
    }


def validate_saldo_period_targets(period_targets, invalid_cells, target_day=None):
    errors = []

    for cell, value in invalid_cells:
        errors.append(
            f"{cell}: periodo no reconocido ({value!r})."
        )

    ordered_targets = sorted(
        period_targets,
        key=lambda item: (
            item["period_context"]["start_day"],
            item["period_context"]["end_day"],
            item["row"],
            item["col"],
        ),
    )
    seen_periods = {}
    # Los conflictos ajenos al periodo operativo se revisaran cuando entren en vigor.
    relevant_targets = set()

    if target_day:
        current_targets = [
            target
            for target in ordered_targets
            if target["period_context"]["start_day"]
            <= target_day
            <= target["period_context"]["end_day"]
        ]
        if len(current_targets) == 1:
            current = current_targets[0]
            relevant_targets.add(id(current))
            previous_candidates = [
                target
                for target in ordered_targets
                if target["period_context"]["end_day"]
                < current["period_context"]["start_day"]
            ]
            if previous_candidates:
                relevant_targets.add(id(previous_candidates[-1]))
        elif not current_targets:
            closed_targets = [
                target
                for target in ordered_targets
                if target["period_context"]["end_day"] < target_day
            ]
            future_targets = [
                target
                for target in ordered_targets
                if target["period_context"]["start_day"] > target_day
            ]
            if closed_targets:
                relevant_targets.add(id(closed_targets[-1]))
            if future_targets:
                relevant_targets.add(id(future_targets[0]))

    for target in ordered_targets:
        context = target["period_context"]
        start_day = context["start_day"]
        end_day = context["end_day"]

        if start_day > end_day:
            errors.append(
                f"{target['cell']}: el inicio {start_day.isoformat()} es "
                f"posterior al final {end_day.isoformat()}."
            )

        period_key = (start_day, end_day)
        if (
            period_key in seen_periods
            and (
                not target_day
                or id(target) in relevant_targets
                or id(seen_periods[period_key]) in relevant_targets
            )
        ):
            errors.append(
                f"{target['cell']} y {seen_periods[period_key]['cell']}: periodo "
                f"duplicado {start_day.isoformat()} a {end_day.isoformat()}."
            )
        else:
            seen_periods[period_key] = target

    for previous, current in zip(ordered_targets, ordered_targets[1:]):
        previous_context = previous["period_context"]
        current_context = current["period_context"]

        if (
            current_context["start_day"] <= previous_context["end_day"]
            and (
                not target_day
                or id(previous) in relevant_targets
                or id(current) in relevant_targets
            )
        ):
            errors.append(
                f"{previous['cell']} y {current['cell']}: periodos "
                f"solapados ({previous_context['start_day'].isoformat()} a "
                f"{previous_context['end_day'].isoformat()} / "
                f"{current_context['start_day'].isoformat()} a "
                f"{current_context['end_day'].isoformat()})."
            )

    if not ordered_targets:
        errors.append("No hay ningun periodo valido en el bloque de saldo.")

    if errors:
        raise SaldoPeriodValidationError(
            "Validacion de periodos de saldo fallida:\n- "
            + "\n- ".join(errors)
        )

    return ordered_targets


def select_saldo_period_targets(ordered_targets, target_day):
    current_candidates = [
        target
        for target in ordered_targets
        if target["period_context"]["start_day"]
        <= target_day
        <= target["period_context"]["end_day"]
    ]

    if len(current_candidates) > 1:
        cells = ", ".join(target["cell"] for target in current_candidates)
        raise SaldoPeriodValidationError(
            f"Mas de un periodo contiene {target_day.isoformat()}: {cells}."
        )

    if current_candidates:
        current = current_candidates[0]
        previous_candidates = [
            target
            for target in ordered_targets
            if target["period_context"]["end_day"]
            < current["period_context"]["start_day"]
        ]
        previous = previous_candidates[-1] if previous_candidates else None

        return {
            "mode": "active",
            "target": current,
            "previous_target": previous,
            "next_target": None,
        }

    closed_targets = [
        target
        for target in ordered_targets
        if target["period_context"]["end_day"] < target_day
    ]
    future_targets = [
        target
        for target in ordered_targets
        if target["period_context"]["start_day"] > target_day
    ]

    return {
        "mode": "pause",
        "target": None,
        "previous_target": closed_targets[-1] if closed_targets else None,
        "next_target": future_targets[0] if future_targets else None,
    }


def find_saldo_targets(
    worksheet,
    target_day,
    continuous_contract=False,
    carry_forward_monthly_budget=False,
):
    values = get_worksheet_values(worksheet)
    contract_range = (
        find_contract_date_range(values)
        if continuous_contract
        else None
    )
    title_prefix = normalize_text("Consumo de saldo cuenta")
    title_row = None

    for row_index, row in enumerate(values, start=1):
        if any(normalize_text(value).startswith(title_prefix) for value in row):
            title_row = row_index
            break

    if not title_row:
        raise ValueError("No se encontro el bloque 'Consumo de saldo cuenta'.")

    header_row = None

    for row_index in range(title_row + 1, min(len(values), title_row + 8) + 1):
        row = values[row_index - 1]
        normalized_cells = [normalize_text(value) for value in row]

        if (
            any(value.startswith("mesesano") for value in normalized_cells)
            and "gastoreal" in normalized_cells
        ):
            header_row = row_index
            break

    if not header_row:
        raise ValueError("No se encontro la cabecera de consumo de saldo.")

    header_values = values[header_row - 1]
    group_starts = [
        col_index
        for col_index, value in enumerate(header_values, start=1)
        if normalize_text(value).startswith("mesesano")
    ]
    sequence_cells = []
    all_period_targets = []
    invalid_period_cells = []
    ignored_period_cells = []
    contract_slots = []
    period_slots = []
    base_sequence_number = None
    base_sequence_start_year = None

    for group_index, group_start_col in enumerate(group_starts):
        group_end_col = (
            group_starts[group_index + 1] - 1
            if group_index + 1 < len(group_starts)
            else len(header_values)
        )
        group_label = header_values[group_start_col - 1]
        real_spend_col = None
        monthly_budget_col = None

        for col_index in range(group_start_col, group_end_col + 1):
            value = header_values[col_index - 1] if len(header_values) >= col_index else ""
            normalized_value = normalize_text(value)
            if normalized_value == "gastoreal":
                real_spend_col = col_index
            elif normalized_value in {
                "presupmes",
                "presupuestomes",
                "presupuestomensual",
            }:
                monthly_budget_col = col_index

        if not real_spend_col:
            continue

        sequence_col = group_start_col
        # Regla estandar preferente: el periodo que corresponde a `Gasto real`
        # esta dos columnas a su izquierda. Funciona tanto para `Julio` como
        # para rangos tipo `11/06 - 10/07`.
        period_col = real_spend_col - 2

        if period_col < group_start_col:
            period_col = find_month_column(
                values,
                header_row,
                group_start_col,
                group_end_col,
                real_spend_col,
            )

        first_sequence_number = None
        for row_index in range(header_row + 1, min(len(values), header_row + 20) + 1):
            row = values[row_index - 1]
            sequence_value = (
                row[sequence_col - 1]
                if len(row) >= sequence_col
                else ""
            )
            first_sequence_number = parse_sequence_number(sequence_value)
            if first_sequence_number is not None:
                break

        parsed_group_year = parse_saldo_year_label(group_label)
        group_start_year = (
            parsed_group_year["start_year"]
            if parsed_group_year
            else None
        )

        if (
            base_sequence_number is None
            and first_sequence_number is not None
            and group_start_year is not None
        ):
            base_sequence_number = first_sequence_number
            base_sequence_start_year = group_start_year

        if (
            base_sequence_number is not None
            and base_sequence_start_year is not None
            and first_sequence_number is not None
        ):
            # La secuencia corrige encabezados fiscales copiados sin cambiar sus fechas.
            sequence_year = base_sequence_start_year + max(
                0,
                (first_sequence_number - base_sequence_number) // 12,
            )
            group_start_year = max(group_start_year or sequence_year, sequence_year)

        group_period_rows = []

        for row_index in range(header_row + 1, min(len(values), header_row + 20) + 1):
            row = values[row_index - 1]
            sequence_value = row[sequence_col - 1] if len(row) >= sequence_col else ""
            sequence_number = parse_sequence_number(sequence_value)
            if sequence_number is None:
                continue

            slot = {
                "row": row_index,
                "sequence_number": sequence_number,
                "sequence_cell": gspread.utils.rowcol_to_a1(
                    row_index,
                    sequence_col,
                ),
                "period_col": period_col,
                "period_cell": gspread.utils.rowcol_to_a1(
                    row_index,
                    period_col,
                ),
                "monthly_budget_col": monthly_budget_col,
                "monthly_budget_cell": (
                    gspread.utils.rowcol_to_a1(
                        row_index,
                        monthly_budget_col,
                    )
                    if monthly_budget_col
                    else None
                ),
                "real_spend_col": real_spend_col,
                "real_spend_cell": gspread.utils.rowcol_to_a1(
                    row_index,
                    real_spend_col,
                ),
                "group_label": group_label,
            }
            period_slots.append(slot)
            if continuous_contract:
                contract_slots.append(slot)

            period_value = row[period_col - 1] if len(row) >= period_col else ""
            if is_uncertain_period(period_value):
                ignored_period_cells.append(
                    gspread.utils.rowcol_to_a1(row_index, period_col)
                )
                continue

            partial_period = parse_partial_month_period(period_value)
            named_range_parts = parse_named_date_range_parts(period_value)
            month_number = (
                parse_month_name(period_value)
                or (partial_period or {}).get("month_number")
            )
            range_start_month = (
                date_range_start_month(period_value)
                or (named_range_parts or {}).get("start_month")
            )

            if not range_start_month and not month_number and not partial_period:
                if str(period_value).strip():
                    invalid_period_cells.append((
                        gspread.utils.rowcol_to_a1(row_index, period_col),
                        period_value,
                    ))
                continue

            group_period_rows.append({
                "row": row_index,
                "col": sequence_col,
                "month_number": month_number,
                "month_label": period_value,
                "range_info": None,
                "range_start_month": range_start_month,
                "named_range_parts": named_range_parts,
                "partial_period": partial_period,
            })

        first_period_month = next(
            (
                item["range_start_month"] or item["month_number"]
                for item in group_period_rows
                if item["range_start_month"] or item["month_number"]
            ),
            None,
        )
        parsed_group_period_rows = []

        for item in group_period_rows:
            if item["range_start_month"]:
                default_start_year = group_start_year
                if (
                    default_start_year
                    and first_period_month
                    and item["range_start_month"] < first_period_month
                ):
                    default_start_year += 1

                if item["named_range_parts"]:
                    item["range_info"] = build_named_date_range(
                        item["named_range_parts"],
                        default_start_year,
                    )
                else:
                    item["range_info"] = parse_date_range(
                        item["month_label"],
                        target_day,
                        default_start_year=default_start_year,
                    )
                if not item["range_info"]:
                    invalid_period_cells.append((
                        gspread.utils.rowcol_to_a1(item["row"], period_col),
                        item["month_label"],
                    ))
                    continue

            parsed_group_period_rows.append(item)

        group_period_rows = parsed_group_period_rows

        for item_index, item in enumerate(group_period_rows):
            sequence_cells.append({
                **item,
                "is_first": item_index == 0,
                "is_last": item_index == len(group_period_rows) - 1,
            })

        for item in group_period_rows:
            candidate = build_saldo_target(
                item,
                real_spend_col,
                sequence_col,
                group_label,
                first_period_month,
                target_day,
                group_start_year,
            )
            if candidate:
                slot = next(
                    (
                        period_slot
                        for period_slot in period_slots
                        if period_slot["row"] == item["row"]
                        and period_slot["real_spend_col"] == real_spend_col
                    ),
                    None,
                )
                if slot:
                    candidate["period_cell"] = slot["period_cell"]
                    candidate["monthly_budget_cell"] = slot[
                        "monthly_budget_cell"
                    ]
                all_period_targets.append(candidate)

    contract_updates = []
    if continuous_contract:
        all_period_targets, contract_updates = apply_continuous_contract_months(
            values,
            all_period_targets,
            contract_slots,
            contract_range,
            target_day,
        )

    ordered_targets = validate_saldo_period_targets(
        all_period_targets,
        invalid_period_cells,
        target_day,
    )
    monthly_renewal = plan_monthly_renewal_updates(
        worksheet,
        values,
        ordered_targets,
        target_day,
    )
    budget_carry_forward = plan_monthly_budget_carry_forward(
        values,
        ordered_targets,
        target_day,
        enabled=carry_forward_monthly_budget,
    )
    selected = select_saldo_period_targets(ordered_targets, target_day)
    selected["sequence_cells"] = sequence_cells
    selected["period_targets"] = ordered_targets
    selected["ignored_period_cells"] = ignored_period_cells
    selected["continuous_contract_range"] = contract_range
    selected["continuous_contract_updates"] = contract_updates
    selected["monthly_renewal"] = monthly_renewal
    selected["budget_carry_forward"] = budget_carry_forward
    selected["period_context"] = (
        selected["target"]["period_context"]
        if selected["target"]
        else None
    )

    return selected


def find_monthly_real_spend_cell(worksheet, target_day):
    target = find_saldo_targets(worksheet, target_day)["target"]
    if not target:
        raise ValueError(
            f"No existe un periodo vigente para {target_day.isoformat()}."
        )
    return target


def build_border(style):
    if style == "NONE":
        return {"style": "NONE"}

    return {
        "style": style,
        "width": 1,
        "color": {
            "red": 0,
            "green": 0,
            "blue": 0,
        },
    }


def update_current_saldo_month_marker(
    worksheet,
    saldo_targets,
    mutation_batch=None,
):
    normal_background = {
        "red": 1,
        "green": 1,
        "blue": 1,
    }
    current_background = {
        "red": 1,
        "green": 0.6,
        "blue": 0,
    }
    requests = []

    for cell in saldo_targets["sequence_cells"]:
        row_index = cell["row"]
        col_index = cell["col"]

        requests.extend([
            {
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": row_index - 1,
                        "endRowIndex": row_index,
                        "startColumnIndex": col_index - 1,
                        "endColumnIndex": col_index,
                    },
                    "cell": {
                        "note": "",
                        "userEnteredFormat": {
                            "backgroundColor": normal_background,
                            "textFormat": {
                                "bold": False,
                            },
                        },
                    },
                    "fields": (
                        "note,"
                        "userEnteredFormat.backgroundColor,"
                        "userEnteredFormat.textFormat.bold"
                    ),
                }
            },
            {
                "updateBorders": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": row_index - 1,
                        "endRowIndex": row_index,
                        "startColumnIndex": col_index - 1,
                        "endColumnIndex": col_index,
                    },
                    "top": build_border("SOLID" if cell["is_first"] else "NONE"),
                    "bottom": build_border("SOLID" if cell["is_last"] else "DOTTED"),
                    "left": build_border("SOLID"),
                    "right": build_border("DOTTED"),
                }
            },
        ])

    target = saldo_targets.get("target")
    if target:
        current_row, current_col = gspread.utils.a1_to_rowcol(
            target["sequence_cell"]
        )
        requests.extend([
            {
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": current_row - 1,
                        "endRowIndex": current_row,
                        "startColumnIndex": current_col - 1,
                        "endColumnIndex": current_col,
                    },
                    "cell": {
                        "note": "Mes en curso.",
                        "userEnteredFormat": {
                            "backgroundColor": current_background,
                            "textFormat": {
                                "bold": True,
                            },
                        },
                    },
                    "fields": (
                        "note,"
                        "userEnteredFormat.backgroundColor,"
                        "userEnteredFormat.textFormat.bold"
                    ),
                }
            },
            {
                "updateBorders": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": current_row - 1,
                        "endRowIndex": current_row,
                        "startColumnIndex": current_col - 1,
                        "endColumnIndex": current_col,
                    },
                    "top": build_border("SOLID"),
                    "bottom": build_border("SOLID"),
                    "left": build_border("SOLID"),
                    "right": build_border("SOLID"),
                }
            },
        ])

    queue_format_requests(worksheet, requests, mutation_batch)


def update_closed_real_spend_during_pause(
    worksheet,
    total_cost,
    saldo_targets,
    mutation_batch=None,
):
    closed_target = saldo_targets.get("previous_target")
    if not closed_target:
        update_current_saldo_month_marker(
            worksheet,
            saldo_targets,
            mutation_batch,
        )
        return None

    closed_cost = round(float(total_cost), 2)
    queue_values_update(
        worksheet,
        closed_target["cell"],
        [[closed_cost]],
        mutation_batch,
    )
    update_current_saldo_month_marker(
        worksheet,
        saldo_targets,
        mutation_batch,
    )

    return {
        **closed_target,
        "monthly_cost": closed_cost,
    }


def update_monthly_real_spend(
    worksheet,
    target_day,
    total_cost,
    previous_total_cost=None,
    saldo_targets=None,
    mutation_batch=None,
    update_marker=True,
):
    saldo_targets = saldo_targets or find_saldo_targets(worksheet, target_day)
    target = saldo_targets["target"]
    monthly_cost = round(float(total_cost), 2)
    previous_target = saldo_targets.get("previous_target")
    updates = [(target["cell"], monthly_cost)]
    previous_result = None

    if previous_target and previous_total_cost is not None:
        previous_cost = round(float(previous_total_cost), 2)
        updates.insert(0, (previous_target["cell"], previous_cost))
        previous_result = {
            **previous_target,
            "monthly_cost": previous_cost,
        }

    for range_name, value in updates:
        queue_values_update(
            worksheet,
            range_name,
            [[value]],
            mutation_batch,
        )
    if update_marker:
        update_current_saldo_month_marker(
            worksheet,
            saldo_targets,
            mutation_batch,
        )

    return {
        **target,
        "monthly_cost": monthly_cost,
        "previous": previous_result,
    }


def extract_year(value):
    match = re.search(r"\b(20\d{2})\b", str(value or ""))
    return int(match.group(1)) if match else None


def is_annual_control_total_cell(value):
    return normalize_text(value) == "totalestodaslascampanas"


def find_annual_control_marker_row(values):
    for row_index, row in enumerate(values, start=1):
        for value in row:
            normalized = normalize_text(value)
            if normalized.startswith("mesesanteriores") or normalized.startswith(
                "anosanteriores"
            ):
                return row_index
    raise ValueError("No se encontro el separador de anos anteriores de la ficha anual.")


def build_annual_control_block(values, period_row, header_row, total_row, marker_row):
    return {
        "values": values,
        "period_row": period_row,
        "period_col": 2,
        "header_row": header_row,
        "data_start_row": header_row + 1,
        "total_row": total_row,
        "marker_row": marker_row,
        "start_col": 2,
        "end_col": 14,
    }


def find_annual_control_total_row(values, header_row, stop_row=None):
    last_row = min(stop_row or len(values), len(values))
    for row_index in range(header_row + 1, last_row + 1):
        if any(is_annual_control_total_cell(value) for value in values[row_index - 1]):
            return row_index
    raise ValueError("No se encontro 'Totales todas las campanas' en la ficha anual.")


def find_annual_control_live_block(worksheet):
    values = get_worksheet_values(worksheet)
    title_row = None
    for row_index, row in enumerate(values, start=1):
        if any(normalize_text(value) == "mesactual" for value in row):
            title_row = row_index
            break
    if title_row is None:
        raise ValueError("No se encontro el bloque MES ACTUAL de la ficha anual.")

    period_row = title_row + 1
    header_row = title_row + 2
    marker_row = find_annual_control_marker_row(values)
    total_row = find_annual_control_total_row(values, header_row, marker_row - 1)
    return build_annual_control_block(
        values,
        period_row,
        header_row,
        total_row,
        marker_row,
    )


def find_annual_control_historical_block(worksheet, year):
    values = get_worksheet_values(worksheet)
    marker_row = find_annual_control_marker_row(values)
    for period_row in range(marker_row + 1, len(values) + 1):
        row = values[period_row - 1]
        if not any(
            normalize_text(value).startswith("periodoseleccionado")
            and extract_year(value) == year
            for value in row
        ):
            continue

        header_row = period_row + 1
        if header_row > len(values):
            raise ValueError(f"El historico anual {year} no tiene cabecera.")
        total_row = find_annual_control_total_row(values, header_row)
        return build_annual_control_block(
            values,
            period_row,
            header_row,
            total_row,
            marker_row,
        )
    return None


def annual_control_period_bounds(sem_client, year, target_day):
    start_day = date(year, 1, 1)
    first_period_start = sem_client.get("first_period_start")
    if first_period_start:
        configured_start = date.fromisoformat(first_period_start)
        if configured_start.year == year:
            start_day = configured_start

    end_day = date(year, 12, 31)
    query_end_day = min(end_day, target_day) if year == target_day.year else end_day
    return {
        "year": year,
        "start_day": start_day,
        "end_day": end_day,
        "query_end_day": query_end_day,
        "label": f"Periodo seleccionado: {year}",
    }


def fetch_annual_control_annual_rows(
    google_ads_client,
    customer_id,
    period,
):
    google_ads_service = google_ads_client.get_service("GoogleAdsService")
    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            campaign.start_date_time,
            campaign.end_date_time,
            campaign_budget.amount_micros,
            campaign_budget.total_amount_micros,
            metrics.clicks,
            metrics.impressions,
            metrics.ctr,
            metrics.average_cpc,
            metrics.average_cpm,
            metrics.cost_micros,
            metrics.conversions
        FROM campaign
        WHERE segments.date BETWEEN '{period['start_day'].isoformat()}'
            AND '{period['query_end_day'].isoformat()}'
        ORDER BY campaign.start_date_time, campaign.end_date_time, campaign.name
    """

    def read_campaigns():
        campaigns = {}
        response = google_ads_service.search_stream(
            customer_id=customer_id,
            query=query,
        )
        for batch in response:
            for result in batch.results:
                campaign = result.campaign
                budget = result.campaign_budget
                metrics = result.metrics
                start_text = campaign.start_date_time or ""
                end_text = campaign.end_date_time or ""
                start_day = (
                    date.fromisoformat(start_text[:10])
                    if len(start_text) >= 10
                    else None
                )
                has_activity = any((
                    metrics.clicks,
                    metrics.impressions,
                    metrics.cost_micros,
                    metrics.conversions,
                ))
                starts_in_period = bool(
                    start_day
                    and period["start_day"] <= start_day <= period["end_day"]
                )
                if not has_activity and not starts_in_period:
                    continue

                campaigns[str(campaign.id)] = {
                    "campaign_id": str(campaign.id),
                    "campaign_name": campaign.name,
                    "location": "",
                    "campaign_status": campaign.status.name.lower(),
                    "start_date": start_text[:10] if start_text else "",
                    "end_date": end_text[:10] if end_text else "",
                    "clicks": int(metrics.clicks),
                    "impressions": int(metrics.impressions),
                    "ctr": round(float(metrics.ctr) * 100, 6),
                    "cost": metrics.cost_micros / 1_000_000,
                    "average_cpc": metrics.average_cpc / 1_000_000,
                    "average_cpm": metrics.average_cpm / 1_000_000,
                    "daily_budget": budget.amount_micros / 1_000_000,
                    "total_budget": budget.total_amount_micros / 1_000_000,
                }
        return campaigns

    campaigns = run_google_ads_read_with_retries(
        read_campaigns,
        f"consultando la ficha anual {period['year']}",
    )
    if not campaigns:
        return []

    campaign_ids = ",".join(sorted(campaigns))
    locations_query = f"""
        SELECT
            campaign.id,
            ad_group.name
        FROM ad_group
        WHERE campaign.id IN ({campaign_ids})
        ORDER BY campaign.id, ad_group.name
    """

    def read_locations():
        locations = {campaign_id: set() for campaign_id in campaigns}
        response = google_ads_service.search_stream(
            customer_id=customer_id,
            query=locations_query,
        )
        for batch in response:
            for result in batch.results:
                campaign_id = str(result.campaign.id)
                ad_group_name = str(result.ad_group.name or "").strip()
                if campaign_id in locations and ad_group_name:
                    locations[campaign_id].add(ad_group_name)
        return locations

    locations = run_google_ads_read_with_retries(
        read_locations,
        f"consultando ubicaciones de la ficha anual {period['year']}",
    )
    for campaign_id, campaign in campaigns.items():
        campaign["location"] = " / ".join(
            sorted(locations.get(campaign_id, set()), key=normalize_text)
        )

    rows = list(campaigns.values())
    rows.sort(key=lambda item: (
        item["start_date"] or "9999-12-31",
        item["end_date"] or "9999-12-31",
        normalize_text(item["campaign_name"]),
    ))
    return rows


def prepare_annual_control_client(sem_client, worksheet, target_day):
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


def queue_annual_control_status_rules(
    worksheet,
    data_start_row,
    total_row,
    mutation_batch,
):
    metadata = worksheet.spreadsheet.fetch_sheet_metadata(params={
        "fields": "sheets(properties(sheetId),conditionalFormats)"
    })
    sheet = next(
        item
        for item in metadata.get("sheets", [])
        if item.get("properties", {}).get("sheetId") == worksheet.id
    )
    matching_indexes = []
    for index, rule in enumerate(sheet.get("conditionalFormats", [])):
        values = (
            rule.get("booleanRule", {})
            .get("condition", {})
            .get("values", [])
        )
        formulas = " ".join(
            str(value.get("userEnteredValue", ""))
            for value in values
        )
        if "LOWER(D" in formulas.upper():
            matching_indexes.append(index)

    requests = [
        {
            "deleteConditionalFormatRule": {
                "sheetId": worksheet.id,
                "index": index,
            }
        }
        for index in reversed(matching_indexes)
    ]
    status_range = {
        "sheetId": worksheet.id,
        "startRowIndex": data_start_row - 1,
        "endRowIndex": total_row - 1,
        "startColumnIndex": 3,
        "endColumnIndex": 4,
    }
    first_status_cell = f"D{data_start_row}"
    first_end_cell = f"F{data_start_row}"
    requests.extend([
        {
            "addConditionalFormatRule": {
                "index": 0,
                "rule": {
                    "ranges": [status_range],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{
                                "userEnteredValue": (
                                    f'=AND(LOWER({first_status_cell})="enabled";'
                                    f'OR({first_end_cell}="";TODAY()<={first_end_cell}))'
                                )
                            }],
                        },
                        "format": {
                            "backgroundColor": ACCOUNT_STATUS_ENABLED_COLOR,
                            "textFormat": {
                                "foregroundColor": {"red": 0, "green": 0, "blue": 0}
                            },
                        },
                    },
                },
            }
        },
        {
            "addConditionalFormatRule": {
                "index": 1,
                "rule": {
                    "ranges": [status_range],
                    "booleanRule": {
                        "condition": {
                            "type": "CUSTOM_FORMULA",
                            "values": [{
                                "userEnteredValue": (
                                    f'=AND({first_status_cell}<>"";OR('
                                    f'LOWER({first_status_cell})<>"enabled";'
                                    f'AND({first_end_cell}<>"";TODAY()>{first_end_cell})))'
                                )
                            }],
                        },
                        "format": {
                            "backgroundColor": {"red": 1, "green": 0, "blue": 0},
                            "textFormat": {
                                "foregroundColor": {"red": 0, "green": 0, "blue": 0}
                            },
                        },
                    },
                },
            }
        },
    ])
    queue_format_requests(worksheet, requests, mutation_batch)


def update_annual_control_block(
    worksheet,
    block,
    campaign_rows,
    period,
    mutation_batch,
    apply_live_status_rules=False,
):
    data_start_row = block["data_start_row"]
    total_row = block["total_row"]
    current_capacity = total_row - data_start_row
    required_capacity = len(campaign_rows) + 1
    inserted_rows = max(0, required_capacity - current_capacity)
    if inserted_rows:
        worksheet.insert_rows(
            [["" for _ in range(worksheet.col_count)] for _ in range(inserted_rows)],
            row=total_row,
            value_input_option="USER_ENTERED",
            inherit_from_before=True,
        )
        invalidate_worksheet_values_cache(worksheet)
        total_row += inserted_rows

    clear_end_row = total_row - 1
    width = block["end_col"] - block["start_col"] + 1
    data_matrix = [
        ["" for _ in range(width)]
        for _ in range(clear_end_row - data_start_row + 1)
    ]
    for index, campaign in enumerate(campaign_rows):
        data_matrix[index] = [
            campaign["campaign_name"],
            campaign["location"],
            campaign["campaign_status"],
            campaign["start_date"],
            campaign["end_date"],
            campaign["clicks"],
            campaign["impressions"],
            campaign["ctr"],
            campaign["cost"],
            campaign["average_cpc"],
            campaign["average_cpm"],
            campaign["daily_budget"],
            campaign["total_budget"],
        ]

    queue_values_update(
        worksheet,
        gspread.utils.rowcol_to_a1(block["period_row"], block["period_col"]),
        [[period["label"]]],
        mutation_batch,
    )
    queue_values_update(
        worksheet,
        f"B{block['header_row']}:N{block['header_row']}",
        [ANNUAL_CONTROL_HEADERS],
        mutation_batch,
    )
    queue_values_update(
        worksheet,
        f"B{data_start_row}:N{clear_end_row}",
        data_matrix,
        mutation_batch,
    )
    queue_values_update(
        worksheet,
        f"E{total_row}",
        [["Totales todas las campa\u00f1as"]],
        mutation_batch,
    )

    data_end_row = max(data_start_row, data_start_row + len(campaign_rows) - 1)
    total_values = [[
        f"=SUM(G{data_start_row}:G{data_end_row})",
        f"=SUM(H{data_start_row}:H{data_end_row})",
        f'=IFERROR(G{total_row}/H{total_row}*100;"")',
        f"=SUM(J{data_start_row}:J{data_end_row})",
        f'=IFERROR(J{total_row}/G{total_row};"")',
        f'=IFERROR(J{total_row}/H{total_row}*1000;"")',
        f"=SUM(M{data_start_row}:M{data_end_row})",
        f"=SUM(N{data_start_row}:N{data_end_row})",
    ]]
    queue_values_update(
        worksheet,
        f"G{total_row}:N{total_row}",
        total_values,
        mutation_batch,
    )

    number_formats = {
        "E": {"type": "DATE", "pattern": "yyyy-mm-dd"},
        "F": {"type": "DATE", "pattern": "yyyy-mm-dd"},
        "G": {"type": "NUMBER", "pattern": "#,##0"},
        "H": {"type": "NUMBER", "pattern": "#,##0"},
        "I": {"type": "NUMBER", "pattern": OPTIONAL_DECIMAL_FORMAT},
        "J": {"type": "CURRENCY", "pattern": "#,##0.##\\ [$\u20ac-1]"},
        "K": {"type": "CURRENCY", "pattern": "#,##0.##\\ [$\u20ac-1]"},
        "L": {"type": "CURRENCY", "pattern": "#,##0.##\\ [$\u20ac-1]"},
        "M": {"type": "CURRENCY", "pattern": "#,##0.##\\ [$\u20ac-1]"},
        "N": {"type": "CURRENCY", "pattern": "#,##0.##\\ [$\u20ac-1]"},
    }
    format_requests = []
    for column_letter, number_format in number_formats.items():
        column_index = gspread.utils.a1_to_rowcol(f"{column_letter}1")[1]
        format_requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": data_start_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": column_index - 1,
                    "endColumnIndex": column_index,
                },
                "cell": {"userEnteredFormat": {"numberFormat": number_format}},
                "fields": "userEnteredFormat.numberFormat",
            }
        })
    queue_format_requests(worksheet, format_requests, mutation_batch)

    if apply_live_status_rules:
        queue_annual_control_status_rules(
            worksheet,
            data_start_row,
            total_row,
            mutation_batch,
        )

    return {
        "period_cell": f"B{block['period_row']}",
        "write_range": f"B{data_start_row}:N{data_start_row + len(campaign_rows) - 1}",
        "clear_range": f"B{data_start_row}:N{clear_end_row}",
        "total_row": total_row,
        "inserted_rows": inserted_rows,
        "campaign_count": len(campaign_rows),
    }


def create_annual_control_historical_block(worksheet, live_block):
    values = get_worksheet_values(worksheet)
    marker_row = find_annual_control_marker_row(values)
    last_total_row = marker_row
    for row_index in range(marker_row + 1, len(values) + 1):
        if any(is_annual_control_total_cell(value) for value in values[row_index - 1]):
            last_total_row = row_index

    destination_start_row = last_total_row + 2
    source_start_row = live_block["period_row"]
    source_end_row = live_block["total_row"]
    destination_end_row = copy_range_to_rows(
        worksheet,
        source_start_row,
        source_end_row,
        live_block["start_col"],
        live_block["end_col"],
        destination_start_row,
    )
    refreshed_values = get_worksheet_values(worksheet, refresh=True)
    return build_annual_control_block(
        refreshed_values,
        destination_start_row,
        destination_start_row + 1,
        destination_end_row,
        marker_row,
    )


def upsert_annual_control_historical_year(
    worksheet,
    live_block,
    campaign_rows,
    period,
    mutation_batch,
):
    historical_block = find_annual_control_historical_block(
        worksheet,
        period["year"],
    )
    created = historical_block is None
    if created:
        historical_block = create_annual_control_historical_block(
            worksheet,
            live_block,
        )
    result = update_annual_control_block(
        worksheet,
        historical_block,
        campaign_rows,
        period,
        mutation_batch,
    )
    return {"created": created, **result}


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


def find_microsoft_annual_area(values):
    """Localiza el bloque MICROSOFT ADS situado antes de MES ACTUAL."""
    mes_actual_row = None
    for row_index, row in enumerate(values, start=1):
        if any(is_month_current_cell(value) for value in row):
            mes_actual_row = row_index
            break

    if mes_actual_row is None:
        raise ValueError("No se encontro la fila MES ACTUAL.")

    candidates = []
    for row_index in range(1, mes_actual_row):
        row = values[row_index - 1]
        for col_index, value in enumerate(row, start=1):
            if normalize_text(value).startswith("microsoftads"):
                candidates.append((row_index, col_index))

    if not candidates:
        raise ValueError(
            "No se encontro el bloque MICROSOFT ADS dentro de "
            "HISTORICO/URL/ACCESOS."
        )

    # El bloque vigente siempre es el situado mas a la izquierda. Desde 2027
    # coexistira con archivos anuales que empiezan en R, AE, AR, etc.
    title_row, start_col = min(candidates, key=lambda item: item[1])
    return {
        "title_row": title_row,
        "start_col": start_col,
        "mes_actual_row": mes_actual_row,
    }


def microsoft_annual_columns(start_col):
    return {
        key: start_col + offset
        for offset, key in enumerate(STANDARD_HEADER_BY_KEY)
    }


def microsoft_archive_start_col(year):
    return (
        MICROSOFT_ANNUAL_ARCHIVE_FIRST_COLUMN
        + (year - MICROSOFT_ANNUAL_BASE_YEAR)
        * MICROSOFT_ANNUAL_ARCHIVE_COLUMN_STEP
    )


def ensure_worksheet_columns(worksheet, required_end_col):
    if worksheet.col_count >= required_end_col:
        return 0

    added = required_end_col - worksheet.col_count
    worksheet.add_cols(added)
    invalidate_worksheet_values_cache(worksheet)
    return added


def queue_microsoft_annual_table(
    worksheet,
    year,
    campaign_rows,
    title_row,
    start_col,
    clear_end_row,
    mutation_batch,
):
    columns = microsoft_annual_columns(start_col)
    end_col = start_col + len(columns) - 1
    header_row = title_row + 1
    data_start_row = header_row + 1
    total_row = data_start_row + len(campaign_rows) + 1
    clear_end_row = max(clear_end_row, total_row)

    title_cell = gspread.utils.rowcol_to_a1(title_row, start_col)
    header_range = (
        f"{gspread.utils.rowcol_to_a1(header_row, start_col)}:"
        f"{gspread.utils.rowcol_to_a1(header_row, end_col)}"
    )
    body_range = (
        f"{gspread.utils.rowcol_to_a1(data_start_row, start_col)}:"
        f"{gspread.utils.rowcol_to_a1(clear_end_row, end_col)}"
    )

    queue_values_update(
        worksheet,
        title_cell,
        [[f"MICROSOFT ADS - {year}"]],
        mutation_batch,
    )
    queue_values_update(
        worksheet,
        header_range,
        [build_standard_header_slice(columns, start_col, end_col)],
        mutation_batch,
    )

    width = end_col - start_col + 1
    body_matrix = [
        ["" for _ in range(width)]
        for _ in range(clear_end_row - data_start_row + 1)
    ]
    for index, output_row in enumerate(
        build_output_matrix(campaign_rows, columns, start_col, end_col)
    ):
        body_matrix[index] = output_row
    queue_values_update(
        worksheet,
        body_range,
        body_matrix,
        mutation_batch,
    )

    update_total_formulas(
        worksheet,
        columns,
        start_col,
        end_col,
        data_start_row,
        len(campaign_rows),
        total_row,
        mutation_batch,
        apply_formats=False,
        conversion_total=sum(
            parse_sheet_number(row.get("conversions", 0))
            for row in campaign_rows
        ),
        metric_totals=summarize_campaign_metrics(campaign_rows),
    )

    requests = [
        {
            "unmergeCells": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": title_row - 1,
                    "endRowIndex": title_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                }
            }
        },
        {
            "mergeCells": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": title_row - 1,
                    "endRowIndex": title_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "mergeType": "MERGE_ALL",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": header_row - 1,
                    "endRowIndex": clear_end_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": title_row - 1,
                    "endRowIndex": title_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {
                            "red": 0,
                            "green": 0.47,
                            "blue": 0.83,
                        },
                        "horizontalAlignment": "CENTER",
                        "verticalAlignment": "MIDDLE",
                        "textFormat": {
                            "bold": True,
                            "foregroundColor": {
                                "red": 1,
                                "green": 1,
                                "blue": 1,
                            },
                        },
                    }
                },
                "fields": "userEnteredFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": header_row - 1,
                    "endRowIndex": header_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {
                            "red": 1,
                            "green": 0.6,
                            "blue": 0,
                        },
                        "horizontalAlignment": "LEFT",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "CLIP",
                        "textFormat": {"bold": True},
                    }
                },
                "fields": "userEnteredFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": columns["ctr"] - 1,
                    "endColumnIndex": columns["average_cpc"],
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {
                            "red": 0.96,
                            "green": 0.76,
                            "blue": 0.56,
                        },
                        "horizontalAlignment": "CENTER",
                        "textFormat": {"bold": True},
                    }
                },
                "fields": "userEnteredFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": columns["clicks"] - 1,
                    "endColumnIndex": columns["impressions"],
                },
                "cell": {
                    "userEnteredFormat": {
                        "textFormat": {"bold": True},
                    }
                },
                "fields": "userEnteredFormat.textFormat.bold",
            }
        },
    ]

    column_widths = [220, 110, 80, 75, 80, 95, 95, 115, 95, 125, 135, 110]
    for offset, pixel_size in enumerate(column_widths):
        requests.append({
            "updateDimensionProperties": {
                "range": {
                    "sheetId": worksheet.id,
                    "dimension": "COLUMNS",
                    "startIndex": start_col - 1 + offset,
                    "endIndex": start_col + offset,
                },
                "properties": {"pixelSize": pixel_size},
                "fields": "pixelSize",
            }
        })

    queue_format_requests(worksheet, requests, mutation_batch)
    apply_live_block_borders(
        worksheet,
        columns,
        start_col,
        end_col,
        header_row,
        data_start_row,
        len(campaign_rows),
        total_row,
        mutation_batch,
    )
    apply_campaign_value_formats(
        worksheet,
        columns,
        data_start_row,
        campaign_rows,
        total_row - 1,
        mutation_batch,
        apply_number_formats=True,
        apply_status_formats=True,
    )
    apply_total_value_formats(
        worksheet,
        columns,
        total_row,
        mutation_batch,
        metric_values=summarize_campaign_metrics(campaign_rows),
    )

    return {
        "year": year,
        "campaign_count": len(campaign_rows),
        "total_cost": round(sum(row["raw_cost"] for row in campaign_rows), 2),
        "total_row": total_row,
        "range": (
            f"{gspread.utils.rowcol_to_a1(title_row, start_col)}:"
            f"{gspread.utils.rowcol_to_a1(total_row, end_col)}"
        ),
    }


def update_microsoft_annual_control(
    worksheet,
    annual_config,
    microsoft_ads,
    mutation_batch,
):
    title_row = annual_config["title_row"]
    start_col = annual_config["start_col"]
    target_year = annual_config["target_year"]
    archives = []

    for archive_year in annual_config["archive_years"]:
        archive_start_col = microsoft_archive_start_col(archive_year)
        archive_end_col = archive_start_col + len(STANDARD_HEADER_BY_KEY) - 1
        ensure_worksheet_columns(worksheet, archive_end_col)
        archive_rows = microsoft_ads["annual_archive_rows"].get(
            archive_year,
            [],
        )
        archive_total_row = title_row + 3 + len(archive_rows)
        archives.append(queue_microsoft_annual_table(
            worksheet,
            archive_year,
            archive_rows,
            title_row,
            archive_start_col,
            archive_total_row,
            mutation_batch,
        ))

    values = get_worksheet_values(worksheet, refresh=True)
    annual_area = find_microsoft_annual_area(values)
    mes_actual_row = annual_area["mes_actual_row"]
    campaign_rows = microsoft_ads["annual_current_rows"]
    desired_total_row = title_row + 3 + len(campaign_rows)
    desired_mes_actual_row = desired_total_row + 2
    inserted_rows = 0
    if mes_actual_row < desired_mes_actual_row:
        inserted_rows = desired_mes_actual_row - mes_actual_row
        worksheet.insert_rows(
            [["" for _ in range(worksheet.col_count)] for _ in range(inserted_rows)],
            row=mes_actual_row,
            value_input_option="USER_ENTERED",
            inherit_from_before=True,
        )
        invalidate_worksheet_values_cache(worksheet)
        mes_actual_row += inserted_rows

    current_result = queue_microsoft_annual_table(
        worksheet,
        target_year,
        campaign_rows,
        title_row,
        start_col,
        mes_actual_row - 1,
        mutation_batch,
    )
    current_result["inserted_rows"] = inserted_rows
    current_result["archives"] = archives
    return current_result


def prepare_sem_client(sem_client, worksheet, target_day):
    if sem_client.get("handler") == ANNUAL_CONTROL_HANDLER:
        return prepare_annual_control_client(
            sem_client,
            worksheet,
            target_day,
        )

    ads_client_config = load_client_config_by_mcc(sem_client["mcc_id"])
    customer_ids = get_sem_customer_ids(sem_client)
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
        "mcc_id": normalize_customer_id(ads_client_config["mcc_id"]),
        "customer_ids": customer_ids,
        "period_contexts": period_contexts,
        "target_day": target_day,
    }

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

    return prepared


def fetch_prepared_sem_client_data(prepared):
    if prepared["sem_client"].get("handler") == ANNUAL_CONTROL_HANDLER:
        return fetch_annual_control_client_data(prepared)

    started_at = time.perf_counter()
    google_ads_client = get_thread_google_ads_client(
        prepared["ads_client_config"]
    )
    account_results = {}
    for customer_id in prepared["customer_ids"]:
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
    result["elapsed_seconds"] = time.perf_counter() - started_at
    return result


def process_sem_client(prepared, ads_data, mutation_batch):
    sem_client = prepared["sem_client"]
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
    for update in monthly_renewal.get("value_updates", []):
        queue_values_update(
            worksheet,
            update["cell"],
            [[update["value"]]],
            mutation_batch,
        )
    if monthly_renewal.get("note_update"):
        note_update = monthly_renewal["note_update"]
        queue_cell_note(
            worksheet,
            note_update["cell"],
            note_update["note"],
            mutation_batch,
        )
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
    annual_result = None
    if microsoft_ads and prepared.get("microsoft_annual"):
        annual_result = update_microsoft_annual_control(
            worksheet,
            prepared["microsoft_annual"],
            microsoft_ads,
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
    print(f"{customer_label}: {', '.join(customer_ids)}")
    print(f"MCC usado: {prepared['mcc_id']}")
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
        "Lectura Google Ads completada en: "
        f"{ads_data['elapsed_seconds']:.2f} s"
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
            update_and_log_account_statuses(
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

        update_and_log_account_statuses(
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
            force_formats=bool(sem_client.get("microsoft_ads_account_id")),
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
    google_total_cost = round(
        sum(
            row["raw_cost"]
            for row in campaign_rows
            if row.get("source_customer_id") != "microsoft"
        ),
        2,
    )
    microsoft_current_cost = round(sum(
        row["raw_cost"]
        for row in campaign_rows
        if row.get("source_customer_id") == "microsoft"
    ), 2)
    total_cost = round(google_total_cost + microsoft_current_cost, 2)
    print(f"Campanas encontradas: {len(campaign_rows)}")
    print(f"Total coste Google Ads detectado: {google_total_cost:.2f} EUR")
    if microsoft_ads:
        print(
            "Coste Microsoft Ads incluido en filas y saldo: "
            f"{microsoft_current_cost:.2f} {microsoft_ads['currency']}"
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
            force_formats=bool(sem_client.get("microsoft_ads_account_id")),
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

    update_and_log_account_statuses(
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
            if sem_client.get("handler") == ANNUAL_CONTROL_HANDLER:
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
                flush_result = format_batch.flush()
                print(
                    "Formatos historicos normalizados: "
                    f"{', '.join(pending_format_names)} "
                    f"({flush_result['format_requests']} operaciones)."
                )
                pending_format_names = []

        if pending_format_names:
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
            prepared_clients.append(
                prepare_sem_client(sem_client, worksheet, target_day)
            )
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
            flush_result = mutation_batch.flush()
            print(
                "Lote escrito correctamente: "
                f"{len(pending_batch_names)} fichas, "
                f"{flush_result['value_ranges']} rangos de valores y "
                f"{flush_result['format_requests']} operaciones de formato."
            )
            pending_batch_names = []

    if pending_batch_names:
        flush_result = mutation_batch.flush()
        print(
            "Lote final escrito correctamente: "
            f"{len(pending_batch_names)} fichas, "
            f"{flush_result['value_ranges']} rangos de valores y "
            f"{flush_result['format_requests']} operaciones de formato."
        )

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


if __name__ == "__main__":
    main()
