#!/usr/bin/env python3
"""Offline importer: write a Bind My Soul checkpoint into an AzerothCore realm.

    python bms_import.py <BindMySoul.lua> --account MYACCT          # dry run
    python bms_import.py <BindMySoul.lua> --account MYACCT --apply   # write

Dry run is the default. Nothing is written until --apply, and even then only
after every pre-flight check has passed.

Design rules, in order of importance:

1.  The realm must be STOPPED. worldserver keeps an in-memory character cache
    (sCharacterCache / AddCharacterCacheEntry) and a live connection pool;
    inserting a character underneath it produces a character the server does
    not know exists. The importer refuses to write while the realm looks live.
2.  Credentials come from the environment or the command line and are never
    written to disk, never echoed, and never stored in the report.
3.  Nothing is guessed silently. Anything that cannot be resolved against the
    target server's own data is skipped and listed in the report.
4.  One transaction. Either the whole character lands or none of it does.

What this importer restores, and what it cannot:

    restored : identity, level, money, equipped items, bag/keyring contents,
               known spells, talents, skills, reputations, action bars,
               completed and active quests, completed achievements, homebind
    not kept : appearance (the client never exposes skin/face/hair after
               login -- AT_LOGIN_CUSTOMIZE is set so the player fixes it),
               world position (spawns at the race/class start point),
               titles (the addon's titles.known is unusable -- IsTitleKnown
               returns 0 for unknown and 0 is truthy in Lua, so every index
               reads as known), equipped bag containers (never captured),
               bank, guild, pets, mounts, glyphs (captured without ids),
               quest objective progress (accepted at zero by design)
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time
from dataclasses import dataclass, field
from typing import Any

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import bms_coa  # noqa: E402
import bms_config  # noqa: E402
import bms_map as M  # noqa: E402
from bms_bundle import (  # noqa: E402
    Bundle,
    BundleEntry,
    BundleError,
    is_bundle,
    read_bundle,
    render_bundle,
)
from bms_dbc import (  # noqa: E402
    FACTION_FLAG_AT_WAR,
    FACTION_FLAG_VISIBLE,
    DbcError,
    Resolvers,
)
from bms_equip import (  # noqa: E402
    FIRST_CUSTOM_CLASS,
    TEAM_BY_RACE,
    Wearer,
    equip_refusal,
    slot_name,
)
from bms_parse import (  # noqa: E402
    extract_checkpoint_codes,
    load_savedvariables,
    parse_checkpoint,
)

# -- AzerothCore constants ------------------------------------------------

TAXIMASK_SIZE = 14            # DBCStructure.h: TaxiMaskSize
EXPLORED_ZONES_SIZE = 128     # Player.h:73
KNOWN_TITLES_WORDS = 6        # Player.h:538, KNOWN_TITLES_SIZE * 2
EQUIPMENT_CACHE_SIZE = 38     # EQUIPMENT_SLOT_END * 2, on a stock core
EQUIPMENT_CACHE_MAX = 128     # anything wider than this is read as corruption

QUEST_STATUS_COMPLETE = 1     # QuestDef.h
QUEST_STATUS_INCOMPLETE = 3

ACTION_BUTTON_SPELL = 0x00    # Player.h:enum ActionButtonType
ACTION_BUTTON_MACRO = 0x40
ACTION_BUTTON_ITEM = 0x80
MAX_ACTION_BUTTONS = 144      # Player.h:261

MAIL_NORMAL = 0               # Mail.h
MAIL_STATIONERY_GM = 61
MAIL_CHECK_MASK_HAS_BODY = 0x10
MAIL_ITEMS_PER_MESSAGE = 12
MAIL_EXPIRY_DAYS = 90

# A value larger than any possible max is clamped on load
# (PlayerStorage.cpp:5616), so this restores the character at full health.
FULL_BAR = 100_000_000

# Player::Create writes both of these, and loading copies both columns back
# without validating them (PlayerStorage.cpp: SetByteValue(PLAYER_BYTES_2, 3,
# restState); SetInt32Value(PLAYER_FIELD_WATCHED_FACTION_INDEX, watchedFaction)).
# The schema default 0 is not a rest state at all; the client's MainMenuBar.lua
# looks it up at first login and errors.
REST_STATE_NOT_RAF_LINKED = 2       # Player.h: enum PlayerRestState
WATCHED_FACTION_NONE = 0xFFFFFFFF   # uint32(-1): no reputation bar

# mod-ascension-compat (AscensionCompat.cpp) keeps per-character state in
# character_settings and reads it back in OnPlayerLogin.
ASCENSION_ACTIVE_SPEC_SETTING = "core.ascension_active_spec"
ASCENSION_STARTER_SETTING = "core.ascension_starter"
ASCENSION_STARTER_REVISION = 1      # RepairStarterKit returns early once the value is >= 1


# -- report primitives -----------------------------------------------------

OK, WARN, FAIL = "ok", "warn", "fail"


@dataclass
class Check:
    name: str
    status: str
    detail: str
    # A FAIL that must stop --apply but not a read-only dry run.
    write_only: bool = False


def blocks_planning(checks: list[Check]) -> bool:
    """Can we still build a plan?

    A write-only failure -- the realm being up -- must not stop a dry run.
    Planning reads the database and writes nothing, and previewing the import
    before taking the realm down is the natural order to do this in.
    """
    return any(c.status == FAIL and not c.write_only for c in checks)


def blocks_writing(checks: list[Check]) -> list[Check]:
    """Every failure blocks a write, write-only ones included."""
    return [c for c in checks if c.status == FAIL]


@dataclass
class Skip:
    category: str
    what: str
    reason: str


@dataclass
class Plan:
    account_id: int = 0
    guid: int = 0
    name: str = ""
    renamed: bool = False
    rows: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    skips: list[Skip] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)

    def add(self, table: str, row: dict[str, Any]) -> None:
        self.rows.setdefault(table, []).append(row)

    def skip(self, category: str, what: str, reason: str) -> None:
        self.skips.append(Skip(category, what, reason))

    def count(self, table: str) -> int:
        return len(self.rows.get(table, []))


class ImportError_(Exception):
    """A condition that makes the import impossible."""


# -- database --------------------------------------------------------------

def connect(args: argparse.Namespace):
    try:
        import pymysql
    except ImportError as exc:  # pragma: no cover - environment dependent
        raise ImportError_(
            "pymysql is not installed. Install it with: python -m pip install pymysql"
        ) from exc

    password = args.password
    if password is None:
        password = os.environ.get(args.password_env)
    if password is None:
        raise ImportError_(
            f"No password. Set ${args.password_env} or pass --password. "
            "The importer never stores it."
        )
    try:
        return pymysql.connect(
            host=args.host,
            port=args.port,
            user=args.user,
            password=password,
            charset="utf8mb4",
            autocommit=False,
            cursorclass=pymysql.cursors.DictCursor,
        )
    except Exception as exc:
        raise ImportError_(f"Cannot connect to MySQL at {args.host}:{args.port}: {exc}") from exc


def query(conn, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    with conn.cursor() as cur:
        cur.execute(sql, params)
        return list(cur.fetchall())


def scalar(conn, sql: str, params: tuple = (), default: Any = None) -> Any:
    rows = query(conn, sql, params)
    if not rows:
        return default
    value = next(iter(rows[0].values()))
    return default if value is None else value


def table_columns(conn, schema: str, table: str) -> dict[str, dict[str, Any]]:
    rows = query(
        conn,
        "SELECT COLUMN_NAME, IS_NULLABLE, COLUMN_DEFAULT, DATA_TYPE, EXTRA, "
        "COLUMN_TYPE, CHARACTER_MAXIMUM_LENGTH "
        "FROM information_schema.COLUMNS WHERE TABLE_SCHEMA=%s AND TABLE_NAME=%s",
        (schema, table),
    )
    return {r["COLUMN_NAME"]: r for r in rows}


# (signed low, signed high, unsigned high) for every MySQL integer type.
INT_RANGES = {
    "tinyint": (-128, 127, 255),
    "smallint": (-32768, 32767, 65535),
    "mediumint": (-8388608, 8388607, 16777215),
    "int": (-2147483648, 2147483647, 4294967295),
    "bigint": (-9223372036854775808, 9223372036854775807, 18446744073709551615),
}


def column_limits(meta: dict[str, Any]) -> tuple[int, int] | None:
    """What an integer column can actually hold, or None if it is not one."""
    data_type = str(meta.get("DATA_TYPE") or "").lower()
    if data_type not in INT_RANGES:
        return None
    low, high, unsigned_high = INT_RANGES[data_type]
    if "unsigned" in str(meta.get("COLUMN_TYPE") or "").lower():
        return 0, unsigned_high
    return low, high


def value_fits(meta: dict[str, Any], value: Any) -> bool:
    """Would MySQL store this value as given, or quietly mangle it?

    A server without STRICT in sql_mode clamps an out-of-range integer to the
    column maximum and truncates an over-long string instead of refusing it.
    That turns a bad id into a duplicate-key rollback with no useful message,
    so the value is checked here rather than discovered mid-transaction.
    """
    if isinstance(value, bool) or value is None:
        return True
    if isinstance(value, int):
        limits = column_limits(meta)
        return limits is None or limits[0] <= value <= limits[1]
    if isinstance(value, str):
        length = meta.get("CHARACTER_MAXIMUM_LENGTH")
        return not length or len(value) <= int(length)
    return True


def fill_required_columns(columns: dict[str, dict[str, Any]], row: dict[str, Any]) -> dict[str, Any]:
    """Add a neutral value for every NOT NULL column that has no default.

    Forks add columns (this realm's `characters` has innTriggerId with no
    default), and under STRICT_TRANS_TABLES an omitted column like that is a
    hard error. Filling them from the live schema keeps the importer portable
    instead of pinned to one server's DDL.
    """
    complete = dict(row)
    for name, meta in columns.items():
        if name in complete:
            continue
        if meta["IS_NULLABLE"] == "YES" or meta["COLUMN_DEFAULT"] is not None:
            continue
        if "auto_increment" in (meta["EXTRA"] or ""):
            continue
        text_like = meta["DATA_TYPE"] in {
            "char", "varchar", "text", "tinytext", "mediumtext", "longtext",
            "blob", "binary", "varbinary", "json",
        }
        complete[name] = "" if text_like else 0
    return complete


def insert(conn, schema: str, table: str, row: dict[str, Any]) -> None:
    cols = ", ".join("`%s`" % c for c in row)
    marks = ", ".join(["%s"] * len(row))
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO `%s`.`%s` (%s) VALUES (%s)" % (schema, table, cols, marks),
            tuple(row.values()),
        )


# -- pre-flight ------------------------------------------------------------

def realm_looks_live(conn, characters_db: str,
                     running: list[tuple[str, Any]] | None = None) -> tuple[bool, str]:
    """Two independent signals that a worldserver is attached to THIS database.

    Reading each running server's own config is what makes this specific. A
    machine hosting several realms always has a worldserver running somewhere,
    and treating that as "the realm is up" would mean every import demands that
    every unrelated realm be stopped. A server whose config names a different
    character schema is reported and then set aside; anything we could not read
    counts against us, because an unidentified worldserver might be this one.
    """
    if running is None:
        running = bms_config.running_servers()
    reasons = []
    aside = []
    try:
        online = scalar(
            conn, "SELECT COUNT(*) FROM `%s`.characters WHERE online <> 0" % characters_db, (), 0
        )
        if online:
            reasons.append(f"{online} character(s) flagged online in {characters_db}")
    except Exception:
        pass
    for _line, config in running:
        if config is None:
            reasons.append("a worldserver is running and did not name a config we could "
                           "read, so it may be this realm")
        elif bms_config.serves_database(config, characters_db):
            reasons.append("a worldserver is running on %s (%s)"
                           % (characters_db, config.path))
        else:
            aside.append(config.characters.database if config.characters else config.path)
    if not reasons and aside:
        return False, "worldservers running, but on %s, not %s" % (", ".join(aside),
                                                                   characters_db)
    return bool(reasons), "; ".join(reasons)


def preflight(conn, args: argparse.Namespace, checkpoint, resolvers: Resolvers | None,
              entry: BundleEntry | None = None) -> list[Check]:
    checks: list[Check] = []
    char = checkpoint.character

    # 1. realm must be stopped
    live, why = realm_looks_live(conn, args.characters_db,
                                 getattr(args, "running_servers", None))
    if live and not args.allow_online:
        checks.append(Check(
            "realm stopped", FAIL,
            f"{why}. worldserver caches characters in memory (sCharacterCache), so a "
            "character inserted now would be invisible to it and may be overwritten. "
            "Stop the realm, or pass --allow-online if you accept that.",
            write_only=True,
        ))
    elif live:
        checks.append(Check("realm stopped", WARN, f"{why} -- overridden with --allow-online."))
    else:
        checks.append(Check("realm stopped", OK, why or "no worldserver detected"))

    # 2. schemas exist
    for label, schema in (("auth", args.auth_db), ("characters", args.characters_db),
                          ("world", args.world_db)):
        found = scalar(conn, "SELECT SCHEMA_NAME FROM information_schema.SCHEMATA "
                             "WHERE SCHEMA_NAME=%s", (schema,))
        checks.append(Check(f"{label} database", OK if found else FAIL,
                            schema if found else f"{schema} does not exist on this server"))

    # 3. account
    account = query(conn, "SELECT id, username FROM `%s`.account WHERE username=%%s"
                    % args.auth_db, (args.account.upper(),))
    if not account:
        account = query(conn, "SELECT id, username FROM `%s`.account WHERE username=%%s"
                        % args.auth_db, (args.account,))
    if account:
        checks.append(Check("target account", OK,
                            "%s (id %d)" % (account[0]["username"], account[0]["id"])))
    else:
        checks.append(Check("target account", FAIL,
                            f"no account named {args.account!r} in {args.auth_db}"))

    # 4. realm identity -- the offline stand-in for an in-game import command
    realms = query(conn, "SELECT id, name, address, port FROM `%s`.realmlist" % args.auth_db)
    captured = str(char.get("realmName") or "")
    names = [r["name"] for r in realms]
    if not realms:
        checks.append(Check("realm identity", WARN, "realmlist is empty; cannot compare realms"))
    elif captured in names:
        checks.append(Check("realm identity", OK,
                            f"checkpoint realm {captured!r} matches a realm on this server"))
    else:
        checks.append(Check(
            "realm identity", WARN,
            f"checkpoint was taken on {captured!r}; this server offers "
            + ", ".join(repr(n) for n in names)
            + ". That is expected when importing to your own realm -- confirm it is "
              "the realm you mean before applying.",
        ))

    # 4b. a bundle's index must describe the checkpoint it actually carries
    if entry is not None:
        disagreements = []
        if (entry.character_name
                and entry.character_name.lower() != str(char.get("name") or "").lower()):
            disagreements.append("names it %r, the checkpoint says %r"
                                 % (entry.character_name, char.get("name")))
        if entry.realm_name and entry.realm_name != captured:
            disagreements.append("places it on %r, the checkpoint says %r"
                                 % (entry.realm_name, captured))
        if disagreements:
            checks.append(Check(
                "bundle index", WARN,
                "the bundle index disagrees with the checkpoint inside it: "
                + "; ".join(disagreements)
                + ". The checkpoint itself is what gets imported.",
            ))
        else:
            checks.append(Check("bundle index", OK,
                                "matches the checkpoint it carries"))

    # 5. identity mappable
    try:
        race = M.race_id(char.get("raceToken"))
        klass = resolve_class(char, resolvers)
        named = (resolvers.class_name(klass) if resolvers else "") or char.get("classToken")
        checks.append(Check("race / class", OK,
                            "%s (%d) / %s (%d)" % (char.get("raceToken"), race,
                                                   named, klass)))
    except M.MappingError as exc:
        checks.append(Check("race / class", FAIL, str(exc)))
        race = klass = 0

    # 6. start position exists for that race/class
    if race and klass:
        start = query(conn, "SELECT map, zone, position_x, position_y, position_z, orientation "
                            "FROM `%s`.playercreateinfo WHERE race=%%s AND class=%%s"
                      % args.world_db, (race, klass))
        checks.append(Check("start position", OK if start else FAIL,
                            "map %d zone %d" % (start[0]["map"], start[0]["zone"]) if start
                            else f"{args.world_db}.playercreateinfo has no row for "
                                 f"race {race} class {klass}"))

    # 7. name availability
    desired = args.name or str(char.get("name") or "")
    taken = {r["name"] for r in query(
        conn, "SELECT name FROM `%s`.characters" % args.characters_db)}
    try:
        final, renamed = M.allocate_name(desired, taken)
        checks.append(Check("character name", WARN if renamed else OK,
                            f"{desired!r} is taken; importing as {final!r} with a rename "
                            "prompt at login" if renamed else f"{final!r} is free"))
    except M.MappingError as exc:
        checks.append(Check("character name", FAIL, str(exc)))

    # 8. DBC resolvers
    if resolvers is None:
        checks.append(Check("DBC resolvers", FAIL,
                            f"--dbc-dir {args.dbc_dir!r} is not usable"))
    else:
        missing = [n for n, present in resolvers.available().items() if not present]
        # The directory and its row counts go in the detail whether or not
        # anything is wrong: which DBC set was read is the single fact this
        # report used to omit, and the one that made a wrong run look right.
        where = "%s (%s)" % (resolvers.dbc_dir, resolvers.fingerprint())
        checks.append(Check("DBC resolvers", WARN if missing else OK,
                            "missing: %s -- %s" % (", ".join(missing), where) if missing
                            else where))

    # 9. is a worldserver on this database running a different DBC set?
    conflict = getattr(args, "config_conflict", None)
    if conflict is not None:
        checks.append(Check(
            "DBC set", FAIL,
            "the worldserver running on %s (%s) reads its DBCs from %s, but this run is "
            "reading %s. Those are different data, so talents, spells, skills and "
            "factions would be resolved against files this realm does not use -- and "
            "whatever failed to resolve would be reported as skipped rather than as "
            "wrong. Re-run with --config \"%s\", or --dbc-dir \"%s\" if you mean it."
            % (args.characters_db, conflict.path, conflict.dbc_dir, args.dbc_dir,
               conflict.path, conflict.dbc_dir)))

    return checks


# -- plan building ---------------------------------------------------------

def blob(count: int, value: int = 0) -> str:
    return " ".join([str(value)] * count)


def _items_from(checkpoint) -> tuple[list[dict], list[dict]]:
    char = checkpoint.character
    return list(char.get("equipment") or []), list(char.get("bags") or [])


def build_plan(conn, args: argparse.Namespace, checkpoint, resolvers: Resolvers) -> Plan:
    char = checkpoint.character
    plan = Plan()

    account = query(conn, "SELECT id FROM `%s`.account WHERE username IN (%%s, %%s)"
                    % args.auth_db, (args.account.upper(), args.account))
    plan.account_id = account[0]["id"] if account else 0

    race = M.race_id(char.get("raceToken"))
    klass = resolve_class(char, resolvers)
    gender = M.gender_id(char.get("sexId"))

    taken = {r["name"] for r in query(
        conn, "SELECT name FROM `%s`.characters" % args.characters_db)}
    plan.name, plan.renamed = M.allocate_name(args.name or str(char.get("name") or ""), taken)

    plan.guid = int(scalar(conn, "SELECT MAX(guid) FROM `%s`.characters" % args.characters_db,
                           (), 0) or 0) + 1
    next_item_guid = int(scalar(conn, "SELECT MAX(guid) FROM `%s`.item_instance"
                                % args.characters_db, (), 0) or 0) + 1

    start = query(conn, "SELECT map, zone, position_x, position_y, position_z, orientation "
                        "FROM `%s`.playercreateinfo WHERE race=%%s AND class=%%s"
                  % args.world_db, (race, klass))
    if not start:
        raise ImportError_(f"No playercreateinfo row for race {race} class {klass}.")
    spawn = start[0]

    # -- characters -------------------------------------------------------
    cache_width = equipment_cache_width(conn, args)
    level = max(1, min(255, int(char.get("level") or 1)))
    plan.add("characters", {
        "guid": plan.guid,
        "account": plan.account_id,
        "name": plan.name,
        "race": race,
        "class": klass,
        "gender": gender,
        "level": level,
        "xp": max(0, int(char.get("experience") or 0)),
        "money": max(0, int(char.get("money") or 0)),
        "position_x": spawn["position_x"],
        "position_y": spawn["position_y"],
        "position_z": spawn["position_z"],
        "map": spawn["map"],
        "orientation": spawn["orientation"],
        "zone": spawn["zone"],
        "taximask": blob(TAXIMASK_SIZE),
        "online": 0,
        "cinematic": 1,           # skip the intro movie for an imported character
        "totaltime": 0,
        "leveltime": 0,
        "logout_time": int(time.time()),
        "at_login": M.at_login_flags(plan.renamed, customize=True),
        "restState": REST_STATE_NOT_RAF_LINKED,
        "watchedFaction": WATCHED_FACTION_NONE,
        "health": FULL_BAR,
        "power1": FULL_BAR,
        "power2": FULL_BAR,
        "power3": FULL_BAR,
        "power4": FULL_BAR,
        "power5": FULL_BAR,
        "power6": FULL_BAR,
        "power7": FULL_BAR,
        "talentGroupsCount": 1,
        "activeTalentGroup": 0,
        "exploredZones": blob(EXPLORED_ZONES_SIZE),
        "equipmentCache": blob(cache_width),
        "knownTitles": blob(KNOWN_TITLES_WORDS),
        "chosenTitle": 0,
        "actionBars": 0,
        "totalKills": max(0, int((char.get("pvpProgress") or {}).get("lifetimeHonorableKills") or 0)),
        "totalHonorPoints": max(0, int((char.get("pvpProgress") or {}).get("honor") or 0)),
        "arenaPoints": max(0, int((char.get("pvpProgress") or {}).get("arena") or 0)),
    })

    plan.add("character_homebind", {
        "guid": plan.guid,
        "mapId": spawn["map"],
        "zoneId": spawn["zone"],
        "posX": spawn["position_x"],
        "posY": spawn["position_y"],
        "posZ": spawn["position_z"],
    })

    plan.notes.append(
        "Spawning at the race/class start point (map %d, zone %d): the checkpoint records "
        "only a bind *zone name* (%r), never coordinates."
        % (spawn["map"], spawn["zone"], char.get("bindLocation"))
    )
    plan.notes.append(
        "Appearance is not in the checkpoint -- the 3.3.5a client never exposes "
        "skin/face/hair after login. AT_LOGIN_CUSTOMIZE is set so the player is walked "
        "through the barber-shop screen at first login."
    )
    titles = char.get("titles") or {}
    if titles.get("known"):
        plan.skip("titles", "%s reported known" % _describe(titles.get("known")),
                  "the addon's title scan is broken (IsTitleKnown returns 0 for unknown and "
                  "0 is truthy in Lua), so every index reads as known; restoring them would "
                  "grant titles the character never earned")

    # -- spells, talents, skills -----------------------------------------
    _plan_spells(plan, char, resolvers)
    _plan_talents(plan, char, klass, resolvers, getattr(args, "talents", "auto"))
    _plan_advancement(plan, char, klass, resolvers, getattr(args, "coa_catalogue", None))
    _plan_skills(plan, char, resolvers)
    _plan_reputations(plan, char, race, klass, resolvers)
    _plan_settings(plan, char, klass, getattr(args, "coa_catalogue", None),
                   getattr(args, "starter_kit", False))

    # -- items ------------------------------------------------------------
    # After spells, skills and reputations: the server loads those before the
    # inventory and checks every equipped item against them.
    wearer = _wearer(plan, char, level, race, klass, resolvers)
    next_item_guid = _plan_items(conn, args, plan, checkpoint, next_item_guid, wearer)
    # Must follow _plan_items: the cache is derived from the rows it created.
    plan.rows["characters"][0]["equipmentCache"] = _equipment_cache(plan, cache_width)

    _plan_action_bars(plan, char)
    _plan_quests(conn, args, plan, char)
    _plan_achievements(plan, char)

    for section, reason in (
        ("glyphs", "the addon captures socket index and type but no glyph name or spell id, "
                   "so there is nothing to resolve against GlyphProperties.dbc"),
        ("companions", "mounts and critters are stored per-account server-side; the checkpoint "
                       "has no ids for them"),
        ("professionRecipes", "captured only for profession windows the player had open "
                              "(captureComplete is false)"),
        ("currencies", "no currency ids are captured"),
    ):
        value = char.get(section)
        populated = bool(value) and value != [] and value != {}
        if populated:
            plan.skip(section, _describe(value), reason)

    return plan


def equipment_cache_width(conn, args: argparse.Namespace) -> int:
    """Ask the target realm how wide its characters.equipmentCache is.

    A stock core has EQUIPMENT_SLOT_END = 19, so 38 values -- but forks add
    equipment slots, and the width is not negotiable: the core reads the blob
    as fixed-size pairs, so a cache of the wrong length is read out of
    alignment. The Ascension fork this was first run against writes 46 (23
    slots), which is why this is measured rather than assumed.

    Existing characters are the only honest source, so sample them and take the
    most common width. Ties go to the wider value, since a short cache is the
    failure mode we are guarding against -- including our own earlier rows.
    """
    counts: dict[int, int] = {}
    for row in query(conn, "SELECT equipmentCache FROM `%s`.characters "
                           "WHERE equipmentCache IS NOT NULL AND equipmentCache <> '' "
                           "LIMIT 50" % args.characters_db):
        width = len(str(row["equipmentCache"]).split())
        if width % 2 == 0 and EQUIPMENT_CACHE_SIZE <= width <= EQUIPMENT_CACHE_MAX:
            counts[width] = counts.get(width, 0) + 1
    if not counts:
        # A realm with no characters yet; the stock layout is the only guess left.
        return EQUIPMENT_CACHE_SIZE
    return max(counts, key=lambda w: (counts[w], w))


def _equipment_cache(plan: Plan, width: int = EQUIPMENT_CACHE_SIZE) -> str:
    """Rebuild characters.equipmentCache from the rows the plan actually holds.

    The character-selection screen renders a character from this field alone --
    not from character_inventory -- so leaving it zeroed shows a fully equipped
    character as naked in the list, and only dressing them once they log in.

    The layout is pairs of (item entry, permanent enchant id), which is what
    Player::SaveToDB writes out of PLAYER_VISIBLE_ITEM_*; `width` comes from
    equipment_cache_width() because forks change how many pairs there are.
    Deriving the contents from the planned rows rather than from the checkpoint
    keeps it honest: an item that got skipped, or relocated out of an equipment
    slot, cannot linger in the cache.
    """
    by_guid = {row["guid"]: row for row in plan.rows.get("item_instance", [])}
    values = [0] * width
    for row in plan.rows.get("character_inventory", []):
        slot = int(row["slot"])
        if slot >= M.EQUIPMENT_SLOT_END:
            continue
        item = by_guid.get(row["item"])
        if item is None:
            continue
        enchantments = str(item.get("enchantments") or "").split()
        values[slot * 2] = int(item["itemEntry"])
        # Field 0 of the enchantment blob is PERM_ENCHANTMENT_SLOT.
        values[slot * 2 + 1] = int(enchantments[0]) if enchantments else 0
    return " ".join(str(value) for value in values)


def _describe(value: Any) -> str:
    if isinstance(value, list):
        return "%d entries" % len(value)
    if isinstance(value, dict):
        for key in ("occupiedCount", "completedCount", "knownCount", "count"):
            if key in value:
                return "%s %s" % (value[key], key)
        return "%d fields" % len(value)
    return str(value)[:60]


def _plan_items(conn, args, plan: Plan, checkpoint, next_guid: int,
                wearer: Wearer | None = None) -> int:
    """Create item_instance + character_inventory rows, mailing what will not fit.

    With a `wearer`, each equipped item is first checked the way the server
    checks it at login (bms_equip.equip_refusal). One the character cannot wear
    yet goes in the backpack instead. Left equipped, the server would take it off
    and mail it, and a Conquest of Azeroth realm would put starter gear in the gap.
    """
    equipment, bags = _items_from(checkpoint)

    wanted: set[int] = set()
    parsed: list[tuple[dict, M.ItemFields]] = []
    for record in equipment + bags:
        try:
            fields = M.parse_item_fields(record)
        except M.MappingError as exc:
            plan.skip("items", str(record.get("name") or "?"), str(exc))
            continue
        parsed.append((record, fields))
        wanted.add(fields.item_id)

    known: set[int] = set()
    max_durability: dict[int, int] = {}
    templates: dict[int, dict[str, Any]] = {}
    if wanted:
        marks = ", ".join(["%s"] * len(wanted))
        # Every column rather than the dozen the equip check reads: a fork that
        # lacks one then falls back to its schema default instead of failing
        # the whole query.
        for r in query(
            conn,
            "SELECT * FROM `%s`.item_template WHERE entry IN (%s)"
            % (args.world_db, marks),
            tuple(sorted(wanted)),
        ):
            known.add(r["entry"])
            templates[r["entry"]] = r
            max_durability[r["entry"]] = max(0, int(r.get("MaxDurability") or 0))
    missing = wanted - known
    if missing and args.synthesize:
        _plan_synthetic_items(conn, args, plan, parsed, missing)
        known |= missing
        for row in plan.rows.get("item_template", []):
            templates.setdefault(row["entry"], row)
        for record, fields in parsed:
            if fields.item_id in missing:
                max_durability[fields.item_id] = _captured_durability(record, "maximum") or 0

    # Placement pass. Backpack and keyring keep their captured slots. Two kinds of
    # item are relocated into free backpack slots, then mailed: equipped items the
    # character cannot wear yet, and items that lived inside an equipped bag (the
    # addon never captures the bag itself, so they have no container).
    used_slots: set[int] = set()
    placements: list[tuple[dict, M.ItemFields, int | None]] = []
    unwearable: list[tuple[dict, M.ItemFields, int, str]] = []
    homeless: list[tuple[dict, M.ItemFields]] = []

    for record, fields in parsed:
        if fields.item_id not in known:
            plan.skip("items", "%s (%d)" % (record.get("name") or "?", fields.item_id),
                      f"not in {args.world_db}.item_template on this server"
                      + ("" if args.synthesize else "; re-run with --synthesize to create a "
                                                    "placeholder row"))
            continue
        if record.get("location") == "equipped":
            try:
                slot = M.equipped_db_slot(record.get("slotId"))
            except M.MappingError as exc:
                plan.skip("items", str(record.get("name") or "?"), str(exc))
                continue
            refusal = (equip_refusal(templates.get(fields.item_id, {}), slot, wearer)
                       if wearer is not None else "")
            if refusal:
                unwearable.append((record, fields, slot, refusal))
                continue
            used_slots.add(slot)
            placements.append((record, fields, slot))
            continue
        try:
            container, slot = M.split_bag_placement(record)
        except M.MappingError as exc:
            plan.skip("items", str(record.get("name") or "?"), str(exc))
            continue
        if container == 0:
            used_slots.add(slot)
            placements.append((record, fields, slot))
        else:
            homeless.append((record, fields))

    free_backpack = [
        s for s in range(M.INVENTORY_SLOT_ITEM_START, M.INVENTORY_SLOT_ITEM_END)
        if s not in used_slots
    ]
    mailed: list[tuple[dict, M.ItemFields]] = []
    landed: dict[int, int | None] = {}     # id(record) -> backpack slot, or None when mailed
    # Unwearable gear first: it was on the character, so it gets the backpack
    # before the contents of bags that no longer exist.
    for record, fields in [(r, f) for r, f, _slot, _why in unwearable] + homeless:
        if free_backpack:
            slot = free_backpack.pop(0)
            used_slots.add(slot)
            placements.append((record, fields, slot))
        else:
            slot = None
            mailed.append((record, fields))
            placements.append((record, fields, None))
        landed[id(record)] = slot

    for record, fields, slot, refusal in unwearable:
        where = landed[id(record)]
        plan.notes.append(
            "Not worn: %s (%d) from the %s slot -- %s. The server would take it off at "
            "login, so it goes %s instead; the player can equip it once they qualify."
            % (record.get("name") or "?", fields.item_id, slot_name(slot), refusal,
               "in the mail" if where is None
               else "in backpack slot %d" % (where - M.INVENTORY_SLOT_ITEM_START + 1))
        )
    if homeless:
        spilled = any(landed[id(record)] is None for record, _fields in homeless)
        plan.notes.append(
            "%d item(s) were inside equipped bags. The addon never captures the bag "
            "containers themselves (Core.lua scans equipment slots 1..19 only), so they "
            "were moved into free backpack slots%s."
            % (len(homeless), " and, where the backpack ran out, into mail" if spilled else "")
        )

    for record, fields, slot in placements:
        guid = next_guid
        next_guid += 1
        plan.add("item_instance", {
            "guid": guid,
            "itemEntry": fields.item_id,
            "owner_guid": plan.guid,
            "creatorGuid": 0,
            "giftCreatorGuid": 0,
            "count": max(1, int(record.get("stackCount") or 1)),
            "duration": 0,
            "charges": "0 0 0 0 0",
            "flags": 0,
            "enchantments": M.enchantments_blob(fields),
            "randomPropertyId": fields.suffix_id,
            "durability": _durability(record, max_durability.get(fields.item_id, 0)),
            "playedTime": 0,
            "text": "",
        })
        if slot is not None:
            plan.add("character_inventory", {
                "guid": plan.guid,
                "bag": 0,
                "slot": slot,
                "item": guid,
            })
        else:
            record["_mail_guid"] = guid

    if mailed:
        _plan_mail(plan, mailed)
    return next_guid


def _captured_durability(record: dict[str, Any], key: str) -> int | None:
    """Read record["durability"][key], or None when the addon captured nothing.

    The addon writes {"current": n, "maximum": n} and omits the key entirely for
    items that have no durability at all (shirts, food, a hearthstone).
    """
    captured = record.get("durability")
    if not isinstance(captured, dict):
        return None
    value = captured.get(key)
    if value is None:
        return None
    try:
        return max(0, int(value))
    except (TypeError, ValueError):
        return None


def _durability(record: dict[str, Any], max_durability: int) -> int:
    """Current durability for an imported item, clamped to the target template.

    Two things went wrong here before. The addon nests durability under a
    "durability" object, but this read a flat "durabilityCurrent" key that has
    never existed, so every item imported at 0 -- fully equipped and entirely
    broken. And 0 is the worst available default: the character was wearing and
    using the gear, so "broken" is the one state we can be confident it was not
    in. When nothing was captured, fall back to the template maximum, which is
    also what AzerothCore gives a freshly created item.

    Items with no durability clamp to 0 naturally, because their template
    MaxDurability is 0.
    """
    current = _captured_durability(record, "current")
    if current is None:
        return max_durability
    return min(current, max_durability)


def _plan_synthetic_items(conn, args, plan: Plan, parsed, missing: set[int]) -> None:
    """Create placeholder item_template rows for items this server has never seen."""
    columns = table_columns(conn, args.world_db, "item_template")
    by_id = {f.item_id: (r, f) for r, f in parsed}
    for entry in sorted(missing):
        record, fields = by_id[entry]
        row = fill_required_columns(columns, {
            "entry": entry,
            "name": str(record.get("name") or "Imported item %d" % entry)[:255],
            "Quality": max(0, int(record.get("quality") or 0)),
            "ItemLevel": max(0, int(record.get("itemLevel") or 0)),
            "RequiredLevel": max(0, int(record.get("requiredLevel") or 0)),
            "stackable": 1,
            "maxcount": 0,
            "MaxDurability": _captured_durability(record, "maximum") or 0,
        })
        plan.add("item_template", row)
        plan.notes.append(
            "Synthesised a PLACEHOLDER item_template row for %d (%r): it has no model, "
            "no stats and no icon. It exists so the item is not lost, not so it works."
            % (entry, record.get("name"))
        )


def _plan_mail(plan: Plan, mailed: list[tuple[dict, Any]]) -> None:
    now = int(time.time())
    expire = now + MAIL_EXPIRY_DAYS * 86400
    for start in range(0, len(mailed), MAIL_ITEMS_PER_MESSAGE):
        chunk = mailed[start:start + MAIL_ITEMS_PER_MESSAGE]
        mail_id = plan.count("mail") + 1  # renumbered against the live table on apply
        plan.add("mail", {
            "id": mail_id,
            "messageType": MAIL_NORMAL,
            "stationery": MAIL_STATIONERY_GM,
            "mailTemplateId": 0,
            "sender": 0,
            "receiver": plan.guid,
            "subject": "Restored belongings",
            "body": "These items could not be placed on your character when it was "
                    "restored: they were inside bags that could not be recovered, or they "
                    "are gear your character cannot wear yet. They were sent to you instead.",
            "has_items": 1,
            "expire_time": expire,
            "deliver_time": now,
            "money": 0,
            "cod": 0,
            "checked": MAIL_CHECK_MASK_HAS_BODY,
        })
        for record, _fields in chunk:
            plan.add("mail_items", {
                "mail_id": mail_id,
                "item_guid": record["_mail_guid"],
                "receiver": plan.guid,
            })


def resolve_class(char: dict, resolvers: Resolvers | None) -> int:
    """Class id for this checkpoint on this server.

    The target's own ChrClasses.dbc is asked first, because it is the only thing
    that knows what a fork's classes are called: Ascension names class 32
    "Runemaster" while UnitClass() returns SPIRITMAGE, and 7 of its 21 custom
    classes disagree that way. The stock table stays as the fallback for a run
    without DBCs, and keeps its clearer message for a custom class that the
    target genuinely cannot represent.
    """
    token = char.get("classToken")
    if resolvers is not None:
        found = resolvers.class_by_token(token)
        if found.ok:
            return found.value
        by_name = resolvers.class_by_token(char.get("className"))
        if by_name.ok:
            return by_name.value
    return M.class_id(token)


def _plan_spells(plan: Plan, char: dict, resolvers: Resolvers) -> None:
    seen: set[int] = set()
    for raw in char.get("knownSpellIds") or []:
        try:
            spell_id = int(raw)
        except (TypeError, ValueError):
            continue
        if spell_id in seen:
            continue
        seen.add(spell_id)
        if not resolvers.spell_exists(spell_id):
            plan.skip("spells", str(spell_id), "no such spell in this server's Spell.dbc")
            continue
        plan.add("character_spell", {"guid": plan.guid, "spell": spell_id, "specMask": 1})

    custom = char.get("knownMysticSpellIds") or []
    if custom:
        plan.skip("spells", "%d Mystic Enchant spells" % len(custom),
                  "Mystic Enchants are an Ascension system with no AzerothCore equivalent")


def _plan_talents(plan: Plan, char: dict, class_id: int, resolvers: Resolvers,
                  mode: str = "auto") -> None:
    """Resolve talents by NAME, never by grid position.

    An Ascension capture reorders the tabs (Fire/Frost/Arcane rather than
    Arcane/Fire/Frost) and packs many talents into the same (tier, column)
    cell, so the client's coordinates mean nothing on a stock server. The
    rank-1 spell name is the only key that survives the trip.

    Nothing is committed until every talent has been tried, because a talent
    build is all-or-nothing in a way the rest of a character is not. Half a
    build is not a weaker version of the build: it is a different one, silently
    missing whichever talents this server renamed or rebalanced away, with the
    leftover points still showing as spent to the player's eye. Under the
    default policy a build that does not land completely is not imported at
    all, leaving the points free so the player rebuilds it deliberately.
    """
    resolved_rows: list[tuple[str, str, int, int]] = []
    failures: list[tuple[str, str, int, str]] = []
    added: set[int] = set()
    captured_points = 0

    for tab in char.get("talentTabs") or []:
        tab_name = str(tab.get("name") or "")
        tab_index = int(tab.get("tabIndex") or 0)
        _resolved_tab, positional = resolvers.resolve_talent_tab(class_id, tab_name, tab_index)
        if positional:
            plan.notes.append(
                "Talent tab %r was matched by position (tabIndex %d among this class's tabs "
                "ordered by TalentTab id), because the target server's TalentTab.dbc has no "
                "tab by that name." % (tab_name, tab_index)
            )
        for talent in tab.get("talents") or []:
            rank = int(talent.get("rank") or 0)
            if rank < 1:
                continue
            name = str(talent.get("name") or "")
            captured_points += rank
            resolved = resolvers.talent_spell(class_id, tab_name, name, rank, tab_index)
            if not resolved.ok:
                failures.append((tab_name, name, rank, resolved.reason))
                continue
            if resolved.note:
                plan.notes.append("Talent %s: %s." % (tab_name, resolved.note))
            if resolved.value in added:
                continue
            added.add(resolved.value)
            resolved_rows.append((tab_name, name, rank, resolved.value))

    landed_points = sum(rank for _, _, rank, _ in resolved_rows)
    rebuilding = mode == "rebuild" or (mode == "auto" and failures)

    if rebuilding and captured_points:
        if mode == "rebuild":
            plan.notes.append(
                "Talents were not imported (--talents rebuild). The character keeps all "
                "%d talent points to spend." % captured_points
            )
        else:
            plan.notes.append(
                "Talents were NOT imported: %d of the %d captured talents do not resolve on "
                "this server, and a partly-applied tree would be a different build, not a "
                "smaller one. All %d talent points are left unspent for the player to rebuild. "
                "Use --talents import to take the %d that do resolve anyway."
                % (len(failures), len(failures) + len(resolved_rows), captured_points,
                   len(resolved_rows))
            )

    for tab_name, name, rank, reason in failures:
        plan.skip("talents", "%s / %s rank %d" % (tab_name, name, rank), reason)

    if rebuilding:
        for tab_name, name, rank, _spell in resolved_rows:
            plan.skip("talents", "%s / %s rank %d" % (tab_name, name, rank),
                      "resolved, but held back so the whole build is rebuilt cleanly")
        # A checkpoint's known-spell list can include a talent's own passive.
        # Leaving those in character_spell would hand the player the effect of
        # a talent they have not paid a point for, which would make the "all
        # points left to spend" note above untrue.
        rank_spells = resolvers.talent_rank_spells()
        carried = plan.rows.get("character_spell") or []
        withheld = [row for row in carried if row.get("spell") in rank_spells]
        if withheld:
            plan.rows["character_spell"] = [
                row for row in carried if row.get("spell") not in rank_spells
            ]
            plan.notes.append(
                "Held back %d known spell(s) that a talent grants, so the rebuilt tree "
                "starts clean: %s."
                % (len(withheld),
                   ", ".join(str(row["spell"]) for row in withheld[:6]))
            )
        return

    if failures and mode == "import":
        plan.notes.append(
            "Talent build is PARTIAL (--talents import): %d of %d talents landed, %d of %d "
            "points. The player keeps the unlanded points."
            % (len(resolved_rows), len(resolved_rows) + len(failures),
               landed_points, captured_points)
        )

    for _tab_name, _name, _rank, spell in resolved_rows:
        plan.add("character_talent", {
            "guid": plan.guid, "spell": spell, "specMask": 1,
        })
        plan.add("character_spell", {
            "guid": plan.guid, "spell": spell, "specMask": 1,
        })


def _advancement_fields(row: dict) -> tuple[Any, list[int], str]:
    """(entry id, spells taught, name) out of one captured advancement entry.

    C_CharacterAdvancement returns the realm's own node records: an `ID` that is
    the catalogue's EntryId, a `Spells` list, and a `Name` better than anything
    Spell.dbc would give -- "Anomaly Spikes" rather than whichever of its two
    spells got looked up. The shorter InternalID/SpellID spelling is accepted
    too, because that is what the addon's own test harness describes and a
    future client may yet return it.
    """
    entry_id = row.get("ID")
    if entry_id is None:
        entry_id = row.get("InternalID", row.get("internalId"))

    raw = row.get("Spells")
    if not isinstance(raw, list):
        raw = row.get("spells") if isinstance(row.get("spells"), list) else []
    if not raw:
        single = row.get("SpellID", row.get("spellId"))
        raw = [single] if single else []

    spells: list[int] = []
    for value in raw:
        try:
            spell = int(value)
        except (TypeError, ValueError):
            continue
        if spell and spell not in spells:
            spells.append(spell)
    return entry_id, spells, str(row.get("Name") or "").strip()


def _cost(row: dict, field: str) -> int:
    try:
        return max(0, int(row.get(field) or 0))
    except (TypeError, ValueError):
        return 0


def _advancement_ranks(kept: list, advancement: dict) -> tuple[list[int], list[str]]:
    """How many ranks of each purchase the character had, and what stays unknown.

    A multi-spell entry is ranked, not a set: the realm's own `.localtalent`
    keeps exactly one of an entry's spells, and charges its cost once per rank.
    The checkpoint does not record the rank it held -- every captured entry
    reports Points 0 -- so taking every spell, as this used to, silently buys
    ranks the character never had and inflates the essence spent.

    What the checkpoint does record is the totals: learnedAbilityEssence and
    learnedTalentEssence. Start every entry at rank 1 and the difference is
    exactly the ranks that are missing. When only one entry could account for
    it, that entry is raised; when several could, nothing is guessed and the
    shortfall is reported so the player can re-spend it deliberately.
    """
    ranks = [1] * len(kept)
    notes: list[str] = []
    for field, index, label in (("learnedAbilityEssence", 2, "ability"),
                                ("learnedTalentEssence", 3, "talent")):
        try:
            target = int(advancement.get(field) or 0)
        except (TypeError, ValueError):
            continue
        if target <= 0:
            continue
        spent = sum(entry[index] * ranks[i] for i, entry in enumerate(kept))
        short = target - spent
        if short <= 0:
            continue
        # Only an entry that costs this essence and has a spell left can absorb it.
        movable = [i for i, entry in enumerate(kept)
                   if entry[index] > 0 and len(entry[1]) > ranks[i]]
        exact = [i for i in movable
                 if short % kept[i][2 if index == 2 else 3] == 0
                 and ranks[i] + short // kept[i][index] <= len(kept[i][1])]
        if len(exact) == 1:
            i = exact[0]
            ranks[i] += short // kept[i][index]
            notes.append(
                "Advancement: %s is the only purchase that can account for the "
                "%d unspent %s essence the capture recorded, so it was restored "
                "at rank %d." % (kept[i][0], short, label, ranks[i]))
        else:
            notes.append(
                "Advancement: the capture spent %d %s essence but the entries it "
                "lists account for %d at one rank each. A checkpoint does not "
                "record per-purchase ranks, and %d purchase(s) could explain the "
                "difference, so none was raised -- the player keeps %d %s essence "
                "to re-spend."
                % (target, label, spent, len(movable), short, label))
    return ranks, notes


def _plan_advancement(plan: Plan, char: dict, class_id: int, resolvers: Resolvers,
                      catalogue: Any = None) -> None:
    """Restore what a Conquest of Azeroth character bought.

    Custom classes keep nothing in Talent.dbc -- TalentTab.dbc stops at class 13,
    so such a character's captured talentTabs are empty and _plan_talents has
    nothing to do. What they bought is recorded as advancement entries instead,
    and the server stores having one as knowing its spells, so restoring the
    spells IS restoring the build.

    This pass is what actually restores it. The captured spellbook is not enough:
    measured on a live Chronomancer, 10 of the 15 spells its 12 purchases teach
    never appear in knownSpellIds, because a passive is not in the spellbook. An
    import that leaned on _plan_spells alone would silently deliver a third of
    the character.

    One purchase can teach several spells -- that Chronomancer's Warpstriker,
    Luck and Anomaly Spikes each teach two -- so every id in the entry is taken,
    not just the first.

    The active specialization is restored separately, by _plan_settings.
    """
    advancement = char.get("advancement")
    if not isinstance(advancement, dict) or not advancement.get("available"):
        return

    level = max(1, min(255, int(char.get("level") or 1)))
    spec = advancement.get("activeSpecializationId")
    try:
        spec_id = int(spec) if spec is not None else 0
    except (TypeError, ValueError):
        spec_id = 0

    planned = {row["spell"] for row in plan.rows.get("character_spell", [])}
    added = restored = taught = 0
    keep: list[tuple[str, list[int], int]] = []   # (what, spells, rank)
    for key, label in (("knownTalentEntries", "talent"),
                       ("knownSpellEntries", "ability")):
        rows = advancement.get(key)
        if not isinstance(rows, list):
            continue
        for row in rows:
            if not isinstance(row, dict):
                continue
            entry_id, spells, given = _advancement_fields(row)
            what = "%s %s" % (row.get("Type") or label,
                              given or ("entry %s" % entry_id))

            if catalogue is not None:
                wrong = catalogue.mismatch(entry_id, class_id, level, spec_id)
                if wrong:
                    plan.skip("advancement", what, wrong)
                    continue
            if not spells:
                plan.skip("advancement", what, "the entry teaches no spell")
                continue
            usable = [s for s in spells if resolvers.spell_exists(s)]
            for missing in [s for s in spells if s not in usable]:
                plan.skip("advancement", "%s (spell %d)" % (what, missing),
                          "not in this server's Spell.dbc")
            if not usable:
                continue
            keep.append((what, usable, _cost(row, "AECost"), _cost(row, "TECost")))

    ranks, shortfalls = _advancement_ranks(keep, advancement)
    for (what, usable, _ae, _te), rank in zip(keep, ranks):
        restored += 1
        taught += 1
        spell_id = usable[min(rank, len(usable)) - 1]
        if spell_id in planned:
            continue
        planned.add(spell_id)
        added += 1
        plan.add("character_spell",
                 {"guid": plan.guid, "spell": spell_id, "specMask": 1})
    for note in shortfalls:
        plan.notes.append(note)

    if restored:
        note = ("Conquest of Azeroth advancement: %d purchased entries restored, "
                "teaching %d spell(s)" % (restored, taught))
        if added:
            note += (", %d of which the captured spellbook did not list" % added)
        plan.notes.append(note + ".")


def _plan_skills(plan: Plan, char: dict, resolvers: Resolvers) -> None:
    for skill in char.get("skills") or []:
        name = str(skill.get("name") or "")
        resolved = resolvers.skill_by_name(name)
        if not resolved.ok:
            plan.skip("skills", name, resolved.reason)
            continue
        value = max(0, int(skill.get("rank") or 0))
        maximum = max(value, int(skill.get("maximum") or 0))
        plan.add("character_skills", {
            "guid": plan.guid, "skill": resolved.value, "value": value, "max": maximum,
        })


def _plan_reputations(plan: Plan, char: dict, race: int, klass: int, resolvers: Resolvers) -> None:
    for rep in char.get("reputations") or []:
        name = str(rep.get("name") or "")
        if rep.get("isHeader") and not rep.get("hasReputation"):
            continue
        resolved = resolvers.faction_by_name(name)
        if not resolved.ok:
            plan.skip("reputations", name, resolved.reason)
            continue
        absolute = int(rep.get("value") or 0)
        base = resolvers.faction_base_rep(resolved.value, race, klass)
        flags = FACTION_FLAG_VISIBLE
        if rep.get("atWarWith"):
            flags |= FACTION_FLAG_AT_WAR
        plan.add("character_reputation", {
            "guid": plan.guid,
            "faction": resolved.value,
            # ReputationMgr stores standing RELATIVE to the race/class base
            # (ReputationMgr.cpp: GetBaseReputation() + state->Standing).
            "standing": absolute - base,
            "flags": flags,
        })


def _plan_action_bars(plan: Plan, char: dict) -> None:
    bars = char.get("actionBars") or {}
    seen: set[int] = set()
    for entry in bars.get("occupied") or []:
        slot = int(entry.get("slot") or 0)
        button = slot - 1
        label = "slot %d (%s)" % (slot, entry.get("actionType"))
        if not 0 <= button < MAX_ACTION_BUTTONS:
            plan.skip("action bars", label, "button index outside 0..%d" % (MAX_ACTION_BUTTONS - 1))
            continue
        if button in seen:
            continue
        kind = str(entry.get("actionType") or "")
        if kind == "spell":
            action, button_type = int(entry.get("spellId") or entry.get("actionId") or 0), ACTION_BUTTON_SPELL
        elif kind == "item":
            action, button_type = int(entry.get("actionId") or 0), ACTION_BUTTON_ITEM
        elif kind == "macro":
            plan.skip("action bars", label,
                      "macro bodies are not captured (macroBodiesIncluded is false), so the "
                      "button would point at a macro that does not exist")
            continue
        else:
            plan.skip("action bars", label, "unsupported action type %r" % kind)
            continue
        if action <= 0:
            plan.skip("action bars", label, "no usable action id")
            continue
        seen.add(button)
        plan.add("character_action", {
            "guid": plan.guid, "spec": 0, "button": button,
            "action": action, "type": button_type,
        })


def _plan_quests(conn, args, plan: Plan, char: dict) -> None:
    quests = char.get("quests") or {}
    active = list(quests.get("active") or [])
    completed = [int(q) for q in (quests.get("completedIds") or []) if str(q).isdigit()]

    wanted = {int(q.get("questId") or 0) for q in active if q.get("questId")} | set(completed)
    known: set[int] = set()
    if wanted:
        marks = ", ".join(["%s"] * len(wanted))
        known = {r["ID"] for r in query(
            conn, "SELECT ID FROM `%s`.quest_template WHERE ID IN (%s)" % (args.world_db, marks),
            tuple(sorted(wanted)))}

    for quest in active:
        quest_id = int(quest.get("questId") or 0)
        if quest_id not in known:
            plan.skip("quests", "%s (%s)" % (quest.get("title") or "?", quest_id),
                      f"not in {args.world_db}.quest_template on this server")
            continue
        plan.add("character_queststatus", {
            "guid": plan.guid,
            "quest": quest_id,
            "status": QUEST_STATUS_COMPLETE if quest.get("complete") else QUEST_STATUS_INCOMPLETE,
            "explored": 1 if quest.get("complete") else 0,
            "timer": 0,
        })
    if active:
        plan.notes.append(
            "Active quests are restored with objective counters at zero. The client only "
            "exposes objectives as display strings like '3/8 Kobolds slain', and parsing "
            "those into mobcount columns would guess at which objective is which."
        )
    for quest_id in completed:
        if quest_id not in known:
            plan.skip("quests", str(quest_id),
                      f"not in {args.world_db}.quest_template on this server")
            continue
        plan.add("character_queststatus_rewarded", {
            "guid": plan.guid, "quest": quest_id, "active": 1,
        })


def _plan_achievements(plan: Plan, char: dict) -> None:
    achievements = char.get("achievements") or {}
    now = int(time.time())
    for entry in achievements.get("completed") or []:
        achievement_id = int(entry.get("id") or 0)
        if achievement_id <= 0:
            continue
        plan.add("character_achievement", {
            "guid": plan.guid, "achievement": achievement_id, "date": now,
        })
    in_progress = achievements.get("inProgress") or []
    if in_progress:
        plan.skip("achievements", "%d in progress" % len(in_progress),
                  "criteria counters are account-wide and client-side; importing them would "
                  "credit progress this character may not own")


def _wearer(plan: Plan, char: dict, level: int, race: int, class_id: int,
            resolvers: Resolvers) -> Wearer:
    """The character as the server sees it when it checks the equipment at login."""
    skills = {int(row["skill"]): int(row["value"])
              for row in plan.rows.get("character_skills", [])}
    spells = {int(row["spell"]) for row in plan.rows.get("character_spell", [])}
    relative = {int(row["faction"]): int(row["standing"])
                for row in plan.rows.get("character_reputation", [])}

    def standing(faction_id: int) -> int:
        # character_reputation.standing is relative to the race/class base.
        return (resolvers.faction_base_rep(faction_id, race, class_id)
                + relative.get(int(faction_id), 0))

    team = str(char.get("faction") or "")
    if team not in ("Alliance", "Horde"):
        team = TEAM_BY_RACE.get(race, "")
    return Wearer(level=level, race=race, class_id=class_id, team=team,
                  skills=skills, spells=spells, standing=standing)


def _setting_data(*values: int) -> str:
    """character_settings.data as Player::SerializeSettingsData writes it.

    Every value is followed by a space -- "32 ", never "32".
    """
    return "".join("%d " % int(value) for value in values)


def _plan_settings(plan: Plan, char: dict, class_id: int, catalogue: Any = None,
                   starter_kit: bool = False) -> None:
    """The per-character state mod-ascension-compat keeps in character_settings.

    Only custom classes have any. At login the module reads the active
    specialization back before it reconciles the character's spells, then runs a
    one-time starter-kit repair unless the character is marked as already given
    the kit (AscensionCompat.cpp: OnPlayerLogin, RepairStarterKit).
    """
    if class_id < FIRST_CUSTOM_CLASS:
        return

    advancement = char.get("advancement")
    spec = advancement.get("activeSpecializationId") if isinstance(advancement, dict) else None
    try:
        spec_id = int(spec) if spec is not None else 0
    except (TypeError, ValueError):
        spec_id = 0
    if spec_id > 0:
        # An empty list means the catalogue does not know this class at all,
        # which is not evidence against the specialization.
        known = catalogue.specs(class_id) if catalogue is not None else []
        if known and spec_id not in known:
            plan.skip("specialization", "specialization %d" % spec_id,
                      "the catalogue gives class %d only specializations %s; the player "
                      "chooses one in game"
                      % (class_id, ", ".join(str(s) for s in known)))
        else:
            plan.add("character_settings", {
                "guid": plan.guid,
                "source": ASCENSION_ACTIVE_SPEC_SETTING,
                "data": _setting_data(spec_id),
            })
            plan.notes.append(
                "Specialization %d restored as character_settings %s. The server reads it "
                "at login, before it reconciles the character's spells."
                % (spec_id, ASCENSION_ACTIVE_SPEC_SETTING))

    if starter_kit:
        plan.notes.append(
            "--starter-kit: at first login the server will add its custom-class starter "
            "kit, putting starter gear in each empty slot the kit covers and a hearthstone "
            "in the bags if there is none.")
        return
    plan.add("character_settings", {
        "guid": plan.guid,
        "source": ASCENSION_STARTER_SETTING,
        "data": _setting_data(ASCENSION_STARTER_REVISION),
    })
    plan.notes.append(
        "Marked as already given the custom-class starter kit (%s = %d). Otherwise the "
        "server's first-login repair puts starter gear in each empty slot the kit covers "
        "and adds a hearthstone; pass --starter-kit to allow that."
        % (ASCENSION_STARTER_SETTING, ASCENSION_STARTER_REVISION))


# -- apply -----------------------------------------------------------------

WRITE_ORDER = [
    "item_template",
    "characters",
    "character_homebind",
    "item_instance",
    "character_inventory",
    "mail",
    "mail_items",
    "character_spell",
    "character_talent",
    "character_skills",
    "character_reputation",
    "character_action",
    "character_queststatus",
    "character_queststatus_rewarded",
    "character_achievement",
    "character_settings",
]

WORLD_TABLES = {"item_template"}


def validate_rows(conn, args: argparse.Namespace, plan: Plan) -> list[Check]:
    """Check every planned row against the live schema without writing anything.

    This is the part of the apply path that can be verified read-only: that
    each table exists, that every column named in a planned row really exists
    on the target, that no NOT NULL column without a default is left unfilled,
    and that every value fits the column it is bound for. It catches the
    failures that would otherwise only appear mid-transaction -- or, on a
    server without STRICT sql_mode, not appear at all.
    """
    checks: list[Check] = []
    problems: list[str] = []
    for table in WRITE_ORDER:
        rows = plan.rows.get(table)
        if not rows:
            continue
        schema = args.world_db if table in WORLD_TABLES else args.characters_db
        columns = table_columns(conn, schema, table)
        if not columns:
            problems.append(f"{schema}.{table} does not exist")
            continue
        unknown: set[str] = set()
        unfilled: set[str] = set()
        oversized: dict[str, tuple[Any, str]] = {}
        for row in rows:
            unknown |= set(row) - set(columns)
            for name, value in row.items():
                meta = columns.get(name)
                if meta is not None and not value_fits(meta, value):
                    oversized.setdefault(
                        name, (value, str(meta.get("COLUMN_TYPE") or "?")))
            complete = fill_required_columns(columns, row)
            for name, meta in columns.items():
                if (name not in complete and meta["IS_NULLABLE"] == "NO"
                        and meta["COLUMN_DEFAULT"] is None
                        and "auto_increment" not in (meta["EXTRA"] or "")):
                    unfilled.add(name)
        if unknown:
            problems.append(f"{table}: no such column(s) {', '.join(sorted(unknown))}")
        if unfilled:
            problems.append(f"{table}: required column(s) unfilled {', '.join(sorted(unfilled))}")
        for name in sorted(oversized):
            value, column_type = oversized[name]
            # Widening the column is the fix for an id the fork outgrew; it is the
            # wrong advice for a string, where the core expects that exact width.
            remedy = ("Shorten the value." if isinstance(value, str)
                      else "Widen the column on the target server.")
            problems.append(
                f"{table}.{name} is {column_type} and cannot hold {value!r}. Without "
                "STRICT in sql_mode MySQL clamps or truncates such a value instead of "
                "refusing it, which usually surfaces later as a duplicate-key rollback "
                f"with no useful message. {remedy}"
            )
    checks.append(Check(
        "schema fit", FAIL if problems else OK,
        "; ".join(problems) if problems
        else "every planned row matches the target schema (%d table(s))"
             % len([t for t in WRITE_ORDER if plan.rows.get(t)]),
    ))
    return checks


def apply_plan(conn, args: argparse.Namespace, plan: Plan, rehearse: bool = False) -> dict[str, int]:
    written: dict[str, int] = {}
    # Renumber mail against the live table now, inside the transaction.
    if plan.rows.get("mail"):
        base = int(scalar(conn, "SELECT MAX(id) FROM `%s`.mail" % args.characters_db, (), 0) or 0)
        remap = {}
        for row in plan.rows["mail"]:
            remap[row["id"]] = base + row["id"]
            row["id"] = remap[row["id"]]
        for row in plan.rows.get("mail_items", []):
            row["mail_id"] = remap[row["mail_id"]]

    schema_cache: dict[tuple[str, str], dict[str, dict[str, Any]]] = {}
    try:
        for table in WRITE_ORDER:
            rows = plan.rows.get(table)
            if not rows:
                continue
            schema = args.world_db if table in WORLD_TABLES else args.characters_db
            key = (schema, table)
            if key not in schema_cache:
                schema_cache[key] = table_columns(conn, schema, table)
            columns = schema_cache[key]
            for row in rows:
                insert(conn, schema, table, fill_required_columns(columns, row))
            written[table] = len(rows)
        if rehearse:
            # Every statement ran against the real schema; keep none of it.
            conn.rollback()
        else:
            conn.commit()
    except Exception:
        conn.rollback()
        raise
    return written


# -- reporting -------------------------------------------------------------

SYMBOLS = {OK: "[ ok ]", WARN: "[warn]", FAIL: "[FAIL]"}


def render(args: argparse.Namespace, checkpoint, checks: list[Check], plan: Plan | None) -> str:
    char = checkpoint.character
    lines: list[str] = []
    lines.append("=" * 76)
    lines.append("Bind My Soul -- offline character import")
    lines.append("=" * 76)
    lines.append("Checkpoint : %s, level %s %s %s (%s)"
                 % (char.get("name"), char.get("level"), char.get("raceName"),
                    char.get("className"), char.get("faction")))
    lines.append("Captured   : %s on %r"
                 % (time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(checkpoint.sealed_at)),
                    char.get("realmName")))
    lines.append("Target     : %s@%s:%s -> %s"
                 % (args.user, args.host, args.port, args.characters_db))
    lines.append("")
    lines.append("PRE-FLIGHT")
    for check in checks:
        lines.append("  %s %-18s %s" % (SYMBOLS[check.status], check.name, check.detail))

    if plan is None:
        lines.append("")
        lines.append("Pre-flight failed; no plan was built and nothing was written.")
        return "\n".join(lines)

    lines.append("")
    lines.append("WILL WRITE  (guid %d, account %d, name %r%s)"
                 % (plan.guid, plan.account_id, plan.name,
                    ", RENAMED" if plan.renamed else ""))
    for table in WRITE_ORDER:
        count = plan.count(table)
        if count:
            lines.append("  %-32s %4d row(s)" % (table, count))

    if plan.skips:
        lines.append("")
        lines.append("WILL SKIP")
        by_category: dict[str, list[Skip]] = {}
        for skip in plan.skips:
            by_category.setdefault(skip.category, []).append(skip)
        for category in sorted(by_category):
            entries = by_category[category]
            lines.append("  %s (%d)" % (category, len(entries)))
            for skip in entries[:12]:
                lines.append("      %-34s %s" % (skip.what[:34], skip.reason))
            if len(entries) > 12:
                lines.append("      ... and %d more" % (len(entries) - 12))

    if plan.notes:
        lines.append("")
        lines.append("NOTES")
        for note in plan.notes:
            lines.append("  - " + note)

    return "\n".join(lines)


def report_json(checkpoint, checks: list[Check], plan: Plan | None,
                args: argparse.Namespace | None = None) -> dict[str, Any]:
    data: dict[str, Any] = {
        "character": {
            key: checkpoint.character.get(key)
            for key in ("name", "level", "raceToken", "classToken", "faction", "realmName")
        },
        "sealedAt": checkpoint.sealed_at,
        "checks": [{"name": c.name, "status": c.status, "detail": c.detail} for c in checks],
    }
    if args is not None:
        # Which config and which DBC set produced this plan. Two reports of the
        # same character are only comparable if you can see they were built
        # from the same data.
        data["source"] = {
            "config": getattr(args, "config_path", None),
            "configOrigin": getattr(args, "config_origin", None),
            "dbcDir": args.dbc_dir or None,
            "charactersDb": args.characters_db,
        }
    if plan is not None:
        data["plan"] = {
            "guid": plan.guid,
            "account": plan.account_id,
            "name": plan.name,
            "renamed": plan.renamed,
            "rows": {t: plan.count(t) for t in WRITE_ORDER if plan.count(t)},
            "skips": [{"category": s.category, "what": s.what, "reason": s.reason}
                      for s in plan.skips],
            "notes": plan.notes,
        }
    return data


# -- CLI -------------------------------------------------------------------

def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        # Python 3.14 otherwise prints the interpreter plus the full path to
        # the console script, which is nobody's idea of a usage line.
        prog=os.path.basename(sys.argv[0]) or "bms-import",
        description="Import a Bind My Soul checkpoint into an AzerothCore realm.",
        epilog="The realm must be stopped. Nothing is written without --apply.",
    )
    parser.add_argument("checkpoint",
                        help="a .bmsr.zip from bindmysoul.com, a SavedVariables BindMySoul.lua, or a file holding a BMSP code")
    parser.add_argument("--index", type=int, default=0,
                        help="which checkpoint in the file (default 0, the sealed one)")
    parser.add_argument("--list", action="store_true", help="list the checkpoints and exit")

    parser.add_argument("--config", default=None, metavar="PATH",
                        help="a worldserver.conf to take the database settings and the "
                             "DBC directory from; without this, one is looked for")
    parser.add_argument("--no-config", action="store_true",
                        help="do not look for a server config; use the flags and "
                             "environment only")

    # These default to None so that a config file can fill them in. Anything
    # given explicitly wins over the config -- see resolve_settings().
    parser.add_argument("--host", default=None)
    parser.add_argument("--port", type=int, default=None)
    parser.add_argument("--user", default=None)
    parser.add_argument("--password", default=None,
                        help="prefer the environment variable; a command line is visible to "
                             "other processes")
    parser.add_argument("--password-env", default="BMS_DB_PASSWORD",
                        help="environment variable holding the password (default BMS_DB_PASSWORD)")
    parser.add_argument("--auth-db", default=None)
    parser.add_argument("--characters-db", default=None)
    parser.add_argument("--world-db", default=None)

    parser.add_argument("--account", required=False,
                        help="the account that should own the imported character")
    parser.add_argument("--dbc-dir", default=None,
                        help="the target server's Data/dbc directory")
    parser.add_argument("--name", default=None, help="override the character name")
    parser.add_argument("--coa-data", default=None, metavar="PATH",
                        help="the realm's Conquest of Azeroth advancement catalogue "
                             "(AscensionCoATalentData.h, or a JSON export of it). "
                             "Optional: it is used to name a custom class's purchases "
                             "and to refuse entries that belong to another class, "
                             "level or specialization.")
    parser.add_argument("--talents", choices=("auto", "import", "rebuild"), default="auto",
                        help="auto (default): import the talent build only if every talent "
                             "in it resolves on this server, otherwise import none and leave "
                             "the points to respend; import: take whatever resolves, even a "
                             "partial build; rebuild: never import talents")
    parser.add_argument("--starter-kit", action="store_true",
                        help="Conquest of Azeroth custom classes only: let the server add "
                             "its starter kit at first login. By default the character is "
                             "marked as already having it, so starter gear does not land "
                             "in the slots the capture left empty")

    parser.add_argument("--apply", action="store_true", help="actually write (default: dry run)")
    parser.add_argument("--rehearse", action="store_true",
                        help="run every INSERT against the live schema inside a transaction, "
                             "then roll back -- proves the writes work without keeping them")
    parser.add_argument("--synthesize", action="store_true",
                        help="create placeholder item_template rows for unknown items")
    parser.add_argument("--allow-online", action="store_true",
                        help="proceed even though a worldserver appears to be running")
    parser.add_argument("--allow-unverified", action="store_true",
                        help="import a bundle whose integrity checks failed")
    parser.add_argument("--json", default=None, help="write the full report to this path")
    return parser.parse_args(argv)


# Each setting: the args attribute, the environment variable, the stock default.
SETTINGS = (
    ("host", "BMS_DB_HOST", "127.0.0.1"),
    ("port", "BMS_DB_PORT", 3306),
    ("user", "BMS_DB_USER", "root"),
    ("auth_db", "BMS_AUTH_DB", "acore_auth"),
    ("characters_db", "BMS_CHARACTERS_DB", "acore_characters"),
    ("world_db", "BMS_WORLD_DB", "acore_world"),
    ("dbc_dir", "BMS_DBC_DIR", ""),
)


def choose_config(args: argparse.Namespace, start: str | None,
                  running: list[bms_config.ServerConfig],
                  ) -> tuple[bms_config.ServerConfig | None, str]:
    """Decide which server config to believe, and say where it came from.

    A running worldserver names its own config on its command line, which makes
    it the only unambiguous answer available. It is used here to break a
    discovery tie that would otherwise stop the run, and in `dbc_conflict` to
    catch the worse case: reading the DBCs of a realm other than the one being
    imported into.
    """
    if args.config:
        return bms_config.read_config(args.config), "--config"
    if args.no_config:
        return None, "not read"

    found = bms_config.find_all_configs(start)
    if len(found) == 1:
        return bms_config.read_config(found[0]), "discovered"
    if len(found) > 1:
        # Discovery works by name and by convention, so several realms on one
        # machine look identical to it. A single running server settles it.
        if len(running) == 1:
            return running[0], "running server"
        raise ImportError_(
            "Found %d server configs and will not guess between them:\n  %s\n"
            "Pass --config with the one you mean." % (len(found), "\n  ".join(found)))
    if len(running) == 1:
        return running[0], "running server"
    return None, "not found"


def _same_dir(a: str | None, b: str | None) -> bool:
    if not a or not b:
        return False
    return os.path.normcase(os.path.abspath(a)) == os.path.normcase(os.path.abspath(b))


def dbc_conflict(characters_db: str, dbc_dir: str | None,
                 running: list[bms_config.ServerConfig],
                 ) -> bms_config.ServerConfig | None:
    """A worldserver running on our target database, from a different DBC set.

    The comparison is against the directory this run will actually read, not
    against the config it came from, so pointing `--dbc-dir` at the right data
    settles the question however the rest of the settings were arrived at.

    Only the DBC directory is compared. Two configs for one realm that disagree
    about nothing that matters here -- a copy under another name, a `.dist`
    beside the real thing -- are not worth stopping for. A different `DataDir`
    is, because every talent, spell, skill and faction name in the checkpoint is
    resolved through it.
    """
    if not dbc_dir:
        return None
    for other in running:
        if (bms_config.serves_database(other, characters_db)
                and not _same_dir(other.dbc_dir, dbc_dir)):
            return other
    return None


def resolve_settings(args: argparse.Namespace,
                     start: str | None = None,
                     running: list[bms_config.ServerConfig] | None = None,
                     ) -> list[tuple[str, str, str]]:
    """Fill in whatever the user did not spell out, and say where it came from.

    Order of authority is flag, then environment, then server config, then the
    stock AzerothCore default. A `worldserver.conf` already records all three
    database DSNs and the data directory, so reading one turns seven required
    flags into none -- which is the difference between this being usable by
    someone who did not write it and not.

    `running` is the list of configs belonging to worldservers running right
    now; pass `[]` to skip the process scan entirely.

    Returns display rows of (setting, value, origin). Passwords never appear.
    """
    if running is None:
        running = bms_config.running_server_configs()

    args.config_path = None
    args.config_origin = "not read"
    args.config_conflict = None
    config, args.config_origin = choose_config(args, start, running)
    if config is not None:
        args.config_path = config.path

    # One connection serves all three schemas, so the credentials come from the
    # character database -- the one this tool actually writes to.
    dsn = None
    if config is not None:
        dsn = config.characters or config.world or config.login
    from_config = {}
    if dsn is not None:
        from_config = {"host": dsn.host, "port": dsn.port, "user": dsn.user}
    if config is not None:
        if config.login:
            from_config["auth_db"] = config.login.database
        if config.characters:
            from_config["characters_db"] = config.characters.database
        if config.world:
            from_config["world_db"] = config.world.database
        if config.dbc_dir:
            from_config["dbc_dir"] = config.dbc_dir

    rows: list[tuple[str, str, str]] = []
    for attribute, variable, fallback in SETTINGS:
        if getattr(args, attribute) not in (None, ""):
            origin = "--%s" % attribute.replace("_", "-")
        elif os.environ.get(variable):
            value = os.environ[variable]
            setattr(args, attribute, int(value) if attribute == "port" else value)
            origin = "$%s" % variable
        elif attribute in from_config:
            setattr(args, attribute, from_config[attribute])
            origin = "config"
        else:
            setattr(args, attribute, fallback)
            origin = "default"
        rows.append((attribute, str(getattr(args, attribute)), origin))

    # Now that the DBC directory is settled, whatever settled it, ask whether
    # the realm that owns this database is running on a different one.
    args.config_conflict = dbc_conflict(args.characters_db, args.dbc_dir, running)

    # The password follows the same order, but is never shown or written down.
    if args.password is not None:
        args.password_origin = "--password"
    elif os.environ.get(args.password_env):
        args.password_origin = "$%s" % args.password_env
    elif dsn is not None and dsn.password:
        args.password = dsn.password
        args.password_origin = "config"
    else:
        args.password_origin = "not set"
    return rows


def load_checkpoint(path: str, index: int) -> tuple[Any, list[str], Bundle | None]:
    """Load checkpoint `index` from any of the three shapes a user can hand us.

    A .bmsr.zip is what the website gives people, so it is the expected input;
    a client's own SavedVariables file and a bare BMSP code file are supported
    for anyone lifting the code out by hand. The bundle comes back too, because
    its integrity report has to be shown before anything is written.
    """
    if not os.path.isfile(path):
        raise ImportError_(f"No such file: {path}")
    bundle: Bundle | None = None
    if is_bundle(path):
        try:
            bundle = read_bundle(path)
        except BundleError as exc:
            raise ImportError_(str(exc)) from None
        codes = bundle.codes
    elif path.lower().endswith(".lua"):
        codes = extract_checkpoint_codes(load_savedvariables(path))
    else:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            codes = [line.strip() for line in handle if line.strip().startswith("BMSP")]
    if not codes:
        raise ImportError_(f"No checkpoint codes found in {path}")
    if not 0 <= index < len(codes):
        raise ImportError_(f"--index {index} is out of range; the file holds {len(codes)}")
    return parse_checkpoint(codes[index]), codes, bundle


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    if args.apply and args.rehearse:
        print("error: --rehearse and --apply are opposites; pick one", file=sys.stderr)
        return 2
    try:
        checkpoint, codes, bundle = load_checkpoint(args.checkpoint, args.index)
    except ImportError_ as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if bundle is not None:
        print(render_bundle(bundle))
        print()
        if not bundle.verified and not args.allow_unverified:
            print("error: %d integrity check(s) failed above. The bundle's contents are not "
                  "what bindmysoul.com signed -- it was edited, truncated, or damaged in "
                  "transit. Re-download it, or pass --allow-unverified to import it anyway."
                  % len(bundle.failures), file=sys.stderr)
            return 2
        if not bundle.verified:
            print("WARNING: importing a bundle that failed %d integrity check(s), because "
                  "--allow-unverified was given.\n" % len(bundle.failures))

    if args.list:
        for position, code in enumerate(codes):
            try:
                one = parse_checkpoint(code)
                print("[%d] %s level %s %s %s on %s -- %s"
                      % (position, one.character.get("name"), one.character.get("level"),
                         one.character.get("raceName"), one.character.get("className"),
                         one.character.get("realmName") or "?",
                         time.strftime("%Y-%m-%d %H:%M", time.localtime(one.sealed_at))))
            except Exception as exc:
                print("[%d] unreadable: %s" % (position, exc))
        if len(codes) > 1:
            print("\nPick one with --index N.")
        return 0

    # One process scan, shared: which config each running worldserver uses
    # settles both "is the realm I am writing to up?" and "am I about to read
    # the DBCs of a different realm?".
    args.running_servers = bms_config.running_servers()
    try:
        settings = resolve_settings(
            args, running=[c for _line, c in args.running_servers if c is not None])
    except (ImportError_, bms_config.ConfigError) as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    if args.config_path:
        print("CONFIG  %s (%s)" % (args.config_path, args.config_origin))
        for name, value, origin in settings:
            print("  %-14s %-44s %s" % (name, value or "(unset)", origin))
        print("  %-14s %-44s %s" % ("password", "<hidden>", args.password_origin))
        if args.config_origin == "running server":
            # Discovery could not have found this one, and it will not find it
            # again once the realm is stopped for --apply.
            print("\n  This config came from the running worldserver, not from "
                  "auto-discovery.\n  Pass --config \"%s\" when you re-run with the "
                  "realm stopped." % args.config_path)
        print()

    if not args.account:
        print("error: --account is required (the account that will own the character)",
              file=sys.stderr)
        return 2

    resolvers: Resolvers | None = None
    if args.dbc_dir:
        try:
            resolvers = Resolvers(args.dbc_dir)
        except DbcError as exc:
            print("warning: %s" % exc, file=sys.stderr)
    else:
        print("error: no DBC directory. A server config supplies one automatically; "
              "otherwise pass --dbc-dir (the target server's Data/dbc).", file=sys.stderr)
        return 2

    args.coa_catalogue = None
    if args.coa_data:
        try:
            args.coa_catalogue = bms_coa.load(args.coa_data)
        except bms_coa.CoaError as exc:
            print("error: %s" % exc, file=sys.stderr)
            return 2

    try:
        conn = connect(args)
    except ImportError_ as exc:
        print("error: %s" % exc, file=sys.stderr)
        return 2

    exit_code = 0
    try:
        entry = bundle.entries[args.index] if bundle is not None else None
        checks = preflight(conn, args, checkpoint, resolvers, entry)
        blocked = blocks_planning(checks)
        plan = None
        if not blocked and resolvers is not None:
            try:
                plan = build_plan(conn, args, checkpoint, resolvers)
            except (ImportError_, M.MappingError, DbcError) as exc:
                checks.append(Check("plan", FAIL, str(exc)))
                blocked = True

        if plan is not None:
            # Every planned row measured against the live schema, read-only.
            checks.extend(validate_rows(conn, args, plan))
            blocked = blocked or blocks_planning(checks)

        print(render(args, checkpoint, checks, plan))

        if args.json:
            with open(args.json, "w", encoding="utf-8") as handle:
                json.dump(report_json(checkpoint, checks, plan, args), handle, indent=2)
            print("\nReport written to %s" % args.json)

        if blocked or plan is None:
            print("\nBLOCKED -- nothing was written.")
            return 1

        if args.apply or args.rehearse:
            # Both of these touch the database, so now every failure counts --
            # including the write-only ones the dry run was allowed to sail past.
            stoppers = blocks_writing(checks)
            if stoppers:
                for check in stoppers:
                    print("\nBLOCKED by %s: %s" % (check.name, check.detail))
                print("\nBLOCKED -- nothing was written.")
                return 1
        if args.rehearse:
            written = apply_plan(conn, args, plan, rehearse=True)
            print("\nREHEARSED -- every statement ran against the live schema, then the "
                  "transaction was rolled back. Nothing was kept:")
            for table in WRITE_ORDER:
                if table in written:
                    print("  %-32s %4d row(s)" % (table, written[table]))
            return 0
        if not args.apply:
            print("\nDRY RUN -- nothing was written. Re-run with --apply to commit.")
            return 0

        written = apply_plan(conn, args, plan)
        print("\nAPPLIED:")
        for table in WRITE_ORDER:
            if table in written:
                print("  %-32s %4d row(s)" % (table, written[table]))
        print("\nCharacter %r (guid %d) is on account %d. Start the realm and log in; the "
              "client will prompt for customisation%s."
              % (plan.name, plan.guid, plan.account_id,
                 " and a rename" if plan.renamed else ""))
    except Exception as exc:
        print("error: %s" % exc, file=sys.stderr)
        exit_code = 2
    finally:
        conn.close()
    return exit_code


def cli() -> int:
    """Console-script entry point: same as main(), but reads sys.argv itself."""
    return main(sys.argv[1:])


if __name__ == "__main__":
    raise SystemExit(cli())
