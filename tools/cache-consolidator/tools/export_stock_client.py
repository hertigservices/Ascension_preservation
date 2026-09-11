#!/usr/bin/env python3
"""Export the merged caches as Lua 5.1 data tables for an addon on a STOCK 3.3.5a client.

    python export_stock_client.py                          # from the configured cachedata
    python export_stock_client.py --data <cachedata>       # from a checkout of ascension-data
    python export_stock_client.py --dbc <Ascension ItemDisplayInfo.dbc> --stock-dbc <stock ItemDisplayInfo.dbc>

WHAT IT WRITES
  <cachedata>/lua/stock-client/
      init.lua           creates the AscensionStockData namespace: field order, file list, counts
      items-NNN.lua      AscensionStockData.items[entry]     = { name, quality, icon, displayId,
                                                                 class, subclass, inventoryType,
                                                                 itemLevel, requiredLevel }
      creatures-NNN.lua  AscensionStockData.creatures[entry] = { name, subname, displayId }
      quests-NNN.lua     AscensionStockData.quests[entry]    = { title }
      README.md          what is in here, where each row came from, and the known placeholders
  <cachedata>/dbc/item_display_icons.tsv.gz   (only when --dbc is given; read back otherwise)
      displayid, icon, stock_displayid

WHY IT EXISTS
  A stock 3.3.5a client has none of Ascension's data files, and an addon cannot read a .wdb
  cache. An addon CAN load Lua files named in its .toc. So the same records the caches hold
  are written here as plain Lua tables, split so no file passes CHUNK_BYTES, with a
  positional row layout -- smaller than named keys, and the order is stated in every file's
  header and in AscensionStockData.fields. Each file only assigns into the namespace, so the
  files load in any order after init.lua.

WHICH RECORD WINS
  The by-mode view for --mode (default conquest-of-azeroth) first; every entry it lacks is
  taken from union/. A Conquest of Azeroth realm therefore gets CoA's own values wherever a
  CoA client ever saw the record, and the widest coverage everywhere else. The README says
  how many rows came from each.

THE ICON, AND WHY THE DBC HAS TO BE ASCENSION'S
  itemcache carries a display id, not an icon; the icon name lives in ItemDisplayInfo.dbc.
  Ascension RENUMBERED that file, so the id has to be looked up in Ascension's own copy --
  the stock file gives a different item's icon for the same id. --dbc reads Ascension's copy
  once and publishes the lookup as dbc/item_display_icons.tsv.gz, so the pipeline (and any
  reader) never needs the DBC again. --stock-dbc adds stock_displayid: the stock 3.3.5a
  display id whose row is byte-identical in model, texture and icon, i.e. the id a stock
  client can draw without Ascension's art. Blank means no such row exists.

WHAT IS NOT FILTERED
  Names are what the Ascension server actually sent. That includes its own stand-ins
  ("Z:DBCtoDB Generated Item", "[MISSING ITEM NAME]", ...). They are left in, because an
  Ascension client would show exactly that, and the README names the frequent ones with
  counts so a consumer can decide for itself.
"""
import os, sys, csv, gzip, struct, argparse, collections, time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config, build_cache

NAMESPACE = "AscensionStockData"
DEFAULT_MODE = "conquest-of-azeroth"
CHUNK_BYTES = 340_000
OUT_SUB = "lua/stock-client"
ICON_TSV = "dbc/item_display_icons.tsv.gz"
SOURCE_NAME = "hertigservices/ascension-data (cachedata)"

# table name -> (cache, Lua row field order)
TABLES = {
    "items":     ("itemcache", ["name", "quality", "icon", "displayId", "class", "subclass",
                                "inventoryType", "itemLevel", "requiredLevel"]),
    "creatures": ("creaturecache", ["name", "subname", "displayId"]),
    "quests":    ("questcache", ["title"]),
}

# ItemDisplayInfo.dbc (3.3.5a, 25 fields): ModelName[2], ModelTexture[2], InventoryIcon[2],
# GeosetGroup[3], Flags, SpellVisualID, GroupSoundIndex, HelmetGeosetVis[2], Texture[8],
# ItemVisual, ParticleColorID.  Same string-field set fix_item_displayids.py uses.
STR_FIELDS = {1, 2, 3, 4, 5, 6, 15, 16, 17, 18, 19, 20, 21, 22}
ICON_FIELD = 5
PLACEHOLDER_MIN_SHARED = 100   # a name shared by this many entries is a stand-in, not a name


