"""SEM: campaign blocks."""

from .account_status import canonical_status_key, status_display_label, translate_status_condition
from .platform_display import campaign_state, read_campaign_states, restore_platform_campaigns, state_requests
from .metrics import (
    build_output_matrix,
    build_standard_header_slice,
    optional_currency_format_for_value,
    optional_decimal_format_for_value,
    optional_percent_format_for_value,
    parse_sheet_number,
    sort_campaign_rows,
    summarize_campaign_metrics,
)

from .periods import (
    date_range_start_month,
    extract_explicit_year,
    is_month_current_cell,
    is_period_cell,
    is_previous_months_cell,
    is_total_cost_cell,
    month_label_for_day,
    parse_date_range,
    parse_period_identity,
    parse_period_month_number,
    range_label_for_days,
    today_in_spain,
)

from datetime import date
import gspread
import json
from .constants import (
    ACCOUNT_STATUS_ENABLED_COLOR,
    DATE_RANGE_PATTERN,
    HEADER_ALIASES,
    HISTORICAL_SIDE_BAND_COLOR,
    INTEGER_NUMBER_FORMAT,
    OBSOLETE_HEADER_ALIASES,
    STANDARD_HEADER_BY_KEY,
)

from .sheets_io import (
    get_worksheet_values,
    invalidate_worksheet_values_cache,
    queue_format_requests,
    queue_values_update,
    set_worksheet_values_cache,
)

from .text import (
    normalize_text,
)


def normalized_header_lookup(row):
    return {
        normalize_text(value): index
        for index, value in enumerate(row, start=1)
        if str(value).strip()
    }


def find_header_columns(header_row):
    lookup = normalized_header_lookup(header_row)
    columns = {}

    for key, aliases in HEADER_ALIASES.items():
        for alias in aliases:
            normalized_alias = normalize_text(alias)
            if normalized_alias in lookup:
                columns[key] = lookup[normalized_alias]
                break

    missing = [key for key in HEADER_ALIASES if key not in columns]

    if missing:
        raise ValueError(
            "Faltan cabeceras requeridas en la tabla SEM: " + ", ".join(missing)
        )

    return columns


def find_obsolete_header_columns(header_row):
    lookup = normalized_header_lookup(header_row)
    columns = {}

    for key, aliases in OBSOLETE_HEADER_ALIASES.items():
        for alias in aliases:
            normalized_alias = normalize_text(alias)
            if normalized_alias in lookup:
                columns[key] = lookup[normalized_alias]
                break

    return columns


def is_legacy_conversion_value_header(value):
    legacy_aliases = {
        normalize_text(alias)
        for key in ("conversion_value", "all_conversions_value")
        for alias in OBSOLETE_HEADER_ALIASES[key]
    }
    return normalize_text(value) in legacy_aliases


def looks_like_campaign_header(row):
    normalized_values = {normalize_text(value) for value in row}
    return (
        any(
            normalize_text(alias) in normalized_values
            for alias in HEADER_ALIASES["campaign_name"]
        )
        and any(
            normalize_text(alias) in normalized_values
            for alias in HEADER_ALIASES["campaign_status"]
        )
        and any(
            normalize_text(alias) in normalized_values
            for alias in HEADER_ALIASES["cost"]
        )
    )


def build_block_from_header(
    values,
    period_row,
    period_col,
    header_row_index,
    header_values=None,
    header_template_row=None,
    historical_marker_row=None,
):
    header_row = header_values or values[header_row_index - 1]
    columns = find_header_columns(header_row)
    required_column_indexes = set(columns.values())
    obsolete_columns = {
        key: col_index
        for key, col_index in find_obsolete_header_columns(header_row).items()
        if col_index not in required_column_indexes
    }

    return {
        "values": values,
        "period_row": period_row,
        "period_col": period_col,
        "header_row": header_row_index,
        "header_values": header_row,
        "header_template_row": header_template_row or header_row_index,
        "historical_marker_row": historical_marker_row,
        "columns": columns,
        "obsolete_columns": obsolete_columns,
    }


def find_first_campaign_header(values, start_row=1):
    for row_index in range(start_row, len(values) + 1):
        row = values[row_index - 1]
        if looks_like_campaign_header(row):
            return row_index, row

    raise ValueError("No se encontro ninguna cabecera de campanas en la pestana.")


def find_live_block(worksheet, target_day):
    values = get_worksheet_values(worksheet)
    mes_actual_rows = []

    for row_index, row in enumerate(values, start=1):
        if any(is_month_current_cell(value) for value in row):
            mes_actual_rows.append(row_index)

    if mes_actual_rows:
        mes_actual_row = min(mes_actual_rows)
        search_start = mes_actual_row + 1
        try:
            historical_marker_row = find_historical_marker_row(values)
        except ValueError:
            historical_marker_row = min(len(values), search_start + 30)
        search_end = min(len(values), historical_marker_row - 1)

        period_row = None
        period_col = None

        for row_index in range(search_start, search_end + 1):
            row = values[row_index - 1]

            for col_index, value in enumerate(row, start=1):
                if is_period_cell(value):
                    period_row = row_index
                    period_col = col_index
                    break

            if period_row:
                break

        for row_index in range(search_start, search_end + 1):
            row = values[row_index - 1]
            if looks_like_campaign_header(row):
                columns = find_header_columns(row)
                period_row = period_row or row_index - 1
                period_col = period_col or min(columns.values())
                return build_block_from_header(
                    values,
                    period_row,
                    period_col,
                    row_index,
                    historical_marker_row=historical_marker_row,
                )

        template_header_row, template_header = find_first_campaign_header(
            values,
            historical_marker_row + 1,
        )
        template_columns = find_header_columns(template_header)
        period_row = period_row or search_start
        period_col = period_col or min(template_columns.values())
        header_row_index = period_row + 1

        return build_block_from_header(
            values,
            period_row,
            period_col,
            header_row_index,
            header_values=template_header,
            header_template_row=template_header_row,
            historical_marker_row=historical_marker_row,
        )

    wanted_period = normalize_text(month_label_for_day(target_day))

    for row_index, row in enumerate(values, start=1):
        for col_index, value in enumerate(row, start=1):
            if is_period_cell(value) and normalize_text(value) == wanted_period:
                return build_block_from_header(
                    values,
                    row_index,
                    col_index,
                    row_index + 1,
                )

    for row_index, row in enumerate(values, start=1):
        if looks_like_campaign_header(row):
            columns = find_header_columns(row)
            period_row = row_index - 1
            period_col = min(columns.values())
            return build_block_from_header(
                values,
                period_row,
                period_col,
                row_index,
            )

    raise ValueError(
        "No se encontro el bloque vivo: falta 'Periodo seleccionado' "
        "y no se pudo localizar la cabecera de campanas."
    )


def find_total_row(values, header_row, start_col, end_col, stop_row=None):
    last_row = min(stop_row or len(values), len(values))

    for row_index in range(header_row + 1, last_row + 1):
        row = values[row_index - 1]
        visible = row[start_col - 1:end_col]

        if any(is_total_cost_cell(value) for value in visible):
            return row_index

        if row_index > header_row + 100:
            break

    raise ValueError("No se encontro la fila 'Total coste campanas'.")


def find_next_period_row(values, start_row):
    """Devuelve el siguiente periodo posterior a ``start_row``, si existe."""
    for row_index in range(start_row + 1, len(values) + 1):
        if any(is_period_cell(value) for value in values[row_index - 1]):
            return row_index

    return None


def normalize_live_total_label(worksheet, block, mutation_batch=None):
    """Corrige la etiqueta del total sin alterar los datos del bloque vivo."""
    columns = block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    total_row = find_total_row(
        block["values"],
        block["header_row"],
        start_col,
        end_col,
        stop_row=block.get("historical_marker_row"),
    )
    label_col = columns["ctr"]
    label_cell = gspread.utils.rowcol_to_a1(total_row, label_col)
    row_values = block["values"][total_row - 1]
    current_label = (
        row_values[label_col - 1]
        if len(row_values) >= label_col
        else ""
    )
    expected_label = "Total coste campa\u00f1as:"

    if current_label == expected_label:
        return {"cell": label_cell, "changed": False}

    queue_values_update(
        worksheet,
        label_cell,
        [[expected_label]],
        mutation_batch,
    )
    return {"cell": label_cell, "changed": True}


def clear_obsolete_live_columns(
    worksheet,
    block,
    total_row,
    mutation_batch=None,
):
    obsolete_columns = sorted(set(block.get("obsolete_columns", {}).values()))

    if not obsolete_columns:
        return []

    clear_ranges = [
        (
            f"{gspread.utils.rowcol_to_a1(block['header_row'], col_index)}:"
            f"{gspread.utils.rowcol_to_a1(total_row, col_index)}"
        )
        for col_index in obsolete_columns
    ]
    requests = []
    for col_index in obsolete_columns:
        queue_values_update(
            worksheet,
            (
                f"{gspread.utils.rowcol_to_a1(block['header_row'], col_index)}:"
                f"{gspread.utils.rowcol_to_a1(total_row, col_index)}"
            ),
            [[""] for _ in range(total_row - block["header_row"] + 1)],
            mutation_batch,
        )
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": block["header_row"] - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": col_index - 1,
                    "endColumnIndex": col_index,
                },
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat",
            }
        })

    queue_format_requests(worksheet, requests, mutation_batch)
    return clear_ranges


def standardize_live_block_columns(worksheet, target_day=None):
    block = find_live_block(worksheet, target_day or today_in_spain())
    columns = block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    marker_row = block.get("historical_marker_row")

    try:
        total_row = find_total_row(
            block["values"],
            block["header_row"],
            start_col,
            end_col,
            marker_row - 1 if marker_row else None,
        )
    except ValueError:
        if not marker_row:
            raise
        total_row = max(block["header_row"] + 1, marker_row - 2)

    header_slice = build_standard_header_slice(columns, start_col, end_col, getattr(worksheet, '_sem_platform_display', False))
    header_range = (
        f"{gspread.utils.rowcol_to_a1(block['header_row'], start_col)}:"
        f"{gspread.utils.rowcol_to_a1(block['header_row'], end_col)}"
    )
    worksheet.update(
        range_name=header_range,
        values=[header_slice],
        value_input_option="USER_ENTERED",
    )
    cleared_ranges = clear_obsolete_live_columns(worksheet, block, total_row)

    return {
        "header_range": header_range,
        "cleared_ranges": cleared_ranges,
        "standard_columns": len(columns),
    }


def count_campaign_rows(values, data_start_row, total_row, campaign_col):
    count = 0

    for row_index in range(data_start_row, total_row):
        row = values[row_index - 1] if len(values) >= row_index else []
        campaign_name = row[campaign_col - 1] if len(row) >= campaign_col else ""

        if not str(campaign_name).strip():
            break

        count += 1

    return count


def find_historical_marker_row(values):
    for row_index, row in enumerate(values, start=1):
        if any(is_previous_months_cell(value) for value in row):
            return row_index

    raise ValueError("No se encontro la fila 'MESES ANTERIORES'.")


