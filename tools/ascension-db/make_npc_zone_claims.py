#!/usr/bin/env python3
"""Derive compact NPC-area claims from a preserved creature_spawn export."""
import argparse
import csv
import gzip
import io
import json
from pathlib import Path


def positive(value):
    value = str(value or '').strip()
    return str(int(value)) if value.isdigit() and int(value) > 0 else None


def claims(spawns, reviewed=None):
    found = {}
    with gzip.open(spawns, 'rt', encoding='utf-8', newline='') as stream:
        reader = csv.DictReader(stream)
        if not reader.fieldnames or not {'entry', 'area_id'} <= set(reader.fieldnames):
            raise ValueError('creature_spawn export is missing entry or area_id')
        for row in reader:
            npc, area = positive(row['entry']), positive(row['area_id'])
            if npc and area:
                found[(int(npc), int(area), 'direct-spawn-area', 'exiles-db-export-2026-09-13')] = None
    if reviewed:
        for row in json.loads(Path(reviewed).read_text(encoding='utf-8')):
            npc, area = positive(row.get('npc_id')), positive(row.get('area_id'))
            evidence, source = str(row.get('evidence', '')).strip(), str(row.get('source', '')).strip()
            if not npc or not area or not evidence or not source:
                raise ValueError('reviewed claims require positive npc_id/area_id, evidence and source')
            found[(int(npc), int(area), evidence, source)] = None
    return sorted(found)


def write(path, rows):
    buffer = io.StringIO(newline='')
    writer = csv.writer(buffer, delimiter='\t', lineterminator='\n')
    writer.writerow(['npc_id', 'area_id', 'evidence', 'source'])
    writer.writerows(rows)
    data = buffer.getvalue().encode()
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    with Path(path).open('wb') as raw:
        with gzip.GzipFile(filename='', fileobj=raw, mode='wb', mtime=0, compresslevel=9) as stream:
            stream.write(data)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--spawns', type=Path, required=True)
    parser.add_argument('--reviewed', type=Path)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    rows = claims(args.spawns, args.reviewed)
    write(args.out, rows)
    print(json.dumps({'claims': len(rows), 'npcs': len({row[0] for row in rows}), 'output': str(args.out)}))


if __name__ == '__main__':
    main()
