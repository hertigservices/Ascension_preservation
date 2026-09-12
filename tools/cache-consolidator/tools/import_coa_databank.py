"""Preserve a reviewed snapshot of f3rr311/CoA-Databank as a supplemental set.

    python -B tools/import_coa_databank.py import CHECKOUT --commit SHA --output supplemental/coa-databank
    python -B tools/import_coa_databank.py verify supplemental/coa-databank/SHA [--checkout CHECKOUT]

Requires the Python standard library and the git executable.

Blobs come out of git itself at the pinned commit, never from a working tree.
A file is published unchanged wherever it can be. Where it cannot, the change is
one of two named transforms, recorded per file beside the upstream blob id, so
anyone holding the commit can re-derive the published bytes (verify --checkout):

- community-identities: a coabuildhub build loses its author, its comments and
  the author of every "similar build", and those people's usernames are replaced
  with [user] in the build's strings. A username that is also ordinary game text
  ("Will", "Crimson") is replaced only in build titles, so guides keep their words.
- local-paths: a drive-rooted path loses its drive (C:\\ becomes <local>\\), and
  a Users\\<name> segment becomes Users\\<user>.

Code, and raw build pages whose markup carries the same identities, are listed as
omitted with the reason. Byte (f3rr311) gave permission to republish, relayed by
James Hertig on 2026-09-12; the repository carries no licence file. See
docs/COA-DATABANK.md.
"""
import argparse
import gzip
import hashlib
import json
import re
import subprocess
import tempfile
from pathlib import Path

SCHEMA_VERSION = 1
SOURCE_URL = 'https://github.com/f3rr311/CoA-Databank'
PERMISSION = ("Republished with the author's permission, relayed by James Hertig on 2026-09-12. "
              "The upstream repository carries no licence file.")
RAW_BUILD_REASON = ("raw build page markup; it embeds build authors' and commenters' usernames, Discord user ids "
                    "and comment text, which cannot be stripped from markup reliably. The stripped files under "
                    "coabuildhub/builds/ carry the same builds")
OMIT = (
    (re.compile(r'.*\.py$'), "code: the author's scrapers and harvest scripts; read them upstream (several embed "
                             "local paths)"),
    (re.compile(r'^coabuildhub/raw/builds/'), RAW_BUILD_REASON),
    (re.compile(r'^coabuildhub/raw/sample-build(\.html|-flight\.txt)$'), RAW_BUILD_REASON),
    (re.compile(r'^\.gitignore$'), 'repository housekeeping'),
)
BUILD_JSON = re.compile(r'^coabuildhub/builds/[a-z0-9-]+/[0-9a-f-]{36}\.json$')
BUILD_MD = re.compile(r'^coabuildhub/builds/[a-z0-9-]+/[0-9a-f-]{36}\.md$')
# Game text a username is tested against: a name found here is an ordinary word too.
NAME_CORPUS = re.compile(r'^coabuildhub/(?:skills|talents|spells|pages)/|^coabuildhub/classes\.json$')
BINARY = ('.webp', '.jpg', '.jpeg', '.png', '.gif', '.blp', '.ico')
PATTERNS = {
    'email': re.compile(r'[\w.+-]+@[\w-]+\.[a-zA-Z]{2,}'),
    'player GUID': re.compile(r'0x[0-9A-Fa-f]{16}'),
    'account path': re.compile(r'WTF[\\/]+Account[\\/]', re.I),
    # A drive, a separator, then a path character. Without the last part the
    # "A:" FAQ label in every ascension.gg page (A:\" inside escaped JSON) reads as a path.
    'local path': re.compile(r'\b[A-Z]:(?:\\+|/)[^"\\/\s<>]', re.I),
    'Discord user id': re.compile(r'discord(?:app)?\.com/(?:avatars|users)/\d'),
    'username field': re.compile(r'"username"\s*:'),
    # Every coabuildhub person record carries one of these, escaped or not (inside
    # markup or a React flight payload). A bare \"username\" is not enough: the
    # ascension.gg pages carry UI labels like \"username\":\"Username\".
    'user record': re.compile(r'\\*"(?:author_id|avatar_url)\\*"\s*:'),
}
# Not people, each allowed as this exact address. Ascension's public support
# mailbox sits in the schema.org block of every ascension.gg page (sometimes after
# a JSON-escaped '>', which the address pattern swallows as "u003e"); the other is
# game text on a GM ban spell, at a TLD that does not exist.
ALLOWED_EMAILS = frozenset({'support@ascension.gg', 'techbot@gnome.mail'})
DRIVE_USER = re.compile(r'\b([A-Za-z]:(?:\\\\|\\|/)[Uu]sers(?:\\\\|\\|/))([^\\/"\'\s<>]+)')
DRIVE = re.compile(r'\b[A-Za-z]:(?=(?:\\+|/)[^"\\/\s<>])')


