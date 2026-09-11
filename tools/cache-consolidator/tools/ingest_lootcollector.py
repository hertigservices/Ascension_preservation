"""LootCollector SavedVariables -> where Worldforged items and Mystic Scrolls are picked up.

    python -B tools/ingest_lootcollector.py

LootCollector is a community addon. Per realm, it records every Worldforged item
and Mystic Scroll that a player, or someone that player's addon syncs with, has
looted: the item id, the zone, and the looter's 0..1 position on the zone map.
Submitters' WTF folders carry it as SavedVariables/LootCollector.lua, plus a
.lua.bak.

Until this stage existed nothing read those files. Intake ledgers .wdb only, and
luamerge knows five addon files by exact name, so 34 of them sat in extracted/
contributing nothing, and nothing said so.

PRIVACY. Every record also names three kinds of player:
- the one who found it (`fp`);
- the one who first shared it (`o`);
- everyone who voted on it (`fp_votes`).
A submission's own path carries its account login. This stage therefore reads
ONLY the numeric fields in NUMERIC, the status word and `xy`, plus the realm key
each record sits under. It never writes a path: a file is identified by its
SHA-256. The raw .lua stays QUARANTINED by scrub.py and is never published, and
check_outputs() refuses to write anything shaped like an address or a path.

What a pin is: GetPlayerMapPosition() at the moment the loot window opened. That
is where the LOOTER stood, not where the object is, typically a few yards from
the spawn. There is no Z. Records are crowd-synced between players, so N files
agreeing are not N witnesses.

Output, in config.OUT/lootcollector/: pins.tsv, items.tsv, sources.tsv and
README.md. The stage keeps a parse cache under config.WORK, keyed by file hash,
because one file takes seconds to parse and the files never change.
"""
import os, sys, io, re, json, hashlib, datetime, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config
import luaser
import modes

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT_DIR = os.path.join(config.OUT, "lootcollector").replace("\\", "/")
CACHE_DIR = os.path.join(config.WORK, "lootcollector-cache").replace("\\", "/")
BOUNDS_FILE = os.path.join(HERE, "worldmaparea_bounds.tsv")
NAMES = ("lootcollector.lua", "lootcollector.lua.bak")
# Bump when records_of() reads anything differently, so every cached parse is redone.
PARSER_VERSION = 1

# The only fields ever read from a discovery record, besides `s` and `xy`.
#   dt type (1 Worldforged, 2 Mystic Scroll)   i item id   z WorldMapArea id + 1
#   iz instance zone   c the addon's own continent number   q item quality
#   t0 first seen   ls last seen (unix time)   mc how many times it was merged
NUMERIC = ("dt", "i", "z", "iz", "c", "q", "t0", "ls", "mc")
STATUSES = ("CONFIRMED", "UNCONFIRMED", "FADING", "STALE")
TYPES = {1: "worldforged", 2: "mystic_scroll"}
GUID = re.compile(r"^\d+-\d+-\d+-\d+-[-0-9.]+$")
# One pin per (type, item, zone) within this distance on both map axes -- the
# same rule the Tareksoh/Worldforged-data union used, so the counts compare.
NEAR = 0.005

PIN_COLS = ["type", "item", "lc_zone", "wma_id", "zone", "map", "norm_x", "norm_y",
            "server_x", "server_y", "realms", "modes", "records", "files",
            "first_seen", "last_seen", "status"]
ITEM_COLS = ["type", "item", "pins", "zones", "maps", "realms", "records",
             "first_seen", "last_seen"]
SOURCE_COLS = ["sha256", "kind", "bytes", "status", "discoveries", "realms"]
# Refused anywhere in the output: an address, a drive path, an account tree.
LEAK = re.compile(r"@|\b[A-Za-z]:[\\/]|WTF[\\/]|Account[\\/]", re.I)


# --------------------------------------------------------------- finding them
def discover(roots=None):
    """{sha256: (a path holding it, is_bak, bytes)} -- one entry per distinct file."""
    roots = [config.EXTRACT] + list(config.SCAN_ROOTS) if roots is None else roots
    seen, found = set(), {}
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dp, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in sorted(files):
                if fn.lower() not in NAMES:
                    continue
                p = os.path.abspath(os.path.join(dp, fn))
                if os.path.normcase(p) in seen:
                    continue
                seen.add(os.path.normcase(p))
                with open(p, "rb") as f:
                    blob = f.read()
                found.setdefault(hashlib.sha256(blob).hexdigest(),
                                 (p, fn.lower().endswith(".bak"), len(blob)))
    return found


# ------------------------------------------------------------------- reading
def _sub(t, key):
    v = t.get(key) if isinstance(t, luaser.Table) else None
    return v if isinstance(v, luaser.Table) else None


