"""Optional platform labels without losing the operational campaign status."""

import json

PLATFORMS = {
    'google': 'Google Ads', 'microsoft': 'Microsoft Ads',
    'meta': 'Meta', 'linkedin': 'LinkedIn', 'openai': 'ChatGPT Ads',
}
PREFIXES = {
    'microsoft': 'MS Ads - ', 'meta': 'Meta Ads - ',
    'linkedin': 'LinkedIn Ads - ', 'openai': 'ChatGPT Ads - ',
}
NOTE_PREFIX = 'SEM campaign metadata: '
STATE_KEY = 'sem_campaign_state_v1'


def platform_campaign(campaign):
    result = dict(campaign)
    source = result.get('source_customer_id')
    source = source if source in PLATFORMS else 'google'
    for candidate, prefix in PREFIXES.items():
        if result['campaign_name'].startswith(prefix):
            source = candidate
            result['campaign_name'] = result['campaign_name'][len(prefix):]
            break
    result['display_platform'] = PLATFORMS[source]
    result['display_source'] = source
    return result


def campaign_state(campaign):
    return {
        'status': campaign['campaign_status'],
        'source': campaign['display_source'],
    }


def state_requests(sheet_id, row, state):
    """Replace only our row metadata; retries never accumulate entries."""
    location = {'dimensionRange': {'sheetId': sheet_id, 'dimension': 'ROWS',
                                  'startIndex': row - 1, 'endIndex': row}}
    return [{
        'deleteDeveloperMetadata': {'dataFilter': {'developerMetadataLookup': {
            'metadataKey': STATE_KEY, 'visibility': 'DOCUMENT',
            'metadataLocation': location, 'locationMatchingStrategy': 'EXACT_LOCATION',
        }}},
    }, {
        'createDeveloperMetadata': {'developerMetadata': {
            'metadataKey': STATE_KEY, 'metadataValue': json.dumps(state, sort_keys=True),
            'location': location, 'visibility': 'DOCUMENT',
        }},
    }]


def read_campaign_states(worksheet, status_col, refresh=False):
    states = getattr(worksheet, '_sem_platform_states', None)
    if states is not None and not refresh:
        return states
    sheet = worksheet.spreadsheet.fetch_sheet_metadata(params={
        'ranges': [worksheet.title], 'includeGridData': True,
        'fields': 'sheets(developerMetadata,data(startRow,startColumn,rowMetadata(developerMetadata),rowData(values(note))))',
    })['sheets'][0]
    states = {}
    # Transitional read support for backups made before the notes were removed.
    for grid in sheet.get('data', []):
        for offset, row in enumerate(grid.get('rowData', [])):
            col = status_col - 1 - grid.get('startColumn', 0)
            values = row.get('values', [])
            note = values[col].get('note', '') if 0 <= col < len(values) else ''
            if note.startswith(NOTE_PREFIX):
                states[offset + grid.get('startRow', 0) + 1] = json.loads(note[len(NOTE_PREFIX):])
    seen = set()
    metadata_items = list(sheet.get('developerMetadata', []))
    for grid in sheet.get('data', []):
        for row in grid.get('rowMetadata', []):
            metadata_items.extend(row.get('developerMetadata', []))
    for item in metadata_items:
        if item.get('metadataKey') != STATE_KEY:
            continue
        location = item.get('location', {}).get('dimensionRange', {})
        if location.get('sheetId') != worksheet.id or location.get('dimension') != 'ROWS':
            continue
        if location.get('endIndex', 0) - location.get('startIndex', 0) != 1:
            raise ValueError('Invalid campaign metadata row span')
        row = location['startIndex'] + 1
        if row in seen:
            raise ValueError(f'Duplicate campaign metadata at row {row}')
        seen.add(row)
        states[row] = json.loads(item['metadataValue'])
    worksheet._sem_platform_states = states
    return states


def restore_platform_campaigns(worksheet, campaigns, status_col):
    """Historical normalisation must sort/color by saved state, not platform."""
    if not getattr(worksheet, '_sem_platform_display', False):
        return campaigns
    states = read_campaign_states(worksheet, status_col)
    restored = []
    for campaign in campaigns:
        row = int(campaign['campaign_id'].split(':')[-1])
        if campaign['campaign_status'] in PLATFORMS.values():
            if row not in states:
                raise ValueError(f'Missing original campaign state at row {row}')
            metadata = states[row]
            if metadata['source'] not in PLATFORMS or metadata['status'] in PLATFORMS.values():
                raise ValueError(f'Invalid campaign metadata at row {row}')
            campaign = dict(campaign, campaign_status=metadata['status'],
                            source_customer_id=metadata['source'])
        restored.append(platform_campaign(campaign))
    return restored
