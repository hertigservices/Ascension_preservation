# AscensionDB preservation browser

A static, searchable view of **all tracked files** in a published `hertigservices/ascension-data` snapshot, styled after recovered AscensionDB pages. The interface is independent of the uploader, private collector, and consolidator. It reads no private submission directories.

## What is included

- Every published TSV row and JSONL record, including mode-specific views, union views, raw-variant index entries, world observations, Exiles DB and BisBeard.
- Structured addon JSON entries, with their complete original payload available in record details.
- Every other tracked file in Sources & coverage, linked to its exact source commit and original download: binary WDB/pack files, Lua code and observations, original supplemental databases, images, documentation and manifests.
- A bounded Internet Archive recovery inventory, verified page captures and short excerpts linked to their originals. Recovered scripts are not executed; guide articles are not republished in full.

Search results group records with identical preserved content across all collections.
A group is keyed by collection, exact ID, title and complete typed payload. Only the
four documented WDB-view bookkeeping fields (`_modes`, `_captured`, `_sources`,
`_locales`) are excluded, and only for decoded cache-view paths. Unknown fields,
translations, gameplay differences and other source claims remain distinct. This
is exact content grouping, not a claim that grouped counts are unique game entities.

Each result expands to the original source records and file paths. Mode, source,
language and zone filters must match the **same member**, before sorting and paging;
the main link opens a matching original. Zone partitions contain only the group's
members actually assigned to that zone. Language comes from an explicit locale
export or a singleton cache locale; aggregate mode/locale sets do not establish pairs.
Unspecified language remains selectable. SQL export expands the filtered groups back
to all matching original records, preserving its existing provenance and reward rules.

Original records, source downloads, coverage counts and atlas observations are unchanged.
`manifest.grouping` records content counts; `manifest.records` still counts source
records. Search row field 8 contains `{id, members}`; each member is
`[recordKey, mode, source, locale]`. Old ungrouped snapshots remain readable.
SQLite bounds build memory; one group appears once per search bucket, so the browser
keeps its bounded sorting windows. Groups over 24 MB fail publication explicitly.
The validator verifies every member against the original content and facets and
requires every original record exactly once across grouped collection buckets.

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


## Compact atlas controls

Tracking markers use small inline SVG icons and screen-space proximity grouping. Every numbered group opens a persistent member chooser, including exact overlaps at maximum zoom; selecting a member preserves the chooser. Filtering closes the chooser, and keyboard focus returns to the matching map marker after zooming. Only roles present in the zone appear as controls; herb/ore display filters use exact resource names and preserve the original object records in exports. Generic NPCs remain neutral; creature sightings use red tracking dots. Advanced filters, map metadata and full source details are expandable, with active filter state visible.

Atlas zone search, map location search, database search and the Name column recognize a shared, reviewed set of dungeon/raid/city shortcuts (BWL, MC, LBRS, SM lib, and others). Search includes literal matches and every recognized meaning of ambiguous aliases such as DM. IDs stay exact, wing queries stay specific, and alias unions retain each source record once before sorting/pagination. Names and map identities are never rewritten.

## Zone filtering

Quests, creatures, world-object/creature observations, loot observations and instance
reference NPCs have a Zone combobox beside the collection filters. Type a name or
area ID to narrow the list, scroll to browse, or use arrow keys and Enter to select.
The choices reflect the selected collection. The exact zone selection is retained
in the URL and combines with name, ID, source, mode, sorting and pagination.

The index uses positive quest `ZoneOrSort` area IDs, explicit Exiles area/location
claims, exact NPC-area claims derived from the companion Exiles database export,
all catalogue `zone_list` entries (even without usable coordinates), loot
observation zones and explicit instance reference floors. Recorded sub-zones also
match their published parent areas. Negative quest categories
and server map IDs are not interpreted as zone IDs. Unknown/custom area IDs stay
searchable with an `Area <ID>` label if no name is known. Records without zone
evidence remain under Unknown zone. Companion export claims join only to Exiles NPC
pages in the same provider ID namespace; locations are never copied to client captures,
other sources or modes. These are recorded zones/locations, not
a complete spawn census. Existing source records and atlas geometry are unchanged.

Zone partitions use the existing content-addressed search format, so filtering
applies before sorting/paging without loading all record details in the browser.
Older manifests without `zoneFilter` continue to work with the control hidden.
Run `python -m unittest test_zone_filter` and `node --test test_search_worker.cjs`.

## Search icons

Search results show an icon beside the name when the build is given an icon host: `--icon-base`, the `ASCENSIONDB_ICON_BASE` environment variable, or the reusable workflow's optional `icon_base` input. Without one, the column is omitted.

- **When icons attach:** at search-index time, as an optional seventh field of a search row. Parsed-file caches are untouched, so enabling icons never forces a reparse.
- **Which collections get icons:** items, item names, loot pins, spells, achievements, currencies and item display icons. BisBeard planner IDs are not item IDs and never get one.
- **Item icons come only from captured client data:** `cachedata/dbc/item_display_icons.tsv.gz`, joined through the union item cache's display IDs.
  - The Exiles site and its database export disagree with the client on 15-18% of the items both cover. A glyph is drawn as a bracer, a chestplate as a cloak. That is consistent with a stock display-id join against Ascension's renumbered ItemDisplayInfo.
  - Their item icons are never used, not even where the client has none: an empty slot is better than a wrong icon.
- **Spell, achievement and currency icons:** Exiles DB spell pages, overridden by a published database export's icon map (`supplemental/<set>/<sha>/icon-map.csv.gz`, columns `kind,id,icon`). The map's item rows are ignored.
- **Only published icons are used.** A name counts only when that icon file is itself published: `supplemental/*/assets/icons/<name>.png`, or the `static/icons-clean/<name>.png` rows of an export's `ASSET_INDEX.csv(.gz)`. Names are normalised: HTML entities decoded, lowercase, with folder and image extension removed.
- **Browser safety:** the browser accepts only an https base or a plain relative path, escapes every name, and replaces an image that fails to load with an empty slot. The icon host must be allowed by `img-src` in `web/_headers`.
- **Build record:** `manifest.json` records the base, the number of published icons and the number of rows carrying one.

Run `node --test test_search_icons.cjs` for the rendering rules and `python -m unittest test_catalog` for the icon index.

## Research donations

`../research-intake` preserves non-cache donations privately and exports explicitly
approved fields under `supplemental/research-intake`. Catalog rows retain source
title, declared entity kind, mode, artifact/record hashes and original ordinals.
Raw private files are never read by the website builder. These are attributed
claims, not confirmed server state or inferred map pins.
