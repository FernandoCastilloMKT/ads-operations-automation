"""Static application constants; importing this module performs no I/O."""

from datetime import date
from zoneinfo import ZoneInfo
import re

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

OPENAI_ANNUAL_PERIOD_KEY = "openai_annual_current"

OPENAI_ANNUAL_ARCHIVE_KEY_PREFIX = "openai_annual_archive_"

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

OPTIONAL_DECIMAL_FORMAT = "#,##0.00"

OPTIONAL_PERCENT_FORMAT = "0.00%"

OPTIONAL_CURRENCY_FORMAT = "[$\u20ac]#,##0.00"

INTEGER_CURRENCY_FORMAT = "[$\u20ac]#,##0"

TOTAL_CURRENCY_FORMAT = OPTIONAL_CURRENCY_FORMAT

INTEGER_NUMBER_FORMAT = "#,##0"

INTEGER_PERCENT_FORMAT = "0%"

GOOGLE_SHEETS_DATE_EPOCH = date(1899, 12, 30)

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

STANDARD_HEADER_BY_KEY = {
    "campaign_name": "Campa\u00f1a",
    "campaign_status": "Estado",
    "clicks": "Clics",
    "ctr": "CTR",
    "average_cpc": "CPC",
    "cost": "Coste",
    "conversions": "Conversiones",
    "cost_per_conversion": "Coste/Conver",
    "impressions": "Impresiones",
    "top_impression_share": "% Impre. Top 3",
    "absolute_top_impression_share": "% Impre. Top 1",
    "daily_budget": "Saldo/d\u00eda",
}

EXPECTED_HEADERS = list(STANDARD_HEADER_BY_KEY.values())

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
        "Campa\u00f1a",
        "Nombre de la campana",
        "Nombre de la campaña",
        "Campaign name",
    ],
    "campaign_status": [
        "Plataforma",
        "Estado",
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
        "Coste/Conver",
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
        "% Impre. Top 3",
    ],
    "absolute_top_impression_share": [
        "% Impre. Top 1",
        "Porcentaje de impresiones en la parte superior absoluta",
        "Absolute top impression percentage",
    ],
    "daily_budget": ["Saldo/d\u00eda", "Presupuesto diario", "Presupuesto", "Daily budget", "Budget"],
}

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

EXTERNAL_ADS_SOURCE_IDS = {"microsoft", "openai", "meta", "linkedin"}
