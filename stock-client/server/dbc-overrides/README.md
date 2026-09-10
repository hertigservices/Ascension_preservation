# DBC overrides shipped to the client (`Data\patch-Y.MPQ`)

`tools/install_client.py` writes every `*.dbc` in this directory into `patch-Y.MPQ`, the
highest free general slot, so these rows beat Ascension's `patch-M/S/T` copies. Loose
`DBFilesClient\` files are ignored by the stock loader; only an archive counts.

| file | origin | why |
|---|---|---|
| `ChrClasses.dbc` (10 rows) | stock 3.3.5a `locale-enUS.MPQ` | Ascension's 32-row table overflows the stock client's fixed class tables at character creation (ERROR #132 with EIP = a class id). The CoA classes are served in-world by mod-ascension-ca instead. |
| `CharBaseInfo.dbc` | stock 3.3.5a | race/class pairs for the stock creation screen (Ascension's 209 rows reference the 32 classes). |
| `CharStartOutfit.dbc` | stock 3.3.5a | starting gear keyed by stock class. |
| `SkillRaceClassInfo.dbc` | Ascension's, rewritten by `tools/patch_skill_race_class.py` | every CoA class skill line (Demolition, Invention, Mechanics, ...) gets ClassMask -1 so a carrier-class character can hold it and the spellbook shows the tab. The same patched file must be in the server's `DataDir`. |

**Not** overridden on purpose: `ChrRaces.dbc`. The client sizes its character-component
tables from ChrRaces and fills them from Ascension's `CharSections` / `CharHairGeosets` /
`CharacterFacialHairStyles` / `CreatureDisplayInfoExtra`, whose race ids reach 63; a stock
ChrRaces here corrupts the heap at every world entry (P2 finding).

The three stock files are unmodified Blizzard data from the 3.3.5a (12340) client, included
for convenience because extracting them needs an MPQ reader; you may equally extract them
yourself from your client's `Data\enUS\locale-enUS.MPQ`.
