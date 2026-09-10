#!/usr/bin/env python3
"""Exact file overlap between MPQ archives WITHOUT knowing any file names.

A hash-table entry is (hashA, hashB, locale, platform, block).  The two name
hashes depend only on the file name, so two archives contain the same path iff
they both carry an entry with the same (hashA, hashB).  Intersecting the entry
sets gives the true overlap matrix, which is what decides whether the LOAD
ORDER of two archives matters (it matters only for files they share).

    python mpqoverlap.py <DataDir> [--min 1] [--focus patch-]

Prints archive sizes (entries), then every pair with a non-empty intersection.
Read-only.
"""
import os, sys, importlib.util

HERE = os.path.dirname(os.path.abspath(__file__))
spec = importlib.util.spec_from_file_location("mpqprobe", os.path.join(HERE, "mpqprobe.py"))
mp = importlib.util.module_from_spec(spec); spec.loader.exec_module(mp)


SPECIAL = {(mp.hash_string(n, 1), mp.hash_string(n, 2))
           for n in ("(listfile)", "(attributes)", "(signature)", "(patch_metadata)")}


def entry_set(a):
    e = a.entries
    s = set()
    for i in range(a.htsize):
        b = i * 4
        blk = e[b + 3]
        if blk in (0xFFFFFFFF, 0xFFFFFFFE):
            continue
        key = (e[b], e[b + 1])
        if key in SPECIAL:
            continue
        s.add(key)
    return s


def main():
    datadir = sys.argv[1]
    minv = int(sys.argv[sys.argv.index("--min") + 1]) if "--min" in sys.argv else 1
    focus = sys.argv[sys.argv.index("--focus") + 1].lower() if "--focus" in sys.argv else ""
    arcs = mp.open_all(datadir)
    sets = {a.name: entry_set(a) for a in arcs}
    print("%-26s %8s" % ("archive", "files"))
    for a in arcs:
        print("%-26s %8d" % (a.name, len(sets[a.name])))
    names = [a.name for a in arcs]
    print("\nOVERLAPS (files shared; later-loaded wins):")
    rows = []
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            x, y = names[i], names[j]
            if focus and not (x.lower().startswith(focus) or y.lower().startswith(focus)):
                continue
            n = len(sets[x] & sets[y])
            if n >= minv:
                rows.append((n, x, y))
    rows.sort(reverse=True)
    for n, x, y in rows:
        print("%8d  %-22s <-> %-22s" % (n, x, y))
    print("%d overlapping pairs" % len(rows))


if __name__ == "__main__":
    main()
