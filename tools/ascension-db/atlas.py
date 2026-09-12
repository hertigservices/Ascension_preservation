"""Attributed atlas built exclusively from a published catalog and captured map bounds.
No client artwork is extracted. Outdoor artwork URLs use a reviewed area-ID crosswalk;
WorldMapArea IDs, server map IDs and source area names remain separate identities.
"""
import collections,csv,gzip,hashlib,json,math,re
from pathlib import Path
from atlas_metadata import enrich_atlas
from instance_maps import add_instance_maps

# Explicit legacy area IDs for external artwork. Never substitute a server/WMA ID.
AREA_ROWS = [
('Dun Morogh',1,0,'DunMorogh'),('Badlands',3,0,''),('Blasted Lands',4,0,'BlastedLands'),('Swamp of Sorrows',8,0,'SwampOfSorrows'),('Duskwood',10,0,''),('Wetlands',11,0,''),('Elwynn Forest',12,0,'Elwynn'),('Durotar',14,1,''),('Dustwallow Marsh',15,1,'Dustwallow'),('Azshara',16,1,''),('The Barrens',17,1,'Barrens'),('Western Plaguelands',28,0,'WesternPlaguelands'),('Stranglethorn Vale',33,0,'Stranglethorn'),('Alterac Mountains',36,0,'Alterac'),('Loch Modan',38,0,'LochModan'),('Westfall',40,0,''),('Deadwind Pass',41,0,'DeadwindPass'),('Redridge Mountains',44,0,'Redridge'),('Arathi Highlands',45,0,'Arathi'),('Burning Steppes',46,0,'BurningSteppes'),('The Hinterlands',47,0,'Hinterlands'),('Searing Gorge',51,0,'SearingGorge'),('Tirisfal Glades',85,0,'Tirisfal'),('Silverpine Forest',130,0,'Silverpine'),('Eastern Plaguelands',139,0,'EasternPlaguelands'),('Teldrassil',141,1,''),('Darkshore',148,1,''),('Mulgore',215,1,''),('Hillsbrad Foothills',267,0,'Hillsbrad'),('Ashenvale',331,1,''),('Feralas',357,1,''),('Felwood',361,1,''),('Thousand Needles',400,1,'ThousandNeedles'),('Desolace',405,1,''),('Stonetalon Mountains',406,1,'StonetalonMountains'),('Tanaris',440,1,''),('Un\'Goro Crater',490,1,'UnGoroCrater'),('Moonglade',493,1,''),('Winterspring',618,1,''),('Silithus',1377,1,''),('Stormwind City',1519,0,'Stormwind'),('Ironforge',1537,0,''),('Orgrimmar',1637,1,''),('Darnassus',1657,1,''),('Undercity',1497,0,''),
('Hellfire Peninsula',3483,530,'Hellfire'),('Zangarmarsh',3521,530,''),('Terokkar Forest',3519,530,'TerokkarForest'),('Nagrand',3518,530,''),('Blade\'s Edge Mountains',3522,530,'BladesEdgeMountains'),('Netherstorm',3523,530,''),('Shadowmoon Valley',3520,530,'ShadowmoonValley'),('Shattrath City',3703,530,'ShattrathCity'),('Azuremyst Isle',3524,530,'AzuremystIsle'),('Bloodmyst Isle',3525,530,'BloodmystIsle'),('Eversong Woods',3430,530,'EversongWoods'),('Ghostlands',3433,530,''),('Silvermoon City',3487,530,'SilvermoonCity'),('The Exodar',3557,530,'TheExodar'),
('Borean Tundra',3537,571,'BoreanTundra'),('Howling Fjord',495,571,'HowlingFjord'),('Dragonblight',65,571,''),('Grizzly Hills',394,571,'GrizzlyHills'),('Zul\'Drak',66,571,'ZulDrak'),('Sholazar Basin',3711,571,'SholazarBasin'),('The Storm Peaks',67,571,'StormPeaks'),('Icecrown',210,571,'IcecrownGlacier'),('Crystalsong Forest',2817,571,'CrystalsongForest'),('Wintergrasp',4197,571,''),('Dalaran',4395,571,'')]
def slug(s): return re.sub('[^a-z0-9]+','-',str(s).lower()).strip('-')
def compact(s): return re.sub('[^a-z0-9]','',str(s).lower())
AREAS={}
for title,area,world,aliases in AREA_ROWS:
 for alias in [title,*aliases.split('|')]:
  if alias: AREAS[(str(world),compact(alias))]=(title,area)
