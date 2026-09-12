"""Preserve a client DBC dump as its difference from a reference client.

    python -B tools/import_dbc_snapshot.py import ZIP --reference DIR --holders TSV \
        --reference-label TEXT --received YYYY-MM-DD --output supplemental/client-dbc-snapshots
    python -B tools/import_dbc_snapshot.py verify supplemental/client-dbc-snapshots/SHA [--reference DIR]

Requires only the Python standard library.

A DBC dump taken from a client is mostly bytes that any copy of that client
already carries. Republishing those proves nothing, so only a member whose bytes
differ from the reference client's own copy is published. Every member is still
named in manifest.json with its SHA-256, so anyone holding the original archive
can prove they hold the same bytes.

The reference is a directory of loose DBCs taken from the reference client, one
per name, and a holders TSV (name, archive, sha256) recording which archive each
came from. Extract them with contrib/mpqtools' `mpqfind --from`, which reads one
named archive and bypasses precedence. A holders row whose hash does not match
the file beside it stops the import.

Each differing DBC gets a record-level report in diff.json, by first field and by
content. See docs/CLIENT-DBC-SNAPSHOTS.md.
"""
import argparse
import collections
import csv
import gzip
import hashlib
import io
import json
import re
import struct
import sys
import tempfile
import zipfile
from array import array
from pathlib import Path

SCHEMA_VERSION = 1
HEADER = struct.Struct('<4s4I')
EXAMPLES = 20
EXAMPLE_FIELDS = 12
PUBLISHED = ('differs', 'no reference')
NOT_DBC = 'not a whole WDBC file; named here with its hash, not republished'
MEMBER_NAME = re.compile(r'[A-Za-z0-9_.() -]+')
PATTERNS = {
    'email': re.compile(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}'),
    'player GUID': re.compile(r'0x[0-9A-Fa-f]{16}'),
    'account path': re.compile(r'WTF[\\/]+Account[\\/]', re.I),
    'local path': re.compile(r'\b[A-Z]:[\\/]', re.I),
}
# Game text, not people: the GM "BAN Hammer" spell tells a banned player to write
# to a mailbox at a TLD that does not exist. Allowed as this exact match only.
ALLOWED = {'email': {'techbot@gnome.mail'}}


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def gz(data):
    return gzip.compress(data, compresslevel=9, mtime=0)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1).encode('utf-8') + b'\n'


def screen(text, label):
    for name, rx in PATTERNS.items():
        for m in rx.finditer(text):
            require(m.group(0) in ALLOWED.get(name, ()), f'{label}: refusing {name}-shaped content')


def dbc_header(data):
    """The WDBC header as a dict, or None when data is not a whole WDBC file."""
    if len(data) < HEADER.size:
        return None
    magic, records, fields, record_size, string_size = HEADER.unpack_from(data)
    if magic != b'WDBC' or HEADER.size + records * record_size + string_size != len(data):
        return None
    return dict(records=records, fields=fields, record_size=record_size, string_size=string_size)


def split(data):
    h = dbc_header(data)
    end = HEADER.size + h['records'] * h['record_size']
    return h, data[HEADER.size:end], data[end:]


def string_starts(block):
    """{offset: text} for every string in a DBC string block, empty ones included:
    Ascension's Spell.dbc points all eight locale slots at one empty string, and a
    column that sometimes names the empty string is still a string column."""
    out, pos = {}, 0
    for piece in block.split(b'\0'):
        if pos < len(block):
            out[pos] = piece.decode('utf-8', 'replace')
        pos += len(piece) + 1
    return out


def screen_strings(data, label):
    screen(split(data)[2].decode('utf-8', 'replace'), label)


def u32s(rows):
    arr = array('I')
    arr.frombytes(rows)
    if sys.byteorder == 'big':
        arr.byteswap()
    return arr


def string_columns(fields, a, b, sa, sb):
    """Fields other than the first whose every nonzero value starts a string in
    both files, with at least two distinct values on one side. A 0/1 flag column
    lands on offset 1 in most string blocks, so one distinct value is not enough."""
    cols = []
    for k in range(1, fields):
        va, vb = set(a[k::fields]), set(b[k::fields])
        va.discard(0)
        vb.discard(0)
        if max(len(va), len(vb)) >= 2 and all(v in sa for v in va) and all(v in sb for v in vb):
            cols.append(k)
    return cols


