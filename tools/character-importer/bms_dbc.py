#!/usr/bin/env python3
"""DBC-backed resolvers for the Bind My Soul importer.

The checkpoint stores several things by *name* only, because the 3.3.5a client
never hands the numeric id to Lua:

    reputations[].name   -> Faction.dbc ID          (character_reputation.faction)
    skills[].name        -> SkillLine.dbc ID        (character_skills.skill)
    talentTabs[].talents -> Talent.dbc RankID[rank] (character_talent.spell)

This module reads the target server's own ``Data/dbc`` directory, so the ids it
produces are always the ids that server will accept -- including on a fork with
patched DBCs.

Field offsets below are taken from AzerothCore's DBC format strings, which are
authoritative for this client build:

    src/server/shared/DataStores/DBCfmt.h
      FactionEntryfmt   "niiiiiiiiiiiiiiiiiiffixssssssssssssssssxxxxxxxxxxxxxxxxxx"
      SkillLinefmt      "nixssssssssssssssssxxx...ixxx...i"
      TalentEntryfmt    "niiiiiiiixxxxixxixxixxx"
      TalentTabEntryfmt "nxxxxxxxxxxxxxxxxxxxiiix"
      GlyphPropertiesfmt "niix"

and the matching structs in DBCStructure.h. Every offset is re-checked against
the file's own header at load time (see ``_expect_fields``), so a fork that
widened a DBC fails loudly instead of silently reading garbage.
"""

from __future__ import annotations

import bisect
import os
import struct
from dataclasses import dataclass
from typing import Any, Iterable

__all__ = [
    "DBC",
    "DbcError",
    "Resolvers",
    "Resolution",
    "class_mask",
    "dbc_header",
    "race_mask",
]

# -- field offsets (0-based, all fields are 4 bytes in WDBC) ---------------

FACTION_ID = 0
FACTION_REP_LIST_ID = 1
FACTION_RACE_MASK = 2      # ..5
FACTION_CLASS_MASK = 6     # ..9
FACTION_BASE_REP = 10      # ..13
FACTION_REP_FLAGS = 14     # ..17
FACTION_NAME = 23          # enUS, first of 16 locale strings
FACTION_FIELDS = 57

SKILL_ID = 0
SKILL_CATEGORY = 1
SKILL_NAME = 3             # enUS displayName
SKILL_FIELDS = 56

TALENT_ID = 0
TALENT_TAB = 1
TALENT_ROW = 2
TALENT_COL = 3
TALENT_RANK = 4            # RankID[0..4]
TALENT_MAX_RANK = 5
TALENT_FIELDS = 23

TALENTTAB_ID = 0
TALENTTAB_NAME = 1         # enUS, first of 16 locale strings
TALENTTAB_CLASS_MASK = 20
TALENTTAB_PAGE = 22
TALENTTAB_FIELDS = 24

SPELL_ID = 0
SPELL_NAME = 136           # enUS; matches tools/spellname.py
SPELL_FIELDS = 234

GLYPH_ID = 0
GLYPH_SPELL_ID = 1
GLYPH_FIELDS = 4

# ChrClasses carries both the display name and the uppercase token UnitClass()
# returns. They are not interchangeable on a fork: Ascension's class 32 is named
# "Runemaster" but its token is SPIRITMAGE, and 7 of its 21 custom classes
# disagree the same way. Reading the target's own file is the only way to map a
# captured token without hard-coding one realm's roster.
CHRCLASSES_ID = 0
CHRCLASSES_NAME = 4        # enUS, first of 16 locale strings
CHRCLASSES_TOKEN = 55
CHRCLASSES_FIELDS = 56

# FactionFlags the server actually reads back out of character_reputation
# (ReputationMgr::LoadFromDB reads only these three bits).
FACTION_FLAG_VISIBLE = 0x01
FACTION_FLAG_AT_WAR = 0x02
FACTION_FLAG_INACTIVE = 0x20


class DbcError(Exception):
    """A DBC file is missing, malformed, or has an unexpected shape."""


