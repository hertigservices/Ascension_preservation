r"""CoA realm mode for ascension_bridge.py -- Conquest of Azeroth classes and
Character-Advancement state on top of the real AzerothCore world.

WHY THIS EXISTS
---------------
The archive has two half-realms; this makes one whole one:

  * world_server.py (:8087) speaks the whole CoA protocol but has no world --
    no terrain, no creatures, no quests.  It is a protocol reimplementation.
  * ascension_bridge.py (:8088) -> AzerothCore (:8086) has the real world but
    advertises REALM_FLAVOUR_WCR, the eleven stock WotLK classes, because the
    core cannot BUILD a character whose class byte is 12..32: `playercreateinfo`
    holds classes 1..9 and 11 only, and the binaries compile a fixed MAX_CLASSES.
    That is the "Free-Pick only" state a bridge realm starts in.

This module is the join.  The core keeps believing every character is a stock
class it can build; the CLIENT is told the character's real CoA class and is
given the CA state that makes the tree usable.  Nothing here needs the core
rebuilt, and nothing here writes to a realm database.

THE TWO ID SPACES -- DO NOT CONFLATE THEM
-----------------------------------------
A CoA class has two different numbers and they are NOT equal:

  * the CHARACTER CLASS BYTE (UNIT_FIELD_BYTES_0, ChrClasses.dbc id).  Tinker
    is 28.  This is what makes a character present as that class, and it is
    also the essence FAMILY key (see below).
  * the CA CLASSTYPE id (CharacterAdvancementClassTypes.dbc id, referenced by
    CharacterAdvancement.dbc column 32).  Tinker is 30.

Joining them by number silently produces a character that is one class and has
another class's talent tree.  They are joined HERE BY NAME, and `selftest()`
prints the resulting table so a wrong join is visible rather than plausible.

ESSENCE IS PER-CLASS AND THAT IS THE POINT
------------------------------------------
SMSG_CA_ESSENCE_BUDGET's third u32 is the essence family, and the families are
not interchangeable: at level 80 family 10 (Free-Pick "Hero") is AE 140 / TE 71
while family 28 (Tinker) is AE 36 / TE 35.  A CoA character served the Free-Pick
budget is exactly the "mixing non-CoA assets" failure to avoid, so
the family key is always the character's own class byte.

STATE LIVES IN A JSON FILE, NOT A REALM DATABASE
------------------------------------------------
`coa_state.json` beside this file holds {character name: {class, entries}}.
Deliberate: the archive's DB writes are gated, this keeps the mode fully
reversible (delete the file and the realm is stock again), and it mirrors what
world_server.py already does with characters.json.
"""
import json
import os
import struct
import threading

BASE = os.path.dirname(os.path.abspath(__file__))
import runtime_paths as paths

# Path contract, identical to world_server.py so the two agree on any layout:
#   ASC_CA_REF    the rexxar-reference directory (its ca-dbc/ holds the CA DBCs)
#   ASC_DBC_DIR   the directory holding Ascension's ChrClasses.dbc (patch-M.MPQ)
#   ASC_COA_STATE the per-character state file
# The defaults resolve to the working layout; every one of these files is
# extracted from your OWN client -- none ship in the repo.  See data/MANIFEST.md.
CA_REF_DIR = os.environ.get("ASC_CA_REF") or paths.CONFIG.get("ca_ref_dir") or os.path.join(BASE, "rexxar-reference")
DBC_DIR = os.path.join(CA_REF_DIR, "ca-dbc")
STATE_PATH = os.environ.get("ASC_COA_STATE") or os.path.join(paths.state_dir(), "coa_state.json")

# Ascension's ChrClasses.dbc: 32 rows where vanilla has 10.  Spelled out the same
# way world_server.py spells it, because the CA DBCs and the base DBCs come out of
# different MPQs and land in different directories.
DBC_BASE_DIR = os.environ.get("ASC_DBC_DIR") or paths.CONFIG.get("dbc_dir") or os.path.join(
    os.path.dirname(os.path.dirname(BASE)), "server-ascension", "Data", "dbc")


