"""Turn the merged record store into a publishable dataset tree.

Three views over the same merged records, because they answer different questions:

  by-mode/<mode>/<cache>.tsv   what a client running THAT mode was actually sent.
                               Authoritative for values: Ascension tunes the same
                               item differently per mode, so this is the table to
                               import if you care about numbers.
  union/<cache>.tsv            widest coverage -- every entry we have ever seen,
                               newest capture wins, with the mode and date it came
                               from in _modes/_captured.  Use it to answer "does
                               entry N exist", not "what are its stats on my realm".
  raw/<cache>.pack.gz          lossless.  Every distinct payload, byte for byte, so
                               a better decoder can be run over it later without
                               re-collecting anything.  Deterministic ordering, so
                               regenerating an unchanged store produces an identical
                               file and git sees no diff.

Dated observations keep their established newest-capture ordering. Browser
upload times are not capture dates. Undated observations add missing entries and
variants; later undated arrivals do not replace an earlier selected observation.  Losing variants are never deleted -- they stay in raw/ and in the index.

EVERYTHING BULKY IS GZIPPED
---------------------------
Uncompressed this dataset is ~756 MB and its largest single file is a 254 MB
itemcache -- GitHub warns over 50 MB and hard-rejects over 100 MB, so it simply
could not be published.  Gzipped it is ~104 MB with nothing over 16 MB, and the
reader needs no tooling we do not already ship: `gzip -d`, any language's
standard library, or `tools/unpack.py`, which also drops a mode's caches
straight into a client cache folder.

mtime=0 on every write, because gzip otherwise stamps the clock into its header
and an unchanged rebuild would then show up as a diff in every single file.
"""
import capture_dates
import os, io, sys, gzip, json, struct, time, contextlib, collections, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wdblib, modes, merge, scrub, config, build_cache
import wdb_decode_items, wdb_decode_creature, wdb_decode_gameobject
import wdb_decode_quest, wdb_decode_pagetext, wdb_decode_npctext_full

STORE = merge.STORE
OUT   = config.OUT


def decode_itemname(entry, payload):
    """[cstr Name][u32 InventoryType] -- verified exact-consumption on all 2,413.
    Returns (row, consumed) like every other decoder."""
    z = payload.find(b"\x00")
    if z < 0:
        return {"entry": entry, "name": "", "InventoryType": 0}, 0
    name = wdblib.decode_str(payload[:z])
    inv = struct.unpack_from("<I", payload, z + 1)[0] if z + 5 <= len(payload) else 0
    return {"entry": entry, "name": name.replace("\t", " ").replace("\r", " ").replace("\n", " "), "InventoryType": inv}, z + 5


DECODERS = {
    "itemcache":       (wdb_decode_items.decode_item,          wdb_decode_items.COLS),
    "creaturecache":   (wdb_decode_creature.decode_creature,   wdb_decode_creature.COLS),
    "gameobjectcache": (wdb_decode_gameobject.decode_go,       wdb_decode_gameobject.COLS),
    "questcache":      (wdb_decode_quest.decode_quest,         wdb_decode_quest.OUT_COLS),
    "pagetextcache":   (wdb_decode_pagetext.decode_pagetext,   wdb_decode_pagetext.COLS),
    "npccache":        (wdb_decode_npctext_full.decode_npctext, wdb_decode_npctext_full.COLS),
    "itemnamecache":   (decode_itemname,       ["entry", "name", "InventoryType"]),
}

PROV = ["_modes", "_captured", "_sources", "_locales"]

GZ = ".gz"


def write_gz(path, data):
    """Write bytes to a deterministic gzip file. `path` already ends in .gz."""
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.GzipFile(path, "wb", 9, mtime=0) as g:
        g.write(data)
    return path


@contextlib.contextmanager
def gz_text(path):
    """Text writer onto a deterministic gzip file, streaming.

    Streaming rather than building the whole view and then compressing it: the
    union itemcache view alone is over 100 MB of text.
    """
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with gzip.GzipFile(path, "wb", 9, mtime=0) as g:
        w = io.TextIOWrapper(g, encoding="utf-8", newline="\n")
        try:
            yield w
        finally:
            w.flush()
            w.detach()          # the GzipFile context manager closes it

