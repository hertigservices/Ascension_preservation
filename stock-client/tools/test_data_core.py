#!/usr/bin/env python3
"""Offline Lua 5.1 and source/output consistency checks; never opens a client or DB."""
import argparse
import csv
import hashlib
import importlib.util
import io
import json
from pathlib import Path
import re
import sys
import unittest
from lupa.lua51 import LuaRuntime
import gen_ca_data as gen

ROOT = Path(__file__).resolve().parents[1]
CORE = ROOT / 'client/Interface/AddOns/!AscensionShim/core'


def assert_lua_value(expected, actual, label):
    if isinstance(expected, dict):
        assert actual is not None and set(actual.keys()) == set(expected), label
        for key, value in expected.items(): assert_lua_value(value, actual[key], label + '.' + str(key))
    elif isinstance(expected, list):
        assert actual is not None and set(actual.keys()) == set(range(1,len(expected)+1)), label
        for key, value in enumerate(expected,1): assert_lua_value(value,actual[key],label+'['+str(key)+']')
    else:
        assert actual == expected and (isinstance(expected,bool) == isinstance(actual,bool)), (label,expected,actual)


def runtime():
    rt=LuaRuntime(unpack_returned_tuples=True)
    for name in ('Namespaces.lua','Data.lua','EventBus.lua','CustomEvents.lua'):
        rt.execute((CORE/name).read_text(encoding='utf-8-sig'))
    assert rt.eval('_VERSION') == 'Lua 5.1'
    return rt


class GeneratorTests(unittest.TestCase):
    def row(self):
        return {'realm':'test','mode':'coa','node':'1','ID':'1','Name':'Example','Class':'Tinker','Tab':'Firearms','Spells':'[{"id":42,"name":"rank one"},{"id":43,"name":"rank two"}]','ConnectedNodes':'[]','isTalent':'False','RequiredLevel':'10','AECost':'2','TECost':'0'}
    def harvest(self, rows):
        buffer=io.StringIO(newline=''); writer=csv.DictWriter(buffer,fieldnames=list(rows[0]),delimiter='\t')
        writer.writeheader();writer.writerows(rows)
        return buffer.getvalue().encode()
    def test_spell_rank_order_and_boolean(self):
        row=gen.normalize(self.row())
        self.assertEqual(row['Spells'],[42,43]);self.assertIs(row['isTalent'],False)
        self.assertEqual(row['SpellNames']['43'],'rank two')
    def test_modes_do_not_merge(self):
        a=self.row(); b=dict(a,mode='freepick',AECost='0')
        groups=gen.load_harvest(self.harvest([a,b]))
        self.assertEqual(len(groups),2)
        self.assertEqual({g['entries'][1]['AECost'] for g in groups.values()},{0,2})
    def test_conflicting_duplicate_refused(self):
        a=self.row();b=dict(a,AECost='0')
        with self.assertRaisesRegex(ValueError,'conflicting duplicate'):gen.load_harvest(self.harvest([a,b]))
    def test_missing_values_are_not_zero(self):
        row=gen.normalize(dict(self.row(),AECost=''))
        self.assertNotIn('AECost',row)
    def test_malformed_spell_cell_refused(self):
        for value in ('not json','{}','[true]','[{"id":0}]'):
            with self.assertRaises((ValueError,KeyError)):gen.normalize(dict(self.row(),Spells=value))
    def test_lua_escaping_roundtrips(self):
        rt=runtime(); value={'quote':'"\\\n\r\t\x00123','unicode':'é漢字','false':False,'array':[1,2,3]}
        assert_lua_value(value,rt.eval(gen.lua(value)),'fixture')
    def test_dbc_bounds(self):
        with self.assertRaises(ValueError):gen.dbc_rows(b'WDBC'+b'\0'*16+b'bad')
    def test_event_routing(self):
        rt=runtime()
        rt.execute('''
        local methods={}
        function methods:RegisterEvent(name) self.stock[name]=true; return "native" end
        function methods:UnregisterEvent(name) self.stock[name]=nil end
        function methods:UnregisterAllEvents() self.stock={} end
        function methods:IsEventRegistered(name) return self.stock[name] == true end
        function methods:GetScript(name) return self.scripts[name] end
        local mt={__index=methods}
        local frame=setmetatable({stock={},scripts={}},mt)
        local other=setmetatable({stock={},scripts={}},mt)
        ASC.Events.InstallFrameType(frame)
        ASC.Events.InstallFrameType(other) -- must not double-wrap shared methods
        ASC.Events.Define({"ASC_TEST"})
        assert(frame:RegisterEvent("PLAYER_LOGIN") == "native")
        assert(frame:IsEventRegistered("PLAYER_LOGIN"))
        frame:RegisterEvent("ASC_TEST");frame:RegisterEvent("ASC_TEST")
        assert(not frame.stock.ASC_TEST and frame:IsEventRegistered("ASC_TEST"))
        local count=0
        frame.scripts.OnEvent=function(self,event,a,b,c)
            assert(event=="ASC_TEST" and a==1 and b==nil and c==3)
            count=count+1;self:UnregisterEvent(event)
        end
        ASC.Events.Fire("ASC_TEST",1,nil,3);ASC.Events.Fire("ASC_TEST",1,nil,3)
        assert(count==1 and not frame:IsEventRegistered("ASC_TEST"))
        frame:RegisterEvent("ASC_TEST");frame:UnregisterAllEvents()
        assert(not frame:IsEventRegistered("ASC_TEST") and not frame:IsEventRegistered("PLAYER_LOGIN"))
        local errors=0
        geterrorhandler=function() return function() errors=errors+1 end end
        frame:RegisterEvent("ASC_TEST");other:RegisterEvent("ASC_TEST")
        frame.scripts.OnEvent=function() error("expected handler failure") end
        local received=false
        other.scripts.OnEvent=function() received=true end
        ASC.Events.Fire("ASC_TEST")
        assert(errors==1 and received)
        ''')


