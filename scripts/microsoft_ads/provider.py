import tempfile
import csv
import io
import time
from bisect import bisect_right
from datetime import date, timedelta
from decimal import Decimal, InvalidOperation

import requests

from .common import (
    add_google_identity_provider_header,
    create_authorization_data,
    create_service_client,
    get_field,
    normalize_text,
)


CAMPAIGN_ENDPOINT = (
    "https://campaign.api.bingads.microsoft.com/"
    "CampaignManagement/v13/Campaigns/QueryByAccountId"
)
CAMPAIGN_TYPES = (
    "Search",
    "Shopping",
    "DynamicSearchAds",
    "Audience",
    "Hotel",
    "PerformanceMax",
    "App",
)
ECB_USD_EUR_ENDPOINT = (
    "https://data-api.ecb.europa.eu/service/data/EXR/D.USD.EUR.SP00.A"
)
MAX_EXTERNAL_RETRIES = 3
RETRY_DELAY_SECONDS = 2
RETRYABLE_HTTP_STATUS_CODES = {408, 429, 500, 502, 503, 504}


def _decimal(value, field_name="valor Microsoft Ads"):
    text = str(value or "").strip().replace("%", "").replace(",", "")
    if not text or text in {"--", "-"}:
        return Decimal("0")
    try:
        parsed = Decimal(text)
    except InvalidOperation as exc:
        raise ValueError(
            f"{field_name} contiene un numero no valido."
        ) from exc
    if not parsed.is_finite():
        raise ValueError(f"{field_name} contiene un numero no finito.")
    return parsed


def _float(value, field_name="valor Microsoft Ads"):
    return float(_decimal(value, field_name))


def _is_retryable_external_error(error):
    if isinstance(error, (requests.Timeout, requests.ConnectionError)):
        return True
    response = getattr(error, "response", None)
    status_code = getattr(response, "status_code", None)
    if status_code in RETRYABLE_HTTP_STATUS_CODES:
        return True
    normalized = normalize_text(error)
    return any(fragment in normalized for fragment in (
        "timeout",
        "timedout",
        "temporarilyunavailable",
        "throttl",
        "toomanyrequests",
        "429",
        "500",
        "502",
        "503",
        "504",
    ))


def _run_with_retries(operation, description, sleep_fn=time.sleep):
    for attempt in range(1, MAX_EXTERNAL_RETRIES + 1):
        try:
            return operation()
        except Exception as exc:
            if (
                not _is_retryable_external_error(exc)
                or attempt >= MAX_EXTERNAL_RETRIES
            ):
                raise
            delay = RETRY_DELAY_SECONDS * attempt
            print(
                f"Microsoft Ads: error temporal {description}; "
                f"reintento {attempt + 1}/{MAX_EXTERNAL_RETRIES} en {delay}s."
            )
            sleep_fn(delay)


def _request_with_retries(method, description, **kwargs):
    def execute_request():
        response = method(**kwargs)
        response.raise_for_status()
        return response

    return _run_with_retries(execute_request, description)


def _normalize_source_currency(value):
    currency = str(value or "").strip().upper()
    if currency != "USD":
        raise ValueError(
            "El proveedor Microsoft Ads solo admite moneda origen USD; "
            f"se recibio {currency or 'vacia'}."
        )
    return currency


def _parse_report_day(value):
    text = str(value or "").strip()
    try:
        return date.fromisoformat(text[:10])
    except ValueError as exc:
        raise ValueError(
            f"Microsoft Ads devolvio una fecha de informe no reconocible: {text}"
        ) from exc


def _normalize_status(value):
    normalized = normalize_text(value)
    if normalized == "active":
        return "enabled"
    if normalized in {"paused", "budgetpaused"}:
        return "paused"
    if normalized:
        return normalized
    return "unknown"


def _fetch_usd_eur_rates(start_day, end_day):
    response = _request_with_retries(
        requests.get,
        "consultando tipos USD/EUR del BCE",
        url=ECB_USD_EUR_ENDPOINT,
        params={
            "startPeriod": date.fromordinal(start_day.toordinal() - 7).isoformat(),
            "endPeriod": end_day.isoformat(),
            "format": "csvdata",
        },
        timeout=30,
    )
    rates = {}
    for row in csv.DictReader(io.StringIO(response.text)):
        period = str(row.get("TIME_PERIOD") or "").strip()
        value = _float(row.get("OBS_VALUE"), "OBS_VALUE del BCE")
        if period and value:
            rates[date.fromisoformat(period)] = value
    if not rates:
        raise RuntimeError("El BCE no devolvio tipos USD/EUR para el periodo.")
    return rates


