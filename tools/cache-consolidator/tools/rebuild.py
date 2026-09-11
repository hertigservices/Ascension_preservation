"""Rebuild merged caches back into real, client-loadable .wdb files.

The decoded TSVs are for humans and databases; this is for the client.  For every
game mode we emit one .wdb per cache type containing the merged union of every
record that mode ever produced -- so a player drops in a cache that already knows
75,000 items instead of the ~40,000 any single submission held.

Header handling: the 24-byte header (magic, build, locale, recordSize, recordVersion,
cacheVersion) is copied VERBATIM from a real submitted file of that cache type, newest
first.  Those three trailing fields are client-version-sensitive and inventing them
risks the client rejecting or mis-reading the file, so we never synthesise them.

Records are written sorted by entry, then the 8-byte zero terminator the client
expects.  Sorted order is deterministic, so an unchanged store rebuilds byte-identical
and git sees no diff.

Every file written is re-parsed and compared payload-by-payload against what went in;
a mismatch is a hard failure, not a warning.

They are published gzipped -- see export.py; the merged season-10-freepick
itemcache alone is 254 MB, past GitHub's hard limit.  The gzip is verified to
decompress back to the exact bytes that were verified as a cache, so the two
proofs meet: what the reader unpacks is what we parsed.
"""
import os, sys, gzip, struct, collections, time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import wdblib, merge, export, build_cache

OUT = export.OUT + "/wdb"

# itemtextcache carries the text of MAIL and letters the player read -- that is other
# people's writing, not game content. Never republish it.
NEVER_PUBLISH = {"itemtextcache", "wowcache", "lua", ".merge-transaction"}


def header_for(cache, slug, srcs, locale="enUS"):
    """Use an observed header of the correct locale, persisted before archiving."""
    cands = [s for s in srcs if s["cache"] == cache and str(s["records"]) != "0"]
    same = [s for s in cands if s["slug"] == slug]
    for pool in (same, cands):
        for s in sorted(pool, key=lambda r: r["captured"], reverse=True):
            try:
                h = bytes.fromhex(s.get("header_hex", ""))
                if len(h) != wdblib.HEADER_LEN:
                    with open(s["path"], "rb") as f:
                        h = f.read(wdblib.HEADER_LEN)
                if len(h) == wdblib.HEADER_LEN and wdblib.read_header(h)[2] == locale:
                    return h, s
            except (OSError, ValueError):
                continue
    return None, None


def build_wdb(header, winners):
    """The exact bytes a client would find on disk."""
    buf = bytearray(header)
    for entry in sorted(winners):
        _sha1, _r, payload = winners[entry]
        buf += struct.pack("<II", entry, len(payload)) + payload
    buf += b"\x00" * 8                      # terminator the client walks to
    return bytes(buf)


def verify(b, winners, path="<memory>"):
    """Re-parse what we just built and compare every payload. Returns (ok, detail)."""
    info = wdblib.inspect(path, data=b)
    if not info.standard:
        return False, "header not recognised after write"
    if info.records != len(winners):
        return False, f"record count {info.records} != {len(winners)}"
    if not info.clean_end:
        return False, "no clean terminator"
    for entry, size, payload in wdblib.iter_records(b):
        want = winners.get(entry)
        if want is None:
            return False, f"entry {entry} not in source set"
        if want[2] != payload:
            return False, f"payload mismatch on entry {entry}"
    return True, f"{info.records} records, {info.cache}, build {info.build}"


