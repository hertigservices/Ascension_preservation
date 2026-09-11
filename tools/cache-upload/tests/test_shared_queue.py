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

    def test_mixed_bundle_files_only_with_explicit_retained_evidence(self):
        import hashlib,intake,zipfile
        for standard in (False,True):
            with self.subTest(standard=standard), tempfile.TemporaryDirectory() as d:
                work=Path(d);inbox=work/'_inbox';inbox.mkdir();out=work/'out';out.mkdir()
                extracted=work/'extracted'/'mixed';extracted.mkdir(parents=True)
                archive=inbox/'mixed.zip'
                with zipfile.ZipFile(archive,'w') as z:
                    z.writestr('itemcache.wdb',b'valid');z.writestr('other.wdb',b'undecoded')
                (extracted/'itemcache.wdb').write_bytes(b'valid');(extracted/'other.wdb').write_bytes(b'undecoded')
                sha=hashlib.sha256(archive.read_bytes()).hexdigest();(extracted/'.intake-sha256').write_text(sha)
                ledger={};source=['id\tsha256\tgroup\trecords']
                for i,name in enumerate(('itemcache.wdb','other.wdb')):
                    p=extracted/name;h=hashlib.sha256(p.read_bytes()).hexdigest()
                    ledger[h]={'sha256':h,'standard':True if i==0 else standard,'clean_end':i==0,'records':1 if i==0 else 0,'note':'' if i==0 else 'unread'}
                    source.append(f'{i}\t{h}\t{intake.group_of(str(p))}\t{1 if i==0 else 0}')
                jobs.write(work/'ledger.json',ledger);(work/'merged').mkdir()
                for p in (work/'merged/sources.tsv',out/'sources.tsv'):p.write_text('\n'.join(source)+'\n',encoding='utf-8')
                before=jobs.snapshot(inbox);retained={}
                self.assertEqual(jobs.eligible_completed(work,out,before),{})
                self.assertEqual(jobs.eligible_completed(work,out,before,retained),before)
                self.assertEqual(len(retained['mixed.zip']),1)
                self.assertIn('other.wdb',retained['mixed.zip'][0]['file'])
                original=archive.read_bytes();jobs.file_completed(work,before,'a'*40,retained)
                self.assertEqual(jobs.archive_completed(work),['mixed.zip'])
                receipt=jobs.read(work/'completed-inputs.json')['roots']['mixed.zip']
                self.assertEqual((inbox/receipt['archived']).read_bytes(),original)
                self.assertEqual(receipt['retained_unparsed'],retained['mixed.zip'])
                self.assertIn('other.wdb',(inbox/'archive/RETAINED-FILES.md').read_text(encoding='utf-8'))
                jobs.archive_completed(work)
                self.assertIn('other.wdb',(inbox/'archive/RETAINED-FILES.md').read_text(encoding='utf-8'))

    def test_unread_only_or_unaccounted_inputs_still_stay_pending(self):
        import hashlib,intake
        with tempfile.TemporaryDirectory() as d:
            work=Path(d);inbox=work/'_inbox';inbox.mkdir();out=work/'out';out.mkdir();(work/'merged').mkdir()
            bad=inbox/'other.wdb';bad.write_bytes(b'undecoded');sha=hashlib.sha256(bad.read_bytes()).hexdigest()
            jobs.write(work/'ledger.json',{sha:{'sha256':sha,'standard':False,'clean_end':False,'records':0,'note':'unread'}})
            text=f'id\tsha256\tgroup\trecords\n1\t{sha}\t{intake.group_of(str(bad))}\t0\n'
            for p in (work/'merged/sources.tsv',out/'sources.tsv'):p.write_text(text,encoding='utf-8')
            retained={};self.assertEqual(jobs.eligible_completed(work,out,jobs.snapshot(inbox),retained),{})
            self.assertEqual(retained,{})
            # A good peer must not make an unledgered or unexported file eligible.
            root=inbox/'mixed';root.mkdir();bad.rename(root/'other.wdb')
            good=root/'itemcache.wdb';good.write_bytes(b'good');h=hashlib.sha256(good.read_bytes()).hexdigest()
            ledger=jobs.read(work/'ledger.json');ledger[h]={'sha256':h,'standard':True,'clean_end':True,'records':1};jobs.write(work/'ledger.json',ledger)
            text+=f'2\t{h}\t{intake.group_of(str(good))}\t1\n'
            for p in (work/'merged/sources.tsv',out/'sources.tsv'):p.write_text(text,encoding='utf-8')
            # The moved unread file has no matching group provenance.
            self.assertEqual(jobs.eligible_completed(work,out,jobs.snapshot(inbox),{}),{})

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
