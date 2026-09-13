"""Would the server let this character wear this item when it loads?

AzerothCore does not trust character_inventory. While it loads a character,
Player::_LoadInventory puts every equipped item back through CanEquipItem, and
anything that fails is taken off and mailed to the player. For an import, that
means the character arrives wearing less than the report said. On a Conquest of
Azeroth realm it is worse: mod-ascension-compat's login starter-kit repair then
fills each emptied slot with starter gear.

This module restates those checks over the rows the importer is about to write,
in the server's order (PlayerStorage.cpp: CanEquipItem -> CanUseItem(Item*) ->
CanUseItem(ItemTemplate*)). An item the character cannot wear yet goes into the
backpack, where the player can equip it once they qualify, and the report names
it and says why.

Three checks are deliberately NOT restated, because what decides them is not in
the capture or the database before the character is in the world:

  * dual wield and Titan's Grip -- FindEquipSlot asks CanDualWield(), which the
    server derives from the character's spells. The capture's own layout is
    trusted: the character was wearing it that way.
  * which classes may use a relic slot (IsClass(..., CLASS_CONTEXT_EQUIP_RELIC)).
  * ScalingStatDistribution.MaxLevel on heirlooms, which lives in a DBC this
    pass does not read.

If the server refuses one of those anyway, it mails the item; nothing is lost.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable

ITEM_CLASS_WEAPON = 2          # ItemTemplate.h: enum ItemClass
ITEM_CLASS_ARMOR = 4
ITEM_QUALITY_HEIRLOOM = 7
ITEM_FLAG2_FACTION_HORDE = 0x00000001      # item_template.FlagsExtra is ItemTemplate::Flags2
ITEM_FLAG2_FACTION_ALLIANCE = 0x00000002

CLASS_WARRIOR, CLASS_PALADIN, CLASS_HUNTER, CLASS_ROGUE, CLASS_PRIEST = 1, 2, 3, 4, 5
CLASS_SHAMAN, CLASS_MAGE, CLASS_WARLOCK, CLASS_DRUID = 7, 8, 9, 11
FIRST_CUSTOM_CLASS = 12        # SharedDefines.h: CLASS_BARBARIAN
LAST_CUSTOM_CLASS = 32         # SharedDefines.h: CLASS_SPIRIT_MAGE

# SharedDefines.h: GetLegacyClassForCustomClass. Items and quests written for a
# stock class also admit the custom classes that fall back to it.
LEGACY_CLASS = {
    12: CLASS_ROGUE,    # Barbarian
    13: CLASS_SHAMAN,   # Witch Doctor
    14: CLASS_ROGUE,    # Demon Hunter (Felsworn)
    15: CLASS_HUNTER,   # Witch Hunter
    16: CLASS_SHAMAN,   # Stormbringer
    17: CLASS_WARRIOR,  # Fleshwarden (Knight of Xoroth)
    18: CLASS_WARRIOR,  # Guardian
    19: CLASS_ROGUE,    # Monk (Templar)
    20: CLASS_DRUID,    # Son of Arugal (Bloodmage)
    21: CLASS_HUNTER,   # Ranger
    22: CLASS_PRIEST,   # Chronomancer
    23: CLASS_WARLOCK,  # Necromancer
    24: CLASS_MAGE,     # Pyromancer
    25: CLASS_PALADIN,  # Cultist
    26: CLASS_DRUID,    # Starcaller
    27: CLASS_PRIEST,   # Sun Cleric
    28: CLASS_HUNTER,   # Tinker
    29: CLASS_SHAMAN,   # Prophet (Venomancer)
    30: CLASS_ROGUE,    # Reaper
    31: CLASS_DRUID,    # Wildwalker (Primalist)
    32: CLASS_SHAMAN,   # Spirit Mage (Runemaster)
}

CUSTOM_CLASS_BITS = 0xFFFFF800
CLASSLESS_CLASS_MASK = 0x00000200   # Ascension's reserved class id 10

TEAM_BY_RACE = {1: "Alliance", 3: "Alliance", 4: "Alliance", 7: "Alliance", 11: "Alliance",
                2: "Horde", 5: "Horde", 6: "Horde", 8: "Horde", 10: "Horde"}

# character_inventory.slot 0..18
SLOT_NAMES = ("head", "neck", "shoulders", "shirt", "chest", "waist", "legs", "feet",
              "wrists", "hands", "finger 1", "finger 2", "trinket 1", "trinket 2", "back",
              "main hand", "off hand", "ranged", "tabard")

# Player::FindEquipSlot, by item_template.InventoryType. The off hand is listed
# for one-handers and two-handers because the capture's layout is trusted there
# (see the module docstring).
SLOTS_BY_INVENTORY_TYPE = {
    1: (0,), 2: (1,), 3: (2,), 4: (3,), 5: (4,), 20: (4,), 6: (5,), 7: (6,), 8: (7,),
    9: (8,), 10: (9,), 11: (10, 11), 12: (12, 13), 16: (14,),
    13: (15, 16),       # INVTYPE_WEAPON
    17: (15, 16),       # INVTYPE_2HWEAPON
    21: (15,),          # INVTYPE_WEAPONMAINHAND
    14: (16,), 22: (16,), 23: (16,),     # shield, off-hand weapon, holdable
    15: (17,), 25: (17,), 26: (17,),     # ranged, thrown, ranged right
    28: (17,),          # INVTYPE_RELIC
    19: (18,),          # INVTYPE_TABARD
}

# ItemTemplate::GetSkill, indexed by subclass.
WEAPON_SKILLS = (44, 172, 45, 46, 54, 160, 229, 43, 55, 0, 136, 0, 0, 473, 0, 173, 176, 253,
                 226, 228, 356)
ARMOR_SKILLS = (0, 415, 414, 413, 293, 0, 433, 0, 0, 0, 0)
SKILL_PLATE_MAIL, SKILL_MAIL = 293, 413

# Item::GetSpell: the spell that teaches each proficiency. Learning one sets its
# skill while the character's spells load, which happens before the inventory.
WEAPON_SPELLS = {0: 196, 1: 197, 2: 264, 3: 266, 4: 198, 5: 199, 6: 200, 7: 201, 8: 202,
                 10: 227, 15: 1180, 16: 2567, 17: 3386, 18: 5011, 19: 5009}
ARMOR_SPELLS = {1: 9078, 2: 9077, 3: 8737, 4: 750, 6: 9116}

SKILL_NAMES = {43: "Swords", 44: "Axes", 45: "Bows", 46: "Guns", 54: "Maces",
               55: "Two-Handed Swords", 136: "Staves", 160: "Two-Handed Maces",
               172: "Two-Handed Axes", 173: "Daggers", 176: "Thrown", 226: "Crossbows",
               228: "Wands", 229: "Polearms", 253: "Spears", 293: "Plate Mail",
               356: "Fishing", 413: "Mail", 414: "Leather", 415: "Cloth", 433: "Shield",
               473: "Fist Weapons"}

# ReputationMgr::ReputationToRank: the lowest standing of each rank.
REPUTATION_RANK_FLOORS = (-42000, -6000, -3000, 0, 3000, 9000, 21000, 42000)
REPUTATION_RANK_NAMES = ("Hated", "Hostile", "Unfriendly", "Neutral", "Friendly", "Honored",
                         "Revered", "Exalted")


def legacy_class(class_id: int) -> int:
    return LEGACY_CLASS.get(int(class_id), int(class_id))


def expand_legacy_class_mask(mask: int) -> int:
    """SharedDefines.h: ExpandLegacyClassMask.

    A mask that already names a custom class was written for this realm and is
    left alone; a stock mask also admits every custom class whose fallback class
    it names.
    """
    mask &= 0xFFFFFFFF
    if mask & CUSTOM_CLASS_BITS:
        return mask
    result = mask
    for class_id in range(FIRST_CUSTOM_CLASS, LAST_CUSTOM_CLASS + 1):
        if mask & (1 << (legacy_class(class_id) - 1)):
            result |= 1 << (class_id - 1)
    return result


def item_allowable_class_mask(allowable_class: int) -> int:
    """ItemTemplate.h: GetItemAllowableClassMask, applied as item_template loads.

    A stock target has no class above 11, so the expansion never changes an
    answer there.
    """
    mask = int(allowable_class) & 0xFFFFFFFF
    if mask == CLASSLESS_CLASS_MASK:
        return mask | CUSTOM_CLASS_BITS
    return expand_legacy_class_mask(mask)


def proficiency_skill(item_class: int, subclass: int) -> int:
    if item_class == ITEM_CLASS_WEAPON and 0 <= subclass < len(WEAPON_SKILLS):
        return WEAPON_SKILLS[subclass]
    if item_class == ITEM_CLASS_ARMOR and 0 <= subclass < len(ARMOR_SKILLS):
        return ARMOR_SKILLS[subclass]
    return 0


PROFICIENCY_BY_SPELL = {spell: WEAPON_SKILLS[sub] for sub, spell in WEAPON_SPELLS.items()}
PROFICIENCY_BY_SPELL.update({spell: ARMOR_SKILLS[sub] for sub, spell in ARMOR_SPELLS.items()})


def reputation_rank(standing: int) -> int:
    for rank in range(len(REPUTATION_RANK_FLOORS) - 1, -1, -1):
        if standing >= REPUTATION_RANK_FLOORS[rank]:
            return rank
    return 0


def slot_name(db_slot: int) -> str:
    return SLOT_NAMES[db_slot] if 0 <= db_slot < len(SLOT_NAMES) else "slot %d" % db_slot


@dataclass
class Wearer:
    """The character as the server sees it when its inventory loads.

    Skills, spells and reputations load before the inventory
    (Player::LoadFromDB), so the planned rows for those tables are exactly what
    each equipped item is checked against.
    """

    level: int
    race: int
    class_id: int
    team: str = ""                                    # "Alliance", "Horde", or "" if unknown
    skills: dict[int, int] = field(default_factory=dict)
    spells: set[int] = field(default_factory=set)
    standing: Callable[[int], int] | None = None      # faction id -> absolute standing

    def skill_value(self, skill: int) -> int:
        value = self.skills.get(int(skill), 0)
        if value <= 0 and any(PROFICIENCY_BY_SPELL.get(s) == skill for s in self.spells):
            return 1
        return value


def _num(item: dict[str, Any], key: str, default: int = 0) -> int:
    value = item.get(key)
    if value is None:
        return default
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def equip_refusal(item: dict[str, Any], db_slot: int, wearer: Wearer) -> str:
    """Why the server would take this item off at load, or "" if it would stay.

    `item` is the item_template row. A key it lacks takes the schema default,
    so a synthesised placeholder -- InventoryType 0 -- is refused exactly as the
    server would refuse it.
    """
    inventory_type = _num(item, "InventoryType")
    slots = SLOTS_BY_INVENTORY_TYPE.get(inventory_type)
    if not slots:
        return "this server does not treat it as wearable (InventoryType %d)" % inventory_type
    if db_slot not in slots:
        return "InventoryType %d does not go in the %s slot" % (inventory_type, slot_name(db_slot))

    flags2 = _num(item, "FlagsExtra") & 0xFFFFFFFF
    if wearer.team:
        if flags2 & ITEM_FLAG2_FACTION_HORDE and wearer.team != "Horde":
            return "it is Horde-only"
        if flags2 & ITEM_FLAG2_FACTION_ALLIANCE and wearer.team != "Alliance":
            return "it is Alliance-only"

    class_mask = item_allowable_class_mask(_num(item, "AllowableClass", -1))
    if not class_mask & (1 << (wearer.class_id - 1)):
        return "its class restriction (AllowableClass 0x%X) excludes class %d" % (
            _num(item, "AllowableClass", -1) & 0xFFFFFFFF, wearer.class_id)
    race_mask = _num(item, "AllowableRace", -1) & 0xFFFFFFFF
    if not race_mask & (1 << (wearer.race - 1)):
        return "its race restriction (AllowableRace 0x%X) excludes race %d" % (race_mask, wearer.race)

    required_skill = _num(item, "RequiredSkill")
    if required_skill:
        have = wearer.skill_value(required_skill)
        need = _num(item, "RequiredSkillRank")
        if have == 0:
            return "it requires skill %d, which the character does not have" % required_skill
        if have < need:
            return "it requires skill %d at %d; the character has %d" % (required_skill, need, have)

    required_spell = _num(item, "requiredspell")
    if required_spell and required_spell not in wearer.spells:
        return "it requires spell %d, which the character does not know" % required_spell

    required_level = _num(item, "RequiredLevel")
    if wearer.level < required_level:
        return "it requires level %d; the character is level %d" % (required_level, wearer.level)

    holiday = _num(item, "HolidayId")
    if holiday:
        return "it can only be worn while world event %d is running" % holiday

    item_class, subclass = _num(item, "class"), _num(item, "subclass")
    skill = proficiency_skill(item_class, subclass)
    if skill and wearer.skill_value(skill) == 0:
        allowed = False
        if _num(item, "Quality") == ITEM_QUALITY_HEIRLOOM and item_class == ITEM_CLASS_ARMOR:
            # Heirloom armour morphs down to what the class wears
            # (CanUseItem(Item*); custom classes answer as their legacy class).
            base = legacy_class(wearer.class_id)
            if base in (CLASS_PALADIN, CLASS_WARRIOR):
                allowed = skill == SKILL_PLATE_MAIL
            elif base in (CLASS_HUNTER, CLASS_SHAMAN):
                allowed = skill == SKILL_MAIL
        if not allowed:
            return "it needs the %s proficiency, which the character does not have" % (
                SKILL_NAMES.get(skill, "skill %d" % skill))

    faction = _num(item, "RequiredReputationFaction")
    if faction and wearer.standing is not None:
        need = _num(item, "RequiredReputationRank")
        have = reputation_rank(wearer.standing(faction))
        if have < need:
            return "it requires %s with faction %d; the character is %s" % (
                REPUTATION_RANK_NAMES[min(need, 7)], faction, REPUTATION_RANK_NAMES[have])

    return ""
