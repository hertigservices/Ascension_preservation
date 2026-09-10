#!/usr/bin/env python3
"""Map Ascension's multi-letter patch archives onto names a STOCK 3.3.5a client loads.

The stock loader iterates one character over two templates, general then locale,
lowest first, later archives overriding earlier ones:

    patch-4.MPQ, patch-enUS-4.MPQ, patch-5.MPQ, patch-enUS-5.MPQ, ... patch-9,
    patch-A.MPQ, patch-enUS-A.MPQ, ... patch-Z.MPQ, patch-enUS-Z.MPQ

Ascension's own loader (Extensions.dll) also loads multi-letter names
(patch-CA..CZZ, CHA, TA/TM/TW, WA/WB*/WC*).  The measured file overlaps
(mpqoverlap.py, 2026-09-09) all have the single-letter archive winning under
plain alphabetical load order (U over TA, Q over CHA, V over C*, Z over WC*),
and the shared files DIFFER byte-for-byte (mpq_compare.py), so order matters.

plan() assigns each multi-letter archive a free single-character slot such that
  * multi-letter archives keep their mutual alphabetical order, and
  * every archive loads BEFORE any single-letter archive it shares files with
    and that sorts after it alphabetically (so the single-letter copy still wins).
Constraints come from the overlap list produced by mpqoverlap.py.

    python mpq_slots.py <DataDir> <overlap.txt>     print the plan
"""
import os, re, sys

CHARS = list("456789ABCDEFGHIJKLMNOPQRSTUVWXYZ")


def chain():
    """Stock load order of the templated slots, lowest priority first.

    The loader globs "patch-?.MPQ" and "patch-%s-?.MPQ", sorts every match on its
    full relative path (case-insensitive) and loads ascending, so ALL locale slots
    ("Data\\enUS\\patch-enUS-?") come before ALL general slots ("Data\\patch-?"),
    digits before letters within each group (review of 2026-09-09, loader 0x405AB0)."""
    out = []
    for c in CHARS:
        out.append("patch-enUS-%s.MPQ" % c)
    for c in CHARS:
        out.append("patch-%s.MPQ" % c)
    return out


def suffix(name):
    n = name.lower()
    if not n.startswith("patch-") or not n.endswith(".mpq"):
        return None
    s = n[6:-4]
    if s.startswith("enus-"):
        return None
    return s.upper()


def is_multi(name):
    s = suffix(name)
    return s is not None and len(s) > 1


def parse_overlaps(path):
    pairs = []
    for ln in open(path, encoding="utf-8", errors="replace"):
        m = re.match(r"\s*(\d+)\s+(\S+)\s+<->\s+(\S+)", ln)
        if m:
            pairs.append((int(m.group(1)), m.group(2), m.group(3)))
    return pairs


def plan(archives, overlaps, excluded=()):
    """archives: list of archive file names present in Ascension's Data dir.
    Returns (mapping {multi_name: slot_name}, notes[])."""
    present = [a for a in archives if suffix(a) is not None and a not in excluded]
    singles = {a for a in present if not is_multi(a)}
    multis = sorted((a for a in present if is_multi(a)), key=lambda a: suffix(a))
    order = chain()
    pos = {name.lower(): i for i, name in enumerate(order)}
    taken = {i for name, i in pos.items() if any(name == s.lower() for s in singles)}
    # ChromieCraft's patch-enUS-4 is excluded from the assembly, so its slot is free.
    # Constraint: for multi M and single S with overlap, if S sorts after M then slot(M) < slot(S).
    must_precede = {m: [] for m in multis}
    notes = []
    for n, x, y in overlaps:
        for m, s in ((x, y), (y, x)):
            if m in must_precede and s in singles:
                if suffix(s) > suffix(m):
                    must_precede[m].append((s, n))
                elif s.lower() in pos:
                    # a template-slot single that sorts BEFORE m: any slot after it keeps Ascension's order
                    notes.append("%s shares %d files with %s and loads after it under Ascension's order; slot kept after %s" % (m, n, s, s))
                # numbered stock patches (patch, patch-2, patch-3) load before every template slot: nothing to do
    mapping = {}
    last = -1
    free = [i for i in range(len(order)) if i not in taken]
    for m in multis:
        limit = min((pos[s.lower()] for s, _ in must_precede[m]), default=len(order))
        # after-constraints: single archives that sort before m and overlap it
        lower = last
        for n, x, y in overlaps:
            for mm, s in ((x, y), (y, x)):
                # numbered stock patches (patch, patch-2, patch-3) load before every template slot
                if mm == m and s in singles and s.lower() in pos and suffix(s) < suffix(m):
                    lower = max(lower, pos[s.lower()])
        cand = [i for i in free if i > lower and i < limit]
        if not cand:
            notes.append("NO SLOT for %s (needs a slot after %d and before %d)" % (m, lower, limit))
            continue
        i = cand[0]
        free.remove(i)
        mapping[m] = order[i]
        last = i
    return mapping, notes


def main():
    datadir, overlap = sys.argv[1], sys.argv[2]
    archives = sorted(os.listdir(datadir))
    mapping, notes = plan(archives, parse_overlaps(overlap), excluded=("patch-B.MPQ",))
    for m in sorted(mapping, key=lambda a: suffix(a)):
        print("%-16s -> %s" % (m, mapping[m]))
    for n in notes:
        print("NOTE:", n)


if __name__ == "__main__":
    main()