def race_mask(race_id: int) -> int:
    return 1 << (int(race_id) - 1)


def class_mask(class_id: int) -> int:
    return 1 << (int(class_id) - 1)


def dbc_header(path: str) -> tuple[int, int] | None:
    """(record count, field count) from a DBC's 20-byte header, or None.

    Header-only on purpose: Spell.dbc is around 90 MB, and this exists so that
    every run can afford to print which DBC set it used. Two directories that
    both contain the right file names are not the same data, and nothing else
    in the output would have shown the difference.
    """
    try:
        with open(path, "rb") as handle:
            head = handle.read(20)
    except OSError:
        return None
    if len(head) < 20 or head[:4] != b"WDBC":
        return None
    count, fields = struct.unpack_from("<II", head, 4)
    return count, fields


class DBC:
    """Minimal WDBC (3.3.5a) reader.

    Header is 5 x uint32: magic 'WDBC', recordCount, fieldCount, recordSize,
    stringBlockSize. Every field is 4 bytes; how to read it is the caller's
    business.
    """

    def __init__(self, path: str):
        self.path = path
        try:
            with open(path, "rb") as handle:
                data = handle.read()
        except OSError as exc:
            raise DbcError(f"Cannot read {path}: {exc}") from exc
        if len(data) < 20:
            raise DbcError(f"{path} is too small to be a DBC.")
        magic, self.count, self.fields, self.record_size, _str_size = struct.unpack_from(
            "<4sIIII", data, 0
        )
        if magic != b"WDBC":
            raise DbcError(f"{path} is not a WDBC file (magic={magic!r}).")
        body = 20 + self.count * self.record_size
        self._records = data[20:body]
        self._strings = data[body:]
        total = self.count * self.fields
        self._ints: tuple[int, ...] = struct.unpack_from("<%di" % total, self._records, 0)
        self.widened = False

    def _expect_fields(self, expected: int) -> None:
        """Refuse to read a DBC that is narrower than the layout we assume.

        A fork that *appends* columns leaves every offset below intact, so a
        wider file is fine (``self.widened`` records it for the preflight
        report). A narrower one means columns were removed or the layout was
        rewritten, and reading it would silently yield the wrong ids.
        """
        self.widened = self.fields > expected
        if self.fields < expected:
            raise DbcError(
                f"{os.path.basename(self.path)} has {self.fields} fields, expected at "
                f"least {expected}. This DBC's layout differs from 3.3.5a; the "
                "importer's field offsets would read the wrong columns."
            )

    def i(self, row: int, col: int) -> int:
        return self._ints[row * self.fields + col]

    def u(self, row: int, col: int) -> int:
        return self.i(row, col) & 0xFFFFFFFF

    def s(self, row: int, col: int) -> str:
        """The string at the offset stored in this column.

        Offset 0 is NOT treated as "no string". Stock 3.3.5a puts a lone NUL
        first, so offset 0 reads as empty there either way -- but that is a
        convention, not a rule, and Ascension's DBCs do not follow it. Measured
        on its tree: the block starts "PLAYER, Human" (Faction), "Fire"
        (TalentTab 41, i.e. Mage Fire), "Pet - Pit Lord" (SkillLine).
        Rejecting offset 0 drops those names and reports the lookup as an
        unresolved skip rather than an error.

        String blocks DEDUPLICATE, so the affected rows are every row whose
        name equals the block's first string -- not one row per file. Usually
        that is one; Achievement.dbc has two (ids 3 and 5610, both
        "Son of a..."). Stock has none in any file checked, which is exactly
        why this stays invisible there.
        """
        offset = self.i(row, col)
        if offset < 0 or offset >= len(self._strings):
            return ""
        end = self._strings.find(b"\0", offset)
        if end < 0:
            end = len(self._strings)
        return self._strings[offset:end].decode("utf-8", "replace")

    def __len__(self) -> int:
        return self.count

    def __repr__(self) -> str:
        return "<DBC %s rows=%d fields=%d>" % (
            os.path.basename(self.path),
            self.count,
            self.fields,
        )