def require(ok, message):
    if not ok:
        raise ValueError(message)


def sha256(data):
    return hashlib.sha256(data).hexdigest()


def git_blob_id(data):
    return hashlib.sha1(b'blob %d\x00' % len(data) + data).hexdigest()


def gz(data):
    return gzip.compress(data, compresslevel=9, mtime=0)


def encode(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=1).encode('utf-8') + b'\n'


def git(checkout, *args):
    r = subprocess.run(['git', '-C', str(checkout), *args], capture_output=True)
    require(r.returncode == 0, 'git ' + ' '.join(args[:2]) + ' failed: '
            + r.stderr.decode('utf-8', 'replace').strip()[:200])
    return r.stdout


def commit_files(checkout, commit):
    """[(path, blob id)] for every file in the commit, sorted by path."""
    out = []
    for entry in git(checkout, 'ls-tree', '-r', '-z', '--full-tree', commit).split(b'\x00'):
        if entry:
            meta, path = entry.split(b'\t', 1)
            _mode, kind, blob = meta.decode().split(' ')
            require(kind == 'blob', 'Unexpected tree entry: ' + path.decode('utf-8', 'replace'))
            out.append((path.decode('utf-8'), blob))
    return sorted(out)


class Blobs:
    """Reads blobs through one `git cat-file --batch`, checking each against its id."""

    def __init__(self, checkout):
        self.proc = subprocess.Popen(['git', '-C', str(checkout), 'cat-file', '--batch'],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE)

    def read(self, blob):
        self.proc.stdin.write(blob.encode() + b'\n')
        self.proc.stdin.flush()
        head = self.proc.stdout.readline().split()
        require(len(head) == 3 and head[1] == b'blob', 'Not a blob: ' + blob)
        data = self.proc.stdout.read(int(head[2]))
        self.proc.stdout.read(1)
        require(git_blob_id(data) == blob, 'A blob does not match its git id')
        return data

    def close(self):
        # stdout first: after a refused read git may still be writing, and a full
        # pipe nobody drains would leave wait() hanging.
        self.proc.stdout.close()
        try:
            self.proc.stdin.close()
        except OSError:
            pass
        self.proc.wait()


def omit_reason(path):
    for rx, reason in OMIT:
        if rx.match(path):
            return reason
    return None


def screen(text, label):
    for name, rx in PATTERNS.items():
        for m in rx.finditer(text):
            found = m.group(0)
            if name == 'email' and (found in ALLOWED_EMAILS or (found.startswith('u003e') and found[5:] in ALLOWED_EMAILS)):
                continue
            raise ValueError(f'{label}: refusing {name}-shaped content')


def usernames(build_objects):
    names = set()
    for obj in build_objects:
        people = [obj['build'].get('author')]
        people += [c.get('author') for c in obj.get('comments', [])]
        people += [s.get('author') for s in obj.get('similar', [])]
        for person in people:
            if isinstance(person, dict) and isinstance(person.get('username'), str) and person['username'].strip():
                names.add(person['username'].strip())
    return sorted(names)


def name_pattern(names, exact=False):
    """Whole-word match for any of names. Three or more characters match case-insensitively
    unless exact; shorter names only as written."""
    if not names:
        return None
    loose = sorted((n for n in names if len(n) >= 3 and not exact), key=lambda s: (-len(s), s))
    strict = sorted((n for n in names if len(n) < 3 or exact), key=lambda s: (-len(s), s))
    parts = []
    if loose:
        parts.append('(?i:' + '|'.join(map(re.escape, loose)) + ')')
    if strict:
        parts.append('(?:' + '|'.join(map(re.escape, strict)) + ')')
    return re.compile(r'(?<![A-Za-z0-9_])(?:' + '|'.join(parts) + r')(?![A-Za-z0-9_])')


