"""Preserve a reviewed db.exil.es (coa-db) offline mirror as a supplemental catalog.

Standard library only. See docs/EXILES-DB.md. No realm connections or WDB writes.

The mirror is far too large to republish verbatim, so this catalog preserves the
parsed records, the crawler's own page index, the original bytes of the pages
whose markup is the data (talent trees and class pages), and a SHA-256 index of
every mirrored file. That index is what lets a reader with the upstream archive
prove they hold the same bytes these records were parsed from.
"""
import argparse
import collections
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
import csv
import gzip
import hashlib
import io
import json
import re
import sqlite3
import sys
import tarfile
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import exiles_parse as parse_html

SCHEMA_VERSION = 1
SOURCE_URL = 'https://db.exil.es/'
SOURCE_NAME = 'coa-db'
MIRROR_ROOT = 'ExilesOfflineDB'
PLACEHOLDER = re.compile(r'^(?:Item|Spell|NPC|Object|Quest|Achievement)\s+#\d+$')
NAMED_SUFFIX = re.compile(r'\s+#\d+$')

# Page types written to their own artifact; everything else shares entities.jsonl.
STREAMS = {
    'spell': 'spells.jsonl.gz',
    'item': 'items.jsonl.gz',
    'npc': 'npcs.jsonl.gz',
    'quest': 'quests.jsonl.gz',
    'achievement': 'achievements.jsonl.gz',
    'tree': 'talents.jsonl.gz',
    'change-log': 'changes.jsonl.gz',
    'history': 'histories.jsonl.gz',
}
OTHER_STREAM = 'entities.jsonl.gz'
# Pages whose markup carries CoA-only structure that no other source records.
PRESERVED_TYPES = ('tree', 'class')
# Captured baselines this catalog is compared against, by mirror page type, with
# the column holding the entity's name. These are named explicitly and required
# to exist: questcache calls it `Title` and puts it at column 65, so a positional
# guess silently compares against `Method` and reports every quest as a conflict.
BASELINES = {'item': ('itemcache', 'name'), 'npc': ('creaturecache', 'name'),
             'gameobject': ('gameobjectcache', 'name'), 'quest': ('questcache', 'Title')}

# The upstream API specification carries a contact email in `info.contact`. The
# field map it documents is worth preserving; a named person's address is not, so
# the address is replaced in place and the unmodified file's hash is recorded.
REDACT_EMAIL = re.compile(rb'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}')
REDACTION = b'<redacted: contact address>'

# A named row rather than a bare tuple: the column order is also the SQLite
# insert order, and every time it changed by hand a positional index elsewhere
# silently started reading a different field.
Entity = collections.namedtuple('Entity', 'route_key page_type page_key name url artifact '
                                          'is_placeholder comparison_status captured_name numeric_id')

PATTERNS = {
    'email': re.compile(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}'),
    'player GUID': re.compile(r'0x[0-9A-Fa-f]{16}'),
    'account path': re.compile(r'WTF[\\/]+Account[\\/]', re.I),
    'local path': re.compile(r'\b[A-Z]:[\\/]', re.I),
}
# Addresses that belong to nobody, written into the game's own text. The GM
# "BAN Hammer" spell tells a banned player to appeal to a mailbox at a
# non-existent TLD. Allowed as these exact strings and nothing wider: any other
# address, including another one at the same domain, still fails.
ALLOWED_LITERALS = frozenset({'techbot@gnome.mail'})

SCHEMA_SQL = '''
        PRAGMA user_version=1;
        CREATE TABLE source (source_sha256 TEXT PRIMARY KEY, metadata_json TEXT NOT NULL);
        -- Keyed on the route, which is unique by construction. `page_key` is
        -- not: paginated and filtered `listing` routes all share one key, so
        -- keying on (page_type, page_key) rejects the very rows it should hold.
        CREATE TABLE entity (route_key TEXT PRIMARY KEY, page_type TEXT NOT NULL,
            page_key TEXT NOT NULL, name TEXT NOT NULL, url TEXT NOT NULL,
            artifact TEXT NOT NULL, is_placeholder INTEGER NOT NULL,
            comparison_status TEXT, captured_name TEXT, numeric_id INTEGER);
        CREATE INDEX entity_page ON entity(page_type, page_key);
        CREATE TABLE talent (tree_key TEXT NOT NULL, ordinal INTEGER NOT NULL,
            spell_id INTEGER, name TEXT, icon TEXT, grid_row INTEGER,
            grid_column INTEGER, max_rank INTEGER,
            PRIMARY KEY(tree_key, ordinal));
        CREATE TABLE tree (tree_key TEXT PRIMARY KEY, name TEXT NOT NULL,
            declared_talents INTEGER, parsed_talents INTEGER,
            grid_columns INTEGER, grid_rows INTEGER);
        CREATE INDEX entity_name ON entity(name COLLATE NOCASE);
        CREATE INDEX entity_status ON entity(comparison_status);
        CREATE INDEX entity_numeric ON entity(numeric_id);
        CREATE INDEX talent_spell ON talent(spell_id);
        '''


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha(data):
    return hashlib.sha256(data).hexdigest()


