"""Turn the submitted world dumps into a catalogue of objects that exist.

A *catalogue*, deliberately, and not a spawn table. The person who collected
these said what they are, and every limit below is theirs, not a guess:

  * only the FIRST sighting of each object was written down;
  * trees, chairs and other common props were filtered out while dumping;
  * some of the files are partial dumps, included on purpose;
  * it is a snapshot from around the Bronzebeard launch, so the newer zones
    (Pale Reach, the custom starting zones) are simply not in it.

So a row here means "this object exists, and here is one place it was seen".
It never means "this object is here, and only here", and a missing object is
no evidence of anything at all. Building a `gameobject` spawn table out of
this would produce a world that is mostly empty and confidently wrong.

What it is genuinely good for is the question it was collected to answer:
**which objects exist, and which of them are Ascension's own** rather than
stock 3.3.5a -- with one worked example position for each.

Two id spaces, kept apart
-------------------------
`dump_npc_*` files are creatures, not GameObjects. They arrived in the same
format and were not mentioned in the description at all. Creature entry 1622
and GameObject entry 1622 are unrelated things, so they are catalogued
separately and never merged.

Stock or Ascension's own
------------------------
Answered by looking the id up in `stock_entries.txt` -- the template ids
AzerothCore's base world database ships, written by make_stock_entries.py --
and never by an id range. This stage used to assume stock GameObjects sit below
200000 and Ascension's above it. Both halves were false: worldforged treasure
like `90636 Forgotten Sack` sits far below it, stock WotLK props like `200296
Washing Tub` above it, and the rule undercounted Ascension's objects by half.

Type and model from the cache
-----------------------------
The dumps read a type and a model for under a quarter of the objects. The
game's own answer for most of the rest is already in this dataset: the merged
gameobjectcache records, which are what the server told a client about each
entry. They fill a blank; they never replace what a dump read.

This stage keeps no state. The catalogue is a pure function of the dump files
on disk, the shipped stock reference, and the merged cache store, which the
merge stage has finished writing before this one runs. Re-running is a
recompute, not a re-merge, and there is no cache of our own to go stale.
"""
import os, sys, io, csv, glob, gzip, json, bisect, collections

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import config

if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

OUT_DIR = os.path.join(config.OUT, "catalogue").replace("\\", "/")

STOCK_FILE = os.path.join(HERE, "stock_entries.txt")
# Which stock template table each id space is looked up in.
STOCK_TABLE = {"gameobject": "gameobject_template",
               "creature": "creature_template"}

# The cache carries a GameObject's type as a number and the dumps wrote a
# name. These are spelled the way the dumps spell them -- `Questgiver`,
# `MOTransport`, `AuraGen`, `Difficulty` -- because 1,751 objects carry both,
# and each of the 20 numbers seen there pairs with exactly one name. The
# numbers the dumps never used follow the 3.3.5a enum.
GO_TYPES = {0: "Door", 1: "Button", 2: "Questgiver", 3: "Chest", 4: "Binder",
            5: "Generic", 6: "Trap", 7: "Chair", 8: "SpellFocus", 9: "Text",
            10: "Goober", 11: "Transport", 12: "AreaDamage", 13: "Camera",
            14: "MapObject", 15: "MOTransport", 16: "DuelArbiter",
            17: "FishingNode", 18: "Ritual", 19: "Mailbox",
            20: "AuctionHouse", 21: "GuardPost", 22: "SpellCaster",
            23: "MeetingStone", 24: "FlagStand", 25: "FishingHole",
            26: "FlagDrop", 27: "MiniGame", 28: "LotteryKiosk",
            29: "CapturePoint", 30: "AuraGen", 31: "Difficulty",
            32: "BarberChair", 33: "DestructibleBuilding", 34: "GuildBank",
            35: "TrapDoor"}