def verify_generated(data_root):
    manifest=json.loads((data_root/'generation.json').read_text(encoding='utf-8-sig'))
    for name,digest in manifest['outputs'].items():
        assert hashlib.sha256((data_root/name).read_bytes()).hexdigest()==digest, name
    results=[]
    for dataset in manifest['datasets']:
        base=data_root/'datasets'/dataset['key']
        expected=json.loads((base/'entries.json').read_text(encoding='utf-8-sig'))
        rt=runtime()
        for relative in (base/'load-order.txt').read_text(encoding='utf-8-sig').splitlines():
            raw=(base/relative).read_bytes()
            if relative.startswith('lua/entries/'):
                assert len(raw)<=gen.MAX_LUA_BYTES, relative
            rt.execute(raw.decode('utf-8-sig'))
        data=rt.globals().ASC.Data
        data.SelectDataset(dataset['key'])
        for record in expected:
            assert_lua_value(record,data.GetEntry(record['ID']),str(record['ID']))
        # Independently decode every ca_entry's SQL JSON, then compare with Lua/JSON source.
        sql_entries=[]
        sql_references={}
        sql_essence=[]
        with (base/'world-staging.sql').open(encoding='utf-8') as source:
            for line in source:
                if line.startswith('INSERT INTO ca_entry VALUES '):
                    match=re.search(r"CONVERT\(X'([0-9a-f]+)' USING utf8mb4\)\);$",line.strip())
                    assert match, line[:100]
                    sql_entries.append(json.loads(bytes.fromhex(match[1]).decode('utf-8')))
                elif line.startswith('INSERT INTO ca_reference VALUES '):
                    cells=re.findall(r"CONVERT\(X'([0-9a-f]+)' USING utf8mb4\)",line)
                    assert len(cells)==3
                    assert bytes.fromhex(cells[0]).decode('utf-8')==dataset['key']
                    kind=bytes.fromhex(cells[1]).decode('utf-8')
                    sql_references.setdefault(kind,[]).append(json.loads(bytes.fromhex(cells[2]).decode('utf-8')))
                elif line.startswith('INSERT INTO ca_essence VALUES '):
                    cells=line.strip().split(' USING utf8mb4),',1)[1].removesuffix(');').split(',')
                    sql_essence.append(dict(zip(('ID','Level','Family','Match1','Match2','Match3','Match4','AE','TE'),map(int,cells))))
        tables=json.loads((base/'tables.json').read_text(encoding='utf-8-sig'))
        assert sql_references=={k:v for k,v in tables.items() if k!='essence'}, 'SQL and client reference tables differ'
        assert sql_essence==tables['essence'], 'SQL and client essence differ'
        assert sql_entries==expected, 'SQL and client entries differ'
        ae,te=data.GetBudget(28,80)
        assert (ae,te)==(36,35), 'Tinker must use family28'
        assert data.GetBudget(10,80)==(140,71), 'Free-Pick must use family10'
        assert data.GetBudget(99999,80)==(None,None,'unavailable')
        assert data.CanEvaluateLearn(1149)==(False,'rules-not-harvested')
        assert data.CanEvaluateLearn(30610)==(False,'missing-relationship')
        for name in ('C_ClassInfo.lua','C_CharacterAdvancement.lua'):
            rt.execute((CORE.parent/'api'/name).read_text(encoding='utf-8-sig'))
        preview=rt.globals().ASC.Preview
        preview.Begin(dataset['key'],28,80)
        info=rt.globals().C_ClassInfo.GetSpecInfo('TINKER','FIREARMS')
        assert info['ID']==49 and info['Name']=='Demolition' and info['PassiveID']==4049
        assert info['PassiveSpell']==805313 and info['Mail'] is True
        assert list(rt.globals().C_ClassInfo.GetAllSpecs('TINKER').values())==['FIREARMS','MECHANICS','INVENTION']
        identities=rt.globals().ASC.Data.Datasets[dataset['key']].tables.classIdentities
        custom=[row for row in identities.values() if row['ID']>11]
        assert len(custom)==21 and all(row['CAClassTypeID'] is not None for row in custom)
        assert preview.Context.identity['CAClassTypeID']==30 and preview.Context.identity['ID']==28
        ca=rt.globals().C_CharacterAdvancement
        assert ca.GetEntryByInternalID(4049)['Spells'][1]==92138
        assert ca.CanAddByEntryID(4049)==(False,'ASC_PREVIEW_READ_ONLY')
        assert ca.AddByEntryID(4049)==(False,'ASC_PREVIEW_READ_ONLY')
        assert ca.GetPendingRankByEntryID(4049)==(0,1)
        assert ca.CanSwitchActiveChrSpec(49) is True and ca.CanSwitchActiveChrSpec(1) is False
        assert ca.SwitchActiveChrSpec(49) is True and ca.GetActiveChrSpec()==49
        assert len(ca.CanApplyPendingBuild())==9
        ca.SetFilteredEntries('Napalm',rt.table())
        assert ca.IsFiltered(4049) is True and ca.IsFiltered(1000) is False
        ca.CancelPendingBuild()
        assert preview.cancelNotification is True
        preview.FlushNotifications()
        assert preview.cancelNotification is None
        summary=data.SelfTest()
        gaps=json.loads((base/'gaps.json').read_text(encoding='utf-8-sig'))
        assert summary['unresolvedRelationships']==len(gaps['missing_relationships'])
        results.append({'dataset':dataset['key'],'entries':len(expected),'buckets':summary['buckets'],'unresolvedRelationships':summary['unresolvedRelationships'],'incompleteRuleEntries':summary['incompleteRuleEntries']})
    print(json.dumps({'lua':'5.1','datasetChecks':results},indent=2))
    return results



