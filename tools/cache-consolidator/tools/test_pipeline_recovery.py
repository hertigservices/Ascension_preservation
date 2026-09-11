"""Regression evidence for the September 2026 independent pipeline review."""
import tempfile, unittest, json, os, hashlib, struct
from pathlib import Path
from unittest.mock import patch
import luaser, merge, merge_checkpoint, export, rebuild, audit_columns, luamerge

class RecoveryTests(unittest.TestCase):
    def test_lua_decimal_bytes_and_mixed_text(self):
        for body, expected in [(r'\195\169','é'), (r'café \226\128\153','café ’'),
                               (r'\xC3\xA9','é'), (r'a\010b','a\nb')]:
            self.assertEqual(luaser._unescape(body), expected)
        for bad in [r'\999',r'\255',r'\xGG',r'\x']:
            with self.assertRaises(luaser.LuaError): luaser._unescape(bad)

    def test_serializer_and_load_encoding(self):
        value = {'X': luaser.table_from({'name': 'café ’'})}
        text = luaser.dumps(value)
        self.assertEqual(luaser.loads(text)['X'].get('name'), 'café ’')
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.lua';p.write_bytes(b'X="bad' + bytes([255]) + b'"')
            with self.assertRaises(UnicodeDecodeError): luaser.load(p)

    def test_partial_metadata_commit_replays_complete_generation(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            for n in ['sources.tsv','itemcache/index.tsv']:
                merge.write_tsv(str(root/n),['value'],[{'value':'old'}])
            original=os.replace
            def interrupted(src,dst):
                if Path(dst)==root/'sources.tsv': raise OSError('simulated power loss')
                return original(src,dst)
            tables=[(str(root/'itemcache/index.tsv'),['value'],[{'value':'new'}]),
                    (str(root/'sources.tsv'),['value'],[{'value':'new'}])]
            with patch.object(merge_checkpoint.os,'replace',interrupted):
                with self.assertRaises(OSError): merge_checkpoint.commit(root,tables,merge.write_tsv)
            self.assertTrue((root/'.merge-transaction/ready.json').exists())
            merge_checkpoint.recover(root)
            self.assertEqual(merge.read_tsv(str(root/'sources.tsv'),[])[0]['value'],'new')
            self.assertEqual(merge.read_tsv(str(root/'itemcache/index.tsv'),[])[0]['value'],'new')
            self.assertFalse((root/'.merge-transaction').exists())

    def test_corrupt_prepared_checkpoint_does_not_partially_install(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);txn=root/'.merge-transaction';txn.mkdir()
            (root/'sources.tsv').write_text('unchanged')
            (txn/'0.tsv').write_text('damaged')
            (txn/'ready.json').write_text(json.dumps([dict(path='sources.tsv',file='0.tsv',sha256='0'*64)]))
            with self.assertRaises(RuntimeError): merge_checkpoint.recover(root)
            self.assertEqual((root/'sources.tsv').read_text(),'unchanged')

    def test_archive_header_is_durable_and_locale_specific(self):
        h=b'BDIW'+struct.pack('<I',12340)+b'SUne'+bytes(12)
        s=dict(cache='itemcache',records='1',slug='free-pick',captured='2026',path='does-not-exist',header_hex=h.hex())
        self.assertEqual(rebuild.header_for('itemcache','free-pick',[s],'enUS')[0],h)
        self.assertIsNone(rebuild.header_for('itemcache','free-pick',[s],'deDE')[0])

    def test_locales_do_not_borrow_a_different_modes_source(self):
        sources={'1':dict(id='1',locale='enUS',slug='free-pick',captured='2020'),
                 '2':dict(id='2',locale='deDE',slug='conquest-of-azeroth',captured='2026')}
        row=dict(srcs='1,2',modes='free-pick,conquest-of-azeroth',locales='deDE,enUS',last_captured='2026',_source_rows=sources)
        recs=[(1,'abc',row,b'x')]
        self.assertFalse(export.pick_winners(recs,mode='conquest-of-azeroth',locale='enUS'))
        selected=export.pick_winners(recs,mode='free-pick',locale='enUS')[1][1]
        self.assertEqual(selected['modes'],'free-pick')
        self.assertEqual(selected['last_captured'],'2020')

    def test_invalid_header_locale_cannot_break_metadata_rows(self):
        self.assertEqual(merge.clean_locale("\n\tXX"), "unknown")
        self.assertEqual(merge.clean_locale("deDE"), "deDE")
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(ValueError):
                merge.write_tsv(str(Path(d)/"source.tsv"),["x"],[{"x":"a\nb"}])

    def test_malformed_existing_metadata_fails_at_load(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"sources.tsv"
            p.write_text("id\tslug\n1\n",encoding="utf-8")
            with self.assertRaises(ValueError): merge.read_tsv(str(p),[])

    def test_empty_auctionator_metadata_is_distinct_from_unknown_data(self):
        for body,expected in [('AUCTIONATOR_PRICE_DATABASE={__dbversion=2}', 'empty_supported'),
                              ('AUCTIONATOR_PRICE_DATABASE={__dbversion=2,["Realm"]={}}', 'empty_supported'),
                              ('AUCTIONATOR_PRICE_DATABASE={__dbversion=2,["Realm"]={["item"]={unrecognised=1}}}', 'needs_review')]:
            with tempfile.TemporaryDirectory() as d:
                p=Path(d)/"Auctionator_Price_Database.lua";p.write_text(body,encoding="utf-8")
                with patch.object(luamerge,"load_state",return_value={"sources":{}}), patch.object(luamerge,"save_state"), patch.object(luamerge,"backfill_submissions",return_value=0), patch.object(luamerge,"discover",return_value=[("auctionator_price_database.lua",str(p))]):
                    state=luamerge.run_merge()
                self.assertEqual(state["sources"][luamerge.sha256(str(p))]["status"],expected)

    def test_rich_auctionator_preserves_observations_and_rejects_unknown_fields(self):
        state={};meta={"captured":"2026-09-11"}
        def capture(price):
            return luaser.loads('AUCTIONATOR_PRICE_DATABASE={["Area 52 - Free-Pick"]={["Test Item"]={id="123",mr='+str(price)+',cc=2,sc=1,lastScan=1234,H5000=200,L5000=100}}}')
        self.assertEqual(luamerge.merge_auctionator(state,capture(150),"a",meta,[]),1)
        self.assertEqual(luamerge.merge_auctionator(state,capture(175),"b",meta,[]),1)
        variants=state["auctionator_observations"]["free-pick"]["Test Item"]
        self.assertEqual(len(variants),2)
        self.assertEqual(state["auctionator"]["free-pick"]["items"]["Test Item"][0],175)
        self.assertTrue(all(v["record"]["H5000"]==200 for v in variants.values()))
        for field in [{"owner":"Private Name"},{"id":"Private Name"},{"mr":True}]:
            record={"id":"123","mr":150,"cc":2,"sc":1,"lastScan":1234,**field}
            with self.assertRaises(luaser.LuaError): luamerge.auctionator_record(luaser.table_from(record))

    def test_reparse_copied_wdb_preserves_original_capture_date(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);store=root/"merged";source=root/"itemcache.wdb"
            source.write_bytes(b"BDIW"+struct.pack("<I",12340)+b"SUne"+bytes(12)+struct.pack("<II",1,4)+b"test"+bytes(8))
            key=(merge.sha256_file(str(source)),"known")
            cls={"realm":"Realm","mode":"free-pick","slug":"free-pick","source":"folder"}
            with patch.object(merge,"STORE",str(store)),patch.object(merge,"SOURCES",str(store/"sources.tsv")),patch.object(merge.config,"LEDGER",str(root/"ledger.json")),patch.object(merge,"scan_disk",return_value={key:str(source)}),patch.object(merge.modes,"seed_json"),patch.object(merge.modes,"classify",return_value=cls),patch.object(merge.capture_dates,"source_date",return_value="2020-01-01"):
                merge.main()
                rows=merge.read_tsv(str(store/"sources.tsv"),[]);rows[0]["parser_revision"]="old"
                merge.write_tsv(str(store/"sources.tsv"),merge.SRC_COLS,rows)
                with patch.object(merge.capture_dates,"source_date",return_value="2026-09-11"):
                    merge.main()
                self.assertEqual(merge.read_tsv(str(store/"sources.tsv"),[])[0]["captured"],"2020-01-01")

    def test_harvest_gossip_hold_keeps_clean_observations(self):
        import harvestmerge
        g=luaser.loads('AscensionRebirthHarvestDB={vendors={Realm={[1]={at="2026",items={}}}},gossips={Realm={[2]={text="Hello Secretchar",at="2026"},[3]={text="Welcome traveler",at="2026"}}}}')
        state={}
        with patch.object(harvestmerge,"own_names",return_value={"secretchar"}):
            n=harvestmerge.merge_wildcardharvest(state,g,"a"*64,{"captured":"2026"},[])
        self.assertGreater(n,0)
        self.assertIn("1",state["harvest"]["vendors"]["Realm"])
        self.assertNotIn("2",state["harvest"]["gossips"]["Realm"])
        self.assertIn("3",state["harvest"]["gossips"]["Realm"])
        self.assertEqual(len(state["retained_records"]["a"*64]),1)
        self.assertNotIn("Secretchar",json.dumps(state))

    def test_lua_replay_retains_original_capture_context(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"Auctionator_Price_Database.lua";p.write_text('AUCTIONATOR_PRICE_DATABASE={__dbversion=2}')
            sid=luamerge.sha256(str(p));original={sid:{"captured":"","group":"original","realm":"Realm","mode":"free-pick","slug":"free-pick","submission":""}}
            with patch.object(luamerge,"load_state",return_value={"sources":{}}),patch.object(luamerge,"save_state"),patch.object(luamerge,"backfill_submissions",return_value=0),patch.object(luamerge,"discover",return_value=[("auctionator_price_database.lua",str(p))]):
                state=luamerge.run_merge(original)
            for key,value in original[sid].items():
                self.assertEqual(state["sources"][sid][key],value)
            with patch.object(luamerge,"load_state",return_value=state):
                with self.assertRaises(RuntimeError):luamerge.run_merge(original)

    def test_auctionator_branch_rollback_preserves_healthy_peer(self):
        g=luaser.loads('AUCTIONATOR_PRICE_DATABASE={__dbversion=4,["Area 52 - Free-Pick"]={Item=100},["Rexxar - Conquest of Azeroth"]={Good=200,Bad={unknown=1}},["Private - Noncanonical"]={Secret=99}}')
        state={};n=luamerge.merge_auctionator(state,g,"a"*64,{"captured":"2026"},[])
        self.assertEqual(n,1)
        self.assertEqual(set(state["auctionator"]),{"free-pick"})
        self.assertEqual(len(state["retained_records"]["a"*64]),2)
        self.assertNotIn("Secret",json.dumps(state))
        self.assertNotIn("Good",json.dumps(state))

    def test_observed_horde_market_label_is_canonical(self):
        self.assertIn("Warcraft Reborn_Horde",luamerge.modes.CANONICAL_MODE_LABELS)
        cls=luamerge.modes.classify("Realm - Warcraft Reborn_Horde")
        self.assertEqual(cls["slug"],"warcraft-reborn-horde")
        g=luaser.loads('AUCTIONATOR_PRICE_DATABASE={["Realm - Warcraft Reborn_Horde"]={["Item"]={id="123:0",mr=10,cc=2,sc=1,lastScan=4}}}')
        state={}
        self.assertEqual(luamerge.merge_auctionator(state,g,"hash",{"captured":""},[]),1)
        self.assertIn("warcraft-reborn-horde",state['auctionator_observations'])

    def test_missing_output_column_fails_gate(self):
        fails=[]
        audit_columns.compare_counts({'counts':{'union/a.tsv.gz:name':1}}, {},fails,[])
        self.assertTrue(fails)

    def test_decoder_failure_is_not_a_successful_export(self):
        with tempfile.TemporaryDirectory() as d:
            with patch.dict(export.DECODERS,{'bad':(lambda *a: (_ for _ in ()).throw(ValueError()),['entry'])}):
                with self.assertRaises(RuntimeError):
                    export.write_view(str(Path(d)/'bad.tsv'),'bad',{1:('abc',{},b'x')})

if __name__=='__main__': unittest.main()