class People:
    """The usernames a build names, and where each may be replaced. A name that also
    occurs in the game-text corpus is an ordinary word as well ("Will"), so it is
    replaced only in titles, as written; every other name is replaced everywhere."""

    def __init__(self, names, corpus):
        self.names = list(names)
        self.common = [n for n in self.names if name_pattern([n]).search(corpus)]
        self.everywhere = name_pattern([n for n in self.names if n not in set(self.common)])
        self.titles = name_pattern(self.common, exact=True)

    def replace(self, text, title=False):
        count = 0
        if self.everywhere is not None:
            text, n = self.everywhere.subn('[user]', text)
            count += n
        if title and self.titles is not None:
            text, n = self.titles.subn('[user]', text)
            count += n
        return text, count


def json_style(obj, raw):
    """The json.dumps settings that reproduce raw exactly, or None."""
    newline = '\r\n' if b'\r\n' in raw else '\n'
    for ensure_ascii in (False, True):
        for indent in (1, 2, 4, None):
            text = json.dumps(obj, ensure_ascii=ensure_ascii, indent=indent)
            if newline != '\n':
                text = text.replace('\n', newline)
            for tail in ('', newline):
                if (text + tail).encode('utf-8') == raw:
                    return dict(ensure_ascii=ensure_ascii, indent=indent, newline=newline, tail=tail)
    return None


def find_author_keys(value):
    if isinstance(value, dict):
        return [k for k in value if k.startswith('author')] + [x for v in value.values() for x in find_author_keys(v)]
    if isinstance(value, list):
        return [x for v in value for x in find_author_keys(v)]
    return []


def strip_build_json(raw, people):
    obj = json.loads(raw.decode('utf-8'))
    style = json_style(obj, raw)
    require(style is not None, 'Cannot reproduce the upstream serialisation of a build file')
    build = obj['build']
    counts = dict(author=int('author' in build or 'author_id' in build), comments=len(obj.get('comments', [])),
                  similar_authors=sum(int('author' in s or 'author_id' in s) for s in obj.get('similar', [])))
    build.pop('author', None)
    build.pop('author_id', None)
    obj.pop('comments', None)
    for s in obj.get('similar', []):
        s.pop('author', None)
        s.pop('author_id', None)
    require(not find_author_keys(obj), 'A build file carries an author field this importer does not know')
    replaced = 0

    def scrub(value):
        nonlocal replaced
        if isinstance(value, dict):
            return {k: scrub(v) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub(v) for v in value]
        if isinstance(value, str):
            text, n = people.replace(value)
            replaced += n
            return text
        return value

    obj = scrub(obj)
    for holder in [obj['build']] + list(obj.get('similar', [])):
        if isinstance(holder.get('title'), str):
            holder['title'], n = people.replace(holder['title'], title=True)
            replaced += n
    if replaced:
        counts['usernames_replaced'] = replaced
    text = json.dumps(obj, ensure_ascii=style['ensure_ascii'], indent=style['indent'])
    if style['newline'] != '\n':
        text = text.replace('\n', style['newline'])
    return text + style['tail'], counts


def strip_build_md(text, people):
    newline = '\r\n' if '\r\n' in text else '\n'
    kept, author_lines, comments = [], 0, 0
    for line in text.split(newline):
        if line.strip() == '## Comments':
            comments = 1
            break
        if line.startswith('- **Author:** '):
            author_lines += 1
            continue
        kept.append(line)
    while kept and not kept[-1].strip():
        kept.pop()
    replaced, titled = 0, False
    for i, line in enumerate(kept):
        is_title = not titled and line.startswith('# ')
        titled = titled or is_title
        kept[i], n = people.replace(line, title=is_title)
        replaced += n
    counts = dict(author_lines=author_lines, comments_section=comments)
    if replaced:
        counts['usernames_replaced'] = replaced
    tail = newline if text.endswith(newline) else ''
    return newline.join(kept) + tail, counts