def ensure_historical_marker_row(worksheet, live_block):
    values = get_worksheet_values(worksheet)
    try:
        return find_historical_marker_row(values)
    except ValueError:
        pass

    columns = live_block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    total_row = find_total_row(
        values,
        live_block["header_row"],
        start_col,
        end_col,
    )
    marker_row = total_row + 2
    existing_row = values[marker_row - 1] if len(values) >= marker_row else []
    occupied = any(
        str(value).strip()
        for value in existing_row[start_col - 1:end_col]
    )
    if occupied:
        worksheet.insert_rows(
            [["" for _ in range(worksheet.col_count)]],
            row=marker_row,
            value_input_option="USER_ENTERED",
            inherit_from_before=True,
        )
        invalidate_worksheet_values_cache(worksheet)

    marker_cell = gspread.utils.rowcol_to_a1(marker_row, start_col)
    worksheet.update(
        range_name=marker_cell,
        values=[["MESES ANTERIORES (Ordenados desde el primer mes hasta el \u00faltimo)"]],
        value_input_option="USER_ENTERED",
    )
    worksheet.spreadsheet.batch_update({
        "requests": [
            {
                "mergeCells": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": marker_row - 1,
                        "endRowIndex": marker_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "mergeType": "MERGE_ALL",
                }
            },
            {
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": marker_row - 1,
                        "endRowIndex": marker_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": {
                                "red": 0.4,
                                "green": 0.4,
                                "blue": 0.4,
                            },
                            "horizontalAlignment": "CENTER",
                            "verticalAlignment": "MIDDLE",
                            "textFormat": {
                                "bold": True,
                                "foregroundColor": {
                                    "red": 1,
                                    "green": 1,
                                    "blue": 1,
                                },
                            },
                        }
                    },
                    "fields": "userEnteredFormat",
                }
            },
        ]
    })
    invalidate_worksheet_values_cache(worksheet)
    get_worksheet_values(worksheet, refresh=True)
    return marker_row


def iter_period_cells(values, start_row=1, reference_day=None):
    for row_index, row in enumerate(values, start=1):
        if row_index < start_row:
            continue

        for col_index, value in enumerate(row, start=1):
            if is_period_cell(value):
                period_identity = parse_period_identity(value, reference_day)
                if period_identity:
                    yield row_index, col_index, value, period_identity


def historical_period_start_for_order(value, target_start_day):
    range_match = DATE_RANGE_PATTERN.search(str(value or ""))
    range_has_year = bool(
        range_match
        and (
            range_match.group("start_year")
            or range_match.group("end_year")
        )
    )

    if range_match and not range_has_year:
        start_month_day = (
            int(range_match.group("start_month")),
            int(range_match.group("start_day")),
        )
        target_month_day = (
            target_start_day.month,
            target_start_day.day,
        )
        # Un rango historico sin ano se interpreta como la ocurrencia mas
        # reciente que no empiece despues del periodo que se va a archivar.
        # Asi 01/12 - 19/12 antes de julio de 2026 es diciembre de 2025.
        default_start_year = target_start_day.year
        if start_month_day > target_month_day:
            default_start_year -= 1
        range_info = parse_date_range(
            value,
            target_start_day,
            default_start_year=default_start_year,
        )
        if range_info:
            return range_info["start_day"]

    identity = parse_period_identity(value, target_start_day)
    if identity:
        return identity["start_day"]

    month_number = parse_period_month_number(value)
    if not month_number:
        return None

    # Los historicos heredados suelen omitir el ano. Para decidir si el nuevo
    # periodo puede anexarse, se interpreta el mes como la ocurrencia mas
    # reciente que no sea posterior al periodo objetivo.
    year = (
        target_start_day.year
        if month_number <= target_start_day.month
        else target_start_day.year - 1
    )
    return date(year, month_number, 1)


def find_historical_block_end_row(
    values,
    period_row,
    start_col,
    end_col,
):
    next_period_row = None
    for row_index in range(period_row + 1, len(values) + 1):
        if any(is_period_cell(value) for value in values[row_index - 1]):
            next_period_row = row_index
            break

    stop_row = next_period_row - 1 if next_period_row else len(values)
    header_row = period_row + 1

    if header_row <= len(values) and looks_like_campaign_header(
        values[header_row - 1]
    ):
        try:
            return find_total_row(
                values,
                header_row,
                start_col,
                end_col,
                stop_row,
            )
        except ValueError:
            pass

        header_lookup = normalized_header_lookup(values[header_row - 1])
        campaign_col = next(
            header_lookup[normalize_text(alias)]
            for alias in HEADER_ALIASES["campaign_name"]
            if normalize_text(alias) in header_lookup
        )
        last_campaign_row = header_row
        for row_index in range(header_row + 1, stop_row + 1):
            row = values[row_index - 1]
            campaign_name = (
                row[campaign_col - 1]
                if len(row) >= campaign_col
                else ""
            )
            if not str(campaign_name).strip():
                break
            last_campaign_row = row_index

        return last_campaign_row

    # Compatibilidad defensiva con cabeceras muy antiguas: el bloque termina
    # tras dos filas completamente vacias consecutivas. Asi se ignoran restos
    # aislados que puedan existir mucho mas abajo en otras columnas.
    last_content_row = period_row
    blank_rows = 0
    for row_index in range(period_row + 1, stop_row + 1):
        row = values[row_index - 1]
        visible = row[start_col - 1:end_col]
        if any(str(value).strip() for value in visible):
            last_content_row = row_index
            blank_rows = 0
            continue

        blank_rows += 1
        if blank_rows >= 2:
            break

    return last_content_row


def clean_orphan_historical_metric_tails(
    worksheet,
    live_block,
    mutation_batch=None,
):
    """Limpia restos K:M que no pertenecen a ningun bloque historico."""
    values = get_worksheet_values(worksheet)
    columns = live_block["columns"]
    tail_keys = (
        "top_impression_share",
        "absolute_top_impression_share",
        "daily_budget",
    )
    if not all(key in columns for key in tail_keys):
        return []

    marker_row = find_historical_marker_row(values)
    start_col = min(columns.values())
    end_col = max(columns.values())
    tail_start_col = min(columns[key] for key in tail_keys)
    covered_rows = set()

    period_rows = [
        row_index
        for row_index in range(marker_row + 1, len(values) + 1)
        if any(is_period_cell(value) for value in values[row_index - 1])
    ]
    for period_row in period_rows:
        block_end_row = find_historical_block_end_row(
            values,
            period_row,
            start_col,
            end_col,
        )
        covered_rows.update(range(period_row, block_end_row + 1))

    orphan_rows = []
    for row_index in range(marker_row + 1, len(values) + 1):
        if row_index in covered_rows:
            continue
        row = values[row_index - 1]
        leading_values = row[start_col - 1:tail_start_col - 1]
        tail_values = row[tail_start_col - 1:end_col]
        if (
            not any(str(value).strip() for value in leading_values)
            and any(str(value).strip() for value in tail_values)
        ):
            orphan_rows.append(row_index)

    ranges = []
    for row_index in orphan_rows:
        if ranges and row_index == ranges[-1][1] + 1:
            ranges[-1] = (ranges[-1][0], row_index)
        else:
            ranges.append((row_index, row_index))

    cleared_ranges = []
    width = end_col - tail_start_col + 1
    for first_row, last_row in ranges:
        range_name = (
            f"{gspread.utils.rowcol_to_a1(first_row, tail_start_col)}:"
            f"{gspread.utils.rowcol_to_a1(last_row, end_col)}"
        )
        queue_values_update(
            worksheet,
            range_name,
            [["" for _ in range(width)] for _ in range(last_row - first_row + 1)],
            mutation_batch,
        )
        queue_format_requests(
            worksheet,
            [{
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": first_row - 1,
                        "endRowIndex": last_row,
                        "startColumnIndex": tail_start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "cell": {"note": "", "userEnteredFormat": {}},
                    "fields": "note,userEnteredFormat",
                }
            }],
            mutation_batch,
        )
        cleared_ranges.append(range_name)

    return cleared_ranges


def resolve_historical_side_band_end(
    historical_block,
    target_end_row,
    inserted_rows=0,
    deleted_rows=0,
):
    """Calcula el final global aunque el periodo actualizado no sea el último."""
    values = historical_block["values"]
    columns = historical_block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    marker_row = find_historical_marker_row(values)
    period_rows = [
        row_index
        for row_index in range(marker_row + 1, len(values) + 1)
        if any(is_period_cell(value) for value in values[row_index - 1])
    ]

    if not period_rows or period_rows[-1] == historical_block["period_row"]:
        return target_end_row

    last_end_row = find_historical_block_end_row(
        values,
        period_rows[-1],
        start_col,
        end_col,
    )
    if period_rows[-1] > historical_block["period_row"]:
        last_end_row += inserted_rows - deleted_rows

    return max(target_end_row, last_end_row)


def find_historical_append_row(
    values,
    marker_row,
    start_col,
    end_col,
    period_context,
):
    period_cells = []

    for row_index in range(marker_row + 1, len(values) + 1):
        row = values[row_index - 1]
        for value in row[start_col - 1:end_col]:
            if is_period_cell(value):
                period_cells.append((row_index, value))
                break

    if not period_cells:
        return marker_row + 2

    target_start_day = period_context["start_day"]
    later_periods = []
    for row_index, value in period_cells:
        existing_start = historical_period_start_for_order(
            value,
            target_start_day,
        )
        if existing_start and existing_start > target_start_day:
            later_periods.append((row_index, str(value).strip()))

    if later_periods:
        row_index, value = later_periods[0]
        raise ValueError(
            "No se puede anadir el periodo historico al final porque ya existe "
            f"un periodo posterior en la fila {row_index}: {value}."
        )

    last_period_row = period_cells[-1][0]
    last_block_end_row = find_historical_block_end_row(
        values,
        last_period_row,
        start_col,
        end_col,
    )
    return last_block_end_row + 2


def historical_period_exists(values, marker_row, period_context):
    wanted_key = period_context["key"]

    for _, _, _, historical_period in iter_period_cells(
        values,
        marker_row + 1,
        period_context["start_day"],
    ):
        if historical_period["key"] == wanted_key:
            return True

    return False


