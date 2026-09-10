#!/usr/bin/env python3
"""Recover the Vanity and Wardrobe (transmogrification) catalogues into JSON and Lua for the pack.

Source: the three collection DBCs Ascension's Extensions.dll reads natively and that no capture
of the live client ever produced (the client's own Transmogrification*.json files are 0 bytes):
`VanityCollection.dbc` (10,678 rows), `Appearances.dbc` (42,884) and `ItemAppearances.dbc`
(202,913). They surfaced in jealous-sound's public CoA server repack (2026-09-10,
`Core/Data/dbc/Ascension/`, "extracted from this client build"); the hub keeps them under
`research/coa-repack/` and `server-coa/Data/dbc/Ascension/`.

Column meanings were established by joining the DBCs against the harvested item catalogue
(`--items-tsv`, an export of item_template) and by jealous-sound's loader
(`AscensionCompat.cpp`, AscensionCollectionService::LoadClientData); they are recorded in
`catalogue.json` metadata as `columns`. What the DBCs do NOT carry, and is therefore absent
or clearly marked as ours: vanity artwork paths, seasonal/bazaar prices (only the webstore
donation price column exists), Ascension's own names and icons for the appearance categories
(slot categories 1..14 use the stock slot strings, the rest are labelled from the items they
hold), and anything about which appearances or vanity items a character owns.

    python gen_collection_data.py --dbc-dir <dir with the three DBCs> --items-tsv <item export>
                                  --itemdisplayinfo-dbc <ItemDisplayInfo.dbc> --out data/collections [--verify]

The item export is `entry, class, subclass, InventoryType, Quality, displayid, name` (tab
separated, no header), e.g.
    mysql -N -B -e "select entry,class,subclass,InventoryType,Quality,displayid,name from item_template" coa2_world
ItemDisplayInfo.dbc must be Ascension's (its display ids are renumbered against stock).
"""
import argparse
import hashlib
import json
import struct
from collections import Counter, defaultdict
from pathlib import Path
import gen_ca_data as gen

KEY = 'coa-repack-collections-20260825'

# Enum.VanityCategory bits (SharedXML/Enum.lua) -> Ascension_VanityCollection's Store.GroupIcons id.
# The addon keys its group icons by these small ids; the mapping from category bit to icon id is
# ours (the DBC carries only the bit mask) and is recorded in the metadata.
GROUP_BY_BIT = [(0x4000000, 3, 'Mounts'), (0x8000000, 4, 'Pets'), (0x10000000, 5, 'Toys'),
                (0x200000, 7, 'Appearances'), (0x800000, 7, 'Appearances'), (0x100000, 8, 'Weapons'),
                (0x1000000, 4, 'Pets'), (0x400000, 17, 'Incarnations'), (0x2000000, 15, 'Ascension Exclusives'),
                (0x20000000, 15, 'Ascension Exclusives'), (0x40000000, 15, 'Ascension Exclusives'),
                (0x80000000, 15, 'Ascension Exclusives')]
INCARNATION_MASK = 0x400004

# Appearance category id (Appearances.dbc field 5) -> stock slot string key, icon, type. Ids 1..14
# are the ones Ascension_AppearanceUI itself tests (IsArmorCategory 1..11, ranged 12, main hand 13,
# off hand 14); the join against item_template confirmed the slot of each.
SLOT_CATEGORIES = {
    1: ('HEADSLOT', 'inv_helmet_03'), 2: ('SHOULDERSLOT', 'inv_shoulder_02'), 3: ('BACKSLOT', 'inv_misc_cape_02'),
    4: ('CHESTSLOT', 'inv_chest_cloth_07'), 5: ('TABARDSLOT', 'inv_shirt_guildtabard_01'), 6: ('SHIRTSLOT', 'inv_shirt_01'),
    7: ('WRISTSLOT', 'inv_bracer_02'), 8: ('HANDSSLOT', 'inv_gauntlets_04'), 9: ('WAISTSLOT', 'inv_belt_03'),
    10: ('LEGSSLOT', 'inv_pants_02'), 11: ('FEETSLOT', 'inv_boots_05'), 12: ('RANGEDSLOT', 'inv_weapon_bow_07'),
    13: ('MAINHANDSLOT', 'inv_sword_04'), 14: ('SECONDARYHANDSLOT', 'inv_shield_04'),
}
TYPE_BY_CATEGORY = {15: 'APPEARANCE_TYPE_ILLUSION', 56: 'APPEARANCE_TYPE_COSMETIC', 62: 'APPEARANCE_TYPE_MOUNT',
                    63: 'APPEARANCE_TYPE_COMPANION', 64: 'APPEARANCE_TYPE_TOY'}
