# -*- coding: utf-8 -*-
"""Merge the WildcardHarvest SavedVariables -- vendor pages, gossip, and the
client's own reference tables.

WHY THIS IS A SEPARATE MODULE

    luamerge.py owns the registry and the file walk; this owns one spec whose
    tree is much larger and much more varied than the other five, and which is
    the only source in the whole archive for two kinds of data that no .wdb
    cache can hold:

      vendors   what an NPC actually had for sale, at what price, in what
                currency.  SMSG_LIST_INVENTORY is a live packet: the client
                answers it into the merchant frame and writes nothing to disk,
                so the only record that survives is an addon that read the
                frame while it was open.
      gossips   the text and options an NPC offered.  npccache.wdb holds the
                npc_text rows, but not which NPC uses which, nor the menu.

    Keeping it out of luamerge.py also keeps that file's diff small, which
    matters because another session edits it.

THE BRANCHES, AND WHAT EACH ONE GETS

    vendors           SNAPSHOT  per (realm, npc), newest capture wins
    gossips           SNAPSHOT  per (realm, npc), ambiguous records held privately
    advancement       PER REALM the Character Advancement node database
    wildcard          PER REALM roll statics, events and the roll spell pool
    byRealm           PER REALM the two above, plus API surfaces and skillCards
    enums             UNION     the client's own enum tables
    apiSurfaces       UNION     named item-id constants and API listings
    dungeons          UNION     dungeon table
    battlegrounds     UNION     battleground table
    skillCards        UNION     skill-card types and costs
    roleRequirements  UNION     role gating conditions
    version/captured  PUBLISH   provenance for the above
    spellsByChar      RE-KEYED  the spells are game data; the character is not
    identity          DROPPED   "<Character>@<n>" -> character state, nothing else

WHY advancement AND wildcard ARE NOT STATIC TABLES

    They look like reference data and they are not.  The same Character
    Advancement node is priced and gated differently per realm: over the eleven
    snapshots here, 15,004 fields disagreed when they were unioned into one
    table, and 14,978 of those disagreements went away the moment the realm was
    part of the key.  A union across realms does not merge two readings of one
    node, it invents a node that exists on neither realm.

    byRealm already carries the per-realm copy, so it is the one that is read;
    the top-level branches are the same tree for whichever realm the file was
    last saved on (byte-identical, checked across all 10,250 nodes) and are
    folded onto that realm.  Three of the eleven files predate byRealm and name
    no realm anywhere, so theirs goes under "(unattributed)" rather than being
    guessed onto a real one.

WHAT IS NOT A CONFLICT

    Four kinds of disagreement are not evidence of anything and must not be
    reported as such, because a merge that cries wolf 86,400 times is a merge
    nobody reads:

      lists       two sightings of a collection, so they union.  This one was
                  losing data outright: rollSpells went 1,592 -> 7 and events
                  13,499 -> 116, because a list was replaced by whichever file
                  merged last rather than added to.
      timestamps  a clock; the newest reading is just the newest.
      probes      calls.*, rapidRollingState, eligibility, tokens.  These are
                  what the API said about whoever happened to be logged in.
                  The single value is worthless and the SET of them is a
                  catalogue of the server's own gating reasons, so they are
                  collected into observed.json instead.
      zero        this harvester writes 0 for a field it has not observed, so a
                  non-zero reading is the only one that saw anything.

WHY VENDORS AND GOSSIP ARE SNAPSHOTS, NOT ACCUMULATORS

    luamerge's three shapes apply here too, and this is the *observation* one.
    A merchant page was true when the frame was open.  Field-merging two
    readings of the same vendor would produce a page that nobody ever saw --
    the union of two stock lists is not a stock list -- so the newest reading
    of a given (realm, npc) replaces the older one whole, and the older one's
    timestamp is kept in the report so the replacement is visible.

    The reference branches are the opposite: they are the client's static
    tables, so they deep-union and a scalar disagreement is a conflict worth
    printing rather than a value to average.

AMBIGUOUS IDENTITY TEXT

    A name in gossip may be substituted player text or an ordinary game word.
    Without an independent template, neither rewriting it nor publishing it is
    justified. Identity metadata supplies comparison tokens but is never exported.
    Each ambiguous gossip observation is retained privately with an opaque digest;
    clean observations in the same file can still merge. Other branch ambiguity
    stops that file's trial merge. Original raw input remains available for review.
"""
import json
import os
import re
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config
import luaser
import modes

