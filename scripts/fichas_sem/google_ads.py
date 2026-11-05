"""SEM: google ads."""

from .account_status import (
    classify_account_operational_status,
    customer_status_display_label,
)

from .metrics import (
    format_number,
    format_share,
    sort_campaign_rows,
)

from .periods import (
    today_in_spain,
)

from datetime import date
from google.ads.googleads.client import GoogleAdsClient
from google.ads.googleads.errors import GoogleAdsException
import os
import re
import time
from .constants import (
    MAX_GOOGLE_ADS_RETRIES,
    RETRY_DELAY_SECONDS,
)
from .runtime import (
    _google_ads_thread_local,
)

from .text import (
    normalize_customer_id,
    normalize_text,
)


def get_developer_token_for_client(cliente):
    developer_token_env = cliente.get(
        "developer_token_env",
        "GOOGLE_ADS_DEVELOPER_TOKEN",
    )
    token = os.getenv(developer_token_env)

    if not token:
        raise SystemExit(
            f"Falta la variable {developer_token_env} para {cliente['nombre']}"
        )

    return token


def create_google_ads_client(cliente):
    required = {
        "client_id": os.getenv("GOOGLE_ADS_CLIENT_ID"),
        "client_secret": os.getenv("GOOGLE_ADS_CLIENT_SECRET"),
        "refresh_token": os.getenv("GOOGLE_ADS_REFRESH_TOKEN"),
    }
    missing = [name for name, value in required.items() if not value]

    if missing:
        raise SystemExit(
            "Faltan variables OAuth de Google Ads: " + ", ".join(missing)
        )

    google_ads_config = {
        "developer_token": get_developer_token_for_client(cliente),
        "client_id": required["client_id"],
        "client_secret": required["client_secret"],
        "refresh_token": required["refresh_token"],
        "login_customer_id": normalize_customer_id(cliente["mcc_id"]),
        "use_proto_plus": True,
    }
    return GoogleAdsClient.load_from_dict(google_ads_config)


def get_thread_google_ads_client(cliente):
    clients = getattr(_google_ads_thread_local, "clients", None)
    if clients is None:
        clients = {}
        _google_ads_thread_local.clients = clients

    mcc_id = normalize_customer_id(cliente["mcc_id"])
    if mcc_id not in clients:
        clients[mcc_id] = create_google_ads_client(cliente)
    return clients[mcc_id]


def is_retryable_google_ads_error(error):
    error_text = str(error).lower()
    return any(
        marker in error_text
        for marker in (
            "429",
            "500",
            "502",
            "503",
            "504",
            "unavailable",
            "backend unavailable",
            "deadline exceeded",
            "internal error",
            "resource has been exhausted",
            "resource_exhausted",
            "too many requests",
        )
    )


def get_google_ads_retry_delay(error, attempt):
    retry_match = re.search(r"retry in (\d+) seconds", str(error).lower())
    if retry_match:
        return int(retry_match.group(1))
    return RETRY_DELAY_SECONDS * attempt


def print_google_ads_error(error, context):
    print(f"Error de Google Ads {context}.")
    if isinstance(error, GoogleAdsException):
        print(f"Request ID: {error.request_id}")
        for failure_error in error.failure.errors:
            print(f"  {failure_error.message}")
    else:
        print(f"  {type(error).__name__}: {error}")


def run_google_ads_read_with_retries(operation, context):
    for attempt in range(1, MAX_GOOGLE_ADS_RETRIES + 1):
        try:
            return operation()
        except Exception as error:
            can_retry = (
                is_retryable_google_ads_error(error)
                and attempt < MAX_GOOGLE_ADS_RETRIES
            )
            if not can_retry:
                print_google_ads_error(error, context)
                raise

            retry_delay = get_google_ads_retry_delay(error, attempt)
            print(
                f"Error temporal de Google Ads {context}. "
                f"Reintento {attempt + 1}/{MAX_GOOGLE_ADS_RETRIES} "
                f"en {retry_delay} segundos..."
            )
            time.sleep(retry_delay)

    raise RuntimeError("La operacion de Google Ads termino sin resultado.")


def fetch_customer_status(google_ads_client, customer_id):
    google_ads_service = google_ads_client.get_service("GoogleAdsService")
    query = """
        SELECT
          customer.status
        FROM customer
        LIMIT 1
    """

    def read_status():
        response = google_ads_service.search_stream(
            customer_id=normalize_customer_id(customer_id),
            query=query,
        )
        for batch in response:
            for row in batch.results:
                return row.customer.status.name.lower()
        return "unknown"

    return run_google_ads_read_with_retries(
        read_status,
        "consultando el estado de la cuenta",
    )


