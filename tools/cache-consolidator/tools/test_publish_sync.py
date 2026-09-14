import unittest,tempfile,subprocess
from pathlib import Path
from publish_sync import synchronize
class SyncTests(unittest.TestCase):
 def test_preserve_independent_changes_and_reject_cache_overlap(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);remote=root/'remote.git';a=root/'a';b=root/'b'
   def git(path,*args):return subprocess.check_output(['git','-C',str(path),*args],stderr=subprocess.DEVNULL,text=True).strip()
   subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
   subprocess.run(['git','clone',str(remote),str(a)],check=True,capture_output=True)
   def config(p):git(p,'config','user.name','Test');git(p,'config','user.email','test@example.invalid')
   def commit(p,name,data):f=p/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(data);git(p,'add',name);git(p,'commit','-m',name)
   config(a);git(a,'checkout','-b','main');commit(a,'cachedata/a','base');git(a,'push','origin','main')
   subprocess.run(['git','clone','-b','main',str(remote),str(b)],check=True,capture_output=True);config(b)
   commit(a,'cachedata/a','new data');before=git(a,'rev-parse','HEAD:cachedata')
   commit(b,'README.md','new docs');git(b,'push','origin','main')
   self.assertTrue(synchronize(a,'main'));self.assertEqual(git(a,'rev-parse','HEAD:cachedata'),before);self.assertEqual((a/'README.md').read_text(),'new docs')
   git(a,'push','origin','main');git(b,'pull','--ff-only');commit(b,'cachedata/a','remote change');git(b,'push','origin','main')
   head=git(a,'rev-parse','HEAD');self.assertFalse(synchronize(a,'main'));self.assertEqual(git(a,'rev-parse','HEAD'),head)
   git(b,'reset','--hard',head);git(b,'push','--force','origin','main')
   commit(b,'.gitattributes','cachedata/** -text');git(b,'push','origin','main')
   self.assertFalse(synchronize(a,'main'));self.assertEqual(git(a,'rev-parse','HEAD'),head)
 def test_manifest_mode_integrates_docs_and_holds_storage_changes(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);remote=root/'remote.git';a=root/'a';b=root/'b'
   def git(path,*args):return subprocess.check_output(['git','-C',str(path),*args],stderr=subprocess.DEVNULL,text=True).strip()
   subprocess.run(['git','init','--bare',str(remote)],check=True,capture_output=True)
   subprocess.run(['git','clone',str(remote),str(a)],check=True,capture_output=True)
   def config(p):git(p,'config','user.name','Test');git(p,'config','user.email','test@example.invalid')
   def commit(p,name,data):f=p/name;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(data);git(p,'add',name);git(p,'commit','-m',name)
   config(a);git(a,'checkout','-b','main');commit(a,'datasets/cache.json','base');git(a,'push','origin','main')
   subprocess.run(['git','clone','-b','main',str(remote),str(b)],check=True,capture_output=True);config(b)
   commit(b,'README.md','docs');git(b,'push','origin','main')
   self.assertTrue(synchronize(a,'main'));self.assertEqual(git(a,'show','HEAD:datasets/cache.json'),'base')
   git(a,'push','origin','main');git(b,'pull','--ff-only');commit(b,'.ascension-storage.json','changed');git(b,'push','origin','main')
   self.assertFalse(synchronize(a,'main'))
if __name__=='__main__':unittest.main()