def sha_file(path):
    digest = hashlib.sha256()
    with open(path, 'rb') as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b''):
            digest.update(chunk)
    return digest.hexdigest()


def encode(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'), ensure_ascii=False).encode()


def parse(raw):
    return json.loads(raw.decode('utf-8-sig') if isinstance(raw, bytes) else raw)


def screen(text):
    for label, pattern in PATTERNS.items():
        for found in pattern.finditer(text):
            if found.group(0) in ALLOWED_LITERALS:
                continue
            require(False, 'Personal data indicator (%s): %r' % (label, found.group(0)))


def gz_write(path, data):
    path.write_bytes(gzip.compress(data, compresslevel=9, mtime=0))


class Stream:
    """A deterministic gzipped JSON Lines writer.

    Records go straight to the file rather than accumulating in memory: the
    spell and item streams together run to well over a gigabyte uncompressed.
    `mtime=0` keeps a rebuild from the same mirror byte-identical.
    """

    def __init__(self, path):
        self.path = path
        self.count = 0
        # Wrap an explicit file object so the gzip header carries no stored
        # filename, matching gzip.compress() for the other artifacts.
        self.raw = open(path, 'wb')
        self.handle = gzip.GzipFile(fileobj=self.raw, mode='wb', compresslevel=9, mtime=0)

    def write(self, record):
        self.handle.write(encode(record) + b'\n')
        self.count += 1

    def close(self):
        self.handle.close()
        self.raw.close()
        return dict(count=self.count)


def numeric(key):
    try:
        return int(key)
    except (TypeError, ValueError):
        return None


def is_placeholder(name):
    """True when the site had no name for the entity and rendered a stub."""
    return bool(PLACEHOLDER.match(name.strip()))


def display_name(name):
    """The entity's name with the site's trailing `#id` disambiguator removed."""
    return NAMED_SUFFIX.sub('', name.strip()).strip()


def read_site(site_db):
    """Routes and the crawler's search index, keyed by route."""
    with closing(sqlite3.connect(site_db.as_uri() + '?mode=ro', uri=True)) as db:
        routes = {}
        for route_key, url, local_path, page_type, page_key in db.execute(
                'SELECT route_key,url,local_path,page_type,page_key FROM route'):
            routes[route_key] = dict(url=url, path=local_path.replace('\\', '/'),
                                     type=page_type, key=page_key)
        names = {}
        for name, page_type, page_key, route_key in db.execute(
                'SELECT name,page_type,page_key,route_key FROM search_index'):
            names[route_key] = name
        meta = dict(db.execute('SELECT key,value FROM meta'))
    return routes, names, meta


def index_mirror(root, workers=16):
    """Every mirrored file with its size and SHA-256, sorted by path.

    Half a million small files is the slow part of an import, and the cost is
    per-file latency -- on-access virus scanning and directory metadata -- not
    hashing throughput. Reads are issued from a thread pool because that
    latency overlaps; the result is re-sorted so the index stays deterministic
    regardless of completion order.
    """
    paths = [p for p in root.rglob('*') if p.is_file()]

    def measure(path):
        return (path.relative_to(root).as_posix(), path.stat().st_size, sha_file(path))

    with ThreadPoolExecutor(max_workers=workers) as pool:
        entries = list(pool.map(measure, paths))
    entries.sort()
    return entries


def read_pages(root, routes, keys, workers=16, ahead=2000):
    """Yield `(route_key, bytes)` in route order, reading ahead on a thread pool.

    Parsing is fast next to opening half a million files, so reads are
    overlapped. The read-ahead is bounded: submitting all of them at once would
    hold the whole mirror in memory. A route whose file cannot be read yields
    None rather than raising, so one absent page cannot end the import.
    """
    def read(route_key):
        try:
            return route_key, (root / routes[route_key]['path']).read_bytes()
        except OSError:
            return route_key, None

    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0, len(keys), ahead):
            for result in pool.map(read, keys[start:start + ahead]):
                yield result


