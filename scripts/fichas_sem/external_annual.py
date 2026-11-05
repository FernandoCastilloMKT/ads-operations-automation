"""SEM: external annual."""

from .campaign_blocks import (
    apply_campaign_value_formats,
    apply_live_block_borders,
    apply_total_value_formats,
    update_total_formulas,
)

from .metrics import (
    build_output_matrix,
    build_standard_header_slice,
    parse_sheet_number,
    summarize_campaign_metrics,
)

from .periods import (
    is_month_current_cell,
)

import gspread
from .constants import (
    MICROSOFT_ANNUAL_ARCHIVE_COLUMN_STEP,
    MICROSOFT_ANNUAL_ARCHIVE_FIRST_COLUMN,
    MICROSOFT_ANNUAL_BASE_YEAR,
    STANDARD_HEADER_BY_KEY,
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


def find_external_annual_area(
    values,
    title_prefix,
    *,
    allow_missing=False,
):
    """Localiza o reserva un bloque anual situado antes de MES ACTUAL."""
    mes_actual_row = None
    for row_index, row in enumerate(values, start=1):
        if any(is_month_current_cell(value) for value in row):
            mes_actual_row = row_index
            break

    if mes_actual_row is None:
        raise ValueError("No se encontro la fila MES ACTUAL.")

    candidates = []
    for row_index in range(1, mes_actual_row):
        row = values[row_index - 1]
        for col_index, value in enumerate(row, start=1):
            if normalize_text(value).startswith(normalize_text(title_prefix)):
                candidates.append((row_index, col_index))

    if not candidates:
        if not allow_missing:
            raise ValueError(
                f"No se encontro el bloque {title_prefix} dentro de "
                "HISTORICO/URL/ACCESOS."
            )
        title_row = mes_actual_row
        while title_row > 1:
            previous_row = values[title_row - 2]
            if any(str(value or "").strip() for value in previous_row):
                break
            title_row -= 1
        return {
            "title_row": title_row,
            "start_col": 2,
            "mes_actual_row": mes_actual_row,
            "needs_creation": True,
        }

    # El bloque vigente siempre es el situado mas a la izquierda. Desde 2027
    # coexistira con archivos anuales que empiezan en R, AE, AR, etc.
    title_row, start_col = min(candidates, key=lambda item: item[1])
    return {
        "title_row": title_row,
        "start_col": start_col,
        "mes_actual_row": mes_actual_row,
        "needs_creation": False,
    }


def find_microsoft_annual_area(values):
    return find_external_annual_area(values, "MICROSOFT ADS")


def find_openai_annual_area(values):
    return find_external_annual_area(
        values,
        "CHATGPT ADS",
        allow_missing=True,
    )


def microsoft_annual_columns(start_col):
    return {
        key: start_col + offset
        for offset, key in enumerate(STANDARD_HEADER_BY_KEY)
    }


def microsoft_archive_start_col(year):
    return (
        MICROSOFT_ANNUAL_ARCHIVE_FIRST_COLUMN
        + (year - MICROSOFT_ANNUAL_BASE_YEAR)
        * MICROSOFT_ANNUAL_ARCHIVE_COLUMN_STEP
    )


def ensure_worksheet_columns(worksheet, required_end_col):
    if worksheet.col_count >= required_end_col:
        return 0

    added = required_end_col - worksheet.col_count
    worksheet.add_cols(added)
    invalidate_worksheet_values_cache(worksheet)
    return added


def queue_external_annual_table(
    worksheet,
    year,
    campaign_rows,
    title_row,
    start_col,
    clear_end_row,
    mutation_batch,
    title_prefix,
):
    columns = microsoft_annual_columns(start_col)
    end_col = start_col + len(columns) - 1
    header_row = title_row + 1
    data_start_row = header_row + 1
    total_row = data_start_row + len(campaign_rows) + 1
    clear_end_row = max(clear_end_row, total_row)

    title_cell = gspread.utils.rowcol_to_a1(title_row, start_col)
    header_range = (
        f"{gspread.utils.rowcol_to_a1(header_row, start_col)}:"
        f"{gspread.utils.rowcol_to_a1(header_row, end_col)}"
    )
    body_range = (
        f"{gspread.utils.rowcol_to_a1(data_start_row, start_col)}:"
        f"{gspread.utils.rowcol_to_a1(clear_end_row, end_col)}"
    )

    queue_values_update(
        worksheet,
        title_cell,
        [[f"{title_prefix} - {year}"]],
        mutation_batch,
    )
    queue_values_update(
        worksheet,
        header_range,
        [build_standard_header_slice(columns, start_col, end_col, getattr(worksheet, '_sem_platform_display', False))],
        mutation_batch,
    )

    width = end_col - start_col + 1
    body_matrix = [
        ["" for _ in range(width)]
        for _ in range(clear_end_row - data_start_row + 1)
    ]
    for index, output_row in enumerate(
        build_output_matrix(campaign_rows, columns, start_col, end_col)
    ):
        body_matrix[index] = output_row
    queue_values_update(
        worksheet,
        body_range,
        body_matrix,
        mutation_batch,
    )

    update_total_formulas(
        worksheet,
        columns,
        start_col,
        end_col,
        data_start_row,
        len(campaign_rows),
        total_row,
        mutation_batch,
        apply_formats=False,
        conversion_total=sum(
            parse_sheet_number(row.get("conversions", 0))
            for row in campaign_rows
        ),
        metric_totals=summarize_campaign_metrics(campaign_rows),
    )

    requests = [
        {
            "mergeCells": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": title_row - 1,
                    "endRowIndex": title_row,
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
                    "startRowIndex": header_row - 1,
                    "endRowIndex": clear_end_row,
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
                    "startRowIndex": title_row - 1,
                    "endRowIndex": title_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {
                            "red": 0,
                            "green": 0.47,
                            "blue": 0.83,
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
        {
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": header_row - 1,
                    "endRowIndex": header_row,
                    "startColumnIndex": start_col - 1,
                    "endColumnIndex": end_col,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {
                            "red": 1,
                            "green": 0.6,
                            "blue": 0,
                        },
                        "horizontalAlignment": "LEFT",
                        "verticalAlignment": "MIDDLE",
                        "wrapStrategy": "CLIP",
                        "textFormat": {"bold": True},
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
                    "startColumnIndex": columns["ctr"] - 1,
                    "endColumnIndex": columns["average_cpc"],
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": {
                            "red": 0.96,
                            "green": 0.76,
                            "blue": 0.56,
                        },
                        "horizontalAlignment": "CENTER",
                        "textFormat": {"bold": True},
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
                        "textFormat": {"bold": True},
                    }
                },
                "fields": "userEnteredFormat.textFormat.bold",
            }
        },
    ]

    queue_format_requests(worksheet, requests, mutation_batch)
    apply_live_block_borders(
        worksheet,
        columns,
        start_col,
        end_col,
        header_row,
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
        total_row - 1,
        mutation_batch,
        apply_number_formats=True,
        apply_status_formats=True,
    )
    apply_total_value_formats(
        worksheet,
        columns,
        total_row,
        mutation_batch,
        metric_values=summarize_campaign_metrics(campaign_rows),
    )

    return {
        "year": year,
        "campaign_count": len(campaign_rows),
        "total_cost": round(sum(row["raw_cost"] for row in campaign_rows), 2),
        "total_row": total_row,
        "range": (
            f"{gspread.utils.rowcol_to_a1(title_row, start_col)}:"
            f"{gspread.utils.rowcol_to_a1(total_row, end_col)}"
        ),
    }


