"""SEM: balance."""

from .errors import (
    SaldoPeriodValidationError,
)

from .periods import (
    add_calendar_months,
    build_named_date_range,
    date_range_start_month,
    is_uncertain_period,
    parse_date_range,
    parse_month_name,
    parse_named_date_range_parts,
    parse_partial_month_period,
    parse_saldo_year_label,
    parse_sequence_number,
    period_context_for_calendar_month,
    period_context_for_range,
)

from .renewals import (
    find_contract_date_range,
    plan_monthly_budget_carry_forward,
    plan_monthly_renewal_updates,
)

from datetime import date
import calendar
import gspread
from .constants import (
    MONTH_NAMES_ES,
)

from .sheets_io import (
    get_worksheet_values,
    queue_format_requests,
    queue_values_update,
)

from .text import (
    normalize_text,
)


def contract_month_context(month_day, contract_range, target_day):
    month_end = date(
        month_day.year,
        month_day.month,
        calendar.monthrange(month_day.year, month_day.month)[1],
    )
    start_day = max(month_day, contract_range["start_day"])
    end_day = min(month_end, contract_range["end_day"])
    if start_day == month_day and end_day == month_end:
        return period_context_for_calendar_month(month_day, target_day)
    return period_context_for_range(
        {"start_day": start_day, "end_day": end_day},
        target_day,
    )


def apply_continuous_contract_months(
    values,
    period_targets,
    contract_slots,
    contract_range,
    target_day,
):
    """Completa los meses comprendidos entre el inicio y el fin del contrato."""
    if not contract_slots:
        raise SaldoPeriodValidationError(
            "El contrato continuo no tiene filas numeradas disponibles."
        )

    slots_by_sequence = {}
    for slot in contract_slots:
        slots_by_sequence.setdefault(slot["sequence_number"], slot)

    first_sequence = min(slots_by_sequence)
    first_month = contract_range["start_day"].replace(day=1)
    last_month = contract_range["end_day"].replace(day=1)
    month_count = (
        (last_month.year - first_month.year) * 12
        + last_month.month
        - first_month.month
        + 1
    )
    explicit_by_row = {target["row"]: target for target in period_targets}
    generated_targets = []
    updates = []

    for offset in range(month_count):
        month_day = add_calendar_months(first_month, offset)
        sequence_number = first_sequence + offset
        slot = slots_by_sequence.get(sequence_number)
        if not slot:
            raise SaldoPeriodValidationError(
                "No hay una fila numerada para el mes continuo "
                f"{MONTH_NAMES_ES[month_day.month]} {month_day.year}."
            )

        context = contract_month_context(month_day, contract_range, target_day)
        explicit = explicit_by_row.get(slot["row"])
        if explicit:
            explicit_start = explicit["period_context"]["start_day"]
            if (
                explicit_start.year,
                explicit_start.month,
            ) != (month_day.year, month_day.month):
                raise SaldoPeriodValidationError(
                    f"{slot['period_cell']}: el mes escrito no coincide con "
                    "la secuencia continua de Fecha inicio/Fecha Fin."
                )
            target = {**explicit, "period_context": context}
        else:
            expected_label = MONTH_NAMES_ES[month_day.month].capitalize()
            row_values = values[slot["row"] - 1]
            while len(row_values) < slot["period_col"]:
                row_values.append("")
            row_values[slot["period_col"] - 1] = expected_label
            updates.append({
                "cell": slot["period_cell"],
                "value": expected_label,
            })
            target = {
                "cell": slot["real_spend_cell"],
                "row": slot["row"],
                "col": slot["real_spend_col"],
                "group_label": slot["group_label"],
                "month_label": expected_label,
                "sequence_cell": slot["sequence_cell"],
                "period_context": context,
            }
        generated_targets.append(target)

    return generated_targets, updates


def find_month_column(values, header_row, group_start_col, group_end_col,
                      real_spend_col):
    best_col = None
    best_count = 0
    last_candidate_col = min(group_end_col, real_spend_col - 1)

    for col_index in range(group_start_col, last_candidate_col + 1):
        month_count = 0

        for row_index in range(header_row + 1, min(len(values), header_row + 20) + 1):
            row = values[row_index - 1]
            value = row[col_index - 1] if len(row) >= col_index else ""

            if parse_month_name(value):
                month_count += 1

        if month_count > best_count:
            best_col = col_index
            best_count = month_count

    if best_col:
        return best_col

    return group_start_col + 1


