"""Consumption: google ads."""

from .aggregation import (
    build_monthly_costs,
)

from concurrent.futures import ThreadPoolExecutor
from concurrent.futures import as_completed
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
import os
import re
import time
from .runtime import (
    ACCOUNT_METADATA,
    FAILED_ACCOUNT_QUERIES,
    client_id,
    client_secret,
    refresh_token,
)
from .constants import (
    MAX_GOOGLE_ADS_RETRIES,
    RETRY_DELAY_SECONDS,
)

from .text import (
    normalize_customer_id,
)


def get_developer_token_for_client(cliente):
    developer_token_env = cliente.get(
        "developer_token_env",
        "GOOGLE_ADS_DEVELOPER_TOKEN"
    )
    token = os.getenv(developer_token_env)

    if not token:
        raise SystemExit(
            f"Falta la variable {developer_token_env} para {cliente['nombre']}"
        )

    return token


def get_manager_sources(cliente):
    configured_sources = cliente.get("manager_sources")

    if not configured_sources:
        configured_sources = [{
            "nombre": cliente.get("nombre", ""),
            "mcc_id": cliente.get("mcc_id", ""),
            "sub_mcc_ids": cliente.get("sub_mcc_ids", []),
        }]

    sources = []
    seen_mcc_ids = set()

    for source in configured_sources:
        mcc_id = normalize_customer_id(source.get("mcc_id", ""))

        if not mcc_id:
            raise ValueError(
                f"Fuente MCC sin mcc_id valido para {cliente.get('nombre', '')}"
            )

        if mcc_id in seen_mcc_ids:
            continue

        sources.append({
            "nombre": source.get("nombre") or mcc_id,
            "mcc_id": mcc_id,
            "sub_mcc_ids": [
                normalize_customer_id(item)
                for item in source.get("sub_mcc_ids", [])
                if normalize_customer_id(item)
            ],
        })
        seen_mcc_ids.add(mcc_id)

    return sources


def create_google_ads_client(cliente, login_customer_id=None):
    google_ads_config = {
        "developer_token": get_developer_token_for_client(cliente),
        "client_id": client_id,
        "client_secret": client_secret,
        "refresh_token": refresh_token,
        "login_customer_id": (
            login_customer_id
            or cliente["mcc_id"]
        ),
        "use_proto_plus": True,
    }
    return GoogleAdsClient.load_from_dict(google_ads_config)


def merge_unique_daily_account_results(groups):
    merged = []
    seen_ids = set()
    duplicate_ids = set()

    for accounts in groups:
        for account in accounts:
            account_id = normalize_customer_id(account.get("id", ""))

            if not account_id or account_id in seen_ids:
                if account_id:
                    duplicate_ids.add(account_id)
                continue

            merged.append(account)
            seen_ids.add(account_id)

    return merged, duplicate_ids


def is_retryable_error(error):
    error_text = str(error).lower()
    return (
        "503" in error_text
        or "500" in error_text
        or "429" in error_text
        or "unavailable" in error_text
        or "backend unavailable" in error_text
        or "internal error" in error_text
        or "resource has been exhausted" in error_text
        or "too many requests" in error_text
    )


def is_skippable_account_error(error):
    """
    Algunas cuentas canceladas/deactivadas aparecen en customer_client pero
    Google Ads ya no permite consultarlas. Las omitimos sin borrar sus filas
    historicas y sin hacer fallar toda la automatizacion.
    """
    error_text = str(error).lower()
    return (
        "not yet enabled or has been deactivated" in error_text
        or "customer_not_enabled" in error_text
    )


def get_retry_delay_seconds(error, attempt):
    error_text = str(error).lower()
    retry_match = re.search(r"retry in (\d+) seconds", error_text)

    if retry_match:
        return int(retry_match.group(1))

    return RETRY_DELAY_SECONDS * attempt


