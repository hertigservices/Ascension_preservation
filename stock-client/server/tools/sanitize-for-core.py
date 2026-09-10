"""Neutralise client values that AzerothCore's fixed-size arrays cannot index.

fix-dbc.py repairs files that are malformed.  This one deals with files that
are perfectly well formed and simply say things the core has no room for:
Ascension extended several 3.3.5a enums, and the core turns those values
straight into array subscripts.  Every case found so far kills the server
during startup, before a port is ever opened:

    Spell.dbc  Effect         SpellMgr.cpp:3040   ASSERT(< TOTAL_SPELL_EFFECTS)
    Spell.dbc  ApplyAuraName  SpellMgr.cpp:3041   ASSERT(< TOTAL_AURAS)
    Achievement_Criteria.dbc  requiredType
                              AchievementMgr.cpp:2660 writes
                              _achievementCriteriasByType[requiredType], an
                              array of ACHIEVEMENT_CRITERIA_TYPE_TOTAL lists -
                              a silent out-of-bounds write, not an assert

This IS a loss of fidelity, which is why it lives apart from fix-dbc.py and
why every change is recorded in sanitized-for-core.json - row id, field, the
original value - so it can all be put back the day the core is widened.

Every bound is read from the core's own headers rather than hard-coded, so the
numbers follow the tree if it is ever patched.  --check-vanilla runs each rule
against the stock DBCs as a control: a rule that fires on vanilla is a wrong
rule, because vanilla data demonstrably loads.

Two actions:
  zero   set the offending field to 0.  Used where 0 is the enum's own "none"
         value, so the spell keeps its name, cost, range and other effects and
         only the Ascension-specific slot goes quiet.
  drop   remove the row.  Used where there is no neutral value - an
         achievement criterion with its type zeroed would still be evaluated,
         as type 0, and would silently corrupt an unrelated achievement.

Usage:  python sanitize-for-core.py [<dbc-dir>] [--dry-run] [--check-vanilla]
"""
import json, os, re, shutil, struct, sys

SRC = r"C:\AzerothRealm\src\src"
DEFAULT_DIR = r"C:\AzerothRealm\server-ascension\Data\dbc"
VANILLA_DIR = r"C:\AzerothRealm\server\Data\dbc"
MANIFEST = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                        "sanitized-for-core.json")

# Field numbers are the DBC's own, taken from the numbered comments on
# SpellEntry / AchievementCriteriaEntry in DBCStructure.h.
RULES = [
    dict(file="Spell.dbc", fields=(71, 72, 73), action="zero",
         what="spell effect type", const="TOTAL_SPELL_EFFECTS",
         header=("server", "shared", "SharedDefines.h")),
    dict(file="Spell.dbc", fields=(95, 96, 97), action="zero",
         what="aura type", const="TOTAL_AURAS",
         header=("server", "game", "Spells", "Auras", "SpellAuraDefines.h")),
    dict(file="Achievement_Criteria.dbc", fields=(2,), action="drop",
         what="achievement criteria type",
         const="ACHIEVEMENT_CRITERIA_TYPE_TOTAL",
         header=("server", "shared", "DataStores", "DBCEnums.h")),
]


def limit(rule):
    p = os.path.join(SRC, *rule["header"])
    txt = open(p, encoding="utf8", errors="replace").read()
    m = re.search(r"\b%s\s*=\s*(\d+)" % rule["const"], txt)
    if not m:
        raise RuntimeError("could not read %s from %s" % (rule["const"], p))
    return int(m.group(1))


def header(blob):
    return struct.unpack("<4I", bytes(blob[4:20]))


def scan(blob, rule, cap):
    """-> [(row, field, value)] for every value the core cannot index."""
    rc, fc, rs, ss = header(blob)
    hits = []
    for r in range(rc):
        for f in rule["fields"]:
            v = struct.unpack_from("<I", blob, 20 + r * rs + f * 4)[0]
            if v >= cap:
                hits.append((r, f, v))
    return hits


def apply_rule(blob, rule, hits):
    rc, fc, rs, ss = header(blob)
    if rule["action"] == "zero":
        for r, f, _ in hits:
            struct.pack_into("<I", blob, 20 + r * rs + f * 4, 0)
        return blob
    drop = {r for r, _, _ in hits}
    keep = bytearray()
    for r in range(rc):
        if r not in drop:
            keep += blob[20 + r * rs: 20 + (r + 1) * rs]
    out = bytearray(blob[:20]) + keep + blob[20 + rc * rs:]
    struct.pack_into("<I", out, 4, rc - len(drop))
    return out


def main(argv):
    dry = "--dry-run" in argv
    control = "--check-vanilla" in argv
    args = [a for a in argv if not a.startswith("--")]
    d = args[0] if args else DEFAULT_DIR

    record = {"dbcDir": d, "rules": {}}
    if os.path.isfile(MANIFEST):
        record = json.load(open(MANIFEST, encoding="utf8"))
        record.setdefault("rules", {})

    by_file = {}
    for rule in RULES:
        by_file.setdefault(rule["file"], []).append(rule)

    for fname, rules in sorted(by_file.items()):
        path = os.path.join(d, fname)
        if not os.path.isfile(path):
            print("%s: absent" % fname)
            continue
        print("%s" % fname)
        blob = bytearray(open(path, "rb").read())
        changed = False
        for rule in rules:
            cap = limit(rule)
            hits = scan(blob, rule, cap)
            key = "%s:%s" % (fname, rule["const"])
            if control:
                vp = os.path.join(VANILLA_DIR, fname)
                if os.path.isfile(vp):
                    v = scan(bytearray(open(vp, "rb").read()), rule, cap)
                    print("    vanilla control %-38s %d hit(s)%s"
                          % (rule["const"], len(v),
                             "   <-- RULE IS WRONG" if v else ""))
            if not hits:
                print("    %-40s clean (all %s < %d)"
                      % (rule["const"], rule["what"], cap))
                continue
            vals = sorted({v for _, _, v in hits})
            rows = sorted({r for r, _, _ in hits})
            print("    %-40s %d slot(s) in %d row(s) >= %d -> %s"
                  % (rule["const"], len(hits), len(rows), cap, rule["action"]))
            print("        %s seen: %s" % (rule["what"], vals))
            if dry:
                continue
            rc, fc, rs, ss = header(blob)
            record["rules"][key] = {
                "action": rule["action"], "limit": cap, "what": rule["what"],
                "entries": [[struct.unpack_from("<I", blob, 20 + r * rs)[0], f, v]
                            for r, f, v in hits]}
            blob = apply_rule(blob, rule, hits)
            changed = True
        if changed:
            if not os.path.exists(path + ".orig"):
                shutil.copyfile(path, path + ".orig")
            with open(path, "wb") as f:
                f.write(blob)
            print("    rewritten (%d rows)" % header(blob)[0])
    if not dry:
        with open(MANIFEST, "w", encoding="utf8") as f:
            json.dump(record, f, separators=(",", ":"))
        print("manifest: %s (%.1f KB)"
              % (MANIFEST, os.path.getsize(MANIFEST) / 1024.0))


if __name__ == "__main__":
    main(sys.argv[1:])
