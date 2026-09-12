#!/usr/bin/env python3
"""The Conquest of Azeroth advancement catalogue.

Ascension's custom classes do not use `Talent.dbc`. A Starcaller's tabs are
empty there, because `TalentTab.dbc` stops at class 13; the abilities and
talents such a character buys live in a separate catalogue the server compiles
in, and a character "has" one by knowing its spells.

A checkpoint records what was bought as advancement entries -- an entry id, the
spell it teaches, and what it cost -- so an import does not actually need this
catalogue to restore the character: the spell ids are already in the capture.
What the catalogue adds is judgement. It says which class an entry belongs to,
which specialization, and what level it needed, so an import can report a
Starcaller's purchases by name and refuse an entry that does not belong to the
character being imported.

It is therefore optional, and read from wherever the operator points it:

    --coa-data <path to AscensionCoATalentData.h>
    --coa-data <path to a .json export of the same table>

Nothing is bundled. The catalogue is the realm's own data, it changes when the
realm changes, and a stale copy shipped inside this tool would be worse than no
copy at all.
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field
from typing import Any, Iterable

__all__ = ["CoaError", "CoaEntry", "Catalogue", "load", "parse_header", "parse_json"]


class CoaError(Exception):
    """The catalogue could not be read."""


@dataclass(frozen=True)
class CoaEntry:
    """One purchasable advancement: an ability or a talent."""

    entry_id: int
    class_id: int
    spec_id: int
    ability_cost: int
    talent_cost: int
    required_level: int
    spells: tuple[int, ...] = ()

    @property
    def kind(self) -> str:
        if self.talent_cost:
            return "talent"
        if self.ability_cost:
            return "ability"
        return "automatic"

    @property
    def cost(self) -> int:
        return self.talent_cost or self.ability_cost


# {EntryId, ClassId, SpecId, SpellCount, AECost, TECost, RequiredLevel, {{a, b, c}}}
_ROW = re.compile(
    r"\{\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*,"
    r"\s*(\d+)\s*,\s*\{\{\s*(\d+)\s*,\s*(\d+)\s*,\s*(\d+)\s*\}\}\s*\}"
)


def parse_header(text: str) -> list[CoaEntry]:
    """Read the generated C++ table.

    The header is a flat list of brace-initialised rows in a fixed order, so it
    is read positionally rather than by field name. A row whose shape does not
    match is ignored instead of guessed at.
    """
    out = []
    for match in _ROW.finditer(text):
        values = [int(v) for v in match.groups()]
        out.append(CoaEntry(
            entry_id=values[0], class_id=values[1], spec_id=values[2],
            ability_cost=values[4], talent_cost=values[5], required_level=values[6],
            spells=tuple(s for s in values[7:10] if s),
        ))
    return out


def parse_json(text: str) -> list[CoaEntry]:
    """Read a JSON export: a list of objects, or {"entries": [...]}.

    Field names are accepted in either the C++ spelling or a lowercase one, so
    an export produced by hand does not have to imitate the header exactly.
    """
    try:
        data = json.loads(text)
    except ValueError as exc:
        raise CoaError(f"not valid JSON: {exc}") from exc
    rows = data.get("entries") if isinstance(data, dict) else data
    if not isinstance(rows, list):
        raise CoaError("expected a list of entries, or an object with an 'entries' list")

    def pick(row: dict, *names: str, default: int = 0) -> int:
        for name in names:
            if name in row:
                try:
                    return int(row[name] or 0)
                except (TypeError, ValueError):
                    return default
        return default

    out = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        spells = row.get("SpellIds") or row.get("spells") or []
        if not isinstance(spells, list):
            spells = [spells]
        single = pick(row, "SpellID", "spellId", "spell", default=0)
        if single and not spells:
            spells = [single]
        out.append(CoaEntry(
            entry_id=pick(row, "EntryId", "InternalID", "entryId", "entry"),
            class_id=pick(row, "ClassId", "classId", "class"),
            spec_id=pick(row, "SpecId", "specId", "spec"),
            ability_cost=pick(row, "AECost", "abilityCost"),
            talent_cost=pick(row, "TECost", "talentCost"),
            required_level=pick(row, "RequiredLevel", "requiredLevel", "level"),
            spells=tuple(int(s) for s in spells if s),
        ))
    return out


@dataclass
class Catalogue:
    """Advancement entries, indexed by the id a checkpoint records."""

    source: str = ""
    entries: list[CoaEntry] = field(default_factory=list)
    _by_id: dict[int, CoaEntry] = field(default_factory=dict, repr=False)

    def __post_init__(self) -> None:
        # A duplicate id would make the lookup arbitrary; keep the first and let
        # the caller see the count rather than silently preferring one.
        self.duplicates = 0
        for entry in self.entries:
            if entry.entry_id in self._by_id:
                self.duplicates += 1
                continue
            self._by_id[entry.entry_id] = entry

    def __len__(self) -> int:
        return len(self._by_id)

    def get(self, entry_id: Any) -> CoaEntry | None:
        try:
            return self._by_id.get(int(entry_id))
        except (TypeError, ValueError):
            return None

    def classes(self) -> list[int]:
        return sorted({entry.class_id for entry in self.entries})

    def for_class(self, class_id: int) -> list[CoaEntry]:
        return [entry for entry in self.entries if entry.class_id == int(class_id)]

    def specs(self, class_id: int) -> list[int]:
        return sorted({e.spec_id for e in self.for_class(class_id) if e.spec_id})

    def mismatch(self, entry_id: Any, class_id: int, level: int,
                 spec_id: int = 0) -> str:
        """Why this entry does not belong to this character, or "" if it does.

        An unknown id is not a mismatch: the catalogue may simply be older than
        the realm, and refusing to import on that basis would be worse than
        importing an entry we cannot describe.
        """
        entry = self.get(entry_id)
        if entry is None:
            return ""
        if entry.class_id != int(class_id):
            return ("entry %d belongs to class %d, not class %d"
                    % (entry.entry_id, entry.class_id, class_id))
        if entry.required_level > int(level):
            return ("entry %d needs level %d; this character is %d"
                    % (entry.entry_id, entry.required_level, level))
        if entry.spec_id and spec_id and entry.spec_id != int(spec_id):
            return ("entry %d belongs to specialization %d, not %d"
                    % (entry.entry_id, entry.spec_id, spec_id))
        return ""


def load(path: str) -> Catalogue:
    """Read a catalogue from a .h or .json file."""
    if not os.path.isfile(path):
        raise CoaError(f"CoA catalogue not found: {path}")
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as handle:
            text = handle.read()
    except OSError as exc:
        raise CoaError(f"cannot read {path}: {exc}") from exc

    if path.lower().endswith(".json"):
        entries = parse_json(text)
    else:
        entries = parse_header(text)
    if not entries:
        raise CoaError(
            f"{path} carried no advancement entries. Point --coa-data at "
            "AscensionCoATalentData.h or a JSON export of it."
        )
    return Catalogue(source=path, entries=entries)


def describe(catalogue: Catalogue) -> str:
    classes = catalogue.classes()
    return ("%d advancement entries for %d classes (%s)"
            % (len(catalogue), len(classes),
               ", ".join(str(c) for c in classes[:12])
               + ("..." if len(classes) > 12 else "")))