OUT = os.path.join(config.OUT, "lua", "harvest").replace("\\", "/")

GLOBAL = "AscensionRebirthHarvestDB"

# Branches merged as whole-record snapshots, keyed realm -> npc.
SNAPSHOT = ("vendors", "gossips")
# Branches merged as a deep union of static tables.
REFERENCE = ("enums", "apiSurfaces",
             "dungeons", "battlegrounds", "skillCards", "roleRequirements")
# Branches that are NOT static: the same Character Advancement node is priced
# and gated differently per realm, so a union across realms invents a node that
# never existed.  Measured over the eleven snapshots here, keying by realm took
# the disagreement from 15,004 fields to 26 -- and all 26 were a zero standing
# in for "not observed yet", which _deep_union fills rather than reports.
# The realm comes from byRealm's own key.  The top-level advancement branch is
# byte-identical to byRealm[thisRealm].advancement in every file that has both
# (checked, all 10,250 nodes), so it is read only when byRealm is absent.
# `wildcard` is here for the same reason: byRealm carries a per-realm copy of
# it (byRealm[realm].wildcardStatics has the same nine keys as wildcard.statics
# in every file that has both), so unioning the top-level copy across realms
# mixes two realms' answers into one table.
REALM_SCOPED = ("advancement", "wildcard")
ADVANCEMENT = "advancement"
UNATTRIBUTED = "(unattributed)"

# A key whose value is a clock. The newest reading wins and says nothing about
# the data, so it is not a disagreement.
TIMESTAMP = ("captured", "at", "time")

# Path segments under which a scalar is not the game's data but the answer the
# API gave about whoever was logged in. Every distinct answer is collected --
# that set is a catalogue of the server's gating reasons -- and none of them is
# reported as a conflict.
# `tokens` is in here because tokens.scroll.count is how many Scrolls of
# Fortune that character was carrying. The name and tokenType beside it are
# real data and never disagree, so nothing is lost by covering the block.
PROBED = ("calls", "rapidRollingState", "eligibility", "eligibilityReasons",
          "startingChoice", "tokens")

# byRealm[realm].wildcardStatics is wildcard.statics again, verbatim -- checked
# on all eight files that carry both. Storing it twice doubles the branch for
# nothing, so the copy is dropped, but only when it really is a copy.
DUPLICATE_OF = {"wildcardStatics": ("wildcard", "statics")}
# Never published, and read only to learn what to redact.
DROP = ("identity",)
# Published, but with the character out of the key.
REKEY = ("spellsByChar",)

# "Onichan@36" -- a character name and the realm slot it was on.
ATKEY = re.compile(r"^(.+)@\d+$")


class LeakError(Exception):
    """A name we were told to remove is still in the text we were about to write."""


# --------------------------------------------------------------- lua -> python

def to_py(v):
    """A Table becomes a dict when it has hash keys, a list when it does not.

    An empty Table becomes an empty list. That is a guess, but it is the same
    guess in both directions and nothing here distinguishes {} from [].
    """
    if isinstance(v, luaser.Table):
        if v.hash:
            out = {str(k): to_py(x) for k, x in v.hash.items()}
            # A table with BOTH parts is one collection whose low keys the
            # serializer happened to write positionally -- `entries` is 3075
            # array slots and 8213 keyed ones, all the same kind of thing.
            # Folding them together under their own 1-based keys keeps them one
            # collection; the alternative, a parallel "_array", is mostly nil
            # holes and merges as a single enormous scalar.
            for i, x in enumerate(v.array, 1):
                if x is None:
                    continue        # a nil hole: as_list() puts it back
                k = str(i)
                if k in out:
                    # Never seen in this data, and if it ever happens the two
                    # values are different things sharing a name.
                    raise ValueError("array slot %d collides with key %r" % (i, k))
                out[k] = to_py(x)
            return out
        return [to_py(x) for x in v.array]
    return v


