"""Capture public, paired route-planner map geometry. Never imports map artwork."""
import argparse,datetime,gzip,hashlib,json,math,re,urllib.request
from pathlib import Path
URL='https://mplus.exil.es/assets/dungeons.json'
# Reviewed instance identities; wings remain separate layouts on the same server map.
MAPS={'blackfathom_deeps':'48','blackrock_depths':'230','blackwinglair':'469','dire_maul_east':'429','dire_maul_north':'429','dire_maul_west':'429','gnomeregan':'90','lower_blackrock_spire':'229','maraudon':'349','moltencore':'409','naxxramas_classic':'533','ragefire_chasm':'389','razorfen_downs':'129','razorfen_kraul':'47','scarlet_monastery_armory':'189','scarlet_monastery_cathedral':'189','scarlet_monastery_graveyard':'189','scarlet_monastery_library':'189','scholomance':'289','shadowfang_keep':'33','stratholme':'329','deadmines':'36','the_stockade':'34','uldaman':'70','upper_blackrock_spire':'229','wailing_caverns':'43','zul_farrak':'209','zulgurub':'309'}
def normalize(raw,*,captured_at,expected=None):
 datetime.date.fromisoformat(captured_at)
 data=json.loads(raw);floors=[];npcs=[];seen=set();dungeons=set()
 if len(data['dungeons'])>500:raise ValueError('Too many instance layouts')
 for d in data['dungeons']:
  if d['id'] in dungeons:raise ValueError('Duplicate instance identity')
  dungeons.add(d['id'])
  if type(d['mapping_id']) is not int or d['mapping_id']<=0:raise ValueError('Invalid provider mapping identity')
  if not d['maps']:raise ValueError('Empty instance floor list')
  if len(d['maps'])>64:raise ValueError('Too many floors')
  if d['id'] not in MAPS:raise ValueError('Unreviewed instance identity: '+d['id'])
  for i,f in enumerate(d['maps'],1):
   label=f['label'];image=f['image'];width,height=f['width'],f['height']
   if not re.fullmatch(r'[A-Za-z0-9_]+',label) or not re.fullmatch(r'maps/[a-z0-9_]+\.webp',image):raise ValueError('Invalid paired image identity')
   if label in seen:raise ValueError('Duplicate floor')
   seen.add(label)
   if not all(type(v) is int and 0<v<=16384 for v in (width,height)):raise ValueError('Invalid image dimensions')
   if f['kg_floor_id'] is not None and (type(f['kg_floor_id']) is not int or f['kg_floor_id']<=0):raise ValueError('Invalid provider floor identity')
   if len(f.get('enemies',[]))>25000:raise ValueError('Too many floor markers')
   floor={'id':d['id']+'--'+label.lower(),'label':label,'name':d['name']+' — Floor '+str(i),'type':'instance-floor','dungeon':d['id'],'dungeon_name':d['name'],'server_map':MAPS[d['id']],'floor':i,'provider_floor_id':f['kg_floor_id'],'mapping_id':d['mapping_id'],'captured_at':captured_at,'image':'https://mplus.exil.es/assets/'+image,'width':width,'height':height,'era':'Classic (40-player)' if d['id']=='naxxramas_classic' else 'Classic reference','url':URL,'source_page':'https://mplus.exil.es/','source_sha256':hashlib.sha256(raw).hexdigest()}
   floors.append(floor);enemy_ids=set()
   for ordinal,e in enumerate(f.get('enemies',[])):
    if type(e.get('classification')) is not int or e['classification'] not in range(1,6):raise ValueError('Invalid provider classification')
    if (e['id'] is None or e['npc_id'] is None) and not(e['id'] is None and e['npc_id'] is None and e.get('ascension_pinned') is True):raise ValueError('Unidentified reference is not an explicit manual landmark')
    x,y=e['pos'];eid=str(e['id']) if e['id'] is not None else 'manual:'+str(ordinal);npc=str(e['npc_id']) if e['npc_id'] is not None else ''
    if eid in enemy_ids:raise ValueError('Duplicate enemy on floor')
    enemy_ids.add(eid)
    if (npc and (not npc.isdecimal() or int(npc)<=0)) or not all(type(v) in (int,float) and math.isfinite(v) for v in [x,y]) or not(0<=x<=width and 0<=y<=height):raise ValueError('Invalid paired NPC position')
    npcs.append({'id':npc or 'reference:'+floor['id']+':'+str(ordinal),'npc_id':npc,'manual':e.get('ascension_pinned',False),'name':str(e['name']),'type':'route-npc' if npc else 'route-landmark','floor_key':floor['id'],'provider_enemy_id':eid,'pixel_x':x,'pixel_y':y,'classification':e.get('classification',0),'dungeon':d['id'],'server_map':MAPS[d['id']],'url':URL,'source_sha256':floor['source_sha256']})
 if dungeons != (set(MAPS) if expected is None else set(expected)):raise ValueError('Incomplete reviewed instance set')
 return floors,npcs

def write_rows(path,rows):
 with path.open('wb') as f:
  with gzip.GzipFile(fileobj=f,mode='wb',mtime=0) as z:
   for row in rows:z.write((json.dumps(row,ensure_ascii=False,separators=(',',':'))+'\n').encode())
def main():
 ap=argparse.ArgumentParser();ap.add_argument('--out',type=Path,required=True);ap.add_argument('--input',type=Path);ap.add_argument('--captured-at',required=True,help='UTC retrieval date, YYYY-MM-DD');a=ap.parse_args()
 raw=a.input.read_bytes() if a.input else urllib.request.urlopen(URL,timeout=60).read(20000000)
 floors,npcs=normalize(raw,captured_at=a.captured_at);out=a.out/hashlib.sha256(raw).hexdigest()[:20];out.mkdir(parents=True,exist_ok=True)
 write_rows(out/'floors.jsonl.gz',floors);write_rows(out/'npcs.jsonl.gz',npcs)
 (out/'README.md').write_text('# Instance route references\n\nPublic source: '+URL+'\n\nSource SHA-256: '+hashlib.sha256(raw).hexdigest()+'\n\nPaired image-pixel geometry from the Exiles M+ planner, which attributes its maps and enemy data to Keystone.guru. Captured as separate reference claims, not observed Ascension spawns. Images remain externally hosted; none are copied into this repository. Older Exiles and loot observations are not assigned inferred floors. Naxxramas (40) is explicitly a Classic layout, not a verified Wrath or Ascension placement. Instance IDs are cross-checked against the published map inventory and https://www.azerothcore.org/wiki/map. These files contain public game data only; player routes and notes are not collected.\n',encoding='utf8')
 print(json.dumps({'output':str(out),'layouts':len(MAPS),'floors':len(floors),'npc_references':len(npcs)}))
if __name__=='__main__':main()
