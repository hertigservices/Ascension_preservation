"""Ascension cache intake: extract -> hash-dedup (on INNER files) -> inventory -> manifest.

Idempotent. Drop any submission (zip/rar/loose folder) into a scanned root and re-run.
Dedup key = sha256 of the extracted file, so a zip and a rar of the same itemcache
collapse to ONE record with a corroboration count. No DB writes, no deletes.
"""
import os, sys, json, hashlib, shutil, subprocess, time, re
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wdblib, config

SEVENZIP = config.sevenzip()
INTAKE   = config.WORK
EXTRACT  = config.EXTRACT
LEDGER   = config.LEDGER
MANIFEST = config.MANIFEST
SCAN_ROOTS = config.SCAN_ROOTS
# handled in its own dedicated workspace; too big to re-extract every run
SKIP_ARCHIVES = {"ascension-harvest-export.zip"}
# Compressed tars need the suffix CHAIN, not splitext: splitext("WDB.tar.gz")
# returns ".gz". A .tar.gz was silently skipped here for exactly that reason --
# no error, just quietly not ingested -- so match on the chain.
ARCHIVE_EXT = {".zip", ".rar", ".7z", ".tar",
               ".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz",
               ".gz", ".bz2", ".xz"}
TAR_CHAINS  = {".tar.gz", ".tgz", ".tar.bz2", ".tbz2", ".tar.xz", ".txz"}
# Written into each extract dir and read back by extract_all(), so a second
# archive that maps to the same dir -- same stem, or the same name entirely --
# gets its own dir instead of being reported "cached" and silently skipped.
#
# SRCMARK holds only a NAME, and archive_inbox.py requires it to be exactly
# that, so it cannot carry the hash: two submissions have arrived called
# Account.zip and a name is not an identity. The content hash therefore lives
# in its own file beside it. Dirs with neither are grandfathered: no marker
# means "mine", because disowning them would re-extract the whole historical
# archive set into hash-suffixed twins.
SRCMARK = ".intake-source"
SRCHASH = ".intake-sha256"

def split_archive(fn):
    """(base, ext) where ext is the full suffix chain for compressed tars."""
    low = fn.lower()
    for ext in sorted(TAR_CHAINS, key=len, reverse=True):
        if low.endswith(ext):
            return fn[:-len(ext)], ext
    base, e = os.path.splitext(fn)
    return base, e.lower()

def sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()

# Beside EXTRACT, never inside it: anything under EXTRACT is scanned.
QUARANTINE = os.path.join(os.path.dirname(EXTRACT), "quarantine")


def occupied(out):
    """True when `out` already holds an extraction."""
    return os.path.isdir(out) and bool(os.listdir(out))


def _marker(out, name):
    try:
        with open(os.path.join(out, name), encoding="utf-8",
                  errors="replace") as f:
            return f.read().strip() or None
    except OSError:
        return None


def already_holds(out, fn, digest):
    """Is the extraction in `out` the archive `fn` whose content is `digest`?

    Content first, name only as a fallback for dirs written before the hash
    marker existed, and True for a dir with no markers at all (grandfathered).
    A name-only test is what lost a submission: both files were called
    Account.zip, the name matched, and the second was reported "cached".
    """
    have = _marker(out, SRCHASH)
    if have:
        return have == digest
    name = _marker(out, SRCMARK)
    return True if name is None else name == fn


def extraction_hashes():
    """Completed, hash-marked extractions survive an archive filename change.

    Reuse the original directory, preserving source IDs and corroboration counts.
    A filename/hash suffix alone is never evidence of completed extraction.
    """
    result = {}
    if not os.path.isdir(EXTRACT):
        return result
    for name in sorted(os.listdir(EXTRACT)):
        out = os.path.join(EXTRACT, name)
        if os.path.islink(out) or os.path.isjunction(out) or not os.path.isdir(out):
            continue
        digest = _marker(out, SRCHASH)
        if (digest and re.fullmatch(r'[a-f0-9]{64}', digest)
                and any(n not in (SRCMARK, SRCHASH) for n in os.listdir(out))):
            result.setdefault(digest, out)
    return result