def _chrclasses_path():
    """Ascension's ChrClasses.dbc -- 32 rows where vanilla has 10.

    TWO DIFFERENT FILES CARRY THIS NAME and picking the wrong one fails SILENTLY:
    the client ships the STOCK 10-row ChrClasses.dbc (ids 1..11, no id > 11)
    alongside the CA DBCs, while the 32-row Ascension one lives with the base DBCs.
    Load the stock file and every lookup still "works" -- CoAClasses just comes up
    with zero classes and nothing raises.  So a candidate is only accepted if it
    actually CONTAINS rows above id 11; a plain existence check is not enough."""
    tried = []
    for d in (DBC_BASE_DIR, DBC_DIR):
        for nm in ("ChrClasses.dbc", "DBFilesClient_ChrClasses.dbc"):
            p = os.path.join(d, nm)
            if not os.path.exists(p):
                continue
            try:
                recs, _s, _n = _read_dbc(p)
            except Exception as exc:
                tried.append("%s (unreadable: %s)" % (p, exc))
                continue
            if any(r[0] > 11 for r in recs):
                return p
            tried.append("%s (%d rows, none above id 11 -- this is the STOCK "
                         "WotLK file, not Ascension's)" % (p, len(recs)))
    if tried:
        raise SystemExit(
            "coa_mode: found ChrClasses.dbc, but not Ascension's 32-row one.\n"
            "  rejected:\n    %s\n"
            "  Ascension's copy comes out of patch-M.MPQ and has ids 12..32\n"
            "  (Barbarian .. Runemaster).  Point ASC_DBC_DIR at the directory\n"
            "  holding THAT file.  See data/MANIFEST.md."
            % "\n    ".join(tried))
    return os.path.join(DBC_BASE_DIR, "ChrClasses.dbc")   # for _require's message

# The public CA export (data/ca-export/classes.json) carries the SAME classtype
# table as CharacterAdvancementClassTypes.dbc, so that one DBC is optional.
CA_EXPORT_CLASSES = os.path.join(
    os.path.dirname(BASE), "data", "ca-export", "classes.json")


def _ca_dbc(name):
    """A CA DBC by bare name, accepted with or without the DBFilesClient_ prefix
    mpqcat gives them."""
    for cand in ("DBFilesClient_" + name, name):
        p = os.path.join(DBC_DIR, cand)
        if os.path.exists(p):
            return p
    return os.path.join(DBC_DIR, "DBFilesClient_" + name)


def _require(path, what, envvar):
    """Fail with the env var to set, not with a bare FileNotFoundError three
    frames deep in the bridge.  A missing DBC here is a setup step, not a bug."""
    if not os.path.exists(path):
        raise SystemExit(
            "coa_mode: cannot find %s\n"
            "  looked in: %s\n"
            "  Extract it from your own client (see data/MANIFEST.md for which\n"
            "  MPQ holds it) or point %s at the directory you extracted it to."
            % (what, path, envvar))
    return path

# CharacterAdvancement.dbc column map -- from tools/ca_export.py, which evidenced
# it against known facts (spell 10 -> 'Blizzard', 133 -> 'Fireball').  Not guessed.
CA_COL_ID = 0
CA_COL_SPELL = 5
CA_COL_CLASSTYPE = 32
CA_COL_TABTYPE = 33
CA_COL_NAME = 47

# ChrClasses.dbc: 0 = id, 4 = Name_lang[0], 55 = Filename (internal name).
CHR_COL_NAME = 4
CHR_COL_FILE = 55

# The stock classes AzerothCore can actually build (playercreateinfo, verified
# 2026-09-09: classes 1-9 and 11, 62 rows).  Class 10 has no row -- that is why
# the Free-Pick hero cannot be created either.
AC_VALID_CLASSES = {1, 2, 3, 4, 5, 6, 7, 8, 9, 11}

# A core with the CoA class work (MAX_CLASSES 33 plus its playercreateinfo rows for
# 12..32) CAN build those classes, and rewriting them to the fallback then produces an
# invalid race/class pair the core refuses. Override with e.g.
#     ASC_AC_VALID_CLASSES="1-9,11-32"
# Ranges and single values, comma separated. Unset keeps the stock-core set above.
def _parse_class_set(spec):
    out = set()
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, _, hi = part.partition("-")
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    return out