def _int(v):
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


def _unit(v):
    if isinstance(v, bool) or not isinstance(v, (int, float)):
        return None
    return float(v) if 0.0 <= v <= 1.0 else None


def records_of(blob):
    """(status, records) for one file's bytes.

    A record is (realm, guid, dt, item, z, iz, c, q, s, t0, ls, mc, x, y); a
    missing q/t0/ls/mc is -1 so records always compare. The guid is the addon's
    own numeric key `c-z-iz-item-x-y`, kept only to recognise the same
    discovery in two files; nothing is written with it.
    """
    if not blob.strip(b"\x00 \t\r\n"):
        return "empty (no content)", []
    try:
        tree = luaser.loads(blob.decode("utf-8", "replace"))
    except (luaser.LuaError, RecursionError):
        # Never carry the parser's message along: it quotes the file, and the
        # file names people.
        return "parse error", []
    out = []
    for value in tree.values():
        realms = _sub(_sub(value, "global"), "realms")
        if realms is None:
            continue
        for realm, rt in realms.hash.items():
            disc = _sub(rt, "discoveries")
            if disc is None or not isinstance(realm, str) or " - " not in realm \
                    or LEAK.search(realm) or len(realm) > 80 or "\t" in realm or "\n" in realm:
                continue
            for guid, rec in disc.hash.items():
                if not isinstance(guid, str) or not GUID.match(guid) or not isinstance(rec, luaser.Table):
                    continue
                n = {k: _int(rec.get(k)) for k in NUMERIC}
                xy = _sub(rec, "xy")
                x = _unit(xy.get("x")) if xy is not None else None
                y = _unit(xy.get("y")) if xy is not None else None
                if not n["dt"] or not n["i"] or not n["z"] or x is None or y is None:
                    continue
                s = rec.get("s")
                out.append((realm, guid, n["dt"], n["i"], n["z"], n["iz"] or 0, n["c"] or 0,
                            -1 if n["q"] is None else n["q"], s if s in STATUSES else "",
                            -1 if n["t0"] is None else n["t0"], -1 if n["ls"] is None else n["ls"],
                            -1 if n["mc"] is None else n["mc"], x, y))
    return ("ok" if out else "ok (no discoveries)"), out


def cached_records(sha, path, cache_dir=None):
    """records_of() for one file, through a parse cache keyed by its hash."""
    cp = os.path.join(cache_dir, sha + ".json") if cache_dir else None
    if cp:
        try:
            with io.open(cp, encoding="utf-8") as f:
                d = json.load(f)
            if d.get("version") == PARSER_VERSION:
                return d["status"], [tuple(r) for r in d["records"]]
        except (OSError, ValueError, KeyError):
            pass
    with open(path, "rb") as f:
        status, recs = records_of(f.read())
    if cp:
        os.makedirs(cache_dir, exist_ok=True)
        with io.open(cp + ".tmp", "w", encoding="utf-8") as f:
            json.dump({"version": PARSER_VERSION, "status": status, "records": recs}, f)
        os.replace(cp + ".tmp", cp)
    return status, recs


# ------------------------------------------------------------- coordinates
def load_bounds(path=BOUNDS_FILE):
    """{WorldMapArea id: dict(map, area_id, zone, name, left, right, top, bottom)}."""
    with io.open(path, encoding="utf-8", newline="") as f:
        lines = [l.rstrip("\n") for l in f if not l.startswith("#") and l.strip()]
    head = lines[0].split("\t")
    need = ["wma_id", "map", "area_id", "zone", "wma_name", "left", "right", "top", "bottom"]
    if head != need:
        raise ValueError(f"{path}: expected columns {need}, found {head}")
    out = {}
    for l in lines[1:]:
        v = dict(zip(head, l.split("\t")))
        out[int(v["wma_id"])] = dict(map=int(v["map"]), area_id=int(v["area_id"]), zone=v["zone"],
                                     name=v["wma_name"], left=float(v["left"]), right=float(v["right"]),
                                     top=float(v["top"]), bottom=float(v["bottom"]))
    return out


def to_server(b, x, y):
    """The inverse of the client's map projection: server (X, Y) from a 0..1 map position."""
    return b["top"] - y * (b["top"] - b["bottom"]), b["left"] - x * (b["left"] - b["right"])


# ------------------------------------------------------------------ folding
def _day(ts):
    if ts is None or ts <= 0:
        return ""
    return datetime.datetime.fromtimestamp(ts, datetime.timezone.utc).strftime("%Y-%m-%d")


_MODE = {}