def fetch_account_operational_status(
    google_ads_client,
    customer_id,
    customer_status=None,
    operational_day=None,
):
    customer_status = (
        customer_status
        or fetch_customer_status(google_ads_client, customer_id)
    )
    operational_day = operational_day or today_in_spain()
    if normalize_text(customer_status) != "enabled":
        return customer_status_display_label(customer_status)

    google_ads_service = google_ads_client.get_service("GoogleAdsService")
    query = """
        SELECT
          campaign.id,
          campaign.status,
          campaign.primary_status,
          campaign.end_date_time
        FROM campaign
    """

    def read_campaign_states():
        campaigns = []
        response = google_ads_service.search_stream(
            customer_id=normalize_customer_id(customer_id),
            query=query,
        )
        for batch in response:
            for row in batch.results:
                campaigns.append({
                    "campaign_id": str(row.campaign.id),
                    "status": row.campaign.status.name.lower(),
                    "primary_status": (
                        row.campaign.primary_status.name.lower()
                    ),
                    "end_date_time": str(
                        row.campaign.end_date_time or ""
                    ),
                })
        return campaigns

    campaigns = run_google_ads_read_with_retries(
        read_campaign_states,
        "consultando el estado operativo de las campanas",
    )
    return classify_account_operational_status(
        customer_status,
        campaigns,
        operational_day,
    )


def build_campaign_rows_from_daily_aggregates(campaigns):
    rows = []

    for campaign_id, campaign in campaigns.items():
        clicks = campaign["clicks"]
        impressions = campaign["impressions"]
        cost = campaign["cost_micros"] / 1_000_000
        conversions = campaign["conversions"]
        average_cpc = cost / clicks if clicks else 0
        ctr = (clicks / impressions * 100) if impressions else 0
        cost_per_conversion = cost / conversions if conversions else ""
        top_share = (
            campaign["top_share_weighted"] / campaign["top_share_weight"]
            if campaign["top_share_weight"]
            else None
        )
        absolute_top_share = (
            campaign["absolute_top_share_weighted"]
            / campaign["absolute_top_share_weight"]
            if campaign["absolute_top_share_weight"]
            else None
        )
        has_activity = any([
            clicks,
            impressions,
            cost,
            conversions,
        ])

        if not has_activity:
            continue

        rows.append({
            "campaign_id": campaign_id,
            "campaign_name": campaign["campaign_name"],
            "campaign_status": campaign["campaign_status"],
            "clicks": int(clicks),
            "ctr": format_number(ctr, 2),
            "average_cpc": format_number(average_cpc, 2),
            "cost": round(cost, 6),
            "conversions": format_number(conversions, 2),
            "cost_per_conversion": (
                format_number(cost_per_conversion, 2)
                if cost_per_conversion != ""
                else ""
            ),
            "impressions": int(impressions),
            "top_impression_share": format_share(top_share),
            "absolute_top_impression_share": format_share(
                absolute_top_share
            ),
            "daily_budget": format_number(
                campaign["daily_budget_micros"] / 1_000_000,
                2,
            ),
            "raw_cost": cost,
        })

    return sort_campaign_rows(rows)


def apply_exact_impression_shares(period_rows, exact_shares):
    for period_key, rows in period_rows.items():
        shares_by_campaign = exact_shares.get(period_key, {})
        for row in rows:
            shares = shares_by_campaign.get(row["campaign_id"])
            if shares is None:
                continue
            row["top_impression_share"] = format_share(shares["top"])
            row["absolute_top_impression_share"] = format_share(
                shares["absolute_top"]
            )


def fetch_exact_impression_shares(
    google_ads_service,
    customer_id,
    period_ranges,
):
    exact_shares = {}

    for period_key, (start_day, end_day) in period_ranges.items():
        query = f"""
            SELECT
              campaign.id,
              metrics.search_top_impression_share,
              metrics.search_absolute_top_impression_share
            FROM campaign
            WHERE
              segments.date BETWEEN '{start_day.isoformat()}' AND '{end_day.isoformat()}'
        """

        def read_shares():
            shares = {}
            response = google_ads_service.search_stream(
                customer_id=normalize_customer_id(customer_id),
                query=query,
            )
            for batch in response:
                for row in batch.results:
                    shares[str(row.campaign.id)] = {
                        "top": float(
                            row.metrics.search_top_impression_share or 0
                        ),
                        "absolute_top": float(
                            row.metrics.search_absolute_top_impression_share
                            or 0
                        ),
                    }
            return shares

        exact_shares[period_key] = run_google_ads_read_with_retries(
            read_shares,
            (
                "consultando porcentajes de impresion exactos del periodo "
                f"{start_day.isoformat()} a {end_day.isoformat()}"
            ),
        )

    return exact_shares


