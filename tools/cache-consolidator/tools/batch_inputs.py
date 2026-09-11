"""Private batch delivery plans, consumed only while the publisher owns its lock."""
import hashlib, json, os, re, shutil, tempfile, csv
from pathlib import Path, PurePosixPath

HEX = re.compile(r'[a-f0-9]{64}')

def inventory(root):
    root=Path(root)
    if root.is_symlink() or root.is_junction():raise ValueError('Linked batch root')
    out={}
    for p in sorted(root.rglob('*')):
        if p.is_symlink() or p.is_junction():raise ValueError('Linked batch input')
        if p.is_file():out[p.relative_to(root).as_posix()]=hashlib.sha256(p.read_bytes()).hexdigest()
    return out


def rules_match(rules, revision):
    # Missing transcripts disable optimization; a stale transcript never proves
    # that old merger state was processed under new code.
    if not isinstance(rules,list) or not 1<=len(rules)<=1000:return False
    try:
        paths=[Path(item['path']).resolve() for item in rules]
        folder=Path(__file__).resolve().parent
        required=set(folder.glob('*.py'))|set(folder.glob('*.json'))
        if not required<=set(paths):return False
        digest=hashlib.sha256(b'published-bundle-v1\0')
        for path,item in zip(paths,rules):
            actual=hashlib.sha256(path.read_bytes()).hexdigest()
            if actual!=item['sha256']:return False
            digest.update(path.name.encode()+b'\0'+bytes.fromhex(actual))
        return digest.hexdigest()==revision
    except (OSError,ValueError,KeyError,TypeError):return False


def prepare(plan_path, inbox):
    if not plan_path:return []
    path=Path(plan_path).resolve();state=path.parent.parent
    plan=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(plan,dict) or plan.get('schema')!=1 or not 1<=len(plan.get('jobs',[]))<=8:raise ValueError('Invalid batch plan')
    if any(not isinstance(row,dict) or not HEX.fullmatch(row.get('bundle','')) for row in plan['jobs']):raise ValueError('Invalid batch entries')
    if len({row['bundle'] for row in plan['jobs']})!=len(plan['jobs']):raise ValueError('Duplicate bundle plan entries')
    inbox=Path(inbox).resolve();checked=[]
    batch_id=hashlib.sha256(json.dumps(sorted(row['bundle'] for row in plan['jobs'])).encode()).hexdigest()[:32]
    batch_root=inbox/('upload-batch-'+batch_id)
    if batch_root.resolve()!=batch_root:raise ValueError('Linked batch destination')
    for row in plan['jobs']:
        if not HEX.fullmatch(row.get('bundle','')) or not HEX.fullmatch(row.get('revision','')):raise ValueError('Invalid batch identity')
        source=Path(row['source']).absolute()
        if source.name!='accepted' or not re.fullmatch('validated-[a-f0-9]{32}',source.parent.name) or source.parent.parent.parent!=state:raise ValueError('Invalid private batch source')
        if source.resolve()!=source or not re.fullmatch('[a-f0-9-]{36}',source.parent.parent.name):raise ValueError('Unsafe batch source')
        name=row['destination']
        if not re.fullmatch('web-[a-f0-9]{32}',name):raise ValueError('Invalid inbox destination')
        destination=batch_root/name
        if destination.resolve()!=destination:raise ValueError('Linked inbox destination')
        expected=row['files']
        if not isinstance(expected,dict) or not 1<=len(expected)<=256:raise ValueError('Invalid batch file list')
        for rel,digest in expected.items():
            if not isinstance(rel,str) or '\\' in rel or ':' in rel or PurePosixPath(rel).is_absolute() or '..' in PurePosixPath(rel).parts or not HEX.fullmatch(digest):raise ValueError('Invalid batch file descriptor')
        actual=destination if destination.exists() else source
        if not actual.is_dir() or inventory(actual)!=expected:raise ValueError('Batch delivery checksum or file list differs')
        checked.append({**row,'source':source,'target':destination})
    expected_batch={row['destination']+'/'+rel:sha for row in checked for rel,sha in row['files'].items()}
    if batch_root.exists():
        if inventory(batch_root)!=expected_batch:raise ValueError('Existing batch differs')
    else:
        # Copy privately, verify the full batch, then expose it with ONE rename.
        # A failed copy or crash cannot leave a consumable prefix in the inbox.
        staging=Path(tempfile.mkdtemp(prefix='.batch-',dir=state))
        try:
            for row in checked:
                destination=staging/row['destination']
                if not destination.exists():shutil.copytree(row['source'],destination)
            if inventory(staging)!=expected_batch:raise ValueError('Private batch copy differs')
            inbox.mkdir(parents=True,exist_ok=True)
            os.rename(staging,batch_root)
        finally:
            if staging.exists():
                if staging.parent.resolve()!=state or staging.is_symlink() or staging.is_junction():raise ValueError('Unsafe staging cleanup')
                shutil.rmtree(staging)
    # A previously merged hash cannot establish evidence under changed rules.
    known=None
    try:
        ledger=json.loads((inbox.parent/'ledger.json').read_text(encoding='utf-8'))
        if not isinstance(ledger,dict):raise ValueError('Invalid ledger')
        with (inbox.parent/'merged'/'sources.tsv').open(encoding='utf-8',newline='') as f:
            reader=csv.DictReader(f,delimiter='\t')
            if not {'sha256','group','id','records'}<=set(reader.fieldnames or []):raise ValueError('Invalid sources')
            known=set(ledger)|{r['sha256'] for r in reader}
    except (OSError,ValueError,KeyError):pass
    for row in checked:
        row['batch_root']=batch_root;row['batch_files']=expected_batch
        row['rules']=plan.get('rules')
        row['no_op_eligible']=row.get('wdb_only') is True and rules_match(row['rules'],row['revision']) and known is not None and all(rel.endswith('.wdb') and sha not in known for rel,sha in row['files'].items())
    return checked


