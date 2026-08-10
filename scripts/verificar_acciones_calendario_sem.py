import argparse
import base64
import hashlib
import html
import json
import logging
import os
import re
import sys
import time
import unicodedata
from dataclasses import dataclass
from datetime import date, datetime, time as datetime_time, timedelta
from email.message import EmailMessage
from pathlib import Path
from zoneinfo import ZoneInfo

import gspread
from dotenv import load_dotenv
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
from google.auth.transport.requests import Request
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as UserCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

from runtime_config import (
    load_consumption_runtime_config,
    load_sem_runtime_config,
    normalize_config_key,
)

BASE_DIR = Path(__file__).resolve().parent.parent
ENV_PATH = BASE_DIR / ".env"
load_dotenv(ENV_PATH, override=True)
SHEETS_CREDENTIALS_PATH = (
    BASE_DIR / "credentials" / "google-sheets-service-account.json"
)
WORKSPACE_CLIENT_PATH = (
    BASE_DIR / "credentials" / "google-workspace-oauth-client.json"
)
WORKSPACE_TOKEN_PATH = (
    BASE_DIR / "credentials" / "google-workspace-token.json"
)
STATE_PATH = BASE_DIR / "logs" / "verificar_acciones_calendario_sem_state.json"

SEM_RUNTIME_CONFIG = load_sem_runtime_config()
CONTROL_SEM_SPREADSHEET_ID = SEM_RUNTIME_CONFIG[
    "control_sem_spreadsheet_id"
]
CONTROL_SEM_WORKSHEET_NAME = SEM_RUNTIME_CONFIG.get(
    "control_sem_worksheet_name",
    "Vista Global",
)
EMAIL_RECIPIENT = SEM_RUNTIME_CONFIG["calendar_email_recipient"]
EMAIL_SUBJECT = "Alerta SEM: acción de calendario no cumplida"
SPAIN_TIMEZONE = ZoneInfo("Europe/Madrid")
MAX_GOOGLE_ADS_RETRIES = 3
MAX_WORKSPACE_API_RETRIES = 3
RETRY_DELAY_SECONDS = 4
STATE_VERSION = 2
STATE_RETENTION_DAYS = 120
CALENDAR_IDS_ENV = "GOOGLE_CALENDAR_IDS"

WORKSPACE_SCOPES = [
    "https://www.googleapis.com/auth/calendar.readonly",
    "https://www.googleapis.com/auth/gmail.send",
]
SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]

MONTHS_ES = {
    "enero": 1,
    "febrero": 2,
    "marzo": 3,
    "abril": 4,
    "mayo": 5,
    "junio": 6,
    "julio": 7,
    "agosto": 8,
    "septiembre": 9,
    "setiembre": 9,
    "octubre": 10,
    "noviembre": 11,
    "diciembre": 12,
}
MONTH_PATTERN = "|".join(MONTHS_ES)
ACTION_PATTERNS = (
    ("reactivate", re.compile(r"\b(?:se\s+)?reactiva(?:r[áa]?)?\b", re.I)),
    ("pause", re.compile(r"\b(?:se\s+)?pausa(?:r[áa]?)?\b", re.I)),
)
STOP_SECTION_NAMES = {
    "clientes firmados sin comenzar",
    "stand by",
    "standby",
    "natalia",
}
CALENDAR_CLIENT_EXCEPTIONS_AFTER_STOP = {
    normalize_config_key(value)
    for value in SEM_RUNTIME_CONFIG.get(
        "calendar_client_exceptions_after_stop",
        [],
    )
}
CALENDAR_ACCESS_ROLES = {"owner", "writer", "reader"}

CALENDAR_ONLY_MCC_CONFIGS = tuple(
    {
        **config,
        "sub_mcc_ids": config.get("sub_mcc_ids", []),
    }
    for config in SEM_RUNTIME_CONFIG.get(
        "sem_only_mcc_configs",
        {},
    ).values()
)

logging.getLogger("google.ads.googleads").setLevel(logging.CRITICAL)


@dataclass(frozen=True)
class ParsedAction:
    client_name: str
    action: str
    title_date_kind: str | None = None
    title_date_value: str | None = None


@dataclass(frozen=True)
class CalendarEvent:
    calendar_id: str
    calendar_name: str
    event_id: str
    title: str
    event_date: date
    html_link: str = ""


@dataclass(frozen=True)
class ClientRecord:
    row_number: int
    client_name: str
    account_id: str
    commercial: str = ""
    technician: str = ""
    sheet_mcc: str = ""
    status: str = ""


@dataclass
class Incident:
    event: CalendarEvent
    client_name: str
    action: str
    result: str
    incident_type: str = "verification_error"
    account_id: str = ""
    mcc_name: str = ""
    mcc_id: str = ""
    commercial: str = ""
    technician: str = ""
    enabled_campaigns: list[str] | None = None

    def dedupe_key(self):
        raw_key = "|".join([
            self.event.calendar_id,
            self.event.event_id,
            self.event.event_date.isoformat(),
            normalize_text(self.client_name),
            self.action,
            normalize_customer_id(self.account_id) or "sin-id",
            self.incident_type,
        ])
        return hashlib.sha256(raw_key.encode("utf-8")).hexdigest()


