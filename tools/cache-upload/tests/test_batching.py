import hashlib, importlib.util, json, struct, tempfile, unittest, uuid, urllib.error
from pathlib import Path
from unittest.mock import patch
from test_collector import bridge

def wdb(entry=123):return b'BDIW'+struct.pack('<I',12340)+b'SUne'+bytes(12)+struct.pack('<II',entry,4)+bytes(4)+bytes(8)
def claim(data,mode='unknown'):
    return {'id':str(uuid.uuid4()),'lease':uuid.uuid4().hex,'expires':int(bridge.time.time())+7200,'manifest':{'policy':1,'files':[{'index':0,'name':'itemcache.wdb','mode':mode,'size':len(data),'sha256':hashlib.sha256(data).hexdigest()}]}}
class Client:
    def __init__(self,claims,data):self.claims=list(claims);self.data=data;self.acks=[];self.renewals=[];self.fail_ack=None
    def call(self,path,payload=None,lease=None,binary=False):
        if path=='collector/claim':return {'submission':self.claims.pop(0) if self.claims else None}
        sid=path.split('/')[1]
        if binary:return self.data[sid]
        if path.endswith('/renew'):self.renewals.append(sid)
        if path.endswith('/ack'):
            if self.fail_ack==sid and payload['status']=='published':raise OSError('response lost')
            self.acks.append((sid,payload))
        return {}
