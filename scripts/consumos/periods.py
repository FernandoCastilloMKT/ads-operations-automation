"""Consumption: periods."""

from calendar import monthrange
from datetime import date
from datetime import timedelta
import re

from .text import (
    normalize_sheet_text,
)


def get_month_dates():
    today = date.today()
    start_date = today.replace(day=1)
    return start_date.isoformat(), today.isoformat()


def get_full_month_dates_for_day(day):
    start_date = day.replace(day=1)
    end_date = day.replace(day=monthrange(day.year, day.month)[1])
    return start_date.isoformat(), end_date.isoformat()


def get_quick_dates(lookback_days):
    today = date.today()
    month_start = today.replace(day=1)
    days = max(1, int(lookback_days))
    quick_start = max(month_start, today - timedelta(days=days - 1))
    return quick_start.isoformat(), today.isoformat()


def parse_month_key(month_key):
    year, month = month_key.split("-")
    return int(year), int(month)


def get_month_start_date(month_key):
    year, month = parse_month_key(month_key)
    return date(year, month, 1)


def get_month_key_from_day(day):
    return str(day)[:7]


def get_reporting_start_date(cliente):
    value = cliente.get("reporting_start_month")
    if value is None:
        return None
    if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}", value):
        raise ValueError("reporting_start_month debe tener formato YYYY-MM.")
    return get_month_start_date(value)


def limit_reporting_month_keys(cliente, month_keys):
    start = get_reporting_start_date(cliente)
    return [key for key in month_keys if start is None or get_month_start_date(key) >= start]


def get_month_keys_between(start_month_key, end_day):
    """
    Devuelve meses en formato YYYY-MM desde el mes inicial hasta el mes actual.
    Se usa para rellenar la hoja historica sin tocar meses futuros.
    """
    year, month = parse_month_key(start_month_key)
    current = date(year, month, 1)
    end_month = end_day.replace(day=1)
    month_keys = []

    while current <= end_month:
        month_keys.append(f"{current.year}-{current.month:02d}")

        if current.month == 12:
            current = date(current.year + 1, 1, 1)
        else:
            current = date(current.year, current.month + 1, 1)

    return month_keys


def get_monthly_period(cliente):
    today = date.today()
    start_month_key = get_monthly_start_month(cliente, today)
    start_date = get_month_start_date(start_month_key)
    month_keys = get_month_keys_between(start_month_key, today)
    return start_date.isoformat(), today.isoformat(), month_keys


def get_month_abbr(month):
    month_abbrs = {
        1: "Ene",
        2: "Feb",
        3: "Mar",
        4: "Abr",
        5: "May",
        6: "Jun",
        7: "Jul",
        8: "Ago",
        9: "Sep",
        10: "Oct",
        11: "Nov",
        12: "Dic",
    }
    return month_abbrs[month]


def get_daily_sheet_name(cliente, target_day=None):
    template = cliente.get("daily_worksheet_name_template")

    if template:
        today = target_day or date.today()
        return template.format(
            month_abbr=get_month_abbr(today.month),
            year=today.year,
            year_2digit=f"{today.year % 100:02d}",
            month=today.month,
            month_2digit=f"{today.month:02d}",
        )

    return cliente.get("daily_worksheet_name") or cliente.get("worksheet_name")


def get_monthly_sheet_name(cliente, target_day=None):
    target_day = target_day or date.today()
    template = cliente.get("monthly_worksheet_name_template")

    if template:
        return template.format(
            year=target_day.year,
            year_2digit=f"{target_day.year % 100:02d}",
        )

    return cliente.get("monthly_worksheet_name")


def get_monthly_start_month(cliente, target_day=None):
    target_day = target_day or date.today()
    first_start_month = cliente.get("monthly_start_month", f"{target_day.year}-01")
    first_year, _ = parse_month_key(first_start_month)

    if target_day.year <= first_year:
        return first_start_month

    return f"{target_day.year}-01"


def add_months(day, month_delta):
    month_index = day.month - 1 + month_delta
    year = day.year + month_index // 12
    month = month_index % 12 + 1
    last_day = monthrange(year, month)[1]
    return day.replace(year=year, month=month, day=min(day.day, last_day))


def get_previous_month_day(day):
    return add_months(day.replace(day=1), -1)


def parse_daily_google_ads_sheet_month(sheet_name):
    normalized = normalize_sheet_text(sheet_name)
    match = re.fullmatch(
        (
            r"(?:consumos)?"
            r"(ene|enero|feb|febrero|mar|marzo|abr|abril|may|mayo|"
            r"jun|junio|jul|julio|ago|agosto|sep|septiembre|"
            r"oct|octubre|nov|noviembre|dic|diciembre)"
            r"(\d{2}|\d{4})gads"
        ),
        normalized,
    )

    if not match:
        return None

    month_aliases = {
        "ene": 1,
        "enero": 1,
        "feb": 2,
        "febrero": 2,
        "mar": 3,
        "marzo": 3,
        "abr": 4,
        "abril": 4,
        "may": 5,
        "mayo": 5,
        "jun": 6,
        "junio": 6,
        "jul": 7,
        "julio": 7,
        "ago": 8,
        "agosto": 8,
        "sep": 9,
        "septiembre": 9,
        "oct": 10,
        "octubre": 10,
        "nov": 11,
        "noviembre": 11,
        "dic": 12,
        "diciembre": 12,
    }
    raw_year = int(match.group(2))
    year = raw_year if raw_year >= 1000 else 2000 + raw_year
    return date(year, month_aliases[match.group(1)], 1)


def get_month_delta(start_day, end_day):
    return (end_day.year - start_day.year) * 12 + end_day.month - start_day.month


def get_current_month_labels():
    today = date.today()
    year = today.year
    month = today.month

    return [
        f"{year}|{month:02d}",
        f"{year}/{month:02d}",
        f"{year}-{month:02d}",
        f"{year}-{month:02d}-01",
        f"{year}{month:02d}",
    ]


def get_month_header_labels(month_key):
    year, month = parse_month_key(month_key)

    return [
        f"{year}|{month:02d}",
        f"{year}/{month:02d}",
        f"{year}-{month:02d}",
        f"{year}-{month:02d}-01",
        f"{year}{month:02d}",
    ]


def get_month_name_labels(month_key):
    _, month = parse_month_key(month_key)
    month_names = {
        1: ["enero", "ene", "jan", "january"],
        2: ["febrero", "feb", "february"],
        3: ["marzo", "mar", "march"],
        4: ["abril", "abr", "apr", "april"],
        5: ["mayo", "may"],
        6: ["junio", "jun", "june"],
        7: ["julio", "jul", "july"],
        8: ["agosto", "ago", "aug", "august"],
        9: ["septiembre", "setiembre", "sep", "september"],
        10: ["octubre", "oct", "october"],
        11: ["noviembre", "nov", "november"],
        12: ["diciembre", "dic", "dec", "december"],
    }
    return month_names[month]


def get_historical_backfill_period(backfill_year, today):
    if backfill_year > today.year:
        raise ValueError("No se puede rellenar un ano futuro.")

    start_day = date(backfill_year, 1, 1)
    end_day = (
        today
        if backfill_year == today.year
        else date(backfill_year, 12, 31)
    )
    month_keys = get_month_keys_between(
        f"{backfill_year}-01",
        end_day,
    )
    return start_day, end_day, month_keys