def copy_range_to_rows(worksheet, source_start_row, source_end_row, start_col,
                       end_col, destination_start_row):
    height = source_end_row - source_start_row + 1
    destination_end_row = destination_start_row + height - 1
    metadata_requests = []
    if getattr(worksheet, '_sem_platform_display', False):
        states = read_campaign_states(worksheet, start_col + 1, refresh=True)
        for row, state in states.items():
            if source_start_row <= row <= source_end_row:
                metadata_requests.extend(state_requests(
                    worksheet.id, destination_start_row + row - source_start_row, state,
                ))

    worksheet.spreadsheet.batch_update({
        "requests": [
            {
                "insertDimension": {
                    "range": {
                        "sheetId": worksheet.id,
                        "dimension": "ROWS",
                        "startIndex": destination_start_row - 1,
                        "endIndex": destination_end_row,
                    },
                    "inheritFromBefore": True,
                }
            },
            {
                "copyPaste": {
                    "source": {
                        "sheetId": worksheet.id,
                        "startRowIndex": source_start_row - 1,
                        "endRowIndex": source_end_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "destination": {
                        "sheetId": worksheet.id,
                        "startRowIndex": destination_start_row - 1,
                        "endRowIndex": destination_end_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "pasteType": "PASTE_NORMAL",
                    "pasteOrientation": "NORMAL",
                }
            },
        ] + metadata_requests
    })
    invalidate_worksheet_values_cache(worksheet)
    worksheet._sem_platform_states = None

    return destination_end_row


def historical_copy_end_row(block, total_row, campaign_count):
    """Una campana se archiva sin la fila de total redundante."""
    if campaign_count == 1:
        return block["header_row"] + 1

    return total_row


def archive_live_block_if_needed(worksheet, block, period_context):
    values = block["values"]
    columns = block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    data_start_row = block["header_row"] + 1
    marker_row = block.get("historical_marker_row") or find_historical_marker_row(values)

    try:
        total_row = find_total_row(
            values,
            block["header_row"],
            start_col,
            end_col,
            marker_row - 1,
        )
    except ValueError:
        total_row = max(block["header_row"] + 1, marker_row - 2)

    period_value = ""

    if len(values) >= block["period_row"]:
        period_row_values = values[block["period_row"] - 1]
        if len(period_row_values) >= block["period_col"]:
            period_value = period_row_values[block["period_col"] - 1]

    live_period = parse_period_identity(
        period_value,
        period_context["start_day"],
    )
    campaign_count = count_campaign_rows(
        values,
        data_start_row,
        total_row,
        columns["campaign_name"],
    )

    if not live_period:
        if campaign_count:
            raise SystemExit(
                "El bloque vivo tiene campanas pero no se pudo leer su periodo. "
                "No archivo ni sobrescribo para no perder historico."
            )

        return {
            "archived": False,
            "reason": "bloque vivo sin periodo y sin campanas",
        }

    if live_period["key"] == period_context["key"]:
        return {
            "archived": False,
            "reason": f"el bloque vivo ya es {period_context['label']}",
        }

    reference_day = period_context["query_end_day"]
    same_active_range = (
        live_period["mode"] == "range"
        and period_context["mode"] == "range"
        and live_period["start_day"] <= reference_day <= live_period["end_day"]
        and period_context["start_day"] <= reference_day <= period_context["end_day"]
    )

    if same_active_range:
        return {
            "archived": False,
            "reason": (
                "el rango vigente se ha redefinido y ambos rangos contienen "
                f"{reference_day.isoformat()}"
            ),
        }

    if live_period["start_day"] > period_context["start_day"]:
        raise SystemExit(
            "El periodo del bloque vivo es posterior al periodo actual "
            f"({live_period['label']} > {period_context['label']}). "
            "No continuo para evitar sobrescribir datos."
        )

    if historical_period_exists(values, marker_row, live_period):
        return {
            "archived": False,
            "reason": f"{live_period['label']} ya existe en historico",
        }

    destination_start_row = find_historical_append_row(
        values,
        marker_row,
        start_col,
        end_col,
        live_period,
    )
    source_start_row = block["period_row"]
    # En historico, una sola campana ya representa el total. Se copia solo
    # periodo, cabecera y campana para no repetir la misma cifra en otra fila.
    source_end_row = historical_copy_end_row(
        block,
        total_row,
        campaign_count,
    )
    destination_end_row = copy_range_to_rows(
        worksheet,
        source_start_row,
        source_end_row,
        start_col,
        end_col,
        destination_start_row,
    )
    destination_header_row = destination_start_row + 1
    destination_data_start_row = destination_header_row + 1
    if campaign_count != 1:
        archived_campaign_rows = extract_campaign_rows_from_block(
            block["values"],
            columns,
            data_start_row,
            data_start_row + campaign_count - 1,
        )
        destination_total_row = destination_end_row
        update_total_formulas(
            worksheet,
            columns,
            start_col,
            end_col,
            destination_data_start_row,
            campaign_count,
            destination_total_row,
            conversion_total=sum(
                parse_sheet_number(
                    block["values"][block["header_row"] + offset][
                        columns["conversions"] - 1
                    ]
                    if (
                        len(block["values"]) > block["header_row"] + offset
                        and len(
                            block["values"][block["header_row"] + offset]
                        ) >= columns["conversions"]
                    )
                    else 0
                )
                for offset in range(campaign_count)
            ),
            metric_totals=summarize_campaign_metrics(
                archived_campaign_rows
            ),
        )
        apply_live_block_borders(
            worksheet,
            columns,
            start_col,
            end_col,
            destination_header_row,
            destination_data_start_row,
            campaign_count,
            destination_total_row,
        )

    apply_historical_side_band(
        worksheet,
        marker_row,
        destination_end_row,
    )

    return {
        "archived": True,
        "period": live_period["label"],
        "range": (
            f"{gspread.utils.rowcol_to_a1(destination_start_row, start_col)}:"
            f"{gspread.utils.rowcol_to_a1(destination_end_row, end_col)}"
        ),
        "rows_inserted": source_end_row - source_start_row + 1,
    }


def ensure_rows_before_total(worksheet, data_start_row, total_row, campaign_count):
    # Entre las campañas y el total debe quedar exactamente una fila vacía.
    required_rows_before_total = campaign_count + 1
    current_rows_before_total = total_row - data_start_row

    if current_rows_before_total == required_rows_before_total:
        return total_row, 0, 0

    if current_rows_before_total > required_rows_before_total:
        desired_total_row = data_start_row + required_rows_before_total
        delete_start_row = desired_total_row
        delete_end_row = total_row - 1
        rows_to_delete = delete_end_row - delete_start_row + 1
        worksheet.delete_rows(delete_start_row, delete_end_row)
        invalidate_worksheet_values_cache(worksheet)
        return desired_total_row, 0, rows_to_delete

    rows_to_insert = required_rows_before_total - current_rows_before_total
    blank_rows = [
        ["" for _ in range(worksheet.col_count)]
        for _ in range(rows_to_insert)
    ]

    worksheet.insert_rows(
        blank_rows,
        row=total_row,
        value_input_option="USER_ENTERED",
        inherit_from_before=True,
    )
    invalidate_worksheet_values_cache(worksheet)

    return total_row + rows_to_insert, rows_to_insert, 0


def compact_historical_total_spacing(
    worksheet,
    start_col=None,
    end_col=None,
    mutation_batch=None,
):
    """Deja una unica fila vacia antes de cada total historico heredado."""
    values = get_worksheet_values(worksheet)
    marker_row = find_historical_marker_row(values)
    period_rows = [
        row_index
        for row_index in range(marker_row + 1, len(values) + 1)
        if any(is_period_cell(value) for value in values[row_index - 1])
    ]
    deletions = []

    for index, period_row in enumerate(period_rows):
        header_row = period_row + 1
        if header_row > len(values) or not looks_like_campaign_header(
            values[header_row - 1]
        ):
            continue

        next_period_row = (
            period_rows[index + 1]
            if index + 1 < len(period_rows)
            else None
        )
        stop_row = next_period_row - 1 if next_period_row else len(values)
        header_lookup = normalized_header_lookup(values[header_row - 1])
        campaign_col = next(
            (
                header_lookup[normalize_text(alias)]
                for alias in HEADER_ALIASES["campaign_name"]
                if normalize_text(alias) in header_lookup
            ),
            None,
        )
        if campaign_col is None:
            continue

        try:
            total_row = find_total_row(
                values,
                header_row,
                campaign_col,
                len(values[header_row - 1]),
                stop_row,
            )
        except ValueError:
            continue

        campaign_count = 0
        for row_index in range(header_row + 1, total_row):
            row = values[row_index - 1]
            campaign_name = (
                row[campaign_col - 1]
                if len(row) >= campaign_col
                else ""
            )
            if not str(campaign_name).strip():
                break
            campaign_count += 1

        desired_total_row = header_row + campaign_count + 2
        if total_row > desired_total_row:
            deletions.append((desired_total_row, total_row - 1))

    deleted_rows = 0
    for start_row, end_row in reversed(deletions):
        worksheet.delete_rows(start_row, end_row)
        deleted_rows += end_row - start_row + 1

    if deleted_rows:
        invalidate_worksheet_values_cache(worksheet)
        refreshed_values = get_worksheet_values(worksheet, refresh=True)
        refreshed_period_rows = [
            row_index
            for row_index in range(marker_row + 1, len(refreshed_values) + 1)
            if any(
                is_period_cell(value)
                for value in refreshed_values[row_index - 1]
            )
        ]
        if refreshed_period_rows and start_col is not None and end_col is not None:
            side_band_end_row = find_historical_block_end_row(
                refreshed_values,
                refreshed_period_rows[-1],
                start_col,
                end_col,
            )
            apply_historical_side_band(
                worksheet,
                marker_row,
                side_band_end_row,
                mutation_batch,
            )

    return {
        "blocks_compacted": len(deletions),
        "deleted_rows": deleted_rows,
    }


def find_missing_historical_total_blocks(values):
    """Localiza historicos heredados con varias campanas y sin total."""
    marker_row = find_historical_marker_row(values)
    period_rows = [
        row_index
        for row_index in range(marker_row + 1, len(values) + 1)
        if any(is_period_cell(value) for value in values[row_index - 1])
    ]
    missing_blocks = []

    for index, period_row in enumerate(period_rows):
        header_row = period_row + 1
        if header_row > len(values) or not looks_like_campaign_header(
            values[header_row - 1]
        ):
            continue

        header_lookup = normalized_header_lookup(values[header_row - 1])
        campaign_col = next(
            (
                header_lookup[normalize_text(alias)]
                for alias in HEADER_ALIASES["campaign_name"]
                if normalize_text(alias) in header_lookup
            ),
            None,
        )
        if campaign_col is None:
            continue

        next_period_row = (
            period_rows[index + 1]
            if index + 1 < len(period_rows)
            else None
        )
        stop_row = next_period_row - 1 if next_period_row else len(values)
        campaign_count = 0
        for row_index in range(header_row + 1, stop_row + 1):
            row = values[row_index - 1]
            campaign_name = (
                row[campaign_col - 1]
                if len(row) >= campaign_col
                else ""
            )
            if not str(campaign_name).strip():
                break
            campaign_count += 1

        if campaign_count < 2:
            continue

        try:
            find_total_row(
                values,
                header_row,
                1,
                max(1, len(values[header_row - 1])),
                stop_row,
            )
            continue
        except ValueError:
            pass

        period_col = next(
            col_index
            for col_index, value in enumerate(
                values[period_row - 1],
                start=1,
            )
            if is_period_cell(value)
        )
        period_label = str(
            values[period_row - 1][period_col - 1]
        ).strip()
        missing_blocks.append({
            "period_row": period_row,
            "period_col": period_col,
            "header_row": header_row,
            "campaign_count": campaign_count,
            "period_label": period_label,
        })

    return missing_blocks


def queue_total_row_format_copy(
    worksheet,
    live_block,
    historical_block,
    total_row,
    mutation_batch=None,
):
    """Copia solo el formato de la fila total viva al total historico."""
    live_columns = live_block["columns"]
    historical_columns = historical_block["columns"]
    live_total_row = find_total_row(
        live_block["values"],
        live_block["header_row"],
        min(live_columns.values()),
        max(live_columns.values()),
        live_block.get("historical_marker_row"),
    )
    start_col = min(historical_columns.values())
    end_col = max(historical_columns.values())
    queue_format_requests(
        worksheet,
        [{
            "copyPaste": {
                "source": {
                    "sheetId": worksheet.id,
                    "startRowIndex": live_total_row - 1,
                    "endRowIndex": live_total_row,
                    "startColumnIndex": min(live_columns.values()) - 1,
                    "endColumnIndex": max(live_columns.values()),
                },
                "destination": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "pasteType": "PASTE_FORMAT",
                "pasteOrientation": "NORMAL",
            }
        }],
        mutation_batch,
    )


def repair_missing_historical_totals(
    worksheet,
    live_block,
    mutation_batch=None,
):
    """Repara todos los totales historicos omitidos, no solo el mes anterior."""
    repaired = []
    values = get_worksheet_values(worksheet)
    missing_blocks = find_missing_historical_total_blocks(values)

    # Insertar filas cambia las coordenadas reales de la hoja. No se pueden
    # dejar escrituras A1 pendientes mientras se reparan varios bloques: una
    # insercion posterior podria hacer que el lote terminase escribiendo un
    # total encima de la campana del mes siguiente. Vaciamos primero el lote
    # y hacemos esta reparacion estructural de forma secuencial.
    repair_batch = mutation_batch
    if missing_blocks and mutation_batch is not None:
        mutation_batch.flush()
        repair_batch = None

    # El batch todavia no es visible en una lectura remota. Procesar una foto
    # fija evita detectar varias veces el mismo total pendiente. El orden
    # inverso mantiene validas las filas superiores si un bloque inferior
    # necesita insertar espacio.
    for missing in reversed(missing_blocks):
        historical_block = build_compatible_historical_block(
            worksheet,
            values,
            missing["period_row"],
            missing["period_col"],
            missing["header_row"],
            live_block,
        )
        historical_block = ensure_historical_total_row(
            worksheet,
            historical_block,
        )
        columns = historical_block["columns"]
        start_col = min(columns.values())
        end_col = max(columns.values())
        total_row = find_total_row(
            historical_block["values"],
            historical_block["header_row"],
            start_col,
            end_col,
            find_next_period_row(
                historical_block["values"],
                historical_block["period_row"],
            ),
        )
        data_start_row = historical_block["header_row"] + 1

        queue_total_row_format_copy(
            worksheet,
            live_block,
            historical_block,
            total_row,
            repair_batch,
        )
        repaired_campaign_rows = extract_campaign_rows_from_block(
            values,
            columns,
            data_start_row,
            data_start_row + missing["campaign_count"] - 1,
        )
        update_total_formulas(
            worksheet,
            columns,
            start_col,
            end_col,
            data_start_row,
            missing["campaign_count"],
            total_row,
            repair_batch,
            conversion_total=sum(
                parse_sheet_number(
                    values[row_index - 1][columns["conversions"] - 1]
                    if len(values[row_index - 1]) >= columns["conversions"]
                    else 0
                )
                for row_index in range(
                    data_start_row,
                    data_start_row + missing["campaign_count"],
                )
            ),
            metric_totals=summarize_campaign_metrics(
                repaired_campaign_rows
            ),
        )
        apply_live_block_borders(
            worksheet,
            columns,
            start_col,
            end_col,
            historical_block["header_row"],
            data_start_row,
            missing["campaign_count"],
            total_row,
            repair_batch,
        )
        repaired.append({
            "period": missing["period_label"],
            "campaign_count": missing["campaign_count"],
            "total_row": total_row,
        })

    repaired.reverse()

    if repaired:
        values = get_worksheet_values(worksheet, refresh=True)
        marker_row = find_historical_marker_row(values)
        period_rows = [
            row_index
            for row_index in range(marker_row + 1, len(values) + 1)
            if any(is_period_cell(value) for value in values[row_index - 1])
        ]
        if period_rows:
            columns = live_block["columns"]
            side_band_end_row = find_historical_block_end_row(
                values,
                period_rows[-1],
                min(columns.values()),
                max(columns.values()),
            )
            apply_historical_side_band(
                worksheet,
                marker_row,
                side_band_end_row,
                repair_batch,
            )

    return repaired


def update_total_formulas(worksheet, columns, start_col, end_col, data_start_row,
                          campaign_count, total_row, mutation_batch=None,
                          apply_formats=True, conversion_total=None,
                          metric_totals=None):
    width = end_col - start_col + 1
    total_row_values = ["" for _ in range(width)]

    sum_end_row = max(data_start_row, data_start_row + campaign_count - 1)
    def col_letter(column_key):
        return gspread.utils.rowcol_to_a1(1, columns[column_key]).rstrip("1")

    def set_sum(column_key):
        if column_key not in columns:
            return

        letter = col_letter(column_key)
        total_row_values[columns[column_key] - start_col] = (
            f"=SUM({letter}{data_start_row}:{letter}{sum_end_row})"
        )

    clicks_col = col_letter("clicks")
    cost_col = col_letter("cost")
    conversions_col = col_letter("conversions")

    total_row_values[columns["clicks"] - start_col] = (
        f"=SUM({clicks_col}{data_start_row}:{clicks_col}{sum_end_row})"
    )
    total_row_values[columns["ctr"] - start_col] = "Total coste campa\u00f1as:"
    total_row_values[columns["cost"] - start_col] = (
        f"=SUM({cost_col}{data_start_row}:{cost_col}{sum_end_row})"
    )
    total_row_values[columns["conversions"] - start_col] = (
        f"=SUM({conversions_col}{data_start_row}:{conversions_col}{sum_end_row})"
    )
    total_row_values[columns["cost_per_conversion"] - start_col] = (
        f'=IFERROR({cost_col}{total_row}/{conversions_col}{total_row};"")'
    )

    set_sum("impressions")

    # La etiqueta suele ocupar una celda combinada CTR:CPC. La API de Values
    # interpreta la celda secundaria de la combinacion como otro destino; si
    # se envia la fila completa, ese valor vacio puede borrar la etiqueta que
    # acaba de escribirse. Se divide la fila y se omite la celda secundaria.
    label_col = columns["ctr"]
    if start_col < label_col:
        queue_values_update(
            worksheet,
            (
                f"{gspread.utils.rowcol_to_a1(total_row, start_col)}:"
                f"{gspread.utils.rowcol_to_a1(total_row, label_col - 1)}"
            ),
            [total_row_values[:label_col - start_col]],
            mutation_batch,
        )
    queue_values_update(
        worksheet,
        gspread.utils.rowcol_to_a1(total_row, label_col),
        [[total_row_values[label_col - start_col]]],
        mutation_batch,
    )
    metrics_start_col = max(label_col + 2, columns["cost"])
    if metrics_start_col <= end_col:
        queue_values_update(
            worksheet,
            (
                f"{gspread.utils.rowcol_to_a1(total_row, metrics_start_col)}:"
                f"{gspread.utils.rowcol_to_a1(total_row, end_col)}"
            ),
            [total_row_values[metrics_start_col - start_col:]],
            mutation_batch,
        )
    if apply_formats:
        apply_total_value_formats(
            worksheet,
            columns,
            total_row,
            mutation_batch,
            conversion_value=conversion_total,
            metric_values=metric_totals,
        )

    if getattr(worksheet, '_sem_platform_display', False):
        label_range = {'sheetId': worksheet.id, 'startRowIndex': total_row - 1,
                       'endRowIndex': total_row, 'startColumnIndex': label_col - 1,
                       'endColumnIndex': columns['average_cpc']}
        queue_format_requests(worksheet, [{
            'mergeCells': {
                'range': label_range,
                'mergeType': 'MERGE_ALL',
            },
        }, {
            'repeatCell': {'range': label_range,
                'cell': {'userEnteredFormat': {
                    'backgroundColor': {'red': 0.9764706, 'green': 0.79607844, 'blue': 0.6117647},
                    'horizontalAlignment': 'RIGHT', 'textFormat': {'bold': True},
                }},
                'fields': 'userEnteredFormat.backgroundColor,userEnteredFormat.horizontalAlignment,userEnteredFormat.textFormat.bold'},
        }, {
            'repeatCell': {'range': dict(label_range,
                startColumnIndex=columns['campaign_status'] - 1, endColumnIndex=columns['campaign_status']),
                'cell': {'userEnteredFormat': {'backgroundColor': {'red': 1, 'green': 1, 'blue': 1}}},
                'fields': 'userEnteredFormat.backgroundColor'},
        }], mutation_batch)
    return col_letter("cost_per_conversion")


def apply_total_value_formats(
    worksheet,
    columns,
    total_row,
    mutation_batch=None,
    conversion_value=None,
    metric_values=None,
):
    metric_values = dict(metric_values or {})
    if conversion_value is not None:
        metric_values.setdefault("conversions", conversion_value)

    number_format_by_key = {
        "clicks": ("NUMBER", "#,##0"),
        "average_cpc": (
            "NUMBER",
            optional_decimal_format_for_value(
                metric_values.get("average_cpc", 0)
            ),
        ),
        "cost": (
            "CURRENCY",
            optional_currency_format_for_value(metric_values.get("cost", 0)),
        ),
        "conversions": (
            "NUMBER",
            optional_decimal_format_for_value(
                metric_values.get("conversions", 0)
            ),
        ),
        "cost_per_conversion": (
            "CURRENCY",
            optional_currency_format_for_value(
                metric_values.get("cost_per_conversion", 0)
            ),
        ),
        "impressions": ("NUMBER", "#,##0"),
        "daily_budget": (
            "NUMBER",
            optional_decimal_format_for_value(
                metric_values.get("daily_budget", 0)
            ),
        ),
    }
    requests = []

    for key, (format_type, pattern) in number_format_by_key.items():
        if key not in columns:
            continue

        col_index = columns[key]
        requests.append(
            {
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": total_row - 1,
                        "endRowIndex": total_row,
                        "startColumnIndex": col_index - 1,
                        "endColumnIndex": col_index,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {
                                "type": format_type,
                                "pattern": pattern,
                            }
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            }
        )

    queue_format_requests(worksheet, requests, mutation_batch)


def find_columns_for_total_row(values, total_row):
    """Busca la cabecera mas cercana asociada a una fila de total."""
    required = {"cost", "cost_per_conversion"}
    for row_index in range(total_row - 1, 0, -1):
        try:
            columns = find_header_columns(values[row_index - 1])
        except ValueError:
            continue
        if required.issubset(columns):
            return columns
    return None


def apply_existing_total_currency_formats(
    worksheet,
    mutation_batch=None,
    metric_totals_by_row=None,
):
    """Anade la unidad EUR a todos los totales vivos e historicos existentes."""
    values = get_worksheet_values(worksheet)
    requests = []
    formatted_rows = []
    metric_totals_by_row = metric_totals_by_row or {}

    for total_row, row in enumerate(values, start=1):
        if not any(is_total_cost_cell(value) for value in row):
            continue

        columns = find_columns_for_total_row(values, total_row)
        if not columns:
            continue

        for key in ("cost", "cost_per_conversion"):
            col_index = columns[key]
            value = metric_totals_by_row.get(total_row, {}).get(
                key,
                row[col_index - 1] if len(row) >= col_index else 0,
            )
            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": total_row - 1,
                        "endRowIndex": total_row,
                        "startColumnIndex": col_index - 1,
                        "endColumnIndex": col_index,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "numberFormat": {
                                "type": "CURRENCY",
                                "pattern": optional_currency_format_for_value(
                                    value
                                ),
                            }
                        }
                    },
                    "fields": "userEnteredFormat.numberFormat",
                }
            })
        formatted_rows.append(total_row)

    queue_format_requests(worksheet, requests, mutation_batch)
    return formatted_rows


