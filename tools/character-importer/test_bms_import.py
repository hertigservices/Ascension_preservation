#!/usr/bin/env python3
"""Tests for importer logic that needs no database.

Run:  python test_bms_import.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bms_config  # noqa: E402
from bms_import import (  # noqa: E402
    EQUIPMENT_CACHE_MAX,
    EQUIPMENT_CACHE_SIZE,
    FAIL,
    OK,
    WARN,
    Check,
    Plan,
    _captured_durability,
    _durability,
    _equipment_cache,
    _plan_advancement,
    _plan_talents,
    blocks_planning,
    blocks_writing,
    choose_config,
    column_limits,
    dbc_conflict,
    equipment_cache_width,
    resolve_class,
    resolve_settings,
    value_fits,
)


class FakeConn:
    """Answers exactly the one SELECT equipment_cache_width() issues."""

    def __init__(self, caches):
        self.caches = caches

    def cursor(self, *_args, **_kwargs):
        return FakeCursor(self.caches)


class FakeCursor:
    def __init__(self, caches):
        self.caches = caches

    def execute(self, *_args, **_kwargs):
        return None

    def fetchall(self):
        return [{"equipmentCache": c} for c in self.caches]

    def __enter__(self):
        return self

    def __exit__(self, *_exc):
        return False


def width_of(caches):
    args = argparse.Namespace(characters_db="chars")
    return equipment_cache_width(FakeConn(caches), args)


def plan_with(items):
    """items: (inventory slot, item entry, perm enchant id)."""
    plan = Plan(guid=7)
    for index, (slot, entry, enchant) in enumerate(items, start=100):
        blob = [0] * 36
        blob[0] = enchant
        plan.add("item_instance", {
            "guid": index,
            "itemEntry": entry,
            "owner_guid": plan.guid,
            "enchantments": " ".join(str(v) for v in blob),
        })
        plan.add("character_inventory", {
            "guid": plan.guid, "bag": 0, "slot": slot, "item": index,
        })
    return plan


class EquipmentCacheTests(unittest.TestCase):
    """characters.equipmentCache is what the character-select screen renders.

    An empty cache shows a fully equipped character as naked in the list, which
    is exactly the bug this function was written to fix.
    """

    def test_shape_is_always_38_values(self):
        for plan in (Plan(guid=1), plan_with([(0, 100, 0)])):
            self.assertEqual(len(_equipment_cache(plan).split()), EQUIPMENT_CACHE_SIZE)

    def test_a_character_with_nothing_equipped_is_all_zeros(self):
        self.assertEqual(set(_equipment_cache(Plan(guid=1)).split()), {"0"})

    def test_entry_lands_at_slot_times_two(self):
        values = _equipment_cache(plan_with([(3, 6096, 0), (15, 35, 0)])).split()
        self.assertEqual(values[3 * 2], "6096")
        self.assertEqual(values[15 * 2], "35")
        self.assertEqual(values[0], "0")

    def test_permanent_enchant_lands_beside_its_item(self):
        values = _equipment_cache(plan_with([(16, 49623, 3789)])).split()
        self.assertEqual(values[16 * 2], "49623")
        self.assertEqual(values[16 * 2 + 1], "3789")

    def test_backpack_and_bank_items_are_excluded(self):
        """Only slots below EQUIPMENT_SLOT_END are visible on the login screen."""
        values = _equipment_cache(
            plan_with([(0, 111, 0), (23, 2070, 0), (38, 159, 0), (86, 5, 0)])).split()
        self.assertEqual(values[0], "111")
        self.assertEqual([v for v in values if v != "0"], ["111"])

    def test_an_inventory_row_with_no_item_instance_is_skipped(self):
        """A dangling reference must not crash the build, nor invent an entry."""
        plan = plan_with([(3, 6096, 0)])
        plan.add("character_inventory", {"guid": 7, "bag": 0, "slot": 4, "item": 999})
        values = _equipment_cache(plan).split()
        self.assertEqual(values[3 * 2], "6096")
        self.assertEqual(values[4 * 2], "0")

    def test_an_item_with_no_enchantment_blob_is_tolerated(self):
        plan = Plan(guid=7)
        plan.add("item_instance", {"guid": 1, "itemEntry": 42, "enchantments": ""})
        plan.add("character_inventory", {"guid": 7, "bag": 0, "slot": 2, "item": 1})
        values = _equipment_cache(plan).split()
        self.assertEqual(values[2 * 2], "42")
        self.assertEqual(values[2 * 2 + 1], "0")

    def test_it_matches_the_real_capture_that_exposed_the_bug(self):
        """Probethree's five equipped items, as committed to the live realm."""
        real = [(3, 6096, 0), (4, 56, 0), (6, 1395, 0), (7, 55, 0), (15, 35, 0)]
        values = _equipment_cache(plan_with(real)).split()
        self.assertEqual(
            [(i // 2, values[i]) for i in range(0, len(values), 2) if values[i] != "0"],
            [(3, "6096"), (4, "56"), (6, "1395"), (7, "55"), (15, "35")])


class CacheWidthTests(unittest.TestCase):
    """The cache width is a property of the target fork, not a constant.

    Stock is 19 slots (38 values); the Ascension realm this was built against
    writes 23 slots (46). The core reads the blob as fixed-size pairs, so a
    short cache is read out of alignment.
    """

    STOCK = " ".join(["0"] * 38)
    FORK = " ".join(["0"] * 46)

    def test_a_stock_realm_reports_38(self):
        self.assertEqual(width_of([self.STOCK, self.STOCK]), 38)

    def test_a_wider_fork_is_detected(self):
        self.assertEqual(width_of([self.FORK, self.FORK, self.FORK]), 46)

    def test_an_empty_realm_falls_back_to_stock(self):
        self.assertEqual(width_of([]), EQUIPMENT_CACHE_SIZE)

    def test_our_own_short_row_does_not_win_a_tie(self):
        """The bug wrote 38 onto a 46-slot realm; a tie must not preserve it."""
        self.assertEqual(width_of([self.STOCK, self.FORK]), 46)

    def test_the_majority_wins_outright(self):
        self.assertEqual(width_of([self.STOCK, self.STOCK, self.STOCK, self.FORK]), 38)

    def test_junk_rows_are_ignored(self):
        for junk in ("", "   ", None, "0 0 0", " ".join(["0"] * 39),
                     " ".join(["0"] * (EQUIPMENT_CACHE_MAX + 2))):
            self.assertEqual(width_of([junk, self.FORK]), 46)

    def test_all_junk_falls_back_rather_than_crashing(self):
        self.assertEqual(width_of(["", "0 0 0", None]), EQUIPMENT_CACHE_SIZE)

    def test_the_blob_is_built_at_the_detected_width(self):
        values = _equipment_cache(plan_with([(4, 56, 0)]), 46)
        self.assertEqual(len(values.split()), 46)
        self.assertEqual(values.split()[4 * 2], "56")

    def test_bag_slots_never_enter_a_wider_cache(self):
        """Slots 19-22 are bag slots on a stock core; width must not admit them."""
        values = _equipment_cache(plan_with([(15, 35, 0), (19, 4498, 0)]), 46).split()
        self.assertEqual(values[15 * 2], "35")
        self.assertEqual([v for v in values if v != "0"], ["35"])


class ResolveSettingsTests(unittest.TestCase):
    """Flag beats environment beats server config beats stock default.

    This decides which realm gets written to, so the ordering is not cosmetic.
    """

    SECRET = "config-side-password"
    CONF = """
LoginDatabaseInfo     = "10.0.0.1;3307;cfguser;{secret};cfg_auth"
WorldDatabaseInfo     = "10.0.0.1;3307;cfguser;{secret};cfg_world"
CharacterDatabaseInfo = "10.0.0.1;3307;cfguser;{secret};cfg_characters"
DataDir = "{data}"
"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="bms-resolve-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)
        os.makedirs(os.path.join(self.root, "Data", "dbc"))
        self.conf = os.path.join(self.root, "worldserver.conf")
        with open(self.conf, "w", encoding="utf-8") as handle:
            handle.write(self.CONF.format(
                secret=self.SECRET,
                data=os.path.join(self.root, "Data").replace("\\", "/")))
        for variable in ("BMS_DB_HOST", "BMS_DB_PORT", "BMS_DB_USER", "BMS_AUTH_DB",
                         "BMS_CHARACTERS_DB", "BMS_WORLD_DB", "BMS_DBC_DIR",
                         "BMS_DB_PASSWORD", "BMS_SERVER_CONFIG"):
            os.environ.pop(variable, None)
        # These tests are about resolution order, not about whatever happens to
        # be running on the machine running them. RunningServerTests covers the
        # process scan deliberately.
        real = bms_config.running_servers
        bms_config.running_servers = lambda: []
        self.addCleanup(setattr, bms_config, "running_servers", real)

    def args(self, **overrides):
        base = dict(config=self.conf, no_config=False, host=None, port=None,
                    user=None, password=None, password_env="BMS_DB_PASSWORD",
                    auth_db=None, characters_db=None, world_db=None, dbc_dir=None)
        base.update(overrides)
        return argparse.Namespace(**base)

    def origins(self, rows):
        return {name: origin for name, _value, origin in rows}

    def test_a_config_supplies_everything(self):
        args = self.args()
        rows = resolve_settings(args)
        self.assertEqual(args.host, "10.0.0.1")
        self.assertEqual(args.port, 3307)
        self.assertEqual(args.user, "cfguser")
        self.assertEqual(args.characters_db, "cfg_characters")
        self.assertEqual(args.world_db, "cfg_world")
        self.assertEqual(args.auth_db, "cfg_auth")
        self.assertTrue(args.dbc_dir.endswith("dbc"))
        self.assertEqual(set(self.origins(rows).values()), {"config"})

    def test_the_password_comes_from_the_config_when_nothing_else_has_one(self):
        """This is what removes the last required environment variable."""
        args = self.args()
        resolve_settings(args)
        self.assertEqual(args.password, self.SECRET)
        self.assertEqual(args.password_origin, "config")

    def test_an_explicit_flag_beats_the_config(self):
        args = self.args(characters_db="mine", host="192.168.0.5")
        rows = self.origins(resolve_settings(args))
        self.assertEqual(args.characters_db, "mine")
        self.assertEqual(args.host, "192.168.0.5")
        self.assertEqual(rows["characters_db"], "--characters-db")
        self.assertEqual(rows["world_db"], "config")

    def test_the_environment_beats_the_config(self):
        os.environ["BMS_CHARACTERS_DB"] = "env_characters"
        self.addCleanup(os.environ.pop, "BMS_CHARACTERS_DB", None)
        args = self.args()
        rows = self.origins(resolve_settings(args))
        self.assertEqual(args.characters_db, "env_characters")
        self.assertEqual(rows["characters_db"], "$BMS_CHARACTERS_DB")

    def test_an_environment_password_beats_the_config(self):
        os.environ["BMS_DB_PASSWORD"] = "from-env"
        self.addCleanup(os.environ.pop, "BMS_DB_PASSWORD", None)
        args = self.args()
        resolve_settings(args)
        self.assertIsNone(args.password, "connect() reads the variable itself")
        self.assertEqual(args.password_origin, "$BMS_DB_PASSWORD")

    def test_an_explicit_password_beats_everything(self):
        os.environ["BMS_DB_PASSWORD"] = "from-env"
        self.addCleanup(os.environ.pop, "BMS_DB_PASSWORD", None)
        args = self.args(password="from-flag")
        resolve_settings(args)
        self.assertEqual(args.password, "from-flag")
        self.assertEqual(args.password_origin, "--password")

    def test_no_config_falls_back_to_stock_defaults(self):
        args = self.args(config=None, no_config=True)
        rows = self.origins(resolve_settings(args))
        self.assertEqual(args.characters_db, "acore_characters")
        self.assertEqual(args.host, "127.0.0.1")
        self.assertEqual(args.port, 3306)
        self.assertEqual(rows["characters_db"], "default")
        self.assertIsNone(args.config_path)
        self.assertEqual(args.password_origin, "not set")

    def test_a_port_from_the_environment_is_still_an_integer(self):
        os.environ["BMS_DB_PORT"] = "3399"
        self.addCleanup(os.environ.pop, "BMS_DB_PORT", None)
        args = self.args(config=None, no_config=True)
        resolve_settings(args)
        self.assertEqual(args.port, 3399)
        self.assertIsInstance(args.port, int)

    def test_more_than_one_config_is_refused_rather_than_guessed(self):
        """Importing into the wrong realm is worse than asking."""
        for name in ("realm-a", "realm-b"):
            configs = os.path.join(self.root, name, "configs")
            os.makedirs(configs)
            shutil.copy(self.conf, os.path.join(configs, "worldserver.conf"))
        args = self.args(config=None)
        with self.assertRaises(Exception) as caught:
            resolve_settings(args, start=self.root)
        message = str(caught.exception)
        self.assertIn("realm-a", message)
        self.assertIn("realm-b", message)
        self.assertIn("--config", message)

    def test_the_reported_rows_never_carry_the_password(self):
        rows = resolve_settings(self.args())
        self.assertNotIn(self.SECRET, "\n".join("%s %s %s" % r for r in rows))


class DurabilityTests(unittest.TestCase):
    """The addon nests durability; reading a flat key imported everything broken."""

    ROBE = {"name": "Apprentice's Robe", "durability": {"current": 35, "maximum": 35}}
    WORN = {"name": "Bent Staff", "durability": {"current": 7, "maximum": 25}}
    SHIRT = {"name": "Apprentice's Shirt"}          # no durability key at all

    def test_captured_current_is_used(self):
        self.assertEqual(_durability(self.WORN, 25), 7)

    def test_a_full_item_stays_full(self):
        self.assertEqual(_durability(self.ROBE, 35), 35)

    def test_an_uncaptured_item_defaults_to_full_not_broken(self):
        """0 would mean broken gear, which is the one state we know it was not in."""
        self.assertEqual(_durability(self.SHIRT, 55), 55)

    def test_an_item_with_no_durability_stays_zero(self):
        """Shirts and food have MaxDurability 0, so the clamp lands on 0 naturally."""
        self.assertEqual(_durability(self.SHIRT, 0), 0)
        self.assertEqual(_durability({"name": "Hearthstone"}, 0), 0)

    def test_capture_is_clamped_to_the_target_template(self):
        """A fork with a lower MaxDurability must not receive an over-value."""
        self.assertEqual(_durability({"durability": {"current": 999}}, 25), 25)

    def test_the_old_flat_key_is_not_honoured(self):
        """durabilityCurrent never existed; treating it as data would mask the bug."""
        self.assertEqual(_durability({"durabilityCurrent": 12}, 40), 40)

    def test_junk_values_fall_back_rather_than_crash(self):
        for junk in ({"durability": "35"}, {"durability": {"current": "x"}},
                     {"durability": {"current": None}}, {"durability": []}):
            self.assertEqual(_durability(junk, 30), 30)

    def test_negative_capture_is_floored(self):
        self.assertEqual(_durability({"durability": {"current": -5}}, 30), 0)

    def test_captured_durability_reads_both_fields(self):
        self.assertEqual(_captured_durability(self.WORN, "current"), 7)
        self.assertEqual(_captured_durability(self.WORN, "maximum"), 25)
        self.assertIsNone(_captured_durability(self.SHIRT, "current"))

    def test_the_real_capture_that_exposed_the_bug(self):
        """Probethree's five equipped items, with the templates the realm holds."""
        real = [({"durability": {"current": 35, "maximum": 35}}, 35, 35),   # Robe
                ({"durability": {"current": 25, "maximum": 25}}, 25, 25),   # Pants
                ({"durability": {"current": 25, "maximum": 25}}, 25, 25),   # Bent Staff
                ({}, 0, 0),                                                   # Shirt
                ({}, 30, 30)]                                                 # Boots
        for record, template_max, expected in real:
            self.assertEqual(_durability(record, template_max), expected)


class GatingTests(unittest.TestCase):
    """Which failures stop a preview, and which stop a write.

    The distinction exists because a dry run touches nothing. Someone with a
    realm up should still be able to see what the import would do; they simply
    cannot commit it until they take the realm down.
    """

    REALM_UP = Check("realm stopped", FAIL, "worldserver.exe is running",
                     write_only=True)
    NO_ACCOUNT = Check("target account", FAIL, "no such account")

    def test_checks_block_writing_by_default(self):
        """write_only must be opt-in: a plain FAIL stops everything."""
        self.assertFalse(Check("x", FAIL, "d").write_only)
        self.assertTrue(blocks_planning([self.NO_ACCOUNT]))

    def test_a_running_realm_does_not_stop_a_preview(self):
        self.assertFalse(blocks_planning([self.REALM_UP]))

    def test_a_running_realm_does_stop_a_write(self):
        stoppers = blocks_writing([self.REALM_UP])
        self.assertEqual([c.name for c in stoppers], ["realm stopped"])

    def test_a_real_failure_stops_both(self):
        checks = [self.NO_ACCOUNT]
        self.assertTrue(blocks_planning(checks))
        self.assertEqual(len(blocks_writing(checks)), 1)

    def test_a_write_only_failure_does_not_mask_a_real_one(self):
        checks = [self.REALM_UP, self.NO_ACCOUNT]
        self.assertTrue(blocks_planning(checks))
        self.assertEqual(len(blocks_writing(checks)), 2)

    def test_passes_and_warnings_block_nothing(self):
        checks = [Check("a", OK, "fine"), Check("b", WARN, "noted")]
        self.assertFalse(blocks_planning(checks))
        self.assertEqual(blocks_writing(checks), [])

    def test_no_checks_at_all_blocks_nothing(self):
        self.assertFalse(blocks_planning([]))
        self.assertEqual(blocks_writing([]), [])


def column(data_type, column_type=None, length=None):
    """One information_schema row, shaped like table_columns() returns."""
    return {
        "DATA_TYPE": data_type,
        "COLUMN_TYPE": column_type if column_type is not None else data_type,
        "CHARACTER_MAXIMUM_LENGTH": length,
    }


class ColumnFitTests(unittest.TestCase):
    """A value that does not fit its column must be caught before the write.

    MySQL without STRICT in sql_mode clamps an out-of-range integer to the column
    maximum rather than refusing it. Two ids that clamp to the same ceiling then
    collide on the primary key and roll the whole transaction back, which reads
    as "the import silently did nothing".
    """

    def test_signed_limits(self):
        self.assertEqual(column_limits(column("tinyint")), (-128, 127))
        self.assertEqual(column_limits(column("smallint")), (-32768, 32767))
        self.assertEqual(column_limits(column("mediumint")), (-8388608, 8388607))
        self.assertEqual(column_limits(column("int")), (-2147483648, 2147483647))

    def test_unsigned_limits_come_from_column_type_not_data_type(self):
        """information_schema puts the signedness in COLUMN_TYPE only."""
        self.assertEqual(
            column_limits(column("smallint", "smallint(5) unsigned")), (0, 65535))
        self.assertEqual(
            column_limits(column("int", "int(10) unsigned")), (0, 4294967295))

    def test_non_integer_columns_have_no_limits(self):
        self.assertIsNone(column_limits(column("varchar", "varchar(12)", 12)))
        self.assertIsNone(column_limits(column("text", "text", 65535)))
        self.assertIsNone(column_limits(column("float")))

    def test_the_achievement_hazard(self):
        """The case this guard exists for: a fork's smallint achievement column.

        Ascension merges Challenge-of-Ascension ids past 65535 into
        Achievement.dbc, while character_achievement.achievement is smallint
        unsigned on a stock schema.
        """
        achievement = column("smallint", "smallint(5) unsigned")
        self.assertTrue(value_fits(achievement, 65535))
        self.assertFalse(value_fits(achievement, 65536))
        self.assertFalse(value_fits(achievement, 200000))

    def test_a_negative_value_never_fits_an_unsigned_column(self):
        self.assertFalse(value_fits(column("int", "int(10) unsigned"), -1))
        self.assertTrue(value_fits(column("smallint"), -1))  # signed, so fine

    def test_strings_are_measured_against_their_declared_length(self):
        name = column("varchar", "varchar(12)", 12)
        self.assertTrue(value_fits(name, "Probethree4"))
        self.assertTrue(value_fits(name, "Abcdefghijkl"))
        self.assertFalse(value_fits(name, "Abcdefghijklm"))

    def test_a_blob_column_with_no_declared_length_accepts_anything(self):
        self.assertTrue(value_fits(column("text", "text", None), "x" * 100000))

    def test_none_and_booleans_are_left_alone(self):
        """A bool is an int in Python; treating it as one here would be noise."""
        tiny = column("tinyint", "tinyint(3) unsigned")
        self.assertTrue(value_fits(tiny, None))
        self.assertTrue(value_fits(tiny, True))
        self.assertTrue(value_fits(tiny, False))

    def test_ascension_spell_ids_fit_an_int_column(self):
        """The 11xxxxx ids this fork uses are fine; only the narrow columns bite."""
        self.assertTrue(value_fits(column("int", "int(10) unsigned"), 1111078))
        self.assertFalse(value_fits(column("smallint", "smallint(5) unsigned"), 1111078))


class RunningServerTests(unittest.TestCase):
    """The running worldserver decides which config is the right one.

    This is the defect these tests exist for: auto-discovery found a
    `worldserver.conf` belonging to the realm next door. It had the same three
    database DSNs, so every write landed in the right schema and the run looked
    clean -- but its DataDir pointed at a different `Data/dbc`, so talents and
    spells were resolved against data this realm does not have. Seven talents
    and seven spells were reported as "skipped: ambiguous" and simply left out.
    """

    CONF = """
LoginDatabaseInfo     = "127.0.0.1;3306;u;p;{prefix}_auth"
WorldDatabaseInfo     = "127.0.0.1;3306;u;p;{prefix}_world"
CharacterDatabaseInfo = "127.0.0.1;3306;u;p;{prefix}_characters"
DataDir = "{data}"
"""

    def setUp(self):
        self.root = tempfile.mkdtemp(prefix="bms-running-")
        self.addCleanup(shutil.rmtree, self.root, ignore_errors=True)

    def config(self, name, prefix="asc", data="Data"):
        """A server config where discovery expects one, with its own Data/dbc."""
        home = os.path.join(self.root, name)
        os.makedirs(os.path.join(home, data, "dbc"), exist_ok=True)
        os.makedirs(os.path.join(home, "configs"), exist_ok=True)
        path = os.path.join(home, "configs", "worldserver.conf")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(self.CONF.format(
                prefix=prefix,
                data=os.path.join(home, data).replace("\\", "/")))
        return bms_config.read_config(path)

    def args(self, **overrides):
        base = dict(config=None, no_config=False)
        base.update(overrides)
        return argparse.Namespace(**base)

    # -- picking a config -------------------------------------------------

    def test_a_running_server_breaks_a_tie_discovery_will_not(self):
        """Two realms side by side is a refusal; one of them running is an answer."""
        for name in ("realm-a", "realm-b"):
            self.config(name)
        running = self.config("realm-a")
        chosen, origin = choose_config(self.args(), self.root, [running])
        self.assertEqual(chosen.path, running.path)
        self.assertEqual(origin, "running server")

    def test_two_running_servers_are_still_a_refusal(self):
        """A tie broken by a coin flip is the thing this tool must never do."""
        running = [self.config(name) for name in ("realm-a", "realm-b")]
        with self.assertRaises(Exception) as caught:
            choose_config(self.args(), self.root, running)
        self.assertIn("--config", str(caught.exception))

    def test_a_running_server_is_found_when_discovery_finds_nothing(self):
        """A config named something else, or kept outside configs/, is invisible."""
        running = self.config("odd-layout")
        empty = tempfile.mkdtemp(prefix="bms-empty-")
        self.addCleanup(shutil.rmtree, empty, ignore_errors=True)
        chosen, origin = choose_config(self.args(), empty, [running])
        self.assertEqual(chosen.path, running.path)
        self.assertEqual(origin, "running server")

    def test_an_explicit_config_is_never_overruled(self):
        """--config is the user saying which realm they mean. It wins."""
        mine = self.config("mine")
        running = self.config("theirs")
        chosen, origin = choose_config(self.args(config=mine.path), self.root, [running])
        self.assertEqual(chosen.path, mine.path)
        self.assertEqual(origin, "--config")

    # -- catching the wrong one -------------------------------------------

    def test_the_same_database_from_a_different_dbc_set_is_a_conflict(self):
        """The exact defect: right schema, wrong DataDir, no complaint."""
        chosen = self.config("neighbour", prefix="asc")
        running = self.config("live", prefix="asc")
        self.assertNotEqual(chosen.dbc_dir, running.dbc_dir)
        found = dbc_conflict("asc_characters", chosen.dbc_dir, [running])
        self.assertIsNotNone(found)
        self.assertEqual(found.path, running.path)

    def test_a_different_realm_running_is_not_a_conflict(self):
        """Machines host several realms; the other ones are none of our business."""
        chosen = self.config("ours", prefix="asc")
        running = self.config("theirs", prefix="acore")
        self.assertIsNone(dbc_conflict("asc_characters", chosen.dbc_dir, [running]))

    def test_pointing_dbc_dir_at_the_right_data_settles_it(self):
        """The check is on what will be read, not on where the setting came from."""
        running = self.config("live", prefix="asc")
        self.assertIsNone(dbc_conflict("asc_characters", running.dbc_dir, [running]))

    def test_the_same_directory_written_differently_is_not_a_conflict(self):
        """A user-typed --dbc-dir uses whatever slashes and case they felt like."""
        running = self.config("live", prefix="asc")
        typed = os.path.join(running.dbc_dir, ".")
        if os.name == "nt":
            typed = typed.replace("\\", "/").upper()
        self.assertIsNone(dbc_conflict("asc_characters", typed, [running]))

    @unittest.skipIf(os.name == "nt", "POSIX path case is significant")
    def test_a_different_case_directory_is_a_conflict_on_posix(self):
        running = self.config("live", prefix="asc")
        typed = running.dbc_dir.upper()
        self.assertIs(dbc_conflict("asc_characters", typed, [running]), running)

    def test_the_same_dbc_set_under_another_name_is_not_a_conflict(self):
        """Only a difference that changes the import is worth stopping for."""
        chosen = self.config("realm", prefix="asc")
        copy = os.path.join(self.root, "realm", "configs", "worldserver-bridge.conf")
        shutil.copy(chosen.path, copy)
        self.assertIsNone(dbc_conflict("asc_characters", chosen.dbc_dir,
                                       [bms_config.read_config(copy)]))

    def test_nothing_running_means_nothing_can_be_said(self):
        """With the realm stopped for --apply there is no process to ask."""
        self.assertIsNone(dbc_conflict("asc_characters", self.config("realm").dbc_dir, []))

    # -- reading a command line -------------------------------------------

    def test_a_windows_command_line_keeps_its_backslashes(self):
        """posix=True splitting would eat every one of them as an escape."""
        line = ('"C:\\Games\\wow-server\\bin\\worldserver.exe" '
                '-c "C:\\Games\\wow-server\\realms\\one\\worldserver.conf"')
        self.assertEqual(bms_config.config_from_command_line(line),
                         "C:\\Games\\wow-server\\realms\\one\\worldserver.conf")

    def test_the_config_flag_is_read_in_each_spelling(self):
        for flag in ("-c", "--config", "-config"):
            self.assertEqual(
                bms_config.config_from_command_line("worldserver %s /etc/w.conf" % flag),
                "/etc/w.conf", flag)
        self.assertEqual(
            bms_config.config_from_command_line("worldserver --config=/etc/w.conf"),
            "/etc/w.conf")

    def test_a_server_started_with_no_config_flag_yields_nothing(self):
        self.assertIsNone(bms_config.config_from_command_line("worldserver.exe"))
        self.assertIsNone(bms_config.config_from_command_line("worldserver.exe -c"))

    def test_a_realm_is_identified_by_its_character_schema(self):
        """Host is written a different way in every config that reaches it."""
        asc = self.config("a", prefix="asc")
        self.assertTrue(bms_config.serves_database(asc, "asc_characters"))
        self.assertTrue(bms_config.serves_database(asc, "ASC_CHARACTERS"))
        self.assertFalse(bms_config.serves_database(asc, "acore_characters"))
        self.assertFalse(bms_config.serves_database(asc, ""))


class StubResolvers:
    """Resolves the talents it was told about and refuses the rest."""

    def __init__(self, known, rank_spells=()):
        self.known = known                  # {(tab, name, rank): spell}
        self._rank_spells = set(rank_spells)

    def resolve_talent_tab(self, class_id, tab_name, tab_index):
        from bms_dbc import Resolution
        return Resolution(True, 1), False

    def talent_spell(self, class_id, tab_name, name, rank, tab_index=0):
        from bms_dbc import Resolution
        spell = self.known.get((tab_name, name, rank))
        if spell is None:
            return Resolution(False, reason="no talent named %r in this tab" % name)
        return Resolution(True, spell)

    def talent_rank_spells(self):
        return self._rank_spells


class TalentPolicyTests(unittest.TestCase):
    """A talent build has to land whole or not at all.

    Half a build is not a weaker build, it is a different one: the talents this
    server renamed or rebalanced away go missing while their points still look
    spent. Leaving the points free is recoverable in a way that is not.
    """

    CAPTURE = {
        "talentTabs": [{
            "name": "Fire", "tabIndex": 1,
            "talents": [
                {"name": "Ignite", "rank": 3},
                {"name": "Incineration", "rank": 2},
                {"name": "Burning Soul", "rank": 0},   # never spent
            ],
        }],
    }

    def plan_with(self, resolvers, mode):
        plan = Plan(account_id=1, guid=7, name="Probe")
        _plan_talents(plan, self.CAPTURE, 8, resolvers, mode)
        return plan

    def all_resolve(self):
        return StubResolvers({("Fire", "Ignite", 3): 1111119,
                              ("Fire", "Incineration", 2): 1118460})

    def one_missing(self):
        return StubResolvers({("Fire", "Ignite", 3): 1111119})

    def test_a_build_that_lands_whole_is_imported(self):
        plan = self.plan_with(self.all_resolve(), "auto")
        self.assertEqual(plan.count("character_talent"), 2)
        self.assertEqual(
            sorted(row["spell"] for row in plan.rows["character_talent"]),
            [1111119, 1118460])

    def test_a_rank_zero_talent_is_not_counted_as_spent(self):
        plan = self.plan_with(self.all_resolve(), "auto")
        self.assertEqual(plan.count("character_talent"), 2)

    def test_one_unresolved_talent_withdraws_the_whole_build(self):
        plan = self.plan_with(self.one_missing(), "auto")
        self.assertEqual(plan.count("character_talent"), 0)
        self.assertEqual(plan.count("character_spell"), 0)

    def test_the_withdrawal_says_how_many_points_are_free(self):
        plan = self.plan_with(self.one_missing(), "auto")
        note = " ".join(plan.notes)
        self.assertIn("NOT imported", note)
        self.assertIn("5 talent points", note)   # rank 3 + rank 2

    def test_every_talent_is_named_in_the_skips_when_withdrawn(self):
        """A count alone cannot be checked; the player needs the names."""
        plan = self.plan_with(self.one_missing(), "auto")
        listed = " ".join(skip.what for skip in plan.skips)
        self.assertIn("Ignite", listed)
        self.assertIn("Incineration", listed)

    def test_import_mode_keeps_a_partial_build_on_request(self):
        plan = self.plan_with(self.one_missing(), "import")
        self.assertEqual(plan.count("character_talent"), 1)
        self.assertIn("PARTIAL", " ".join(plan.notes))

    def test_rebuild_mode_withdraws_even_a_build_that_would_land(self):
        plan = self.plan_with(self.all_resolve(), "rebuild")
        self.assertEqual(plan.count("character_talent"), 0)
        self.assertIn("rebuild", " ".join(plan.notes))

    def test_a_withdrawn_build_does_not_leave_its_passives_learned(self):
        """The known-spell list can carry a talent's own passive."""
        resolvers = StubResolvers({("Fire", "Ignite", 3): 1111119},
                                  rank_spells={1111119, 1118460})
        plan = Plan(account_id=1, guid=7, name="Probe")
        plan.add("character_spell", {"guid": 7, "spell": 1118460, "specMask": 1})
        plan.add("character_spell", {"guid": 7, "spell": 133, "specMask": 1})
        _plan_talents(plan, self.CAPTURE, 8, resolvers, "auto")
        left = [row["spell"] for row in plan.rows["character_spell"]]
        self.assertEqual(left, [133])
        self.assertIn("Held back", " ".join(plan.notes))

    def test_an_ordinary_spell_is_not_held_back(self):
        resolvers = StubResolvers({("Fire", "Ignite", 3): 1111119},
                                  rank_spells={1111119})
        plan = Plan(account_id=1, guid=7, name="Probe")
        plan.add("character_spell", {"guid": 7, "spell": 133, "specMask": 1})
        _plan_talents(plan, self.CAPTURE, 8, resolvers, "auto")
        self.assertEqual([row["spell"] for row in plan.rows["character_spell"]], [133])


class AdvancementResolvers(StubResolvers):
    """Adds the spell/class lookups the advancement pass needs."""

    def __init__(self, spells, classes=None):
        super().__init__({})
        self._spells = dict(spells)          # {spell_id: name}
        self._classes = classes or {}        # {folded token: class id}

    def spell_exists(self, spell_id):
        return int(spell_id) in self._spells

    def spell_name(self, spell_id):
        return self._spells.get(int(spell_id), "")

    def class_by_token(self, token):
        from bms_dbc import Resolution
        key = "".join(c for c in str(token or "").casefold() if c.isalnum())
        if key in self._classes:
            return Resolution(True, self._classes[key])
        return Resolution(False, reason="no class named %r" % token)

    def class_name(self, class_id):
        return "class %d" % class_id


class ResolveClassTests(unittest.TestCase):
    """The target's ChrClasses.dbc knows what a fork calls its classes."""

    def test_the_targets_own_file_wins(self):
        res = AdvancementResolvers({}, {"starcaller": 26})
        self.assertEqual(resolve_class({"classToken": "STARCALLER"}, res), 26)

    def test_the_display_name_is_tried_when_the_token_fails(self):
        res = AdvancementResolvers({}, {"runemaster": 32})
        self.assertEqual(
            resolve_class({"classToken": "SPIRITMAGE", "className": "Runemaster"}, res), 32)

    def test_a_stock_class_still_resolves_without_dbcs(self):
        self.assertEqual(resolve_class({"classToken": "MAGE"}, None), 8)

    def test_a_custom_class_without_dbcs_keeps_the_clearer_message(self):
        import bms_map
        with self.assertRaises(bms_map.MappingError):
            resolve_class({"classToken": "TINKER"}, None)


class AdvancementTests(unittest.TestCase):
    """Conquest of Azeroth characters buy advancement entries, not talents.

    TalentTab.dbc stops at class 13, so such a character's captured talentTabs
    are empty. The server stores having an entry as knowing its spells, so
    restoring the spells is restoring the build.

    The entries below are the real shape, taken from a live Chronomancer export:
    C_CharacterAdvancement returns the realm's own node records, keyed `ID`, with
    a `Spells` list and the realm's own `Name`.
    """

    SPELLS = {706114: "Gift of the Infinite Dragonflight",
              707556: "Warpstriker", 707830: "Warpstriker",
              503825: "Anomaly Spikes", 504886: "Anomaly Spikes",
              805847: "Clasp of Infinity", 300250: "Moonstone Hilt"}

    WARPSTRIKER = {"ID": 7216, "Name": "Warpstriker", "Type": "Talent",
                   "Spells": [707556, 707830], "TECost": 1, "Class": "Chronomancer"}
    CLASP = {"ID": 30249, "Name": "Clasp of Infinity", "Type": "Ability",
             "Spells": [805847], "AECost": 1, "Class": "Chronomancer"}

    def capture(self, talents=(), abilities=(), spec=32, available=True, level=20):
        return {
            "level": level,
            "advancement": {
                "available": available,
                "activeSpecializationId": spec,
                "knownTalentEntries": list(talents),
                "knownSpellEntries": list(abilities),
            },
        }

    def plan_with(self, char, catalogue=None, already=(), class_id=22):
        plan = Plan(account_id=1, guid=7, name="Probe")
        for spell in already:
            plan.add("character_spell", {"guid": 7, "spell": spell, "specMask": 1})
        _plan_advancement(plan, char, class_id,
                          AdvancementResolvers(self.SPELLS), catalogue)
        return plan

    def test_a_purchase_the_spellbook_missed_is_restored(self):
        """A passive is not in the spellbook; 10 of a live character's 15 were not."""
        plan = self.plan_with(self.capture(talents=[self.CLASP]))
        self.assertEqual([r["spell"] for r in plan.rows["character_spell"]], [805847])

    def test_a_multi_spell_purchase_is_ranked_not_a_set(self):
        """The realm keeps exactly one spell of an entry -- its rank.

        Taking both silently buys a rank the character never had, and the
        realm then charges its essence twice.
        """
        plan = self.plan_with(self.capture(talents=[self.WARPSTRIKER]))
        self.assertEqual([r["spell"] for r in plan.rows["character_spell"]], [707556])

    def test_the_essence_total_can_raise_a_lone_candidate_to_its_real_rank(self):
        """One entry able to absorb the shortfall is an answer, not a guess."""
        char = self.capture(talents=[self.WARPSTRIKER])
        char["advancement"]["learnedTalentEssence"] = 2   # rank 1 accounts for 1
        plan = self.plan_with(char)
        self.assertEqual([r["spell"] for r in plan.rows["character_spell"]], [707830])
        self.assertIn("only purchase that can account", " ".join(plan.notes))

    def test_an_ambiguous_shortfall_is_reported_rather_than_guessed(self):
        spikes = {"ID": 6156, "Name": "Anomaly Spikes", "Type": "Talent",
                  "Spells": [503825, 504886], "TECost": 1}
        char = self.capture(talents=[self.WARPSTRIKER, spikes])
        char["advancement"]["learnedTalentEssence"] = 3   # rank 1 each accounts for 2
        plan = self.plan_with(char)
        self.assertEqual(sorted(r["spell"] for r in plan.rows["character_spell"]),
                         [503825, 707556])
        note = " ".join(plan.notes)
        self.assertIn("2 purchase(s) could explain", note)
        self.assertIn("keeps 1 talent essence", note)

    def test_a_spell_already_planned_is_not_written_twice(self):
        """character_spell is keyed on (guid, spell); a duplicate would fail the insert."""
        plan = self.plan_with(self.capture(talents=[self.WARPSTRIKER]), already=[707556])
        self.assertEqual([r["spell"] for r in plan.rows["character_spell"]], [707556])

    def test_a_repeated_spell_inside_one_entry_is_written_once(self):
        entry = dict(self.WARPSTRIKER, Spells=[707556, 707556])
        plan = self.plan_with(self.capture(talents=[entry]))
        self.assertEqual([r["spell"] for r in plan.rows["character_spell"]], [707556])

    def test_abilities_and_talents_are_both_restored(self):
        plan = self.plan_with(self.capture(talents=[self.WARPSTRIKER],
                                           abilities=[self.CLASP]))
        self.assertEqual(sorted(r["spell"] for r in plan.rows["character_spell"]),
                         [707556, 805847])

    def test_entries_and_spells_are_counted_separately(self):
        note = " ".join(self.plan_with(
            self.capture(talents=[self.WARPSTRIKER], abilities=[self.CLASP])).notes)
        self.assertIn("2 purchased entries restored", note)
        self.assertIn("teaching 2 spell(s)", note)

    def test_the_realms_own_name_is_used_rather_than_a_spell_name(self):
        """'Anomaly Spikes' beats whichever of its two spells got looked up."""
        entry = dict(self.WARPSTRIKER, ID=99, Spells=[999999])
        plan = self.plan_with(self.capture(talents=[entry]))
        self.assertIn("Warpstriker", " ".join(s.what for s in plan.skips))

    def test_the_short_harness_spelling_is_still_accepted(self):
        """A future client may return InternalID/SpellID instead."""
        plan = self.plan_with(self.capture(
            talents=[{"InternalID": 6770, "SpellID": 300250}]))
        self.assertEqual([r["spell"] for r in plan.rows["character_spell"]], [300250])

    def test_the_unrestorable_specialization_is_called_out(self):
        """The server keeps the active spec in memory only, so it cannot be written."""
        note = " ".join(self.plan_with(self.capture(talents=[self.CLASP])).notes)
        self.assertIn("specialization 32", note)
        self.assertIn("memory only", note)

    def test_a_spell_the_target_does_not_have_is_skipped_by_name(self):
        entry = dict(self.CLASP, Spells=[999999])
        plan = self.plan_with(self.capture(talents=[entry]))
        self.assertEqual(plan.count("character_spell"), 0)
        self.assertIn("999999", " ".join(s.what for s in plan.skips))

    def test_one_unknown_spell_does_not_lose_its_siblings(self):
        entry = dict(self.WARPSTRIKER, Spells=[707556, 999999])
        plan = self.plan_with(self.capture(talents=[entry]))
        self.assertEqual([r["spell"] for r in plan.rows["character_spell"]], [707556])
        self.assertIn("999999", " ".join(s.what for s in plan.skips))

    def test_an_entry_with_no_spell_is_skipped(self):
        plan = self.plan_with(self.capture(talents=[{"ID": 4043, "Name": "Empty"}]))
        self.assertEqual(plan.count("character_spell"), 0)
        self.assertIn("teaches no spell", " ".join(s.reason for s in plan.skips))

    def test_a_capture_without_the_api_does_nothing(self):
        plan = self.plan_with(self.capture(available=False, talents=[self.CLASP]))
        self.assertEqual(plan.count("character_spell"), 0)
        self.assertEqual(plan.notes, [])

    def test_a_stock_capture_is_untouched(self):
        plan = Plan(account_id=1, guid=7, name="Probe")
        _plan_advancement(plan, {"level": 20}, 8, AdvancementResolvers(self.SPELLS), None)
        self.assertEqual(plan.count("character_spell"), 0)

    def test_the_catalogue_refuses_an_entry_of_another_class(self):
        import bms_coa
        cat = bms_coa.Catalogue(entries=bms_coa.parse_json(
            '[{"EntryId": 30249, "ClassId": 12, "SpellIds": [805847]}]'))
        plan = self.plan_with(self.capture(talents=[self.CLASP]), catalogue=cat)
        self.assertEqual(plan.count("character_spell"), 0)
        self.assertIn("class 12", " ".join(s.reason for s in plan.skips))

    def test_the_catalogue_refuses_an_entry_above_the_level(self):
        import bms_coa
        cat = bms_coa.Catalogue(entries=bms_coa.parse_json(
            '[{"EntryId": 30249, "ClassId": 22, "RequiredLevel": 70, "SpellIds": [805847]}]'))
        plan = self.plan_with(self.capture(talents=[self.CLASP], level=20), catalogue=cat)
        self.assertEqual(plan.count("character_spell"), 0)
        self.assertIn("needs level 70", " ".join(s.reason for s in plan.skips))

    def test_an_entry_the_catalogue_has_never_heard_of_is_still_restored(self):
        """A catalogue older than the realm must not cost the player their build."""
        import bms_coa
        cat = bms_coa.Catalogue(entries=bms_coa.parse_json(
            '[{"EntryId": 1, "ClassId": 22, "SpellIds": [5]}]'))
        entry = dict(self.CLASP, ID=987654)
        plan = self.plan_with(self.capture(talents=[entry]), catalogue=cat)
        self.assertEqual([r["spell"] for r in plan.rows["character_spell"]], [805847])



if __name__ == "__main__":
    unittest.main(verbosity=2)
