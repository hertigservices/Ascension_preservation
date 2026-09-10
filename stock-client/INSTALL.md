# Installing the stock-client port (Conquest of Azeroth mode)

Two halves: a **client** anyone can build from a stock 3.3.5a client plus Ascension's data
archives, and a **server** (AzerothCore + `mod-ascension-ca`) that serves the Character
Advancement data. Everything below is what `C:\AzerothRealm\client-coa` / the `coa2` realm
were built with on 2026-09-10; the phase table in `README.md` says what is verified.

Requirements: Python 3.11+, a stock 3.3.5a (12340) client (ChromieCraft's is known good),
Ascension's `Data\patch-*.MPQ` archives (from an Ascension install), and for the server a
Windows or Linux AzerothCore build environment.

## 1. Data and addon pack (once per dataset)

```
cd stock-client
python -B tools/gen_ca_data.py --harvest <advancement.tsv> --dbc-export <ca-dbc-export> --essence-dbc <CharacterAdvancementEssence.dbc> --specs-dbc <ChrSpecs.dbc> --chrclasses-dbc <Ascension ChrClasses.dbc> --out data
python -B tools/gen_enchant_data.py --harvest-zip <ascension-harvest-export.zip> --out data/enchants
python -B tools/build_addon_pack.py --dataset unattributed-7f0c805382 --class-byte 28 --level 80 --catalogue data/build-catalogue --enchants data/enchants --out build/p2-core-preview
python tools/assemble_panel_pack.py --core build/p2-core-preview --ui-tree <ui-tree>/Interface --out build/p2-panel
python tools/gen_glue_data.py --dataset data/datasets/unattributed-7f0c805382 --chrclasses <Ascension ChrClasses.dbc> --atlas <ui-tree>/Interface/SharedXML/AtlasInfo.lua --coa-realm "Conquest of Azeroth" --out client/Interface/GlueXML/AscensionCreateData.lua
```

Always run `build_addon_pack.py` before `assemble_panel_pack.py`: the assembler copies the
core pack from `build/p2-core-preview`, so an assemble on its own ships the previous core.
`--coa-realm` (repeatable) names every realm whose character-creation screen shows the CoA
class chooser; on any other realm (a Free-Pick / Hero realm) the chooser stays hidden and
creation is stock.

```
```

`<ui-tree>` is Ascension's extracted Interface tree (their SharedXML/FrameXML/AddOns from
`patch-B.MPQ`; see `data/MANIFEST.md` in the repository root for where the recovered
sources live). `assemble_panel_pack.py` copies the Ascension files it needs verbatim,
renames the 34 template names the stock FrameXML already owns to `ASC_*`, and prints any
file it could not find.

## 2. Client

```
python tools/install_client.py --stock <stock client dir> --ascension-data <dir with patch-*.MPQ> --out <new client dir> --realmlist <host:port> --realm-name "<realm on the list>" --stock-ui-tree <stock-ui-tree>/Interface
```

Hardlinks (or `--copy`) the archives, maps Ascension's multi-letter archives onto free
single-character slots (`ARCHIVE-MAP.txt`), writes `patch-Y.MPQ` from
`server/dbc-overrides`, patches `Wow.exe` (`client/patch_wow.py`; refuses anything but the
recorded stock binary), installs the addon pack and the character-creation class chooser
(`client/Interface/GlueXML`). Run it from a native shell (PowerShell / cmd): under an MSYS
bash the overlay copy has been seen to land nowhere. Re-running only refreshes what changed.

Window size: the Collections panel is 1294 UI units wide and Ascension scales the UI to
0.9, so the default `Config.wtf` asks for 1600x900 (with `hwDetect 0`, or the first launch
replaces it with its own 1024x768 pick); smaller windows clip the panel. `--realm-name`
writes `SET realmName`: with two realms on the list (CoA and Free-Pick) a client without a
remembered realm stops at the Realm Selection dialog after login, which is where a clean
rehearsal of these steps first stopped (2026-09-10).

## 3. Server

1. Build AzerothCore with `server/mod-ascension-ca` in `modules/` (any recent master;
   the module uses only PlayerScript/WorldScript hooks and the database API).
2. DBC set for the worldserver's `DataDir`: Ascension's DBC files (from `patch-M/S/T`) run
   through `server/tools/fix-dbc.py <dbc-dir>` (repairs malformed string/index columns)
   and `server/tools/sanitize-for-core.py <dbc-dir>` (zeroes the effect/aura/criteria
   values the core has no enum for; every change is journaled to `sanitized-for-core.json`
   -- 10,359 of 209,509 spells lose a slot, 430 CA entries are affected). Then open the
   CoA skill lines to carrier classes:
   `python tools/patch_skill_race_class.py <dbc-dir>/SkillRaceClassInfo.dbc <dbc-dir>/SkillRaceClassInfo.dbc --skillline <dbc-dir>/SkillLine.dbc --open-categories 6,7,8`
   (the same patched file is in `server/dbc-overrides`). `--open-categories 6,7,8` also opens
   every weapon, class and armour skill line to all races and classes, which a Free-Pick
   (Hero) character needs because it learns abilities from all ten stock classes; leave it
   off for a CoA-only server.