SKIPPED_CATEGORIES = {0: 'unassigned', 55: 'QA test items (names begin "QA PVP Test")'}


def read_dbc(path, expect_fields=None):
    blob = Path(path).read_bytes()
    magic, rows, fields, size, strings = struct.unpack_from('<4sIIII', blob, 0)
    if magic != b'WDBC' or size % 4:
        raise ValueError('not a WDBC file: %s' % path)
    physical = size // 4
    if expect_fields is not None and physical != expect_fields:
        raise ValueError('%s: expected %d dword fields, header says %d, record size says %d' % (path, expect_fields, fields, physical))
    body = blob[20:20 + rows * size]
    table = blob[20 + rows * size:20 + rows * size + strings]
    records = [struct.unpack_from('<%dI' % physical, body, r * size) for r in range(rows)]
    return records, table, hashlib.sha256(blob).hexdigest(), len(blob)


def dbc_string(table, offset):
    if not offset:
        return ''
    end = table.find(b'\0', offset)
    return table[offset:end if end >= 0 else None].decode('utf-8', 'replace')


def read_items(path):
    items = {}
    for line in Path(path).read_text(encoding='utf-8', errors='replace').splitlines():
        parts = line.split('\t')
        if len(parts) < 7 or not parts[0].isdigit():
            continue
        entry = int(parts[0])
        if entry in items:
            raise ValueError('duplicate item %d in the item export' % entry)
        items[entry] = {'class': int(parts[1]), 'subclass': int(parts[2]), 'inventoryType': int(parts[3]),
                        'quality': int(parts[4]), 'displayid': int(parts[5]), 'name': '\t'.join(parts[6:]).strip()}
    if len(items) < 100000:
        raise ValueError('the item export has only %d rows; expected the full Ascension item catalogue' % len(items))
    return items


def read_icons(path):
    records, table, digest, size = read_dbc(path, 25)
    icons = {}
    for r in records:
        icon = dbc_string(table, r[5])
        if icon:
            icons[r[0]] = icon
    return icons, {'file': 'ItemDisplayInfo.dbc', 'rows': len(records), 'sha256': digest, 'bytes': size}


def group_for(mask):
    for bit, group, _ in GROUP_BY_BIT:
        if mask & bit:
            return group
    return 15


def vanity_records(dbc_dir, items, icons):
    records, table, digest, size = read_dbc(Path(dbc_dir) / 'VanityCollection.dbc', 77)
    out = []
    seen = set()
    unresolved = 0
    for r in records:
        item_id = r[1]
        if not item_id or item_id in seen:
            continue
        seen.add(item_id)
        item = items.get(item_id)
        if item is None:
            unresolved += 1
        contents = [v for v in r[18:27] if v]
        rec = {'itemid': item_id, 'name': item['name'] if item else 'Item %d' % item_id,
               'quality': item['quality'] if item else 1,
               'icon': (icons.get(item['displayid']) if item else None) or 'INV_Misc_QuestionMark',
               'category': r[2], 'group': group_for(r[2]), 'flags': r[12], 'description': dbc_string(table, r[42]),
               'dpCost': r[5], 'spCost': 0, 'btCost': 0, 'creaturePreview': r[16], 'contentsPreview': contents,
               'learnedSpell': r[76]}
        if r[3]:
            rec['ownerHint'] = r[3]  # column 3: 824 distinct values, mostly item ids; meaning not established
        if r[59]:
            rec['column59'] = r[59]  # 703 distinct values; meaning not established, kept for later work
        out.append(rec)
    out.sort(key=lambda x: x['itemid'])
    return out, unresolved, {'file': 'VanityCollection.dbc', 'rows': len(records), 'sha256': digest, 'bytes': size}


def label_from_names(names):
    """Most common 'Prefix:' among the category's item names, when at least 80% share it."""
    prefixes = Counter(n.split(':', 1)[0].strip() for n in names if ':' in n)
    if not prefixes:
        return None
    prefix, count = prefixes.most_common(1)[0]
    if count * 5 >= len(names) * 4 and 2 <= len(prefix) <= 40:
        return prefix
    return None


