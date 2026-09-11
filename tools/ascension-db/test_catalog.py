import gzip,json,subprocess,sys,tempfile,unittest
from pathlib import Path
import build
class CatalogTests(unittest.TestCase):
    def test_large_ids_and_provenance(self):
        x=build.identify('supplemental/bisbeard/snapshot/rows.jsonl.gz',{'record':{'id':'9007199254740993123','name':'Example'},'base_item_id':'1'},0)
        self.assertEqual(x[0],'9007199254740993123');self.assertEqual(x[4],'BisBeard');self.assertEqual(x[2],'planner-item')
    def test_modes_not_inferred_for_addons(self):
        x=build.identify('cachedata/lua/harvest/vendors.tsv',{'entry':'1','name':'Vendor'},0)
        self.assertEqual(x[3],'Unspecified');self.assertEqual(x[4],'Addon observations')
    def test_modes_are_retained(self):
        self.assertEqual(build.identify('cachedata/by-mode/conquest-of-azeroth/itemcache.tsv.gz',{'entry':'1','name':'One'},0)[3],'conquest-of-azeroth')
        self.assertEqual(build.identify('cachedata/union/itemcache.tsv.gz',{'entry':'1','name':'One','_modes':'a,b'},0)[3],'a,b')
    def test_unknown_and_binary_are_references(self):
        for p in ['cachedata/raw/itemcache.pack.gz','cachedata/lua/MobSpells.lua','future/new-format.xyz']:
            self.assertEqual(build.adapter(p)[0],'reference')
    def test_unicode_search(self):
        self.assertEqual(build.tokens('Épée de Straße'),['epee','de','strasse']);self.assertEqual(build.tokens('Меч'),['меч'])
    def test_malformed_tsv_is_not_silently_dropped(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'bad.tsv';p.write_text('id\tname\n1\tone\textra\n',encoding='utf-8')
            with self.assertRaises(ValueError):list(build.rows(p,'tsv'))
    def test_distinct_variants_survive(self):
        a=build.identify('cachedata/by-mode/a/itemcache.tsv.gz',{'entry':'1','name':'Old','damage':'4'},0)
        b=build.identify('cachedata/by-mode/b/itemcache.tsv.gz',{'entry':'1','name':'New','damage':'8'},0)
        self.assertNotEqual(a,b);self.assertEqual(a[5]['damage'],'4');self.assertEqual(b[5]['damage'],'8')
    def test_missing_id_is_explicit_row_reference(self):
        self.assertEqual(build.identify("cachedata/sources.tsv",{"file":"example"},7)[0],"row:7")
    def test_html_stays_data(self):
        r={'entry':'1','name':'<img src=x onerror=alert(1)>','description':'<script>alert(1)</script>'}
        self.assertEqual(build.identify('cachedata/union/itemcache.tsv.gz',r,0)[5],r)
if __name__=='__main__':unittest.main()
