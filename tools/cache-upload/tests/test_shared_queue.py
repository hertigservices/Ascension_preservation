import json,sys,tempfile,unittest,uuid
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]/'cache-consolidator'/'tools'))
import intake_jobs as jobs

class SharedQueueTests(unittest.TestCase):
    def test_only_successfully_completed_unchanged_roots_are_filed(self):
        with tempfile.TemporaryDirectory() as d:
            work=Path(d);inbox=work/'_inbox';inbox.mkdir();good=inbox/'good';good.mkdir();(good/'x.wdb').write_bytes(b'one');late=inbox/'late';late.mkdir();(late/'x.wdb').write_bytes(b'two')
            before=jobs.snapshot(inbox)
            self.assertEqual(jobs.archive_completed(work),[])
            jobs.file_completed(work,before,'a'*40)
            (late/'new.wdb').write_bytes(b'late');(inbox/'new.wdb').write_bytes(b'new')
            self.assertEqual(jobs.archive_completed(work),['good'])
            self.assertTrue(late.exists());self.assertTrue((inbox/'new.wdb').exists());self.assertEqual(jobs.archive_completed(work),[])
            self.assertNotIn('archive',jobs.snapshot(inbox))
    def test_same_size_changed_bytes_and_archive_collision_are_retained(self):
        with tempfile.TemporaryDirectory() as d:
            work=Path(d);inbox=work/'_inbox';inbox.mkdir();p=inbox/'x.wdb';p.write_bytes(b'one');before=jobs.snapshot(inbox);jobs.file_completed(work,before,'a'*40);p.write_bytes(b'two')
            self.assertEqual(jobs.archive_completed(work),[]);self.assertEqual(p.read_bytes(),b'two')
            p.write_bytes(b'one');dest=inbox/'archive'/jobs.time.strftime('%Y-%m');dest.mkdir(parents=True);(dest/'x.wdb').write_bytes(b'old')
            self.assertEqual(jobs.archive_completed(work),[]);self.assertEqual(p.read_bytes(),b'one');self.assertEqual((dest/'x.wdb').read_bytes(),b'old')
    def test_durable_requests_order_retry_busy_and_failure(self):
        with tempfile.TemporaryDirectory() as d:
            work=Path(d);a=jobs.enqueue(work);b=jobs.enqueue(work,push=False)
            cfg={'work':d,'out':str(work/'out'),'publish_repo':str(work/'repo'),'publisher':str(work/'publish.py')}
            with patch.object(jobs.subprocess,'run',return_value=type('Result',(),{'returncode':75})()):jobs.process_manual(cfg)
            self.assertEqual(jobs.read(a)['status'],'queued');self.assertEqual(jobs.read(b)['status'],'queued')
            item=jobs.read(a);item.update(next_attempt=0,attempts=4);jobs.write(a,item)
            with patch.object(jobs.subprocess,'run',return_value=type('Result',(),{'returncode':1})()):jobs.process_manual(cfg)
            self.assertEqual(jobs.read(a)['status'],'failed')
            with patch.object(jobs.subprocess,'run',return_value=type('Result',(),{'returncode':0})()) as run:jobs.process_manual(cfg)
            self.assertEqual(jobs.read(b)['status'],'done');self.assertNotIn('--push',run.call_args.args[0])
    def test_status_view_supports_legacy_results_batch_leader_and_manual_queue(self):
        import contribution_view
        with tempfile.TemporaryDirectory() as d:
            work=Path(d);state=work/'private';sid=str(uuid.uuid4());leader=str(uuid.uuid4())
            jobs.write(work/'shared-collector.json',{'state':str(state),'enabled':True})
            jobs.write(state/'stream-status.json',{'checked':jobs.time.time(),'submissions':[{'id':sid,'status':'processing','bytes':2048,'created':1,'modes':['free-pick']}]})
            jobs.write(state/sid/'batch.json',{'publisher_submission':leader});(state/leader).mkdir();(state/leader/'publisher.log').write_text('== consolidating\npushing main to origin')
            rows,snapshot=contribution_view.rows(work);self.assertEqual(rows[0]['stage'],'Pushing to GitHub');self.assertEqual(rows[0]['id'],sid)
            (state/'stream-status.json').unlink();jobs.write(state/sid/'result.json',{'status':'published','commit':'a'*40})
            rows,_=contribution_view.rows(work);self.assertEqual(rows[0]['status'],'published')
    def test_only_incorporated_supported_content_qualifies(self):
        import intake,hashlib
        with tempfile.TemporaryDirectory() as d:
            work=Path(d);inbox=work/'_inbox';inbox.mkdir();out=work/'out';out.mkdir();(work/'merged').mkdir()
            p=inbox/'itemcache.wdb';p.write_bytes(b'good');(inbox/'bad.zip').write_bytes(b'corrupt');(inbox/'notes.txt').write_text('unsupported')
            sha=hashlib.sha256(p.read_bytes()).hexdigest();jobs.write(work/'ledger.json',{sha:{'standard':True,'clean_end':True,'records':1}})
            source='id\tsha256\tgroup\trecords\n1\t'+sha+'\t'+intake.group_of(str(p))+'\t1\n'
            for path in (work/'merged'/'sources.tsv',out/'sources.tsv'):path.write_text(source,encoding='utf-8')
            self.assertEqual(set(jobs.eligible_completed(work,out,jobs.snapshot(inbox))),{'itemcache.wdb'})
    def test_zero_record_lua_is_retained(self):
        import hashlib
        with tempfile.TemporaryDirectory() as d:
            work=Path(d);inbox=work/'_inbox';inbox.mkdir();p=inbox/'MobSpells.lua';p.write_text('MobSpellsDB = {}');sha=hashlib.sha256(p.read_bytes()).hexdigest()
            jobs.write(work/'merged'/'lua'/'state.json',{'sources':{sha:{'spec':'mobspells.lua','records':0}}})
            self.assertEqual(jobs.eligible_completed(work,work/'out',jobs.snapshot(inbox)),{})
    def test_successfully_merged_zero_record_lua_can_be_archived(self):
        import luamerge,hashlib
        for key in ('auctionator_price_database.lua','gathermate2.lua'):
            with self.subTest(key=key), tempfile.TemporaryDirectory() as d:
                work=Path(d);inbox=work/'_inbox';inbox.mkdir();p=inbox/key
                p.write_text(luamerge.SPECS[key]['globals'][0]+' = {}')
                state_path=work/'merged/lua/state.json';state_path.parent.mkdir(parents=True)
                with patch.object(luamerge,'STATE',str(state_path)), patch.object(luamerge,'STORE',str(state_path.parent)), patch.object(luamerge,'discover',return_value=[(key,str(p))]):
                    luamerge.run_merge()
                sha=hashlib.sha256(p.read_bytes()).hexdigest()
                state=jobs.read(state_path);source=state['sources'][sha]
                self.assertEqual(source['records'],0)
                before=jobs.snapshot(inbox)
                self.assertEqual(jobs.eligible_completed(work,work/'out',before),before)
                for changes in ({'error':'parse failed'},{'empty':True},{'records':-1},{'records':False},{'spec':'wrong.lua'}):
                    state['sources'][sha]={**source,**changes};jobs.write(state_path,state)
                    self.assertEqual(jobs.eligible_completed(work,work/'out',before),{})
                state['sources'][sha]=source;jobs.write(state_path,state)
                jobs.file_completed(work,before,'a'*40)
                self.assertEqual(jobs.archive_completed(work),[key])
                self.assertEqual(jobs.tree(inbox/'archive'/jobs.time.strftime('%Y-%m')/key),before[key])

    def test_unstable_inbox_never_starts_consolidation(self):
        import publish
        with patch.object(publish,'status_paths',return_value=[]),patch.object(publish,'nul',return_value=[]),patch.object(publish.time,'sleep'),patch.object(jobs,'snapshot',side_effect=[{'x':{'f':'one'}},{'x':{'f':'two'}}]),patch.object(publish,'consolidate') as consolidate,patch.dict(publish.os.environ,{'ASCENSION_MANUAL_REQUEST':'','ASCENSION_BATCH_PLAN':''}):
            self.assertIsNone(publish._publish(True));consolidate.assert_not_called()
    def test_stale_collector_leaves_durable_request_without_blocking_gui(self):
        import tray_app
        with tempfile.TemporaryDirectory() as d:
            jobs.write(Path(d)/'shared-collector.json',{'enabled':True,'heartbeat':0})
            with patch.object(tray_app.config,'WORK',d):
                runner=tray_app.Runner(lambda _:None,lambda:None)
                self.assertEqual(runner.run(True,'manual'),0)
                self.assertIn('queued',runner.last_result)
                self.assertEqual(jobs.read(next((Path(d)/'manual-queue').glob('*.json')))['status'],'queued')
    def test_live_view_keeps_newest_first_across_refresh_and_restart(self):
        import tkinter as tk
        import contribution_view
        with tempfile.TemporaryDirectory() as d:
            root=tk.Tk();root.withdraw()
            try:
                work=Path(d);state=work/'private'
                jobs.write(work/'shared-collector.json',{'state':str(state)})
                older={'id':str(uuid.uuid4()),'created':100,'status':'published'}
                newer={'id':str(uuid.uuid4()),'created':200,'status':'published'}
                jobs.write(state/'stream-status.json',{'submissions':[older,newer]})
                view=contribution_view.ContributionsView(root,work,lambda _:None)
                view.table.selection_set(older['id'])
                request=jobs.enqueue(work)
                expected=('manual-'+request.stem,newer['id'],older['id'])
                view.refresh()
                self.assertEqual(view.table.get_children(),expected)
                self.assertEqual(view.table.selection(),(older['id'],))
                item=jobs.read(request);item['status']='processing';jobs.write(request,item)
                view.refresh()
                self.assertEqual(view.table.get_children(),expected)
                self.assertEqual(view.table.set(expected[0],'status'),'processing')
                restarted=contribution_view.ContributionsView(root,work,lambda _:None)
                self.assertEqual(restarted.table.get_children(),expected)
            finally:root.destroy()
    def test_tk_live_view_builds_and_refreshes(self):
        import tkinter as tk
        from contribution_view import ContributionsView
        with tempfile.TemporaryDirectory() as d:
            root=tk.Tk();root.withdraw()
            try:
                view=ContributionsView(root,d,lambda _:None);root.update_idletasks();self.assertEqual(len(view.table['columns']),6)
            finally:root.destroy()
if __name__=='__main__':unittest.main()
