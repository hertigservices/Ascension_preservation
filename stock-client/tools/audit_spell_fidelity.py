#!/usr/bin/env python3
"""Audit recorded spell-effect losses against normalized CA entries and current DBC.

Read-only. Uses the parsed ordered harvest spell IDs, separated by dataset. A
recorded field loss is not labelled a wholly inert spell. No gameplay is tested.
"""
import argparse
from collections import Counter,defaultdict
import hashlib
import json
import mmap
from pathlib import Path
import struct
import gen_ca_data as gen

RULES={'Spell.dbc:TOTAL_SPELL_EFFECTS':('effect',{71,72,73}),
       'Spell.dbc:TOTAL_AURAS':('aura',{95,96,97})}


def journal_changes(raw):
    journal=json.loads(raw)
    changes=defaultdict(list)
    seen=set()
    for rule,(kind,fields) in RULES.items():
        body=journal['rules'][rule]
        if body['action']!='zero':raise ValueError('unsupported sanitizer action')
        for spell,field,old in body['entries']:
            spell=gen.integer(spell,'journal spell');field=gen.integer(field,'journal field');old=gen.integer(old,'journal value')
            if spell<=0 or field not in fields or old<body['limit']:raise ValueError('invalid sanitizer journal entry')
            if (spell,field) in seen:raise ValueError('duplicate sanitizer field')
            seen.add((spell,field))
            changes[spell].append({'kind':kind,'field':field,'originalValue':old,'recordedReplacement':0})
    return dict(changes)


def read_current_dbc(path,wanted):
    before=path.stat()
    result={}
    with path.open('rb') as source,mmap.mmap(source.fileno(),0,access=mmap.ACCESS_READ) as raw:
        if len(raw)<20:raise ValueError('truncated DBC')
        signature,count,fields,stride,strings=struct.unpack_from('<4s4I',raw)
        if signature!=b'WDBC' or stride!=fields*4 or fields<=97 or 20+count*stride+strings!=len(raw):raise ValueError('unsupported Spell.dbc layout')
        digest=hashlib.sha256(raw).hexdigest()
        seen=set()
        for index in range(count):
            offset=20+index*stride
            sid=struct.unpack_from('<I',raw,offset)[0]
            if sid not in wanted:continue
            if sid in seen:raise ValueError('duplicate requested spell ID in DBC')
            seen.add(sid)
            result[sid]={field:struct.unpack_from('<I',raw,offset+field*4)[0] for field in (71,72,73,95,96,97)}
    after=path.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('Spell.dbc changed during audit')
    return result,{'file':path.name,'bytes':before.st_size,'sha256':digest,'records':count,'fields':fields}


