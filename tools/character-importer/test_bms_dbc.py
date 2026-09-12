"""Tests for the WDBC reader.

Fixtures are built in memory rather than read from a real DBC: the point of
these is the byte-level contract, and a real DBC would make the suite depend on
which server happens to be installed.
"""

import os
import struct
import tempfile
import unittest

import bms_dbc
from bms_dbc import DBC, DbcError, dbc_header


def write_dbc(path, rows, strings, fields=None):
    """A minimal well-formed WDBC.

    `rows` is a list of int tuples, `strings` the raw string block exactly as
    the caller wants it laid out -- including whether it starts with a NUL,
    which is the whole subject of several tests below.
    """
    fields = fields if fields is not None else (len(rows[0]) if rows else 1)
    record_size = fields * 4
    head = struct.pack("<4sIIII", b"WDBC", len(rows), fields, record_size, len(strings))
    body = b"".join(struct.pack("<%di" % fields, *row) for row in rows)
    with open(path, "wb") as handle:
        handle.write(head + body + strings)
    return path


class DbcFixtureMixin:
    def setUp(self):
        self.dir = tempfile.mkdtemp()
        self.addCleanup(self._clean)

    def _clean(self):
        for name in os.listdir(self.dir):
            os.unlink(os.path.join(self.dir, name))
        os.rmdir(self.dir)

    def build(self, name, rows, strings, fields=None):
        return write_dbc(os.path.join(self.dir, name), rows, strings, fields)


class StringBlockTests(DbcFixtureMixin, unittest.TestCase):
    def test_offset_zero_returns_the_string_that_is_there(self):
        """Ascension's blocks start with a real string, and a row points at it.

        This is the bug this test exists for: treating offset 0 as "no string"
        drops that row's name, and the importer reports the lookup as an
        unresolved skip rather than as an error.
        """
        strings = b"Fire\x00Frost\x00"
        path = self.build("TalentTab.dbc", [(0,), (5,)], strings)
        dbc = DBC(path)
        self.assertEqual(dbc.s(0, 0), "Fire")
        self.assertEqual(dbc.s(1, 0), "Frost")

    def test_rows_sharing_a_name_share_one_offset(self):
        """String blocks deduplicate, so "one row per file" is not the rule.

        Every row whose name equals the block's first string reads at offset 0.
        Measured on Ascension's Achievement.dbc: ids 3 and 5610 are both
        "Son of a...", and both were blanked by the old special case.
        """
        strings = b"Son of a...\x00Level 10\x00"
        path = self.build("Achievement.dbc", [(0,), (12,), (0,)], strings)
        dbc = DBC(path)
        self.assertEqual(
            [dbc.s(row, 0) for row in range(3)],
            ["Son of a...", "Level 10", "Son of a..."],
        )

    def test_stock_layout_still_reads_offset_zero_as_empty(self):
        """The fix must not change stock behaviour.

        Stock 3.3.5a puts a lone NUL first, so offset 0 is genuinely the empty
        string there -- and it stays empty for the ordinary reason (the byte at
        offset 0 is a terminator), not because of a special case.
        """
        strings = b"\x00PLAYER, Human\x00"
        path = self.build("Faction.dbc", [(0,), (1,)], strings)
        dbc = DBC(path)
        self.assertEqual(dbc.s(0, 0), "")
        self.assertEqual(dbc.s(1, 0), "PLAYER, Human")

    def test_offset_past_the_block_is_empty_not_an_exception(self):
        path = self.build("Faction.dbc", [(9999,)], b"\x00Hi\x00")
        self.assertEqual(DBC(path).s(0, 0), "")

    def test_negative_offset_is_empty(self):
        path = self.build("Faction.dbc", [(-4,)], b"\x00Hi\x00")
        self.assertEqual(DBC(path).s(0, 0), "")

    def test_unterminated_final_string_reads_to_the_end(self):
        path = self.build("Faction.dbc", [(1,)], b"\x00Trailing")
        self.assertEqual(DBC(path).s(0, 0), "Trailing")

    def test_utf8_is_decoded_and_bad_bytes_do_not_raise(self):
        path = self.build("Faction.dbc", [(1,), (7,)], b"\x00Caf\xc3\xa9\x00\xff\xfe\x00")
        dbc = DBC(path)
        self.assertEqual(dbc.s(0, 0), "Café")
        self.assertTrue(dbc.s(1, 0))  # replaced, not an exception


