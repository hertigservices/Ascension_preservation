# Prompt for an AI agent: import the Ascension data into an AzerothCore realm

Copy everything below the line into any coding agent (Claude, Grok, ChatGPT, Codex â€¦)
that has shell access to the server. Fill in the `<placeholders>`. The prompt references
only the two public repositories, so it works on anyone's realm.

---

You are working on an AzerothCore 3.3.5a (build 12340) realm. Goal: import the recovered
Ascension game data (items, creatures, quests, gameobjects, npc/page text) published in
`hertigservices/ascension-data` into this realm's world database, using the tools in
`hertigservices/Ascension_preservation`, and install the matching client caches so the game
shows the imported items. Work in this order and stop to show me the preview before writing
anything.

## Constraints

- Never put a database password on a command line. Use `--ask-password`, or a MySQL option
  file with a `[client]` section passed via `--defaults-file`, and keep that file out of any repo.
- Do not restart the worldserver without telling me first. `item_template` is only read at
  startup, so one restart is required after the import.
- Write scripts to a file and run the file. Do not pipe scripts into an interpreter.
- Report named rows, not counts (e.g. "item 134892 Worldforged Scroll: Edge of Fury now
  resolves"), because a wrong count looks exactly like a right one.

## Step 1 â€“ Get the tools and the dataset

```bash
git clone https://github.com/hertigservices/Ascension_preservation
git clone https://github.com/hertigservices/ascension-data
python Ascension_preservation/tools/data-storage/dataset.py download ascension-data/datasets/cache.json --out AscensionData
cd Ascension_preservation/tools/cache-consolidator
python -B tools/import_world.py --data ../../../AscensionData/cachedata --list-sources
```

The tools live in `Ascension_preservation/tools/cache-consolidator`; the data lives in
`AscensionData/cachedata`. Pass `--data <path to AscensionData>/cachedata` on every
command below, or set the `ASCENSION_CACHE_DATA` environment variable to that path once.
Read `README.md` and `docs/USING-THE-DATA.md` in the tools folder before continuing.
Python 3.12+ and a `mysql` client are required. If `mysql` / `mysqldump` are not on PATH,
pass `--mysql` and `--mysqldump` with full paths.

## Step 2 â€“ Pick the source

The default `union` holds every record ever seen across all Ascension modes (the most recently
seen version of each id wins). If the realm is meant to mirror ONE mode, pass
`--source <mode>` (for example `--source conquest-of-azeroth`). Ask me which one if unclear.

## Step 3 â€“ Preview (no writes)

```bash
python -B tools/import_world.py --data <cachedata> --db <world_db> --user <user> --ask-password [--source <mode>]
```

It stages into `_cachemerge_*` tables, runs an ADD pass (rows the DB lacks, guarded by
`WHERE NOT EXISTS`) and a FILL pass (only columns still at their schema default), and writes
`import-out/report.md`. Show me:

1. rows to add per table,
2. cells to fill per table,
3. the **conflicts** section â€“ cells where both sides hold real, different values. Those are
   NEVER overwritten,
4. the refused-values list â€“ values that did not fit a column's range.

If I want stock content left byte-for-byte untouched, add `--add-only`.

## Step 4 â€“ Apply

Only after I approve:

```bash
python -B tools/import_world.py --data <cachedata> --db <world_db> --user <user> --ask-password [--source <mode>] --apply
```

It takes a `mysqldump` backup of the touched tables into `import-out/` first. Keep it. The
run is idempotent; re-running is a no-op.

## Step 5 â€“ Cache-version handshake (important)

Note the value of `version.cache_id` in the world DB (a fresh AzerothCore is 16). The client
deletes its cache files at login unless their 24-byte header carries this exact number. Either
pass `--set-cache-version N` to `import_world.py`, or use the same `N` in step 7.

## Step 6 â€“ Restart and verify server side

Ask me, then restart the worldserver. Ignore the startup line `>> Loaded NNNN Item Templates`:
it counts only rows that match `Item.dbc`, while every row is in fact loaded. Verify from the
worldserver console or SOAP instead:

```text
.lookup item Worldforged Scroll
.lookup item 134892
.lookup quest 8200519
```

Each should resolve. Then mail one imported item to a test character and confirm it arrives:

```text
.send items <char> "test" "test" 134892
```

## Step 7 â€“ Client caches

On the player's machine, with the game closed:

```bash
python -B tools/install.py --client "<WoW folder>" --data <cachedata> --list-modes
python -B tools/install.py --client "<WoW folder>" --data <cachedata> --target root --mode <mode> --cache-version <N>
python -B tools/install.py --client "<WoW folder>" --data <cachedata> --target root --mode <mode> --cache-version <N> --write
```

- Stock 3.3.5a clients use `--target root` (flat `Cache\WDB\enUS\`).
- Ascension clients use `--target realm`, which writes per-realm folders.
- Replaced files are backed up to `Cache\WDB-backup-<stamp>`; `--undo` restores them.
- `<N>` must equal the server's `cache_id` from step 5.
- If the player has no checkout of the dataset, `--fetch` downloads it from GitHub instead
  of `--data`.

Verify in game:

```text
/run print(GetItemInfo(134892))
```

It must print the item name, quality 5 and item level 60 without the item ever having been
seen. If it prints `nil`, the cache-version handshake failed: recheck `<N>`.

## Known caveats on a stock client

Document these for players; do not try to fix them here.

- Ascension renumbered `ItemDisplayInfo.dbc`, so many imported items draw the wrong model or
  icon unless the Ascension art patches are installed.
- Custom spells referenced by items have no `Spell.dbc` row on a stock client; their tooltip
  lines are blank.
- Custom zones referenced by quests and creatures do not exist in stock map data.

## Deliverables

1. The preview report and the conflicts you saw, before apply.
2. The named `.lookup` results after the restart.
3. The exact `install.py` command used and the `GetItemInfo` output.
4. Where the backups are (`import-out/*.sql` and `Cache\WDB-backup-*`).

---

*Prerequisites for the server owner:* the realm must be built from a recent AzerothCore, since
the importer targets the current `item_template` and side-table schema.
