import datetime as dt
import hashlib
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from dataset import canonical
import live_catalog as live
from live_backup import ArchiveIndex,archive_objects
from restore_catalog import restore


class Error(Exception):
    def __init__(self,code):self.response={'Error':{'Code':code}}


class S3:
    def __init__(self):self.data={};self.puts=[];self.deletes=[];self.fail_key=None;self.before_put=None
    def get_object(self,*,Bucket,Key,IfMatch=None):
        if Key not in self.data:raise Error('404')
        body=self.data[Key];etag='"'+hashlib.md5(body).hexdigest()+'"'
        if IfMatch and etag!=IfMatch:raise Error('412')
        return {'Body':io.BytesIO(body),'ETag':etag}
    def put_object(self,*,Bucket,Key,Body,IfMatch=None,IfNoneMatch=None,**kw):
        if self.before_put:self.before_put(Key)
        if Key==self.fail_key:raise Error('500')
        if IfNoneMatch and Key in self.data:raise Error('412')
        if IfMatch:self.get_object(Bucket=Bucket,Key=Key,IfMatch=IfMatch)
        self.data[Key]=Body;self.puts.append(Key);return {}
    def get_paginator(self,name):
        store=self
        class Paginator:
            def paginate(self,*,Bucket,Prefix='',Delimiter=None):
                yield {'Contents':[{'Key':k,'Size':len(v),'ETag':'"'+hashlib.md5(v).hexdigest()+'"'} for k,v in store.data.items() if k.startswith(Prefix)]}
        return Paginator()
    def delete_objects(self,*,Bucket,Delete):
        for obj in Delete['Objects']:self.deletes.append(obj['Key']);self.data.pop(obj['Key'],None)
        return {'Deleted':Delete['Objects']}


def report(files):
    value={'schema':'ascension-objects-1','kind':'catalog','files':{k:{'bytes':len(v),'sha256':hashlib.sha256(v).hexdigest()} for k,v in files.items()}}
    value['snapshot']=hashlib.sha256(canonical(value)).hexdigest();return value


class LiveTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.archive=live.Archive(self.root/'archive');self.store=S3()
        self.files={'records/one.json.gz':b'unchanged','manifest.json':b'old'};self.old=report(self.files);base=live.prefix(self.old['snapshot'])
        self.store.data.update({base+k:v for k,v in self.files.items()})
        self.store.data[base+'storage-manifest.json']=canonical(self.old)
        self.current={'schema':'ascension-current-1','snapshot':self.old['snapshot'],'prefix':base,'previous':None}
        self.store.data['catalog/current.json']=canonical(self.current);self.store.data['images/icons/a.png']=b'picture'
        self.budget=live.Budget(self.root/'budget.json',limit=10000)
        self.index=ArchiveIndex(self.archive.root)
    def tearDown(self):self.index.db.close();self.tmp.cleanup()
    def backup(self):archive_objects(self.store,self.archive,self.index,live.list_objects(self.store),workers=1)
    def build(self,files):
        output=self.root/'output';output.mkdir(exist_ok=True)
        for name,body in files.items():p=output/name;p.parent.mkdir(exist_ok=True,parents=True);p.write_bytes(body)
        return output,report(files)
    def test_migration_reuses_every_existing_payload_and_cleans_only_history(self):
        self.store.data['catalog/snapshots/'+'f'*64+'/orphan']=b'abandoned';self.backup()
        result=live.promote(self.store,self.archive,self.budget,self.old)
        self.assertEqual(result['new_payload_objects'],0)
        self.assertFalse(any(k.startswith('catalog/objects/') for k in self.store.puts))
        receipt=live.cleanup(self.store,self.archive,self.index,journal=self.root/'delete.jsonl')
        self.assertEqual(receipt['deleted_objects'],1);self.assertIn('images/icons/a.png',self.store.data)
        self.assertEqual(live.current_files(self.store,live.pointer(self.store)[0])[0],self.old)
    def test_new_publication_reuses_content_even_if_filename_changes(self):
        self.backup();live.promote(self.store,self.archive,self.budget,self.old)
        output,new=self.build({'records/renamed.json.gz':b'unchanged','manifest.json':b'new'})
        result=live.promote(self.store,self.archive,self.budget,new,output)
        self.assertEqual(result['new_payload_objects'],1)
        self.assertEqual(live.pointer(self.store)[0]['previous'],None)
        self.backup();live.cleanup(self.store,self.archive,self.index,journal=self.root/'delete.jsonl')
        retained=live.retained_keys(self.store,live.pointer(self.store)[0])|{'images/icons/a.png'}
        self.assertEqual(set(self.store.data),retained)
        self.assertIn(self.current['prefix']+'records/one.json.gz',retained)
    def test_upload_failure_leaves_current_intact_and_reservation_charged(self):
        self.backup();output,new=self.build({'a':b'new'});self.store.fail_key='catalog/objects/'+new['files']['a']['sha256']
        with self.assertRaises(Error):live.promote(self.store,self.archive,self.budget,new,output)
        self.assertEqual(live.pointer(self.store)[0],self.current)
        self.assertGreater(sum(self.budget.data['periods'].values()),0)
    def test_cleanup_refuses_unbacked_object_without_deleting_anything(self):
        self.backup();live.promote(self.store,self.archive,self.budget,self.old)
        self.store.data['catalog/unexpected']=b'unknown'
        with self.assertRaisesRegex(ValueError,'WD recovery'):live.cleanup(self.store,self.archive,self.index,journal=self.root/'delete.jsonl')
        self.assertEqual(self.store.deletes,[])
    def test_cleanup_refuses_corrupt_backup(self):
        key='catalog/old';self.store.data[key]=b'old';self.backup();live.promote(self.store,self.archive,self.budget,self.old)
        self.archive.path(self.index.get(key)['sha256']).write_bytes(b'bad')
        with self.assertRaises(ValueError):live.cleanup(self.store,self.archive,self.index,journal=self.root/'delete.jsonl')
        self.assertEqual(self.store.deletes,[])
    def test_capacity_and_write_caps_fail_before_any_put(self):
        self.backup();output,new=self.build({'a':b'new'})
        for options in ({'max_public_bytes':1},{'max_new_objects':0}):
            with self.assertRaises(ValueError):live.promote(self.store,self.archive,self.budget,new,output,**options)
        tiny=live.Budget(self.root/'tiny.json',limit=1)
        with self.assertRaisesRegex(ValueError,'Monthly'):live.promote(self.store,self.archive,tiny,new,output)
        self.assertEqual(self.store.puts,[])
    def test_pointer_race_does_not_replace_other_publisher(self):
        self.backup();output,new=self.build({'a':b'new'})
        other={**self.current,'previous':'f'*64}
        self.store.before_put=lambda key:self.store.data.__setitem__('catalog/current.json',canonical(other)) if key.startswith('catalog/objects/') else None
        with self.assertRaisesRegex(ValueError,'changed'):live.promote(self.store,self.archive,self.budget,new,output)
        self.assertEqual(live.pointer(self.store)[0],other)
    def test_same_layout_retry_is_noop(self):
        self.backup();live.promote(self.store,self.archive,self.budget,self.old);count=len(self.store.puts)
        self.assertTrue(live.promote(self.store,self.archive,self.budget,self.old)['unchanged']);self.assertEqual(len(self.store.puts),count)
    def test_restore_deleted_history_without_network(self):
        self.backup();self.store.data.clear()
        output=self.root/'restored';receipt=restore(self.archive,self.index,self.old['snapshot'],output)
        self.assertTrue(receipt['all_hashes_matched'])
        for name,body in self.files.items():self.assertEqual((output/name).read_bytes(),body)
        (output/'manifest.json').write_bytes(b'edited restored copy')
        self.assertEqual(self.archive.path(self.old['files']['manifest.json']['sha256']).read_bytes(),b'old')
    def test_wrong_volume_stops(self):
        with patch.object(live.subprocess,'check_output',return_value='other-disk'):
            with self.assertRaisesRegex(ValueError,'Wrong backup'):live.Archive(self.root/'absent','expected')
    def test_calendar_billing_period_and_durable_reservation(self):
        a=live.Budget(self.root/'period.json',limit=20,now=dt.datetime(2026,10,9,tzinfo=dt.timezone.utc));a.reserve(3)
        b=live.Budget(self.root/'period.json',limit=20,now=dt.datetime(2026,10,9,tzinfo=dt.timezone.utc));self.assertEqual(b.period,'2026-09-10')
        with self.assertRaises(ValueError):b.reserve(3)
        c=live.Budget(self.root/'period.json',limit=20,now=dt.datetime(2026,10,10,tzinfo=dt.timezone.utc));c.reserve(3);self.assertEqual(c.period,'2026-10-10')


if __name__=='__main__':unittest.main()