def _rate_for_day(rates, day):
    available_days = sorted(rates)
    index = bisect_right(available_days, day) - 1
    if index < 0:
        raise RuntimeError(
            f"No hay un tipo USD/EUR del BCE aplicable a {day.isoformat()}."
        )
    return rates[available_days[index]]


def _average_rate_for_period(rates, start_day, end_day):
    period_values = [
        rate
        for day, rate in rates.items()
        if start_day <= day <= end_day
    ]
    if period_values:
        return sum(period_values) / len(period_values)
    return _rate_for_day(rates, end_day)


def _monthly_average_rates(rates, start_day, end_day):
    """Devuelve un promedio BCE independiente para cada mes del periodo."""
    monthly_rates = {}
    cursor = start_day.replace(day=1)
    while cursor <= end_day:
        if cursor.month == 12:
            next_month = date(cursor.year + 1, 1, 1)
        else:
            next_month = date(cursor.year, cursor.month + 1, 1)
        month_start = max(start_day, cursor)
        month_end = min(end_day, next_month - timedelta(days=1))
        monthly_rates[(cursor.year, cursor.month)] = _average_rate_for_period(
            rates,
            month_start,
            month_end,
        )
        cursor = next_month
    return monthly_rates


def _campaign_catalog(authorization_data, account_id, customer_id):
    authorization_data.account_id = int(account_id)
    authorization_data.customer_id = int(customer_id)
    service = create_service_client(
        "CampaignManagementService",
        authorization_data,
    )
    headers = service.create_rest_headers()
    headers["IdentityProvider"] = "Google"
    headers["Content-Type"] = "application/json"

    campaigns_by_id = {}
    for campaign_type in CAMPAIGN_TYPES:
        response = _request_with_retries(
            requests.post,
            f"consultando catalogo {campaign_type}",
            url=CAMPAIGN_ENDPOINT,
            headers=headers,
            json={
                "AccountId": str(account_id),
                "CampaignType": campaign_type,
            },
            timeout=60,
        )
        for campaign in response.json().get("Campaigns") or []:
            campaign_id = str(get_field(campaign, "Id", "id"))
            campaigns_by_id[campaign_id] = {
                "name": str(get_field(campaign, "Name", "name") or ""),
                "status": _normalize_status(
                    get_field(campaign, "Status", "status")
                ),
                "daily_budget": _float(
                    get_field(campaign, "DailyBudget", "daily_budget"),
                    "DailyBudget",
                ),
            }
    return campaigns_by_id


