"""Merge addon SavedVariables (.lua) submissions, and write them back as .lua.

Why this is a separate stage from merge.py
------------------------------------------
A .wdb cache is a flat list of independent records, so merging is set union on a
record hash. A SavedVariables file is a *tree*, and different branches of the
same tree need opposite treatment:

    MobSpellsDB.global.mobs      server-observed combat data  -> merge and keep
    MobSpellsDB.profileKeys      "<Character> - <Realm> - <Mode>" -> pure PII
    AIO_sv_Addons                server-pushed addon code     -> merge and keep
    AIO_sv                       hotbars keyed by character   -> character state

So there is no generic "merge two lua files" operation. Each supported file gets
an explicit spec below saying which globals hold the payload and how to combine
two readings of the same leaf. Anything not in the registry is not published at
all -- same allow-list rule as scrub.py, for the same reason.

The three merge shapes
----------------------
Deciding this per file is the whole job, because the same operation is right in
one file and destructive in the next:

  accumulator   MobSpells: `amountMin` is the smallest hit that submitter saw,
                so bounds widen and observed flags OR. Newest-wins would throw
                away a wider range someone else recorded.
  observation   Auctionator: an auction price was true at a moment and is not
                true now. Widening a price range says something nobody observed,
                so the newest capture wins outright.
  existence     GatherMate: a herb node is either there or not. Union, and a
                disagreement about what is at one spot is a conflict to report
                rather than a value to average.

Only the caches can be split per game mode -- the client files them in a
per-mode folder. Most of these addons keep one account-wide table with no record
of which realm an observation came from, and are published unsplit. Auctionator
is the exception: it keys its own database by "<Realm> - <Mode>", so it splits.
"""
import os, sys, json, hashlib, time, re, copy

PROCESSING_REVISION = "2026-09-11.3"

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config, modes, luaser, harvestmerge, reference_merge, capture_dates

STORE = os.path.join(config.STORE, "lua").replace("\\", "/")
STATE = os.path.join(STORE, "state.json").replace("\\", "/")
OUT = os.path.join(config.OUT, "lua").replace("\\", "/")
ADDONS_OUT = os.path.join(OUT, "addons").replace("\\", "/")

SCAN_ROOTS = config.SCAN_ROOTS + [config.EXTRACT]

# A leaf field is combined by one of these rules.
MIN, MAX, OR, FIRST, NEWEST, DROP = "min", "max", "or", "first", "newest", "drop"


def _rule_for(spec, field):
    return spec["fields"].get(field, spec.get("default", FIRST))


# --------------------------------------------------------------- the registry

SPECS = {
    **{name: {"globals": (global_name,), "shape": "references", "fields": {}, "why": "filtered class, item, quest and spell reference snapshots"} for name, global_name in reference_merge.GLOBALS.items()},
    # MobSpells: an addon that records what mobs cast and how hard they hit.
    # Nothing here comes from the client's own files -- it is observation of
    # server behaviour, which is exactly what is otherwise unrecoverable.
    "mobspells.lua": {
        "globals": ("MobSpellsDB",),
        "payload": ("global", "mobs"),
        "shape": "accumulator",
        "fields": {
            # ephemeral per-session values with no preservation value; lastGUID
            # is also a live object GUID, which the publish audit rejects.
            "lastGUID": DROP,
            "lastTime": DROP,
            "amountMin": MIN, "minSpeed": MIN,
            "amountMax": MAX, "maxSpeed": MAX,
            "critical": OR, "resisted": OR, "missing": OR, "dodge": OR,
            "parry": OR, "glancing": OR, "blocked": OR, "resist": OR,
            "reflect": OR, "interruptable": OR, "immune": OR, "absorbed": OR,
            "school": FIRST, "schoolName": FIRST,
        },
        "default": FIRST,
        "why": "what mobs cast and how hard it hit, observed from combat events",
    },
    # AIO pushes addon Lua from the server to the client and caches it here.
    # This is server-authored code -- the custom UI itself -- keyed by filename
    # with a CRC, so a differing CRC means a different server build.
    "aio_client.lua": {
        "globals": ("AIO_sv_Addons",),
        "shape": "code",
        "fields": {},
        "why": "the addon code Ascension pushes to the client at login",
    },
    # Auctionator keys its own database by "<Realm> - <Mode>", which makes this
    # the one addon here whose data carries its own game mode.
    "auctionator_price_database.lua": {
        "globals": ("AUCTIONATOR_PRICE_DATABASE",),
        "shape": "prices",
        "fields": {},
        "why": "auction house prices seen per realm and game mode",
    },
    # Gather node positions. No cache and no query response carries these: the
    # client only ever learns them by a player standing next to one.
    "gathermate2.lua": {
        # GatherMate2DB itself is profiles/config and is deliberately absent.
        "globals": ("GatherMate2HerbDB", "GatherMate2MineDB", "GatherMate2FishDB",
                    "GatherMate2GasDB", "GatherMate2TreeDB", "GatherMate2TreasureDB"),
        "shape": "nodes",
        "fields": {},
        "why": "herb, mine, fishing and treasure node positions by zone",
    },
    # A sniffer someone ran against Ascension's custom client events.
    "coasniff.lua": {
        "globals": ("CoASniffDB",),
        "shape": "events",
        "fields": {},
        "why": "the custom client events Ascension fires, and their argument shapes",
    },
    # An in-game harvester someone ran across several realms and modes. It is
    # the only source here for two things no .wdb can hold: what an NPC had for
    # sale (SMSG_LIST_INVENTORY is answered into a frame and never written to
    # disk) and which gossip text an NPC actually uses. Branch policy, the $n
    # restoration and the snapshot-vs-union split all live in harvestmerge.py.
    "wildcardharvest.lua": {
        "globals": (harvestmerge.GLOBAL,),
        "shape": "harvest",
        "fields": {},
        "why": "vendor pages, NPC gossip and the client's own reference tables",
    },
}