def get_account_daily_spend_with_retries(
    google_ads_client,
    account,
    start_date,
    end_date,
    context
):
    account_id = account["id"]

    for attempt in range(1, MAX_GOOGLE_ADS_RETRIES + 1):
        try:
            return get_account_daily_spend(
                google_ads_client,
                account_id,
                start_date,
                end_date
            )
        except GoogleAdsException as ex:
            if is_retryable_error(ex) and attempt < MAX_GOOGLE_ADS_RETRIES:
                retry_delay = get_retry_delay_seconds(ex, attempt)
                print(
                    f"  Error temporal consultando {account_id} "
                    f"({context}). Reintento {attempt + 1}/{MAX_GOOGLE_ADS_RETRIES} "
                    f"en {retry_delay} segundos..."
                )
                time.sleep(retry_delay)
                continue

            if is_skippable_account_error(ex):
                FAILED_ACCOUNT_QUERIES.append({
                    "context": context,
                    "id": account_id,
                    "name": account.get("name", ""),
                    "error": "Cuenta no consultable: no habilitada o desactivada",
                    "fatal": False,
                })
                print(
                    f"  Omito cuenta no consultable {account_id} "
                    f"({account.get('name', '')})."
                )
                return None, {}, 0

            FAILED_ACCOUNT_QUERIES.append({
                "context": context,
                "id": account_id,
                "name": account.get("name", ""),
                "error": f"GoogleAdsException Request ID: {ex.request_id}",
                "fatal": True,
            })
            print(f"  No se pudo consultar la cuenta {account_id}. Request ID: {ex.request_id}")
            return None, {}, 0
        except Exception as ex:
            if is_retryable_error(ex) and attempt < MAX_GOOGLE_ADS_RETRIES:
                retry_delay = get_retry_delay_seconds(ex, attempt)
                print(
                    f"  Error temporal consultando {account_id} "
                    f"({context}). Reintento {attempt + 1}/{MAX_GOOGLE_ADS_RETRIES} "
                    f"en {retry_delay} segundos..."
                )
                time.sleep(retry_delay)
                continue

            FAILED_ACCOUNT_QUERIES.append({
                "context": context,
                "id": account_id,
                "name": account.get("name", ""),
                "error": str(ex),
                "fatal": True,
            })
            print(f"  Error consultando cuenta {account_id}: {ex}")
            return None, {}, 0


def get_child_accounts(google_ads_client, manager_customer_id):
    """
    Devuelve todas las cuentas finales visibles dentro de un MCC/sub-MCC.
    Importante: NO filtramos solo ENABLED, porque hay cuentas suspendidas
    que sí tuvieron gasto en el mes y deben aparecer en el histórico.
    """
    google_ads_service = google_ads_client.get_service("GoogleAdsService")

    # No filtramos por status ni por hidden. Primero traemos todas las
    # cuentas finales que devuelve el sub-MCC y despues solo descartamos
    # las que no tengan coste en el periodo consultado.
    query = """
        SELECT
          customer_client.id,
          customer_client.descriptive_name,
          customer_client.manager,
          customer_client.status,
          customer_client.hidden,
          customer_client.level
        FROM customer_client
        WHERE
          customer_client.manager = FALSE
        ORDER BY
          customer_client.level,
          customer_client.descriptive_name
    """

    for attempt in range(1, MAX_GOOGLE_ADS_RETRIES + 1):
        try:
            response = google_ads_service.search_stream(
                customer_id=manager_customer_id,
                query=query
            )

            accounts = []

            for batch in response:
                for row in batch.results:
                    accounts.append({
                        "id": str(row.customer_client.id),
                        "name": row.customer_client.descriptive_name,
                        "status": row.customer_client.status.name,
                        "hidden": bool(row.customer_client.hidden),
                        "level": int(row.customer_client.level),
                    })

            return accounts
        except (GoogleAdsException, Exception) as ex:
            if is_retryable_error(ex) and attempt < MAX_GOOGLE_ADS_RETRIES:
                retry_delay = get_retry_delay_seconds(ex, attempt)
                print(
                    f"  Error temporal listando cuentas de {manager_customer_id}. "
                    f"Reintento {attempt + 1}/{MAX_GOOGLE_ADS_RETRIES} "
                    f"en {retry_delay} segundos..."
                )
                time.sleep(retry_delay)
                continue

            raise


