#!/usr/bin/env python3
"""Build bounded reward-item identity evidence from verified public cache views."""
import argparse
import csv
import gzip
import hashlib
import io
import json
from pathlib import Path


def rows(path, expected):
    raw = path.read_bytes()
    if len(raw) != expected['bytes'] or hashlib.sha256(raw).hexdigest() != expected['sha256']:
        raise ValueError('Public source hash mismatch: ' + path.name)
    yield from csv.DictReader(io.StringIO(gzip.decompress(raw).decode('utf-8')), delimiter='\t', quoting=csv.QUOTE_NONE)


def build(manifest, root, revision):
    result = {'schema': 'ascension-quest-item-evidence-1', 'data_revision': revision,
              'cache_snapshot': manifest['snapshot'], 'sources': {}}
    for name, metadata in sorted(manifest['files'].items()):
        if not name.endswith('/questcache.tsv.gz'):
            continue
        needed = set()
        for row in rows(root / name, metadata):
            for field, value in row.items():
                if field.startswith(('RewardItem', 'RewardChoiceItemId')) and value != '0':
                    needed.add(value)
        item_path = name.replace('/questcache.tsv.gz', '/itemcache.tsv.gz')
        if item_path not in manifest['files']:
            continue
        items = {}
        for row in rows(root / item_path, manifest['files'][item_path]):
            if row['entry'] in needed:
                if row['entry'] in items:
                    raise ValueError('Duplicate item identity in one published view')
                items[row['entry']] = [row['name'], row['class'], row['subclass']]
        result['sources'][name] = {'path': item_path, 'sha256': manifest['files'][item_path]['sha256'], 'items': items}
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--data', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--out', type=Path, required=True)
    args = parser.parse_args()
    value = build(json.loads(args.manifest.read_text()), args.data, args.revision)
    with args.out.open('xb') as output:
        output.write(gzip.compress(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(',', ':')).encode(), mtime=0))
    print(json.dumps({'source_views': len(value['sources']), 'item_observations': sum(len(v['items']) for v in value['sources'].values()), 'bytes': args.out.stat().st_size}))
