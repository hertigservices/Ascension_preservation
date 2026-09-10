#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""ascension_bridge.py -- put the Ascension client into the REAL AzerothCore world.

Why this exists
===============
`world_server.py` is a hand-written Python world server.  It was the right tool for
reverse-engineering the client's protocol, and it now does spells, auras, action
bars, the whole Character Advancement stack and character creation.  What it does
not have, and never will without reimplementing a game server, is a *world*: the
155 000 creature spawns, 6 100 gossip menus, 41 600 vendor rows, 17 900 trainer
rows and 18 600 quests that make NPCs behave "just like the main game".

All of that already exists on this machine.  `server-ascension\\worldserver.exe` is
stock AzerothCore running against Ascension's own DBCs and the `asc_*` databases,
and it boots clean.  It was never played only because the Ascension client cannot
log into it: the client speaks a custom auth protocol and, on the world channel,
sends **plaintext headers** and a handful of opcodes AzerothCore has never heard of.

This bridge is the translation layer.

    Ascension client  <--plaintext, custom opcodes-->  BRIDGE  <--RC4, stock-->  AzerothCore

Client side
-----------
* Plaintext headers in both directions; see TROUBLESHOOTING and world_server.py.
* The listen port is NOT free to choose.  Extensions.dll checks the endpoint the
  client connected to against a hard-coded allow-list, and a miss arms a buggy
  kill-switch that crashes the client a few seconds after the world draws.  The
  only loopback entries are 8085, 8087 and 8088.  See archive_ports.py.

AzerothCore side
----------------
* We authenticate as an ordinary client: read `SMSG_AUTH_CHALLENGE`, answer
  `CMSG_AUTH_SESSION` with a digest over a session key **we choose** and write into
  `asc_auth.account.session_key` first.  The client's own key is never needed --
  the two halves of the connection are separately authenticated.
