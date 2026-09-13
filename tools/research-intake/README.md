# Ascension Research Intake

Local preservation and structured ingestion for donations that are not covered by
the cache uploader. Python 3.12+; standard library only (Python 3.14 for tar.zst).
No model calls, paid APIs, hosted storage, network fetches, SQL execution, or realm
changes occur during intake.

## Start here

Run `launch.ps1`, choose a short source label, then **Add files** or **Add folder**.
Alternatively put donations under `inbox/<source-label>/`. The desktop checks that
folder every 20 seconds and waits for 60 seconds of unchanged size/mtime before
processing it. Keep the window open for automatic processing. Nothing is removed
from the drop folder. Explicit file selection processes immediately.

The installed maintainer copy uses `C:/AscensionArchive/research-intake` for private
storage. Other users default to `%LOCALAPPDATA%/AscensionPreservation/research-intake`.
The `--root` flag chooses another drive. The tool refuses storage inside a Git
checkout. Stop processing, copy the **whole storage folder** (including SQLite/WAL,
objects, reports, policies and watcher state), verify the copy and change the root
when moving to a new drive. Keep the old copy until verified. Storage is not a backup.

## What is preserved

1. The entire original file is copied in 1 MiB blocks, hashed with SHA-256, flushed,
   and exposed atomically. Same-content files share one stored object. Originals,
   including unknown formats, have no automatic expiration or cleanup.
2. Inner ZIP, TAR and gzip members receive their own hashes, so changing an archive
   wrapper does not duplicate stored payloads. Nested archives are processed up to
   five levels. Every source and member occurrence retains private provenance.
3. Readers write into a local SQLite catalog. Equal record payloads share storage;
   repeated observations and conflicting values keep separate occurrence links.
   A reader failure rolls back that file's entire parse and records a visible hold.
4. IDs, GUIDs, named ID fields and timestamps receive a private lookup index. The
   original fields, names, positions and combat events remain available for future
   joins. Finding the same number in two fields is not proof of entity identity.

The catalog is a preservation/evidence database. It does **not** modify `coa_world`,
make confirmed spawns from player positions, flatten loot pools, resolve conflicting
modes, or decide that an unmatched record must be new game content. Capture time is
never inferred from arrival time. Empty strings, nulls, zeroes, large integer IDs and
comma-decimal strings are retained.

## Reader coverage

| Input | Result |
|---|---|
| JSON top-level arrays, JSONL/NDJSON | Streamed records; nested objects retained |
| Other JSON values | Parsed up to 8 MiB per value; larger objects held |
| CSV/TSV | Named columns, detected comma/semicolon/tab separator; no guessed numeric conversion |
| SQLite backup | Read-only, immutable connection; actual ordinary tables only; no views/triggers executed; blobs base64 encoded |
| PostgreSQL plain SQL COPY | Named table/column rows; SQL and DDL never executed |
| MySQL literal INSERT | Quoted literals and nulls; positional rows remain explicitly unnamed without column lists |
| HTML | Inert visible text, title and original link strings; scripts/styles not run |
| Combat/text logs | Every line plus recognized event/timestamp/GUID fields, privately |
| Ascension `.loc` | Exact ID/text sequence; locale and namespace remain in original provenance |
| Standard WDBC | Uninterpreted u32 rows and exact string bytes; no guessed schema |
| ZIP/TAR/gzip, including nested | Bounded expansion into hash-addressed objects, never extraction to donor paths |
| RAR/7z, custom PG dumps, Lua, terrain, other binaries | Original retained with **reader needed** status |

8 MiB is the per-record limit, not a file-size limit. Multi-gigabyte JSON arrays,
JSONL, SQL COPY and tabular files stream. A MySQL extended INSERT exceeding 8 MiB
on one line, non-UTF-8 text, dialect extensions and giant nested JSON objects need a
specialized reader. An explicit hold is preferable to silently truncating them.
SQLite must be a consistent single-file backup in DELETE journal mode. WAL-mode
files and sidecars are preserved but held, so missing WAL records cannot silently
produce an incomplete catalog.
Archive links, unsafe paths, encryption and resource limits hold the container.
Previously parsed safe members remain separately accounted for.

Defaults: 32 GiB expanded bytes per job, 100,000 files/members, 10 million records,
one hour, 5 GiB free-space reserve. Every copy needs temporary room; indexes and
originals consume local disk. Limits are configurable on `ingest`. A time/space
limit does not delete the source. Mid-copy failures explicitly report that the
input was not successfully preserved. A changed file is reprocessed after it settles.
Watcher detection uses size/mtime; use explicit ingestion if content changes while
both are deliberately preserved. Source labels should describe capture provenance,
not donor contact details.

## Useful commands

Run from this tool directory, always supplying the same storage root:

```powershell
python -B intake.py --root D:/AscensionResearch ingest D:/Donations/scrape.zip --source coa-scrape
python -B intake.py --root D:/AscensionResearch status
python -B intake.py --root D:/AscensionResearch find 1781 --field map_id
python -B intake.py --root D:/AscensionResearch ingest D:/Donations/scrape.zip --source coa-scrape --reprocess
python -B watch.py --root D:/AscensionResearch
python -B baseline.py --root D:/AscensionResearch --data D:/clean-ascension-data --source coa-scrape
```

