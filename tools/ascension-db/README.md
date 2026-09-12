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


## World Atlas and sortable indexes

The World Atlas includes every published map inventory entry and captured WorldMapArea, enriched with exact-ID Exiles map/area relationships. Custom CoA/Ascension worlds remain navigable even without coordinate observations or artwork. Conflicting names remain attributed aliases; a map ID, WorldMapArea ID and external artwork area ID are distinct namespaces.

Atlas layers retain example creature/object sightings, source-attributed Exiles NPC claims with explicit vendor/quest roles, and LootCollector worldforged/mystic/other loot pins. Vendor and quest filters use same-record headings and stock tables, or explicit catalogue Questgiver types; overlapping roles share one marker. Loot pins mark the player when loot opened, not a confirmed drop source or rate. Catalogue entries are incomplete example sightings. Game modes are never inferred. Source records, coordinate values and map links remain inspectable.

Normalized loot positions are converted to percentages explicitly. Valid zone percentages use reviewed coordinate spaces. Ambiguous instance coordinates and out-of-range raw values use separate schematic views; raw values remain intact and plotted extents are derived per area. Nonfinite/missing coordinates are reported as unmapped. Conflicting captured bounds stop the build. No instance floor is guessed. Internal names such as Aszhara are not silently treated as Azshara.

A small explicit crosswalk permits reference outdoor maps hosted by Wowhead. These images remain external, have no role in record identity, and degrade to a working coordinate grid on failure. No client artwork, terrain binaries or extracted assets are committed or deployed by this feature. Custom zones without an established image association use coordinate views and preserved map metadata.

The atlas supports world/zone search, source/mode/origin/layer filters, marker clustering, mouse/touch/keyboard navigation, observation lists, source-record links and filtered JSON export. Atlas build artifacts are content-addressed per map space, with a reverse record-to-zone index. Publication validation checks coordinates, raw projections, all inventory map identities, facets, counts, hashes and every record link.

Search columns now filter and sort the entire candidate set before pagination: name, exact ID, collection, source and game mode. Integer IDs retain full precision. URL parameters preserve filters, sort direction and page. Search uses bounded worker caches and streams candidates with cancellation and progress; broad initial sorts must read all candidate parts. Nearby pages reuse the ordered result window. Sources & coverage has full pagination, column sorting, source/status/text filters and numeric record-count ranges.

Run the regression checks with `python -m unittest test_catalog test_atlas test_atlas_metadata test_instance_maps -v` and `node --test test_search_worker.cjs test_coverage_controls.cjs test_atlas_roles.cjs test_collections.cjs`. The existing workflow runs these checks plus full catalog/atlas validation before publishing. Parser cache revisions are explicit, so atlas/layout changes do not invalidate unaffected records; bump the relevant parser revision when changing parsing or record identity.


## Instance floor reference maps

A separately attributed Exiles M+ / Keystone.guru snapshot adds paired floor images and image-pixel NPC references. The initial import covers 28 Classic layouts / 63 floors, including separate wings and the Classic 40-player Naxxramas layout. These reference locations do not change or infer floors for existing Exiles claims or LootCollector observations. The headline keeps reference and observed counts separate. Floor tabs show the paired reference views; Original observations opens the existing coordinate evidence.

`capture_instance_maps.py --captured-at YYYY-MM-DD --out <data>/supplemental/instance-route-maps` normalizes the public `https://mplus.exil.es/assets/dungeons.json` export into attributed floor/NPC records. Reviewed server-map identities are explicit; unknown layouts fail closed. Width/height and provider pixel positions are retained, validated, and scaled together. Manual pins without NPC IDs retain synthetic reference identities and have no numeric NPC lookup link. Source snapshot hashes, provider identifiers, era, original pixel values and source-record links remain available. Capturing the same export is deterministic. No third-party script or map artwork is committed or executed; images load from the narrow allowed external host. Missing images use the matching coordinate grid.

The paired image URL and image dimensions must be verified before publication. The data's floor identity is distinct from WorldMapArea, server map, area, and mapping IDs. Original observations are not fitted to reference images, even when a matching NPC ID is known. Source route positions are historical/reference information and may differ from Ascension variants. Future additions require reliable source geometry and a reviewed identity crosswalk.

## Collection discovery

The homepage's All collections directory and both search selectors use the complete published catalog. New structured kinds appear automatically on the next successful data build; locale and mode views retain their existing entity categories. Auctionator observations have the readable label Auction price observations. Labels do not reclassify records or change source counts. Reference-only files remain in Sources & coverage.