# Files whose name carries a date or a label rather than being fixed. Matched
# only after the exact table above misses, so an exact name always wins.
PATTERNS = (
    (re.compile(r"^harvest_v[456]_early\.lua$", re.I), "wildcardharvest.lua"),
    (re.compile(r"^wildcardharvest[\w.\-()' ]*\.lua$", re.I), "wildcardharvest.lua"),
)

ALIASES = {"mobspells.lua.bak": "mobspells.lua",
           "aio_client.lua.bak": "aio_client.lua",
           "auctionator_price_database.lua.bak": "auctionator_price_database.lua",
           "gathermate2.lua.bak": "gathermate2.lua",
           "coasniff.lua.bak": "coasniff.lua"}


def spec_for(filename):
    key = filename.lower()
    key = ALIASES.get(key, key)
    if key in SPECS:
        return key, SPECS[key]
    # A .bak is the addon's own backup of the same file, so it resolves to the
    # same spec -- and to the same submission, see submission_of().
    bare = key[:-4] if key.endswith(".bak") else key
    for rx, target in PATTERNS:
        if rx.match(bare):
            return target, SPECS[target]
    return key, SPECS.get(key)


# ------------------------------------------------------------------ discovery

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def group_of(path):
    """Folder that gives this file its provenance, mirroring merge.py.

    SavedVariables live in WTF/Account/<acct>/SavedVariables/ (account-wide) or
    .../<Realm - Mode>/<Char>/SavedVariables/ (per character). The per-character
    one carries a mode; the account-wide one does not.
    """
    parts = path.replace("\\", "/").split("/")
    if "SavedVariables" in parts:
        i = parts.index("SavedVariables")
        if i >= 1:
            prev = parts[i - 1]
            # WTF/Account/<acct>/SavedVariables -> account-wide, no mode
            if i >= 2 and parts[i - 2].lower() == "account":
                return ""
            return prev
    # A file dropped loose into the inbox has no provenance folder at all. Its
    # containing directory is our own bookkeeping ("archive"), not a realm, and
    # returning it would invent a game mode that was never observed.
    if os.path.abspath(path).startswith(os.path.abspath(config.INBOX)):
        return ""
    return os.path.basename(os.path.dirname(path))


def submission_of(path):
    """Which submission a file arrived in -- the unit that stands for a person.

    `srcs` counts distinct files, which is not the same thing: one contributor
    with several characters produces several SavedVariables files, and two
    contributors who both captured the same bytes produce one. For deciding
    whether executable code has been independently confirmed, the archive it
    was submitted in is the closest thing we have to "somebody else said so".
    """
    ap = os.path.abspath(path)
    ex = os.path.abspath(config.EXTRACT)
    if ap.startswith(ex):
        rel = os.path.relpath(ap, ex).replace("\\", "/")
        return "archive:" + rel.split("/")[0]
    # X.lua and X.lua.bak are the same submission: the .bak is the addon's own
    # backup of that very file, not a second person confirming anything.
    base = os.path.basename(ap)
    if base.lower().endswith(".bak"):
        base = base[:-4]
    return "loose:" + base


def backfill_submissions(state):
    """Give older state entries the submission field they were written without.

    Cheap and idempotent: the files are on disk and their hashes are their
    keys, so this is a rescan, not a re-merge.
    """
    missing = [sid for sid, m in state.get("sources", {}).items()
               if not m.get("submission")]
    if not missing:
        return 0
    want, fixed = set(missing), 0
    for _key, path in discover():
        sid = sha256(path)
        if sid in want:
            state["sources"][sid]["submission"] = submission_of(path)
            fixed += 1
    return fixed


def discover():
    """Every .lua under the scan roots that we have a spec for."""
    found, seen = [], set()
    for root in SCAN_ROOTS:
        if not os.path.isdir(root):
            continue
        for dp, _d, fs in os.walk(root):
            for fn in fs:
                if not fn.lower().endswith((".lua", ".lua.bak")):
                    continue
                key, spec = spec_for(fn)
                if not spec:
                    continue
                p = os.path.join(dp, fn).replace("\\", "/")
                rp = os.path.realpath(p)
                if rp in seen:
                    continue
                seen.add(rp)
                found.append((key, p))
    return sorted(found)


# --------------------------------------------------------------------- merge

