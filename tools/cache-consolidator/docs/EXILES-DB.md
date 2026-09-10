# Exiles database supplemental importer

This importer preserves a reviewed offline mirror of `db.exil.es` as a separate,
queryable catalog. It does not write a realm database, manufacture WDB records,
replace captured values, or infer any numeric field the site did not state.

`db.exil.es` ("coa-db") is a Conquest of Azeroth database site whose own footer
attributes it to the Project Ascension guild *Exiles*. The mirror was published
as [`Duff-SPP/AcensionOfflineDatabase`](https://github.com/Duff-SPP/AcensionOfflineDatabase),
crawled 2026-08-29 and released 2026-09-10 as a single-volume 7-Zip archive. That
attribution does not authenticate the crawl or establish the site's accuracy. No
license grant from either party is asserted by this documentation.

**The upstream site's data backend was unreachable when this catalog was built.**
The site root and the `i.exil.es` asset host answered from cache, while every
entity page and every `/api/v1/` path returned `503 Backend fetch failed`. The
mirror should therefore be treated as difficult or impossible to re-collect. It
is also, for the same reason, impossible to re-verify against the live site.

## What the mirror is, and what it is not

It is a crawl of rendered HTML: 517,758 files and 13.75 GB, of which 493,029 are
pages. It is **not** a database dump and **not** an API capture. The site does
publish a JSON API whose schemas are DBC-grade — `SpellEffectDto` alone carries
`aura_id`, `mechanic`, `misc_value`, `target_a`/`target_b`, `base_points`,
`die_sides`, `real_points_per_level` and `damage_multiplier` — but the crawl
captured only two of its entity responses. Everything else survives as rendered
display text: `"Instant"` rather than `cast_time_ms`, `"Physical"` rather than
`school_mask`. `openapi.json` is preserved so the field map survives even though
the endpoint that served it does not.

The one place rendered markup carries raw values is the item tooltip, which
embeds HTML comment markers such as `<!--stat7-->` before `+25 Stamina` and
`<!--?28231:1:80:70-->` at the end. Those codes are preserved **verbatim and
uninterpreted**: `stat7` is recorded as `stat7`, never as "Stamina".

### The specification's own declarations

`openapi.json` states `info.license` as **`AGPL-3.0-or-later`** and attributes
contact to **Sub-Net e.U.** Both are recorded in the manifest under
`source.api_specification`. The licence line describes the API the document
specifies; it is not a grant covering the mirrored database content, and this
catalog asserts nothing about how that content may be reused.

The specification also arrived with a named individual's email address in
`info.contact`. The published copy is byte-identical to the original except that
every address is replaced with `<redacted: contact address>`; the organisation
name is kept, the redaction count and the unmodified file's SHA-256 are recorded
in the manifest, and `verify` refuses a snapshot in which one reappears.

## Reproduce the import

Python 3.10 or newer; standard library only. Run from this cache-consolidator
component directory. Extract the archive, then use actual paths in place of the
examples. Indexing rehashes every mirrored file, so the import reads the whole
13.75 GB once and takes appreciably longer than the other importers.

```sh
python -B tools/import_exiles.py import MIRROR/ExilesOfflineDB --site-db MIRROR/ExilesOfflineDB/data/offline_site.sqlite --archive ExilesOfflineDB_Baseline.7z.001 --expected-sha256 REVIEWED_ARCHIVE_SHA256 --output supplemental/exiles-db --cache cachedata/union --baseline-revision FULL_BASELINE_GIT_COMMIT
python -B tools/import_exiles.py verify supplemental/exiles-db/REVIEWED_ARCHIVE_SHA256
python -B tools/test_exiles.py
```

The SHA-256 argument binds the operation to the archive reviewed by the operator;
a mismatch fails before anything is written. The archive is only hashed — its
bytes and local path are not copied. Page-to-file mapping comes from the
crawler's own `offline_site.sqlite`, not from directory walking, so a page the
crawler never recorded is never published, and a route whose file is absent is
counted in `missing_files` rather than silently skipped.

## Output contract (schema version 1)

A successful import creates `OUTPUT/<full-archive-sha256>/`:

| File | Content |
| --- | --- |
| `mirror.index.tsv.gz` | Path, size and SHA-256 of all 517,758 mirrored files. |
| `names.tsv.gz` | The crawler's whole search index: page type, key, name and URL. |
| `spells.jsonl.gz` | Spell pages: info table, effects, icon, school class, casting NPCs. |
| `items.jsonl.gz` | Item pages: tooltip lines with marker codes, drop sources, change history. |
| `npcs.jsonl.gz` | NPC pages: meta, display id, loot and pickpocket tables with the site's chances, cast lists. |
| `quests.jsonl.gz`, `achievements.jsonl.gz` | Quest and achievement pages. |
| `entities.jsonl.gz` | Every other page type: area, dungeon, map, skill, currency, pets, gameobject, class, listing. |
| `talents.jsonl.gz` | Talent trees: each cell's spell id, name, icon, grid position and maximum rank. |
| `changes.jsonl.gz` | The daily changelog: field-level diffs with old and new values, significance and source. |
| `histories.jsonl.gz` | Per-entity change history pages. |
| `structural-pages.tar.gz` | Byte-exact original HTML of the talent tree and class pages. |
| `openapi.json.gz` | The original API specification, with contact addresses redacted in place. |
| `catalog.sqlite.gz` | Queryable entity, tree and talent tables. |
| `comparison.json.gz` | Named conflict, missing-candidate and unnamed lists against a captured baseline. |
| `manifest.json` | Schema, provenance, counts, interpretation limits, and every artifact's hash and size. |

Gzip timestamps are zeroed and every stream is written in sorted route order, so
a rebuild from the same mirror and baseline is byte-identical. Every record
carries `source_path` and `source_sha256` naming the mirrored file it was parsed
from, and `verify` requires that hash to match `mirror.index.tsv.gz`. The file
index lives only in that TSV: repeating half a million high-entropy hashes inside
the SQLite as well would roughly double the catalog to say the same thing twice.

### Every record also keeps the page's generic structure

A hand-written extractor knows the fields worth naming. It does not know what a
page carries that nobody has looked at yet. Records from tuned page types
therefore also carry a `structure` object — headings, `paragraphs`,
`list_items`, `fields`, `meta`, `tables`, `history` and `references` — built by
the same generic reader used for untuned types.

`fields` is the one that is easy to miss: the site renders several single values
as a bare `<div class="quest-rewards__xp">Experience: 33,100 XP</div>`, belonging
to no list, table or definition list. Money, experience, talent-point and
learned-spell rewards on quests all take that shape, and an extractor that read
only lists and tables dropped every one of them silently.

The extractors were checked by comparing, for each page type, the word multiset
of the page's rendered main content against the word multiset of everything
stored in the record. Every entity page type now reports nothing missing. What
remains uncaptured on a handful of types is interface chrome and figures derived
from data that *is* captured: the changelog's filter bar, a class page's
`473 abilities · 65 talents` summary line, and the `0/` current-rank prefix on a
talent cell whose maximum rank is stored as an integer.

### How this differs from the BisBeard importer

`import_bisbeard.py` preserves its whole source and re-derives every record from
it during `verify`. A 13.75 GB mirror cannot be republished that way, so this
importer preserves original bytes only where the markup *is* the data — the
talent tree and class pages — and identifies the rest by hash. `verify` proves
the published artifacts are internally consistent, unmodified, free of
personal-data indicators, and traceable to named mirrored files; **it cannot
prove the parse was faithful to bytes it does not hold.** A reader who obtains
the upstream archive can close that gap with `mirror.index.tsv.gz`.

## Two talent tree layouts

The site renders talent trees two ways from the same cell markup, and reading only
the first will silently drop 62 of the 155 trees:

- `talent-grid` — the 93 CoA class trees, positioned, with `grid-row`/`grid-column`
  coordinates and explicit grid dimensions on the container's `style` attribute.
- `moa-tree-flat` — the 62 stock class trees, an unpositioned list. Cells have no
  coordinates; `row` and `column` are recorded as null rather than invented.

Cells are read from the enclosing `talent-tree` section so both are covered, and
each tree's parsed talent count is checked against the count the page's own header
declares. `verify` fails if any tree disagrees with its page.

## Creature spawn tables

NPC pages carry a `Map | Area | Coords | Spawns` table, which lands in each
record's `structure.tables`. 17,902 of the 41,817 NPC pages have one, giving
63,419 rows and 88,558 declared spawn points across 185 named areas.

Read the coordinate column by range, not by assumption. 97.9% of parsed rows are
within 0–100 on both axes and are zone-map percentages; the remaining 2.1% sit
on instance and transport maps and look like raw world units. The site mixes
both in one column without marking which is which. Nothing here converts them,
because a wrong transform would produce coordinates that look entirely
plausible. Gameobject pages carry no spawn table.

## Comparison semantics

`comparison.json.gz` compares mirror entities with captured `cachedata/union`
exports by ID and case-insensitive name only. It verifies no stat, no loot source
and no probability. Each compared entity gets one status:

- `name-match` — the ID is captured and the names agree.
- `name-conflict` — the ID is captured and the names differ.
- `candidate-missing` — the ID is not in the captured baseline. A research
  candidate, not a proven game entity.
- `unnamed-in-mirror` — the site rendered a stub such as `Item #48333`. Reported
  separately so a stub is never mistaken for a conflicting name, and never
  imported over a real captured name.

The site appends a trailing `#id` to some names for disambiguation
(`Ice Chest #188192`); that suffix is removed before comparing. A stub name is
exactly the entity word and the id, which is why `Ice Chest #188192` compares as
`Ice Chest` while `Item #48333` is reported as unnamed.

The report lists every conflicting and missing row by name and id, not just
counts, so a wrong number cannot read like a right one.

### The baseline's name column is declared, never guessed

Each captured cache is read with the name column named explicitly in `BASELINES`,
and a baseline missing its declared column fails the import. This is not
defensiveness for its own sake. Three of the four caches call it `name` — at
column 4, 1 and 3 — but `questcache` calls it **`Title`, at column 65**. A
tolerant loader that fell back to the second column compared every quest against
`Method`, whose values are `0` and `2`, and produced **10,273 confident, entirely
wrong name conflicts** where the real figure is 96. Nothing about the output
looked wrong; it was caught only by printing named example rows beside the
counts. There is a regression test.

## Interpretation limits

- Website values are source-attributed claims. Captured WDB data remains
  authoritative wherever the two disagree.
- Drop percentages are the site's own stated figures. They were not observed
  here and no probability is derived from them. Many rows state `Observed 0`.
- The crawl recorded 39,858 failed asset fetches, so the mirrored icon set is
  incomplete. Absence of an icon is not evidence the icon does not exist.
- IDs are Ascension's renumbered space. Item display IDs in particular do not
  match stock 3.3.5a; see the display-id notes before joining anything by ID.
- 12,639 item, 62,115 spell, 9,463 quest, 1,932 npc and 491 gameobject pages are
  `/history` variants rather than entities, and are streamed separately so they
  cannot inflate an entity count.
- This catalog has not been applied to a live realm.
