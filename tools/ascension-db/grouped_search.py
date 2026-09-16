"""Content groups for browsing; original record identities and evidence stay intact.

Only documented WDB export bookkeeping is excluded from content identity. Unknown
fields, types, array order, translated text and every gameplay field remain exact.
SQLite bounds build memory; the browser streams already grouped search partitions.
"""
import collections
import gzip
import hashlib
import itertools
import json
import re
import sqlite3
from pathlib import Path

from zone_filter import ZoneIndex, KINDS, STEMS

SCHEMA = 'ascension-content-groups-1'
BOOKKEEPING = {'_modes', '_captured', '_sources', '_locales'}
CACHE_VIEW = re.compile(r'^cachedata/(?:by-locale/[^/]+/)?(?:union|by-mode/[^/]+)/[^/]*cache\.tsv(?:\.gz)?$')


def dump(value):
    return json.dumps(value, ensure_ascii=False, separators=(',', ':'))


def split(value):
    return sorted({x.strip() for x in re.split(r'[,|]', str(value)) if x.strip()})


def content_id(path, record):
    payload = record[5]
    if CACHE_VIEW.fullmatch(path):
        payload = {k: v for k, v in payload.items() if k not in BOOKKEEPING}
    # Title is included because names are searchable and may be source-derived.
    value = [record[2], record[0], record[1], payload]
    return hashlib.sha256(json.dumps(value, ensure_ascii=False, sort_keys=True,
                                    separators=(',', ':'), allow_nan=False).encode()).hexdigest()


def locale(path, record):
    match = re.match(r'^cachedata/by-locale/([^/]+)/', path)
    if match:
        return match[1]
    # Aggregate modes/locales are independent sets, not proven pairs. Only a
    # singleton locale may be used as an exact filter outside a locale export.
    values = split(record[5].get('_locales', '')) if CACHE_VIEW.fullmatch(path) else []
    return values[0] if len(values) == 1 else 'Unspecified'


def search_row(gid, rows):
    first = rows[0]
    members = [r[7] for r in rows]  # key, mode, source, locale
    return [first[0], first[1], first[2],
            ','.join(sorted({v for m in members for v in split(m[1])})),
            ', '.join(sorted({m[2] for m in members})), first[5], first[6],
            {'id': gid, 'members': members}]


def build_grouped_search(root, manifest, atlas, buckets, icons, stage):
    root = Path(root)
    db = sqlite3.connect(str(Path(stage) / 'groups.sqlite'))
    db.execute('PRAGMA journal_mode=OFF')
    db.execute('PRAGMA synchronous=OFF')
    db.execute('PRAGMA cache_size=-32768')
    db.execute('PRAGMA temp_store=FILE')
    db.execute('CREATE TABLE records (gid TEXT, key TEXT, row TEXT, zones TEXT)')
    zones = ZoneIndex(atlas)
    with gzip.open(root / manifest['atlas']['links'], 'rt', encoding='utf-8') as stream:
        links = json.load(stream)
    counts = collections.defaultdict(collections.Counter)
    eligible = collections.Counter()
    locales = set()
    batch = []
    try:
        for fid, info in sorted(manifest['files'].items()):
            path = info['path']
            zone_file = Path(path).name.split('.')[0] in STEMS or '/instance-route-maps/' in path or '/research-intake/' in path
            for part in sorted((root / 'records' / fid).glob('*.json.gz')):
                with gzip.open(part, 'rt', encoding='utf-8') as stream:
                    for offset, record in enumerate(json.load(stream)):
                        key = f'{fid}/{part.name.split(".")[0]}/{offset}'
                        eid, title, kind, mode, source = record[:5]
                        language = locale(path, record)
                        locales.add(language)
                        row = [key, title, kind, mode, source, eid,
                               icons.lookup(kind, eid) if icons else '',
                               [key, mode, source, language]]
                        found = set()
                        if zone_file and kind in KINDS:
                            eligible[kind] += 1
                            found = zones.memberships(path, record)
                            if kind in ('route-npc', 'route-landmark'):
                                for atlas_key in links.get(key, []):
                                    facet = 'atlas:' + atlas_key
                                    zones.labels[facet] = zones.atlas[atlas_key]['name']
                                    found.add(facet)
                            found = found or {'unknown'}
                            for zone in found:
                                counts[zone][kind] += 1
                        batch.append((content_id(path, record), key, dump(row), dump(sorted(found))))
                        if len(batch) >= 2000:
                            db.executemany('INSERT INTO records VALUES (?,?,?,?)', batch)
                            batch.clear()
        db.executemany('INSERT INTO records VALUES (?,?,?,?)', batch)
        db.commit()
        print('Grouping preserved content across all collections…', flush=True)
        db.execute('CREATE INDEX group_order ON records(gid,key)')
        browsing = collections.defaultdict(list)
        group_counts = collections.Counter()
        total = max_members = 0
        for gid, group in itertools.groupby(db.execute('SELECT gid,row,zones FROM records ORDER BY gid,key'), key=lambda r: r[0]):
            records = [(json.loads(r), json.loads(z)) for _, r, z in group]
            row = search_row(gid, [r for r, _ in records])
            # Fail explicitly rather than silently dropping extreme groups.
            if len(dump(row).encode()) > 24000000:
                raise ValueError('Content group exceeds supported search asset size: ' + gid)
            total += 1
            max_members = max(max_members, len(records))
            group_counts[row[2]] += 1
            if len(browsing[row[2]]) < 100:
                browsing[row[2]].append(row)
            # Import here to share exactly the index's Unicode token rules.
            from build import tokens
            prefixes = {'browse:' + row[2], 'id:' + row[5][:2]}
            prefixes.update('name:' + t[:2] for t in tokens(row[1]) if len(t) >= 2)
            for prefix in prefixes:
                buckets.add(prefix, row)
            # Zone membership belongs to each record. A zone result must never
            # expose a sibling's source/mode/locale as matching that zone.
            zone_members = collections.defaultdict(list)
            for member, found in records:
                for zone in found:
                    zone_members[zone].append(member)
            for zone, members in zone_members.items():
                buckets.add('zone:' + zone, search_row(gid, members))
        zones.labels['unknown'] = 'Unknown zone'
        manifest['zoneFilter'] = {
            'schema': 'ascension-zones-1', 'kinds': dict(eligible),
            'zones': [{'key': key, 'label': zones.labels[key], 'kinds': dict(value), 'count': sum(value.values())}
                      for key, value in sorted(counts.items(), key=lambda p: (zones.labels[p[0]].casefold(), p[0]))],
        }
        manifest['grouping'] = {'schema': SCHEMA, 'groups': total, 'records': manifest['records'],
                                'kinds': dict(group_counts), 'max_members': max_members}
        manifest['locales'] = sorted(locales)
        manifest['browse'] = dict(browsing)
        print(f'Grouped {manifest["records"]:,} source records into {total:,} content results; largest group {max_members:,}.', flush=True)
    finally:
        db.close()
