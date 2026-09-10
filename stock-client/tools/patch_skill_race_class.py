#!/usr/bin/env python3
"""Open skill lines to every class in SkillRaceClassInfo.dbc.

    python patch_skill_race_class.py <in.dbc> <out.dbc> [--stock-classes 12]
                                     [--skillline <SkillLine.dbc> --open-categories 6,7,8]

Default (CoA carriers): Ascension's SkillRaceClassInfo gives every CoA class skill line
(99 Demolition, 100 Invention, 102 Mechanics, ...) a ClassMask with only that CoA class's
bit (bit 27 for Tinker = class 28). On the stock-client port a CoA character is
server-side a stock carrier class, so AzerothCore's Player::SetSkill refuses the skill and
the load path deletes it ("invalid for the race/class combination"), and the spellbook
never gets the tab. This rewrites ClassMask to -1 (any class) on every row whose mask has
NO bit below <stock-classes>, i.e. rows that could never match a stock class anyway.

--open-categories (Free-Pick / Hero): a Hero picks abilities from all ten stock classes
and, like Ascension's Heroes, can use every weapon and wear every armour. With
SkillLine.dbc at hand, every row whose skill belongs to the given SkillLine categories
(6 weapon, 7 class, 8 armour) gets ClassMask = -1 and RaceMask = -1, so
Player::_LoadSkills keeps them for any race/class. Rows of other categories
(professions, secondary skills, languages) are left alone.

Idempotent; prints the rows it changed.
"""
import struct, sys


def read_categories(path):
    data = open(path, "rb").read()
    magic, nrec, nfld, rsize, ssize = struct.unpack_from("<4sIIII", data, 0)
    assert magic == b"WDBC", "not a DBC: %s" % path
    cats = {}
    for r in range(nrec):
        row = struct.unpack_from("<ii", data, 20 + r * rsize)
        cats[row[0]] = row[1]
    return cats


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    if len(args) < 2:
        print(__doc__)
        return 2
    src, dst = args[0], args[1]
    stock = 12
    if "--stock-classes" in sys.argv:
        stock = int(sys.argv[sys.argv.index("--stock-classes") + 1])
    skillline = sys.argv[sys.argv.index("--skillline") + 1] if "--skillline" in sys.argv else None
    open_cats = set()
    if "--open-categories" in sys.argv:
        open_cats = {int(x) for x in sys.argv[sys.argv.index("--open-categories") + 1].split(",") if x}
        if not skillline:
            print("--open-categories needs --skillline <SkillLine.dbc>")
            return 2
    cats = read_categories(skillline) if skillline else {}
    stock_mask = (1 << stock) - 1
    data = bytearray(open(src, "rb").read())
    magic, nrec, nfld, rsize, ssize = struct.unpack_from("<4sIIII", data, 0)
    assert magic == b"WDBC" and nfld == 8 and rsize == 32, "not a 3.3.5 SkillRaceClassInfo.dbc"
    changed = []
    for r in range(nrec):
        off = 20 + r * rsize
        rid, skill, race, cls = struct.unpack_from("<iiii", data, off)
        why = None
        if cls != -1 and cls != 0 and (cls & stock_mask) == 0:
            why = "no stock class bit"
        if open_cats and cats.get(skill) in open_cats and (cls != -1 or race != -1):
            why = "category %d" % cats.get(skill)
            struct.pack_into("<i", data, off + 8, -1)
        if why:
            struct.pack_into("<i", data, off + 12, -1)
            changed.append((rid, skill, race, cls, why))
    with open(dst, "wb") as f:
        f.write(data)
    for rid, skill, race, cls, why in changed:
        print("row %d skill %d: racemask %#x classmask %#x -> -1 (%s)" % (rid, skill, race & 0xFFFFFFFF, cls & 0xFFFFFFFF, why))
    print("%s: %d of %d rows opened -> %s" % (src, len(changed), nrec, dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