def extract_campaign_rows_from_block(
    values,
    columns,
    data_start_row,
    block_stop_row,
):
    campaign_rows = []
    optional_keys = {
        "cost_per_conversion",
        "top_impression_share",
        "absolute_top_impression_share",
    }
    header_row = values[data_start_row - 2] if data_start_row >= 2 else []
    cost_per_conversion_col = columns["cost_per_conversion"]
    cost_per_conversion_header = (
        header_row[cost_per_conversion_col - 1]
        if len(header_row) >= cost_per_conversion_col
        else ""
    )
    migrate_conversion_value = is_legacy_conversion_value_header(
        cost_per_conversion_header
    )

    for row_index in range(data_start_row, block_stop_row + 1):
        row = values[row_index - 1]
        campaign_name_col = columns["campaign_name"]
        campaign_name = (
            row[campaign_name_col - 1]
            if len(row) >= campaign_name_col
            else ""
        )
        if not str(campaign_name).strip():
            break
        if any(is_total_cost_cell(value) for value in row):
            break
        if any(is_period_cell(value) for value in row):
            break

        campaign = {
            "campaign_id": f"sheet-row:{row_index}",
            "campaign_name": str(campaign_name).strip(),
        }
        for key, col_index in columns.items():
            if key == "campaign_name":
                continue
            value = row[col_index - 1] if len(row) >= col_index else ""
            if key == "campaign_status":
                campaign[key] = str(value).strip()
            elif key in optional_keys and not str(value).strip():
                campaign[key] = ""
            else:
                campaign[key] = parse_sheet_number(value)

        campaign["raw_cost"] = parse_sheet_number(campaign.get("cost", 0))
        if migrate_conversion_value:
            conversions = parse_sheet_number(campaign.get("conversions", 0))
            campaign["cost_per_conversion"] = (
                campaign["raw_cost"] / conversions
                if conversions
                else ""
            )
        campaign_rows.append(campaign)

    return campaign_rows


def canonical_period_label(period_identity):
    if period_identity["mode"] == "range":
        return range_label_for_days(
            period_identity["start_day"],
            period_identity["end_day"],
        )
    return month_label_for_day(period_identity["start_day"])


