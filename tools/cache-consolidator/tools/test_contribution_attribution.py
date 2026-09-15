import hashlib
import json
from pathlib import Path
import tempfile
import unittest
import contribution_attribution as a


class ContributionAttributionTests(unittest.TestCase):
    def fixture(self, root, extra=None):
        sid='00000000-0000-0000-0000-000000000001';job=root/sid
        raw=b'AUCTIONATOR_PRICE_DATABASE={["Area 52 - Free-Pick"]={["Item"]=10},["Elune - Season 9"]={}}'
        rel='accepted/part-0000/enUS/auctionator_price_database.lua'
        p=job/'validated-test'/rel;p.parent.mkdir(parents=True);p.write_bytes(raw)
        digest=hashlib.sha256(raw).hexdigest()
        files=[{'index':0,'name':'auctionator_price_database.lua','mode':'unknown','size':len(raw),'sha256':digest}]
        if extra:files.append(extra)
        (job/'provenance.json').write_text(json.dumps({'submission':sid,'manifest':{'files':files},'validation':{'accepted':[{'index':0,'path':rel,'sha256':digest}]}}))
        return {'id':sid,'modes':['unknown']},p

    def test_content_evidence_ignores_empty_realm(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);entry,_=self.fixture(root)
            result=a.summarize(entry,root)
            self.assertEqual(result['modes'],['free-pick']);self.assertEqual(result['realms'],['Area 52'])

    def test_adjacent_unknown_wdb_keeps_unknown(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);entry,_=self.fixture(root,{'index':1,'name':'itemcache.wdb','mode':'unknown'})
            self.assertEqual(a.summarize(entry,root)['modes'],['free-pick','unknown'])

    def test_changed_bytes_and_outside_paths_are_not_evidence(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);entry,p=self.fixture(root);p.write_bytes(p.read_bytes().replace(b'Area 52',b'Area 53'))
            self.assertEqual(a.summarize(entry,root),{})
            provenance=root/entry['id']/'provenance.json';data=json.loads(provenance.read_text())
            data['validation']['accepted'][0]['path']='../../other.lua';provenance.write_text(json.dumps(data))
            self.assertEqual(a.summarize(entry,root),{})

    def test_real_gui_size_cells(self):
        self.assertEqual(a.size_text(391),'391 B')
        self.assertEqual(a.size_text(0),'0 B')
        self.assertEqual(a.size_text(None),'—')
        self.assertEqual(a.size_text(2048),'2.0 KiB')

    def test_damaged_provenance_keeps_original_display(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);entry,_=self.fixture(root)
            p=root/entry['id']/'provenance.json';data=json.loads(p.read_text())
            del data['manifest']['files'][0]['size'];p.write_text(json.dumps(data))
            self.assertEqual(a.summarize(entry,root),{})
            p.write_text('[]');self.assertEqual(a.summarize(entry,root),{})
