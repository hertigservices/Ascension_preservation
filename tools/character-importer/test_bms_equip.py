#!/usr/bin/env python3
"""Tests for the server's equip rules, restated in bms_equip.

Run:  python test_bms_equip.py
"""

from __future__ import annotations

import os
import sys
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from bms_equip import (  # noqa: E402
    Wearer,
    equip_refusal,
    expand_legacy_class_mask,
    item_allowable_class_mask,
    legacy_class,
    reputation_rank,
)

CHEST, FINGER_2, MAIN_HAND, OFF_HAND = 4, 11, 15, 16
CHRONOMANCER, FLESHWARDEN, PYROMANCER, SUN_CLERIC = 22, 17, 24, 27


def bit(class_id):
    return 1 << (class_id - 1)


def item(**overrides):
    """A cloth chest anyone can wear: the item_template columns the check reads."""
    row = {"entry": 1, "class": 4, "subclass": 1, "InventoryType": 5, "Quality": 2,
           "RequiredLevel": 0, "AllowableClass": -1, "AllowableRace": -1,
           "RequiredSkill": 0, "RequiredSkillRank": 0, "requiredspell": 0,
           "FlagsExtra": 0, "HolidayId": 0,
           "RequiredReputationFaction": 0, "RequiredReputationRank": 0}
    row.update(overrides)
    return row


def chronomancer(**overrides):
    """The live export's character: a level-20 Alliance Human Chronomancer with Cloth."""
    fields = dict(level=20, race=1, class_id=CHRONOMANCER, team="Alliance",
                  skills={415: 1, 54: 1}, spells=set())
    fields.update(overrides)
    return Wearer(**fields)


class ClassMaskTests(unittest.TestCase):
    """GetItemAllowableClassMask widens stock masks to the classes that fall back to them."""

    def test_a_priest_item_admits_the_priest_fallback_classes(self):
        mask = item_allowable_class_mask(bit(5))
        self.assertTrue(mask & bit(CHRONOMANCER))
        self.assertTrue(mask & bit(SUN_CLERIC))
        self.assertFalse(mask & bit(PYROMANCER))

    def test_a_mask_naming_a_custom_class_is_not_widened(self):
        authored = bit(5) | bit(PYROMANCER)
        self.assertEqual(expand_legacy_class_mask(authored), authored)

    def test_the_classless_bit_alone_admits_every_custom_class(self):
        mask = item_allowable_class_mask(0x200)
        for class_id in range(12, 33):
            self.assertTrue(mask & bit(class_id), class_id)
        self.assertFalse(mask & bit(5))

    def test_every_class_mask_stays_every_class(self):
        self.assertEqual(item_allowable_class_mask(-1), 0xFFFFFFFF)

    def test_the_fallback_table_matches_the_server(self):
        self.assertEqual(legacy_class(CHRONOMANCER), 5)
        self.assertEqual(legacy_class(FLESHWARDEN), 1)
        self.assertEqual(legacy_class(32), 7)
        self.assertEqual(legacy_class(8), 8)


