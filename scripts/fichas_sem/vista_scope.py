"""Live Vista Global membership is the execution boundary for SEM."""

import re
from urllib.parse import urlparse, parse_qs

from .account_cells import locate_vista_global_sections
from .constants import VISTA_GLOBAL_SIGNED_MARKER, VISTA_GLOBAL_STANDBY_MARKER, VISTA_GLOBAL_NATALIA_MARKER
from .text import normalize_text


def local_sheet_id(link, spreadsheet_id):
    if not link:
        return None
    parsed = urlparse(link)
    if parsed.scheme or parsed.netloc or parsed.path:
        if parsed.scheme != "https" or parsed.netloc != "docs.google.com" or not re.fullmatch(
            rf"/spreadsheets/d/{re.escape(spreadsheet_id)}/(?:edit|view)?/?", parsed.path
        ):
            raise ValueError("Vista Global contiene un enlace de cliente ajeno al documento.")
    gid = parse_qs(parsed.fragment).get("gid", parse_qs(parsed.query).get("gid", []))
    if len(gid) != 1 or not re.fullmatch(r"\d+", gid[0]):
        raise ValueError("Enlace de cliente sin un gid local unico.")
    return int(gid[0])


def parse_scope(cells, spreadsheet_id, worksheet_ids, clients):
    values = [[cell.get("formattedValue", "") for cell in row] for row in cells]
    for marker in (VISTA_GLOBAL_SIGNED_MARKER, VISTA_GLOBAL_STANDBY_MARKER, VISTA_GLOBAL_NATALIA_MARKER):
        count = sum(any(normalize_text(v) == normalize_text(marker) for v in row[:3]) for row in values)
        if count != 1:
            raise ValueError("Los separadores de Vista Global faltan o estan duplicados.")
    sections = locate_vista_global_sections(values)
    aliases = {}
    for title, sheet_id in worksheet_ids.items():
        aliases.setdefault(normalize_text(title), set()).add(sheet_id)
    for client in clients:
        if client["worksheet_name"] in worksheet_ids:
            aliases.setdefault(normalize_text(client["nombre"]),set()).add(worksheet_ids[client["worksheet_name"]])
    allowed, seen, membership = set(), set(), []
    for index, row in enumerate(cells):
        if index == 0 or index in (sections["signed_row"],sections["standby_row"],sections["natalia_row"]):
            continue
        cell = row[2] if len(row) > 2 else {}
        name = str(cell.get("formattedValue", "")).strip()
        if not name:
            continue
        link = cell.get("hyperlink") or cell.get("userEnteredFormat",{}).get("textFormat",{}).get("link",{}).get("uri")
        gid = local_sheet_id(link, spreadsheet_id) if link else None
        if gid is None:
            matches = aliases.get(normalize_text(name),set())
            if len(matches)>1:
                raise ValueError("Nombre de cliente ambiguo en Vista Global.")
            gid = next(iter(matches), None)
        if gid is not None:
            if gid not in worksheet_ids.values() or gid in seen:
                raise ValueError("Ficha duplicada o destino inexistente en Vista Global.")
            seen.add(gid)
            if sections["active_start"] <= index < sections["signed_row"] or index > sections["natalia_row"]:
                allowed.add(gid)
        membership.append((index, name, gid))
    return {"allowed_sheet_ids":frozenset(allowed), "fingerprint":(tuple(sorted(sections.items())),tuple(membership))}


def read_scope(spreadsheet, worksheets, clients):
    from .runtime import VISTA_GLOBAL_WORKSHEET_NAME
    import gspread
    worksheet = worksheets[VISTA_GLOBAL_WORKSHEET_NAME]
    response = spreadsheet.fetch_sheet_metadata(params={
        "ranges":[gspread.utils.absolute_range_name(worksheet.title,f"A1:C{worksheet.row_count}")],
        "includeGridData":True,
        "fields":"sheets(properties(sheetId),data(startRow,startColumn,rowData(values(formattedValue,hyperlink,userEnteredFormat))))",
    })
    sheets=response.get("sheets",[])
    if len(sheets)!=1 or sheets[0].get("properties",{}).get("sheetId")!=worksheet.id:
        raise ValueError("No se pudo leer el ambito de Vista Global.")
    data=sheets[0].get("data",[])
    if len(data)!=1 or data[0].get("startRow",0)!=0 or data[0].get("startColumn",0)!=0:
        raise ValueError("Lectura de Vista Global incompleta.")
    cells=[r.get("values",[]) for r in data[0].get("rowData",[])]
    return parse_scope(cells,spreadsheet.id,{name:ws.id for name,ws in worksheets.items()},clients)


def filter_clients(clients, worksheets, scope):
    allowed, excluded = [], []
    for client in clients:
        worksheet=worksheets.get(client["worksheet_name"])
        (allowed if worksheet and worksheet.id in scope["allowed_sheet_ids"] else excluded).append(client)
    return allowed, excluded


def check_scope_unchanged(spreadsheet, worksheets, clients, expected):
    if read_scope(spreadsheet,worksheets,clients)["fingerprint"]!=expected["fingerprint"]:
        raise ValueError("Vista Global ha cambiado durante la ejecucion; vuelve a solicitarla.")


def navigation_request(sheet_id, spreadsheet_id, global_sheet_id):
    return {"repeatCell":{
        "range":{"sheetId":sheet_id,"startRowIndex":0,"endRowIndex":1,"startColumnIndex":0,"endColumnIndex":1},
        "cell":{"userEnteredFormat":{"textFormat":{
            "link":{"uri":f"https://docs.google.com/spreadsheets/d/{spreadsheet_id}/edit#gid={global_sheet_id}"},
            "bold":True, "underline":False,
            "foregroundColorStyle":{"rgbColor":{"red":0,"green":0,"blue":0}},
        }}},
        "fields":"userEnteredFormat.textFormat.link,userEnteredFormat.textFormat.bold,userEnteredFormat.textFormat.underline,userEnteredFormat.textFormat.foregroundColorStyle",
    }}