class HeaderTests(DbcFixtureMixin, unittest.TestCase):
    def test_header_reports_counts_without_reading_the_body(self):
        path = self.build("Talent.dbc", [(1, 2), (3, 4), (5, 6)], b"\x00")
        self.assertEqual(dbc_header(path), (3, 2))

    def test_header_of_a_non_dbc_is_none(self):
        path = os.path.join(self.dir, "notes.txt")
        with open(path, "wb") as handle:
            handle.write(b"this is not a DBC at all, really")
        self.assertIsNone(dbc_header(path))

    def test_header_of_a_missing_file_is_none(self):
        self.assertIsNone(dbc_header(os.path.join(self.dir, "absent.dbc")))

    def test_header_of_a_truncated_file_is_none(self):
        path = os.path.join(self.dir, "short.dbc")
        with open(path, "wb") as handle:
            handle.write(b"WDBC\x01")
        self.assertIsNone(dbc_header(path))


class RecordTests(DbcFixtureMixin, unittest.TestCase):
    def test_fields_are_read_by_row_and_column(self):
        path = self.build("Talent.dbc", [(10, 11, 12), (20, 21, 22)], b"\x00")
        dbc = DBC(path)
        self.assertEqual(dbc.i(1, 2), 22)
        self.assertEqual(len(dbc), 2)

    def test_u_reads_a_high_id_as_unsigned(self):
        """Ascension ids run past 2^31, and struct 'i' would make them negative."""
        path = self.build("Achievement.dbc", [(-1,)], b"\x00")
        self.assertEqual(DBC(path).u(0, 0), 0xFFFFFFFF)

    def test_a_non_wdbc_file_is_refused(self):
        path = os.path.join(self.dir, "fake.dbc")
        with open(path, "wb") as handle:
            handle.write(b"XXXX" + b"\x00" * 32)
        with self.assertRaises(DbcError):
            DBC(path)

    def test_a_narrower_layout_is_refused(self):
        """Removed columns mean every offset below is wrong; a wider file is fine."""
        path = self.build("Talent.dbc", [(1, 2)], b"\x00", fields=2)
        dbc = DBC(path)
        with self.assertRaises(DbcError):
            dbc._expect_fields(bms_dbc.TALENT_FIELDS)

    def test_a_wider_layout_is_allowed_and_recorded(self):
        wide = bms_dbc.TALENT_FIELDS + 3
        path = self.build("Talent.dbc", [tuple(range(wide))], b"\x00", fields=wide)
        dbc = DBC(path)
        dbc._expect_fields(bms_dbc.TALENT_FIELDS)
        self.assertTrue(dbc.widened)


