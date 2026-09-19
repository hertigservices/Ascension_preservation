"""Build a static, attributed catalog from a clean published ascension-data checkout.
Only changed source blobs are parsed again. No private inbox or runtime input is read.
"""
import argparse, collections, csv, gzip, hashlib, html, json, os, re, shutil, subprocess, sys, tempfile, time, unicodedata
from pathlib import Path
from atlas import build_atlas
from grouped_search import build_grouped_search
from datasets import resolve as resolve_datasets
import attribution
BASE=Path(__file__).resolve().parent
SCHEMA='ascensiondb-1'
DISPLAY_ICON_PATH='cachedata/dbc/item_display_icons.tsv.gz'
KIND={'itemcache':'item','creaturecache':'npc','gameobjectcache':'gameobject','questcache':'quest','npccache':'gossip','pagetextcache':'page','itemnamecache':'item-name','creatures':'world-creature','gameobjects':'world-object','advancement':'advancement','vendors':'vendor','gossips':'gossip-observation','item_display_icons':'display-icon'}
LABELS={'route-npc':'Instance reference NPCs','instance-floor':'Instance floor maps','item':'Items','npc':'Creatures','gameobject':'World objects','quest':'Quests','spell':'Spells','achievement':'Achievements','talent':'Talent trees','guide':'Guides','raw-variant':'Captured variants'}
def dump(v): return json.dumps(v,ensure_ascii=False,separators=(',',':'))
def norm(s): return ''.join(c for c in unicodedata.normalize('NFKD',str(s)) if not unicodedata.combining(c)).lower().replace('ß','ss')
def tokens(s): return re.findall(r'[^\W_]+',norm(s))
def zipped(path,v):
    data=dump(v).encode(); path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('wb') as f:
        with gzip.GzipFile(fileobj=f,mode='wb',mtime=0,compresslevel=6) as z: z.write(data)
    return len(data)
def git(root,*args): return subprocess.check_output(['git','-C',str(root),*args])
def source_type(name):
    if '/research-intake/' in name: return 'Research contributions'
    if '/instance-route-maps/' in name: return 'Exiles route planner'
    if '/lootcollector/' in name: return 'LootCollector'
    if '/exiles-db/' in name: return 'Exiles DB'
    if '/bisbeard/' in name: return 'BisBeard'
    if '/lua/' in name or '/catalogue/' in name: return 'Addon observations'
    return 'Client captures'
def adapter(name):
    if '/assets/' in name: return 'reference','Preserved website asset'
    if Path(name).name.startswith('npc-zone-claims.tsv'): return 'reference','Attributed NPC-to-area claims consumed by the zone index'
    if name.endswith(('.tsv','.tsv.gz')): return 'tsv','Every row searchable; all original fields retained'
    if name.endswith('.jsonl.gz'): return 'jsonl','Every record searchable; source claims remain separate'
    if name.endswith('.json') and '/lua/' in name: return 'json','Structured addon reference; all top-level entries retained'
    if name.endswith(('.wdb.gz','.pack.gz')): return 'reference','Binary capture preserved for download; raw index describes variants'
    if name.endswith(('.lua','.lua.gz')): return 'reference','Published addon/code reference; not executed or interpreted as confirmed game facts'
    if name.endswith(('.sqlite.gz','.dexie.gz')): return 'reference','Original supplemental database; searchable JSON export indexed separately'
    return 'reference','Documentation, provenance, inventory or supporting file'