def saldo_month_start_day(
    group_label,
    first_month,
    month_number,
    group_start_year=None,
):
    parsed_year = parse_saldo_year_label(group_label)

    if not first_month or not month_number:
        return None

    year = group_start_year or (
        parsed_year["start_year"] if parsed_year else None
    )
    if not year:
        return None

    if month_number < first_month:
        year += 1

    return date(year, month_number, 1)


def build_saldo_target(
    item,
    real_spend_col,
    sequence_col,
    group_label,
    first_month,
    target_day,
    group_start_year=None,
):
    if item.get("range_info"):
        period_context = period_context_for_range(item["range_info"], target_day)
    else:
        month_day = saldo_month_start_day(
            group_label,
            first_month,
            item.get("month_number"),
            group_start_year,
        )
        if not month_day:
            return None

        partial_period = item.get("partial_period")
        if partial_period:
            end_day_number = partial_period.get("end_day_number") or calendar.monthrange(
                month_day.year,
                month_day.month,
            )[1]
            try:
                partial_range = {
                    "start_day": date(
                        month_day.year,
                        month_day.month,
                        partial_period["start_day_number"],
                    ),
                    "end_day": date(
                        month_day.year,
                        month_day.month,
                        end_day_number,
                    ),
                }
            except ValueError as exc:
                raise SaldoPeriodValidationError(
                    f"Periodo parcial no valido: {item['month_label']!r}."
                ) from exc
            period_context = period_context_for_range(partial_range, target_day)
        else:
            period_context = period_context_for_calendar_month(month_day, target_day)

    return {
        "cell": gspread.utils.rowcol_to_a1(item["row"], real_spend_col),
        "row": item["row"],
        "col": real_spend_col,
        "group_label": group_label,
        "month_label": item["month_label"],
        "sequence_cell": gspread.utils.rowcol_to_a1(
            item["row"],
            sequence_col,
        ),
        "period_context": period_context,
    }


def validate_saldo_period_targets(period_targets, invalid_cells, target_day=None):
    errors = []

    for cell, value in invalid_cells:
        errors.append(
            f"{cell}: periodo no reconocido ({value!r})."
        )

    ordered_targets = sorted(
        period_targets,
        key=lambda item: (
            item["period_context"]["start_day"],
            item["period_context"]["end_day"],
            item["row"],
            item["col"],
        ),
    )
    seen_periods = {}
    # Los conflictos ajenos al periodo operativo se revisaran cuando entren en vigor.
    relevant_targets = set()

    if target_day:
        current_targets = [
            target
            for target in ordered_targets
            if target["period_context"]["start_day"]
            <= target_day
            <= target["period_context"]["end_day"]
        ]
        if len(current_targets) == 1:
            current = current_targets[0]
            relevant_targets.add(id(current))
            previous_candidates = [
                target
                for target in ordered_targets
                if target["period_context"]["end_day"]
                < current["period_context"]["start_day"]
            ]
            if previous_candidates:
                relevant_targets.add(id(previous_candidates[-1]))
        elif not current_targets:
            closed_targets = [
                target
                for target in ordered_targets
                if target["period_context"]["end_day"] < target_day
            ]
            future_targets = [
                target
                for target in ordered_targets
                if target["period_context"]["start_day"] > target_day
            ]
            if closed_targets:
                relevant_targets.add(id(closed_targets[-1]))
            if future_targets:
                relevant_targets.add(id(future_targets[0]))

    for target in ordered_targets:
        context = target["period_context"]
        start_day = context["start_day"]
        end_day = context["end_day"]

        if start_day > end_day:
            errors.append(
                f"{target['cell']}: el inicio {start_day.isoformat()} es "
                f"posterior al final {end_day.isoformat()}."
            )

        period_key = (start_day, end_day)
        if (
            period_key in seen_periods
            and (
                not target_day
                or id(target) in relevant_targets
                or id(seen_periods[period_key]) in relevant_targets
            )
        ):
            errors.append(
                f"{target['cell']} y {seen_periods[period_key]['cell']}: periodo "
                f"duplicado {start_day.isoformat()} a {end_day.isoformat()}."
            )
        else:
            seen_periods[period_key] = target

    for previous, current in zip(ordered_targets, ordered_targets[1:]):
        previous_context = previous["period_context"]
        current_context = current["period_context"]

        if (
            current_context["start_day"] <= previous_context["end_day"]
            and (
                not target_day
                or id(previous) in relevant_targets
                or id(current) in relevant_targets
            )
        ):
            errors.append(
                f"{previous['cell']} y {current['cell']}: periodos "
                f"solapados ({previous_context['start_day'].isoformat()} a "
                f"{previous_context['end_day'].isoformat()} / "
                f"{current_context['start_day'].isoformat()} a "
                f"{current_context['end_day'].isoformat()})."
            )

    if not ordered_targets:
        errors.append("No hay ningun periodo valido en el bloque de saldo.")

    if errors:
        raise SaldoPeriodValidationError(
            "Validacion de periodos de saldo fallida:\n- "
            + "\n- ".join(errors)
        )

    return ordered_targets