# Field names as the dump files write them, mapped to ours.
FIELDS = {
    "id": "id",
    "name": "name",
    "type": "type",
    "displayid": "display_id",
    "map internal name": "zone",
    "mapx": "map_x",
    "mapy": "map_y",
    "internal map id": "map_id",
    "internal x": "x",
    "internal y": "y",
    "internal z": "z",
    "lockid": "lock_id",
    "locktype": "lock_type",
}


# --------------------------------------------------------------- finding them
def discover():
    """Every dump file we can see, with the submission it arrived in.

    A submission is the extract directory an archive was unpacked into, which
    is the closest thing to "one person sent this". Loose files dropped
    straight into the inbox are labelled by their own name instead.
    """
    seen, found = set(), []
    roots = [config.EXTRACT] + list(config.SCAN_ROOTS)
    for root in roots:
        if not os.path.isdir(root):
            continue
        for dp, dirs, files in os.walk(root):
            dirs[:] = [d for d in dirs if not d.startswith(".")]
            for fn in files:
                low = fn.lower()
                if not low.startswith("dump_"):
                    continue
                if not (low.endswith(".txt") or low.endswith(".csv")):
                    continue
                p = os.path.abspath(os.path.join(dp, fn))
                key = os.path.normcase(p)
                if key in seen:
                    continue
                seen.add(key)
                found.append((p, submission_of(p, root)))
    return sorted(found)


def submission_of(path, root):
    """Which upload a dump file arrived in."""
    rel = os.path.relpath(path, root).replace("\\", "/")
    head = rel.split("/")[0]
    return head if "/" in rel else "loose:" + head


def kind_of(path):
    """`gameobject` or `creature`, from the filename.

    The npc dumps are the same format and the same tooling, and nothing inside
    a record says which id space it belongs to. The filename is the only
    signal there is, so it is taken literally.
    """
    return ("creature" if os.path.basename(path).lower().startswith("dump_npc_")
            else "gameobject")


def zone_of(path):
    """The zone name from the filename, left exactly as written.

    Deliberately not spell-corrected. `Aszhara` and `Azshara` are two different
    zones in these dumps -- different internal map names, and not one id in
    common -- so "fixing" the apparent typo would silently merge two places.
    `UnGoroCrater` and `UngoroCrater` differ only in case, which NTFS treats as
    the same name; they survive only because they landed in separate extract
    directories, so nothing here may lowercase a zone either.
    """
    stem = os.path.splitext(os.path.basename(path))[0]
    for pre in ("dump_npc_", "dump_"):
        if stem.lower().startswith(pre):
            return stem[len(pre):]
    return stem


# ------------------------------------------------------------------ parsing
def parse_csv(text):
    """The `.csv` files: semicolon-separated, with a COMMA decimal point.

    `"-9430,91015625"` is one number. Read as ordinary comma CSV every
    coordinate in the set is destroyed silently, which is why the delimiter is
    stated rather than sniffed. Four rows also carry doubled quotes inside a
    quoted field, so this uses a real CSV reader and not a split.
    """
    rows = list(csv.reader(io.StringIO(text), delimiter=";", quotechar='"'))
    if not rows:
        return []
    head = [FIELDS.get(h, FIELDS.get(h.replace(" ", ""), h))
            for h in (c.strip().lower() for c in rows[0])]
    out = []
    for r in rows[1:]:
        if not any(c.strip() for c in r):
            continue
        out.append({k: v.strip() for k, v in zip(head, r)})
    return out


def parse_lua_ish(text):
    """The `.txt` files, which are not JSON however much they look like it.

    One single line of `{id:95696,Name:"Riding Cloak",...}` records: unquoted
    keys, no trailing newline, and no enclosing array. Object names contain
    commas (`Standing, Exterior, Medium - Brewfest`), colons (`Wanted Poster:
    ...`) and in nine files a backslash-escaped quote, so this scans the string
    with the quoting rules in hand instead of splitting on any character.
    """
    out = []
    for body in _records(text):
        rec = {}
        for part in _split_top(body, ","):
            k, sep, v = _split_once(part, ":")
            if not sep:
                continue
            key = _unquote(k.strip()).strip().lower()
            rec[FIELDS.get(key, FIELDS.get(key.replace(" ", ""), key))] = \
                _unquote(v.strip())
        if rec:
            out.append(rec)
    return out


