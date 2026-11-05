"""Calendar-year campaign tables; independent of monthly contract balances."""

from copy import deepcopy
from datetime import date
import math
import re
import time

from .account_status import canonical_status_key, status_display_label
from .constants import EXPECTED_HEADERS
from .metrics import (
    optional_currency_format_for_value,
    optional_decimal_format_for_value,
    parse_sheet_number,
    sort_campaign_rows,
)
from .text import normalize_text

HANDLER = "calendar_year_campaigns"
KEYS = (
    "campaign_name", "campaign_status", "clicks", "ctr", "average_cpc",
    "cost", "conversions", "cost_per_conversion", "impressions",
    "top_impression_share", "absolute_top_impression_share", "daily_budget",
)
CURRENCY = {"average_cpc", "cost", "cost_per_conversion", "daily_budget"}


def read_guard(worksheet):
    # The manual checkbox/status are outside these business-data ranges.
    return [worksheet.get(f"A1:M{worksheet.row_count}", value_render_option="FORMULA"),
            worksheet.get("N2", value_render_option="FORMULA")]


def parse_layout(values, first_year, target_day):
    blocks = []
    marker = None
    for index, row in enumerate(values, 1):
        label = str(row[1] if len(row) > 1 else "").strip()
        if normalize_text(label).startswith(("mesesanteriores", "anosanteriores")):
            if marker is not None:
                raise ValueError("Separador anual duplicado.")
            marker = index
        if re.fullmatch(r"20\d{2}", label):
            blocks.append({"year": int(label), "title": index, "total": None})
        if any(normalize_text(cell) == "totalcostecampanas" for cell in row):
            if not blocks or blocks[-1]["total"] is not None:
                raise ValueError("Total anual sin bloque o duplicado.")
            blocks[-1]["total"] = index
    if not blocks or blocks[0]["title"] != 4 or marker is None:
        raise ValueError("La ficha no tiene el diseno anual B4:M ni su separador.")
    live = blocks[0]
    years = [block["year"] for block in blocks]
    if len(years) != len(set(years)) or any(b["total"] is None for b in blocks):
        raise ValueError("Anos duplicados o total anual ausente.")
    if not first_year <= live["year"] <= target_day.year or target_day.year - live["year"] > 3:
        raise ValueError("Ano vivo incompatible o recuperacion superior a tres anos.")
    if live["total"] >= marker or (len(blocks) > 1 and marker >= blocks[1]["title"]):
        raise ValueError("Separador anual fuera de lugar.")
    if years[1:] != sorted(years[1:]) or any(y >= live["year"] for y in years[1:]):
        raise ValueError("El historico anual debe estar ordenado y anterior al bloque vivo.")
    if not set(range(first_year, live["year"])).issubset(years[1:]):
        raise ValueError("Falta un ano gestionado en el historico; se conserva la ficha.")
    for block in blocks:
        if block["total"] <= block["title"] + 2:
            raise ValueError("Bloque anual sin espacio para campanas.")
        header = values[block["title"]]
        if normalize_text(header[1]) not in ("campana", "nombredelacampana", "campaignname"):
            raise ValueError("Cabecera anual no reconocida.")
    return {"blocks": blocks, "marker": marker}


def prepare(sem_client, worksheet, target_day):
    from .configuration import get_sem_customer_ids, load_client_config_by_mcc
    customer_ids = get_sem_customer_ids(sem_client)
    if len(customer_ids) != 1:
        raise ValueError("La ficha anual requiere una unica cuenta.")
    guard = read_guard(worksheet)
    values = guard[0]
    sheet_id = re.sub(r"\D", "", str(values[1][10]))
    if sheet_id != customer_ids[0]:
        raise ValueError("La cuenta K2 no coincide con la configuracion anual.")
    first = sem_client["first_managed_year"]
    if type(first) is not int or not 2000 <= first <= target_day.year:
        raise ValueError("Primer ano gestionado no valido.")
    layout = parse_layout(values, first, target_day)
    years = set(range(layout["blocks"][0]["year"], target_day.year + 1))
    # Reconcile late conversions/spend during January, never the manual baseline.
    if target_day.month == 1 and target_day.year - 1 >= first:
        years.add(target_day.year - 1)
    periods = {str(year): {"query_start_day": date(year, 1, 1),
                           "query_end_day": min(date(year, 12, 31), target_day)}
               for year in sorted(years)}
    return {"sem_client": sem_client, "worksheet": worksheet, "target_day": target_day,
            "customer_ids": customer_ids, "layout": layout, "guard": guard,
            "period_contexts": periods,
            "ads_client_config": load_client_config_by_mcc(sem_client["mcc_id"])}


def fetch(prepared):
    from .google_ads import fetch_campaign_periods, get_thread_google_ads_client
    started = time.perf_counter()
    result = fetch_campaign_periods(
        get_thread_google_ads_client(prepared["ads_client_config"]),
        prepared["customer_ids"][0], prepared["period_contexts"], prepared["target_day"],
    )
    result["elapsed_seconds"] = time.perf_counter() - started
    return result


