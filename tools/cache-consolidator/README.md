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

[Worldforged](docs/WORLDFORGED.md) covers two things:
- the `lootcollector` stage, which reads only the numbers from submitted LootCollector addon logs, since every
  record in them names people;
- the republished Tareksoh/Worldforged-data snapshot, with its importer and verifier.

[Exiles database catalog](docs/EXILES-DB.md) preserves a reviewed offline mirror of the
`db.exil.es` CoA site â€” spells, loot tables, talent trees and a change log â€” with the
same rule: website values are attributed claims, and captured WDB records stay authoritative.

## Inspect the processing pipeline

Start with [How contributions become the public dataset](docs/PROCESSING-PIPELINE.md).
It links each processing stage, explains variant retention and publication gates,
and records the installed-source comparison.

## Incremental generated outputs

Decoded views and rebuilt WDBs reuse unchanged cache categories. Stock-client Lua
reuses unchanged tables. The private `WORK/.build-cache/` directory records content
hashes of merged records, referenced source metadata, modes, WDB header donors,
selected stock views, icon lookup, generator code/configuration, and generated files.
Every reused file is hashed again. Missing or damaged output/evidence and changed
rules force regeneration; stale modes and chunks are still pruned. Summary documents
and source listings are regenerated each run.

This does not skip intake, merging, data-loss checks, privacy audits, or Git publication
checks. The first run after installation or a rule change builds a fresh baseline;
subsequent runs can reuse it. A changed item category still rebuilds that category,
and full audits can still take time. The cache is disposable, never part of the
published dataset, and does not establish contribution incorporation by itself.

For a diagnostic full regeneration, run in PowerShell:

```powershell
$env:ASCENSION_FORCE_REBUILD = '1'
python tools/update.py
Remove-Item Env:ASCENSION_FORCE_REBUILD
```

Use the ordinary publisher/GUI for publication. Do not run this diagnostic beside
an active publisher. Rebuild verification failures return a failing exit code.