def select_saldo_period_targets(ordered_targets, target_day):
    current_candidates = [
        target
        for target in ordered_targets
        if target["period_context"]["start_day"]
        <= target_day
        <= target["period_context"]["end_day"]
    ]

    if len(current_candidates) > 1:
        cells = ", ".join(target["cell"] for target in current_candidates)
        raise SaldoPeriodValidationError(
            f"Mas de un periodo contiene {target_day.isoformat()}: {cells}."
        )

    if current_candidates:
        current = current_candidates[0]
        previous_candidates = [
            target
            for target in ordered_targets
            if target["period_context"]["end_day"]
            < current["period_context"]["start_day"]
        ]
        previous = previous_candidates[-1] if previous_candidates else None

        return {
            "mode": "active",
            "target": current,
            "previous_target": previous,
            "next_target": None,
        }

    closed_targets = [
        target
        for target in ordered_targets
        if target["period_context"]["end_day"] < target_day
    ]
    future_targets = [
        target
        for target in ordered_targets
        if target["period_context"]["start_day"] > target_day
    ]

    return {
        "mode": "pause",
        "target": None,
        "previous_target": closed_targets[-1] if closed_targets else None,
        "next_target": future_targets[0] if future_targets else None,
    }


def find_saldo_targets(
    worksheet,
    target_day,
    continuous_contract=False,
    carry_forward_monthly_budget=False,
):
    values = get_worksheet_values(worksheet)
    contract_range = (
        find_contract_date_range(values)
        if continuous_contract
        else None
    )
    title_prefix = normalize_text("Consumo de saldo cuenta")
    title_row = None

    for row_index, row in enumerate(values, start=1):
        if any(normalize_text(value).startswith(title_prefix) for value in row):
            title_row = row_index
            break

    if not title_row:
        raise ValueError("No se encontro el bloque 'Consumo de saldo cuenta'.")

    header_row = None

    for row_index in range(title_row + 1, min(len(values), title_row + 8) + 1):
        row = values[row_index - 1]
        normalized_cells = [normalize_text(value) for value in row]

        if (
            any(value.startswith("mesesano") for value in normalized_cells)
            and "gastoreal" in normalized_cells
        ):
            header_row = row_index
            break

    if not header_row:
        raise ValueError("No se encontro la cabecera de consumo de saldo.")

    header_values = values[header_row - 1]
    group_starts = [
        col_index
        for col_index, value in enumerate(header_values, start=1)
        if normalize_text(value).startswith("mesesano")
    ]
    sequence_cells = []
    all_period_targets = []
    invalid_period_cells = []
    ignored_period_cells = []
    contract_slots = []
    period_slots = []
    base_sequence_number = None
    base_sequence_start_year = None

    for group_index, group_start_col in enumerate(group_starts):
        group_end_col = (
            group_starts[group_index + 1] - 1
            if group_index + 1 < len(group_starts)
            else len(header_values)
        )
        group_label = header_values[group_start_col - 1]
        real_spend_col = None
        monthly_budget_col = None

        for col_index in range(group_start_col, group_end_col + 1):
            value = header_values[col_index - 1] if len(header_values) >= col_index else ""
            normalized_value = normalize_text(value)
            if normalized_value == "gastoreal":
                real_spend_col = col_index
            elif normalized_value in {
                "presupmes",
                "presupuestomes",
                "presupuestomensual",
            }:
                monthly_budget_col = col_index

        if not real_spend_col:
            continue

        sequence_col = group_start_col
        # Regla estandar preferente: el periodo que corresponde a `Gasto real`
        # esta dos columnas a su izquierda. Funciona tanto para `Julio` como
        # para rangos tipo `11/06 - 10/07`.
        period_col = real_spend_col - 2

        if period_col < group_start_col:
            period_col = find_month_column(
                values,
                header_row,
                group_start_col,
                group_end_col,
                real_spend_col,
            )

        first_sequence_number = None
        for row_index in range(header_row + 1, min(len(values), header_row + 20) + 1):
            row = values[row_index - 1]
            sequence_value = (
                row[sequence_col - 1]
                if len(row) >= sequence_col
                else ""
            )
            first_sequence_number = parse_sequence_number(sequence_value)
            if first_sequence_number is not None:
                break

        parsed_group_year = parse_saldo_year_label(group_label)
        group_start_year = (
            parsed_group_year["start_year"]
            if parsed_group_year
            else None
        )

        if (
            base_sequence_number is None
            and first_sequence_number is not None
            and group_start_year is not None
        ):
            base_sequence_number = first_sequence_number
            base_sequence_start_year = group_start_year

        if (
            base_sequence_number is not None
            and base_sequence_start_year is not None
            and first_sequence_number is not None
        ):
            # La secuencia corrige encabezados fiscales copiados sin cambiar sus fechas.
            sequence_year = base_sequence_start_year + max(
                0,
                (first_sequence_number - base_sequence_number) // 12,
            )
            group_start_year = max(group_start_year or sequence_year, sequence_year)

        group_period_rows = []

        for row_index in range(header_row + 1, min(len(values), header_row + 20) + 1):
            row = values[row_index - 1]
            sequence_value = row[sequence_col - 1] if len(row) >= sequence_col else ""
            sequence_number = parse_sequence_number(sequence_value)
            if sequence_number is None:
                continue

            slot = {
                "row": row_index,
                "sequence_number": sequence_number,
                "sequence_cell": gspread.utils.rowcol_to_a1(
                    row_index,
                    sequence_col,
                ),
                "period_col": period_col,
                "period_cell": gspread.utils.rowcol_to_a1(
                    row_index,
                    period_col,
                ),
                "monthly_budget_col": monthly_budget_col,
                "monthly_budget_cell": (
                    gspread.utils.rowcol_to_a1(
                        row_index,
                        monthly_budget_col,
                    )
                    if monthly_budget_col
                    else None
                ),
                "real_spend_col": real_spend_col,
                "real_spend_cell": gspread.utils.rowcol_to_a1(
                    row_index,
                    real_spend_col,
                ),
                "group_label": group_label,
            }
            period_slots.append(slot)
            if continuous_contract:
                contract_slots.append(slot)

            period_value = row[period_col - 1] if len(row) >= period_col else ""
            if is_uncertain_period(period_value):
                ignored_period_cells.append(
                    gspread.utils.rowcol_to_a1(row_index, period_col)
                )
                continue

            partial_period = parse_partial_month_period(period_value)
            named_range_parts = parse_named_date_range_parts(period_value)
            month_number = (
                parse_month_name(period_value)
                or (partial_period or {}).get("month_number")
            )
            range_start_month = (
                date_range_start_month(period_value)
                or (named_range_parts or {}).get("start_month")
            )

            if not range_start_month and not month_number and not partial_period:
                if str(period_value).strip():
                    invalid_period_cells.append((
                        gspread.utils.rowcol_to_a1(row_index, period_col),
                        period_value,
                    ))
                continue

            group_period_rows.append({
                "row": row_index,
                "col": sequence_col,
                "month_number": month_number,
                "month_label": period_value,
                "range_info": None,
                "range_start_month": range_start_month,
                "named_range_parts": named_range_parts,
                "partial_period": partial_period,
            })

        first_period_month = next(
            (
                item["range_start_month"] or item["month_number"]
                for item in group_period_rows
                if item["range_start_month"] or item["month_number"]
            ),
            None,
        )
        parsed_group_period_rows = []

        for item in group_period_rows:
            if item["range_start_month"]:
                default_start_year = group_start_year
                if (
                    default_start_year
                    and first_period_month
                    and item["range_start_month"] < first_period_month
                ):
                    default_start_year += 1

                if item["named_range_parts"]:
                    item["range_info"] = build_named_date_range(
                        item["named_range_parts"],
                        default_start_year,
                    )
                else:
                    item["range_info"] = parse_date_range(
                        item["month_label"],
                        target_day,
                        default_start_year=default_start_year,
                    )
                if not item["range_info"]:
                    invalid_period_cells.append((
                        gspread.utils.rowcol_to_a1(item["row"], period_col),
                        item["month_label"],
                    ))
                    continue

            parsed_group_period_rows.append(item)

        group_period_rows = parsed_group_period_rows

        for item_index, item in enumerate(group_period_rows):
            sequence_cells.append({
                **item,
                "is_first": item_index == 0,
                "is_last": item_index == len(group_period_rows) - 1,
            })

        for item in group_period_rows:
            candidate = build_saldo_target(
                item,
                real_spend_col,
                sequence_col,
                group_label,
                first_period_month,
                target_day,
                group_start_year,
            )
            if candidate:
                slot = next(
                    (
                        period_slot
                        for period_slot in period_slots
                        if period_slot["row"] == item["row"]
                        and period_slot["real_spend_col"] == real_spend_col
                    ),
                    None,
                )
                if slot:
                    candidate["period_cell"] = slot["period_cell"]
                    candidate["monthly_budget_cell"] = slot[
                        "monthly_budget_cell"
                    ]
                all_period_targets.append(candidate)

    contract_updates = []
    if continuous_contract:
        all_period_targets, contract_updates = apply_continuous_contract_months(
            values,
            all_period_targets,
            contract_slots,
            contract_range,
            target_day,
        )

    ordered_targets = validate_saldo_period_targets(
        all_period_targets,
        invalid_period_cells,
        target_day,
    )
    monthly_renewal = plan_monthly_renewal_updates(
        worksheet,
        values,
        ordered_targets,
        target_day,
    )
    budget_carry_forward = plan_monthly_budget_carry_forward(
        values,
        ordered_targets,
        target_day,
        enabled=carry_forward_monthly_budget,
    )
    selected = select_saldo_period_targets(ordered_targets, target_day)
    selected["sequence_cells"] = sequence_cells
    selected["period_targets"] = ordered_targets
    selected["ignored_period_cells"] = ignored_period_cells
    selected["continuous_contract_range"] = contract_range
    selected["continuous_contract_updates"] = contract_updates
    selected["monthly_renewal"] = monthly_renewal
    selected["budget_carry_forward"] = budget_carry_forward
    selected["period_context"] = (
        selected["target"]["period_context"]
        if selected["target"]
        else None
    )

    return selected


