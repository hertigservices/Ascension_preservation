import hashlib, importlib.util, json, subprocess, tempfile, unittest, uuid
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/"cache-consolidator"/"tools"))
from unittest.mock import patch
from test_batching import bridge, claim, wdb, Client
MODULE=Path(__file__).resolve().parents[2]/'cache-consolidator'/'tools'/'batch_inputs.py'
spec=importlib.util.spec_from_file_location('batch_inputs',MODULE);batch_inputs=importlib.util.module_from_spec(spec);spec.loader.exec_module(batch_inputs)


def fixture_incorporation(rows, work, out):
    # Synthetic pipeline evidence for isolated delivery and real-Git tests.
    import intake
    work=Path(work);out=Path(out);(work/'merged').mkdir(parents=True,exist_ok=True);out.mkdir(parents=True,exist_ok=True)
    ledger={};sources=['id\tsha256\tgroup\trecords\n']
    for row in rows:
        for rel,sha in row['files'].items():
            ledger[sha]={'standard':True,'clean_end':True,'records':1}
            sources.append(str(len(sources))+'\t'+sha+'\t'+intake.group_of(str(row['target']/rel))+'\t1\n')
    (work/'ledger.json').write_text(json.dumps(ledger),encoding='utf-8')
    for path in (work/'merged'/'sources.tsv',out/'sources.tsv'):path.write_text(''.join(sources),encoding='utf-8')