3. Characters database: widen `character_achievement.achievement` and
   `character_achievement_progress.criteria` to `int unsigned` (Ascension achievement ids
   reach 322,523), then apply `server/sql/characters/ascension_ca_characters.sql`.
4. World database: apply `server/sql/world/ascension_ca_world.sql` (generated by
   `python tools/gen_server_sql.py --dataset data/datasets/<key> --out server/sql --chrclasses <Ascension ChrClasses.dbc> --builds <builds.json>`;
   drop-and-recreate, safe to re-run). Item/creature/quest content comes from the
   consolidator's `import_world.py` (see the repository root `docs/`).
5. `worldserver.conf`: `DataDir` = the DBC set above; the module's keys
   (`AscensionCA.Enable = 1`, `AscensionCA.Mode = "coa"`, `AscensionCA.DefaultClassByte = 28`)
   go in the realm's own `worldserver.conf`, **never** in `configs/modules/mod_ascension_ca.conf`:
   AzerothCore loads the module directory after the main file and a key found there
   overwrites the main file's value, so one module conf would force every realm that
   shares the binaries into the same mode. The shipped `conf/mod_ascension_ca.conf.dist`
   is documentation only and defines no `AscensionCA.*` key on purpose.
6. Start the worldserver first and wait for `mod-ascension-ca: 10255 entries, 32 classes,
   101 specializations` in the log before the authserver; a realmlist row whose `flag`
   is 3 makes the authserver refuse to start.

### 3b. A second realm in Free-Pick (Hero) mode from the same binaries

One server directory can back a CoA realm and a Hero realm at once; each is one
worldserver process with its own config and characters database:

1. Copy `worldserver.conf` to `worldserver-hero.conf` and change: `RealmID` (a second
   `realmlist` row, e.g. `Ascension`), `WorldServerPort` (e.g. 8090), `SOAP.Port`,
   `LogsDir` (its own directory; readiness is judged from that file),
   `CharacterDatabaseInfo` (its own database, created empty and populated by the
   worldserver's auto-setup, then `server/sql/characters/*.sql` and the files under
   `server/sql/characters/updates/`), `AscensionCA.Mode = "freepick"` and
   `AscensionCA.DefaultClassByte = 10`.
2. Start it with `worldserver.exe -c configs/worldserver-hero.conf`. Both realms share the
   authserver, the auth database and the world database; the client picks the realm on its
   realm list (`SET realmName "<name>"` in `WTF/Config.wtf` remembers the choice).
3. The Hero realm needs the `--open-categories 6,7,8` skill-line patch from step 2 in both
   the server DBC set and the client's `patch-Y.MPQ`; without it a Hero cannot learn the
   weapon skills its cross-class abilities sit on.

Hero characters are created as a stock class (the chooser is hidden on realms not named
by `--coa-realm`); the module forces class byte 10 and the Hero rules
(`server/mod-ascension-ca/README.md`, "Rules (Free-Pick / Hero model)").

## 4. What to expect in game

- Character creation shows the 21 CoA classes; the class name, icon and specialization
  list are Ascension's, the descriptive bullets under it are still the carrier class's.
- First login as a new character: the module logs `CoA class <byte> (<name>) chosen`; the
  Collections panel opens on that class's specialization chooser (`/ca` reopens it).
- Activate a specialization, click nodes, Save Changes: spells land in the spellbook under
  the class skill-line tab and on the action bar; right-click + Save unlearns.
- `/ca inspect <name>` prints an online character's class, specialization and known set.
- On a Free-Pick (Hero) realm: the stock character-select screen has no create key, so
  `INSERT` or `F5` opens creation. `/ca` opens Ascension's classic Character Advancement
  panel (class list with General, Abilities / Talents / Mastery tabs, essence header);
  abilities cost Ability Essence, talents Talent Essence, masteries and traits are granted
  automatically once the class points and level are met. `/ca enchants` opens the Mystic
  Enchant collection over the 3,822 recovered enchants (the Collection tab defaults to the
  Known filter, so a fresh character sees an empty page until the filter is changed; no
  costs, slots or altars were recovered, so nothing can be applied). `/ca architect` lists
  the 357 recovered Dawnrise community builds; the build editor works locally and cannot
  publish.
- Errors: the shim writes Lua errors to the server log as `ASC LOG [<name>] ...`; XML
  template/font problems only appear in the client's `Logs\FrameXML.log`.
