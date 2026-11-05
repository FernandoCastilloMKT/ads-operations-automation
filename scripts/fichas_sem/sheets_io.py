"""SEM: sheets io."""

from .errors import (
    SaldoPeriodValidationError,
)

from .metrics import (
    parse_sheet_number,
)

from .renewals import (
    find_contract_date_range,
    has_monthly_renewal_without_end,
)

from google.oauth2.service_account import Credentials
from gspread.http_client import BackOffHTTPClient
from .dimensions import DimensionPreservingHTTPClient, reject_resizing
import gspread
from .constants import (
    SCOPES,
    SHEET_WRITE_BATCH_CLIENTS,
)
from .runtime import (
    sheets_credentials_path,
)


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
        # Reject the entire queued batch before values can be partially written.
        reject_resizing(self.format_requests)
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


def create_sheets_client():
    credentials = Credentials.from_service_account_file(
        sheets_credentials_path,
        scopes=SCOPES,
    )
    # Sheets limita las escrituras por usuario y minuto. El cliente con
    # backoff espera y reintenta 429/5xx sin abandonar una ficha a medias.
    return gspread.authorize(
        credentials,
        http_client=DimensionPreservingHTTPClient,
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


def queue_monthly_renewal(worksheet, renewal, mutation_batch):
    """Guarda fecha, presupuesto y nota en la misma peticion atomica.

    Los valores de renovacion nunca van a values_batch_update: una respuesta
    perdida o un fallo del batch debe conservar fecha y marca juntas.
    """
    if not renewal.get("applied"):
        return
    note_update = renewal.get("note_update")
    updates = renewal.get("value_updates", [])
    if not note_update or not any(
        update["cell"] == note_update["cell"]
        and update["kind"] == "contract_end"
        for update in updates
    ):
        raise SaldoPeriodValidationError(
            "La renovacion debe contener una fecha final y su nota de control."
        )
    requests = []
    for update in updates:
        row, col = gspread.utils.a1_to_rowcol(update["cell"])
        value = update["value"]
        if update["kind"] == "monthly_budget":
            value = parse_sheet_number(value)
        cell = {"userEnteredValue": {"numberValue": value}}
        fields = "userEnteredValue"
        if update["cell"] == note_update["cell"]:
            cell["note"] = note_update["note"]
            fields += ",note"
        requests.append({
            "updateCells": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": row - 1,
                    "endRowIndex": row,
                    "startColumnIndex": col - 1,
                    "endColumnIndex": col,
                },
                "rows": [{"values": [cell]}],
                "fields": fields,
            }
        })
    mutation_batch.add_requests(requests)
