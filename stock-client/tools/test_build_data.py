#!/usr/bin/env python3
"""Offline full-catalogue Lua/JSON/SQL parity and read-only API checks."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import unittest
from lupa.lua51 import LuaRuntime
import gen_build_data as gen
from test_data_core import assert_lua_value

ROOT=Path(__file__).resolve().parents[1]
SOURCE=ROOT/'client/Interface/AddOns/!AscensionShim'


class BuildTests(unittest.TestCase):
    def fixture(self):
        bid='00000000-0000-4000-8000-000000000001'
        record=dict(ID=bid,Name='Fixture',AuthorName='Fixture',Category='Leveling',Description='guide',Icon='icon',Roles='PLAYER_ROLE_DAMAGE',DifficultyRating='BUILD_DIFFICULTY_RATING_EASY',PrimaryStat='STAT_INTELLECT',Spells=[{'Spell':42,'Level':10}],RandomEnchants={},WeaponTypes=[],ArmorTypes=[])
        return {'builds':{bid:record},'detailed':{bid:False}}
    def test_full_record_preserved(self):
        fixture=self.fixture();fixture['builds'][next(iter(fixture['builds']))]['AdditionalCapturedField']={'x':'é漢字'}
        rows,detailed=gen.normalize(json.dumps(fixture).encode())
        self.assertEqual(rows,list(fixture['builds'].values()))
        self.assertEqual(detailed,fixture['detailed'])
    def test_identity_mismatch_refused(self):
        fixture=self.fixture();next(iter(fixture['builds'].values()))['ID']='bad'
        with self.assertRaises(ValueError):gen.normalize(json.dumps(fixture).encode())
    def test_unknown_detail_identity_refused(self):
        fixture=self.fixture();fixture['detailed']['missing']=True
        with self.assertRaises(ValueError):gen.normalize(json.dumps(fixture).encode())
    def test_boolean_spell_id_refused(self):
        fixture=self.fixture();next(iter(fixture['builds'].values()))['Spells'][0]['Spell']=True
        with self.assertRaises(ValueError):gen.normalize(json.dumps(fixture).encode())


def verify(directory):
    manifest=json.loads((directory/'generation.json').read_text(encoding='utf-8-sig'))
    for name,digest in manifest['outputs'].items():assert hashlib.sha256((directory/name).read_bytes()).hexdigest()==digest,name
    expected=json.loads((directory/'catalogue.json').read_text(encoding='utf-8-sig'))
    rt=LuaRuntime(unpack_returned_tuples=True)
    for name in ('core/Namespaces.lua','core/EventBus.lua','core/CustomEvents.lua','core/BuildData.lua'):
        rt.execute((SOURCE/name).read_text(encoding='utf-8-sig'))
    for name in (directory/'load-order.txt').read_text(encoding='utf-8-sig').splitlines():
        rt.execute((directory/name).read_text(encoding='utf-8-sig'))
    builds=rt.globals().ASC.Builds
    builds.Select(expected['metadata']['key'])
    catalogue=builds.Current()
    for record in expected['builds']:assert_lua_value(record,catalogue.records[record['ID']],record['ID'])
    assert_lua_value(expected['detailed'],catalogue.detailed,'detailed')
    sql_builds=[];sql_spells=[]
    for line in (directory/'world-staging.sql').read_text(encoding='utf-8-sig').splitlines():
        if line.startswith('INSERT INTO ca_build VALUES ') or line.startswith('INSERT INTO ca_build_spell VALUES '):
            match=re.search(r"CONVERT\(X'([0-9a-f]+)' USING utf8mb4\)\);$",line)
            assert match
            value=json.loads(bytes.fromhex(match[1]).decode('utf-8'))
            (sql_spells if line.startswith('INSERT INTO ca_build_spell ') else sql_builds).append(value)
    assert sql_builds==expected['builds']
    assert sql_spells==[s for r in expected['builds'] for s in r['Spells']]
    rt.execute((SOURCE/'api/C_BuildCreator.lua').read_text(encoding='utf-8-sig'))
    api=rt.globals().C_BuildCreator
    empty=rt.table()
    # Spy only on the owned event dispatcher; no client or source UI is executed.
    rt.execute('captured={}; ASC.Events.Fire=function(event,...) captured[#captured+1]={event,...} end')
    assert api.QueryAllBuilds('Leveling') == (True,None)
    assert api.GetNumBuilds()==expected['metadata']['categoryCounts']['Leveling']
    assert len(rt.globals().captured)==0,'query must defer its result event until after ShowLoading'
    builds.FlushNotifications()
    assert rt.globals().captured[1][1]=='BUILD_CREATOR_CATEGORY_RESULT'
    assert rt.globals().captured[1][2]=='Leveling'
    api.QueryAllBuilds('None')
    assert api.GetNumBuilds()==len(expected['builds'])
    record=api.GetBuildAtIndex(1)
    original=api.GetBuild(record['ID'])
    record['Name']='mutated by caller'
    assert api.GetBuild(record['ID'])['Name']==original['Name'],'callers must not mutate recovered data'
    assert api.UpdateFilter('',empty,rt.table_from({'SORT_RATING_DESCENDING':True})) is True
    votes=[api.GetBuildAtIndex(i)['Upvotes'] for i in range(1,api.GetNumBuilds()+1)]
    assert votes==sorted(votes,reverse=True)
    api.UpdateFilter('Molten Earth',empty,empty)
    assert api.GetNumBuilds()>=1
    assert api.UpdateFilter('',rt.table_from({'FILTER_CLASS_TINKER':True}),empty)==(False,'unsupported-preview-build-filter')
    assert api.GetNumBuilds()==0,'unsupported class rules must not silently show unfiltered builds'
    api.QueryAllBuilds('None')
    api.GetBuildAtIndex(1)['Spells'][1]['Spell']=-1
    assert api.GetBuildAtIndex(1)['Spells'][1]['Spell']>0
    ok,reasons=api.CanActivateBuild(original['ID'])
    assert ok is False and reasons[1]=='ASC_PREVIEW_READ_ONLY'
    for operation in ('ActivateBuild','DeactivateBuild','RateBuild','BookmarkBuild'):
        assert api[operation](original['ID'])==(False,'ASC_PREVIEW_READ_ONLY')
    assert api.GetActiveBuild() is None and api.IsOwnedBuild(original['ID']) is False
    assert api.GetNumBookmarkedBuilds()==0
    assert api.GetBuild('missing') is None
    print('PASS:',len(expected['builds']),'complete builds;',len(sql_spells),'captured spell-plan rows; Lua 5.1/JSON/SQL parity; catalogue API and deferred events')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=ROOT/'data/build-catalogue')
    args=parser.parse_args()
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(BuildTests)
    if not unittest.TextTestRunner(verbosity=2).run(suite).wasSuccessful():return 1
    verify(args.data)
    return 0


if __name__=='__main__':raise SystemExit(main())