def _download_daily_report(
    authorization_data,
    account_id,
    customer_id,
    start_day,
    end_day,
):
    from bingads.service_client import ServiceClient
    from bingads.v13.reporting import (
        ReportingDownloadParameters,
        ReportingServiceManager,
    )
    from openapi_client.models.reporting.account_through_campaign_report_scope import (
        AccountThroughCampaignReportScope,
    )
    from openapi_client.models.reporting.campaign_performance_report_column import (
        CampaignPerformanceReportColumn,
    )
    from openapi_client.models.reporting.campaign_performance_report_request import (
        CampaignPerformanceReportRequest,
    )
    from openapi_client.models.reporting.date import Date
    from openapi_client.models.reporting.report_aggregation import ReportAggregation
    from openapi_client.models.reporting.report_format import ReportFormat
    from openapi_client.models.reporting.report_time import ReportTime
    from openapi_client.models.reporting.report_time_zone import ReportTimeZone

    authorization_data.account_id = int(account_id)
    authorization_data.customer_id = int(customer_id)
    reporting_service = add_google_identity_provider_header(ServiceClient(
        service="ReportingService",
        version=13,
        authorization_data=authorization_data,
        environment="production",
    ))
    request = CampaignPerformanceReportRequest(
        Aggregation=ReportAggregation.DAILY,
        ExcludeColumnHeaders=False,
        ExcludeReportFooter=True,
        ExcludeReportHeader=False,
        Format=ReportFormat.CSV,
        ReportName="SEM Microsoft Ads",
        ReturnOnlyCompleteData=False,
        Time=ReportTime(
            CustomDateRangeStart=Date(
                Year=start_day.year,
                Month=start_day.month,
                Day=start_day.day,
            ),
            CustomDateRangeEnd=Date(
                Year=end_day.year,
                Month=end_day.month,
                Day=end_day.day,
            ),
            ReportTimeZone=ReportTimeZone.BRUSSELSCOPENHAGENMADRIDPARIS,
        ),
        Scope=AccountThroughCampaignReportScope(
            AccountIds=[str(account_id)],
            Campaigns=None,
        ),
        Columns=[
            CampaignPerformanceReportColumn.TIMEPERIOD,
            CampaignPerformanceReportColumn.CAMPAIGNID,
            CampaignPerformanceReportColumn.CAMPAIGNNAME,
            CampaignPerformanceReportColumn.CAMPAIGNSTATUS,
            CampaignPerformanceReportColumn.CLICKS,
            CampaignPerformanceReportColumn.IMPRESSIONS,
            CampaignPerformanceReportColumn.SPEND,
            CampaignPerformanceReportColumn.CONVERSIONS,
            CampaignPerformanceReportColumn.TOPIMPRESSIONRATEPERCENT,
            CampaignPerformanceReportColumn.ABSOLUTETOPIMPRESSIONRATEPERCENT,
        ],
    )

    with tempfile.TemporaryDirectory(prefix="msads-sem-") as directory:
        manager = ReportingServiceManager(
            authorization_data=authorization_data,
            poll_interval_in_milliseconds=3000,
            environment="production",
            working_directory=directory,
        )
        add_google_identity_provider_header(manager._service_client)
        parameters = ReportingDownloadParameters(
            report_request=request,
            result_file_directory=directory,
            result_file_name="campaign-daily.csv",
            overwrite_result_file=True,
            timeout_in_milliseconds=300000,
        )
        report = _run_with_retries(
            lambda: manager.download_report(parameters),
            "descargando informe diario",
        )
        if report is None:
            return []
        records = []
        try:
            for record in report.report_records:
                records.append({
                    "day": _parse_report_day(record.value("TimePeriod")),
                    "campaign_id": str(record.value("CampaignId") or ""),
                    "campaign_name": str(record.value("CampaignName") or ""),
                    "campaign_status": _normalize_status(
                        record.value("CampaignStatus")
                    ),
                    "clicks": int(_decimal(record.value("Clicks"), "Clicks")),
                    "impressions": int(
                        _decimal(record.value("Impressions"), "Impressions")
                    ),
                    "cost": _float(record.value("Spend"), "Spend"),
                    "conversions": _float(
                        record.value("Conversions"),
                        "Conversions",
                    ),
                    "top_rate_percent": _float(
                        record.value("TopImpressionRatePercent"),
                        "TopImpressionRatePercent",
                    ),
                    "absolute_top_rate_percent": _float(
                        record.value("AbsoluteTopImpressionRatePercent"),
                        "AbsoluteTopImpressionRatePercent",
                    ),
                })
        finally:
            report.close()
        return records


