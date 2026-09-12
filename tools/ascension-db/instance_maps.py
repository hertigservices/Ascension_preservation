"""Instance floors use their provider's paired pixel coordinates, never guessed world transforms."""
import gzip,json,math,re
IMAGE=re.compile(r'^https://mplus\.exil\.es/assets/maps/[a-z0-9_]+\.webp$')
SOURCE='Exiles route planner'
def records(root,manifest,filename):
 for fid,f in manifest['files'].items():
  if '/instance-route-maps/' not in f['path'] or not f['path'].endswith('/'+filename):continue
  for path in sorted((root/'records'/fid).glob('*.json.gz'),key=lambda p:int(p.name.split('.')[0])):
   with gzip.open(path,'rt',encoding='utf8') as r:rows=json.load(r)
   for off,row in enumerate(rows):yield f'{fid}/{path.name.split(".")[0]}/{off}',row[5]
def add_instance_maps(root,manifest,zones,add,inventory):
 floors={}
 for record,f in records(root,manifest,'floors.jsonl.gz'):
  key='instance-'+f['id'];mid=str(f['server_map'])
  if key in zones:raise ValueError('Duplicate instance floor snapshot')
  if not IMAGE.fullmatch(f['image']) or not all(type(f[k]) is int and 0<f[k]<=16384 for k in ['width','height']):raise ValueError('Invalid instance image geometry')
  if not manifest.get('sample') and mid not in inventory:raise ValueError('Instance map missing from published inventory')
  floors[f['id']]=f
  zones[key]={'key':key,'name':f['name'],'map':mid,'wma':'','region':'Ascension & other worlds','aliases':[f['dungeon_name'],f['label']],'space':'floor','image':f['image'],'bounds':None,'inventory':inventory.get(mid),'floor':f['floor'],'layout':f['dungeon'],'layout_name':f['dungeon_name'],'era':f['era'],'width':f['width'],'height':f['height'],'provider_floor_id':f['provider_floor_id'],'mapping_id':f['mapping_id'],'source_record':record,'source_url':f['source_page'],'source_sha256':f['source_sha256']}
 for record,p in records(root,manifest,'npcs.jsonl.gz'):
  f=floors.get(p['floor_key'])
  if not f:
   if manifest.get('sample'):continue
   raise ValueError('Reference NPC has no paired floor')
  if p['source_sha256']!=f['source_sha256'] or str(p['server_map'])!=str(f['server_map']) or p['dungeon']!=f['dungeon']:raise ValueError('Reference floor identity mismatch')
  x,y=p['pixel_x'],p['pixel_y']
  if not all(type(v) in (int,float) and math.isfinite(v) for v in (x,y)) or not(0<=x<=f['width'] and 0<=y<=f['height']):raise ValueError('Reference coordinate outside paired image')
  layer='route-bosses' if p['classification'] in (3,4) else 'route-npcs'
  add(record,p['name'],p['id'],'npc' if p['npc_id'] else 'landmark',layer,SOURCE,'Unspecified','instance-'+p['floor_key'],(x,y),{'meaning':'Route-planner reference position on its paired floor image; not an observed Ascension spawn or drop source. Original sightings remain in their own coordinate views.','floor':f['floor'],'layout':f['dungeon_name'],'reference_era':f['era'],'provider_enemy_id':p['provider_enemy_id'],'provider_floor_id':f['provider_floor_id'],'provider_classification':p['classification'],'manual_reference':p['manual'],'pixel_width':f['width'],'pixel_height':f['height']})