def as_list(v):
    """A value that should be a sequence, however to_py rendered it.

    A pure array is already a list.  A table that also had keys is a dict whose
    1..n keys are those same positions, so the holes have to be put back or
    every field after one shifts left -- info[] has six of them and costs[] has
    eight hundred.
    """
    if isinstance(v, list):
        return v
    if isinstance(v, dict):
        idx = [int(k) for k in v if str(k).isdigit()]
        if not idx:
            return []
        return [v.get(str(i)) for i in range(1, max(idx) + 1)]
    return []


def arr(v):
    """The array part of a Table, or [] -- used for the API return tuples."""
    if isinstance(v, luaser.Table):
        return list(v.array)
    return []


def at(seq, i, default=None):
    """seq[i] with Lua's nil holes tolerated."""
    if 0 <= i < len(seq):
        x = seq[i]
        return default if x is None else x
    return default


# ------------------------------------------------------------------- redaction

ITEM_ID = re.compile(r"\|Hitem:(\d+):")


def item_id(link):
    m = ITEM_ID.search(link or "")
    return int(m.group(1)) if m else 0


def item_name(link):
    m = re.search(r"\[([^\]]*)\]\|h", link or "")
    return m.group(1) if m else ""


def own_names(db):
    """Every character name this submitter played, from the identity branch."""
    names = set()
    ident = db.get("identity")
    if isinstance(ident, luaser.Table):
        for k in ident.hash:
            m = ATKEY.match(str(k))
            if m and m.group(1):
                names.add(m.group(1))
            rec = ident.hash[k]
            if isinstance(rec, luaser.Table):
                nm = rec.get("name")
                if isinstance(nm, str) and nm:
                    names.add(nm)
    return names


def restore_tokens(text, names):
    """Compatibility hook: retain original text and report zero substitutions."""
    if not text or not names:
        return text, 0
    # Without an independent original template a matching word might be a game
    # term (e.g. Storm). Never silently replace it. assert_clean holds ambiguity.
    return text, 0



def assert_clean(obj, names, where):
    """Refuse to publish anything still carrying one of those names."""
    if not names:
        return
    pat = re.compile("|".join(r"\b%s\b" % re.escape(n) for n in names), re.I)

    def walk(v, path):
        if isinstance(v, dict):
            for k, x in v.items():
                enum_label = path.lower().startswith("enums") and isinstance(x, (int, float)) and str(k).lower() in ("player", "account", "creature")
                if not enum_label and pat.search(str(k)):
                    raise LeakError("ambiguous identity key in game reference data")
                walk(x, path + "." + str(k))
        elif isinstance(v, list):
            for i, x in enumerate(v):
                walk(x, "%s[%d]" % (path, i))
        elif isinstance(v, str) and pat.search(v):
            game_field = re.fullmatch(r"spells\.\d+\.(name|schoolName)", path, re.I)
            if not game_field or any(v.casefold() == n.casefold() for n in names):
                raise LeakError("ambiguous identity in game reference text")

    walk(obj, where)


# ----------------------------------------------------------------------- merge

def _brief(v, n=60):
    """A conflict is printed with %r, so a subtree has to be summarised first."""
    if isinstance(v, dict):
        return "<%d keys>" % len(v)
    if isinstance(v, list):
        return "<%d items>" % len(v)
    s = repr(v)
    return v if len(s) <= n else s[:n] + "..."


def _unobserved(x):
    """0 (but not False, and not "") is this harvester's not-yet-seen marker."""
    return isinstance(x, (int, float)) and not isinstance(x, bool) and x == 0


def _key(x):
    """A value's identity for set membership. Order-insensitive for dicts."""
    try:
        return json.dumps(x, sort_keys=True, default=str)
    except (TypeError, ValueError):
        return repr(x)


def _union_list(a, b):
    """What we already had, plus whatever b adds that is not in it.

    Deduplicating `a` as well would be wrong: two identical events really did
    happen twice, and collapsing them loses a reading.  So `a` is copied
    untouched and only genuinely new values are appended -- this can add, and
    can never subtract.

    Every list in this data is a collection: a list of API names, of roll
    spells, of events.  The API return tuples, where position matters and
    appending would be nonsense, come out of to_py as dicts because the client
    writes an "n" alongside them, so they never reach this function.
    """
    seen = set(_key(x) for x in a)
    out = list(a)
    for x in b:
        k = _key(x)
        if k not in seen:
            seen.add(k)
            out.append(x)
    return out