def load_baseline(cache_dir, cache, column):
    """`entry -> name` from a captured union export, or None when absent.

    The name column is named by the caller and must be present. Falling back to
    a positional guess is how a comparison ends up measuring the wrong field
    and reporting thousands of confident, wrong conflicts.
    """
    path = cache_dir / (cache + '.tsv.gz')
    if not path.exists():
        return None, None
    names = {}
    raw = path.read_bytes()
    with gzip.open(io.BytesIO(raw), 'rt', encoding='utf-8', newline='') as handle:
        reader = csv.DictReader(handle, delimiter='\t')
        require(column in (reader.fieldnames or []),
                'Baseline %s has no %r column; found %s' %
                (cache, column, ', '.join((reader.fieldnames or [])[:8])))
        for row in reader:
            entry = numeric(row.get('entry'))
            if entry is not None:
                names[entry] = (row.get(column) or '').strip()
    return names, sha(raw)


def classify(page_type, page_key, name, baselines):
    """Compare one mirror entity with the captured baseline for its type."""
    declared = BASELINES.get(page_type)
    cache = declared[0] if declared else None
    entry = numeric(page_key)
    if not cache or entry is None or baselines.get(cache) is None:
        return None, None
    captured = baselines[cache].get(entry)
    if captured is None:
        return 'candidate-missing', None
    if is_placeholder(name):
        return 'unnamed-in-mirror', captured
    if display_name(name).casefold() == captured.casefold():
        return 'name-match', captured
    return 'name-conflict', captured


def build_database(path, source_meta, entities, trees):
    """Entities, trees and talents only.

    The mirrored-file index deliberately lives in `mirror.index.tsv.gz` alone.
    Carrying half a million high-entropy hashes here as well would roughly
    double the published catalog to say the same thing twice.
    """
    with closing(sqlite3.connect(path)) as db, db:
        db.executescript(SCHEMA_SQL)
        db.execute('INSERT INTO source VALUES (?,?)',
                   (source_meta['archive_sha256'], encode(source_meta).decode()))
        db.executemany('INSERT INTO entity VALUES (?,?,?,?,?,?,?,?,?,?)', entities)
        db.executemany('INSERT INTO tree VALUES (?,?,?,?,?,?)',
                       [(t['key'], t['name'], t['declared_talents'], len(t['talents']),
                         t['columns'], t['rows']) for t in trees])
        db.executemany('INSERT INTO talent VALUES (?,?,?,?,?,?,?,?)',
                       [(t['key'], i, c['spell_id'], c['name'], c['icon'],
                         c['row'], c['column'], c['max_rank'])
                        for t in trees for i, c in enumerate(t['talents'])])
        require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'SQLite integrity failure')


def database_schema(db):
    return db.execute('SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name').fetchall()


def comparison_report(entities, baselines, baseline_hashes, baseline_revision):
    """Named rows, not just counts: every conflict and every missing candidate."""
    conflicts, missing, unnamed = [], [], []
    covered = collections.Counter()
    for entity in entities:
        if entity.comparison_status is None:
            continue
        covered[entity.page_type] += 1
        row = dict(page_type=entity.page_type, page_key=entity.page_key,
                   numeric_id=entity.numeric_id, mirror_name=entity.name,
                   captured_name=entity.captured_name)
        if entity.comparison_status == 'name-conflict':
            conflicts.append(row)
        elif entity.comparison_status == 'candidate-missing':
            missing.append(row)
        elif entity.comparison_status == 'unnamed-in-mirror':
            unnamed.append(row)
    only_captured = {}
    for page_type, (cache, _column) in sorted(BASELINES.items()):
        if baselines.get(cache) is None:
            continue
        seen = {e.numeric_id for e in entities
                if e.page_type == page_type and e.numeric_id is not None}
        only_captured[page_type] = len(set(baselines[cache]) - seen)
    return dict(
        method='ID association and case-insensitive names only; no stat, source or '
               'probability verification. The mirror\'s trailing "#id" disambiguator is '
               'removed before comparing, and a stub name is reported as unnamed rather '
               'than as a conflict.',
        baselines={cache: dict(sha256=baseline_hashes[cache],
                               entries=len(baselines[cache] or {}))
                   for cache in sorted(baseline_hashes) if baselines.get(cache) is not None},
        baseline_revision=baseline_revision,
        compared=dict(sorted(covered.items())),
        captured_ids_absent_from_mirror=only_captured,
        counts=dict(name_conflicts=len(conflicts), candidate_missing=len(missing),
                    unnamed_in_mirror=len(unnamed)),
        name_conflicts=sorted(conflicts, key=lambda r: (r['page_type'], r['numeric_id'])),
        candidate_missing=sorted(missing, key=lambda r: (r['page_type'], r['numeric_id'])),
        unnamed_in_mirror=sorted(unnamed, key=lambda r: (r['page_type'], r['numeric_id'])))


