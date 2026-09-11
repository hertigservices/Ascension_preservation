"""Durable manual requests and exact-input completion receipts for the shared worker."""
import hashlib,json,os,time,uuid,subprocess
from pathlib import Path

def read(path,default=None):
    try:return json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError,ValueError):return default

def write(path,data):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    temp.write_text(json.dumps(data,indent=2),encoding='utf-8');os.replace(temp,path)

def enqueue(work,push=True,tidy=True,rename=True,pristine=(),tidy_only=False):
    for existing in (Path(work)/'manual-queue').glob('*.json'):
        item=read(existing,{})
        if item.get('status') in ('queued','processing') and all(item.get(k)==v for k,v in {'push':bool(push),'tidy':bool(tidy),'rename':bool(rename),'tidy_only':bool(tidy_only)}.items()):return existing
    path=Path(work)/'manual-queue'/(str(uuid.uuid4())+'.json')
    write(path,{'schema':1,'id':path.stem,'created':time.time(),'status':'queued','push':bool(push),'tidy':bool(tidy),'rename':bool(rename),'pristine':list(pristine),'tidy_only':bool(tidy_only)})
    return path

def tree(path):
    path=Path(path)
    if path.is_symlink() or path.is_junction():raise ValueError('Linked input')
    files=[path] if path.is_file() else sorted(path.rglob('*'))
    result={}
    for p in files:
        if p.is_symlink() or p.is_junction():raise ValueError('Linked input')
        if p.is_file():
            h=hashlib.sha256()
            with p.open('rb') as f:
                for chunk in iter(lambda:f.read(1024*1024),b''):h.update(chunk)
            result['.' if p==path else p.relative_to(path).as_posix()]=h.hexdigest()
    return result

def snapshot(inbox):
    inbox=Path(inbox);result={}
    for p in sorted(inbox.iterdir()) if inbox.exists() else []:
        if p.name=='archive' or p.name.startswith('.'):continue
        try:
            files=tree(p)
            if files:result[p.name]=files
        except (OSError,ValueError):continue
    return result

def file_completed(work,snapshot,commit,retained=None):
    # A successful remote push is required before automatically filing inputs.
    path=Path(work)/'completed-inputs.json';data=read(path,{'schema':1,'roots':{}})
    if not isinstance(data,dict) or data.get('schema')!=1 or not isinstance(data.get('roots'),dict):data={'schema':1,'roots':{}}
    for name,files in snapshot.items():data['roots'][name]={'files':files,'commit':commit,'completed':time.time(),'retained_unparsed':(retained or {}).get(name,[])}
    write(path,data)

def archive_completed(work):
    work=Path(work);inbox=(work/'_inbox').resolve();path=work/'completed-inputs.json';data=read(path,{'schema':1,'roots':{}})
    if not isinstance(data,dict) or data.get('schema')!=1 or not isinstance(data.get('roots'),dict):data={'schema':1,'roots':{}}
    moved=[]
    # Human-readable index of unresolved bytes retained inside archived originals.
    def report():
        lines=['# Retained files needing parser attention', '',
               'Supported data from these bundles was published. These listed files',
               'were inspected but not fully decoded. Their originals remain intact',
               'inside the archived bundles, which remain available to intake.', '']
        for name,entry in sorted(data['roots'].items()):
            if not entry.get('archived') or not entry.get('retained_unparsed'):continue
            lines += ['## '+name.replace('\n',' '), '', 'Archive: '+entry['archived'],
                      'Published commit: '+entry['commit'], '']
            for item in entry['retained_unparsed']:
                lines += ['- '+item['file'].replace('\n',' ')+' â€” '+item['reason'],
                          '  SHA-256: '+item['sha256']]
            lines.append('')
        target=inbox/'archive'/'RETAINED-FILES.md'
        if target.parent.exists() and target.parent.resolve()==target.parent:
            temp=target.with_name('.retained-'+uuid.uuid4().hex+'.tmp')
            temp.write_text('\n'.join(lines)+'\n',encoding='utf-8');os.replace(temp,target)
    for name,entry in list(data['roots'].items()):
        if Path(name).name!=name or name=='archive':continue
        src=inbox/name;dst=inbox/'archive'/time.strftime('%Y-%m')/name
        if src.resolve()!=src or dst.resolve()!=dst or not src.exists():continue
        try:
            if tree(src)!=entry['files']:continue
            dst.parent.mkdir(parents=True,exist_ok=True)
            if dst.exists():continue # Preserve both identity and evidence on collisions.
            os.rename(src,dst)
            if tree(dst)!=entry['files']:
                if not src.exists():os.rename(dst,src)
                continue
            entry['archived']=dst.relative_to(inbox).as_posix();moved.append(name)
            write(path,data)
        except (OSError,ValueError):continue
    report()
    return moved