def get_account_daily_spend(google_ads_client, customer_id, start_date, end_date):
    google_ads_service = google_ads_client.get_service("GoogleAdsService")

    query = f"""
        SELECT
          customer.id,
          customer.descriptive_name,
          segments.date,
          metrics.cost_micros
        FROM customer
        WHERE
          segments.date BETWEEN '{start_date}' AND '{end_date}'
        ORDER BY
          segments.date
    """

    response = google_ads_service.search_stream(
        customer_id=customer_id,
        query=query
    )

    account_name = None
    daily_costs = {}

    for batch in response:
        for row in batch.results:
            account_name = row.customer.descriptive_name
            day = str(row.segments.date)
            cost = row.metrics.cost_micros / 1_000_000

            if cost > 0:
                daily_costs[day] = round(daily_costs.get(day, 0) + cost, 2)

    total_cost = round(sum(daily_costs.values()), 2)

    return account_name, daily_costs, total_cost


def build_spend_result_for_account(
    google_ads_client,
    account,
    start_date,
    end_date,
    context
):
    account_id = account["id"]

    account_name, daily_costs, total_cost = get_account_daily_spend_with_retries(
        google_ads_client,
        account,
        start_date,
        end_date,
        context
    )

    if total_cost <= 0:
        return None

    return {
        "name": account_name or account["name"],
        "id": account_id,
        "status": account["status"],
        "hidden": account.get("hidden", False),
        "level": account.get("level"),
        "daily_costs": daily_costs,
        "total_cost": total_cost,
    }


def get_accounts_with_daily_spend_for_manager(
    google_ads_client,
    manager_customer_id,
    start_date,
    end_date,
    context="hoja diaria",
    parallel_workers=1
):
    child_accounts = get_child_accounts(google_ads_client, manager_customer_id)

    for account in child_accounts:
        account_id = normalize_customer_id(account.get("id", ""))

        if account_id:
            ACCOUNT_METADATA[account_id] = {
                "name": account.get("name", ""),
                "status": account.get("status", ""),
                "hidden": account.get("hidden", False),
            }

    print(f"  Cuentas finales encontradas en {manager_customer_id}: {len(child_accounts)}")

    enabled_count = sum(1 for account in child_accounts if account["status"] == "ENABLED")
    suspended_count = sum(1 for account in child_accounts if account["status"] == "SUSPENDED")
    canceled_count = sum(1 for account in child_accounts if account["status"] == "CANCELED")
    hidden_count = sum(1 for account in child_accounts if account.get("hidden") is True)

    print(f"  ENABLED: {enabled_count}")
    print(f"  SUSPENDED: {suspended_count}")
    print(f"  CANCELED: {canceled_count}")
    print(f"  HIDDEN: {hidden_count}")

    if not child_accounts:
        return []

    workers = max(1, int(parallel_workers or 1))
    workers = min(workers, len(child_accounts))

    if workers == 1:
        results = []

        for index, account in enumerate(child_accounts):
            result = build_spend_result_for_account(
                google_ads_client,
                account,
                start_date,
                end_date,
                context
            )

            if result:
                results.append((index, result))

        return [result for _, result in results]

    print(f"  Consultas de gasto en paralelo: {workers} hilos")

    results = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        future_to_index = {
            executor.submit(
                build_spend_result_for_account,
                google_ads_client,
                account,
                start_date,
                end_date,
                context
            ): index
            for index, account in enumerate(child_accounts)
        }

        for future in as_completed(future_to_index):
            index = future_to_index[future]
            result = future.result()

            if result:
                results.append((index, result))

    results.sort(key=lambda item: item[0])
    return [result for _, result in results]


def get_accounts_with_daily_spend(
    google_ads_client,
    cliente,
    start_date,
    end_date,
    context="hoja diaria",
    parallel_workers=1
):
    mcc_id = cliente["mcc_id"]
    sub_mcc_ids = cliente.get("sub_mcc_ids", [])

    all_results = []
    seen_ids = set()

    if sub_mcc_ids:
        print("Sub-MCCs configurados. Solo se consultarán estos:")
        for sub_mcc_id in sub_mcc_ids:
            print(f"  - {sub_mcc_id}")

        for sub_mcc_id in sub_mcc_ids:
            print(f"\nConsultando sub-MCC {sub_mcc_id}...")
            sub_results = get_accounts_with_daily_spend_for_manager(
                google_ads_client,
                sub_mcc_id,
                start_date,
                end_date,
                context,
                parallel_workers
            )

            for item in sub_results:
                account_id = normalize_customer_id(item["id"])

                if account_id not in seen_ids:
                    all_results.append(item)
                    seen_ids.add(account_id)

    else:
        print("No hay sub-MCCs configurados. Se consultará el MCC principal completo.")
        all_results = get_accounts_with_daily_spend_for_manager(
            google_ads_client,
            mcc_id,
            start_date,
            end_date,
            context,
            parallel_workers
        )

    return all_results