def existing_period_label_updates(values):
    entries = []
    for row_index, row in enumerate(values, start=1):
        for col_index, value in enumerate(row, start=1):
            if not is_period_cell(value):
                continue
            start_month = (
                parse_period_month_number(value)
                or date_range_start_month(value)
            )
            entries.append({
                "row": row_index,
                "col": col_index,
                "value": value,
                "month": start_month,
                "explicit_start": (
                    parse_period_identity(value, today_in_spain())
                    if extract_explicit_year(value) is not None
                    else None
                ),
            })
            break

    historical_marker_row = None
    try:
        historical_marker_row = find_historical_marker_row(values)
    except ValueError:
        pass

    historical_entries = [
        entry
        for entry in entries
        if historical_marker_row is not None
        and entry["row"] > historical_marker_row
    ]
    updates = []
    for entry in entries:
        identity = entry["explicit_start"]
        if identity is None and entry in historical_entries and entry["month"]:
            inferred_year = infer_historical_month_year(
                historical_entries,
                entry["row"],
            )
            if inferred_year:
                reference_day = date(
                    inferred_year,
                    entry["month"],
                    1,
                )
                identity = parse_period_identity(
                    entry["value"],
                    reference_day,
                )
                if identity is None:
                    identity = {
                        "mode": "month",
                        "start_day": reference_day,
                    }

        if identity is None:
            continue
        label = canonical_period_label(identity)
        if str(entry["value"]).strip() == label:
            continue
        updates.append({
            "cell": gspread.utils.rowcol_to_a1(entry["row"], entry["col"]),
            "value": label,
        })

    return updates


def normalize_existing_standard_block_formats(
    worksheet,
    mutation_batch=None,
    historical_only=False,
):
    """Normaliza formatos de todos los bloques estandar ya archivados."""
    values = get_worksheet_values(worksheet)
    first_header_row = (
        find_historical_marker_row(values) + 1 if historical_only else 1
    )
    header_rows = [
        row_index
        for row_index, row in enumerate(values, start=1)
        if row_index >= first_header_row and looks_like_campaign_header(row)
    ]
    numeric_values = worksheet.get_all_values(value_render_option="UNFORMATTED_VALUE")
    # Validate every block before queuing changes; formatted cells can hide corruption.
    for header_row in header_rows:
        try:
            columns = find_header_columns(values[header_row - 1])
        except ValueError:
            continue
        stop = (find_next_period_row(values, header_row) or len(values) + 1) - 1
        for campaign in extract_campaign_rows_from_block(
            numeric_values, columns, header_row + 1, stop,
        ):
            for metric in ("clicks", "impressions"):
                number = float(campaign[metric])
                if not number.is_integer() or number < 0:
                    raise ValueError(
                        f"{worksheet.title}: {metric} no entero en "
                        f"{campaign['campaign_id']}; revisar respaldo antes de normalizar"
                    )
    result = {
        "blocks": 0,
        "campaigns": 0,
        "totals": 0,
        "sorted_blocks": 0,
        "period_labels": 0,
    }

    period_updates = existing_period_label_updates(values)
    if historical_only:
        period_updates = [
            update for update in period_updates
            if gspread.utils.a1_to_rowcol(update["cell"])[0] >= first_header_row
        ]
    for update in period_updates:
        queue_values_update(
            worksheet,
            update["cell"],
            [[update["value"]]],
            mutation_batch,
        )
    result["period_labels"] = len(period_updates)

    for header_position, header_row in enumerate(header_rows):
        try:
            columns = find_header_columns(values[header_row - 1])
        except ValueError:
            continue

        start_col = min(columns.values())
        end_col = max(columns.values())
        standard_header = build_standard_header_slice(
            columns,
            start_col,
            end_col,
            getattr(worksheet, '_sem_platform_display', False),
        )
        current_header = values[header_row - 1][start_col - 1:end_col]
        cost_per_conversion_header = values[header_row - 1][
            columns["cost_per_conversion"] - 1
        ]
        requires_metric_migration = is_legacy_conversion_value_header(
            cost_per_conversion_header
        )
        if current_header != standard_header:
            queue_values_update(
                worksheet,
                (
                    f"{gspread.utils.rowcol_to_a1(header_row, start_col)}:"
                    f"{gspread.utils.rowcol_to_a1(header_row, end_col)}"
                ),
                [standard_header],
                mutation_batch,
            )

        next_header_row = (
            header_rows[header_position + 1]
            if header_position + 1 < len(header_rows)
            else len(values) + 1
        )
        next_period_row = find_next_period_row(values, header_row)
        block_stop_row = min(
            next_header_row,
            next_period_row or len(values) + 1,
        ) - 1
        data_start_row = header_row + 1
        campaign_rows = extract_campaign_rows_from_block(
            numeric_values,
            columns,
            data_start_row,
            block_stop_row,
        )

        if not campaign_rows:
            continue

        campaign_rows = restore_platform_campaigns(worksheet, campaign_rows, columns['campaign_status'])
        ordered_rows = sort_campaign_rows(campaign_rows)
        order_changed = [row["campaign_id"] for row in ordered_rows] != [
            row["campaign_id"] for row in campaign_rows
        ]
        statuses_need_translation = any(
            numeric_values[data_start_row - 1 + index][columns['campaign_status'] - 1]
            != row.get('display_platform', status_display_label(row['campaign_status']))
            for index, row in enumerate(campaign_rows)
        )
        if order_changed or requires_metric_migration or statuses_need_translation:
            queue_values_update(
                worksheet,
                (
                    f"{gspread.utils.rowcol_to_a1(data_start_row, start_col)}:"
                    f"{gspread.utils.rowcol_to_a1(data_start_row + len(ordered_rows) - 1, end_col)}"
                ),
                build_output_matrix(
                    ordered_rows,
                    columns,
                    start_col,
                    end_col,
                ),
                mutation_batch,
            )
            if order_changed:
                result["sorted_blocks"] += 1
        campaign_rows = ordered_rows

        data_end_row = data_start_row + len(campaign_rows) - 1
        apply_campaign_value_formats(
            worksheet,
            columns,
            data_start_row,
            campaign_rows,
            data_end_row,
            mutation_batch,
            apply_number_formats=True,
            apply_status_formats=True,
        )
        result["blocks"] += 1
        result["campaigns"] += len(campaign_rows)

        try:
            total_row = find_total_row(
                values,
                header_row,
                min(columns.values()),
                max(columns.values()),
                stop_row=block_stop_row,
            )
        except ValueError:
            apply_single_campaign_historical_borders(
                worksheet, start_col, end_col, header_row,
                data_end_row, data_end_row, mutation_batch,
            )
            continue

        total_values = values[total_row - 1]
        is_legacy_totals_row = any(
            normalize_text(value) == "totales"
            for value in total_values[start_col - 1:end_col]
        )
        if is_legacy_totals_row:
            queue_legacy_historical_historical_total(
                worksheet, columns, start_col, end_col, data_start_row,
                len(campaign_rows), total_row, mutation_batch,
                metric_totals=summarize_campaign_metrics(campaign_rows),
            )
            apply_single_campaign_historical_borders(
                worksheet, start_col, end_col, header_row,
                data_end_row, data_end_row, mutation_batch,
            )
        else:
            update_total_formulas(
                worksheet,
                columns,
                start_col,
                end_col,
                data_start_row,
                len(campaign_rows),
                total_row,
                mutation_batch,
                metric_totals=summarize_campaign_metrics(campaign_rows),
            )
            apply_live_block_borders(
                worksheet, columns, start_col, end_col, header_row,
                data_start_row, len(campaign_rows), total_row, mutation_batch,
            )
        result["totals"] += 1

    return result


def normalize_enabled_conditional_format_colors(
    spreadsheet,
    selected_worksheets,
    mutation_batch=None,
):
    """Traduce condiciones de estado y conserva el verde de los activos."""
    selected_sheet_ids = {worksheet.id for worksheet in selected_worksheets}
    metadata = spreadsheet.fetch_sheet_metadata(params={
        "includeGridData": "false",
        "fields": "sheets(properties(sheetId,title),conditionalFormats)",
    })
    requests = []
    changed = []

    for sheet in metadata.get("sheets", []):
        properties = sheet.get("properties", {})
        sheet_id = properties.get("sheetId")
        if sheet_id not in selected_sheet_ids:
            continue

        for rule_index, rule in enumerate(sheet.get("conditionalFormats", [])):
            boolean_rule = rule.get("booleanRule", {})
            condition = boolean_rule.get("condition", {})
            translated_condition = translate_status_condition(condition)
            condition_values = [
                normalize_text(value.get("userEnteredValue"))
                for value in condition.get("values", [])
            ]
            is_active_rule = condition.get("type") in {"TEXT_EQ", "TEXT_CONTAINS"} and any(
                canonical_status_key(value) == "enabled" for value in condition_values
            )
            if translated_condition == condition and not is_active_rule:
                continue

            format_config = boolean_rule.get("format", {})
            current_color = (
                format_config.get("backgroundColorStyle", {})
                .get("rgbColor")
                or format_config.get("backgroundColor", {})
            )
            expected_color = ACCOUNT_STATUS_ENABLED_COLOR
            if translated_condition == condition and all(
                abs(current_color.get(channel, 0) - expected_color[channel])
                < 0.000001
                for channel in ("red", "green", "blue")
            ):
                continue

            updated_rule = json.loads(json.dumps(rule))
            updated_rule["booleanRule"]["condition"] = translated_condition
            updated_format = updated_rule["booleanRule"].setdefault(
                "format",
                {},
            )
            if is_active_rule:
                updated_format["backgroundColor"] = dict(expected_color)
                updated_format["backgroundColorStyle"] = {
                    "rgbColor": dict(expected_color),
                }
            requests.append({
                "updateConditionalFormatRule": {
                    "sheetId": sheet_id,
                    "index": rule_index,
                    "rule": updated_rule,
                }
            })
            changed.append({
                "worksheet": properties.get("title", ""),
                "rule_index": rule_index,
            })

    if mutation_batch is not None:
        mutation_batch.add_requests(requests)
    elif requests:
        spreadsheet.batch_update({"requests": requests})
    return changed


def apply_live_block_borders(
    worksheet,
    columns,
    start_col,
    end_col,
    header_row,
    data_start_row,
    campaign_count,
    total_row,
    mutation_batch=None,
):
    border = {
        "style": "SOLID",
        "width": 1,
        "color": {
            "red": 0,
            "green": 0,
            "blue": 0,
        },
    }
    no_border = {"style": "NONE"}
    last_campaign_row = max(header_row, data_start_row + campaign_count - 1)
    total_start_col = columns["clicks"]
    total_formula_keys = [
        "clicks",
        "cost",
        "conversions",
        "cost_per_conversion",
        "impressions",
    ]
    total_end_col = max(
        columns[key]
        for key in total_formula_keys
        if key in columns
    )

    queue_format_requests(
        worksheet,
        [
            {
                "updateBorders": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": header_row - 1,
                        "endRowIndex": total_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "top": no_border,
                    "bottom": no_border,
                    "left": no_border,
                    "right": no_border,
                    "innerHorizontal": no_border,
                    "innerVertical": no_border,
                }
            },
            {
                "updateBorders": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": header_row - 1,
                        "endRowIndex": last_campaign_row,
                        "startColumnIndex": start_col - 1,
                        "endColumnIndex": end_col,
                    },
                    "top": border,
                    "bottom": border,
                    "left": border,
                    "right": border,
                    "innerHorizontal": border,
                    "innerVertical": border,
                }
            },
            {
                "updateBorders": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": total_row - 1,
                        "endRowIndex": total_row,
                        "startColumnIndex": total_start_col - 1,
                        "endColumnIndex": total_end_col,
                    },
                    "top": border,
                    "bottom": border,
                    "left": border,
                    "right": border,
                    "innerVertical": border,
                }
            },
        ],
        mutation_batch,
    )