# Plain-language purpose of every cache file, for someone arriving with no context.
# (opcode the client stored, what the file is for, what it does NOT contain)
CACHE_PURPOSE = {
    "itemcache": (
        "SMSG_ITEM_QUERY_SINGLE_RESPONSE",
        "Item definitions: name, icon, quality, item level, stats, damage, price, "
        "and on-use spells. The client writes one record each time it asks the server "
        "\"what is item N?\", so it never has to ask twice. This is what lets tooltips, "
        "bags and the auction house draw an item the moment you see it.",
        "Not loot tables, not drop sources, not where an item comes from."),
    "creaturecache": (
        "SMSG_CREATURE_QUERY_RESPONSE",
        "NPC and mob identity: name, the title under the name, creature type, family, "
        "rank (normal/elite/boss) and up to four display models. Written when the "
        "client first sees a creature and needs to label it.",
        "A thin slice only — no level, faction, health, loot or AI. Those never leave "
        "the server, so no cache can contain them."),
    "gameobjectcache": (
        "SMSG_GAMEOBJECT_QUERY_RESPONSE",
        "World objects: doors, chests, ore veins, herbs, mailboxes, campfires. Holds "
        "the object's type, display model, name, and 24 type-specific data fields "
        "(lock id, loot id, spell focus, and so on).",
        "Not placements. No cache records where objects stand in the world."),
    "questcache": (
        "SMSG_QUEST_QUERY_RESPONSE",
        "Quest text and structure: title, objectives, description, completion text, "
        "required kills and items, and rewards. Written when the client opens a quest.",
        "Not quest chains or availability conditions."),
    "npccache": (
        "SMSG_NPC_TEXT_UPDATE",
        "The gossip text an NPC greets you with. Eight alternative blocks per entry, "
        "each with a probability, male and female wording, language and emotes.",
        "Not vendor stock, not trainer lists."),
    "pagetextcache": (
        "SMSG_PAGE_TEXT_QUERY_RESPONSE",
        "The body text of readable objects — books, signs, letters on the ground. Pages "
        "chain together through NextPageId.",
        "Not the item that holds the book; that is in itemcache."),
    "itemnamecache": (
        "SMSG_ITEM_NAME_QUERY_RESPONSE",
        "A minimal item record: name and inventory slot only. The client uses it where "
        "it needs to label an item it has never fully queried, such as in search results.",
        "Deliberately tiny — no stats. Use itemcache for anything real."),
    "itemtextcache": (
        "SMSG_ITEM_TEXT_QUERY_RESPONSE",
        "The written body of mail and letters the player has read.",
        "NEVER PUBLISHED. This is other players' correspondence, not game data."),
    "wowcache": (
        "(undocumented)",
        "A small misc client cache whose format we have not verified.",
        "NEVER PUBLISHED while its contents are unconfirmed."),
}


def load_cache(cache):
    """[(entry, sha1, row, payload)] for one cache type, sorted."""
    idx = merge.read_tsv(f"{STORE}/{cache}/index.tsv", merge.IDX_COLS)
    if not idx:
        return []
    with open(f"{STORE}/{cache}/pack.bin", "rb") as f:
        blob = f.read()
    sources = {r["id"]: r for r in merge.read_tsv(merge.SOURCES, merge.SRC_COLS)}
    out = []
    capture_context = {}
    for r in idx:
        capture_dates.attach_context(r, sources, capture_context)
        r["locales"] = r.get("locales") or ",".join(sorted({sources[sid].get("locale") or "unknown" for sid in r["srcs"].split(",")}))
        r["_source_rows"] = sources
        off, size = int(r["offset"]), int(r["size"])
        payload = blob[off + 8: off + 8 + size]
        out.append((int(r["entry"]), r["sha1"], r, payload))
    out.sort(key=lambda t: (t[0], t[1]))
    return out


