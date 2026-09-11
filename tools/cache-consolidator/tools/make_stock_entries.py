"""Write stock_entries.txt: the template ids stock 3.3.5a already has.

    python -B tools/make_stock_entries.py --src <azerothcore-wotlk>/data/sql/base/db_world

The world catalogue says which objects and creatures are Ascension's own. An
id range cannot answer that. The catalogue used to assume stock GameObjects
sit below 200000 and Ascension's above it, and both halves were false:
worldforged treasure like `90636 Forgotten Sack` sits far below it, and stock
WotLK props like `200296 Washing Tub` sit above it. The only honest reference
is the stock template table itself, and the copy anybody can check is
AzerothCore's base world database, which ships with its source.

Only entry ids are written, as inclusive ranges -- no names, no other fields.
An id is all the catalogue asks about, and it keeps this file small enough to
read in a diff when AzerothCore moves.
"""
import argparse, os, re, subprocess, sys

HERE = os.path.dirname(os.path.abspath(__file__))
OUT = os.path.join(HERE, "stock_entries.txt")
TABLES = ("gameobject_template", "creature_template")

# A row of AzerothCore's base SQL starts its line with "(<entry>,".
ROW = re.compile(r"^\((\d+),", re.M)

# Ids that must come out of a correct parse. A reader that finds nothing
# reports "no stock ids" just as confidently as a real result, so a miss here
# stops the run instead of writing a reference that calls everything custom.
CONTROLS = {"gameobject_template": 31,    # Old Lion Statue, Redridge
            "creature_template": 68}      # Stormwind City Guard


def entries(src, table):
    path = os.path.join(src, table + ".sql")
    with open(path, encoding="utf-8", errors="replace") as f:
        ids = {int(m.group(1)) for m in ROW.finditer(f.read())}
    if CONTROLS[table] not in ids:
        sys.exit(f"{path}: control id {CONTROLS[table]} not found in "
                 f"{len(ids):,} parsed ids; refusing to write a reference")
    return ids


def ranges(ids):
    out, run = [], None
    for n in sorted(ids):
        if run and n == run[1] + 1:
            run[1] = n
        else:
            run = [n, n]
            out.append(run)
    return out


def revision(src):
    try:
        r = subprocess.run(["git", "-C", src, "log", "-1", "--format=%H %cs"],
                           capture_output=True, text=True, timeout=30)
        return r.stdout.strip() if r.returncode == 0 else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--src", required=True,
                    help="AzerothCore data/sql/base/db_world directory")
    ap.add_argument("--out", default=OUT)
    a = ap.parse_args()
    rev = revision(a.src)
    L = ["# Stock 3.3.5a template entry ids, from AzerothCore's base world database.",
         "# Written by make_stock_entries.py; do not edit by hand, regenerate.",
         f"# Source revision: {rev or 'unknown'}",
         "# One id, or an inclusive lo-hi range, per line.",
         ""]
    for t in TABLES:
        ids = entries(a.src, t)
        rs = ranges(ids)
        L.append(f"[{t}]")
        L += [f"{lo}" if lo == hi else f"{lo}-{hi}" for lo, hi in rs]
        L.append("")
        print(f"  {t:<20} {len(ids):>6,} ids in {len(rs):,} ranges "
              f"(max {max(ids)})")
    with open(a.out, "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L))
    print(f"-> {a.out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
