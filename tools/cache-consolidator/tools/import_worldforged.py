"""Preserve a reviewed snapshot of Tareksoh/Worldforged-data as a supplemental set.

    python -B tools/import_worldforged.py import CHECKOUT --commit SHA \
        --cache union/itemcache.tsv.gz --baseline-revision REV --output supplemental/worldforged
    python -B tools/import_worldforged.py verify supplemental/worldforged/SHA

Requires the Python standard library and the git executable.

Blobs come out of git itself at the pinned commit, never from the working tree.
A local edit, or an untracked file beside the checkout, therefore cannot be
published under the upstream commit's name. The verifier recomputes every file's
git blob id, so the published bytes provably are the upstream commit's bytes.

Only the datasets under data/ are republished. The author's tools and prose are
listed as omitted, with the reason: they embed the author's local paths. The
author gave permission to republish (relayed by James Hertig, 2026-09-11); the
repository itself carries no licence file. See docs/WORLDFORGED.md.
"""
import argparse
import csv
import gzip
import hashlib
import io
import json
import re
import subprocess
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1
SOURCE_URL = 'https://github.com/Tareksoh/Worldforged-data'
PERMISSION = ("Republished with the author's permission, relayed by James Hertig on 2026-09-11. "
              "The upstream repository carries no licence file.")
PUBLISH = re.compile(r'^data/[^\x00]+\.(csv|json)$')
OMIT_REASONS = (
    ('tools/', "code: the author's generators and LootCollector decoder; read them upstream"),
    ('docs/', "the author's measurement report; it embeds the author's local paths"),
    ('README.md', "the author's description of the files; it embeds local paths. Summarised in our README"),
    ('PRIVACY-GATE.md', "the author's own privacy-gate record; read it upstream"),
    ('.gitignore', 'repository housekeeping'),
)
PATTERNS = {
    'email': re.compile(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}'),
    'player GUID': re.compile(r'0x[0-9A-Fa-f]{16}'),
    'account path': re.compile(r'WTF[\\/]+Account[\\/]', re.I),
    'local path': re.compile(r'\b[A-Z]:[\\/]', re.I),
}
# The columns that name an item, per republished CSV, for the comparison with
# captured data. Named, never guessed: a file that lacks them fails the import.
ITEM_COLUMNS = {'data/wf_pins_union.csv': ('item_id', 'item_name'),
                'data/wf_items_union.csv': ('item_id', 'item_name'),
                'data/exiles_wf_ladder.csv': ('item_id', 'name'),
                'data/provenance/dump_zips_wf_items.csv': ('item_id', 'item_name'),
                'data/provenance/dump_zips_wf_locations.csv': ('item_id', 'item_name')}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_blob_id(data):
    return hashlib.sha1(b'blob %d\x00' % len(data) + data).hexdigest()


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')


def git(checkout, *args):
    r = subprocess.run(['git', '-C', str(checkout), *args], capture_output=True)
    require(r.returncode == 0, 'git ' + ' '.join(args[:2]) + ' failed: '
            + r.stderr.decode('utf-8', 'replace').strip()[:200])
    return r.stdout


def commit_files(checkout, commit):
    """[(path, blob id)] for every file in the commit."""
    out = []
    for entry in git(checkout, 'ls-tree', '-r', '-z', '--full-tree', commit).split(b'\x00'):
        if entry:
            meta, path = entry.split(b'\t', 1)
            _mode, kind, blob = meta.decode().split(' ')
            require(kind == 'blob', 'Unexpected tree entry: ' + path.decode('utf-8', 'replace'))
            out.append((path.decode('utf-8'), blob))
    return out


def read_blob(checkout, blob):
    data = git(checkout, 'cat-file', 'blob', blob)
    require(git_blob_id(data) == blob, 'A blob does not match its git id')
    return data


def screen(text, label):
    for name, rx in PATTERNS.items():
        require(not rx.search(text), f'{label}: refusing {name}-shaped content')