CONTINENTS={'0':'Eastern Kingdoms','1':'Kalimdor','530':'Outland','571':'Northrend'}
def number(v):
 try:
  n=float(v)
  return n if math.isfinite(n) else None
 except (TypeError,ValueError): return None
def percent(x,y,scale=1):
 x,y=number(x),number(y)
 if x is None or y is None or not (0<=x<=100/scale and 0<=y<=100/scale): return None
 return round(x*scale,5),round(y*scale,5)
def write_gz(path,value):
 b=json.dumps(value,ensure_ascii=False,separators=(',',':')).encode();path.parent.mkdir(parents=True,exist_ok=True)
 with path.open('wb') as f:
  with gzip.GzipFile(fileobj=f,mode='wb',mtime=0) as z:z.write(b)
 return b
def build_atlas(root,manifest,data_root):
 root=Path(root);data_root=Path(data_root);zones={};points=collections.defaultdict(list);links=collections.defaultdict(list);skipped=collections.Counter();inventory={};bounds=[];bounds_sources=[]
 for path in sorted(data_root.glob('supplemental/worldforged/*/data/worldmaparea_bounds_captured.csv.gz')):
  with gzip.open(path,'rt',encoding='utf8') as f: bounds.extend(csv.DictReader(f))
  bounds_sources.append(path.relative_to(data_root).as_posix())
 inv=data_root/'cachedata/mapdata/maps.tsv'
 if inv.exists():
  with inv.open(encoding='utf8',newline='') as f: inventory={r['map_id']:r for r in csv.DictReader(f,delimiter='\t',quoting=csv.QUOTE_NONE)}
 unique_bounds={}
 for b in bounds:
  wid=b['wma_id']
  if wid in unique_bounds and unique_bounds[wid]!=b:raise ValueError('Conflicting captured bounds for WorldMapArea '+wid)
  unique_bounds[wid]=b
 bounds=list(unique_bounds.values())
 by_wma=unique_bounds
 by_name=collections.defaultdict(list)
 for b in bounds:by_name[(b['server_map'],compact(b['name']))].append(b)
 def zone(name,world='',wma='',space='percent'):
  world=str(world or '');name=str(name or 'Unnamed area');known=AREAS.get((world,compact(name)))
  # Exact captured WMA identity takes precedence, particularly for custom floors.
  b=by_wma.get(str(wma)) if wma else None
  if b and b['server_map']!=world:b=None
  if not b:
   candidates=by_name.get((world,compact(name)),[])
   if not candidates and known:
    candidates=[x for x in bounds if x['server_map']==world and AREAS.get((world,compact(x['name'])))==known]
   if len(candidates)==1:b=candidates[0]
  key='wma-'+b['wma_id'] if b else 'area-'+slug(world+'-'+name)
  if space!='percent':key+='-'+space
  if key not in zones:
   title=known[0] if known else name
   z={'key':key,'name':title,'map':world,'wma':b['wma_id'] if b else '', 'region':CONTINENTS.get(world,'Instances & other worlds'),'aliases':[], 'space':space,'image':'','bounds':b,'inventory':inventory.get(world)}
   if known and space=='percent':
    expansion='classic' if world in ('0','1') else 'wotlk'
    z['image']=f'https://wow.zamimg.com/images/wow/{expansion}/maps/enus/original/{known[1]}.jpg'
    z['image_area_id']=known[1]
   zones[key]=z
  if name not in zones[key]['aliases']:zones[key]['aliases'].append(name)
  return key
 for b in bounds:zone(b['name'],b['server_map'],b['wma_id'])
 for mid,info in inventory.items():
  if not any(z['map']==mid for z in zones.values()):
   k=zone(info.get('name') or 'Map '+mid,mid,space='inventory');zones[k]['region']='Instances & other worlds'
 def add(record,name,eid,entity,layer,source,mode,z,xy,meta,roles=None):
  p={'key':record+':'+z+':'+str(len(points[z])),'record':record,'name':name,'id':str(eid),'entity':entity,'layer':layer,'source':source,'mode':mode or 'Unspecified','zone':z,'x':xy[0],'y':xy[1],'plot_x':xy[0],'plot_y':xy[1],'meta':meta}
  p['roles']=roles or [layer]
  points[z].append(p)
  if z not in links[record]:links[record].append(z)
 selected=[(fid,f) for fid,f in manifest['files'].items() if f['path'] in ('cachedata/catalogue/creatures.tsv','cachedata/catalogue/gameobjects.tsv','cachedata/lootcollector/pins.tsv') or ('/exiles-db/' in f['path'] and f['path'].endswith('/npcs.jsonl.gz'))]
 for fid,f in selected:
  for path in sorted((root/'records'/fid).glob('*.json.gz'),key=lambda p:int(p.name.split('.')[0])):
   with gzip.open(path,'rt',encoding='utf8') as reader:rows=json.load(reader)
   for offset,r in enumerate(rows):
    eid,title,kind,mode,source,payload=r;record=f'{fid}/{path.name.split(".")[0]}/{offset}'
    if '/catalogue/' in f['path']:
     xy=percent(payload.get('example_map_x'),payload.get('example_map_y'))
     if not xy or xy==(0,0):skipped['Catalogue: missing or invalid map coordinates']+=1;continue
     world=payload.get('example_map_id','');name=payload.get('example_zone','')
     if not name:skipped['Catalogue: missing zone']+=1;continue
     # Map filename aliases have an explicit crosswalk; retain the source spelling in details.
     if not world:
      worlds={w for w,n in AREAS if n==compact(name)}
      if len(worlds)==1:world=worlds.pop()
     z=zone(name,world);entity='npc' if kind=='world-creature' else 'gameobject'
     add(record,title,eid,entity,'creatures' if entity=='npc' else 'quest-givers' if payload.get('type')=='Questgiver' else 'objects','Addon observations',mode,z,xy,{'meaning':'One example sighting; not a complete spawn list.','source_zone':name,'origin':payload.get('origin',''),'object_type':payload.get('type',''),'world_x':payload.get('example_x',''),'world_y':payload.get('example_y',''),'world_z':payload.get('example_z',''),'sightings':payload.get('sightings','')})
    elif '/lootcollector/' in f['path']:
     xy=percent(payload.get('norm_x'),payload.get('norm_y'),100)
     if xy is None:skipped['Loot pins: invalid normalized coordinates']+=1;continue
     name=payload.get('zone','');world=payload.get('map','');wma=payload.get('wma_id','');z=zone(name,world,wma);typ=payload.get('type','other');eid=payload.get('item','')
     layer={'worldforged':'worldforged','mystic':'mystic','mystic_scroll':'mystic','mystic-scroll':'mystic','scroll':'mystic'}.get(typ,'other-loot')
     title=('Worldforged item' if layer=='worldforged' else 'Mystic scroll' if layer=='mystic' else 'Loot item')+' #'+eid
     add(record,title,eid,'item',layer,'LootCollector',payload.get('modes','').replace('|',','),z,xy,{'meaning':'Player position when the loot window opened; not a confirmed item source or drop rate.','source_zone':name,'type':typ,'realms':payload.get('realms',''),'world_x':payload.get('server_x',''),'world_y':payload.get('server_y',''),'first_seen':payload.get('first_seen',''),'last_seen':payload.get('last_seen',''),'addon_status':payload.get('status',''),'records':payload.get('records',''),'files':payload.get('files','')})
    else:
     structure=payload.get('structure',{});headings=structure.get('headings',[])
     roles=[]
     if 'Vendor' in headings and any(t.get('header')==['Item','Price','Stock'] and t.get('rows') for t in structure.get('tables',[])):roles.append('vendors')
     if 'Starts Quests' in headings or 'Ends Quests' in headings:roles.append('quest-givers')
     roles=roles or ['npc-claims']
     role_labels=(['Vendor'] if 'vendors' in roles else [])+[x for x in ['Starts Quests','Ends Quests'] if x in headings]
     refs=structure.get('references',[])
     for table in payload.get('structure',{}).get('tables',[]):
      if table.get('header')!=['Map','Area','Coords','Spawns']:continue
      for row in table.get('rows',[]):
       if len(row)!=4:skipped['Exiles: malformed location row']+=1;continue
       mapname,area,coords,count=row;parts=[number(x.strip()) for x in coords.split(',')]
       if len(parts)!=2 or any(x is None for x in parts):skipped['Exiles: missing or malformed coordinates']+=1;continue
       xy=tuple(parts);inside=percent(*xy) is not None
       matching={str(x['key']) for x in refs if x.get('type')=='map' and x.get('label')==mapname}
       world=next(iter(matching)) if len(matching)==1 else next((k for k,v in CONTINENTS.items() if v==mapname),'')
       # Instance coordinates may be world units even when inside [0,100]. Keep their
       # reported coordinate view separate from calibrated zone-map imagery.
       space=('percent' if world in CONTINENTS else 'reported') if inside else 'world'
       z=zone(area,world,space=space)
       add(record,title,eid,'npc',roles[0],'Exiles DB',mode,z,xy,{'meaning':'Historical website location claim. '+('Reported coordinates; coordinate units and floor are unverified.' if space!='percent' else 'Reported zone percentages; not independently verified.'),'source_zone':area,'map_name':mapname,'declared_spawns':count,'roles':', '.join(role_labels)},roles)
 add_instance_maps(root,manifest,zones,add,inventory)
 layers=collections.Counter();sources=set();modes=set();total=0
 for key,z in zones.items():
  ps=points[key]
  if z['space']=='world' and ps:
   xs=[p['x'] for p in ps];ys=[p['y'] for p in ps];dx=max(max(xs)-min(xs),1);dy=max(max(ys)-min(ys),1)
   z['extent']={'left':min(xs)-dx*.05,'right':max(xs)+dx*.05,'top':min(ys)-dy*.05,'bottom':max(ys)+dy*.05}
   e=z['extent']
   for p in ps:p['plot_x']=(p['x']-e['left'])/(e['right']-e['left'])*100;p['plot_y']=(p['y']-e['top'])/(e['bottom']-e['top'])*100
  if z['space']=='floor':
   for p in ps:p['plot_x']=p['x']/z['width']*100;p['plot_y']=p['y']/z['height']*100
  z['count']=len(ps);z['layers']=dict(collections.Counter(p['layer'] for p in ps));layers.update(z['layers']);total+=len(ps)
  for p in ps:sources.add(p['source']);modes.update(x.strip() for x in p['mode'].split(',') if x.strip())
  if ps:
   content=json.dumps(ps,ensure_ascii=False,separators=(',',':')).encode();h=hashlib.sha256(content).hexdigest()[:20];z['file']=f'atlas/{h}.json.gz';write_gz(root/z['file'],ps)
  else:z['file']=''
  z['aliases'].sort()
 result={'schema':'ascension-atlas-1','revision':manifest['revision'],'zones':sorted(zones.values(),key=lambda z:(z['region'],z['name'],z['key'])),'observations':total,'layers':dict(layers),'sources':sorted(sources),'modes':sorted(modes),'unmapped':dict(skipped),'bounds_sources':bounds_sources,'inventory_maps':len(inventory),'bounds_count':len(bounds)}
 result['reference_locations']=sum(v for k,v in layers.items() if k.startswith('route-'))
 result['preserved_observations']=total-result['reference_locations']
 result['instance_layouts']=len({z['layout'] for z in zones.values() if z['space']=='floor'})
 result['instance_floors']=sum(z['space']=='floor' for z in zones.values())
 enrich_atlas(root,manifest,result)
 write_gz(root/'atlas-links.json.gz',dict(links));write_gz(root/'atlas-manifest.json.gz',result)
 manifest['atlas']={'manifest':'atlas-manifest.json.gz','links':'atlas-links.json.gz','observations':total,'zones':len(result['zones']),'unmapped':sum(skipped.values())}
 return result