def _probed(path):
    return any(str(p) in PROBED for p in path)


def _observe(stats, path, *values):
    """Record every distinct answer seen at a probed path."""
    if stats is None:
        return
    obs = stats.setdefault("observed", {})
    slot = obs.setdefault(".".join(str(p) for p in path), {})
    for v in values:
        slot[_key(v)] = slot.get(_key(v), 0) + 1


def _deep_union(dst, src, path, conflicts, stats=None, preferred=None):
    """Union two static tables. Scalar disagreement is reported, newest wins.

    Four things are NOT disagreements and must not be reported as such:
      lists        two sightings of a collection, so they union
      timestamps   a clock, where the newest reading is simply the newest
      probed       what the API said about whoever was logged in; the set of
                   answers is the data, the last one is not
      zero         this harvester writes 0 for a field it has not observed, so
                   a non-zero reading is the only one that saw anything
    """
    for k, v in src.items():
        prior = preferred.get(k) if isinstance(preferred, dict) else None
        has_prior = isinstance(preferred, dict) and k in preferred
        p = path + (k,)
        if k not in dst:
            dst[k] = v
        elif isinstance(dst[k], dict) and isinstance(v, dict):
            _deep_union(dst[k], v, p, conflicts, stats, prior)
        elif isinstance(dst[k], list) and isinstance(v, list):
            before = len(dst[k])
            dst[k] = _union_list(dst[k], v)
            if stats is not None and len(dst[k]) != before:
                stats["list_gain"] = (stats.get("list_gain", 0)
                                      + len(dst[k]) - before)
        elif dst[k] == v:
            pass
        elif str(k) in TIMESTAMP:
            dst[k] = max(str(dst[k]), str(v))
        elif _probed(p):
            _observe(stats, p, dst[k], v)
            if not (has_prior and dst[k] == prior):
                dst[k] = v
        elif _unobserved(dst[k]) and not _unobserved(v):
            dst[k] = v
            if stats is not None:
                stats["filled"] = stats.get("filled", 0) + 1
        elif _unobserved(v) and not _unobserved(dst[k]):
            if stats is not None:
                stats["filled"] = stats.get("filled", 0) + 1
        else:
            conflicts.append((p, _brief(dst[k]), _brief(v)))
            if not (has_prior and dst[k] == prior):
                dst[k] = v


