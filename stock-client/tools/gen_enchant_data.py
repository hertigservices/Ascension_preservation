#!/usr/bin/env python3
"""Recover the Mystic Enchant catalogue (3,822 enchants) into JSON and Lua for the addon pack.

Source: the item-scrape addon's saved variables inside the harvest ZIP,
`AscensionScrape_2026-09-02_area52-enchants.lua` (AscensionRebirthScrapeDB.enchants), which
recorded C_MysticEnchant.GetEnchantInfoBySpell for every enchant the live collection UI listed:
SpellID, SpellName, ClientName, IsWorldforged, Known (for the scraping character), MaxStacks,
Icon, Quality (an Enum.EnchantQualityEnum key such as RE_QUALITY_EPIC).

What was NOT captured -- and is therefore absent from the output rather than invented: scroll
items, costs, altar levels, slot rules, class/spec requirements, required levels. The
stock-client Mystic Enchant panel shows the collection and reports every altar action as
unavailable (there is no Mystic Altar on the port).

    python gen_enchant_data.py --harvest-zip <ascension-harvest-export.zip> --out data/enchants [--verify]
"""
import argparse
import hashlib
import json
import re
import zipfile
from collections import Counter
from pathlib import Path
import gen_ca_data as gen

MEMBER = 'ascension-harvest-export/addon-harvest-savedvariables/item-scrape/AscensionScrape_2026-09-02_area52-enchants.lua'
KEY = 'area52-enchants-20260902'
RECORD = re.compile(r'\[(\d+)\]\s*=\s*\{(.*?)\n\t\t\},', re.S)
FIELD = re.compile(r'\["(\w+)"\]\s*=\s*(true|false|-?\d+|"(?:[^"\\]|\\.)*"),')


def lua_string(text):
    return text[1:-1].encode('utf-8').decode('unicode_escape') if '\\' in text else text[1:-1]


def parse(raw):
    text = raw.decode('utf-8')
    if 'AscensionRebirthScrapeDB' not in text or '["enchants"]' not in text:
        raise ValueError('unexpected scrape shape')
    meta = {}
    m = re.search(r'\["meta"\]\s*=\s*\{(.*?)\n\t\},', text, re.S)
    if m:
        for f in FIELD.finditer(m.group(1)):
            k, v = f.group(1), f.group(2)
            meta[k] = int(v) if v.lstrip('-').isdigit() else (v == 'true' if v in ('true', 'false') else lua_string(v))
    records = []
    body = text[text.index('["enchants"]'):text.index('["meta"]')]
    for r in RECORD.finditer(body):
        rec = {}
        for f in FIELD.finditer(r.group(2)):
            k, v = f.group(1), f.group(2)
            rec[k] = int(v) if v.lstrip('-').isdigit() else (v == 'true' if v in ('true', 'false') else lua_string(v))
        if rec.get('SpellID') != int(r.group(1)):
            raise ValueError('enchant key/SpellID mismatch at %s' % r.group(1))
        for k in ('SpellName', 'Icon', 'Quality'):
            if not isinstance(rec.get(k), str):
                raise ValueError('missing string field %s on %s' % (k, r.group(1)))
        for k in ('MaxStacks',):
            if not isinstance(rec.get(k), int):
                raise ValueError('missing integer field %s on %s' % (k, r.group(1)))
        rec['IsWorldforged'] = bool(rec.get('IsWorldforged'))
        rec.pop('Known', None)  # the scraping character's collection, not the catalogue
        records.append(rec)
    if meta.get('n') and meta['n'] != len(records):
        raise ValueError('scrape meta says %s enchants, parsed %d' % (meta['n'], len(records)))
    records.sort(key=lambda r: r['SpellID'])
    return records, meta


def assemble(raw, member=MEMBER):
    records, meta = parse(raw)
    metadata = {'format': 1, 'key': KEY, 'realm': meta.get('realm', ''), 'capturedDate': str(meta.get('scrapedAt', ''))[:10],
                'kind': 'captured-mystic-enchant-catalogue', 'enchantCount': len(records),
                'source': {'member': member, 'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest()},
                'qualityCounts': dict(sorted(Counter(r['Quality'] for r in records).items())),
                'worldforgedCount': sum(1 for r in records if r['IsWorldforged']),
                'notCaptured': ['scroll items', 'costs', 'altar levels', 'slot rules', 'class and spec requirements', 'required levels']}
    outputs = {'catalogue.json': gen.canonical({'metadata': metadata, 'enchants': records}) + '\n'}
    prefix = '-- Generated from the captured Mystic Enchant collection; costs and slot rules were never captured.\n'
    outputs['lua/Initialize.lua'] = prefix + 'ASC.Enchants.Begin(' + gen.lua(metadata) + ')\n'
    order = ['lua/Initialize.lua']
    chunk = prefix
    part = 1
    for record in records:
        line = 'ASC.Enchants.Add(' + gen.lua(record) + ')\n'
        if len((chunk + line).encode()) > gen.MAX_LUA_BYTES:
            name = 'lua/enchants-%03d.lua' % part
            outputs[name] = chunk
            order.append(name)
            part += 1
            chunk = prefix
        chunk += line
    name = 'lua/enchants-%03d.lua' % part
    outputs[name] = chunk
    order.append(name)
    outputs['lua/Finalize.lua'] = prefix + 'ASC.Enchants.Finalize()\n'
    order.append('lua/Finalize.lua')
    outputs['load-order.txt'] = '\n'.join(order) + '\n'
    outputs['generation.json'] = gen.canonical({'format': 1, 'metadata': metadata,
                                                'outputs': {k: hashlib.sha256(v.encode()).hexdigest() for k, v in sorted(outputs.items())}}) + '\n'
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--harvest-zip', type=Path, required=True)
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    with zipfile.ZipFile(args.harvest_zip) as archive:
        matches = [i for i in archive.infolist() if i.filename == MEMBER]
        if len(matches) != 1 or matches[0].file_size > 64_000_000:
            raise ValueError('missing, duplicate or oversized scrape member')
        raw = archive.read(matches[0])
    outputs = assemble(raw)
    gen.write_outputs(args.out, outputs, args.verify)
    print('Verified' if args.verify else 'Generated', len(outputs), 'files;',
          json.loads(outputs['generation.json'])['metadata']['enchantCount'], 'captured Mystic Enchants')


if __name__ == '__main__':
    main()
