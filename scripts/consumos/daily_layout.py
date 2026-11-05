"""Narrow daily-grid cleanup: legacy green fills and unused trailing columns."""

import re
from calendar import monthrange
from datetime import date
from gspread.utils import rowcol_to_a1


def grid_cells(sheet):
    for grid in sheet.get("data", []):
        for row_index, row in enumerate(grid.get("rowData", []), grid.get("startRow", 0)):
            for col_index, cell in enumerate(row.get("values", []), grid.get("startColumn", 0)):
                yield row_index, col_index, cell


def is_green_color(color):
    rgb = color.get("rgbColor", color)
    return (
        rgb.get("green", 0) > rgb.get("red", 0) + 0.01
        and rgb.get("green", 0) > rgb.get("blue", 0) + 0.01
    )


def build_daily_layout_requests(sheet, header_row_index, first_date_col, target_day, source_day=None):
    """Plan before writes; refuse to delete unexpected trailing user content."""
    props = sheet["properties"]
    sheet_id = props["sheetId"]
    grid = props["gridProperties"]
    last_col = first_date_col + monthrange(target_day.year, target_day.month)[1] - 1
    source_day = source_day or target_day
    copied_month = (source_day.year, source_day.month) != (target_day.year, target_day.month)
    source_last = first_date_col + monthrange(source_day.year, source_day.month)[1] - 1
    requests = []

    for row, col, cell in grid_cells(sheet):
        if col < last_col:
            continue
        value = cell.get("userEnteredValue", {})
        if cell.get("note") or cell.get("dataValidation"):
            raise ValueError("Hay notas o validaciones fuera del mes; revisar antes de eliminar columnas.")
        if not value or value == {"stringValue": ""}:
            continue
        # Only a freshly duplicated, already emptied template may retain these
        # generated old headers/totals. Existing monthly data is never exempt.
        if copied_month and col < source_last:
            expected_date = source_day.replace(day=col - first_date_col + 2).isoformat()
            serial = (date.fromisoformat(expected_date) - date(1899, 12, 30)).days
            if row == header_row_index - 1 and value in ({"stringValue": expected_date}, {"numberValue": serial}):
                continue
            formula = value.get("formulaValue", "")
            label = rowcol_to_a1(1, col + 1)[:-1]
            generated_total = rf"=SUM(?:A)?\({label}{header_row_index + 1}:{label}\d*\)"
            if row == 0 and re.fullmatch(generated_total, formula, re.I):
                continue
        raise ValueError("Hay datos o formulas fuera del ultimo dia; no se eliminan columnas.")

    for key in ("merges", "tables", "protectedRanges"):
        for item in sheet.get(key, []):
            area = item.get("range", item)
            if area.get("endColumnIndex", grid["columnCount"]) > last_col:
                raise ValueError("Hay una estructura protegida o combinada fuera del mes; revisar manualmente.")

    for band in sheet.get("bandedRanges", []):
        colors = [value for name, value in band.get("rowProperties", {}).items() if "Color" in name]
        colors += [value for name, value in band.get("columnProperties", {}).items() if "Color" in name]
        if not any(is_green_color(color) for color in colors):
            continue
        area = band["range"]
        if area.get("startRowIndex", 0) < header_row_index or area.get("startColumnIndex", 0) < first_date_col - 1:
            raise ValueError("El fondo alterno verde invade cabeceras u otras columnas; no se modifica.")
        requests.append({"deleteBanding": {"bandedRangeId": band["bandedRangeId"]}})

    for row, col, cell in grid_cells(sheet):
        if row < header_row_index or not first_date_col - 1 <= col < last_col:
            continue
        fmt = cell.get("userEnteredFormat", {})
        if not any(is_green_color(fmt.get(name, {})) for name in ("backgroundColor", "backgroundColorStyle")):
            continue
        requests.append({"repeatCell": {
            "range": {"sheetId": sheet_id, "startRowIndex": row, "endRowIndex": row + 1,
                      "startColumnIndex": col, "endColumnIndex": col + 1},
            "cell": {}, "fields": "userEnteredFormat.backgroundColor,userEnteredFormat.backgroundColorStyle",
        }})

    if grid["columnCount"] > last_col:
        requests.append({"deleteDimension": {"range": {
            "sheetId": sheet_id, "dimension": "COLUMNS", "startIndex": last_col,
            "endIndex": grid["columnCount"],
        }}})
    return requests


def fetch_daily_layout(worksheet):
    title = "'" + worksheet.title.replace("'", "''") + "'"
    doc = worksheet.spreadsheet.fetch_sheet_metadata(params={
        "ranges": [title], "includeGridData": True,
        "fields": "sheets(properties,merges,tables,protectedRanges,bandedRanges,data(startRow,startColumn,rowData(values(userEnteredValue,userEnteredFormat,note,dataValidation))))",
    })
    return next(sheet for sheet in doc["sheets"] if sheet["properties"]["sheetId"] == worksheet.id)