def appearance_records(dbc_dir, items, icons):
    records, table, digest, size = read_dbc(Path(dbc_dir) / 'Appearances.dbc', 17)
    by_category = defaultdict(list)
    out = []
    unresolved = 0
    for r in records:
        app_id, item_id, type_code, category, secondary, alt_item = r[0], r[1], r[2], r[5], r[6], r[8]
        if not app_id or category in SKIPPED_CATEGORIES:
            continue
        item = items.get(item_id)
        if item is None:
            unresolved += 1
        rec = {'id': app_id, 'item': item_id, 'category': category, 'typeCode': type_code, 'secondary': secondary,
               'name': item['name'] if item else 'Item %d' % item_id, 'quality': item['quality'] if item else 1}
        if item:
            rec['inv'] = item['inventoryType']
            icon = icons.get(item['displayid'])
            if icon:
                rec['icon'] = icon
        if alt_item and alt_item != item_id:
            rec['sourceItem'] = alt_item
        if r[3] and r[3] != item_id:
            rec['display'] = r[3]  # differs from the item id on 1,790 rows; a creature/spell display id there
        if r[11] != 100:
            rec['scale'] = r[11]
        out.append(rec)
        by_category[category].append(rec)
    out.sort(key=lambda x: x['id'])
    categories = []
    for category in sorted(by_category):
        recs = by_category[category]
        if category in SLOT_CATEGORIES:
            key, icon = SLOT_CATEGORIES[category]
            categories.append({'id': category, 'type': 'APPEARANCE_TYPE_ITEM', 'nameKey': key, 'icon': icon, 'count': len(recs),
                               'labelSource': 'stock slot string'})
            continue
        label = label_from_names([x['name'] for x in recs])
        app_type = TYPE_BY_CATEGORY.get(category)
        if app_type is None:
            app_type = 'APPEARANCE_TYPE_INCARNATION' if 17 <= category <= 32 else 'APPEARANCE_TYPE_COSMETIC'
        categories.append({'id': category, 'type': app_type, 'name': label or 'Category %d' % category,
                           'icon': 'INV_Misc_QuestionMark', 'count': len(recs),
                           'labelSource': 'item name prefix' if label else 'placeholder'})
    return out, categories, unresolved, {'file': 'Appearances.dbc', 'rows': len(records), 'sha256': digest, 'bytes': size}


def item_appearance_pairs(dbc_dir, known_appearances):
    records, table, digest, size = read_dbc(Path(dbc_dir) / 'ItemAppearances.dbc', 3)
    pairs = []
    dropped = 0
    for r in records:
        if r[1] and r[2] in known_appearances:
            pairs.append((r[1], r[2]))
        else:
            dropped += 1
    pairs.sort()
    return pairs, dropped, {'file': 'ItemAppearances.dbc', 'rows': len(records), 'sha256': digest, 'bytes': size}


def chunked(prefix, records, name, call, outputs, order):
    part = 1
    chunk = prefix
    for record in records:
        line = call(record)
        if len((chunk + line).encode()) > gen.MAX_LUA_BYTES:
            fname = 'lua/%s-%03d.lua' % (name, part)
            outputs[fname] = chunk
            order.append(fname)
            part += 1
            chunk = prefix
        chunk += line
    fname = 'lua/%s-%03d.lua' % (name, part)
    outputs[fname] = chunk
    order.append(fname)


