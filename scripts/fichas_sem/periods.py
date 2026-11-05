"""SEM: periods."""

from datetime import date
from datetime import datetime
import calendar
import re
import unicodedata
from .constants import (
    DATE_RANGE_PATTERN,
    GOOGLE_SHEETS_DATE_EPOCH,
    MONTH_NAMES_ES,
    MONTH_NUMBERS_BY_NAME,
    PARTIAL_MONTH_END_PATTERN,
    PARTIAL_MONTH_START_PATTERN,
    SPAIN_TIMEZONE,
)

from .text import (
    fold_text,
    normalize_text,
)


def parse_month_name(value):
    exact_match = MONTH_NUMBERS_BY_NAME.get(normalize_text(value))
    if exact_match:
        return exact_match

    for token in re.findall(r"[a-z]+", fold_text(value)):
        month_number = MONTH_NUMBERS_BY_NAME.get(token)
        if month_number:
            return month_number

    return None


def is_total_cost_cell(value):
    # La variante `campa?as` quedo escrita por una conversion de codificacion
    # antigua. Se reconoce para poder repararla al actualizar la fila.
    return normalize_text(value) in {
        "totalcostecampanas",
        "totalcostecampaas",
        "totales",
    }


def today_in_spain():
    return datetime.now(SPAIN_TIMEZONE).date()


def get_month_dates():
    today = today_in_spain()
    return today.replace(day=1), today


def parse_partial_month_period(value):
    text = fold_text(value)

    for mode, pattern in (
        ("month_start_to_date", PARTIAL_MONTH_END_PATTERN),
        ("date_to_month_end", PARTIAL_MONTH_START_PATTERN),
    ):
        match = pattern.match(text)
        if not match:
            continue

        month_number = MONTH_NUMBERS_BY_NAME.get(match.group("month"))
        numeric_month = int(match.group("numeric_month"))
        day_number = int(match.group("day"))

        if not month_number or numeric_month != month_number:
            return None

        if day_number < 1 or day_number > calendar.monthrange(2000, month_number)[1]:
            return None

        return {
            "month_number": month_number,
            "start_day_number": 1 if mode == "month_start_to_date" else day_number,
            "end_day_number": day_number if mode == "month_start_to_date" else None,
        }

    return None


def parse_named_date_range_parts(value):
    text = fold_text(value).strip()

    day_span = re.fullmatch(
        r"\(?\s*(?P<start_day>\d{1,2})\s*-\s*"
        r"(?P<end_day>\d{1,2})\s*\)?\s*(?P<month>[a-z]+)",
        text,
    )
    if day_span:
        month_number = MONTH_NUMBERS_BY_NAME.get(day_span.group("month"))
        if month_number:
            return {
                "start_day_number": int(day_span.group("start_day")),
                "start_month": month_number,
                "end_day_number": int(day_span.group("end_day")),
                "end_month": month_number,
            }

    month_to_numeric = re.fullmatch(
        r"(?P<month>[a-z]+)\s*-\s*(?P<end_day>\d{1,2})\s*/\s*"
        r"(?P<end_month>\d{1,2})",
        text,
    )
    if month_to_numeric:
        month_number = MONTH_NUMBERS_BY_NAME.get(
            month_to_numeric.group("month")
        )
        if month_number:
            return {
                "start_day_number": 1,
                "start_month": month_number,
                "end_day_number": int(month_to_numeric.group("end_day")),
                "end_month": int(month_to_numeric.group("end_month")),
            }

    numeric_to_month = re.fullmatch(
        r"(?P<start_day>\d{1,2})\s*[/\-]\s*"
        r"(?P<start_month>\d{1,2})\s+-\s+"
        r"(?:(?P<end_day>\d{1,2})\s*/\s*)?"
        r"(?P<end_month>[a-z]+)",
        text,
    )
    if numeric_to_month:
        end_month = MONTH_NUMBERS_BY_NAME.get(
            numeric_to_month.group("end_month")
        )
        if end_month:
            end_day = numeric_to_month.group("end_day")
            return {
                "start_day_number": int(numeric_to_month.group("start_day")),
                "start_month": int(numeric_to_month.group("start_month")),
                "end_day_number": int(end_day) if end_day else None,
                "end_month": end_month,
            }

    return None


def build_named_date_range(parts, start_year):
    if not parts or not start_year:
        return None

    end_year = start_year
    if parts["end_month"] < parts["start_month"]:
        end_year += 1

    end_day_number = parts["end_day_number"] or calendar.monthrange(
        end_year,
        parts["end_month"],
    )[1]

    try:
        return {
            "start_day": date(
                start_year,
                parts["start_month"],
                parts["start_day_number"],
            ),
            "end_day": date(
                end_year,
                parts["end_month"],
                end_day_number,
            ),
        }
    except ValueError:
        return None