def ingest(mirror, site_db, output, expected_sha256, archive, cache_dir, baseline_revision=None):
    mirror = Path(mirror)
    root = mirror / MIRROR_ROOT if (mirror / MIRROR_ROOT).is_dir() else mirror
    require(root.is_dir(), 'Mirror root not found: %s' % root)
    archive_hash = sha_file(archive)
    require(archive_hash == expected_sha256,
            'Archive hash %s does not match the reviewed %s' % (archive_hash, expected_sha256))

    routes, names, site_meta = read_site(Path(site_db))
    mirror_index = index_mirror(root)
    index_by_path = {path: (size, digest) for path, size, digest in mirror_index}

    baselines, baseline_hashes = {}, {}
    for cache, column in sorted(set(BASELINES.values())):
        loaded, digest = load_baseline(Path(cache_dir), cache, column)
        baselines[cache], baseline_hashes[cache] = loaded, digest

    output = Path(output)
    output.mkdir(parents=True, exist_ok=True)
    streams = {name: Stream(output / name) for name in set(STREAMS.values()) | {OTHER_STREAM}}
    entities, trees, preserved = [], [], []
    per_type = collections.Counter()
    parsed_pages = missing_files = 0

    html_routes = [key for key in sorted(routes) if routes[key]['path'].endswith('.html')]
    for route_key, raw in read_pages(root, routes, html_routes):
        route = routes[route_key]
        if raw is None:
            missing_files += 1
            continue
        page = raw.decode('utf-8', errors='replace')
        page_type, page_key = route['type'], route['key']
        if page_key.endswith('/history'):
            page_type = 'history'
        name = names.get(route_key, '')
        record = parse_html.parse_page(page_type, page_key, page, name=name or None)
        record['url'] = route['url']
        record['source_path'] = route['path']
        record['source_sha256'] = index_by_path.get(route['path'], (None, None))[1]
        stream_name = STREAMS.get(record['type'], OTHER_STREAM)
        streams[stream_name].write(record)
        parsed_pages += 1
        per_type[record['type']] += 1

        if record['type'] == 'tree':
            trees.append(record)
        if route['type'] in PRESERVED_TYPES:
            preserved.append((route['path'], raw))
        if page_type not in ('history', 'change-log') and name:
            status, captured = classify(page_type, page_key, name, baselines)
            entities.append(Entity(route_key=route_key, page_type=page_type, page_key=page_key,
                                   name=name, url=route['url'], artifact=stream_name,
                                   is_placeholder=int(is_placeholder(name)),
                                   comparison_status=status, captured_name=captured,
                                   numeric_id=numeric(page_key)))

    artifacts = {name: stream.close() for name, stream in sorted(streams.items())}

    index_buffer = io.StringIO()
    writer = csv.writer(index_buffer, delimiter='\t', lineterminator='\n')
    writer.writerow(['path', 'bytes', 'sha256'])
    writer.writerows(mirror_index)
    gz_write(output / 'mirror.index.tsv.gz', index_buffer.getvalue().encode())

    name_buffer = io.StringIO()
    writer = csv.writer(name_buffer, delimiter='\t', lineterminator='\n')
    writer.writerow(['page_type', 'page_key', 'name', 'url'])
    for route_key in sorted(names):
        route = routes.get(route_key)
        if route:
            writer.writerow([route['type'], route['key'], names[route_key], route['url']])
    gz_write(output / 'names.tsv.gz', name_buffer.getvalue().encode())

    spec = root / 'mirror' / 'db.exil.es' / 'api' / 'openapi.json'
    require(spec.exists(), 'API specification missing from the mirror')
    spec_raw = spec.read_bytes()
    spec_published, redactions = REDACT_EMAIL.subn(REDACTION, spec_raw)
    gz_write(output / 'openapi.json.gz', spec_published)
    spec_meta = dict(sha256=sha(spec_raw), bytes=len(spec_raw), redacted_addresses=redactions,
                     declared_license=(parse(spec_raw).get('info', {})
                                       .get('license', {}).get('name')))

    # Original bytes for the pages whose markup is the data.
    tar_buffer = io.BytesIO()
    with tarfile.open(fileobj=tar_buffer, mode='w') as tar:
        for path, raw in sorted(preserved):
            info = tarfile.TarInfo(path)
            info.size = len(raw)
            info.mtime = 0
            info.uid = info.gid = 0
            info.uname = info.gname = ''
            tar.addfile(info, io.BytesIO(raw))
    gz_write(output / 'structural-pages.tar.gz', tar_buffer.getvalue())

    source_meta = dict(name=SOURCE_NAME, url=SOURCE_URL,
                       archive_sha256=archive_hash, archive_bytes=archive.stat().st_size,
                       archive_name=archive.name,
                       upstream='https://github.com/Duff-SPP/AcensionOfflineDatabase',
                       crawler_built_at=site_meta.get('built_at'),
                       api_specification=spec_meta,
                       attribution='Offline mirror of db.exil.es ("coa-db"), published by '
                                   'Duff-SPP; the site is attributed to the Project Ascension '
                                   'guild Exiles. Not independently authenticated.')

    report = comparison_report(entities, baselines, baseline_hashes, baseline_revision)
    gz_write(output / 'comparison.json.gz', encode(report))

    with tempfile.TemporaryDirectory(prefix='exiles-build-') as tmp:
        dbpath = Path(tmp) / 'catalog.sqlite'
        build_database(dbpath, source_meta, entities, trees)
        gz_write(output / 'catalog.sqlite.gz', dbpath.read_bytes())

    counts = dict(mirror_files=len(mirror_index),
                  mirror_bytes=sum(size for _, size, _ in mirror_index),
                  routes=len(routes), indexed_names=len(names),
                  parsed_pages=parsed_pages, missing_files=missing_files,
                  pages_by_type=dict(sorted(per_type.items())),
                  entities=len(entities),
                  placeholders=sum(e.is_placeholder for e in entities),
                  trees=len(trees), talents=sum(len(t['talents']) for t in trees),
                  preserved_pages=len(preserved),
                  comparison=report['counts'])

    manifest = dict(schema_version=SCHEMA_VERSION, source=source_meta, counts=counts,
                    semantics=dict(
                        kind='supplemental third-party mirror of a community database site',
                        game='Conquest of Azeroth (source attribution)',
                        authority='rendered website values; captured WDB data remains authoritative',
                        values='display strings as rendered, plus the raw tooltip marker codes; '
                               'no numeric field is reconstructed from prose',
                        drop_rates='the site\'s own stated percentages, not independently observed',
                        completeness='the crawl recorded 39,858 asset fetch failures, so the '
                                     'mirrored icon set is incomplete',
                        original_bytes='talent tree and class pages only; the remaining mirrored '
                                       'bytes are identified by SHA-256 in mirror.index.tsv.gz'),
                    artifacts={})
    for path in sorted(output.iterdir()):
        if path.name == 'manifest.json':
            continue
        blob = path.read_bytes()
        manifest['artifacts'][path.name] = dict(bytes=len(blob), sha256=sha(blob))
    for name, info in artifacts.items():
        manifest['artifacts'][name]['records'] = info['count']
    (output / 'manifest.json').write_bytes(encode(manifest) + b'\n')
    return manifest