def combine(rule, old, new):
    if rule == DROP:
        return None
    if old is None:
        return new
    if new is None:
        return old
    try:
        if rule == MIN:
            return min(old, new)
        if rule == MAX:
            return max(old, new)
    except TypeError:
        return old          # mixed types: keep what we had rather than guess
    if rule == OR:
        return bool(old) or bool(new)
    if rule == NEWEST:
        return new
    return old              # FIRST


def merge_leaf(spec, dst, src_table, conflicts, where):
    """Fold one leaf table (a spell's stats) into the accumulated dict."""
    for k, v in src_table.hash.items():
        rule = _rule_for(spec, k)
        if rule == DROP:
            continue
        if isinstance(v, luaser.Table):
            continue        # no nested tables at this level in any known spec
        if rule == FIRST and k in dst and dst[k] != v:
            conflicts.append((where + (k,), dst[k], v))
            continue
        merged = combine(rule, dst.get(k), v)
        if merged is not None:
            dst[k] = merged


def merge_mobspells(state, g, sid, meta, conflicts):
    spec = SPECS["mobspells.lua"]
    node = g.get("MobSpellsDB")
    for step in spec["payload"]:
        if not isinstance(node, luaser.Table):
            return 0
        node = node.get(step)
    if not isinstance(node, luaser.Table):
        return 0
    store = state.setdefault("mobspells", {})
    n = 0
    for zone, zt in node.hash.items():
        if not isinstance(zt, luaser.Table):
            continue
        z = store.setdefault(str(zone), {})
        for entry, mt in zt.hash.items():
            if not isinstance(mt, luaser.Table):
                continue
            rec = z.setdefault(str(entry), {"name": None, "spells": {}, "srcs": []})
            name = mt.get("name")
            if isinstance(name, str):
                if rec["name"] is None:
                    rec["name"] = name
                elif rec["name"] != name:
                    conflicts.append((("mob", zone, entry, "name"), rec["name"], name))
            spells = mt.get("spells")
            if isinstance(spells, luaser.Table):
                for spell, st in spells.hash.items():
                    if not isinstance(st, luaser.Table):
                        continue
                    d = rec["spells"].setdefault(str(spell), {})
                    merge_leaf(spec, d, st, conflicts, ("mob", zone, entry, spell))
                    n += 1
            if sid not in rec["srcs"]:
                rec["srcs"].append(sid)
    return n


def merge_aio(state, g, sid, meta, conflicts):
    tree = g.get("AIO_sv_Addons")
    if not isinstance(tree, luaser.Table):
        return 0
    store = state.setdefault("aio", {})
    n = 0
    for fname, ft in tree.hash.items():
        if not isinstance(ft, luaser.Table):
            continue
        code, crc = ft.get("code"), ft.get("crc")
        if not isinstance(code, str):
            continue
        # Variant key is the code's own hash: two servers that pushed identical
        # code collapse to one entry even if the addon recorded a different crc.
        vid = hashlib.sha1(code.encode("utf-8")).hexdigest()[:16]
        v = store.setdefault(str(fname), {}).setdefault(vid, {
            "code": code, "crc": crc, "srcs": [], "bytes": len(code)})
        if sid not in v["srcs"]:
            v["srcs"].append(sid)
        n += 1
    return n


def auctionator_record(value):
    """Recognize the observed v4 market record; unknown fields require review.

    mr is the most recent lowest buyout. History and lastScan are preserved
    verbatim, without inferring a timestamp unit or averaging observations.
    """
    if value.array or not {"id", "mr", "cc", "sc", "lastScan"} <= value.hash.keys():
        raise luaser.LuaError("unsupported_schema: Auctionator market record")
    out = {}
    for key, field in value.hash.items():
        if key == "id":
            if not isinstance(field, str) or not re.fullmatch(r"[0-9]+(?::[0-9]+)?", field):
                raise luaser.LuaError("unsupported_schema: Auctionator item id")
        elif key in {"mr", "cc", "sc", "lastScan"} or isinstance(key, str) and re.fullmatch(r"[HL][0-9]+", key):
            if type(field) is not int or field < 0:
                raise luaser.LuaError("unsupported_schema: Auctionator numeric field")
        else:
            raise luaser.LuaError("unsupported_schema: Auctionator record field")
        out[key] = field
    return out