@dataclass
class Resolution:
    """The outcome of one name -> id lookup."""

    ok: bool
    value: int = 0
    reason: str = ""
    note: str = ""

    def __bool__(self) -> bool:
        return self.ok


def _norm(text: Any) -> str:
    return " ".join(str(text or "").split()).casefold()


def _fold_token(text: Any) -> str:
    """Compare class tokens ignoring case, spaces and punctuation.

    "Witch Doctor", "WITCHDOCTOR" and "witch_doctor" are the same class.
    """
    return "".join(c for c in str(text or "").casefold() if c.isalnum())


def _dominant_span(ids: list[int]) -> tuple[int, int] | None:
    """The id range of the largest generation in a set of talent ids.

    A rebalanced tab can leave one original talent as the sole occupant of a
    cell -- CoA's Warrior/Protection keeps stock talent 140 because its
    replacement was renamed and moved elsewhere. A plain min/max would let that
    single straggler stretch the range across both id generations and match
    everything, so split at the one gap that is decisive (wider than the spread
    on either side of it) and keep the more populous side. A tab with a single
    coherent generation has no such gap and is left whole, which is what keeps
    a stock Talent.dbc resolving exactly as before.
    """
    if not ids:
        return None
    ordered = sorted(ids)
    if len(ordered) < 3:
        return ordered[0], ordered[-1]
    gaps = sorted(
        ((ordered[i + 1] - ordered[i], i) for i in range(len(ordered) - 1)),
        reverse=True,
    )
    widest, at = gaps[0]
    runner_up = gaps[1][0]
    # One generation's ids are lumpy -- CoA's Mage/Fire anchors cluster at
    # 110023-110036, 111639-111852 and 112212, so gaps of ~1600 are normal
    # *within* a generation. The jump between generations is ~110000. Measured
    # on this file the two are two orders of magnitude apart (4.5x the runner-up
    # inside Mage/Fire, 116x across the boundary in Warrior/Protection), so
    # requiring an order of magnitude splits real generations and leaves
    # ordinary lumpiness alone.
    if runner_up > 0 and widest >= 10 * runner_up:
        left, right = ordered[: at + 1], ordered[at + 1:]
        keep = left if len(left) >= len(right) else right
        if len(keep) >= 2:
            return keep[0], keep[-1]
    return ordered[0], ordered[-1]