def apply_single_campaign_historical_borders(
    worksheet,
    start_col,
    end_col,
    header_row,
    campaign_row,
    clear_end_row,
    mutation_batch=None,
):
    """Deja bordes solo en cabecera y campana, nunca en una fila de total."""
    border = {
        "style": "SOLID",
        "width": 1,
        "color": {"red": 0, "green": 0, "blue": 0},
    }
    no_border = {"style": "NONE"}
    requests = [
        {
            "updateBorders": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": header_row - 1,
                    "endRowIndex": clear_end_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "top": no_border,
                "bottom": no_border,
                "left": no_border,
                "right": no_border,
                "innerHorizontal": no_border,
                "innerVertical": no_border,
            }
        },
        {
            "updateBorders": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": header_row - 1,
                    "endRowIndex": campaign_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "top": border,
                "bottom": border,
                "left": border,
                "right": border,
                "innerHorizontal": border,
                "innerVertical": border,
            }
        },
    ]

    if clear_end_row > campaign_row:
        requests.insert(0, {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": campaign_row,
                    "endRowIndex": clear_end_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat",
            }
        })

    queue_format_requests(worksheet, requests, mutation_batch)


def apply_historical_side_band(
    worksheet,
    marker_row,
    end_row,
    mutation_batch=None,
):
    """Mantiene la banda gris y su borde derecho hasta el último histórico."""
    requests = [{
        "repeatCell": {
            "range": {
                "sheetId": worksheet.id,
                "startRowIndex": marker_row - 1,
                "endRowIndex": end_row,
                "startColumnIndex": 0,
                "endColumnIndex": 1,
            },
            "cell": {
                "userEnteredFormat": {
                    "backgroundColor": HISTORICAL_SIDE_BAND_COLOR,
                }
            },
            "fields": "userEnteredFormat.backgroundColor",
        }
    }, {
        "updateBorders": {
            "range": {
                "sheetId": worksheet.id,
                "startRowIndex": marker_row - 1,
                "endRowIndex": end_row,
                "startColumnIndex": 0,
                "endColumnIndex": 1,
            },
            "right": {"style": "SOLID", "color": {"red": 0, "green": 0, "blue": 0}},
        }
    }]

    if end_row < worksheet.row_count:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": end_row,
                    "endRowIndex": worksheet.row_count,
                    "startColumnIndex": 0,
                    "endColumnIndex": 1,
                },
                "cell": {
                    "userEnteredFormat": {}
                },
                "fields": (
                    "userEnteredFormat.backgroundColor,"
                    "userEnteredFormat.backgroundColorStyle"
                ),
            }
        })

    queue_format_requests(worksheet, requests, mutation_batch)


def apply_campaign_value_formats(
    worksheet,
    columns,
    data_start_row,
    campaign_rows,
    clear_end_row,
    mutation_batch=None,
    apply_number_formats=True,
    apply_status_formats=True,
):
    campaign_count = len(campaign_rows)
    if not apply_number_formats and not apply_status_formats:
        return

    status_col = columns["campaign_status"]
    metric_keys = (
        "clicks",
        "ctr",
        "average_cpc",
        "cost",
        "conversions",
        "cost_per_conversion",
        "impressions",
        "top_impression_share",
        "absolute_top_impression_share",
        "daily_budget",
    )
    requests = []

    if apply_status_formats:
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": data_start_row - 1,
                    "endRowIndex": clear_end_row,
                    "startColumnIndex": status_col - 1,
                    "endColumnIndex": status_col,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {
                            "red": 1,
                            "green": 1,
                            "blue": 1,
                        },
                        "textFormat": {
                            "bold": False,
                            "foregroundColor": {
                                "red": 0,
                                "green": 0,
                                "blue": 0,
                            },
                        },
                    }
                },
                "fields": (
                    "userEnteredFormat.backgroundColor,"
                    "userEnteredFormat.textFormat.bold,"
                    "userEnteredFormat.textFormat.foregroundColor"
                ),
            }
        })

    if apply_number_formats and campaign_count > 0:
        for key in metric_keys:
            if key not in columns:
                continue

            col_index = columns[key]
            patterns = [
                (
                    INTEGER_NUMBER_FORMAT
                    if key in {"clicks", "impressions"}
                    else optional_decimal_format_for_value(
                        campaign.get(key, 0)
                    )
                )
                for campaign in campaign_rows
            ]
            run_start = 0
            for index in range(1, campaign_count + 1):
                run_ended = (
                    index == campaign_count
                    or patterns[index] != patterns[run_start]
                )
                if not run_ended:
                    continue

                requests.append({
                    "repeatCell": {
                        "range": {
                            "sheetId": worksheet.id,
                            "startRowIndex": data_start_row - 1 + run_start,
                            "endRowIndex": data_start_row - 1 + index,
                            "startColumnIndex": col_index - 1,
                            "endColumnIndex": col_index,
                        },
                        "cell": {
                            "userEnteredFormat": {
                                "numberFormat": {
                                    "type": "NUMBER",
                                    "pattern": patterns[run_start],
                                }
                            }
                        },
                        "fields": "userEnteredFormat.numberFormat",
                    }
                })
                run_start = index

    if apply_status_formats:
        for index, campaign in enumerate(campaign_rows):
            # En la ficha con totales historicos heredados estas filas indican la fuente, no un estado Ads.
            # Se dejan con el formato neutro aplicado al rango completo.
            if campaign.get("source_customer_id") == "external":
                continue

            is_enabled = canonical_status_key(campaign["campaign_status"]) == "enabled"
            background_color = (
                ACCOUNT_STATUS_ENABLED_COLOR
                if is_enabled
                else {"red": 1, "green": 0, "blue": 0}
            )
            text_color = (
                {"red": 0, "green": 0, "blue": 0}
                if is_enabled
                else {"red": 1, "green": 1, "blue": 1}
            )
            row_index = data_start_row + index
            if campaign.get('display_platform'):
                requests.extend(state_requests(worksheet.id, row_index, campaign_state(campaign)))

            requests.append({
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": row_index - 1,
                        "endRowIndex": row_index,
                        "startColumnIndex": status_col - 1,
                        "endColumnIndex": status_col,
                    },
                    "cell": {
                        "userEnteredFormat": {
                            "backgroundColor": background_color,
                            "textFormat": {
                                "bold": False,
                                "foregroundColor": text_color,
                            },
                        }
                    },
                    "fields": (
                        "userEnteredFormat.backgroundColor,"
                        "userEnteredFormat.textFormat.bold,"
                        "userEnteredFormat.textFormat.foregroundColor"
                    ),
                }
            })

    queue_format_requests(worksheet, requests, mutation_batch)


def update_live_block(
    worksheet,
    campaign_rows,
    block,
    period_context,
    mutation_batch=None,
    force_formats=False,
):
    campaign_rows = sort_campaign_rows(list(campaign_rows))
    values = block["values"]
    columns = block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    data_start_row = block["header_row"] + 1
    marker_row = block.get("historical_marker_row")

    total_missing = False
    try:
        total_row = find_total_row(
            values,
            block["header_row"],
            start_col,
            end_col,
            marker_row - 1 if marker_row else None,
        )
    except ValueError:
        if not marker_row:
            raise

        total_missing = True
        total_row = max(block["header_row"] + 1, marker_row - 2)

    existing_campaign_count = count_campaign_rows(
        values,
        data_start_row,
        total_row,
        columns["campaign_name"],
    )
    existing_statuses = []
    for row_index in range(
        data_start_row,
        data_start_row + existing_campaign_count,
    ):
        row = values[row_index - 1] if len(values) >= row_index else []
        status_col = columns["campaign_status"]
        existing_statuses.append(
            row[status_col - 1] if len(row) >= status_col else ""
        )

    total_row, inserted_rows, deleted_rows = ensure_rows_before_total(
        worksheet,
        data_start_row,
        total_row,
        len(campaign_rows),
    )
    clear_end_row = total_row - 1
    obsolete_clear_ranges = clear_obsolete_live_columns(
        worksheet,
        block,
        total_row,
        mutation_batch,
    )

    period_label = period_context["label"]
    period_cell = gspread.utils.rowcol_to_a1(
        block["period_row"],
        block["period_col"],
    )

    clear_start = gspread.utils.rowcol_to_a1(data_start_row, start_col)
    clear_end = gspread.utils.rowcol_to_a1(clear_end_row, end_col)
    clear_range = f"{clear_start}:{clear_end}"

    output_matrix = build_output_matrix(
        campaign_rows,
        columns,
        start_col,
        end_col,
    )
    header_slice = build_standard_header_slice(columns, start_col, end_col, getattr(worksheet, '_sem_platform_display', False))
    current_header = (
        values[block["header_row"] - 1][start_col - 1:end_col]
        if len(values) >= block["header_row"]
        else []
    )
    header_changed = [normalize_text(value) for value in current_header] != [
        normalize_text(value) for value in header_slice
    ]
    structure_changed = any([
        total_missing,
        inserted_rows,
        deleted_rows,
        existing_campaign_count != len(campaign_rows),
        header_changed,
        obsolete_clear_ranges,
    ])
    new_statuses = [status_display_label(row["campaign_status"]) for row in campaign_rows]
    statuses_changed = structure_changed or (
        [normalize_text(value) for value in existing_statuses]
        != [normalize_text(value) for value in new_statuses]
    )

    queue_values_update(
        worksheet,
        period_cell,
        [[period_label]],
        mutation_batch,
    )
    queue_values_update(
        worksheet,
        (
            f"{gspread.utils.rowcol_to_a1(block['header_row'], start_col)}:"
            f"{gspread.utils.rowcol_to_a1(block['header_row'], end_col)}"
        ),
        [header_slice],
        mutation_batch,
    )

    width = end_col - start_col + 1
    data_matrix = [
        ["" for _ in range(width)]
        for _ in range(clear_end_row - data_start_row + 1)
    ]
    for index, output_row in enumerate(output_matrix):
        data_matrix[index] = output_row
    queue_values_update(
        worksheet,
        clear_range,
        data_matrix,
        mutation_batch,
    )

    if output_matrix:
        write_start = gspread.utils.rowcol_to_a1(data_start_row, start_col)
        write_end = gspread.utils.rowcol_to_a1(
            data_start_row + len(output_matrix) - 1,
            end_col,
        )
        write_range = f"{write_start}:{write_end}"
    else:
        write_range = "(sin campanas que escribir)"

    update_total_formulas(
        worksheet,
        columns,
        start_col,
        end_col,
        data_start_row,
        len(campaign_rows),
        total_row,
        mutation_batch,
        apply_formats=True,
        conversion_total=sum(
            parse_sheet_number(row.get("conversions", 0))
            for row in campaign_rows
        ),
        metric_totals=summarize_campaign_metrics(campaign_rows),
    )
    if structure_changed or force_formats:
        apply_live_block_borders(
            worksheet,
            columns,
            start_col,
            end_col,
            block["header_row"],
            data_start_row,
            len(campaign_rows),
            total_row,
            mutation_batch,
        )
    apply_campaign_value_formats(
        worksheet,
        columns,
        data_start_row,
        campaign_rows,
        clear_end_row,
        mutation_batch,
        apply_number_formats=True,
        apply_status_formats=True,
    )

    return {
        "period_cell": period_cell,
        "period_label": period_label,
        "clear_range": clear_range,
        "write_range": write_range,
        "header_row": block["header_row"],
        "period_row": block["period_row"],
        "total_row": total_row,
        "inserted_rows": inserted_rows,
        "deleted_rows": deleted_rows,
        "obsolete_clear_ranges": obsolete_clear_ranges,
        "formats_updated": True,
    }


