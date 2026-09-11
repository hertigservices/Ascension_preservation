import unittest,sys,tempfile
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import luaser,reference_merge
class ReferenceTests(unittest.TestCase):
 def test_snapshots_filter_private_branches_deduplicate_and_preserve_conflicts(self):
  state={};g=luaser.loads('CoAReaderDB={edges={[1]={2}},knownSpells={3},identity={name="Private"},tree={player="Private",name="Class"}}')
  reference_merge.merge('ascension_coareader.lua',state,g,'one',{},[])
  reference_merge.merge('ascension_coareader.lua',state,g,'two',{},[])
  self.assertEqual(len(state['reference_captures']['ascension_coareader.lua']),1)
  g2=luaser.loads('CoAReaderDB={edges={[1]={4}}}')
  reference_merge.merge('ascension_coareader.lua',state,g2,'three',{},[])
  self.assertEqual(len(state['reference_captures']['ascension_coareader.lua']),2)
  with tempfile.TemporaryDirectory() as tmp:
   output=reference_merge.write(state,tmp);self.assertEqual(len(output),2)
   for path,note in output:
    text=Path(path).read_text();self.assertNotIn('Private',text);self.assertNotIn('knownSpells',text);luaser.loads(text)
 def test_harvest_only_keeps_numeric_game_references(self):
  g=luaser.loads('AscensionHarvestDB={realms={Realm={items={[12]={name="Sword",player="Private"},Other="Private"},guild={name="Private"}}},watch={Realm={spells={[42]="Fireball"},casters={Private=true}}},sweeps={Realm={quest={data={[6]={title="Quest"}},cursor=42}}}}')
  text=luaser.dumps(reference_merge.filtered('ascensionharvest.lua',g))
  self.assertIn('Sword',text);self.assertIn('Fireball',text);self.assertIn('Quest',text)
  for value in ['Private','cursor','guild','casters','Other']:self.assertNotIn(value,text)
 def test_control_bytes_followed_by_digits_round_trip_without_changing_spell_text(self):
  original={'DB':luaser.table_from({'text':'Dazed'+chr(3)+'11'})}
  text=luaser.dumps(original)
  self.assertEqual(luaser.loads(text)['DB'].get('text'),original['DB'].get('text'))
 def test_executable_lua_rejected_before_merge(self):
  with self.assertRaises(luaser.LuaError):luaser.loads('CoAReaderDB=os.execute("bad")')
if __name__=='__main__':unittest.main()