def merge_auctionator(state, g, sid, meta, conflicts):
    """Keep complete validated market branches; privately hold unknown schemas."""
    tree = g.get("AUCTIONATOR_PRICE_DATABASE")
    if not isinstance(tree, luaser.Table):
        return 0
    store = state.setdefault("auctionator", {})
    n = 0
    for group, gt in tree.hash.items():
        if not isinstance(gt, luaser.Table):
            if group == "__dbversion":
                continue
            raise luaser.LuaError("unsupported_schema: Auctionator root field")
        if not len(gt):
            continue
        cls = modes.classify(str(group))
        parsed = []
        try:
            if cls["mode"] not in modes.CANONICAL_MODE_LABELS or gt.array:
                raise luaser.LuaError("unsupported_schema: Auctionator market group")
            shapes = set()
            for item, value in gt.hash.items():
                observation = auctionator_record(value) if isinstance(value, luaser.Table) else None
                price = observation["mr"] if observation is not None else value
                shapes.add(observation is not None)
                if not isinstance(item, str) or type(price) not in (int, float) or not __import__("math").isfinite(price) or price < 0:
                    raise luaser.LuaError("unsupported_schema: Auctionator scalar price")
                parsed.append((item, price, observation))
            if len(shapes) > 1:
                raise luaser.LuaError("unsupported_schema: mixed Auctionator branch")
        except luaser.LuaError:
            digest = hashlib.sha256(luaser.dumps({"observation": gt}).encode("utf-8")).hexdigest()
            hold = {"branch": "auctionator", "record_sha256": digest, "reason": "unsupported_market_schema"}
            held = state.setdefault("retained_records", {}).setdefault(sid, [])
            if hold not in held:
                held.append(hold)
            continue
        slug = cls["slug"]
        bucket = store.setdefault(slug, {"mode": cls["mode"], "realm": cls["realm"],
                                         "group": str(group), "items": {}})
        for item, price, observation in parsed:
            if observation is not None:
                encoded = json.dumps(observation, sort_keys=True, separators=(",", ":"))
                digest = hashlib.sha256(encoded.encode("utf-8")).hexdigest()
                variants = state.setdefault("auctionator_observations", {}).setdefault(slug, {}).setdefault(item, {})
                capture = variants.setdefault(digest, {"record": observation, "sources": {}})
                capture["sources"][sid] = meta["captured"]
            prev = bucket["items"].get(item)
            if prev is None or (meta["captured"] or "") >= (prev[1] or ""):
                bucket["items"][item] = [price, meta["captured"]]
            n += 1
    return n


_NODE_DBS = ("GatherMate2HerbDB", "GatherMate2MineDB", "GatherMate2FishDB",
             "GatherMate2GasDB", "GatherMate2TreeDB", "GatherMate2TreasureDB")


def merge_gathermate(state, g, sid, meta, conflicts):
    """A node is either at a spot or not, so this is union.

    Two submissions disagreeing about what stands at one coordinate is a real
    conflict -- the node was changed, or one client mis-recorded it -- and is
    reported rather than silently resolved."""
    store = state.setdefault("gathermate", {})
    n = 0
    for dbname in _NODE_DBS:
        tree = g.get(dbname)
        if not isinstance(tree, luaser.Table):
            continue
        db = store.setdefault(dbname, {})
        for zone, zt in tree.hash.items():
            if not isinstance(zt, luaser.Table):
                continue
            z = db.setdefault(str(zone), {})
            for coord, node in zt.hash.items():
                if isinstance(node, luaser.Table):
                    continue
                key = str(coord)
                if key in z and z[key] != node:
                    conflicts.append(((dbname, zone, coord), z[key], node))
                    # During a controlled replay, preserve a prior winner only
                    # when these freshly parsed bytes independently observe it.
                    prior = meta.get("replay_gathermate", {}).get(dbname, {}).get(str(zone), {})
                    if key in prior and node == prior[key]:
                        z[key] = node
                    continue
                z[key] = node
                n += 1
    return n


def merge_coasniff(state, g, sid, meta, conflicts):
    """Keep the distinct events and one example of each argument shape.

    The timestamps are GetTime() since that client booted, so they order events
    within one session and mean nothing across submissions."""
    tree = g.get("CoASniffDB")
    if not isinstance(tree, luaser.Table):
        return 0
    events = tree.get("events")
    if not isinstance(events, luaser.Table):
        return 0
    store = state.setdefault("coasniff", {})
    n = 0
    for ev in events.array + list(events.hash.values()):
        if not isinstance(ev, luaser.Table):
            continue
        name, args = ev.get("e"), ev.get("a")
        if not isinstance(name, str):
            continue
        rec = store.setdefault(name, {"args": [], "seen": 0})
        rec["seen"] += 1
        a = args if isinstance(args, str) else (json.dumps(harvestmerge.to_py(args), sort_keys=True, ensure_ascii=False) if args is not None else "")
        if a and a not in rec["args"]:
            rec["args"].append(a)
            rec["args"].sort()
        n += 1
    return n


MERGERS = {
           **{name: (lambda state,g,sid,meta,conflicts,name=name: reference_merge.merge(name,state,g,sid,meta,conflicts)) for name in reference_merge.GLOBALS},"mobspells.lua": merge_mobspells,
           "aio_client.lua": merge_aio,
           "auctionator_price_database.lua": merge_auctionator,
           "gathermate2.lua": merge_gathermate,
           "coasniff.lua": merge_coasniff,
           "wildcardharvest.lua": harvestmerge.merge_wildcardharvest}


def load_state():
    if os.path.exists(STATE):
        with open(STATE, encoding="utf-8") as f:
            return json.load(f)
    return {"sources": {}}


def save_state(state):
    os.makedirs(STORE, exist_ok=True)
    tmp = STATE + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        json.dump(state, f, indent=1, sort_keys=True, ensure_ascii=False)
    os.replace(tmp, STATE)


