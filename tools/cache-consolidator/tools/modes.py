"""Realm / game-mode taxonomy for Ascension cache submissions.

A client writes its caches to  Cache\\WDB\\enUS\\<Realm> - <Mode>\\ , so the directory
that directly contains a .wdb is the provenance unit.  This module turns that folder
name into (realm, mode) and a stable slug used for the published per-mode sections.

Three kinds of folder show up in real submissions:
  "Rexxar - Conquest of Azeroth"   -> realm + mode, the normal case
  "CoA QA"                         -> no separator; resolved by the OVERRIDES table
  "enUS" / "WDB" / archive stems    -> the user zipped from too high up; mode unknown,
                                       recoverable by fingerprint (see infer.py usage)

Edit modes.json (written on first run) to correct or extend the mapping without
touching code; it wins over the built-in defaults.
"""
import os, re, json, sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import config

# Lives in the workspace, not next to the code: it is per-install data that grows as
# new realms are seen, and it must not end up committed to the tool's repository.
MODES_JSON = os.path.join(config.WORK, "modes.json").replace("\\", "/")

UNKNOWN = "unknown"

# Folder names that carry no "<Realm> - <Mode>" separator, or that need correcting.
# group folder name -> (realm, mode)
OVERRIDES = {
    "CoA QA":              ("CoA QA",  "Conquest of Azeroth"),
    "Voljin- Conquest of Azeroth": ("Vol'jin", "Conquest of Azeroth"),
    "Vol'jin":             ("Vol'jin", UNKNOWN),
    # zipped-from-too-high-up drops: realm and mode both unrecoverable from the path
    "enUS":                ("", UNKNOWN),
    "enUS (realm root)":   ("", UNKNOWN),
    "WDB":                 ("", UNKNOWN),
    "(root)":              ("", UNKNOWN),
    "VolJin_COA_wdb_-_Coin": ("Vol'jin", "Conquest of Azeroth"),
    "discord resources":   ("", UNKNOWN),
}

# canonical mode label -> (slug, family, season).  `family` groups the seasonal
# re-runs of one ruleset so a reader can ask for "free-pick" and get them all.
MODE_TABLE = {
    "Conquest of Azeroth": ("conquest-of-azeroth", "conquest-of-azeroth", ""),
    "Free-Pick":           ("free-pick",           "free-pick",           ""),
    "Season 10 Freepick":  ("season-10-freepick",  "free-pick",           "10"),
    "Season 10 Wildcard":  ("season-10-wildcard",  "wildcard",            "10"),
    "Season 9":            ("season-9",            "season-9",            "9"),
    "Warcraft Reborn":     ("warcraft-reborn",     "warcraft-reborn",     ""),
    "Stress Test":         ("stress-test",         "stress-test",         ""),
    "Development":         ("development",         "conquest-of-azeroth", ""),
    "Live QA":             ("live-qa",             "free-pick",           ""),
    # CoA's pre-release realms.  Same family as live CoA -- it is the same
    # ruleset -- but a separate slug on purpose: a beta item is not evidence
    # about the live one, and folding them would hide the retunes between them.
    "CoA Beta":            ("coa-beta",            "conquest-of-azeroth", ""),
    "CoA Alpha - Development": ("coa-alpha",       "conquest-of-azeroth", ""),
    # A public test realm exists to carry values that differ from live, so it is
    # its own mode.  Folding it into CoA would corrupt both directions at once.
    "CoA PTR":             ("coa-ptr",             "conquest-of-azeroth", ""),
    UNKNOWN:               (UNKNOWN,               UNKNOWN,               ""),
}

# Snapshot before classify() can add user-defined mode labels.
CANONICAL_MODE_LABELS = frozenset(MODE_TABLE) - {UNKNOWN}

# "Area 52 - Free-Pick" splits, but "Free-Pick" itself must not: require a space
# AFTER the dash, which the mode's own internal hyphen never has.
SPLIT = re.compile(r"\s*-\s+")

# A group whose folder name states no realm or mode -- "enUS", "WDB", a loose
# drop -- carries the submission it came from in trailing brackets, so that
# "enUS (realm root) [WDB__f8cd0267]" and "enUS (realm root) [zkb6p7w]" stay
# separate provenance units.  Thirteen different people zipped from above the
# realm folder; sharing one label meant one identification would be stamped
# onto all thirteen at once.  See intake.label_for.
QUALIFIER = re.compile(r"\s*\[[^\]]+\]$")


def base_group(group):
    """A group name with its submission qualifier removed."""
    return QUALIFIER.sub("", group).strip()


def _load_json():
    if os.path.exists(MODES_JSON):
        with open(MODES_JSON, encoding="utf-8") as f:
            return json.load(f)
    return {}


def _save_json(d):
    with open(MODES_JSON, "w", encoding="utf-8") as f:
        json.dump(d, f, indent=1, ensure_ascii=False, sort_keys=True)


def classify(group):
    """group folder name -> dict(realm, mode, slug, family, season, source).

    `source` says how the mode was decided: "path", "override", or "unknown"
    (a caller may later overwrite it with "inferred" after fingerprinting)."""
    user = _load_json()
    # The fully qualified name is tried first so one specific submission can be
    # hand-corrected in modes.json without touching the others that share its
    # folder name; everything after falls back to the bare name.
    bare = base_group(group)
    if group in user:
        u = user[group]
        realm, mode = u.get("realm", ""), u.get("mode", UNKNOWN)
        how = "modes.json"
    elif bare in user:
        u = user[bare]
        realm, mode = u.get("realm", ""), u.get("mode", UNKNOWN)
        how = "modes.json"
    elif bare in OVERRIDES:
        realm, mode = OVERRIDES[bare]
        how = "override"
    else:
        parts = SPLIT.split(bare, 1)
        if len(parts) == 2 and parts[1].strip():
            realm, mode = parts[0].strip(), parts[1].strip()
            how = "path"
        else:
            realm, mode = bare.strip(), UNKNOWN
            how = "unknown"
    if mode not in MODE_TABLE:
        # a mode we have never seen: keep the label, derive a slug, no family
        slug = re.sub(r"[^a-z0-9]+", "-", mode.lower()).strip("-") or UNKNOWN
        MODE_TABLE[mode] = (slug, slug, "")
    slug, family, season = MODE_TABLE[mode]
    if mode == UNKNOWN:
        how = "unknown"
    return {"realm": realm, "mode": mode, "slug": slug,
            "family": family, "season": season, "source": how}


def seed_json(groups):
    """Write modes.json with every group seen so far, so it can be hand-corrected.
    Existing hand edits are preserved."""
    user = _load_json()
    for g in sorted(groups):
        if g in user:
            continue
        c = classify(g)
        user[g] = {"realm": c["realm"], "mode": c["mode"]}
    _save_json(user)
    return user


if __name__ == "__main__":
    import sys
    for g in sys.argv[1:]:
        print(g, "->", classify(g))
