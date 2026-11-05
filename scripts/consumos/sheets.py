"""Consumption: sheets."""

from .periods import (
    get_current_month_labels,
    get_daily_sheet_name,
    get_month_abbr,
    get_month_header_labels,
    get_month_name_labels,
    get_monthly_sheet_name,
    get_previous_month_day,
)

from calendar import monthrange
from datetime import date
from google.oauth2.service_account import Credentials
from gspread.exceptions import WorksheetNotFound
import gspread
import re
from .constants import (
    EURO_NUMBER_FORMAT_PATTERN,
    SCOPES,
)
from .runtime import (
    sheets_credentials_path,
)

from .text import (
    normalize_customer_id,
    normalize_match_text,
    normalize_sheet_text,
    parse_sheet_number,
)
from .daily_layout import build_daily_layout_requests, fetch_daily_layout


def get_header_aliases(labels):
    return {
        normalize_match_text(label)
        for label in labels
    }


def create_sheets_client():
    credentials = Credentials.from_service_account_file(
        sheets_credentials_path,
        scopes=SCOPES,
    )
    return gspread.authorize(credentials)


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


def build_daily_summary_formula_updates(
    header_row_index,
    first_date_col,
    days_in_target_month,
):
    last_date_col = first_date_col + days_in_target_month - 1
    first_day_cell = gspread.utils.rowcol_to_a1(1, first_date_col)
    last_day_cell = gspread.utils.rowcol_to_a1(1, last_date_col)
    updates = [{
        "range": "C1",
        "values": [[f"=SUM({first_day_cell}:{last_day_cell})"]],
    }]

    first_data_row = header_row_index + 1
    for col in range(first_date_col, last_date_col + 1):
        column_label = re.sub(
            r"\d+",
            "",
            gspread.utils.rowcol_to_a1(1, col),
        )
        updates.append({
            "range": f"{column_label}1",
            "values": [[f"=SUM({column_label}{first_data_row}:{column_label})"]],
        })
    return updates


def build_restore_daily_column_requests(
    worksheet,
    first_date_col,
    source_day,
    target_day,
):
    source_days = monthrange(source_day.year, source_day.month)[1]
    target_days = monthrange(target_day.year, target_day.month)[1]
    if target_days <= source_days:
        return []

    requests = []
    for day_number in range(source_days + 1, target_days + 1):
        source_day_number = day_number - 7
        source_col = first_date_col + source_day_number - 1
        destination_col = first_date_col + day_number - 1
        requests.append({
            "copyPaste": {
                "source": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 0,
                    "endRowIndex": worksheet.row_count,
                    "startColumnIndex": source_col - 1,
                    "endColumnIndex": source_col,
                },
                "destination": {
                    "sheetId": worksheet.id,
                    "startRowIndex": 0,
                    "endRowIndex": worksheet.row_count,
                    "startColumnIndex": destination_col - 1,
                    "endColumnIndex": destination_col,
                },
                "pasteType": "PASTE_NORMAL",
                "pasteOrientation": "NORMAL",
            }
        })
    return requests


def clear_out_of_month_daily_cells(worksheet, values, target_day):
    header_row_index, header_row = find_header_row(values)
    date_columns = get_date_columns(header_row)
    if not date_columns:
        return []

    requests = build_daily_layout_requests(
        fetch_daily_layout(worksheet), header_row_index, min(date_columns.values()), target_day,
    )
    if requests:
        worksheet.spreadsheet.batch_update({"requests": requests})
    return requests


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

    layout_requests = build_daily_layout_requests(
        fetch_daily_layout(worksheet), header_row_index, min_col, target_day, source_day,
    )

    if max_col > worksheet.col_count:
        worksheet.add_cols(max_col - worksheet.col_count)

    column_restore_requests = build_restore_daily_column_requests(
        worksheet,
        min_col,
        source_day,
        target_day,
    )
    if column_restore_requests:
        worksheet.spreadsheet.batch_update({"requests": column_restore_requests})

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
    updates.extend(build_daily_summary_formula_updates(
        header_row_index,
        min_col,
        days_in_target_month,
    ))

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
    if layout_requests:
        layout_requests = build_daily_layout_requests(
            fetch_daily_layout(worksheet), header_row_index, min_col, target_day, source_day,
        )
        worksheet.spreadsheet.batch_update({"requests": layout_requests})
        last_date_col = min_col + days_in_target_month - 1
        worksheet._properties["gridProperties"]["columnCount"] = last_date_col


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
        insert_sheet_index=source_worksheet.index + 1,
        new_sheet_name=target_sheet_name,
    )

    clear_daily_sheet_data(worksheet)
    update_daily_sheet_date_headers(worksheet, source_day, target_day)

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
