import hashlib, importlib.util, json, os, tempfile, unittest, uuid
from pathlib import Path
from unittest.mock import patch
SPEC=importlib.util.spec_from_file_location('bridge',Path(__file__).parents[1]/'collector'/'collector.py')
bridge=importlib.util.module_from_spec(SPEC);SPEC.loader.exec_module(bridge)

class Client:
    def __init__(self,data): self.data=data;self.acks=[]
    def call(self,path,payload=None,lease=None,binary=False):
        if binary: return self.data[int(path.rsplit('/',1)[1])]
        if path.endswith('/ack'): self.acks.append(payload)
        return {}

class BridgeTests(unittest.TestCase):
    def test_large_manifest_limit_matches_upload_service(self):
        part={'name':'itemcache.wdb','mode':'unknown','size':4*1024*1024,'sha256':'a'*64}
        manifest={'policy':1,'files':[dict(part,index=i) for i in range(77)]}
        bridge.validate_manifest(manifest)
        manifest['files']=[dict(part,index=i) for i in range(128)]
        bridge.validate_manifest(manifest)
        manifest['files'].append(dict(part,index=128,size=1))
        with self.assertRaises(ValueError): bridge.validate_manifest(manifest)
    def test_real_validator_atomic_delivery_and_retry(self):
        import struct
        data=b'BDIW'+struct.pack('<I',12340)+b'SUne'+bytes(12)+struct.pack('<II',123,4)+bytes(4)+bytes(8)
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sid=str(uuid.uuid4());client=Client([data])
            claim={'id':sid,'lease':'test','expires':int(bridge.time.time())+3600,'manifest':{'policy':1,'files':[{'index':0,'name':'itemcache.wdb','mode':'conquest-of-azeroth','size':len(data),'sha256':hashlib.sha256(data).hexdigest()}]}}
            config={'state':str(root/'private'),'inbox':str(root/'work'/'_inbox'),'node':'node'}
            result=bridge.process_one(client,claim,config,publish=False)
            self.assertEqual(result['status'],'staged')
            paths=list((root/'work'/'_inbox').rglob('*.wdb'))
            self.assertEqual(len(paths),1);self.assertEqual(paths[0].read_bytes(),data)
            self.assertIn('Conquest of Azeroth',str(paths[0]))
            bridge.process_one(client,claim,config,publish=False)
            self.assertEqual(len(list((root/'work'/'_inbox').rglob('*.wdb'))),1)
            self.assertEqual(client.acks,[])
    def test_review_code_never_enters_watched_inbox(self):
        data=b'AIO_sv_Addons={}\n'
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sid=str(uuid.uuid4());client=Client([data]);claim={'id':sid,'lease':'test','expires':int(bridge.time.time())+3600,'manifest':{'policy':1,'files':[{'index':0,'name':'aio_client.lua','mode':'unknown','size':len(data),'sha256':hashlib.sha256(data).hexdigest()}]}}
            config={'state':str(root/'private'),'inbox':str(root/'work'/'_inbox'),'node':'node'}
            result=bridge.process_one(client,claim,config,publish=False)
            self.assertEqual(result['status'],'needs_review')
            self.assertFalse((root/'work'/'_inbox').exists())
            self.assertTrue(list((root/'private'/sid/'review').rglob('aio_client.lua')))
    def test_ascension_references_are_validated_and_delivered_for_automatic_preservation(self):
        for name,data in [('ascension_coareader.lua',b'CoAReaderDB={["edges"]={[101]={[1]=102}}}\n'),('ascensionharvest.lua',b'AscensionHarvestDB={["watch"]={["Realm"]={["spells"]={[42]="Fireball"}}}}\n')]:
            with self.subTest(name=name), tempfile.TemporaryDirectory() as tmp:
                root=Path(tmp);sid=str(uuid.uuid4());client=Client([data]);claim={'id':sid,'lease':'test','expires':int(bridge.time.time())+3600,'manifest':{'policy':1,'files':[{'index':0,'name':name,'mode':'unknown','size':len(data),'sha256':hashlib.sha256(data).hexdigest(),'review':False}]}}
                config={'state':str(root/'private'),'inbox':str(root/'work'/'_inbox'),'node':'node'}
                result=bridge.process_one(client,claim,config,publish=False)
                self.assertEqual(result['status'],'staged')
                self.assertTrue((root/'work'/'_inbox').exists())
                self.assertTrue(list((root/'work'/'_inbox').rglob(name)))
    def test_invalid_download_never_enters_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sid=str(uuid.uuid4());claim={'id':sid,'lease':'test','expires':int(bridge.time.time())+3600,'manifest':{'policy':1,'files':[{'index':0,'name':'itemcache.wdb','mode':'unknown','size':3,'sha256':'0'*64}]}}
            with self.assertRaises(ValueError): bridge.process_one(Client([b'bad']),claim,{'state':str(root/'private'),'inbox':str(root/'inbox')},publish=False)
            self.assertFalse((root/'inbox').exists())
    def test_retry_of_acked_result_does_not_republish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);sid=str(uuid.uuid4());job=root/'private'/sid;job.mkdir(parents=True)
            client=Client([]);claim={'id':sid,'lease':'test','manifest':{'policy':1,'files':[{'index':0,'name':'itemcache.wdb','mode':'unknown','size':3,'sha256':'0'*64}]}}
            config={'state':str(root/'private'),'inbox':str(root/'inbox'),'publish_repo':'unused'}
            key=bridge.bundle_key(claim['manifest'],bridge.processing_revision(config))
            (job/'result.json').write_text(json.dumps({'schema':2,'submission':sid,'bundle':key,'result':{'status':'published','commit':'a'*40}}))
            with patch.object(bridge,'verified_commit',return_value=True),patch.object(bridge,'run_publisher') as publish:
                bridge.process_one(client,claim,config)
            publish.assert_not_called();self.assertEqual(client.acks[0]['status'],'published')
    def test_state_cannot_be_inside_watched_inbox(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);claim={'id':str(uuid.uuid4()),'lease':'test','manifest':{'policy':1,'files':[{'index':0,'name':'itemcache.wdb','mode':'unknown','size':3,'sha256':'0'*64}]}}
            with self.assertRaises(ValueError): bridge.process_one(Client([]),claim,{'state':str(root/'inbox'/'private'),'inbox':str(root/'inbox')})
    def test_validator_does_not_inherit_credentials(self):
        with patch.dict(os.environ,{'GH_TOKEN':'secret','ASCENSION_UPLOAD_COLLECTOR_TOKEN':'secret','NODE_OPTIONS':'--require bad.js'}):
            env=bridge.validator_environment();self.assertNotIn('GH_TOKEN',env);self.assertNotIn('ASCENSION_UPLOAD_COLLECTOR_TOKEN',env);self.assertNotIn('NODE_OPTIONS',env)
if __name__=='__main__':unittest.main()