class ActionTitleError(ValueError):
    pass


def parse_args():
    parser = argparse.ArgumentParser(
        description=(
            "Verifica acciones SEM de Google Calendar contra Google Ads."
        )
    )
    parser.add_argument(
        "--fecha",
        help="Fecha de eventos a revisar en YYYY-MM-DD. Por defecto, ayer.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Construye las alertas sin enviar correo ni guardar anti-spam.",
    )
    parser.add_argument(
        "--calendar-id",
        action="append",
        default=[],
        help=(
            "Limita la lectura a un Calendar ID. Se puede repetir. "
            "Sin este argumento se revisan todos los calendarios legibles."
        ),
    )
    return parser.parse_args()


def normalize_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(
        character
        for character in normalized
        if not unicodedata.combining(character)
    )
    return re.sub(r"\s+", " ", ascii_text.lower()).strip()


def normalize_header(value):
    return re.sub(r"[^a-z0-9]+", "", normalize_text(value))


def normalize_customer_id(value):
    return "".join(character for character in str(value or "") if character.isdigit())


def resolve_calendar_ids(explicit_ids=None, environment_value=None):
    explicit = [
        str(calendar_id).strip()
        for calendar_id in explicit_ids or []
        if str(calendar_id).strip()
    ]
    if explicit:
        return list(dict.fromkeys(explicit))

    raw_value = (
        os.getenv(CALENDAR_IDS_ENV, "")
        if environment_value is None
        else environment_value
    )
    configured = [
        value.strip()
        for value in re.split(r"[,;\r\n]+", str(raw_value or ""))
        if value.strip()
    ]
    return list(dict.fromkeys(configured))


def execute_workspace_request(request):
    return request.execute(num_retries=MAX_WORKSPACE_API_RETRIES)


def yesterday_in_spain():
    return datetime.now(SPAIN_TIMEZONE).date() - timedelta(days=1)


def parse_target_date(raw_value):
    if not raw_value:
        return yesterday_in_spain()

    try:
        return date.fromisoformat(raw_value)
    except ValueError as exc:
        raise SystemExit("--fecha debe usar el formato YYYY-MM-DD.") from exc


def parse_title_date(tail, event_date):
    clean_tail = normalize_text(tail).strip(" .,-")
    clean_tail = re.sub(r"^el\s+", "", clean_tail)
    if not clean_tail:
        return None, None

    month_match = re.fullmatch(rf"({MONTH_PATTERN})", clean_tail)
    if month_match:
        month = MONTHS_ES[month_match.group(1)]
        if event_date.month != month:
            raise ActionTitleError(
                "El mes indicado en el título no coincide con la fecha del evento."
            )
        return "month", f"{month:02d}"

    words_match = re.fullmatch(
        rf"(\d{{1,2}})\s+(?:de\s+)?({MONTH_PATTERN})"
        rf"(?:\s+(?:de\s+)?(\d{{4}}))?",
        clean_tail,
    )
    if words_match:
        day = int(words_match.group(1))
        month = MONTHS_ES[words_match.group(2)]
        year = int(words_match.group(3) or event_date.year)
        try:
            title_date = date(year, month, day)
        except ValueError as exc:
            raise ActionTitleError("La fecha escrita en el título no es válida.") from exc
        if title_date != event_date:
            raise ActionTitleError(
                "La fecha indicada en el título no coincide con la fecha del evento."
            )
        return "date", title_date.isoformat()

    numeric_match = re.fullmatch(
        r"(\d{1,2})/(\d{1,2})(?:/(\d{2}|\d{4}))?",
        clean_tail,
    )
    if numeric_match:
        day = int(numeric_match.group(1))
        month = int(numeric_match.group(2))
        raw_year = numeric_match.group(3)
        if raw_year is None:
            year = event_date.year
        elif len(raw_year) == 2:
            year = 2000 + int(raw_year)
        else:
            year = int(raw_year)
        try:
            title_date = date(year, month, day)
        except ValueError as exc:
            raise ActionTitleError("La fecha escrita en el título no es válida.") from exc
        if title_date != event_date:
            raise ActionTitleError(
                "La fecha indicada en el título no coincide con la fecha del evento."
            )
        return "date", title_date.isoformat()

    raise ActionTitleError(
        "No se reconoce el texto situado después de la acción SEM."
    )


def parse_action_title(title, event_date):
    clean_title = re.sub(r"^\s*\[\s*SEM\s*\]\s*", "", str(title or ""), flags=re.I)
    prefixed = clean_title != str(title or "")
    clean_title = clean_title.replace("–", "-").replace("—", "-").strip()

    action_match = None
    action = None
    for candidate_action, pattern in ACTION_PATTERNS:
        match = pattern.search(clean_title)
        if match and (action_match is None or match.start() < action_match.start()):
            action_match = match
            action = candidate_action

    if action_match is None:
        if prefixed:
            raise ActionTitleError(
                "El evento [SEM] no contiene una acción de pausa o reactivación."
            )
        return None

    client_name = clean_title[:action_match.start()].strip(" :-")
    if not client_name:
        raise ActionTitleError("No se pudo extraer el cliente del título.")

    tail = clean_title[action_match.end():]
    title_date_kind, title_date_value = parse_title_date(tail, event_date)
    return ParsedAction(
        client_name=client_name,
        action=action,
        title_date_kind=title_date_kind,
        title_date_value=title_date_value,
    )


