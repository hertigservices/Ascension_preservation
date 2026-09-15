# Source attribution audit — September 15, 2026

Audited published data revision `ac5fe27a0d4c3d8c3c8cda69abd2ed088d18a471`. All 560 release-backed cache files matched their manifest SHA-256; all 22,380 supplemental files were tracked and unchanged. The census covered 22,940 source files, including 246 indexed files / 8,629,595 source records and 22,694 references. Counts include repeated published views, not unique game entities.

## Findings and corrections

The generic index read `_modes` and `modes`, overlooking singular `mode`, explicit realm-mode labels, and known mode keys. Source-specific adapters now expose that existing evidence without changing original record fields or merging captures.

| Source | Records gaining a previously unspecified mode |
| --- | ---: |
| `cachedata/lootcollector/items.tsv` | 2,540 |
| `cachedata/lootcollector/sources.tsv` | 20 |
| `cachedata/lua/Auctionator.observations.json` | 1 |
| `cachedata/lua/harvest/advancement.tsv` | 20,500 |
| `cachedata/lua/harvest/byRealm.json` | 2 |
| `cachedata/lua/harvest/gossips.tsv` | 15 |
| `cachedata/lua/harvest/vendors.tsv` | 3,791 |
| `cachedata/sources.tsv` | 3,431 |
| **Total** | **30,300** |

43,213 mode labels change in total: this also includes preserving explicit unknowns as `unknown`, normalizing known labels and splitting multi-mode LootCollector values for filtering. Added record metadata distinguishes realm, game mode, evidence field/basis, unknowns and conflicts. The validator recomputes attribution from the untouched payload. Only affected parser caches are invalidated; legacy reuse cannot restore their old labels.

10,253 advancement records, 360 cache-source rows, two LootCollector source records and one unattributed realm branch remain unknown. No new fingerprint inference, mode-to-realm lookup, or attribution copied from neighboring files is performed. Previously inferred cache-source modes retain their original `inferred:*` basis; the 3,431 source-row fixes expose existing labels rather than establishing new facts. Supplemental adapters remain unchanged; the census found no unhandled top-level realm/mode fields in indexed records. Nested source structures and reference-only files can still contain evidence requiring future format-specific readers.

## Upload history

The desktop summary now shows bytes/KiB for small uploads, separates game modes from realms, and reads only manifest-hash-verified nonempty Auctionator branches. Empty realm tables supply no observations. Unknown WDB files remain unknown even when a neighboring Auctionator file identifies a realm. Ten retained published submissions supply additional realm evidence; five lose an otherwise misleading unknown marker. The 391-byte example becomes `free-pick`, `Area 52`. Original server metadata and payloads remain intact.

## Reproduction

Run `python tools/ascension-db/audit_attribution.py --cachedata <verified-cachedata> --supplemental <published-supplemental> --out <private-report.json>`. The report contains per-file hashes, before/after counts and evidence bases. It does not read credentials or mutate inputs.

## Verification

40 catalog Python tests, 12 contribution/GUI tests, and the browser rules passed locally. The full catalog build and deployment receipts are recorded by the accompanying workstation continuation report. No R2 Data Catalog/Iceberg feature was enabled.