def process_manual(config):
    work=Path(config['work']);folder=work/'manual-queue'
    pending=[(p,read(p,{})) for p in folder.glob('*.json')]
    pending=sorted(((p,r) for p,r in pending if r.get('status') in ('queued','processing') and r.get('next_attempt',0)<=time.time()),key=lambda pair:pair[1].get('created',0))
    if not pending:return False
    path,item=pending[0];item.update(status='processing',started=time.time());write(path,item)
    env=os.environ.copy();env.pop('ASCENSION_BATCH_PLAN',None)
    env.update(ASCENSION_MANUAL_REQUEST=str(path),ASCENSION_CACHE_WORK=str(work),ASCENSION_CACHE_OUT=config['out'],CONSOLIDATOR_REPO=config['publish_repo'])
    log=path.with_suffix('.log')
    with log.open('ab') as f:
        result=subprocess.run([config.get('python',__import__('sys').executable),'-u','-B',config['publisher']]+(['--push'] if item['push'] else []),env=env,stdin=subprocess.DEVNULL,stdout=f,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    attempts=item.get('attempts',0)+(1 if result.returncode not in (0,75) else 0)
    status='done' if result.returncode==0 else 'queued' if attempts<5 else 'failed'
    item.update(status=status,attempts=attempts,next_attempt=time.time()+(20 if result.returncode==75 else [60,300,900,1800,3600][min(attempts,4)]),finished=time.time(),exit_code=result.returncode)
    write(path,item);return True

def manual_request(work,path):
    if not path:return None
    p=Path(path).resolve();folder=(Path(work)/'manual-queue').resolve()
    if p.parent!=folder or str(uuid.UUID(p.stem))!=p.stem:raise ValueError('Invalid manual request path')
    item=read(p,{})
    if item.get('schema')!=1 or item.get('status')!='processing':raise ValueError('Inactive manual request')
    return item


def eligible_completed(work,out,inputs,retained=None):
    """File only roots whose supported contents reached the merger/export ledger.
    Unknown-only roots and failed archives remain pending. Raw evidence is kept.
    With an explicit retained report, mixed bundles can be filed after every WDB
    is ledgered and exported, while undecoded bytes are identified for later work.
    The default remains strict for callers that cannot preserve that distinction.
    """
    import csv,intake,luamerge
    work=Path(work);out=Path(out);ledger=read(work/'ledger.json',{})
    def sources(path):
        try:
            with path.open(encoding='utf-8',newline='') as f:return list(csv.DictReader(f,delimiter='\t'))
        except OSError:return []
    merged=sources(work/'merged'/'sources.tsv');exported={r['id']:r for r in sources(out/'sources.tsv')}
    lua=read(work/'merged'/'lua'/'state.json',{}).get('sources',{})
    archives={}
    for marker in (work/'extracted').glob('*/.intake-sha256'):
        try:archives[marker.read_text(encoding='utf-8').strip()]=marker.parent
        except OSError:pass
    def check(p,sha,depth=0):
        if depth>12:return False
        if p.suffix.lower()=='.wdb':
            entry=ledger.get(sha,{})
            matches=[r for r in merged if r.get('sha256')==sha and r.get('group')==intake.group_of(str(p))]
            accounted = bool(entry.get('sha256')==sha and type(entry.get('records')) is int
                             and entry['records']>=0 and matches and all(
                exported.get(r['id'],{}).get('sha256')==sha
                and exported.get(r['id'],{}).get('records')==str(entry['records'])
                for r in matches))
            if entry.get('standard') and entry.get('clean_end'):
                # Older ledgers/tests may omit the redundant embedded SHA.
                return bool(matches and all(exported.get(r['id'],{}).get('sha256')==sha and exported.get(r['id'],{}).get('records')==str(entry.get('records')) for r in matches))
            if retained is not None and accounted and entry.get('note') and (
                    entry.get('standard') is False or entry.get('clean_end') is False):
                pending.append({'file':str(p.relative_to(work)).replace('\\','/'),
                                'sha256':sha,'reason':'Unsupported cache format' if entry.get('standard') is False else 'Incomplete cache record stream'})
                return None  # Retained evidence, never proof of successful decoding.
            return False
        key,spec=luamerge.spec_for(p.name)
        if spec:
            source=lua.get(sha,{})
            # Zero leaves can be a successful merge (empty addon database or
            # records already represented). Require the metadata written only
            # after the merger returns, not a missing-global/parse-error entry.
            successful_zero = source.get('records') == 0 and {
                'filename','group','realm','mode','slug','captured','submission'
            } <= source.keys()
            return (source.get('spec')==key and not source.get('error')
                    and not source.get('empty') and type(source.get('records')) is int
                    and (source['records']>0 or successful_zero))
        if intake.split_archive(p.name)[1] in intake.ARCHIVE_EXT:
            folder=archives.get(sha)
            if folder is None:return False
            checks=[check(folder/rel,digest,depth+1) for rel,digest in tree(folder).items() if not rel.startswith('.intake-')]
            return any(v is True for v in checks) and all(v is not False for v in checks)
        return None
    good={}
    for name,files in inputs.items():
        root=work/'_inbox'/name;pending=[]
        try:
            if tree(root)!=files:continue
            checks=[check(root if rel=='.' else root/rel,sha) for rel,sha in files.items()]
            if any(v is True for v in checks) and all(v is not False for v in checks):
                good[name]=files
                if retained is not None and pending:retained[name]=pending
        except (OSError,ValueError,KeyError):continue
    return good
