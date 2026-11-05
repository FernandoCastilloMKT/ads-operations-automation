"""Thin black outlines around the existing three-row annual balance summaries."""

from .sheets_io import get_worksheet_values, queue_format_requests
from .text import normalize_text


def summary_border_requests(sheet_id, values):
    requests = []
    border = {"style":"SOLID","colorStyle":{"rgbColor":{"red":0,"green":0,"blue":0}}}
    for r,row in enumerate(values):
        for c,value in enumerate(row):
            if normalize_text(value) != "totalgastado" or r+2 >= len(values):
                continue
            below = [normalize_text(values[r+i][c]) if c < len(values[r+i]) else "" for i in (1,2)]
            if not below[0].startswith(("totalano","sobranteano")) or not below[1].startswith("sobranteano"):
                continue
            ends = [j for j in range(c+1,min(c+4,len(row))) if str(row[j]).strip()]
            if not ends:
                continue
            end = ends[0]+1
            requests.append({"updateBorders":{"range":{"sheetId":sheet_id,"startRowIndex":r,"endRowIndex":r+3,
                "startColumnIndex":c,"endColumnIndex":end},"top":border,"bottom":border,"left":border,"right":border}})
    return requests


def repair_summary_borders(worksheet, batch):
    queue_format_requests(worksheet,summary_border_requests(worksheet.id,get_worksheet_values(worksheet)),batch)