def extract_all():
    """Extract every archive under the scan roots into EXTRACT/<stem>/ (skip if already done)."""
    os.makedirs(EXTRACT, exist_ok=True)
    done = []
    completed_hashes = extraction_hashes()
    # Pre-scan for stem collisions (WDB.zip vs WDB.tar.gz vs WDB.rar). Only
    # colliding stems get an ext-qualified dir; everything else keeps the
    # historical bare-stem dir so existing extractions are not re-done.
    claims = {}
    for root in SCAN_ROOTS:
        if not os.path.isdir(root): continue
        for dirpath, _dirs, files in os.walk(root):
            if os.path.abspath(dirpath).startswith(os.path.abspath(EXTRACT)):
                continue
            for fn in files:
                base, ext = split_archive(fn)
                if ext not in ARCHIVE_EXT or fn in SKIP_ARCHIVES: continue
                claims.setdefault(re.sub(r"[^A-Za-z0-9._()+-]", "_", base), set()).add(fn)
    contested = {s for s, v in claims.items() if len(v) > 1}
    for s in sorted(contested):
        print("  note: stem %r claimed by %s -- each gets its own dir"
              % (s, ", ".join(sorted(claims[s]))))
    for root in SCAN_ROOTS:
        if not os.path.isdir(root): continue
        for dirpath, _dirs, files in os.walk(root):
            if os.path.abspath(dirpath).startswith(os.path.abspath(EXTRACT)):
                continue  # never recurse into our own extract output
            for fn in files:
                base, ext = split_archive(fn)
                if ext not in ARCHIVE_EXT: continue
                if fn in SKIP_ARCHIVES: continue
                src = os.path.join(dirpath, fn)
                stem = re.sub(r"[^A-Za-z0-9._()+-]", "_", base)
                if stem in contested:
                    stem = stem + "__" + ext.replace(".", "")
                out = os.path.join(EXTRACT, stem)
                # Does that dir hold THIS archive, or a different one that
                # merely shared its name? The pre-scan above separates WDB.zip
                # from WDB.rar, but it keys on the set of NAMES, so two files
                # genuinely called Account.zip -- one just dropped in _inbox,
                # one already filed under archive/ -- collapse to a single
                # claim and a single dir. The second was reported "cached" and
                # never extracted: no error, exit 0, a whole submission lost.
                # Hashing is why this is gated on the dir already existing:
                # a first extraction should not pay for a read of every byte.
                tagged = bool(re.search(r'__[a-f0-9]{8,64}$', base, re.I))
                digest = sha256(src) if occupied(out) or tagged else None
                if tagged and digest in completed_hashes:
                    done.append((fn, "cached (same archive content)")); continue
                if digest and not already_holds(out, fn, digest):
                    out = os.path.join(EXTRACT, "%s__%s" % (stem, digest[:8]))
                if occupied(out):
                    done.append((fn, "cached")); continue
                os.makedirs(out, exist_ok=True)
                r = subprocess.run([SEVENZIP, "x", src, "-o"+out, "-y", "-bd", "-bb0"],
                                   capture_output=True, text=True)
                ok = r.returncode == 0
                # .tar.gz unwraps to a .tar; 7z needs a second pass to open it
                if ok and ext in TAR_CHAINS:
                    inner = [x for x in os.listdir(out) if x.lower().endswith(".tar")]
                    for t in inner:
                        tp = os.path.join(out, t)
                        r2 = subprocess.run([SEVENZIP, "x", tp, "-o"+out, "-y", "-bd", "-bb0"],
                                            capture_output=True, text=True)
                        ok = ok and r2.returncode == 0
                        if r2.returncode == 0:
                            os.remove(tp)
                if ok:
                    with open(os.path.join(out, SRCMARK), "w") as f: f.write(fn)
                    with open(os.path.join(out, SRCHASH), "w") as f:
                        f.write(digest or sha256(src))
                    completed_hashes[digest or sha256(src)] = out
                    done.append((fn, "ok"))
                else:
                    where = quarantine(out, fn, (r.stderr or r.stdout or "").strip())
                    done.append((fn, f"FAILED -> quarantine/{os.path.basename(where)}"
                                     if where else f"ERR {r.returncode}"))
    return done