def content_counts(arr, fields, starts, scols):
    """Counter of row contents, ignoring the first field and reading string columns as text."""
    numeric = [k for k in range(1, fields) if k not in set(scols)]
    out = collections.Counter()
    for i in range(0, len(arr), fields):
        row = arr[i:i + fields]
        key = array('I', (row[k] for k in numeric)).tobytes()
        key += '\0'.join(starts.get(row[k], '') for k in scols).encode('utf-8')
        out[hashlib.blake2b(key, digest_size=16).digest()] += 1
    return out


def diff_dbc(snapshot, reference):
    """Record-level report: rows only in either file and changed rows, by first
    field, plus row counts by content (which survives a table keyed by row index)."""
    ha, rows_a, blk_a = split(snapshot)
    hb, rows_b, blk_b = split(reference)
    report = dict(snapshot=ha, reference=hb)
    f = ha['fields']
    if f == 0 or f != hb['fields'] or ha['record_size'] != 4 * f or hb['record_size'] != 4 * f:
        ca = collections.Counter(rows_a[i:i + ha['record_size']] for i in range(0, len(rows_a), max(1, ha['record_size'])))
        cb = collections.Counter(rows_b[i:i + hb['record_size']] for i in range(0, len(rows_b), max(1, hb['record_size'])))
        report['by_content'] = dict(key='whole record bytes; the layout is not one 4-byte value per field',
                                    only_in_snapshot=sum((ca - cb).values()), only_in_reference=sum((cb - ca).values()))
        return report
    a, b = u32s(rows_a), u32s(rows_b)
    sa, sb = string_starts(blk_a), string_starts(blk_b)
    scols = string_columns(f, a, b, sa, sb)
    report['string_columns'] = scols

    def index(arr):
        out, dup = {}, 0
        for i, v in enumerate(arr[0::f]):
            if v in out:
                dup += 1
            else:
                out[v] = i
        return out, dup

    ida, dup_a = index(a)
    idb, dup_b = index(b)
    same_strings = blk_a == blk_b
    sset = set(scols)
    changed, examples = 0, []
    for v in sorted(set(ida) & set(idb)):
        i, j = ida[v], idb[v]
        if rows_a[i * 4 * f:(i + 1) * 4 * f] == rows_b[j * 4 * f:(j + 1) * 4 * f] and (same_strings or not scols):
            continue
        fa, fb = a[i * f:(i + 1) * f], b[j * f:(j + 1) * f]
        fields = []
        for k in range(f):
            x, y = fa[k], fb[k]
            if k in sset:
                tx, ty = sa.get(x, ''), sb.get(y, '')
                if tx != ty:
                    fields.append(dict(field=k, snapshot=tx, reference=ty))
            elif x != y:
                fields.append(dict(field=k, snapshot=x, reference=y))
        if fields:
            changed += 1
            if len(examples) < EXAMPLES:
                examples.append(dict(id=v, fields_changed=len(fields), fields=fields[:EXAMPLE_FIELDS]))
    only_a, only_b = sorted(set(ida) - set(idb)), sorted(set(idb) - set(ida))
    report['by_first_field'] = dict(
        duplicate_first_field=dict(snapshot=dup_a, reference=dup_b),
        only_in_snapshot=len(only_a), only_in_snapshot_ids=only_a[:EXAMPLES],
        only_in_reference=len(only_b), only_in_reference_ids=only_b[:EXAMPLES],
        changed=changed, changed_examples=examples)
    ca, cb = content_counts(a, f, sa, scols), content_counts(b, f, sb, scols)
    report['by_content'] = dict(key='every field but the first; string columns compared as text',
                                only_in_snapshot=sum((ca - cb).values()), only_in_reference=sum((cb - ca).values()))
    return json.loads(encode(report))