def textopen(p): return gzip.open(p,'rt',encoding='utf-8',newline='') if p.suffix=='.gz' else p.open(encoding='utf-8',newline='')
def rows(path,kind):
    with textopen(path) as f:
        if kind=='tsv':
            csv.field_size_limit(100000000)
            for r in csv.DictReader(f,delimiter='\t',quoting=csv.QUOTE_NONE if '/catalogue/' in path.as_posix() or '/lootcollector/' in path.as_posix() else csv.QUOTE_MINIMAL):
                if None in r: raise ValueError('Malformed TSV row with extra columns')
                yield r
        elif kind=='jsonl':
            for l in f:
                if l.strip():
                    try: yield json.loads(l)
                    except json.JSONDecodeError as exc:
                        if '/coa-databank/' not in path.as_posix() or '/databank/palette/' not in path.as_posix() or not exc.msg.startswith('Invalid \\escape'):raise
                        repaired=re.sub(r'(?<!\\)(?:\\\\)*\\(?!["\\/bfnrtu])',lambda m:m[0]+'\\',l)
                        yield json.loads(repaired)
        else:
            v=json.load(f)
            if isinstance(v,list): yield from v
            elif isinstance(v,dict):
                for k,d in v.items(): yield {'key':k,'value':d}
            else: yield {'value':v}
def identify(path,r,i):
    if not isinstance(r,dict): r={'value':r}
    stem=Path(path).name.split('.')[0]; source=source_type(path)
    kind=KIND.get(stem,stem.rstrip('s') or 'reference')
    if '/research-intake/' in path: kind=str(r.get('type') or 'research-evidence'); source=str(r.get('source') or 'Research contributions')
    if '/raw/' in path: kind='raw-variant'
    if '/exiles-db/' in path: kind=str(r.get('type') or {'talents':'talent','changes':'change'}.get(stem,kind))
    if '/bisbeard/' in path: kind='planner-item'
    if '/instance-route-maps/' in path: kind=str(r['type'])
    inner=r.get('record',r)
    if not isinstance(inner,dict): inner=r
    identifier=str(inner.get('entry',inner.get('id',r.get('key',r.get('planner_id',f'row:{i}')))))
    name=next((str(inner[k]) for k in ('name','Name','title','Title','page_title','text','Text','label','spellName','npcName') if inner.get(k)),None)
    if not name:
        v=r.get('value');name=next((str(v[k]) for k in ('name','Name','title') if isinstance(v,dict) and v.get(k)),None)
    if path==DISPLAY_ICON_PATH:
        if inner.get('displayid') not in (None,''):
            identifier=str(inner['displayid'])
        name=icon_name(inner.get('icon')) or name
    name=name or f'{LABELS.get(kind,kind.replace("-"," ").title())} #{identifier}'
    if path.endswith('/lootcollector/pins.tsv'):
        identifier=str(r.get('item',''));name=str(r.get('type','Loot'))+' item #'+identifier;kind='loot-pin'
    mode=path.split('/')[2] if path.startswith('cachedata/by-mode/') else str(r.get('_modes',r.get('modes','Unspecified')))
    attributed=attribution.resolve(path,r)
    if attributed is not None: mode=attribution.mode_text(attributed)
    return identifier,name[:500],kind,mode,source,r

# Search-row icons: collection -> the ID namespace its icon is keyed by. BisBeard planner IDs are deliberately
# absent; they are not item IDs.
ICON_KINDS={'item':'item','item-name':'item','loot-pin':'item','spell':'spell','achievement':'achievement','currency':'currency','display-icon':'display'}
def icon_name(value):
    """Normalise an icon reference to a published file stem: HTML entities decoded, lowercase, no folder, no image extension."""
    n=html.unescape(str(value or '')).strip().replace('\\','/').rsplit('/',1)[-1].lower()
    for ext in ('.blp','.tga','.png','.jpg','.webp'):
        if n.endswith(ext): n=n[:-len(ext)]
    return n.strip()
def csv_rows(path,delimiter=','):
    csv.field_size_limit(100000000)
    with textopen(path) as f: yield from csv.DictReader(f,delimiter=delimiter)
