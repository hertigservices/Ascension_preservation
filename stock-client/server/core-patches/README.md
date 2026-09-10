# Core patches

Changes to AzerothCore itself that the port needs beyond what `mod-ascension-ca` can do from a
module. Each is a unified diff against the AzerothCore tree (paths under `src/server/`), applied
from the source root with `git apply -p1 <patch>`.

## `ascension-spell-enums.patch` (2026-09-10)

**What it does.** Widens the spell-effect enum to Ascension's range (`TOTAL_SPELL_EFFECTS`
165 to 199, ids 165..198) and the aura enum to Ascension's range (`TOTAL_AURAS` 317 to 367, ids
317..366), extends the three tables sized by them (the effect handler table, the aura handler
table, `SpellEffectInfo::_data`), adds a null guard to both dispatchers, and implements the
class-agnostic effects Ascension's spells use:

| Effect | Handler | Behaviour |
|---|---|---|
| 165 `MODIFY_COOLDOWN` | `EffectAscensionModifyCooldown` | adds `damage` ms to the cooldown of spell `MiscValue` (0 = reset; `MiscValueB` also clears the category) |
| 166 `RESTORE_BASE_MANA_PCT` | `EffectAscensionRestoreBaseManaPct` | energizes `damage`% of base mana |
| 173 `REFRESH_AURA` | `EffectAscensionRefreshAura` | refreshes / sets / extends the duration of aura `MiscValue` |
| 175 `MODIFY_AURA_STACKS` | `EffectAscensionModifyAuraStacks` | adds `MiscValue` stacks of aura `TriggerSpell` (applies it if absent) |
| 176 `MODIFY_AURA_STACKS_2` | `EffectAscensionModifyAuraStacksBySpell` | adds `damage` stacks of aura `MiscValue` |
| 177 `MODIFY_AURA_DURATION` | `EffectAscensionModifyAuraDuration` | adds `damage` ms to aura `MiscValue` (removes it at or below zero) |
| 181 `RESTORE_BASE_HEALTH_PCT` | `EffectAscensionRestoreBaseHealthPct` | heals `damage`% of base health through the normal healing bonuses |
| 183 `TRIGGER_SPELL_DELAYED` | `EffectAscensionTriggerSpellDelayed` | casts `TriggerSpell` on the target after `damage` ms |
| 195 `RESET_COOLDOWN` | `EffectAscensionResetCooldown` | clears the cooldown of spell `MiscValue` |
| every other id in 165..198 | `EffectNULL` | loads, does nothing |

Auras: 333 `MOD_HIT_CHANCE_ALL_PCT` (melee + spell hit) and 344 `MOD_ATTACK_POWER_FLAT` (melee +
ranged attack power) are combinations of stock handlers; every other id in 317..366 is
`HandleNoImmediateEffect`, i.e. the aura exists and is script-visible but changes nothing.

**Why.** Ascension's `Spell.dbc` uses these ids on 10,406 rows (6,536 effect slots on 5,388 spells,
5,149 aura slots on 5,018 spells; 430 Character Advancement entries are affected). A stock core
asserts on them at startup, so `server/tools/sanitize-for-core.py` zeroes those slots and journals
them. With this patch the zeroing is unnecessary for those two rules: run
`server/tools/unsanitize-for-core.py <dbc-dir> --journal sanitized-for-core.json` (or start from a
DBC set that was never sanitized). The Achievement_Criteria rule is untouched and stays applied.

**Where it comes from.** Lifted from jealous-sound's public CoA server fork
(`github.com/jealous-sound/azerothcore-wotlk-coa`, HEAD `379aecc28`, 2026-09-10; core AGPL-3.0,
module MIT), files `SharedDefines.h`, `SpellAuraDefines.h`, `SpellEffects.cpp`, `Spell.cpp/.h`,
`SpellAuraEffects.cpp/.h`, `SpellInfo.cpp`. Deliberately NOT lifted from the same files:
`MAX_CLASSES 33` and the real class ids 12..32 (the stock client is `MAX_CLASSES 12`; this port
uses carrier classes), spell charges (`RESTORE_SPELL_CHARGES`, 187, is `EffectNULL` here),
`APPLY_AURA_TO_SUMMONS` (190; his fork maps it to the area-aura handler, a reconstruction choice),
the per-class special cases inside his versions of the handlers above (Thunder Ward, Earthshaping),
the stat-derived auras 327/328/345 (they need his StatSystem changes), his raid-buff spell-group
rules and every per-class spell id. Those are candidates for later work, not silently included.

**Verification.** Built with the playerbots-based tree this port uses (`src-coa2`, MSVC 2022,
RelWithDebInfo, 2026-09-10 10:51-11:05, exit 0). The resulting worldserver, started on its own
Data root and config, loaded the Ascension `Spell.dbc` with all 11,685 journaled slots restored
(209,509 rows; the restored file differs from the old pre-sanitize backup only in the 562 rows
whose out-of-range locale string offsets `fix-dbc.py` had repaired in between) and reached
`mod-ascension-ca: 10255 entries` in 76 s with no assertion. Nothing was cast against it yet:
see `README.md` phase table P3 for what has and has not been exercised in game.