class RebalancedTreeTests(DbcFixtureMixin, unittest.TestCase):
    """A fork that rebalanced a tree and kept the originals in the file.

    Modelled on the CoA repack's Talent.dbc, where every stock Mage/Fire talent
    is still present, collapsed onto tier 0 and sharing cells, beside a
    replacement laid out one per cell across real tiers. Picking the lower id
    there would import the launch-era talent the fork deliberately replaced.
    """

    CLASS_MAGE = 8
    TAB = 41

    def _talent(self, talent_id, row, col, rank1, tab=None):
        fields = [0] * bms_dbc.TALENT_FIELDS
        fields[bms_dbc.TALENT_ID] = talent_id
        fields[bms_dbc.TALENT_TAB] = self.TAB if tab is None else tab
        fields[bms_dbc.TALENT_ROW] = row
        fields[bms_dbc.TALENT_COL] = col
        fields[bms_dbc.TALENT_RANK] = rank1
        return tuple(fields)

    def _build(self, talents, names):
        """names: {spell_id: label}. Lays out Spell.dbc and TalentTab.dbc too."""
        blob = b"\x00"
        offsets = {}
        for spell_id, label in names.items():
            offsets[spell_id] = len(blob)
            blob += label.encode("utf-8") + b"\x00"

        spell_rows = []
        for spell_id, label in names.items():
            fields = [0] * (bms_dbc.SPELL_NAME + 1)
            fields[bms_dbc.SPELL_ID] = spell_id
            fields[bms_dbc.SPELL_NAME] = offsets[spell_id]
            spell_rows.append(tuple(fields))
        self.build("Spell.dbc", spell_rows, blob, fields=bms_dbc.SPELL_NAME + 1)

        tab_blob = b"\x00Fire\x00"
        tab = [0] * bms_dbc.TALENTTAB_FIELDS
        tab[bms_dbc.TALENTTAB_ID] = self.TAB
        tab[bms_dbc.TALENTTAB_NAME] = 1
        tab[bms_dbc.TALENTTAB_CLASS_MASK] = bms_dbc.class_mask(self.CLASS_MAGE)
        tab[bms_dbc.TALENTTAB_PAGE] = 0
        self.build("TalentTab.dbc", [tuple(tab)], tab_blob,
                   fields=bms_dbc.TALENTTAB_FIELDS)

        self.build("Talent.dbc", talents, b"\x00", fields=bms_dbc.TALENT_FIELDS)
        return bms_dbc.Resolvers(self.dir)

    def _rebalanced(self):
        # Four stock talents piled into one tier-0 cell, beside their
        # replacements laid out one per cell. Only 'Ignite' is contested.
        talents = [
            self._talent(25, 0, 0, 11095),      # stock, piled
            self._talent(27, 0, 0, 11078),      # stock, piled
            self._talent(34, 0, 0, 11119),      # stock, piled  (Ignite)
            self._talent(28, 0, 0, 11100),      # stock, piled
            self._talent(110034, 0, 0, 1111119),  # replacement Ignite, piled too
            self._talent(110025, 3, 0, 1111095),  # anchors: sole in their cells
            self._talent(110027, 1, 1, 1111078),
            self._talent(110028, 2, 0, 1111100),
        ]
        names = {
            11095: "Improved Scorch", 11078: "Improved Fire Blast",
            11119: "Ignite", 11100: "Flame Throwing",
            1111119: "Ignite", 1111095: "Improved Scorch",
            1111078: "Improved Fire Blast", 1111100: "Flame Throwing",
        }
        return self._build(talents, names)

    def test_the_replacement_wins_over_the_talent_it_replaced(self):
        res = self._rebalanced()
        got = res.talent_spell(self.CLASS_MAGE, "Fire", "Ignite", 1)
        self.assertTrue(got.ok, got.reason)
        self.assertEqual(got.value, 1111119)

    def test_the_swap_is_reported_rather_than_made_silently(self):
        res = self._rebalanced()
        got = res.talent_spell(self.CLASS_MAGE, "Fire", "Ignite", 1)
        self.assertIn("1111119", got.note)
        self.assertIn("11119", got.note)

    def test_a_tree_with_one_generation_still_refuses_to_guess(self):
        """Two same-named talents that are genuinely both on the tree."""
        talents = [
            self._talent(40, 0, 0, 53483),
            self._talent(41, 0, 0, 53554),
            self._talent(42, 1, 0, 11100),
            self._talent(43, 2, 0, 11095),
        ]
        names = {53483: "Mobility", 53554: "Mobility",
                 11100: "Flame Throwing", 11095: "Improved Scorch"}
        res = self._build(talents, names)
        got = res.talent_spell(self.CLASS_MAGE, "Fire", "Mobility", 1)
        self.assertFalse(got.ok)
        self.assertIn("ambiguous", got.reason)

    def test_a_lone_straggler_does_not_stretch_the_window(self):
        """One surviving original as a sole cell occupant must not match all."""
        talents = [
            self._talent(140, 0, 3, 12299),       # straggler original, anchor
            self._talent(141, 0, 0, 12301),       # stock, piled
            self._talent(110141, 0, 0, 1112301),  # replacement, piled
            self._talent(110144, 1, 1, 1150685),  # anchors
            self._talent(110146, 3, 2, 1112308),
            self._talent(110147, 2, 1, 1112797),
        ]
        names = {12299: "Firm Grip", 12301: "Improved Bloodrage",
                 1112301: "Improved Bloodrage", 1150685: "Incite",
                 1112308: "Puncture", 1112797: "Improved Revenge"}
        res = self._build(talents, names)
        got = res.talent_spell(self.CLASS_MAGE, "Fire", "Improved Bloodrage", 1)
        self.assertTrue(got.ok, got.reason)
        self.assertEqual(got.value, 1112301)

    def test_talent_rank_spells_collects_every_rank(self):
        res = self._rebalanced()
        self.assertIn(1111119, res.talent_rank_spells())
        self.assertIn(11119, res.talent_rank_spells())
        self.assertNotIn(0, res.talent_rank_spells())