class IconIndex:
    """Icon names for search rows. A name is only used when that icon file is itself published, so a result never
    references an image the archive does not hold.

    Item icons come only from captured client data: item display icons joined through the union item cache. The
    Exiles site and its database export assign item icons that disagree with the client for 15-18% of the items both
    cover (glyphs drawn as bracers, a chestplate as a cloak), as a stock display-id join against Ascension's
    renumbered ItemDisplayInfo would. Neither is used for items; a missing icon is better than a wrong one.
    Spell, achievement and currency icons come from Exiles spell pages, overridden by a published export's icon map."""
    def __init__(self,data,paths):
        paths=sorted(paths);present=set(paths)
        self.available=set();self.maps={k:{} for k in set(ICON_KINDS.values())}
        for p in paths:
            m=re.fullmatch(r'supplemental/[^/]+/assets/icons/([^/]+)\.png',p)
            if m: self.available.add(m.group(1).lower())
        exports=sorted(p.rsplit('/',1)[0] for p in paths if re.fullmatch(r'supplemental/[^/]+/[0-9a-f]{16,64}/icon-map\.csv\.gz',p))
        for root in exports:
            for index in (f'{root}/ASSET_INDEX.csv.gz',f'{root}/ASSET_INDEX.csv'):
                if index in present:
                    for r in csv_rows(data/index):
                        m=re.fullmatch(r'static/icons-clean/([^/]+)\.png',r.get('path') or '')
                        if m: self.available.add(m.group(1).lower())
        for p in paths:
            if re.fullmatch(r'supplemental/exiles-db/[0-9a-f]+/spells\.jsonl\.gz',p):
                for r in rows(data/p,'jsonl'):
                    if str(r.get('type'))=='spell' and r.get('icon') and r.get('id') is not None: self.maps['spell'][str(r['id'])]=icon_name(r['icon'])
        for root in exports:
            # A database export's own icon assignments, precomputed as kind,id,icon. Its item rows are ignored.
            for r in csv_rows(data/f'{root}/icon-map.csv.gz'):
                kind=r.get('kind','')
                if kind in ('spell','achievement','currency') and r.get('id') and r.get('icon'): self.maps[kind][str(r['id'])]=icon_name(r['icon'])
        if 'cachedata/dbc/item_display_icons.tsv.gz' in present:
            for r in csv_rows(data/'cachedata/dbc/item_display_icons.tsv.gz','\t'):
                if r.get('icon'): self.maps['display'][str(r['displayid'])]=icon_name(r['icon'])
        if 'cachedata/union/itemcache.tsv.gz' in present:
            with textopen(data/'cachedata/union/itemcache.tsv.gz') as f:
                reader=csv.reader(f,delimiter='\t');head=next(reader);entry,display=head.index('entry'),head.index('displayid')
                for r in reader:
                    icon=self.maps['display'].get(r[display]) if len(r)>display else None
                    if icon: self.maps['item'][r[entry]]=icon
    def lookup(self,kind,identifier):
        k=ICON_KINDS.get(kind);icon=self.maps[k].get(identifier) if k else None
        return icon if icon and icon in self.available else ''

