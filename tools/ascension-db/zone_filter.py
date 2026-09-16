"""Source-record zone facets, separate from coordinate/floor identity in the atlas.

Never propagate locations across entity IDs, modes or sources. Quest ZoneOrSort
is an area ID only when positive; negative values are quest categories.
"""
import collections
import gzip
import hashlib
import json
import re
from pathlib import Path

from atlas import AREA_ROWS, compact

KINDS = {'quest', 'npc', 'gameobject', 'world-creature', 'world-object',
         'loot-pin', 'route-npc', 'route-landmark'}
STEMS = {'questcache', 'creaturecache', 'gameobjectcache', 'quests', 'npcs',
         'creatures', 'gameobjects', 'pins', 'entities', 'npcs-by-floor'}


def records(root, manifest):
    for fid, info in sorted(manifest['files'].items()):
        path = info['path']
        if Path(path).name.split('.')[0] not in STEMS and '/instance-route-maps/' not in path and '/research-intake/' not in path:
            continue
        for part in sorted((root / 'records' / fid).glob('*.json.gz')):
            with gzip.open(part, 'rt', encoding='utf-8') as stream:
                for offset, row in enumerate(json.load(stream)):
                    if row[2] in KINDS:
                        yield f'{fid}/{part.name.split(".")[0]}/{offset}', path, row


class ZoneIndex:
    def __init__(self, atlas):
        self.labels = {}
        self.names = collections.defaultdict(set)
        self.reviewed = collections.defaultdict(set)
        self.worlds = {}
        self.parents = {}
        for area in atlas.get('areas', []):
            key = 'area:' + area['id']
            self.labels[key] = area['name'] if compact(area['name']) else 'Area ' + area['id']
            self.worlds[key] = set(area.get('maps', []))
            self.parents[key] = {'area:' + str(aid) for aid in area.get('parents', [])}
            for name in [area['name'], *area.get('alternate_names', [])]:
                if compact(name):
                    self.names[compact(name)].add(key)
        # The atlas's reviewed outdoor crosswalk includes client map filenames.
        for name, aid, world, aliases in AREA_ROWS:
            key = 'area:' + str(aid)
            if key not in self.labels or self.labels[key] == 'Area ' + str(aid):
                self.labels[key] = name
            self.worlds.setdefault(key, set()).add(str(world))
            for alias in [name, *aliases.split('|')]:
                if alias:
                    self.names[compact(alias)].add(key)
                    self.reviewed[compact(alias)].add((str(world), key))
        self.atlas = {z['key']: z for z in atlas.get('zones', [])}

    def area(self, value):
        value = str(value).strip()
        if not re.fullmatch(r'[0-9]+', value) or not 0 < int(value) < 2**31:
            return None
        key = 'area:' + str(int(value))
        self.labels.setdefault(key, 'Area ' + str(int(value)))
        return key

    def named(self, name, world=''):
        name = str(name or '').strip()
        if not name or name.lower() in ('unknown', 'unspecified', '0'):
            return None
        reviewed = {key for mid, key in self.reviewed.get(compact(name), set()) if world == '' or str(world) == mid}
        if len(reviewed) == 1:
            return next(iter(reviewed))
        matches = self.names.get(compact(name), set())
        if world != '':
            matches = {key for key in matches if not self.worlds.get(key) or str(world) in self.worlds[key]}
        if len(matches) == 1:
            return next(iter(matches))
        if matches:
            # Some areas and their sub-areas share a name (e.g. Westfall).
            # A shared ancestor is a valid zone match without choosing a sub-area.
            common = set.intersection(*(self.ancestors({key}) for key in matches)) & matches
            if len(common) == 1:
                return next(iter(common))
        # Ambiguous names stay separate; server-map IDs are never area IDs.
        key = 'named:' + hashlib.sha256((str(world) + '\0' + name.casefold()).encode()).hexdigest()[:20]
        self.labels.setdefault(key, name + (f' (map {world})' if world != '' else ''))
        return key

    def ancestors(self, found):
        found = set(found)
        pending = list(found)
        while pending:
            for parent in self.parents.get(pending.pop(), set()) - found:
                self.labels.setdefault(parent, 'Area ' + parent[5:])
                found.add(parent)
                pending.append(parent)
        return found

    def memberships(self, path, row):
        kind, payload = row[2], row[5]
        inner = payload.get('record', payload)
        if not isinstance(inner, dict):
            inner = payload
        found = set()
        if kind == 'quest':
            found.add(self.area(inner.get('ZoneOrSort', '')))
        if '/catalogue/' in path:
            found.add(self.named(payload.get('example_zone'), payload.get('example_map_id', '')))
            for name in str(payload.get('zone_list', '')).split('|'):
                found.add(self.named(name))
        if kind == 'loot-pin':
            found.add(self.named(payload.get('zone'), payload.get('map', '')))
        if '/exiles-db/' in path:
            structure = payload.get('structure', payload)
            for table in structure.get('tables', []):
                if table.get('header') == ['Map', 'Area', 'Coords', 'Spawns']:
                    for location in table.get('rows', []):
                        if len(location) == 4:
                            refs = structure.get('references', [])
                            worlds = {str(r['key']) for r in refs if r.get('type') == 'map' and r.get('label') == location[0]}
                            found.add(self.named(location[1], next(iter(worlds)) if len(worlds) == 1 else ''))
            if kind == 'quest':
                for ref in structure.get('references', []):
                    if ref.get('type') == 'area':
                        found.add(self.area(ref.get('key', '')))
        found.discard(None)
        # A recorded sub-zone also belongs to its explicitly published ancestors.
        # Walk with a visited set because untrusted source hierarchies may cycle.
        return self.ancestors(found)


def build_zone_filter(root, manifest, atlas, buckets, icons=None):
    root = Path(root)
    index = ZoneIndex(atlas)
    with gzip.open(root / manifest['atlas']['links'], 'rt', encoding='utf-8') as stream:
        links = json.load(stream)
    counts = collections.defaultdict(collections.Counter)
    kinds = collections.Counter()
    for key, path, record in records(root, manifest):
        eid, title, kind, mode, source = record[:5]
        zones = index.memberships(path, record)
        if kind in ('route-npc', 'route-landmark'):
            for atlas_key in links.get(key, []):
                zone = index.atlas[atlas_key]
                facet = 'atlas:' + atlas_key
                index.labels[facet] = zone['name']
                zones.add(facet)
        kinds[kind] += 1
        row = [key, title, kind, mode, source, eid]
        icon = icons.lookup(kind, eid) if icons else ''
        if icon:
            row.append(icon)
        for zone in sorted(zones or {'unknown'}):
            counts[zone][kind] += 1
            buckets.add('zone:' + zone, row)
    index.labels['unknown'] = 'Unknown zone'
    manifest['zoneFilter'] = {
        'schema': 'ascension-zones-1',
        'kinds': dict(kinds),
        'zones': [{'key': key, 'label': index.labels[key], 'kinds': dict(value), 'count': sum(value.values())}
                  for key, value in sorted(counts.items(), key=lambda pair: (index.labels[pair[0]].casefold(), pair[0]))],
    }