def merge_wildcardharvest(state, g, sid, meta, conflicts):
    """Fold one harvest file into state['harvest']. Returns leaves merged."""
    db = g.get(GLOBAL)
    if not isinstance(db, luaser.Table):
        return 0

    names = own_names(db)
    preferred = meta.get("replay_harvest", {})
    store = state.setdefault("harvest", {})
    store.setdefault("_names_seen", 0)
    n = 0

    # ---- snapshot branches: realm -> npc -> whole record, newest wins
    for branch in SNAPSHOT:
        tree = db.get(branch)
        if not isinstance(tree, luaser.Table):
            continue
        bucket = store.setdefault(branch, {})
        for realm, npcs in tree.hash.items():
            if not isinstance(npcs, luaser.Table):
                continue
            rb = bucket.setdefault(str(realm), {})
            for npc, rec in npcs.hash.items():
                if not isinstance(rec, luaser.Table):
                    continue
                py = to_py(rec)
                if branch == "gossips":
                    txt = py.get("text")
                    if isinstance(txt, str):
                        py["text"], k = restore_tokens(txt, names)
                        store["_names_seen"] += k
                try:
                    assert_clean(py, names, "%s.%s.%s" % (branch, realm, npc))
                except LeakError:
                    if branch != "gossips":
                        raise
                    # Hold this whole observation, never fabricate replacement text.
                    # The private source hash plus record digest identifies it for replay.
                    digest = __import__("hashlib").sha256(json.dumps(py, sort_keys=True, ensure_ascii=False).encode("utf-8")).hexdigest()
                    hold = {"branch": "gossips", "record_sha256": digest, "reason": "ambiguous_identity"}
                    held = state.setdefault("retained_records", {}).setdefault(sid, [])
                    if hold not in held:
                        held.append(hold)
                    continue
                key = str(npc)
                prev = rb.get(key)
                # 'at' is the client's own capture clock; the file mtime is not,
                # because eleven snapshots were unpacked here in one second.
                if prev is None or str(py.get("at", "")) >= str(prev.get("at", "")):
                    py["_source"] = sid[:12]
                    rb[key] = py
                n += 1

    # ---- realm-scoped: byRealm carries one subtree per realm, already keyed.
    # A file from before the harvester learned about realms has no byRealm at
    # all and no other realm marker anywhere in it, so its advancement goes
    # under an explicit unknown rather than being guessed onto a named realm.
    stats = store.setdefault("_stats", {})
    by = store.setdefault("byRealm", {})
    tree = db.get("byRealm")
    if isinstance(tree, luaser.Table) and tree.hash:
        for realm, sub in tree.hash.items():
            if not isinstance(sub, luaser.Table):
                continue
            py = to_py(sub)
            assert_clean(py, names, "byRealm.%s" % realm)
            for dup, where in DUPLICATE_OF.items():
                if dup not in py:
                    continue
                other = db
                for step in where:
                    other = other.get(step) if isinstance(other, luaser.Table) \
                        else None
                if other is not None and to_py(other) == py[dup]:
                    del py[dup]
            _deep_union(by.setdefault(str(realm), {}), py,
                        ("byRealm", str(realm)), conflicts, stats, preferred.get("byRealm", {}).get(str(realm)))
            n += 1
        # The top-level copies belong to whichever realm this file was saved
        # on. byRealm names exactly one, so there is no guessing to do.
        if len(tree.hash) == 1:
            realm = str(list(tree.hash)[0])
            for branch in REALM_SCOPED:
                sub = db.get(branch)
                if sub is None:
                    continue
                py = to_py(sub)
                assert_clean(py, names, branch)
                _deep_union(by[realm].setdefault(branch, {}), py,
                            ("byRealm", realm, branch), conflicts, stats, preferred.get("byRealm", {}).get(realm, {}).get(branch))
                n += 1
    else:
        for branch in REALM_SCOPED:
            sub = db.get(branch)
            if sub is None:
                continue
            py = to_py(sub)
            assert_clean(py, names, branch)
            _deep_union(
                by.setdefault(UNATTRIBUTED, {}).setdefault(branch, {}),
                py, ("byRealm", UNATTRIBUTED, branch), conflicts, stats, preferred.get("byRealm", {}).get(UNATTRIBUTED, {}).get(branch))
            n += 1

    # ---- reference branches: deep union of static tables
    for branch in REFERENCE:
        tree = db.get(branch)
        if tree is None:
            continue
        py = to_py(tree)
        assert_clean(py, names, branch)
        if isinstance(py, dict):
            _deep_union(store.setdefault(branch, {}), py, (branch,),
                        conflicts, stats, preferred.get(branch))
        else:
            store[branch] = py
        n += 1

    # ---- spellsByChar: keep the spells, drop the character
    tree = db.get("spellsByChar")
    if isinstance(tree, luaser.Table):
        spells = store.setdefault("spells", {})
        for _who, byid in tree.hash.items():
            if not isinstance(byid, luaser.Table):
                continue
            for spell, rec in byid.hash.items():
                py = to_py(rec)
                assert_clean(py, names, "spells.%s" % spell)
                if py == preferred.get("spells", {}).get(str(spell)):
                    spells[str(spell)] = py
                else:
                    spells.setdefault(str(spell), py)
                n += 1

    for branch in DROP:
        store.pop(branch, None)

    ver = db.get("version")
    if ver is not None:
        store["version"] = ver
    return n


# ---------------------------------------------------------------------- export

def npc_entry(key):
    """The creature entry out of a harvester key like "112785:18448:31".

    Only the first component is an entry; the rest distinguishes one capture of
    that vendor from another and means nothing to creature_template.
    """
    head = str(key).split(":")[0]
    return int(head) if head.isdigit() else 0