def build_compatible_historical_block(
    worksheet,
    values,
    period_row,
    period_col,
    header_row,
    live_block,
):
    try:
        historical_block = build_block_from_header(
            values,
            period_row,
            period_col,
            header_row,
        )
    except ValueError:
        columns = live_block["columns"]
        header_slice = build_standard_header_slice(
            columns,
            min(columns.values()),
            max(columns.values()),
            getattr(worksheet, '_sem_platform_display', False),
        )
        return build_block_from_header(
            values,
            period_row,
            period_col,
            header_row,
            header_values=header_slice,
            header_template_row=live_block.get("header_template_row"),
        )

    live_columns = live_block["columns"]
    historical_columns = historical_block["columns"]
    misplaced = [
        key
        for key, live_col in live_columns.items()
        if historical_columns.get(key) != live_col
    ]
    if misplaced:
        details = ", ".join(
            f"{key}={historical_columns.get(key)}->{live_columns[key]}"
            for key in misplaced
        )
        raise ValueError(
            "El bloque historico no esta alineado horizontalmente con "
            f"MES ACTUAL ({details}). No se actualiza para evitar desplazar "
            "metricas."
        )

    return historical_block


def find_historical_period_block(worksheet, period_context, live_block):
    values = get_worksheet_values(worksheet)
    marker_row = find_historical_marker_row(values)
    month_fallbacks = []
    start_col = min(live_block["columns"].values())
    end_col = max(live_block["columns"].values())
    historical_month_entries = []

    for period_row in range(marker_row + 1, len(values) + 1):
        row = values[period_row - 1]
        for period_col in range(start_col, min(end_col, len(row)) + 1):
            value = row[period_col - 1]
            if not is_period_cell(value):
                continue
            if (
                period_row >= len(values)
                or not looks_like_campaign_header(values[period_row])
            ):
                continue
            historical_month_entries.append({
                "row": period_row,
                "col": period_col,
                "value": value,
                "month": parse_period_month_number(value),
                "explicit_start": (
                    parse_period_identity(value, period_context["start_day"])
                    if extract_explicit_year(value) is not None
                    else None
                ),
            })
            break

    for period_row in range(marker_row + 1, len(values) + 1):
        row = values[period_row - 1]
        for period_col, value in enumerate(row, start=1):
            if not is_period_cell(value):
                continue

            identity = parse_period_identity(
                value,
                period_context["start_day"],
            )
            if identity and identity["key"] == period_context["key"]:
                header_row = period_row + 1
                if header_row > len(values) or not looks_like_campaign_header(
                    values[header_row - 1]
                ):
                    raise ValueError(
                        "El periodo historico existe, pero no tiene una cabecera "
                        f"de campanas valida en la fila {header_row}."
                    )

                return build_compatible_historical_block(
                    worksheet,
                    values,
                    period_row,
                    period_col,
                    header_row,
                    live_block,
                )

            if (
                period_context["mode"] == "month"
                and extract_explicit_year(value) is None
                and parse_period_month_number(value)
                == period_context["start_day"].month
            ):
                month_fallbacks.append((period_row, period_col, value))

    if month_fallbacks:
        matching_fallbacks = []
        wanted_year = period_context["start_day"].year
        for period_row, period_col, value in month_fallbacks:
            inferred_year = infer_historical_month_year(
                historical_month_entries,
                period_row,
            )
            if inferred_year == wanted_year:
                matching_fallbacks.append((period_row, period_col, value))

        if not matching_fallbacks:
            return None

        period_row, period_col, _ = matching_fallbacks[-1]
        header_row = period_row + 1
        if header_row > len(values) or not looks_like_campaign_header(
            values[header_row - 1]
        ):
            raise ValueError(
                "El periodo historico sin ano existe, pero no tiene una cabecera "
                f"de campanas valida en la fila {header_row}."
            )

        return build_compatible_historical_block(
            worksheet,
            values,
            period_row,
            period_col,
            header_row,
            live_block,
        )

    return None


def infer_historical_month_year(entries, target_row):
    """Deduce el ano de un mes heredado sin sobrescribir otro ejercicio."""
    target_index = next(
        (
            index
            for index, entry in enumerate(entries)
            if entry["row"] == target_row
        ),
        None,
    )
    if target_index is None or not entries[target_index]["month"]:
        return None

    inferred_years = []

    for anchor_index in range(target_index - 1, -1, -1):
        anchor = entries[anchor_index]
        if not anchor["explicit_start"]:
            continue
        year = anchor["explicit_start"]["start_day"].year
        previous_month = anchor["explicit_start"]["start_day"].month
        for entry in entries[anchor_index + 1:target_index + 1]:
            month = entry["month"]
            if not month:
                continue
            if month < previous_month:
                year += 1
            previous_month = month
        inferred_years.append(year)
        break

    for anchor_index in range(target_index + 1, len(entries)):
        anchor = entries[anchor_index]
        if not anchor["explicit_start"]:
            continue
        year = anchor["explicit_start"]["start_day"].year
        next_month = anchor["explicit_start"]["start_day"].month
        for entry in reversed(entries[target_index:anchor_index]):
            month = entry["month"]
            if not month:
                continue
            if month > next_month:
                year -= 1
            next_month = month
        inferred_years.append(year)
        break

    if not inferred_years or len(set(inferred_years)) != 1:
        return None
    return inferred_years[0]


def extract_preserved_historical_campaign_rows(
    worksheet,
    sem_client,
    period_context,
    live_block,
):
    """Lee filas de fuentes externas que Google Ads no puede reconstruir."""
    preserved_statuses = {
        canonical_status_key(status)
        for status in sem_client.get("preserve_historical_statuses", [])
    }
    if not preserved_statuses:
        return []

    historical_block = find_historical_period_block(
        worksheet,
        period_context,
        live_block,
    )
    if historical_block is None:
        return []

    values = historical_block["values"]
    columns = historical_block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    next_period_row = find_next_period_row(
        values,
        historical_block["header_row"],
    )
    try:
        campaign_stop_row = find_total_row(
            values,
            historical_block["header_row"],
            start_col,
            end_col,
            next_period_row - 1 if next_period_row else None,
        )
    except ValueError:
        campaign_stop_row = find_historical_block_end_row(
            values,
            historical_block["period_row"],
            start_col,
            end_col,
        ) + 1

    def cell_value(row, key):
        col_index = columns[key]
        return row[col_index - 1] if len(row) >= col_index else ""

    preserved_rows = []
    for row_index in range(
        historical_block["header_row"] + 1,
        campaign_stop_row,
    ):
        row = values[row_index - 1] if len(values) >= row_index else []
        campaign_name = str(cell_value(row, "campaign_name")).strip()
        campaign_status = str(cell_value(row, "campaign_status")).strip()
        if not campaign_name or canonical_status_key(campaign_status) not in preserved_statuses:
            continue

        cost = parse_sheet_number(cell_value(row, "cost"))
        optional_keys = {
            "cost_per_conversion",
            "top_impression_share",
            "absolute_top_impression_share",
        }
        numeric_values = {}
        for key in STANDARD_HEADER_BY_KEY:
            if key in {"campaign_name", "campaign_status"}:
                continue
            raw_value = cell_value(row, key)
            numeric_values[key] = (
                ""
                if key in optional_keys and not str(raw_value).strip()
                else parse_sheet_number(raw_value)
            )

        for integer_key in ("clicks", "impressions"):
            numeric_values[integer_key] = int(
                round(float(numeric_values[integer_key] or 0))
            )

        preserved_rows.append({
            "campaign_id": f"external:{period_context['key']}:{row_index}",
            "campaign_name": campaign_name,
            "campaign_status": campaign_status,
            **numeric_values,
            "cost": cost,
            "raw_cost": cost,
            "source_customer_id": "external",
        })

    return preserved_rows


def read_external_current_cost(worksheet, sem_client):
    cell = sem_client.get("external_current_cost_cell")
    if not cell:
        return 0.0

    row_index, col_index = gspread.utils.a1_to_rowcol(cell)
    values = get_worksheet_values(worksheet)
    row = values[row_index - 1] if len(values) >= row_index else []
    value = row[col_index - 1] if len(row) >= col_index else ""
    return parse_sheet_number(value)


def create_historical_period_block_from_live(
    worksheet,
    live_block,
    period_context,
    campaign_count,
):
    values = get_worksheet_values(worksheet)
    columns = live_block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    marker_row = find_historical_marker_row(values)

    try:
        live_total_row = find_total_row(
            values,
            live_block["header_row"],
            start_col,
            end_col,
            marker_row - 1,
        )
    except ValueError:
        live_total_row = max(live_block["header_row"] + 1, marker_row - 2)

    source_end_row = historical_copy_end_row(
        live_block,
        live_total_row,
        campaign_count,
    )
    destination_start_row = find_historical_append_row(
        values,
        marker_row,
        start_col,
        end_col,
        period_context,
    )
    destination_end_row = copy_range_to_rows(
        worksheet,
        live_block["period_row"],
        source_end_row,
        start_col,
        end_col,
        destination_start_row,
    )

    refreshed_values = get_worksheet_values(worksheet, refresh=True)
    historical_block = build_block_from_header(
        refreshed_values,
        destination_start_row,
        start_col,
        destination_start_row + 1,
    )

    return historical_block, {
        "range": (
            f"{gspread.utils.rowcol_to_a1(destination_start_row, start_col)}:"
            f"{gspread.utils.rowcol_to_a1(destination_end_row, end_col)}"
        ),
        "rows_inserted": destination_end_row - destination_start_row + 1,
    }


def update_single_campaign_historical_block(
    worksheet,
    campaign_rows,
    historical_block,
    period_context,
    mutation_batch=None,
):
    """Actualiza un historico de una campana sin duplicar sus totales."""
    if len(campaign_rows) != 1:
        raise ValueError(
            "La actualizacion historica sin total requiere una sola campana."
        )

    values = historical_block["values"]
    columns = historical_block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    data_start_row = historical_block["header_row"] + 1
    block_end_row = find_historical_block_end_row(
        values,
        historical_block["period_row"],
        start_col,
        end_col,
    )
    clear_end_row = max(data_start_row, block_end_row)
    obsolete_clear_ranges = clear_obsolete_live_columns(
        worksheet,
        historical_block,
        clear_end_row,
        mutation_batch,
    )

    period_cell = gspread.utils.rowcol_to_a1(
        historical_block["period_row"],
        historical_block["period_col"],
    )
    header_range = (
        f"{gspread.utils.rowcol_to_a1(historical_block['header_row'], start_col)}:"
        f"{gspread.utils.rowcol_to_a1(historical_block['header_row'], end_col)}"
    )
    clear_range = (
        f"{gspread.utils.rowcol_to_a1(data_start_row, start_col)}:"
        f"{gspread.utils.rowcol_to_a1(clear_end_row, end_col)}"
    )
    output_row = build_output_matrix(
        campaign_rows,
        columns,
        start_col,
        end_col,
    )[0]
    width = end_col - start_col + 1
    data_matrix = [output_row]
    data_matrix.extend(
        ["" for _ in range(width)]
        for _ in range(clear_end_row - data_start_row)
    )

    queue_values_update(
        worksheet,
        period_cell,
        [[period_context["label"]]],
        mutation_batch,
    )
    queue_values_update(
        worksheet,
        header_range,
        [build_standard_header_slice(columns, start_col, end_col, getattr(worksheet, '_sem_platform_display', False))],
        mutation_batch,
    )
    queue_values_update(
        worksheet,
        clear_range,
        data_matrix,
        mutation_batch,
    )

    apply_single_campaign_historical_borders(
        worksheet,
        start_col,
        end_col,
        historical_block["header_row"],
        data_start_row,
        clear_end_row,
        mutation_batch,
    )
    apply_campaign_value_formats(
        worksheet,
        columns,
        data_start_row,
        campaign_rows,
        data_start_row,
        mutation_batch,
        apply_number_formats=True,
        apply_status_formats=True,
    )
    side_band_end_row = resolve_historical_side_band_end(
        historical_block,
        data_start_row,
    )
    apply_historical_side_band(
        worksheet,
        find_historical_marker_row(values),
        side_band_end_row,
        mutation_batch,
    )

    write_range = (
        f"{gspread.utils.rowcol_to_a1(data_start_row, start_col)}:"
        f"{gspread.utils.rowcol_to_a1(data_start_row, end_col)}"
    )
    return {
        "period_cell": period_cell,
        "period_label": period_context["label"],
        "clear_range": clear_range,
        "write_range": write_range,
        "header_row": historical_block["header_row"],
        "period_row": historical_block["period_row"],
        "total_row": None,
        "end_row": data_start_row,
        "inserted_rows": 0,
        "deleted_rows": 0,
        "obsolete_clear_ranges": obsolete_clear_ranges,
        "formats_updated": True,
        "includes_total": False,
    }


