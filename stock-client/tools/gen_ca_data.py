#!/usr/bin/env python3
"""Generate matching CA preview Lua, normalized JSON and SQL staging data.

Inputs are read-only. Realm/mode variants remain separate; no DB connection is made.
Requires explicit input/output paths. Generated Lua uses only Lua 5.1 syntax.
"""
import argparse
from collections import Counter, defaultdict
import csv
import hashlib
import io
import json
import math
import os
from pathlib import Path
import re
import struct
import tempfile

FORMAT = 1
MAX_LUA_BYTES = 350_000
STRINGS = {'Name', 'Class', 'Tab', 'Type', 'NodeType', 'Quality', 'Anchor', 'Color', 'Icon'}
ARRAYS = {'ConnectedNodes', 'Masteries', 'RequiredIDs', 'SpellCastReq'}
BOOLEANS = {'isAbility', 'isMastery', 'isTalent'}
META = {'realm', 'mode', 'node', 'seenBy'}
NUMBERS = set('ID quality RequiredLevel AECost TECost QualityCost essenceCost talentCost Column Distance Flags Group ParentNode Points PositionX PositionY RequiredAEInvestment RequiredClassAEInvestment RequiredClassPoints RequiredClassTEInvestment RequiredTEInvestment RequiredTabAEInvestment RequiredTabTEInvestment Row SizeX SizeY'.split())
RELATIONS = ('ConnectedNodes', 'RequiredIDs', 'Masteries')


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def read_source(path, label, sources):
    path = Path(path)
    before = path.stat()
    raw = path.read_bytes()
    after = path.stat()
    if (before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_ino, after.st_size, after.st_mtime_ns):
        raise ValueError('input changed while reading: ' + str(path))
    sources[label] = {'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()}
    return raw


def integer(value, what):
    if isinstance(value, bool):
        raise ValueError(what + ': boolean is not an integer ID')
    if isinstance(value, int):
        return value
    if isinstance(value, str) and re.fullmatch(r'-?\d+', value):
        return int(value)
    raise ValueError(what + ': expected integer')


def number(value, what):
    if re.fullmatch(r'-?\d+', value):
        return int(value)
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(what + ': non-finite number')
    return result


def normalize(row):
    unknown = set(row) - STRINGS - ARRAYS - BOOLEANS - META - NUMBERS - {'Spells'}
    if unknown:
        raise ValueError('unclassified harvest columns: ' + repr(sorted(unknown)))
    out = {}
    for key, value in row.items():
        if value is None:
            raise ValueError('ragged harvest row')
        if key in META or value == '':
            continue
        if key in STRINGS:
            out[key] = value
        elif key in NUMBERS:
            out[key] = number(value, key)
        elif key in BOOLEANS:
            if value.lower() not in ('true', 'false'):
                raise ValueError('invalid boolean in ' + key)
            out[key] = value.lower() == 'true'
        elif key in ARRAYS:
            values = json.loads(value)
            if not isinstance(values, list):
                raise ValueError(key + ' must be a JSON array')
            out[key] = values if key == 'SpellCastReq' else [integer(v, key) for v in values]
        elif key == 'Spells':
            spells = json.loads(value)
            if not isinstance(spells, list):
                raise ValueError('Spells must be a JSON array')
            out['Spells'], out['SpellNames'] = [], {}
            for item in spells:
                sid = integer(item['id'] if isinstance(item, dict) else item, 'Spells')
                if sid <= 0:
                    raise ValueError('spell ID must be positive')
                out['Spells'].append(sid)
                if isinstance(item, dict) and item.get('name'):
                    out['SpellNames'][str(sid)] = item['name']
    eid = integer(out.get('ID'), 'entry ID')
    if eid <= 0 or eid != integer(row['node'], 'node'):
        raise ValueError('node and entry ID differ')
    if not out.get('Class') or not out.get('Tab') or 'Spells' not in out:
        raise ValueError('entry is missing identity or spells')
    out['SpellIDs'] = list(out['Spells'])
    out['Harvested'] = True
    return out


def dataset_key(realm, mode):
    slug = re.sub(r'[^a-z0-9]+', '-', realm.lower()).strip('-') or 'unnamed'
    digest = hashlib.sha256(canonical([realm, mode]).encode()).hexdigest()[:10]
    return slug[:40] + '-' + digest


def load_harvest(raw):
    groups = {}
    reader = csv.DictReader(io.StringIO(raw.decode('utf-8-sig'), newline=''), delimiter='\t')
    for line, row in enumerate(reader, 2):
        try:
            pair = (row['realm'], row['mode'])
            key = dataset_key(*pair)
            group = groups.setdefault(key, {'realm': pair[0], 'mode': pair[1], 'entries': {}, 'rows': 0})
            record = normalize(row)
            eid = record['ID']
            if eid in group['entries'] and group['entries'][eid] != record:
                raise ValueError('conflicting duplicate ID within one realm/mode')
            group['entries'][eid] = record
            group['rows'] += 1
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError('harvest line %d: %s' % (line, exc)) from exc
    if not groups:
        raise ValueError('harvest has no entries')
    return groups


def dbc_rows(raw):
    if len(raw) < 20 or raw[:4] != b'WDBC':
        raise ValueError('invalid WDBC header')
    count, fields, stride, strings = struct.unpack_from('<4I', raw, 4)
    if stride % 4 or 20 + count * stride + strings != len(raw):
        raise ValueError('invalid WDBC record/string bounds')
    return [struct.unpack_from('<%dI' % (stride // 4), raw, 20 + n * stride) for n in range(count)]


def load_essence(raw):
    result = []
    ids = set()
    for row in dbc_rows(raw):
        if len(row) != 9 or row[0] in ids:
            raise ValueError('unexpected essence row layout or duplicate ID')
        ids.add(row[0])
        result.append(dict(zip(('ID', 'Level', 'Family', 'Match1', 'Match2', 'Match3', 'Match4', 'AE', 'TE'), row)))
    return sorted(result, key=lambda r: r['ID'])



def dbc_strings(raw):
    rows = dbc_rows(raw)
    count, fields, stride, size = struct.unpack_from('<4I', raw, 4)
    block = raw[20 + count * stride:]
    starts = {0} | {n + 1 for n, byte in enumerate(block) if byte == 0 and n + 1 < len(block)}
    def string(offset):
        if offset not in starts:
            raise ValueError('DBC string does not point to a string start')
        end = block.find(b'\0', offset)
        if end < 0:
            raise ValueError('unterminated DBC string')
        return block[offset:end].decode('utf-8')
    return rows, string


def load_specs(raw):
    # Verified against GetSpecInfo 0x100DAC60 in original Extensions.dll SHA256
    # 0f8d847b3adc44a963606f0cd4f7938ad6fcd6f4d87feac3131c7153bdf3bb11.
    # Native records collapse the localized Name/Description groups; on disk these
    # start at columns29/46, followed by SortOrder63 and LootSpecID64.
    rows, text = dbc_strings(raw)
    result = []
    for row in rows:
        if len(row) != 65:
            raise ValueError('unexpected ChrSpecs stride')
        record = {'ID': row[0], 'Class': text(row[1]), 'Spec': text(row[2]),
                  'SpecFilename': text(row[3]), 'DifficultyRating': text(row[17]),
                  'Name': text(row[29]), 'Description': text(row[46]),
                  'SortOrder': row[63], 'LootSpecID': row[64],
                  'PassiveSpell': row[27], 'PassiveID': row[28]}
        for column, name in enumerate(('Cloth', 'Leather', 'Mail', 'Plate'), 4):
            record[name] = bool(row[column])
        for column, name in enumerate(('MeleeDPS', 'RangedDPS', 'CasterDPS', 'Tank', 'Healer', 'Support'), 11):
            record[name] = bool(row[column])
        for name, start, sentinel in (('PrimaryStats', 8, 'None'), ('PrimaryResources', 18, 'NONE'), ('SecondaryResources', 21, 'NONE')):
            record[name] = [text(v) for v in row[start:start+3] if text(v) != sentinel]
        record['ExampleSpells'] = [v for v in row[24:27] if v]
        result.append(record)
    if len({r['ID'] for r in result}) != len(result):
        raise ValueError('duplicate specialization ID')
    return sorted(result, key=lambda r: r['ID'])


def load_class_identities(raw, classes):
    rows, text = dbc_strings(raw)
    names = {}
    displays = defaultdict(list)
    for entry in classes:
        name = entry['Name'].strip().casefold()
        if name in names:
            raise ValueError('duplicate internal CA class name')
        names[name] = entry
        if entry.get('Display'):
            displays[entry['Display'].strip().casefold()].append(entry)
    result = []
    for row in rows:
        if len(row) < 56:
            raise ValueError('unexpected ChrClasses stride')
        record = {'ID': row[0], 'Name': text(row[4]), 'Token': text(row[55])}
        # Reborn classes share display labels with stock classes. Prefer exact
        # internal-name/token matches; an ambiguous display label is not an ID join.
        match = names.get(record['Token'].strip().casefold()) or names.get(record['Name'].strip().casefold())
        if not match:
            options = displays.get(record['Name'].strip().casefold(), [])
            if len(options) > 1:
                raise ValueError('ambiguous display-only class join: ' + record['Name'])
            match = options[0] if options else None
        if match:
            record.update(CAClassTypeID=match['ID'], CAClassName=match['Name'])
        result.append(record)
    if not any(r['ID'] > 11 for r in result):
        raise ValueError('stock ChrClasses decoy: no custom class bytes')
    return sorted(result, key=lambda r: r['ID'])


def lua(value):
    if value is None:
        return 'nil'
    if value is True:
        return 'true'
    if value is False:
        return 'false'
    if isinstance(value, (int, float)):
        if not math.isfinite(value):
            raise ValueError('non-finite Lua number')
        return repr(value)
    if isinstance(value, str):
        # Decimal escapes are fixed-width so a following digit cannot join them.
        return '"' + ''.join('\\%03d' % ord(c) if ord(c) < 32 or ord(c) == 127 else '\\' + c if c in '\\"' else c for c in value) + '"'
    if isinstance(value, list):
        return '{' + ','.join(lua(v) for v in value) + '}'
    if isinstance(value, dict):
        return '{' + ','.join('[' + lua(k) + ']=' + lua(value[k]) for k in sorted(value, key=lambda k: (type(k).__name__, str(k)))) + '}'
    raise TypeError(type(value).__name__)


def sql_text(value):
    return "CONVERT(X'%s' USING utf8mb4)" % str(value).encode('utf-8').hex()


def sql_number(value):
    return 'NULL' if value is None else str(integer(value, 'SQL scalar'))


SQL_HEADER = '''-- Generated staging data only. Does not modify characters or teach spells.
-- Import into an isolated world DB only after review. Tables are shared with the future P3 module.
CREATE TABLE IF NOT EXISTS ca_dataset (dataset VARCHAR(64) NOT NULL PRIMARY KEY, realm TEXT NOT NULL, mode TEXT NOT NULL, content_sha256 CHAR(64) NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS ca_entry (dataset VARCHAR(64) NOT NULL, entry INT UNSIGNED NOT NULL, class_name VARCHAR(80) NOT NULL, tab_name VARCHAR(80) NOT NULL, required_level INT NULL, ae_cost INT NULL, te_cost INT NULL, harvested TINYINT NOT NULL, data_json MEDIUMTEXT NOT NULL, PRIMARY KEY(dataset,entry)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS ca_spell (dataset VARCHAR(64) NOT NULL, entry INT UNSIGNED NOT NULL, rank_index INT UNSIGNED NOT NULL, spell INT UNSIGNED NOT NULL, PRIMARY KEY(dataset,entry,rank_index)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS ca_edge (dataset VARCHAR(64) NOT NULL, entry INT UNSIGNED NOT NULL, kind VARCHAR(24) NOT NULL, ordinal INT UNSIGNED NOT NULL, target INT UNSIGNED NOT NULL, resolved TINYINT NOT NULL, PRIMARY KEY(dataset,entry,kind,ordinal)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS ca_essence (dataset VARCHAR(64) NOT NULL, id INT UNSIGNED NOT NULL, level INT UNSIGNED NOT NULL, family INT UNSIGNED NOT NULL, match1 INT UNSIGNED NOT NULL, match2 INT UNSIGNED NOT NULL, match3 INT UNSIGNED NOT NULL, match4 INT UNSIGNED NOT NULL, ae INT UNSIGNED NOT NULL, te INT UNSIGNED NOT NULL, PRIMARY KEY(dataset,id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS ca_reference (dataset VARCHAR(64) NOT NULL, kind VARCHAR(24) NOT NULL, id INT UNSIGNED NOT NULL, data_json MEDIUMTEXT NOT NULL, PRIMARY KEY(dataset,kind,id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
START TRANSACTION;
'''


def assemble(groups, dbcs, essence, sources):
    outputs = {}
    summaries = []
    raw_entries = {integer(r['ID'], 'DBC entry'): r for r in dbcs['entries']}
    if len(raw_entries) != len(dbcs['entries']):
        raise ValueError('duplicate DBC entry ID')
    all_variants = defaultdict(dict)
    for key, group in sorted(groups.items()):
        records = group['entries']
        missing_dbc = sorted(set(records) - set(raw_entries))
        dbc_only = sorted(set(raw_entries) - set(records))
        for eid, raw in raw_entries.items():
            if eid not in records:
                records[eid] = {field: raw[field] for field in ('ID', 'Name', 'Type', 'Icon', 'Anchor', 'Color', 'NodeType', 'PositionX', 'PositionY', 'SizeX', 'SizeY') if field in raw}
                records[eid].update(Class=raw['ClassType'], Tab=raw['TabType'], Harvested=False,
                                    Spells=[raw[name] for name in ('SpellID', 'SpellID2', 'SpellID3', 'SpellID4', 'SpellID5') if raw.get(name)])
                records[eid]['SpellIDs'] = list(records[eid]['Spells'])
            for field in ('Description', 'Description2', 'ClassTypeID', 'TabTypeID'):
                if field in raw:
                    records[eid][field] = raw[field]
        entries = [records[eid] for eid in sorted(records)]
        gaps = {'dbc_only_entries': dbc_only, 'harvest_entries_without_dbc': missing_dbc, 'missing_relationships': [], 'opaque_spell_requirements': [], 'missing_icons': []}
        buckets = defaultdict(list)
        for record in entries:
            eid = record['ID']
            buckets[(record['Class'], record['Tab'])].append(eid)
            all_variants[eid][key] = record
            for relation in RELATIONS:
                for target in record.get(relation, []):
                    if target not in records:
                        gaps['missing_relationships'].append({'entry': eid, 'kind': relation, 'target': target})
            if '<table>' in record.get('SpellCastReq', []):
                gaps['opaque_spell_requirements'].append(eid)
            if not record.get('Icon'):
                gaps['missing_icons'].append(eid)
        content_hash = hashlib.sha256(canonical(entries).encode()).hexdigest()
        metadata = dict(format=FORMAT, key=key, realm=group['realm'], mode=group['mode'], entryCount=len(entries), harvestedCount=sum(r['Harvested'] for r in entries),
                        classCount=len({r['Class'] for r in entries}), bucketCount=len(buckets),
                        nonPlaceholderClassCount=len({r['Class'] for r in entries if r['Class'] != 'None'}),
                        nonPlaceholderBucketCount=sum(c != 'None' for c, t in buckets), contentSha256=content_hash)
        metadata['buckets'] = [{'Class': c, 'Tab': t, 'Count': len(ids)} for (c, t), ids in sorted(buckets.items())]
        base = 'datasets/' + key + '/'
        outputs[base + 'entries.json'] = canonical(entries) + '\n'
        outputs[base + 'gaps.json'] = canonical(gaps) + '\n'
        outputs[base + 'manifest.json'] = canonical(metadata) + '\n'
        tables = {'classes': dbcs['classes'], 'tabs': dbcs['tabs'], 'categories': dbcs['categories'], 'essence': essence, 'specs': dbcs['specs'], 'classIdentities': dbcs['classIdentities']}
        outputs[base + 'tables.json'] = canonical(tables) + '\n'
        prefix = '-- Generated by gen_ca_data.py; do not hand-edit.\n'
        outputs[base + 'lua/Initialize.lua'] = prefix + 'assert(ASC and ASC.Data, "Ascension data core must load first")\nASC.Data.BeginDataset(' + lua(metadata) + ')\n'
        outputs[base + 'lua/Tables.lua'] = prefix + 'ASC.Data.SetTables(' + lua(tables) + ')\n'
        by_class = defaultdict(list)
        for record in entries:
            by_class[record['Class']].append(record)
        load_order = ['lua/Initialize.lua', 'lua/Tables.lua']
        for class_name, class_entries in sorted(by_class.items()):
            safe = re.sub(r'[^A-Za-z0-9_-]', '_', class_name)
            # Include digest to prevent sanitized filenames colliding.
            safe += '-' + hashlib.sha256(class_name.encode()).hexdigest()[:6]
            part, chunk = 1, prefix
            for record in class_entries:
                line = 'ASC.Data.AddEntry(' + lua(record) + ')\n'
                if len((prefix + line).encode()) > MAX_LUA_BYTES:
                    raise ValueError('one entry exceeds Lua chunk limit')
                if len((chunk + line).encode()) > MAX_LUA_BYTES:
                    name = 'lua/entries/%s-%03d.lua' % (safe, part)
                    outputs[base + name] = chunk; load_order.append(name)
                    part += 1; chunk = prefix
                chunk += line
            name = 'lua/entries/%s-%03d.lua' % (safe, part)
            outputs[base + name] = chunk; load_order.append(name)
        outputs[base + 'lua/Finalize.lua'] = prefix + 'ASC.Data.FinalizeDataset()\n'
        load_order.append('lua/Finalize.lua')
        outputs[base + 'load-order.txt'] = '\n'.join(load_order) + '\n'
        sql = [SQL_HEADER]
        skey = sql_text(key)
        for table in ('ca_edge', 'ca_spell', 'ca_entry', 'ca_essence', 'ca_reference', 'ca_dataset'):
            sql.append('DELETE FROM %s WHERE dataset=%s;\n' % (table, skey))
        sql.append('INSERT INTO ca_dataset VALUES (%s,%s,%s,%s);\n' % (skey, sql_text(group['realm']), sql_text(group['mode']), sql_text(content_hash)))
        for record in entries:
            eid = record['ID']
            values = [skey, str(eid), sql_text(record['Class']), sql_text(record['Tab']), sql_number(record.get('RequiredLevel')), sql_number(record.get('AECost')), sql_number(record.get('TECost')), str(int(record['Harvested'])), sql_text(canonical(record))]
            sql.append('INSERT INTO ca_entry VALUES (' + ','.join(values) + ');\n')
            for rank, spell in enumerate(record['Spells'], 1):
                sql.append('INSERT INTO ca_spell VALUES (%s,%d,%d,%d);\n' % (skey, eid, rank, spell))
            for relation in RELATIONS:
                for ordinal, target in enumerate(record.get(relation, []), 1):
                    sql.append('INSERT INTO ca_edge VALUES (%s,%d,%s,%d,%d,%d);\n' % (skey, eid, sql_text(relation), ordinal, target, int(target in records)))
        # Preserve all matching-flag rows; callers choose a matching curve explicitly.
        for r in essence:
            values = ','.join(str(r[k]) for k in ('ID', 'Level', 'Family', 'Match1', 'Match2', 'Match3', 'Match4', 'AE', 'TE'))
            sql.append('INSERT INTO ca_essence VALUES (' + skey + ',' + values + ');\n')
        for kind in ('classes', 'tabs', 'categories', 'specs', 'classIdentities'):
            seen_ids = set()
            for record in tables[kind]:
                rid = integer(record['ID'], kind + ' ID')
                if rid in seen_ids:
                    raise ValueError('duplicate reference ID in ' + kind)
                seen_ids.add(rid)
                sql.append('INSERT INTO ca_reference VALUES (%s,%s,%d,%s);\n' % (skey, sql_text(kind), rid, sql_text(canonical(record))))
        sql.append('COMMIT;\n')
        outputs[base + 'world-staging.sql'] = ''.join(sql)
        summaries.append(metadata)
    conflicts = []
    for eid, variants in sorted(all_variants.items()):
        fields = sorted(set().union(*(r.keys() for r in variants.values())))
        changed = [field for field in fields if len({canonical(r.get(field)) for r in variants.values()}) > 1]
        if changed:
            conflicts.append({'entry': eid, 'fields': changed, 'values': {key: {f: r.get(f) for f in changed} for key, r in sorted(variants.items())}})
    outputs['realm-differences.json'] = canonical(conflicts) + '\n'
    outputs['generation.json'] = canonical({'format': FORMAT, 'sources': sources, 'datasets': summaries, 'entriesDifferingBetweenDatasets': len(conflicts), 'outputs': {name: hashlib.sha256(value.encode()).hexdigest() for name, value in sorted(outputs.items())}}) + '\n'
    return outputs


def write_outputs(root, outputs, verify=False):
    root = Path(root).resolve()
    for name, value in outputs.items():
        path = root / name
        if not path.resolve().is_relative_to(root):
            raise ValueError('output escapes generation root')
        data = value.encode('utf-8')
        if verify:
            if not path.exists() or path.read_bytes() != data:
                raise ValueError('generated file differs: ' + str(path))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.exists() and path.read_bytes() == data:
            continue
        temporary = None
        try:
            with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + '.', delete=False) as f:
                temporary = Path(f.name); f.write(data); f.flush(); os.fsync(f.fileno())
            os.replace(temporary, path); temporary = None
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--harvest', type=Path, required=True)
    ap.add_argument('--dbc-export', type=Path, required=True)
    ap.add_argument('--essence-dbc', type=Path, required=True)
    ap.add_argument('--specs-dbc', type=Path, required=True)
    ap.add_argument('--chrclasses-dbc', type=Path, required=True)
    ap.add_argument('--out', type=Path, required=True)
    ap.add_argument('--verify', action='store_true', help='compare deterministic outputs without writing')
    args = ap.parse_args()
    sources = {}
    for source in (args.harvest.parent, args.dbc_export, args.essence_dbc.parent, args.specs_dbc.parent, args.chrclasses_dbc.parent):
        if args.out.resolve().is_relative_to(source.resolve()) or source.resolve().is_relative_to(args.out.resolve()):
            ap.error('output and source directories must be disjoint')
    groups = load_harvest(read_source(args.harvest, 'advancement.tsv', sources))
    dbcs = {name: json.loads(read_source(args.dbc_export / (name + '.json'), name + '.json', sources)) for name in ('entries', 'classes', 'tabs', 'categories')}
    essence = load_essence(read_source(args.essence_dbc, 'CharacterAdvancementEssence.dbc', sources))
    dbcs['specs'] = load_specs(read_source(args.specs_dbc, 'ChrSpecs.dbc', sources))
    dbcs['classIdentities'] = load_class_identities(read_source(args.chrclasses_dbc, 'ChrClasses.dbc', sources), dbcs['classes'])
    outputs = assemble(groups, dbcs, essence, sources)
    write_outputs(args.out, outputs, args.verify)
    manifest = json.loads(outputs['generation.json'])
    print(('Verified' if args.verify else 'Generated'), len(outputs), 'files;', len(groups), 'separate datasets;', len(essence), 'essence rows.')
    for d in manifest['datasets']:
        print(d['key'], d['entryCount'], 'entries;', d['bucketCount'], 'buckets;', d['nonPlaceholderBucketCount'], 'non-placeholder buckets')
    print('Entries with realm-dependent fields:', manifest['entriesDifferingBetweenDatasets'])


if __name__ == '__main__':
    main()
