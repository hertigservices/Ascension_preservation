import gzip
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
import baseline
import intake
import publish
import watch


class WorkflowTests(unittest.TestCase):
    def setUp(self):
        self.t=tempfile.TemporaryDirectory();self.base=Path(self.t.name);self.root=self.base/'private';self.donor=self.base/'donor';self.donor.mkdir()
    def tearDown(self):self.t.cleanup()
    def git(self,repo,*args):
        return subprocess.check_output(['git','-C',str(repo),*args],stderr=subprocess.STDOUT).decode().strip()
    def repo(self):
        p=self.base/'data';p.mkdir();self.git(p,'init');self.git(p,'config','user.name','Fixture');self.git(p,'config','user.email','fixture@example.invalid')
        (p/'.ascension-data.json').write_text('{}');self.git(p,'add','.');self.git(p,'commit','-m','fixture');return p
    def test_quiet_watch_and_no_reparse(self):
        folder=self.root/'inbox'/'test';folder.mkdir(parents=True);(folder/'a.json').write_text('{"id":1}')
        self.assertEqual(watch.sweep(self.root,0),[])
        self.assertEqual(watch.sweep(self.root,0)[0]['status'],'preserved')
        self.assertEqual(watch.sweep(self.root,0),[])
        (folder/'a.json').write_text('{"id":2}')
        self.assertEqual(watch.sweep(self.root,0),[]);watch.sweep(self.root,0)
        with intake.Intake(self.root,reserve=0) as e:self.assertEqual(e.summary()['documents'],2)
    def test_baseline_exact_match_and_incremental_index(self):
        repo=self.repo();p=repo/'npcs.jsonl.gz'
        with gzip.open(p,'wt') as f:f.write('{"id":1,"name":"One"}\n')
        self.git(repo,'add','.');self.git(repo,'commit','-m','records')
        p=self.donor/'rows.jsonl';p.write_text('{"id":1,"name":"One"}\n{"id":1,"name":"Variant"}\n')
        with intake.Intake(self.root,reserve=0) as e:
            e.ingest(p,'test');baseline.index(e,repo);report=baseline.compare(e,'test')
            self.assertEqual(report['exact_payloads_already_published'],1);self.assertEqual(report['not_exactly_matched'],1)
            self.assertEqual(baseline.index(e,repo)['reused_files'],1)
    def test_export_delivery_full_browser_build(self):
        repo=self.repo();worktree=self.base/'publication';self.git(repo,'worktree','add','-b','research',str(worktree))
        gd=Path(self.git(worktree,'rev-parse','--absolute-git-dir'));intake.atomic_json(gd/'research-publisher.json',{'schema':1})
        p=self.donor/'rows.jsonl';p.write_text('{"id":"9007199254740993123","name":"Restored NPC","map":1,"x":3,"y":4}\n')
        policy=self.base/'policy.json';policy.write_text(json.dumps({'source':'test','title':'Test research','publication':'approved','permission':'Synthetic test fixture','collections':{'records':{'kind':'npc','fields':{'id':'id','name':'name','map':'map','x':'x','y':'y'}}}}))
        with intake.Intake(self.root,reserve=0) as e:
            e.ingest(p,'test');snapshot=intake.export(e,'test',policy,self.base/'staging')
        result=publish.deliver(snapshot,worktree);self.assertFalse(result['pushed']);self.assertEqual(self.git(worktree,'status','--porcelain'),'')
        self.assertEqual(publish.deliver(snapshot,worktree)['commit'],result['commit'])
        browser=Path(__file__).resolve().parent.parent/'ascension-db';dist=self.base/'site'
        r=subprocess.run([sys.executable,'-B',str(browser/'build.py'),'--data',str(worktree),'--out',str(dist),'--cache',str(self.base/'cache')],cwd=browser,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr+'\n'+r.stdout[-2000:])
        r=subprocess.run([sys.executable,'-B',str(browser/'validate.py'),str(dist)],cwd=browser,capture_output=True,text=True)
        self.assertEqual(r.returncode,0,r.stderr+'\n'+r.stdout[-2000:])
        manifest=json.loads((dist/'manifest.json').read_text())
        self.assertEqual(manifest['kinds']['npc']['records'],1)
        self.assertIn('Test research',manifest['sources'])
        record_file=next(v for v in manifest['files'].values() if '/research-intake/' in v.get('path',''))
        self.assertEqual(record_file['records'],1)
    def test_public_budget_and_private_raw_never_delivered(self):
        p=self.donor/'rows.json';p.write_text('[{"id":1},{"id":2}]')
        policy=self.base/'policy.json';policy.write_text(json.dumps({'source':'test','publication':'approved','permission':'Fixture','max_records':1,'collections':{'records':{'kind':'npc','fields':{'id':'id'}}}}))
        with intake.Intake(self.root,reserve=0) as e:
            e.ingest(p,'test')
            with self.assertRaises(ValueError):intake.export(e,'test',policy,self.base/'public')
        self.assertEqual(list((self.root/'exports').iterdir()),[])

if __name__=='__main__':unittest.main()
