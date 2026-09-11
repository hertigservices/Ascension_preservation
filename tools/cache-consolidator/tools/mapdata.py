# -*- coding: utf-8 -*-
"""Inventory submitted SERVER map data: maps, vmaps, mmaps, Cameras and dbc.

    python tools/mapdata.py            # inventory every map-data tree found
    python tools/mapdata.py --list     # say what was found, write nothing

WHY THIS EXISTS

    A 2 GB submission of extracted terrain arrived, unpacked cleanly, reported
    `ok`, and contributed nothing. Not because it was rejected -- because
    `intake.py` ledgers `*.wdb` and nothing else, and no other stage had a
    reader for a `.map` file either. The whole pipeline is allow-lists, which is
    the right design for a public dataset, but it means an unrecognised
    submission is indistinguishable from an empty one. This stage is the reader
    that class of submission never had.

WHAT IT PUBLISHES, AND WHAT IT DELIBERATELY DOES NOT

    It publishes the INVENTORY, never the terrain. Which map ids exist, how many
    tiles each has, whether vmaps and mmaps were built for them, and what the
    accompanying DBC set is -- a few hundred rows of text. The payload itself is
    gigabytes of derived binary that a server operator regenerates from their
    own client with the core's own extractors; putting it in a git repository
    would be both enormous and the wrong artefact.

    So the useful, small, citable fact is "map 884 exists and is called
    Depthbreaker's Hollow, with 4 tiles of terrain and no mmaps" -- and that is
    what comes out.

NAMING THE IDS

    A submission's own `dbc/Map.dbc` is usually the STOCK one, which defines
    none of the custom ids whose terrain is the interesting part. Every Map.dbc
    reachable under extracted/ is read, plus any `map_dbc_refs` in the work
    config, and the union is used as a lookup. An id no reference names is
    still reported -- unnamed, and counted as such, because "we have terrain
    nobody can identify" is a finding rather than a gap to hide.

    A reference is a LOOKUP, never a source of rows. Nothing is invented for an
    id that has no tiles on disk.
"""
import argparse
import io
import json
import os
import re
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config

OUT_DIR = os.path.join(config.OUT, "mapdata").replace("\\", "/")
LEDGER = os.path.join(config.WORK, "mapdata.json").replace("\\", "/")

# Written by intake.py into each extraction. Read, never written, here.
SRCMARK = ".intake-source"
SRCHASH = ".intake-sha256"

# Tile filenames the core's extractors produce.
#
# The map id is NOT a fixed three digits, and assuming it is quietly destroys
# the answer rather than failing: this archive holds both `0002035.map` (map 2)
# and `10013030.map` (map 1001) in one directory, and reading the first three
# characters of the second gives map 100 -- an id that does not exist, sitting
# plausibly among ids that do. So the concatenated names are parsed from the
# RIGHT: the last four digits are always the tile's x and y, and whatever
# precedes them is the id, however many digits that takes.
MAPTILE = re.compile(r"^(\d{5,})\.map$", re.I)
MMTILE = re.compile(r"^(\d{5,})\.mmtile$", re.I)
# These two are separator-delimited or bare, so they need no such care -- but
# they still must not assume a width.
VMTILE = re.compile(r"^(\d+)_(\d{2})_(\d{2})\.vmtile$", re.I)
VMTREE = re.compile(r"^(\d+)\.vmtree$", re.I)
MMAP = re.compile(r"^(\d+)\.mmap$", re.I)


def tile_map_id(digits):
    """Map id from a concatenated <id><xx><yy> tile name."""
    return int(digits[:-4])

# Stock 3.3.5a tops out at 724. Above it is Ascension's own range -- a rule of
# thumb for reporting only, never used to include or exclude a row.
STOCK_MAX = 724


# ------------------------------------------------------------------ DBC
def dbc_header(path):
    """(records, fields, recsize, stringblock) or None if it is not a DBC.

    Deliberately reads 20 bytes and stops. The point is to tell an Ascension
    DBC from a stock one by its shape, and a row count does that; decoding the
    body would mean knowing 250 different layouts.
    """
    try:
        with open(path, "rb") as f:
            head = f.read(20)
    except OSError:
        return None
    if len(head) < 20 or head[:4] != b"WDBC":
        return None
    return struct.unpack_from("<IIII", head, 4)


