import gzip
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import attribution
import build
from validate import validate


class AttributionTests(unittest.TestCase):
    def test_harvest_record_and_unattributed_branch(self):
        path='cachedata/lua/harvest/advancement.tsv'
        row={'entry':'4','realm':'Dawnrise','mode':'Season 10 Freepick'}
        result=build.identify(path,row,0)
        self.assertEqual(result[3],'season-10-freepick')
        self.assertIs(result[5],row)
        self.assertEqual(attribution.resolve(path,row)['realms'],['Dawnrise'])
        self.assertEqual(build.identify(path,{'realm':'(unattributed)','mode':'unknown'},0)[3],'unknown')
        self.assertEqual(attribution.resolve(path,{'realm':'(unattributed)','mode':'unknown'})['realms'],[])

    def test_multi_realm_aggregate_and_no_realm_guess(self):
        path='cachedata/lootcollector/items.tsv'
        info=attribution.resolve(path,{'realms':"Area 52 - Free-Pick|Vol'jin - Conquest of Azeroth"})
        self.assertEqual(info['modes'],['conquest-of-azeroth','free-pick'])
        self.assertEqual(info['realms'],['Area 52',"Vol'jin"])
        self.assertEqual(info['status'],'recorded')
        info=attribution.resolve('cachedata/lua/Auctionator.observations.json',{'key':'warcraft-reborn-horde','value':{}})
        self.assertEqual(info['modes'],['warcraft-reborn-horde'])
        self.assertEqual(info['realms'],[])
        self.assertIsNone(attribution.resolve('supplemental/unrelated.json',{'realm':'Area 52','mode':'Free-Pick'}))

    def test_missing_and_conflicting_evidence_stays_visible(self):
        path='cachedata/lootcollector/pins.tsv'
        info=attribution.resolve(path,{'realms':'Area 52 - Free-Pick|unknown','modes':'free-pick|unknown'})
        self.assertEqual(info['status'],'mixed')
        self.assertEqual(attribution.mode_text(info),'free-pick,unknown')
        info=attribution.resolve(path,{'realms':'Area 52 - Free-Pick','modes':'conquest-of-azeroth'})
        self.assertEqual(info['status'],'conflicting')
        self.assertEqual(info['modes'],['conquest-of-azeroth','free-pick'])
        self.assertIsNone(attribution.group('Area 52'))
        self.assertIsNone(attribution.group('arbitrary - invented mode'))

    def test_inference_retains_its_basis(self):
        info=attribution.resolve('cachedata/sources.tsv',{'mode':'Free-Pick','realm':'Unknown realm','mode_source':'inferred:0.975/40485'})
        self.assertEqual(info['evidence'][0]['basis'],'inferred:0.975/40485')
        self.assertEqual(info['realms'],[])

    def test_rebuild_rejects_stale_legacy_attribution_and_preserves_payload(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);data=root/'data';data.mkdir();cache=root/'cache';cache.mkdir();out=root/'out'
            path='cachedata/lua/harvest/vendors.tsv';p=data/path;p.parent.mkdir(parents=True)
            p.write_text('entry\tname\trealm\tmode\n1\tVendor\tDawnrise\tSeason 10 Freepick\n')
            for args in [('init',),('add','.'),('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','Fixture')]:
                subprocess.run(['git','-C',str(data),*args],check=True,capture_output=True)
            blob=build.git(data,'rev-parse','HEAD:'+path).decode().strip()
            old='old-unknown';stale=cache/old;stale.mkdir();(stale/'meta.json').write_text('{"records":1,"parts":1}')
            with gzip.open(stale/'index.jsonl.gz','wt') as f:f.write(json.dumps([old+'/0/0','Vendor','vendor','Unspecified','Addon observations','1'])+'\n')
            previous=root/'previous.json';previous.write_text(json.dumps({'schema':build.SCHEMA,'files':{old:{'path':path,'blob':blob,'records':1}}}))
            args=['build','--data',str(data),'--out',str(out),'--cache',str(cache),'--reuse-manifest',str(previous),'--hosting','r2']
            with patch.object(sys,'argv',args):build.main()
            manifest=json.loads((out/'manifest.json').read_text());self.assertNotIn(old,manifest['files'])
            row=manifest['browse']['vendor'][0];self.assertEqual(row[3],'season-10-freepick')
            fid,part,_=row[0].split('/')
            with gzip.open(out/'records'/fid/(part+'.json.gz'),'rt') as f:record=json.load(f)[0]
            self.assertEqual(record[5],{'entry':'1','name':'Vendor','realm':'Dawnrise','mode':'Season 10 Freepick'})
            self.assertEqual(record[6]['realms'],['Dawnrise'])
            validate(out)
            record[6]['realms']=['Invented realm']
            build.zipped(out/'records'/fid/(part+'.json.gz'),[record])
            with self.assertRaisesRegex(ValueError,'Attribution does not match'): validate(out)
            record[6]['realms']=['Dawnrise']
            build.zipped(out/'records'/fid/(part+'.json.gz'),[record])
            with patch.object(sys,'argv',args):build.main()
            self.assertEqual(json.loads((out/'manifest.json').read_text())['parsed_files'],0)
