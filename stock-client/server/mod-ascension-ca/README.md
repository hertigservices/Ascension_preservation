# mod-ascension-ca

AzerothCore module that serves Ascension's Character Advancement (Conquest of Azeroth
model first) to the stock-client addon pack. Drop the directory into `modules/`, rebuild,
apply `../sql/world/*.sql` to the world database and `../sql/characters/*.sql` to the
characters database (both are idempotent), copy `conf/mod_ascension_ca.conf.dist` next to
your `worldserver.conf`.

## Transport

The stock 3.3.5a client has no custom opcodes, so the protocol rides on addon chat:

| direction | packet | text |
|---|---|---|
| client → server | `SendAddonMessage("ASC", ..., "WHISPER", me)` | `CMD\t<verb>[\t<body>]` |
| server → client | `CHAT_MSG_WHISPER` + `LANG_ADDON`, sender = target = the player | `ASC\t<verb>[\t<body>]` |

`OnPlayerBeforeSendChatMessage` consumes the client's lines and `OnPlayerCanUseChat`
stops the core from relaying them (a whisper to yourself would echo straight back).
Bodies longer than 200 characters travel as `<verb>\t#\t<seq>\t<count>\t<chunk>` in both
directions and are reassembled per verb.

| verb (client → server) | reply |
|---|---|
| `HELLO` | `HELLO\t<protocol>\t<mode>\t<enabled>` (also sent at login; protocol is 3) |
| `PING` | `PONG` |
| `LOG\t<text>` | nothing; the text lands in the server log as `ASC LOG [name] ...` |
| `STATE` | `STATE\t<classByte>\t<specId>\t<ae>\t<te>\t<level>\t<entry:rank,...>` |
| `APPLY\t<entry:rank,...>` | `RESULT\tAPPLY\tOK` or `RESULT\tAPPLY\tERR\t<reason>\t<entry>`, then `STATE` |
| `SPEC\t<id>` | `RESULT\tSPEC\t...`, then `STATE` |
| `RESET\ttalents` / `RESET\tall` | `RESULT\tRESET\tOK`, then `STATE` |
| `CLASS\t<classByte>[\t<archetypeUUID>]` | `RESULT\tCLASS\tOK` or `ERR` (`unknown-class`, `already-chosen`, `carrier-mismatch`, `bad-archetype`), then `STATE`. Accepted once, while the character has no CA rows; the stock class the character was created with must carry the same power bar (`ascension_ca_class.power_type`). With an archetype, its build entries are learned as far as level and budget allow, and the rest on every level-up. |

| `INSPECT\t<name>` | `INSPECT\t<name>\t<classByte>\t<specId>\t<level>\t<entry:rank,...>` for an online character, or `RESULT\tINSPECT\tERR\tnot-found` |

`STATE` is `STATE\t<classByte>\t<specId>\t<ae>\t<te>\t<level>\t<chosen>\t<entry:rank,...>`;
`chosen` = 0 until `CLASS` has been accepted (the addon pack then consults the glue mailbox).

`APPLY` carries the **complete** wanted set, never a delta; the server validates the whole
set and replaces the character's known set (the same contract Ascension's own 0x727
upload had).

## Rules (CoA model)

- Budgets come from `ascension_ca_essence(family = CoA class byte, level)`: AE is the
  "Class" tab's points, TE the active specialization tab's points. One point per rank.
- An entry must belong to the character's CoA class (`ascension_ca_class.ca_class_type`)
  and, unless it is a Class-tab entry, to the active specialization (the entry's tab name
  equals the spec token, case-insensitively).
- `RequiredIDs` (links of kind `req`) must be in the set; `RequiredLevel` and the
  `Required*Investment` / `RequiredClassPoints` columns are checked against the points
  the rest of the set spends in the same tab / class.
- Rank r grants the entry's rank spells 1..r (`ascension_ca_entry_spell`); dropping to a
  lower rank removes the higher ones. Activating a specialization learns its
  `passive_spell` and unlearns the previous specialization's tree.
