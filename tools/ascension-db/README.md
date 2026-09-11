# AscensionDB preservation browser

A static, searchable view of **all tracked files** in a published `hertigservices/ascension-data` snapshot, styled after recovered AscensionDB pages. The interface is independent of the uploader, private collector, and consolidator. It reads no private submission directories.

## What is included

- Every published TSV row and JSONL record, including mode-specific views, union views, raw-variant index entries, world observations, Exiles DB and BisBeard.
- Structured addon JSON entries, with their complete original payload available in record details.
- Every other tracked file in Sources & coverage, linked to its exact source commit and original download: binary WDB/pack files, Lua code and observations, original supplemental databases, images, documentation and manifests.
- A bounded Internet Archive recovery inventory, verified page captures and short excerpts linked to their originals. Recovered scripts are not executed; guide articles are not republished in full.

**Searchable source-record counts are not unique game-entity counts.** A union view, a mode-specific capture, raw variant metadata and a website's claim can all describe the same ID. These remain separate. Selecting an ID across sources is a comparison aid, not proof that different entity types or planner IDs are interchangeable.

### Coverage boundaries

Names and exact numeric IDs are searchable. Names match word prefixes (two or more letters); description text is readable on record pages but is not a full-text search index. All original fields are retained, including conflicts and provenance fields. IDs stay strings in search results. A missing mode is displayed as Unspecified, never guessed from an addon or website.

Raw-variant index rows describe retained payloads; they are not a promise that every binary variant has a decoded detail page. Lua files and other unsupported formats remain references, with a reason visible in the report. New TSV/JSONL parsing errors fail the build; unknown new formats remain explicitly listed as references. Coverage is of the published repository, not of all game content or unpublished/raw submissions.

WDB item definitions do not establish loot sources; game-object definitions do not establish positions. Supplemental drop lists, coordinates and descriptions remain claims attributed to the supplemental source. The world catalogue is an observation catalogue, not a complete spawn table.

## Build and verify

Python 3.12+ and Git; no Python packages required. Use a clean detached checkout of the published data commit, never the active consolidator working tree.

```sh
python -m unittest test_catalog -v
python build.py --data /path/to/clean/ascension-data --out dist
python validate.py dist
python -m http.server 5184 --directory dist
```

`--sample N` creates a visibly marked development sample. The publication validator rejects samples. A source blob plus its path and builder version keys each parsed-file cache; unchanged files reuse the exact parsed data and index. Global search partitions are regenerated to account for additions/deletions. Compressed, content-addressed search files and detail chunks are loaded on demand by a browser worker. Unfiltered collection browsing fetches only enough chunks for the requested page.

`build-report.json` records the input commit, output counts/size, and parsed/reused file counts. `coverage.json.gz` accounts for every source file. `manifest.json` maps indexed files to Git blob IDs. Original downloads always point at the recorded source commit.

## Automatic publication

The reusable `.github/workflows/ascension-db.yml` workflow is called by a small workflow in `ascension-data` on every main-branch push. The caller pins the reviewed preservation-source commit. GitHub builds a complete snapshot, runs rule tests and validates every detail/search partition, then publishes a GitHub Pages artifact. Failed builds never reach the deploy job; the previous deployment stays online. Concurrent pushes are serialized (GitHub may coalesce superseded pending runs); the newest snapshot is eventually published.

The Cloudflare `ascension-db` Worker serves the interface only. Its `config.json` points at `https://hertigservices.github.io/ascension-data/` for catalog data. This separates interface hosting from data publication and avoids placing a Cloudflare account credential in GitHub Actions. The GitHub Pages version is also a complete independent copy of the interface.

Source changes require updating the pinned caller revision and deploying the frontend; data pushes require no manual action. This initial builder reparses only changed input files, but changed compressed exports still require parsing that entire file. This does not modify or slow the consolidator's publishing algorithm.

## Recovery evidence

`recover.py` performs bounded public Wayback requests with delays, size limits, recorded hashes and explicit failures. It normalizes relative homepage links, rejects empty responses and obvious archive/error wrappers, and never executes downloaded content. Raw evidence stays in the chosen research directory. `recovered/report.json` contains the publishable inventory, source/capture URLs, outcomes and limited excerpts. The inventory is explicitly incomplete; archive listings alone are not treated as recovered pages.

## Security and operational model

The browser treats all dataset fields as text, escapes them before display, permits only HTTP(S) source links, and runs searches in a separate worker. No uploaded Lua or archived JavaScript executes. It has no write endpoints, account login, API secrets or private intake access. Static hosting headers restrict scripts to this origin. Existing visitors may need to refresh after a new snapshot if an old content-addressed chunk is no longer present; the interface reports that condition.

For the intake/processing mechanism, see `../cache-consolidator/docs/PROCESSING-PIPELINE.md`.

## Verification notes

The initial browser checks cover name and exact-ID search, collection browsing, record details/download links, coverage filtering, recovered pages, mobile overflow, and injected HTML remaining inert. The optional `document.modelContext` search adapter was exercised through a browser test harness (valid result and intentional invalid-input failure); native WebMCP availability depends on the browser and was not independently verified. Python regression tests cover provenance, variants, malformed TSV rejection, explicit synthetic row references and large string IDs. Full-data validation is a separate publication gate.
