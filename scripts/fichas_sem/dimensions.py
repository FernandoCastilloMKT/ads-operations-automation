"""Preserve manual SEM row heights and column widths, not cell formatting."""

from copy import deepcopy

import gspread
from gspread.http_client import BackOffHTTPClient


class DimensionProtectionError(ValueError):
    pass


def reject_resizing(requests):
    for request in requests:
        if "autoResizeDimensions" in request:
            raise DimensionProtectionError("SEM no permite autoajustar filas ni columnas.")
        dimension = request.get("updateDimensionProperties")
        if dimension is not None:
            fields = str(dimension.get("fields", ""))
            if "*" in fields or "pixelSize" in fields or "pixelSize" in dimension.get("properties", {}):
                raise DimensionProtectionError("SEM no permite cambiar alturas ni anchos manuales.")
        if request.get("copyPaste", {}).get("pasteType") == "PASTE_COLUMN_WIDTHS":
            raise DimensionProtectionError("SEM no permite pegar anchos de otra tabla.")


def read_dimensions(spreadsheet, worksheets):
    response = spreadsheet.fetch_sheet_metadata(params={
        "ranges": [gspread.utils.absolute_range_name(ws.title) for ws in worksheets],
        "includeGridData": True,
        "fields": "sheets(properties(sheetId,gridProperties(rowCount,columnCount)),data(startRow,startColumn,rowMetadata(pixelSize),columnMetadata(pixelSize)))",
    })
    result = {}
    for sheet in response.get("sheets", []):
        props = sheet["properties"]
        data = sheet.get("data", [])
        if len(data) != 1 or data[0].get("startRow", 0) or data[0].get("startColumn", 0):
            raise DimensionProtectionError("No se pudieron leer todas las medidas de la ficha.")
        state = {}
        for axis, key, count in (("ROWS", "rowMetadata", "rowCount"), ("COLUMNS", "columnMetadata", "columnCount")):
            entries = data[0].get(key, [])
            sizes = [entry.get("pixelSize") for entry in entries]
            if len(sizes) != props["gridProperties"][count] or any(type(v) is not int or v <= 0 for v in sizes):
                raise DimensionProtectionError("Medidas ausentes o invalidas; no se escribira la ficha.")
            state[axis] = sizes
        if props["sheetId"] in result:
            raise DimensionProtectionError("Lectura duplicada de medidas.")
        result[props["sheetId"]] = state
    if set(result) != {ws.id for ws in worksheets}:
        raise DimensionProtectionError("Faltan fichas en la lectura de medidas.")
    return result


def pin_requests(states):
    requests = []
    for sheet_id, axes in states.items():
        for axis, sizes in axes.items():
            start = 0
            while start < len(sizes):
                end = start + 1
                while end < len(sizes) and sizes[end] == sizes[start]:
                    end += 1
                requests.append({"updateDimensionProperties": {
                    "range": {"sheetId": sheet_id, "dimension": axis, "startIndex": start, "endIndex": end},
                    "properties": {"pixelSize": sizes[start]}, "fields": "pixelSize",
                }})
                start = end
    return requests


def track_structure(states, requests):
    """Track position shifts; new appended dimensions have no old size to protect."""
    result = deepcopy(states)
    for request in requests:
        for kind in ("insertDimension", "deleteDimension", "moveDimension", "appendDimension"):
            if kind not in request:
                continue
            operation = request[kind]
            range_ = operation.get("range", operation.get("source", operation))
            state = result.get(range_.get("sheetId"))
            if state is None:
                continue
            sizes = state[range_["dimension"]]
            if kind == "appendDimension":
                sizes.extend([None] * operation["length"])
                continue
            start, end = range_["startIndex"], range_["endIndex"]
            if kind == "insertDimension":
                neighbour = start - 1 if operation.get("inheritFromBefore", False) else start
                size = sizes[neighbour] if 0 <= neighbour < len(sizes) else None
                sizes[start:start] = [size] * (end - start)
            elif kind == "deleteDimension":
                del sizes[start:end]
            else:
                moved = sizes[start:end]
                del sizes[start:end]
                destination = operation["destinationIndex"]
                if destination > start:
                    destination -= end - start
                sizes[destination:destination] = moved
    return result


class DimensionPreservingHTTPClient(BackOffHTTPClient):
    """Use existing retry policy; intercept native writes, including direct ones."""

    def batch_update(self, id, body):
        requests = (body or {}).get("requests", [])
        reject_resizing(requests)
        tracked = getattr(self, "_sem_dimensions", {}).get(id)
        expected = track_structure(tracked, requests) if tracked is not None else None
        response = super().batch_update(id, body)
        if expected is not None:
            self._sem_dimensions[id] = expected
        return response

    def pin_dimensions(self, spreadsheet_id, states):
        # Only this trusted path may set pixelSize, to the just-read exact sizes.
        # Setting current sizes explicitly also disables implicit row auto-height.
        super().batch_update(spreadsheet_id, {"requests": pin_requests(states)})
        if not hasattr(self, "_sem_dimensions"):
            self._sem_dimensions = {}
        self._sem_dimensions[spreadsheet_id] = deepcopy(states)


def protect_dimensions(spreadsheet, worksheets):
    if not isinstance(spreadsheet.client, DimensionPreservingHTTPClient):
        raise DimensionProtectionError("El cliente SEM no tiene proteccion de dimensiones.")
    states = read_dimensions(spreadsheet, worksheets)
    spreadsheet.client.pin_dimensions(spreadsheet.id, states)
    print(f"Alturas y anchos actuales protegidos: {len(states)} hojas.")


def verify_dimensions(spreadsheet, worksheets):
    expected = spreadsheet.client._sem_dimensions[spreadsheet.id]
    actual = read_dimensions(spreadsheet, worksheets)
    for sheet_id, axes in expected.items():
        for axis, sizes in axes.items():
            readback = actual[sheet_id][axis]
            if len(sizes) != len(readback) or any(old is not None and old != new for old, new in zip(sizes, readback)):
                raise DimensionProtectionError(
                    "Las medidas han cambiado durante la ejecucion (edicion manual u otro proceso). "
                    "No se restauran medidas antiguas para no pisar ajustes recientes."
                )
    print("Verificacion de alturas y anchos: medidas conservadas.")