def _records(s):
    """Yield the body of each top-level `{...}`, quotes and escapes respected."""
    depth, start, in_str, esc = 0, None, False, False
    for i, c in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == "{":
            if depth == 0:
                start = i + 1
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0 and start is not None:
                yield s[start:i]
                start = None
            elif depth < 0:            # a truncated partial dump; resynchronise
                depth = 0


def _split_top(s, sep):
    """Split on `sep`, ignoring any that sit inside a string or braces."""
    out, buf, depth, in_str, esc = [], [], 0, False, False
    for c in s:
        if in_str:
            buf.append(c)
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c in "{[":
            depth += 1
        elif c in "}]":
            depth -= 1
        elif c == sep and depth == 0:
            out.append("".join(buf)); buf = []
            continue
        buf.append(c)
    if buf:
        out.append("".join(buf))
    return out


def _split_once(s, sep):
    """Split at the first `sep` outside a string. Names contain colons."""
    in_str, esc = False, False
    for i, c in enumerate(s):
        if in_str:
            if esc:
                esc = False
            elif c == "\\":
                esc = True
            elif c == '"':
                in_str = False
            continue
        if c == '"':
            in_str = True
        elif c == sep:
            return s[:i], sep, s[i + 1:]
    return s, "", ""


def _unquote(v):
    """Strip one layer of quotes and undo the two escapes these files use."""
    v = v.strip()
    if len(v) >= 2 and v[0] == '"' and v[-1] == '"':
        v = v[1:-1]
        v = v.replace('\\"', '"').replace("\\\\", "\\")
    return v


def _num(v):
    """A coordinate, accepting either decimal separator. None if unreadable."""
    if v is None:
        return None
    v = str(v).strip().strip('"')
    if not v:
        return None
    try:
        return float(v.replace(",", "."))
    except ValueError:
        return None


def _int(v):
    n = _num(v)
    return int(n) if n is not None else None


# ------------------------------------------------------------------ judgement
def trusted_type(t, display_id):
    """The `Type` column, but only where it can be believed.

    `Door` is what the dumper wrote when it could not read an object's type:
    6,393 of the 6,395 `Door` rows have no `DisplayId` at all, while every row
    of every other type has one. `Grave Moss`, a herb node, is filed as a
    `Door`. Publishing that breakdown as though it were real would tell readers
    the world is four-fifths doors.

    So an untyped `Door` becomes blank -- unknown, stated as unknown. The two
    that do carry a DisplayId are kept, because those look like real doors.
    """
    t = (t or "").strip()
    if not t or (t == "Door" and not (display_id or "").strip()):
        return ""
    return t


def has_position(r):
    """Whether a sighting carries a usable position.

    28 records sit at exactly 0,0,0 with no real location -- `Bonfire Damage`,
    `RPG PROP TURLE SHELL`, `Xavian Waterfall`. The rows are still evidence the
    object exists, so they are catalogued; it is the coordinates that are
    dropped, not the object. Filter on the position, never on the row.
    """
    x, y, z = r.get("x"), r.get("y"), r.get("z")
    return None not in (x, y, z) and (x, y, z) != (0.0, 0.0, 0.0)


def load_stock(path=STOCK_FILE):
    """{table: sorted [(lo, hi)]} from stock_entries.txt.

    A missing or empty table stops the run. With no reference every object
    would come out as Ascension's own, and that reads exactly like a result.
    """
    tables, cur = {}, None
    with io.open(path, encoding="utf-8") as f:
        for n, ln in enumerate(f, 1):
            ln = ln.strip()
            if not ln or ln.startswith("#"):
                continue
            if ln.startswith("[") and ln.endswith("]"):
                cur = tables.setdefault(ln[1:-1], [])
                continue
            if cur is None:
                raise SystemExit(f"{path}:{n}: an id before any [table] line")
            lo, _, hi = ln.partition("-")
            cur.append((int(lo), int(hi or lo)))
    for t in STOCK_TABLE.values():
        if not tables.get(t):
            raise SystemExit(f"{path}: no [{t}] ids; refusing to guess which "
                             "entries are stock")
    return {t: sorted(r) for t, r in tables.items()}


