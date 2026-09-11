"""Validate atlas references, coordinate spaces and full inventory before publication."""
import collections,gzip,hashlib,json,math,re
from pathlib import Path
PART=re.compile(r'^atlas/[0-9a-f]{20}\.json\.gz$')
REC=re.compile(r'^([a-z0-9]+)/([0-9]+)/([0-9]+)$')
LAYERS={'creatures','objects','npc-claims','vendors','quest-givers','worldforged','mystic','other-loot'}
IMG=re.compile(r'^https://wow\.zamimg\.com/images/wow/(?:classic|wotlk)/maps/enus/original/[0-9]+\.jpg$')
def gz(p):
 with gzip.open(p,'rt',encoding='utf8') as f:return json.load(f)
def exists(k,m,pc):
 x=REC.fullmatch(str(k));return bool(x and x[1] in m['files'] and (x[1],x[2]) in pc and int(x[3])<pc[(x[1],x[2])])
def inventory_ids(root,m):
 out=set()
 for fid,info in m['files'].items():
  if info['path']!='cachedata/mapdata/maps.tsv':continue
  for p in (root/'records'/fid).glob('*.json.gz'):
   for r in gz(p):
    mid=str(r[5].get('map_id',''))
    if not mid or mid in out:raise ValueError('Invalid/duplicate catalog map ID')
    out.add(mid)
 return out
def validate_atlas(root,m,pc):
 root=Path(root);i=m.get('atlas',{})
 if i.get('manifest')!='atlas-manifest.json.gz' or i.get('links')!='atlas-links.json.gz':raise ValueError('Invalid atlas paths')
 a,links=gz(root/i['manifest']),gz(root/i['links'])
 if a.get('schema')!='ascension-atlas-1' or a.get('revision')!=m['revision']:raise ValueError('Atlas schema/revision mismatch')
 zs,ms=a.get('zones'),a.get('maps');zkeys=[str(z.get('key','')) for z in zs];mids=[str(x.get('id','')) for x in ms]
 if any(not x for x in zkeys) or len(zkeys)!=len(set(zkeys)):raise ValueError('Invalid/duplicate zone key')
 if any(not x for x in mids) or len(mids)!=len(set(mids)):raise ValueError('Invalid/duplicate map ID')
 expected=inventory_ids(root,m);inventory_rows=[str(x['inventory'].get('map_id','')) for x in ms if x.get('inventory')]
 if len(inventory_rows)!=len(set(inventory_rows)):raise ValueError('Duplicate inventory map identity')
 actual=set(inventory_rows)
 if m.get('sample'):
  if not expected<=actual:raise ValueError('Sample inventory coverage mismatch')
 elif actual!=expected or a.get('inventory_maps')!=len(expected):raise ValueError('Inventory coverage mismatch')
 if not set(mids)<=set(str(z.get('map','')) for z in zs):raise ValueError('A map is not navigable')
 for entry in ms:
  if entry.get('inventory') and str(entry['inventory'].get('map_id'))!=str(entry['id']):raise ValueError('Inventory attached to wrong map ID')
  for key in entry.get('records',[]):
   if not exists(key,m,pc):raise ValueError('Dangling map source record')
 for area in a.get('areas',[]):
  for key in area.get('records',[]):
   if not exists(key,m,pc):raise ValueError('Dangling area source record')
 total=0;layers=collections.Counter();sources=set();modes=set();reverse=collections.defaultdict(set);pkeys=set();zset=set(zkeys)
 for z in zs:
  key=str(z['key']);image=str(z.get('image',''));rel=str(z.get('file',''));count=z.get('count')
  if image and not IMG.fullmatch(image):raise ValueError('Invalid image URL')
  if image and z.get('space')!='percent':raise ValueError('Unverified coordinate space has artwork')
  if not isinstance(count,int) or isinstance(count,bool) or count<0:raise ValueError('Invalid zone count')
  if not rel:rows=[]
  else:
   if not PART.fullmatch(rel) or not (root/rel).is_file():raise ValueError('Unsafe/missing atlas part')
   if (root/rel).stat().st_size>25000000:raise ValueError('Oversized atlas part')
   rows=gz(root/rel);raw=json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode()
   if hashlib.sha256(raw).hexdigest()[:20]!=Path(rel).name.split('.')[0]:raise ValueError('Atlas part hash mismatch')
  if len(rows)!=count:raise ValueError('Zone count mismatch')
  local=collections.Counter()
  for p in rows:
   pk,record=str(p.get('key','')),str(p.get('record',''))
   if p.get('zone')!=key or not pk or pk in pkeys:raise ValueError('Invalid/duplicate point')
   pkeys.add(pk)
   if not exists(record,m,pc):raise ValueError('Dangling point record')
   for f in ('x','y','plot_x','plot_y'):
    v=p.get(f)
    if not isinstance(v,(int,float)) or isinstance(v,bool) or not math.isfinite(v) or (f.startswith('plot_') and not 0<=v<=100):raise ValueError('Invalid coordinate')
   if z.get('space')=='world':
    e=z.get('extent',{})
    for rawf,plotf,lo,hi in [('x','plot_x','left','right'),('y','plot_y','top','bottom')]:
     if not all(isinstance(e.get(k),(int,float)) and math.isfinite(e[k]) for k in [lo,hi]) or e[hi]<=e[lo]:raise ValueError('Invalid schematic extent')
     if abs((p[rawf]-e[lo])/(e[hi]-e[lo])*100-p[plotf])>1e-7:raise ValueError('Incorrect raw-coordinate projection')
   elif p['x']!=p['plot_x'] or p['y']!=p['plot_y']:raise ValueError('Unexpected coordinate transformation')
   layer,source=str(p.get('layer','')),str(p.get('source',''))
   if layer not in LAYERS or not source:raise ValueError('Invalid layer/source')
   roles=p.get('roles',[layer])
   if not isinstance(roles,list) or layer not in roles or any(r not in LAYERS for r in roles) or len(set(roles))!=len(roles):raise ValueError('Invalid point roles')
   local[layer]+=1;layers[layer]+=1;sources.add(source);modes.update(x.strip() for x in str(p.get('mode','')).split(',') if x.strip());reverse[record].add(key)
  if dict(local)!=z.get('layers',{}):raise ValueError('Zone layer mismatch')
  total+=len(rows)
 if total!=a.get('observations') or total!=i.get('observations') or len(zs)!=i.get('zones'):raise ValueError('Atlas totals mismatch')
 if dict(layers)!=a.get('layers',{}) or sorted(sources)!=a.get('sources') or sorted(modes)!=a.get('modes'):raise ValueError('Atlas facets mismatch')
 normalized={}
 for record,zones in links.items():
  if not exists(record,m,pc) or not isinstance(zones,list) or any(z not in zset for z in zones) or len(zones)!=len(set(zones)):raise ValueError('Invalid reverse link')
  normalized[record]=set(zones)
 if normalized!=dict(reverse):raise ValueError('Links do not reverse-match points')
 return {'observations':total,'zones':len(zs),'maps':len(mids)}
