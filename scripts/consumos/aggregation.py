"""Consumption: aggregation."""

from .periods import (
    get_month_key_from_day,
)

from .text import (
    normalize_customer_id,
)


def build_monthly_costs(daily_costs, month_keys):
    monthly_costs = {month_key: 0 for month_key in month_keys}

    for day, cost in daily_costs.items():
        month_key = get_month_key_from_day(day)

        if month_key in monthly_costs:
            monthly_costs[month_key] = round(monthly_costs[month_key] + cost, 2)

    return {
        month_key: cost
        for month_key, cost in monthly_costs.items()
        if cost > 0
    }


def filter_daily_costs_by_period(daily_costs, start_date, end_date):
    return {
        day: cost
        for day, cost in daily_costs.items()
        if start_date <= day <= end_date
    }


def build_accounts_for_daily_sheet(accounts_with_period_spend, start_date, end_date):
    """
    Prepara la hoja diaria desde la lectura unica de Google Ads.
    Asi la hoja diaria y la mensual parten de la misma foto de datos.
    """
    results = []

    for account in accounts_with_period_spend:
        daily_costs = filter_daily_costs_by_period(
            account["daily_costs"],
            start_date,
            end_date
        )
        total_cost = round(sum(daily_costs.values()), 2)

        if total_cost <= 0:
            continue

        results.append({
            "name": account["name"],
            "id": account["id"],
            "status": account.get("status", ""),
            "hidden": account.get("hidden", False),
            "level": account.get("level"),
            "daily_costs": daily_costs,
            "total_cost": total_cost,
        })

    return results


def build_accounts_for_monthly_sheet(accounts_with_period_spend, month_keys):
    """
    Prepara la hoja mensual desde la misma lectura usada por la hoja diaria.
    Evita que Google Ads devuelva centimos distintos entre dos consultas.
    """
    results = []

    for account in accounts_with_period_spend:
        monthly_costs = build_monthly_costs(account["daily_costs"], month_keys)
        total_cost = round(sum(monthly_costs.values()), 2)

        if total_cost <= 0:
            continue

        results.append({
            "name": account["name"],
            "id": account["id"],
            "status": account.get("status", ""),
            "hidden": account.get("hidden", False),
            "level": account.get("level"),
            "monthly_costs": monthly_costs,
            "total_cost": total_cost,
        })

    return results


def merge_monthly_account_results(accounts):
    merged = {}
    order = []

    for account in accounts:
        account_id = normalize_customer_id(account["id"])

        if not account_id:
            continue

        if account_id not in merged:
            merged[account_id] = {
                "name": account["name"],
                "id": account_id,
                "status": account.get("status", ""),
                "hidden": account.get("hidden", False),
                "monthly_costs": {},
                "total_cost": 0,
            }
            order.append(account_id)

        item = merged[account_id]

        if not item["name"] and account.get("name"):
            item["name"] = account["name"]

        if not item["status"] and account.get("status"):
            item["status"] = account["status"]

        if account.get("hidden"):
            item["hidden"] = True

        for month_key, cost in account.get("monthly_costs", {}).items():
            item["monthly_costs"][month_key] = round(
                item["monthly_costs"].get(month_key, 0) + cost,
                2
            )

    results = []

    for account_id in order:
        item = merged[account_id]
        item["total_cost"] = round(sum(item["monthly_costs"].values()), 2)

        if item["total_cost"] > 0:
            results.append(item)

    return results


def filter_monthly_account_results(accounts, month_keys):
    wanted_month_keys = set(month_keys)
    results = []

    for account in accounts:
        monthly_costs = {
            month_key: cost
            for month_key, cost in account.get("monthly_costs", {}).items()
            if month_key in wanted_month_keys
        }
        total_cost = round(sum(monthly_costs.values()), 2)

        if total_cost <= 0:
            continue

        results.append({
            "name": account["name"],
            "id": account["id"],
            "status": account.get("status", ""),
            "hidden": account.get("hidden", False),
            "monthly_costs": monthly_costs,
            "total_cost": total_cost,
        })

    return results
