"""Enrich atlas navigation from preserved map inventory and Exiles entity records.

Map identity comes from explicit string IDs. Name disagreements are retained; area
references establish hierarchy, never coordinates or confirmed spawn locations.
"""
import gzip
import json
from pathlib import Path

CONTINENTS = {'0': 'Eastern Kingdoms', '1': 'Kalimdor', '530': 'Outland', '571': 'Northrend'}
OTHER_REGION = 'Ascension & other worlds'


def _name(value):
    value = str(value or '').strip()
    if value in ('', '\ufffd', 'coa-db —', 'coa-db -'):
        return ''
    return value


def _sort_id(value):
    return (0, int(value)) if value.isdecimal() else (1, value)


def _records(root, manifest):
    for fid, info in sorted(manifest.get('files', {}).items()):
        if '/exiles-db/' not in info['path'] or not info['path'].endswith('/entities.jsonl.gz'):
            continue
        folder = root / 'records' / fid
        for part in sorted(folder.glob('*.json.gz'), key=lambda p: int(p.name.split('.')[0])):
            with gzip.open(part, 'rt', encoding='utf8') as reader:
                rows = json.load(reader)
            for offset, row in enumerate(rows):
                if len(row) >= 6 and row[2] in ('map', 'area'):
                    yield f'{fid}/{part.name.split(".")[0]}/{offset}', row