def is_stock(ranges, gid):
    i = bisect.bisect_right(ranges, (gid, float("inf"))) - 1
    return i >= 0 and ranges[i][0] <= gid <= ranges[i][1]


def cache_attributes():
    """entry -> (type number, display id) from the merged gameobjectcache.

    The same records, and the same winner per entry, that the published
    union/gameobjectcache view shows -- read through export's own functions
    rather than a second copy of its winner rule, and from the store rather
    than the union file because this stage runs before export writes it.
    """
    import export
    decode = export.DECODERS["gameobjectcache"][0]
    out = {}
    for entry, (_sha1, _row, payload) in export.pick_winners(
            export.load_cache("gameobjectcache")).items():
        try:
            d = decode(entry, payload)[0]
        except Exception:
            continue                    # export counts these as bad and skips them too
        out[entry] = (d.get("type"), d.get("displayId"))
    return out


def apply_cache(folded, attrs):
    """Fill type and display id from the cache where no dump read them.

    A dump's value always stands. Where both exist they are compared and the
    agreement is counted, so the README states it from this run rather than
    from a number somebody measured once.
    """
    st = collections.Counter()
    for gid, e in folded.items():
        a = attrs.get(gid)
        if a is None:
            continue
        num, disp = a
        name = GO_TYPES.get(num)
        if e["types"]:
            if name:
                st["type_compared"] += 1
                st["type_agree"] += e["types"].most_common(1)[0][0] == name
        elif name:
            e["cache_type"] = name
            st["type_filled"] += 1
        else:
            st["type_unknown_number"] += 1
        if disp is None:
            continue
        if e["displays"]:
            st["display_compared"] += 1
            st["display_agree"] += e["displays"].most_common(1)[0][0] == str(disp)
        else:
            e["cache_display"] = str(disp)
            st["display_filled"] += 1
    return st


# ------------------------------------------------------------------ gathering
def sightings():
    """Read every dump file into a flat list of sightings, per id space."""
    per = {"gameobject": [], "creature": []}
    files, bad = 0, []
    for path, sub in discover():
        try:
            text = io.open(path, encoding="utf-8-sig", errors="replace").read()
        except OSError as e:
            bad.append((path, str(e)))
            continue
        rows = (parse_csv(text) if path.lower().endswith(".csv")
                else parse_lua_ish(text))
        zone_from_name, kind = zone_of(path), kind_of(path)
        n = 0
        for r in rows:
            gid = _int(r.get("id"))
            if gid is None:
                continue
            per[kind].append({
                "id": gid,
                "name": (r.get("name") or "").strip(),
                "type": trusted_type(r.get("type"), r.get("display_id")),
                "display_id": (r.get("display_id") or "").strip(),
                # The zone inside the record is the client's own internal map
                # name and is the one to trust; the filename is the fallback
                # for records that do not carry it.
                "zone": (r.get("zone") or "").strip() or zone_from_name,
                "map_id": _int(r.get("map_id")),
                "x": _num(r.get("x")), "y": _num(r.get("y")),
                "z": _num(r.get("z")),
                "map_x": _num(r.get("map_x")), "map_y": _num(r.get("map_y")),
                "lock_id": _int(r.get("lock_id")),
                "lock_type": (r.get("lock_type") or "").strip(),
                "submission": sub,
                "file": os.path.basename(path),
            })
            n += 1
        files += 1
        if not n:
            bad.append((path, "no records parsed"))
    return per, files, bad


