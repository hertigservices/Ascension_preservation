#!/usr/bin/env python3
"""Build a fail-closed review queue for NPC-to-zone quest relationships.

Quest zones describe quests, not necessarily every NPC related to them.  This tool
therefore records relationship candidates and review decisions; it never turns a
candidate into a live claim.  Accepted rows must also exist in reviewed.json and
are incorporated by make_npc_zone_claims.py.
"""
import argparse
import collections
import csv
import gzip
import io
import json
import re
import unicodedata
from pathlib import Path


FIELDS = [
    'npc_id', 'npc_name', 'area_id', 'quest_id', 'quest_title', 'relation',
    'objective_index', 'name_match', 'existing_area_ids', 'decision',
    'evidence', 'modes', 'locales',
]
STOPWORDS = {'a', 'an', 'and', 'at', 'for', 'from', 'in', 'of', 'on', 'the', 'to'}


def textopen(path):
    path = Path(path)
    return gzip.open(path, 'rt', encoding='utf-8', newline='') if path.suffix == '.gz' else path.open(encoding='utf-8', newline='')


def normalized(value):
    value = ''.join(c for c in unicodedata.normalize('NFKD', str(value or '')) if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+', value.casefold()))


def positive(value):
    value = str(value or '').strip()
    return int(value) if value.isdigit() and int(value) > 0 else None


def read_claim_pairs(path, evidence=None):
    pairs = set()
    if not path:
        return pairs
    if str(path).endswith('.json'):
        rows = json.loads(Path(path).read_text(encoding='utf-8'))
    else:
        with textopen(path) as stream:
            rows = list(csv.DictReader(stream, delimiter='\t'))
    for row in rows:
        if evidence and row.get('evidence') != evidence:
            continue
        npc, area = positive(row.get('npc_id')), positive(row.get('area_id'))
        if npc and area:
            pairs.add((npc, area))
    return pairs


def read_spawns(path):
    pairs = set()
    with textopen(path) as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {'entry', 'area_id'} <= set(reader.fieldnames):
            raise ValueError('creature_spawn export is missing entry or area_id')
        for row in reader:
            npc, area = positive(row['entry']), positive(row['area_id'])
            if npc and area:
                pairs.add((npc, area))
    return pairs


def read_npcs(path):
    npcs, existing = {}, collections.defaultdict(set)
    with textopen(path) as stream:
        for line in stream:
            if not line.strip():
                continue
            row = json.loads(line)
            npc = positive(row.get('id'))
            if not npc:
                continue
            npcs[npc] = str(row.get('name') or row.get('page_title') or f'NPC #{npc}')
            for ref in row.get('structure', {}).get('references', []):
                if ref.get('type') == 'area' and positive(ref.get('key')):
                    existing[npc].add(positive(ref['key']))
    return npcs, existing


def read_areas(path):
    areas = {}
    with textopen(path) as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {'id', 'name'} <= set(reader.fieldnames):
            raise ValueError('area export is missing id or name')
        for row in reader:
            area = positive(row['id'])
            if area and normalized(row.get('name')):
                areas[area] = str(row['name'])
    return areas


def read_relations(path, relation):
    found = collections.defaultdict(list)
    if not path:
        return found
    with textopen(path) as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {'id', 'quest_id'} <= set(reader.fieldnames):
            raise ValueError(f'{relation} export is missing id or quest_id')
        for row in reader:
            npc, quest = positive(row['id']), positive(row['quest_id'])
            if npc and quest:
                found[quest].append((npc, relation, ''))
    return found


def quest_rows(path):
    with textopen(path) as stream:
        reader = csv.DictReader(stream, delimiter='\t')
        required = {'entry', 'ZoneOrSort', 'Title', 'RequiredNpcOrGo1', 'ObjectiveText1'}
        if not reader.fieldnames or not required <= set(reader.fieldnames):
            raise ValueError('quest cache is missing relationship fields')
        yield from reader


def audit(quest_cache, npcs_path, areas_path, spawns_path, reviewed_path=None,
          starts_path=None, ends_path=None):
    npcs, existing = read_npcs(npcs_path)
    for npc, area in read_spawns(spawns_path):
        existing[npc].add(area)
    accepted = read_claim_pairs(reviewed_path)
    areas = read_areas(areas_path)
    starters = read_relations(starts_path, 'quest-starter')
    enders = read_relations(ends_path, 'quest-ender')
    result = {}

    def add(q, npc, relation, objective_index='', name_match=False, evidence=''):
        area, quest = positive(q.get('ZoneOrSort')), positive(q.get('entry'))
        if not area or not quest or npc not in npcs or area in existing[npc]:
            return
        current = ','.join(str(x) for x in sorted(existing[npc]))
        decision = 'accepted-reviewed' if (npc, area) in accepted else ('review-required-unzoned' if not current else 'review-required-conflict')
        key = (npc, area, quest, relation, str(objective_index))
        result[key] = {
            'npc_id': npc, 'npc_name': npcs[npc], 'area_id': area,
            'quest_id': quest, 'quest_title': q.get('Title', ''),
            'relation': relation, 'objective_index': objective_index,
            'name_match': 'yes' if name_match else 'no',
            'existing_area_ids': current, 'decision': decision,
            'evidence': evidence, 'modes': q.get('_modes', ''),
            'locales': q.get('_locales', ''),
        }

    quests = list(quest_rows(quest_cache))
    for q in quests:
        quest = positive(q.get('entry'))
        area = positive(q.get('ZoneOrSort'))
        if not quest or not area:
            continue
        for index in range(1, 5):
            npc = positive(q.get(f'RequiredNpcOrGo{index}'))
            if npc not in npcs:
                continue
            objective = q.get(f'ObjectiveText{index}', '')
            match = bool(re.search(r'(?<!\w)' + re.escape(npcs[npc]) + r'(?!\w)', objective, re.IGNORECASE))
            add(q, npc, 'objective-target', index, match, objective)
        for npc, relation, objective_index in starters.get(quest, []) + enders.get(quest, []):
            add(q, npc, relation, objective_index, False, relation)

    # Catch high-signal prose-only cases such as "Injured Northshire Guard":
    # strip the quest area's distinctive name words, then require a multi-word
    # residual phrase to occur verbatim in that same quest's prose.
    by_area = collections.defaultdict(list)
    for q in quests:
        area = positive(q.get('ZoneOrSort'))
        if area:
            prose = ' '.join(normalized(q.get(field, '')) for field in ('Title', 'Objectives', 'Details', 'EndText', 'CompletedText'))
            by_area[area].append((q, prose))
    area_words_by_id = {area: set(normalized(name).split()) - STOPWORDS for area, name in areas.items()}
    areas_by_word = collections.defaultdict(set)
    for area, words in area_words_by_id.items():
        for word in words:
            areas_by_word[word].add(area)
    for npc, name in npcs.items():
        if existing[npc]:
            continue
        name_words = normalized(name).split()
        candidate_areas = set().union(*(areas_by_word.get(word, set()) for word in name_words))
        for area in candidate_areas:
            area_words = area_words_by_id[area]
            residual = [word for word in name_words if word not in area_words and word not in STOPWORDS]
            if len(residual) < 2:
                continue
            phrase = ' '.join(residual)
            for q, prose in by_area.get(area, []):
                if re.search(r'(?<!\w)' + re.escape(phrase) + r'(?!\w)', prose):
                    add(q, npc, 'quest-text-zone-name', '', True, f'area token removed from NPC name; residual phrase: {phrase}')

    order = {'accepted-reviewed': 0, 'review-required-unzoned': 1, 'review-required-conflict': 2}
    return sorted(result.values(), key=lambda r: (order[r['decision']], int(r['area_id']), int(r['npc_id']), int(r['quest_id']), r['relation'], str(r['objective_index'])))


def write(path, rows):
    buffer = io.StringIO(newline='')
    writer = csv.DictWriter(buffer, fieldnames=FIELDS, delimiter='\t', lineterminator='\n')
    writer.writeheader()
    writer.writerows(rows)
    data = buffer.getvalue().encode()
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('wb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0, compresslevel=9) as stream:
            stream.write(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--quest-cache', type=Path, required=True)
    parser.add_argument('--npcs', type=Path, required=True)
    parser.add_argument('--areas', type=Path, required=True)
    parser.add_argument('--spawns', type=Path, required=True)
    parser.add_argument('--reviewed', type=Path)
    parser.add_argument('--quest-starts', type=Path)
    parser.add_argument('--quest-ends', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    rows = audit(args.quest_cache, args.npcs, args.areas, args.spawns, args.reviewed, args.quest_starts, args.quest_ends)
    write(args.out, rows)
    counts = collections.Counter(row['decision'] for row in rows)
    print(json.dumps({'candidates': len(rows), 'decisions': dict(sorted(counts.items())), 'output': str(args.out)}))


if __name__ == '__main__':
    main()