def fetch_client_daily_spend(
    cliente,
    start_date,
    end_date,
    context,
    parallel_workers,
):
    # A missing account must not inherit discovery from a previous workbook.
    ACCOUNT_METADATA.clear()
    manager_sources = get_manager_sources(cliente)
    source_result_groups = []

    print("Fuentes MCC configuradas:")
    for source in manager_sources:
        print(f"  - {source['nombre']}: {source['mcc_id']}")

    for source in manager_sources:
        print(
            f"\nConsultando fuente MCC {source['nombre']} "
            f"({source['mcc_id']})..."
        )
        google_ads_client = create_google_ads_client(
            cliente,
            login_customer_id=source["mcc_id"],
        )
        source_client = dict(cliente)
        source_client["mcc_id"] = source["mcc_id"]
        source_client["sub_mcc_ids"] = source["sub_mcc_ids"]

        source_result_groups.append(get_accounts_with_daily_spend(
            google_ads_client,
            source_client,
            start_date,
            end_date,
            context,
            parallel_workers,
        ))

    accounts, duplicate_ids = merge_unique_daily_account_results(
        source_result_groups
    )

    if duplicate_ids:
        print(
            f"Cuentas visibles desde varias fuentes MCC y deduplicadas: "
            f"{len(duplicate_ids)}"
        )

    return accounts


def get_accounts_with_monthly_spend_for_manager(
    google_ads_client,
    manager_customer_id,
    start_date,
    end_date,
    month_keys
):
    child_accounts = get_child_accounts(google_ads_client, manager_customer_id)
    results = []

    print(f"  Cuentas finales encontradas en {manager_customer_id}: {len(child_accounts)}")

    for account in child_accounts:
        account_id = account["id"]

        account_name, daily_costs, total_cost = get_account_daily_spend_with_retries(
            google_ads_client,
            account,
            start_date,
            end_date,
            "hoja historica mensual"
        )

        if total_cost > 0:
            monthly_costs = build_monthly_costs(daily_costs, month_keys)

            results.append({
                "name": account_name or account["name"],
                "id": account_id,
                "status": account["status"],
                "monthly_costs": monthly_costs,
                "total_cost": round(sum(monthly_costs.values()), 2),
            })

    return results


def get_accounts_with_monthly_spend(
    google_ads_client,
    cliente,
    start_date,
    end_date,
    month_keys
):
    mcc_id = cliente["mcc_id"]
    sub_mcc_ids = cliente.get("sub_mcc_ids", [])

    all_results = []
    seen_ids = set()

    if sub_mcc_ids:
        print("Sub-MCCs configurados para historico mensual. Solo se consultaran estos:")
        for sub_mcc_id in sub_mcc_ids:
            print(f"  - {sub_mcc_id}")

        for sub_mcc_id in sub_mcc_ids:
            print(f"\nConsultando historico mensual en sub-MCC {sub_mcc_id}...")
            sub_results = get_accounts_with_monthly_spend_for_manager(
                google_ads_client,
                sub_mcc_id,
                start_date,
                end_date,
                month_keys
            )

            for item in sub_results:
                account_id = normalize_customer_id(item["id"])

                if account_id not in seen_ids:
                    all_results.append(item)
                    seen_ids.add(account_id)

    else:
        print("No hay sub-MCCs configurados. Se consultara el MCC principal completo.")
        all_results = get_accounts_with_monthly_spend_for_manager(
            google_ads_client,
            mcc_id,
            start_date,
            end_date,
            month_keys
        )

    return all_results