def fold(rows):
    """One entry per id, keeping the best example rather than the first seen.

    "Best" is a sighting with real coordinates, chosen in a fixed order so the
    catalogue is identical whichever order the files were read in. Disagreeing
    names are all kept: where two dumps call an id different things that is
    worth seeing, not worth silently resolving.
    """
    by = collections.OrderedDict()
    for r in rows:
        e = by.get(r["id"])
        if e is None:
            e = by[r["id"]] = {
                "id": r["id"], "names": collections.Counter(),
                "types": collections.Counter(), "displays": collections.Counter(),
                "zones": set(), "submissions": set(), "files": set(),
                "sightings": 0, "best": None,
                "lock_id": None, "lock_type": "",
                "cache_type": "", "cache_display": "", "origin": "",
            }
        e["sightings"] += 1
        if r["name"]:
            e["names"][r["name"]] += 1
        if r["type"]:
            e["types"][r["type"]] += 1
        if r["display_id"]:
            e["displays"][r["display_id"]] += 1
        if r["zone"]:
            e["zones"].add(r["zone"])
        e["submissions"].add(r["submission"])
        e["files"].add(r["file"])
        if e["lock_id"] is None and r["lock_id"] is not None:
            e["lock_id"], e["lock_type"] = r["lock_id"], r["lock_type"]
        if has_position(r):
            key = (r["zone"], r["x"], r["y"], r["z"])
            if e["best"] is None or key < e["best"][0]:
                e["best"] = (key, r)
    return by


# ------------------------------------------------------------------- writing
# `origin` is last so that anything already reading these files by position
# keeps working.
COLS = ["id", "name", "type", "display_id", "sightings", "submissions",
        "zones", "example_zone", "example_map_id",
        "example_x", "example_y", "example_z", "example_map_x", "example_map_y",
        "lock_id", "lock_type", "other_names", "seen_in", "zone_list", "origin"]


def _cell(v):
    """A TSV cell that cannot break the row it is in."""
    if v is None:
        return ""
    s = str(v)
    return s.replace("\t", " ").replace("\r", " ").replace("\n", " ").strip()


def _coord(v):
    """Coordinates to 3 decimals: the dumps carry 11, which is false precision
    for a position that was only ever one sighting out of many."""
    return "" if v is None else f"{v:.3f}"


def write_tsv(path, folded):
    n = 0
    with io.open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\t".join(COLS) + "\n")
        for gid in sorted(folded):
            e = folded[gid]
            best = e["best"][1] if e["best"] else {}
            names = [nm for nm, _c in e["names"].most_common()]
            f.write("\t".join(_cell(v) for v in [
                gid,
                names[0] if names else "",
                final_type(e),
                (e["displays"].most_common(1)[0][0] if e["displays"]
                 else e["cache_display"]),
                e["sightings"], len(e["submissions"]), len(e["zones"]),
                best.get("zone", ""), best.get("map_id"),
                _coord(best.get("x")), _coord(best.get("y")),
                _coord(best.get("z")),
                _coord(best.get("map_x")), _coord(best.get("map_y")),
                e["lock_id"], e["lock_type"],
                " | ".join(names[1:]),
                " | ".join(sorted(e["submissions"])),
                " | ".join(sorted(e["zones"])),
                e["origin"],
            ]) + "\n")
            n += 1
    return n


def final_type(e):
    """What a dump read, else what the cache said, else unknown."""
    return e["types"].most_common(1)[0][0] if e["types"] else e["cache_type"]


def set_origins(folded, ranges):
    for gid, e in folded.items():
        e["origin"] = "stock" if is_stock(ranges, gid) else "ascension"


