"""Small standalone Microsoft contract summary; no combined Google balance."""

import gspread

from .account_status import canonical_status_key, status_display_label
from .metrics import optional_currency_format_for_value, optional_decimal_format_for_value
from .periods import parse_sheet_date
from .sheets_io import get_worksheet_values, queue_format_requests, queue_values_update
from .text import normalize_text


def prepare_microsoft_contract_summary(worksheet, config):
    values = get_worksheet_values(worksheet)
    matches = [(r, c) for r, row in enumerate(values, 1) for c, value in enumerate(row, 1)
               if normalize_text(value).startswith('datosgeneralesdecampanamicrosoftadvertising')]
    if len(matches) != 1:
        raise ValueError('Microsoft contract summary title missing or ambiguous')
    title_row, start_col = matches[0]
    def cell(r, c):
        row = values[r - 1] if r <= len(values) else []
        return row[c - 1] if c <= len(row) else ''
    for offset, label in ((0, 'Fecha de inicio'), (1, 'Fecha fin'), (7, 'Consumo')):
        if normalize_text(cell(title_row + 1, start_col + offset)) != normalize_text(label):
            raise ValueError('Unexpected Microsoft contract summary columns')
    start = parse_sheet_date(cell(title_row + 2, start_col))
    end = parse_sheet_date(cell(title_row + 2, start_col + 1))
    if start is None or end is None or end < start:
        raise ValueError('Invalid Microsoft contract dates')
    expected = ['Campana', 'Estado', 'Inicio campana', 'Fin campana',
                'Clics', 'Impresiones', 'Coste', 'CPC', 'Conversiones']
    if [normalize_text(cell(title_row + 3, start_col + i)) for i in range(9)] != [normalize_text(v) for v in expected]:
        raise ValueError('Unexpected Microsoft contract campaign columns')
    capacity = config['campaign_capacity']
    if not isinstance(capacity, int) or not 1 <= capacity <= 30:
        raise ValueError('Invalid Microsoft contract capacity')
    data_start = title_row + 4
    data_end = data_start + capacity - 1
    if any(normalize_text(v) == 'mesactual' for row in values[title_row:data_end] for v in row):
        raise ValueError('Microsoft campaign capacity overlaps MES ACTUAL')
    range_name = f'{gspread.utils.rowcol_to_a1(title_row,start_col)}:{gspread.utils.rowcol_to_a1(data_end,start_col+8)}'
    previous_dates = {}
    for r in range(data_start, data_end + 1):
        name = normalize_text(cell(r, start_col))
        if name:
            if name in previous_dates:
                raise ValueError('Duplicate Microsoft campaign names in manual table')
            previous_dates[name] = [cell(r, start_col + 2), cell(r, start_col + 3)]
    return {'title_row': title_row, 'start_col': start_col, 'data_start': data_start,
            'data_end': data_end, 'start_day': start, 'end_day': end,
            'range': range_name, 'guard': worksheet.get(range_name), 'previous_dates': previous_dates}


def write_microsoft_contract_summary(worksheet, plan, data, batch):
    if data.get('not_started') or data.get('preserve_closed'):
        return
    if worksheet.get(plan['range']) != plan['guard']:
        raise ValueError('Microsoft contract changed during read; no summary written')
    rows = data['rows']
    capacity = plan['data_end'] - plan['data_start'] + 1
    if len(rows) > capacity:
        raise ValueError('Microsoft contract has more campaigns than reserved rows')
    names = [normalize_text(row['campaign_name']) for row in rows]
    if any(not name for name in names) or len(set(names)) != len(names):
        raise ValueError('Microsoft campaign names missing or ambiguous')
    start_col = plan['start_col']
    matrix = []
    requests = []
    for index, campaign in enumerate(rows):
        dates = plan['previous_dates'].get(normalize_text(campaign['campaign_name']), ['', ''])
        matrix.append([campaign['campaign_name'], status_display_label(campaign['campaign_status']),
            *dates, campaign['clicks'], campaign['impressions'], campaign['cost'],
            campaign['average_cpc'], campaign['conversions']])
        row = plan['data_start'] + index
        active = canonical_status_key(campaign['campaign_status']) == 'enabled'
        formats = {
            1: {'backgroundColor': {'red': int(not active), 'green': int(active), 'blue': 0},
                'textFormat': {'foregroundColor': {'red': int(not active), 'green': int(not active), 'blue': int(not active)}}},
            4: {'numberFormat': {'type': 'NUMBER', 'pattern': '#,##0'}},
            5: {'numberFormat': {'type': 'NUMBER', 'pattern': '#,##0'}},
            6: {'numberFormat': {'type': 'CURRENCY', 'pattern': optional_currency_format_for_value(campaign['cost'])}},
            7: {'numberFormat': {'type': 'CURRENCY', 'pattern': optional_currency_format_for_value(campaign['average_cpc'])}},
            8: {'numberFormat': {'type': 'NUMBER', 'pattern': optional_decimal_format_for_value(campaign['conversions'])}},
        }
        for offset, fmt in formats.items():
            fields = ('userEnteredFormat.backgroundColor,userEnteredFormat.textFormat.foregroundColor'
                      if offset == 1 else 'userEnteredFormat.numberFormat')
            requests.append({'repeatCell': {'range': {'sheetId': worksheet.id,
                'startRowIndex': row-1, 'endRowIndex': row,
                'startColumnIndex': start_col-1+offset, 'endColumnIndex': start_col+offset},
                'cell': {'userEnteredFormat': fmt}, 'fields': fields}})
    matrix.extend([[''] * 9 for _ in range(capacity - len(rows))])
    body = f'{gspread.utils.rowcol_to_a1(plan["data_start"],start_col)}:{gspread.utils.rowcol_to_a1(plan["data_end"],start_col+8)}'
    queue_values_update(worksheet, body, matrix, batch)
    cost_letter = gspread.utils.rowcol_to_a1(1,start_col+6).rstrip('1')
    consumption = gspread.utils.rowcol_to_a1(plan['title_row']+2,start_col+7)
    queue_values_update(worksheet, consumption,
        [[f'=SUM({cost_letter}{plan["data_start"]}:{cost_letter}{plan["data_end"]})']], batch)
    total = sum(row['raw_cost'] for row in rows)
    requests.append({'repeatCell': {'range': {'sheetId': worksheet.id,
        'startRowIndex': plan['title_row']+1, 'endRowIndex': plan['title_row']+2,
        'startColumnIndex': start_col+6, 'endColumnIndex': start_col+7},
        'cell': {'userEnteredFormat': {'numberFormat': {'type': 'CURRENCY', 'pattern': optional_currency_format_for_value(total)}}},
        'fields': 'userEnteredFormat.numberFormat'}})
    queue_format_requests(worksheet, requests, batch)