def load_workspace_credentials(interactive=True):
    credentials = None
    token_json = os.getenv("GOOGLE_WORKSPACE_TOKEN_JSON")
    if token_json:
        credentials = UserCredentials.from_authorized_user_info(
            json.loads(token_json),
            WORKSPACE_SCOPES,
        )
    elif WORKSPACE_TOKEN_PATH.exists():
        credentials = UserCredentials.from_authorized_user_file(
            WORKSPACE_TOKEN_PATH,
            WORKSPACE_SCOPES,
        )

    if credentials and credentials.expired and credentials.refresh_token:
        credentials.refresh(Request())

    if not credentials or not credentials.valid or not credentials.has_scopes(WORKSPACE_SCOPES):
        if not interactive:
            raise RuntimeError("No hay credenciales OAuth validas de Google Workspace.")
        if not WORKSPACE_CLIENT_PATH.exists():
            raise FileNotFoundError(
                f"Falta la credencial OAuth: {WORKSPACE_CLIENT_PATH}"
            )
        flow = InstalledAppFlow.from_client_secrets_file(
            WORKSPACE_CLIENT_PATH,
            WORKSPACE_SCOPES,
        )
        credentials = flow.run_local_server(
            host="localhost",
            port=0,
            authorization_prompt_message=(
                "Abriendo Google para autorizar Calendar y Gmail..."
            ),
            success_message=(
                "Autorizacion completada. Puedes cerrar esta ventana."
            ),
            open_browser=True,
        )

    if not token_json:
        WORKSPACE_TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)
        WORKSPACE_TOKEN_PATH.write_text(credentials.to_json(), encoding="utf-8")

    return credentials


def event_start_date(event):
    start = event.get("start", {})
    if start.get("date"):
        return date.fromisoformat(start["date"])

    raw_datetime = start.get("dateTime")
    if not raw_datetime:
        raise ValueError("El evento no tiene fecha de inicio.")
    parsed = datetime.fromisoformat(raw_datetime.replace("Z", "+00:00"))
    return parsed.astimezone(SPAIN_TIMEZONE).date()


def should_skip_calendar(calendar_entry):
    calendar_id = normalize_text(calendar_entry.get("id", ""))
    if calendar_entry.get("deleted"):
        return True
    if calendar_entry.get("accessRole") not in CALENDAR_ACCESS_ROLES:
        return True
    return (
        "#holiday@group.v.calendar.google.com" in calendar_id
        or "addressbook#contacts@group.v.calendar.google.com" in calendar_id
    )


def list_readable_calendars(calendar_service, explicit_ids=None):
    if explicit_ids:
        return [
            {
                "id": calendar_id,
                "summary": calendar_id,
                "accessRole": "reader",
            }
            for calendar_id in dict.fromkeys(explicit_ids)
        ]

    calendars = []
    page_token = None
    while True:
        response = execute_workspace_request(calendar_service.calendarList().list(
            pageToken=page_token,
            showDeleted=False,
            showHidden=False,
            minAccessRole="reader",
        ))
        calendars.extend(
            entry
            for entry in response.get("items", [])
            if not should_skip_calendar(entry)
        )
        page_token = response.get("nextPageToken")
        if not page_token:
            break
    return calendars


def list_events_for_date(calendar_service, target_date, explicit_ids=None):
    day_start = datetime.combine(
        target_date,
        datetime_time.min,
        tzinfo=SPAIN_TIMEZONE,
    )
    day_end = day_start + timedelta(days=1)
    calendars = list_readable_calendars(calendar_service, explicit_ids)
    print(f"Calendarios legibles revisados: {len(calendars)}")
    events = []

    for calendar_entry in calendars:
        calendar_id = calendar_entry["id"]
        calendar_name = calendar_entry.get("summary") or calendar_id
        page_token = None
        calendar_events = 0
        while True:
            response = execute_workspace_request(calendar_service.events().list(
                calendarId=calendar_id,
                timeMin=day_start.isoformat(),
                timeMax=day_end.isoformat(),
                singleEvents=True,
                showDeleted=False,
                orderBy="startTime",
                pageToken=page_token,
                fields=(
                    "nextPageToken,items(id,summary,status,start,eventType,htmlLink)"
                ),
            ))
            for item in response.get("items", []):
                if item.get("status") == "cancelled":
                    continue
                if item.get("eventType", "default") != "default":
                    continue
                try:
                    item_date = event_start_date(item)
                except (ValueError, TypeError):
                    continue
                if item_date != target_date:
                    continue
                events.append(CalendarEvent(
                    calendar_id=calendar_id,
                    calendar_name=calendar_name,
                    event_id=item.get("id", "sin-id"),
                    title=item.get("summary", "(sin titulo)"),
                    event_date=item_date,
                    html_link=item.get("htmlLink", ""),
                ))
                calendar_events += 1
            page_token = response.get("nextPageToken")
            if not page_token:
                break
        if calendar_events:
            print(f"  {calendar_name}: {calendar_events} eventos del dia")

    print(f"Eventos totales encontrados en la fecha: {len(events)}")
    return events