def read_map_dbc(path):
    """{map id: (directory, instance type, name)} from one Map.dbc.

    Returns {} rather than raising for anything unreadable: a reference file is
    a convenience, and a malformed one must not take the stage down with it.
    """
    try:
        with open(path, "rb") as f:
            raw = f.read()
    except OSError:
        return {}
    if len(raw) < 20 or raw[:4] != b"WDBC":
        return {}
    nrec, nfield, recsize, ssize = struct.unpack_from("<IIII", raw, 4)
    hdr = 20
    body = hdr + nrec * recsize
    if nfield < 6 or recsize < nfield * 4 or body + ssize > len(raw):
        return {}
    strings = raw[body:body + ssize]

    def s(off):
        if off <= 0 or off >= len(strings):
            return ""
        end = strings.find(b"\x00", off)
        if end < 0:
            end = len(strings)
        return strings[off:end].decode("utf-8", "replace")

    out = {}
    for i in range(nrec):
        f = struct.unpack_from("<%dI" % nfield, raw, hdr + i * recsize)
        # 3.3.5a Map.dbc: 0 ID, 1 Directory, 2 InstanceType, 5 MapName_lang[0].
        out[f[0]] = (s(f[1]), f[2], s(f[5]))
    return out


# ------------------------------------------------------------------ discovery
def trees(root=None):
    """Every directory under extracted/ that holds server map data.

    Keyed on the PARENT of a maps/vmaps/mmaps directory, so a submission that
    wrapped its output in Data/ is found at the same depth as one that did not.
    A dbc/ or Cameras/ directory alone does not qualify: those travel with
    client dumps too, and calling one of those a map-data submission would put
    an empty terrain inventory into the dataset.
    """
    root = root or config.EXTRACT
    found = {}
    if not os.path.isdir(root):
        return found
    for dp, dirs, files in os.walk(root):
        base = os.path.basename(dp).lower()
        if base not in ("maps", "vmaps", "mmaps"):
            continue
        if not any(MAPTILE.match(f) or VMTILE.match(f) or VMTREE.match(f)
                   or MMTILE.match(f) or MMAP.match(f) for f in files):
            continue
        found.setdefault(os.path.dirname(dp), set()).add(base)
    return found


def marker(tree, name):
    try:
        with io.open(os.path.join(tree, name), encoding="utf-8",
                     errors="replace") as f:
            return f.read().strip()
    except OSError:
        return ""


def source_of(tree):
    """Which submission this tree came from, walking up to intake's markers.

    The markers sit on the extraction root; a tree nested inside one (Data/,
    say) has none of its own, and reporting "unknown" for it would lose the
    provenance intake had already established.
    """
    cur = os.path.abspath(tree)
    stop = os.path.abspath(config.EXTRACT)
    while True:
        name = marker(cur, SRCMARK)
        if name:
            return name, marker(cur, SRCHASH)
        parent = os.path.dirname(cur)
        if parent == cur or not cur.startswith(stop) or cur == stop:
            return os.path.basename(tree), ""
        cur = parent


# ------------------------------------------------------------------ inventory
def inventory(tree):
    """Tile and DBC counts for one map-data tree."""
    maps, vmt, vmtree, mmt, mmap_ = {}, {}, set(), {}, set()
    models = {"vmo": 0, "m2": 0, "dtree": 0}
    dbcs, bad, cameras, total = [], [], 0, 0

    for sub, want in (("maps", "map"), ("vmaps", "vm"), ("mmaps", "mm"),
                      ("dbc", "dbc"), ("Cameras", "cam")):
        d = os.path.join(tree, sub)
        if not os.path.isdir(d):
            continue
        for dp, _dirs, files in os.walk(d):
            for fn in files:
                p = os.path.join(dp, fn)
                try:
                    total += os.path.getsize(p)
                except OSError:
                    pass
                low = fn.lower()
                m = MAPTILE.match(fn)
                if m:
                    mid = tile_map_id(m.group(1))
                    maps[mid] = maps.get(mid, 0) + 1
                    continue
                m = VMTILE.match(fn)
                if m:
                    vmt[int(m.group(1))] = vmt.get(int(m.group(1)), 0) + 1
                    continue
                m = VMTREE.match(fn)
                if m:
                    vmtree.add(int(m.group(1)))
                    continue
                m = MMTILE.match(fn)
                if m:
                    mid = tile_map_id(m.group(1))
                    mmt[mid] = mmt.get(mid, 0) + 1
                    continue
                m = MMAP.match(fn)
                if m:
                    mmap_.add(int(m.group(1)))
                    continue
                if low.endswith(".vmo"):
                    models["vmo"] += 1
                elif low.endswith(".m2"):
                    models["m2"] += 1
                elif low.endswith(".dtree"):
                    models["dtree"] += 1
                elif low.endswith(".dbc"):
                    h = dbc_header(p)
                    if h:
                        dbcs.append((fn, os.path.getsize(p), h[0], h[1], h[2]))
                    else:
                        # Named and counted, never quietly dropped. A file that
                        # calls itself a DBC and has no WDBC header is either a
                        # truncated upload or a placeholder the client ships
                        # empty, and both are worth a line -- silently omitting
                        # it is how a 0-byte CharVariations.dbc passed through
                        # this stage looking exactly like a file that was fine.
                        bad.append((fn, os.path.getsize(p)))
                elif want == "cam":
                    cameras += 1

    return {"maps": maps, "vmtiles": vmt, "vmtrees": vmtree, "mmtiles": mmt,
            "mmaps": mmap_, "models": models, "dbcs": dbcs, "bad_dbcs": bad,
            "cameras": cameras, "bytes": total}


