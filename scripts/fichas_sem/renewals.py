"""SEM: renewals."""

from .errors import (
    SaldoPeriodValidationError,
)

from .periods import (
    add_one_contract_month,
    google_sheets_date_serial,
    parse_sheet_date,
)

from datetime import date
import calendar
import gspread
from .constants import (
    MONTHLY_RENEWAL_NOTE_PREFIX,
    MONTHLY_RENEWAL_TEXTS,
)
from .runtime import (
    MONTHLY_RENEWAL_NOTE_PREFIXES,
)

from .text import (
    normalize_text,
)


def find_contract_date_range(values):
    """Localiza Fecha inicio/Fecha Fin y devuelve el contrato continuo."""
    for row_index, row in enumerate(values):
        normalized = [normalize_text(value) for value in row]
        if "fechainicio" not in normalized or "fechafin" not in normalized:
            continue

        if row_index + 1 >= len(values):
            break

        start_col = normalized.index("fechainicio")
        end_col = normalized.index("fechafin")
        value_row = values[row_index + 1]
        start_value = value_row[start_col] if len(value_row) > start_col else ""
        end_value = value_row[end_col] if len(value_row) > end_col else ""
        start_day = parse_sheet_date(start_value)
        end_day = parse_sheet_date(end_value)

        if not start_day or not end_day:
            raise SaldoPeriodValidationError(
                "Fecha inicio o Fecha Fin no tiene un formato reconocido."
            )
        if end_day < start_day:
            raise SaldoPeriodValidationError(
                "Fecha Fin es anterior a Fecha inicio."
            )
        return {
            "start_day": start_day,
            "end_day": end_day,
            "start_cell": gspread.utils.rowcol_to_a1(
                row_index + 2,
                start_col + 1,
            ),
            "end_cell": gspread.utils.rowcol_to_a1(
                row_index + 2,
                end_col + 1,
            ),
        }

    raise SaldoPeriodValidationError(
        "No se encontraron Fecha inicio y Fecha Fin para el contrato continuo."
    )


def has_monthly_renewal_without_end(values):
    renewal_keys = [normalize_text(value) for value in MONTHLY_RENEWAL_TEXTS]
    return any(
        any(renewal_key in normalize_text(value) for renewal_key in renewal_keys)
        for row in values
        for value in row
    )


def monthly_renewal_note(existing_note, renewal_month):
    marker = MONTHLY_RENEWAL_NOTE_PREFIX + renewal_month
    kept_lines = [
        line
        for line in str(existing_note or "").splitlines()
        if not any(
            line.startswith(prefix)
            for prefix in MONTHLY_RENEWAL_NOTE_PREFIXES
        )
    ]
    kept_lines.append(marker)
    return "\n".join(line for line in kept_lines if line).strip()