def create_sheets_client():
    credentials = service_account.Credentials.from_service_account_file(
        SHEETS_CREDENTIALS_PATH,
        scopes=SHEETS_SCOPES,
    )
    return gspread.authorize(credentials)


def find_header(values):
    for row_number, row in enumerate(values, start=1):
        lookup = {normalize_header(value): index for index, value in enumerate(row)}
        if "cliente" in lookup and "idcuenta" in lookup:
            return row_number, lookup
    raise ValueError("Vista Global no contiene las cabeceras Cliente / ID Cuenta.")


def is_stop_section(row):
    normalized_cells = {normalize_text(value) for value in row if str(value).strip()}
    return any(value in STOP_SECTION_NAMES for value in normalized_cells)


def extract_account_ids(value):
    matches = re.findall(r"\d{3}\D{0,3}\d{3}\D{0,3}\d{4}", str(value or ""))
    ids = [normalize_customer_id(match) for match in matches]
    ids = [account_id for account_id in ids if len(account_id) == 10]
    if not ids:
        digits = normalize_customer_id(value)
        if len(digits) == 10:
            ids = [digits]
    return list(dict.fromkeys(ids))


def build_client_index(values):
    header_row, columns = find_header(values)
    aliases = {
        "commercial": ("comercial",),
        "technician": ("tecnico",),
        "mcc": ("mcc",),
        "status": ("estado",),
    }

    def optional_column(key):
        return next(
            (columns[name] for name in aliases[key] if name in columns),
            None,
        )

    index = {}
    inside_operational_block = True
    for row_number in range(header_row + 1, len(values) + 1):
        row = values[row_number - 1]
        if is_stop_section(row):
            inside_operational_block = False
            continue
        client_col = columns["cliente"]
        id_col = columns["idcuenta"]
        client_name = row[client_col] if len(row) > client_col else ""
        if not str(client_name).strip():
            continue
        if (
            not inside_operational_block
            and normalize_text(client_name)
            not in CALENDAR_CLIENT_EXCEPTIONS_AFTER_STOP
        ):
            continue
        account_ids = extract_account_ids(row[id_col] if len(row) > id_col else "")
        for account_id in account_ids or [""]:
            record_values = {}
            for key in aliases:
                col = optional_column(key)
                record_values[key] = row[col] if col is not None and len(row) > col else ""
            record = ClientRecord(
                row_number=row_number,
                client_name=str(client_name).strip(),
                account_id=account_id,
                commercial=str(record_values["commercial"]).strip(),
                technician=str(record_values["technician"]).strip(),
                sheet_mcc=str(record_values["mcc"]).strip(),
                status=str(record_values["status"]).strip(),
            )
            index.setdefault(normalize_text(client_name), []).append(record)
    return index


def load_client_index():
    sheets_client = create_sheets_client()
    spreadsheet = sheets_client.open_by_key(CONTROL_SEM_SPREADSHEET_ID)
    worksheet = spreadsheet.worksheet(CONTROL_SEM_WORKSHEET_NAME)
    values = worksheet.get_all_values()
    index = build_client_index(values)
    print(
        f"Vista Global leida en modo solo lectura: {len(index)} clientes operativos."
    )
    return index


def build_calendar_mcc_configs(config):
    clients = [
        dict(client)
        for client in config.get("clientes", [])
        if client.get("activo") is True
    ]
    configured_ids = {
        normalize_customer_id(client.get("mcc_id"))
        for client in clients
    }
    for sem_only_config in CALENDAR_ONLY_MCC_CONFIGS:
        mcc_id = normalize_customer_id(sem_only_config["mcc_id"])
        if mcc_id not in configured_ids:
            clients.append(dict(sem_only_config))
            configured_ids.add(mcc_id)
    return clients


def load_active_mcc_configs():
    config = load_consumption_runtime_config()
    clients = build_calendar_mcc_configs(config)
    if not clients:
        raise RuntimeError("config_clientes.json no contiene MCCs activos.")
    return clients


def get_developer_token(client_config):
    variable_name = client_config.get(
        "developer_token_env",
        "GOOGLE_ADS_DEVELOPER_TOKEN",
    )
    token = os.getenv(variable_name)
    if not token:
        raise RuntimeError(f"Falta la variable requerida {variable_name}.")
    return token


def create_google_ads_client(client_config, login_customer_id):
    required_variables = {
        "GOOGLE_ADS_CLIENT_ID": os.getenv("GOOGLE_ADS_CLIENT_ID"),
        "GOOGLE_ADS_CLIENT_SECRET": os.getenv("GOOGLE_ADS_CLIENT_SECRET"),
        "GOOGLE_ADS_REFRESH_TOKEN": os.getenv("GOOGLE_ADS_REFRESH_TOKEN"),
    }
    missing = [name for name, value in required_variables.items() if not value]
    if missing:
        raise RuntimeError("Faltan variables OAuth de Google Ads: " + ", ".join(missing))
    return GoogleAdsClient.load_from_dict({
        "developer_token": get_developer_token(client_config),
        "client_id": required_variables["GOOGLE_ADS_CLIENT_ID"],
        "client_secret": required_variables["GOOGLE_ADS_CLIENT_SECRET"],
        "refresh_token": required_variables["GOOGLE_ADS_REFRESH_TOKEN"],
        "login_customer_id": normalize_customer_id(login_customer_id),
        "use_proto_plus": True,
    })