* Three fields have to be rewritten or AzerothCore drops the socket: the build
  (client sends 12344, core accepts 12340), the realm id (client sends 11, this
  core is realm 1) and the digest (recomputed for the core's own seed).
* After `AUTH_OK` the core encrypts every header with ARC4-drop1024 keyed by
  HMAC-SHA1 over the session key.  We hold both stream states and re-frame every
  packet: strip the core's crypt going out to the client, add it going in.

Opcode policy
-------------
Stock 3.3.5a opcodes stop at `NUM_MSG_TYPES = 0x521`.  Every Ascension addition --
0x58D configs, 0x5C2 build activate, 0x62E/0x630 build queries, 0x722/0x725/0x726
Character Advancement, 0x727 known-entry upload, 0x9BC realm info -- is above that.
So the rule is simply: **an opcode >= 0x521 never crosses the bridge.**  Client-side
customs are answered here or dropped; the core never sees a packet it cannot parse.

    python ascension_bridge.py            # listen 8088, upstream 127.0.0.1:8086

Read-only with respect to the client.  Touches `asc_auth.account` only.
"""
import hashlib
import hmac
import os
import socket
import struct
import sys
import threading
import time
import traceback

BASE = os.path.dirname(os.path.abspath(__file__))
import runtime_paths as paths
sys.path.insert(0, BASE)

# Force UTF-8 on the streams we print to.  On Windows these default to the ANSI
# code page (cp1252), and a single non-ASCII byte in a logged packet dump then
# raises UnicodeEncodeError inside log() -- on a pump thread, which drops the
# session.  errors="replace" means a stray byte costs a "?" instead of a
# disconnect.  Best effort: a redirected or detached stream may have no
# reconfigure(), and log() carries a matching guard for that case.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# world_server.py is the canonical source for the crypt, the realm-flavour bytes
# and every Ascension-custom packet builder; importing keeps the two from drifting.
# It has no module-level side effects -- everything lives behind __main__.
from world_server import (RC4, SERVER_ENC_SEED, SERVER_DEC_SEED, smsg_realm_info,
                          REALM_FLAVOUR_WCR, REALM_FLAVOUR_COA, REALM_FLAVOUR_DEV,
                          SMSG_TUTORIAL_FLAGS, smsg_ca_active_spec,
                          build_values_update, u32le,
                          UNIT_FIELD_BYTES_0, PLAYER_END)
import chardata

# ---- configuration ----------------------------------------------------------
# Loopback by default. Set ASC_BRIDGE_HOST=0.0.0.0 to serve other machines on a
# trusted LAN; those clients need authgate.cfg with allow_remote_world = 1.
LISTEN_HOST = os.environ.get("ASC_BRIDGE_HOST", "127.0.0.1")
LISTEN_PORT = int(os.environ.get("ASC_BRIDGE_PORT", "8088"))   # 8085/8087/8088 ONLY
AC_HOST = os.environ.get("ASC_AC_HOST", "127.0.0.1")
AC_PORT = int(os.environ.get("ASC_AC_PORT", "8086"))
AC_REALM_ID = int(os.environ.get("ASC_AC_REALM_ID", "1"))
AC_BUILD = int(os.environ.get("ASC_AC_BUILD", "12340"))

LOG_PATH = paths.log_file("bridge_log.txt")
LOG_MAX_BYTES = 32 * 1024 * 1024

CLIENT_AUTH_SEED = bytes.fromhex("11223344")   # what WE challenge the client with
CHALLENGE_TAIL = bytes(32)

# The realm-flavour bytes decide which character-creation UI the client puts up,
# and only one of them produces characters AzerothCore can actually build.
#
#   DEV / LIVE  -> CanCreateHero()  -> class is forced to HERO_CLASS_ID (10),
#                  the classless "Free-Pick" hero.  Class 10 has no
#                  `playercreateinfo` row, so the core answers CHAR_CREATE_FAILED.
#   COA         -> CanCreateCoA()   -> the custom classes, ids 12..32.  Worse.
#   WCR         -> CanCreateWCR()   -> CharacterCreate_ShowRegularClasses(), the
#                  11 stock WotLK classes (CharacterCreate.lua:1676).
#
# So the bridge advertises WCR, and deliberately does NOT send SMSG_UPDATE_CONFIGS:
# CanCreateArchetype() (CharacterCreate.lua:12) is realm-name AND config, and with
# the config absent the archetype steps stay out of the flow.
# COA REALM MODE (ASC_COA=1).  Flips the flavour to COA so the client offers the
# 21 Conquest of Azeroth classes, and turns on the three things that makes them
# work on a core that cannot build them:
#   * CMSG_CHAR_CREATE remembers the CoA class, then substitutes a carrier class
#     the core CAN build (fix_char_create already did the substitution half).
#   * SMSG_CHAR_ENUM projects the CoA class back, so the character presents as
#     itself rather than as its carrier.
#   * a values update puts the CoA class in UNIT_FIELD_BYTES_0, which is the
#     dword the CA ENGINE reads -- the char-enum rewrite above does not reach it.
#     Without this the panel is labelled Tinker while the engine is still the
#     carrier Warrior: measured AE 80 / TE 71 (the stock-class essence row shared
#     by keys 1..9 and 11) instead of Tinker's 36 / 35, CanLearnID false on every
#     node, and the spec chooser's button dead because SwitchActiveChrSpec has
#     nothing valid to switch to.
#   * the CA bootstrap gains the essence budget and known-entry packets, served
#     from the character's OWN class family -- never Free-Pick's.
# Default OFF: with ASC_COA unset this file behaves exactly as before.
# Accepted as an argv flag as well as an env var: control/realms.py starts
# helpers with an `args` string and has no way to set environment variables, so
# a profile-driven CoA realm needs the flag form.
COA_MODE = (os.environ.get("ASC_COA", "") not in ("", "0")
            or "--coa" in sys.argv)

# CoA mode uses DEV, *not* REALM_FLAVOUR_COA.  This is the one place in the file
# where the correct constant is not the one whose name matches the mode, so:
#
# REALM_FLAVOUR_COA sets only +0x46.  Four consecutive sessions served it
# (b009 11:08:52, b010 11:09:15, b002 11:17:34, b009 11:22:14 on 2026-09-09) and
# every one of them died the same way -- the client took AUTH_OK, took
# SMSG_REALM_INFO, opened COP_GET_CHARACTERS, and hung up ~40 ms later without
# ever putting CMSG_CHAR_ENUM on the wire (client Logs/connection.log).  It is
# not a crash: no dump was written and Fatal.txt stayed empty, so the client is
# calling Disconnect on purpose, below Lua -- CharacterSelect.lua contains no CoA
# gate at all, and the character-select Lua has not run yet at that point.
# Every session on this bridge that DID reach a character list, across days, was
# served REALM_FLAVOUR_WCR.  The 8 flavour bytes are the only thing that differs
# between the two groups on the wire.
#
# DEV sets +0x44 instead, and world_server.py -- the implementation that is
# proven to render the CoA tree and create CoA characters -- has always used it
# (ARCHIVE_REALM_FLAVOUR = REALM_FLAVOUR_DEV).  It is not a weaker choice: the
# client derives CanCreateCoA as (+0x46 or +0x44) and CanCreateWCR as
# (+0x47 or +0x44), so the Dev byte turns on CoA creation AND classic creation,
# and it is what keeps the advancement-record filter open --
# dl = IsDevelopment || !(+0x46 || +0x47) is false under REALM_FLAVOUR_COA, which
# HIDES the late CoA classes the COA flavour is supposed to be offering.  See the
# flavour comment in world_server.py, and TROUBLESHOOTING.md under
# "dc's at character select".
#
# ASC_REALM_FLAVOUR=coa|dev|wcr|live overrides, so the next person to doubt this
# can re-run the A/B without editing code.
_FLAVOURS = {"coa": REALM_FLAVOUR_COA, "dev": REALM_FLAVOUR_DEV,
             "wcr": REALM_FLAVOUR_WCR}
BRIDGE_REALM_FLAVOUR = _FLAVOURS.get(
    os.environ.get("ASC_REALM_FLAVOUR", "").strip().lower(),
    REALM_FLAVOUR_DEV if COA_MODE else REALM_FLAVOUR_WCR)

COA_STATE = None
COA_CLASSES = None
COA_TREE = None
COA_ESSENCE = None
COA_CLASS_NAMES = {}
if COA_MODE:
    import coa_mode
    COA_STATE = coa_mode.CoAState()
    COA_CLASSES = coa_mode.CoAClasses()
    COA_TREE = coa_mode.CATree()
    COA_ESSENCE = coa_mode.Essence()
    COA_CLASS_NAMES = COA_CLASSES.names()

SMSG_AUTH_CHALLENGE = 0x1EC
CMSG_AUTH_SESSION = 0x1ED
SMSG_AUTH_RESPONSE = 0x1EE
CMSG_CHAR_CREATE = 0x036
SMSG_CHAR_ENUM = 0x03B
SMSG_REALM_INFO = 0x9BC

SMSG_MESSAGECHAT = 0x096
SMSG_LOGIN_VERIFY_WORLD = 0x236
CMSG_SET_ACTIVE_MOVER = 0x26A
# Value from this file's own CNAMES table (0x03D).  It was referenced by the CoA
# roster lookup in pump_client_to_core and never defined, which is a NameError on
# the FIRST client packet of every CoA session -- `opcode == CMSG_PLAYER_LOGIN`
# resolves the name whatever the opcode is.  It stayed invisible because the CoA
# realm-flavour bug killed the session one packet earlier, so the client had never
# sent anything for it to fire on.
CMSG_PLAYER_LOGIN = 0x03D

# CHARACTER ADVANCEMENT BOOTSTRAP.  SMSG_CA_ACTIVE_SPEC (0x725) is the ONE
# Ascension custom this bridge has to originate, and it is not optional: opening
# the CA panel without it crashes the client outright.
#
# The opcode is above FIRST_CUSTOM_OPCODE, so AzerothCore neither knows it nor
# could ever send it -- the bridge is the only thing in the chain that can.  Its
# handler (0x10171d90) does the one-time bootstrap of the whole client-side CA
# state: on the first 0x725 of a session it builds the per-GUID container and
# calls 0x10173d00, which allocates the 0x278-byte pending-build object and
# stores it at CASingleton+0x24.  The per-entry visibility filter (0x101c6bd0,
# reached from GetEntriesByClass) then does, for every entry with flags bit 19:
#       state = [CASingleton+0x24]
#       hit   = state->find(entry->id)     ; 0x10152920: mov edx,[ecx+0x24]
# With no 0x725 ever sent, state is NULL and that instruction faults on
# 0x00000024 -- ERROR #132 ACCESS_VIOLATION, Current Addon function
# SetFilteredEntries.  See world_server.py:418-437, which documents the same
# handler from the disassembly, and TROUBLESHOOTING.md for the crash report.
#
# TIMING is copied from world_server.py's flush_ca(), not invented here.  The
# handler keys its container off the LOCAL PLAYER GUID, and during the loading
# screen the client has no player object to key on -- sent with the login burst
# it arrives before the thing it fills into exists.  CMSG_SET_ACTIVE_MOVER is
# the client stating that the player object now exists and the loading screen is
# done, so that is the trigger, with a timer as the safety net for a session
# where it never arrives.
#
# Armed on EVERY SMSG_LOGIN_VERIFY_WORLD, not once per socket: one TCP session
# can enter the world repeatedly (logout -> character select -> Enter World),
# and a second entry can be a different character, whose GUID needs its own
# container.  Re-sending for a GUID that already has one is a no-op in the
# handler.
#
# DELIBERATELY 0x725 ALONE.  world_server.py follows it with 5600 x 0x722
# (essence budget) and a 0x726 (known entries), both of which are archive state
# that AzerothCore does not have and this bridge cannot invent.  Their absence
# costs visibility of bit-19 entries -- state->find() answers "not known", so
# those entries render HIDDEN rather than faulting -- which is the documented
# "spec" mode, and is the whole point: the panel opens and enumerates instead of
# taking the client down.  Adding packets to this stream is also how the bridge
# has broken the client before (Ascension widened 0x266/0x267 to 10 bytes), so
# it stays at the minimum that fixes the fault.
SMSG_CA_ACTIVE_SPEC = 0x725
SMSG_CA_ESSENCE_BUDGET = 0x722
SMSG_CA_KNOWN_ENTRIES = 0x726
CMSG_CA_KNOWN_ENTRIES_UPLOAD = 0x727
# Stock 3.3.5a, not a custom.  The bridge already forwards the core's own
# SMSG_UPDATE_OBJECTs untouched; this is one extra VALUES block on a GUID the
# client demonstrably already has (it is sent on CMSG_SET_ACTIVE_MOVER, which is
# the client telling us it has its player object).
SMSG_UPDATE_OBJECT = 0x0A9
# Set ASC_COA_CLASS_BYTE=0 to serve the CoA class in the character list but leave
# UNIT_FIELD_BYTES_0 as the core wrote it -- i.e. to reproduce the pre-fix
# behaviour without editing code, the way ASC_REALM_FLAVOUR does for the flavour.
COA_WRITE_CLASS_BYTE = os.environ.get("ASC_COA_CLASS_BYTE", "1") not in ("", "0")
CA_BOOTSTRAP = os.environ.get("ASC_CA_BOOTSTRAP", "1") not in ("", "0")
CA_BOOTSTRAP_FALLBACK = float(os.environ.get("ASC_CA_FALLBACK", "10"))
CA_ACTIVE_INDEX = int(os.environ.get("ASC_CA_ACTIVE_INDEX", "0"))
CA_SPEC_SLOTS = int(os.environ.get("ASC_CA_SPEC_SLOTS", "1"))

# ARCHIVE IDENTIFICATION.  The client's realmList CVar is restored to the live
# address by native code before the player is in world (measured 2026-09-02:
# GetCVar("realmList") -> 51.210.230.10 on a session established to
# 127.0.0.1:8088), so GlobalOverwrites.lua's ASC_IsArchive() cannot tell where
# it is running once the world is loaded.  Since that file is shared with the
# LIVE install through the client-ascension junction, guessing is not an option
# -- so the archive announces itself and live simply never does.  One system
# chat line, a few seconds after SMSG_LOGIN_VERIFY_WORLD.
ARCHIVE_TOKEN = "ASCARCHIVE-LOCAL-REALM"
ARCHIVE_HELLO = ("Ascension archive realm (%s).  /asctal opens the talent tree."
                 % ARCHIVE_TOKEN)
ARCHIVE_HELLO_DELAY = float(os.environ.get("ASC_HELLO_DELAY", "6"))

# BISECTING A CLIENT CRASH.  Set ASC_DROP_OPCODES to a space/comma separated
# list of server->client opcodes ("0x4C0 0x12A") and the bridge will log them
# and then NOT forward them.  This exists because the client started dying at
# 0x6E7A5BCE inside Extensions.dll on the very millisecond of CMSG_PLAYER_LOGIN,
# with an identical stack every time, once the character had seven talents
# instead of three -- and the only way to find out which login packet does it is
# to take them away one at a time.
SMSG_SET_FLAT_SPELL_MODIFIER = 0x266
SMSG_SET_PCT_SPELL_MODIFIER = 0x267

DROP_OPCODES = frozenset(
    int(x, 0) for x in
    os.environ.get("ASC_DROP_OPCODES", "").replace(",", " ").split())

# The login spell-mod resend (0x266/0x267) is the PROVEN carrier of the
# 0x6E7A5BCE crash: with talents held at {11069, 11070}, forwarding it crashed
# 3/3 and dropping it logged in 2/2, while dropping an unrelated opcode still
# crashed.  ASC_SPELLMOD_MODE reshapes the stream so the exact trigger can be
# isolated without touching the talent set:
#   firstop  only the first effect index per (opcode, op) is forwarded
#   eff0     every packet rewritten to effect index 0
#   uniq     exact duplicate (op, eff, val) triples suppressed
#   one      only the very first spell-mod packet of the login
SPELLMOD_MODE = os.environ.get("ASC_SPELLMOD_MODE", "").strip().lower()
SPELLMOD_BODY_BYTES = 16


CHAT_MSG_SYSTEM = 0x00
LANG_UNIVERSAL = 0


def smsg_system_chat(text):
    """SMSG_MESSAGECHAT the way the 3.3.5 client parses a default-path message:
    uint8 type, uint32 language, uint64 sender, uint32 flags, uint64 receiver,
    uint32 len (NUL included), the text, uint8 chatTag.  Checked against a real
    packet rather than assumed: a 7-character SAY logged 38 bytes, and
    1+4+8+4+8+4+8+1 is exactly 38."""
    msg = text.encode("utf-8") + b"\x00"
    return (struct.pack("<BIQIQI", CHAT_MSG_SYSTEM, LANG_UNIVERSAL, 0, 0, 0, len(msg))
            + msg + b"\x00")


FIRST_CUSTOM_OPCODE = 0x521          # NUM_MSG_TYPES; at or above this is Ascension's

# AzerothCore's playable classes in 3.3.5a.  Ascension's classless realms send a
# Character-Advancement class byte (10 = Tinker, up to 32) that has no
# `playercreateinfo` row, so the core would refuse the creation outright.
AC_VALID_CLASSES = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11}
# A core carrying the CoA class work has real playercreateinfo rows for 12..32, so
# those classes must NOT be substituted -- they build natively.  coa_mode owns that
# set (and its ASC_AC_VALID_CLASSES override); adopt it rather than shadowing it
# with the stock list, which is what silently forced every CoA class through the
# carrier-class path even on a core that could build it.
if COA_MODE:
    AC_VALID_CLASSES = coa_mode.AC_VALID_CLASSES
AC_FALLBACK_CLASS = int(os.environ.get("ASC_FALLBACK_CLASS", "1"))   # Warrior

# Stock 3.3.5a race/class pairs.  Player::Create refuses an invalid pair outright
# ("Possible hacking-attempt ... invalid race/class pair"), so a carrier class is
# only usable if the chosen RACE can actually be it: race 10 (Blood Elf) has no
# Warrior, and substituting the class-1 default there fails creation every time.
AC_RACE_CLASSES = {
    1:  {1, 2, 4, 5, 6, 8, 9},        # Human
    2:  {1, 3, 4, 5, 6, 7, 9},        # Orc
    3:  {1, 2, 3, 4, 5, 6},           # Dwarf
    4:  {1, 3, 4, 5, 6, 11},          # Night Elf
    5:  {1, 4, 5, 6, 8, 9},           # Undead
    6:  {1, 3, 6, 7, 11},             # Tauren
    7:  {1, 4, 6, 8, 9},              # Gnome
    8:  {1, 3, 4, 5, 6, 8, 9},        # Troll
    10: {2, 3, 4, 5, 6, 8, 9},        # Blood Elf
    11: {1, 2, 3, 5, 6, 8},           # Draenei
}


def carrier_class_for(race):
    """A class the core can build AND this race is allowed to be."""
    allowed = AC_RACE_CLASSES.get(race)
    if allowed is None:                       # unknown/custom race: keep the default
        return AC_FALLBACK_CLASS
    if AC_FALLBACK_CLASS in allowed:
        return AC_FALLBACK_CLASS
    usable = sorted(allowed & set(AC_VALID_CLASSES))
    return usable[0] if usable else AC_FALLBACK_CLASS

# Set ASC_BRIDGE_TRACE=1 to log every opcode in both directions.  Off, only the
# packets below are named -- enough to tell "the client never asked" apart from
# "the core never answered", which is the question that actually comes up.
TRACE = os.environ.get("ASC_BRIDGE_TRACE", "") not in ("", "0")

CMSG_PING = 0x1DC
CMSG_MESSAGECHAT = 0x095

# Opcode names are DIRECTION-SPECIFIC: 0x12A is SMSG_INITIAL_SPELLS going out
# and nothing at all coming in, and an earlier single flat table mislabelled it
# (and 0x12B, 0x17B, 0x17F, 0x12E ...) which made the world log actively
# misleading.  Two tables now, both transcribed from the core's own header,
# src/server/game/Server/Protocol/Opcodes.h -- if you add one, copy the value
# from there, do not guess it.
CNAMES = {                                  # client -> core
    0x036: "CMSG_CHAR_CREATE",       0x037: "CMSG_CHAR_ENUM",
    0x038: "CMSG_CHAR_DELETE",       0x03D: "CMSG_PLAYER_LOGIN",
    0x05C: "CMSG_QUEST_QUERY",       0x095: "CMSG_MESSAGECHAT",
    0x108: "CMSG_AUTOSTORE_LOOT_ITEM",
    0x12E: "CMSG_CAST_SPELL",        0x141: "CMSG_ATTACKSWING",
    0x142: "CMSG_ATTACKSTOP",        0x15D: "CMSG_LOOT",
    0x15E: "CMSG_LOOT_MONEY",        0x15F: "CMSG_LOOT_RELEASE",
    0x17B: "CMSG_GOSSIP_HELLO",      0x17C: "CMSG_GOSSIP_SELECT_OPTION",
    0x182: "CMSG_QUESTGIVER_STATUS_QUERY",
    0x184: "CMSG_QUESTGIVER_HELLO",  0x186: "CMSG_QUESTGIVER_QUERY_QUEST",
    0x189: "CMSG_QUESTGIVER_ACCEPT_QUEST",
    0x18A: "CMSG_QUESTGIVER_COMPLETE_QUEST",
    0x18C: "CMSG_QUESTGIVER_REQUEST_REWARD",
    0x18E: "CMSG_QUESTGIVER_CHOOSE_REWARD",
    0x19E: "CMSG_LIST_INVENTORY",    0x1B0: "CMSG_TRAINER_LIST",
    0x213: "CMSG_UNLEARN_TALENTS",   0x251: "CMSG_LEARN_TALENT",
    0x26A: "CMSG_SET_ACTIVE_MOVER",  0x2E7: "CMSG_WARDEN_DATA",
}
SNAMES = {                                  # core -> client
    0x03A: "SMSG_CHAR_CREATE",       0x03B: "SMSG_CHAR_ENUM",
    0x03C: "SMSG_CHAR_DELETE",       0x03E: "SMSG_NEW_WORLD",
    0x03F: "SMSG_TRANSFER_PENDING",  0x041: "SMSG_CHARACTER_LOGIN_FAILED",
    0x05D: "SMSG_QUEST_QUERY_RESPONSE",
    0x096: "SMSG_MESSAGECHAT",       0x0A9: "SMSG_UPDATE_OBJECT",
    0x0FD: "SMSG_TUTORIAL_FLAGS",    0x127: "SMSG_SET_PROFICIENCY",
    0x129: "SMSG_ACTION_BUTTONS",    0x12A: "SMSG_INITIAL_SPELLS",
    0x12B: "SMSG_LEARNED_SPELL",     0x12C: "SMSG_SUPERCEDED_SPELL",
    0x130: "SMSG_CAST_FAILED",       0x131: "SMSG_SPELL_START",
    0x132: "SMSG_SPELL_GO",          0x143: "SMSG_ATTACKSTART",
    0x144: "SMSG_ATTACKSTOP",        0x145: "SMSG_ATTACKSWING_NOTINRANGE",
    0x146: "SMSG_ATTACKSWING_BADFACING",
    0x148: "SMSG_ATTACKSWING_DEADTARGET",
    0x149: "SMSG_ATTACKSWING_CANT_ATTACK",
    0x160: "SMSG_LOOT_RESPONSE",     0x161: "SMSG_LOOT_RELEASE_RESPONSE",
    0x17D: "SMSG_GOSSIP_MESSAGE",    0x17E: "SMSG_GOSSIP_COMPLETE",
    0x183: "SMSG_QUESTGIVER_STATUS", 0x185: "SMSG_QUESTGIVER_QUEST_LIST",
    0x188: "SMSG_QUESTGIVER_QUEST_DETAILS",
    0x18B: "SMSG_QUESTGIVER_REQUEST_ITEMS",
    0x18D: "SMSG_QUESTGIVER_OFFER_REWARD",
    0x18F: "SMSG_QUESTGIVER_QUEST_INVALID",
    0x191: "SMSG_QUESTGIVER_QUEST_COMPLETE",
    0x192: "SMSG_QUESTGIVER_QUEST_FAILED",
    0x19F: "SMSG_LIST_INVENTORY",    0x1B1: "SMSG_TRAINER_LIST",
    0x1D0: "SMSG_LOG_XPGAIN",        0x1D4: "SMSG_LEVELUP_INFO",
    0x1EE: "SMSG_AUTH_RESPONSE",     0x1F6: "SMSG_COMPRESSED_UPDATE_OBJECT",
    0x224: "SMSG_GOSSIP_POI",        0x236: "SMSG_LOGIN_VERIFY_WORLD",
    0x24C: "SMSG_SPELLLOGEXECUTE",   0x24E: "SMSG_PERIODICAURALOG",
    0x250: "SMSG_SPELLNONMELEEDAMAGELOG",
    0x2E6: "SMSG_WARDEN_DATA",       0x418: "SMSG_QUESTGIVER_STATUS_MULTIPLE",
    0x4C0: "SMSG_TALENTS_INFO",      0x9BC: "SMSG_REALM_INFO",
}


# Named, but far too frequent in the world to log every time -- object updates
# and the movement/status chatter arrive by the hundred per second.  TRACE
# still shows them.
NOISY = {0x0A9, 0x1F6, 0x182, 0x183, 0x418, 0x24C, 0x24E, 0x250}


def opname(op, c2s):
    t = CNAMES if c2s else SNAMES
    return t.get(op, "0x%03X" % op)


# NOTE: these are the ASCENSION client's chat-type numbers, not AzerothCore's --
# a plain SendChatMessage("x") arrives as type 1, not 0.  The number is printed
# alongside the name so a wrong guess here is visible instead of misleading.
CHAT_TYPES = {1: "SAY", 2: "PARTY", 3: "RAID", 4: "GUILD", 6: "OFFICER",
              7: "YELL", 8: "WHISPER", 0x0F: "EMOTE", 0x12: "CHANNEL"}


def describe_chat(body):
    """CMSG_MESSAGECHAT: uint32 type, uint32 language, then one or two cstrings
    depending on the type.  Logged in full because it is the ONLY cheap textual
    read-back channel out of the running client -- Logs/WoWChatLog.txt does not
    flush while the client is up and dprint() does not reach Logs/LUA.txt in the
    world, so an in-game probe reports by saying its answer:
        /run SendChatMessage("ASCPROBE hp="..UnitHealth("player"))
    and the answer lands in this file a fraction of a second later."""
    if len(body) < 8:
        return "short (%d B)" % len(body)
    typ, lang = struct.unpack("<II", body[:8])
    parts = [p.decode("utf-8", "replace") for p in body[8:].split(b"\x00") if p]
    return "%s(%d): %s" % (CHAT_TYPES.get(typ, "type"), typ, " | ".join(parts))


CMSG_LEARN_TALENT = 0x251
SMSG_CAST_FAILED = 0x130
SMSG_TRAINER_LIST = 0x1B1
# Ascension is a classless realm and its client THROWS AWAY every service in a
# CLASS trainer list.  Measured, not guessed: the core sends 6 services, all
# state=0 (green) -- see describe_trainer_list below -- the window opens, the
# greeting out of that same packet is displayed by GetTrainerGreetingText(),
# and GetNumTrainerServices() still answers 0 with available/unavailable/used
# all filtered in.  Change nothing but the uint32 trainer type from 0 (CLASS)
# to 2 (TRADESKILL) and the identical list renders: 7 rows, every spell green,
# and Train sends CMSG_TRAINER_BUY_SPELL as usual.  The header reads "Recipes"
# instead of the class name, which is the whole price.
#
# Only type 0 is touched -- profession trainers are already 2, and mount (1)
# and pet (3) trainers render fine as themselves.
# ASC_TRAINER_TYPE overrides the replacement value; ASC_TRAINER_TYPE=0 turns
# the rewrite off and shows the core's real behaviour again.
TRAINER_TYPE_OVERRIDE = int(os.environ.get("ASC_TRAINER_TYPE", "2"))


def describe_trainer_list(body):
    """SMSG_TRAINER_LIST: uint64 guid, uint32 type, uint32 count,
    then count * 38-byte records, then the greeting as a cstring."""
    if len(body) < 16:
        return "short (%d B)" % len(body)
    guid, ttype, count = struct.unpack_from("<QII", body, 0)
    out = ["guid=0x%X type=%d count=%d" % (guid, ttype, count)]
    off = 16
    for i in range(min(count, 12)):
        if off + 38 > len(body):
            out.append("  #%d TRUNCATED" % i)
            break
        spell, state, cost = struct.unpack_from("<IBI", body, off)
        req_level = body[off + 17]
        out.append("  #%d spell=%d state=%d cost=%d reqLevel=%d"
                   % (i, spell, state, cost, req_level))
        off += 38
    tail = body[off:].split(b"\x00")[0]
    out.append("  greeting=%r" % tail.decode("utf-8", "replace"))
    return "\n".join(out)



def describe_learn_talent(body):
    """CMSG_LEARN_TALENT: uint32 talentId, uint32 requestedRank.  The id comes
    out of the CLIENT's Talent.dbc; the core looks it up in its own.  When the
    two DBC sets disagree the learn is refused with no error text anywhere, so
    the id is the only way to tell 'the client asked for the wrong talent' apart
    from 'the core would not grant it'."""
    if len(body) < 8:
        return "short (%d B)" % len(body)
    tid, rank = struct.unpack("<II", body[:8])
    return "talentId=%d rank=%d" % (tid, rank)


def describe_cast_failed(body):
    """SMSG_CAST_FAILED: uint8 castCount, uint32 spellId, uint8 result."""
    if len(body) < 6:
        return "short (%d B)" % len(body)
    cnt = body[0]
    spell = struct.unpack("<I", body[1:5])[0]
    res = body[5]
    return "spell=%d result=0x%02X count=%d" % (spell, res, cnt)


def worth_logging(op, c2s):
    t = CNAMES if c2s else SNAMES
    return TRACE or (op in t and op not in NOISY)


# ResponseCodes, src/server/shared/SharedDefines.h:3622.  SMSG_CHAR_CREATE and
# SMSG_CHARACTER_LOGIN_FAILED are a single byte out of this enum, and that byte
# is the difference between "the client never asked" and "the core said no, for
# this reason" -- so it always gets spelled out, never logged as a length.
RESPONSE_CODES = {
    0x2E: "CHAR_CREATE_IN_PROGRESS",   0x2F: "CHAR_CREATE_SUCCESS",
    0x30: "CHAR_CREATE_ERROR",         0x31: "CHAR_CREATE_FAILED",
    0x32: "CHAR_CREATE_NAME_IN_USE",   0x33: "CHAR_CREATE_DISABLED",
    0x34: "CHAR_CREATE_PVP_TEAMS_VIOLATION",
    0x35: "CHAR_CREATE_SERVER_LIMIT",  0x36: "CHAR_CREATE_ACCOUNT_LIMIT",
    0x37: "CHAR_CREATE_SERVER_QUEUE",  0x38: "CHAR_CREATE_ONLY_EXISTING",
    0x39: "CHAR_CREATE_EXPANSION",     0x3A: "CHAR_CREATE_EXPANSION_CLASS",
    0x3B: "CHAR_CREATE_LEVEL_REQUIREMENT",
    0x3C: "CHAR_CREATE_UNIQUE_CLASS_LIMIT",
    0x3D: "CHAR_CREATE_CHARACTER_IN_GUILD",
    0x3E: "CHAR_CREATE_RESTRICTED_RACECLASS",
    0x3F: "CHAR_CREATE_CHARACTER_CHOOSE_RACE",
    0x4C: "CHAR_LOGIN_IN_PROGRESS",    0x4D: "CHAR_LOGIN_SUCCESS",
    0x4E: "CHAR_LOGIN_NO_WORLD",       0x4F: "CHAR_LOGIN_DUPLICATE_CHARACTER",
    0x50: "CHAR_LOGIN_NO_INSTANCES",   0x51: "CHAR_LOGIN_FAILED",
    0x52: "CHAR_LOGIN_DISABLED",       0x53: "CHAR_LOGIN_NO_CHARACTER",
}
SMSG_CHAR_CREATE = 0x03A
CHAR_CREATE_SUCCESS = 0x2F        # ResponseCodes, see RESPONSE_CODES above
SMSG_CHARACTER_LOGIN_FAILED = 0x041


def describe_char_create(body):
    """CMSG_CHAR_CREATE: cstring name, then race, class, gender, skin, face,
    hairStyle, hairColour, facialHair, outfitId -- one byte each."""
    try:
        z = body.index(b"\x00")
    except ValueError:
        return "unparsable (%d B)" % len(body)
    t = body[z + 1:]
    if len(t) < 9:
        return "name=%r short tail (%d B)" % (body[:z], len(t))
    return ("name=%s race=%d class=%d gender=%d skin=%d face=%d hair=%d/%d "
            "facial=%d outfit=%d"
            % ((body[:z].decode("ascii", "replace"),) + tuple(t[:9])))

_log_lock = threading.Lock()


def log(msg):
    """Never raise.  log() is called from both pump threads, so an exception in
    here does not just lose a line -- it unwinds the pump and drops the session.

    The stdout write is the dangerous half: a Windows console defaults to cp1252,
    and any non-ASCII byte in a logged string (a character name, a chat line --
    describe_chat logs chat in full) makes print() raise UnicodeEncodeError.  That
    surfaced as an instant disconnect on entering the world.  The streams are
    reconfigured to UTF-8 at import; this guard covers the case where they could
    not be (already detached, or a stream that has no reconfigure)."""
    line = "[%s] %s" % (time.strftime("%H:%M:%S"), msg)
    with _log_lock:
        try:
            print(line, flush=True)
        except UnicodeEncodeError:
            print(line.encode("ascii", "replace").decode("ascii"), flush=True)
        except Exception:
            pass
        try:
            with open(LOG_PATH, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass


def rotate_log():
    try:
        if os.path.exists(LOG_PATH) and os.path.getsize(LOG_PATH) > LOG_MAX_BYTES:
            os.replace(LOG_PATH, LOG_PATH + ".1")
    except Exception:
        pass


# ---- ARC4-drop1024, as AuthCrypt uses it ------------------------------------
def make_crypt(session_key40):
    """Return (s2c, c2s) stream states for talking to the core AS A CLIENT.

    The seed names are from the CORE's point of view -- it ENCRYPTS what it
    sends and DECRYPTS what it receives -- so our roles are the mirror image:
    s2c decrypts headers the core sent us, c2s encrypts headers we send it."""
    s2c = RC4(hmac.new(SERVER_ENC_SEED, session_key40, hashlib.sha1).digest())
    c2s = RC4(hmac.new(SERVER_DEC_SEED, session_key40, hashlib.sha1).digest())
    s2c.crypt(bytes(1024))
    c2s.crypt(bytes(1024))
    return s2c, c2s


# ---- socket helpers ---------------------------------------------------------
def recv_exact(sock, n):
    buf = b""
    while len(buf) < n:
        try:
            chunk = sock.recv(n - len(buf))
        except (socket.timeout, OSError):
            return None
        if not chunk:
            return None
        buf += chunk
    return buf


def frame_s2c(opcode, body, enc=None):
    """Server->client: uint16 BE size (opcode+body) + uint16 LE opcode."""
    size = len(body) + 2
    if size > 0x7FFF:
        hdr = bytes([0x80 | ((size >> 16) & 0xFF), (size >> 8) & 0xFF, size & 0xFF,
                     opcode & 0xFF, (opcode >> 8) & 0xFF])
    else:
        hdr = bytes([(size >> 8) & 0xFF, size & 0xFF, opcode & 0xFF, (opcode >> 8) & 0xFF])
    if enc is not None:
        hdr = enc.crypt(hdr)
    return hdr + body


def frame_c2s(opcode, body, enc=None):
    """Client->server: uint16 BE size (opcode+body) + uint32 LE opcode."""
    size = len(body) + 4
    hdr = struct.pack(">H", size) + struct.pack("<I", opcode)
    if enc is not None:
        hdr = enc.crypt(hdr)
    return hdr + body


def read_c2s(sock, dec=None):
    """Read one client->server packet. Returns (opcode, body) or None."""
    hdr = recv_exact(sock, 6)
    if hdr is None:
        return None
    if dec is not None:
        hdr = dec.crypt(hdr)
    size = struct.unpack(">H", hdr[:2])[0]
    opcode = struct.unpack("<I", hdr[2:6])[0]
    blen = size - 4
    if blen < 0 or blen > 0x10000:
        raise ValueError("implausible C->S header: size=%d opcode=0x%X" % (size, opcode))
    body = recv_exact(sock, blen) if blen else b""
    if body is None:
        return None
    return opcode, body


def read_s2c(sock, dec=None):
    """Read one server->client packet. The size field is 2 or 3 bytes; the flag
    lives in the top bit of the FIRST byte, so it has to be decrypted alone before
    we know how many more bytes belong to this header."""
    b0 = recv_exact(sock, 1)
    if b0 is None:
        return None
    if dec is not None:
        b0 = dec.crypt(b0)
    if b0[0] & 0x80:
        rest = recv_exact(sock, 4)
        if rest is None:
            return None
        if dec is not None:
            rest = dec.crypt(rest)
        size = ((b0[0] & 0x7F) << 16) | (rest[0] << 8) | rest[1]
        opcode = rest[2] | (rest[3] << 8)
    else:
        rest = recv_exact(sock, 3)
        if rest is None:
            return None
        if dec is not None:
            rest = dec.crypt(rest)
        size = (b0[0] << 8) | rest[0]
        opcode = rest[1] | (rest[2] << 8)
    blen = size - 2
    if blen < 0 or blen > 0x800000:
        raise ValueError("implausible S->C header: size=%d opcode=0x%X" % (size, opcode))
    body = recv_exact(sock, blen) if blen else b""
    if body is None:
        return None
    return opcode, body


# ---- CMSG_AUTH_SESSION ------------------------------------------------------
def parse_auth_session(body):
    f = {}
    p = 0

    def u32():
        nonlocal p
        v = struct.unpack_from("<I", body, p)[0]
        p += 4
        return v

    f["build"] = u32()
    f["loginServerID"] = u32()
    z = body.index(b"\x00", p)
    f["account"] = body[p:z]
    p = z + 1
    f["loginServerType"] = u32()
    f["clientSeed"] = body[p:p + 4]
    p += 4
    f["regionID"] = u32()
    f["battlegroupID"] = u32()
    f["realmID"] = u32()
    f["dosResponse"] = body[p:p + 8]
    p += 8
    f["digest"] = body[p:p + 20]
    p += 20
    f["addon_raw"] = body[p:]
    return f


def build_auth_session(f, build, realm_id, digest):
    out = struct.pack("<I", build)
    out += struct.pack("<I", f["loginServerID"])
    out += f["account"] + b"\x00"
    out += struct.pack("<I", f["loginServerType"])
    out += f["clientSeed"]
    out += struct.pack("<I", f["regionID"])
    out += struct.pack("<I", f["battlegroupID"])
    out += struct.pack("<I", realm_id)
    out += f["dosResponse"]
    out += digest
    out += f["addon_raw"]
    return out


def auth_digest(account, client_seed, server_seed, key40):
    h = hashlib.sha1()
    h.update(account)
    h.update(b"\x00\x00\x00\x00")
    h.update(client_seed)
    h.update(server_seed)
    h.update(key40)
    return h.digest()


# ---- the one thing we write: asc_auth.account.session_key --------------------
_DB_CFG = None


def db_config():
    """Read the core's own LoginDatabaseInfo rather than keeping a second copy of
    the credentials here."""
    global _DB_CFG
    if _DB_CFG is not None:
        return _DB_CFG
    conf = paths.path("worldserver_conf", "ASC_WORLDSERVER_CONF",
                      os.path.join(os.path.dirname(os.path.dirname(BASE)),
                                   "server-ascension", "configs", "worldserver.conf"))
    with open(conf, encoding="utf-8", errors="replace") as fh:
        for line in fh:
            if line.startswith("LoginDatabaseInfo"):
                val = line.split("=", 1)[1].strip().strip('"')
                host, port, user, pw, dbname = val.split(";")
                _DB_CFG = dict(host=host, port=int(port), user=user,
                               password=pw, database=dbname)
                return _DB_CFG
    raise RuntimeError("no LoginDatabaseInfo in %s" % conf)


def stage_session_key(account, key40):
    """Create the account if it is new, then hand the core the key we will sign with.

    `salt`/`verifier` stay zeroed: they are the SRP6 material the *auth* server
    uses, and nothing in the world handshake reads them.  This account exists only
    so the core has somewhere to keep a session key."""
    import pymysql
    cfg = db_config()
    conn = pymysql.connect(charset="utf8mb4", autocommit=True, **cfg)
    try:
        with conn.cursor() as cur:
            cur.execute("SELECT id FROM account WHERE username = %s", (account,))
            row = cur.fetchone()
            if row is None:
                cur.execute(
                    "INSERT INTO account (username, salt, verifier, session_key,"
                    " email, reg_mail, expansion, last_ip, os)"
                    " VALUES (%s, %s, %s, %s, '', '', 2, '127.0.0.1', 'Win')",
                    (account, bytes(32), bytes(32), key40))
                cur.execute("SELECT id FROM account WHERE username = %s", (account,))
                row = cur.fetchone()
                if row is None:
                    # The INSERT reported success but the name does not read
                    # back, so what MySQL stored is not what we sent.  With
                    # AzerothCore's default sql_mode (no STRICT_TRANS_TABLES)
                    # an over-long string is TRUNCATED silently rather than
                    # rejected, and `username` is varchar(32).  Without this
                    # check the next line is row[0] on None, and the resulting
                    # "'NoneType' is not subscriptable" says nothing about the
                    # real cause.  Name it instead.
                    raise RuntimeError(
                        "account %r did not read back after INSERT -- almost "
                        "certainly truncated to fit account.username "
                        "varchar(32) (len was %d) under a non-strict sql_mode"
                        % (account, len(account)))
                log("    created asc_auth account %r (id %s)" % (account, row[0]))
            # `os` is not cosmetic: with Warden enabled the core rejects the
            # session outright (AUTH_REJECT, "invalid client OS ()") if the
            # column is anything but 'Win' or 'OSX', and the rejection arrives
            # AFTER the crypt is armed, so it reads as a crypt bug if you only
            # look at the wire.
            cur.execute("UPDATE account SET session_key = %s, os = 'Win',"
                        " locked = 0, online = 0 WHERE id = %s",
                        (key40, row[0]))
            return int(row[0])
    finally:
        conn.close()


# ---- one bridged session ----------------------------------------------------
class Session(object):
    def __init__(self, client, cid, addr):
        self.client = client
        self.cid = cid
        self.addr = addr
        self.core = None
        self.s2c = None          # decrypts headers the core sends us
        self.c2s = None          # encrypts headers we send the core
        self.alive = True
        self.account = b"?"
        self.customs_sent = False
        self.reason = None
        # (name, class) staged by fix_char_create, committed to COA_STATE only
        # once the core answers CHAR_CREATE_SUCCESS.
        self.pending_coa_class = None
        # Two threads write to the client socket -- the core->client pump and,
        # off timers, the archive hello and the CA bootstrap -- and the client
        # headers are plaintext, so a half-written packet is not recoverable by
        # anything downstream.  One lock around every client write.
        self.client_lock = threading.Lock()
        self.ca_lock = threading.Lock()
        self.ca_armed = False        # world entry seen, 0x725 still owed
        self.coa_roster = {}         # guid -> name, harvested from SMSG_CHAR_ENUM
        self.coa_looks = {}          # guid -> (race, gender), same packet
        self.coa_guid = None         # the GUID this session is playing
        self.coa_name = None         # the character this session is playing
        self.ca_timer = None

    def log(self, msg):
        log("b%03d: %s" % (self.cid, msg))

    def send_client(self, opcode, body):
        """Write one plaintext packet to the client. Returns False once the
        socket is gone, so callers on timer threads can give up quietly."""
        try:
            with self.client_lock:
                self.client.sendall(frame_s2c(opcode, body))
        except OSError:
            return False
        return True

    # -- handshake ------------------------------------------------------------
    def run(self):
        try:
            self.handshake()
        except Exception as e:
            self.log("!! handshake failed: %r" % (e,))
            self.close()
            return
        t = threading.Thread(target=self._guarded,
                             args=(self.pump_core_to_client, "core"), daemon=True)
        t.start()
        try:
            self._guarded(self.pump_client_to_core, "client")
        finally:
            self.close(self.reason)

    def _guarded(self, pump, which):
        """Run a pump and never let an exception vanish.

        run() is a thread target, so anything that is not OSError/ValueError
        escapes to threading's excepthook and out to a stderr nobody is reading.
        The session then ends via close(None), which logs a bare "session closed"
        -- indistinguishable from the client hanging up. That is precisely how the
        CoA character-select disconnect presented on 2026-09-09, and how the
        char-create hang presented before it: a crash wearing a clean disconnect's
        clothes. The traceback IS the diagnosis, so it goes in bridge_log.txt
        where someone will actually find it."""
        try:
            pump()
        except Exception:
            self.reason = self.reason or (
                "%s pump crashed -- traceback above" % which)
            self.log("!! %s pump crashed:\n%s" % (which, traceback.format_exc()))

    def handshake(self):
        # 1. challenge the client exactly as world_server.py does
        body = struct.pack("<I", 1) + CLIENT_AUTH_SEED + CHALLENGE_TAIL
        self.client.sendall(frame_s2c(SMSG_AUTH_CHALLENGE, body))

        pkt = read_c2s(self.client)
        if pkt is None:
            raise RuntimeError("client closed before CMSG_AUTH_SESSION")
        opcode, abody = pkt
        if opcode != CMSG_AUTH_SESSION:
            raise RuntimeError("expected CMSG_AUTH_SESSION, got 0x%X" % opcode)
        f = parse_auth_session(abody)
        self.account = f["account"]
        self.log("CMSG_AUTH_SESSION build=%d account=%r realmID=%d addon=%dB"
                 % (f["build"], f["account"], f["realmID"], len(f["addon_raw"])))

        # 2. choose a session key and give it to the core BEFORE we connect
        key40 = os.urandom(40)
        acct_id = stage_session_key(f["account"].decode("utf-8", "replace"), key40)
        self.log("staged session key for asc_auth account id %d" % acct_id)

        # 3. authenticate to the core as a client
        self.core = socket.create_connection((AC_HOST, AC_PORT), timeout=30)
        self.core.settimeout(None)
        pkt = read_s2c(self.core)
        if pkt is None:
            raise RuntimeError("core closed before SMSG_AUTH_CHALLENGE")
        opcode, cbody = pkt
        if opcode != SMSG_AUTH_CHALLENGE:
            raise RuntimeError("core sent 0x%X, not SMSG_AUTH_CHALLENGE" % opcode)
        server_seed = cbody[4:8]
        self.log("core seed %s" % server_seed.hex())

        digest = auth_digest(f["account"], f["clientSeed"], server_seed, key40)
        out = build_auth_session(f, AC_BUILD, AC_REALM_ID, digest)
        self.core.sendall(frame_c2s(CMSG_AUTH_SESSION, out))
        self.log("-> core CMSG_AUTH_SESSION (build %d -> %d, realm %d -> %d)"
                 % (f["build"], AC_BUILD, f["realmID"], AC_REALM_ID))

        # 4. from here the core's headers are encrypted
        self.s2c, self.c2s = make_crypt(key40)

        # Forward whatever the core sends until its verdict arrives, so a refusal
        # is named in the log instead of showing up later as a silent hang.
        for _ in range(8):
            pkt = read_s2c(self.core, self.s2c)
            if pkt is None:
                raise RuntimeError("core closed instead of answering the auth session")
            opcode, rbody = pkt
            self.client.sendall(frame_s2c(opcode, rbody))
            if opcode == SMSG_AUTH_RESPONSE:
                res = rbody[0] if rbody else 0xFF
                self.log("core AuthResult 0x%02X (%s)"
                         % (res, "AUTH_OK" if res == 0x0C else "REFUSED"))
                if res != 0x0C:
                    raise RuntimeError("core refused the session")
                return
            self.log("<- core 0x%03X (%d B) before the auth response" % (opcode, len(rbody)))
        raise RuntimeError("core never sent SMSG_AUTH_RESPONSE")

    # -- pumps ----------------------------------------------------------------
    def pump_client_to_core(self):
        while self.alive:
            try:
                pkt = read_c2s(self.client)
            except ValueError as e:
                self.log("!! client stream: %s" % e)
                break
            except OSError as e:
                self.log("!! client socket: %r" % (e,))
                break
            if pkt is None:
                self.reason = self.reason or "client hung up (EOF)"
                break
            opcode, body = pkt
            if opcode == CMSG_CHAR_CREATE:
                self.log("   C->S CMSG_CHAR_CREATE %s" % describe_char_create(body))
            elif opcode == CMSG_LEARN_TALENT:
                self.log("   C->S CMSG_LEARN_TALENT %s" % describe_learn_talent(body))
            elif opcode == CMSG_MESSAGECHAT:
                self.log("   C->S chat %s" % describe_chat(body))
            elif worth_logging(opcode, True) and opcode != CMSG_PING:
                self.log("   C->S %s (%d B)" % (opname(opcode, True), len(body)))
            # CMSG_PLAYER_LOGIN carries the GUID and nothing else, so the name
            # this session is playing is resolved through the roster harvested
            # from the character list the client was just shown.
            if COA_MODE and opcode == CMSG_PLAYER_LOGIN and len(body) >= 8:
                guid = struct.unpack_from("<Q", body, 0)[0]
                self.coa_guid = guid
                self.coa_name = self.coa_roster.get(guid)
                self.log("   CoA: player login guid=0x%X -> %r (class %s)"
                         % (guid, self.coa_name,
                            COA_STATE.get_class(self.coa_name)
                            if self.coa_name else None))
            if opcode >= FIRST_CUSTOM_OPCODE:
                self.handle_custom(opcode, body)
                continue
            if opcode == CMSG_CHAR_CREATE:
                body = self.fix_char_create(body)
            try:
                self.core.sendall(frame_c2s(opcode, body, self.c2s))
            except OSError as e:
                # Never break silently here. A bare `break` leaves self.reason
                # unset, so run() calls close(None) and the log shows only
                # "session closed" -- indistinguishable from a clean client
                # disconnect, which is exactly how this looked from the outside
                # when the CoA realm dropped every session at character select
                # on 2026-09-09. Name the opcode we were forwarding: that is the
                # packet the core refused, and it is the whole diagnosis.
                self.reason = self.reason or (
                    "core refused %s (%d B): %r -- the core closed this session's "
                    "socket, look for its reason in the worldserver log"
                    % (opname(opcode, True), len(body), e))
                self.log("!! core send failed on %s: %r"
                         % (opname(opcode, True), e))
                break
            # After forwarding, never instead of it: the core wants this packet
            # too, and the client is only told about its CA state once the core
            # has been told the player is moving.
            if opcode == CMSG_SET_ACTIVE_MOVER and self.ca_armed:
                self.send_ca_bootstrap("SET_ACTIVE_MOVER -- client has its "
                                       "player object")

    spellmod_seen = None

    def pump_core_to_client(self):
        self.spellmod_seen = {}
        while self.alive:
            try:
                pkt = read_s2c(self.core, self.s2c)
            except ValueError as e:
                self.log("!! core stream: %s" % e)
                break
            except OSError as e:
                self.log("!! core socket: %r" % (e,))
                break
            if pkt is None:
                self.reason = self.reason or ("core hung up (EOF) -- look for a "
                                              "kick reason in logs-bridge/Server.log")
                break
            opcode, body = pkt
            if opcode in (SMSG_CHAR_CREATE, SMSG_CHARACTER_LOGIN_FAILED) and body:
                # opname() REQUIRES the direction arg.  Calling it with one arg
                # raised TypeError right here the instant SMSG_CHAR_CREATE arrived,
                # and because this log sits OUTSIDE the read_s2c try/except it took
                # the whole core->client pump thread down with it -- so the
                # create-success ack (and every S->C packet after it) never reached
                # the client, hanging the "Creating character" dialog even though
                # the core had already written the row and sent the ack.  The fix
                # is simply to pass the direction so the pump survives and forwards
                # the real reply (success OR a genuine failure code) at send_client
                # below.  See CoA reconstruction log 3.2.1 -- same bug, same repo.
                self.log("   S->C %s -> %s"
                         % (opname(opcode, False),
                            RESPONSE_CODES.get(body[0], "0x%02X" % body[0])))
                if opcode == SMSG_CHAR_CREATE:
                    pending = getattr(self, "pending_coa_class", None)
                    if pending is not None:
                        pname, pclass = pending
                        if body[0] == CHAR_CREATE_SUCCESS:
                            COA_STATE.set_class(pname, pclass)
                            self.log("   CoA: committed %r -> class %d"
                                     % (pname, pclass))
                        else:
                            self.log("   CoA: create refused; %r -> class %d NOT "
                                     "recorded" % (pname, pclass))
                        self.pending_coa_class = None
            elif opcode == SMSG_CAST_FAILED:
                self.log("   S->C SMSG_CAST_FAILED %s" % describe_cast_failed(body))
            elif opcode == SMSG_TRAINER_LIST:
                self.log("   S->C SMSG_TRAINER_LIST\n%s" % describe_trainer_list(body))
                if (TRAINER_TYPE_OVERRIDE and len(body) >= 12
                        and struct.unpack_from("<I", body, 8)[0] == 0):
                    body = (body[:8]
                            + struct.pack("<I", TRAINER_TYPE_OVERRIDE)
                            + body[12:])
                    self.log("   .. CLASS trainer list re-typed %d so the "
                             "classless client will render it"
                             % TRAINER_TYPE_OVERRIDE)
            elif opcode in (SMSG_SET_FLAT_SPELL_MODIFIER,
                            SMSG_SET_PCT_SPELL_MODIFIER) and len(body) >= 6:
                # The login spell-mod resend (CharacterHandler.cpp, "Xinef: we
                # need to resend all spell mods") is the only login packet whose
                # CONTENT varies with the talent set once 0x4C0 and 0x12A are
                # ruled out.  Log (effect index, op, value) so a crashing login
                # can be compared with a passing one bucket by bucket.
                eff, op = body[0], body[1]
                val = struct.unpack_from("<i", body, 2)[0]
                self.log("   S->C %s eff=%d op=%d val=%d"
                         % (opname(opcode, False), eff, op, val))
            elif worth_logging(opcode, False):
                self.log("   S->C %s (%d B)" % (opname(opcode, False), len(body)))
            # The Ascension client reads a 10-byte spell-mod body where stock
            # 3.3.5a sends 6.  Un-padded, every one of these overran its buffer
            # and the login died at Extensions.dll+0x324BCE.  See
            # TROUBLESHOOTING.md, "ERROR #132 ... on the millisecond of
            # CMSG_PLAYER_LOGIN".  Verified live: 6 and 8 bytes crash, 10
            # through 32 do not.
            if (opcode in (SMSG_SET_FLAT_SPELL_MODIFIER,
                           SMSG_SET_PCT_SPELL_MODIFIER)
                    and len(body) < SPELLMOD_BODY_BYTES
                    and SPELLMOD_MODE != "raw"):
                body = body + bytes(SPELLMOD_BODY_BYTES - len(body))
            if SPELLMOD_MODE and opcode in (SMSG_SET_FLAT_SPELL_MODIFIER,
                                            SMSG_SET_PCT_SPELL_MODIFIER)                     and len(body) >= 6:
                eff, op = body[0], body[1]
                val = struct.unpack_from("<i", body, 2)[0]
                seen = self.spellmod_seen
                drop = False
                if SPELLMOD_MODE == "firstop":
                    owner = seen.setdefault((opcode, op), eff)
                    drop = owner != eff
                elif SPELLMOD_MODE == "eff0":
                    body = bytes([0]) + body[1:]
                elif SPELLMOD_MODE == "uniq":
                    drop = (opcode, op, eff, val) in seen
                    seen[(opcode, op, eff, val)] = True
                elif SPELLMOD_MODE.startswith("pad"):
                    body = body + bytes(int(SPELLMOD_MODE[3:]))
                elif SPELLMOD_MODE.startswith("op"):
                    # Rewrite the SpellModOp byte, leaving effect index and
                    # value alone.  ASC_SPELLMOD_MODE=op7 -> every packet op 7.
                    body = body[:1] + bytes([int(SPELLMOD_MODE[2:])]) + body[2:]
                elif SPELLMOD_MODE == "one" or SPELLMOD_MODE.startswith("n"):
                    limit = 1 if SPELLMOD_MODE == "one" else int(
                        SPELLMOD_MODE[1:])
                    n = seen.get("n", 0)
                    drop = n >= limit
                    seen["n"] = n + 1
                if drop:
                    self.log("   .. SPELLMOD %s eff=%d op=%d val=%d withheld "
                             "[mode=%s]" % (opname(opcode, False), eff, op,
                                            val, SPELLMOD_MODE))
                    continue
            if opcode in DROP_OPCODES:
                self.log("   .. DROPPED %s (%d B) [ASC_DROP_OPCODES]"
                         % (opname(opcode, False), len(body)))
                continue
            # CoA mode: the core lists every character under the carrier class it
            # was built with.  Put the real class back before the client sees it,
            # or the character select screen shows 21 Warriors.
            if COA_MODE and opcode == SMSG_CHAR_ENUM and body:
                body, changed, roster, looks = coa_mode.rewrite_char_enum(
                    body, lambda n: COA_STATE.get_class(n))
                self.coa_roster.update(roster)
                self.coa_looks.update(looks)
                for name, was, now in changed:
                    self.log("   CoA: char enum %r class %s -> %s (%s)"
                             % (name, was, now, COA_CLASS_NAMES.get(now, "?")))
            if not self.send_client(opcode, body):
                # send_client swallows OSError and returns False, so this was the
                # ONE exit in the whole session that logged nothing at all and set
                # no reason -- close(None) then printed a bare "session closed",
                # which is exactly what a clean client disconnect prints. That is
                # what made the CoA character-select drop look like a bridge crash
                # for two restarts on 2026-09-09; it was neither a crash nor our
                # bug, it was the client hanging up first. Name it.
                self.reason = self.reason or (
                    "client socket gone while forwarding %s (%d B) -- the CLIENT "
                    "hung up first; check its Logs/connection.log and Errors/"
                    % (opname(opcode, False), len(body)))
                break
            if opcode == SMSG_TUTORIAL_FLAGS and not self.customs_sent:
                self.send_customs()
            # EVERY world entry, not just the first.  One TCP session can enter
            # the world more than once (logout -> character select -> Enter
            # World), and each entry reloads FrameXML, which resets
            # ASC_ARCHIVE_SEEN back to false.  A once-per-socket hello left the
            # archive gate shut for every re-entry.
            if opcode == SMSG_LOGIN_VERIFY_WORLD:
                threading.Timer(ARCHIVE_HELLO_DELAY,
                                self.send_archive_hello).start()
                self.arm_ca_bootstrap()
        self.close(self.reason)

    def send_archive_hello(self):
        """Tell the client, in a way only this bridge can, that it is on the
        archive.  Delayed past world entry because the chat frame has to exist
        to raise CHAT_MSG_SYSTEM, and that is what GlobalOverwrites.lua listens
        for to set ASC_ARCHIVE_SEEN."""
        if not self.send_client(SMSG_MESSAGECHAT, smsg_system_chat(ARCHIVE_HELLO)):
            return
        self.log("-> client archive hello (%s)" % ARCHIVE_TOKEN)

    def arm_ca_bootstrap(self):
        """World entry seen. Owe the client one 0x725, to be paid when it says
        its player object exists."""
        if not CA_BOOTSTRAP:
            return
        with self.ca_lock:
            self.ca_armed = True
            if self.ca_timer is not None:
                self.ca_timer.cancel()
            self.ca_timer = threading.Timer(
                CA_BOOTSTRAP_FALLBACK, self.send_ca_bootstrap,
                args=("fallback -- no SET_ACTIVE_MOVER after %gs"
                      % CA_BOOTSTRAP_FALLBACK,))
            self.ca_timer.daemon = True
            self.ca_timer.start()

    def send_ca_bootstrap(self, why):
        """SMSG_CA_ACTIVE_SPEC, once per world entry. Both the SET_ACTIVE_MOVER
        path and the fallback timer land here, so it disarms under the lock."""
        with self.ca_lock:
            if not self.ca_armed:
                return
            self.ca_armed = False
            if self.ca_timer is not None:
                self.ca_timer.cancel()
                self.ca_timer = None
        if not self.send_client(SMSG_CA_ACTIVE_SPEC,
                                smsg_ca_active_spec(CA_ACTIVE_INDEX,
                                                    CA_SPEC_SLOTS)):
            return
        self.log("-> client SMSG_CA_ACTIVE_SPEC active=%d slots=%d (%s) -- CA "
                 "state allocated, panel will not fault"
                 % (CA_ACTIVE_INDEX, CA_SPEC_SLOTS, why))
        if COA_MODE:
            self.send_coa_state()

    def send_class_byte(self, clas):
        """Put the CoA class in UNIT_FIELD_BYTES_0, the dword the CA ENGINE reads.

        Rewriting SMSG_CHAR_ENUM is not enough and this is the evidence, measured
        on the maintainer's level-80 Tinker (guid 1) on 2026-09-09 BEFORE this existed:

            UnitClassID("player")   28        <- char-enum rewrite, correct
            UnitClass("player")     "Tinker"  <- same source, correct
            GetRemainingAE/TE       80 / 71   <- the STOCK-class essence row,
                                                 shared by keys 1..9 and 11.
                                                 Tinker's own family is 36 / 35.
            CanLearnID(<Tinker>)    false
            CanLearnID(<Mage>)      false     <- a Warrior can learn neither

        Two sources disagreed: the labels came from the rewritten character
        record, the engine from the descriptor dword the core wrote with the
        carrier class.  `GetEntriesByClass`'s filter takes its class argument as
        `[[obj+8]+0x5c] >> 8` (Extensions.dll 0x1017bb1f) -- that dword, nothing
        else -- so nothing short of correcting it moves the engine.

        Sent BEFORE the essence budget on purpose.  The engine reads the class on
        demand rather than caching it, so order is not strictly load-bearing, but
        a budget that lands while the engine still believes it is a Warrior is
        exactly the state this method exists to end.

        Same mechanism as world_server.py's `.class` command (its values-update
        call is the model for this one), which is measured to reach the CA
        subsystem immediately with no relog -- though the class NAME caches at
        object-create time.  Here the name is already right from the char enum,
        so both halves should agree without a relog.

        Failure is non-fatal by construction: if race/gender were never harvested
        this returns without sending, leaving the pre-fix behaviour rather than
        guessing an appearance and silently changing the character's race."""
        if not COA_WRITE_CLASS_BYTE:
            self.log("   CoA: UNIT_FIELD_BYTES_0 left as the core wrote it "
                     "[ASC_COA_CLASS_BYTE=0] -- the CA engine will use the "
                     "carrier class %d, not %d" % (AC_FALLBACK_CLASS, clas))
            return
        guid = self.coa_guid
        look = self.coa_looks.get(guid) if guid is not None else None
        if guid is None or look is None:
            self.log("   CoA: no race/gender harvested for guid %r -- class byte "
                     "NOT written (the CA engine keeps the carrier class). "
                     "UNIT_FIELD_BYTES_0 is one dword and race rides in it, so "
                     "guessing here would change the character's race."
                     % (guid,))
            return
        race, gender = look
        # Power is the CARRIER's, deliberately -- see coa_mode.bytes0_for.
        power = chardata.power_for(AC_FALLBACK_CLASS) & 0xFF
        bytes0 = coa_mode.bytes0_for(race, clas, gender, power)
        if not self.send_client(
                SMSG_UPDATE_OBJECT,
                build_values_update(guid, {UNIT_FIELD_BYTES_0: u32le(bytes0)},
                                    PLAYER_END)):
            return
        self.log("-> client SMSG_UPDATE_OBJECT UNIT_FIELD_BYTES_0=0x%08X for "
                 "guid 0x%X (race %d, class %d (%s), gender %d, power %d from "
                 "carrier class %d) -- the CA engine reads THIS, not the char "
                 "enum" % (bytes0, guid, race, clas,
                           COA_CLASS_NAMES.get(clas, "?"), gender, power,
                           AC_FALLBACK_CLASS))

    def send_coa_state(self):
        """The two packets the WCR bridge deliberately omits, now that there is a
        CoA class to serve them for.

        Order matters and is world_server.py's: 0x725 (already sent above) builds
        the client-side container, THEN the essence budget, THEN the known set.
        Sent in the other order the budget lands in a container that does not
        exist yet.

        The essence family is the character's OWN class byte. Serving family 10
        (Free-Pick, AE 140 / TE 71) to a Tinker would hand it more than three
        times its real budget -- the precise 'mixing non-CoA assets' failure this
        realm exists to avoid."""
        name = self.coa_name
        clas = COA_STATE.get_class(name) if name else None
        if clas is None or clas not in COA_CLASS_NAMES:
            # `clas not in COA_CLASS_NAMES` is the second half of the guard in
            # fix_char_create, checked again at the point of service because
            # coa_state.json outlives any one build of this file and may already
            # hold a class byte an older, laxer version wrote.
            self.log("   CoA: no CoA class stored for %r (got %r) -- essence and "
                     "known-entry packets withheld (character predates CoA mode, "
                     "or was made as a stock or Free-Pick class)" % (name, clas))
            return
        self.send_class_byte(clas)
        rows = COA_ESSENCE.rows_for(clas)
        if not rows:
            self.log("   CoA: class %d has no essence family in the DBC -- "
                     "withheld rather than sending a zero budget" % clas)
        else:
            sent = 0
            for r in rows:
                if not self.send_client(
                        SMSG_CA_ESSENCE_BUDGET,
                        coa_mode.smsg_ca_essence_budget(
                            r[0], r[1], r[2], r[7], r[8])):
                    return
                sent += 1
            ae, te = COA_ESSENCE.budget(clas, 80)
            self.log("-> client %d x SMSG_CA_ESSENCE_BUDGET family=%d (%s); "
                     "at level 80 that family is AE %d / TE %d"
                     % (sent, clas, COA_CLASS_NAMES.get(clas, "?"), ae, te))
        known = COA_STATE.get_entries(name)
        if self.send_client(SMSG_CA_KNOWN_ENTRIES,
                            coa_mode.smsg_ca_known_entries(known)):
            info = COA_CLASSES.by_byte.get(clas, {})
            ct = info.get("classtype")
            self.log("-> client SMSG_CA_KNOWN_ENTRIES %d entr%s (%s tree = "
                     "classtype %s, %d entries available)"
                     % (len(known), "y" if len(known) == 1 else "ies",
                        COA_CLASS_NAMES.get(clas, "?"), ct,
                        len(COA_TREE.entries_for(ct)) if ct is not None else 0))

    def send_customs(self):
        """Ascension-only packets the core cannot know about, in world_server.py's
        proven order: they go out after SMSG_TUTORIAL_FLAGS, at the glue stage,
        because the gate they open is read while the character screens are being
        built -- long before anyone enters the world."""
        self.customs_sent = True
        # RealmInfo is a process-global singleton in the client, so one send holds
        # for the whole session. +0x48 nonzero is the addon-loadability gate:
        # without it every LoadOnDemand Ascension addon reports loadable=nil and
        # the custom UI stays dark.
        self.send_client(SMSG_REALM_INFO,
                         smsg_realm_info(flags=BRIDGE_REALM_FLAVOUR))
        self.log("-> client SMSG_REALM_INFO flavour=%r (addon gate open, stock "
                 "class creation)" % (tuple(BRIDGE_REALM_FLAVOUR),))

    # -- translation ----------------------------------------------------------
    def handle_custom(self, opcode, body):
        """Ascension's own opcodes never reach the core. Answer what we can, and
        say plainly what we dropped -- a silent drop here looks exactly like a
        protocol bug three hours later."""
        # CMSG_CA_KNOWN_ENTRIES (0x727) is the client half of a learn OR an
        # unlearn: it uploads its whole idea of the known set. In CoA mode the
        # bridge is the authority, so it stores that set and echoes the
        # server's own 0x726 back -- which is also what drags the spellbook
        # along. Without this a learned node is forgotten on relog.
        if COA_MODE and opcode == CMSG_CA_KNOWN_ENTRIES_UPLOAD and self.coa_name:
            records = coa_mode.parse_cmsg_ca_known_entries(body)
            COA_STATE.set_entries(self.coa_name, records)
            self.log("   CoA: %r uploaded %d known entr%s -- stored"
                     % (self.coa_name, len(records),
                        "y" if len(records) == 1 else "ies"))
            self.send_client(SMSG_CA_KNOWN_ENTRIES,
                             coa_mode.smsg_ca_known_entries(records))
            return
        self.log("   custom 0x%03X (%d B) not forwarded" % (opcode, len(body)))

    def fix_char_create(self, body):
        """The classless realm sends a Character-Advancement class byte, which has
        no `playercreateinfo` row, so the core would answer CHAR_CREATE_FAILED.
        Substitute a class the core can actually build."""
        try:
            z = body.index(b"\x00")
        except ValueError:
            return body
        tail = bytearray(body[z + 1:])
        if len(tail) < 2:
            return body
        clas = tail[1]
        if clas in AC_VALID_CLASSES:
            return body
        # Must respect the chosen race: see carrier_class_for().
        race = tail[0]
        carrier = carrier_class_for(race)
        # CoA mode: the substitution is no longer a one-way loss.  Remember which
        # class the player actually chose, keyed by the character NAME (the only
        # identifier present in both CMSG_CHAR_CREATE and SMSG_CHAR_ENUM -- the
        # GUID does not exist yet here), so the enum can project it back.
        if COA_MODE and COA_STATE is not None:
            name = body[:z].decode("utf-8", "replace")
            if not name:
                pass
            elif clas in COA_CLASS_NAMES:
                # Do NOT persist yet.  The core can still refuse this create (an
                # invalid race/class pair, a duplicate name), and a name recorded
                # for a character that was never built stays in coa_state.json
                # forever -- the next character to reuse that name is then enum-
                # projected to a class it does not have, which shows up in game as
                # "the UI says Cultist but I have Paladin spells".  Commit only on
                # CHAR_CREATE_SUCCESS; see pending_coa_class below.
                self.pending_coa_class = (name, clas)
                self.log("   CoA: %r chose class %d (%s); carrier class %d for "
                         "the core (race %d)" % (name, clas,
                                                 COA_CLASS_NAMES.get(clas, "?"),
                                                 carrier, race))
            else:
                # Not every class the core cannot build is a CoA class. The Dev
                # flavour this realm serves also turns CanCreateHero on, and
                # CharacterCreate.lua:1533 makes HERO_CLASS_ID (10) -- Free-Pick
                # -- the DEFAULT selection, so a player who never touches the Swap
                # Classes button creates a class-10 character here. Recording that
                # as "this character's CoA class" would later hand it
                # Essence().rows_for(10): Free-Pick's own family, AE 140 / TE 71 at
                # 80, against a real CoA class's AE 36 / TE 35. Storing nothing
                # makes get_class() return None, and send_coa_state() already
                # withholds the essence and known-entry packets on None and says
                # why. Only CoA classes 12..32 are CoA classes.
                self.pending_coa_class = None
                self.log("   CoA: %r chose class %d (%s), which is not a CoA "
                         "class -- carrier class %d for the core, and NO CoA "
                         "class recorded, so it will never be served CoA essence"
                         % (name, clas,
                            "Free-Pick hero" if clas == 10 else "?",
                            carrier))
        tail[1] = carrier
        self.log("   CHAR_CREATE class %d has no playercreateinfo row -> %d "
                 "(race %d)" % (clas, carrier, race))
        return body[:z + 1] + bytes(tail)

    def close(self, reason=None):
        if not self.alive:
            return
        self.alive = False
        with self.ca_lock:
            self.ca_armed = False
            if self.ca_timer is not None:
                self.ca_timer.cancel()
                self.ca_timer = None
        if reason:
            self.log("closing: %s" % reason)
        for s in (self.client, self.core):
            try:
                if s:
                    s.close()
            except Exception:
                pass
        self.log("session closed")


