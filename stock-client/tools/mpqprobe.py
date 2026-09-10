#!/usr/bin/env python3
"""Membership probe for listfile-less MPQ archives (WoW 3.3.5a, MPQ v1/v2).

Ascension's custom patch MPQs carry no (listfile), so nothing can *enumerate*
them -- but the hash table answers "is <name> in here?" for any name we can
spell.  This parses every archive's hash table once (decrypted in memory) and
then answers thousands of membership questions per second, which mpqfind.exe
(one process, all archives re-opened, per question) cannot.

    python mpqprobe.py <DataDir> --which  <name> [<name> ...]
    python mpqprobe.py <DataDir> --owners <listfile.txt>        one line per name
    python mpqprobe.py <DataDir> --count  <listfile.txt>        per-archive hit counts
    python mpqprobe.py <DataDir> --stats                        archive sizes / table sizes

Output for --which/--owners: name<TAB>archive-that-wins<TAB>all-archives-holding-it
Precedence is the client's: locale patch-enUS-N > patch-N > base, and among
lettered patches later letters win (patch-B over patch-A); numbered patches win
over lettered ones only within their tier.  The exact order the client uses is
argued in extract_tree.py; here it is made explicit in `precedence()` so a wrong
guess is one function to fix, not many.

Read-only.  Pure Python, no third-party modules.
"""
import os, struct, sys, glob

MPQ_HASH_TABLE_INDEX = 0
MPQ_HASH_NAME_A = 1
MPQ_HASH_NAME_B = 2
MPQ_HASH_FILE_KEY = 3

_crypt = None


def crypt_table():
    global _crypt
    if _crypt is not None:
        return _crypt
    tbl = [0] * 0x500
    seed = 0x00100001
    for i in range(0x100):
        idx = i
        for _ in range(5):
            seed = (seed * 125 + 3) % 0x2AAAAB
            t1 = (seed & 0xFFFF) << 0x10
            seed = (seed * 125 + 3) % 0x2AAAAB
            t2 = seed & 0xFFFF
            tbl[idx] = t1 | t2
            idx += 0x100
    _crypt = tbl
    return tbl


def hash_string(s, htype):
    tbl = crypt_table()
    seed1 = 0x7FED7FED
    seed2 = 0xEEEEEEEE
    for ch in s.upper().replace("/", "\\").encode("latin1", "replace"):
        seed1 = (tbl[(htype << 8) + ch] ^ ((seed1 + seed2) & 0xFFFFFFFF)) & 0xFFFFFFFF
        seed2 = (ch + seed1 + seed2 + (seed2 << 5) + 3) & 0xFFFFFFFF
    return seed1


def decrypt(data, key):
    tbl = crypt_table()
    seed = 0xEEEEEEEE
    out = bytearray(len(data))
    n = len(data) // 4
    vals = struct.unpack_from("<%dI" % n, data)
    res = []
    for v in vals:
        seed = (seed + tbl[0x400 + (key & 0xFF)]) & 0xFFFFFFFF
        ch = v ^ ((key + seed) & 0xFFFFFFFF)
        ch &= 0xFFFFFFFF
        key = (((~key << 0x15) + 0x11111111) | (key >> 0x0B)) & 0xFFFFFFFF
        seed = (ch + seed + (seed << 5) + 3) & 0xFFFFFFFF
        res.append(ch)
    struct.pack_into("<%dI" % n, out, 0, *res)
    return bytes(out)


class Archive:
    def __init__(self, path):
        self.path = path
        self.name = os.path.basename(path)
        with open(path, "rb") as f:
            head = f.read(0x200 * 64)
            # Header sits at a 512-byte boundary; WoW MPQs may carry a user-data block first.
            pos = 0
            hdr = None
            while pos + 32 <= len(head):
                sig = head[pos:pos + 4]
                if sig == b"MPQ\x1a":
                    hdr = pos
                    break
                if sig == b"MPQ\x1b":  # user data block, real header follows
                    pos += struct.unpack_from("<I", head, pos + 8)[0]
                    continue
                pos += 0x200
            if hdr is None:
                raise ValueError("no MPQ header in %s" % path)
            self.base = hdr
            (hsize, asize, fmt, block_shift, htpos, btpos, htsize, btsize) = struct.unpack_from(
                "<IIHHIIII", head, hdr + 4)
            self.version = fmt
            hthi = 0
            if fmt >= 1 and hsize >= 0x2C:
                ext_bt, hthi, bthi = struct.unpack_from("<QHH", head, hdr + 0x20)
            self.htpos = hdr + htpos + (hthi << 32)
            self.htsize = htsize
            f.seek(self.htpos)
            raw = f.read(htsize * 16)
        if len(raw) != htsize * 16:
            raise ValueError("short hash table in %s" % path)
        key = hash_string("(hash table)", MPQ_HASH_FILE_KEY)
        tbl = decrypt(raw, key)
        self.entries = struct.unpack_from("<%dI" % (htsize * 4), tbl)
        self.mask = htsize - 1
        self.files = sum(1 for i in range(htsize) if self.entries[i * 4 + 3] not in (0xFFFFFFFF, 0xFFFFFFFE))

    def has(self, name):
        h0 = hash_string(name, MPQ_HASH_TABLE_INDEX) & self.mask
        ha = hash_string(name, MPQ_HASH_NAME_A)
        hb = hash_string(name, MPQ_HASH_NAME_B)
        i = h0
        e = self.entries
        for _ in range(self.htsize):
            b = i * 4
            blk = e[b + 3]
            if blk == 0xFFFFFFFF:
                return False
            if blk != 0xFFFFFFFE and e[b] == ha and e[b + 1] == hb:
                return True
            i = (i + 1) & self.mask
        return False


