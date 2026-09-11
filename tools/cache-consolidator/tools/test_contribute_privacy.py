import unittest,tempfile,struct,gzip,json,zipfile,hashlib
from pathlib import Path
import contribute,wdblib

class ContributionTests(unittest.TestCase):
    def test_lua_uses_structural_policy(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/"MobSpells.lua"
            p.write_text('MobSpellsDB={profileKeys={Alice="private"},profiles={secret=1},global={mobs={[1]={[12]={name="Wolf",lastGUID="0x1234567812345678"}}}}}',encoding='utf-8')
            clean,name=contribute.sanitize_savedvariables(p)
            self.assertEqual(name,'mobspells.lua')
            self.assertIn('Wolf',clean)
            for private in ['Alice','profileKeys','profiles','secret','lastGUID']:
                self.assertNotIn(private,clean)
            p.write_bytes(b'X="bad'+bytes([255])+b'"')
            with self.assertRaises(UnicodeDecodeError): contribute.sanitize_savedvariables(p)

    def test_known_records_still_arrive_as_valid_wdb(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);payload=b'Page'+bytes(1)+struct.pack('<I',0)
            header=b'XTPW'+struct.pack('<I',12340)+b'SUne'+bytes(12)
            data=header+struct.pack('<II',7,len(payload))+payload+bytes(8)
            p=root/'pagetextcache.wdb';p.write_bytes(data)
            with gzip.open(root/'pagetextcache.index.tsv.gz','wt') as f:
                f.write('sha1\n'+hashlib.sha1(payload).hexdigest()+'\n')
            record=contribute.plan_cache(str(p),'Realm - Free-Pick','enUS',str(root))
            self.assertEqual(record['new'],0)
            target=root/'contribution.zip'
            contribute.write_bundle(str(target),str(root),[record],[],[],{'Realm - Free-Pick':{'slug':'free-pick'}})
            with zipfile.ZipFile(target) as z:
                member=next(n for n in z.namelist() if n.endswith('.wdb'))
                shipped=z.read(member)
            self.assertEqual(shipped,data)
            self.assertEqual(wdblib.inspect('pagetextcache.wdb',data=shipped).records,1)

if __name__=='__main__':unittest.main()