class EquipRefusalTests(unittest.TestCase):

    def test_an_ordinary_item_stays_on(self):
        self.assertEqual(equip_refusal(item(), CHEST, chronomancer()), "")

    def test_gear_above_the_characters_level_comes_off(self):
        # Sanguine Armor on the live export: RequiredLevel 23 on a level 20.
        why = equip_refusal(item(RequiredLevel=23), CHEST, chronomancer())
        self.assertIn("level 23", why)
        self.assertIn("level 20", why)

    def test_gear_at_exactly_the_characters_level_stays_on(self):
        self.assertEqual(equip_refusal(item(RequiredLevel=20), CHEST, chronomancer()), "")

    def test_a_class_restriction_is_read_through_the_fallback_class(self):
        self.assertEqual(equip_refusal(item(AllowableClass=bit(5)), CHEST, chronomancer()), "")
        self.assertIn("class 22", equip_refusal(item(AllowableClass=bit(8)), CHEST, chronomancer()))

    def test_a_race_restriction(self):
        self.assertIn("race 1", equip_refusal(item(AllowableRace=1 << 1), CHEST, chronomancer()))

    def test_faction_only_items(self):
        self.assertIn("Horde-only", equip_refusal(item(FlagsExtra=0x1), CHEST, chronomancer()))
        self.assertEqual(equip_refusal(item(FlagsExtra=0x2), CHEST, chronomancer()), "")

    def test_an_unknown_team_does_not_refuse_on_faction(self):
        self.assertEqual(equip_refusal(item(FlagsExtra=0x1), CHEST, chronomancer(team="")), "")

    def test_the_slot_must_fit_the_inventory_type(self):
        self.assertIn("main hand", equip_refusal(item(), MAIN_HAND, chronomancer()))
        ring = item(InventoryType=11, subclass=0)
        self.assertEqual(equip_refusal(ring, FINGER_2, chronomancer()), "")

    def test_a_placeholder_row_with_no_inventory_type_comes_off(self):
        # --synthesize writes a row with no InventoryType; the server's
        # FindEquipSlot returns NULL_SLOT for it.
        placeholder = {"entry": 9, "RequiredLevel": 5}
        self.assertIn("InventoryType 0", equip_refusal(placeholder, CHEST, chronomancer()))

    def test_the_capture_is_trusted_about_dual_wielding(self):
        one_hander = item(InventoryType=13, **{"class": 2, "subclass": 4})
        self.assertEqual(equip_refusal(one_hander, OFF_HAND, chronomancer()), "")

    def test_armour_needs_its_proficiency(self):
        mail = item(subclass=3)
        self.assertIn("Mail", equip_refusal(mail, CHEST, chronomancer()))
        self.assertEqual(equip_refusal(item(subclass=0), CHEST, chronomancer(skills={})), "")

    def test_a_proficiency_spell_counts_as_the_skill(self):
        # Learning Mail (8737) sets skill 413 while spells load, before the inventory.
        self.assertEqual(equip_refusal(item(subclass=3), CHEST, chronomancer(spells={8737})), "")

    def test_a_weapon_needs_its_weapon_skill(self):
        mace = item(InventoryType=13, **{"class": 2, "subclass": 4})
        self.assertEqual(equip_refusal(mace, MAIN_HAND, chronomancer()), "")
        self.assertIn("Maces", equip_refusal(mace, MAIN_HAND, chronomancer(skills={415: 1})))

    def test_heirloom_plate_morphs_for_a_warrior_fallback_class_only(self):
        heirloom = item(subclass=4, Quality=7)
        self.assertEqual(equip_refusal(heirloom, CHEST, chronomancer(class_id=FLESHWARDEN)), "")
        self.assertIn("Plate Mail", equip_refusal(heirloom, CHEST, chronomancer()))

    def test_required_skill_and_rank(self):
        goggles = item(RequiredSkill=202, RequiredSkillRank=200)
        self.assertIn("does not have", equip_refusal(goggles, CHEST, chronomancer()))
        low = chronomancer(skills={415: 1, 202: 150})
        self.assertIn("at 200", equip_refusal(goggles, CHEST, low))
        high = chronomancer(skills={415: 1, 202: 200})
        self.assertEqual(equip_refusal(goggles, CHEST, high), "")

    def test_required_spell(self):
        self.assertIn("spell 12345", equip_refusal(item(requiredspell=12345), CHEST, chronomancer()))
        self.assertEqual(
            equip_refusal(item(requiredspell=12345), CHEST, chronomancer(spells={12345})), "")

    def test_a_world_event_item_comes_off(self):
        self.assertIn("world event", equip_refusal(item(HolidayId=141), CHEST, chronomancer()))

    def test_a_reputation_requirement(self):
        honored = item(RequiredReputationFaction=72, RequiredReputationRank=5)
        self.assertIn("Honored", equip_refusal(honored, CHEST, chronomancer(standing=lambda f: 8999)))
        self.assertEqual(equip_refusal(honored, CHEST, chronomancer(standing=lambda f: 9000)), "")


class ReputationRankTests(unittest.TestCase):

    def test_rank_floors(self):
        self.assertEqual(reputation_rank(-42000), 0)
        self.assertEqual(reputation_rank(-6001), 0)
        self.assertEqual(reputation_rank(-6000), 1)
        self.assertEqual(reputation_rank(-1), 2)
        self.assertEqual(reputation_rank(0), 3)
        self.assertEqual(reputation_rank(2999), 3)
        self.assertEqual(reputation_rank(3000), 4)
        self.assertEqual(reputation_rank(21000), 6)
        self.assertEqual(reputation_rank(42000), 7)


if __name__ == "__main__":
    unittest.main()