def incorporation(checked, work, out):
    """Require accepted WDB inputs in merger state and the exported view.
    Lua uses existing pipeline checks and never qualifies for skip evidence."""
    if not checked:return
    import intake
    work=Path(work);out=Path(out)
    wdb=[(row,rel,sha) for row in checked for rel,sha in row['files'].items() if rel.endswith('.wdb')]
    if wdb:
        ledger=json.loads((work/'ledger.json').read_text(encoding='utf-8'))
        with (work/'merged'/'sources.tsv').open(encoding='utf-8',newline='') as f:sources=list(csv.DictReader(f,delimiter='\t'))
        with (out/'sources.tsv').open(encoding='utf-8',newline='') as f:published={r['id']:r for r in csv.DictReader(f,delimiter='\t')}
        for row,rel,sha in wdb:
            entry=ledger.get(sha,{})
            group=intake.group_of(str(row['target']/rel))
            matches=[r for r in sources if r['sha256']==sha and r['group']==group]
            if not entry.get('standard') or not entry.get('clean_end') or not matches:raise ValueError('WDB input not incorporated into merger')
            if type(entry.get('records')) is not int or entry['records']<=0:row['no_op_eligible']=False
            for source in matches:
                exported=published.get(source['id'],{})
                if exported.get('sha256')!=sha or exported.get('records')!=source['records'] or source['records']!=str(entry['records']):raise ValueError('WDB input missing from published source ledger')



def record(checked, out, work=None):
    if checked and inventory(checked[0]['batch_root'])!=checked[0]['batch_files']:raise ValueError('Batch changed during consolidation')
    for row in checked:
        if inventory(row['target'])!=row['files']:raise ValueError('Batch input changed during consolidation')
    if checked:incorporation(checked,work or checked[0]['batch_root'].parent.parent,out)
    for row in checked:
        target=Path(out)/'contributions'/(row['bundle']+'.json');target.parent.mkdir(parents=True,exist_ok=True)
        # Only opaque hashes are public; no receipt IDs, private paths or tokens.
        proof={'schema':2,'bundle':row['bundle'],'revision':row['revision'],'no_op_eligible':row['no_op_eligible'] and rules_match(row['rules'],row['revision'])}
        temp=target.with_suffix('.tmp');temp.write_text(json.dumps(proof,sort_keys=True)+'\n',encoding='utf-8');os.replace(temp,target)