def mode_of(realm):
    if realm not in _MODE:
        m = modes.classify(realm)
        _MODE[realm] = m.get("slug") or m.get("mode") or ""
    return _MODE[realm]


def build(found, bounds, cache_dir=None):
    """Fold every distinct file into pins, items and a per-file source list."""
    sources, best, held_by = [], {}, collections.defaultdict(set)
    for sha in sorted(found):
        path, is_bak, size = found[sha]
        status, recs = cached_records(sha, path, cache_dir)
        sources.append(dict(sha256=sha, kind="bak" if is_bak else "lua", bytes=size, status=status,
                            discoveries=len(recs), realms="|".join(sorted({r[0] for r in recs}))))
        for r in recs:
            key = (r[0], r[1])
            held_by[key].add(sha)
            # The most recently confirmed copy of a discovery wins: the addon
            # moves a pin's stored position as players confirm it.
            if key not in best or (r[10], r) > (best[key][10], best[key]):
                best[key] = r

    groups = collections.defaultdict(list)
    for key, r in best.items():
        groups[(r[2], r[3], r[4])].append((r[12], r[13], key, r))
    pins = []
    for (dt, item, z), pts in sorted(groups.items()):
        clusters = []
        for p in sorted(pts, key=lambda p: (p[0], p[1], p[2])):
            for c in clusters:
                if abs(p[0] - c[0][0]) <= NEAR and abs(p[1] - c[0][1]) <= NEAR:
                    c.append(p)
                    break
            else:
                clusters.append([p])
        for ms in clusters:
            recs = [m[3] for m in ms]
            x = sum(m[0] for m in ms) / len(ms)
            y = sum(m[1] for m in ms) / len(ms)
            b = bounds.get(z - 1)
            sx, sy = to_server(b, x, y) if b else ("", "")
            latest = max(recs, key=lambda r: (r[10], r))
            first = min((r[9] for r in recs if r[9] > 0), default=-1)
            pins.append(dict(type=TYPES.get(dt, f"type {dt}"), item=item, lc_zone=z, wma_id=z - 1,
                             zone=b["zone"] if b else "", map=b["map"] if b else "",
                             norm_x=f"{x:.4f}", norm_y=f"{y:.4f}",
                             server_x=f"{sx:.1f}" if b else "", server_y=f"{sy:.1f}" if b else "",
                             realms="|".join(sorted({r[0] for r in recs})),
                             modes="|".join(sorted({mode_of(r[0]) for r in recs} - {""})),
                             records=len(ms), files=len(set().union(*(held_by[m[2]] for m in ms))),
                             first_seen=_day(first), last_seen=_day(max(r[10] for r in recs)),
                             status=latest[8]))

    per = collections.OrderedDict()
    for p in pins:
        k = (p["type"], p["item"])
        e = per.setdefault(k, dict(type=p["type"], item=p["item"], pins=0, zones=set(), maps=set(),
                                   realms=set(), records=0, first_seen="", last_seen=""))
        e["pins"] += 1
        e["records"] += p["records"]
        if p["zone"]:
            e["zones"].add(p["zone"])
        if p["map"] != "":
            e["maps"].add(str(p["map"]))
        e["realms"].update(p["realms"].split("|"))
        if p["first_seen"] and (not e["first_seen"] or p["first_seen"] < e["first_seen"]):
            e["first_seen"] = p["first_seen"]
        if p["last_seen"] > e["last_seen"]:
            e["last_seen"] = p["last_seen"]
    items = []
    for e in per.values():
        items.append(dict(e, zones="|".join(sorted(e["zones"])),
                          maps="|".join(sorted(e["maps"], key=int)), realms="|".join(sorted(e["realms"]))))
    return dict(pins=pins, items=items, sources=sources, discoveries=len(best),
                unmapped=sorted({p["lc_zone"] for p in pins if p["map"] == ""}))


# ------------------------------------------------------------------ writing
def check_outputs(texts):
    """Refuse to publish anything shaped like an address, a drive path or an account tree."""
    for name, text in texts.items():
        m = LEAK.search(text)
        if m:
            raise ValueError(f"{name}: refusing to write {m.group(0)!r}-shaped content")


def tsv(cols, rows):
    out = ["\t".join(cols)]
    for r in rows:
        cells = [str(r[c]) for c in cols]
        if any("\t" in c or "\n" in c or "\r" in c for c in cells):
            raise ValueError("a cell would break its row")
        out.append("\t".join(cells))
    return "\n".join(out) + "\n"


