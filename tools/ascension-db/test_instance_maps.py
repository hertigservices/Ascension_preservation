import copy,json,unittest
from capture_instance_maps import normalize as capture
def normalize(raw):return capture(raw,captured_at="2026-09-11",expected={"blackwinglair"})
import test_atlas
from atlas import build_atlas
from validate_atlas import validate_atlas
class CaptureTests(unittest.TestCase):
 def source(self):return {'dungeons':[{'id':'blackwinglair','name':'Blackwing Lair','mapping_id':422,'maps':[{'label':'blackwinglair_floor1','image':'maps/blackwinglair_floor1.webp','width':6144,'height':4096,'kg_floor_id':310,'enemies':[{'id':1,'npc_id':12435,'name':'Razorgore','pos':[2892.0832,2150.3568],'classification':3}]}]}]}
 def test_paired_pixels_keep_ids_and_no_inferred_game_mode(self):
  fs,ps=normalize(json.dumps(self.source()).encode());self.assertEqual(fs[0]['server_map'],'469');self.assertEqual(ps[0]['pixel_x'],2892.0832);self.assertEqual(ps[0]['id'],'12435');self.assertNotIn('modes',ps[0])
 def test_manual_pins_do_not_invent_npc_ids(self):
  s=self.source();e=s['dungeons'][0]['maps'][0]['enemies'][0];e.update(id=None,npc_id=None,ascension_pinned=True);fs,ps=normalize(json.dumps(s).encode());self.assertEqual(ps[0]['type'],'route-landmark');self.assertEqual(ps[0]['npc_id'],'');self.assertTrue(ps[0]['id'].startswith('reference:'))
 def test_invalid_pairing_and_coordinates_fail(self):
  for key,value in [('image','../private.webp'),('width',0),('label','../../x')]:
   s=self.source();s['dungeons'][0]['maps'][0][key]=value
   with self.assertRaises(ValueError):normalize(json.dumps(s).encode())
  for position in [[-1,4],[7000,3],[float('nan'),1]]:
   s=self.source();s['dungeons'][0]['maps'][0]['enemies'][0]['pos']=position
   with self.assertRaises(ValueError):normalize(json.dumps(s).encode())
 def test_invalid_provider_domains_and_implicit_landmarks_fail(self):
  for key,value in [('classification',True),('classification',0),('classification',6),('id',None),('npc_id',None)]:
   s=self.source();s['dungeons'][0]['maps'][0]['enemies'][0][key]=value
   with self.assertRaises(ValueError):normalize(json.dumps(s).encode())
  for value in [True,0,-1,'422']:
   s=self.source();s['dungeons'][0]['mapping_id']=value
   with self.assertRaises(ValueError):normalize(json.dumps(s).encode())
 def test_duplicate_floors_and_unreviewed_instances_fail(self):
  s=self.source();s['dungeons'][0]['maps']*=2
  with self.assertRaises(ValueError):normalize(json.dumps(s).encode())
  s=self.source();s['dungeons'][0]['id']='unknown_custom_instance'
  with self.assertRaises(ValueError):normalize(json.dumps(s).encode())
 def test_complete_capture_rejects_missing_layouts_and_invalid_dates(self):
  with self.assertRaises(ValueError):capture(json.dumps(self.source()).encode(),captured_at='2026-09-11')
  with self.assertRaises(ValueError):capture(json.dumps(self.source()).encode(),captured_at='not-a-date')
  s=self.source();s['dungeons'][0]['maps']=[]
  with self.assertRaises(ValueError):normalize(json.dumps(s).encode())
  s=self.source();s['dungeons']*=2
  with self.assertRaises(ValueError):normalize(json.dumps(s).encode())
 def test_floor_projection_and_existing_observations_stay_separate(self):
  fixture=test_atlas.AtlasTests();fixture.setUp()
  try:
   fs,ps=normalize(json.dumps(self.source()).encode());fs[0]['server_map']='0';ps[0]['server_map']='0'
   fixture.file('floors','supplemental/instance-route-maps/test/floors.jsonl.gz',[[f['id'],f['name'],'instance-floor','Unspecified','Exiles route planner',f] for f in fs]);fixture.file('routepoints','supplemental/instance-route-maps/test/npcs.jsonl.gz',[[p['id'],p['name'],p['type'],'Unspecified','Exiles route planner',p] for p in ps])
   a=fixture.build();self.assertEqual(a['preserved_observations'],6);self.assertEqual(a['reference_locations'],1);self.assertEqual(validate_atlas(fixture.root,fixture.m,fixture.pc)['observations'],7)
   z=next(z for z in a['zones'] if z['space']=='floor');point=fixture.load(z['file'])[0];self.assertEqual(point['x'],2892.0832);self.assertAlmostEqual(point['plot_x'],2892.0832/6144*100);self.assertEqual(point['source'],'Exiles route planner')
   from atlas import write_gz
   import hashlib
   for field in ['provider_enemy_id','manual_reference','floor','layout','reference_era','provider_floor_id','pixel_width','pixel_height']:
    altered=copy.deepcopy(point);altered['meta'][field]='tampered';rows=[altered];raw=json.dumps(rows,ensure_ascii=False,separators=(',',':')).encode();original_file=z['file'];z['file']='atlas/'+hashlib.sha256(raw).hexdigest()[:20]+'.json.gz';write_gz(fixture.root/z['file'],rows);write_gz(fixture.root/'atlas-manifest.json.gz',a)
    with self.assertRaisesRegex(ValueError,'reference metadata'):validate_atlas(fixture.root,fixture.m,fixture.pc)
    z['file']=original_file
   z['source_record']='objects/0/0';write_gz(fixture.root/'atlas-manifest.json.gz',a)
   with self.assertRaisesRegex(ValueError,'paired floor'):validate_atlas(fixture.root,fixture.m,fixture.pc)
  finally:fixture.doCleanups()
if __name__=='__main__':unittest.main()