def is_retryable_google_ads_error(error):
    text = normalize_text(error)
    return any(fragment in text for fragment in (
        "429",
        "500",
        "503",
        "unavailable",
        "backend unavailable",
        "internal error",
        "resource has been exhausted",
        "too many requests",
    ))


def query_enabled_campaigns(google_ads_client, account_id):
    service = google_ads_client.get_service("GoogleAdsService")
    query = """
        SELECT
          campaign.id,
          campaign.name,
          campaign.status
        FROM campaign
        WHERE campaign.status = ENABLED
        ORDER BY campaign.name
    """
    response = service.search_stream(customer_id=account_id, query=query)
    campaigns = []
    for batch in response:
        for row in batch.results:
            campaigns.append({
                "id": str(row.campaign.id),
                "name": row.campaign.name,
                "status": row.campaign.status.name,
            })
    return campaigns


def query_enabled_campaigns_with_retries(google_ads_client, account_id):
    for attempt in range(1, MAX_GOOGLE_ADS_RETRIES + 1):
        try:
            return query_enabled_campaigns(google_ads_client, account_id)
        except GoogleAdsException as exc:
            if is_retryable_google_ads_error(exc) and attempt < MAX_GOOGLE_ADS_RETRIES:
                delay = RETRY_DELAY_SECONDS * attempt
                print(
                    f"  Error temporal en Ads. Reintento "
                    f"{attempt + 1}/{MAX_GOOGLE_ADS_RETRIES} en {delay}s..."
                )
                time.sleep(delay)
                continue
            raise


def discover_account_mcc(account_id, active_mcc_configs):
    matches = []
    errors = []
    for config in active_mcc_configs:
        login_ids = [config["mcc_id"], *config.get("sub_mcc_ids", [])]
        config_match = None
        for login_id in dict.fromkeys(login_ids):
            try:
                ads_client = create_google_ads_client(config, login_id)
                campaigns = query_enabled_campaigns_with_retries(
                    ads_client,
                    account_id,
                )
                config_match = {
                    "config": config,
                    "login_customer_id": normalize_customer_id(login_id),
                    "campaigns": campaigns,
                }
                break
            except GoogleAdsException as exc:
                if is_retryable_google_ads_error(exc):
                    raise
                errors.append((config.get("nombre", ""), exc.request_id or "sin-request-id"))
            except Exception as exc:
                if is_retryable_google_ads_error(exc):
                    raise
                errors.append((config.get("nombre", ""), type(exc).__name__))
        if config_match:
            matches.append(config_match)

    return matches, errors


def action_label(action):
    return "pausar" if action == "pause" else "reactivar"


def pause_violation_result(campaign_count):
    campaign_word = "campaña" if campaign_count == 1 else "campañas"
    campaign_verb = "queda" if campaign_count == 1 else "quedan"
    return (
        f"Pausa no cumplida: {campaign_verb} {campaign_count} "
        f"{campaign_word} ENABLED."
    )


def build_parse_incident(event, message):
    return Incident(
        event=event,
        client_name="No detectado",
        action="interpretar evento",
        result=message,
        incident_type="event_parse_error",
    )


