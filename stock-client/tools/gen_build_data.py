#!/usr/bin/env python3
"""Recover full community build records into matching JSON, Lua and SQL.

Reads one named JSON member from a local harvest ZIP. No extracted source files,
client writes, DB connections, build activation or network calls are performed.
"""
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import uuid
import zipfile
import gen_ca_data as gen

MEMBER = 'ascension-harvest-export/derived-live-comparison-data/builds/builds-dawnrise-2026-08-29.json'
KEY = 'dawnrise-20260829'


def normalize(raw):
    source = json.loads(raw)
    if set(source) != {'builds', 'detailed'} or not isinstance(source['builds'],dict):
        raise ValueError('unexpected captured build document shape')
    records=[]
    for bid, record in sorted(source['builds'].items()):
        if str(uuid.UUID(bid)) != bid or record.get('ID') != bid:
            raise ValueError('build key/ID mismatch')
        for field in ('Name','AuthorName','Category','Description','Icon','Roles','DifficultyRating','PrimaryStat'):
            if not isinstance(record.get(field),str):raise ValueError('missing build string: '+field)
        for field in ('Spells','RandomEnchants','WeaponTypes','ArmorTypes'):
            if not isinstance(record.get(field),list) and record.get(field) != {}:raise ValueError('missing build array: '+field)
        for spell in record['Spells']:
            if gen.integer(spell['Spell'],'build spell')<=0 or gen.integer(spell['Level'],'build level')<0:
                raise ValueError('invalid build spell')
        records.append(record)
    if not set(source['detailed']).issubset(source['builds']) or any(type(v) is not bool for v in source['detailed'].values()):
        raise ValueError('invalid detailed-build map')
    return records,source['detailed']


def assemble(raw, member=MEMBER):
    records,detailed=normalize(raw)
    metadata={'format':1,'key':KEY,'realm':'Dawnrise','capturedDate':'2026-08-29',
              'kind':'captured-community-catalogue','buildCount':len(records),
              'source':{'member':member,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()},
              'categoryCounts':dict(sorted(Counter(r['Category'] for r in records).items())),
              'activationSupported':False}
    outputs={'catalogue.json':gen.canonical({'metadata':metadata,'builds':records,'detailed':detailed})+'\n'}
    prefix='-- Generated from captured community builds; no activation rules are inferred.\n'
    outputs['lua/Initialize.lua']=prefix+'ASC.Builds.Begin('+gen.lua(metadata)+')\n'
    order=['lua/Initialize.lua']; chunk=prefix;part=1
    for record in records:
        value={'record':record,'detailed':detailed.get(record['ID'])}
        # None remains absent in Lua; a missing detail flag is not changed to false.
        if value['detailed'] is None:del value['detailed']
        line='ASC.Builds.Add('+gen.lua(value)+')\n'
        if len((prefix+line).encode())>gen.MAX_LUA_BYTES:raise ValueError('one build exceeds chunk limit')
        if len((chunk+line).encode())>gen.MAX_LUA_BYTES:
            name='lua/builds-%03d.lua'%part;outputs[name]=chunk;order.append(name);part+=1;chunk=prefix
        chunk+=line
    name='lua/builds-%03d.lua'%part;outputs[name]=chunk;order.append(name)
    outputs['lua/Finalize.lua']=prefix+'ASC.Builds.Finalize()\n';order.append('lua/Finalize.lua')
    outputs['load-order.txt']='\n'.join(order)+'\n'
    sql=['''-- Generated staging catalogue. No player state or activation is changed.
CREATE TABLE IF NOT EXISTS ca_build_catalogue (catalogue VARCHAR(64) NOT NULL PRIMARY KEY, metadata_json MEDIUMTEXT NOT NULL) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS ca_build (catalogue VARCHAR(64) NOT NULL, id CHAR(36) NOT NULL, detailed TINYINT NULL, data_json MEDIUMTEXT NOT NULL, PRIMARY KEY(catalogue,id)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
CREATE TABLE IF NOT EXISTS ca_build_spell (catalogue VARCHAR(64) NOT NULL, build_id CHAR(36) NOT NULL, ordinal INT UNSIGNED NOT NULL, spell INT UNSIGNED NOT NULL, level INT UNSIGNED NOT NULL, data_json MEDIUMTEXT NOT NULL, PRIMARY KEY(catalogue,build_id,ordinal)) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
START TRANSACTION;
''']
    key=gen.sql_text(KEY)
    for table in ('ca_build_spell','ca_build','ca_build_catalogue'):
        sql.append('DELETE FROM %s WHERE catalogue=%s;\n'%(table,key))
    sql.append('INSERT INTO ca_build_catalogue VALUES (%s,%s);\n'%(key,gen.sql_text(gen.canonical(metadata))))
    for record in records:
        flag='NULL' if record['ID'] not in detailed else str(int(detailed[record['ID']]))
        bid=gen.sql_text(record['ID'])
        sql.append('INSERT INTO ca_build VALUES (%s,%s,%s,%s);\n'%(key,bid,flag,gen.sql_text(gen.canonical(record))))
        for index,spell in enumerate(record['Spells'],1):
            sql.append('INSERT INTO ca_build_spell VALUES (%s,%s,%d,%d,%d,%s);\n'%(key,bid,index,spell['Spell'],spell['Level'],gen.sql_text(gen.canonical(spell))))
    sql.append('COMMIT;\n');outputs['world-staging.sql']=''.join(sql)
    outputs['generation.json']=gen.canonical({'format':1,'metadata':metadata,'outputs':{k:hashlib.sha256(v.encode()).hexdigest() for k,v in sorted(outputs.items())}})+'\n'
    return outputs


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harvest-zip',type=Path,required=True)
    parser.add_argument('--out',type=Path,required=True)
    parser.add_argument('--verify',action='store_true')
    args=parser.parse_args()
    if args.harvest_zip.resolve().is_relative_to(args.out.resolve()) or args.out.resolve().is_relative_to(args.harvest_zip.parent.resolve()):
        parser.error('output must not overlap the source directory')
    before=args.harvest_zip.stat()
    with zipfile.ZipFile(args.harvest_zip) as archive:
        matches=[i for i in archive.infolist() if i.filename==MEMBER]
        if len(matches)!=1 or matches[0].file_size>128_000_000:
            raise ValueError('missing, duplicate or oversized captured JSON member')
        raw=archive.read(matches[0])
    after=args.harvest_zip.stat()
    if (before.st_size,before.st_mtime_ns)!=(after.st_size,after.st_mtime_ns):raise ValueError('archive changed during read')
    outputs=assemble(raw)
    gen.write_outputs(args.out,outputs,args.verify)
    print('Verified' if args.verify else 'Generated',len(outputs),'files;',json.loads(outputs['generation.json'])['metadata']['buildCount'],'captured community builds')


if __name__=='__main__':main()