def main():
    rotate_log()
    log("=" * 78)
    log("ascension_bridge: client %s:%d  ->  AzerothCore %s:%d (realm %d, build %d)"
        % (LISTEN_HOST, LISTEN_PORT, AC_HOST, AC_PORT, AC_REALM_ID, AC_BUILD))
    if LISTEN_PORT not in (8085, 8087, 8088):
        log("!! port %d is NOT on the client's endpoint allow-list -- the client "
            "will crash a few seconds after the world draws. See archive_ports.py."
            % LISTEN_PORT)
    # Which mode this bridge is in has to be in the log, because it is otherwise
    # invisible: the CoA bridge and the Free-Pick one are the same script run by
    # the same python.exe on the same port, so nothing short of the command line
    # tells them apart. The hub's start_helpers() skips a helper whose port is
    # already open, so a bridge left over from the other profile is silently
    # reused rather than replaced -- and the only symptom is a CoA realm serving
    # Free-Pick classes and Free-Pick's much larger essence budget.
    if COA_MODE:
        log("mode: CoA -- realm flavour %s, %d custom classes, state in %s"
            % (tuple(BRIDGE_REALM_FLAVOUR), len(COA_CLASS_NAMES),
               os.path.basename(getattr(COA_STATE, "path", "coa_state.json"))))
    else:
        log("mode: Free-Pick/WCR -- realm flavour %s (pass --coa for the CoA realm)"
            % (tuple(BRIDGE_REALM_FLAVOUR),))
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind((LISTEN_HOST, LISTEN_PORT))
    srv.listen(4)
    log("listening.")
    cid = 0
    while True:
        conn, addr = srv.accept()
        cid += 1
        log("b%03d: connection from %s:%d" % (cid, addr[0], addr[1]))
        threading.Thread(target=Session(conn, cid, addr).run, daemon=True).start()


if __name__ == "__main__":
    main()