def summarise(path, text):
    if path.endswith('.csv'):
        rows = list(csv.reader(io.StringIO(text)))
        require(bool(rows), path + ': empty CSV')
        return dict(kind='csv', columns=rows[0], rows=len(rows) - 1)
    value = json.loads(text)
    return dict(kind='json', top_level=sorted(value) if isinstance(value, dict) else type(value).__name__)


def omitted_reason(path):
    for prefix, reason in OMIT_REASONS:
        if path == prefix or path.startswith(prefix):
            return reason
    return 'not one of the datasets under data/'


def cache_names(raw):
    """{entry: name} from a captured union itemcache.tsv.gz, by column name."""
    text = gzip.decompress(raw).decode('utf-8')
    reader = csv.reader(io.StringIO(text), delimiter='\t', quoting=csv.QUOTE_NONE)
    head = next(reader)
    require('entry' in head and 'name' in head, 'Baseline lacks entry/name columns')
    e, n = head.index('entry'), head.index('name')
    return {int(r[e]): r[n] for r in reader if r and r[e].isdigit()}


def item_refs(files):
    """{path: [(item id, name or None)]} for every republished file that names items."""
    refs = {}
    for path, text in files.items():
        if path in ITEM_COLUMNS:
            id_col, name_col = ITEM_COLUMNS[path]
            reader = csv.DictReader(io.StringIO(text))
            require(id_col in reader.fieldnames and name_col in reader.fieldnames,
                    f'{path}: expected columns {id_col} and {name_col}')
            refs[path] = [(int(r[id_col]), r[name_col]) for r in reader]
        elif path.endswith('/wf_upgrade_chains.json'):
            data = json.loads(text)['data']
            refs[path] = [(int(k), None) for k in data] + [(int(v), None) for d in data.values() for v in d.values()]
        elif path.endswith('/lootcollector_wf.json'):
            refs[path] = [(int(r['id']), r.get('name')) for r in json.loads(text)['items']]
    return refs


def compare(files, captured, baseline_hash):
    report = dict(baseline_sha256=baseline_hash,
                  method='item id presence and case-insensitive names only; no stat verification', files={})
    for path, refs in sorted(item_refs(files).items()):
        ids = sorted({i for i, _ in refs})
        missing = [i for i in ids if i not in captured]
        conflicts = sorted({(i, n, captured[i]) for i, n in refs
                            if n and i in captured and n.casefold() != captured[i].casefold()})
        report['files'][path] = dict(distinct_ids=len(ids), captured=len(ids) - len(missing), missing_ids=missing,
                                     name_conflicts=[dict(item_id=i, source_name=n, captured_name=c)
                                                     for i, n, c in conflicts])
    return report


def gz(data):
    return gzip.compress(data, compresslevel=9, mtime=0)


def verify(folder):
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_bytes())
    require(manifest.get('schema_version') == SCHEMA_VERSION, 'Unknown snapshot schema')
    expected = {p + '.gz' for p in manifest['files']} | {'comparison.json.gz'}
    require(set(manifest['artifacts']) == expected, 'Unexpected artifact list')
    present = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    require(present == expected | {'manifest.json'}, 'Unexpected snapshot files')
    files = {}
    for name, meta in manifest['artifacts'].items():
        blob = (folder / name).read_bytes()
        require(sha256(blob) == meta['sha256'] and len(blob) == meta['bytes'], 'Artifact mismatch: ' + name)
    for path, meta in manifest['files'].items():
        data = gzip.decompress((folder / (path + '.gz')).read_bytes())
        require(sha256(data) == meta['sha256'] and len(data) == meta['bytes'], 'Content mismatch: ' + path)
        require(git_blob_id(data) == meta['git_blob'], 'Not the upstream bytes: ' + path)
        text = data.decode('utf-8')
        screen(text, path)
        require(summarise(path, text) == meta['summary'], 'Summary mismatch: ' + path)
        files[path] = text
    comparison = json.loads(gzip.decompress((folder / 'comparison.json.gz').read_bytes()))
    require(set(comparison['files']) == set(item_refs(files)), 'Comparison does not cover the item files')
    screen((folder / 'manifest.json').read_text(encoding='utf-8'), 'manifest.json')
    return manifest