class ClassTokenTests(DbcFixtureMixin, unittest.TestCase):
    """A fork's class token and its display name are not the same string.

    Ascension names class 32 "Runemaster" while UnitClass() returns SPIRITMAGE,
    and 7 of its 21 custom classes disagree that way, so a captured token can
    only be mapped by reading the target's own ChrClasses.dbc.
    """

    def build_classes(self, rows):
        """rows: [(class_id, token, name)] -> a ChrClasses.dbc in the fixture dir."""
        blob = b"\x00"
        offsets = {}
        for _cid, token, name in rows:
            for text in (token, name):
                if text not in offsets:
                    offsets[text] = len(blob)
                    blob += text.encode("utf-8") + b"\x00"
        records = []
        for cid, token, name in rows:
            fields = [0] * (bms_dbc.CHRCLASSES_TOKEN + 1)
            fields[bms_dbc.CHRCLASSES_ID] = cid
            fields[bms_dbc.CHRCLASSES_NAME] = offsets[name]
            fields[bms_dbc.CHRCLASSES_TOKEN] = offsets[token]
            records.append(tuple(fields))
        self.build("ChrClasses.dbc", records, blob,
                   fields=bms_dbc.CHRCLASSES_TOKEN + 1)
        return bms_dbc.Resolvers(self.dir)

    def fork(self):
        return self.build_classes([
            (8, "MAGE", "Mage"),
            (13, "WITCHDOCTOR", "Witch Doctor"),
            (26, "STARCALLER", "Starcaller"),
            (32, "SPIRITMAGE", "Runemaster"),
        ])

    def test_the_token_a_capture_records_resolves(self):
        self.assertEqual(self.fork().class_by_token("STARCALLER").value, 26)

    def test_a_token_that_disagrees_with_the_display_name_resolves(self):
        self.assertEqual(self.fork().class_by_token("SPIRITMAGE").value, 32)

    def test_the_display_name_resolves_too(self):
        """Which of the two a checkpoint recorded is not worth guessing."""
        self.assertEqual(self.fork().class_by_token("Runemaster").value, 32)

    def test_spacing_and_case_do_not_matter(self):
        res = self.fork()
        self.assertEqual(res.class_by_token("witch doctor").value, 13)
        self.assertEqual(res.class_by_token("Witch_Doctor").value, 13)
        self.assertEqual(res.class_by_token("WITCHDOCTOR").value, 13)

    def test_a_class_the_target_does_not_have_is_refused(self):
        stock = self.build_classes([(8, "MAGE", "Mage"), (11, "DRUID", "Druid")])
        got = stock.class_by_token("STARCALLER")
        self.assertFalse(got.ok)
        self.assertIn("ChrClasses.dbc", got.reason)

    def test_an_empty_token_is_refused_rather_than_matched(self):
        self.assertFalse(self.fork().class_by_token("").ok)
        self.assertFalse(self.fork().class_by_token(None).ok)

    def test_two_classes_sharing_a_name_stay_ambiguous(self):
        res = self.build_classes([(8, "MAGE", "Mage"), (40, "MAGE2", "Mage")])
        got = res.class_by_token("Mage")
        self.assertFalse(got.ok)
        self.assertIn("ambiguous", got.reason)

    def test_the_display_name_is_available_for_reporting(self):
        self.assertEqual(self.fork().class_name(32), "Runemaster")

    def test_a_target_without_the_file_resolves_nothing_instead_of_raising(self):
        res = bms_dbc.Resolvers(self.dir)
        self.assertFalse(res.class_by_token("MAGE").ok)


class DominantSpanTests(unittest.TestCase):
    def test_lumpy_ids_of_one_generation_are_kept_whole(self):
        ids = [110023, 110024, 110036, 111639, 111852, 112212]
        self.assertEqual(bms_dbc._dominant_span(ids), (110023, 112212))

    def test_a_generation_boundary_is_cut_away(self):
        ids = [140, 110138, 110140, 110702, 112247]
        self.assertEqual(bms_dbc._dominant_span(ids), (110138, 112247))

    def test_too_few_ids_to_judge_are_left_alone(self):
        self.assertEqual(bms_dbc._dominant_span([5, 900000]), (5, 900000))
        self.assertIsNone(bms_dbc._dominant_span([]))


if __name__ == "__main__":
    unittest.main()