def run_merge(replay_sources=None, replay_gathermate=None, replay_harvest=None):
    state = load_state()
    if any(x is not None for x in (replay_sources, replay_gathermate, replay_harvest)) and state != {"sources": {}}:
        raise RuntimeError("Replay baseline requires a fresh shadow Lua state")
    if state.get("sources") and state.get("processing_revision") != PROCESSING_REVISION:
        raise RuntimeError("Lua processing rules changed: controlled replay into a fresh shadow state is required")
    state["processing_revision"] = PROCESSING_REVISION
    files = discover()
    conflicts, added, skipped = [], 0, 0
    print(f"{len(files)} SavedVariables files match a known spec")
    for key, path in files:
        sid = sha256(path)
        prev = state["sources"].get(sid)
        # A file that failed to parse is retried on every run: the usual reason
        # for the failure is a gap in our parser, so improving the parser has to
        # be enough to pick the file up without hand-clearing the store.
        if prev is not None and not prev.get("error") and prev.get("processing_revision") == PROCESSING_REVISION:
            skipped += 1
            continue
        try:
            g = luaser.load(path)
        except (luaser.LuaError, UnicodeError, OSError) as e:
            # A hand-edited file -- one a submitter redacted by hand -- may no
            # longer be valid Lua. Report it rather than dropping it silently.
            print(f"  UNPARSEABLE {os.path.basename(path)}: {e}")
            state["sources"][sid] = {"filename": os.path.basename(path),
                                     "spec": key, "error": str(e), "records": 0}
            continue
        spec = SPECS[key]
        if not any(gn in g for gn in spec["globals"]):
            print(f"  no {'/'.join(spec['globals'])} in {os.path.basename(path)},"
                  f" skipping")
            state["sources"][sid] = {"filename": os.path.basename(path),
                                     "spec": key, "records": 0, "error": "unsupported_schema: expected global missing"}
            continue
        original = (replay_sources or {}).get(sid, {})
        grp = original.get("group", group_of(path))
        cls = modes.classify(grp) if grp else {"realm": "", "mode": "unknown",
                                               "slug": "unknown",
                                               "source": "account-wide"}
        for field in ("realm", "mode", "slug"):
            if field in original:
                cls[field] = original[field]
        meta = {"captured": original["captured"] if "captured" in original else capture_dates.source_date(path)}
        if replay_gathermate is not None and key == "gathermate2.lua":
            meta["replay_gathermate"] = replay_gathermate
        if replay_harvest is not None and key == "wildcardharvest.lua":
            meta["replay_harvest"] = replay_harvest
        trial = copy.deepcopy(state)
        trial.get("retained_records", {}).pop(sid, None)
        file_conflicts = []
        try:
            n = MERGERS[key](trial, g, sid, meta, file_conflicts)
            if n == 0:
                # An empty expected payload is different from an unrecognised shape.
                expected = [g.get(gn) for gn in spec["globals"] if gn in g]
                if key == "mobspells.lua":
                    value = g.get("MobSpellsDB")
                    for part in ("global", "mobs"):
                        value = value.get(part) if isinstance(value, luaser.Table) else None
                    expected = [value]
                if key == "wildcardharvest.lua":
                    tree = g.get(harvestmerge.GLOBAL)
                    branches = (*harvestmerge.SNAPSHOT, *harvestmerge.REFERENCE, *harvestmerge.REALM_SCOPED, "byRealm", "spellsByChar")
                    expected = [tree.get(branch) for branch in branches if isinstance(tree, luaser.Table) and branch in tree]
                if key == "auctionator_price_database.lua":
                    tree = g.get("AUCTIONATOR_PRICE_DATABASE")
                    if isinstance(tree, luaser.Table) and not tree.array:
                        expected = [value for label, value in tree.hash.items() if str(label).lower() != "__dbversion"]
                        if not expected and all(str(label).lower() == "__dbversion" for label in tree.hash):
                            expected = [luaser.Table()]
                if key == "coasniff.lua":
                    tree = g.get("CoASniffDB")
                    expected = [tree.get("events") if isinstance(tree, luaser.Table) else None]
                if not expected or not all(isinstance(v, luaser.Table) and len(v) == 0 for v in expected):
                    raise luaser.LuaError("unsupported_schema: no recognised records consumed")
        except (harvestmerge.LeakError, ValueError, TypeError, UnicodeError) as exc:
            # The trial is discarded in its entirety: partial mutations never leak
            # into other files. Do not put private values in public diagnostics.
            state.get("conflicts", {}).pop(sid, None)
            state["sources"][sid] = {"filename": os.path.basename(path), "spec": key,
                "records": 0, "error": type(exc).__name__, "status": "needs_review",
                "processing_revision": PROCESSING_REVISION}
            print(f"  RETAINED {key}: {type(exc).__name__}; original remains private")
            continue
        state = trial
        conflicts.extend(file_conflicts)
        state.setdefault("conflicts", {})[sid] = [
            {"path": [str(p) for p in where], "previous": old, "observed": new}
            for where, old, new in file_conflicts]
        state["sources"][sid] = {
            "filename": os.path.basename(path), "spec": key,
            "group": grp, "realm": cls["realm"], "mode": cls["mode"],
            "slug": cls["slug"], "records": n, "captured": meta["captured"],
            "submission": original["submission"] if "submission" in original else submission_of(path),
            "processing_revision": PROCESSING_REVISION,
            "status": ("processed_with_retained" if state.get("retained_records", {}).get(sid)
                       else "processed" if n else "empty_supported"),
            "retained_records": len(state.get("retained_records", {}).get(sid, [])),
        }
        added += 1
        print(f"  + {os.path.basename(path):<32} {n:>6,} leaves   "
              f"{cls['realm'] or '(account-wide)'} / {cls['mode']}")
    n_back = backfill_submissions(state)
    if n_back:
        print(f"  filled in the submission of {n_back} earlier source file(s)")
    save_state(state)
    print(f"\n{added} new, {skipped} already merged, {len(conflicts)} field conflicts")
    for path, a, b in conflicts[:10]:
        print(f"  conflict {'.'.join(str(x) for x in path)}: {a!r} vs {b!r}")
    if len(conflicts) > 10:
        print(f"  ... and {len(conflicts) - 10} more")
    return state


