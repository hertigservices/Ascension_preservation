"""Record-level merge of every Ascension client cache we hold.

The existing intake.py dedups whole FILES (sha256 of the inner file).  That is not
enough: no single snapshot is complete.  The best itemcache anyone submitted holds
42,616 items, but the union across all submissions holds 75,984 -- so the interesting
object is the union of RECORDS, not the best file.

This builds that union, incrementally and idempotently:

  * every .wdb record is keyed by (entry, sha1(payload))
  * an identical payload seen in 29 different submissions is stored ONCE, carrying a
    list of the sources that corroborate it  -> that is the "discard duplicates" half
  * a DIFFERENT payload for the same entry is NOT overwritten and NOT dropped.  It is
    a real variant: Ascension re-tunes items between patches and across game modes,
    so both readings are true, each for its own mode and capture date.  Collapsing
    them would silently destroy data.  Views (see export.py) pick a winner per mode;
    the store keeps everything.

Layout under merged/:
  sources.tsv        one row per (file sha256, folder it appeared in)
  <cache>/pack.bin   [entry u32][size u32][payload] for each DISTINCT record
  <cache>/index.tsv  entry, sha1, size, offset, modes, srcs, first/last capture date

Re-run after dropping new caches in.  Existing records are never rewritten; only new
ones append, so the store is safe to interrupt.
"""
import os, sys, json, struct, hashlib, collections, time, re

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wdblib, modes, config, intake, capture_dates, merge_checkpoint

PARSER_REVISION = "2026-09-11.2"

STORE   = config.STORE
SOURCES = config.SOURCES
SCAN_ROOTS = config.SCAN_ROOTS + [config.EXTRACT]

SRC_COLS = ["id", "sha256", "cache", "filename", "group", "realm", "mode", "slug",
            "mode_source", "records", "captured", "path", "locale", "build", "header_hex", "parser_revision"]
IDX_COLS = ["entry", "sha1", "size", "offset", "modes", "srcs",
            "first_captured", "last_captured", "locales"]

# fingerprint inference thresholds for submissions zipped from above the realm folder
INFER_MIN_SHARED = 500
INFER_MIN_AGREE  = 0.90
# How far ahead of the runner-up the winner must be to count as an identification
# rather than a coincidence.  See infer_mode: the caches that cannot distinguish
# modes score an exact 0.0000% margin, and the cache that can never drops below
# 0.04%, so anything in between separates the two cleanly.
INFER_MIN_MARGIN = 0.0001


def clean_locale(value):
    return value if isinstance(value, str) and re.fullmatch(r"[a-z]{2}[A-Z]{2}", value) else "unknown"


def read_tsv(path, cols):
    if not os.path.exists(path):
        return []
    out = []
    with open(path, encoding="utf-8") as f:
        head = f.readline().rstrip("\n").split("\t")
        for line in f:
            if not line.strip():
                continue
            vals = line.rstrip("\n").split("\t")
            if len(vals) != len(head):
                raise ValueError(f"Malformed TSV row in {path}; expected {len(head)} fields, got {len(vals)}")
            out.append(dict(zip(head, vals)))
    return out


def write_tsv(path, cols, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="\n") as f:
        f.write("\t".join(cols) + "\n")
        for r in rows:
            values = [str(r.get(c, "")) for c in cols]
            if any(any(ch in value for ch in "\t\r\n") for value in values):
                raise ValueError("Unsafe TSV field; original data retained")
            f.write("\t".join(values) + "\n")
    os.replace(tmp, path)


def group_of(path):
    """The provenance group for one .wdb. Deliberately not implemented here.

    This was a second copy of intake's rule, and a second copy of a rule is a
    bug waiting for someone to change one of them. When intake started keeping
    separate submissions apart -- several people zip from above the realm
    folder and shared one label -- this copy did not, so the keys the merge
    builds no longer matched the keys intake writes, and the merge quietly saw
    nothing new to do.
    """
    return intake.group_of(path)


