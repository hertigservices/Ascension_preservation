"""Private, disposable evidence for deterministic generated files.

Never skips intake, merging, or audits. ASCENSION_FORCE_REBUILD=1 ignores saved
entries. Hash inputs, rules and outputs, not mtimes; damaged entries are misses.
"""
import csv
import hashlib
import json
import os
from pathlib import Path
import sys
import uuid
import zlib
import config


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     ensure_ascii=True).encode()).hexdigest()


def file_hash(path):
    p = Path(path)
    if not p.exists():
        return None
    with p.open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()


def fingerprint(paths, value=None):
    return digest({'files': [(str(Path(p).resolve()), file_hash(p)) for p in paths],
                   'value': value})


def rules():
    # Any local Python/rule/config change conservatively invalidates all entries.
    here = Path(__file__).resolve().parent
    paths = sorted(p for p in here.iterdir() if p.suffix in ('.py', '.json'))
    paths += [Path(config.WORK) / 'config.json']
    return fingerprint(paths, [sys.version, sys.platform, zlib.ZLIB_RUNTIME_VERSION,
                               config.WORK, config.OUT, config.STORE])


def cache_inputs(cache, sources, slugs):
    # Include every referenced ID, even in an inconsistent cross-category store.
    index = Path(config.STORE) / cache / 'index.tsv'
    refs = set()
    if index.exists():
        with index.open(encoding='utf-8', newline='') as f:
            for row in csv.DictReader(f, delimiter='\t'):
                refs.update(row['srcs'].split(','))
    return fingerprint([Path(config.STORE) / cache / 'index.tsv',
                        Path(config.STORE) / cache / 'pack.bin'],
                       [[s for s in sources if s['cache'] == cache or s['id'] in refs], slugs])


class BuildCache:
    def __init__(self, stage, output_root):
        self.stage = stage
        self.root = Path(output_root).resolve()
        self.revision = rules()
        self.sources_hash = file_hash(config.SOURCES)
        self.directory = Path(config.WORK) / '.build-cache' / stage / digest(str(self.root))
        self.force = os.environ.get('ASCENSION_FORCE_REBUILD') == '1'

    def path(self, key):
        return self.directory / (digest(key) + '.json')

    def relative(self, path):
        return Path(path).resolve().relative_to(self.root).as_posix()

    def valid_metadata(self, meta, outputs):
        if not isinstance(meta, dict):
            return False
        if self.stage == 'export':
            return (bool(outputs) and isinstance(meta.get('stats'), dict)
                    and isinstance(meta['stats'].get('union'), list)
                    and len(meta['stats']['union']) == 3
                    and isinstance(meta['stats'].get('records'), int)
                    and isinstance(meta.get('mode_rows'), dict))
        if self.stage == 'rebuild':
            return bool(outputs) and all(isinstance(v, list) and len(v) == 4 and v[3] is True
                                         for v in meta.values())
        if self.stage == 'stock':
            return (isinstance(meta.get('files'), list) and meta['files'] == list(outputs)
                    and isinstance(meta.get('counts'), dict)
                    and all(isinstance(meta['counts'].get(k), int) for k in ('rows', 'primary', 'union'))
                    and isinstance(meta.get('captured'), str)
                    and isinstance(meta.get('placeholders'), list))
        return False

    def load(self, key, inputs):
        if self.force:
            return None
        try:
            envelope = json.loads(self.path(key).read_text(encoding='utf-8'))
            entry = envelope['entry']
            if envelope['sha256'] != digest(entry):
                return None
            if (entry['schema'], entry['revision'], entry['inputs']) != (1, self.revision, inputs):
                return None
            outputs = entry['outputs']
            if not isinstance(outputs, dict) or not self.valid_metadata(entry['metadata'], outputs):
                return None
            for rel, expected in outputs.items():
                p = self.root / rel
                if p.is_symlink() or p.is_junction() or self.relative(p) != rel or not expected or file_hash(p) != expected:
                    return None
            return entry['metadata'], [str(self.root / rel) for rel in outputs]
        except (OSError, ValueError, KeyError, TypeError):
            return None

    def save(self, key, inputs, paths, metadata):
        # Record only successful generation/verification, with atomic replacement.
        if self.revision != rules() or self.sources_hash != file_hash(config.SOURCES):
            raise RuntimeError('rules or merged sources changed during generation')
        outputs = {self.relative(p): file_hash(p) for p in paths}
        if any(h is None for h in outputs.values()):
            raise RuntimeError('generated output disappeared before cache recording')
        entry = dict(schema=1, revision=self.revision, inputs=inputs,
                     outputs=outputs, metadata=metadata)
        self.directory.mkdir(parents=True, exist_ok=True)
        temp = self.path(key).with_suffix('.' + uuid.uuid4().hex + '.tmp')
        try:
            temp.write_text(json.dumps({'entry': entry, 'sha256': digest(entry)}), encoding='utf-8')
            os.replace(temp, self.path(key))
        finally:
            temp.unlink(missing_ok=True)