# --------------------------------------------------------------------- export

def T(mapping=None, array=None):
    return luaser.table_from(mapping, array)


def _key(s):
    """Restore an integer key that JSON turned into a string."""
    try:
        return int(s)
    except ValueError:
        return s


def write_mobspells(state):
    store = state.get("mobspells") or {}
    if not store:
        return None
    mobs = T()
    n_mobs = n_spells = 0
    for zone, entries in store.items():
        zt = T()
        for entry, rec in entries.items():
            spells = T()
            for spell, d in rec["spells"].items():
                spells.hash[_key(spell)] = T({k: v for k, v in d.items()})
                n_spells += 1
            mt = T({"spells": spells})
            if rec.get("name"):
                mt.hash["name"] = rec["name"]
            zt.hash[_key(entry)] = mt
            n_mobs += 1
        mobs.hash[_key(zone)] = zt
    # profileKeys/profiles are omitted entirely; AceDB recreates them on load.
    db = T({"global": T({"mobs": mobs})})
    path = f"{OUT}/MobSpells.lua"
    luaser.dump({"MobSpellsDB": db}, path)
    return path, f"{n_mobs:,} creatures in {len(store):,} zones, {n_spells:,} spells"


_SAFE_NAME = re.compile(r"[^A-Za-z0-9._-]")


def choose_addon_build(srcinfo, variants):
    """Pick which build of one addon gets published. The single source of truth.

    Ranked by how many separate submissions carried the build -- see
    submission_of() for why that, and not the number of files. Ties fall to the
    oldest capture, which is a fact about the world rather than a property of
    the bytes; a tie there too is refused outright.

    Returns (vid, variant, note, tied) where note is "corroborated",
    "single submission" or "UNRESOLVED", and tied is the full list of builds
    that could not be separated.

    Both the writer and the verifier call this. They used to hold separate
    copies of the rule, which silently drifted apart.
    """
    def submissions(v):
        return {srcinfo.get(sid, {}).get("submission") or ("file:" + sid[:12])
                for sid in v["srcs"]}

    def earliest(v):
        d = sorted(x for x in (srcinfo.get(sid, {}).get("captured")
                               for sid in v["srcs"]) if x)
        return d[0] if d else ""

    ranked = sorted(variants.items(),
                    key=lambda kv: (len(submissions(kv[1])),
                                    _neg_date(earliest(kv[1]))),
                    reverse=True)
    top = ranked[0]
    tied = [kv for kv in ranked
            if len(submissions(kv[1])) == len(submissions(top[1]))
            and earliest(kv[1]) == earliest(top[1])]
    if len(tied) > 1:
        return top[0], top[1], "UNRESOLVED", tied
    note = ("corroborated" if len(submissions(top[1])) >= 2
            else "single submission")
    return top[0], top[1], note, tied


def addon_stats(srcinfo, v):
    """(distinct submissions, distinct files, oldest capture) for one build."""
    subs_ = {srcinfo.get(sid, {}).get("submission") or ("file:" + sid[:12])
             for sid in v["srcs"]}
    dates = sorted(x for x in (srcinfo.get(sid, {}).get("captured")
                               for sid in v["srcs"]) if x)
    return len(subs_), len(v["srcs"]), (dates[0] if dates else "")


def _neg_date(d):
    """Sort key that makes an OLDER date rank higher, with '' ranking last."""
    return tuple(-int(x) for x in d.split("-")) if d else (-9999,)


