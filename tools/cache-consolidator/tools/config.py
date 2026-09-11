"""Where everything lives. Import this instead of hardcoding paths.

Defaults keep intake, state and output outside the source checkout, under
%LOCALAPPDATA%/AscensionPreservation/cache (or ~/AscensionPreservation/cache).
WORK contains _inbox, extracted, merged and ledger.json; OUT defaults to
WORK/cachedata. The publish checkout defaults to a sibling ascension-data.

A private runtime.local.json beside this module can set work, out and publish_repo.
Environment overrides ASCENSION_CACHE_WORK, ASCENSION_CACHE_OUT,
ASCENSION_CACHE_DATA and CONSOLIDATOR_REPO take precedence. A config.json in WORK
controls scan roots and tools:

    { "extra_scan_roots": ["D:/somewhere/else"], "sevenzip": "C:/.../7z.exe" }

`extra_scan_roots` is how you add collections that live outside _inbox without
moving them.
"""
import os, json, shutil

HERE = os.path.dirname(os.path.abspath(__file__))


def _env(name, default):
    v = os.environ.get(name)
    return v if v else default


_runtime = {}
_runtime_path = os.path.join(HERE, "runtime.local.json")
if os.path.isfile(_runtime_path):
    with open(_runtime_path, encoding="utf-8-sig") as f:
        _runtime = json.load(f)
_default_work = os.path.join(os.environ.get("LOCALAPPDATA") or os.path.expanduser("~"),
                             "AscensionPreservation", "cache")
WORK = os.path.normpath(_env("ASCENSION_CACHE_WORK", _runtime.get("work", _default_work)))
OUT = os.path.normpath(_env("ASCENSION_CACHE_OUT",
                            _runtime.get("out", os.path.join(WORK, "cachedata"))))

_cfg = {}
_cfg_path = os.path.join(WORK, "config.json")
if os.path.exists(_cfg_path):
    with open(_cfg_path, encoding="utf-8") as f:
        _cfg = json.load(f)

INBOX    = os.path.join(WORK, "_inbox").replace("\\", "/")
EXTRACT  = os.path.join(WORK, "extracted").replace("\\", "/")
STORE    = os.path.join(WORK, "merged").replace("\\", "/")
LEDGER   = os.path.join(WORK, "ledger.json").replace("\\", "/")
MANIFEST = os.path.join(WORK, "MANIFEST.md").replace("\\", "/")
SOURCES  = STORE + "/sources.tsv"
WORK     = WORK.replace("\\", "/")
OUT      = OUT.replace("\\", "/")

# Map.dbc copies consulted ONLY to put a name against a map id found in a
# submitted map-data tree. A submission carries its own Map.dbc, but that copy
# is usually the stock one, which defines none of the custom ids whose terrain
# is the interesting part -- so the inventory comes out complete and anonymous.
# Read-only, never published, and never a source of rows: an id no reference
# names is still reported, just without a name.
MAP_DBC_REFS = [p.replace("\\", "/") for p in _cfg.get("map_dbc_refs", [])]

# Collections kept outside _inbox. Scanned read-only; nothing is ever written there.
EXTRA_SCAN_ROOTS = [p.replace("\\", "/") for p in _cfg.get("extra_scan_roots", [])]
if os.environ.get("ASCENSION_CACHE_EXTRA_ROOTS"):
    EXTRA_SCAN_ROOTS += [p.strip().replace("\\", "/")
                         for p in os.environ["ASCENSION_CACHE_EXTRA_ROOTS"].split(";")
                         if p.strip()]

# Read roots, in the order they are scanned.
SCAN_ROOTS = EXTRA_SCAN_ROOTS + [INBOX]


def sevenzip():
    """7-Zip, needed only to unpack submitted archives."""
    p = _cfg.get("sevenzip") or os.environ.get("SEVENZIP")
    if p and os.path.exists(p):
        return p
    for c in (r"C:\Program Files\7-Zip\7z.exe",
              r"C:\Program Files (x86)\7-Zip\7z.exe"):
        if os.path.exists(c):
            return c
    return shutil.which("7z") or shutil.which("7za") or r"C:\Program Files\7-Zip\7z.exe"


if __name__ == "__main__":
    for k in ("WORK", "OUT", "INBOX", "EXTRACT", "STORE", "LEDGER"):
        print(f"{k:<10} {globals()[k]}")
    print(f"{'7-Zip':<10} {sevenzip()}")
    print(f"{'scan':<10} {SCAN_ROOTS}")

# Dataset reads and publication remain independent of source location.
DATA = os.path.normpath(_env("ASCENSION_CACHE_DATA", OUT))
PUBLISH_REPO = _env("CONSOLIDATOR_REPO", _runtime.get("publish_repo", os.path.join(os.path.dirname(WORK), "ascension-data")))