def verify_event(event, client_index, active_mcc_configs):
    try:
        parsed = parse_action_title(event.title, event.event_date)
    except ActionTitleError as exc:
        return "incident", build_parse_incident(event, str(exc))

    if parsed is None:
        return "ignored", None

    matching_records = client_index.get(normalize_text(parsed.client_name), [])
    if not matching_records:
        return "incident", Incident(
            event=event,
            client_name=parsed.client_name,
            action=action_label(parsed.action),
            result="El cliente no aparece en el bloque operativo de Vista Global.",
            incident_type="client_not_found",
        )

    unique_matches = {
        (record.row_number, record.account_id): record
        for record in matching_records
    }
    if len(unique_matches) != 1:
        return "incident", Incident(
            event=event,
            client_name=parsed.client_name,
            action=action_label(parsed.action),
            result=(
                "Vista Global contiene varias coincidencias para el cliente; "
                "no se ha consultado Google Ads."
            ),
            incident_type="client_ambiguous",
        )

    record = next(iter(unique_matches.values()))
    if not record.account_id:
        return "incident", Incident(
            event=event,
            client_name=record.client_name,
            action=action_label(parsed.action),
            result="La fila de Vista Global no contiene un ID de cuenta válido.",
            incident_type="invalid_account_id",
            commercial=record.commercial,
            technician=record.technician,
        )

    matches, _ = discover_account_mcc(record.account_id, active_mcc_configs)
    if not matches:
        return "incident", Incident(
            event=event,
            client_name=record.client_name,
            action=action_label(parsed.action),
            account_id=record.account_id,
            commercial=record.commercial,
            technician=record.technician,
            result=(
                "La cuenta no es verificable desde los MCC activos de "
                "config_clientes.json."
            ),
            incident_type="account_not_verifiable",
        )
    if len(matches) > 1:
        return "incident", Incident(
            event=event,
            client_name=record.client_name,
            action=action_label(parsed.action),
            account_id=record.account_id,
            commercial=record.commercial,
            technician=record.technician,
            result="La cuenta es accesible desde varios MCC y la asignación es ambigua.",
            incident_type="mcc_ambiguous",
        )

    match = matches[0]
    campaigns = match["campaigns"]
    mcc_config = match["config"]
    campaign_names = [campaign["name"] for campaign in campaigns]
    base = {
        "event": event,
        "client_name": record.client_name,
        "action": action_label(parsed.action),
        "account_id": record.account_id,
        "mcc_name": mcc_config.get("nombre", ""),
        "mcc_id": normalize_customer_id(mcc_config.get("mcc_id", "")),
        "commercial": record.commercial,
        "technician": record.technician,
    }

    if parsed.action == "pause" and campaigns:
        campaign_count = len(campaigns)
        return "incident", Incident(
            **base,
            result=pause_violation_result(campaign_count),
            incident_type="pause_not_fulfilled",
            enabled_campaigns=campaign_names,
        )
    if parsed.action == "reactivate" and not campaigns:
        return "incident", Incident(
            **base,
            result="Reactivación no cumplida: no hay campañas ENABLED.",
            incident_type="reactivation_not_fulfilled",
            enabled_campaigns=[],
        )

    return "fulfilled", {
        "client_name": record.client_name,
        "action": action_label(parsed.action),
        "account_id": record.account_id,
        "enabled_campaigns": len(campaigns),
        "mcc_name": mcc_config.get("nombre", ""),
    }


def load_state(path=STATE_PATH):
    if not path.exists():
        return {"version": STATE_VERSION, "sent": {}}
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"No se puede leer el estado anti-spam: {path}") from exc
    if not isinstance(state.get("sent"), dict):
        raise RuntimeError("El estado anti-spam tiene un formato invalido.")
    return state


def prune_state(
    state,
    reference_day=None,
    retention_days=STATE_RETENTION_DAYS,
):
    reference_day = reference_day or datetime.now(SPAIN_TIMEZONE).date()
    cutoff = reference_day - timedelta(days=retention_days)
    sent = state["sent"]
    removed = 0
    for key, payload in list(sent.items()):
        try:
            event_date = date.fromisoformat(str(payload.get("event_date", "")))
        except (AttributeError, TypeError, ValueError):
            continue
        if event_date < cutoff:
            del sent[key]
            removed += 1
    previous_version = state.get("version")
    state["version"] = STATE_VERSION
    return removed, previous_version != STATE_VERSION


def save_state(state, path=STATE_PATH):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_suffix(path.suffix + ".tmp")
    temporary_path.write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    os.replace(temporary_path, path)


def format_account_id(account_id):
    digits = normalize_customer_id(account_id)
    if len(digits) != 10:
        return digits or "No disponible"
    return f"{digits[:3]}-{digits[3:6]}-{digits[6:]}"


def format_spanish_date(value):
    return value.strftime("%d/%m/%Y")