def _write_addon_report(rows):
    """Say, in plain words, how far each published addon can be trusted.

    These files are program code that was pushed to players' clients, and this
    project republishes them. Anyone rebuilding the UI from this dataset is
    going to run them. They deserve to know which ones only one person ever
    sent us, and which ones we could not decide between at all -- without
    reading the merge code to find out.
    """
    if not rows:
        return
    rows = sorted(rows, key=lambda r: (r[5] != "UNRESOLVED", r[2], r[0].lower()))
    out = os.path.join(OUT, "ADDONS-PROVENANCE.md")
    with open(out, "w", encoding="utf-8", newline="\n") as f:
        f.write("# How well corroborated is each published addon?\n\n"
                "These files are **executable code** that Ascension's server\n"
                "pushed to game clients, recovered from players' saved\n"
                "variables. If you rebuild the interface from this dataset you\n"
                "will be running them, so here is exactly how much confirmation\n"
                "each one has.\n\n"
                "**submissions** is the number of separate uploads a build\n"
                "arrived in -- the closest thing we have to *different people\n"
                "independently saw the same code*. **files** counts the\n"
                "individual saved-variable files instead, which is always the\n"
                "same or larger, because one person with several characters\n"
                "sends several files.\n\n"
                "* **corroborated** -- two or more separate submissions carried\n"
                "  this exact build.\n"
                "* **single submission** -- one upload is the only evidence this\n"
                "  is what the server sent. Almost certainly genuine, but it\n"
                "  rests on one person's word, so treat it as unverified code.\n"
                "* **UNRESOLVED** -- two or more different builds are equally\n"
                "  corroborated and equally old, and nothing but the code's own\n"
                "  bytes could separate them. We refuse to pick on that basis,\n"
                "  so the file is **left out** of the merged `AIO_Client.lua`\n"
                "  and every build is written to `addons/unresolved/` for a\n"
                "  person to compare.\n\n"
                "*Oldest capture* is the earliest modification time of a\n"
                "file carrying this build. For a file that arrived inside an\n"
                "archive that may be when it was unpacked here rather than when\n"
                "the player recorded it, so read it as a rough ordering and not\n"
                "a precise date. It breaks ties between equally corroborated\n"
                "builds, in preference to anything derived from the code's own\n"
                "bytes.\n\n"
                "| addon file | builds seen | submissions | files | oldest capture | status |\n"
                "|---|---:|---:|---:|---|---|\n")
        for name, nvar, nsub, nsrc, when, note in rows:
            f.write("| `%s` | %d | %d | %d | %s | %s |\n"
                    % (name, nvar, nsub, nsrc, when or "unknown", note))
    n_un = sum(1 for r in rows if r[5] == "UNRESOLVED")
    n_one = sum(1 for r in rows if r[5] == "single submission")
    print(f"  addon provenance: {len(rows)} file(s), {n_one} on a single "
          f"submission, {n_un} unresolved -> lua/ADDONS-PROVENANCE.md")


def write_aio(state):
    store = state.get("aio") or {}
    if not store:
        return None
    addons = T()
    n_files = n_variants = 0
    extra = {}
    os.makedirs(ADDONS_OUT, exist_ok=True)
    srcinfo = state.get("sources") or {}
    report, unresolved_dir = [], os.path.join(ADDONS_OUT, "unresolved")
    for fname, variants in store.items():
        vid, v, note, tied = choose_addon_build(srcinfo, variants)
        nsub, nsrc, when = addon_stats(srcinfo, v)
        report.append((fname, len(variants), nsub, nsrc, when, note))
        n_variants += len(variants)
        if note == "UNRESOLVED":
            # Nothing left to separate them but the bytes themselves, and the
            # bytes are the thing under suspicion. Refuse rather than guess.
            os.makedirs(unresolved_dir, exist_ok=True)
            for vid_, v_ in tied:
                safe_ = _SAFE_NAME.sub("_", fname)
                with open(f"{unresolved_dir}/{safe_}__{vid_}.lua", "w",
                          encoding="utf-8", newline="\n") as f:
                    f.write(v_["code"])
            print(f"  !! {fname}: {len(tied)} builds equally corroborated and "
                  f"equally old -- left OUT of the merged file; all builds "
                  f"written to addons/unresolved/ for a person to decide")
            continue
        addons.hash[fname] = T({"name": fname, "crc": v["crc"], "code": v["code"]})
        n_files += 1
        # Also write the code out as an actual .lua source file. The merged
        # SavedVariables is what the client wants; a source tree is what a
        # person rebuilding the UI wants, and it is the same bytes either way.
        safe = _SAFE_NAME.sub("_", fname)
        if not safe.lower().endswith(".lua"):
            safe += ".lua"
        with open(f"{ADDONS_OUT}/{safe}", "w", encoding="utf-8", newline="\n") as f:
            f.write(v["code"])
        if len(variants) > 1:
            extra[fname] = {k: {"crc": d["crc"], "bytes": d["bytes"],
                                "sources": len(d["srcs"])}
                            for k, d in variants.items()}
    _write_addon_report(report)
    path = f"{OUT}/AIO_Client.lua"
    # AIO_sv (hotbars, frame positions, character names) is deliberately absent.
    luaser.dump({"AIO_sv_Addons": addons}, path)
    if extra:
        with open(f"{OUT}/AIO_Client.variants.json", "w",
                  encoding="utf-8", newline="\n") as f:
            json.dump(extra, f, indent=1, sort_keys=True)
    return path, (f"{n_files:,} pushed addon files"
                  + (f" ({n_variants:,} builds)" if n_variants != n_files else "")
                  + f", also extracted to addons/")


