"""Source-specific attribution from preserved evidence, never from item similarity.

Realm names are not inferred from a game mode. Account-wide files may contain
several independent realm branches; only the current record's branch is used.
"""
import re

MODES = {
    'Conquest of Azeroth': 'conquest-of-azeroth', 'Free-Pick': 'free-pick',
    'Season 10 Freepick': 'season-10-freepick', 'Season 10 Wildcard': 'season-10-wildcard',
    'Season 9': 'season-9', 'Warcraft Reborn': 'warcraft-reborn',
    'Warcraft Reborn_Horde': 'warcraft-reborn-horde', 'CoA PTR': 'coa-ptr',
    'CoA Beta': 'coa-beta', 'CoA Alpha - Development': 'coa-alpha',
    'Live QA': 'live-qa', 'Stress Test': 'stress-test', 'Development': 'development',
}
EMPTY = {'', 'unknown', 'unknown realm', 'unspecified', '(unattributed)', '(account-wide)'}
SINGULAR = {'cachedata/sources.tsv', 'cachedata/lua/harvest/advancement.tsv',
            'cachedata/lua/harvest/vendors.tsv', 'cachedata/lua/harvest/gossips.tsv'}
GROUPED = {'cachedata/lua/harvest/byRealm.json', 'cachedata/lua/Auctionator.observations.json'}
LOOT = {'cachedata/lootcollector/items.tsv', 'cachedata/lootcollector/sources.tsv',
        'cachedata/lootcollector/pins.tsv'}


def affected(path):
    return path in SINGULAR | GROUPED | LOOT


def values(value):
    if isinstance(value, list):
        return [str(x).strip() for x in value if isinstance(x, (str, int)) and str(x).strip()]
    return [x.strip() for x in re.split(r'[,|]', str(value or '')) if x.strip()]


def canonical(mode):
    return MODES.get(mode, mode)


def group(label):
    """Require an explicit, recognized mode suffix, not a realm-name lookup."""
    for mode in sorted(MODES, key=len, reverse=True):
        suffix = ' - ' + mode
        if label.endswith(suffix) and label[:-len(suffix)].strip():
            return label[:-len(suffix)].strip(), MODES[mode]
    return None


def resolve(path, row):
    if not affected(path) or not isinstance(row, dict):
        return None
    if path in SINGULAR and not any(row.get(k) for k in ('mode', 'realm', '_modes', 'modes')):
        return None
    evidence = []
    realms = set()
    modes = set()
    unknown = False

    def add(field, raw_modes, raw_realms=(), basis='record'):
        nonlocal unknown
        mm = values(raw_modes)
        unknown |= any(x.lower() in EMPTY for x in mm)
        known = {canonical(x) for x in mm if x.lower() not in EMPTY}
        rr = {x for x in raw_realms if x.lower() not in EMPTY}
        modes.update(known); realms.update(rr)
        if mm or rr:
            evidence.append({'field': field, 'modes': sorted(known), 'realms': sorted(rr), 'basis': basis})

    if path in SINGULAR:
        add('mode / realm', row.get('mode'), values(row.get('realm')), str(row.get('mode_source') or 'record'))
    if path in LOOT:
        if row.get('modes'):
            add('modes', row['modes'])
        for label in values(row.get('realms')):
            pair = group(label)
            if pair:
                add('realms', pair[1], [pair[0]], 'explicit realm-mode label')
            else:
                unknown = True
    if path == 'cachedata/lua/harvest/byRealm.json':
        pair = group(str(row.get('key', '')))
        if pair:
            add('key', pair[1], [pair[0]], 'explicit realm-mode branch')
        else:
            unknown = True
    if path == 'cachedata/lua/Auctionator.observations.json':
        key = row.get('key')
        if key in MODES.values():
            add('key', key, basis='published mode branch')
        else:
            unknown = True
    # Preserve independent declarations and expose disagreement rather than
    # silently choosing one. An unknown declaration is absence, not contradiction.
    for field in ('_modes', 'modes'):
        if row.get(field) and not (path in LOOT and field == 'modes'):
            add(field, row[field])
    declarations = [set(e['modes']) for e in evidence if e['modes']]
    # Several realm branches legitimately aggregate into one LootCollector row.
    declared = {canonical(x) for x in values(row.get('modes')) if x.lower() not in EMPTY}
    grouped = {m for e in evidence if e['field'] == 'realms' for m in e['modes']}
    conflict = bool(declared and grouped and declared != grouped) if path in LOOT else len({tuple(sorted(x)) for x in declarations}) > 1
    status = 'conflicting' if conflict else 'mixed' if modes and unknown else 'recorded' if modes else 'unknown'
    return {'modes': sorted(modes), 'realms': sorted(realms), 'status': status, 'evidence': evidence}


def mode_text(info):
    if not info['modes']:
        return 'unknown' if info['status'] == 'unknown' else 'Unspecified'
    return ','.join(info['modes'] + (['unknown'] if info['status'] == 'mixed' else []))
