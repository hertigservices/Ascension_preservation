import io,json,tempfile,unittest,hashlib
from pathlib import Path
from unittest.mock import patch
import dataset,r2_publish

class Failure(Exception):
    def __init__(self,code):self.response={'Error':{'Code':code}}
class Store:
    def __init__(self):self.data={};self.fail=False
    def get_object(self,*,Bucket,Key):
        if Key not in self.data:raise Failure('404')
        b,m=self.data[Key];return {'Body':io.BytesIO(b),'ETag':hashlib.sha256(b).hexdigest()}
    def head_object(self,*,Bucket,Key):
        v=self.get_object(Bucket=Bucket,Key=Key);b,m=self.data[Key];return {'ContentLength':len(b),'Metadata':m}
    def put_object(self,*,Bucket,Key,Body,Metadata=None,IfMatch=None,IfNoneMatch=None,**kwargs):
        if self.fail and not Key.endswith('current.json'):raise Failure('500')
        if IfNoneMatch=='*' and Key in self.data:raise Failure('412')
        if IfMatch and self.get_object(Bucket=Bucket,Key=Key)['ETag']!=IfMatch:raise Failure('412')
        self.data[Key]=(Body if isinstance(Body,bytes) else Body.read(),Metadata or {})

class StorageTests(unittest.TestCase):
    def setUp(self):self.t=tempfile.TemporaryDirectory();self.p=Path(self.t.name);self.source=self.p/'source';self.source.mkdir()
    def tearDown(self):self.t.cleanup()
    def test_roundtrip_selection_and_reuse(self):
        (self.source/'a.bin').write_bytes(b'hello'*1000);(self.source/'b.bin').write_bytes(b'world'*1000)
        m=dataset.prepare(self.source,self.p/'packs','fixture',prefix='cachedata')
        same=dataset.prepare(self.source,self.p/'packs','fixture',prefix='cachedata',previous=m);self.assertEqual(m,same)
        report=dataset.restore(m,self.p/'out',self.p/'cache',select='cachedata/a.bin',local_packs=self.p/'packs')
        self.assertEqual(report['selected'],1);self.assertEqual((self.p/'out/cachedata/a.bin').read_bytes(),b'hello'*1000)
        self.assertFalse((self.p/'out/cachedata/b.bin').exists())
    def test_large_file_parts_and_duplicate_content(self):
        (self.source/'a').write_bytes(b'abcdef'*20);(self.source/'b').write_bytes(b'abcdef'*20)
        with patch.object(dataset,'CHUNK',18):
            m=dataset.prepare(self.source,self.p/'packs','fixture');self.assertGreater(len(m['files']['a']['parts']),1)
            dataset.restore(m,self.p/'out',self.p/'cache',local_packs=self.p/'packs')
        self.assertEqual((self.p/'out/a').read_bytes(),(self.source/'a').read_bytes())
    def test_corruption_and_traversal_fail(self):
        (self.source/'a').write_bytes(b'hello');m=dataset.prepare(self.source,self.p/'packs','fixture')
        (self.p/'packs'/next(iter(m['packs']))).write_bytes(b'bad')
        with self.assertRaises(ValueError):dataset.restore(m,self.p/'out',self.p/'cache',local_packs=self.p/'packs')
        for path in ('../x','a/../b','C:/x','a\\b','CON','x/aux.txt'):
            with self.assertRaises(ValueError):dataset.safe_path(path)
    def test_failed_upload_does_not_promote(self):
        (self.source/'a').write_bytes(b'hello');report=r2_publish.inventory(self.source,'media');store=Store()
        current=r2_publish.publish(store,'fixture',self.source,report,'media',1)
        (self.source/'a').write_bytes(b'changed');report2=r2_publish.inventory(self.source,'media');store.fail=True
        with self.assertRaises(Failure):r2_publish.publish(store,'fixture',self.source,report2,'media',1)
        self.assertEqual(json.loads(store.data['media/current.json'][0]),current)
    def test_pointer_cas_and_idempotence(self):
        (self.source/'a').write_bytes(b'hello');report=r2_publish.inventory(self.source,'media');store=Store()
        a=r2_publish.publish(store,'fixture',self.source,report,'media',1);b=r2_publish.publish(store,'fixture',self.source,report,'media',1);self.assertEqual(a,b)
    def test_retry_restarts_bad_complete_partial(self):
        target=self.p/'download';target.with_name('download.part').write_bytes(b'bad')
        class Response(io.BytesIO):
            status=200;headers={}
        with patch('urllib.request.urlopen',return_value=Response(b'new')):
            dataset.download('https://example.invalid',target,hashlib.sha256(b'new').hexdigest(),3)
        self.assertEqual(target.read_bytes(),b'new')
if __name__=='__main__':unittest.main()
