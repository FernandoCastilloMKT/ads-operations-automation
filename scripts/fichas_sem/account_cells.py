"""SEM: account cells."""

from .account_status import (
    aggregate_account_operational_status,
    canonical_status_key,
    status_display_label,
)

from .configuration import (
    get_sem_customer_ids,
)

from .metrics import (
    parse_sheet_number,
)

import gspread
from .constants import (
    ACCOUNT_STATUS_DISABLED_COLOR,
    ACCOUNT_STATUS_ENABLED_COLOR,
    VISTA_GLOBAL_CLIENT_COLUMN,
    VISTA_GLOBAL_MONTHLY_BUDGET_COLUMN,
    VISTA_GLOBAL_NATALIA_MARKER,
    VISTA_GLOBAL_SIGNED_MARKER,
    VISTA_GLOBAL_STANDBY_MARKER,
    VISTA_GLOBAL_STATUS_COLUMN,
)
from .runtime import (
    VISTA_GLOBAL_WORKSHEET_NAME,
)

from .sheets_io import (
    queue_format_requests,
    queue_values_update,
)

from .text import (
    normalize_customer_id,
    normalize_text,
)


def update_account_status_cell(
    worksheet,
    sem_client,
    account_status,
    mutation_batch=None,
):
    status_cell = sem_client.get("account_status_cell", "A2")
    status_row, status_col = gspread.utils.a1_to_rowcol(status_cell)
    is_enabled = canonical_status_key(account_status) == "enabled"
    background_color = (
        ACCOUNT_STATUS_ENABLED_COLOR
        if is_enabled
        else ACCOUNT_STATUS_DISABLED_COLOR
    )
    text_color = (
        {"red": 0, "green": 0, "blue": 0}
        if is_enabled
        else {"red": 1, "green": 1, "blue": 1}
    )

    queue_values_update(
        worksheet,
        status_cell,
        [[status_display_label(account_status)]],
        mutation_batch,
    )
    queue_format_requests(
        worksheet,
        [{
            "repeatCell": {
                "range": {
                    "sheetId": worksheet.id,
                    "startRowIndex": status_row - 1,
                    "endRowIndex": status_row,
                    "startColumnIndex": status_col - 1,
                    "endColumnIndex": status_col,
                },
                "cell": {
                    "userEnteredFormat": {
                        "backgroundColor": background_color,
                        "textFormat": {
                            "bold": True,
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
        }],
        mutation_batch,
    )
    return status_cell


def update_configured_account_statuses(
    worksheet,
    sem_client,
    account_statuses,
    mutation_batch=None,
):
    customer_ids = get_sem_customer_ids(sem_client)
    configured_cells = {
        normalize_customer_id(customer_id): cell
        for customer_id, cell in sem_client.get(
            "account_status_cells",
            {},
        ).items()
    }
    write_individual_statuses = sem_client.get(
        "write_individual_account_statuses",
        True,
    )

    if (
        len(customer_ids) > 1
        and write_individual_statuses
        and not configured_cells
    ):
        raise ValueError(
            f"La ficha multicuenta {sem_client['nombre']} necesita "
            "account_status_cells."
        )

    updates = []
    start_index = 0
    if len(customer_ids) > 1:
        aggregate_status = aggregate_account_operational_status(
            account_statuses,
            customer_ids,
            require_all_enabled=sem_client.get(
                "require_all_accounts_enabled",
                False,
            ),
        )
        primary_customer_id = customer_ids[0]
        aggregate_cell = configured_cells.get(
            primary_customer_id,
            sem_client.get("account_status_cell", "A2"),
        )
        update_account_status_cell(
            worksheet,
            {"account_status_cell": aggregate_cell},
            aggregate_status,
            mutation_batch,
        )
        updates.append({
            "customer_id": "GLOBAL",
            "status": aggregate_status,
            "cell": aggregate_cell,
        })
        start_index = 1

    individual_customer_ids = (
        customer_ids[start_index:]
        if write_individual_statuses
        else []
    )
    for customer_id in individual_customer_ids:
        status = account_statuses.get(customer_id, "unknown")
        status_config = {
            "account_status_cell": configured_cells.get(
                customer_id,
                sem_client.get("account_status_cell", "A2"),
            )
        }
        cell = update_account_status_cell(
            worksheet,
            status_config,
            status,
            mutation_batch,
        )
        updates.append({
            "customer_id": customer_id,
            "status": status,
            "cell": cell,
        })

    for status_cell in sem_client.get("clear_account_status_cells", []):
        status_row, status_col = gspread.utils.a1_to_rowcol(status_cell)
        queue_values_update(
            worksheet,
            status_cell,
            [[""]],
            mutation_batch,
        )
        queue_format_requests(
            worksheet,
            [{
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": status_row - 1,
                        "endRowIndex": status_row,
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
            }],
            mutation_batch,
        )
    return updates


def update_and_log_account_statuses(
    worksheet,
    sem_client,
    account_statuses,
    mutation_batch=None,
):
    updates = update_configured_account_statuses(
        worksheet,
        sem_client,
        account_statuses,
        mutation_batch,
    )
    for update in updates:
        print(
            "Estado escrito: "
            f"cuenta {update['customer_id']} = {update['status']} "
            f"en {update['cell']}"
        )
    return updates


def find_vista_global_marker_row(values, marker):
    marker_key = normalize_text(marker)
    for row_index, row in enumerate(values):
        if any(normalize_text(value) == marker_key for value in row[:3]):
            return row_index
    raise ValueError(
        f"No se encontro el marcador '{marker}' en Vista Global."
    )


def locate_vista_global_sections(values):
    signed_row = find_vista_global_marker_row(
        values,
        VISTA_GLOBAL_SIGNED_MARKER,
    )
    standby_row = find_vista_global_marker_row(
        values,
        VISTA_GLOBAL_STANDBY_MARKER,
    )
    natalia_row = find_vista_global_marker_row(
        values,
        VISTA_GLOBAL_NATALIA_MARKER,
    )
    if not (1 < signed_row < standby_row < natalia_row):
        raise ValueError(
            "Los bloques de Vista Global no conservan el orden esperado: "
            "activos, firmados sin comenzar, Stand By y Natalia."
        )
    return {
        "active_start": 1,
        "signed_row": signed_row,
        "standby_row": standby_row,
        "natalia_row": natalia_row,
    }


def vista_global_row_value(row, column_index):
    return row[column_index] if column_index < len(row) else ""


def find_active_insertion_index(values, sections, monthly_budget):
    for row_index in range(
        sections["active_start"],
        sections["signed_row"],
    ):
        row = values[row_index]
        client_name = vista_global_row_value(
            row,
            VISTA_GLOBAL_CLIENT_COLUMN,
        )
        if not str(client_name).strip():
            continue
        existing_budget = parse_sheet_number(
            vista_global_row_value(
                row,
                VISTA_GLOBAL_MONTHLY_BUDGET_COLUMN,
            )
        )
        if existing_budget < monthly_budget:
            return row_index
    return sections["signed_row"]


def plan_next_vista_global_status_move(values):
    # Membership now controls execution. Only the user can move clients between
    # sections; an old green status must not silently re-enable a Stand By row.
    locate_vista_global_sections(values)
    return None


def reconcile_vista_global_status_blocks(spreadsheet):
    worksheet = spreadsheet.worksheet(VISTA_GLOBAL_WORKSHEET_NAME)
    moved = []

    for _ in range(100):
        values = worksheet.get_all_values()
        move = plan_next_vista_global_status_move(values)
        if move is None:
            break
        spreadsheet.batch_update({
            "requests": [{
                "moveDimension": {
                    "source": {
                        "sheetId": worksheet.id,
                        "dimension": "ROWS",
                        "startIndex": move["source_index"],
                        "endIndex": move["source_index"] + 1,
                    },
                    "destinationIndex": move["destination_index"],
                }
            }]
        })
        moved.append(move)
        destination = (
            "Stand By"
            if move["direction"] == "standby"
            else "clientes activos"
        )
        print(
            "Vista Global: "
            f"{move['client']} movido a {destination} "
            f"(estado {move['status']})."
        )
    else:
        raise RuntimeError(
            "Vista Global supero el limite seguro de 100 movimientos."
        )

    return moved