def quarantine(out, fn, why):
    """Move a failed extraction out of the way, and say where it went.

    It must not stay under EXTRACT. The "have we already done this one?" test is
    `isdir(out) and listdir(out)`, so a half-written directory left in place
    reports `cached` on every future run: the archive is never retried, and the
    fragments 7-Zip did write are scanned into the ledger as if they were a real
    submission. A truncated upload would fail once, silently, and be treated as
    finished forever.

    It is moved rather than deleted. A failed extraction is the most interesting
    kind -- a corrupt upload worth asking the sender to redo, or an archive doing
    something it should not -- and deleting the evidence to keep the folder tidy
    would be exactly the wrong instinct.
    """
    os.makedirs(QUARANTINE, exist_ok=True)
    stamp = time.strftime("%Y%m%d-%H%M%S")
    dest = os.path.join(QUARANTINE, f"{os.path.basename(out)}__{stamp}")
    try:
        shutil.move(out, dest)
    except OSError as e:
        print(f"  !! {fn} failed to extract AND could not be quarantined: {e}")
        print(f"     remove {out} by hand or it will report 'cached' forever")
        return None
    try:
        with open(os.path.join(dest, "WHY-THIS-IS-HERE.txt"), "w",
                  encoding="utf-8") as f:
            f.write("Extraction of this archive failed, so its partial output was\n"
                    "moved here. Nothing in this folder has been scanned or\n"
                    "published, and the archive will be retried on the next run.\n\n"
                    f"archive : {fn}\n"
                    f"when    : {stamp}\n"
                    f"7-Zip said:\n{why or '(nothing)'}\n")
    except OSError:
        pass
    print(f"  !! {fn} failed to extract; partial output quarantined at {dest}")
    return dest

# Folder names that state neither realm nor mode. A submission that lands in one
# of these is unidentified by its path, and several different submissions land in
# each -- so these, and only these, get the submission appended.
AMBIGUOUS = {"enUS (realm root)", "WDB", "(root)"}


# Path segments that are our own filing, not anybody's submission: the inbox
# tidy bucket and the year-month folders inside it.
TIDY = re.compile(r"^(?:archive|\d{4}-\d{2})$")


def submission_segment(rel):
    """The first segment of a relative path that names a submission.

    Skipping the tidy bucket matters for more than tidiness. The same upload is
    reachable both as `<stem>/enUS/itemcache.wdb` under the extract directory
    and as `archive/2026-09/<stem>/enUS/itemcache.wdb` once the inbox has been
    swept, and the two must produce the same answer -- otherwise a file's group
    changes depending on which path the walk happened to reach it by.
    """
    for seg in rel.split("/")[:-1]:
        # Atomic collector batch wrappers are transport, not contributor identity.
        if re.fullmatch(r"upload-batch-[a-f0-9]{32}", seg):
            continue
        if not TIDY.match(seg):
            return seg
    return ""


def qualify(grp, submission):
    """Keep two submissions apart when their folder name cannot.

    Everyone who zips from above the realm folder produces the same group name,
    and thirteen separate submissions currently share `enUS (realm root)`. That
    matters because the mode is recovered by fingerprinting, and merge.py
    records one verdict PER GROUP -- so a confident identification of one
    upload would be applied to all thirteen, labelling twelve other people's
    caches with a mode nobody measured.

    Appending the submission makes each one its own provenance unit, so each is
    identified on its own evidence or stays honestly unknown. Groups that name
    a realm and mode are left alone: their name is already the answer.
    """
    return f"{grp} [{submission}]" if grp in AMBIGUOUS and submission else grp


def group_of(path):
    """The provenance group for one .wdb -- the single source of truth.

    merge.py used to work this out for itself with its own copy of the rule.
    The two then had to be changed together, and when only one was, the group
    names in the ledger and the group names in the merge store silently stopped
    matching: the merge saw an unchanged set of keys and did nothing at all.
    One rule, called from both places, so that cannot happen again.
    """
    return label_for(path)[1]


def label_for(path):
    """Return (provenance_label, group). Group = the realm/character folder that
    directly contains the wdb (the meaningful unit), collapsing the generic 'enUS'
    container and '(root)' loose drops, then qualifying those by submission."""
    ap = os.path.abspath(path)
    parent = os.path.basename(os.path.dirname(ap))
    grp = parent if parent not in ("enUS",) else "enUS (realm root)"
    ex = os.path.abspath(EXTRACT)
    if ap.startswith(ex):
        rel = os.path.relpath(ap, ex).replace("\\", "/")
        return "archive:" + rel, qualify(grp, submission_segment(rel))
    for root in SCAN_ROOTS:
        ra = os.path.abspath(root)
        if ap.startswith(ra):
            rel = os.path.relpath(ap, ra).replace("\\", "/")
            # A bare file at the scan root has no containing submission to name,
            # so it gets no qualifier -- one per file would be fragmentation,
            # not provenance.
            if "/" not in rel: grp = "(root)"
            stable_rel = "/".join(seg for seg in rel.split("/") if not re.fullmatch(r"upload-batch-[a-f0-9]{32}", seg))
            return "loose:" + stable_rel, qualify(grp, submission_segment(rel))
    return ap.replace("\\", "/"), grp