def summarise(folded):
    s = {"ids": len(folded)}
    s["ascension"] = sum(1 for e in folded.values() if e["origin"] == "ascension")
    s["stock"] = s["ids"] - s["ascension"]
    # The two ways the old id-range rule was wrong, as named rows, so the
    # README shows its evidence instead of asserting it.
    low = sorted(g for g, e in folded.items()
                 if e["origin"] == "ascension" and g < 200000)
    high = sorted(g for g, e in folded.items()
                  if e["origin"] == "stock" and g >= 200000)
    s["low_ascension"], s["high_stock"] = len(low), len(high)
    s["by_range"] = sum(1 for g in folded if g >= 200000)
    # Worldforged chests make the point best -- they are what the dumps were
    # collected to find -- so prefer them when there are enough.
    chests = [g for g in low if final_type(folded[g]) == "Chest"]
    pick = chests if len(chests) >= 4 else low
    s["low_examples"] = [(g, _first_name(folded[g]))
                         for g in pick[::max(1, len(pick) // 4)][:4]]
    s["high_examples"] = [(g, _first_name(folded[g])) for g in high[:4]]
    s["typed"] = sum(1 for e in folded.values() if e["types"])
    s["typed_cache"] = sum(1 for e in folded.values()
                           if not e["types"] and e["cache_type"])
    s["untyped"] = sum(1 for e in folded.values() if not final_type(e))
    s["displayed"] = sum(1 for e in folded.values() if e["displays"])
    s["displayed_cache"] = sum(1 for e in folded.values()
                               if not e["displays"] and e["cache_display"])
    s["no_position"] = sum(1 for e in folded.values() if not e["best"])
    s["unnamed"] = sum(1 for e in folded.values() if not e["names"])
    s["name_conflicts"] = sum(1 for e in folded.values() if len(e["names"]) > 1)
    s["corroborated"] = sum(1 for e in folded.values() if len(e["submissions"]) > 1)
    s["zones"] = len({z for e in folded.values() for z in e["zones"]})
    s["types"] = collections.Counter(final_type(e) for e in folded.values()
                                     if final_type(e))
    return s


def _first_name(e):
    return e["names"].most_common(1)[0][0] if e["names"] else "(unnamed)"


def stock_revision(path=STOCK_FILE):
    """The AzerothCore revision the stock reference was written from."""
    with io.open(path, encoding="utf-8") as f:
        for ln in f:
            if ln.startswith("# Source revision:"):
                rev = ln.split(":", 1)[1].split()
                return " ".join([rev[0][:10]] + rev[1:]) if rev else "unknown revision"
    return "unknown revision"


def readme(go, cr, files, st, stock_rev):
    """Say what this is, and what it is not, before anybody builds on it."""
    L = ["# The world catalogue (`catalogue/`)\n",
         "Two lists of things that exist in the world: **objects** and",
         "**creatures**. They came from players walking the world with a dump",
         "addon running, not from the game's own cache files, so they are the",
         "one part of this dataset that is an observation rather than a",
         "recording.\n",
         "## Read this before you use it\n",
         "**This is a catalogue, not a spawn table.** The person who collected it",
         "said so plainly, and there are four separate reasons, each enough on",
         "its own:\n",
         "1. **Only the first sighting of each object was written down.** A row",
         "   means *this exists, and here is one place it was seen*. It never",
         "   means *this is here, and only here*.",
         "2. **Trees, chairs and other common props were filtered out** during",
         "   the dump. An object being absent proves nothing whatsoever.",
         "3. **Some of the dumps are partial**, and were included deliberately,",
         "   so coverage is uneven between them by design.",
         "4. **It is a snapshot from around the Bronzebeard launch**, about a",
         "   year before it was submitted. The newer zones -- Pale Reach, the",
         "   custom starting zones -- are not in it at all.\n",
         "If you load this into a `gameobject` table you will get a world that",
         "is mostly empty and confidently wrong. What it is genuinely good for",
         "is the question it was collected to answer: **which objects exist, and",
         "which of them are Ascension's own** rather than stock 3.3.5a.\n",
         "## The two files\n",
         f"| file | rows | what it lists |",
         "|---|---:|---|",
         f"| `gameobjects.tsv` | {go['ids']:,} | doors, chests, herb nodes, "
         "portals, props -- anything the client calls a GameObject |",
         f"| `creatures.tsv` | {cr['ids']:,} | NPCs, from the `dump_npc_*` "
         "files |\n",
         "**They are separate id spaces and must stay separate.** Creature 1622",
         "and GameObject 1622 are unrelated things. The creature dumps were not",
         "even mentioned in the submission -- they turned up inside it -- so",
         "they are catalogued on their own rather than folded in.\n",
         "## What the columns mean\n",
         "| column | meaning |",
         "|---|---|",
         "| `id` | the entry id the server uses |",
         "| `name` | the name most dumps agreed on |",
         "| `type` | object type: what a dump read, else what the game's own "
         "cache says; **blank where neither knows** (see below) |",
         "| `display_id` | the model id, from a dump, else from the cache; "
         "blank if neither has it |",
         "| `sightings` | how many rows across all files mention this id |",
         "| `submissions` | how many separate uploads saw it -- uploads, not "
         "people (see below) |",
         "| `zones` | how many distinct zones it was seen in |",
         "| `example_*` | **one** position it was seen at, not its only one |",
         "| `lock_id`, `lock_type` | for locked objects, where recorded |",
         "| `other_names` | every other name any dump gave this id |",
         "| `seen_in` | which uploads it came from |",
         "| `zone_list` | every zone it was seen in |",
         "| `origin` | `stock` if stock 3.3.5a has this entry, `ascension` if "
         "it does not (see below) |\n",
         "## Reading these files\n",
         "They are tab-separated with no quoting at all, and some names begin",
         "with a double quote: `\"Evidence\"`, `\"Borrowed\" Dark Iron Signet`.",
         "A CSV reader left on its defaults takes that quote for field quoting",
         "and strips it without a word. Turn quoting off -- in Python,",
         "`csv.reader(f, delimiter=\"\\t\", quoting=csv.QUOTE_NONE)`.\n",
         "## Which of these are Ascension's own?\n",
         "The `origin` column. An entry is `stock` if stock 3.3.5a's template",
         "table has it -- looked up in the ids AzerothCore's base world",
         "database ships, `stock_entries.txt` beside the tool -- and",
         "`ascension` if it does not. That is everything Ascension added,",
         "whether they made it or brought it back from a later expansion.\n",
         "| file | ascension | stock |",
         "|---|---:|---:|",
         f"| `gameobjects.tsv` | {go['ascension']:,} | {go['stock']:,} |",
         f"| `creatures.tsv` | {cr['ascension']:,} | {cr['stock']:,} |\n",
         "**This used to be answered by an id range, and the range was",
         "wrong.** Earlier versions of this file said stock objects sit below",
         f"200000 and Ascension's above it. {go['low_ascension']:,} of",
         "Ascension's objects sit below it -- "
         + ", ".join(f"`{g} {n}`" for g, n in go["low_examples"]) + " --",
         f"and {go['high_stock']:,} stock objects sit above it -- "
         + ", ".join(f"`{g} {n}`" for g, n in go["high_examples"]) + ".",
         f"The range called {go['by_range']:,} objects Ascension's; the stock",
         f"table says {go['ascension']:,}.\n",
         "An id being stock says the entry exists in 3.3.5a, not that",
         "Ascension left it alone: a few stock entries carry a different name",
         "here. `origin` answers only the first question.\n",
         "## Where `type` and `display_id` come from\n",
         "First from the dumps, and there is a catch. The dumper wrote `Door`",
         "when it could not read an object's type, and it could not read it",
         "most of the time: of the 6,395 rows marked `Door`, 6,393 have no",
         "model id either, while every row of every other type has one.",
         "`Grave Moss` -- a herb -- is filed as a `Door`. So an untyped `Door`",
         "counts as **unknown**, not as a door.\n",
         "Where no dump read a value, it comes from the game itself: the",
         "gameobjectcache records elsewhere in this dataset, which are the",
         "server's own description of each entry. That supplied the type of",
         f"{go['typed_cache']:,} objects and the model of "
         f"{go['displayed_cache']:,}. A dump's value is never replaced. Where",
         "both exist they were compared on this run: the types agree for",
         f"{st['type_agree']:,} of {st['type_compared']:,} objects and the",
         f"models for {st['display_agree']:,} of {st['display_compared']:,}.\n",
         f"**{go['untyped']:,} objects are still of unknown type**: no dump",
         "read it and no cache record covers them. The types as published:\n"]
    for t, n in go["types"].most_common():
        L.append(f"* `{t}` — {n:,}")
    L += ["",
          "## Honest gaps\n",
          f"* **{go['no_position']:,} objects have no usable position.** Their",
          "  coordinates were recorded as exactly 0,0,0. The object is real and",
          "  stays listed; only its position is missing.",
          f"* **{go['unnamed']:,} objects have no name** in any dump.",
          f"* **{go['name_conflicts']:,} objects were given more than one name.**",
          "  All of them are kept, in `other_names`, rather than one being",
          "  quietly chosen and the rest dropped.",
          f"* **{go['corroborated']:,} objects were seen in more than one",
          "  upload.** Uploads are not people. One contributor can send several",
          "  -- `docs/GAMEOBJECT-DUMPS.md` says who sent what -- so a second",
          "  upload is a second sighting, not a second witness.",
          f"* Zone names are printed exactly as the dumps wrote them. `Aszhara`",
          "  and `Azshara` are **two different zones** here, not a typo -- they",
          "  have different internal map names and not one id in common.\n",
          f"Built from {files} dump files across {go['zones']} zones. Stock",
          f"reference: AzerothCore base world database, {stock_rev}.\n"]
    return "\n".join(L) + "\n"


def main():
    per, files, bad = sightings()
    if not files:
        print("no dump_*.txt / dump_*.csv files found; nothing to catalogue")
        return 0
    stock = load_stock()
    os.makedirs(OUT_DIR, exist_ok=True)
    go = fold(per["gameobject"])
    cr = fold(per["creature"])
    set_origins(go, stock[STOCK_TABLE["gameobject"]])
    set_origins(cr, stock[STOCK_TABLE["creature"]])
    # Creatures stay as the dumps left them: the shared `type` column means a
    # GameObject type, and a creature's type is a different thing entirely.
    st = apply_cache(go, cache_attributes())
    n_go = write_tsv(os.path.join(OUT_DIR, "gameobjects.tsv"), go)
    n_cr = write_tsv(os.path.join(OUT_DIR, "creatures.tsv"), cr)
    sgo, scr = summarise(go), summarise(cr)

    with io.open(os.path.join(OUT_DIR, "README.md"), "w",
                 encoding="utf-8", newline="\n") as f:
        f.write(readme(sgo, scr, files, st, stock_revision()))

    print(f"  read {files} dump file(s): "
          f"{len(per['gameobject']):,} object sightings, "
          f"{len(per['creature']):,} creature sightings")
    print(f"  gameobjects.tsv  {n_go:,} objects   "
          f"({sgo['ascension']:,} Ascension's own, {sgo['stock']:,} stock; "
          f"{sgo['no_position']:,} with no position)")
    print(f"    type: {sgo['typed']:,} from dumps + {sgo['typed_cache']:,} from "
          f"cache, {sgo['untyped']:,} unknown; agree {st['type_agree']:,}/"
          f"{st['type_compared']:,}")
    print(f"    model: {sgo['displayed']:,} from dumps + "
          f"{sgo['displayed_cache']:,} from cache; agree "
          f"{st['display_agree']:,}/{st['display_compared']:,}")
    if st["type_unknown_number"]:
        print(f"  !! {st['type_unknown_number']:,} cache records carry a type "
              "number GO_TYPES does not name; left blank")
    print(f"  creatures.tsv    {n_cr:,} creatures ({scr['ascension']:,} "
          f"Ascension's own, {scr['stock']:,} stock; {scr['no_position']:,} "
          f"with no position)")
    print(f"  across {sgo['zones']} zones; {sgo['corroborated']:,} objects "
          f"seen by more than one upload -> catalogue/README.md")
    for p, why in bad:
        print(f"  !! {os.path.basename(p)}: {why}")
    # A file we could not read is worth saying out loud, but it is not a reason
    # to stop the pipeline: the rest of the catalogue is still correct.
    return 0


if __name__ == "__main__":
    sys.exit(main())