def audit_entries(entries,changes,current):
    classes=defaultdict(lambda:{'entries':0,'affectedEntries':0,'affectedSpells':set(),'entriesWithNoNonzeroEffectSpell':0})
    affected=[]
    all_spells=set()
    for entry in entries:
        cls=classes[entry['Class']];cls['entries']+=1
        # Generated Spells came from JSON cells, preserving rank order.
        ids=set(entry['Spells']);all_spells.update(ids)
        hits=sorted(ids&changes.keys())
        if not hits:continue
        cls['affectedEntries']+=1;cls['affectedSpells'].update(hits)
        rows=[]
        for sid in hits:
            state=current.get(sid)
            rows.append({'spell':sid,'name':entry.get('SpellNames',{}).get(str(sid)),
                         'changes':[dict(change,currentValue=state.get(change['field']) if state else None) for change in changes[sid]],
                         'missingFromCurrentDBC':state is None,
                         'noNonzeroEffectSlots':bool(state is not None and all(state[f]==0 for f in (71,72,73)))})
        if any(row['noNonzeroEffectSlots'] for row in rows):cls['entriesWithNoNonzeroEffectSpell']+=1
        affected.append({'entry':entry['ID'],'name':entry['Name'],'class':entry['Class'],'tab':entry['Tab'],'spells':rows})
    return {'entries':len(entries),'uniqueReferencedSpells':len(all_spells),'affectedEntryCount':len(affected),
            'affectedSpellCount':len(all_spells&changes.keys()),
            'perClass':[dict({'class':name},**{k:len(v) if isinstance(v,set) else v for k,v in values.items()}) for name,values in sorted(classes.items())],
            'affectedEntries':affected}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data',type=Path,required=True)
    parser.add_argument('--journal',type=Path,required=True)
    parser.add_argument('--spell-dbc',type=Path,required=True)
    parser.add_argument('--build-catalogue',type=Path)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    out=args.out.resolve()
    for source in (args.data,args.journal,args.spell_dbc,args.build_catalogue):
        if source is not None and (source.resolve().is_relative_to(out) or out.is_relative_to(source.resolve())):
            parser.error('report output must not overlap input paths')
    sources={}
    changes=journal_changes(gen.read_source(args.journal,'sanitizerJournal',sources))
    generation_raw=gen.read_source(args.data/'generation.json','caGeneration',sources)
    generation=json.loads(generation_raw)
    datasets={};wanted=set(changes)
    for dataset in generation['datasets']:
        name='datasets/'+dataset['key']+'/entries.json'
        raw=gen.read_source(args.data/name,name,sources)
        if hashlib.sha256(raw).hexdigest()!=generation['outputs'][name]:raise ValueError('CA generation hash mismatch')
        entries=json.loads(raw);datasets[dataset['key']]=entries
        wanted.update(s for e in entries for s in e['Spells'])
    builds=None
    if args.build_catalogue:
        raw=gen.read_source(args.build_catalogue/'catalogue.json','buildCatalogue',sources)
        manifest=json.loads((args.build_catalogue/'generation.json').read_text(encoding='utf-8-sig'))
        if hashlib.sha256(raw).hexdigest()!=manifest['outputs']['catalogue.json']:raise ValueError('build generation hash mismatch')
        builds=json.loads(raw);wanted.update(s['Spell'] for r in builds['builds'] for s in r['Spells'])
    current,sources['currentSpellDBC']=read_current_dbc(args.spell_dbc,wanted)
    mismatches=[{'spell':sid,**change,'currentValue':current.get(sid,{}).get(change['field'])} for sid,rows in changes.items() for change in rows if current.get(sid,{}).get(change['field'])!=0]
    report={'format':1,'sources':sources,'scope':'Recorded sanitizer losses; current DBC field audit, not gameplay verification',
            'journalFieldCount':sum(map(len,changes.values())),'journalSpellCount':len(changes),
            'journalChangesNotZeroInCurrentDBC':mismatches,
            'originalEnums':[{'kind':kind,'value':value,'changedFields':count} for (kind,value),count in Counter((r['kind'],r['originalValue']) for rows in changes.values() for r in rows).most_common()],
            'datasets':{key:audit_entries(entries,changes,current) for key,entries in datasets.items()},
            'limitations':['A lost field does not prove that the entire spell is inert.',
                'No nonzero effect slots describes only the inspected DBC; scripts or other core logic may still act.',
                'This does not identify unsupported effects that were absent from the supplied journal.',
                'Community build plans remain Dawnrise capture data and are not silently relabelled as CoA builds.']}
    if builds:
        report['builds']={'metadata':builds['metadata'],'affected':[]}
        for record in builds['builds']:
            hits=sorted({s['Spell'] for s in record['Spells']}&changes.keys())
            if hits:report['builds']['affected'].append({'id':record['ID'],'name':record['Name'],'spells':hits})
    out.mkdir(parents=True,exist_ok=True)
    (out/'spell-fidelity.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n',encoding='utf-8')
    lines=['# Stock-port spell fidelity audit','',report['scope']+'.','',
           '| Dataset | Entries | Entries with recorded losses | Referenced spells with recorded losses |','|---|---:|---:|---:|']
    for key,value in report['datasets'].items():lines.append('| %s | %d | %d | %d |'%(key,value['entries'],value['affectedEntryCount'],value['affectedSpellCount']))
    lines+=['',str(len(mismatches))+' journal fields differ from the recorded zero replacement in the inspected server DBC.','',
            'Per-class counts, named entries, exact spell IDs, original enums and current field values are in `spell-fidelity.json`.','',
            'Do not call every affected entry inert. Some spells retain other effects; full behavior needs core implementation and gameplay checks.']
    (out/'README.md').write_text('\n'.join(lines)+'\n',encoding='utf-8')
    print('\n'.join(lines))
    if builds:print('Community builds with at least one recorded spell loss:',len(report['builds']['affected']))


if __name__=='__main__':main()
