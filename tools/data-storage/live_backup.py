"""Resumable WD archive index for live-only catalog retention."""
import json
from pathlib import Path
import re
import sqlite3
from concurrent.futures import ThreadPoolExecutor
from live_catalog import Archive, list_objects


class ArchiveIndex:
    def __init__(self, root):
        self.db=sqlite3.connect(Path(root)/'remote-index.sqlite')
        self.db.execute('PRAGMA journal_mode=WAL')
        self.db.executescript('CREATE TABLE IF NOT EXISTS objects(key TEXT PRIMARY KEY, bytes INTEGER NOT NULL, etag TEXT NOT NULL, sha256 TEXT NOT NULL); CREATE TABLE IF NOT EXISTS blobs(sha256 TEXT PRIMARY KEY, md5 TEXT NOT NULL, bytes INTEGER NOT NULL); CREATE INDEX IF NOT EXISTS md5_size ON blobs(md5,bytes);')

    def get(self,key):
        row=self.db.execute('SELECT key,bytes,etag,sha256 FROM objects WHERE key=?',(key,)).fetchone()
        return dict(zip(('key','bytes','etag','sha256'),row)) if row else None

    def save(self,obj):
        self.db.execute('INSERT OR REPLACE INTO objects VALUES(?,?,?,?)',tuple(obj[k] for k in ('key','bytes','etag','sha256')))
        md5=obj['etag'].strip('"')
        if re.fullmatch('[0-9a-f]{32}',md5):self.db.execute('INSERT OR REPLACE INTO blobs VALUES(?,?,?)',(obj['sha256'],md5,obj['bytes']))

    def lookup(self,etag,size):
        row=self.db.execute('SELECT sha256,bytes FROM blobs WHERE md5=? AND bytes=?',(etag.strip('"'),size)).fetchone()
        return {'sha256':row[0],'bytes':row[1]} if row else None

    def import_jsonl(self,path):
        count=0
        for line in Path(path).open():
            self.save(json.loads(line));count+=1
            if count%10000==0:self.db.commit()
        self.db.commit();return count


def archive_objects(client,archive,index,objects,workers=16):
    """Group equal single-part ETags; verify one blob against every represented key."""
    archive.check_volume();groups={};reused=0
    for obj in objects.values():
        prior=index.get(obj['key'])
        if prior and all(prior[k]==obj[k] for k in ('bytes','etag')):
            # cleanup separately rehashes every distinct candidate blob before deletion.
            reused+=1;continue
        if not re.fullmatch(r'"?[0-9a-f]{32}"?',obj['etag']):raise ValueError('Opaque ETag needs separate archival verification')
        groups.setdefault((obj['etag'],obj['bytes']),[]).append(obj)
    entries=[]
    for ident,group in groups.items():
        expected=index.lookup(*ident)
        if expected is None and re.fullmatch(r'catalog/objects/[0-9a-f]{64}',group[0]['key']):
            expected={'sha256':group[0]['key'].rsplit('/',1)[1],'bytes':group[0]['bytes']}
        entries.append((group,expected))
    count=0
    def obtain(item):
        group,expected=item
        return group,archive.remote(client,group[0],expected)
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for start in range(0,len(entries),1000):
            archive.check_volume()
            for group,saved in pool.map(obtain,entries[start:start+1000]):
                for obj in group:index.save({**obj,'sha256':saved['sha256']});count+=1
            index.db.commit()
            print(f'WD backup: {count} newly indexed objects; {reused} retained receipts',flush=True)
    return {'newly_archived_objects':count,'previously_archived_objects':reused}