def ingest(checkout, output, commit, cache, baseline_revision):
    require(re.fullmatch(r'[0-9a-f]{40}', commit or '') is not None, 'Expected a full 40-character commit id')
    require(re.fullmatch(r'[0-9a-f]{40}', baseline_revision or '') is not None, 'Expected a full baseline commit id')
    require(git(checkout, 'cat-file', '-t', commit).strip() == b'commit', 'Not a commit: ' + commit)
    author, date, subject = git(checkout, 'show', '-s', '--format=%an%x00%aI%x00%s', commit).decode('utf-8').rstrip('\n').split('\x00')
    entries = commit_files(checkout, commit)
    chosen = [(p, b) for p, b in entries if PUBLISH.match(p)]
    require(bool(chosen), 'No datasets under data/ in that commit')
    files, meta = {}, {}
    for path, blob in chosen:
        data = read_blob(checkout, blob)
        text = data.decode('utf-8')
        screen(text, path)
        files[path] = text
        meta[path] = dict(bytes=len(data), sha256=sha256(data), git_blob=blob, summary=summarise(path, text))
    baseline = Path(cache).read_bytes()
    comparison = compare(files, cache_names(baseline), sha256(baseline))
    source = dict(url=SOURCE_URL, commit=commit, author=author, date=date, subject=subject, permission=PERMISSION)
    target = Path(output) / commit
    if target.exists():
        old = verify(target)
        require(old['source'] == source and old['baseline']['sha256'] == sha256(baseline)
                and old['baseline']['revision'] == baseline_revision,
                'Snapshot already exists with different provenance or baseline; use a separate output root')
        return target
    manifest = dict(schema_version=SCHEMA_VERSION, source=source,
                    baseline=dict(sha256=sha256(baseline), revision=baseline_revision, format='union itemcache.tsv.gz'),
                    files=meta, omitted=[dict(path=p, reason=omitted_reason(p)) for p, _ in entries if (p, _) not in chosen],
                    semantics=dict(kind='supplemental third-party dataset',
                                   authority='the author\'s derived measurements; captured data remains authoritative',
                                   pins='looter position at pickup time, not the spawn point; no Z',
                                   ladder='rung labels differ between the two ladder sources; key on ids and item level'),
                    artifacts={})
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.worldforged-build-', dir=target.parent) as tmp:
        staging = Path(tmp) / 'snapshot'
        for path, text in files.items():
            dest = staging / (path + '.gz')
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(gz(text.encode('utf-8')))
        (staging / 'comparison.json.gz').write_bytes(gz(encode(comparison) + b'\n'))
        for p in sorted(x for x in staging.rglob('*') if x.is_file()):
            blob = p.read_bytes()
            manifest['artifacts'][p.relative_to(staging).as_posix()] = dict(bytes=len(blob), sha256=sha256(blob))
        (staging / 'manifest.json').write_bytes(json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=1).encode('utf-8') + b'\n')
        verify(staging)
        staging.rename(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import', help='Validate and atomically create an immutable snapshot')
    imp.add_argument('checkout', type=Path, help='A git checkout that contains the commit')
    imp.add_argument('--commit', required=True, help='Full id of the upstream commit reviewed for publication')
    imp.add_argument('--output', type=Path, required=True)
    imp.add_argument('--cache', type=Path, required=True, help='Captured union itemcache.tsv.gz, used only for comparison')
    imp.add_argument('--baseline-revision', required=True, help='Full data-repository commit the cache came from')
    check = sub.add_parser('verify', help='Check hashes, upstream blob ids and the comparison')
    check.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    try:
        if args.command == 'import':
            print('Preserved and verified: ' + str(ingest(args.checkout, args.output, args.commit, args.cache,
                                                          args.baseline_revision)))
        else:
            m = verify(args.snapshot)
            print(f"Verified {len(m['files'])} files from {m['source']['url']} @ {m['source']['commit'][:12]}")
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, 'Import refused: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