def pick_winners(recs, mode=None, locale=None):
    """entry -> (sha1, row, payload); dated captures take precedence; undated observations remain stable."""
    by_entry = collections.defaultdict(list)
    for entry, sha1, r, payload in recs:
        if mode is not None and mode not in r["modes"].split(","):
            continue
        supported = r.get("locales", "unknown").split(",")
        if locale is not None and locale not in supported:
            continue
        if locale is not None and r.get("_source_rows"):
            ids = [sid for sid in r["srcs"].split(",")
                   if (r["_source_rows"][sid].get("locale") or "unknown") == locale
                   and (mode is None or r["_source_rows"][sid]["slug"] == mode)]
            if not ids:
                continue
            dates = [r["_source_rows"][sid]["captured"] for sid in ids if r["_source_rows"][sid]["captured"]]
            r = dict(r, srcs=",".join(ids), locales=locale,
                     modes=",".join(sorted({r["_source_rows"][sid]["slug"] for sid in ids})),
                     first_captured=min(dates, default=""), last_captured=max(dates, default=""))
            # Date ranking and provenance must describe this locale's sources.
            capture_dates.attach_context(r, r["_source_rows"], {})
        by_entry[entry].append((sha1, r, payload))
    return {e: max(v, key=lambda t: (("enUS" in t[1].get("locales", "").split(",")) if locale is None else True,
                                    capture_dates.winner_rank(t[1], t[0], mode)))
            for e, v in by_entry.items()}


def write_view(path, cache, winners, note_cols=True, mode=None):
    """Write one decoded view. Every decoder returns (row, consumed_bytes); a correct
    field layout consumes EXACTLY the payload, so anything else is a decode failure
    and is counted, not silently written."""
    fn, cols = DECODERS[cache]
    allcols = list(cols) + (PROV if note_cols else [])
    bad = inexact = 0
    with gz_text(path + GZ) as f:
        f.write("\t".join(allcols) + "\n")
        for entry in sorted(winners):
            sha1, r, payload = winners[entry]
            try:
                res = fn(entry, payload)
                d, consumed = res[0], res[1]
            except Exception:
                bad += 1
                continue
            if consumed != len(payload):
                inexact += 1
            if note_cols:
                d = dict(d)
                d["_modes"] = r["modes"]
                d["_captured"] = capture_dates.winner_date(r, mode)
                d["_sources"] = len(r["srcs"].split(","))
                d["_locales"] = r.get("locales", "unknown")
            f.write("\t".join(str(d.get(c, "")).replace("\t", " ").replace("\n", " ")
                              for c in allcols) + "\n")
    if bad or inexact:
        raise RuntimeError(f"{cache}: {bad} undecodable and {inexact} inexact records; raw originals retained, publication blocked")
    return len(winners) - bad, bad, inexact


def prune(root, keep, protect=()):
    """Delete anything under `root` this run did not write.

    Output directories are per game mode, and a mode can stop existing -- a
    folder that used to classify as `unknown` gets a real label, or a submission
    is withdrawn.  Without this the old directory survives forever and we publish
    a mode the current data does not support, holding records that are now filed
    correctly somewhere else.  A reader has no way to tell the stale copy from
    the live one, so it has to go.
    """
    if not os.path.isdir(root):
        return []
    removed = []
    # Cached paths are resolved; Windows temporary paths can use 8.3 aliases.
    # Compare the same filesystem identity before pruning a reused output.
    keep = {os.path.normcase(os.path.realpath(p)) for p in keep}
    protect = {os.path.normcase(p) for p in protect}
    for dp, _dirs, files in os.walk(root, topdown=False):
        for fn in files:
            if os.path.normcase(fn) in protect:
                continue
            p = os.path.join(dp, fn)
            if os.path.normcase(os.path.realpath(p)) not in keep:
                os.remove(p)
                removed.append(os.path.relpath(p, root).replace("\\", "/"))
        if dp != root and not os.listdir(dp):
            os.rmdir(dp)
    return removed