VENDOR_COLS = ["realm", "mode", "npc", "npc_key", "npc_name", "slot",
               "item", "item_name",
               "money_cost", "quantity", "available", "usable", "extended_cost",
               "honor_cost", "arena_cost", "cost_item_1", "cost_count_1",
               "cost_item_2", "cost_count_2", "cost_item_3", "cost_count_3",
               "cost_item_4", "cost_count_4", "cost_item_5", "cost_count_5",
               "captured"]

GOSSIP_COLS = ["realm", "mode", "npc", "npc_key", "text", "n_options",
               "options", "n_quests", "quests", "captured"]


def _tsv(path, cols, rows):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        f.write("\t".join(cols) + "\n")
        for r in rows:
            f.write("\t".join(
                str(r.get(c, "")).replace("\t", " ").replace("\r", " ")
                                 .replace("\n", "$B")
                for c in cols) + "\n")
    return path


def vendor_rows(store):
    """Flatten vendors into one row per item slot.

    The costs array is six wide because that is what the API returns; only the
    filled entries mean anything, and three is as many as any page here uses.
    """
    out = []
    for realm, npcs in sorted((store.get("vendors") or {}).items()):
        cls = modes.classify(realm)
        for npc, rec in sorted(npcs.items(),
                               key=lambda kv: (npc_entry(kv[0]), kv[0])):
            items = as_list(rec.get("items"))
            for slot, it in enumerate(items, 1):
                if not isinstance(it, dict):
                    continue
                info = as_list(it.get("info"))
                cinfo = as_list(it.get("costInfo"))
                costs = as_list(it.get("costs"))
                link = it.get("link") or ""
                row = {
                    "realm": cls["realm"] or realm, "mode": cls["mode"],
                    "npc": npc_entry(npc), "npc_key": npc,
                    "npc_name": rec.get("npcName", ""),
                    "slot": slot,
                    "item": item_id(link) or "",
                    "item_name": item_name(link) or at(info, 0, ""),
                    "money_cost": at(info, 2, 0),
                    "quantity": at(info, 3, 1),
                    "available": at(info, 4, -1),
                    "usable": at(info, 5, ""),
                    "extended_cost": at(info, 6, 0),
                    "honor_cost": at(cinfo, 0, 0),
                    "arena_cost": at(cinfo, 1, 0),
                    "captured": rec.get("at", ""),
                }
                k = 0
                for c in costs:
                    c = as_list(c)
                    if not c:
                        continue
                    clink, cval = at(c, 2, ""), at(c, 1, 0)
                    cid = item_id(clink)
                    if not cid or not cval:
                        continue
                    k += 1
                    if k > 5:
                        break
                    row["cost_item_%d" % k] = cid
                    row["cost_count_%d" % k] = cval
                out.append(row)
    return out


def gossip_rows(store):
    out = []
    for realm, npcs in sorted((store.get("gossips") or {}).items()):
        cls = modes.classify(realm)
        for npc, rec in sorted(npcs.items(),
                               key=lambda kv: (npc_entry(kv[0]), kv[0])):
            opts = as_list(rec.get("options"))
            real = [o for o in opts if isinstance(o, dict) and
                    any(k != "n" for k in o)]
            quests = rec.get("quests") or {}
            nq = 0
            if isinstance(quests, dict):
                for _k, v in quests.items():
                    if isinstance(v, list):
                        nq += len(v)
                    elif isinstance(v, dict):
                        nq += sum(1 for k2 in v if k2 != "n")
            out.append({
                "realm": cls["realm"] or realm, "mode": cls["mode"],
                "npc": npc_entry(npc), "npc_key": npc,
                "text": rec.get("text", ""),
                "n_options": len(real),
                "options": json.dumps(real, ensure_ascii=False,
                                      sort_keys=True) if real else "",
                "n_quests": nq,
                "quests": json.dumps(quests, ensure_ascii=False, sort_keys=True),
                "captured": rec.get("at", ""),
            })
    return out


