"""Validate every grouped member against its original immutable record."""
import collections
import hashlib
import sqlite3
import tempfile
from pathlib import Path
from grouped_search import SCHEMA, content_id, locale, dump, split


def signature(row, member):
    return hashlib.sha256(dump([row[1], row[2], row[5], member]).encode()).digest()


class GroupValidator:
    def __init__(self, manifest):
        if manifest['grouping']['schema'] != SCHEMA:
            raise ValueError('Unsupported content grouping schema')
        self.manifest = manifest
        self.tmp = tempfile.TemporaryDirectory(prefix='validate-content-groups-')
        self.db = sqlite3.connect(str(Path(self.tmp.name) / 'check.sqlite'))
        self.db.execute('PRAGMA journal_mode=OFF')
        self.db.execute('PRAGMA synchronous=OFF')
        self.db.execute('PRAGMA cache_size=-32768')
        self.db.execute('CREATE TABLE members (key TEXT PRIMARY KEY, gid TEXT, signature BLOB, seen INTEGER DEFAULT 0) WITHOUT ROWID')
        self.db.execute('CREATE TABLE groups (gid TEXT PRIMARY KEY, signature BLOB) WITHOUT ROWID')
        self.counts = collections.Counter()

    def record(self, path, key, record):
        row = [key, record[1], record[2], record[3], record[4], record[0]]
        member = [key, record[3], record[4], locale(path, record)]
        self.db.execute('INSERT INTO members(key,gid,signature) VALUES (?,?,?)',
                        (key, content_id(path, record), signature(row, member)))

    def row(self, bucket, row):
        if len(row) != 8 or not isinstance(row[7], dict):
            raise ValueError('Missing grouped search evidence')
        gid, members = row[7].get('id'), row[7].get('members')
        if not members or row[0] != members[0][0] or len({m[0] for m in members}) != len(members):
            raise ValueError('Missing, duplicate or mismatched group members')
        if row[3] != ','.join(sorted({v for m in members for v in split(m[1])})) or row[4] != ', '.join(sorted({m[2] for m in members})):
            raise ValueError('Group facets do not match member evidence')
        if bucket.startswith('zone:') or bucket.startswith('browse:'):
            for member in members:
                expected = self.db.execute('SELECT gid,signature,seen FROM members WHERE key=?', (member[0],)).fetchone()
                if not expected or expected[:2] != (gid, signature(row, member)):
                    raise ValueError('Grouped member differs from preserved record')
                if bucket.startswith('browse:'):
                    if expected[2]:
                        raise ValueError('Source record appears in more than one browse group')
                    self.db.execute('UPDATE members SET seen=1 WHERE key=?', (member[0],))
        if not bucket.startswith('zone:'):
            digest = hashlib.sha256(dump(row).encode()).digest()
            prior = self.db.execute('SELECT signature FROM groups WHERE gid=?', (gid,)).fetchone()
            if prior and prior[0] != digest:
                raise ValueError('Inconsistent group across search partitions')
            self.db.execute('INSERT OR IGNORE INTO groups VALUES (?,?)', (gid, digest))
        if bucket.startswith('browse:'):
            if bucket != 'browse:' + row[2]:
                raise ValueError('Group appears in wrong collection')
            self.counts[row[2]] += 1

    def finish(self):
        grouping = self.manifest['grouping']
        missing = self.db.execute('SELECT COUNT(*) FROM members WHERE seen=0').fetchone()[0]
        if missing or dict(self.counts) != grouping['kinds'] or sum(self.counts.values()) != grouping['groups'] or grouping['records'] != self.manifest['records']:
            raise ValueError('Grouped coverage does not account for every source record')

    def close(self):
        self.db.close()
        self.tmp.cleanup()
