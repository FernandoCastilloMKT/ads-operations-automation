"""Independent Meta summary in HISTORICO/URL/ACCESOS, never Google balances."""

import gspread

from .account_status import STATUS_LABELS_ES, canonical_status_key, status_display_label
from .metrics import optional_currency_format_for_value, optional_decimal_format_for_value
from .periods import parse_sheet_date
from .sheets_io import get_worksheet_values, queue_format_requests, queue_values_update
from .text import normalize_text


def prepare_meta_contract_summary(worksheet, config):
    values = get_worksheet_values(worksheet)
    matches = [(r,c) for r,row in enumerate(values,1) for c,value in enumerate(row,1)
               if normalize_text(value) == "datosgeneralesdecampanametaads"]
    if len(matches) != 1:
        raise ValueError("Meta contract title missing or ambiguous")
    row, col = matches[0]
    def cell(r,c):
        return values[r-1][c-1] if r <= len(values) and c <= len(values[r-1]) else ""
    if any(normalize_text(cell(row+1,col+offset)) != normalize_text(label)
           for offset,label in ((0,"Fecha de inicio"),(1,"Fecha fin"),(7,"Consumo"),(8,"Resto"))):
        raise ValueError("Unexpected Meta contract summary columns")
    headers = ["Nombre del conjunto de anuncios","Estado del conjunto de anuncios","Clics (todos)",
               "Costo","Alcance","Impresiones","Frecuencia","Acciones","Clics en enlaces"]
    if [normalize_text(cell(row+3,col+i)) for i in range(9)] != [normalize_text(label) for label in headers]:
        raise ValueError("Unexpected Meta adset columns")
    start,end = parse_sheet_date(cell(row+2,col)),parse_sheet_date(cell(row+2,col+1))
    if start is None or end is None or end < start:
        raise ValueError("Invalid Meta contract dates")
    capacity = config["campaign_capacity"]
    if not isinstance(capacity,int) or not 1 <= capacity <= 20:
        raise ValueError("Invalid Meta contract capacity")
    first,last = row+4,row+3+capacity
    body_range = f"{gspread.utils.rowcol_to_a1(first,col)}:{gspread.utils.rowcol_to_a1(last,col+8)}"
    guard_range = f"{gspread.utils.rowcol_to_a1(row,col)}:{gspread.utils.rowcol_to_a1(last,col+8)}"
    if any(normalize_text(value) == "mesactual" or normalize_text(value).startswith("encuentade")
           for record in values[first-1:last] for value in record[col-1:col+8]):
        raise ValueError("Meta summary capacity overlaps another block")
    return {"title_row":row,"start_col":col,"first":first,"last":last,"start_day":start,"end_day":end,
            "body_range":body_range,"guard_range":guard_range,"guard":worksheet.get(guard_range)}


def write_meta_contract_summary(worksheet, plan, data, batch):
    if data.get("not_started") or data.get("preserve_closed"):
        return
    if worksheet.get(plan["guard_range"]) != plan["guard"]:
        raise ValueError("Meta contract changed during read; no summary written")
    rows = data["rows"]
    capacity = plan["last"]-plan["first"]+1
    if len(rows) > capacity or len({row["id"] for row in rows}) != len(rows):
        raise ValueError("Meta summary overflow or duplicate adsets")
    matrix,requests = [],[]
    col = plan["start_col"]
    for index,item in enumerate(rows):
        status = {"CAMPAIGN_PAUSED":"paused","ADSET_PAUSED":"paused",
                  "PENDING_REVIEW":"inreview","PENDING_BILLING_INFO":"pending",
                  "WITH_ISSUES":"Con incidencias","IN_PROCESS":"pending"}.get(item["status"],item["status"])
        if canonical_status_key(status) not in STATUS_LABELS_ES and status != "Con incidencias":
            status = "unknown"
        matrix.append([item["name"],status_display_label(status),item["clicks"],item["cost"],item["reach"],
                       item["impressions"],item["frequency"] if item["frequency"] is not None else "",item["actions"],item["link_clicks"]])
        row = plan["first"]+index
        active = canonical_status_key(status) == "enabled"
        requests.append({"repeatCell":{"range":{"sheetId":worksheet.id,"startRowIndex":row-1,"endRowIndex":row,
            "startColumnIndex":col,"endColumnIndex":col+1},"cell":{"userEnteredFormat":{
            "backgroundColor":{"red":int(not active),"green":int(active),"blue":0},
            "textFormat":{"foregroundColor":{"red":int(not active),"green":int(not active),"blue":int(not active)}}}},
            "fields":"userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.foregroundColor"}})
        for offset in (2,3,4,5,6,7,8):
            value = matrix[-1][offset]
            fmt = {"type":"CURRENCY" if offset==3 else "NUMBER","pattern":
                   optional_currency_format_for_value(value) if offset==3 else optional_decimal_format_for_value(value or 0)}
            requests.append({"repeatCell":{"range":{"sheetId":worksheet.id,"startRowIndex":row-1,"endRowIndex":row,
                "startColumnIndex":col-1+offset,"endColumnIndex":col+offset},"cell":{"userEnteredFormat":{"numberFormat":fmt}},
                "fields":"userEnteredFormat.numberFormat"}})
    matrix.extend([[""]*9 for _ in range(capacity-len(rows))])
    queue_values_update(worksheet,plan["body_range"],matrix,batch)
    cost_col = gspread.utils.rowcol_to_a1(1,col+3).rstrip("1")
    consumption = gspread.utils.rowcol_to_a1(plan["title_row"]+2,col+7)
    queue_values_update(worksheet,consumption,[[f'=SUM({cost_col}{plan["first"]}:{cost_col}{plan["last"]})']],batch)
    queue_format_requests(worksheet,requests,batch)
