"""Consumption: visibility."""

from .periods import (
    add_months,
    parse_daily_google_ads_sheet_month,
)

from datetime import date

from .text import (
    normalize_sheet_text,
)


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
    to_hide, _ = plan_daily_sheet_visibility_changes(
        worksheets,
        target_day,
        visible_months,
    )
    return to_hide


def plan_daily_sheet_visibility_changes(
    worksheets,
    target_day,
    visible_months=3,
):
    keep_from = add_months(target_day.replace(day=1), -(visible_months - 1))
    to_hide = []
    to_show = []

    for worksheet in worksheets:
        sheet_month = parse_daily_google_ads_sheet_month(worksheet.title)

        if not sheet_month:
            continue

        should_be_visible = keep_from <= sheet_month <= target_day.replace(day=1)
        is_hidden = worksheet._properties.get("hidden", False)

        if not should_be_visible and not is_hidden:
            to_hide.append(worksheet)
        elif should_be_visible and is_hidden:
            to_show.append(worksheet)

    return to_hide, to_show


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
    to_hide, to_show = plan_daily_sheet_visibility_changes(
        worksheets,
        target_day,
        visible_months,
    )

    if not to_hide and not to_show:
        print("Visibilidad mensual: las tres hojas recientes ya estan correctas.")
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
        } for worksheet in to_hide] + [{
            "updateSheetProperties": {
                "properties": {
                    "sheetId": worksheet.id,
                    "hidden": False,
                },
                "fields": "hidden",
            }
        } for worksheet in to_show]
    })

    if to_hide:
        print("Hojas diarias antiguas ocultadas:")
        for worksheet in to_hide:
            print(f"  - {worksheet.title}")
    if to_show:
        print("Hojas diarias recientes mostradas:")
        for worksheet in to_show:
            print(f"  - {worksheet.title}")

    return [worksheet.title for worksheet in [*to_hide, *to_show]]