def fetch_campaign_periods(
    google_ads_client,
    customer_id,
    period_contexts,
    operational_day=None,
):
    if not period_contexts:
        technical_status = fetch_customer_status(
            google_ads_client,
            customer_id,
        )
        return {
            "account_status": fetch_account_operational_status(
                google_ads_client,
                customer_id,
                technical_status,
                operational_day,
            ),
            "technical_account_status": technical_status,
            "period_rows": {},
        }

    period_ranges = {
        key: (
            context["query_start_day"],
            context["query_end_day"],
        )
        for key, context in period_contexts.items()
    }
    query_start_day = min(start for start, _ in period_ranges.values())
    query_end_day = max(end for _, end in period_ranges.values())
    google_ads_service = google_ads_client.get_service("GoogleAdsService")
    query = f"""
        SELECT
          customer.status,
          segments.date,
          campaign.id,
          campaign.name,
          campaign.status,
          metrics.clicks,
          metrics.cost_micros,
          metrics.conversions,
          metrics.impressions,
          metrics.search_top_impression_share,
          metrics.search_absolute_top_impression_share,
          campaign_budget.amount_micros
        FROM campaign
        WHERE
          segments.date BETWEEN '{query_start_day.isoformat()}' AND '{query_end_day.isoformat()}'
        ORDER BY
          segments.date,
          campaign.id
    """

    def read_campaigns():
        account_status = None
        aggregates = {key: {} for key in period_contexts}
        response = google_ads_service.search_stream(
            customer_id=normalize_customer_id(customer_id),
            query=query,
        )

        for batch in response:
            for row in batch.results:
                account_status = row.customer.status.name.lower()
                row_day = date.fromisoformat(str(row.segments.date))
                period_key = next(
                    (
                        key
                        for key, (start_day, end_day) in period_ranges.items()
                        if start_day <= row_day <= end_day
                    ),
                    None,
                )
                if period_key is None:
                    continue

                campaign_id = str(row.campaign.id)
                campaigns = aggregates[period_key]
                campaign = campaigns.setdefault(campaign_id, {
                    "campaign_name": row.campaign.name,
                    "campaign_status": row.campaign.status.name.lower(),
                    "latest_day": row_day,
                    "clicks": 0,
                    "cost_micros": 0,
                    "conversions": 0.0,
                    "impressions": 0,
                    "daily_budget_micros": 0,
                    "top_share_weighted": 0.0,
                    "top_share_weight": 0,
                    "absolute_top_share_weighted": 0.0,
                    "absolute_top_share_weight": 0,
                })

                if row_day >= campaign["latest_day"]:
                    campaign["latest_day"] = row_day
                    campaign["campaign_name"] = row.campaign.name
                    campaign["campaign_status"] = (
                        row.campaign.status.name.lower()
                    )
                    campaign["daily_budget_micros"] = int(
                        row.campaign_budget.amount_micros or 0
                    )

                impressions = int(row.metrics.impressions or 0)
                campaign["clicks"] += int(row.metrics.clicks or 0)
                campaign["cost_micros"] += int(
                    row.metrics.cost_micros or 0
                )
                campaign["conversions"] += float(
                    row.metrics.conversions or 0
                )
                campaign["impressions"] += impressions

                top_share = float(
                    row.metrics.search_top_impression_share or 0
                )
                if top_share and impressions:
                    campaign["top_share_weighted"] += (
                        top_share * impressions
                    )
                    campaign["top_share_weight"] += impressions

                absolute_top_share = float(
                    row.metrics.search_absolute_top_impression_share or 0
                )
                if absolute_top_share and impressions:
                    campaign["absolute_top_share_weighted"] += (
                        absolute_top_share * impressions
                    )
                    campaign["absolute_top_share_weight"] += impressions

        return {
            "account_status": account_status,
            "period_rows": {
                key: build_campaign_rows_from_daily_aggregates(campaigns)
                for key, campaigns in aggregates.items()
            },
        }

    result = run_google_ads_read_with_retries(
        read_campaigns,
        (
            "consultando campanas agrupadas del periodo "
            f"{query_start_day.isoformat()} a {query_end_day.isoformat()}"
        ),
    )
    if not result["account_status"]:
        result["account_status"] = fetch_customer_status(
            google_ads_client,
            customer_id,
        )
    technical_status = result["account_status"]
    result["technical_account_status"] = technical_status
    result["account_status"] = fetch_account_operational_status(
        google_ads_client,
        customer_id,
        technical_status,
        operational_day,
    )
    exact_shares = fetch_exact_impression_shares(
        google_ads_service,
        customer_id,
        period_ranges,
    )
    apply_exact_impression_shares(result["period_rows"], exact_shares)
    return result


