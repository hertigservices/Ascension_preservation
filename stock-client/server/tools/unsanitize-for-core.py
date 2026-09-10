#!/usr/bin/env python3
"""Put back the Spell.dbc effect / aura values that sanitize-for-core.py zeroed.

sanitize-for-core.py neutralises effect ids >= TOTAL_SPELL_EFFECTS and aura ids >= TOTAL_AURAS
because a stock AzerothCore ASSERTs on them at startup, and journals every change (row id,
field, original value) in sanitized-for-core.json. With the enum-widening core patch applied
(server/core-patches/ascension-spell-enums.patch: effects 165..198, auras 317..366) the core
loads those ids, so this tool restores them from the journal.

Only the Spell.dbc rules are restored. The Achievement_Criteria.dbc drops stay dropped: the core
patch does not widen ACHIEVEMENT_CRITERIA_TYPE_TOTAL, and a criterion with an unknown type is
still an out-of-bounds write there.

    python unsanitize-for-core.py <dbc-dir> --journal <sanitized-for-core.json> [--max-effect 198] [--max-aura 366] [--dry-run]

Refuses to write a value the given bounds cannot hold, so it cannot recreate the crash it undoes.
A backup Spell.dbc.sanitized is written next to the file the first time it is rewritten.
"""
import argparse
import json
import shutil
import struct
from pathlib import Path


def header(blob):
    return struct.unpack_from('<4I', blob, 4)


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument('dbc_dir', type=Path)
    parser.add_argument('--journal', type=Path, required=True, help='sanitized-for-core.json written by sanitize-for-core.py')
    parser.add_argument('--max-effect', type=int, default=198, help='highest effect id the core now handles (TOTAL_SPELL_EFFECTS - 1)')
    parser.add_argument('--max-aura', type=int, default=366, help='highest aura id the core now handles (TOTAL_AURAS - 1)')
    parser.add_argument('--dry-run', action='store_true')
    args = parser.parse_args()

    journal = json.loads(args.journal.read_text(encoding='utf-8'))
    rules = journal.get('rules', {})
    path = args.dbc_dir / 'Spell.dbc'
    blob = bytearray(path.read_bytes())
    rows, fields, size, strings = header(blob)
    index = {}
    for r in range(rows):
        index[struct.unpack_from('<I', blob, 20 + r * size)[0]] = r

    restored, refused, missing = 0, 0, 0
    for key, limit in (('Spell.dbc:TOTAL_SPELL_EFFECTS', args.max_effect), ('Spell.dbc:TOTAL_AURAS', args.max_aura)):
        rule = rules.get(key)
        if not rule:
            print('journal has no rule', key)
            continue
        if rule.get('action') != 'zero':
            raise SystemExit('unexpected action %r for %s' % (rule.get('action'), key))
        for spell_id, field, value in rule['entries']:
            r = index.get(spell_id)
            if r is None:
                missing += 1
                continue
            if value > limit:
                refused += 1
                continue
            offset = 20 + r * size + field * 4
            current = struct.unpack_from('<I', blob, offset)[0]
            if current != 0 and current != value:
                raise SystemExit('spell %d field %d holds %d, expected 0 or %d; is this the sanitized file?' % (spell_id, field, current, value))
            if current != value:
                struct.pack_into('<I', blob, offset, value)
                restored += 1
    print('restored %d slot(s); refused %d above the given bounds; %d journal rows absent from this file' % (restored, refused, missing))
    if args.dry_run or restored == 0:
        return
    backup = path.with_suffix('.dbc.sanitized')
    if not backup.exists():
        shutil.copyfile(path, backup)
        print('backup:', backup)
    path.write_bytes(blob)
    print('rewritten:', path)


if __name__ == '__main__':
    main()