def grid(sheet_id, start, end, left=1, right=13):
    return {"sheetId": sheet_id, "startRowIndex": start - 1, "endRowIndex": end,
            "startColumnIndex": left, "endColumnIndex": right}


def cell_value(value):
    if value is None or value == "":
        return {}
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError("Metrica anual no finita.")
        return {"numberValue": value}
    return {"stringValue": str(value)}


def write_values(sheet_id, row, col, matrix, formula_columns=()):
    return {"updateCells": {"start": {"sheetId": sheet_id, "rowIndex": row - 1,
                                      "columnIndex": col - 1},
                            "rows": [{"values": [{"userEnteredValue":
                                {"formulaValue": v} if offset in formula_columns else cell_value(v)}
                                for offset, v in enumerate(values)]} for values in matrix],
                            "fields": "userEnteredValue"}}


def repeat(range_, format_):
    return {"repeatCell": {"range": range_, "cell": {"userEnteredFormat": format_},
                           "fields": ",".join("userEnteredFormat." + k for k in format_)}}


def validate_rows(rows):
    if not isinstance(rows, list) or len(rows) > 1000:
        raise ValueError("Respuesta anual ausente o excesiva.")
    seen = set()
    for row in rows:
        campaign_id = str(row["campaign_id"])
        if not campaign_id or campaign_id in seen or not row["campaign_name"]:
            raise ValueError("Campana anual duplicada o sin identidad.")
        seen.add(campaign_id)
        for key in KEYS[2:]:
            value = row[key]
            if value != "" and (not isinstance(value, (int, float)) or not math.isfinite(value)):
                raise ValueError("Metrica anual no numerica.")