def read_holders(reference, holders):
    out = {}
    with open(holders, encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f, delimiter='\t')
        require({'name', 'archive', 'sha256'} <= set(reader.fieldnames or ()),
                'The holders TSV needs name, archive and sha256 columns')
        for r in reader:
            require(r['name'] not in out, 'Duplicate holders row: ' + r['name'])
            p = Path(reference) / r['name']
            require(p.is_file(), 'Reference copy missing: ' + r['name'])
            require(sha256(p.read_bytes()) == r['sha256'], 'Reference copy does not match its holders row: ' + r['name'])
            out[r['name']] = dict(archive=r['archive'], sha256=r['sha256'])
    return out


def summary(members):
    counts = collections.Counter(m['verdict'] for m in members.values())
    return {k: counts.get(k, 0) for k in ('identical', 'differs', 'no reference', 'not republished')}


def verify(folder, reference=None):
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_bytes())
    require(manifest.get('schema_version') == SCHEMA_VERSION, 'Unknown snapshot schema')
    members = manifest['members']
    published = sorted(n for n, m in members.items() if m['verdict'] in PUBLISHED)
    expected = {'dbc/' + n + '.gz' for n in published} | {'diff.json.gz'}
    require(set(manifest['artifacts']) == expected, 'Unexpected artifact list')
    present = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    require(present == expected | {'manifest.json'}, 'Unexpected snapshot files')
    for name, meta in manifest['artifacts'].items():
        blob = (folder / name).read_bytes()
        require(sha256(blob) == meta['sha256'] and len(blob) == meta['bytes'], 'Artifact mismatch: ' + name)
    for name, m in members.items():
        ref, verdict = m['reference'], m['verdict']
        if verdict == 'identical':
            require(ref is not None and ref['sha256'] == m['sha256'], 'Identical member without a matching reference: ' + name)
        elif verdict == 'differs':
            require(ref is not None and ref['sha256'] != m['sha256'], 'Differing member without a distinct reference: ' + name)
        elif verdict == 'no reference':
            require(ref is None and m['header'] is not None, 'Inconsistent unreferenced member: ' + name)
        else:
            require(verdict == 'not republished' and m['header'] is None, 'Unknown verdict for ' + name)
    require(manifest['summary'] == summary(members), 'Summary does not match the members')
    diffs = json.loads(gzip.decompress((folder / 'diff.json.gz').read_bytes()))
    require(set(diffs) == {n for n, m in members.items() if m['verdict'] == 'differs'},
            'diff.json does not cover exactly the differing members')
    for name in published:
        m = members[name]
        data = gzip.decompress((folder / ('dbc/' + name + '.gz')).read_bytes())
        require(sha256(data) == m['sha256'] and len(data) == m['bytes'], 'Content mismatch: ' + name)
        require(dbc_header(data) == m['header'], 'Header mismatch: ' + name)
        screen_strings(data, name)
        if reference is not None and m['verdict'] == 'differs':
            ref = (Path(reference) / name).read_bytes()
            require(sha256(ref) == m['reference']['sha256'], 'The reference copy is not the recorded one: ' + name)
            require(diff_dbc(data, ref) == diffs[name], 'diff.json does not match a recomputation: ' + name)
    if reference is not None:
        for name, m in members.items():
            if m['verdict'] == 'identical':
                p = Path(reference) / name
                require(p.is_file() and sha256(p.read_bytes()) == m['sha256'], 'The reference does not hold: ' + name)
    screen((folder / 'manifest.json').read_text(encoding='utf-8'), 'manifest.json')
    return manifest