def derive(path, data, people):
    """(published content, kind, transforms) for one upstream blob."""
    inner = gzip.decompress(data) if path.endswith('.gz') else data
    if path.lower().endswith(BINARY) or path.lower().endswith(tuple(b + '.gz' for b in BINARY)):
        return inner, 'binary', {}
    try:
        text = inner.decode('utf-8')
    except UnicodeDecodeError:
        raise ValueError(path + ': neither UTF-8 text nor a known binary type')
    transforms = {}
    if BUILD_JSON.match(path):
        text, transforms['community-identities'] = strip_build_json(inner, people)
    elif BUILD_MD.match(path):
        text, transforms['community-identities'] = strip_build_md(text, people)
    text, users = DRIVE_USER.subn(r'\1<user>', text)
    text, drives = DRIVE.subn('<local>', text)
    if users or drives:
        transforms['local-paths'] = dict(drives=drives, user_segments=users)
    content = text.encode('utf-8')
    if content == inner:
        return inner, 'text', {}
    return content, 'text', transforms


def artifact_name(path):
    return path if path.endswith('.gz') else path + '.gz'


def build(checkout, commit):
    """(files, omitted, artifact bytes by name, username counts) at the commit."""
    entries = commit_files(checkout, commit)
    blobs = Blobs(checkout)
    try:
        names = usernames(json.loads(blobs.read(b)) for p, b in entries if BUILD_JSON.match(p))
        corpus = '\n'.join(blobs.read(b).decode('utf-8', 'replace') for p, b in entries
                           if NAME_CORPUS.match(p) and omit_reason(p) is None)
        people = People(names, corpus)
        files, omitted, artifacts, first_by_blob = {}, [], {}, {}
        for path, blob in entries:
            reason = omit_reason(path)
            if reason:
                omitted.append(dict(path=path, source_blob=blob, reason=reason))
                continue
            data = blobs.read(blob)
            content, kind, transforms = derive(path, data, people)
            meta = dict(source_blob=blob, source_bytes=len(data), kind=kind, transforms=transforms,
                        content_bytes=len(content), content_sha256=sha256(content))
            if blob in first_by_blob:
                first = first_by_blob[blob]
                meta.update(artifact=files[first]['artifact'], duplicate_of=first)
            else:
                first_by_blob[blob] = path
                meta['artifact'] = artifact_name(path)
                untouched_gz = path.endswith('.gz') and not transforms
                artifacts[meta['artifact']] = data if untouched_gz else gz(content)
            files[path] = meta
    finally:
        blobs.close()
    return files, omitted, artifacts, dict(distinct=len(names), title_only=len(people.common))


def verify(folder, checkout=None):
    folder = Path(folder)
    manifest = json.loads((folder / 'manifest.json').read_bytes())
    require(manifest.get('schema_version') == SCHEMA_VERSION, 'Unknown snapshot schema')
    files = manifest['files']
    expected = {m['artifact'] for m in files.values()}
    require(set(manifest['artifacts']) == expected, 'Unexpected artifact list')
    present = {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()}
    require(present == expected | {'manifest.json'}, 'Unexpected snapshot files')
    for name, meta in manifest['artifacts'].items():
        blob = (folder / name).read_bytes()
        require(sha256(blob) == meta['sha256'] and len(blob) == meta['bytes'], 'Artifact mismatch: ' + name)
    for path, m in files.items():
        require(omit_reason(path) is None, 'A published path matches an omission rule: ' + path)
        art = (folder / m['artifact']).read_bytes()
        if m.get('duplicate_of'):
            first = files[m['duplicate_of']]
            require(first['source_blob'] == m['source_blob'] and first['artifact'] == m['artifact']
                    and not first.get('duplicate_of'), 'Broken duplicate: ' + path)
        if path.endswith('.gz') and not m['transforms']:
            require(git_blob_id(art) == m['source_blob'], 'Not the upstream bytes: ' + path)
        content = gzip.decompress(art)
        if not m['transforms'] and not path.endswith('.gz'):
            require(git_blob_id(content) == m['source_blob'], 'Not the upstream bytes: ' + path)
        require(sha256(content) == m['content_sha256'] and len(content) == m['content_bytes'], 'Content mismatch: ' + path)
        if m['kind'] == 'text':
            screen(content.decode('utf-8'), path)
        else:
            require(not m['transforms'], 'A binary file claims a transform: ' + path)
    for o in manifest['omitted']:
        require(omit_reason(o['path']) == o['reason'], 'An omission does not match its rule: ' + o['path'])
    screen((folder / 'manifest.json').read_text(encoding='utf-8'), 'manifest.json')
    if checkout is not None:
        again, omitted, artifacts, names = build(checkout, manifest['source']['commit'])
        require(again == files and omitted == manifest['omitted'] and names == manifest['usernames'],
                'Re-deriving the snapshot from the commit gives a different result')
        for name, data in artifacts.items():
            require(gzip.decompress(data) == gzip.decompress((folder / name).read_bytes()),
                    'Re-derived content differs: ' + name)
    return manifest


