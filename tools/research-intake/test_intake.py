import gzip
import importlib.util
import io
import json
from pathlib import Path
import sqlite3
import struct
import sys
import tarfile
import tempfile
import unittest
import zipfile
import intake
import readers


class IntakeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.base=Path(self.tmp.name)
        self.root=self.base/'private'; self.donations=self.base/'donations'; self.donations.mkdir()
    def tearDown(self):self.tmp.cleanup()
    def engine(self,**kw):return intake.Intake(self.root,reserve=0,**kw)
    def file(self,name,data):
        p=self.donations/name; p.parent.mkdir(parents=True,exist_ok=True)
        p.write_bytes(data if isinstance(data,bytes) else data.encode());return p
    def policy(self,**overrides):
        policy={'source':'test','title':'Test donated data','publication':'approved','permission':'Synthetic test fixture','mode':'conquest-of-azeroth','collections':{'records':{'kind':'npc','fields':{'id':'id','name':'name'}}}}
        policy.update(overrides);p=self.base/'policy.json';p.write_text(json.dumps(policy));return p
    def test_repeat_and_cross_source_provenance(self):
        p=self.file('a.jsonl','{"id":1,"name":"One"}\n{"id":1,"name":"Other variant"}\n')
        with self.engine() as e:
            e.ingest(p,'first'); e.ingest(p,'first'); e.ingest(p,'second')
            self.assertEqual(e.summary()['documents'],1);self.assertEqual(e.summary()['unique_record_payloads'],2)
            self.assertEqual(e.db.execute('SELECT count(*) FROM receipts').fetchone()[0],2)
            self.assertEqual(len(e.query('1','id')),2)
    def test_same_name_changed_content(self):
        p=self.file('same.json','[{"id":1}]')
        with self.engine() as e:
            e.ingest(p);p.write_text('[{"id":2}]');e.ingest(p)
            self.assertEqual(e.summary()['documents'],2)
    def test_unknown_retained(self):
        raw=b'future data\x00\xff';p=self.file('sample.mystery',raw)
        with self.engine() as e:
            r=e.ingest(p);self.assertEqual(r['status'],'needs-review')
            self.assertEqual(e.object_path(intake.digest(raw)).read_bytes(),raw)
    def test_partial_parse_is_atomic_and_reprocessable(self):
        p=self.file('bad.jsonl','{"id":1}\nNOT JSON\n')
        with self.engine() as e:
            e.ingest(p);self.assertEqual(e.summary()['unique_record_payloads'],0)
            self.assertEqual(e.db.execute('SELECT count(*) FROM occurrences').fetchone()[0],0)
    def test_json_duplicate_keys_and_large_value_held(self):
        for value in ['{"id":1,"id":2}','[1,]','[1]trailing']:
            with self.assertRaises((ValueError,readers.Held)):list(readers.json_array(io.StringIO(value)))
        with self.assertRaises(readers.Held):list(readers.json_array(io.StringIO('["'+'x'*readers.MAX_RECORD+'"]')))
    def test_streaming_json_array(self):
        values=[{'id':i,'name':'abc'*1000} for i in range(100)]
        self.assertEqual(list(readers.json_array(io.StringIO(json.dumps(values)))),values)
        self.assertEqual(list(readers.json_array(io.StringIO('[]'))),[])
    def test_sql_literals_and_copy_no_execution(self):
        sql="DROP DATABASE actual;\nCOPY public.npc (id, name, note) FROM stdin;\n1\tOne\t\\N\n2\tTwo\thello\\tthere\n\\.\nINSERT INTO `items` (`id`,`name`) VALUES (9,'O''Brien'),(10,'a\\nb');\n"
        rows=list(readers.read_sql(io.StringIO(sql)))
        self.assertEqual(rows[0],('public.npc',{'id':'1','name':'One','note':None}))
        self.assertEqual(rows[1][1]['note'],'hello\tthere');self.assertEqual(rows[2][1]['name'],"O'Brien")
        with self.assertRaises(readers.Held):list(readers.read_sql(io.StringIO('INSERT INTO x VALUES (danger());\n')))
    def test_sql_mixed_update_is_not_reported_as_fully_parsed(self):
        p=self.file('mixed.sql',"INSERT INTO npc (id,name) VALUES (448,'Hogger');\nUPDATE npc SET name='Changed' WHERE id=448;\n")
        with self.engine() as e:
            result=e.ingest(p)
            self.assertEqual(result['status'],'needs-review')
            self.assertEqual(e.summary()['record_occurrences'],0)
    def test_sqlite_types_and_view_not_run(self):
        p=self.donations/'db.sqlite';db=sqlite3.connect(p)
        db.executescript('CREATE TABLE npc(id INTEGER,name TEXT,payload BLOB); CREATE VIEW bad AS SELECT unknown_func();')
        db.execute('INSERT INTO npc VALUES(?,?,?)',(9007199254740993,'One',b'\x00\xff'));db.commit();db.close()
        rows=list(readers.read_records(p,'db.sqlite'))
        self.assertEqual(rows[0][1]['id'],9007199254740993);self.assertEqual(rows[0][1]['payload'],{'base64':'AP8='})
    def test_wal_database_and_sidecars_preserved_without_partial_parse(self):
        p=self.donations/'db.sqlite';db=sqlite3.connect(p)
        db.execute('PRAGMA journal_mode=WAL');db.execute('CREATE TABLE npc(id INTEGER)');db.commit()
        with self.engine() as e:
            result=e.ingest(self.donations)
            self.assertEqual(result['status'],'needs-review')
            self.assertEqual(e.summary()['record_occurrences'],0)
            self.assertGreaterEqual(e.summary()['documents'],2)
        db.close()
    def test_zip_inner_dedup_and_nested_archives(self):
        inner=io.BytesIO()
        with zipfile.ZipFile(inner,'w') as z:z.writestr('a.json','{"id":1}')
        p=self.donations/'one.zip'
        with zipfile.ZipFile(p,'w') as z:z.writestr('inner.zip',inner.getvalue());z.writestr('other.json','{"id":1}')
        with self.engine() as e:
            e.ingest(p,'first');e.ingest(p,'second')
            self.assertEqual(e.summary()['unique_record_payloads'],1)
            self.assertEqual(e.summary()['containers'],2)
            self.assertEqual(e.summary()['held'],0)
            self.assertEqual(e.db.execute("SELECT count(*) FROM receipts WHERE source='second'").fetchone()[0],4)
    def test_zip_traversal_encryption_symlink_and_budget(self):
        p=self.donations/'bad.zip'
        with zipfile.ZipFile(p,'w') as z:z.writestr('../escape.json','{}')
        with self.engine() as e:
            r=e.ingest(p);self.assertEqual(r['status'],'needs-review');self.assertFalse((self.base/'escape.json').exists())
        p=self.donations/'big.zip'
        with zipfile.ZipFile(p,'w',zipfile.ZIP_DEFLATED) as z:z.writestr('one.json','x'*10000)
        with self.engine(expanded=100) as e:
            e.ingest(p);self.assertTrue(e.object_path(intake.digest(p.read_bytes())).exists())
            self.assertEqual(list((self.root/'temporary').iterdir()),[])
    def test_tar_and_gzip(self):
        p=self.donations/'test.tar.gz'
        with tarfile.open(p,'w:gz') as t:
            raw=b'{"id":1}\n';info=tarfile.TarInfo('a.jsonl');info.size=len(raw);t.addfile(info,io.BytesIO(raw))
        with self.engine() as e:e.ingest(p);self.assertEqual(e.summary()['record_occurrences'],1)
    def test_csv_locale_strings_untouched(self):
        p=self.file('a.csv','id;name;x\n1;"Standing, Exterior";"-9430,91015625"\n')
        row=list(readers.read_records(p,p.name))[0][1];self.assertEqual(row['x'],'-9430,91015625')
    def test_html_inert(self):
        p=self.file('a.html','<title>One</title><script>danger()</script><p>Text</p><a href="javascript:danger()">Link</a>')
        row=list(readers.read_records(p,p.name))[0][1];self.assertNotIn('danger()',row['text']);self.assertEqual(row['links'],['javascript:danger()'])
    def test_localisation_and_raw_dbc(self):
        p=self.file('name.loc',struct.pack('<III',1,7,8)+b'Blizzard')
        self.assertEqual(list(readers.read_records(p,p.name))[0][1],{'id':'7','text':'Blizzard'})
        p=self.file('spell.dbc',b'WDBC'+struct.pack('<4I',1,2,8,3)+struct.pack('<2I',7,1)+b'\0x\0')
        rows=list(readers.read_records(p,p.name));self.assertEqual(rows[0][1]['u32'],[7,1]);self.assertEqual(rows[2][1]['offset'],1)
    def test_export_explicit_fields_and_hash_verification(self):
        p=self.file('data.jsonl','{"id":"9007199254740993123","name":"One","author":"Private Person"}\n')
        with self.engine() as e:
            e.ingest(p,'test');out=intake.export(e,'test',self.policy(),self.base/'public')
            report=intake.verify_export(out);self.assertEqual(report['public_records'],1)
            public_file=next(out.glob('*.gz'))
            with gzip.open(public_file,'rt') as f:row=json.loads(f.readline())
            self.assertEqual(row['record']['id'],'9007199254740993123');self.assertNotIn('author',row['record'])
            self.assertEqual(row['evidence']['omitted_fields_count'],1)
            self.assertEqual(intake.export(e,'test',self.policy(),self.base/'public'),out)
            public_file.write_bytes(b'bad')
            with self.assertRaises(ValueError):intake.verify_export(out)
    def test_export_refuses_unapproved_and_changed_schema_and_identity(self):
        p=self.file('data.json','{"id":1,"name":"person@example.com"}')
        with self.engine() as e:
            e.ingest(p,'test')
            with self.assertRaises(ValueError):intake.export(e,'test',self.policy(publication='private'),self.base/'public')
            with self.assertRaises(ValueError):intake.export(e,'test',self.policy(),self.base/'public')
            self.assertEqual(list((self.root/'exports').iterdir()),[])
    def test_privacy_screen_distinguishes_urls_and_tooltip_escapes_from_drive_paths(self):
        intake.screen('https://ascension.gg/status')
        intake.screen(r'Instant:\n')
        with self.assertRaises(readers.Held):intake.screen(r'C:\Users\Player\file.txt')
    def test_lock_and_corruption(self):
        p=self.file('data.json','{}')
        with self.engine() as e:
            with self.assertRaises(RuntimeError):self.engine()
            e.ingest(p);e.object_path(intake.digest(b'{}')).write_bytes(b'[]')
            r=e.ingest(p);self.assertEqual(r['status'],'needs-review');self.assertTrue(r['copy_failures'])
    def test_root_git_guard_and_inbox(self):
        private=self.base/'repo';private.mkdir();(private/'.git').write_text('gitdir: test')
        with self.assertRaises(ValueError):intake.Intake(private/'raw')
        with self.engine() as e:
            p=self.root/'inbox'/'test';p.mkdir();(p/'a.json').write_text('{}')
            self.assertEqual(e.ingest(p,'test')['status'],'preserved')

if __name__=='__main__':unittest.main()
