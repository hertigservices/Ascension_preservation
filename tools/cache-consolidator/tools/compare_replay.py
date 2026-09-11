"""Read-only comparison of an isolated Lua replay against a frozen public baseline.

This does not accept losses or publish data. The JSON report names every missing
artifact, decreased coverage counter and changed semantic digest for review.
"""
import argparse, hashlib, json, gzip, zlib
from pathlib import Path
import audit_columns, luaser


def leaves(value):
    if isinstance(value, luaser.Table):
        return sum(leaves(v) for v in value.array) + sum(leaves(v) for v in value.hash.values())
    if isinstance(value, dict):
        return sum(leaves(v) for v in value.values())
    if isinstance(value, list):
        return sum(leaves(v) for v in value)
    return 1


def inventory(root):
    root = Path(root)
    if not root.is_dir():
        raise ValueError('Comparison root does not exist')
    files, counts, unreadable = {}, {}, []
    expected = {'constant': {}, 'counts': {}}
    for path in sorted(root.rglob('*')):
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        files[rel] = {'bytes': None, 'sha256': None}
        try:
            data = path.read_bytes()
            files[rel] = {'bytes': len(data), 'sha256': hashlib.sha256(data).hexdigest()}
            if rel.endswith(('.tsv', '.tsv.gz', '.json', '.json.gz', '.lua')):
                (gzip.decompress(data) if rel.endswith('.gz') else data).decode('utf-8')
            if rel.endswith('.tsv') or rel.endswith('.tsv.gz'):
                audit_columns.check_tsv(str(path), rel, expected, unreadable, [], counts)
            elif rel.endswith('.json') or rel.endswith('.json.gz'):
                audit_columns.check_json(str(path), rel, expected, unreadable, [], counts)
                text = audit_columns.read_text(str(path))
                canonical = json.dumps(json.loads(text), sort_keys=True, separators=(',', ':'), ensure_ascii=False)
                files[rel]['semantic_sha256'] = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
            elif rel.endswith('.lua') and path.parent == root:
                globals_ = luaser.loads(data.decode('utf-8'))
                for name, value in globals_.items():
                    counts[rel + ':' + name + ':leaves'] = leaves(value)
                canonical = luaser.dumps(globals_)
                files[rel]['semantic_sha256'] = hashlib.sha256(canonical.encode('utf-8')).hexdigest()
        except (ValueError, TypeError, UnicodeError, OSError, EOFError, zlib.error):
            unreadable.append(('UNREADABLE', rel, 'comparison could not parse artifact'))
    return {'files': files, 'counts': counts,
            'unreadable': [list(v) for v in unreadable if v[0] == 'UNREADABLE'],
            'structural_findings': [list(v) for v in unreadable if v[0] != 'UNREADABLE']}


def compare(before, after):
    decreases = {key: {'before': value, 'after': after['counts'].get(key, 0)}
                 for key, value in before['counts'].items()
                 if key not in after['counts'] or after['counts'][key] < value}
    missing = sorted(before['files'].keys() - after['files'].keys())
    changed = sorted(key for key in before['files'].keys() & after['files'].keys()
                     if before['files'][key].get('semantic_sha256', before['files'][key]['sha256'])
                     != after['files'][key].get('semantic_sha256', after['files'][key]['sha256']))
    structures = after.get('structural_findings', [])
    new_structures = [v for v in structures if v not in before.get('structural_findings', [])]
    return {'missing_artifacts': missing, 'decreased_counts': decreases,
            'changed_artifacts_requiring_review': changed,
            'added_artifacts': sorted(after['files'].keys() - before['files'].keys()),
            'unreadable': before['unreadable'] + after['unreadable'],
            'no_coverage_loss': not (missing or decreases or before['unreadable'] or after['unreadable']),
            'new_structural_findings': new_structures,
            'review_required': bool(changed or missing or decreases or new_structures or after['files'].keys() - before['files'].keys() or before['unreadable'] or after['unreadable']),
            'publication_allowed': False}


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--baseline', required=True)
    ap.add_argument('--candidate', required=True)
    ap.add_argument('--report', required=True)
    args = ap.parse_args()
    before, after = inventory(args.baseline), inventory(args.candidate)
    result = compare(before, after)
    report = Path(args.report)
    report.parent.mkdir(parents=True, exist_ok=True)
    report.write_text(json.dumps({'comparison': result, 'baseline': before, 'candidate': after}, indent=2), encoding='utf-8')
    print(json.dumps({k: len(v) if isinstance(v, (list, dict)) else v for k, v in result.items()}))
    return 1 if result['review_required'] else 0


if __name__ == '__main__':
    raise SystemExit(main())
