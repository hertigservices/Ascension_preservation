# How community contributions become the public dataset

This guide describes the processing source published on 2026-09-11. The source
repository and [ascension-data](https://github.com/hertigservices/ascension-data)
serve different purposes: this repository contains the software; ascension-data
contains its published outputs. A dataset push does not update this source repository.

## Follow a contribution

1. **Select and filter on the contributor's computer.** The
   [folder picker](../../cache-upload/lib/game-folders.mjs) accepts the client root
   or the actual WDB/Account folder. The [preparation worker](../../cache-upload/lib/prepare.worker.mjs)
   inspects supported files, filters Lua branches and splits large WDBs at record
   boundaries. Original account directory paths are not sent in upload metadata.
   [policy-contract.json](../../cache-upload/shared/policy-contract.json) is the
   authoritative supported-name, mode and review list; [policy.mjs](../../cache-upload/shared/policy.mjs)
   implements the checks. Lua is parsed as literal data, never executed.
2. **Receive into private storage.** The [HTTP API](../../cache-upload/lib/intake-api.mjs)
   checks Turnstile, origin, admission limits, file metadata and checksums. It stores
   parts privately in R2 and queue state in D1. A receipt token grants status access,
   not access to uploaded files. Receiving an upload does not mean it is published.
3. **Collect and validate again.** The [collector](../../cache-upload/collector/collector.py)
   claims leased jobs, downloads and verifies the parts, and invokes the
   [Node validator](../../cache-upload/collector/validate.mjs) with resource limits.
   Accepted files are staged for processing. The current policy holds AIO,
   CoASniff and WildcardHarvest for private review; a mixed contribution may publish
   accepted data while its review-only material remains held. A review hold is not
   a parser failure and is not bypassed by successful WDB processing.
4. **Coordinate online and manual work.** The collector batches eligible uploads;
   [intake_jobs.py](../tools/intake_jobs.py) provides the durable manual queue and
   completion journal. The [publisher](../tools/publish.py) takes an exclusive lock,
   checks that incoming contents have settled, and runs the pipeline. The desktop
   window is a control/status view; closing it does not stop the background collector.
5. **Extract, decode and consolidate.** [update.py](../tools/update.py) lists the
   actual stage order. [intake.py](../tools/intake.py) extracts archives, checks WDB
   structure through [wdblib.py](../tools/wdblib.py), and ledgers inner-file SHA-256
   hashes. [merge.py](../tools/merge.py) stores records by entry ID and payload hash.
   Identical records share provenance; differing payloads remain separate variants.
   [luamerge.py](../tools/luamerge.py), [harvestmerge.py](../tools/harvestmerge.py) and
   [reference_merge.py](../tools/reference_merge.py) handle their respective game-data
   trees. Manual research inputs can also supply world-dump catalogues and server
   terrain inventories. [mapdata.py](../tools/mapdata.py) publishes inventory metadata,
   not terrain binaries. Those manual inputs are broader than the browser allowlist.
6. **Generate usable views.** [export.py](../tools/export.py) produces per-mode and
   union decoded views as well as raw variant views. [capture_dates.py](../tools/capture_dates.py)
   implements the capture-date ranking and deterministic tie breaking used to choose
   a view's winner. A view is a selection, not proof that every source agrees.
   [rebuild.py](../tools/rebuild.py) rebuilds client WDBs; the stock-client exporters
   generate their separate outputs. [build_cache.py](../tools/build_cache.py) reuses
   generated categories only when recorded inputs, rules and output hashes still
   match. This does not skip merging or publication audits.
7. **Audit, then publish.** [audit_columns.py](../tools/audit_columns.py) checks for
   unexplained loss of populated fields against documented
   [expectations](../tools/column_expectations.json). [audit_publish.py](../tools/audit_publish.py)
   checks the output for prohibited identifiers. These are concrete checks, not a
   guarantee that arbitrary data can never contain personal information. The publisher
   refuses failed stages, audits the dataset checkout, commits eligible changes and
   pushes normally. The narrowly targeted unresolved-delta retry uses `--no-thin`;
   it does not force-push or bypass the gates.
8. **Confirm and file.** The collector verifies remote publication and bundle evidence
   before reporting published. Exact repeats can reuse an existing published commit
   only with valid evidence; a matching filename alone is insufficient. Inbox filing
   requires successful incorporation and unchanged content hashes. It moves eligible
   roots to the archive, preserving originals. Unsupported or incompletely accounted
   inputs remain pending. Successfully merged zero-record Lua data can qualify when
   the merger's completion metadata is present; malformed or unrecognized data cannot.

## Storage and failure behavior

R2 upload objects have a configured retention period; deleting cloud upload objects
is separate from retaining the local original archive and published dataset. Retention
requires the operator's bucket lifecycle settings as well as collector cleanup. Do not
infer that an empty cloud bucket means local evidence was deleted. Private config,
secrets, raw submission directories and queue/receipt tokens are not source artifacts.

A failed audit leaves the contribution unpublished for investigation. Temporary lock
contention and eligible retry conditions retain durable work. The receipt and desktop
view distinguish receiving, processing, publication and review holds. Historical source
or data publication does not mean every new contribution is automatically safe to publish.

## Installed source versus this checkout

The accompanying [hash inventory](installed-source-hashes.json) records the installed
consolidator source on 2026-09-11, hashing text after normalizing CRLF to LF. Most modules
match this checkout. [INSTALLED-VARIANTS.patch](INSTALLED-VARIANTS.patch) records the
remaining differences: the installed privacy audit has a narrower exact-string
exception list, and the installer/importer's help and missing-dataset handling are older.
The public exporter also normalizes Windows short-path aliases before pruning reused
outputs; this portability fix was discovered by CI and is not in the recorded deployment.
Apply the patch to a separate LF checkout (`git -c core.autocrlf=false clone ...`)
to inspect the exact installed variants.
Configuration, optional lookup data, local inputs and credentials are intentionally excluded.
This is a dated source comparison, not a promise that a future deployment remains identical.

The collector and shared validation/policy files were also compared with their installed
copies. Third-party `luaparse` code is obtained through the committed npm lockfile;
node_modules is not vendored. The upload service source and its deployment instructions
are in the [upload README](../../cache-upload/README.md).

## Reproduce checks without touching a real inbox

Use a disposable work/output directory and dataset repository; set `ASCENSION_CACHE_WORK`,
`ASCENSION_CACHE_OUT`, `ASCENSION_CACHE_DATA` and `CONSOLIDATOR_REPO` accordingly, and clear
`ASCENSION_CACHE_EXTRA_ROOTS`. Do not run publisher commands against the maintainer's live
configuration for a code review.

Run each `tools/cache-consolidator/tools/test_*.py` script with Python. In
`tools/cache-upload`, install the locked Node dependencies with `npm ci`, then run
`npm test` and `python -B -m unittest discover -s tests -p "test_*.py"`.
The desktop tests require Tk; Windows Python includes it. These tests use synthetic
inputs and temporary directories. They do not establish in-game compatibility or
perform a production Cloudflare challenge or GitHub dataset push.