_ac_classes_env = os.environ.get("ASC_AC_VALID_CLASSES", "").strip()
if _ac_classes_env:
    try:
        AC_VALID_CLASSES = _parse_class_set(_ac_classes_env)
    except ValueError:
        pass


# ---------------------------------------------------------------- DBC reading

def _read_dbc(path):
    """(records, string_fn, ncols).  Ascension's string blocks do NOT open with
    the conventional NUL -- offset 0 is a real string and the empty string lives
    at some other offset -- so a string is only valid if it starts where a string
    actually starts.  Never special-case offset 0."""
    with open(path, "rb") as f:
        data = f.read()
    if data[:4] != b"WDBC":
        raise ValueError("%s is not a WDBC file" % path)
    rc, fc, rs, ss = struct.unpack_from("<IIII", data, 4)
    ncol = rs // 4
    sb = 20 + rc * rs
    blk = data[sb:sb + ss]
    if 20 + rc * rs + ss != len(data):
        raise ValueError("%s: header arithmetic does not match file size" % path)
    starts = {0}
    for i, b in enumerate(blk):
        if b == 0 and i + 1 < len(blk):
            starts.add(i + 1)
    recs = [struct.unpack_from("<%dI" % ncol, data, 20 + i * rs) for i in range(rc)]

    def s(off):
        if off not in starts:
            return None
        end = blk.find(b"\x00", off)
        if end <= off:
            return None
        return blk[off:end].decode("utf-8", "replace")

    return recs, s, ncol


# ---------------------------------------------------------------- class table

class CoAClasses(object):
    """The CoA classes, with both id spaces joined BY NAME."""

    def __init__(self):
        chr_recs, chr_s, _ = _read_dbc(
            _require(_chrclasses_path(),
                     "Ascension's ChrClasses.dbc (patch-M.MPQ)", "ASC_DBC_DIR"))
        ct_dbc = _ca_dbc("CharacterAdvancementClassTypes.dbc")

        # class byte -> display name, for the ids stock WotLK does not have
        self.by_byte = {}
        for r in chr_recs:
            if r[0] <= 11:
                continue
            name = chr_s(r[CHR_COL_NAME])
            if name:
                self.by_byte[r[0]] = {
                    "class_byte": r[0],
                    "name": name,
                    "internal": chr_s(r[CHR_COL_FILE]) or "",
                    "classtype": None,
                }

        # CA classtype rows: field 1 = internal name, field 6 = display name.
        # Same table, two possible sources -- the DBC from your client, or the
        # public export of it.  The join below is BY NAME either way, so which
        # source supplied the ids does not change the result.
        ct_by_name = {}
        if os.path.exists(ct_dbc):
            ct_recs, ct_s, _ = _read_dbc(ct_dbc)
            self.classtype_source = ct_dbc
            for r in ct_recs:
                for nm in (ct_s(r[6]), ct_s(r[1])):
                    if nm:
                        ct_by_name.setdefault(nm.strip().lower(), r[0])
        elif os.path.exists(CA_EXPORT_CLASSES):
            self.classtype_source = CA_EXPORT_CLASSES
            with open(CA_EXPORT_CLASSES, encoding="utf-8") as f:
                for row in json.load(f):
                    for nm in (row.get("Display"), row.get("Name")):
                        if nm:
                            ct_by_name.setdefault(nm.strip().lower(), row["ID"])
        else:
            _require(ct_dbc, "CharacterAdvancementClassTypes.dbc (patch-M.MPQ) "
                             "-- or data/ca-export/classes.json", "ASC_CA_REF")

        for byte, info in self.by_byte.items():
            for key in (info["name"], info["internal"]):
                if not key:
                    continue
                hit = ct_by_name.get(key.strip().lower())
                if hit is not None:
                    info["classtype"] = hit
                    break

    def unmatched(self):
        return [i for i in self.by_byte.values() if i["classtype"] is None]

    def names(self):
        return dict((b, i["name"]) for b, i in self.by_byte.items())


# ---------------------------------------------------------------- CA entries