class DeliveryTests(unittest.TestCase):
    def plan(self,root):
        job=root/'private'/str(uuid.uuid4());accepted=job/('validated-'+uuid.uuid4().hex)/'accepted';accepted.mkdir(parents=True)
        (accepted/'part-0000').mkdir();(accepted/'part-0000'/'itemcache.wdb').write_bytes(wdb())
        cfg={'publisher':str(MODULE.parent/'publish.py')}
        row={'wdb_only':True,'bundle':'a'*64,'revision':bridge.processing_revision(cfg),'source':str(accepted),'destination':'web-'+'c'*32,'files':batch_inputs.inventory(accepted)}
        plan=job/'publication-plan.json';plan.write_text(json.dumps({'schema':1,'rules':bridge.processing_rules({'publisher':str(MODULE.parent/'publish.py')}),'jobs':[row]}));return plan,row
    def test_delivery_retry_and_commit_evidence_preserve_exact_bytes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,row=self.plan(root);inbox=root/'inbox'
            checked=batch_inputs.prepare(plan,inbox);self.assertTrue(Path(row['source']).exists())
            batch_inputs.prepare(plan,inbox);fixture_incorporation(checked,root,root/'out');batch_inputs.record(checked,root/'out')
            proof=json.loads((root/'out'/'contributions'/('a'*64+'.json')).read_text())
            self.assertEqual(set(proof),{'schema','bundle','revision','no_op_eligible'})
            self.assertEqual(batch_inputs.inventory(checked[0]['target']),row['files'])
    def test_missing_or_changed_exported_wdb_blocks_publication_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,row=self.plan(root);rows=batch_inputs.prepare(plan,root/'inbox')
            fixture_incorporation(rows,root,root/'out')
            (root/'out'/'sources.tsv').write_text('id\tsha256\tgroup\trecords\n',encoding='utf-8')
            with self.assertRaises(ValueError):batch_inputs.record(rows,root/'out',root)
            self.assertFalse((root/'out'/'contributions').exists())
    def test_known_wdb_and_lua_cannot_establish_noop_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,row=self.plan(root);fixture_incorporation([],root,root/'out');rows=batch_inputs.prepare(plan,root/'inbox')
            self.assertTrue(rows[0]['no_op_eligible'])
            fixture_incorporation(rows,root,root/'out')
            rows=batch_inputs.prepare(plan,root/'inbox')
            self.assertFalse(rows[0]['no_op_eligible'])
            batch_inputs.record(rows,root/'out',root)
            proof=json.loads((root/'out'/'contributions'/('a'*64+'.json')).read_text())
            self.assertFalse(proof['no_op_eligible'])
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,row=self.plan(root);source=Path(row['source']);file=next(source.rglob('*.wdb'));file.rename(file.with_suffix('.lua'));row['files']=batch_inputs.inventory(source);plan.write_text(json.dumps({'schema':1,'rules':bridge.processing_rules({'publisher':str(MODULE.parent/'publish.py')}),'jobs':[row]}))
            fixture_incorporation([],root,root/'out');rows=batch_inputs.prepare(plan,root/'inbox');self.assertFalse(rows[0]['no_op_eligible'])
    def test_stale_rules_and_missing_ledger_disable_noop(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,row=self.plan(root);fixture_incorporation([],root,root/'out')
            data=json.loads(plan.read_text());data['rules'][0]['sha256']='0'*64;plan.write_text(json.dumps(data))
            rows=batch_inputs.prepare(plan,root/'inbox');self.assertFalse(rows[0]['no_op_eligible'])
            (root/'ledger.json').unlink()
            rows=batch_inputs.prepare(plan,root/'inbox');self.assertFalse(rows[0]['no_op_eligible'])
    def test_known_merged_hash_disables_noop_even_if_ledger_lost_entry(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,row=self.plan(root);fixture_incorporation([],root,root/'out')
            rows=batch_inputs.prepare(plan,root/'inbox');fixture_incorporation(rows,root,root/'out');(root/'ledger.json').write_text('{}')
            rows=batch_inputs.prepare(plan,root/'inbox');self.assertFalse(rows[0]['no_op_eligible'])
    def test_extra_file_blocks_retry_and_late_change_blocks_proof(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,row=self.plan(root);checked=batch_inputs.prepare(plan,root/'inbox');extra=checked[0]['target']/'extra.lua';extra.write_text('not part of validated input')
            with self.assertRaises(ValueError):batch_inputs.prepare(plan,root/'inbox')
            with self.assertRaises(ValueError):batch_inputs.record(checked,root/'out')
            self.assertFalse((root/'out').exists())
    def test_invalid_peer_prevents_any_delivery(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,row=self.plan(root);data=json.loads(plan.read_text());data['jobs'].append(dict(row,destination='../escape'));plan.write_text(json.dumps(data))
            with self.assertRaises(ValueError):batch_inputs.prepare(plan,root/'inbox')
            self.assertTrue(Path(row['source']).exists());self.assertFalse((root/'inbox').exists())
    def test_second_copy_failure_exposes_no_partial_batch(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,row=self.plan(root);_,second=self.plan(root)
            second['bundle']='d'*64;second['destination']='web-'+'e'*32
            plan.write_text(json.dumps({'schema':1,'jobs':[row,second]}))
            original=batch_inputs.shutil.copytree;calls=[]
            def copy(source,dest,*args,**kwargs):
                if Path(source).name=='accepted':
                    calls.append(source)
                    if len(calls)==2:raise OSError('injected copy failure')
                return original(source,dest,*args,**kwargs)
            with patch.object(batch_inputs.shutil,'copytree',side_effect=copy):
                with self.assertRaises(OSError):batch_inputs.prepare(plan,root/'inbox')
            self.assertFalse((root/'inbox').exists())
            self.assertTrue(Path(row['source']).exists());self.assertTrue(Path(second['source']).exists())
            self.assertFalse(list((root/'private').glob('.batch-*')))
    def test_real_publisher_gate_blocks_proof_until_pipeline_succeeds(self):
        import sys
        sys.path.insert(0,str(MODULE.parent))
        import publish, intake
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);plan,row=self.plan(root)
            with patch.dict(publish.os.environ,{'ASCENSION_BATCH_PLAN':str(plan)}),patch.object(publish,'LOCK',str(root/'publish.lock')),patch.object(publish.config,'INBOX',str(root/'inbox')),patch.object(publish.config,'OUT',str(root/'out')),patch.object(publish,'status_paths',return_value=[]),patch.object(publish,'nul',return_value=[]),patch.object(publish,'consolidate',return_value=False):
                self.assertFalse(publish.publish(False))
            self.assertFalse((root/'out'/'contributions').exists());self.assertFalse((root/'publish.lock').exists())
            checked=batch_inputs.prepare(plan,root/'inbox')
            with self.assertRaises(FileNotFoundError):batch_inputs.record(checked,root/'out',root)
            self.assertFalse((root/'out'/'contributions').exists())
            fixture_incorporation(checked,root,root/'out')
            with patch.dict(publish.os.environ,{'ASCENSION_BATCH_PLAN':str(plan)}),patch.object(publish,'LOCK',str(root/'publish.lock')),patch.object(publish.config,'WORK',str(root)),patch.object(publish.config,'INBOX',str(root/'inbox')),patch.object(publish.config,'OUT',str(root/'out')),patch.object(publish,'status_paths',return_value=[]),patch.object(publish,'nul',return_value=[]),patch.object(publish,'consolidate',return_value=True),patch.object(publish,'sync_tools',return_value=[]),patch.object(publish,'sync_data',return_value=0),patch.object(publish,'audit',return_value=True):
                self.assertTrue(publish.publish(False))
            self.assertTrue((root/'out'/'contributions'/('a'*64+'.json')).exists())
            logical='web-'+'c'*32
            self.assertEqual(intake.submission_segment('archive/2026-09/upload-batch-'+'d'*32+'/'+logical+'/enUS/itemcache.wdb'),logical)
    def test_real_local_git_batch_then_verified_duplicate_without_second_commit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);repo=root/'repo';bare=root/'remote.git'
            def git(*args,cwd=None):return subprocess.check_output(['git',*args],cwd=cwd,stderr=subprocess.DEVNULL,text=True).strip()
            git('init','--bare',str(bare));git('init','-b','main',str(repo));git('config','user.name','Batch Test',cwd=repo);git('config','user.email','batch@example.invalid',cwd=repo);git('remote','add','origin',str(bare),cwd=repo)
            pub=root/'publisher.py'
            pub.write_text('import os,sys,subprocess\nfrom pathlib import Path\nsys.path.insert(0,TOOLS)\nsys.path.insert(0,TESTS)\nimport batch_inputs\nfrom test_batch_delivery import fixture_incorporation\nrows=batch_inputs.prepare(os.environ["ASCENSION_BATCH_PLAN"],Path(os.environ["ASCENSION_CACHE_WORK"])/"_inbox")\nfixture_incorporation(rows,os.environ["ASCENSION_CACHE_WORK"],os.environ["ASCENSION_CACHE_OUT"])\nbatch_inputs.record(rows,os.environ["ASCENSION_CACHE_OUT"])\nrepo=os.environ["CONSOLIDATOR_REPO"]\nfor args in [("add","cachedata"),("commit","-m","Test batch"),("push","origin","main")]:subprocess.run(["git","-C",repo,*args],check=True)\n'.replace("TOOLS",repr(str(MODULE.parent))).replace("TESTS",repr(str(Path(__file__).parent))),encoding="utf-8")
            cfg={'state':str(root/'private'),'inbox':str(root/'work'/'_inbox'),'work':str(root/'work'),'out':str(repo/'cachedata'),'publish_repo':str(repo),'publisher':str(pub),'node':'node'}
            fixture_incorporation([],root/'work',repo/'cachedata')
            cs=[claim(wdb(1)),claim(wdb(2))];client=Client(cs[1:],{cs[0]['id']:wdb(1),cs[1]['id']:wdb(2)})
            original_rules=bridge.processing_rules
            def real_rules(config):return original_rules({**config,'publisher':str(MODULE.parent/'publish.py')})
            with patch.object(bridge,'processing_rules',side_effect=real_rules):
                self.assertTrue(bridge.process_batch(client,cs[0],cfg))
            head=git('rev-parse','HEAD',cwd=repo)
            self.assertEqual(len(list((repo/'cachedata'/'contributions').glob('*.json'))),2)
            self.assertEqual({r['commit'] for _,r in client.acks},{head})
            repeat=claim(wdb(1));client=Client([],{repeat['id']:wdb(1)})
            with patch.object(bridge,'run_publisher') as publish,patch.object(bridge,'processing_rules',side_effect=real_rules):
                result=bridge.process_one(client,repeat,cfg)
            publish.assert_not_called();self.assertTrue(result['duplicate']);self.assertEqual(result['commit'],head)
            self.assertEqual(git('rev-list','--count','HEAD',cwd=repo),'1')
if __name__=='__main__':unittest.main()
