import io,json,subprocess,tempfile,unittest,sys,zipfile,urllib.error
from pathlib import Path
from unittest.mock import patch
import install
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'data-storage'))
import dataset
class FetchTests(unittest.TestCase):
 def test_fetch_uses_release_manifest_and_only_client_files(self):
  with tempfile.TemporaryDirectory() as d:
   root=Path(d);source=root/'source';source.mkdir()
   for name in ['wdb/mode/itemcache.wdb.gz','lua/addon.lua','union/items.tsv']:
    p=source/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(name.encode())
   manifest=dataset.prepare(source,root/'packs','cache',prefix='cachedata')
   restore=dataset.restore
   def local_restore(m,destination,cache,select):return restore(m,destination,cache,select,root/'packs')
   with patch.object(install.urllib.request,'urlopen',return_value=io.BytesIO(dataset.canonical(manifest))),patch.object(dataset,'restore',side_effect=local_restore):
    self.assertEqual(install.fetch_dataset(root/'download'),2)
   self.assertEqual((root/'download/wdb/mode/itemcache.wdb.gz').read_bytes(),b'wdb/mode/itemcache.wdb.gz')
   self.assertFalse((root/'download/union').exists())
 def test_pre_cutover_data_uses_legacy_zip_only_on_manifest_404(self):
  payload=io.BytesIO()
  with zipfile.ZipFile(payload,'w') as z:z.writestr('ascension-data-main/cachedata/wdb/mode/itemcache.wdb.gz',b'cached bytes')
  with tempfile.TemporaryDirectory() as d:
   missing=urllib.error.HTTPError(install.DATA_MANIFEST,404,'Not found',{},None)
   with patch.object(install.urllib.request,'urlopen',side_effect=[missing,io.BytesIO(payload.getvalue())]):
    self.assertEqual(install.fetch_dataset(d),1)
   self.assertEqual((Path(d)/'wdb/mode/itemcache.wdb.gz').read_bytes(),b'cached bytes')
if __name__=='__main__':unittest.main()