def ingest(zip_path, reference, holders, label, received, output, submitter='not recorded'):
    require(re.fullmatch(r'\d{4}-\d{2}-\d{2}', received or '') is not None, 'Expected --received as YYYY-MM-DD')
    raw = Path(zip_path).read_bytes()
    refs = read_holders(reference, holders)
    members, published, diffs = {}, {}, {}
    with zipfile.ZipFile(io.BytesIO(raw)) as z:
        for info in z.infolist():
            if info.is_dir():
                continue
            name = info.filename
            require(MEMBER_NAME.fullmatch(name) is not None, 'Refusing a member name that is not a bare file name: ' + name)
            require(name not in members, 'Duplicate member: ' + name)
            data = z.read(info)
            header = dbc_header(data)
            m = dict(bytes=len(data), sha256=sha256(data), zip_date='%04d-%02d-%02dT%02d:%02d:%02d' % info.date_time,
                     header=header, reference=None)
            if header is None:
                m.update(verdict='not republished', reason=NOT_DBC)
            elif name not in refs:
                m['verdict'] = 'no reference'
                published[name] = data
            else:
                m['reference'] = refs[name]
                if refs[name]['sha256'] == m['sha256']:
                    m['verdict'] = 'identical'
                else:
                    m['verdict'] = 'differs'
                    published[name] = data
                    diffs[name] = diff_dbc(data, (Path(reference) / name).read_bytes())
            members[name] = m
    require(bool(members), 'The archive holds no files')
    for name, data in published.items():
        screen_strings(data, name)
    source = dict(archive=Path(zip_path).name, sha256=sha256(raw), bytes=len(raw), received=received, submitter=submitter)
    target = Path(output) / source['sha256']
    if target.exists():
        old = verify(target)
        require(old['source'] == source and old['members'] == members and old['reference'] == dict(label=label),
                'Snapshot already exists with different content or reference; use a separate output root')
        return target
    manifest = dict(schema_version=SCHEMA_VERSION, source=source, reference=dict(label=label),
                    members=members, summary=summary(members),
                    semantics=dict(
                        kind='client DBC dump, preserved as its difference from a reference client',
                        authority='the reference client carries the identical members; a published member is what '
                                  'the dump held, not a correction of the reference',
                        zip_date="the archive's own file time, local to whoever made it, without a time zone",
                        by_first_field='rows keyed by their first field; wrong for a table keyed by row index, '
                                       'where one inserted row shifts every row after it',
                        by_content='rows as a multiset of every field but the first; string columns (every nonzero '
                                   'value starts a string in both files, two or more distinct values) compared as text'),
                    artifacts={})
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.dbc-snapshot-build-', dir=target.parent) as tmp:
        staging = Path(tmp) / 'snapshot'
        (staging / 'dbc').mkdir(parents=True)
        for name, data in published.items():
            (staging / 'dbc' / (name + '.gz')).write_bytes(gz(data))
        (staging / 'diff.json.gz').write_bytes(gz(encode(diffs)))
        for p in sorted(x for x in staging.rglob('*') if x.is_file()):
            blob = p.read_bytes()
            manifest['artifacts'][p.relative_to(staging).as_posix()] = dict(bytes=len(blob), sha256=sha256(blob))
        (staging / 'manifest.json').write_bytes(encode(manifest))
        verify(staging, reference)
        staging.rename(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import', help='Compare a DBC dump with a reference client and preserve the difference')
    imp.add_argument('zip', type=Path, help='The DBC dump, as received')
    imp.add_argument('--reference', type=Path, required=True, help='Loose DBCs taken from the reference client')
    imp.add_argument('--holders', type=Path, required=True, help='TSV: name, archive, sha256 of each reference copy')
    imp.add_argument('--reference-label', required=True, help='What the reference client is; no local paths')
    imp.add_argument('--received', required=True, help='Date the dump arrived, YYYY-MM-DD')
    imp.add_argument('--submitter', default='not recorded')
    imp.add_argument('--output', type=Path, required=True)
    check = sub.add_parser('verify', help='Check hashes, verdicts and, given the reference, every diff')
    check.add_argument('snapshot', type=Path)
    check.add_argument('--reference', type=Path, help='Recompute every verdict and diff against these loose DBCs')
    args = parser.parse_args()
    try:
        if args.command == 'import':
            target = ingest(args.zip, args.reference, args.holders, args.reference_label, args.received,
                            args.output, args.submitter)
            print('Preserved and verified: ' + str(target))
        else:
            m = verify(args.snapshot, args.reference)
            s = m['summary']
            print(f"Verified {len(m['members'])} members of {m['source']['archive']}: {s['identical']} identical, "
                  f"{s['differs']} differ, {s['no reference']} without reference, {s['not republished']} not DBCs"
                  + (' (diffs recomputed)' if args.reference else ''))
    except (ValueError, OSError, zipfile.BadZipFile) as error:
        parser.exit(1, 'Import refused: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