def _build_rows(records, catalog, prefix, monthly_exchange_rates, budget_day):
    aggregates = {}
    for record in records:
        campaign_id = record["campaign_id"]
        campaign = aggregates.setdefault(campaign_id, {
            "campaign_name": record["campaign_name"],
            "campaign_status": record["campaign_status"],
            "latest_day": record["day"],
            "clicks": 0,
            "impressions": 0,
            "cost": 0.0,
            "conversions": 0.0,
            "top_weighted": 0.0,
            "top_weight": 0,
            "absolute_top_weighted": 0.0,
            "absolute_top_weight": 0,
        })
        if record["day"] >= campaign["latest_day"]:
            campaign["latest_day"] = record["day"]
            campaign["campaign_name"] = record["campaign_name"]
            campaign["campaign_status"] = record["campaign_status"]

        impressions = record["impressions"]
        campaign["clicks"] += record["clicks"]
        campaign["impressions"] += impressions
        exchange_rate = monthly_exchange_rates[
            (record["day"].year, record["day"].month)
        ]
        campaign["cost"] += record["cost"] / exchange_rate
        campaign["conversions"] += record["conversions"]
        if record["top_rate_percent"] and impressions:
            campaign["top_weighted"] += (
                record["top_rate_percent"] / 100 * impressions
            )
            campaign["top_weight"] += impressions
        if record["absolute_top_rate_percent"] and impressions:
            campaign["absolute_top_weighted"] += (
                record["absolute_top_rate_percent"] / 100 * impressions
            )
            campaign["absolute_top_weight"] += impressions

    rows = []
    for campaign_id, campaign in aggregates.items():
        clicks = campaign["clicks"]
        impressions = campaign["impressions"]
        cost = campaign["cost"]
        conversions = campaign["conversions"]
        catalog_item = catalog.get(campaign_id, {})
        name = catalog_item.get("name") or campaign["campaign_name"]
        status = catalog_item.get("status") or campaign["campaign_status"]
        rows.append({
            "campaign_id": f"microsoft:{campaign_id}",
            "campaign_name": f"{prefix}{name}",
            "campaign_status": status,
            "clicks": clicks,
            "ctr": round(clicks / impressions * 100, 2) if impressions else 0,
            "average_cpc": round(cost / clicks, 2) if clicks else 0,
            "cost": round(cost, 6),
            "conversions": round(conversions, 2),
            "cost_per_conversion": (
                round(cost / conversions, 2) if conversions else ""
            ),
            "impressions": impressions,
            "top_impression_share": (
                round(campaign["top_weighted"] / campaign["top_weight"], 2)
                if campaign["top_weight"] else ""
            ),
            "absolute_top_impression_share": (
                round(
                    campaign["absolute_top_weighted"]
                    / campaign["absolute_top_weight"],
                    2,
                )
                if campaign["absolute_top_weight"] else ""
            ),
            "daily_budget": round(
                catalog_item.get("daily_budget", 0)
                / monthly_exchange_rates[(budget_day.year, budget_day.month)],
                2,
            ),
            "raw_cost": cost,
            "source_customer_id": "microsoft",
        })

    rows.sort(key=lambda item: normalize_text(item["campaign_name"]))
    return rows


def fetch_microsoft_campaign_periods(
    account_id,
    customer_id,
    period_contexts,
    campaign_prefix="MS Ads - ",
    source_currency="USD",
):
    if not period_contexts:
        return {"period_rows": {}, "currency": None}

    source_currency = _normalize_source_currency(source_currency)
    _, authorization_data = create_authorization_data()
    catalog = _campaign_catalog(
        authorization_data,
        account_id,
        customer_id,
    )
    start_day = min(
        context["query_start_day"] for context in period_contexts.values()
    )
    end_day = max(
        context["query_end_day"] for context in period_contexts.values()
    )
    records = _download_daily_report(
        authorization_data,
        account_id,
        customer_id,
        start_day,
        end_day,
    )
    rates = _fetch_usd_eur_rates(start_day, end_day)
    monthly_exchange_rates = _monthly_average_rates(
        rates,
        start_day,
        end_day,
    )
    period_rows = {}
    exchange_rates = {}
    for period_key, context in period_contexts.items():
        period_records = [
            record
            for record in records
            if context["query_start_day"]
            <= record["day"]
            <= context["query_end_day"]
        ]
        period_months = {
            (record["day"].year, record["day"].month)
            for record in period_records
        }
        if not period_months:
            period_months = {
                (
                    context["query_end_day"].year,
                    context["query_end_day"].month,
                )
            }
        exchange_rates[period_key] = {
            f"{year:04d}-{month:02d}": monthly_exchange_rates[(year, month)]
            for year, month in sorted(period_months)
        }
        period_rows[period_key] = _build_rows(
            period_records,
            catalog,
            campaign_prefix,
            monthly_exchange_rates,
            context["query_end_day"],
        )

    return {
        "period_rows": period_rows,
        "source_currency": source_currency,
        "currency": "EUR",
        "exchange_rate_source": "ECB monthly average reference rates",
        "exchange_rates": exchange_rates,
    }