# ---------------------------------------------------------------- DBC -> icon table
def read_dbc(path):
    """id -> (icon, content key over every non-id field, strings resolved)."""
    with open(path, "rb") as fh:
        raw = fh.read()
    magic, nrec, nfield, recsize, strsize = struct.unpack_from("<4sIIII", raw, 0)
    if magic != b"WDBC":
        raise SystemExit(f"{path}: not a DBC (magic {magic!r})")
    if nfield != 25:
        raise SystemExit(f"{path}: {nfield} fields, expected 25 (3.3.5a ItemDisplayInfo)")
    str_off = 20 + nrec * recsize

    def s(off):
        so = struct.unpack_from("<I", raw, off)[0]
        if so == 0:
            return ""
        end = raw.index(b"\0", str_off + so)
        return raw[str_off + so:end].decode("utf-8", "replace")

    out = {}
    for i in range(nrec):
        off = 20 + i * recsize
        rid = struct.unpack_from("<I", raw, off)[0]
        key = tuple(s(off + f * 4) if f in STR_FIELDS
                    else struct.unpack_from("<I", raw, off + f * 4)[0]
                    for f in range(1, nfield))
        out[rid] = (key[ICON_FIELD - 1], key)
    return out


def build_icon_rows(asc_path, stock_path):
    asc = read_dbc(asc_path)
    by_content = collections.defaultdict(list)
    if stock_path:
        for rid, (_icon, key) in read_dbc(stock_path).items():
            by_content[key].append(rid)
    rows = []
    for rid in sorted(asc):
        icon, key = asc[rid]
        stock = min(by_content[key]) if key in by_content else ""
        rows.append((rid, icon, stock))
    return rows


def write_icon_tsv(data, rows):
    path = os.path.join(data, ICON_TSV)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.open(path, "wt", encoding="utf-8", newline="\n") as f:
        f.write("displayid\ticon\tstock_displayid\n")
        for rid, icon, stock in rows:
            f.write(f"{rid}\t{icon}\t{stock}\n")
    return path


def read_icon_tsv(data):
    path = os.path.join(data, ICON_TSV)
    if not os.path.isfile(path):
        return None
    icons = {}
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        r = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        head = next(r)
        di, ii, si = head.index("displayid"), head.index("icon"), head.index("stock_displayid")
        for row in r:
            icons[int(row[di])] = (row[ii], row[si])
    return icons


# ---------------------------------------------------------------- the cache views
def read_view(data, view, cache):
    """entry -> row dict for one exported view; None when the view has no such file."""
    path = os.path.join(data, view, f"{cache}.tsv.gz")
    if not os.path.isfile(path):
        return None, ""
    rows, newest = {}, ""
    with gzip.open(path, "rt", encoding="utf-8", newline="") as f:
        r = csv.reader(f, delimiter="\t", quoting=csv.QUOTE_NONE)
        head = next(r)
        ei = head.index("entry")
        ci = head.index("_captured") if "_captured" in head else None
        for row in r:
            if len(row) != len(head):
                continue
            d = dict(zip(head, row))
            rows[int(row[ei])] = d
            if ci is not None and row[ci] > newest:
                newest = row[ci]
    return rows, newest


def merged_view(data, mode, cache):
    """--mode rows win; union fills what that mode never saw."""
    primary, newest_p = read_view(data, os.path.join("by-mode", mode), cache)
    union, newest_u = read_view(data, "union", cache)
    if union is None:
        raise SystemExit(f"missing {os.path.join(data, 'union', cache + '.tsv.gz')}")
    primary = primary or {}
    out = dict(union)
    out.update(primary)
    from_primary = len(primary)
    from_union = len(out) - from_primary
    return out, from_primary, from_union, max(newest_p, newest_u)


# ---------------------------------------------------------------- Lua
_ESC = {"\\": "\\\\", '"': '\\"', "\n": "\\n", "\r": "\\r", "\t": "\\t", "\0": "\\0"}


