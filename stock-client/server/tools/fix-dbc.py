"""Repair the ways a foreign client's DBCs kill AzerothCore on startup.

Both were found the same way: worldserver died inside LoadDBCStores with
nothing in the log, and the crash report under server-ascension/Crashes named
the frame.  Neither is detectable by comparing field counts and record sizes,
which is why the DBCs looked identical to vanilla's and still would not load.

  1. String columns that are not strings.
     Ascension's Spell.dbc leaves the seven unused locale slots of SpellName
     and Rank filled with junk on ~500 of its 209,509 spells.  The client never
     reads those slots; AzerothCore reads every locale column its format
     declares, takes the junk as an offset into the string block and walks off
     the end -

         ASSERT(stringOffset < file.stringSize)     DBCFileLoader.h:75

     Repair: point every out-of-range offset at the string block's final byte,
     which is the NUL that terminates the last string, so the column reads as
     the empty string - exactly what a stock DBC holds in an unused locale
     slot.  Sizes do not change, so no other offset moves.

  2. Index columns holding 0xFFFFFFFF.
     Ascension's WorldMapArea.dbc carries 25 rows with an id of -1.
     AutoProduceData sizes its lookup table as (max id + 1), that addition
     overflows to zero, and the very next line writes through the zero-length
     table:

         indexTable[getRecord(y).getUInt(i)] = &dataTable[offset];
                                               DBCFileLoader.cpp:231

     Repair: drop those rows.  An id of 0xFFFFFFFF cannot be looked up by
     anything - the core indexes by id - so the rows are unreachable data, not
     content.  recordCount in the header comes down to match; the string block
     is copied through untouched, so every surviving offset stays valid.

  3. Gaps in a positional index column.
     TaxiPathNode.dbc numbers each node within its path.  DBCStores sizes the
     per-path vector as (highest index + 1) and then fills it by index, so any
     path whose numbering skips a value leaves a null in the middle:

         sTaxiPathNodesByPath[entry->path][entry->index] = entry;
                                               DBCStores.cpp line 585

     and the first thing that walks every node dereferences it:

         if (node->arrivalEventID)          ObjectMgr.cpp line 6294

     Ascension has exactly one such path (1984, numbered 1-29 instead of
     0-28); vanilla has none.  Repair: renumber that path's nodes to be
     contiguous from zero, keeping their existing order.  index is an ordinal
     used only for sequencing the flight, and nothing outside this file refers
     to it, so the route itself is unchanged.

Only files the core actually loads are considered, and within them only rows
that are already unusable.  A column whose values are all valid is never
touched: a wrong guess there would silently replace real data.

Usage:  python fix-dbc.py [<dbc-dir>] [--dry-run]
"""
import importlib.util, os, shutil, struct, sys

_HERE = os.path.dirname(os.path.abspath(__file__))
# check-dbc-fmt.py has a hyphen in its name, so it is imported by path.
_spec = importlib.util.spec_from_file_location(
    "dbcfmt", os.path.join(_HERE, "check-dbc-fmt.py"))
dbcfmt = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(dbcfmt)

DEFAULT_DIR = r"C:\AzerothRealm\server-ascension\Data\dbc"
NO_ID = 0xFFFFFFFF


def backup(path):
    if not os.path.exists(path + ".orig"):
        shutil.copyfile(path, path + ".orig")


def repair(path, fmt, dry):
    """-> list of human-readable repairs made (or that would be made)."""
    blob = bytearray(open(path, "rb").read())
    if blob[:4] != b"WDBC":
        return []
    rc, fc, rs, ss = struct.unpack("<I I I I", bytes(blob[4:20]))
    if len(fmt) != fc or rc == 0:
        return []                        # core ignores this file entirely
    off, _ = dbcfmt.offsets(fmt)
    base = 20
    done, dirty = [], False

    # --- 2. unreachable rows whose index would overflow the lookup table ----
    if "n" in fmt:
        i = fmt.index("n")
        drop = [r for r in range(rc)
                if struct.unpack_from("<I", blob, base + r * rs + off[i])[0] == NO_ID]
        if drop:
            done.append("dropped %d row(s) with an id of 0xFFFFFFFF" % len(drop))
            if not dry:
                keep = bytearray()
                dropped = set(drop)
                for r in range(rc):
                    if r not in dropped:
                        keep += blob[base + r * rs: base + (r + 1) * rs]
                strings = blob[base + rc * rs:]
                rc -= len(drop)
                blob = bytearray(blob[:base]) + keep + strings
                struct.pack_into("<I", blob, 4, rc)
                dirty = True

    # --- 3. positional index columns with gaps ------------------------------
    if os.path.basename(path).lower() == "taxipathnode.dbc" and len(fmt) > 2:
        by_path = {}
        for r in range(rc):
            key = struct.unpack_from("<I", blob, base + r * rs + off[1])[0]
            ix = struct.unpack_from("<I", blob, base + r * rs + off[2])[0]
            by_path.setdefault(key, []).append((ix, r))
        fixed = []
        for key, rows in by_path.items():
            rows.sort()
            if [ix for ix, _ in rows] == list(range(len(rows))):
                continue
            fixed.append(key)
            if not dry:
                for new, (_, r) in enumerate(rows):
                    struct.pack_into("<I", blob, base + r * rs + off[2], new)
                dirty = True
        if fixed:
            done.append("renumbered node indices contiguously on %d path(s): %s"
                        % (len(fixed), ", ".join(str(k) for k in sorted(fixed))))

    # --- 1. string columns that do not hold string offsets ------------------
    bad = []
    for idx, c in enumerate(fmt):
        if c != "s":
            continue
        for r in range(rc):
            at = base + r * rs + off[idx]
            if struct.unpack_from("<I", blob, at)[0] >= ss:
                bad.append((idx, at))
    if bad:
        empty = ss - 1                   # the NUL that ends the last string
        if ss < 1 or blob[base + rc * rs + empty] != 0:
            raise RuntimeError("%s: string block does not end in NUL, so there "
                               "is no safe empty string to point at" % path)
        per = {}
        for idx, at in bad:
            per[idx] = per.get(idx, 0) + 1
            if not dry:
                struct.pack_into("<I", blob, at, empty)
                dirty = True
        done.append("redirected %d out-of-range string offset(s) in column(s) %s"
                    % (len(bad), ", ".join(str(k) for k in sorted(per))))

    if dirty and not dry:
        backup(path)
        with open(path, "wb") as f:
            f.write(blob)
    return done


def main(argv):
    dry = "--dry-run" in argv
    dirs = [a for a in argv if not a.startswith("--")] or [DEFAULT_DIR]
    formats = dbcfmt.load_formats()
    for d in dirs:
        print("=== %s%s ===" % (d, "  (dry run)" if dry else ""))
        touched = 0
        for fname in sorted(formats):
            p = os.path.join(d, fname)
            if not os.path.isfile(p):
                continue
            done = repair(p, formats[fname][0], dry)
            if done:
                touched += 1
                print("  %s" % fname)
                for x in done:
                    print("      %s" % x)
        print("  %d file(s) %s" % (touched, "would change" if dry else "repaired"))


if __name__ == "__main__":
    main(sys.argv[1:])