class CATree(object):
    """Entries per CA classtype, so a character can be told what its own tree
    holds without ever being served another class's entries."""

    def __init__(self):
        recs, s, ncol = _read_dbc(
            _require(_ca_dbc("CharacterAdvancement.dbc"),
                     "CharacterAdvancement.dbc (patch-M.MPQ)", "ASC_CA_REF"))
        self.ncol = ncol
        self.by_classtype = {}
        for r in recs:
            self.by_classtype.setdefault(r[CA_COL_CLASSTYPE], []).append({
                "id": r[CA_COL_ID],
                "spell": r[CA_COL_SPELL],
                "tab": r[CA_COL_TABTYPE],
                "name": s(r[CA_COL_NAME]) or "",
            })

    def entries_for(self, classtype):
        return self.by_classtype.get(classtype, [])


# ---------------------------------------------------------------- essence

class Essence(object):
    """CharacterAdvancementEssence.dbc, keyed by FAMILY (the class byte).

    Layout per world_server.py: row[1] = level, row[2] = family key, rows 3..6
    are match flags, row[7] = ability essence, row[8] = talent essence."""

    def __init__(self):
        recs, _s, _n = _read_dbc(
            _require(_ca_dbc("CharacterAdvancementEssence.dbc"),
                     "CharacterAdvancementEssence.dbc (patch-M.MPQ)", "ASC_CA_REF"))
        self.rows = recs
        self._curves = {}

    def curve(self, family):
        if family not in self._curves:
            self._curves[family] = dict(
                (r[1], (r[7], r[8])) for r in self.rows
                if r[2] == family and not (r[3] or r[4] or r[5] or r[6]))
        return self._curves[family]

    def budget(self, family, level):
        """(ability, talent) for this class's own family, clamped to the range
        the DBC defines.  Returns (0, 0) when the family has no rows at all --
        the caller must treat that as 'do not send', not as 'zero essence'."""
        c = self.curve(family)
        if not c:
            return (0, 0)
        if level in c:
            return c[level]
        lo, hi = min(c), max(c)
        return c[hi if level > hi else lo]

    def rows_for(self, family):
        return [r for r in self.rows if r[2] == family]


# ---------------------------------------------------------------- wire format

def smsg_ca_essence_budget(row_id, level, family, ability, talent, combo=0):
    """One SMSG_CA_ESSENCE_BUDGET (0x722) record: nine u32s, 36 bytes.
    Ported verbatim from world_server.py:1997 -- do not re-derive."""
    return struct.pack("<9I", row_id, level, family,
                       (combo >> 0) & 1, (combo >> 1) & 1,
                       (combo >> 2) & 1, (combo >> 3) & 1,
                       ability, talent)


def smsg_ca_known_entries(records):
    """SMSG_CA_KNOWN_ENTRIES (0x726): u32 count then 21 bytes per record.
    Ported verbatim from world_server.py:1256."""
    out = struct.pack("<I", len(records))
    for entry_id, rank, unk0c, flag, unk18, unk1c in records:
        out += struct.pack("<IIIBII", entry_id & 0xFFFFFFFF, rank & 0xFFFFFFFF,
                           unk0c & 0xFFFFFFFF, flag & 0xFF,
                           unk18 & 0xFFFFFFFF, unk1c & 0xFFFFFFFF)
    return out