def is_uncertain_period(value):
    return str(value or "").strip().endswith("?")


def normalize_year(value, base_year=None):
    if not value:
        return base_year

    year = int(value)

    if year < 100:
        century = (base_year or today_in_spain().year) // 100 * 100
        year = century + year

    return year


def extract_explicit_year(value):
    match = re.search(r"\b(20\d{2})\b", str(value or ""))
    return int(match.group(1)) if match else None


def parse_date_range(
    value,
    reference_day=None,
    require_contains=False,
    default_start_year=None,
):
    text = str(value or "")
    match = DATE_RANGE_PATTERN.search(text)

    if not match:
        return None

    reference_day = reference_day or today_in_spain()
    explicit_year = extract_explicit_year(text)
    start_day = int(match.group("start_day"))
    start_month = int(match.group("start_month"))
    end_day = int(match.group("end_day"))
    end_month = int(match.group("end_month"))
    start_year_text = match.group("start_year")
    end_year_text = match.group("end_year")
    candidate_start_years = []

    if start_year_text:
        candidate_start_years.append(normalize_year(start_year_text, reference_day.year))
    elif explicit_year:
        candidate_start_years.append(explicit_year)
    elif default_start_year:
        candidate_start_years.append(default_start_year)
    else:
        candidate_start_years.extend([
            reference_day.year - 1,
            reference_day.year,
            reference_day.year + 1,
        ])

    candidates = []

    for start_year in candidate_start_years:
        if end_year_text:
            end_year = normalize_year(end_year_text, start_year)
        else:
            end_year = start_year
            if (end_month, end_day) < (start_month, start_day):
                end_year += 1

        try:
            start = date(start_year, start_month, start_day)
            end = date(end_year, end_month, end_day)
        except ValueError:
            continue

        candidates.append({
            "start_day": start,
            "end_day": end,
            "contains_reference": start <= reference_day <= end,
        })

    if not candidates:
        return None

    containing = [
        candidate
        for candidate in candidates
        if candidate["contains_reference"]
    ]

    if require_contains and not containing:
        return None

    if containing:
        return containing[0]

    return min(
        candidates,
        key=lambda candidate: abs((candidate["start_day"] - reference_day).days),
    )


def month_label_for_day(day):
    month_name = MONTH_NAMES_ES[day.month].capitalize()
    return f"Periodo seleccionado: {month_name} {day.year}"


def range_label_for_days(start_day, end_day):
    if start_day.year == end_day.year:
        return (
            "Periodo seleccionado: "
            f"{start_day:%d/%m} - {end_day:%d/%m} {end_day.year}"
        )
    return (
        "Periodo seleccionado: "
        f"{start_day:%d/%m/%Y} - {end_day:%d/%m/%Y}"
    )


def period_context_for_calendar_month(month_day, target_day):
    start_day = month_day.replace(day=1)
    end_day = date(
        start_day.year,
        start_day.month,
        calendar.monthrange(start_day.year, start_day.month)[1],
    )

    return {
        "mode": "month",
        "start_day": start_day,
        "end_day": end_day,
        "query_start_day": start_day,
        "query_end_day": min(end_day, target_day),
        "key": month_key_for_day(start_day),
        "label": month_label_for_day(start_day),
    }


def period_context_for_month(target_day):
    return period_context_for_calendar_month(target_day, target_day)


def period_context_for_range(range_info, target_day):
    query_end_day = min(range_info["end_day"], target_day)

    return {
        "mode": "range",
        "start_day": range_info["start_day"],
        "end_day": range_info["end_day"],
        "query_start_day": range_info["start_day"],
        "query_end_day": query_end_day,
        "key": (
            f"{range_info['start_day'].isoformat()}:"
            f"{range_info['end_day'].isoformat()}"
        ),
        "label": range_label_for_days(
            range_info["start_day"],
            range_info["end_day"],
        ),
    }


def month_key_for_day(day):
    return f"{day.year}-{day.month:02d}"


def parse_period_identity(value, reference_day=None):
    range_info = parse_date_range(value, reference_day)

    if range_info:
        return {
            "mode": "range",
            "start_day": range_info["start_day"],
            "end_day": range_info["end_day"],
            "key": (
                f"{range_info['start_day'].isoformat()}:"
                f"{range_info['end_day'].isoformat()}"
            ),
            "label": range_label_for_days(
                range_info["start_day"],
                range_info["end_day"],
            ),
        }

    period_month = parse_period_month(value)

    if not period_month:
        return None

    return {
        "mode": "month",
        "start_day": period_month,
        "key": month_key_for_day(period_month),
        "label": month_key_for_day(period_month),
    }