def build_email_content(target_date, incidents):
    incident_cards = []
    plain_sections = []
    for position, incident in enumerate(incidents, start=1):
        campaigns = incident.enabled_campaigns
        event_title = html.escape(incident.event.title)
        calendar_name = html.escape(incident.event.calendar_name)
        client_name = html.escape(incident.client_name)
        action = html.escape(incident.action.capitalize())
        account_id = html.escape(format_account_id(incident.account_id))
        mcc_name = html.escape(incident.mcc_name or "No disponible")
        commercial = html.escape(incident.commercial or "-")
        technician = html.escape(incident.technician or "-")
        result = html.escape(incident.result)
        is_operational_alert = incident.incident_type in {
            "pause_not_fulfilled",
            "reactivation_not_fulfilled",
        }
        result_background = "#fff1f0" if is_operational_alert else "#fff7e8"
        result_border = "#f04438" if is_operational_alert else "#f79009"

        if campaigns is None:
            campaign_html = (
                '<div style="color:#667085;font-size:13px">'
                "No disponible por el resultado de la verificación."
                "</div>"
            )
            campaign_plain = "No disponible por el resultado de la verificación."
        elif not campaigns:
            campaign_html = (
                '<div style="color:#667085;font-size:13px">'
                "No se han detectado campañas ENABLED."
                "</div>"
            )
            campaign_plain = "Ninguna"
        else:
            campaign_html = "".join(
                '<div style="padding:5px 0;color:#344054;font-size:13px">'
                '<span style="color:#d92d20;font-weight:bold">&#8226;</span>&nbsp; '
                f"{html.escape(name)}</div>"
                for name in campaigns
            )
            campaign_plain = ", ".join(campaigns)

        incident_cards.append(f"""
          <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                 style="margin:0 0 18px;border:1px solid #d0d5dd;border-radius:8px;background:#ffffff">
            <tr>
              <td style="padding:18px 20px 14px;border-bottom:1px solid #eaecf0">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0">
                  <tr>
                    <td style="font-size:17px;font-weight:700;color:#101828">{client_name}</td>
                    <td align="right" style="font-size:12px;color:#667085">Incidencia {position}</td>
                  </tr>
                </table>
                <div style="margin-top:6px;font-size:13px;color:#475467">{event_title}</div>
                <div style="margin-top:3px;font-size:12px;color:#98a2b3">Calendario: {calendar_name}</div>
              </td>
            </tr>
            <tr>
              <td style="padding:16px 20px">
                <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                       style="font-size:13px;color:#344054">
                  <tr>
                    <td width="25%" style="padding:0 12px 12px 0"><strong>Acción esperada</strong><br>{action}</td>
                    <td width="25%" style="padding:0 12px 12px 0"><strong>ID de cuenta</strong><br>{account_id}</td>
                    <td width="25%" style="padding:0 12px 12px 0"><strong>MCC</strong><br>{mcc_name}</td>
                    <td width="25%" style="padding:0 0 12px"><strong>Técnico</strong><br>{technician}</td>
                  </tr>
                  <tr>
                    <td colspan="4" style="padding:0 0 14px"><strong>Comercial</strong><br>{commercial}</td>
                  </tr>
                </table>
                <div style="padding:12px 14px;border-left:4px solid {result_border};background:{result_background};color:#344054;font-size:13px;line-height:1.45">
                  <strong style="color:#101828">Resultado</strong><br>{result}
                </div>
                <div style="padding-top:14px">
                  <div style="margin-bottom:5px;font-size:13px;font-weight:700;color:#101828">Campañas ENABLED</div>
                  {campaign_html}
                </div>
              </td>
            </tr>
          </table>
        """)
        plain_sections.append(
            "\n".join([
                f"INCIDENCIA {position}",
                f"Evento: {incident.event.title}",
                f"Calendario: {incident.event.calendar_name}",
                f"Cliente: {incident.client_name}",
                f"Acción esperada: {incident.action.capitalize()}",
                f"ID de cuenta: {format_account_id(incident.account_id)}",
                f"MCC: {incident.mcc_name or 'No disponible'}",
                f"Comercial: {incident.commercial or '-'}",
                f"Técnico: {incident.technician or '-'}",
                f"Resultado: {incident.result}",
                f"Campañas ENABLED: {campaign_plain}",
            ])
        )

    incident_count = len(incidents)
    incident_word = "incidencia" if incident_count == 1 else "incidencias"
    reviewed_date = format_spanish_date(target_date)
    html_body = f"""
    <!doctype html>
    <html>
      <body style="margin:0;padding:0;background:#f2f4f7;font-family:Arial,sans-serif;color:#101828">
        <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#f2f4f7">
          <tr>
            <td align="center" style="padding:28px 12px">
              <table role="presentation" width="720" cellspacing="0" cellpadding="0"
                     style="width:100%;max-width:720px;background:#ffffff;border-radius:8px;overflow:hidden">
                <tr>
                  <td style="padding:24px 28px;background:#16324f;color:#ffffff">
                    <div style="font-size:12px;font-weight:700;text-transform:uppercase;color:#b9d7ea">Control SEM</div>
                    <div style="margin-top:7px;font-size:23px;font-weight:700;line-height:1.25">Acción de calendario no cumplida</div>
                    <div style="margin-top:9px;font-size:13px;color:#d7e7f2">Revisión automática de Google Calendar y Google Ads</div>
                  </td>
                </tr>
                <tr>
                  <td style="padding:22px 28px 8px">
                    <table role="presentation" width="100%" cellspacing="0" cellpadding="0"
                           style="background:#f8fafc;border:1px solid #eaecf0;border-radius:6px">
                      <tr>
                        <td style="padding:14px 16px;font-size:13px;color:#475467">
                          <strong style="color:#101828">Fecha revisada</strong><br>{reviewed_date}
                        </td>
                        <td align="right" style="padding:14px 16px">
                          <span style="display:inline-block;padding:6px 10px;border-radius:14px;background:#fee4e2;color:#b42318;font-size:12px;font-weight:700">
                            {incident_count} {incident_word}
                          </span>
                        </td>
                      </tr>
                    </table>
                  </td>
                </tr>
                <tr>
                  <td style="padding:16px 28px 10px">
                    {''.join(incident_cards)}
                  </td>
                </tr>
                <tr>
                  <td style="padding:16px 28px 22px;border-top:1px solid #eaecf0;color:#667085;font-size:12px;line-height:1.5">
                    Aviso generado automáticamente. Google Sheets y Google Ads se consultaron en modo de solo lectura.
                  </td>
                </tr>
              </table>
            </td>
          </tr>
        </table>
      </body>
    </html>
    """
    plain_body = (
        "ALERTA SEM: ACCIÓN DE CALENDARIO NO CUMPLIDA\n"
        f"Fecha revisada: {reviewed_date}\n"
        f"Incidencias: {incident_count}\n\n"
        + "\n\n---\n\n".join(plain_sections)
    )
    return plain_body, html_body