class Resolvers:
    """Name -> id lookups against one server's DBC directory.

    Every table is loaded lazily, so a run that never touches talents never
    pays for Spell.dbc (which is ~90 MB of strings).
    """

    def __init__(self, dbc_dir: str):
        self.dbc_dir = dbc_dir
        if not os.path.isdir(dbc_dir):
            raise DbcError(f"DBC directory not found: {dbc_dir}")
        self._cache: dict[str, DBC] = {}
        self._faction_by_name: dict[str, list[int]] | None = None
        self._faction_rows: dict[int, int] | None = None
        self._skill_by_name: dict[str, list[int]] | None = None
        self._spell_names: dict[int, str] | None = None
        self._talent_index: dict[int, dict[str, list[int]]] | None = None
        self._talent_tabs: list[tuple[int, str, int, int]] | None = None
        self._talent_cell_users: dict[int, dict[tuple[int, int], int]] | None = None
        self._talent_anchor_span: dict[int, list[int]] | None = None
        self._talent_rank_spells: set[int] | None = None
        self._class_by_token: dict[str, list[int]] | None = None
        self._class_names: dict[int, str] | None = None
        self._glyph_by_spell: dict[int, int] | None = None

    # -- file access ------------------------------------------------------

    def _open(self, name: str, expect_fields: int | None = None) -> DBC:
        if name not in self._cache:
            path = os.path.join(self.dbc_dir, name)
            if not os.path.isfile(path):
                raise DbcError(
                    f"{name} not found in {self.dbc_dir}. Point --dbc-dir at the "
                    "server's Data/dbc directory."
                )
            dbc = DBC(path)
            if expect_fields is not None:
                dbc._expect_fields(expect_fields)
            self._cache[name] = dbc
        return self._cache[name]

    def available(self) -> dict[str, bool]:
        """Which DBCs the resolvers need, and whether they are present."""
        wanted = [
            "Faction.dbc",
            "SkillLine.dbc",
            "Talent.dbc",
            "TalentTab.dbc",
            "Spell.dbc",
            "GlyphProperties.dbc",
        ]
        return {n: os.path.isfile(os.path.join(self.dbc_dir, n)) for n in wanted}

    def fingerprint(self) -> str:
        """A one-line description of WHICH DBC set this is, not just where.

        Row counts are the cheapest thing that tells two sets apart, and telling
        them apart is the whole point: a fork's Talent.dbc and the stock one
        both load, both resolve most names, and disagree about exactly the
        talents that then go missing from the imported character.
        """
        parts = []
        for name in ("Talent.dbc", "Spell.dbc"):
            header = dbc_header(os.path.join(self.dbc_dir, name))
            parts.append("%s %s" % (name, "%d rows" % header[0] if header else "unreadable"))
        return ", ".join(parts)

    # -- factions ---------------------------------------------------------

    def _load_factions(self) -> None:
        if self._faction_by_name is not None:
            return
        dbc = self._open("Faction.dbc", FACTION_FIELDS)
        by_name: dict[str, list[int]] = {}
        rows: dict[int, int] = {}
        for row in range(len(dbc)):
            faction_id = dbc.u(row, FACTION_ID)
            rows[faction_id] = row
            name = _norm(dbc.s(row, FACTION_NAME))
            if name:
                by_name.setdefault(name, []).append(faction_id)
        self._faction_by_name = by_name
        self._faction_rows = rows

    def faction_by_name(self, name: str) -> Resolution:
        """Resolve a reputation pane name to a Faction.dbc ID.

        Only factions with reputationListID >= 0 can appear in the pane, so
        that filter resolves nearly every duplicate name on its own.
        """
        self._load_factions()
        assert self._faction_by_name is not None and self._faction_rows is not None
        dbc = self._open("Faction.dbc", FACTION_FIELDS)
        candidates = self._faction_by_name.get(_norm(name), [])
        if not candidates:
            return Resolution(False, reason="no faction with this name in Faction.dbc")
        listed = [
            fid
            for fid in candidates
            if dbc.i(self._faction_rows[fid], FACTION_REP_LIST_ID) >= 0
        ]
        pool = listed or candidates
        if len(pool) > 1:
            return Resolution(
                False,
                reason="ambiguous: Faction.dbc has %d factions named this (%s)"
                % (len(pool), ", ".join(str(f) for f in sorted(pool)[:5])),
            )
        return Resolution(True, pool[0])

    def faction_base_rep(self, faction_id: int, race_id: int, class_id: int) -> int:
        """GetBaseReputation(): the standing the character starts with.

        character_reputation.standing is stored *relative* to this, so the
        importer must subtract it from the absolute value the addon captured.
        Mirrors ReputationMgr::GetBaseReputation (ReputationMgr.cpp).
        """
        return self._faction_base(faction_id, race_id, class_id, FACTION_BASE_REP)

    def faction_default_flags(self, faction_id: int, race_id: int, class_id: int) -> int:
        """GetDefaultStateFlags(): the DBC flags for this race/class."""
        return self._faction_base(faction_id, race_id, class_id, FACTION_REP_FLAGS)

    def _faction_base(self, faction_id: int, race_id: int, class_id: int, base_col: int) -> int:
        self._load_factions()
        assert self._faction_rows is not None
        row = self._faction_rows.get(int(faction_id))
        if row is None:
            return 0
        dbc = self._open("Faction.dbc", FACTION_FIELDS)
        rmask = race_mask(race_id)
        cmask = class_mask(class_id)
        for index in range(4):
            faction_races = dbc.u(row, FACTION_RACE_MASK + index)
            faction_classes = dbc.u(row, FACTION_CLASS_MASK + index)
            if (faction_races & rmask or (faction_races == 0 and faction_classes != 0)) and (
                faction_classes & cmask or faction_classes == 0
            ):
                return dbc.i(row, base_col + index)
        return 0

    # -- skills -----------------------------------------------------------

    def _load_skills(self) -> None:
        if self._skill_by_name is not None:
            return
        dbc = self._open("SkillLine.dbc", SKILL_FIELDS)
        by_name: dict[str, list[int]] = {}
        for row in range(len(dbc)):
            name = _norm(dbc.s(row, SKILL_NAME))
            if name:
                by_name.setdefault(name, []).append(dbc.u(row, SKILL_ID))
        self._skill_by_name = by_name

    def skill_by_name(self, name: str) -> Resolution:
        self._load_skills()
        assert self._skill_by_name is not None
        candidates = self._skill_by_name.get(_norm(name), [])
        if not candidates:
            return Resolution(False, reason="no skill with this name in SkillLine.dbc")
        if len(candidates) > 1:
            return Resolution(
                False,
                reason="ambiguous: %d skills named this (%s)"
                % (len(candidates), ", ".join(str(s) for s in sorted(candidates)[:5])),
            )
        return Resolution(True, candidates[0])

    # -- spells / talents -------------------------------------------------

    def spell_name(self, spell_id: int) -> str:
        self._load_spell_names()
        assert self._spell_names is not None
        return self._spell_names.get(int(spell_id), "")

    def spell_exists(self, spell_id: int) -> bool:
        self._load_spell_names()
        assert self._spell_names is not None
        return int(spell_id) in self._spell_names

    def _load_spell_names(self) -> None:
        if self._spell_names is not None:
            return
        dbc = self._open("Spell.dbc")
        if dbc.fields <= SPELL_NAME:
            raise DbcError(
                "Spell.dbc has %d fields; the enUS name column (%d) is out of range."
                % (dbc.fields, SPELL_NAME)
            )
        self._spell_names = {
            dbc.u(row, SPELL_ID): dbc.s(row, SPELL_NAME) for row in range(len(dbc))
        }

    def _load_talents(self) -> None:
        """Index talents by tab, then by the name of their first rank's spell.

        Positional (tier, column) lookup is deliberately NOT used: an Ascension
        capture packs many talents into the same grid cell and reorders the
        tabs, so the client's tier/column carry no meaning on the target
        server. The rank-1 spell name is the only stable key.
        """
        if self._talent_index is not None:
            return
        self._load_spell_names()
        assert self._spell_names is not None
        tab_dbc = self._open("TalentTab.dbc", TALENTTAB_FIELDS)
        tabs = [
            (
                tab_dbc.u(row, TALENTTAB_ID),
                tab_dbc.s(row, TALENTTAB_NAME),
                tab_dbc.u(row, TALENTTAB_CLASS_MASK),
                tab_dbc.u(row, TALENTTAB_PAGE),
            )
            for row in range(len(tab_dbc))
        ]
        talent_dbc = self._open("Talent.dbc", TALENT_FIELDS)
        index: dict[int, dict[str, list[int]]] = {}
        cells: dict[int, dict[tuple[int, int], int]] = {}
        for row in range(len(talent_dbc)):
            tab_id = talent_dbc.u(row, TALENT_TAB)
            cell = (talent_dbc.u(row, TALENT_ROW), talent_dbc.u(row, TALENT_COL))
            cells.setdefault(tab_id, {})
            cells[tab_id][cell] = cells[tab_id].get(cell, 0) + 1
            first_rank = talent_dbc.u(row, TALENT_RANK)
            name = _norm(self._spell_names.get(first_rank, ""))
            if not name:
                continue
            index.setdefault(tab_id, {}).setdefault(name, []).append(row)
        self._talent_tabs = tabs
        self._talent_index = index
        self._talent_cell_users = cells

    def _tab_anchors(self, tab_id: int) -> list[int]:
        """The talent ids of the tree this tab actually draws.

        A talent tree is a grid: the client draws one talent per (tier, column)
        cell. Rows that are the *sole* occupant of their cell are therefore
        certainly on the drawn tree -- call them anchors. Rows piled several-deep
        into one cell cannot all be drawn, so at most one of them is live.

        A fork that rebalances a tree tends to leave the originals in the file
        rather than delete them, collapsed onto a single tier, and add its
        replacements as fresh rows laid out across real tiers. The replacements
        become the anchors, so proximity to them identifies which copy of a
        duplicated name belongs to the live tree -- without hard-coding any
        fork's id offsets.

        Measured on the CoA repack's Talent.dbc: Mage/Fire holds 29 stock-id
        rows stacked into 4 cells all on tier 0, beside 28 replacement rows
        spread one per cell across tiers 0-10. Every anchor is a replacement.

        Anchors outside the tab's dominant id generation are dropped, because a
        single original that happened to survive as a sole occupant would
        otherwise vouch for the whole abandoned generation.
        """
        if self._talent_anchor_span is None:
            self._talent_anchor_span = {}
        cached = self._talent_anchor_span.get(tab_id)
        if cached is not None:
            return cached
        assert self._talent_cell_users is not None
        assert self._talent_index is not None
        users = self._talent_cell_users.get(tab_id, {})
        talent_dbc = self._open("Talent.dbc", TALENT_FIELDS)
        anchors = [
            talent_dbc.u(row, TALENT_ID)
            for names in (self._talent_index.get(tab_id) or {}).values()
            for row in names
            if users.get(
                (talent_dbc.u(row, TALENT_ROW), talent_dbc.u(row, TALENT_COL)), 0
            ) == 1
        ]
        span = _dominant_span(anchors)
        kept = sorted(
            a for a in anchors if span is None or span[0] <= a <= span[1]
        )
        self._talent_anchor_span[tab_id] = kept
        return kept

    def _live_tree_rows(self, tab_id: int, rows: list[int]) -> list[int]:
        """Narrow same-name talent rows to the ones on the tab's drawn tree.

        A replacement that is itself piled -- it shares a cell with the original
        it replaces -- is not an anchor, so it can sit outside the anchors' own
        min/max: CoA's Frostbite is talent 110047 against a Frost anchor range
        starting at 110061. What still separates it from the original is
        distance: 14 from the nearest anchor, against 110014 for the stock copy.

        So rank the candidates by how far they sit from the nearest anchor and
        keep the closest, but only when it wins by an order of magnitude. A tab
        whose same-named talents are all equally close is one where this cannot
        tell them apart, and staying ambiguous there is the point -- guessing
        would be how a launch-era talent gets imported over its replacement.
        """
        anchors = self._tab_anchors(tab_id)
        if len(anchors) < 2 or len(rows) < 2:
            return rows
        talent_dbc = self._open("Talent.dbc", TALENT_FIELDS)

        def distance(row: int) -> int:
            talent_id = talent_dbc.u(row, TALENT_ID)
            at = bisect.bisect_left(anchors, talent_id)
            near = []
            if at < len(anchors):
                near.append(anchors[at] - talent_id)
            if at:
                near.append(talent_id - anchors[at - 1])
            return min(near)

        ranked = sorted((distance(row), row) for row in rows)
        best = ranked[0][0]
        rival = next(
            (
                d
                for d, row in ranked
                if talent_dbc.u(row, TALENT_RANK)
                != talent_dbc.u(ranked[0][1], TALENT_RANK)
            ),
            None,
        )
        if rival is None:
            return rows
        if rival >= max(best * 10, 1):
            return [row for d, row in ranked if d == best]
        return rows

    def _load_classes(self) -> None:
        if self._class_by_token is not None:
            return
        by_token: dict[str, list[int]] = {}
        names: dict[int, str] = {}
        try:
            dbc = self._open("ChrClasses.dbc")
        except DbcError:
            self._class_by_token = {}
            self._class_names = {}
            return
        if dbc.fields <= CHRCLASSES_TOKEN:
            self._class_by_token = {}
            self._class_names = {}
            return
        for row in range(len(dbc)):
            class_id = dbc.u(row, CHRCLASSES_ID)
            token = dbc.s(row, CHRCLASSES_TOKEN)
            name = dbc.s(row, CHRCLASSES_NAME)
            names[class_id] = name or token
            # A capture carries UnitClass()'s token, but accept the display name
            # too: they disagree for most of Ascension's custom classes, and
            # which one a checkpoint recorded is not worth guessing.
            for key in (token, name):
                folded = _fold_token(key)
                if folded and class_id not in by_token.setdefault(folded, []):
                    by_token[folded].append(class_id)
        self._class_by_token = by_token
        self._class_names = names

    def class_by_token(self, token: Any) -> Resolution:
        """UnitClass() token (or class display name) -> class id on this server."""
        self._load_classes()
        assert self._class_by_token is not None
        folded = _fold_token(token)
        if not folded:
            return Resolution(False, reason="no class token in the checkpoint")
        found = self._class_by_token.get(folded) or []
        if not found:
            return Resolution(
                False, reason="no class named %r in this server's ChrClasses.dbc" % token)
        if len(found) > 1:
            return Resolution(
                False,
                reason="ambiguous class token %r (matches classes %s)"
                % (token, ", ".join(str(c) for c in sorted(found))),
            )
        return Resolution(True, found[0])

    def class_name(self, class_id: int) -> str:
        self._load_classes()
        return (self._class_names or {}).get(int(class_id), "")

    def talent_rank_spells(self) -> set[int]:
        """Every spell any talent rank teaches, on this server.

        Used to keep a held-back talent build honest: the checkpoint's known
        spell list can carry a talent's own passive, and writing that to
        character_spell would hand the player the effect without the point.
        """
        if self._talent_rank_spells is None:
            talent_dbc = self._open("Talent.dbc", TALENT_FIELDS)
            spells = set()
            for row in range(len(talent_dbc)):
                for offset in range(TALENT_MAX_RANK):
                    spell = talent_dbc.u(row, TALENT_RANK + offset)
                    if spell:
                        spells.add(spell)
            self._talent_rank_spells = spells
        return self._talent_rank_spells

    def talent_tabs_for_class(self, class_id: int) -> list[tuple[int, str, int, int]]:
        """This class's talent tabs, in the order the client indexes them.

        GetTalentTabInfo(i) walks the class's tabs by TalentTab ID ascending,
        not by tabpage. Verified against a real capture: an Ascension Mage
        reports tabIndex 1/2/3 with 57/56/69 talents, which is exactly
        TalentTab 41/61/81 in that server's Talent.dbc.
        """
        self._load_talents()
        assert self._talent_tabs is not None
        cmask = class_mask(class_id)
        return sorted((tab for tab in self._talent_tabs if tab[2] & cmask), key=lambda t: t[0])

    def resolve_talent_tab(
        self, class_id: int, tab_name: str, tab_index: int = 0
    ) -> tuple[Resolution, bool]:
        """Find a talent tab by name, falling back to its index.

        Returns (resolution, matched_positionally).

        CORRECTED: this fallback was originally justified by the claim that
        "Ascension blanks some tab names", because TalentTab 41 (Mage Fire)
        read as empty. Ascension blanks nothing -- `DBC.s` was rejecting string
        offset 0, and Ascension's string block starts with a real string rather
        than stock's lone NUL, so exactly one row per DBC lost its name. Fixed
        in `DBC.s`; row 41 reads "Fire".

        The fallback stays anyway, because a name-only lookup is fragile for
        reasons that survive that fix: a non-enUS client capture reports
        localised tab names, and a fork may rename a tab outright.
        """
        tabs = self.talent_tabs_for_class(class_id)
        if not tabs:
            return Resolution(False, reason="this class has no talent tabs in TalentTab.dbc"), False
        named = [tab for tab in tabs if _norm(tab[1]) == _norm(tab_name)] if tab_name else []
        if len(named) == 1:
            return Resolution(True, named[0][0]), False
        if len(named) > 1:
            return Resolution(False, reason="ambiguous talent tab name %r" % tab_name), False
        index = int(tab_index or 0)
        if 1 <= index <= len(tabs):
            return Resolution(True, tabs[index - 1][0]), True
        return (
            Resolution(
                False,
                reason="no tab named %r for this class, and tabIndex %r is outside 1..%d"
                % (tab_name, tab_index, len(tabs)),
            ),
            False,
        )

    def talent_spell(
        self, class_id: int, tab_name: str, talent_name: str, rank: int, tab_index: int = 0
    ) -> Resolution:
        """(class, tab, talent name, rank) -> the spell id that rank teaches."""
        self._load_talents()
        assert self._talent_index is not None
        rank = int(rank)
        if rank < 1:
            return Resolution(False, reason="rank is zero; nothing to learn")
        if rank > TALENT_MAX_RANK:
            return Resolution(False, reason=f"rank {rank} exceeds the 5 ranks Talent.dbc stores")
        tab, _positional = self.resolve_talent_tab(class_id, tab_name, tab_index)
        if not tab.ok:
            return tab
        rows = self._talent_index.get(tab.value, {}).get(_norm(talent_name), [])
        if not rows:
            return Resolution(
                False, reason="no talent named %r in this tab" % talent_name
            )
        talent_dbc = self._open("Talent.dbc", TALENT_FIELDS)
        # A patched Talent.dbc can list the same talent more than once. That is
        # only a real ambiguity if the copies teach *different* spells at this
        # rank; identical copies collapse to one answer.
        spells = {talent_dbc.u(row, TALENT_RANK + rank - 1) for row in rows}
        spells.discard(0)
        if not spells:
            return Resolution(
                False, reason="%r has no rank %d on this server" % (talent_name, rank)
            )
        note = ""
        if len(spells) > 1:
            # Prefer the copy that sits on the tree the client can actually
            # draw. Picking the lowest id instead would hand a rebalanced fork
            # its own abandoned originals.
            live = self._live_tree_rows(tab.value, rows)
            narrowed = {talent_dbc.u(row, TALENT_RANK + rank - 1) for row in live}
            narrowed.discard(0)
            if len(narrowed) == 1:
                note = (
                    "%r exists %d times in this tab; took the copy on the laid-out "
                    "tree (spell %d) over %s"
                    % (
                        talent_name,
                        len(rows),
                        next(iter(narrowed)),
                        ", ".join(
                            str(s) for s in sorted(spells - narrowed)[:4]
                        ),
                    )
                )
                spells = narrowed
        if len(spells) > 1:
            return Resolution(
                False,
                reason="ambiguous: %d talents named %r in this tab teach different rank-%d "
                "spells (%s), and none is alone in its tree position"
                % (len(rows), talent_name, rank, ", ".join(str(s) for s in sorted(spells)[:4])),
            )
        return Resolution(True, spells.pop(), note=note)

    # -- glyphs -----------------------------------------------------------

    def glyph_by_spell(self, spell_id: int) -> Resolution:
        if self._glyph_by_spell is None:
            dbc = self._open("GlyphProperties.dbc", GLYPH_FIELDS)
            self._glyph_by_spell = {
                dbc.u(row, GLYPH_SPELL_ID): dbc.u(row, GLYPH_ID) for row in range(len(dbc))
            }
        found = self._glyph_by_spell.get(int(spell_id))
        if not found:
            return Resolution(False, reason="no glyph in GlyphProperties.dbc casts this spell")
        return Resolution(True, found)


def known_spell_filter(resolvers: Resolvers, spell_ids: Iterable[Any]) -> tuple[list[int], list[int]]:
    """Split captured spell ids into (present on this server, absent)."""
    present: list[int] = []
    absent: list[int] = []
    for raw in spell_ids or []:
        try:
            spell_id = int(raw)
        except (TypeError, ValueError):
            continue
        (present if resolvers.spell_exists(spell_id) else absent).append(spell_id)
    return present, absent