def main():
    t0 = time.time()
    srcs = merge.read_tsv(merge.SOURCES, merge.SRC_COLS)
    caches = sorted(d for d in os.listdir(merge.STORE)
                    if os.path.isdir(f"{merge.STORE}/{d}") and d not in NEVER_PUBLISH)
    slugs = sorted({s["slug"] for s in srcs if s["slug"] and s["records"] != "0"})

    built = collections.defaultdict(dict)
    failures = []
    written = []
    reuse = build_cache.BuildCache('rebuild', OUT)
    for cache in caches:
        donors = {slug: header_for(cache, slug, srcs)[0] for slug in slugs}
        def signature(headers):
            return build_cache.digest([build_cache.cache_inputs(cache, srcs, slugs),
                {slug: (h.hex() if h is not None else None)
                 for slug, h in headers.items()}])
        inputs = signature(donors)
        hit = reuse.load(cache, inputs)
        if hit:
            meta, paths = hit
            for slug, result in meta.items():
                built[slug][cache] = result
            written.extend(paths)
            print(f"  {cache:<18} reused verified WDB files")
            continue
        start, failure_start = len(written), len(failures)
        recs = export.load_cache(cache)
        if not recs:
            continue
        for slug in slugs:
            w = export.pick_winners(recs, mode=slug, locale="enUS")
            if not w:
                continue
            header = donors[slug]
            if header is None:
                failures.append(f"{slug}/{cache}: no header donor")
                continue
            p = f"{OUT}/{slug}/{cache}.wdb.gz"
            data = build_wdb(header, w)
            export.write_gz(p, data)
            written.append(p)
            ok, detail = verify(data, w, p)
            if ok:
                # The compression is the new failure mode this packaging adds, so
                # prove it rather than trusting it: what unpacks must be the exact
                # bytes that just passed the cache check.
                with gzip.open(p, "rb") as g:
                    if g.read() != data:
                        ok, detail = False, "gzip did not round-trip to the same bytes"
            if not ok:
                failures.append(f"{slug}/{cache}: {detail}")
            built[slug][cache] = (len(w), len(data), os.path.getsize(p), ok)

        for locale in sorted({loc for _, _, row, _ in recs for loc in row.get("locales", "unknown").split(",")} - {"enUS", "unknown"}):
            if not __import__('re').fullmatch(r"[a-z]{2}[A-Z]{2}", locale):
                raise RuntimeError("Invalid locale")
            for slug in slugs:
                w = export.pick_winners(recs, mode=slug, locale=locale)
                if not w:
                    continue
                header = header_for(cache, slug, srcs, locale)[0]
                if header is None:
                    failures.append(f"{locale}/{slug}/{cache}: no header donor")
                    continue
                p = f"{OUT}/by-locale/{locale}/{slug}/{cache}.wdb.gz"
                data = build_wdb(header, w)
                ok, detail = verify(data, w, p)
                if not ok:
                    failures.append(detail)
                    continue
                export.write_gz(p, data)
                with gzip.open(p, "rb") as f:
                    if f.read() != data:
                        raise RuntimeError("Localized WDB compression mismatch")
                written.append(p)
        if inputs != signature({slug: header_for(cache, slug, srcs)[0] for slug in slugs}):
            raise RuntimeError('merged inputs or header donors changed during rebuild')
        if len(failures) == failure_start:
            reuse.save(cache, inputs, written[start:],
                       {slug: result[cache] for slug, result in built.items() if cache in result})

    print(f"{'mode':<22}{'cache':<18}{'entries':>9}{'cache bytes':>14}"
          f"{'.gz':>12}  verified")
    for slug in sorted(built):
        for cache in sorted(built[slug]):
            n, size, gz, ok = built[slug][cache]
            print(f"{slug:<22}{cache:<18}{n:>9}{size:>14,}{gz:>12,}  "
                  f"{'OK' if ok else 'FAILED'}")
    if failures:
        print("\nFAILURES:")
        for f in failures:
            print("  " + f)
    else:
        print("\nall rebuilt caches re-parsed and matched payload-for-payload")

    if failures:
        return 1
    write_readme(built)
    # A mode that no longer exists must not leave a stale .wdb behind: it would
    # look exactly like a current one and hand someone a cache we cannot vouch
    # for.  README.md is this run's own output, so it is protected.
    for gone in export.prune(OUT, written, protect=("README.md",)):
        print(f"  pruned stale wdb/{gone}")
    print(f"\nwdb -> {OUT}  ({time.time()-t0:.0f}s)")
    return 1 if failures else 0


def write_readme(built):
    L = ["# Merged client caches (.wdb)\n",
         "One folder per game mode. Each file is the union of every record that mode",
         "has produced across all submitted caches, in the client's own format.\n",
         "## Using them\n",
         "They are stored gzipped, because the largest is 254 MB uncompressed and",
         "GitHub refuses any file over 100 MB. Unpack a mode straight into your own",
         "cache folder with the tool in this repository:\n",
         "```",
         "python tools/unpack.py --mode <mode> --into \"C:/.../WDB/enUS/<Your Realm> - <Mode>\"",
         "```\n",
         "Or do it by hand — `gunzip` each file, then copy it into:\n",
         "```",
         "World of Warcraft\\Cache\\WDB\\enUS\\<Your Realm> - <Mode>\\",
         "```\n",
         "The folder is named after **your** realm, so pick the folder matching the mode",
         "and copy the `.wdb` files into your existing realm folder. Close the client",
         "first — it rewrites these files on exit and will overwrite your changes.\n",
         "A cache only ever helps the client skip a lookup it would otherwise ask the",
         "server for. If a record here is stale relative to your realm, the client",
         "corrects it the next time the server sends that entry.\n",
         "## Contents\n",
         "| mode | " + " | ".join(sorted({c for v in built.values() for c in v})) + " |",
         "|---|" + "---:|" * len({c for v in built.values() for c in v})]
    allc = sorted({c for v in built.values() for c in v})
    for slug in sorted(built):
        L.append(f"| `{slug}` | " +
                 " | ".join(f"{built[slug][c][0]:,}" if c in built[slug] else "—"
                            for c in allc) + " |")
    L += ["", "Counts are entries, not file sizes; the files on disk are `.wdb.gz`.\n",
          "`unknown` is not a game mode. It is what arrived with no realm folder and",
          "could not be identified from its contents -- creature, gameobject and NPC",
          "records look the same in every mode, which is precisely why they cannot",
          "name one. Those are safe to use anywhere; its quest text may not be."]
    L += ["", "`itemtextcache` is deliberately never published: it holds the text of mail",
          "and letters the player read, which is other people's writing, not game data.\n"]
    os.makedirs(OUT, exist_ok=True)
    with open(f"{OUT}/README.md", "w", encoding="utf-8", newline="\n") as f:
        f.write("\n".join(L) + "\n")


if __name__ == "__main__":
    sys.exit(main())
