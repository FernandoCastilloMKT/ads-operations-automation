"""Font-only requests and invariant fingerprints for native Sheets."""

import copy
import hashlib
import json
import unicodedata


def arial_targets(config):
    """Only the SEM control workbook is authorized for Arial normalization."""
    targets = [item for item in config["workbooks"] if item["key"] == "sem"]
    if len(targets) != 1:
        raise ValueError("Se requiere un unico libro Control SEM")
    return targets


def arial_request(sheet_id):
    return {"repeatCell": {"range": {"sheetId": sheet_id},
        "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": "Arial"}}},
        "fields": "userEnteredFormat.textFormat.fontFamily"}}


def contract_label_ranges(sheet):
    """Protect contract banners by content, including their complete merge."""
    result = []
    for r, c, cell in cells(sheet):
        if r >= 20 or c >= 5:
            continue
        text = cell.get("userEnteredValue", {}).get("stringValue", "")
        text = " ".join("".join(char for char in unicodedata.normalize("NFKD", text)
                                 if not unicodedata.combining(char)).lower().split())
        if not text.startswith(("renueva mes a mes", "tiene fecha fin", "sin fecha fin",
                                "hasta nueva orden", "pausada hasta nueva orden", "pausado hasta nueva orden")):
            continue
        area = {"startRowIndex": r, "endRowIndex": r+1,
                "startColumnIndex": c, "endColumnIndex": c+1}
        for merge in sheet.get("merges", []):
            if (merge.get("startRowIndex", 0) <= r < merge["endRowIndex"] and
                    merge.get("startColumnIndex", 0) <= c < merge["endColumnIndex"]):
                area = {key: value for key, value in merge.items() if key != "sheetId"}
                break
        result.append(area)
    return result


def protected_position(r, c, areas):
    return any(area.get("startRowIndex", 0) <= r < area["endRowIndex"] and
               area.get("startColumnIndex", 0) <= c < area["endColumnIndex"] for area in areas)


def size_cap_requests(document, maximum=10):
    """Cap existing cell/rich fonts; never enlarge smaller text or edit banners."""
    default = document.get("properties", {}).get("defaultFormat", {}).get("textFormat", {})
    def is_arial(family):
        return family.split(",", 1)[0].strip().casefold() == "arial"
    if default.get("fontSize", 10) > maximum or not is_arial(default.get("fontFamily", "Arial")):
        raise ValueError("Fuente predeterminada inesperada; revisar antes de normalizar")
    requests = []
    for sheet in document["sheets"]:
        props = sheet["properties"]
        if props.get("sheetType", "GRID") != "GRID" or props["title"].upper().startswith(("_BACKUP", "BACKUP_")):
            continue
        areas = contract_label_ranges(sheet)
        groups = []
        for r, c, cell in cells(sheet):
            if protected_position(r, c, areas):
                continue
            tf = cell.get("userEnteredFormat", {}).get("textFormat", {})
            changes = {}
            if tf.get("fontSize", default.get("fontSize", 10)) > maximum:
                changes["fontSize"] = maximum
            if not is_arial(tf.get("fontFamily", default.get("fontFamily", "Arial"))):
                changes["fontFamily"] = "Arial"
            if changes:
                if groups and groups[-1][:2] == [r, c] and groups[-1][3] == changes:
                    groups[-1][1] = c+1
                else:
                    groups.append([r, c+1, c, changes])
            rich = copy.deepcopy(cell.get("textFormatRuns", []))
            changed = False
            for run in rich:
                style = run.get("format", {})
                if style.get("fontSize", 0) > maximum:
                    style["fontSize"] = maximum
                    changed = True
                if not is_arial(style.get("fontFamily", "Arial")):
                    style["fontFamily"] = "Arial"
                    changed = True
            if changed:
                length = len(cell.get("userEnteredValue", {}).get("stringValue", "").encode("utf-16-le")) // 2
                rich = [run for run in rich if run.get("startIndex", 0) < length]
                area = {"sheetId": props["sheetId"], "startRowIndex": r, "endRowIndex": r+1,
                        "startColumnIndex": c, "endColumnIndex": c+1}
                requests.append({"updateCells": {"range": area,
                    "rows": [{"values": [{"textFormatRuns": rich}]}], "fields": "textFormatRuns"}})
                if tf.get("link"):
                    requests.append({"repeatCell": {"range": area,
                        "cell": {"userEnteredFormat": {"textFormat": {"link": copy.deepcopy(tf["link"])}}},
                        "fields": "userEnteredFormat.textFormat.link"}})
        for r, end, start, changes in groups:
            requests.append({"repeatCell": {"range": {"sheetId": props["sheetId"],
                "startRowIndex": r, "endRowIndex": r+1, "startColumnIndex": start, "endColumnIndex": end},
                "cell": {"userEnteredFormat": {"textFormat": changes}},
                "fields": ",".join("userEnteredFormat.textFormat."+key for key in changes)}})
    return requests


def typography_fingerprint(document):
    def strip_size(value):
        if isinstance(value, dict):
            return {key: strip_size(item) for key, item in value.items() if key != "fontSize"}
        if isinstance(value, list):
            return [strip_size(item) for item in value]
        return value
    return fingerprint(strip_size(document), ignore_font=True)


def without_font(value):
    if isinstance(value, dict):
        result = {key: without_font(item) for key, item in value.items() if key != "fontFamily"}
        return {key: item for key, item in result.items() if item != {} and item != []}
    if isinstance(value, list):
        return [without_font(item) for item in value]
    return value


def cells(sheet):
    for data in sheet.get("data", []):
        for r, row in enumerate(data.get("rowData", []), data.get("startRow", 0)):
            for c, cell in enumerate(row.get("values", []), data.get("startColumn", 0)):
                yield r, c, cell


