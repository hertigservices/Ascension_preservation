# Running the bridge against a CoA-capable core

Contributed by **maribela**, against
[`jealous-sound/azerothcore-wotlk-coa`](https://github.com/jealous-sound/azerothcore-wotlk-coa)
— a public AzerothCore fork whose world database carries real `playercreateinfo`
rows for classes **12..32**.

Everything here is **opt-in**. Unset, the bridge keeps the stock-core behaviour
it has always had.

## The problem the carrier class solves

Stock AzerothCore knows ten playable classes. Ascension's classless realms send a
Character-Advancement class byte up to 32, and `Player::Create` refuses a class
with no `playercreateinfo` row outright. So the bridge rewrites the class byte to
a **carrier class** the core *can* build, remembers the player's real choice
keyed by character name in `coa_state`, and projects that identity back over
`SMSG_CHAR_ENUM` and in-world.

On a core that has those rows, the substitution is not just unnecessary — it is
actively wrong. The class builds natively; rewriting it throws away a real class
in favour of a fake one.

## Turning it on

```sh
export ASC_AC_VALID_CLASSES="1-9,11-32"
```

Ranges and single values, comma separated. Unset keeps the stock-core set
(`1..9, 11`). Note what is **not** in that range: class **10**, the Free-Pick
hero, has no `playercreateinfo` row on *any* core, so it still takes the carrier
path.

Verified against this repository's own tables:

```
coa_mode.AC_VALID_CLASSES = [1..9, 11, 12, 13, ... 32]
  12 in set: True | 10 in set: False | 33 in set: False
bridge AC_VALID_CLASSES is coa_mode's: True
```

## Three bugs this fixes

### 1. The override was computed, then silently discarded

`coa_mode` owns `AC_VALID_CLASSES` and the environment override. But
`ascension_bridge` defined **its own copy** of the stock list, *after* importing
`coa_mode` — so the override was parsed, applied to `coa_mode`, and then shadowed
by the module-level assignment further down the file. Every CoA class went
through the carrier path even on a core that could build it. The bridge now
adopts `coa_mode`'s set instead of redefining it.

### 2. A fixed carrier class produced an illegal race/class pair

`AC_FALLBACK_CLASS` defaulted to 1 (Warrior) for every race. `Player::Create`
validates the pair, and **race 10 (Blood Elf) has no Warrior**, so every Blood
Elf creation failed with:

```
Player::Create: Possible hacking-attempt: Account N tried creating a character
named 'X' with an invalid race/class pair (10/1) - refusing to do so.
```

`carrier_class_for(race)` now picks from the classes that race is legally allowed
to be, preferring `ASC_FALLBACK_CLASS` when it is legal. Actual output, all 3.3.5a
playable races:

| Race | Carrier | Race | Carrier |
|---|---|---|---|
| 1 Human | 1 | 6 Tauren | 1 |
| 2 Orc | 1 | 7 Gnome | 1 |
| 3 Dwarf | 1 | 8 Troll | 1 |
| 4 Night Elf | 1 | 10 Blood Elf | **2 (Paladin)** |
| 5 Undead | 1 | 11 Draenei | 1 |

An unknown or custom race keeps the configured default. **This still matters
with `ASC_AC_VALID_CLASSES` set**: class 10 has no row on any core, and
`CharacterCreate.lua` makes it the default selection when `CanCreateHero` is on,
so a player who never touches Swap Classes lands on exactly this path.

### 3. A refused creation left the name mapped forever

The chosen CoA class used to be written to `coa_state` when the request was
**sent**. If the core then refused the creation — invalid pair, duplicate name —
the mapping stayed. The next character to reuse that name was enum-projected to a
class it did not have, which shows up in game as a character whose portrait and
class label disagree with its spellbook.

The choice is now staged on the session and committed only when the core answers
`CHAR_CREATE_SUCCESS` (`0x2F`, confirmed against the bridge's own
`RESPONSE_CODES` table). A refusal logs the name and class it did **not** record.

## Verification boundary

The logic above was exercised against this repository's real DBC set and its own
response-code table. There is **no CoA-capable core installed on the maintainer's
box**, so native creation of classes 12..32 end-to-end was verified by the
contributor on their own server, not here. The related core lift from the same
fork is documented separately in
[`stock-client/server/core-patches/README.md`](../stock-client/server/core-patches/README.md).