def assemble(dbc_dir, items_tsv, itemdisplayinfo):
    items = read_items(items_tsv)
    icons, idi_source = read_icons(itemdisplayinfo)
    vanity, vanity_unresolved, vanity_source = vanity_records(dbc_dir, items, icons)
    appearances, categories, app_unresolved, app_source = appearance_records(dbc_dir, items, icons)
    pairs, pairs_dropped, pairs_source = item_appearance_pairs(dbc_dir, {a['id'] for a in appearances})
    metadata = {
        'format': 1, 'key': KEY, 'kind': 'coa-repack-collection-dbcs', 'capturedDate': '2026-08-25',
        'provenance': 'jealous-sound CoA repack Core/Data/dbc/Ascension/ (public release 2026-09-10); the DBC files are dated 2026-08-25',
        'vanityCount': len(vanity), 'appearanceCount': len(appearances), 'itemAppearanceCount': len(pairs),
        'categoryCount': len(categories),
        'sources': [vanity_source, app_source, pairs_source, idi_source],
        'itemExport': {'rows': len(items), 'sha256': hashlib.sha256(Path(items_tsv).read_bytes()).hexdigest()},
        'unresolved': {'vanityItems': vanity_unresolved, 'appearanceItems': app_unresolved, 'itemAppearanceRowsDropped': pairs_dropped},
        'skippedCategories': {str(k): v for k, v in SKIPPED_CATEGORIES.items()},
        'columns': {
            'VanityCollection.dbc': {'1': 'item id', '2': 'Enum.VanityCategory mask', '3': 'unknown (kept as ownerHint)',
                                     '5': 'webstore donation-point price', '12': 'Enum.VanityFlags', '16': 'creature preview entry',
                                     '18..26': 'contents preview item ids', '42': 'description', '59': 'unknown', '76': 'learned spell'},
            'Appearances.dbc': {'0': 'appearance id', '1': 'item id', '2': 'type code', '3': 'display id', '5': 'category',
                                '6': 'secondary category', '8': 'source item id', '11': 'scale percent',
                                'inv/icon': 'not DBC columns: the item export\'s InventoryType and the ItemDisplayInfo icon'},
            'ItemAppearances.dbc': {'0': 'row id', '1': 'item id', '2': 'appearance id'},
        },
        'ours': ['vanity group icon ids (GROUP_BY_BIT)', 'category labels and icons outside slots 1..14', 'appearance type per category',
                 'spCost/btCost = 0 (no seasonal or bazaar price column exists)', 'artwork = none (not in the DBC)'],
        'notCaptured': ['ownership', 'vanity artwork', 'seasonal and bazaar prices', "Ascension's category names and icons", 'outfits', 'item sets as appearances'],
        'groupIcons': [{'bit': b, 'group': g, 'label': l} for b, g, l in GROUP_BY_BIT],
    }
    outputs = {'catalogue.json': gen.canonical({'metadata': metadata, 'categories': categories, 'vanity': vanity,
                                                'appearances': appearances, 'itemAppearances': [list(p) for p in pairs]}) + '\n'}
    prefix = "-- Generated from the CoA repack's collection DBCs (tools/gen_collection_data.py); ownership and prices were never captured.\n"
    outputs['lua/Initialize.lua'] = prefix + 'ASC.Collections.Begin(' + gen.lua(metadata) + ')\n'
    for c in categories:
        outputs['lua/Initialize.lua'] += 'ASC.Collections.AddCategory(' + gen.lua(c) + ')\n'
    order = ['lua/Initialize.lua']
    chunked(prefix, vanity, 'vanity', lambda r: 'ASC.Collections.AddVanity(' + gen.lua(r) + ')\n', outputs, order)
    chunked(prefix, appearances, 'appearances', lambda r: 'ASC.Collections.AddAppearance(' + gen.lua(r) + ')\n', outputs, order)
    # flat {item, appearance, item, appearance, ...} arrays, 2,000 pairs per call
    groups = [pairs[i:i + 2000] for i in range(0, len(pairs), 2000)]
    chunked(prefix, groups, 'itemappearances', lambda g: 'ASC.Collections.MapItems({' + ','.join('%d,%d' % p for p in g) + '})\n', outputs, order)
    outputs['lua/Finalize.lua'] = prefix + 'ASC.Collections.Finalize()\n'
    order.append('lua/Finalize.lua')
    outputs['load-order.txt'] = '\n'.join(order) + '\n'
    outputs['generation.json'] = gen.canonical({'format': 1, 'metadata': metadata,
                                                'outputs': {k: hashlib.sha256(v.encode()).hexdigest() for k, v in sorted(outputs.items())}}) + '\n'
    return outputs


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('--dbc-dir', type=Path, required=True, help='directory holding VanityCollection.dbc, Appearances.dbc, ItemAppearances.dbc')
    parser.add_argument('--items-tsv', type=Path, required=True, help='item_template export: entry, class, subclass, InventoryType, Quality, displayid, name')
    parser.add_argument('--itemdisplayinfo-dbc', type=Path, required=True, help="Ascension's ItemDisplayInfo.dbc (icon names)")
    parser.add_argument('--out', type=Path, required=True)
    parser.add_argument('--verify', action='store_true')
    args = parser.parse_args()
    outputs = assemble(args.dbc_dir, args.items_tsv, args.itemdisplayinfo_dbc)
    gen.write_outputs(args.out, outputs, args.verify)
    meta = json.loads(outputs['generation.json'])['metadata']
    print('Verified' if args.verify else 'Generated', len(outputs), 'files;', meta['vanityCount'], 'vanity items,',
          meta['appearanceCount'], 'appearances in', meta['categoryCount'], 'categories,', meta['itemAppearanceCount'], 'item mappings')


if __name__ == '__main__':
    main()
