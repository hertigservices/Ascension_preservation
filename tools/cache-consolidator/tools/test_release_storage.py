import json,subprocess,tempfile,unittest,sys
from pathlib import Path
from unittest.mock import patch
import release_storage
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'cache-upload/collector'))
import collector

class ReleaseTests(unittest.TestCase):
 def test_receipts_survive_manifest_publication_and_bulk_stays_out_of_git(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);repo=root/'repo';repo.mkdir();data=repo/'cachedata';data.mkdir()
   def git(*a):return subprocess.check_output(['git','-C',str(repo),*a],text=True,stderr=subprocess.DEVNULL).strip()
   git('init');git('config','user.name','Test');git('config','user.email','test@example.invalid')
   (repo/'.gitignore').write_text('/cachedata/*\n!/cachedata/contributions/\n')
   git('add','.gitignore');git('commit','-m','base')
   key='a'*64;folder=data/'contributions';folder.mkdir();proof=folder/(key+'.json')
   proof.write_text(json.dumps({'schema':2,'bundle':key,'revision':'b'*64,'no_op_eligible':True}))
   (data/'large.txt').write_text('public data')
   with patch.object(release_storage.dataset,'publish') as publish:
    m=release_storage.deliver(repo,data,root/'packs',True)
    publish.assert_called_once()
   self.assertEqual(git('ls-files','cachedata'),f'cachedata/contributions/{key}.json')
   self.assertIn('cachedata/large.txt',m['files'])
   self.assertTrue(collector.commit_has_bundle(str(repo),git('rev-parse','HEAD'),key,require_noop=True))
   with patch.object(release_storage.dataset,'publish'):
    release_storage.deliver(repo,data,root/'packs',True)
   self.assertEqual(git('status','--porcelain'),'')
   (repo/'unrelated').write_text('draft');git('add','unrelated')
   with self.assertRaisesRegex(ValueError,'Unrelated staged'):release_storage.deliver(repo,data,root/'packs',False)
   git('reset','HEAD','unrelated');git('add','-f','cachedata/large.txt');git('commit','-m','legacy bulk')
   with self.assertRaisesRegex(ValueError,'migration'):release_storage.deliver(repo,data,root/'packs',False)
 def test_receipts_reject_extra_fields(self):
  with tempfile.TemporaryDirectory() as d:
   data=Path(d);folder=data/'contributions';folder.mkdir();key='a'*64
   (folder/(key+'.json')).write_text(json.dumps({'schema':2,'bundle':key,'revision':'b'*64,'no_op_eligible':False,'private':'unexpected'}))
   with self.assertRaisesRegex(ValueError,'Invalid contribution'):release_storage.check_receipts(data)
if __name__=='__main__':unittest.main()
