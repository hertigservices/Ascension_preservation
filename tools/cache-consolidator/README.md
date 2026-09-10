# Ascension cache tools

Canonical source for cache intake, decoding, lossless variant consolidation,
client-cache installation, and world-database import. Each command remains usable
independently of the server and character importer.

The dataset is maintained separately in
[hertigservices/ascension-data](https://github.com/hertigservices/ascension-data).
Supply its cachedata directory using `ASCENSION_CACHE_DATA` or `--data`.

```powershell
python tools/install.py --help
python tools/import_world.py --help
python tools/publish.py --help
```

Mutable intake, decoded records, dedup ledger and generated output live outside
this source tree. Set `ASCENSION_CACHE_WORK`, `ASCENSION_CACHE_OUT`, and
`CONSOLIDATOR_REPO` for an existing installation. A private `runtime.local.json`
beside `tools/config.py` can instead set `work`, `out`, and `publish_repo`.

The data repository's `.ascension-data.json` marker disables historical tool
copying. Publishing there audits and commits datasets, never this source tree.

See [using the data](docs/USING-THE-DATA.md), [format](docs/WDB-FORMAT.md), and
[contributing](CONTRIBUTING.md). The original README is retained as historical
context in docs/LEGACY-README.md. Original author history is retained through a
Git subtree split of the old tools directory.

[BisBeard supplemental catalog](docs/BISBEARD.md) has its own lossless importer and verifier; it does not replace captured WDB records.

[Exiles database catalog](docs/EXILES-DB.md) preserves a reviewed offline mirror of the
`db.exil.es` CoA site — spells, loot tables, talent trees and a change log — with the
same rule: website values are attributed claims, and captured WDB records stay authoritative.