class Buckets:
    # Stay comfortably below common service-level file descriptor limits. Closed
    # gzip streams are reopened in append mode, so this changes only resource use.
    MAX_OPEN = 256
    def __init__(self,root): self.root=root;self.handles=collections.OrderedDict();self.keys={}
    def add(self,key,row,line=None):
        filename=self.keys.get(key)
        if filename is None:filename=hashlib.sha256(key.encode()).hexdigest()[:16]+'.jsonl.gz';self.keys[key]=filename
        if key in self.handles: f=self.handles.pop(key)
        else:
            if len(self.handles)>=self.MAX_OPEN: self.handles.popitem(last=False)[1].close()
            f=gzip.open(self.root/filename,'at',encoding='utf-8',compresslevel=1)
        self.handles[key]=f;f.write(line if line is not None else dump(row)+'\n')
    def close(self):
        for f in self.handles.values(): f.close()
        self.handles.clear()

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--data',type=Path,required=True);ap.add_argument('--out',type=Path,default=BASE/'dist');ap.add_argument('--cache',type=Path,default=BASE/'.build-cache');ap.add_argument('--sample',type=int,default=0);ap.add_argument('--reuse-manifest',type=Path,help='Trusted previous catalog manifest for unchanged legacy parser cache reuse');ap.add_argument('--hosting',choices=['static','r2'],default='static');ap.add_argument('--icon-base',default=os.environ.get('ASCENSIONDB_ICON_BASE',''),help='URL prefix of the published icon files (…/<name>.png); empty disables search icons');a=ap.parse_args()
    a.data=a.data.resolve();a.out=a.out.resolve();a.cache=a.cache.resolve()
    if git(a.data,'status','--porcelain','--untracked-files=no').strip(): raise SystemExit('Use a clean published checkout, never a live publisher working tree.')
    revision=git(a.data,'rev-parse','HEAD').decode().strip()
    entries=[]
    for l in git(a.data,'ls-tree','-r','HEAD').decode().splitlines():
        meta,path=l.split('\t',1);mode,typ,blob=meta.split()
        if typ=='blob': entries.append((path,blob))
    if a.icon_base and not (a.icon_base.startswith('https://') or (':' not in a.icon_base and not a.icon_base.startswith('//') and re.fullmatch(r'[\w./-]+',a.icon_base))): raise SystemExit('--icon-base must be an https URL or a relative path')
    a.out.mkdir(parents=True,exist_ok=True);a.cache.mkdir(parents=True,exist_ok=True)
    entries,resolved,downloads,data_view=resolve_datasets(a.data,entries,a.cache)
    # Built from the resolved view, so icon inputs held in release-backed datasets count as present.
    icons=IconIndex(data_view,[p for p,_ in entries]) if a.icon_base else None;icon_rows=0
    if icons: print(f'Icon index: {len(icons.available):,} published icons; mapped IDs '+', '.join(f'{k} {len(v):,}' for k,v in sorted(icons.maps.items())),flush=True)
    # Stable parser revisions keep unrelated atlas/UI changes from reparsing millions of records.
    # Bump the applicable revision whenever a parser or identity adapter changes.
    version='2ac2e8a89519b336'
    manifest={'schema':SCHEMA,'revision':revision,'sample':a.sample or None,'sources':{},'kinds':{},'modes':[],'search':{},'browse':{},'files':{},'built_at':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'downloads':downloads,'records':0,'indexed_files':0,'reference_files':0,'total_files':len(entries)}
    coverage=[];kinds=collections.Counter();modes=set();browsing=collections.defaultdict(list)
    stage=Path(tempfile.mkdtemp(prefix='catalog-search-',dir=a.cache));buckets=Buckets(stage)
    legacy={}
    if a.reuse_manifest:
        previous=json.loads(a.reuse_manifest.read_text(encoding='utf-8'))
        if previous.get('schema')!=SCHEMA or previous.get('sample'):raise ValueError('Cache reuse requires a complete compatible catalog')
        legacy={(v['path'],v.get('blob')):(k,v['records']) for k,v in previous['files'].items() if '/catalogue/' not in v['path'] and '/lootcollector/' not in v['path'] and v['path']!=DISPLAY_ICON_PATH and not attribution.affected(v['path'])}
    parsed=0;reused=0
    try:
        for path,blob in entries:
            src=resolved[path];ak,reason=adapter(path)
            parser_version='display-icon-identity-1' if path==DISPLAY_ICON_PATH else 'coa-json-literal-escapes-1' if '/coa-databank/' in path else 'research-evidence-1' if '/research-intake/' in path else 'atlas-location-parser-1' if '/catalogue/' in path or '/lootcollector/' in path else version
            if attribution.affected(path): parser_version='record-attribution-1'
            fid=hashlib.sha256((path+'\0'+blob+parser_version+str(a.sample)).encode()).hexdigest()[:20]
            prior=legacy.get((path,blob))
            if prior and (a.cache/prior[0]/'meta.json').exists() and (a.cache/prior[0]/'index.jsonl.gz').exists():
                if json.loads((a.cache/prior[0]/'meta.json').read_text(encoding='utf-8'))['records']!=prior[1]:raise ValueError('Legacy cache count mismatch')
                fid=prior[0]
            info={'path':path,'blob':blob,'bytes':src.stat().st_size,'status':'indexed' if ak!='reference' else 'reference','reason':reason,'records':0,'source':source_type(path)}
            if ak=='reference':
                manifest['reference_files']+=1;coverage.append(info);continue
            cache=a.cache/fid;meta=cache/'meta.json';idx=cache/'index.jsonl.gz'
            if not meta.exists():
                cache.mkdir(exist_ok=True);chunk=[];chunk_bytes=0;part=0;count=0
                with gzip.open(idx,'wt',encoding='utf-8') as index:
                    for i,r in enumerate(rows(src,ak)):
                        if a.sample and i>=a.sample: break
                        eid,title,kind,mode,source,payload=identify(path,r,i)
                        packed=[eid,title,kind,mode,source,payload]
                        attributed=attribution.resolve(path,payload)
                        if attributed is not None: packed.append(attributed)
                        size=len(dump(packed).encode())
                        if chunk and (len(chunk)>=500 or chunk_bytes+size>8000000):
                            zipped(cache/f'{part}.json.gz',chunk);part+=1;chunk=[];chunk_bytes=0
                        if size>24000000: raise ValueError(f'One record exceeds static asset size: {path}:{i}')
                        key=f'{fid}/{part}/{len(chunk)}'; row=[key,title,kind,mode,source,eid]
                        index.write(dump(row)+'\n');chunk.append(packed);chunk_bytes+=size;count+=1
                    if chunk: zipped(cache/f'{part}.json.gz',chunk)
                meta.write_text(dump({'records':count,'parts':part+1 if count else 0}),encoding='utf-8');parsed+=1
            else: reused+=1
            fm=json.loads(meta.read_text(encoding='utf-8'));info['records']=fm['records'];manifest['records']+=fm['records'];manifest['indexed_files']+=1
            manifest['files'][fid]={'path':path,'blob':blob,'records':fm['records']}
            dest=a.out/'records'/fid;dest.mkdir(parents=True,exist_ok=True)
            for part in range(fm['parts']):
                f=cache/f'{part}.json.gz';d=dest/f.name
                if not d.exists(): shutil.copyfile(f,d)
            with gzip.open(idx,'rt',encoding='utf-8') as index:
                for line in index:
                    row=json.loads(line);key,title,kind,mode,source,eid=row
                    # The optional seventh field is added after the parser cache, so icons never force a reparse.
                    icon=icons.lookup(kind,eid) if icons else ''
                    if icon: row.append(icon);line=dump(row)+'\n';icon_rows+=1
                    kinds[kind]+=1;modes.update(x.strip() for x in mode.split(',') if x.strip())
                    manifest['sources'][source]=manifest['sources'].get(source,0)+1

            coverage.append(info)
            print(f'Indexed {path}: {fm["records"]:,}',flush=True)
        # Recovered historical pages are extracted as inert text, not executable archived HTML.
        recovery_path=BASE/'recovered'/'report.json'
        recovery=json.loads(recovery_path.read_text(encoding='utf-8')) if recovery_path.exists() else {'records':[],'results':[],'inventory':[]}
        for i,r in enumerate(recovery.get('records',[])):
            fid='recovered'+hashlib.sha256(dump(r).encode()).hexdigest()[:12]
            kind=r.get('type','recovered-page');packed=[str(i),r['name'],kind,'Unspecified','Wayback recovery',r]
            zipped(a.out/'records'/fid/'0.json.gz',[packed]);manifest['files'][fid]={'path':'recovered/report.json','archive_url':r.get('archive_url'),'records':1}
            row=[f'{fid}/0/0',r['name'],kind,'Unspecified','Wayback recovery',str(i)]
            kinds[kind]+=1;manifest['records']+=1;manifest['sources']['Wayback recovery']=manifest['sources'].get('Wayback recovery',0)+1
        atlas = build_atlas(a.out,manifest,data_view)
        build_grouped_search(a.out,manifest,atlas,buckets,icons,stage,data_view)
        buckets.close()
        # Fold exact-ID buckets into first-two-digit buckets to keep the asset count bounded.
        # Prefix keys remain available for selecting candidate chunks; exact match is checked in browser.
        search_groups=collections.defaultdict(list)
        for key,name in buckets.keys.items():
            group=('id:'+key[3:5]) if key.startswith('id:') else key
            search_groups[group].append(name)
        for group,files in sorted(search_groups.items()):
            prefix=hashlib.sha256(group.encode()).hexdigest()[:16];parts=[];chunk=[];size=0;count=0
            def flush():
                nonlocal chunk,size
                if not chunk:return
                content=dump(chunk).encode();h=hashlib.sha256(content).hexdigest()[:20];rel=f'search/{h}.json.gz';zipped(a.out/rel,chunk);parts.append(rel);chunk=[];size=0
            for name in files:
                with textopen(stage/name) as f:
                    for line in f:
                        if len(chunk)>=12000 or size+len(line)>4000000: flush()
                        row=json.loads(line);chunk.append(row);count+=1;size+=len(line)
            flush();manifest['search'][group]={'parts':parts,'count':count}
        if icons:
            manifest['icons']={'base':a.icon_base,'extension':'.png','available':len(icons.available),'rows':icon_rows}
            print(f'Search icons: {icon_rows:,} rows reference one of {len(icons.available):,} published icons',flush=True)
        manifest['kinds']={k:{'label':LABELS.get(k,k.replace('-',' ').title()),'records':n} for k,n in kinds.items()};manifest['modes']=sorted(modes)
        manifest['recovery']={'pages':len(recovery.get('records',[])),'attempted':len(recovery.get('results',[])),'inventory':len(recovery.get('inventory',[])),'complete_mirror':False}
        zipped(a.out/'coverage.json.gz',coverage);zipped(a.out/'recovery.json.gz',recovery)
        manifest['parsed_files']=parsed;manifest['reused_files']=reused
        (a.out/'manifest.json').write_text(dump(manifest),encoding='utf-8')
        for f in (BASE/'web').iterdir():
            if f.is_file(): shutil.copyfile(f,a.out/f.name)
        # Remove only obsolete generated files inside this explicitly selected output directory.
        live={str((a.out/'records'/fid).resolve()) for fid in manifest['files']}
        records=a.out/'records'
        if records.exists():
            for d in records.iterdir():
                if d.is_dir() and str(d.resolve()) not in live and d.resolve().is_relative_to(a.out): shutil.rmtree(d)
        active={p for v in manifest['search'].values() for p in v['parts']}
        for f in (a.out/'search').glob('*'):
            if f.relative_to(a.out).as_posix() not in active: f.unlink()
        files=[f for f in a.out.rglob('*') if f.is_file()]
        if a.hosting=='static' and len(files)>19500: raise ValueError(f'Too many assets for free Worker: {len(files)}')
        if any(f.stat().st_size>(64000000 if a.hosting=='r2' else 25000000) for f in files): raise ValueError('Oversized static asset')
        if len(coverage)!=len(entries) or sum(i['records'] for i in coverage)+manifest['recovery']['pages']!=manifest['records']: raise ValueError('Coverage accounting mismatch')
        (a.out/'build-report.json').write_text(dump({'revision':revision,'records':manifest['records'],'files_accounted':len(coverage),'assets':len(files),'bytes':sum(f.stat().st_size for f in files),'parsed_files':parsed,'reused_files':reused,'sample':a.sample or None}),encoding='utf-8')
        print('BUILD COMPLETE',dump(json.loads((a.out/'build-report.json').read_text(encoding='utf-8'))),flush=True)
    finally:
        buckets.close()
        if stage.resolve().is_relative_to(a.cache.resolve()): shutil.rmtree(stage)
if __name__=='__main__': main()