# The columns worth reading first. Everything else follows alphabetically, so
# a field the harvester gains later still lands in the file without an edit.
ADV_LEAD = ["realm", "mode", "node", "ID", "Name", "Class", "Tab", "Type",
            "NodeType", "Quality", "quality", "RequiredLevel", "AECost",
            "TECost", "QualityCost", "essenceCost", "talentCost", "Spells"]


def advancement_rows(store):
    """One row per (realm, Character Advancement node).

    Realm is a real column, not decoration: the same node is priced and gated
    differently on Freepick than on Wildcard, and collapsing the two would
    publish a node that exists on neither.
    """
    out, seen = [], set()
    for realm, tree in sorted((store.get("byRealm") or {}).items()):
        adv = tree.get(ADVANCEMENT)
        ent = adv.get("entries") if isinstance(adv, dict) else None
        if not isinstance(ent, dict):
            continue
        cls = modes.classify(realm)
        for key, node in ent.items():
            if not isinstance(node, dict) or node.get("ID") is None:
                continue
            row = {"realm": cls["realm"] or realm, "mode": cls["mode"],
                   "node": key}
            for f, v in node.items():
                row[f] = (json.dumps(v, ensure_ascii=False, sort_keys=True)
                          if isinstance(v, (dict, list)) else v)
                seen.add(f)
            out.append(row)
    cols = [c for c in ADV_LEAD if c in seen or c in ("realm", "mode", "node")]
    cols += sorted(c for c in seen if c not in ADV_LEAD)
    out.sort(key=lambda r: (r["realm"], str(r.get("ID"))))
    return cols, out


def _json(path, obj):
    with open(path, "w", encoding="utf-8", newline="\n") as f:
        json.dump(obj, f, indent=1, sort_keys=True, ensure_ascii=False)
    return path


def write_wildcardharvest(state):
    store = state.get("harvest") or {}
    if not store:
        return None
    os.makedirs(OUT, exist_ok=True)
    written = []

    vr = vendor_rows(store)
    if vr:
        written.append((_tsv(os.path.join(OUT, "vendors.tsv"), VENDOR_COLS, vr),
                        "%d item slots across %d vendor pages"
                        % (len(vr), sum(len(v) for v in store["vendors"].values()))))
    gr = gossip_rows(store)
    if gr:
        written.append((_tsv(os.path.join(OUT, "gossips.tsv"), GOSSIP_COLS, gr),
                        "%d NPC gossip captures" % len(gr)))

    acols, arows = advancement_rows(store)
    if arows:
        realms = sorted(store.get("byRealm") or {})
        written.append((_tsv(os.path.join(OUT, "advancement.tsv"), acols, arows),
                        "%d nodes across %d realm(s): %s"
                        % (len(arows), len(realms), ", ".join(realms))))

    # byRealm minus the advancement tree, which advancement.tsv now holds --
    # writing both would put 7 MB of the same nodes in the output twice.
    rest = {r: {k: v for k, v in t.items() if k != ADVANCEMENT}
            for r, t in (store.get("byRealm") or {}).items()}
    rest = {r: t for r, t in rest.items() if t}
    if rest:
        written.append((_json(os.path.join(OUT, "byRealm.json"), rest),
                        "%d realm(s), advancement held separately" % len(rest)))

    # The client's static tables, one file each so a reader can take the small
    # ones without the big ones.
    for branch in REFERENCE:
        if store.get(branch):
            written.append((_json(os.path.join(OUT, "%s.json" % branch),
                                  store[branch]),
                            "%d top-level keys" % len(store[branch])
                            if isinstance(store[branch], dict)
                            else "%d entries" % len(store[branch])))
    obs = (store.get("_stats") or {}).get("observed")
    if obs:
        # Sorted by how many distinct answers a probe gave, because the ones
        # with the most are the ones whose vocabulary is worth reading.
        written.append((_json(os.path.join(OUT, "observed.json"),
                              {k: sorted(v) for k, v in obs.items()}),
                        "%d probes, %d distinct answers"
                        % (len(obs), sum(len(v) for v in obs.values()))))

    if store.get("spells"):
        written.append((_json(os.path.join(OUT, "spells.json"), store["spells"]),
                        "%d spells, character keys removed" % len(store["spells"])))
    return written
