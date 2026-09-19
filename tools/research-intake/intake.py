"""Private, content-addressed research intake. Python standard library only."""
import argparse
import contextlib
import datetime
import gzip
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import sqlite3
import stat
import tarfile
import tempfile
import time
import uuid
import zipfile
import readers
import privacy

CHUNK = 1024 * 1024
DEFAULT_ROOT = Path(os.environ.get('LOCALAPPDATA',str(Path.home()))) / 'AscensionPreservation' / 'research-intake'


def now(): return datetime.datetime.now(datetime.timezone.utc).isoformat()
def digest(value): return hashlib.sha256(value).hexdigest()
def encoded(value): return readers.json_text(value).encode('utf-8')


def atomic_json(path,value):
    path.parent.mkdir(parents=True,exist_ok=True)
    tmp=path.with_name(path.name+'.tmp-'+uuid.uuid4().hex)
    try:
        with tmp.open('wb') as f: f.write(encoded(value)); f.flush(); os.fsync(f.fileno())
        os.replace(tmp,path)
    finally: tmp.unlink(missing_ok=True)


def linked(path):
    return path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction())


class Lock:
    def __init__(self,path): self.path=path
    def __enter__(self):
        self.f=self.path.open('a+b'); self.f.seek(0)
        if os.name=='nt':
            import msvcrt
            if self.path.stat().st_size==0: self.f.write(b'0'); self.f.flush()
            self.f.seek(0)
            try: msvcrt.locking(self.f.fileno(),msvcrt.LK_NBLCK,1)
            except OSError: self.f.close(); raise RuntimeError('Research intake is already processing another job')
        else:
            import fcntl
            try: fcntl.flock(self.f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            except OSError: self.f.close(); raise RuntimeError('Research intake is already processing another job')
        return self
    def __exit__(self,*args): self.f.close()


class Budget:
    def __init__(self,root,expanded=32*1024**3,members=100000,records=10000000,seconds=3600,reserve=5*1024**3):
        self.root=root; self.expanded=expanded; self.members=members; self.records=records
        self.seconds=seconds; self.reserve=reserve; self.start=time.monotonic(); self.written=0; self.count=0; self.rows=0
    def tick(self):
        if time.monotonic()-self.start>self.seconds: raise readers.Held('Job time budget reached; reprocess with a larger budget')
    def space(self,size):
        self.tick()
        if shutil.disk_usage(self.root).free-size<self.reserve: raise readers.Held('Free-space reserve reached; originals are never automatically deleted')
    def member(self):
        self.tick(); self.count+=1
        if self.count>self.members: raise readers.Held('Archive/file count budget reached')
    def row(self):
        self.tick(); self.rows+=1
        if self.rows>self.records: raise readers.Held('Job record budget reached; original retained')


SCHEMA = '''
CREATE TABLE IF NOT EXISTS sources(id TEXT PRIMARY KEY, metadata TEXT NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS artifacts(hash TEXT PRIMARY KEY, bytes INTEGER NOT NULL, created TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS runs(id TEXT PRIMARY KEY, source TEXT NOT NULL, input TEXT NOT NULL, started TEXT NOT NULL, finished TEXT, status TEXT, detail TEXT);
CREATE TABLE IF NOT EXISTS documents(id TEXT PRIMARY KEY, hash TEXT NOT NULL, format_name TEXT NOT NULL, reader TEXT NOT NULL, status TEXT NOT NULL, rows INTEGER NOT NULL DEFAULT 0, detail TEXT);
CREATE TABLE IF NOT EXISTS receipts(id TEXT PRIMARY KEY, run TEXT NOT NULL, source TEXT NOT NULL, document TEXT NOT NULL, parent TEXT, locator TEXT NOT NULL, created TEXT NOT NULL, UNIQUE(source,document,parent,locator));
CREATE TABLE IF NOT EXISTS records(hash TEXT PRIMARY KEY, collection TEXT NOT NULL, payload TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS occurrences(document TEXT NOT NULL, ordinal INTEGER NOT NULL, record TEXT NOT NULL, PRIMARY KEY(document,ordinal));
CREATE INDEX IF NOT EXISTS occurrence_record ON occurrences(record);
CREATE INDEX IF NOT EXISTS receipts_document ON receipts(document);
CREATE TABLE IF NOT EXISTS links(document TEXT NOT NULL, ordinal INTEGER NOT NULL, field TEXT NOT NULL, value TEXT NOT NULL);
CREATE INDEX IF NOT EXISTS lookup_link ON links(field,value);
'''


class Intake:
    def __init__(self,root=DEFAULT_ROOT,**limits):
        self.root=Path(root).resolve(); self.root.mkdir(parents=True,exist_ok=True)
        # Raw archives must never be under the auto-published data checkout.
        for parent in [self.root,*self.root.parents]:
            if (parent/'.git').exists() or (parent/'.ascension-data.json').exists():
                raise ValueError('Private intake storage must be outside all Git repositories')
        self.lock=Lock(self.root/'intake.lock'); self.lock.__enter__()
        self.db=sqlite3.connect(self.root/'catalog.sqlite'); self.db.row_factory=sqlite3.Row
        self.db.execute('PRAGMA journal_mode=WAL'); self.db.execute('PRAGMA synchronous=FULL')
        self.db.executescript(SCHEMA)
        self.db.execute("UPDATE runs SET status='interrupted',detail='Previous process stopped; rerun safely' WHERE finished IS NULL")
        self.db.execute("UPDATE documents SET status='interrupted' WHERE status='processing'"); self.db.commit()
        for name in ('objects','temporary','inbox','reports','exports'): (self.root/name).mkdir(exist_ok=True)
        self.budget=Budget(self.root,**limits)
    def __enter__(self): return self
    def __exit__(self,*args): self.db.close(); self.lock.__exit__(*args)
    def object_path(self,sha): return self.root/'objects'/sha[:2]/sha
    def source(self,source,metadata=None):
        if not re.fullmatch('[a-z0-9][a-z0-9-]{0,63}',source): raise ValueError('Source ID must be lowercase letters, digits and hyphens (max 64)')
        existing=self.db.execute('SELECT metadata FROM sources WHERE id=?',(source,)).fetchone()
        if existing and metadata is None: return json.loads(existing[0])
        meta=metadata or {'title':source,'mode':'Unspecified','captured_at':None,'permission':'private; publication not granted','lane':'private','kind':'ad-hoc','trust':'unknown','known_caps':{}}
        if meta.get('lane','private') not in ('private','public'): raise ValueError('Source lane must be private or public')
        if not meta.get('title') or not meta.get('permission'): raise ValueError('Source metadata needs title and permission')
        self.db.execute('INSERT INTO sources VALUES(?,?,?) ON CONFLICT(id) DO UPDATE SET metadata=excluded.metadata',(source,readers.json_text(meta),now())); self.db.commit()
        return meta
    def preserve(self,f,expected=None,expanded=False):
        """Copy in bounded chunks, fsync and atomically expose only verified objects."""
        self.budget.space(expected or CHUNK)
        fd,temp=tempfile.mkstemp(dir=self.root/'temporary'); temp=Path(temp); h=hashlib.sha256(); size=0
        try:
            with os.fdopen(fd,'wb') as out:
                while True:
                    self.budget.tick(); chunk=f.read(CHUNK)
                    if not chunk: break
                    if expanded:
                        self.budget.written+=len(chunk)
                        if self.budget.written>self.budget.expanded: raise readers.Held('Expanded-byte budget reached; parent archive retained')
                    self.budget.space(len(chunk)); out.write(chunk); h.update(chunk); size+=len(chunk)
                out.flush(); os.fsync(out.fileno())
            if expected is not None and size!=expected: raise readers.Held('Input changed or archive member is truncated')
            sha=h.hexdigest(); target=self.object_path(sha); target.parent.mkdir(exist_ok=True)
            if target.exists():
                # Same-size corruption must not turn a repeated donation into false proof.
                with target.open('rb') as current:
                    check=hashlib.file_digest(current,'sha256').hexdigest()
                if check!=sha: raise readers.Held('Stored object failed integrity check; retain donor input and repair storage')
                temp.unlink()
            else: os.replace(temp,target)
            self.db.execute('INSERT OR IGNORE INTO artifacts VALUES(?,?,?)',(sha,size,now())); self.db.commit()
            return sha
        finally: temp.unlink(missing_ok=True)
    def document(self,sha,name):
        # Path context is retained in receipts; format selection is extension-only.
        suffix=''.join(Path(name).suffixes).lower()[-100:]
        ident=digest(encoded([sha,suffix,readers.VERSION]))
        self.db.execute('INSERT OR IGNORE INTO documents(id,hash,format_name,reader,status) VALUES(?,?,?,?,?)',(ident,sha,suffix,readers.VERSION,'pending')); self.db.commit()
        return ident
    def receive(self,sha,name,source,run,parent=None,reprocess=False,depth=0):
        doc=self.document(sha,name)
        receipt=digest(encoded([source,doc,parent,name]))
        self.db.execute('INSERT OR IGNORE INTO receipts VALUES(?,?,?,?,?,?,?)',(receipt,run,source,doc,parent,name,now())); self.db.commit()
        state=self.db.execute('SELECT status FROM documents WHERE id=?',(doc,)).fetchone()[0]
        # Archive children still need receipts for each new source/wrapper.
        archive=self.archive_kind(name)
        if state=='parsed' and not reprocess: return doc
        if state=='held' and not reprocess: return doc
        path=self.object_path(sha)
        try:
            if archive:
                if depth>=5: raise readers.Held('Nested archive depth reached; inner archive retained')
                self.unpack(path,name,source,run,doc,reprocess,depth,archive)
                self.db.execute("UPDATE documents SET status='container',detail=NULL WHERE id=?",(doc,)); self.db.commit()
            else: self.parse(doc,path,name)
        except Exception as e:
            self.db.rollback()
            self.db.execute("UPDATE documents SET status='held',detail=? WHERE id=?",(str(e)[:1000],doc)); self.db.commit()
        return doc
    @staticmethod
    def archive_kind(name):
        low=name.lower()
        if low.endswith('.zip'): return 'zip'
        if low.endswith(('.tar','.tar.gz','.tgz','.tar.bz2','.tbz2','.tar.xz','.txz','.tar.zst','.tzst')): return 'tar'
        if low.endswith('.gz'): return 'gzip'
        return None
    def unpack(self,path,name,source,run,parent,reprocess,depth,kind):
        def child(stream,label,size=None):
            self.budget.member()
            sha=self.preserve(stream,size,True)
            self.receive(sha,label,source,run,parent,reprocess,depth+1)
        if kind=='gzip':
            with gzip.open(path,'rb') as f: child(f,name[:-3])
        elif kind=='zip':
            with zipfile.ZipFile(path) as z:
                if len(z.infolist())>self.budget.members: raise readers.Held('ZIP member budget reached')
                for number,member in enumerate(z.infolist()):
                    if member.is_dir(): continue
                    self.safe_member(member.filename)
                    mode=member.external_attr>>16
                    if stat.S_ISLNK(mode) or member.flag_bits&1: raise readers.Held('Archive contains a link or encrypted member; container retained')
                    with z.open(member) as f: child(f,str(number)+':'+member.filename,member.file_size)
        else:
            with tarfile.open(path,mode='r|*') as tar:
                for number,member in enumerate(tar):
                    if member.isdir(): continue
                    self.safe_member(member.name)
                    if not member.isfile(): raise readers.Held('Archive contains non-regular members; container retained')
                    with tar.extractfile(member) as f: child(f,str(number)+':'+member.name,member.size)
    @staticmethod
    def safe_member(name):
        p=PurePosixPath(name.replace('\\','/'))
        if p.is_absolute() or '..' in p.parts or ':' in name or '\x00' in name:
            raise readers.Held('Unsafe archive member path; archive preserved without extraction')
    def parse(self,doc,path,name):
        self.db.execute("UPDATE documents SET status='processing' WHERE id=?",(doc,)); self.db.commit()
        self.db.execute('BEGIN')
        self.db.execute('DELETE FROM occurrences WHERE document=?',(doc,)); self.db.execute('DELETE FROM links WHERE document=?',(doc,))
        count=0
        try:
            for collection,value in readers.read_records(path,name,self.budget.row):
                payload=readers.json_text(value)
                self.budget.space(len(payload.encode('utf-8'))*2+4096)
                if len(payload.encode('utf-8'))>readers.MAX_RECORD: raise readers.Held('Expanded record exceeds 8 MiB')
                key=digest(encoded([collection,value]))
                self.db.execute('INSERT OR IGNORE INTO records VALUES(?,?,?)',(key,collection,payload))
                self.db.execute('INSERT INTO occurrences VALUES(?,?,?)',(doc,count,key))
                if isinstance(value,dict):
                    for field,v in value.items():
                        if (field.lower() in ('id','entry','map','area','zone','timestamp') or field.lower().endswith(('_id','_guid'))) and isinstance(v,(str,int,float)):
                            s=str(v)
                            if len(s)<=512: self.db.execute('INSERT INTO links VALUES(?,?,?,?)',(doc,count,field,s))
                count+=1
                if count%10000==0: self.budget.space(CHUNK)
            self.db.execute("UPDATE documents SET status='parsed',rows=?,detail=NULL WHERE id=?",(count,doc)); self.db.commit()
        except BaseException:
            self.db.rollback(); raise
    def ingest(self,path,source='unspecified',reprocess=False):
        path=Path(path).absolute(); self.source(source)
        in_inbox=path.resolve().is_relative_to(self.root/'inbox')
        if not in_inbox and (self.root==path.resolve() or path.resolve() in self.root.parents or self.root in path.resolve().parents):
            raise ValueError('Input and storage directories must not overlap')
        run=uuid.uuid4().hex
        self.db.execute('INSERT INTO runs(id,source,input,started,status) VALUES(?,?,?,?,?)',(run,source,str(path),now(),'processing')); self.db.commit()
        failures=[]
        def files(p):
            if linked(p): raise readers.Held('Symlinks/junctions are not followed')
            if p.is_file(): yield p
            elif p.is_dir():
                for child in sorted(p.iterdir()):
                    yield from files(child)
            else: raise readers.Held('Input is not a regular file or directory')
        try:
            for f in files(path):
                try:
                    self.budget.member(); before=f.stat()
                    with f.open('rb') as stream: sha=self.preserve(stream,before.st_size)
                    after=f.stat()
                    if (after.st_size,after.st_mtime_ns)!=(before.st_size,before.st_mtime_ns):
                        raise readers.Held('Input changed during copying; stable original required')
                    self.receive(sha,f.relative_to(path).as_posix() if path.is_dir() else f.name,source,run,reprocess=reprocess)
                except (OSError,ValueError,RuntimeError) as e: failures.append({'file':str(f),'reason':str(e)})
        except (OSError,ValueError,RuntimeError) as e: failures.append({'file':str(path),'reason':str(e)})
        held=self.db.execute("SELECT count(DISTINCT d.id) FROM documents d JOIN receipts r ON r.document=d.id WHERE r.source=? AND d.status NOT IN ('parsed','container')",(source,)).fetchone()[0]
        status='needs-review' if failures or held else 'preserved'
        self.db.execute('UPDATE runs SET finished=?,status=?,detail=? WHERE id=?',(now(),status,readers.json_text(failures),run)); self.db.commit()
        report={'run':run,'source':source,'status':status,'copy_failures':failures,'summary':self.summary(source),'examples':self.examples(source),'incorporation':{'received':'L0: originals hashed locally','read':'L1: parsed files only; holds remain explicit','published':'not established by this run'}}
        atomic_json(self.root/'reports'/(run+'.json'),report)
        return report
    def summary(self,source=None):
        clause=' WHERE r.source=?' if source else ''; args=(source,) if source else ()
        rows=self.db.execute('SELECT DISTINCT d.* FROM documents d JOIN receipts r ON r.document=d.id'+clause,args).fetchall()
        return {'documents':len(rows),'parsed':sum(r['status']=='parsed' for r in rows),'containers':sum(r['status']=='container' for r in rows),'held':sum(r['status'] not in ('parsed','container') for r in rows),'record_occurrences':sum(r['rows'] for r in rows),'unique_record_payloads':self.db.execute('SELECT count(*) FROM records').fetchone()[0],'stored_bytes':self.db.execute('SELECT coalesce(sum(bytes),0) FROM artifacts').fetchone()[0]}
    def examples(self,source,limit=5):
        result=[];seen=set();sample_bytes=0
        rows=self.db.execute('SELECT r.locator,o.ordinal,x.collection,x.payload FROM receipts r JOIN occurrences o ON o.document=r.document JOIN records x ON x.hash=o.record WHERE r.source=? ORDER BY r.document,o.ordinal LIMIT 5000',(source,))
        for row in rows:
            sample_bytes+=len(row['payload'].encode('utf-8'))
            if sample_bytes>16*CHUNK:break
            value=json.loads(row['payload'])
            if not isinstance(value,dict):continue
            name=next((str(value[k]) for k in ('name','Name','title','Title','text','label','npcName','spellName') if value.get(k)),None)
            if not name or name in seen:continue
            seen.add(name)
            result.append({'collection':row['collection'],'id':value.get('id',value.get('entry')),'name':name[:200],'locator':row['locator'],'ordinal':row['ordinal']})
            if len(result)>=limit:break
        return result
    def query(self,value,field=None,limit=100):
        args=[str(value)]; predicate='l.value=?'
        if field: predicate+=' AND l.field=?'; args.append(field)
        args.append(min(1000,max(1,limit)))
        sql='''SELECT l.field,l.value,l.document,l.ordinal,r.collection,r.payload FROM links l JOIN occurrences o ON o.document=l.document AND o.ordinal=l.ordinal JOIN records r ON r.hash=o.record WHERE '''+predicate+' LIMIT ?'
        return [dict(row) for row in self.db.execute(sql,args)]


PRIVATE_KEYS = re.compile(r'(?:password|secret|token|email|account|username|avatar|author|comment|player|character|source_guid|target_guid|source_name|target_name|session|cookie|authorization)',re.I)
PRIVATE_TEXT = re.compile(r'[\w.+-]+@[\w-]+\.[A-Za-z]{2,}|(?:(?<![A-Za-z])[A-Z]:[\\/]|/(?:home|Users)/)|WTF[\\/]Account[\\/]|0x[0-9a-f]{16}|discord(?:app)?\.com/avatars/',re.I)


def screen(value):
    if isinstance(value,dict):
        for key,v in value.items():
            if PRIVATE_KEYS.search(key): raise readers.Held('Public selection contains a private/person field: '+key)
            screen(v)
    elif isinstance(value,list):
        for v in value: screen(v)
    elif isinstance(value,str) and PRIVATE_TEXT.search(value): raise readers.Held('Public selection contains an identity, credential-like value or local path')


def export(intake,source,policy_path,out):
    """Prepare immutable, reviewed-field data, never raw objects or auto-push."""
    identity_guard=privacy.configured(intake.root)
    source_meta=intake.source(source)
    policy=json.loads(Path(policy_path).read_text(encoding='utf-8-sig'))
    if policy.get('source')!=source or policy.get('publication')!='approved' or not policy.get('permission'):
        raise ValueError('Policy needs this source, publication=approved and recorded permission')
    rules=policy.get('collections',{})
    if not rules: raise ValueError('Policy must explicitly map public fields for each permitted collection')
    for key,rule in rules.items():
        if not re.fullmatch('[a-z][a-z0-9-]{0,63}',rule.get('kind','')) or not rule.get('fields'): raise ValueError('Invalid collection rule')
        if any(not isinstance(a,str) or not isinstance(b,str) for a,b in rule['fields'].items()): raise ValueError('Field mappings must be strings')
    title=policy.get('title',source); mode=policy.get('mode','Unspecified')
    screen([title,mode,policy['permission']])
    documents=[dict(r) for r in intake.db.execute('SELECT DISTINCT d.id,d.hash,d.format_name,d.status,d.rows FROM documents d JOIN receipts r ON r.document=d.id WHERE r.source=? ORDER BY d.id',(source,))]
    if not documents: raise ValueError('Source has no preserved inputs')
    snapshot=digest(encoded({'policy':policy,'documents':documents,'reader':readers.VERSION,'source_trust':source_meta.get('trust','unknown'),'known_caps':source_meta.get('known_caps',{}),'identity_rules':identity_guard.revision if identity_guard else None}))
    base=Path(out).resolve()
    if base==intake.root or base in intake.root.parents or intake.root in base.parents: raise ValueError('Public output must be separate from private storage')
    for parent in [base,*base.parents]:
        if (parent/'.git').exists(): raise ValueError('Prepare exports outside Git; publish.py delivers a verified snapshot to a dedicated worktree')
    final=base/'supplemental'/'research-intake'/source/snapshot
    if final.exists():
        verify_export(final,identity_guard); return final
    final.parent.mkdir(parents=True,exist_ok=True)
    stage=Path(tempfile.mkdtemp(prefix='.preparing-',dir=intake.root/'exports'))
    counts={}; excluded={}; hashes=[]; public_count=0; file=None; z=None; size=0; part=0; current=None
    def close():
        nonlocal z,file
        if z: z.close(); file.close(); z=None; file=None
    try:
        for doc in documents:
            if doc['status']!='parsed': continue
            for row in intake.db.execute('SELECT o.ordinal,r.hash,r.collection,r.payload FROM occurrences o JOIN records r ON r.hash=o.record WHERE o.document=? ORDER BY o.ordinal',(doc['id'],)):
                rule=rules.get(row['collection'])
                if not rule: excluded[row['collection']]=excluded.get(row['collection'],0)+1; continue
                raw=json.loads(row['payload'])
                if not isinstance(raw,dict): raise ValueError('Public collection requires object records')
                missing=set(rule['fields'].values())-raw.keys()
                if missing: raise ValueError('Public schema changed; required fields missing: '+','.join(sorted(missing)))
                selected={dest:raw[src] for dest,src in rule['fields'].items()}
                screen(selected)
                key=str(selected.get('id',selected.get('entry','evidence:'+row['hash'])))
                name=str(selected.get('name',selected.get('title',rule['kind']+' #'+key)))
                item={'schema':'ascension-research-evidence-1','type':rule['kind'],'record':dict(selected,id=key,name=name),'_modes':mode,'source':title,'evidence':{'artifact_sha256':doc['hash'],'document':doc['id'],'ordinal':row['ordinal'],'original_record_sha256':row['hash'],'reader':readers.VERSION,'source_id':source,'captured_at':policy.get('captured_at'),'interpretation':'source claim; not verified server data','trust':rule.get('trust',source_meta.get('trust','unknown')),'known_caps':rule.get('known_caps',source_meta.get('known_caps',{})),'observation_type':rule.get('observation_type','unknown'),'omitted_fields':sorted(set(raw)-set(rule['fields'].values()))}}
                # Omitted key names themselves may reveal identities (dynamic account keys).
                item['evidence']['omitted_fields_count']=len(item['evidence'].pop('omitted_fields'))
                if identity_guard: identity_guard.check(item)
                data=encoded(item)+b'\n'
                if len(data)>readers.MAX_RECORD: raise ValueError('Public record too large')
                kind=rule['kind']
                if z is None or current!=kind or size+len(data)>8*CHUNK:
                    close(); current=kind; size=0; part+=1
                    file=(stage/(kind+'-'+str(part).zfill(5)+'.jsonl.gz')).open('wb')
                    z=gzip.GzipFile(filename='',fileobj=file,mode='wb',mtime=0)
                if public_count >= policy.get('max_records',1000000): raise ValueError('Public record budget reached; narrow the policy or explicitly raise its limit')
                z.write(data); size+=len(data); counts[kind]=counts.get(kind,0)+1; public_count+=1
        close()
        for path in sorted(stage.iterdir()):
            with path.open('rb') as f: sha=hashlib.file_digest(f,'sha256').hexdigest()
            hashes.append({'path':path.name,'sha256':sha,'bytes':path.stat().st_size})
        if sum(x['bytes'] for x in hashes)>policy.get('max_compressed_bytes',128*CHUNK): raise ValueError('Public byte budget reached; narrow the policy or explicitly raise its limit')
        manifest={'schema':1,'source':source,'title':title,'snapshot':snapshot,'policy_sha256':digest(encoded(policy)),'permission':policy['permission'],'reader':readers.VERSION,'mode':mode,'public_records':public_count,'counts':counts,'unselected_collections':excluded,'artifacts':[{'sha256':d['hash'],'status':d['status'],'records':d['rows']} for d in documents],'files':hashes,'raw_storage':'Private originals retained locally; not included in public export','limits':'Observations and inferred relationships are not verified spawn, loot or vendor tables'}
        manifest['identity_scan']='required-and-passed' if identity_guard else 'not-configured'
        if identity_guard: identity_guard.check(manifest)
        screen(manifest)
        atomic_json(stage/'manifest.json',manifest)
        verify_export(stage,identity_guard)
        # Cross-volume safe: copy to an opaque sibling, validate, then rename.
        pending=final.with_name('.preparing-'+uuid.uuid4().hex)
        shutil.copytree(stage,pending); verify_export(pending,identity_guard); os.replace(pending,final)
        return final
    finally:
        close()
        if stage.resolve().is_relative_to(intake.root/'exports'): shutil.rmtree(stage)


def verify_export(path,identity_guard=None):
    path=Path(path); manifest=json.loads((path/'manifest.json').read_text(encoding='utf-8')); count=0
    if manifest.get('identity_scan')=='required-and-passed' and identity_guard is None: raise ValueError('This export requires the private identity scan; pass the configured intake root')
    if identity_guard: identity_guard.check(manifest)
    expected={'manifest.json'}
    screen(manifest)
    for item in manifest['files']:
        if Path(item['path']).name!=item['path']: raise ValueError('Unsafe manifest path')
        p=path/item['path']; expected.add(p.name)
        with p.open('rb') as f: sha=hashlib.file_digest(f,'sha256').hexdigest()
        if sha!=item['sha256'] or p.stat().st_size!=item['bytes']: raise ValueError('Export hash mismatch')
        with gzip.open(p,'rt',encoding='utf-8') as f:
            for line in readers.bounded_lines(f):
                value=readers.json_load(line); screen(value); count+=1
                if identity_guard: identity_guard.check(value)
    if count!=manifest['public_records'] or {p.name for p in path.iterdir()}!=expected: raise ValueError('Export accounting mismatch')
    return manifest


def main():
    ap=argparse.ArgumentParser(description='Preserve research donations locally, then prepare selected public evidence for AscensionDB.')
    ap.add_argument('--root',type=Path,default=DEFAULT_ROOT)
    sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('ingest'); p.add_argument('path',type=Path); p.add_argument('--source',default='unspecified'); p.add_argument('--reprocess',action='store_true')
    p.add_argument('--expanded-gib',type=float,default=32); p.add_argument('--reserve-gib',type=float,default=5); p.add_argument('--seconds',type=int,default=3600); p.add_argument('--max-records',type=int,default=10000000)
    sub.add_parser('status')
    p=sub.add_parser('register');p.add_argument('--source',required=True);p.add_argument('--metadata',type=Path,required=True)
    p=sub.add_parser('find'); p.add_argument('value'); p.add_argument('--field'); p.add_argument('--limit',type=int,default=100)
    p=sub.add_parser('export'); p.add_argument('--source',required=True); p.add_argument('--policy',type=Path,required=True); p.add_argument('--out',type=Path,required=True)
    p=sub.add_parser('verify'); p.add_argument('path',type=Path)
    a=ap.parse_args()
    if a.command=='verify': print(readers.json_text(verify_export(a.path,privacy.configured(a.root)))); return
    limits={}
    if a.command=='ingest':
        if min(a.expanded_gib,a.seconds,a.max_records)<=0 or a.reserve_gib<0: ap.error('Budgets must be positive')
        limits=dict(expanded=int(a.expanded_gib*1024**3),reserve=int(a.reserve_gib*1024**3),seconds=a.seconds,records=a.max_records)
    with Intake(a.root,**limits) as intake:
        if a.command=='ingest': result=intake.ingest(a.path,a.source,a.reprocess)
        elif a.command=='status': result=intake.summary()
        elif a.command=='register': intake.source(a.source,json.loads(a.metadata.read_text(encoding='utf-8-sig')));result={'source':a.source,'status':'registered privately'}
        elif a.command=='find': result=intake.query(a.value,a.field,a.limit)
        else: result={'export':str(export(intake,a.source,a.policy,a.out)),'status':'prepared; not pushed'}
        print(json.dumps(result,ensure_ascii=False,indent=2))
        if a.command=='ingest' and result['status']=='needs-review': raise SystemExit(2)


if __name__=='__main__': main()