def write_auctionator(state):
    store = state.get("auctionator") or {}
    if not store:
        return None
    db = T()
    n = 0
    for slug, bucket in store.items():
        if not bucket["items"]:
            # Someone installed the addon but never ran a scan: the realm key is
            # there with nothing under it. Publishing that claims we have prices
            # for a mode we have none for.
            continue
        t = T()
        for item, (price, _when) in bucket["items"].items():
            t.hash[item] = price
            n += 1
        db.hash[bucket["group"]] = t
    path = f"{OUT}/Auctionator_Price_Database.lua"
    luaser.dump({"AUCTIONATOR_PRICE_DATABASE": db}, path)
    observations = state.get("auctionator_observations")
    if observations:
        with open(f"{OUT}/Auctionator.observations.json", "w", encoding="utf-8", newline="\n") as f:
            json.dump(observations, f, indent=1, sort_keys=True, ensure_ascii=False)
    return path, f"{n:,} item prices across {len(db.hash)} modes"


def write_gathermate(state):
    store = state.get("gathermate") or {}
    if not store:
        return None
    out, n, zones = {}, 0, set()
    for dbname, db in store.items():
        t = T()
        for zone, coords in db.items():
            zt = T()
            for coord, node in coords.items():
                zt.hash[_key(coord)] = node
                n += 1
            t.hash[_key(zone)] = zt
            zones.add(zone)
        out[dbname] = t
    path = f"{OUT}/GatherMate2.lua"
    luaser.dump(out, path)
    return path, f"{n:,} nodes across {len(zones)} zones"


def write_coasniff(state):
    store = state.get("coasniff") or {}
    if not store:
        return None
    # Published as JSON, not .lua: nobody wants to load a sniffer log back into
    # the game, and what makes it useful is being readable as documentation.
    path = f"{OUT}/coa-client-events.json"
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump({k: {"args": v["args"]} for k, v in sorted(store.items())},
                  f, indent=1, sort_keys=True)
    return path, f"{len(store)} distinct client events"


WRITERS = [lambda state: reference_merge.write(state,OUT),write_mobspells, write_aio, write_auctionator,
           write_gathermate, write_coasniff,
           harvestmerge.write_wildcardharvest]


def run_export(state):
    os.makedirs(OUT, exist_ok=True)
    print()
    for w in WRITERS:
        r = w(state)
        if not r:
            continue
        # A writer returns one (path, note) or a list of them.
        for path, note in ([r] if isinstance(r, tuple) else r):
            rel = os.path.relpath(path, OUT).replace("\\", "/")
            print(f"  {rel:<34} {note}  ({os.path.getsize(path):,} B)")
    return verify(state)


def verify(state):
    """Re-read what we just wrote.

    A SavedVariables file the client cannot load is worthless, and parsing it
    back ourselves is the only cheap proof we have. The check is stricter than
    "it parses": re-serialising must reproduce the file byte for byte, which is
    what makes a re-run produce no git diff.

    The extracted addon sources are deliberately not parsed -- they are real Lua
    with functions and control flow, which this parser does not read and should
    not pretend to. They are checked against the code we stored instead.
    """
    bad = checked = 0
    for fn in sorted(os.listdir(OUT)) if os.path.isdir(OUT) else []:
        p = os.path.join(OUT, fn)
        if not fn.endswith(".lua") or os.path.isdir(p):
            continue
        checked += 1
        try:
            g = luaser.load(p)
        except (luaser.LuaError, UnicodeError, OSError) as e:
            print(f"  VERIFY FAILED {fn}: {e}")
            bad += 1
            continue
        with open(p, encoding="utf-8") as f:
            if luaser.dumps(g) != f.read():
                print(f"  VERIFY FAILED {fn}: not stable under re-serialisation")
                bad += 1
    print(f"re-parsed {checked} written .lua files: "
          + ("OK" if not bad else f"{bad} FAILED"))

    src_bad = src_n = 0
    _srcinfo = state.get("sources") or {}
    for fname, variants in (state.get("aio") or {}).items():
        _vid, v, _note, _tied = choose_addon_build(_srcinfo, variants)
        if _note == "UNRESOLVED":
            # Deliberately not written to addons/<name>.lua; its builds live in
            # addons/unresolved/ instead, so there is nothing to compare here.
            continue
        p = os.path.join(ADDONS_OUT, _SAFE_NAME.sub("_", fname))
        if not p.lower().endswith(".lua"):
            p += ".lua"
        src_n += 1
        try:
            with open(p, encoding="utf-8") as f:
                if f.read() != v["code"]:
                    print(f"  VERIFY FAILED addons/{os.path.basename(p)}:"
                          f" does not match the cached code")
                    src_bad += 1
        except OSError as e:
            print(f"  VERIFY FAILED addons/{os.path.basename(p)}: {e}")
            src_bad += 1
    if src_n:
        print(f"extracted {src_n} addon sources: "
              + ("byte-identical to the cache" if not src_bad
                 else f"{src_bad} FAILED"))
    return bad == 0 and src_bad == 0


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--replay-baseline-state", help="Private prior state; fresh shadow replay only")
    args = parser.parse_args()
    baseline_sources = None
    baseline_gathermate = None
    baseline_harvest = None
    if args.replay_baseline_state:
        with open(args.replay_baseline_state, encoding="utf-8") as f:
            baseline = json.load(f)
            baseline_sources = baseline["sources"]
            baseline_gathermate = baseline.get("gathermate", {})
            baseline_harvest = baseline.get("harvest", {})
    st = run_merge(baseline_sources, baseline_gathermate, baseline_harvest)
    sys.exit(0 if run_export(st) else 1)