def verify(folder):
    """Check artifact hashes, internal agreement and personal-data indicators.

    The source mirror is too large to republish, so this cannot re-derive the
    records from original bytes the way the BisBeard importer does. It proves
    that the published artifacts are internally consistent and unmodified, and
    that every parsed record names the mirrored file and hash it came from.
    """
    folder = Path(folder)
    manifest = parse((folder / 'manifest.json').read_bytes())
    require(manifest['schema_version'] == SCHEMA_VERSION, 'Unknown catalog schema')
    require({p.name for p in folder.iterdir()} == set(manifest['artifacts']) | {'manifest.json'},
            'Unexpected snapshot files')
    for name, metadata in sorted(manifest['artifacts'].items()):
        blob = (folder / name).read_bytes()
        require(sha(blob) == metadata['sha256'] and len(blob) == metadata['bytes'],
                'Artifact mismatch: ' + name)

    index = {}
    with gzip.open(folder / 'mirror.index.tsv.gz', 'rt', encoding='utf-8', newline='') as handle:
        for row in csv.DictReader(handle, delimiter='\t'):
            index[row['path']] = row['sha256']
    require(len(index) == manifest['counts']['mirror_files'], 'Mirror index row count mismatch')

    seen = collections.Counter()
    trees = 0
    for name in sorted(set(STREAMS.values()) | {OTHER_STREAM}):
        records = [parse(line) for line in
                   gzip.decompress((folder / name).read_bytes()).splitlines()]
        require(len(records) == manifest['artifacts'][name]['records'],
                'Record count mismatch: ' + name)
        for record in records:
            require(record['source_sha256'] == index.get(record['source_path']),
                    'Record does not match the mirror index: %s' % record['source_path'])
            seen[record['type']] += 1
            if record['type'] == 'tree':
                trees += 1
                require(len(record['talents']) == record['declared_talents'],
                        'Talent count disagrees with the tree page: %s' % record['key'])
    require(seen == collections.Counter(manifest['counts']['pages_by_type']),
            'Per-type page counts disagree with the manifest')
    require(trees == manifest['counts']['trees'], 'Tree count mismatch')

    with tempfile.TemporaryDirectory(prefix='exiles-verify-') as tmp:
        dbpath = Path(tmp) / 'catalog.sqlite'
        dbpath.write_bytes(gzip.decompress((folder / 'catalog.sqlite.gz').read_bytes()))
        with closing(sqlite3.connect(dbpath.as_uri() + '?mode=ro', uri=True)) as db:
            with closing(sqlite3.connect(':memory:')) as expected:
                expected.executescript(SCHEMA_SQL)
                require(database_schema(db) == database_schema(expected), 'Unexpected SQLite schema')
            require(db.execute('PRAGMA integrity_check').fetchone()[0] == 'ok', 'Invalid SQLite database')
            require(db.execute('SELECT count(*) FROM entity').fetchone()[0] == manifest['counts']['entities'],
                    'SQLite entity loss')
            require(db.execute('SELECT count(*) FROM talent').fetchone()[0] == manifest['counts']['talents'],
                    'SQLite talent loss')
            for statement in db.iterdump():
                screen(statement)

    report = parse(gzip.decompress((folder / 'comparison.json.gz').read_bytes()))
    require(report['counts'] == manifest['counts']['comparison'], 'Comparison count mismatch')
    # Screen every artifact, not a chosen few. The API specification arrived with
    # a contact address in it, and an artifact nobody thought to list is exactly
    # the one that carries the thing you were screening for.
    for name in sorted(manifest['artifacts']):
        if name.endswith('.sqlite.gz'):
            continue  # already screened above, as logical SQL rather than raw bytes
        screen(gzip.decompress((folder / name).read_bytes()).decode('utf-8', errors='replace'))
    screen((folder / 'manifest.json').read_text(encoding='utf-8'))
    return manifest


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import', help='Parse a reviewed mirror into an immutable snapshot')
    imp.add_argument('mirror', type=Path, help='Extracted mirror root (the ExilesOfflineDB folder)')
    imp.add_argument('--site-db', type=Path, required=True, help="The crawler's offline_site.sqlite")
    imp.add_argument('--archive', type=Path, required=True, help='Original archive; its hash is recorded')
    imp.add_argument('--expected-sha256', required=True, help='SHA-256 of the archive reviewed for publication')
    imp.add_argument('--output', type=Path, required=True)
    imp.add_argument('--cache', type=Path, required=True, help='cachedata/union directory used only for comparison')
    imp.add_argument('--baseline-revision', help='Full Git commit identifying the captured comparison baseline')
    check = sub.add_parser('verify', help='Check hashes, internal agreement and personal-data indicators')
    check.add_argument('snapshot', type=Path)
    args = parser.parse_args()
    if args.command == 'import':
        manifest = ingest(args.mirror, args.site_db, args.output, args.expected_sha256,
                          args.archive, args.cache, args.baseline_revision)
        print(json.dumps(manifest['counts'], indent=2, ensure_ascii=False))
    else:
        manifest = verify(args.snapshot)
        print('Verified %s: %d artifacts, %d mirrored files.' % (
            args.snapshot, len(manifest['artifacts']), manifest['counts']['mirror_files']))


if __name__ == '__main__':
    main()