def external_annual_unmerge_request(
    worksheet,
    title_row,
    start_col,
    end_col,
    clear_end_row,
    title_prefix,
):
    unmerge_end_col = end_col
    if title_prefix == "CHATGPT ADS" and start_col == 2:
        unmerge_end_col = min(end_col + 2, worksheet.col_count)
    return {
        "unmergeCells": {
            "range": {
                "sheetId": worksheet.id,
                "startRowIndex": title_row - 1,
                "endRowIndex": clear_end_row,
                "startColumnIndex": start_col - 1,
                "endColumnIndex": unmerge_end_col,
            }
        }
    }


def unmerge_external_annual_area(
    worksheet,
    title_row,
    start_col,
    clear_end_row,
    title_prefix,
):
    """Descombina antes del batch de valores, no despues de escribirlo."""
    end_col = start_col + len(STANDARD_HEADER_BY_KEY) - 1
    worksheet.spreadsheet.batch_update({
        "requests": [external_annual_unmerge_request(
            worksheet,
            title_row,
            start_col,
            end_col,
            clear_end_row,
            title_prefix,
        )]
    })


def update_external_annual_control(
    worksheet,
    annual_config,
    ads_data,
    mutation_batch,
    *,
    title_prefix,
    area_finder,
):
    title_row = annual_config["title_row"]
    start_col = annual_config["start_col"]
    target_year = annual_config["target_year"]
    archives = []

    for archive_year in annual_config["archive_years"]:
        archive_start_col = microsoft_archive_start_col(archive_year)
        archive_end_col = archive_start_col + len(STANDARD_HEADER_BY_KEY) - 1
        ensure_worksheet_columns(worksheet, archive_end_col)
        archive_rows = ads_data["annual_archive_rows"].get(
            archive_year,
            [],
        )
        archive_total_row = title_row + 3 + len(archive_rows)
        unmerge_external_annual_area(
            worksheet,
            title_row,
            archive_start_col,
            archive_total_row,
            title_prefix,
        )
        archives.append(queue_external_annual_table(
            worksheet,
            archive_year,
            archive_rows,
            title_row,
            archive_start_col,
            archive_total_row,
            mutation_batch,
            title_prefix,
        ))

    values = get_worksheet_values(worksheet, refresh=True)
    annual_area = area_finder(values)
    mes_actual_row = annual_area["mes_actual_row"]
    campaign_rows = ads_data["annual_current_rows"]
    desired_total_row = title_row + 3 + len(campaign_rows)
    desired_mes_actual_row = desired_total_row + 2
    inserted_rows = 0
    if mes_actual_row < desired_mes_actual_row:
        inserted_rows = desired_mes_actual_row - mes_actual_row
        worksheet.insert_rows(
            [["" for _ in range(worksheet.col_count)] for _ in range(inserted_rows)],
            row=mes_actual_row,
            value_input_option="USER_ENTERED",
            inherit_from_before=True,
        )
        invalidate_worksheet_values_cache(worksheet)
        mes_actual_row += inserted_rows

    unmerge_external_annual_area(
        worksheet,
        title_row,
        start_col,
        mes_actual_row - 1,
        title_prefix,
    )
    current_result = queue_external_annual_table(
        worksheet,
        target_year,
        campaign_rows,
        title_row,
        start_col,
        mes_actual_row - 1,
        mutation_batch,
        title_prefix,
    )
    current_result["inserted_rows"] = inserted_rows
    current_result["archives"] = archives
    return current_result


def update_microsoft_annual_control(
    worksheet,
    annual_config,
    microsoft_ads,
    mutation_batch,
):
    return update_external_annual_control(
        worksheet,
        annual_config,
        microsoft_ads,
        mutation_batch,
        title_prefix="MICROSOFT ADS",
        area_finder=find_microsoft_annual_area,
    )


def update_openai_annual_control(
    worksheet,
    annual_config,
    openai_ads,
    mutation_batch,
):
    return update_external_annual_control(
        worksheet,
        annual_config,
        openai_ads,
        mutation_batch,
        title_prefix="CHATGPT ADS",
        area_finder=find_openai_annual_area,
    )
