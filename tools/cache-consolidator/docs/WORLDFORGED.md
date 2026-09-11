# Worldforged: where items are picked up, and how they upgrade

Worldforged items are Ascension-custom gear, picked up from world props such as sacks, stuck weapons, corpses
and disturbed dirt. Each base item climbs an upgrade ladder, and every rung is its own item id. This component
holds two views of that system:

| | Where | Built by |
|---|---|---|
| Pickup pins derived from the LootCollector addon logs that people submitted | `cachedata/lootcollector/` | the `lootcollector` pipeline stage, `tools/ingest_lootcollector.py` |
| Tareksoh's [Worldforged-data](https://github.com/Tareksoh/Worldforged-data), republished with permission | `supplemental/worldforged/<commit>/` in the data repository | `tools/import_worldforged.py` |

## The `lootcollector` stage

LootCollector records, per realm, every Worldforged item and Mystic Scroll a player looted, plus those synced
to them by other players. Each record holds the item id, the zone (`z`, the WorldMapArea id + 1) and the
looter's 0..1 position on that zone's map. Submitters' WTF folders carry it as
`SavedVariables/LootCollector.lua` and `.lua.bak`.

Until 2026-09-11 nothing read these files. Intake ledgers `.wdb` only, and `luamerge.py` knows five addon
files by exact name, so 34 copies (22 distinct) sat in `extracted/` contributing nothing.

**Privacy is the design constraint.** Every record also names three kinds of player: the one who found it
(`fp`), the one who first shared it (`o`) and every voter (`fp_votes`). A submission's path carries its account
login. So the stage:
- reads only `dt, i, z, iz, c, q, t0, ls, mc`, the status word `s`, `xy`, and the realm key each record sits under;
- identifies a file by SHA-256 and never writes a path;
- refuses to write anything shaped like an address, a drive path or an account tree (`check_outputs`).

The raw `.lua` files remain QUARANTINED by `scrub.py`.

It writes four files to `cachedata/lootcollector/`:
- `pins.tsv`: one row per place an item was looted, with type, item, zone, map position, server X/Y, realms,
  modes, and first and last seen;
- `items.tsv`: one row per item;
- `sources.tsv`: one row per distinct file, by hash, with whether it could be read;
- a `README.md` generated from the run.

One pin is kept per (type, item, zone) within 0.005 map units. Each discovery contributes its most recently
confirmed copy, because the addon moves a stored position as players confirm it.

```
python -B tools/ingest_lootcollector.py
python -B tools/test_lootcollector.py
```

Parsing a file takes seconds, so parses are cached under `config.WORK/lootcollector-cache/`, keyed by file hash.

### Map position to server coordinates

`X = top - y*(top - bottom)` and `Y = left - x*(left - right)`, using the row `WorldMapArea[z - 1]`. These must
be the bounds the client projected with: Ascension's own table, from `patch-M.MPQ`. That table has 310 rows
(md5 `385136e35c9b279ff195a3414d59b9f8`). The copy in a server's `dbc` folder here had only 285, and stock has
108. The shipped `tools/worldmaparea_bounds.tsv` is generated from the client's two DBCs:

```
mpqfind <client>/Data --from patch-M.MPQ "DBFilesClient\WorldMapArea.dbc" WorldMapArea.dbc
mpqfind <client>/Data --from patch-M.MPQ "DBFilesClient\AreaTable.dbc" AreaTable.dbc
python -B tools/make_worldmaparea_bounds.py WorldMapArea.dbc AreaTable.dbc
```

Checked against a pin Tareksoh's set measured independently: item 132418 in Razorfen Kraul at map
(0.1273, 0.3504) is server (2178.0, 1965.2). On the 5,136 pins both sets hold, server coordinates agree to a
median of 0.00 yd.

## The republished snapshot

`import_worldforged.py` reads the pinned commit's blobs from git itself, never from a working tree. It
republishes only the 11 datasets under `data/`, and names every other file as omitted, with the reason: the
author's tools and prose embed local paths. It checks every item id against the captured itemcache
(`comparison.json`), and refuses address-, GUID- or path-shaped content.

`verify` recomputes each file's SHA-256 and git blob id, so the published bytes provably are the upstream
commit's.

```
python -B tools/import_worldforged.py import <checkout> --commit <sha> --cache <union itemcache.tsv.gz> \
    --baseline-revision <data repo commit> --output <data repo>/supplemental/worldforged
python -B tools/import_worldforged.py verify <data repo>/supplemental/worldforged/<sha>
python -B tools/test_worldforged.py
```

## Reading either view: what the data can and cannot say

- **A pin is where the looter stood when the loot window opened, not the spawn.** It is typically a few yards
  away, with a long tail; a couple of items sit hundreds of yards off. No source records a height.
- **Records are crowd-synced.** The same discovery appears identically on four realms, so `files` counts files,
  not witnesses.
- **The two upgrade ladders agree on ids and disagree on rung names.** LootCollector's chain for item 415065 runs
  base 45 → Dungeon 60 → ZG 64 → Tier 1 70 → Tier 2 77 → AQ 81 → Tier 3 88. The Exiles ladder calls the same ids
  Base, Base, Lvl 60, ZG, BWL, AQ40 and Naxx, and adds an ilvl-73 "MC" rung. Of the 1,823 MC ids, only 91 appear
  in any LootCollector chain. Key an upgrade table on ids and item level, never on labels.
- 984 LootCollector chain ids are stock raid gear (e.g. `10904 Faceguard of Wrath`), not Worldforged. 166 exist
  nowhere in the captured data.
- **Nothing here says which object an item drops from, how often, or how the upgrade was performed.** For 668
  items a gameobject in the captured cache carries the item's own name. That binding comes from names, and
  complements the [GameObject dumps](GAMEOBJECT-DUMPS.md); everything else is proximity, and nearest is not source.