def scan():
    ledger = {}
    if os.path.exists(LEDGER):
        with open(LEDGER, encoding="utf-8") as f:
            ledger = json.load(f)
    seen_before = set(ledger)
    walk_roots = SCAN_ROOTS + [EXTRACT]
    for root in walk_roots:
        if not os.path.isdir(root): continue
        for dirpath, _dirs, files in os.walk(root):
            for fn in files:
                if not fn.lower().endswith(".wdb"): continue
                p = os.path.join(dirpath, fn)
                h = sha256(p)
                lbl, grp = label_for(p)
                if h in ledger:
                    srcs = ledger[h]["sources"]
                    if lbl not in srcs: srcs.append(lbl)
                    # Entries filed before submissions were kept apart still
                    # carry the bare ambiguous label.  Upgrade in place, and
                    # only ever in that direction: a file can be seen at more
                    # than one path, and letting the group change freely would
                    # make it flip between runs.
                    if ledger[h].get("group") in AMBIGUOUS and grp not in AMBIGUOUS:
                        ledger[h]["group"] = grp
                    continue
                info = wdblib.inspect(p.replace("\\", "/"))
                ledger[h] = {
                    "sha256": h, "filename": fn, "size": os.path.getsize(p),
                    "cache": info.cache, "label": info.label, "build": info.build,
                    "locale": info.locale, "records": info.records,
                    "standard": info.standard, "clean_end": info.clean_end,
                    "note": info.note or "", "group": grp, "sources": [lbl],
                    "first_seen": time.strftime("%Y-%m-%d %H:%M:%S"),
                }
    with open(LEDGER, "w", encoding="utf-8") as f:
        json.dump(ledger, f, indent=1)
    return ledger, seen_before

def manifest(ledger, seen_before):
    entries = list(ledger.values())
    new = [e for e in entries if e["sha256"] not in seen_before]
    # best (max-record) standard snapshot per cache type = union-coverage ceiling
    best = {}
    for e in entries:
        if not e["standard"]: continue
        b = best.get(e["cache"])
        if b is None or e["records"] > b["records"]:
            best[e["cache"]] = e
    L = []
    L.append("# Ascension cache intake — manifest\n")
    L.append(f"_Generated {time.strftime('%Y-%m-%d %H:%M:%S')}. "
             f"{len(entries)} distinct files, {len(new)} new this run._\n")
    L.append("Dedup is on the **inner file hash**, so identical zip/rar re-submissions "
             "collapse to one row; `submitters` counts how many distinct sources carried it.\n")

    L.append("## Best snapshot per cache type (max records seen = coverage ceiling)\n")
    L.append("| cache | best records | build | from |")
    L.append("|---|---:|---:|---|")
    order = ["itemcache","questcache","creaturecache","gameobjectcache","npccache",
             "pagetextcache","itemnamecache","itemtextcache","wowcache"]
    for c in order:
        if c in best:
            e = best[c]
            L.append(f"| {c} | {e['records']:,} | {e['build']} | {e['group']} |")
    L.append("")

    L.append("## Every distinct cache file\n")
    L.append("| cache | records | size | build | group | submitters | note |")
    L.append("|---|---:|---:|---:|---|---:|---|")
    for e in sorted(entries, key=lambda x: (x["cache"], -x["records"], -x["size"])):
        tag = " **NEW**" if e["sha256"] in {n['sha256'] for n in new} else ""
        L.append(f"| {e['cache']}{tag} | {e['records']:,} | {e['size']:,} | {e['build']} "
                 f"| {e['group']} | {len(e['sources'])} | {e['note']} |")
    L.append("")

    L.append("## Provenance (which source carried each distinct file)\n")
    for e in sorted(entries, key=lambda x: x["group"]):
        L.append(f"- `{e['cache']}` {e['records']:,} rec, sha `{e['sha256'][:12]}` — "
                 + "; ".join(e["sources"]))
    with open(MANIFEST, "w", encoding="utf-8") as f:
        f.write("\n".join(L) + "\n")

def main():
    os.makedirs(INTAKE + "/_inbox", exist_ok=True)
    ex = extract_all()
    print("extract:", ", ".join(f"{n}:{s}" for n, s in ex) or "(none)")
    ledger, seen = scan()
    manifest(ledger, seen)
    tot = len(ledger); new = sum(1 for e in ledger.values() if e["sha256"] not in seen)
    print(f"ledger: {tot} distinct wdb files ({new} new). manifest -> {MANIFEST}")

if __name__ == "__main__":
    main()
