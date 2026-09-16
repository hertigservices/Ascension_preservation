import gzip,json,subprocess,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
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
    def test_storage_origin_is_allowed_for_data_and_images(self):
        headers=(Path(__file__).parent/'web/_headers').read_text(encoding='utf-8')
        origin='https://ascension-public-data.ascension-archive.workers.dev'
        for directive in ('connect-src','img-src'):
            self.assertIn(origin,headers.split(directive,1)[1].split(';',1)[0])
    def test_scoped_legacy_json_escapes_preserve_slashes(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d)/'supplemental/coa-databank/fixture/databank/palette/spells.jsonl.gz';p.parent.mkdir(parents=True)
            with gzip.open(p,'wt',encoding='utf-8') as f:f.write(r'{"id":1,"path":"Icons\Quest"}'+"\n")
            self.assertEqual(list(build.rows(p,'jsonl'))[0]['path'],r'Icons\Quest')
            other=Path(d)/'unknown.jsonl.gz';other.write_bytes(p.read_bytes())
            with self.assertRaises(json.JSONDecodeError):list(build.rows(other,'jsonl'))
    def test_missing_id_is_explicit_row_reference(self):
        self.assertEqual(build.identify("cachedata/sources.tsv",{"file":"example"},7)[0],"row:7")
    def test_html_stays_data(self):
        r={'entry':'1','name':'<img src=x onerror=alert(1)>','description':'<script>alert(1)</script>'}
        self.assertEqual(build.identify('cachedata/union/itemcache.tsv.gz',r,0)[5],r)
    def test_display_icon_identity_preserves_capture(self):
        for identifier in ('7',0,'9007199254740993123'):
            r={'displayid':identifier,'icon':r'Interface\Icons\INV_Spear_01.blp','stock_displayid':'999'}
            x=build.identify(build.DISPLAY_ICON_PATH,r,42)
            self.assertEqual(x[:3],(str(identifier),'inv_spear_01','display-icon'))
            self.assertIs(x[5],r)
        self.assertEqual(build.identify(build.DISPLAY_ICON_PATH,{'stock_displayid':'999'},42)[0],'row:42')
        self.assertEqual(build.identify('cachedata/union/itemcache.tsv.gz',{'entry':'5','displayid':'7'},42)[0],'5')

    def test_display_icon_rebuild_ignores_legacy_identity_cache(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);data=root/'data';data.mkdir();out=root/'out';cache=root/'cache'
            p=data/build.DISPLAY_ICON_PATH;p.parent.mkdir(parents=True)
            with gzip.open(p,'wt',encoding='utf-8') as f:f.write('displayid\ticon\tstock_displayid\n7\tINV_A\t99\n')
            asset=data/'supplemental/exiles-db/assets/icons/inv_a.png';asset.parent.mkdir(parents=True);asset.write_bytes(b'fixture')
            for args in [('init',),('add','.'),('-c','user.name=Test','-c','user.email=test@example.invalid','commit','-m','Fixture')]:
                subprocess.run(['git','-C',str(data),*args],check=True,capture_output=True)
            blob=build.git(data,'rev-parse','HEAD:'+build.DISPLAY_ICON_PATH).decode().strip()
            old='obsolete-display-cache';stale=cache/old;stale.mkdir(parents=True)
            (stale/'meta.json').write_text('{"records":1,"parts":1}')
            with gzip.open(stale/'index.jsonl.gz','wt') as f:f.write(json.dumps([old+'/0/0','Display Icon #row:0','display-icon','Unspecified','Client captures','row:0'])+'\n')
            build.zipped(stale/'0.json.gz',[['row:0','Display Icon #row:0','display-icon','Unspecified','Client captures',{}]])
            previous=root/'previous.json';previous.write_text(json.dumps({'schema':build.SCHEMA,'files':{old:{'path':build.DISPLAY_ICON_PATH,'blob':blob,'records':1}}}))
            args=['build','--data',str(data),'--out',str(out),'--cache',str(cache),'--reuse-manifest',str(previous),'--icon-base','https://example.invalid/icons/','--hosting','r2']
            with patch.object(sys,'argv',args):build.main()
            manifest=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
            self.assertNotIn(old,manifest['files'])
            row=manifest['browse']['display-icon'][0]
            self.assertEqual(row[1],'inv_a');self.assertEqual(row[5:7],[ '7','inv_a'])
            self.assertIn('id:7',manifest['search'])
            with patch.object(sys,'argv',args):build.main()
            again=json.loads((out/'manifest.json').read_text(encoding='utf-8'))
            self.assertEqual(again['parsed_files'],0)
            self.assertEqual(again['browse']['display-icon'],manifest['browse']['display-icon'])

    def test_icon_names_are_normalised(self):
        self.assertEqual(build.icon_name('Interface\\Icons\\INV_Chest_Fur.TGA'),'inv_chest_fur')
        self.assertEqual(build.icon_name('interface/icons/leywalk'),'leywalk')
        self.assertEqual(build.icon_name('inv_misc_fork&amp;knife'),'inv_misc_fork&knife')  # HTML-escaped page field
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
            put('supplemental/exiles-db/abc123/items.jsonl.gz','{"type":"item","id":"100","icon":"inv_b"}\n',True)
            put('supplemental/exiles-db/abc123/spells.jsonl.gz','{"type":"spell","id":"5","icon":"Interface/Icons/INV_A.blp"}\n{"type":"spell","id":"6","icon":"missing"}\n{"type":"spell","id":"7","icon":"inv_a"}\n',True)
            export='supplemental/rebuild/0123456789abcdef'
            put(export+'/ASSET_INDEX.csv','path,size_bytes,sha256\nstatic/icons-clean/inv_b.png,1,x\n')
            put(export+'/icon-map.csv.gz','kind,id,icon\nitem,100,inv_b\nitem,101,inv_b\nspell,7,inv_b\ncurrency,9,inv_b\nnpc,101,inv_a\n',True)
            icons=build.IconIndex(root,[p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()])
            self.assertEqual(icons.lookup('item','100'),'inv_a')      # captured display icon wins over the site and export
            self.assertEqual(icons.lookup('item','101'),'')           # export item rows are never used, even as a fallback
            self.assertEqual(icons.lookup('spell','5'),'inv_a')       # Exiles path + extension normalised
            self.assertEqual(icons.lookup('spell','6'),'')            # icon file not published
            self.assertEqual(icons.lookup('spell','7'),'inv_b')       # export overrides an Exiles spell page
            self.assertEqual(icons.lookup('currency','9'),'inv_b')
            self.assertEqual(icons.lookup('planner-item','100'),'')   # planner IDs are not item IDs
            self.assertEqual(icons.lookup('npc','100'),'')
            for r in build.rows(root/build.DISPLAY_ICON_PATH,'tsv'):
                eid,_,kind,*_=build.identify(build.DISPLAY_ICON_PATH,r,0)
                self.assertEqual(icons.lookup(kind,eid),'inv_a' if eid=='7' else '')
if __name__=='__main__':unittest.main()
