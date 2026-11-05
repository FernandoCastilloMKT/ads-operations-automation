"""Consumption: text."""

import re
import unicodedata


def normalize_match_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


def parse_sheet_number(value):
    if isinstance(value, (int, float)):
        return float(value)

    clean_value = (
        str(value)
        .replace("€", "")
        .replace("â‚¬", "")
        .replace("\xa0", "")
        .replace(" ", "")
        .strip()
    )

    if not clean_value:
        return 0

    if "," in clean_value and "." in clean_value:
        clean_value = clean_value.replace(".", "").replace(",", ".")
    elif "," in clean_value:
        clean_value = clean_value.replace(",", ".")

    try:
        return float(clean_value)
    except ValueError:
        return 0


def normalize_sheet_text(value):
    return normalize_match_text(value)


def normalize_customer_id(value):
    return "".join(ch for ch in str(value) if ch.isdigit())
