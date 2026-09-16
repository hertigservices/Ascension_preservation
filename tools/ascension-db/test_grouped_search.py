import copy
import gzip
import json
import tempfile
import unittest
from pathlib import Path
from grouped_search import content_id, locale, search_row, build_grouped_search
from build import Buckets, zipped
from validate_groups import GroupValidator


class GroupTests(unittest.TestCase):
    def record(self, **fields):
        return ['375250', 'Rune', 'item', 'free-pick', 'Client captures', {'entry':'375250','name':'Rune','BagFamily':'8192',**fields}]

    def test_only_known_cache_bookkeeping_is_ignored(self):
        path='cachedata/by-mode/free-pick/itemcache.tsv.gz'
        a=self.record(_captured='a',_sources='3',_modes='a,b',_locales='enUS')
        b=self.record(_captured='b',_sources='5',_modes='a',_locales='zhCN')
        self.assertEqual(content_id(path,a),content_id('cachedata/by-locale/enUS/union/itemcache.tsv.gz',b))
        for field,value in [('BagFamily','0'),('description','different'),('_unknown','new'),('entry',375250)]:
            different=copy.deepcopy(b);different[5][field]=value
            self.assertNotEqual(content_id(path,a),content_id(path,different))
        self.assertNotEqual(content_id('supplemental/items.tsv',a),content_id('supplemental/items.tsv',b))
        different=copy.deepcopy(a);different[2]='planner-item'
        self.assertNotEqual(content_id(path,a),content_id(path,different))

    def test_locales_do_not_invent_mode_locale_pairs(self):
        r=self.record(_locales='enUS,zhCN')
        self.assertEqual(locale('cachedata/by-mode/free-pick/itemcache.tsv.gz',r),'Unspecified')
        self.assertEqual(locale('cachedata/by-locale/zhCN/by-mode/stress-test/itemcache.tsv.gz',r),'zhCN')
        self.assertEqual(locale('cachedata/by-mode/free-pick/itemcache.tsv.gz',self.record(_locales='enUS')),'enUS')

    def test_preserved_live_item_has_four_content_groups(self):
        # Synthetic regression reproducing the observed 28 client rows, without
        # depending on the workstation or a changing public snapshot.
        rows=[self.record() for _ in range(22)]
        rows += [self.record(BagFamily='0') for _ in range(2)]
        rows += [self.record(name='Mark',description='Old',displayid='30658') for _ in range(2)]
        rows += [self.record(name='Marque',description='French') for _ in range(2)]
        self.assertEqual(len({content_id('cachedata/union/itemcache.tsv.gz',r) for r in rows}),4)

    def test_grouping_and_validator_preserve_all_members_and_zone_scopes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);stage=root/'stage';stage.mkdir();buckets=Buckets(stage)
            a=['1','Quest','quest','CoA','Client captures',{'entry':'1','name':'Quest','ZoneOrSort':'12','_locales':'enUS'}]
            b=copy.deepcopy(a);b[3]='Draft';b[5]['_locales']='frFR'
            paths={'a':'cachedata/by-locale/enUS/by-mode/CoA/questcache.tsv.gz','b':'cachedata/by-locale/frFR/by-mode/Draft/questcache.tsv.gz'}
            manifest={'files':{k:{'path':v,'records':1} for k,v in paths.items()},'atlas':{'links':'links.json.gz'},'records':2}
            zipped(root/'links.json.gz',{})
            for key,r in [('a',a),('b',b)]:zipped(root/'records'/key/'0.json.gz',[r])
            build_grouped_search(root,manifest,{'areas':[],'zones':[]},buckets,None,stage);buckets.close()
            self.assertEqual(manifest['grouping']['groups'],1)
            row=manifest['browse']['quest'][0]
            self.assertEqual(len(row[7]['members']),2)
            validator=GroupValidator(manifest)
            try:
                validator.record(paths['a'],'a/0/0',a);validator.record(paths['b'],'b/0/0',b)
                validator.row('browse:quest',row);validator.finish()
                altered=copy.deepcopy(row);altered[7]['members'][0][3]='frFR'
                with self.assertRaisesRegex(ValueError,'differs'):validator.row('zone:area:12',altered)
            finally:validator.close()
            # Zone subset rows use the same identity but retain only their own members.
            rows=[['a','N','npc','CoA','A','1','',['a','CoA','A','enUS']],['b','N','npc','Draft','B','1','',['b','Draft','B','frFR']]]
            subset=search_row('group',rows[1:])
            self.assertEqual(subset[0],'b');self.assertEqual(subset[3],'Draft');self.assertEqual(subset[7]['members'],[rows[1][7]])

if __name__=='__main__':unittest.main()
