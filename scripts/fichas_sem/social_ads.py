"""Adaptador SEM social: completa ambas lecturas antes de devolver metricas."""

import math

from .account_status import aggregate_account_operational_status, classify_account_operational_status
from .metrics import sort_campaign_rows

SOCIAL_HANDLER = 'social_campaigns'


def _number(value):
    try:
        result = float(value)
    except (ValueError, TypeError) as exc:
        raise ValueError('Metrica social ausente o no numerica.') from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError('Metrica social negativa o no finita.')
    return result


def _status(value):
    if value == 'ACTIVE':
        return 'enabled'
    if value in {'PAUSED', 'CAMPAIGN_PAUSED', 'ADSET_PAUSED'}:
        return 'paused'
    return str(value or 'unknown').lower()


def _campaign_row(source, row):
    clicks, impressions = _number(row['clicks']), _number(row['impressions'])
    if not clicks.is_integer() or not impressions.is_integer():
        raise ValueError('Clics o impresiones sociales fraccionarios.')
    cost, conversions = _number(row['cost']), _number(row['conversions'])
    if not str(row['id']).isdigit() or not str(row['name']).strip():
        raise ValueError('Campana social sin ID o nombre.')
    return {
        'campaign_id': source + ':' + str(row['id']),
        'campaign_name': ('Meta Ads - ' if source == 'meta' else 'LinkedIn Ads - ') + row['name'],
        'campaign_status': _status(row['status']),
        'clicks': int(clicks), 'impressions': int(impressions),
        'ctr': round(clicks / impressions * 100, 2) if impressions else 0,
        'average_cpc': round(cost / clicks, 2) if clicks else 0,
        'cost': round(cost, 6), 'raw_cost': cost, 'conversions': conversions,
        'cost_per_conversion': round(cost / conversions, 2) if conversions else '',
        'daily_budget': _number(row['daily_budget']) if row['daily_budget'] is not None else '',
        'top_impression_share': '', 'absolute_top_impression_share': '',
        'source_customer_id': source,
    }


def fetch_social_campaign_periods(config, contexts, operational_day):
    from meta_ads.sem_provider import fetch_meta_sem_periods
    from linkedin_ads.sem_provider import fetch_linkedin_sem_periods

    results = {
        'meta': fetch_meta_sem_periods(config, contexts, operational_day),
        'linkedin': fetch_linkedin_sem_periods(config, contexts, operational_day),
    }
    period_rows = {key: [] for key in contexts}
    statuses = {}
    for source, result in results.items():
        if result['currency'] != 'EUR' or set(result['periods']) != set(contexts):
            raise ValueError('Respuesta social incompleta o moneda no EUR.')
        for key, rows in result['periods'].items():
            period_rows[key].extend(_campaign_row(source, row) for row in rows)
        statuses[source] = classify_account_operational_status(
            'enabled' if result['account_active'] else 'suspended',
            [{'status': _status(row['status']), 'end_date': row['end_date']} for row in result['campaigns']],
            operational_day,
        )
    return {
        'account_statuses': statuses,
        'social_status': aggregate_account_operational_status(statuses),
        'technical_account_statuses': {},
        'period_rows': {key: sort_campaign_rows(rows) for key, rows in period_rows.items()},
    }