def parse_cmsg_ca_known_entries(body):
    """CMSG_CA_KNOWN_ENTRIES (0x727) -> list of records.  A ragged tail costs the
    records it could not parse, never the connection."""
    if len(body) < 4:
        return []
    count = struct.unpack_from("<I", body, 0)[0]
    out, off = [], 4
    for _ in range(min(count, (len(body) - 4) // 21)):
        entry_id, rank, unk0c = struct.unpack_from("<III", body, off)
        flag = body[off + 12]
        unk18, unk1c = struct.unpack_from("<II", body, off + 13)
        out.append((entry_id, rank, unk0c, flag, unk18, unk1c))
        off += 21
    return out


# ---------------------------------------------------------------- char enum

def rewrite_char_enum(body, class_for_name):
    """Project each character's real CoA class back into SMSG_CHAR_ENUM (0x3B).

    The core stored a carrier class it could build (see fix_char_create); the
    client must be told the CoA class or the character shows as a Warrior and
    the CA panel offers the wrong tree.  `class_for_name(name) -> byte or None`.

    3.3.5a record layout, in order:
        u64 guid, cstring name, u8 race, u8 CLASS, u8 gender,
        u8 skin/face/hairStyle/hairColour/facialHair, u8 level,
        u32 zone, u32 map, 3*float position, u32 guildId,
        u32 charFlags, u32 customizeFlags, u8 firstLogin,
        u32 petDisplayId, u32 petLevel, u32 petFamily,
        23 * (u32 displayId, u8 invType, u32 enchant)

    Returns (new_body, [(name, from, to)], {guid: name}, {guid: (race, gender)}).
    The roster is how the session later turns the GUID in CMSG_PLAYER_LOGIN into
    the name this state store is keyed by.

    The fourth value exists because rewriting the class byte HERE is not enough.
    It fixes what the character list shows and what UnitClass() reports, but the
    CA engine does not read either: it reads the class out of the player's
    UNIT_FIELD_BYTES_0 descriptor dword, where the core wrote the CARRIER class.
    Measured 2026-09-09 -- a class-28 Tinker reported AE 80 / TE 71, which is the
    stock-class essence row shared by keys 1..9 and 11, not Tinker's own 36 / 35,
    and CanLearnID refused every Tinker node.  Correcting that dword means
    rebuilding it whole -- race | class<<8 | gender<<16 | power<<24 -- so race and
    gender have to come from somewhere, and this packet is the only place the
    bridge sees them for a character it did not just watch being created.
    Guessing them would silently change the character's race.

    On ANY parse surprise the ORIGINAL body is returned untouched: a mangled
    character list is far worse than a character shown with its carrier class,
    and this runs on every login.
    """
    if not body:
        return body, [], {}, {}
    out = bytearray()
    changed = []
    roster = {}
    looks = {}
    try:
        count = body[0]
        out.append(count)
        off = 1
        for _ in range(count):
            start = off
            guid = struct.unpack_from("<Q", body, off)[0]
            off += 8                                  # guid
            z = body.index(b"\x00", off)
            name = body[off:z].decode("utf-8", "replace")
            roster[guid] = name
            off = z + 1
            race_off = off
            class_off = off + 1
            off += 3                                  # race, class, gender
            off += 5                                  # appearance
            off += 1                                  # level
            off += 4 + 4                              # zone, map
            off += 12                                 # x, y, z
            off += 4                                  # guildId
            off += 4 + 4                              # charFlags, customizeFlags
            off += 1                                  # firstLogin
            off += 12                                 # pet display/level/family
            off += 23 * 9                             # equipment view
            if off > len(body):
                raise ValueError("record for %r runs past the packet" % name)
            rec = bytearray(body[start:off])
            want = class_for_name(name)
            if want is not None and rec[class_off - start] != want:
                changed.append((name, rec[class_off - start], want))
                rec[class_off - start] = want
            # race and gender are read from the ORIGINAL body, never from `rec`:
            # rec is the copy whose class byte we may just have overwritten, and
            # taking neighbours out of a buffer being mutated is how an off-by-one
            # turns into a changed race rather than a crash.
            looks[guid] = (body[race_off], body[race_off + 2])
            out += rec
        if off != len(body):
            raise ValueError("trailing %d bytes after %d records"
                             % (len(body) - off, count))
    except (IndexError, ValueError, struct.error) as e:
        # struct.error is NOT a subclass of ValueError -- it derives straight from
        # Exception -- so unpack_from() on a short body escaped this handler
        # entirely and broke the "on ANY parse surprise the ORIGINAL body is
        # returned" promise three lines up in the docstring. It took the whole
        # client->core pump with it, which reads as a clean disconnect. Verified:
        # a body of b"\x01" (count says 1 record, no record follows) raised
        # `unpack_from requires a buffer of at least 9 bytes` before this was added.
        return body, [("<parse failed: %s>" % e, 0, 0)], {}, {}
    return bytes(out), changed, roster, looks


def bytes0_for(race, clas, gender, power):
    """Pack UNIT_FIELD_BYTES_0 (descriptor dword 23) -- race | class<<8 |
    gender<<16 | power<<24.

    This is the dword the CA engine reads to decide which class's tree, essence
    family and learnable set the character gets: `GetEntriesByClass`'s filter is
    handed `[[obj+8]+0x5c] >> 8`, i.e. exactly the class byte below.  Rewriting
    SMSG_CHAR_ENUM does NOT reach it -- that only fixes the character list and
    UnitClass().

    POWER IS THE CARRIER CLASS'S, NOT THE CoA CLASS'S, AND THAT IS DELIBERATE.
    The core is really simulating the carrier class, so it is really tracking
    that class's resource -- rage for Warrior.  chardata.power_for() has no row
    for 12..32 and falls through to mana, so recomputing power from the CoA class
    would paint a mana bar over a core that is filling a rage bar.  The CA engine
    reads only the class byte, so the power slot has nothing to gain and a
    desynced resource bar to lose: pass power_for(carrier), not power_for(clas).
    """
    return ((race & 0xFF)
            | ((clas & 0xFF) << 8)
            | ((gender & 0xFF) << 16)
            | ((power & 0xFF) << 24))


# ---------------------------------------------------------------- state store

class CoAState(object):
    """{character name (lower): {"class": byte, "entries": [[id,rank,...], ...]}}

    Written atomically via a temp file + replace so a crash mid-write cannot
    leave a truncated JSON that loses every character's class."""

    def __init__(self, path=STATE_PATH):
        self.path = path
        self.lock = threading.Lock()
        self.data = {}
        if os.path.exists(path):
            try:
                with open(path, encoding="utf-8") as f:
                    self.data = json.load(f)
            except (ValueError, OSError):
                self.data = {}

    def _flush(self):
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(self.data, f, indent=1)
        os.replace(tmp, self.path)

    def set_class(self, name, class_byte):
        with self.lock:
            e = self.data.setdefault(name.lower(), {})
            e["class"] = class_byte
            e.setdefault("entries", [])
            self._flush()

    def get_class(self, name):
        e = self.data.get(name.lower())
        return e.get("class") if e else None

    def set_entries(self, name, records):
        with self.lock:
            e = self.data.setdefault(name.lower(), {})
            e["entries"] = [list(r) for r in records]
            self._flush()

    def get_entries(self, name):
        e = self.data.get(name.lower())
        return [tuple(r) for r in e.get("entries", [])] if e else []


# ---------------------------------------------------------------- self-test

def selftest():
    """Print the joined table. A wrong join must be VISIBLE, not plausible --
    this session's recurring failure was a probe that produced a believable
    wrong answer, so the evidence gets printed rather than asserted."""
    import sys
    sys.stdout.reconfigure(encoding="utf-8", errors="replace")

    cls = CoAClasses()
    tree = CATree()
    ess = Essence()

    print("CoA classes from ChrClasses.dbc (ids > 11): %d" % len(cls.by_byte))
    print("  classtypes from: %s" % getattr(cls, "classtype_source", "?"))
    if not cls.by_byte:
        # An empty table below would otherwise print as a clean pass -- the exact
        # shape of "a wrong count reads like a right one".  Fail instead.
        raise SystemExit(
            "coa_mode: ZERO CoA classes loaded -- the table below would be empty "
            "and every 'no mismatch' line beneath it would be vacuously true.\n"
            "  This means ChrClasses.dbc parsed but held no id above 11.")
    print()
    print("%-6s %-22s %-20s %-10s %-8s %s"
          % ("byte", "name", "internal", "classtype", "entries", "essence@80 (AE/TE)"))
    print("-" * 96)
    missing_ct, missing_ess = [], []
    for byte in sorted(cls.by_byte):
        i = cls.by_byte[byte]
        ct = i["classtype"]
        n = len(tree.entries_for(ct)) if ct is not None else 0
        ae, te = ess.budget(byte, 80)
        if ct is None:
            missing_ct.append(i["name"])
        if (ae, te) == (0, 0):
            missing_ess.append(i["name"])
        print("%-6d %-22s %-20s %-10s %-8d %s"
              % (byte, i["name"][:22], i["internal"][:20],
                 "-" if ct is None else ct, n, "%d / %d" % (ae, te)))
    print("-" * 96)
    print("classes with no CA classtype match : %s" % (missing_ct or "none"))
    print("classes with no essence family     : %s" % (missing_ess or "none"))
    print()
    print("Free-Pick 'Hero' family 10 @80     : %d / %d  (must NOT be served to a CoA class)"
          % ess.budget(10, 80))


if __name__ == "__main__":
    selftest()
