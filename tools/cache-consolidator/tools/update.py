"""One command for the whole pipeline. Drop a submission in _inbox, run this.

    python tools/update.py

  1. intake   extract archives, hash-dedup whole files, update ledger + MANIFEST
  2. merge    fold every record into the union store, dedup identical payloads,
              keep genuine variants, identify unlabelled submissions by fingerprint
  3. catalogue  turn the submitted world dumps into a list of objects and
                creatures that exist -- a catalogue, never a spawn table
  4. mapdata  inventory submitted server terrain: which map ids exist, how many
              tiles each has, and what DBC set came with them
  5. export   write the decoded per-mode / union / raw views
  6. rebuild  write merged, client-loadable .wdb files per game mode
  7. audit    scan everything about to be published for player data

Every stage is idempotent, so re-running after a bad drop is safe and cheap.
The audit is the gate: a non-zero exit means DO NOT PUBLISH until it is explained.
"""
import os, sys, subprocess, time

HERE = os.path.dirname(os.path.abspath(__file__))
STAGES = [("intake",  "intake.py"),
          ("merge",   "merge.py"),
          # A .lua submission is a tree whose branches need opposite treatment,
          # not a flat record list, so it merges in its own stage.  It runs
          # before export because the file guide describes its output too, and
          # before the audit so that output is covered by the publish gate.
          ("lua",     "luamerge.py"),
          # The world dumps are observations, not cache files -- nobody's client
          # wrote them, players walked the world with a dump addon running. They
          # need no merge store, only a recompute from the files on disk, so they
          # sit here: after the caches, before the guide that describes them.
          ("catalogue", "ingest_gameobjects.py"),
          # Server map data -- maps/vmaps/mmaps/dbc. Also not a cache: no
          # player's client wrote it, a core's extractors did. It is a stage
          # because for a while it was not one, and the cost of that was
          # invisible rather than loud: a 2 GB submission unpacked, reported
          # `ok`, and contributed nothing, because intake ledgers *.wdb and no
          # other stage could read a .map. Runs beside the catalogue, before the
          # guide that has to describe its output.
          ("mapdata", "mapdata.py"),
          ("export",  "export.py"),
          # The same records once more, as Lua tables an addon on a STOCK 3.3.5a
          # client can load. It reads export's views, so it runs after them, and
          # it writes under lua/ so both gates below cover its output.
          ("stock",   "export_stock_client.py"),
          ("rebuild", "rebuild.py"),
          # Two gates, asking opposite questions of the same tree. `columns`
          # asks whether the data we meant to publish is still in there;
          # `audit` asks whether anything is in there that must not be. The
          # first exists because the dataset shipped once with every vendor
          # price silently zeroed and every other check passed -- they all
          # compare our encoder against our decoder. See audit_columns.py.
          ("columns", "audit_columns.py"),
          ("audit",   "audit_publish.py")]

# Stages that judge the output rather than produce it. A failure in one means
# the result is not fit to publish, but the run still finishes: stopping early
# would hide what the other gate had to say, and both answers are wanted at
# once rather than one bad drop at a time.
GATES = {"columns", "audit"}

BANNER = {
    "columns": "!! DATA-LOSS CHECK FAILED -- do not publish until every dead "
               "column and branch is explained.",
    "audit":   "!! AUDIT FAILED -- do not publish until every hit is explained.",
}


def main():
    t0 = time.time()
    failed = []
    for name, script in STAGES:
        # flush before handing the console to the child, or our banners land last
        print(f"\n{'='*70}\n== {name}\n{'='*70}", flush=True)
        r = subprocess.run([sys.executable, os.path.join(HERE, script)])
        if r.returncode != 0:
            failed.append(name)
            if name in GATES:
                print("\n" + BANNER[name])
            else:
                print(f"\n!! {name} failed (exit {r.returncode}); stopping.")
                break
    print(f"\n{'='*70}")
    if failed:
        print("FAILED: " + ", ".join(failed))
    else:
        print(f"all stages OK in {time.time()-t0:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