def plan_updates(sheet_id, row_count, layout, period_rows, target_year, values):
    """Pure request planner: archive, replace and totals commit in one native batch."""
    layout = deepcopy(layout)
    blocks = layout["blocks"]
    live = blocks[0]
    old_year = live["year"]
    requests = []
    for key, rows in period_rows.items():
        validate_rows(rows)
        old = next((b for b in blocks if b["year"] == int(key)), None)
        if old and not rows:
            old_cost = values[old["total"] - 1][6]
            if parse_sheet_number(old_cost):
                raise ValueError("Lectura anual vacia con gasto existente; se conserva la ficha.")
    if str(target_year) not in period_rows:
        raise ValueError("Falta la lectura del ano actual.")
    if old_year < target_year:
        if any(str(y) not in period_rows for y in range(old_year, target_year)):
            raise ValueError("Faltan anos por cerrar; no se renueva la ficha.")
        for year in range(old_year, target_year):
            start = blocks[-1]["total"] + 2
            height = max(6, len(period_rows[str(year)]) + 4)
            end = start + height - 1
            if end > row_count:
                requests.append({"appendDimension": {"sheetId": sheet_id,
                                                      "dimension": "ROWS", "length": end - row_count}})
                row_count = end
            # Only append into unused cells. Never replace an unrecognized manual area.
            if any(any(str(v).strip() for v in row[1:13]) for row in values[start-1:end]):
                raise ValueError("Hay datos manuales donde se archivaria el ano.")
            block = {"year": year, "title": start, "total": end}
            blocks.append(block)
            requests.extend([
                {"mergeCells": {"range": grid(sheet_id, start, start), "mergeType": "MERGE_ALL"}},
                {"mergeCells": {"range": grid(sheet_id, end, end, 4, 6), "mergeType": "MERGE_ALL"}},
            ])
        live["year"] = target_year

    managed = [b for b in blocks if str(b["year"]) in period_rows]
    for block in sorted(managed, key=lambda b: b["title"]):
        rows = sort_campaign_rows(period_rows[str(block["year"])])
        start = block["title"] + 2
        required = len(rows) + 1  # Blank spacer before the total.
        extra = max(0, required - (block["total"] - start))
        if extra:
            insertion = block["total"]
            requests.append({"insertDimension": {"range": {"sheetId": sheet_id, "dimension": "ROWS",
                "startIndex": insertion - 1, "endIndex": insertion - 1 + extra}, "inheritFromBefore": True}})
            for other in blocks:
                if other["title"] >= insertion:
                    other["title"] += extra
                if other["total"] >= insertion:
                    other["total"] += extra
            if layout["marker"] >= insertion:
                layout["marker"] += extra
        total = block["total"]
        matrix = [[status_display_label(r[k]) if k == "campaign_status" else r[k]
                   for k in KEYS] for r in rows]
        matrix += [[""] * 12 for _ in range(total - start - len(rows))]
        requests.extend([
            write_values(sheet_id, block["title"], 2, [[str(block["year"])]]),
            write_values(sheet_id, block["title"] + 1, 2, [EXPECTED_HEADERS]),
            write_values(sheet_id, start, 2, matrix),
            repeat(grid(sheet_id, start, total - 1), {"backgroundColor": {"red": 1, "green": 1, "blue": 1},
                   "textFormat": {"fontFamily": "Arial", "fontSize": 10, "foregroundColor": {"red": 0, "green": 0, "blue": 0}},
                   "wrapStrategy": "CLIP"}),
        ])
        border = {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}}
        requests.append({"updateBorders": {"range": grid(sheet_id, block["title"]+1, start+max(1,len(rows))-1),
            **{side: border for side in ("top", "bottom", "left", "right", "innerHorizontal", "innerVertical")}}})
        for index, row in enumerate(rows, start):
            active = canonical_status_key(row["campaign_status"]) == "enabled"
            requests.append(repeat(grid(sheet_id, index, index, 2, 3), {"backgroundColor": {
                "red": 0 if active else 1, "green": 1 if active else 0, "blue": 0}}))
            for offset, key in enumerate(KEYS[2:], 3):
                currency = key in CURRENCY
                pattern = (optional_currency_format_for_value if currency else optional_decimal_format_for_value)(row[key])
                requests.append(repeat(grid(sheet_id, index, index, offset, offset+1), {
                    "numberFormat": {"type": "CURRENCY" if currency else "NUMBER", "pattern": pattern}}))
        # Formula totals include every row; no rounded intermediary sums.
        last = max(start, start + len(rows) - 1)
        total_values = ["", "", f"=SUM(D{start}:D{last})", "Total coste campa\u00f1as:", "",
                        f"=SUM(G{start}:G{last})", f"=SUM(H{start}:H{last})",
                        f"=IFERROR(G{total}/H{total};0)", f"=SUM(J{start}:J{last})", "", "", ""]
        requests.append(write_values(sheet_id, total, 2, [total_values], formula_columns=(2,5,6,7,8)))
        requests.append(repeat(grid(sheet_id, block["title"], block["title"]), {
            "backgroundColor": {"red": .427, "green": .62, "blue": .922},
            "textFormat": {"fontFamily": "Arial", "fontSize": 10, "bold": True}, "horizontalAlignment": "CENTER"}))
        requests.append(repeat(grid(sheet_id, block["title"]+1, block["title"]+1), {
            "backgroundColor": {"red": 1, "green": .6, "blue": 0},
            "textFormat": {"fontFamily": "Arial", "fontSize": 10, "bold": True}, "wrapStrategy": "CLIP"}))
        requests.append(repeat(grid(sheet_id, total, total, 4, 6), {
            "backgroundColor": {"red": .976, "green": .796, "blue": .612},
            "textFormat": {"fontFamily": "Arial", "fontSize": 10, "bold": True}}))
        cost = sum(r.get("raw_cost", r["cost"]) for r in rows)
        conversions = sum(r["conversions"] for r in rows)
        for column, value in ((3, sum(r["clicks"] for r in rows)), (6,cost), (7,conversions),
                              (8,cost/conversions if conversions else 0), (9,sum(r["impressions"] for r in rows))):
            currency = column in (6,8)
            pattern = (optional_currency_format_for_value if currency else optional_decimal_format_for_value)(value)
            requests.append(repeat(grid(sheet_id,total,total,column,column+1), {
                "numberFormat": {"type": "CURRENCY" if currency else "NUMBER", "pattern": pattern}}))
        requests.append({"updateBorders": {"range": grid(sheet_id,total,total,3,10),
            **{side: border for side in ("top","bottom","left","right","innerVertical")}}})
    requests.append(write_values(sheet_id, layout["marker"], 2,
        [["A\u00d1OS ANTERIORES (Ordenados desde el primero hasta el \u00faltimo)"]]))
    requests.append({"updateCells": {"start": {"sheetId": sheet_id,"rowIndex":1,"columnIndex":13},
        "rows":[{"values":[{"userEnteredValue":{"formulaValue":"=" + "+".join(f"G{b['total']}" for b in blocks)}}]}],
        "fields":"userEnteredValue"}})
    return requests, layout


def process(prepared, ads_data, mutation_batch):
    from .account_cells import update_and_log_account_statuses
    worksheet = prepared["worksheet"]
    if set(ads_data["period_rows"]) != set(prepared["period_contexts"]):
        raise ValueError("Lectura anual incompleta; no se escribe.")
    requests, _ = plan_updates(worksheet.id, worksheet.row_count, prepared["layout"],
        ads_data["period_rows"], prepared["target_day"].year, prepared["guard"][0])
    if read_guard(worksheet) != prepared["guard"]:
        raise ValueError("La ficha anual ha cambiado durante la lectura; no se escribe.")
    update_and_log_account_statuses(worksheet, prepared["sem_client"],
        {prepared["customer_ids"][0]: ads_data["account_status"]}, mutation_batch)
    mutation_batch.add_requests(requests)
    print(f"Ficha anual preparada: {worksheet.title}; anos: {', '.join(prepared['period_contexts'])}")
    return {"mode": "calendar_year", "worksheet": worksheet.title}
