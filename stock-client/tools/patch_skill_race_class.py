#!/usr/bin/env python3
"""Open Ascension's class skill lines to carrier classes in SkillRaceClassInfo.dbc.

    python patch_skill_race_class.py <in.dbc> <out.dbc> [--stock-classes 12]

Ascension's SkillRaceClassInfo gives every CoA class skill line (99 Demolition, 100
Invention, 102 Mechanics, ...) a ClassMask with only that CoA class's bit (bit 27 for
Tinker = class 28). On the stock-client port a CoA character is server-side a stock
carrier class, so AzerothCore's Player::SetSkill refuses the skill and the load path
deletes it ("invalid for the race/class combination"), and the spellbook never gets the
tab. This rewrites ClassMask to -1 (any class) on every row whose mask has NO bit below
<stock-classes>, i.e. rows that could never match a stock class anyway. Rows that already
admit a stock class are left alone. Idempotent; prints the rows it changed.
"""
import struct, sys


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    src, dst = sys.argv[1], sys.argv[2]
    stock = 12
    if "--stock-classes" in sys.argv:
        stock = int(sys.argv[sys.argv.index("--stock-classes") + 1])
    stock_mask = (1 << stock) - 1
    data = bytearray(open(src, "rb").read())
    magic, nrec, nfld, rsize, ssize = struct.unpack_from("<4sIIII", data, 0)
    assert magic == b"WDBC" and nfld == 8 and rsize == 32, "not a 3.3.5 SkillRaceClassInfo.dbc"
    changed = []
    for r in range(nrec):
        off = 20 + r * rsize
        rid, skill, race, cls = struct.unpack_from("<iiii", data, off)
        if cls != -1 and cls != 0 and (cls & stock_mask) == 0:
            struct.pack_into("<i", data, off + 12, -1)
            changed.append((rid, skill, cls))
    with open(dst, "wb") as f:
        f.write(data)
    for rid, skill, cls in changed:
        print("row %d skill %d: classmask %#x -> -1" % (rid, skill, cls & 0xFFFFFFFF))
    print("%s: %d of %d rows opened -> %s" % (src, len(changed), nrec, dst))
    return 0


if __name__ == "__main__":
    sys.exit(main())