def send_email(gmail_service, target_date, incidents):
    plain_body, html_body = build_email_content(target_date, incidents)
    message = EmailMessage()
    message["To"] = EMAIL_RECIPIENT
    message["Subject"] = EMAIL_SUBJECT
    message.set_content(plain_body)
    message.add_alternative(html_body, subtype="html")
    encoded = base64.urlsafe_b64encode(message.as_bytes()).decode("ascii")
    result = execute_workspace_request(gmail_service.users().messages().send(
        userId="me",
        body={"raw": encoded},
    ))
    return result.get("id", "sin-id")


def print_dry_run(target_date, incidents):
    plain_body, _ = build_email_content(target_date, incidents)
    print("\n--- EMAIL DRY-RUN (NO ENVIADO) ---")
    print(f"Para: {EMAIL_RECIPIENT}")
    print(f"Asunto: {EMAIL_SUBJECT}")
    print(plain_body)
    print("--- FIN EMAIL DRY-RUN ---")


def main():
    args = parse_args()
    target_date = parse_target_date(args.fecha)
    print(f"Fecha operativa Europe/Madrid: {datetime.now(SPAIN_TIMEZONE).isoformat()}")
    print(f"Fecha de eventos revisada: {target_date.isoformat()}")
    print(f"Modo dry-run: {args.dry_run}")
    print("Acceso Google Ads: solo lectura de campañas.")
    print("Acceso Google Sheets: solo lectura de Vista Global.")

    workspace_credentials = load_workspace_credentials(interactive=True)
    calendar_service = build(
        "calendar",
        "v3",
        credentials=workspace_credentials,
        cache_discovery=False,
    )
    calendar_ids = resolve_calendar_ids(args.calendar_id)
    if calendar_ids:
        print(f"Calendarios limitados por lista permitida: {len(calendar_ids)}")
    else:
        print(
            "AVISO: GOOGLE_CALENDAR_IDS no esta configurado; "
            "se revisaran todos los calendarios legibles."
        )
    events = list_events_for_date(
        calendar_service,
        target_date,
        explicit_ids=calendar_ids,
    )
    client_index = load_client_index()
    active_mcc_configs = load_active_mcc_configs()
    print(f"MCCs activos disponibles para verificar: {len(active_mcc_configs)}")

    incidents = []
    fulfilled = []
    ignored = 0
    for event in events:
        print(f"\nEvento: {event.title} [{event.calendar_name}]")
        try:
            status, result = verify_event(event, client_index, active_mcc_configs)
        except Exception as exc:
            status = "incident"
            result = build_parse_incident(
                event,
                f"Error inesperado durante la verificación: {type(exc).__name__}.",
            )
        if status == "ignored":
            ignored += 1
            print("  Ignorado: no es una accion SEM reconocida.")
        elif status == "fulfilled":
            fulfilled.append(result)
            print(
                f"  Correcto: {result['client_name']} / {result['action']} / "
                f"campañas ENABLED={result['enabled_campaigns']}"
            )
        else:
            incidents.append(result)
            print(f"  INCIDENCIA: {result.result}")

    print("\nResumen:")
    print(f"  Acciones cumplidas: {len(fulfilled)}")
    print(f"  Incidencias detectadas: {len(incidents)}")
    print(f"  Eventos ignorados: {ignored}")

    state = load_state()
    removed_state_entries, state_migrated = prune_state(state)
    state_changed = bool(removed_state_entries or state_migrated)
    if removed_state_entries:
        print(
            "Entradas anti-spam caducadas eliminadas: "
            f"{removed_state_entries}."
        )

    if not incidents:
        if state_changed and not args.dry_run:
            save_state(state)
        print("No hay incidencias. No se envia email.")
        return

    pending_incidents = [
        incident
        for incident in incidents
        if incident.dedupe_key() not in state["sent"]
    ]
    repeated = len(incidents) - len(pending_incidents)
    if repeated:
        print(f"Incidencias ya notificadas y omitidas por anti-spam: {repeated}")
    if not pending_incidents:
        if state_changed and not args.dry_run:
            save_state(state)
        print("No quedan incidencias nuevas. No se envia email.")
        return

    if args.dry_run:
        print_dry_run(target_date, pending_incidents)
        print("Dry-run: no se envia email y no se modifica el estado anti-spam.")
        return

    gmail_service = build(
        "gmail",
        "v1",
        credentials=workspace_credentials,
        cache_discovery=False,
    )
    message_id = send_email(gmail_service, target_date, pending_incidents)
    sent_at = datetime.now(SPAIN_TIMEZONE).isoformat()
    for incident in pending_incidents:
        state["sent"][incident.dedupe_key()] = {
            "sent_at": sent_at,
            "event_date": incident.event.event_date.isoformat(),
            "event_id": incident.event.event_id,
            "client": incident.client_name,
            "action": incident.action,
            "account_id": normalize_customer_id(incident.account_id),
            "incident_type": incident.incident_type,
        }
    save_state(state)
    print(
        f"Email enviado correctamente a {EMAIL_RECIPIENT}: "
        f"{len(pending_incidents)} incidencias, message_id={message_id}."
    )
    print(f"Estado anti-spam actualizado: {STATE_PATH}")


if __name__ == "__main__":
    main()