- Reason strings: `unknown-entry`, `other-class`, `max-rank`, `other-specialization`,
  `level`, `prerequisite`, `investment`, `no-class-points`, `no-spec-points`, `invalid-spec`.

## Rules (Free-Pick / Hero model, `AscensionCA.Mode = "freepick"`)

- Every character is Hero class byte 10 (`LoadPlayer` forces it; `SPEC` and `CLASS` answer
  `ERR`); the pool is every entry whose class type is one of the ten stock classes
  (`ClassTypeID` 1..12), so a Hero picks across classes.
- Budgets come from `ascension_ca_essence(family = 10, level)`: abilities cost their
  `AECost` from Ability Essence, talents (`Talent` flag or a `TE` cost) cost `TECost` per rank
  from Talent Essence. Level 1: 8 AE / 0 TE; level 10: 28 / 1; level 80: 140 / 71.
- `RequiredIDs` and `ConnectedNodes` parents must be in the set; `RequiredLevel`,
  `RequiredAE/TE` and `RequiredClassPoints` (AE + TE spent inside the entry's class) are
  checked against the rest of the set.
- Masteries (`0x1000`) and traits (`0x400000`) are never bought: the server adds them to the
  set when the character's class points reach `RequiredClassPoints` and the level is met
  (`HeroAddAutoGrants`), and `APPLY` strips them from the wanted set before validating so a
  client cannot pay for them. `RESET` keeps every non-talent entry.
- With `AscensionCA.HeroProficiencies = 1` (default) a Hero is taught the weapon and armour
  proficiencies of all ten classes at login (`HERO_PROFICIENCIES`), since its abilities sit
  on skill lines the carrier class never had.
- Reason strings are the panel's own keys: `CA_LEARN_UNKNOWN`, `CA_LEARN_WRONG_CLASS`,
  `CA_LEARN_ALREADY_KNOWN`, `CA_LEARN_LOW_LEVEL`, `CA_LEARN_MISSING_REQUIRED_ID`,
  `CA_LEARN_MISSING_CONNECTED_ENTRIES`, `CA_LEARN_MISSING_AE` / `_TE`,
  `CA_LEARN_NOT_ENOUGH_INVESTED_AE` / `_TE`, `CA_LEARN_CONDITIONS_FAILED`.

## Tables

World (reference, generated by `tools/gen_server_sql.py` from one dataset):
`ascension_ca_dataset`, `ascension_ca_class`, `ascension_ca_spec`, `ascension_ca_entry`,
`ascension_ca_entry_spell`, `ascension_ca_entry_link`, `ascension_ca_essence`.

Characters: `ascension_ca_character(guid, class_byte, spec_id, chosen, archetype, updated_at)`
(`chosen`/`archetype` arrive with `../sql/characters/updates/2026_09_10_01_chosen_archetype.sql`;
a worldserver started against a characters database without them aborts at login with
"Your database structure is not up to date"),
`ascension_ca_known(guid, entry, rank)`.

## Configuration

`AscensionCA.Enable`, `AscensionCA.Mode` (`coa` | `freepick` | `wildcard`, announced in
HELLO), `AscensionCA.DefaultClassByte` (the class byte a character without a row gets; 28 =
Tinker on a CoA realm, 10 on a Hero realm), `AscensionCA.HeroProficiencies`,
`AscensionCA.Debug`.

Put these keys in each realm's own `worldserver*.conf`. AzerothCore reads
`configs/modules/*.conf` **after** the main config and a key found there replaces the main
file's value (`Config.cpp` erases and re-emplaces), so a module conf that names
`AscensionCA.Mode` silently forces every realm sharing the binaries into that mode. The
shipped `conf/mod_ascension_ca.conf.dist` therefore documents the keys and defines only
`AscensionCA.ConfigDistVersion`.
