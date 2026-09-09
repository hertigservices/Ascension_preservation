"""Import the CoA tree's edge graph from the CoAReader SavedVariables.

`ConnectedNodes` is not a column in CharacterAdvancement.dbc and cannot be
recovered offline -- the engine parses it into a heap vector at entry+0xE8, so
the only source is a running client.  `CoAReader_DoEdges` (the `edges` step of
the Ascension_CoAReader autorun) harvests it into CoAReaderDB.edges; this reads
that back out and writes rexxar-reference/ca-dbc-export/edges.json.

    python tools/edges_import.py            # import + validate + write
    python tools/edges_import.py --check    # validate only, write nothing

The Lua is parsed by brace balance and a couple of regexes rather than with a
Lua interpreter: the file is a 437 KB flat dump and the `edges` table is a
uniform `["id"] = { n, ... }` block, so a real parser buys nothing here.
"""

import collections
import csv
import io
import json
import os
import re
import sys

BASE = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CLIENT = os.path.join(os.path.dirname(os.path.dirname(BASE)), "client-ascension")
SV = os.path.join(CLIENT, "WTF", "Account", "TEST", "SavedVariables",
                  "Ascension_CoAReader.lua")
EXPORT = os.path.join(BASE, "rexxar-reference", "ca-dbc-export")
OUT = os.path.join(EXPORT, "edges.json")
ENTRIES = os.path.join(EXPORT, "entries.csv")


def lua_block(src, key):
    """Slice `["key"] = { ... }` out of a SavedVariables dump by brace balance."""
    m = re.search(r'^\t\["%s"\] = \{' % re.escape(key), src, re.M)
    if not m:
        raise SystemExit("no [%r] block in %s -- run the `edges` probe first" % (key, SV))
    i = src.index("{", m.start())
    depth, j = 0, i
    while True:
        if src[j] == "{":
            depth += 1
        elif src[j] == "}":
            depth -= 1
            if depth == 0:
                return src[i:j + 1]
        j += 1


def load_edges():
    if not os.path.exists(SV):
        raise SystemExit("missing %s" % SV)
    with io.open(SV, encoding="utf-8", errors="replace") as f:
        src = f.read()
    block = lua_block(src, "edges")
    edges = {}
    for m in re.finditer(r'\["(\d+)"\] = \{(.*?)\n\t\t\}', block, re.S):
        edges[int(m.group(1))] = [int(x) for x in
                                  re.findall(r"^\s*(\d+),", m.group(2), re.M)]
    return edges


def load_entries():
    rows = {}
    if not os.path.exists(ENTRIES):
        return rows
    with io.open(ENTRIES, encoding="utf-8", errors="replace", newline="") as f:
        for r in csv.DictReader(f):
            try:
                rows[int(r["ID"])] = r
            except (KeyError, TypeError, ValueError):
                pass
    return rows


def num(row, col):
    try:
        return int(float(row.get(col) or 0))
    except (TypeError, ValueError):
        return 0


def validate(edges, rows):
    """Re-check the invariants SCHEMA.md documents.  Returns True if they hold."""
    total = sum(len(v) for v in edges.values())
    ok = True

    recip = sum(1 for a, vs in edges.items() for b in vs if a in edges.get(b, ()))
    print("nodes=%d edges=%d maxDegree=%d" %
          (len(edges), total, max((len(v) for v in edges.values()), default=0)))
    print("reciprocated endpoints: %d (expected 0 -- the graph is directed)" % recip)
    ok &= recip == 0

    if not rows:
        print("entries.csv missing or empty -- cannot validate direction / boundary checks")
        return False

    dy = collections.Counter()
    cross = 0
    for a, vs in edges.items():
        ra = rows.get(a)
        if not ra:
            print("missing source in entries.csv: %d" % a)
            ok = False
            continue
        for b in vs:
            rb = rows.get(b)
            if not rb:
                continue
            dy[num(rb, "PositionY") - num(ra, "PositionY")] += 1
            cross += (ra.get("ClassType"), ra.get("TabType")) !=                      (rb.get("ClassType"), rb.get("TabType"))
    print("delta PositionY: %s" % sorted(dy.items()))
    print("edges crossing a Class/Tab boundary: %d (expected 0)" % cross)
    ok &= cross == 0
    ok &= all(d <= 0 for d in dy)                 # never points down the tree

    involved = (set(edges) | {b for vs in edges.values() for b in vs}) & set(rows)
    roots = collections.Counter(num(rows[i], "PositionY")
                                for i in involved if i not in edges)
    print("PositionY of nodes with no ConnectedNodes: %s (expected {0: n})"
          % sorted(roots.items()))
    ok &= set(roots) == {0}

    dangling = sorted({b for vs in edges.values() for b in vs} - set(rows))
    print("dangling targets (drop on import): %s" % dangling)
    return ok


def main():
    edges = load_edges()
    ok = validate(edges, load_entries())
    if "--check" in sys.argv[1:]:
        print("check only -- nothing written")
    elif ok:
        with io.open(OUT, "w", encoding="utf-8") as f:
            json.dump({str(k): edges[k] for k in sorted(edges)}, f,
                      indent=0, sort_keys=True)
        print("wrote %s (%d bytes)" % (OUT, os.path.getsize(OUT)))
    if not ok:
        print("WARNING: an invariant documented in SCHEMA.md no longer holds")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