def enrich_atlas(root, manifest, atlas_result):
    """Mutate and return an atlas result before its manifest is serialized.

    maps[].records and areas[].record(s) link back into the catalog. areas[].parents
    and children are explicit preserved area IDs. No per-area zones are generated.
    """
    root = Path(root)
    maps, areas = {}, {}

    def world(mid):
        mid = str(mid)
        if mid not in maps:
            maps[mid] = {'id': mid, 'name': '', 'alternate_names': set(), 'records': set(),
                         'areas': set(), 'inventory': None, 'name_source': ''}
        return maps[mid]

    def area(aid):
        aid = str(aid)
        if aid not in areas:
            areas[aid] = {'id': aid, 'name': '', 'alternate_names': set(), 'records': set(),
                          'parents': set(), 'children': set(), 'maps': set()}
        return areas[aid]

    def named(entry, name, preferred=False):
        name = _name(name)
        if not name:
            return
        if name != entry['name']:
            if not entry['name'] or preferred:
                if entry['name']:
                    entry['alternate_names'].add(entry['name'])
                entry['name'] = name
            else:
                entry['alternate_names'].add(name)

    def connect(mid, aid):
        world(mid)['areas'].add(str(aid))
        area(aid)['maps'].add(str(mid))

    # Existing map summaries and zones collectively contain the complete inventory.
    for previous in atlas_result.get('maps', []):
        mid = previous.get('id', previous.get('map_id', previous.get('map', '')))
        if mid is not None and str(mid) != '':
            target = world(mid)
            inventory = previous.get('inventory') or (previous if 'map_id' in previous else None)
            if inventory:
                target['inventory'] = inventory
                named(target, inventory.get('name'), preferred=True)
                target['name_source'] = 'Published map inventory'
            else:
                named(target, previous.get('name'))
    for zone in atlas_result.get('zones', []):
        mid = str(zone.get('map', ''))
        if not mid:
            continue
        target = world(mid)
        inventory = zone.get('inventory')
        if inventory:
            target['inventory'] = inventory
            named(target, inventory.get('name'), preferred=True)
            if target['name']:
                target['name_source'] = 'Published map inventory'

    for record, row in _records(root, manifest):
        identifier, title, kind, _mode, _source, payload = row
        identifier = str(identifier)
        if not identifier:
            continue
        target = world(identifier) if kind == 'map' else area(identifier)
        target['records'].add(record)
        named(target, payload.get('name') or title, preferred=kind == 'area' and not target['records'] - {record})
        if kind == 'map' and not target['name_source']:
            target['name_source'] = 'Exiles DB'
        refs = payload.get('references', payload.get('structure', {}).get('references', []))
        tables = payload.get('tables', payload.get('structure', {}).get('tables', []))
        for ref in refs:
            ref_type, ref_id = ref.get('type'), str(ref.get('key', ''))
            if not ref_id or ref_type not in ('map', 'area'):
                continue
            reference = world(ref_id) if ref_type == 'map' else area(ref_id)
            named(reference, ref.get('label'))
            if kind == 'map' and ref_type == 'area':
                connect(identifier, ref_id)
            elif kind == 'area' and ref_type == 'map':
                connect(ref_id, identifier)
        if kind == 'area':
            parent_labels = {str(cells[1]) for table in tables if not table.get('header')
                             for cells in table.get('rows', []) if len(cells) == 2 and cells[0] == 'Parent zone'}
            parents = {str(ref['key']) for ref in refs if ref.get('type') == 'area' and ref.get('label') in parent_labels}
            for parent_id in parents - {identifier}:
                target['parents'].add(parent_id)
                area(parent_id)['children'].add(identifier)
            # A two-column ID/Name table on an area page is its explicit sub-zone list.
            if 'Sub-zones' in payload.get('headings', []):
                referenced = {str(ref['key']) for ref in refs if ref.get('type') == 'area'}
                for table in tables:
                    if table.get('header') != ['ID', 'Name']:
                        continue
                    for cells in table.get('rows', []):
                        if len(cells) == 2 and str(cells[0]) in referenced and str(cells[0]) != identifier:
                            child = area(cells[0]); named(child, cells[1])
                            child['parents'].add(identifier); target['children'].add(child['id'])

    for entry in areas.values():
        entry['name'] = entry['name'] or 'Area ' + entry['id']
        entry['alternate_names'] = sorted(entry['alternate_names'] - {entry['name']})
        for field in ('records', 'parents', 'children', 'maps'):
            entry[field] = sorted(entry[field], key=_sort_id if field != 'records' else None)
        entry['record'] = entry['records'][0] if entry['records'] else ''
    for entry in maps.values():
        entry['name'] = entry['name'] or CONTINENTS.get(entry['id']) or 'Map ' + entry['id']
        entry['alternate_names'] = sorted(entry['alternate_names'] - {entry['name']})
        entry['records'] = sorted(entry['records'])
        entry['record'] = entry['records'][0] if entry['records'] else ''
        entry['areas'] = sorted((areas[aid] for aid in entry['areas']), key=lambda a: (a['name'].casefold(), _sort_id(a['id'])))
        entry['region'] = CONTINENTS.get(entry['id'], OTHER_REGION)
    atlas_result['maps'] = sorted(maps.values(), key=lambda m: (m['name'].casefold(), _sort_id(m['id'])))
    atlas_result['areas'] = sorted(areas.values(), key=lambda a: _sort_id(a['id']))

    represented = set()
    for zone in atlas_result.get('zones', []):
        mid = str(zone.get('map', '')); represented.add(mid)
        zone['map_name'] = maps[mid]['name'] if mid in maps else 'Unspecified map'
        zone['region'] = CONTINENTS.get(mid, OTHER_REGION)
    for mid in maps.keys() - represented:
        entry = maps[mid]
        atlas_result.setdefault('zones', []).append({
            'key': 'map-' + mid + '-metadata', 'name': entry['name'], 'map': mid, 'map_name': entry['name'],
            'wma': '', 'region': entry['region'], 'aliases': entry['alternate_names'], 'space': 'inventory',
            'image': '', 'bounds': None, 'inventory': entry['inventory'], 'count': 0, 'layers': {}, 'file': '',
        })
    atlas_result['zones'].sort(key=lambda z: (z['region'], z['name'], z['key']))
    atlas_result['metadata'] = {'maps': len(maps), 'areas': len(areas), 'source': 'Published map inventory and Exiles DB',
                              'note': 'Names and area relations are preserved source claims. They do not establish coordinates or spawn locations.'}
    return atlas_result
