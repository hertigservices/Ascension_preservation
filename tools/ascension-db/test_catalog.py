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
    def test_icon_names_are_normalised(self):
        self.assertEqual(build.icon_name('Interface\\Icons\\INV_Chest_Fur.TGA'),'inv_chest_fur')
        self.assertEqual(build.icon_name('interface/icons/leywalk'),'leywalk')
    def test_icon_index_only_references_published_icons(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d)
            def put(rel,text,compressed=False):
                p=root/rel;p.parent.mkdir(parents=True,exist_ok=True)
                if compressed:
                    with gzip.open(p,'wt',encoding='utf-8',newline='') as f:f.write(text)
                else:p.write_text(text,encoding='utf-8',newline='')
            put('supplemental/exiles-db/assets/icons/inv_a.png','')
            put('cachedata/dbc/item_display_icons.tsv.gz','displayid\ticon\tstock_displayid\n7\tINV_A\t\n8\tINV_Gone\t\n',True)
            put('cachedata/union/itemcache.tsv.gz','entry\tname\tdisplayid\n100\tSword\t7\n101\tAxe\t8\n',True)
            put('supplemental/exiles-db/abc123/spells.jsonl.gz','{"type":"spell","id":"5","icon":"Interface/Icons/INV_A.blp"}\n{"type":"spell","id":"6","icon":"missing"}\n',True)
            export='supplemental/rebuild/0123456789abcdef'
            put(export+'/ASSET_INDEX.csv','path,size_bytes,sha256\nstatic/icons-clean/inv_b.png,1,x\n')
            put(export+'/icon-map.csv.gz','kind,id,icon\nitem,101,inv_b\nnpc,101,inv_a\n',True)
            icons=build.IconIndex(root,[p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()])
            self.assertEqual(icons.lookup('item','100'),'inv_a')      # display id -> display icon
            self.assertEqual(icons.lookup('item','101'),'inv_b')      # export overrides an unpublished display icon
            self.assertEqual(icons.lookup('spell','5'),'inv_a')       # Exiles path + extension normalised
            self.assertEqual(icons.lookup('spell','6'),'')            # icon file not published
            self.assertEqual(icons.lookup('planner-item','100'),'')   # planner IDs are not item IDs
            self.assertEqual(icons.lookup('npc','100'),'')
if __name__=='__main__':unittest.main()
