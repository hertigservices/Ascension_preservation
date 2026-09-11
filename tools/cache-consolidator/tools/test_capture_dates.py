import unittest,sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent))
import capture_dates, export
class CaptureTests(unittest.TestCase):
 def test_web_date_unknown_and_archiving_does_not_change_it(self):
  for p in ['C:/intake/_inbox/web-'+'a'*32+'/part-0001/enUS/itemcache.wdb','C:/intake/_inbox/archive/2026-09/web-'+'a'*32+'/part-0001/enUS/itemcache.wdb']:
   self.assertEqual(capture_dates.source_date(p),'')
 def test_existing_web_dates_repair_without_losing_original_observations(self):
  sources={'original':{'id':'1','captured':'2026-08-30','path':'original/itemcache.wdb'},'web':{'id':'2','captured':'2026-09-10','path':'web-'+'a'*32+'/part-0001/itemcache.wdb'}}
  row={'srcs':{'1','2'},'first_captured':'2026-08-30','last_captured':'2026-09-10','payload':b'unchanged'}
  only={'srcs':{'2'},'first_captured':'2026-09-10','last_captured':'2026-09-10'}
  self.assertEqual(capture_dates.repair_dates(sources,{'item':{1:row,2:only}}),1)
  self.assertEqual(row['last_captured'],'2026-08-30');self.assertEqual(row['payload'],b'unchanged');self.assertEqual(only['last_captured'],'')
  self.assertEqual(capture_dates.repair_dates(sources,{'item':{1:row,2:only}}),0)
 def test_other_mode_cannot_lend_a_date_to_an_undated_upload(self):
  sources={'1':{'id':'1','captured':'2026-08-30','slug':'free-pick'},'2':{'id':'2','captured':'2026-09-01','slug':'conquest-of-azeroth'},'3':{'id':'3','captured':'','slug':'free-pick'}}
  old={'srcs':'1','modes':'free-pick','last_captured':'2026-08-30'}
  new={'srcs':'2,3','modes':'free-pick,conquest-of-azeroth','last_captured':'2026-09-01'}
  for r in [old,new]:capture_dates.attach_context(r,sources)
  recs=[(1,'a',old,b'old'),(1,'z',new,b'new')]
  self.assertEqual(export.pick_winners(recs,'free-pick')[1][2],b'old')
  self.assertEqual(export.pick_winners(recs)[1][2],b'new')
 def test_undated_reuploads_preserve_existing_winner_but_add_new_entries(self):
  sources={'1':{'id':'1','captured':'','slug':'free-pick'},'2':{'id':'2','captured':'','slug':'free-pick'}}
  rows=[{'srcs':str(i),'modes':'free-pick','last_captured':''} for i in [1,2]]
  for r in rows:capture_dates.attach_context(r,sources)
  recs=[(1,'a',rows[0],b'first'),(1,'z',rows[1],b'later'),(2,'z',rows[1],b'new entry')]
  result=export.pick_winners(recs,'free-pick');self.assertEqual(result[1][2],b'first');self.assertEqual(result[2][2],b'new entry')
if __name__=='__main__':unittest.main()