def name_lookup(extra=()):
    """Merged {map id: (directory, type, name)} from every Map.dbc we can see.

    Later files win only where earlier ones had no row, so a stock Map.dbc
    found first cannot blank out a custom name found later.
    """
    out = {}
    seen = []
    for dp, _dirs, files in os.walk(config.EXTRACT):
        for fn in files:
            if fn.lower() == "map.dbc":
                seen.append(os.path.join(dp, fn))
    seen += [p for p in list(extra) + list(config.MAP_DBC_REFS) if os.path.isfile(p)]
    for p in seen:
        for mid, row in read_map_dbc(p).items():
            if mid not in out or (not out[mid][2] and row[2]):
                out[mid] = row
    return out, seen


# ------------------------------------------------------------------ writing
def write_tables(rows, dbc_rows, names_from, stats):
    os.makedirs(OUT_DIR, exist_ok=True)

    with io.open(os.path.join(OUT_DIR, "maps.tsv"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write("map_id\tname\tdirectory\tinstance_type\tterrain_tiles"
                "\tvmap_tiles\tvmap_tree\tmmap_tiles\tmmap_index"
                "\tabove_stock_range\tsubmission\n")
        for r in rows:
            f.write("\t".join(str(x) for x in r) + "\n")

    with io.open(os.path.join(OUT_DIR, "dbc.tsv"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write("file\tbytes\trecords\tfields\trecord_size\tsubmission\n")
        for r in dbc_rows:
            f.write("\t".join(str(x) for x in r) + "\n")

    named = sum(1 for r in rows if r[1])
    with io.open(os.path.join(OUT_DIR, "README.md"), "w", encoding="utf-8",
                 newline="\n") as f:
        f.write(
            "# Map data inventory\n\n"
            "What terrain exists, not the terrain itself.\n\n"
            "`maps.tsv` has one row per map id found in a submitted server\n"
            "data tree -- the output of a core's `mapextractor` /\n"
            "`vmap4extractor` / `mmaps_generator`. `dbc.tsv` lists the DBC set\n"
            "that travelled with it, by record count, which is what\n"
            "distinguishes an Ascension DBC from a stock one without needing a\n"
            "reference copy of either.\n\n"
            "**The payload is not published here.** Terrain is derived data: a\n"
            "server operator regenerates it from their own client with their\n"
            "own core's extractors. What is worth preserving is the list of\n"
            "what was found, so a map id can be cited, matched and asked after.\n\n"
            "## What is in this one\n\n"
            "| | |\n|---|---:|\n"
            "| map ids with terrain | %d |\n"
            "| named by a `Map.dbc` we can see | %d |\n"
            "| unnamed | %d |\n"
            "| above the stock 3.3.5a range (> %d) | %d |\n"
            "| terrain tiles | %d |\n"
            "| DBC files inventoried | %d |\n\n"
            "An unnamed id means terrain exists for a map no `Map.dbc` in the\n"
            "archive defines. That is a real finding rather than an error: the\n"
            "tiles are evidence the map existed.\n"
            % (len(rows), named, len(rows) - named, STOCK_MAX,
               stats["above"], stats["tiles"], len(dbc_rows)))
        if names_from:
            f.write("\nNames were resolved from %d `Map.dbc` %s.\n"
                    % (names_from, "copy" if names_from == 1 else "copies"))
        if stats["bad"]:
            f.write("\n## Files that call themselves a DBC and are not\n\n"
                    "Listed rather than dropped: a truncated upload and a "
                    "deliberately\nempty placeholder look the same from here, "
                    "and both are worth knowing.\n\n")
            for fn, sz in stats["bad"]:
                f.write("- `%s` — %d bytes, no `WDBC` header\n" % (fn, sz))


def update_ledger(entries):
    """Record each tree by its submission hash, the way the WDB ledger does.

    Keyed on intake's content hash rather than the directory name, so the same
    submission arriving twice under two names is one entry with two sources --
    and a re-run does not inflate anything.
    """
    old = {}
    if os.path.exists(LEDGER):
        try:
            with io.open(LEDGER, encoding="utf-8") as f:
                old = json.load(f)
        except (OSError, ValueError):
            old = {}
    for key, entry in entries.items():
        prev = old.get(key)
        if prev:
            entry["first_seen"] = prev.get("first_seen", entry["first_seen"])
            entry["sources"] = sorted(set(prev.get("sources", [])) |
                                      set(entry["sources"]))
        old[key] = entry
    tmp = LEDGER + ".tmp"
    with io.open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(old, f, indent=1, sort_keys=True, ensure_ascii=False)
    os.replace(tmp, LEDGER)
    return old


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--list", action="store_true",
                    help="report what was found and write nothing")
    ap.add_argument("--map-dbc", action="append", default=[], metavar="PATH",
                    help="another Map.dbc to use when naming ids (repeatable)")
    a = ap.parse_args(argv)

    found = trees()
    if not found:
        print("no server map data found under extracted/; nothing to inventory")
        return 0

    names, name_files = name_lookup(a.map_dbc)
    import time
    now = time.strftime("%Y-%m-%d %H:%M:%S")

    rows, dbc_rows, ledger, bad_dbcs = [], [], {}, []
    tiles = above = 0
    for tree in sorted(found):
        inv = inventory(tree)
        src, digest = source_of(tree)
        ids = sorted(set(inv["maps"]) | set(inv["vmtiles"]) | set(inv["mmtiles"])
                     | inv["vmtrees"] | inv["mmaps"])
        for mid in ids:
            d, itype, nm = names.get(mid, ("", "", ""))
            rows.append((mid, nm, d, itype, inv["maps"].get(mid, 0),
                         inv["vmtiles"].get(mid, 0),
                         1 if mid in inv["vmtrees"] else 0,
                         inv["mmtiles"].get(mid, 0),
                         1 if mid in inv["mmaps"] else 0,
                         1 if mid > STOCK_MAX else 0, src))
            tiles += inv["maps"].get(mid, 0)
            above += 1 if mid > STOCK_MAX else 0
        for fn, sz, nrec, nfield, recsize in sorted(inv["dbcs"]):
            dbc_rows.append((fn, sz, nrec, nfield, recsize, src))

        unnamed = [m for m in ids if not names.get(m, ("", "", ""))[2]]
        print("  %-34s %4d map id(s), %5d tile(s), %3d dbc, %6.1f MB"
              % (src[:34], len(ids), sum(inv["maps"].values()),
                 len(inv["dbcs"]), inv["bytes"] / 1048576.0))
        if unnamed:
            print("     %d id(s) no Map.dbc names: %s"
                  % (len(unnamed), " ".join(str(m) for m in unnamed[:24])
                     + (" ..." if len(unnamed) > 24 else "")))
        for fn, sz in sorted(inv["bad_dbcs"]):
            print("     !! %s is %d bytes and has no WDBC header" % (fn, sz))
            bad_dbcs.append((fn, sz))

        ledger[digest or src] = {
            "submission": src, "sha256": digest, "first_seen": now,
            "last_seen": now, "sources": [src],
            "map_ids": ids, "terrain_tiles": sum(inv["maps"].values()),
            "vmap_tiles": sum(inv["vmtiles"].values()),
            "mmap_tiles": sum(inv["mmtiles"].values()),
            "dbc_files": len(inv["dbcs"]), "cameras": inv["cameras"],
            "unreadable_dbc": [{"file": f, "bytes": s}
                               for f, s in sorted(inv["bad_dbcs"])],
            "models": inv["models"], "bytes": inv["bytes"],
        }

    if a.list:
        print("\n--list: nothing written")
        return 0

    write_tables(rows, dbc_rows, len(name_files),
                 {"tiles": tiles, "above": above, "bad": sorted(set(bad_dbcs))})
    update_ledger(ledger)
    named = sum(1 for r in rows if r[1])
    print("\n  %d map id(s) across %d submission(s): %d named, %d unnamed, "
          "%d above the stock range" % (len(rows), len(found), named,
                                        len(rows) - named, above))
    print("  inventory -> %s" % OUT_DIR)
    return 0


if __name__ == "__main__":
    sys.exit(main())