def find_monthly_real_spend_cell(worksheet, target_day):
    target = find_saldo_targets(worksheet, target_day)["target"]
    if not target:
        raise ValueError(
            f"No existe un periodo vigente para {target_day.isoformat()}."
        )
    return target


def build_border(style):
    if style == "NONE":
        return {"style": "NONE"}

    return {
        "style": style,
        "width": 1,
        "color": {
            "red": 0,
            "green": 0,
            "blue": 0,
        },
    }


def update_current_saldo_month_marker(
    worksheet,
    saldo_targets,
    mutation_batch=None,
):
    normal_background = {
        "red": 1,
        "green": 1,
        "blue": 1,
    }
    current_background = {
        "red": 1,
        "green": 0.6,
        "blue": 0,
    }
    requests = []

    for cell in saldo_targets["sequence_cells"]:
        row_index = cell["row"]
        col_index = cell["col"]

        requests.extend([
            {
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": row_index - 1,
                        "endRowIndex": row_index,
                        "startColumnIndex": col_index - 1,
                        "endColumnIndex": col_index,
                    },
                    "cell": {
                        "note": "",
                        "userEnteredFormat": {
                            "backgroundColor": normal_background,
                            "textFormat": {
                                "bold": False,
                            },
                        },
                    },
                    "fields": (
                        "note,"
                        "userEnteredFormat.backgroundColor,"
                        "userEnteredFormat.textFormat.bold"
                    ),
                }
            },
            {
                "updateBorders": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": row_index - 1,
                        "endRowIndex": row_index,
                        "startColumnIndex": col_index - 1,
                        "endColumnIndex": col_index,
                    },
                    "top": build_border("SOLID" if cell["is_first"] else "NONE"),
                    "bottom": build_border("SOLID" if cell["is_last"] else "DOTTED"),
                    "left": build_border("SOLID"),
                    "right": build_border("DOTTED"),
                }
            },
        ])

    target = saldo_targets.get("target")
    if target:
        current_row, current_col = gspread.utils.a1_to_rowcol(
            target["sequence_cell"]
        )
        requests.extend([
            {
                "repeatCell": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": current_row - 1,
                        "endRowIndex": current_row,
                        "startColumnIndex": current_col - 1,
                        "endColumnIndex": current_col,
                    },
                    "cell": {
                        "note": "Mes en curso.",
                        "userEnteredFormat": {
                            "backgroundColor": current_background,
                            "textFormat": {
                                "bold": True,
                            },
                        },
                    },
                    "fields": (
                        "note,"
                        "userEnteredFormat.backgroundColor,"
                        "userEnteredFormat.textFormat.bold"
                    ),
                }
            },
            {
                "updateBorders": {
                    "range": {
                        "sheetId": worksheet.id,
                        "startRowIndex": current_row - 1,
                        "endRowIndex": current_row,
                        "startColumnIndex": current_col - 1,
                        "endColumnIndex": current_col,
                    },
                    "top": build_border("SOLID"),
                    "bottom": build_border("SOLID"),
                    "left": build_border("SOLID"),
                    "right": build_border("SOLID"),
                }
            },
        ])

    queue_format_requests(worksheet, requests, mutation_batch)