def readme(res):
    pins, items, sources = res["pins"], res["items"], res["sources"]
    by_type = collections.Counter(p["type"] for p in pins)
    item_type = collections.Counter(i["type"] for i in items)
    status = collections.Counter(s["status"] for s in sources)
    realms = collections.Counter(r for p in pins for r in p["realms"].split("|"))
    placed = sum(1 for p in pins if p["server_x"] != "")
    ex = next((p for p in pins if p["type"] == "worldforged" and p["server_x"] != ""), None)
    L = ["# LootCollector: where Worldforged items and Mystic Scrolls are picked up\n",
         "Written by `tools/ingest_lootcollector.py` from the LootCollector addon's",
         "SavedVariables found in community submissions. Nothing here is edited by hand.\n",
         "## What a row is\n",
         "A **pin** is one place an item was looted. The position is where the looter stood when the",
         "loot window opened, not where the object is. It is usually within a few yards of the spawn,",
         "with a long tail. **There is no height (Z).** Records are crowd-synced between players by",
         "the addon, so `files` counts files that carry a pin, not people who saw it.\n",
         "`norm_x`/`norm_y` are the position on the zone map (0..1). `server_x`/`server_y` convert it",
         "with the client's own WorldMapArea bounds (Ascension's table, not stock's). `lc_zone` is the",
         "addon's zone id, which is the WorldMapArea id + 1. One pin is kept per (type, item, zone)",
         f"within {NEAR} map units, and its position is the mean of the copies it merges.\n",
         "## Counts from this run\n",
         "| | |", "|---|---:|",
         f"| distinct LootCollector files read | {len(sources):,} |",
         f"| distinct discoveries (realm, addon key) | {res['discoveries']:,} |"]
    for t in sorted(by_type):
        L.append(f"| {t} pins / items | {by_type[t]:,} / {item_type[t]:,} |")
    L.append(f"| pins with server coordinates | {placed:,} of {len(pins):,} |")
    if res["unmapped"]:
        L.append(f"| zone ids with no WorldMapArea row | {', '.join(map(str, res['unmapped'][:12]))} |")
    L += ["", "Files by status: " + ", ".join(f"{k} {v}" for k, v in sorted(status.items())) + ".", "",
          "Pins by realm: " + ", ".join(f"{k} {v:,}" for k, v in realms.most_common()) + ".\n"]
    if ex:
        L.append(f"Example: item {ex['item']} in {ex['zone']} at map ({ex['norm_x']}, {ex['norm_y']}) "
                 f"= server ({ex['server_x']}, {ex['server_y']}) on map {ex['map']}.\n")
    L += ["## Files\n",
          "| file | one row per |", "|---|---|",
          "| `pins.tsv` | pin: type, item, zone, map position, server X/Y, realms, first and last seen |",
          "| `items.tsv` | item and type: how many pins, in which zones and maps |",
          "| `sources.tsv` | distinct LootCollector file, by SHA-256: whether it could be read and what it held |\n",
          "## What was left out, on purpose\n",
          "Every LootCollector record also names the player who found it, the player who shared it and",
          "everyone who voted on it. None of that is read. Only the numeric fields, the addon's status",
          "word and the realm are taken, and the stage refuses to write anything shaped like an address",
          "or a path. The raw SavedVariables are never published.\n",
          "Upgrade tiers, drop rates and which object an item comes from are **not** in LootCollector's",
          "logs. See `docs/WORLDFORGED.md` in the tools repository.\n"]
    return "\n".join(L)


def main():
    found = discover()
    if not found:
        print("no LootCollector.lua files found; nothing to do")
        return 0
    bounds = load_bounds()
    res = build(found, bounds, CACHE_DIR)
    texts = {"pins.tsv": tsv(PIN_COLS, res["pins"]),
             "items.tsv": tsv(ITEM_COLS, res["items"]),
             "sources.tsv": tsv(SOURCE_COLS, res["sources"]),
             "README.md": readme(res)}
    check_outputs(texts)
    os.makedirs(OUT_DIR, exist_ok=True)
    for name, text in texts.items():
        with io.open(os.path.join(OUT_DIR, name), "w", encoding="utf-8", newline="\n") as f:
            f.write(text)
    by_type = collections.Counter(p["type"] for p in res["pins"])
    status = collections.Counter(s["status"] for s in res["sources"])
    print(f"  read {len(found)} distinct LootCollector file(s): "
          + ", ".join(f"{k} {v}" for k, v in sorted(status.items())))
    print(f"  {res['discoveries']:,} distinct discoveries -> "
          + ", ".join(f"{k} {v:,} pins" for k, v in sorted(by_type.items()))
          + f", {len(res['items']):,} items -> lootcollector/")
    if res["unmapped"]:
        print(f"  !! {len(res['unmapped'])} zone id(s) have no WorldMapArea row, so no server "
              f"coordinates: {res['unmapped'][:10]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
