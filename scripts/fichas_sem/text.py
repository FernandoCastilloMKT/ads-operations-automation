"""SEM: text."""

import re
import unicodedata


def normalize_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    ascii_text = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    return re.sub(r"[^a-z0-9]+", "", ascii_text.lower())


def normalize_sem_client_selector(value):
    raw_value = str(value or "")
    if not raw_value.strip():
        raise SystemExit("El cliente SEM no puede estar vacio.")
    if any(unicodedata.category(char).startswith("C") for char in raw_value):
        raise SystemExit(
            "El cliente SEM contiene caracteres de control no permitidos."
        )

    normalized = unicodedata.normalize("NFKD", raw_value)
    without_accents = "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    )
    return " ".join(without_accents.casefold().split())


def normalize_customer_id(value):
    return "".join(ch for ch in str(value or "") if ch.isdigit())


def fold_text(value):
    normalized = unicodedata.normalize("NFKD", str(value or ""))
    return "".join(
        char for char in normalized
        if not unicodedata.combining(char)
    ).lower()