def _esc_char(c):
    if c in _ESC:
        return _ESC[c]
    o = ord(c)
    if o < 32 or o == 127:
        return "\\%03d" % o
    return c


def lua_str(s):
    if not s:
        return '""'
    if any(c in _ESC or ord(c) < 32 or ord(c) == 127 for c in s):
        s = "".join(_esc_char(c) for c in s)
    return '"' + s + '"'


def num(x):
    try:
        return int(x)
    except (TypeError, ValueError):
        try:
            return int(float(x))
        except (TypeError, ValueError):
            return 0


def item_row(r, icons, stats):
    d = num(r.get("displayid"))
    icon = ""
    if not d:
        stats["no_displayid"] += 1
    elif icons is not None:
        hit = icons.get(d)
        if hit:
            icon = hit[0]
            stats["icon_resolved"] += 1
        else:
            stats["icon_unresolved"] += 1
    return (lua_str(r.get("name", "")), num(r.get("Quality")), lua_str(icon), d,
            num(r.get("class")), num(r.get("subclass")), num(r.get("InventoryType")),
            num(r.get("ItemLevel")), num(r.get("RequiredLevel")))


def creature_row(r, icons, stats):
    return (lua_str(r.get("name", "")), lua_str(r.get("subname", "")), num(r.get("modelid1")))


def quest_row(r, icons, stats):
    return (lua_str(r.get("Title", "")),)


ROW = {"items": item_row, "creatures": creature_row, "quests": quest_row}


def header(table, part, total, mode, captured):
    fields = ", ".join(TABLES[table][1])
    return (f"-- {NAMESPACE}.{table} part {part} of {total}\n"
            f"-- Source: {SOURCE_NAME}; mode {mode}, union fallback; data captured through {captured}.\n"
            f"-- Generated by tools/export_stock_client.py -- regenerated on every publish, do not edit.\n"
            f"-- Row layout: T[entry] = {{ {fields} }}\n"
            f"local T = {NAMESPACE}.{table}\n")


