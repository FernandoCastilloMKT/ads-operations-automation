"""Consumption: failures."""


from .runtime import (
    FAILED_ACCOUNT_QUERIES,
)

from .text import (
    normalize_customer_id,
)


def print_failed_account_queries():
    if not FAILED_ACCOUNT_QUERIES:
        print("\nResumen consultas Google Ads: sin cuentas fallidas.")
        return

    fatal_items = [
        item for item in FAILED_ACCOUNT_QUERIES
        if item.get("fatal", True)
    ]
    skipped_items = [
        item for item in FAILED_ACCOUNT_QUERIES
        if not item.get("fatal", True)
    ]

    if skipped_items:
        print("\nResumen consultas Google Ads: cuentas omitidas sin borrar historico.")
        for item in skipped_items:
            print(
                f"  {item['context']} | {item['id']} | "
                f"{item['name']} | {item['error']}"
            )

    if not fatal_items:
        return

    print("\nResumen consultas Google Ads: cuentas fallidas tras reintentos.")
    for item in fatal_items:
        print(
            f"  {item['context']} | {item['id']} | "
            f"{item['name']} | {item['error']}"
        )


def get_failed_account_ids(context=None):
    return {
        normalize_customer_id(item["id"])
        for item in FAILED_ACCOUNT_QUERIES
        if context is None or item["context"] == context
    }