def parse_period_month(value):
    text = str(value or "").strip()

    if not text:
        return None

    normalized = unicodedata.normalize("NFKD", text)
    normalized = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    ).lower()
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    normalized = re.sub(r"^(periodo|perido) seleccionado", "", normalized).strip()

    month_lookup = {
        normalize_text(name): month
        for month, name in MONTH_NAMES_ES.items()
    }
    month_lookup.update({
        "ene": 1,
        "feb": 2,
        "mar": 3,
        "abr": 4,
        "may": 5,
        "jun": 6,
        "jul": 7,
        "ago": 8,
        "sep": 9,
        "oct": 10,
        "nov": 11,
        "dic": 12,
    })

    year = None
    month = None

    for token in normalized.split():
        if re.fullmatch(r"20\d{2}", token):
            year = int(token)
            continue

        token_month = month_lookup.get(normalize_text(token))
        if token_month:
            month = token_month

    if not year or not month:
        return None

    return date(year, month, 1)


def parse_period_month_number(value):
    if not is_period_cell(value):
        return None

    normalized = fold_text(value)
    normalized = re.sub(r"[^a-z0-9]+", " ", normalized).strip()
    normalized = re.sub(
        r"^(periodo|perido) seleccionado",
        "",
        normalized,
    ).strip()

    for token in normalized.split():
        month_number = MONTH_NUMBERS_BY_NAME.get(token)
        if month_number:
            return month_number

    return None


def is_period_cell(value):
    normalized = normalize_text(value)
    return normalized.startswith("periodoseleccionado") or normalized.startswith(
        "peridoseleccionado"
    )


def is_month_current_cell(value):
    return normalize_text(value) == "mesactual"


def is_previous_months_cell(value):
    normalized = normalize_text(value)
    return normalized.startswith("mesesanteriores")


def parse_saldo_year_label(value):
    match = re.search(r"(20\d{2})(?:\D+(\d{2,4}))?", str(value or ""))

    if not match:
        return None

    start_year = int(match.group(1))
    end_year = None

    if match.group(2):
        end_text = match.group(2)
        end_year = int(end_text)

        if len(end_text) == 2:
            end_year = (start_year // 100) * 100 + end_year
            if end_year < start_year:
                end_year += 100

    return {
        "start_year": start_year,
        "end_year": end_year,
    }


def parse_sequence_number(value):
    text = str(value or "").strip().replace(",", ".")

    if not re.fullmatch(r"\d+(?:\.0+)?", text):
        return None

    return int(float(text))


def date_range_start_month(value):
    match = DATE_RANGE_PATTERN.search(str(value or ""))

    if not match:
        return None

    return int(match.group("start_month"))


def parse_sheet_date(value):
    """Interpreta fechas ISO, numericas o con meses abreviados en espanol."""
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value

    text = str(value or "").strip()
    if not text:
        return None

    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        pass

    normalized = unicodedata.normalize("NFKD", text.lower())
    normalized = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    match = re.fullmatch(
        r"\s*(\d{1,2})\s*[-/. ]\s*([a-z]+|\d{1,2})"
        r"\s*[-/. ]\s*(\d{4})\s*",
        normalized,
    )
    if not match:
        return None

    day_number = int(match.group(1))
    month_token = match.group(2)
    month_number = (
        int(month_token)
        if month_token.isdigit()
        else MONTH_NUMBERS_BY_NAME.get(month_token)
    )
    if not month_number:
        return None

    try:
        return date(int(match.group(3)), month_number, day_number)
    except ValueError:
        return None


def add_calendar_months(month_day, offset):
    month_index = month_day.year * 12 + month_day.month - 1 + offset
    return date(month_index // 12, month_index % 12 + 1, 1)


def add_one_contract_month(day):
    next_month = add_calendar_months(day.replace(day=1), 1)
    next_month_last_day = calendar.monthrange(
        next_month.year,
        next_month.month,
    )[1]
    current_month_last_day = calendar.monthrange(day.year, day.month)[1]
    target_day = (
        next_month_last_day
        if day.day == current_month_last_day
        else min(day.day, next_month_last_day)
    )
    return date(next_month.year, next_month.month, target_day)


def google_sheets_date_serial(day):
    return (day - GOOGLE_SHEETS_DATE_EPOCH).days