def ensure_historical_total_row(worksheet, historical_block):
    # Algunos historicos de Supermetrics terminan tras las campanas y no tienen total.
    values = historical_block["values"]
    columns = historical_block["columns"]
    start_col = min(columns.values())
    end_col = max(columns.values())
    period_row = historical_block["period_row"]
    header_row = historical_block["header_row"]
    next_period_row = None

    for row_index in range(header_row + 1, len(values) + 1):
        if any(is_period_cell(value) for value in values[row_index - 1]):
            next_period_row = row_index
            break

    try:
        find_total_row(
            values,
            header_row,
            start_col,
            end_col,
            next_period_row - 1 if next_period_row else None,
        )
        return historical_block
    except ValueError:
        pass

    data_start_row = header_row + 1
    campaign_col = columns["campaign_name"]
    campaign_count = 0

    for row_index in range(data_start_row, len(values) + 1):
        if next_period_row and row_index >= next_period_row:
            break

        row = values[row_index - 1]
        campaign_name = (
            row[campaign_col - 1]
            if len(row) >= campaign_col
            else ""
        )
        if not str(campaign_name).strip():
            break

        campaign_count += 1

    total_row = data_start_row + campaign_count + 1
    structure_changed = False
    if next_period_row and total_row >= next_period_row:
        rows_to_insert = total_row - next_period_row + 1
        worksheet.insert_rows(
            [["" for _ in range(worksheet.col_count)] for _ in range(rows_to_insert)],
            row=next_period_row,
            value_input_option="USER_ENTERED",
            inherit_from_before=True,
        )
        invalidate_worksheet_values_cache(worksheet)
        structure_changed = True

    # No se escribe la etiqueta y se vuelve a leer inmediatamente. Sheets
    # puede devolver durante unos instantes la version anterior y provocar un
    # falso "no se encontro". El bloque local marca la fila que ya existe y
    # la escritura definitiva del total se envia despues en el mismo batch.
    source_values = (
        get_worksheet_values(worksheet, refresh=True)
        if structure_changed
        else values
    )
    refreshed_values = [list(row) for row in source_values]
    while len(refreshed_values) < total_row:
        refreshed_values.append([])
    total_values = refreshed_values[total_row - 1]
    while len(total_values) < columns["ctr"]:
        total_values.append("")
    total_values[columns["ctr"] - 1] = "Total coste campa\u00f1as:"
    set_worksheet_values_cache(worksheet, refreshed_values)
    return build_block_from_header(
        refreshed_values,
        period_row,
        historical_block["period_col"],
        header_row,
    )


def upsert_historical_period(
    worksheet,
    live_block,
    campaign_rows,
    period_context,
    mutation_batch=None,
    force_formats=False,
):
    ensure_historical_marker_row(worksheet, live_block)
    historical_block = find_historical_period_block(
        worksheet,
        period_context,
        live_block,
    )
    created = historical_block is None
    creation_result = None

    if created:
        historical_block, creation_result = create_historical_period_block_from_live(
            worksheet,
            live_block,
            period_context,
            len(campaign_rows),
        )

    if len(campaign_rows) == 1:
        write_result = update_single_campaign_historical_block(
            worksheet,
            campaign_rows,
            historical_block,
            period_context,
            mutation_batch,
        )
    else:
        historical_block = ensure_historical_total_row(
            worksheet,
            historical_block,
        )
        write_result = update_live_block(
            worksheet,
            campaign_rows,
            historical_block,
            period_context,
            mutation_batch,
            force_formats=force_formats,
        )
        write_result["end_row"] = write_result["total_row"]
        write_result["includes_total"] = True
        side_band_end_row = resolve_historical_side_band_end(
            historical_block,
            write_result["end_row"],
            write_result["inserted_rows"],
            write_result["deleted_rows"],
        )
        apply_historical_side_band(
            worksheet,
            find_historical_marker_row(historical_block["values"]),
            side_band_end_row,
            mutation_batch,
        )

    return {
        "created": created,
        "creation": creation_result,
        "write": write_result,
        "period": period_context["label"],
    }


def queue_legacy_historical_historical_total(
    worksheet,
    columns,
    start_col,
    end_col,
    data_start_row,
    campaign_count,
    total_row,
    mutation_batch=None,
    conversion_total=None,
    metric_totals=None,
):
    """Restaura la fila TOTALES historica propia de la ficha con totales historicos heredados."""
    width = end_col - start_col + 1
    values = ["" for _ in range(width)]
    data_end_row = data_start_row + campaign_count - 1

    def letter(key):
        return gspread.utils.rowcol_to_a1(1, columns[key]).rstrip("1")

    def set_value(key, value):
        values[columns[key] - start_col] = value

    clicks = letter("clicks")
    cost = letter("cost")
    conversions = letter("conversions")
    impressions = letter("impressions")

    set_value("campaign_status", "TOTALES")
    set_value("clicks", f"=SUM({clicks}{data_start_row}:{clicks}{data_end_row})")
    set_value("ctr", f'=IFERROR({clicks}{total_row}/{impressions}{total_row};"")')
    set_value("average_cpc", f'=IFERROR({cost}{total_row}/{clicks}{total_row};"")')
    set_value("cost", f"=SUM({cost}{data_start_row}:{cost}{data_end_row})")
    set_value(
        "conversions",
        f"=SUM({conversions}{data_start_row}:{conversions}{data_end_row})",
    )
    set_value(
        "cost_per_conversion",
        f'=IFERROR({cost}{total_row}/{conversions}{total_row};"")',
    )
    set_value(
        "impressions",
        f"=SUM({impressions}{data_start_row}:{impressions}{data_end_row})",
    )

    total_range = (
        f"{gspread.utils.rowcol_to_a1(total_row, start_col)}:"
        f"{gspread.utils.rowcol_to_a1(total_row, end_col)}"
    )
    queue_values_update(
        worksheet,
        total_range,
        [values],
        mutation_batch,
    )

    border = {
        "style": "SOLID",
        "width": 1,
        "color": {"red": 0, "green": 0, "blue": 0},
    }
    no_border = {"style": "NONE"}
    requests = [
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "cell": {"userEnteredFormat": {}},
                "fields": "userEnteredFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": columns["campaign_status"] - 1,
                    "endColumnIndex": columns["campaign_status"],
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {
                            "red": 0.9764706,
                            "green": 0.79607844,
                            "blue": 0.6117647,
                        },
                        "textFormat": {
                            "fontFamily": "Arial",
                            "bold": True,
                            "italic": True,
                        },
                    }
                },
                "fields": "userEnteredFormat",
            }
        },
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": columns["clicks"] - 1,
                    "endColumnIndex": columns["impressions"],
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {"red": 1, "green": 1, "blue": 1},
                        "horizontalAlignment": "RIGHT",
                        "textFormat": {"fontFamily": "Arial"},
                    }
                },
                "fields": (
                    "userEnteredFormat.backgroundColor,"
                    "userEnteredFormat.horizontalAlignment,"
                    "userEnteredFormat.textFormat.fontFamily"
                ),
            }
        },
        {
            "updateBorders": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "top": no_border,
                "bottom": no_border,
                "left": border if start_col == 2 else no_border,
                "right": no_border,
                "innerVertical": no_border,
            }
        },
        {
            "updateBorders": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": columns["campaign_status"] - 1,
                    "endColumnIndex": columns["impressions"],
                },
                "top": border,
                "bottom": border,
                "left": border,
                "right": border,
                "innerVertical": border,
            }
        },
    ]

    metric_totals = dict(metric_totals or {})
    if conversion_total is not None:
        metric_totals.setdefault("conversions", conversion_total)
    number_formats = {
        "clicks": ("NUMBER", "#,##0"),
        "ctr": (
            "PERCENT",
            optional_percent_format_for_value(metric_totals.get("ctr", 0)),
        ),
        "average_cpc": (
            "CURRENCY",
            optional_currency_format_for_value(
                metric_totals.get("average_cpc", 0)
            ),
        ),
        "cost": (
            "CURRENCY",
            optional_currency_format_for_value(metric_totals.get("cost", 0)),
        ),
        "conversions": (
            "NUMBER",
            optional_decimal_format_for_value(
                metric_totals.get("conversions", 0)
            ),
        ),
        "cost_per_conversion": (
            "CURRENCY",
            optional_currency_format_for_value(
                metric_totals.get("cost_per_conversion", 0)
            ),
        ),
        "impressions": ("NUMBER", "#,##0"),
    }
    for key, (format_type, pattern) in number_formats.items():
        col_index = columns[key]
        requests.append({
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": total_row - 1,
                    "endRowIndex": total_row,
                    "startColumnIndex": col_index - 1,
                    "endColumnIndex": col_index,
                },
                "cell": {
                    "userEnteredFormat": {
                        "numberFormat": {
                            "type": format_type,
                            "pattern": pattern,
                        }
                    }
                },
                "fields": "userEnteredFormat.numberFormat",
            }
        })

    requests.append({
        "repeatCell": {
            "range": {
                "sheetId": worksheet.id,
                "startRowIndex": total_row - 1,
                "endRowIndex": total_row,
                "startColumnIndex": columns["cost"] - 1,
                "endColumnIndex": columns["cost"],
            },
            "cell": {
                "userEnteredFormat": {
                    "textFormat": {"bold": True},
                }
            },
            "fields": "userEnteredFormat.textFormat.bold",
        }
    })
    queue_format_requests(worksheet, requests, mutation_batch)


def normalize_legacy_historical_historical_totals(
    worksheet,
    mutation_batch=None,
    force_total_rows=None,
):
    """Normaliza todas las filas de total del historico mensual de la ficha con totales historicos heredados."""
    values = get_worksheet_values(worksheet, refresh=True)
    marker_row = find_historical_marker_row(values)
    normalized = []
    force_total_rows = set(force_total_rows or [])

    for period_row in range(marker_row + 1, len(values) + 1):
        if not any(is_period_cell(value) for value in values[period_row - 1]):
            continue

        header_row = period_row + 1
        if header_row > len(values) or not looks_like_campaign_header(
            values[header_row - 1]
        ):
            continue

        columns = find_header_columns(values[header_row - 1])
        start_col = min(columns.values())
        end_col = max(columns.values())
        next_period_row = find_next_period_row(values, period_row)
        try:
            total_row = find_total_row(
                values,
                header_row,
                start_col,
                end_col,
                next_period_row - 1 if next_period_row else None,
            )
        except ValueError:
            continue

        data_start_row = header_row + 1
        campaign_count = count_campaign_rows(
            values,
            data_start_row,
            total_row,
            columns["campaign_name"],
        )
        if campaign_count <= 1:
            continue

        campaign_rows = extract_campaign_rows_from_block(
            values,
            columns,
            data_start_row,
            data_start_row + campaign_count - 1,
        )

        queue_legacy_historical_historical_total(
            worksheet,
            columns,
            start_col,
            end_col,
            data_start_row,
            campaign_count,
            total_row,
            mutation_batch,
            conversion_total=sum(
                parse_sheet_number(
                    values[row_index - 1][columns["conversions"] - 1]
                    if len(values[row_index - 1]) >= columns["conversions"]
                    else 0
                )
                for row_index in range(
                    data_start_row,
                    data_start_row + campaign_count,
                )
            ),
            metric_totals=summarize_campaign_metrics(campaign_rows),
        )
        normalized.append(total_row)

    return normalized