def verify_pack(pack):
    rt=LuaRuntime(unpack_returned_tuples=True)
    rt.execute('\n    local methods={}\n    function methods:RegisterEvent(name) self.events[name]=true end\n    function methods:UnregisterEvent(name) self.events[name]=nil end\n    function methods:UnregisterAllEvents() self.events={} end\n    function methods:IsEventRegistered(name) return self.events[name] == true end\n    function methods:GetScript(name) return self.scripts[name] end\n    function methods:SetScript(name,fn) self.scripts[name]=fn end\n    function methods:Hide() self.hidden=true end\n    local mt={__index=methods}\n    function CreateFrame() return setmetatable({events={},scripts={}},mt) end\n    SlashCmdList={}\n    TEST_MESSAGES={}\n    DEFAULT_CHAT_FRAME={AddMessage=function(self,text) TEST_MESSAGES[#TEST_MESSAGES+1]=text end}\n    ')
    manifest=json.loads((pack/'PACK-MANIFEST.json').read_text(encoding='utf-8-sig'))
    for name,digest in manifest['files'].items():
        assert hashlib.sha256((pack/name).read_bytes()).hexdigest()==digest,name
    order=[]
    for line in (pack/'!AscensionShim.toc').read_text(encoding='utf-8-sig').splitlines():
        if not line.strip() or line.startswith('#'):continue
        source=(pack/line.replace('\\','/')).resolve()
        assert source.is_relative_to(pack.resolve()) and source.is_file(),line
        rt.execute(source.read_text(encoding='utf-8-sig'));order.append(line)
    rt.globals().SlashCmdList.ASCENSIONPREVIEW('selftest')
    assert '10255 entries' in rt.globals().TEST_MESSAGES[1]
    rt.globals().SlashCmdList.ASCENSIONPREVIEW('spec 49')
    assert rt.globals().C_CharacterAdvancement.GetActiveChrSpec()==49
    assert rt.globals().ASC.Preview.Context.readOnly is True
    if manifest['config'].get('buildCatalogue'):
        rt.globals().SlashCmdList.ASCENSIONPREVIEW('builds Leveling')
        assert rt.globals().C_BuildCreator.GetNumBuilds()==139
        rt.globals().ASC.Preview.Driver.GetScript(rt.globals().ASC.Preview.Driver,'OnUpdate')()
    print('Pack bootstrap passed under a mocked stock host:',len(order),'Lua files. Visual verification remains pending.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,default=ROOT/'data')
    args=parser.parse_args()
    suite=unittest.defaultTestLoader.loadTestsFromTestCase(GeneratorTests)
    result=unittest.TextTestRunner(verbosity=2).run(suite)
    if not result.wasSuccessful():return 1
    verify_generated(args.data)
    verify_pack(ROOT/'build/p2-core-preview/Interface/AddOns/!AscensionShim')
    return 0

if __name__=='__main__':raise SystemExit(main())