def ingest(checkout, output, commit):
    require(re.fullmatch(r'[0-9a-f]{40}', commit or '') is not None, 'Expected a full 40-character commit id')
    require(git(checkout, 'cat-file', '-t', commit).strip() == b'commit', 'Not a commit: ' + commit)
    author, date, subject = git(checkout, 'show', '-s', '--format=%an%x00%aI%x00%s', commit).decode('utf-8').rstrip('\n').split('\x00')
    files, omitted, artifacts, names = build(checkout, commit)
    require(bool(files), 'Nothing to publish in that commit')
    source = dict(url=SOURCE_URL, commit=commit, author=author, date=date, subject=subject, permission=PERMISSION)
    target = Path(output) / commit
    if target.exists():
        old = verify(target)
        require(old['source'] == source and old['files'] == files, 'Snapshot already exists with different content; '
                                                                   'use a separate output root')
        return target
    manifest = dict(schema_version=SCHEMA_VERSION, source=source, files=files, omitted=omitted, usernames=names,
                    semantics=dict(
                        kind='supplemental third-party dataset',
                        authority="the author's scrape, harvest and derived indexes; captured data remains authoritative",
                        transforms='community-identities: build authors, comments and similar-build authors removed, '
                                   'and their usernames replaced with [user] in build strings (in titles only, as '
                                   'written, for a name that is also game text). local-paths: drive roots become '
                                   '<local>, Users\\<name> becomes Users\\<user>. Untransformed files are the upstream bytes',
                        duplicates='a path whose upstream blob another path already published shares that artifact'),
                    artifacts={})
    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='.coa-databank-build-', dir=target.parent) as tmp:
        staging = Path(tmp) / 'snapshot'
        for name, data in artifacts.items():
            dest = staging / name
            dest.parent.mkdir(parents=True, exist_ok=True)
            dest.write_bytes(data)
        for name in sorted(artifacts):
            blob = (staging / name).read_bytes()
            manifest['artifacts'][name] = dict(bytes=len(blob), sha256=sha256(blob))
        (staging / 'manifest.json').write_bytes(encode(manifest))
        verify(staging)
        staging.rename(target)
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = parser.add_subparsers(dest='command', required=True)
    imp = sub.add_parser('import', help='Validate, transform and atomically create an immutable snapshot')
    imp.add_argument('checkout', type=Path, help='A git checkout that contains the commit')
    imp.add_argument('--commit', required=True, help='Full id of the upstream commit reviewed for publication')
    imp.add_argument('--output', type=Path, required=True)
    check = sub.add_parser('verify', help='Check hashes, upstream blob ids and the screen')
    check.add_argument('snapshot', type=Path)
    check.add_argument('--checkout', type=Path, help='Also re-derive every published file from the commit')
    args = parser.parse_args()
    try:
        if args.command == 'import':
            print('Preserved and verified: ' + str(ingest(args.checkout, args.output, args.commit)))
        else:
            m = verify(args.snapshot, args.checkout)
            changed = sum(1 for f in m['files'].values() if f['transforms'])
            print(f"Verified {len(m['files'])} files ({changed} transformed, {len(m['omitted'])} omitted) from "
                  f"{m['source']['url']} @ {m['source']['commit'][:12]}" + (' (re-derived)' if args.checkout else ''))
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        parser.exit(1, 'Import refused: ' + str(error) + '\n')


if __name__ == '__main__':
    main()
