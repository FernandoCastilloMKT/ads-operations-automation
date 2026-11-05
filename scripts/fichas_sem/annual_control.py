"""SEM: annual control."""

from .campaign_blocks import (
    copy_range_to_rows,
)

from datetime import date
import gspread
import re
from .account_status import status_display_label
from .constants import (
    ACCOUNT_STATUS_ENABLED_COLOR,
    ANNUAL_CONTROL_HEADERS,
)

from .metrics import (
    optional_decimal_format_for_value,
    optional_number_format_for_value,
    parse_sheet_number,
)

from .sheets_io import (
    get_worksheet_values,
    invalidate_worksheet_values_cache,
    queue_format_requests,
    queue_values_update,
)

from .text import (
    normalize_text,
)


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
                                    f'=AND(LOWER({first_status_cell})="activo";'
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
                                    f'LOWER({first_status_cell})<>"activo";'
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
            status_display_label(campaign["campaign_status"]),
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
    clicks = sum(parse_sheet_number(row["clicks"]) for row in campaign_rows)
    impressions = sum(parse_sheet_number(row["impressions"]) for row in campaign_rows)
    cost = sum(parse_sheet_number(row["cost"]) for row in campaign_rows)
    totals = [
        clicks / impressions * 100 if impressions else 0,
        cost,
        cost / clicks if clicks else 0,
        cost / impressions * 1000 if impressions else 0,
        sum(parse_sheet_number(row["daily_budget"]) for row in campaign_rows),
        sum(parse_sheet_number(row["total_budget"]) for row in campaign_rows),
    ]
    numeric_rows = [
        (data_start_row + index, row[7:13])
        for index, row in enumerate(data_matrix[:len(campaign_rows)])
    ] + [(total_row, totals)]
    for row_index, numeric_values in numeric_rows:
        for offset, value in enumerate(numeric_values):
            pattern = (
                optional_decimal_format_for_value(value) if offset == 0 else
                optional_number_format_for_value(
                    value,
                    decimal_pattern="#,##0.00\\ [$\u20ac-1]",
                    integer_pattern="#,##0\\ [$\u20ac-1]",
                )
            )
            format_requests.append({"repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": row_index - 1, "endRowIndex": row_index,
                    "startColumnIndex": 8 + offset, "endColumnIndex": 9 + offset,
                },
                "cell": {"userEnteredFormat": {"numberFormat": {
                    "type": "NUMBER" if offset == 0 else "CURRENCY", "pattern": pattern,
                }}},
                "fields": "userEnteredFormat.numberFormat",
            }})
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