def update_closed_real_spend_during_pause(
    worksheet,
    total_cost,
    saldo_targets,
    mutation_batch=None,
):
    closed_target = saldo_targets.get("previous_target")
    if not closed_target:
        update_current_saldo_month_marker(
            worksheet,
            saldo_targets,
            mutation_batch,
        )
        return None

    closed_cost = round(float(total_cost), 2)
    queue_values_update(
        worksheet,
        closed_target["cell"],
        [[closed_cost]],
        mutation_batch,
    )
    update_current_saldo_month_marker(
        worksheet,
        saldo_targets,
        mutation_batch,
    )

    return {
        **closed_target,
        "monthly_cost": closed_cost,
    }


def update_monthly_real_spend(
    worksheet,
    target_day,
    total_cost,
    previous_total_cost=None,
    saldo_targets=None,
    mutation_batch=None,
    update_marker=True,
):
    saldo_targets = saldo_targets or find_saldo_targets(worksheet, target_day)
    target = saldo_targets["target"]
    monthly_cost = round(float(total_cost), 2)
    previous_target = saldo_targets.get("previous_target")
    updates = [(target["cell"], monthly_cost)]
    previous_result = None

    if previous_target and previous_total_cost is not None:
        previous_cost = round(float(previous_total_cost), 2)
        updates.insert(0, (previous_target["cell"], previous_cost))
        previous_result = {
            **previous_target,
            "monthly_cost": previous_cost,
        }

    for range_name, value in updates:
        queue_values_update(
            worksheet,
            range_name,
            [[value]],
            mutation_batch,
        )
    if update_marker:
        update_current_saldo_month_marker(
            worksheet,
            saldo_targets,
            mutation_batch,
        )

    return {
        **target,
        "monthly_cost": monthly_cost,
        "previous": previous_result,
    }