class BatchTests(unittest.TestCase):
    def setUp(self):
        import logging
        logger=logging.getLogger('ascension-upload-collector');old=logger.disabled;logger.disabled=True
        self.addCleanup(setattr,logger,'disabled',old)

    def config(self,root):return {'state':str(root/'private'),'inbox':str(root/'work'/'_inbox'),'node':'node','publish_repo':'unused'}
    def test_batch_claims_every_waiting_peer_before_validation_and_publishes_once(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=self.config(root);cs=[claim(wdb(n)) for n in (1,2,3)];client=Client(cs[1:],{c['id']:wdb(n) for c,n in zip(cs,(1,2,3))})
            original=bridge.prepare_submission
            def prepare(*args):
                self.assertEqual(client.claims,[])
                return original(*args)
            def publish(config,jobs,leases):
                self.assertEqual(len(jobs),3);self.assertFalse(Path(config['inbox']).exists())
                self.assertFalse(client.acks)
                return 'a'*40
            with patch.object(bridge,'prepare_submission',side_effect=prepare),patch.object(bridge,'run_publisher',side_effect=publish) as pub:
                self.assertTrue(bridge.process_batch(client,cs[0],cfg))
            self.assertEqual(pub.call_count,1);self.assertEqual(len(client.acks),3)
            self.assertEqual({a[1]['commit'] for a in client.acks},{'a'*40})
    def test_invalid_peer_cannot_poison_valid_peer(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cs=[claim(wdb(1)),claim(wdb(2))];client=Client(cs[1:],{cs[0]['id']:b'bad',cs[1]['id']:wdb(2)})
            with patch.object(bridge,'run_publisher',return_value='a'*40) as pub:
                self.assertFalse(bridge.process_batch(client,cs[0],self.config(root)))
            self.assertEqual(len(pub.call_args.args[1]),1)
            self.assertEqual(dict(client.acks)[cs[0]['id']]['status'],'received')
            self.assertEqual(dict(client.acks)[cs[1]['id']]['status'],'published')
    def test_ack_failure_keeps_terminal_result_and_does_not_downgrade_peers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=self.config(root);cs=[claim(wdb(1)),claim(wdb(2))];client=Client(cs[1:],{cs[0]['id']:wdb(1),cs[1]['id']:wdb(2)});client.fail_ack=cs[0]['id']
            with patch.object(bridge,'run_publisher',return_value='a'*40) as pub:
                self.assertFalse(bridge.process_batch(client,cs[0],cfg))
                for _ in range(5):bridge.replay_pending_acks(client,cfg['state'])
                self.assertTrue(all(result['status']=='published' for _,result in client.acks))
                client.fail_ack=None;bridge.replay_pending_acks(client,cfg['state'])
            self.assertEqual(pub.call_count,1);self.assertEqual(len(client.acks),2)
            self.assertFalse(list(Path(cfg['state']).glob('*/pending-ack.json')))
    def test_failed_publisher_never_records_publication_evidence(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=self.config(root);c=claim(wdb());client=Client([],{c['id']:wdb()})
            with patch.object(bridge,'run_publisher',side_effect=RuntimeError('audit failed')):
                self.assertFalse(bridge.process_batch(client,c,cfg))
            self.assertFalse((Path(cfg['state'])/'published-bundles.json').exists())
            self.assertFalse((Path(cfg['state'])/c['id']/'result.json').exists())
            self.assertEqual(client.acks[0][1]['status'],'received')
    def test_exact_verified_repeat_skips_publisher_and_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=self.config(root);c=claim(wdb());client=Client([],{c['id']:wdb()})
            key=bridge.bundle_key(c['manifest'],bridge.processing_revision(cfg))
            with patch.object(bridge,'verified_bundles',return_value={key:'a'*40}),patch.object(bridge,'run_publisher') as pub:
                result=bridge.process_one(client,c,cfg)
            pub.assert_not_called();self.assertFalse(Path(cfg['inbox']).exists());self.assertTrue(result['duplicate'])
    def test_semantic_key_preserves_mode_policy_multiplicity_and_ignores_order(self):
        a=claim(wdb())['manifest'];b=json.loads(json.dumps(a));b['files'][0]['mode']='free-pick'
        self.assertNotEqual(bridge.bundle_key(a,'rev'),bridge.bundle_key(b,'rev'))
        b=json.loads(json.dumps(a));b['files'].append(dict(b['files'][0],index=1))
        self.assertNotEqual(bridge.bundle_key(a,'rev'),bridge.bundle_key(b,'rev'))
        self.assertNotEqual(bridge.bundle_key(a,'rev'),bridge.bundle_key(a,'new-rev'))
        b['files'][1]['sha256']='b'*64;c=json.loads(json.dumps(b));c['files'].reverse()
        self.assertEqual(bridge.bundle_key(b,'rev'),bridge.bundle_key(c,'rev'))
    def test_review_mixed_job_keeps_hold_and_is_never_noop_indexed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=self.config(root);c=claim(wdb());job=root/'private'/c['id'];accepted=job/'validated-test'/'accepted';accepted.mkdir(parents=True);(accepted/'x').write_bytes(wdb())
            prepared={'claim':c,'job':job,'validated':accepted.parent,'accepted':True,'review':True};leases=bridge.Leases(Client([],{}));leases.add(c)
            with patch.object(bridge,'run_publisher',return_value='a'*40):
                results=bridge.finish_batch(leases.client,[prepared],cfg,leases)
            self.assertEqual(results[c['id']]['status'],'needs_review');self.assertEqual(results[c['id']]['commit'],'a'*40)
            self.assertEqual(json.loads((root/'private'/'published-bundles.json').read_text())['bundles'],{})
    def test_lease_409_blocks_new_publication(self):
        c=claim(wdb());client=Client([],{});leases=bridge.Leases(client);leases.add(c)
        with patch.object(client,'call',side_effect=urllib.error.HTTPError('https://example',409,'expired',{},None)):
            leases.tick(force=True)
        with self.assertRaises(RuntimeError):leases.assert_active([{'claim':c}])
    def test_lost_peer_during_preparation_does_not_poison_owned_peers(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=self.config(root);cs=[claim(wdb(n)) for n in (1,2,3)];client=Client(cs[1:],{c['id']:wdb(n) for c,n in zip(cs,(1,2,3))})
            original=bridge.prepare_submission
            def prepare(client,claim,config,leases):
                job=original(client,claim,config,leases)
                if claim['id']==cs[2]['id']:leases.lost.add(cs[1]['id'])
                return job
            with patch.object(bridge,'prepare_submission',side_effect=prepare),patch.object(bridge,'run_publisher',return_value='a'*40) as pub:
                self.assertFalse(bridge.process_batch(client,cs[0],cfg))
            self.assertEqual([j['claim']['id'] for j in pub.call_args.args[1]],[cs[0]['id'],cs[2]['id']])
            self.assertEqual({sid for sid,_ in client.acks},{cs[0]['id'],cs[2]['id']})
    def test_known_duplicate_finishes_even_when_new_peer_fails_publish(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=self.config(root);cs=[claim(wdb(1)),claim(wdb(2))];client=Client(cs[1:],{cs[0]['id']:wdb(1),cs[1]['id']:wdb(2)})
            key=bridge.bundle_key(cs[0]['manifest'],bridge.processing_revision(cfg))
            with patch.object(bridge,'verified_bundles',return_value={key:'a'*40}),patch.object(bridge,'run_publisher',side_effect=RuntimeError('audit failed')):
                self.assertFalse(bridge.process_batch(client,cs[0],cfg))
            self.assertEqual(dict(client.acks)[cs[0]['id']]['status'],'published')
            self.assertEqual(dict(client.acks)[cs[1]['id']]['status'],'received')
    def test_corrupt_or_commit_unbound_index_cannot_skip(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);cfg=self.config(root);root.joinpath('private').mkdir();index=root/'private'/'published-bundles.json';index.write_text('broken')
            self.assertEqual(bridge.verified_bundles(cfg,['x']),{})
            index.write_text(json.dumps({'schema':1,'bundles':{'x':'a'*40}}))
            with patch.object(bridge,'published_head',return_value='a'*40),patch.object(bridge.subprocess,'run') as run,patch.object(bridge,'commit_has_bundle',return_value=False):
                run.return_value.returncode=0;self.assertEqual(bridge.verified_bundles(cfg,['x']),{})
if __name__=='__main__':unittest.main()