def precedence(name):
    """Higher sorts first.  Mirrors the client's load order for 3.3.5a:
    locale patches beat general patches; within a family numbered patch-N and
    lettered patch-X are loaded after patch.MPQ with later letters/numbers last,
    and the last loaded archive wins."""
    # 3.3.5a (build 12340) loads, each later archive overriding earlier ones:
    #   1. the fixed base table (common, expansion, lichking, locale/speech, patch.MPQ,
    #      patch-<loc>.MPQ, patch-2, patch-<loc>-2, patch-3, patch-<loc>-3, ...)
    #   2. the glob set: templates "patch-?.MPQ" and "patch-%s-?.MPQ" ('?' = ONE
    #      character), all matches sorted case-insensitively on the full relative
    #      path and loaded in ascending order (loader 0x405AB0, comparator 0x401200,
    #      verified by the 2026-09-09 review).  "Data\enUS\patch-enUS-X" sorts before
    #      "Data\patch-X", so EVERY general lettered archive beats EVERY locale one,
    #      and within each group digits 4..9 precede letters A..Z.
    # Multi-letter names never match the template; Ascension's own loader adds
    # them, and plain string order after the single-letter ones is the only
    # defensible model for that (CA after C, WC2 after WC1, Z last).
    n = name.lower()
    stem = n[:-4]
    loc = 1 if "-enus" in stem else 0
    if stem == "patch" or stem == "patch-enus":
        return (1, 0, 0, "")
    if stem.startswith("patch-"):
        suffix = stem[6:].replace("enus", "").strip("-")
        if suffix in ("2", "3"):
            return (1, int(suffix), loc, "")
        # general (loc=0) beats locale (loc=1): rank general higher
        return (2, 1 - loc, 0, suffix.upper())
    return (0, 0, 0, "")


def open_all(datadir):
    paths = glob.glob(os.path.join(datadir, "*.MPQ")) + glob.glob(os.path.join(datadir, "*.mpq"))
    paths += glob.glob(os.path.join(datadir, "enUS", "*.MPQ")) + glob.glob(os.path.join(datadir, "enUS", "*.mpq"))
    seen, uniq = set(), []
    for p in paths:
        k = os.path.normcase(os.path.abspath(p))
        if k not in seen:
            seen.add(k)
            uniq.append(p)
    arcs = []
    for p in sorted(uniq):
        try:
            arcs.append(Archive(p))
        except Exception as ex:  # noqa
            print("!! %s: %s" % (p, ex), file=sys.stderr)
    arcs.sort(key=lambda a: precedence(a.name), reverse=True)
    return arcs


def owners(arcs, name):
    hits = [a.name for a in arcs if a.has(name)]
    return (hits[0] if hits else "", hits)


def main():
    if len(sys.argv) < 3:
        print(__doc__)
        return 2
    datadir = sys.argv[1]
    mode = sys.argv[2]
    arcs = open_all(datadir)
    if mode == "--stats":
        for a in arcs:
            print("%-22s v%d  hash-table %6d  files~%6d  %8.1f MB" % (
                a.name, a.version, a.htsize, a.files, os.path.getsize(a.path) / 1048576.0))
        return 0
    if mode == "--which":
        names = sys.argv[3:]
    else:
        names = [ln.strip() for ln in open(sys.argv[3], encoding="utf-8", errors="replace") if ln.strip() and not ln.startswith("#")]
    if mode in ("--which", "--owners"):
        for nm in names:
            win, hits = owners(arcs, nm)
            print("%s\t%s\t%s" % (nm, win, ",".join(hits)))
        return 0
    if mode == "--count":
        counts = {a.name: 0 for a in arcs}
        wins = {a.name: 0 for a in arcs}
        missing = 0
        for nm in names:
            win, hits = owners(arcs, nm)
            if not hits:
                missing += 1
                continue
            wins[win] += 1
            for h in hits:
                counts[h] += 1
        print("%-22s %8s %8s" % ("archive", "wins", "holds"))
        for a in arcs:
            if counts[a.name] or wins[a.name]:
                print("%-22s %8d %8d" % (a.name, wins[a.name], counts[a.name]))
        print("names %d, missing %d" % (len(names), missing))
        return 0
    print(__doc__)
    return 2


if __name__ == "__main__":
    sys.exit(main())