`find` compares exact indexed values and returns record locations/payloads (at most
1,000). It performs no cross-namespace ID merging or spawn inference. `baseline.py`
indexes the published checkout's tracked TSV and JSONL files, incrementally by Git
blob, then reports exact matching decoded payloads. Unknown published formats remain
outside comparison coverage. Equal payloads do not imply independent corroboration;
unmatched payloads may differ only in formatting/schema. Nothing is discarded.

## AscensionDB publication

Raw donations and the local SQLite catalog remain private. For each trusted source,
create `policies/<source-label>.json` using `policy.example.json`. Record permission,
source title/mode and an explicit mapping of fields allowed in each collection.
Change `publication` to `approved` only after reviewing the source and its fields.
This is a reusable source rule, not a manual review of each future 2 GB donation.
Unknown collections stay local; schema changes and identity/path matches block the
public export. A heuristic privacy screen is an additional check, not a guarantee
that arbitrary prose is safe. Do not approve free-text, player traces, account
schemas or raw databases for public export without an appropriate sanitizer.

The watcher automatically prepares an export when a matching policy exists. It
writes into a separate sibling `research-intake-public-staging` directory. To retry
an export after a policy change, run `export` explicitly (changing a policy alone
does not pretend that the input folder changed).

```powershell
python -B intake.py --root D:/AscensionResearch export --source coa-scrape --policy D:/AscensionResearch/policies/coa-scrape.json --out D:/ResearchPublicStaging
python -B intake.py --root D:/AscensionResearch verify D:/ResearchPublicStaging/supplemental/research-intake/coa-scrape/SNAPSHOT
```

Every export has deterministic, hashed compressed shards, source metadata, original
record/artefact hashes, exact record ordinals, reader version, hold accounting and
omitted-field counts. Default public limits are one million records / 128 MiB
compressed per snapshot; exceeding them holds the export without losing originals.

Use a **dedicated secondary `ascension-data` Git worktree** for delivery. The active
cache publisher checkout is never an export staging directory. `publish.py` stages
only a small verified dataset manifest, refuses unrelated changes, commits locally, and
pushes only when `--push` is explicitly requested. It never force-pushes. A concurrent
remote advance can require reconciling the dedicated branch before retrying; the
snapshot remains intact. Bulk shards are packaged outside Git and uploaded to GitHub Releases before a pushed commit can advertise them. A run without --push prepares packages and commits the manifest locally for review; its release links are not live until publication. See ../data-storage/README.md for downloading and R2 hosting.

```powershell
python -B publish.py --root D:/AscensionResearch --checkout D:/research-data-publication --initialize-checkout
python -B publish.py --root D:/AscensionResearch --checkout D:/research-data-publication --snapshot D:/ResearchPublicStaging/supplemental/research-intake/coa-scrape/SNAPSHOT
# After reviewing the prepared result / granting standing publication authorization:
python -B publish.py --root D:/AscensionResearch --checkout D:/research-data-publication --snapshot D:/ResearchPublicStaging/supplemental/research-intake/coa-scrape/SNAPSHOT --push
```

The accompanying AscensionDB adapter displays each row's declared collection and
source. Update the data workflow's two pinned source revisions to the reviewed
preservation commit when publishing these tool changes. The existing data push
workflow then rebuilds and validates the website. Use the R2 workflow for catalogs beyond Pages capacity; its source builder resolves release manifests. No map inference is performed. Public source-record counts remain distinct from unique
entities. This version prepares automatic exports locally; it does not silently
push donations or extend the public cache-upload endpoint to arbitrary files.

## Verification and deployment

`python -B -m unittest discover -v` covers storage/provenance, malformed data,
archive boundaries, exact comparison, quiet-folder processing, policy gates,
publication and a full AscensionDB build/validator on a synthetic dataset.
`benchmark.py --parent <temporary-disk-directory> --report <report.json>` is an
opt-in >2 GiB structured-file benchmark that cleans up its synthetic inputs.

`deployment.json` lists the installed files. Use Azeroth Control `tools/deploy.py`
to plan/apply/verify source deployment; keep state and policies outside the installed
tool directory. There is no realm restart, service change or public upload-service
change in this deployment.


## Source trust, named evidence and anonymity

`register --source <id> --metadata <private-source.json>` records a source's title,
permission, lane, capture mode/date, frozen/ad-hoc kind, trust and known caps in the
private catalog. Public rules may specify `trust`, `known_caps` and
`observation_type` for each collection; defaults stay unknown. Scraped or aggregated
loot remains explicitly a claim. Future server-gap tooling can consume these fields
without treating every ingested record as authoritative.

Every ingestion report distinguishes local receipt (L0), parsing (L1) and public
publication (not established by ingestion), and includes up to five readable named
examples when available. Empty localisation strings are preserved; row counts do
not claim that every row contains a translation. This implements the preservation
part of Claude's 2026-09-12 handoff; its server snapshots, SQL packages, realm tests,
PRs, history cleanup and backup proposals remain separate work.

For sources requiring anonymity, configure a private `publication-config.json`
under the intake root with `identity_patterns_file` pointing to an external UTF-8
file containing one regular expression per line (`#` comments ignored). Patterns
and matching identities are never logged or copied into source, exports or test
fixtures. An invalid, missing or empty required file blocks publication. The
maintainer installation is configured to use its existing private pattern file.
Generic installations without this configuration report `identity_scan:
not-configured`; this is not a clean anonymity verdict. `verify` and `publish.py`
must receive the same `--root` for exports requiring this scan. Identity rules,
source trust and policy revisions participate in snapshot identity.