def main():
    t0 = time.time()
    os.makedirs(OUT, exist_ok=True)
    srcs = merge.read_tsv(merge.SOURCES, merge.SRC_COLS)
    caches = sorted(d for d in os.listdir(STORE)
                    if os.path.isdir(f"{STORE}/{d}") and d in DECODERS)

    # every mode slug that actually carries records
    slugs = sorted({s["slug"] for s in srcs if s["slug"] and s["records"] != "0"})
    stats = collections.defaultdict(dict)      # cache -> view -> (rows, bad)
    mode_rows = collections.defaultdict(dict)  # slug -> cache -> rows
    written = []                               # every path this run produced

    reuse = build_cache.BuildCache('export', OUT)
    for cache in caches:
        inputs = build_cache.cache_inputs(cache, srcs, slugs)
        hit = reuse.load(cache, inputs)
        if hit:
            meta, paths = hit
            stats[cache] = meta['stats']
            for slug, count in meta['mode_rows'].items():
                mode_rows[slug][cache] = count
            written.extend(paths)
            print(f"  {cache:<18} reused verified decoded exports")
            continue
        start = len(written)
        category_ok = True
        recs = load_cache(cache)
        if not recs:
            continue
        # ---- union -----------------------------------------------------------
        w = pick_winners(recs)
        stats[cache]["union"] = write_view(f"{OUT}/union/{cache}.tsv", cache, w)
        written.append(f"{OUT}/union/{cache}.tsv{GZ}")
        # ---- per mode --------------------------------------------------------
        for slug in slugs:
            wm = pick_winners(recs, mode=slug)
            if not wm:
                continue
            result = write_view(f"{OUT}/by-mode/{slug}/{cache}.tsv", cache, wm, mode=slug)
            category_ok = category_ok and not any(result[1:])
            written.append(f"{OUT}/by-mode/{slug}/{cache}.tsv{GZ}")
            mode_rows[slug][cache] = len(wm)
        # Additive locale views: do not collapse translated variants into one row.
        for locale in sorted({loc for _, _, row, _ in recs for loc in row["locales"].split(",")}):
            if not re.fullmatch(r"[a-z]{2}[A-Z]{2}|unknown", locale):
                raise RuntimeError("Invalid locale in record provenance")
            for slug in [None] + slugs:
                selected = pick_winners(recs, mode=slug, locale=locale)
                if not selected:
                    continue
                rel = f"by-mode/{slug}" if slug else "union"
                path = f"{OUT}/by-locale/{locale}/{rel}/{cache}.tsv"
                write_view(path, cache, selected, mode=slug)
                written.append(path + GZ)
        # ---- raw, deterministic ---------------------------------------------
        os.makedirs(f"{OUT}/raw", exist_ok=True)
        buf = bytearray()
        for entry, sha1, r, payload in recs:      # already sorted (entry, sha1)
            buf += struct.pack("<II", entry, len(payload)) + payload
        write_gz(f"{OUT}/raw/{cache}.pack.gz", bytes(buf))
        with gz_text(f"{OUT}/raw/{cache}.index.tsv{GZ}") as f:
            f.write("\t".join(["entry", "sha1", "size", "modes", "first_captured",
                                "last_captured", "n_sources", "locales"]) + "\n")
            for e, s, r, _p in recs:
                f.write(f"{e}\t{s}\t{r['size']}\t{r['modes']}\t"
                        f"{r['first_captured']}\t{r['last_captured']}\t"
                        f"{len(r['srcs'].split(','))}\t{r['locales']}\n")
        written += [f"{OUT}/raw/{cache}.pack.gz", f"{OUT}/raw/{cache}.index.tsv{GZ}"]
        stats[cache]["records"] = len(recs)
        rows, bad, inexact = stats[cache]["union"]
        if inputs != build_cache.cache_inputs(cache, srcs, slugs):
            raise RuntimeError('merged inputs changed during export')
        if category_ok and not (bad or inexact):
            reuse.save(cache, inputs, written[start:],
                       {'stats': stats[cache],
                        'mode_rows': {slug: rows[cache] for slug, rows in mode_rows.items() if cache in rows}})
        flag = "" if not (bad or inexact) else f"  !! {bad} failed, {inexact} inexact"
        print(f"  {cache:<18} union {rows:>6}  distinct {len(recs):>6}{flag}")

    # Public locators are independent of machine layout and replay roots.
    merge.write_tsv(f"{OUT}/sources.tsv", merge.SRC_COLS, public_source_rows(srcs))
    write_docs(OUT, caches, slugs, stats, mode_rows, srcs)
    write_file_guide(OUT, stats)

    for root in (f"{OUT}/by-mode", f"{OUT}/union", f"{OUT}/raw", f"{OUT}/by-locale"):
        for gone in prune(root, written):
            print(f"  pruned stale {os.path.basename(root)}/{gone}")
    print(f"\nexported -> {OUT}  ({time.time()-t0:.0f}s)")


