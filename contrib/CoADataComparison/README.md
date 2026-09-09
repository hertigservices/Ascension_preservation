# contrib/CoADataComparison — read-only CA / community-data comparison

An optional research instrument, **not part of the runnable server stack**. It compares the engine Character-Advancement export with a locally held CoA Build Hub archive without importing client content or replacing runtime evidence.

## Files

| File | Purpose |
|---|---|
| `compare_ca.py` | Standard-library command-line comparator |
| `test_compare_ca.py` | Synthetic fixtures and automated tests; no client install required |
| `CONTRIBUTION-PROPOSAL.md` | Scope, coverage findings, source attribution and review limits |
| `data/comparison-ids.json` | Identifier lists from one verified comparison (no description text) |
| `data/README.md` | How to read those lists |

## Inputs

Supply your own data. Nothing is downloaded.

- `--ca-root`: directory containing `entries.json` and `edges.json`, in the formats used by [`data/ca-export`](../../data/ca-export/SCHEMA.md).
- `--hub-root`: an archived Build Hub export containing `classes.json`, `spells/master_index.json`, and the per-class `skills/<slug>.json` and `talents/<slug>.json` files. The archive uses a class list with `slug`, skill lists with `count`/`skills`, and talent trees with node IDs and dependency references. This is not the loose client `CharacterAdvancementData.json` schema.
- `--upstream-revision`: full 40-character Git revision identifying the intended baseline. This value is a **caller assertion**, not verification that the export came from that revision. Per-file SHA-256 values identify the actual bytes read.
- `--output`: a new JSON report outside both input roots. Existing outputs must not be overwritten.

From the repository root, with Python 3.11 or later:

```sh
python contrib/CoADataComparison/compare_ca.py --ca-root data/ca-export --hub-root /path/to/private/hub-archive --upstream-revision 664f2768f0695187349fc38842eae6a1588cc352 --output /path/to/private/reports/ca-comparison.json
```

Use quoted local paths where needed. Keep generated real-data reports outside the repository. Do not put private inputs under this contribution directory.

## Output and interpretation

The report contains numeric identifier comparisons, availability counts, source fingerprints and explicitly labeled dependency hypotheses. It does **not** export description text, HTML tooltips, icons, names or absolute local source paths. It does not rewrite either dataset.

- Rank overlap means present in one of the five CA spell-ID fields, not present in every client table.
- Description enrichment means the CA row lacks both descriptions and a matched community primary-spell record has text. It does not validate the text's accuracy, version or redistribution rights.
- Dependency mapping requires a unique primary-spell match. Ambiguous and dangling references remain visible instead of being silently assigned.
- Community talent node IDs are four unsigned integers separated by hyphens, optionally followed by one lowercase letter for stacked ranks (for example `12-87-1-0` or `12-87-0-5a`). Other identifier shapes are rejected.
- A community edge absent from the runtime graph is a **candidate discrepancy**, not an authoritative addition. Cross-class/tab discrepancies must remain separate.
- Source revision and hashes do not establish semantic equivalence across client builds.

Invalid input must produce a nonzero exit and no successful report. This tool is intentionally stricter than a best-effort importer: fix malformed or duplicate identifiers at their source, rather than allowing silent loss through dictionary overwrites.

## Tests

```sh
python -m unittest discover -s contrib/CoADataComparison -p 'test_*.py' -v
```

Tests construct synthetic records in temporary directories. They do not use SavedVariables, credentials, a game client, a server, a database, or network access. No third-party Python dependency is required.

## Privacy, provenance and license

Follow the repository's [`NOTICE.md`](../../NOTICE.md), [`LICENSE`](../../LICENSE), and [`data/MANIFEST.md`](../../data/MANIFEST.md). Original code and tests are contributed under the repository's MIT terms; that does not license third-party client/community content.

Community source: https://coabuildhub.com. The archive used for the initial comparison records https://db.ascension.gg as its skills source and 2026-07-31 as its scrape date. Those are archived provenance claims, not a current crawl. The tool does not assert that every user-supplied archive has that origin/date.

Do not attach raw client files, community descriptions, guides/comments, or private source archives to a public issue or PR. Review generated reports before sharing; removing content text is a risk reduction, not blanket legal clearance.
