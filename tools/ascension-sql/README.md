# Quest rewards and currency SQL

[AscensionDB quest search](https://ascension-db.ascension-archive.workers.dev/#search?kind=quest)
has **Convert to SQL** immediately before **Clear column filters**.
It converts every matching source record, including later pages, and downloads
`ascension-quests-<revision>.sql.gz`. Search text, source, game mode, name and exact
ID and Zone constraints all apply. Browsing and export use the same Zone facet
and matching predicate; a zone absent from the pinned snapshot is refused.

[Full SQL report and download](https://ascension-db.ascension-archive.workers.dev/quest-sql.html).

## What the file contains

- Complete original quest fields, source file identity, mode, locale and attribution.
- Typed quest reward columns: fixed/choice items and quantities, money, max-level
  money, XP difficulty, honor, arena points, talent points, spells, titles and
  reputation. Missing or rejected values stay NULL; original values remain in JSON.
- Reward item identities from corresponding published mode/locale item views.
  Item currencies, including Rune of Ascension **375250**, retain their actual
  item IDs and quantities. No currency conversion ratio or spending behavior is invented.
- Every field conversion issue, including sources without structured reward data.
- A guarded **server-import procedure** for an AzerothCore-style MySQL world schema,
  plus a repeatable rollback procedure. Loading the export stages data; applying
  changes requires the explicit call below.

SQL strings use UTF-8 hex literals. Integers are validated exactly without JavaScript
precision loss, rounding, clamping or signed wrapping. Quantities up to 100,000 occur
in the published data. A stock SMALLINT UNSIGNED quantity column cannot hold more
than 65,535; the importer refuses that row and reports the column. It never widens
your schema automatically.

Different source records are retained separately, including overlapping mode and
locale views. Do not add their counts as unique quests or independent observations.
Matching IDs do not establish unchanged Ascension behavior or prove a source's realm.
The earlier 99.37% stock-overlap result measured NPC quest-start/end relationships,
not quest rewards or the percentage of vanilla files.

## Import into a disposable world copy

Validated dialect: **MySQL 8.4**. MySQL 8.0 and MariaDB are not certified here.
Use a disposable world copy and stop its worldserver for the import. Do not use
`mysql --force`: existing export tables or SQL errors must stop the load.

```sh
gzip -dc ascension-quests-REVISION.sql.gz | mysql --defaults-extra-file=/private/mysql.cnf disposable_world
```

The export does not create/select a database and contains no credentials. Loading
creates `ascension_quest_*` staging tables and `ascension_reward_import_journal`.
For research alone, it can instead be loaded into a new empty database; the server
procedure refuses application unless compatible world tables exist.

In a separate SQL session, list the exact source views and dry-run ONE mode-specific
view. This resolves overlapping records explicitly instead of picking a winning
source or applying the same quest several times:

```sql
SELECT DISTINCT source_path, game_mode FROM ascension_quest_export;

CALL ascension_apply_quest_rewards(
  'cachedata/by-mode/conquest-of-azeroth/questcache.tsv.gz',
  'conquest-of-azeroth', FALSE);
```

The result lists every selected source record with `ready`, `unchanged`,
`already-applied`, or a specific rejection reason. After reviewing it, change FALSE
to TRUE to apply **only ready rows** transactionally. Call it outside an existing
transaction. Repeating TRUE leaves already-applied rows unchanged.

Executable corrections cover fixed/choice reward items and quantities, money,
max-level money, XP difficulty, honor, arena points and talent points. The importer
requires an existing quest with the same exact title, existing reward items with
matching source names, valid item/quantity pairs, unambiguous source identity and
compatible integer widths. Unknown mode, unsupported source, missing templates,
invalid fields and conflicting item identities are not guessed or default-filled.

Spell, title, reputation and honor-multiplier values are converted and preserved,
but differences from the target require separate core/DBC review and reject that
quest from automatic application. A nonzero reputation mask likewise needs a
core-specific adapter. Custom currencies implemented outside captured item/money
reward fields cannot be reconstructed from an absent field. A matching item name
does not implement a currency vendor, account balance, script or spell handler.

Before/after reward values are journalled. To restore this export's applied rewards:

```sql
CALL ascension_rollback_quest_rewards();
```

Rollback refuses changed/missing reward rows or changed quest identities without a
partial restore. It affects world reward definitions, **not already earned rewards
or character progress**. It does not create missing quest/item templates, import
NPC relationships, change quest requirements or perform realm restarts.

## Generate the same SQL from the command line

Requires Node 22+ and curl, without npm packages, tokens or paid services. Use an
immutable catalog snapshot URL from the full report, a NEW output directory and
optionally a reusable download cache:

```sh
node tools/ascension-sql/convert.cjs \
  --catalog https://ascension-public-data.ascension-archive.workers.dev/catalog/snapshots/HASH/ \
  --filters 'kind=quest&mode=conquest-of-azeroth' \
  --out QuestSQL --cache QuestSQL-cache
```

Outputs: `quests.sql.gz`, `report.json`, and `manifest.json` with output hashes and
converter implementation hashes. Failed conversions retain an explicitly named
`.partial` file; they never rename it as a complete export. The browser exposes no
partial download. Cancel the browser export by clicking **Cancel SQL export**;
changing the search also cancels it. The browser limits compressed output to 256 MiB
and uncompressed SQL to 2 GiB and asks for narrower filters if exceeded.

Implementation is shared with the website in `../ascension-db/web/quest-sql.js`,
`quest-server-sql.js`, `quest-export.js`, and `search-query.js`. There are no separate
browser/CLI reward mappings or filter implementations.

The compact `quest-item-evidence.json.gz` companion is generated solely from public
manifest-hashed quest/item views. Rebuild it using `build_item_evidence.py`; every
source byte is checked, and the asset retains its public snapshot, source paths
and hashes. It contains only reward-referenced item identities, not private donors,
client binaries, realm backups or character/account data.

## Validation

```sh
node --test tools/ascension-sql/test_quest_sql.cjs tools/ascension-db/test_*.cjs
docker run -d --name quest-sql-test --label assignment=quest-sql-publication \
  --network none -e MYSQL_ALLOW_EMPTY_PASSWORD=yes mysql:8.4.9
# Wait for mysqladmin ping before the test:
python3 tools/ascension-sql/test_mysql.py --container quest-sql-test
```

The integration helper refuses containers without the explicit label and network
boundary and creates a unique fixture database. It exercises large currency values,
schema widths, missing items, title/name disagreement, malformed data, duplicate
sources, dry run, application, repeat application, edit refusal and guarded rollback.
These database checks do not establish gameplay or custom currency spending behavior.

Field semantics reference: [AzerothCore quest_template](https://www.azerothcore.org/wiki/quest_template).