def public_source_rows(srcs):
    """Opaque stable locators; actual archive paths remain in private state."""
    rows = []
    for source in srcs:
        row = dict(source)
        sid, digest = str(row["id"]), row["sha256"]
        if not sid.isascii() or not sid.isdigit() or not re.fullmatch(r"[0-9a-f]{64}", digest):
            raise ValueError("Invalid source identity; refusing public provenance")
        row["path"] = f"sources/{sid}/{digest}.wdb"
        rows.append(row)
    return rows


def write_file_guide(out, stats):
    """What every file in this dataset is, in plain language, for a reader who has
    never opened a WDB and is not running any of our tooling."""
    L = ["# What each file is\n",
         "A short guide to every kind of file here — what the game used it for, what it",
         "contains, and what it deliberately does not. No tooling required to read this.\n",
         "## Where this data comes from\n",
         "The WoW client keeps a folder called `Cache\\WDB`. Whenever the server tells it",
         "about something — an item, a creature, a quest — the client writes that answer",
         "to disk so it never has to ask again. Those files are what players submitted.\n",
         "Two consequences shape everything here:\n",
         "1. **A cache only holds what that player actually looked at.** Nobody's cache is",
         "   complete. Merging many of them is the only way to approach full coverage.",
         "2. **A cache holds what the server said _at the time_.** When Ascension retunes",
         "   an item, older caches keep the older values. Both readings were true; they",
         "   are kept as variants rather than one silently overwriting the other.\n",
         "## The cache files\n"]
    for cache, (opcode, what, notnot) in CACHE_PURPOSE.items():
        n = stats.get(cache, {}).get("records")
        head = f"### `{cache}.wdb`"
        if n:
            head += f"  — {n:,} distinct records held"
        L += [head, "", f"*Stored from:* `{opcode}`", "", what, "",
              f"**Not in this file:** {notnot}", ""]
    L += lua_guide_section()
    L += catalogue_guide_section()
    L += ["## Everything is gzipped\n",
          "Every data file here ends in `.gz`. That is not a preference — uncompressed",
          "this dataset is about 756 MB and its largest single file is a 254 MB",
          "itemcache, and GitHub refuses to store any file over 100 MB. Compressed the",
          "whole thing is about 104 MB.\n",
          "Gzip is not an archive format like `.zip`; each `.gz` holds exactly one file,",
          "so `itemcache.wdb.gz` unpacks to `itemcache.wdb` and nothing else. Open one",
          "with 7-Zip, with `gunzip file.gz` on Mac or Linux, or straight from code",
          "(`gzip.open` in Python, `zcat` in a shell pipeline) without unpacking at all.\n",
          "To put a mode's caches into your own game, use the tool in this repository:\n",
          "```",
          "python tools/unpack.py                          list what is here",
          "python tools/unpack.py --mode <mode>            unpack one mode",
          "python tools/unpack.py --mode <mode> --into DIR ...into your cache folder",
          "```\n",
          "It refuses to overwrite an existing cache unless you pass `--force`, and keeps",
          "a `.bak` when it does. Your own cache is the only record of what your realm",
          "told your client, so it is not ours to discard.\n",
          "## File formats in this repository\n",
          "| file | format | read it with |",
          "|---|---|---|",
          "| `wdb/<mode>/*.wdb.gz` | the client's own binary cache format | `tools/unpack.py`, "
          "then the game client |",
          "| `by-mode/<mode>/*.tsv.gz` | tab-separated text, one row per entry | Excel, "
          "`pandas` (reads `.gz` directly), `LOAD DATA INFILE` |",
          "| `union/*.tsv.gz` | same, widest coverage across all modes | as above |",
          "| `raw/*.pack.gz` | `[entry u32][size u32][payload]` records | any "
          "language; no header to skip |",
          "| `raw/*.index.tsv.gz` | one row per stored record with its sha1 and provenance | "
          "text editor, after unpacking |",
          "| `sources.tsv` | every submitted file: realm, mode, capture date, counts | "
          "text editor (this one is not compressed) |",
          "| `catalogue/*.tsv` | objects and creatures observed in the world | "
          "text editor, Excel (not compressed either) |\n",
          "The `.wdb` files are the ones to use if you just want a better cache. The TSVs",
          "are for importing into a database. The `raw/` packs are for writing your own",
          "decoder without having to re-collect anything.\n"]
    L += ["| `lua/MobSpells.lua` | addon SavedVariables, plain Lua | a text editor, or "
          "the addon itself |",
          "| `lua/AIO_Client.lua` | addon SavedVariables, plain Lua | a text editor |",
          "| `lua/stock-client/*.lua` | item / creature / quest records as Lua 5.1 tables "
          "for an addon on a stock 3.3.5a client | any addon (see its README.md) |",
          "| `dbc/item_display_icons.tsv.gz` | Ascension display id -> icon name, and the "
          "stock display id with identical art | text editor, Excel |\n"]
    with open(f"{out}/FILE-GUIDE.md", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")


def catalogue_guide_section():
    """The `catalogue/` files are the only part of this dataset that is an
    observation rather than a recording, and a reader has to be told that
    before they build on it. Everything else here is bytes a server sent to a
    client; these are notes a player took while walking around."""
    d = os.path.join(config.OUT, "catalogue")
    if not os.path.isdir(d):
        return []
    rows, ids = {}, {}
    for name in ("gameobjects", "creatures"):
        p = os.path.join(d, name + ".tsv")
        if os.path.exists(p):
            with io.open(p, encoding="utf-8") as f:
                next(f, None)
                ids[name] = {ln.split("\t", 1)[0] for ln in f if ln.strip()}
            rows[name] = len(ids[name])
    if not rows:
        return []
    # Measured, not remembered: the overlap is the proof that these two id
    # spaces must stay apart, so it must not be a number that can go stale.
    both = len(ids.get("gameobjects", set()) & ids.get("creatures", set()))
    L = ["## The world catalogue (`catalogue/`)\n",
         "Plain tab-separated lists of **things that exist in the world**: "
         f"{rows.get('gameobjects', 0):,} objects and "
         f"{rows.get('creatures', 0):,} creatures, each with one example",
         "position, how many separate uploads saw it, and every zone it turned",
         "up in.\n",
         "These did not come from anybody's cache. Players walked the world with",
         "a dump addon running and sent in what it wrote, which makes this the",
         "one part of the dataset that is an *observation* rather than a",
         "recording of what a server said.\n",
         "> **It is a catalogue, not a spawn table.** Only the first sighting of",
         "> each object was recorded, common props like trees and chairs were",
         "> filtered out before it ever reached us, some of the dumps are",
         "> partial, and it is a snapshot that predates the newer zones. Load it",
         "> into a `gameobject` table and you will get a world that is mostly",
         "> empty and confidently wrong. `catalogue/README.md` gives the full",
         "> list of what it can and cannot tell you.\n",
         "> Objects and creatures are **separate id spaces** and are kept in",
         "> separate files. Creature 1622 and GameObject 1622 are unrelated",
         f"> things; {both} ids exist in both.\n",
         "These two are not gzipped, for the same reason `sources.tsv` is not:",
         "they are small enough to open, grep and read without unpacking",
         "anything.\n"]
    return L


def lua_guide_section():
    """The `lua/` outputs are not caches, and the difference matters to a reader:
    they are addon files, they were merged field-by-field rather than record-by-
    record, and unlike the caches they cannot be split per game mode."""
    state = {}
    sp = os.path.join(config.STORE, "lua", "state.json")
    if os.path.exists(sp):
        with open(sp, encoding="utf-8") as f:
            state = json.load(f)
    if not state.get("mobspells") and not state.get("aio"):
        return []
    L = ["## The addon files (`lua/`)\n",
         "These did not come from `Cache\\WDB`. They are addon **SavedVariables** — the",
         "file an addon writes when you log out — and they are published in that same",
         "format, so you can drop one into your account's own SavedVariables folder",
         "and the addon will read it.\n",
         "They are merged differently from the caches. A cache record is a fact the",
         "server stated, so two copies either agree or are separate variants. An addon",
         "entry is an *accumulator*: `amountMin` is the smallest hit that player ever",
         "saw. So numeric ranges are widened to cover every submission, observed flags",
         "are OR'd together, and only identity fields are expected to agree.\n",
         "> **These cannot be split by game mode.** The client files its caches in a",
         "> per-mode folder, which is what makes the per-mode `.wdb` sections possible.",
         "> These addons store one table for the whole account with no record of which",
         "> realm or mode an observation came from, so the merged file blends them.\n"]
    ms = state.get("mobspells") or {}
    if ms:
        mobs = sum(len(v) for v in ms.values())
        spells = sum(len(m.get("spells", {})) for v in ms.values() for m in v.values())
        L += [f"### `lua/MobSpells.lua` — {mobs:,} creatures across {len(ms):,} zones, "
              f"{spells:,} spell records", "",
              "What mobs actually cast, and how hard it hit: spell id, damage school,",
              "the observed damage range, swing speeds, and whether that ability was",
              "ever seen to crit, miss, get dodged, parried, blocked or resisted.",
              "",
              "This is the only data here that was never sent as a query response — it is",
              "*observed server behaviour*, reconstructed from combat log events, which",
              "is precisely the part that no cache file can contain.",
              "",
              "**Not in this file:** the `profileKeys` and `profiles` branches, which key",
              "settings by `<Character> - <Realm> - <Mode>`; and the per-record `lastGUID`",
              "and `lastTime` fields, which name a specific mob instance in a specific",
              "play session and mean nothing outside it.", ""]
    aio = state.get("aio") or {}
    if aio:
        builds = sum(len(v) for v in aio.values())
        L += [f"### `lua/AIO_Client.lua` — {len(aio):,} server-pushed addon files"
              + (f", {builds:,} distinct builds seen" if builds != len(aio) else ""), "",
              "AIO is the framework Ascension uses to push addon code from the server to",
              "the client at login; the client caches what it received here. So this is",
              "the custom UI itself — the source of the panels that do not exist in a",
              "stock 3.3.5a client.",
              "",
              "Entries are keyed by filename and deduplicated on the code's own hash, so",
              "one entry per distinct build. Where more than one build of a file was",
              "submitted, `AIO_Client.variants.json` lists them all and the `.lua` carries",
              "the most corroborated one.",
              "",
              "**Not in this file:** the `AIO_sv` branch — action bar layouts and frame",
              "positions, keyed by character name.", ""]
    return L


def write_docs(out, caches, slugs, stats, mode_rows, srcs):
    fam = {}
    for s in srcs:
        if s["slug"]:
            fam[s["slug"]] = modes.MODE_TABLE.get(s["mode"], (s["slug"], s["slug"], ""))[1]
    L = ["# Ascension client cache — merged dataset\n",
         f"_Merged from {len({s['sha256'] for s in srcs})} distinct submitted cache "
         f"files, the newest captured {max((s['captured'] for s in srcs), default='?')[:10]}._\n",
         "Community-submitted `Cache\\WDB` folders from the Ascension WoW client, merged",
         "at the **record** level. Identical records collapse to one row no matter how",
         "many people submitted them; genuinely different records for the same entry are",
         "kept as variants rather than overwritten, because Ascension tunes the same",
         "entry differently per game mode and between patches.\n",
         "## Layout\n",
         "| path | what it is |",
         "|---|---|",
         "| `wdb/<mode>/<cache>.wdb.gz` | merged caches in the client's own format. Drop them into your game. |",
         "| `by-mode/<mode>/<cache>.tsv.gz` | what a client on that mode was sent. **Use this for values.** |",
         "| `union/<cache>.tsv.gz` | widest coverage, newest capture wins. Use for existence, not stats. |",
         "| `raw/<cache>.pack.gz` | lossless payloads, `[entry u32][size u32][payload]`. |",
         "| `raw/<cache>.index.tsv.gz` | per record: sha1, size, modes, capture dates, corroboration count. |",
         "| `sources.tsv` | every submitted file: realm, mode, capture date, record count. |",
         "| `lua/` | merged addon SavedVariables, and the server-pushed UI code. |",
         "| `lua/stock-client/` | the item, creature and quest records as Lua 5.1 tables an addon on a **stock** client can load; `dbc/item_display_icons.tsv.gz` is the icon lookup they use. |",
         "| `mapdata/` | which **server** map ids exist as extracted terrain, and the DBC set submitted with them. An inventory, never the terrain itself — that is derived data a server operator regenerates from their own client. |",
         "| `lootcollector/` | where Worldforged items and Mystic Scrolls were picked up, read from players' LootCollector addon logs: item, zone, map position and server X/Y. A pin is where the looter stood, not the spawn, and has no height; `lootcollector/README.md` says the rest. |\n",
         "**The data files are gzipped.** Uncompressed this dataset is ~756 MB and its",
         "largest file is a 254 MB itemcache; GitHub rejects anything over 100 MB. Each",
         "`.gz` holds one file — open it with 7-Zip, `gunzip`, or directly from code.",
         "`python tools/unpack.py --mode <mode> --into <your cache folder>` does the",
         "whole job for a game mode, without overwriting the cache you already have.\n",
         "`_modes` / `_captured` / `_sources` columns on each row carry provenance:",
         "which modes produced that exact record, its newest capture date, and how many",
         "independent submissions corroborate it.\n",
         "## Coverage per cache type\n",
         "| cache | distinct records | union entries |", "|---|---:|---:|"]
    for c in caches:
        if "union" in stats[c]:
            L.append(f"| {c} | {stats[c]['records']:,} | {stats[c]['union'][0]:,} |")
    L += ["", "## Game modes\n",
          "| mode | family | " + " | ".join(caches) + " |",
          "|---|---|" + "---:|" * len(caches)]
    for slug in slugs:
        row = " | ".join(f"{mode_rows[slug].get(c, 0):,}" for c in caches)
        L.append(f"| `{slug}` | {fam.get(slug, slug)} | {row} |")
    L += ["", "### `unknown` is not a game mode\n",
          "It is the records we could not attribute: submissions that arrived without a",
          "realm folder, and whose contents do not identify one. Creature, gameobject",
          "and NPC records score identically against every mode we have measured --",
          "which is exactly why they cannot identify one -- so those are safe to use",
          "anywhere. Quest text can and does differ between modes, so treat",
          "`unknown/questcache` as a starting point rather than an authority.\n"]
    L += ["", "`free-pick`, `season-10-freepick` and `live-qa` are the same ruleset in",
          "different seasons — compare them, don't assume they agree; the item tuning",
          "genuinely changed between seasons.\n",
          "## Caveats\n",
          "- Capture date is the cache file's mtime: when that player's client last wrote",
          "  it, an upper bound on record age. There is no per-record timestamp in a WDB.",
          "- A cache only holds what that player actually looked at, so every submission",
          "  is a partial view. Coverage grows as more are merged.",
          "- Submissions zipped from above the realm folder lose their mode. Those are",
          "  re-identified by payload fingerprint against known-mode data and marked",
          "  `inferred:<agreement>/<entries compared>` in `sources.tsv`.",
          "- Strings are decoded UTF-8 first (the client writes UTF-8); a latin1 read",
          "  mangles every apostrophe and accent.\n"]
    with open(f"{out}/README.md", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    main()