def write_chunks(outdir, table, rows, mode, captured):
    """rows: iterable of (entry, tuple) in ascending entry order. Returns the file list."""
    body, size, chunks = [], 0, []
    hdr_len = len(header(table, 999, 999, mode, captured).encode("utf-8"))

    def flush():
        if body:
            chunks.append(list(body))
        body.clear()

    for entry, vals in rows:
        line = f"T[{entry}]={{{','.join(str(v) for v in vals)}}}\n"
        n = len(line.encode("utf-8"))
        if body and hdr_len + size + n > CHUNK_BYTES:
            flush()
            size = 0
        body.append(line)
        size += n
    flush()

    files = []
    for i, lines in enumerate(chunks, 1):
        name = f"{table}-{i:03d}.lua"
        with open(os.path.join(outdir, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(header(table, i, len(chunks), mode, captured))
            f.writelines(lines)
        files.append(name)
    return files


def write_init(outdir, mode, captured, files, counts):
    L = [f"-- {NAMESPACE}: Ascension's item, creature and quest records for an addon on a stock 3.3.5a client.",
         f"-- Source: {SOURCE_NAME}; mode {mode}, union fallback; data captured through {captured}.",
         "-- Generated by tools/export_stock_client.py -- regenerated on every publish, do not edit.",
         "-- Load this file first; the part files assign into the tables it creates and may load in any order.",
         f"{NAMESPACE} = {NAMESPACE} or {{}}",
         f"local D = {NAMESPACE}"]
    for t in TABLES:
        L.append(f"D.{t} = D.{t} or {{}}")
    L.append("D.fields = {")
    for t, (_c, fields) in TABLES.items():
        L.append(f"  {t} = {{ {', '.join(lua_str(f) for f in fields)} }},")
    L.append("}")
    L.append("D.meta = {")
    L.append(f"  source = {lua_str(SOURCE_NAME)},")
    L.append(f"  mode = {lua_str(mode)},")
    L.append('  fallback = "union",')
    L.append(f"  capturedThrough = {lua_str(captured)},")
    L.append("  counts = { " + ", ".join(f"{t} = {counts[t]['rows']}" for t in TABLES) + " },")
    L.append("}")
    L.append("D.files = {")
    for t in TABLES:
        for name in files[t]:
            L.append(f"  {lua_str(name)},")
    L.append("}")
    with open(os.path.join(outdir, "init.lua"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")


def placeholder_names(view):
    shared = collections.Counter(r.get("name", "") for r in view.values())
    return [(n, c) for n, c in shared.most_common() if c >= PLACEHOLDER_MIN_SHARED and n]


def write_readme(outdir, mode, captured, files, counts, placeholders, icons_present):
    n = counts
    L = ["# `lua/stock-client/` — the caches as Lua tables for a stock 3.3.5a client", "",
         "An addon on a stock client cannot read a `.wdb` cache, but it can load Lua files",
         "named in its `.toc`. These files hold the same records the caches hold, as plain",
         f"Lua 5.1 tables under one global, `{NAMESPACE}`. Load `init.lua` first; every other",
         "file only assigns into the tables it creates, so their order does not matter.", "",
         f"Source: {SOURCE_NAME}. Mode `{mode}` first, `union/` for every entry that mode",
         f"never saw. Data captured through {captured}. Regenerated on every publish by",
         "`tools/export_stock_client.py` in the Ascension_preservation repository.", "",
         "## Tables", "",
         "| table | rows | from `" + mode + "` | from `union` | files | row layout |",
         "|---|---:|---:|---:|---:|---|"]
    for t, (_c, fields) in TABLES.items():
        L.append(f"| `{NAMESPACE}.{t}` | {n[t]['rows']:,} | {n[t]['primary']:,} | "
                 f"{n[t]['union']:,} | {len(files[t])} | `{{ {', '.join(fields)} }}` |")
    L += ["", "Rows are positional to keep the files small; the order above is also in",
          f"`{NAMESPACE}.fields`. Numbers are integers, strings are UTF-8 as the server sent them,",
          "and a field the record did not carry is `0` or `\"\"`.", "",
          "## Items: the icon and the display id", ""]
    it = n["items"]
    if icons_present:
        L += ["`icon` is the `InventoryIcon` name from **Ascension's** `ItemDisplayInfo.dbc` (that file",
              "is renumbered, so a stock client resolves the same `displayId` to a different item's",
              "icon). Use `\"Interface\\\\Icons\\\\\" .. icon` for the texture. The lookup itself is",
              "published as `dbc/item_display_icons.tsv.gz` (`displayid`, `icon`, `stock_displayid`;",
              "the last is the stock 3.3.5a display id with byte-identical art, blank when none).", "",
              "| items | count |", "|---|---:|",
              f"| with an icon | {it.get('icon_resolved', 0):,} |",
              f"| display id not in Ascension's DBC | {it.get('icon_unresolved', 0):,} |",
              f"| display id 0 in the record | {it.get('no_displayid', 0):,} |"]
    else:
        L += ["**`icon` is empty in this build**: `dbc/item_display_icons.tsv.gz` was not present when",
              "the export ran. Run `export_stock_client.py --dbc <Ascension ItemDisplayInfo.dbc>` once",
              "to publish the lookup; later runs read it back."]
    L += ["", "## Names that are not names", "",
          "Nothing is filtered: a name is what the Ascension server sent. Its own stand-ins",
          "are therefore in here too, and they are easy to spot because one string covers",
          f"many entries. Names shared by {PLACEHOLDER_MIN_SHARED}+ item entries:", "",
          "| name | item entries |", "|---|---:|"]
    for name, c in placeholders[:15]:
        L.append(f"| `{name}` | {c:,} |")
    L += ["", "## Caveats on a stock client", "",
          "- The icon file must exist in the client's MPQs. Ascension-only icons need Ascension's art patches.",
          "- `displayId` draws the right model only with Ascension's `ItemDisplayInfo.dbc` in the client;",
          "  use `stock_displayid` from the lookup where it is set, otherwise expect the wrong model.",
          "- Spells referenced by items are not in these tables and have no `Spell.dbc` row on a stock client.",
          ""]
    with open(os.path.join(outdir, "README.md"), "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter,
                                 epilog=__doc__)
    ap.add_argument("--data", default=config.DATA, help="cachedata/ folder (default: the configured one)")
    ap.add_argument("--mode", default=DEFAULT_MODE, help=f"by-mode view that wins (default {DEFAULT_MODE})")
    ap.add_argument("--dbc", help="Ascension ItemDisplayInfo.dbc: (re)build dbc/item_display_icons.tsv.gz")
    ap.add_argument("--stock-dbc", help="stock 3.3.5a ItemDisplayInfo.dbc: fill stock_displayid (needs --dbc)")
    ap.add_argument("--out", help=f"output folder (default <data>/{OUT_SUB})")
    a = ap.parse_args(argv)
    t0 = time.time()
    data = os.path.normpath(a.data)
    outdir = os.path.normpath(a.out or os.path.join(data, OUT_SUB))
    os.makedirs(outdir, exist_ok=True)

    if a.dbc:
        rows = build_icon_rows(a.dbc, a.stock_dbc)
        p = write_icon_tsv(data, rows)
        with_stock = sum(1 for r in rows if r[2] != "")
        print(f"  {os.path.relpath(p, data)}: {len(rows):,} display ids, "
              f"{with_stock:,} with a byte-identical stock row")
    elif a.stock_dbc:
        ap.error("--stock-dbc needs --dbc")
    icons = read_icon_tsv(data)
    if icons is None:
        print(f"  !! no {ICON_TSV} under {data}; item icons will be empty (run once with --dbc)")

    files, counts, captured = {}, {}, ""
    placeholders = []
    written = set()
    reuse = build_cache.BuildCache('stock', outdir)
    for table, (cache, _fields) in TABLES.items():
        input_paths = [os.path.join(data, view, cache + '.tsv.gz')
                       for view in (os.path.join('by-mode', a.mode), 'union')]
        input_paths.append(os.path.join(data, ICON_TSV))
        incoming = captured
        inputs = build_cache.fingerprint(input_paths, [a.mode, incoming])
        hit = reuse.load(table, inputs)
        if hit:
            meta, paths = hit
            files[table], counts[table] = meta['files'], meta['counts']
            captured = meta['captured']
            if table == 'items':
                placeholders = meta['placeholders']
            written.update(os.path.basename(p) for p in paths)
            print(f"  {table:<10} reused verified stock-client tables")
            continue
        view, n_primary, n_union, newest = merged_view(data, a.mode, cache)
        captured = max(captured, newest)
        stats = collections.Counter()
        rowfn = ROW[table]
        rows = ((e, rowfn(view[e], icons, stats)) for e in sorted(view))
        files[table] = write_chunks(outdir, table, rows, a.mode, newest or captured)
        counts[table] = {"rows": len(view), "primary": n_primary, "union": n_union, **stats}
        written.update(files[table])
        if table == "items":
            placeholders = placeholder_names(view)
        if inputs != build_cache.fingerprint(input_paths, [a.mode, incoming]):
            raise RuntimeError('decoded inputs changed during stock export')
        reuse.save(table, inputs, [os.path.join(outdir, f) for f in files[table]],
                   {'files': files[table], 'counts': counts[table], 'captured': captured,
                    'placeholders': placeholders if table == 'items' else []})
        print(f"  {table:<10} {len(view):>7,} rows  ({n_primary:,} from {a.mode}, "
              f"{n_union:,} from union)  -> {len(files[table])} files")
    write_init(outdir, a.mode, captured, files, counts)
    write_readme(outdir, a.mode, captured, files, counts, placeholders, icons is not None)
    written.update(("init.lua", "README.md"))
    # A stale part file from a larger earlier run would still load and resurrect rows
    # the current data no longer has, so anything this run did not write goes.
    for fn in os.listdir(outdir):
        if fn not in written and (fn.endswith(".lua") or fn == "README.md"):
            os.remove(os.path.join(outdir, fn))
            print(f"  pruned stale {fn}")
    it = counts["items"]
    if icons is not None:
        print(f"  items: {it.get('icon_resolved', 0):,} icons resolved, "
              f"{it.get('icon_unresolved', 0):,} display ids not in the DBC, "
              f"{it.get('no_displayid', 0):,} with display id 0")
    print(f"  placeholders (>= {PLACEHOLDER_MIN_SHARED} entries): " +
          ", ".join(f"{n!r} x{c:,}" for n, c in placeholders[:6]))
    print(f"stock-client tables -> {outdir}  ({time.time() - t0:.0f}s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