def fetch_campaign_rows(google_ads_client, customer_id, start_day, end_day):
    context = {
        "query_start_day": start_day,
        "query_end_day": end_day,
    }
    return fetch_campaign_periods(
        google_ads_client,
        customer_id,
        {"single": context},
    )["period_rows"]["single"]


def fetch_annual_control_annual_rows(
    google_ads_client,
    customer_id,
    period,
):
    google_ads_service = google_ads_client.get_service("GoogleAdsService")
    query = f"""
        SELECT
            campaign.id,
            campaign.name,
            campaign.status,
            campaign.start_date_time,
            campaign.end_date_time,
            campaign_budget.amount_micros,
            campaign_budget.total_amount_micros,
            metrics.clicks,
            metrics.impressions,
            metrics.ctr,
            metrics.average_cpc,
            metrics.average_cpm,
            metrics.cost_micros,
            metrics.conversions
        FROM campaign
        WHERE segments.date BETWEEN '{period['start_day'].isoformat()}'
            AND '{period['query_end_day'].isoformat()}'
        ORDER BY campaign.start_date_time, campaign.end_date_time, campaign.name
    """

    def read_campaigns():
        campaigns = {}
        response = google_ads_service.search_stream(
            customer_id=customer_id,
            query=query,
        )
        for batch in response:
            for result in batch.results:
                campaign = result.campaign
                budget = result.campaign_budget
                metrics = result.metrics
                start_text = campaign.start_date_time or ""
                end_text = campaign.end_date_time or ""
                start_day = (
                    date.fromisoformat(start_text[:10])
                    if len(start_text) >= 10
                    else None
                )
                has_activity = any((
                    metrics.clicks,
                    metrics.impressions,
                    metrics.cost_micros,
                    metrics.conversions,
                ))
                starts_in_period = bool(
                    start_day
                    and period["start_day"] <= start_day <= period["end_day"]
                )
                if not has_activity and not starts_in_period:
                    continue

                campaigns[str(campaign.id)] = {
                    "campaign_id": str(campaign.id),
                    "campaign_name": campaign.name,
                    "location": "",
                    "campaign_status": campaign.status.name.lower(),
                    "start_date": start_text[:10] if start_text else "",
                    "end_date": end_text[:10] if end_text else "",
                    "clicks": int(metrics.clicks),
                    "impressions": int(metrics.impressions),
                    "ctr": round(float(metrics.ctr) * 100, 6),
                    "cost": metrics.cost_micros / 1_000_000,
                    "average_cpc": metrics.average_cpc / 1_000_000,
                    "average_cpm": metrics.average_cpm / 1_000_000,
                    "daily_budget": budget.amount_micros / 1_000_000,
                    "total_budget": budget.total_amount_micros / 1_000_000,
                }
        return campaigns

    campaigns = run_google_ads_read_with_retries(
        read_campaigns,
        f"consultando la ficha anual {period['year']}",
    )
    if not campaigns:
        return []

    campaign_ids = ",".join(sorted(campaigns))
    locations_query = f"""
        SELECT
            campaign.id,
            ad_group.name
        FROM ad_group
        WHERE campaign.id IN ({campaign_ids})
        ORDER BY campaign.id, ad_group.name
    """

    def read_locations():
        locations = {campaign_id: set() for campaign_id in campaigns}
        response = google_ads_service.search_stream(
            customer_id=customer_id,
            query=locations_query,
        )
        for batch in response:
            for result in batch.results:
                campaign_id = str(result.campaign.id)
                ad_group_name = str(result.ad_group.name or "").strip()
                if campaign_id in locations and ad_group_name:
                    locations[campaign_id].add(ad_group_name)
        return locations

    locations = run_google_ads_read_with_retries(
        read_locations,
        f"consultando ubicaciones de la ficha anual {period['year']}",
    )
    for campaign_id, campaign in campaigns.items():
        campaign["location"] = " / ".join(
            sorted(locations.get(campaign_id, set()), key=normalize_text)
        )

    rows = list(campaigns.values())
    rows.sort(key=lambda item: (
        item["start_date"] or "9999-12-31",
        item["end_date"] or "9999-12-31",
        normalize_text(item["campaign_name"]),
    ))
    return rows