def plan_monthly_renewal_updates(
    worksheet,
    values,
    period_targets,
    target_day,
):
    result = {
        "enabled": has_monthly_renewal_without_end(values),
        "applied": False,
        "catch_up": False,
        "value_updates": [],
        "note_update": None,
        "period_label": None,
        "reason": None,
    }
    if not result["enabled"]:
        return result

    contract_range = find_contract_date_range(values)
    active_targets = [
        target
        for target in period_targets
        if target["period_context"]["start_day"]
        <= target_day
        <= target["period_context"]["end_day"]
    ]
    if len(active_targets) != 1:
        result["reason"] = "No hay un unico periodo escrito vigente para hoy."
        return result

    # Una ficha puede cambiar de meses naturales a rangos personalizados en
    # otro ano. Solo el periodo vigente decide que regla de renovacion usar;
    # los rangos futuros no deben reclasificar los meses actuales.
    current_month_target = active_targets[0]
    uses_custom_ranges = (
        current_month_target["period_context"]["mode"] == "range"
    )

    if uses_custom_ranges:
        range_start = current_month_target["period_context"]["start_day"]
        if (range_start.year, range_start.month) != (
            target_day.year,
            target_day.month,
        ):
            result["reason"] = (
                "El rango vigente empezo en un mes anterior y todavia no ha "
                "comenzado el siguiente periodo."
            )
            return result
        result["catch_up"] = target_day != range_start
    else:
        current_month_end = date(
            target_day.year,
            target_day.month,
            calendar.monthrange(target_day.year, target_day.month)[1],
        )
        current_month_start = target_day.replace(day=1)
        result["catch_up"] = target_day.day != 1
        if (
            result["catch_up"]
            and contract_range["end_day"] >= current_month_start
        ):
            result["reason"] = (
                "Fuera del dia 1 solo se recuperan renovaciones cuya Fecha Fin "
                "es anterior al mes actual."
            )
            return result

        if (
            current_month_target["period_context"]["start_day"].year,
            current_month_target["period_context"]["start_day"].month,
        ) != (target_day.year, target_day.month):
            result["reason"] = (
                "No hay un mes escrito que empiece en el mes actual."
            )
            return result

    previous_targets = [
        target
        for target in period_targets
        if target["period_context"]["end_day"]
        < current_month_target["period_context"]["start_day"]
    ]
    if not previous_targets:
        budget_cell = current_month_target.get("monthly_budget_cell")
        if budget_cell:
            budget_row, budget_col = gspread.utils.a1_to_rowcol(budget_cell)
            budget_value = (
                values[budget_row - 1][budget_col - 1]
                if len(values) >= budget_row
                and len(values[budget_row - 1]) >= budget_col
                else ""
            )
            if (
                str(budget_value).strip()
                and contract_range["start_day"]
                == current_month_target["period_context"]["start_day"]
                and contract_range["end_day"]
                >= current_month_target["period_context"]["end_day"]
            ):
                result["reason"] = "Primer periodo ya inicializado con presupuesto y fecha fin."
                return result
        raise SaldoPeriodValidationError(
            "La renovacion mensual no encontro el periodo anterior."
        )
    current_budget_cell = current_month_target.get("monthly_budget_cell")
    if not current_budget_cell:
        raise SaldoPeriodValidationError(
            "La renovacion mensual no encontro la columna 'Presup mes'."
        )

    renewal_start = current_month_target["period_context"]["start_day"]
    renewal_month = f"{renewal_start.year:04d}-{renewal_start.month:02d}"
    existing_note = getattr(
        worksheet,
        "_sem_monthly_renewal_note",
        None,
    )
    if existing_note is None:
        existing_note = worksheet.get_note(contract_range["end_cell"])
    if any(
        prefix + renewal_month in str(existing_note or "")
        for prefix in MONTHLY_RENEWAL_NOTE_PREFIXES
    ):
        result["reason"] = (
            f"La renovacion {renewal_month} ya estaba aplicada."
        )
        return result

    current_budget_row, current_budget_col = (
        gspread.utils.a1_to_rowcol(current_budget_cell)
    )
    current_budget = (
        values[current_budget_row - 1][current_budget_col - 1]
        if len(values) >= current_budget_row
        and len(values[current_budget_row - 1]) >= current_budget_col
        else ""
    )

    previous_budget = ""
    previous_budget_cell = None
    for previous_target in reversed(previous_targets):
        candidate_cell = previous_target.get("monthly_budget_cell")
        if not candidate_cell:
            continue
        candidate_row, candidate_col = gspread.utils.a1_to_rowcol(
            candidate_cell
        )
        candidate_value = (
            values[candidate_row - 1][candidate_col - 1]
            if len(values) >= candidate_row
            and len(values[candidate_row - 1]) >= candidate_col
            else ""
        )
        if str(candidate_value).strip():
            previous_budget = candidate_value
            previous_budget_cell = candidate_cell
            break

    if previous_budget_cell is None:
        raise SaldoPeriodValidationError(
            "La renovacion mensual no encontro ningun presupuesto anterior "
            "informado."
        )

    if not str(current_budget).strip():
        result["value_updates"].append({
            "cell": current_budget_cell,
            "value": previous_budget,
            "kind": "monthly_budget",
            "source_cell": previous_budget_cell,
        })

    next_contract_end = add_one_contract_month(contract_range["end_day"])
    if uses_custom_ranges:
        next_contract_end = max(
            next_contract_end,
            current_month_target["period_context"]["end_day"],
        )
    else:
        next_contract_end = max(next_contract_end, current_month_end)
    result["value_updates"].append({
        "cell": contract_range["end_cell"],
        "value": google_sheets_date_serial(next_contract_end),
        "kind": "contract_end",
        "date": next_contract_end,
    })
    result["note_update"] = {
        "cell": contract_range["end_cell"],
        "note": monthly_renewal_note(existing_note, renewal_month),
    }
    result["period_label"] = current_month_target["month_label"]
    result["applied"] = True
    return result


def plan_monthly_budget_carry_forward(
    values,
    period_targets,
    target_day,
    enabled=False,
):
    """Copia el ultimo presupuesto al periodo vigente sin ampliar contrato."""
    result = {
        "enabled": bool(enabled),
        "applied": False,
        "value_update": None,
        "period_label": None,
        "reason": None,
    }
    if not result["enabled"]:
        return result

    active_targets = [
        target
        for target in period_targets
        if target["period_context"]["start_day"]
        <= target_day
        <= target["period_context"]["end_day"]
    ]
    if len(active_targets) != 1:
        result["reason"] = "No hay un unico periodo vigente para copiar presupuesto."
        return result

    current_target = active_targets[0]
    current_cell = current_target.get("monthly_budget_cell")
    if not current_cell:
        result["reason"] = "El periodo vigente no tiene columna 'Presup mes'."
        return result

    current_row, current_col = gspread.utils.a1_to_rowcol(current_cell)
    current_value = (
        values[current_row - 1][current_col - 1]
        if len(values) >= current_row
        and len(values[current_row - 1]) >= current_col
        else ""
    )
    if str(current_value).strip():
        result["reason"] = "El presupuesto vigente ya esta informado."
        return result

    previous_targets = [
        target
        for target in period_targets
        if target["period_context"]["end_day"]
        < current_target["period_context"]["start_day"]
    ]
    for previous_target in reversed(previous_targets):
        source_cell = previous_target.get("monthly_budget_cell")
        if not source_cell:
            continue
        source_row, source_col = gspread.utils.a1_to_rowcol(source_cell)
        source_value = (
            values[source_row - 1][source_col - 1]
            if len(values) >= source_row
            and len(values[source_row - 1]) >= source_col
            else ""
        )
        if not str(source_value).strip():
            continue

        result["value_update"] = {
            "cell": current_cell,
            "value": source_value,
            "kind": "monthly_budget_carry_forward",
            "source_cell": source_cell,
        }
        result["period_label"] = current_target["month_label"]
        result["applied"] = True
        return result

    result["reason"] = "No existe un presupuesto anterior informado."
    return result