def fingerprint(document, ignore_font=False):
    normalized = {"properties": document.get("properties"), "sheets": []}
    for sheet in document["sheets"]:
        item = {key: value for key, value in sheet.items() if key != "data"}
        item["cells"] = []
        for r, c, cell in cells(sheet):
            value = without_font(cell) if ignore_font else cell
            if ignore_font and value.get("textFormatRuns"):
                length = len(cell.get("userEnteredValue", {}).get("stringValue", "").encode("utf-16-le")) // 2
                value["textFormatRuns"] = [run for run in value["textFormatRuns"] if run.get("startIndex", 0) < length]
                if not value["textFormatRuns"]:
                    del value["textFormatRuns"]
                elif len(value["textFormatRuns"]) == 1:
                    run = value["textFormatRuns"][0]
                    if run.get("startIndex", 0) == 0 and "link" not in run.get("format", {}):
                        # Sheets folds a single full-span rich style into cell format.
                        if run.get("format"):
                            value.setdefault("userEnteredFormat", {}).setdefault("textFormat", {}).update(run["format"])
                        del value["textFormatRuns"]
            if value:
                item["cells"].append([r, c, value])
        item["dimensions"] = [{key: data.get(key) for key in ("startRow", "startColumn", "rowMetadata", "columnMetadata") if data.get(key) is not None} for data in sheet.get("data", [])]
        normalized["sheets"].append(item)
    return hashlib.sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()


def font_requests(document):
    requests = []
    for sheet in document["sheets"]:
        props = sheet["properties"]
        if props.get("sheetType", "GRID") != "GRID" or props["title"].upper().startswith(("_BACKUP", "BACKUP_")):
            continue
        requests.append(arial_request(props["sheetId"]))
        for r, c, cell in cells(sheet):
            runs = copy.deepcopy(cell.get("textFormatRuns", []))
            if not any(run.get("format", {}).get("fontFamily", "Arial") != "Arial" for run in runs):
                continue
            length = len(cell.get("userEnteredValue", {}).get("stringValue", "").encode("utf-16-le")) // 2
            # Google can return a zero-length terminal run that it rejects on write.
            runs = [run for run in runs if run.get("startIndex", 0) < length]
            for run in runs:
                if "fontFamily" in run.get("format", {}):
                    run["format"]["fontFamily"] = "Arial"
            requests.append({"updateCells": {"range": {"sheetId": props["sheetId"],
                "startRowIndex": r, "endRowIndex": r+1, "startColumnIndex": c, "endColumnIndex": c+1},
                "rows": [{"values": [{"textFormatRuns": runs}]}], "fields": "textFormatRuns"}})
    return requests


def non_arial(document):
    mismatches = 0
    for sheet in document["sheets"]:
        if sheet["properties"]["title"].upper().startswith(("_BACKUP", "BACKUP_")):
            continue
        for _, _, cell in cells(sheet):
            family = cell.get("userEnteredFormat", {}).get("textFormat", {}).get("fontFamily")
            if family and family != "Arial":
                mismatches += 1
            mismatches += sum(run.get("format", {}).get("fontFamily", "Arial") != "Arial" for run in cell.get("textFormatRuns", []))
    return mismatches


def restore_font_requests(document):
    """Restore only prior families; never write old account data or formulas."""
    requests = []
    for sheet in document["sheets"]:
        props = sheet["properties"]
        if props.get("sheetType", "GRID") != "GRID" or props["title"].upper().startswith(("_BACKUP", "BACKUP_")):
            continue
        requests.append({"repeatCell": {"range": {"sheetId": props["sheetId"]},
            "cell": {}, "fields": "userEnteredFormat.textFormat.fontFamily"}})
        runs = []
        for r, c, cell in cells(sheet):
            family = cell.get("userEnteredFormat", {}).get("textFormat", {}).get("fontFamily")
            if family:
                if runs and runs[-1][0] == r and runs[-1][2] == c and runs[-1][3] == family:
                    runs[-1][2] = c+1
                else:
                    runs.append([r, c, c+1, family])
            rich = copy.deepcopy(cell.get("textFormatRuns", []))
            if any(run.get("format", {}).get("fontFamily", "Arial") != "Arial" for run in rich):
                length = len(cell.get("userEnteredValue", {}).get("stringValue", "").encode("utf-16-le")) // 2
                rich = [run for run in rich if run.get("startIndex", 0) < length]
                requests.append({"updateCells": {"range": {"sheetId": props["sheetId"],
                    "startRowIndex": r, "endRowIndex": r+1, "startColumnIndex": c, "endColumnIndex": c+1},
                    "rows": [{"values": [{"textFormatRuns": rich}]}], "fields": "textFormatRuns"}})
                link = cell.get("userEnteredFormat", {}).get("textFormat", {}).get("link")
                if link:
                    requests.append({"repeatCell": {"range": {"sheetId": props["sheetId"],
                        "startRowIndex": r, "endRowIndex": r+1, "startColumnIndex": c, "endColumnIndex": c+1},
                        "cell": {"userEnteredFormat": {"textFormat": {"link": copy.deepcopy(link)}}},
                        "fields": "userEnteredFormat.textFormat.link"}})
        for r, start, end, family in runs:
            requests.append({"repeatCell": {"range": {"sheetId": props["sheetId"],
                "startRowIndex": r, "endRowIndex": r+1, "startColumnIndex": start, "endColumnIndex": end},
                "cell": {"userEnteredFormat": {"textFormat": {"fontFamily": family}}},
                "fields": "userEnteredFormat.textFormat.fontFamily"}})
    return requests
