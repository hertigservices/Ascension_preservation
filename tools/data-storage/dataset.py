"""Versioned public datasets: deterministic bounded packs, manifests and verified downloads.
Only pass audited public exports here. This module does not decide publication permission.
No donor code is executed; no archive paths are extracted.
"""
import argparse, hashlib, io, json, os, re, shutil, subprocess, tarfile, tempfile, time
import urllib.request
from pathlib import Path, PurePosixPath

SCHEMA = 'ascension-dataset-1'
BLOCK = 1024 * 1024
CHUNK = 64 * BLOCK
PACK_LIMIT = 512 * BLOCK

def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode('utf-8')

def digest(path):
    with Path(path).open('rb') as f:
        return hashlib.file_digest(f, 'sha256').hexdigest()

def atomic(path, value):
    path = Path(path); path.parent.mkdir(parents=True, exist_ok=True)
    pending = path.with_name(path.name + '.tmp')
    with pending.open('wb') as f:
        f.write(canonical(value)); f.flush(); os.fsync(f.fileno())
    pending.replace(path)

def safe_path(name):
    p = PurePosixPath(name)
    if not name or p.is_absolute() or '\\' in name or ':' in name or any(x in ('', '.', '..') for x in name.split('/')):
        raise ValueError('Invalid dataset path')
    if any(x.rstrip(' .') != x or x.split('.')[0].upper() in {'CON','PRN','AUX','NUL',*(f'COM{i}' for i in range(1,10)),*(f'LPT{i}' for i in range(1,10))} for x in p.parts):
        raise ValueError('Nonportable dataset path')
    return name

def validate(m):
    if m.get('schema') != SCHEMA or not re.fullmatch('[a-z0-9][a-z0-9-]{0,79}', m.get('dataset','')):
        raise ValueError('Invalid dataset manifest')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',m.get('repository','')) or not re.fullmatch(r'[a-z0-9-]+',m.get('release','')):raise ValueError('Invalid release location')
    identity = dict(m); snapshot = identity.pop('snapshot', None)
    if hashlib.sha256(canonical(identity)).hexdigest() != snapshot:
        raise ValueError('Manifest snapshot checksum mismatch')
    seen = set()
    for name, entry in m['files'].items():
        safe_path(name)
        if name.casefold() in seen: raise ValueError('Case-colliding dataset paths')
        seen.add(name.casefold())
        if not re.fullmatch('[0-9a-f]{64}', entry['sha256']): raise ValueError('Invalid checksum')
        if sum(p['bytes'] for p in entry['parts']) != entry['bytes']: raise ValueError('File size mismatch')
        for p in entry['parts']:
            if not re.fullmatch('[0-9a-f]{64}',p['sha256']) or not 0 < p['bytes'] <= CHUNK: raise ValueError('Invalid part')
            if p['pack'] not in m['packs']: raise ValueError('Missing pack')
    for name, pack in m['packs'].items():
        if not re.fullmatch(r'pack-[0-9a-f]{64}\.tar', name) or name != 'pack-'+pack['sha256']+'.tar': raise ValueError('Invalid pack')
        if not 0 < pack['bytes'] <= PACK_LIMIT: raise ValueError('Oversized pack')
        url = pack.get('url','')
        if url and not url.startswith('https://github.com/'+m['repository']+'/releases/download/'):
            raise ValueError('Pack must use the declared GitHub repository')
    return m

class Segment(io.RawIOBase):
    def __init__(self,path,offset,length): self.f=Path(path).open('rb');self.f.seek(offset);self.left=length
    def read(self,n=-1):
        n=self.left if n<0 else min(n,self.left);data=self.f.read(n);self.left-=len(data);return data
    def close(self): self.f.close();super().close()