def sha256_file(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def scan_disk():
    """Every .wdb on disk -> one entry per (sha256, group) placement."""
    found = {}
    for root in SCAN_ROOTS:
        if not os.path.isdir(root):
            continue
        for dp, _d, fs in os.walk(root):
            for fn in fs:
                if not fn.lower().endswith(".wdb"):
                    continue
                p = os.path.join(dp, fn).replace("\\", "/")
                h = sha256_file(p)
                g = group_of(p)
                found.setdefault((h, g), p)      # first path wins; content is identical
    return found


def build_profiles(index):
    """cache -> slug -> {entry: set(sha1)} from what is already merged, used to
    identify submissions whose folder name lost the realm/mode. Any cache type can
    serve as the fingerprint; a submission with no itemcache is identified from its
    creaturecache instead."""
    prof = collections.defaultdict(
        lambda: collections.defaultdict(lambda: collections.defaultdict(set)))
    for cache, idx in index.items():
        for (entry, sha1), row in idx.items():
            for slug in row["modes"]:
                if slug != modes.UNKNOWN:
                    prof[cache][slug][entry].add(sha1)
    return prof


def infer_mode(cache, fingerprint, profiles):
    """fingerprint = {entry: sha1} of one unknown source.

    Returns (slug, agree, shared, margin), or (None, ...) when this cache cannot
    tell the modes apart.

    The margin -- how far the winner is ahead of the best rival -- is the whole
    point.  Agreement alone identifies nothing, because most caches are identical
    across modes: creatures and gameobjects are not retuned per mode, so every
    mode scores 100% and the "winner" is just whichever profile happened to
    overlap most, which is always the largest mode.  Measured over the
    submissions whose folder name states their mode, creaturecache and
    gameobjectcache produce a 0.0000% margin every single time, while itemcache
    is never below 0.04% when it is right.  Items are what a mode actually
    changes, so items are what can recognise one.

    A tie is therefore not a weak answer to be taken anyway; it is the absence of
    an answer, and it gets reported as unknown.
    """
    scored = []
    for slug, prof in profiles.get(cache, {}).items():
        shared = [e for e in fingerprint if e in prof]
        if len(shared) < INFER_MIN_SHARED:
            continue
        agree = sum(1 for e in shared if fingerprint[e] in prof[e]) / len(shared)
        scored.append((agree, len(shared), slug))
    if not scored:
        return (None, 0.0, 0, 0.0)
    scored.sort(reverse=True)
    agree, shared, slug = scored[0]
    rival = next((a for a, _n, s in scored[1:] if s != slug), None)
    if rival is None:
        # Only one mode has enough overlap to compare against. Nothing contests
        # the verdict, so treat the whole agreement as the margin.
        return (slug, agree, shared, agree)
    margin = agree - rival
    if margin < INFER_MIN_MARGIN:
        return (None, agree, shared, margin)
    return (slug, agree, shared, margin)


def load_store():
    """Return (sources_by_key, index_by_cache, next_id)."""
    merge_checkpoint.recover(STORE)
    srcs = {}
    for r in read_tsv(SOURCES, SRC_COLS):
        srcs[(r["sha256"], r["group"])] = r
    index = {}
    if os.path.isdir(STORE):
        for cache in sorted(os.listdir(STORE)):
            idx_path = f"{STORE}/{cache}/index.tsv"
            if not os.path.exists(idx_path):
                continue
            d = {}
            for r in read_tsv(idx_path, IDX_COLS):
                d[(int(r["entry"]), r["sha1"])] = {
                    "size": int(r["size"]), "offset": int(r["offset"]),
                    "modes": set(filter(None, r["modes"].split(","))),
                    "srcs": set(filter(None, r["srcs"].split(","))),
                    "first_captured": r["first_captured"],
                    "last_captured": r["last_captured"],
                }
            index[cache] = d
    next_id = 1 + max([int(r["id"]) for r in srcs.values()] or [0])
    return srcs, index, next_id


def main():
    t0 = time.time()
    os.makedirs(STORE, exist_ok=True)
    srcs, index, next_id = load_store()
    known_keys = set(srcs)
    try:
        with open(config.LEDGER, encoding="utf-8") as f:
            ledger = json.load(f)
    except FileNotFoundError:
        ledger = {}
    for row in srcs.values():
        record = ledger.get(row["sha256"], {})
        row["locale"] = clean_locale(row.get("locale") or record.get("locale"))
        row["build"] = row.get("build") or record.get("build") or ""
        cls = modes.classify(row["group"])
        if cls["source"] in ("override", "modes.json"):
            for name in ("mode", "realm", "slug"):
                row[name] = cls[name]
            row["mode_source"] = cls["source"]
    disk = scan_disk()
    new_keys = [k for k in disk if k not in known_keys or
                srcs[k].get("parser_revision") != PARSER_REVISION]
    for k in known_keys & disk.keys():
        srcs[k]["path"] = disk[k]
    print(f"sources on disk: {len(disk)}  already merged: {len(known_keys)}  "
          f"new: {len(new_keys)}")

    modes.seed_json({g for _h, g in disk})

    # ---- read new files, classify, collect records -------------------------------
    pending = []          # (srckey, srcrow, [(entry, payload), ...])
    fingerprints = {}     # srckey -> {entry: sha1}, any cache type, for inference
    for (h, g) in sorted(new_keys, key=lambda k: disk[k]):
        p = disk[(h, g)]
        info = wdblib.inspect(p)
        cls = modes.classify(g)
        prior = srcs.get((h, g))
        captured = prior["captured"] if prior is not None else capture_dates.source_date(p)
        row = {"id": "", "sha256": h, "cache": info.cache,
               "filename": os.path.basename(p), "group": g,
               "realm": cls["realm"], "mode": cls["mode"], "slug": cls["slug"],
               "mode_source": cls["source"], "records": info.records,
               "captured": captured, "path": p, "locale": clean_locale(info.locale),
               "build": info.build, "header_hex": "", "parser_revision": PARSER_REVISION}
        recs = []
        if info.standard and info.records:
            with open(p, "rb") as f:
                b = f.read()
            row["header_hex"] = b[:wdblib.HEADER_LEN].hex()
            fp = {}
            for entry, size, payload in wdblib.iter_records(b):
                s1 = hashlib.sha1(payload).hexdigest()
                recs.append((entry, s1, payload))
                fp[entry] = s1
            if fp:
                fingerprints[(h, g)] = fp
        pending.append(((h, g), row, recs))

    # ---- resolve unknown modes by fingerprint ------------------------------------
    profiles = build_profiles(index)
    # also let brand-new known-mode sources in this same run act as references
    for key, row, recs in pending:
        if row["slug"] != modes.UNKNOWN and key in fingerprints:
            for e, s1 in fingerprints[key].items():
                profiles[row["cache"]][row["slug"]][e].add(s1)
    # Infer per GROUP, keeping whichever of its files DISCRIMINATES best -- not
    # whichever has the most entries. A creaturecache overlapping 6,000 entries
    # is worse evidence than an itemcache overlapping 900, because the creature
    # records are the same in every mode and the item records are not. Ranking by
    # evidence size picks the uninformative cache precisely because it is bigger.
    inferred = {}
    for key, row, recs in pending:
        if row["slug"] != modes.UNKNOWN:
            continue
        fp = fingerprints.get(key)
        if not fp:
            continue
        slug, agree, shared, margin = infer_mode(row["cache"], fp, profiles)
        if slug and agree >= INFER_MIN_AGREE:
            prev = inferred.get(row["group"])
            if prev is None or margin > prev[3]:
                inferred[row["group"]] = (slug, agree, shared, margin)
    # apply per GROUP, so a group's other files inherit the verdict
    for key, row, recs in pending:
        if row["slug"] == modes.UNKNOWN and row["group"] in inferred:
            slug, agree, shared, margin = inferred[row["group"]]
            row["slug"] = slug
            row["mode"] = slug
            row["mode_source"] = f"inferred:{agree:.3f}/{shared}"
    for g, (slug, agree, shared, margin) in sorted(inferred.items()):
        print(f"  inferred mode for {g!r}: {slug} "
              f"({agree:.1%} over {shared} entries, {margin:.2%} ahead of the next)")

    # ---- merge records into the packs -------------------------------------------
    added = collections.Counter(); dup = collections.Counter()
    packs = {}
    for key, row, recs in pending:
        if key in srcs:
            row["id"] = srcs[key]["id"]
            if all(row[field] == srcs[key][field] for field in ("slug", "mode", "realm")):
                row["mode_source"] = srcs[key]["mode_source"]
            if int(row["records"]) < int(srcs[key]["records"]):
                raise RuntimeError("Reprocessing reduced source records; original store retained")
        else:
            row["id"] = str(next_id); next_id += 1
        srcs[key] = row
        if not recs:
            continue
        cache = row["cache"]
        idx = index.setdefault(cache, {})
        if cache not in packs:
            os.makedirs(f"{STORE}/{cache}", exist_ok=True)
            packs[cache] = open(f"{STORE}/{cache}/pack.bin", "ab")
        pk = packs[cache]
        for entry, s1, payload in recs:
            k = (entry, s1)
            r = idx.get(k)
            if r is None:
                off = pk.tell()
                pk.write(struct.pack("<II", entry, len(payload)) + payload)
                r = idx[k] = {"size": len(payload), "offset": off,
                              "modes": set(), "srcs": set(),
                              "first_captured": row["captured"],
                              "last_captured": row["captured"]}
                added[cache] += 1
            else:
                dup[cache] += 1
            r["modes"].add(row["slug"])
            r["srcs"].add(row["id"])
            r["first_captured"] = min(r["first_captured"], row["captured"])
            r["last_captured"] = max(r["last_captured"], row["captured"])
    for pk in packs.values():
        pk.flush()
        os.fsync(pk.fileno())
        pk.close()

    repaired = capture_dates.repair_dates(srcs, index)
    if repaired:
        print(f"Corrected upload-time capture dates on {repaired} browser sources")

    # ---- persist -----------------------------------------------------------------
    sources_by_id = {s["id"]: s for s in srcs.values()}
    tables = []
    for cache, idx in index.items():
        rows = [{"entry": e, "sha1": s, "size": r["size"], "offset": r["offset"],
                 "modes": ",".join(sorted(r["modes"])),
                 "srcs": ",".join(sorted(r["srcs"], key=int)),
                 "first_captured": r["first_captured"],
                 "last_captured": r["last_captured"],
                 "locales": ",".join(sorted({sources_by_id[sid].get("locale") or "unknown"
                                           for sid in r["srcs"]}))}
                for (e, s), r in sorted(idx.items())]
        for row in rows:
            row["modes"] = ",".join(sorted({sources_by_id[sid]["slug"] for sid in row["srcs"].split(",")}))
        tables.append((f"{STORE}/{cache}/index.tsv", IDX_COLS, rows))
    tables.append((SOURCES, SRC_COLS, sorted(srcs.values(), key=lambda r: int(r["id"]))))
    merge_checkpoint.commit(STORE, tables, write_tsv)

    print(f"\n{'cache':<18}{'records':>10}{'entries':>10}{'+new':>8}{'dup':>10}")
    for cache in sorted(index):
        ents = len({e for e, _s in index[cache]})
        print(f"{cache:<18}{len(index[cache]):>10}{ents:>10}"
              f"{added[cache]:>8}{dup[cache]:>10}")
    print(f"\nstore: {STORE}   ({time.time()-t0:.0f}s)")


if __name__ == "__main__":
    main()
