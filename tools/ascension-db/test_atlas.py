import copy,gzip,hashlib,json,tempfile,unittest
from pathlib import Path
from atlas import build_atlas,write_gz
from validate_atlas import validate_atlas

class AtlasTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.root=Path(self.temp.name)/'out';self.data=Path(self.temp.name)/'data';self.root.mkdir();(self.data/'cachedata/mapdata').mkdir(parents=True)
  self.inventory=[{'map_id':'0','name':'Eastern Kingdoms','terrain_tiles':'10','vmap_tiles':'9','above_stock_range':'0'},{'map_id':'930','name':'Underworld','terrain_tiles':'3','vmap_tiles':'2','above_stock_range':'1'}]
  (self.data/'cachedata/mapdata/maps.tsv').write_text('map_id\tname\tterrain_tiles\tvmap_tiles\tabove_stock_range\n'+'\n'.join('\t'.join(r.values()) for r in self.inventory),encoding='utf8')
  self.m={'revision':'a'*40,'files':{},'sample':None};self.pc={}
  self.file('mapdata','cachedata/mapdata/maps.tsv',[[r['map_id'],r['name'],'map','Unspecified','Client captures',r] for r in self.inventory])
  base={'id':'42','name':'Example','example_map_id':'0','example_zone':'Elwynn','example_map_x':'25','example_map_y':'75','example_x':'-1','example_y':'2','example_z':'3','origin':'ascension'}
  self.file('objects','cachedata/catalogue/gameobjects.tsv',[['42','Object 42','world-object','Unspecified','Addon observations',base]])
  self.file('creatures','cachedata/catalogue/creatures.tsv',[['42','Creature 42','world-creature','Unspecified','Addon observations',base]])
  pin={'item':'9007199254740993123','zone':'Elwynn Forest','map':'0','norm_x':'.25','norm_y':'.75','type':'mystic_scroll','modes':'conquest-of-azeroth','realms':'Realm','wma_id':''}
  self.file('pins','cachedata/lootcollector/pins.tsv',[[pin['item'],'Loot','loot-pin','conquest-of-azeroth','LootCollector',pin]])
  npc={'id':'42','name':'Claim 42','structure':{'references':[{'type':'map','key':'930','label':'Underworld'}],'tables':[{'header':['Map','Area','Coords','Spawns'],'rows':[['Underworld','Lower Halls','-200, 400','2'],['Underworld','Lower Halls','-100, 500','1'],['Underworld','Lower Halls','20, 30','1'],['Underworld','Lower Halls','NaN, 5','1']]}]}}
  self.file('npcs','supplemental/exiles-db/snapshot/npcs.jsonl.gz',[['42','Claim 42','npc','Unspecified','Exiles DB',npc]])
 def file(self,fid,path,rows):
  write_gz(self.root/'records'/fid/'0.json.gz',rows);self.m['files'][fid]={'path':path,'records':len(rows)};self.pc[(fid,'0')]=len(rows)
 def build(self):return build_atlas(self.root,self.m,self.data)
 def load(self,name):return json.load(gzip.open(self.root/name,'rt',encoding='utf8'))
 def test_full_atlas_preserves_spaces_ids_and_source_semantics(self):
  a=self.build();self.assertEqual(a['observations'],6);self.assertEqual(validate_atlas(self.root,self.m,self.pc)['observations'],6)
  ps=[p for z in a['zones'] if z['file'] for p in self.load(z['file'])];self.assertEqual(len({p['key'] for p in ps}),6)
  self.assertEqual({p['entity'] for p in ps if p['source']=='Addon observations'},{'npc','gameobject'})
  p=next(p for p in ps if p['layer']=='mystic');self.assertEqual(p['id'],'9007199254740993123');self.assertEqual((p['x'],p['y']),(25,75));self.assertIn('Player position',p['meta']['meaning'])
  raw=next(z for z in a['zones'] if z['space']=='world');self.assertFalse(raw['image']);self.assertEqual(raw['count'],2);self.assertTrue(all(0<p['plot_x']<100 for p in self.load(raw['file'])));self.assertEqual(self.load(raw['file'])[0]['x'],-200)
  self.assertTrue(any(z['map']=='930' for z in a['zones']));self.assertEqual(a['unmapped']['Exiles: missing or malformed coordinates'],1)
 def test_explicit_vendor_and_quest_roles_share_one_marker(self):
  rows=self.load('records/npcs/0.json.gz');structure=rows[0][5]['structure'];structure['headings']=['Vendor','Starts Quests','Ends Quests'];structure['tables'].append({'header':['Item','Price','Stock'],'rows':[['Bread','1','Unlimited']]});self.file('npcs',self.m['files']['npcs']['path'],rows)
  objects=self.load('records/objects/0.json.gz');objects[0][5]['type']='Questgiver';self.file('objects',self.m['files']['objects']['path'],objects)
  a=self.build();self.assertEqual(validate_atlas(self.root,self.m,self.pc)['observations'],6)
  ps=[p for z in a['zones'] if z['file'] for p in self.load(z['file'])];npcs=[p for p in ps if p['source']=='Exiles DB']
  self.assertEqual(len(npcs),3);self.assertTrue(all(p['roles']==['vendors','quest-givers'] for p in npcs));self.assertEqual(a['layers']['vendors'],3)
  self.assertEqual(next(p for p in ps if p['entity']=='gameobject')['layer'],'quest-givers')
 def test_vendor_heading_without_stock_table_is_not_a_vendor(self):
  rows=self.load('records/npcs/0.json.gz');rows[0][5]['structure']['headings']=['Vendor'];self.file('npcs',self.m['files']['npcs']['path'],rows)
  a=self.build();self.assertNotIn('vendors',a['layers'])
  ps=[p for z in a['zones'] if z['file'] for p in self.load(z['file'])];self.assertTrue(all('Vendor' not in p['meta'].get('roles','') for p in ps))
 def test_suspicious_alias_does_not_get_other_zone_artwork(self):
  r=['1','Object','world-object','Unspecified','Addon observations',{'example_zone':'Aszhara','example_map_id':'1','example_map_x':'20','example_map_y':'30'}];self.file('objects','cachedata/catalogue/gameobjects.tsv',[r]);a=self.build();z=next(z for z in a['zones'] if z['name']=='Aszhara');self.assertFalse(z['image'])
 def test_conflicting_captured_bounds_fail(self):
  for snap,right in [('one','-100'),('two','-200')]:
   p=self.data/'supplemental/worldforged'/snap/'data/worldmaparea_bounds_captured.csv.gz';p.parent.mkdir(parents=True)
   with gzip.open(p,'wt',encoding='utf8') as f:f.write('wma_id,name,server_map,top,bottom,left,right\n30,Elwynn,0,100,0,0,'+right+'\n')
  with self.assertRaisesRegex(ValueError,'Conflicting'):self.build()
 def test_validator_rejects_missing_part_wrong_revision_and_dangling_links(self):
  a=self.build();z=next(z for z in a['zones'] if z['file']);part=self.root/z['file'];saved=part.read_bytes();part.unlink()
  with self.assertRaises(ValueError):validate_atlas(self.root,self.m,self.pc)
  part.write_bytes(saved);a['revision']='b'*40;write_gz(self.root/'atlas-manifest.json.gz',a)
  with self.assertRaises(ValueError):validate_atlas(self.root,self.m,self.pc)
  self.build();write_gz(self.root/'atlas-links.json.gz',{})
  with self.assertRaises(ValueError):validate_atlas(self.root,self.m,self.pc)
 def test_validator_rejects_artwork_injection_and_inventory_omission(self):
  a=self.build();a['zones'][0]['image']='javascript:alert(1)';write_gz(self.root/'atlas-manifest.json.gz',a)
  with self.assertRaises(ValueError):validate_atlas(self.root,self.m,self.pc)
  a=self.build();a['maps']=[m for m in a['maps'] if m['id']!='930'];write_gz(self.root/'atlas-manifest.json.gz',a)
  with self.assertRaises(ValueError):validate_atlas(self.root,self.m,self.pc)
 def test_validator_rejects_inventory_attached_to_wrong_map(self):
  a=self.build();next(m for m in a['maps'] if m['id']=='930')['id']='999';write_gz(self.root/'atlas-manifest.json.gz',a)
  with self.assertRaises(ValueError):validate_atlas(self.root,self.m,self.pc)
 def test_validator_rejects_tampered_part_even_when_json_is_valid(self):
  a=self.build();z=next(z for z in a['zones'] if z['file']);ps=self.load(z['file']);ps[0]['plot_x']=float('inf');write_gz(self.root/z['file'],ps)
  with self.assertRaises(ValueError):validate_atlas(self.root,self.m,self.pc)

if __name__=='__main__':unittest.main()