def prepare(root, output, dataset, repository='hertigservices/ascension-data', prefix='', previous=None):
    root=Path(root).resolve();output=Path(output).resolve()
    if (root/'.git').exists():raise ValueError('Pass an audited export directory, not an entire Git checkout')
    if output.is_relative_to(root): raise ValueError('Package staging must be outside the input tree')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repository): raise ValueError('Invalid repository')
    if prefix: safe_path(prefix)
    output.mkdir(parents=True,exist_ok=True)
    files={};objects={}
    for path in sorted(root.rglob('*')):
        if path.is_symlink() or (hasattr(path,'is_junction') and path.is_junction()): raise ValueError('Links cannot enter a dataset')
        if not path.is_file(): continue
        name=safe_path((prefix+'/' if prefix else '')+path.relative_to(root).as_posix())
        before=path.stat();whole=hashlib.sha256();parts=[]
        with path.open('rb') as f:
            offset=0
            while True:
                data=f.read(CHUNK)
                if not data: break
                whole.update(data);h=hashlib.sha256(data).hexdigest()
                objects.setdefault(h,(path,offset,len(data)));parts.append({'sha256':h,'bytes':len(data)});offset+=len(data)
        after=path.stat()
        if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns): raise ValueError('Input changed during packaging')
        files[name]={'sha256':whole.hexdigest(),'bytes':offset,'parts':parts}
    packs={};locations={};groups={}
    for h in sorted(objects): groups.setdefault(h[:2],[]).append(h)
    # Hash buckets keep unrelated additions from repacking the whole collection.
    for group in groups.values():
        batches=[];batch=[];size=10240
        for h in group:
            n=512+((objects[h][2]+511)//512)*512
            if size+n>PACK_LIMIT:
                batches.append(batch);batch=[];size=10240
            batch.append(h);size+=n
        if batch:batches.append(batch)
        for batch in batches:
            fd,temp=tempfile.mkstemp(prefix='pack-',suffix='.tmp',dir=output);os.close(fd);temp=Path(temp)
            try:
                with tarfile.open(temp,'w',format=tarfile.USTAR_FORMAT) as tar:
                    for h in batch:
                        path,offset,n=objects[h];info=tarfile.TarInfo(h);info.size=n;info.mode=0o644
                        # Recheck the exact segment while writing; input mutation fails closed.
                        with Segment(path,offset,n) as segment:
                            data=segment.read()
                            if hashlib.sha256(data).hexdigest()!=h: raise ValueError('Input changed during packaging')
                            tar.addfile(info,io.BytesIO(data))
                h=digest(temp);name='pack-'+h+'.tar';target=output/name
                if target.exists():
                    if digest(target)!=h: raise ValueError('Corrupt existing package')
                    temp.unlink()
                else:temp.replace(target)
                packs[name]={'sha256':h,'bytes':target.stat().st_size}
                for obj in batch: locations[obj]=name
            finally:
                if temp.exists():temp.unlink()
    old=validate(previous)['packs'] if previous else {}
    for name,pack in packs.items():
        if name in old and old[name].get('url'):pack['url']=old[name]['url']
    for entry in files.values():
        for part in entry['parts']:part['pack']=locations[part['sha256']]
    # Tag is based on content before URLs; unchanged packs can retain older release URLs.
    identity={'schema':SCHEMA,'dataset':dataset,'repository':repository,'files':files,'packs':{n:{'sha256':v['sha256'],'bytes':v['bytes']} for n,v in packs.items()}}
    tag='data-'+dataset+'-'+hashlib.sha256(canonical(identity)).hexdigest()[:20]
    content={'schema':SCHEMA,'dataset':dataset,'repository':repository,'files':files,'packs':packs}
    for name,pack in packs.items():pack.setdefault('url',f'https://github.com/{repository}/releases/download/{tag}/{name}')
    content['release']=tag;content['snapshot']=hashlib.sha256(canonical(content)).hexdigest()
    validate(content);atomic(output/'dataset.json',content)
    return content

def download(url,path,sha,size):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    if path.exists() and path.stat().st_size==size and digest(path)==sha:return
    partial=path.with_name(path.name+'.part');offset=partial.stat().st_size if partial.exists() else 0
    if offset>=size:partial.unlink();offset=0
    req=urllib.request.Request(url,headers={'User-Agent':'AscensionPreservation/1.0',**({'Range':f'bytes={offset}-'} if offset else {})})
    with urllib.request.urlopen(req,timeout=120) as response:
        resume=offset and response.status==206 and response.headers.get('Content-Range','').startswith(f'bytes {offset}-')
        if response.status==206 and not resume: raise ValueError('Unexpected partial response')
        received=offset if resume else 0
        with partial.open('ab' if resume else 'wb') as f:
            while block:=response.read(BLOCK):
                received+=len(block)
                if received>size:raise ValueError('Download exceeds declared size')
                f.write(block)
    if partial.stat().st_size!=size:raise ValueError('Incomplete download; retry resumes it')
    if digest(partial)!=sha:
        partial.unlink();raise ValueError('Download checksum mismatch; retry starts fresh')
    partial.replace(path)

def restore(m, destination, cache, select='', local_packs=None):
    validate(m);destination=Path(destination).resolve();cache=Path(cache).resolve();cache.mkdir(parents=True,exist_ok=True)
    selected={p:e for p,e in m['files'].items() if not select or p==select or p.startswith(select.rstrip('/')+'/')}
    if not selected:raise ValueError('No files match the selected collection')
    needed={p['pack'] for e in selected.values() for p in e['parts']}
    for name in sorted(needed):
        pack=m['packs'][name];target=cache/name
        if local_packs:
            src=Path(local_packs)/name
            if src.stat().st_size!=pack['bytes'] or digest(src)!=pack['sha256']:raise ValueError('Corrupt package')
            if src.resolve()!=target.resolve():shutil.copyfile(src,target)
        else:download(pack['url'],target,pack['sha256'],pack['bytes'])
    restored=0
    for name,e in selected.items():
        target=destination/name
        # Reject existing links in every ancestor, even when they resolve back inside destination.
        current=target
        while current!=destination.parent:
            if current.is_symlink() or (hasattr(current,'is_junction') and current.is_junction()):raise ValueError('Destination contains a link')
            if current==destination:break
            current=current.parent
        target.parent.mkdir(parents=True,exist_ok=True)
        if target.exists() and target.stat().st_size==e['bytes'] and digest(target)==e['sha256']:continue
        pending=target.with_name(target.name+'.download')
        if pending.is_symlink():raise ValueError('Unsafe temporary destination')
        with pending.open('wb') as out:
            for part in e['parts']:
                with tarfile.open(cache/part['pack'],'r:') as tar:
                    member=tar.getmember(part['sha256'])
                    if not member.isfile() or member.size!=part['bytes']:raise ValueError('Invalid package member')
                    f=tar.extractfile(member);h=hashlib.sha256();count=0
                    while block:=f.read(BLOCK):out.write(block);h.update(block);count+=len(block)
                    if count!=part['bytes'] or h.hexdigest()!=part['sha256']:raise ValueError('Part checksum mismatch')
            out.flush();os.fsync(out.fileno())
        if pending.stat().st_size!=e['bytes'] or digest(pending)!=e['sha256']:raise ValueError('Restored file checksum mismatch')
        pending.replace(target);restored+=1
    return {'selected':len(selected),'restored':restored,'packs':len(needed)}

def gh(*args):
    executable=shutil.which('gh') or str(Path(os.environ.get('ProgramFiles','C:/Program Files'))/'GitHub CLI/gh.exe')
    p=subprocess.run([executable,*args],capture_output=True,text=True,encoding='utf-8')
    if p.returncode:raise RuntimeError(p.stderr.strip())
    return p.stdout

def find_release(repo, tag):
    """Find a release across every API page, including authenticated drafts."""
    pages=json.loads(gh('api',f'repos/{repo}/releases?per_page=100','--paginate','--slurp'))
    return next((release for page in pages for release in page if release['tag_name']==tag),None)

def await_release(repo, tag, attempts=6):
    """Allow GitHub's release-list API to catch up after draft creation."""
    for attempt in range(attempts):
        release=find_release(repo,tag)
        if release is not None:return release
        if attempt+1<attempts:time.sleep(min(2**attempt,8))
    raise RuntimeError(f'Created Release {tag!r} is not visible through the GitHub API')

def publish(staging):
    staging=Path(staging);m=validate(json.loads((staging/'dataset.json').read_text(encoding='utf-8-sig')))
    repo=m['repository'];tag=m['release'];own={n:v for n,v in m['packs'].items() if '/'+tag+'/' in v['url']}
    if len(own)+1>1000:raise ValueError('Release has too many assets; divide the dataset')
    for name,pack in own.items():
        if (staging/name).stat().st_size!=pack['bytes'] or digest(staging/name)!=pack['sha256']:raise ValueError('Corrupt staging package')
    release=find_release(repo,tag)
    if release is None:
        gh('release','create',tag,'--repo',repo,'--draft','--title',f"{m['dataset']} dataset {m['snapshot'][:12]}",'--notes','Verified public dataset. Download dataset.json and use tools/data-storage/dataset.py to retrieve selected files or the complete collection.')
        release=await_release(repo,tag)
    # --paginate --slurp handles releases larger than one API page.
    pages=json.loads(gh('api',f"repos/{repo}/releases/{release['id']}/assets?per_page=100",'--paginate','--slurp'))
    assets={a['name']:a for page in pages for a in page}
    expected={**own,'dataset.json':{'sha256':digest(staging/'dataset.json'),'bytes':(staging/'dataset.json').stat().st_size}}
    for index,(name,entry) in enumerate(expected.items()):
        if index%20==0:print(f'Uploading release assets: {index}/{len(expected)}',flush=True)
        if name not in assets:
            if not release['draft']:raise ValueError('Published release is incomplete; do not mutate it')
            gh('release','upload',tag,str(staging/name),'--repo',repo)
    pages=json.loads(gh('api',f"repos/{repo}/releases/{release['id']}/assets?per_page=100",'--paginate','--slurp'))
    assets={a['name']:a for page in pages for a in page}
    for name,e in expected.items():
        a=assets.get(name,{})
        if a.get('size')!=e['bytes'] or a.get('digest')!='sha256:'+e['sha256']:raise ValueError('Remote asset digest mismatch or unavailable; release remains unpromoted')
    if release['draft']:gh('release','edit',tag,'--repo',repo,'--draft=false','--latest=false')
    return m

def main():
    ap=argparse.ArgumentParser(description='Preserve public datasets outside Git; verify every downloaded byte.')
    sub=ap.add_subparsers(dest='command',required=True)
    p=sub.add_parser('prepare');p.add_argument('--input',type=Path,required=True);p.add_argument('--out',type=Path,required=True);p.add_argument('--dataset',required=True);p.add_argument('--prefix',default='');p.add_argument('--repository',default='hertigservices/ascension-data');p.add_argument('--previous',type=Path)
    p=sub.add_parser('publish');p.add_argument('staging',type=Path)
    p=sub.add_parser('download');p.add_argument('manifest',type=Path);p.add_argument('--out',type=Path,required=True);p.add_argument('--cache',type=Path,default=Path('.ascension-downloads'));p.add_argument('--select',default='');p.add_argument('--local-packs',type=Path)
    a=ap.parse_args()
    if a.command=='prepare':
        m=prepare(a.input,a.out,a.dataset,a.repository,a.prefix,json.loads(a.previous.read_text()) if a.previous else None);print(json.dumps({'manifest':str(a.out/'dataset.json'),'files':len(m['files']),'packs':len(m['packs'])}))
    elif a.command=='publish':print(json.dumps({'snapshot':publish(a.staging)['snapshot']}))
    else:print(json.dumps(restore(json.loads(a.manifest.read_text(encoding='utf-8-sig')),a.out,a.cache,a.select,a.local_packs)))
if __name__=='__main__':main()
