"""Normalize Jeff-Fro's sanitized CoA class spellbooks for public evidence export.

The original JSON remains the preservation object.  This creates one deterministic
record per spellbook observation so public policy can select only reviewed fields
without publishing the surrounding character snapshot.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import tempfile


PRIVATE_TEXT = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[A-Za-z]{2,}", re.I), "[redacted email]", "email"),
    (re.compile(r"(?:(?<![A-Za-z])[A-Z]:[\\/]|/(?:home|Users)/)[^\s|<>\"]*", re.I), "[redacted local path]", "local_path"),
    (re.compile(r"WTF[\\/]Account[\\/][^\s|<>\"]*", re.I), "[redacted account path]", "account_path"),
    (re.compile(r"0x[0-9a-f]{16}", re.I), "[redacted identifier]", "long_hex_identifier"),
    (re.compile(r"(?:https?://)?(?:cdn\.)?discord(?:app)?\.com/avatars/[^\s|<>\"]+", re.I), "[redacted avatar URL]", "avatar_url"),
)


def sha256(path):
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def class_names(path):
    result = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        if not line.startswith("|"):
            continue
        cells = [cell.strip().strip("`") for cell in line.strip().strip("|").split("|")]
        if len(cells) != 3 or cells[0] in ("In-game display name", "---"):
            continue
        display, code, advancement = cells
        if not re.fullmatch(r"[A-Z][A-Z0-9]*", code):
            continue
        if code in result:
            raise ValueError("Duplicate class code in class-name map: " + code)
        result[code] = (display, advancement)
    if not result:
        raise ValueError("Class-name map contained no usable rows")
    return result


def records(spellbooks, name_map):
    document = json.loads(Path(spellbooks).read_text(encoding="utf-8-sig"))
    if not isinstance(document, dict) or not isinstance(document.get("classes"), dict):
        raise ValueError("Spellbook input needs a classes object")
    captured = re.search(r"\b\d{4}-\d{2}-\d{2}\b", str(document.get("source", "")))
    if not captured:
        raise ValueError("Spellbook source does not state a capture date")
    captured = captured.group(0)
    result = []
    seen = set()
    for code in sorted(document["classes"]):
        snapshot = document["classes"][code]
        if code not in name_map:
            raise ValueError("Class code is absent from class-name map: " + code)
        if not isinstance(snapshot, dict) or snapshot.get("class") != code:
            raise ValueError("Class snapshot disagrees with its key: " + code)
        realm_mode = snapshot.get("realm_mode")
        if realm_mode not in ("Voljin-CoA", "Rexxar-CoA"):
            raise ValueError("Unexpected realm/mode label for " + code)
        display, advancement = name_map[code]
        spellbook = snapshot.get("spellbook")
        if not isinstance(spellbook, list):
            raise ValueError("Class has no spellbook list: " + code)
        for category_ordinal, category in enumerate(spellbook):
            if not isinstance(category, dict) or not isinstance(category.get("spells"), list):
                raise ValueError("Invalid spellbook category for " + code)
            category_name = str(category.get("name", ""))
            for spell_ordinal, spell in enumerate(category["spells"]):
                if not isinstance(spell, dict) or not isinstance(spell.get("id"), int):
                    raise ValueError("Invalid spell observation for " + code)
                identity = "%s:%03d:%04d:%d" % (
                    code, category_ordinal, spell_ordinal, spell["id"])
                if identity in seen:
                    raise ValueError("Duplicate normalized identity: " + identity)
                seen.add(identity)
                result.append({
                    "id": identity,
                    "spell_id": spell["id"],
                    "name": str(spell.get("name", "")),
                    "rank": str(spell.get("rank", "")),
                    "class_code": code,
                    "class_name": display,
                    "advancement_key": advancement,
                    "category": category_name,
                    "realm_mode": realm_mode,
                    "captured_at": captured,
                })
    return result


def atomic_write(path, data):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix="." + path.name + ".", dir=path.parent)
    temporary = Path(temporary)
    try:
        with os.fdopen(fd, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def identity_patterns(path):
    if path is None:
        return []
    lines = Path(path).read_text(encoding="utf-8-sig").splitlines()
    try:
        patterns = [re.compile(line.strip(), re.I) for line in lines
                    if line.strip() and not line.lstrip().startswith("#")]
    except re.error as error:
        raise ValueError("Private identity-pattern file is invalid") from error
    if not patterns:
        raise ValueError("Private identity-pattern file is empty")
    return patterns


def sanitize(value, private_patterns, counts):
    if not isinstance(value, str):
        return value
    for pattern, replacement, label in PRIVATE_TEXT:
        value, changed = pattern.subn(replacement, value)
        counts[label] = counts.get(label, 0) + changed
    for pattern in private_patterns:
        value, changed = pattern.subn("[redacted identity]", value)
        counts["private_identity"] = counts.get("private_identity", 0) + changed
    return value


def normalize_lines(source, output, receipt, kind, fields, private_patterns=None):
    private_patterns = identity_patterns(private_patterns)
    counts = {}
    rows = []
    seen = set()
    with Path(source).open(encoding="utf-8-sig") as stream:
        for ordinal, line in enumerate(stream):
            if not line.strip():
                continue
            raw = json.loads(line)
            if not isinstance(raw, dict):
                raise ValueError("NDJSON row is not an object at ordinal %d" % ordinal)
            missing = set(fields) - raw.keys()
            if missing:
                raise ValueError("NDJSON row is missing fields: " + ", ".join(sorted(missing)))
            row = {field: sanitize(raw[field], private_patterns, counts) for field in fields}
            identity = str(row.get("id"))
            if identity in seen:
                raise ValueError("Duplicate source id: " + identity)
            seen.add(identity)
            rows.append(row)
    payload = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")) + "\n").encode("utf-8")
                       for row in rows)
    atomic_write(output, payload)
    report = {
        "schema": 1,
        "normalizer": kind,
        "input": {"name": Path(source).name, "sha256": sha256(source)},
        "output": {"name": Path(output).name, "sha256": hashlib.sha256(payload).hexdigest(),
                   "bytes": len(payload), "records": len(rows)},
        "redactions": {key: value for key, value in sorted(counts.items()) if value},
        "selected_fields": list(fields),
    }
    atomic_write(receipt, (json.dumps(report, ensure_ascii=False, indent=2,
                                      sort_keys=True) + "\n").encode("utf-8"))
    return report


def build(spellbooks, name_map, output, receipt):
    rows = records(spellbooks, class_names(name_map))
    payload = b"".join((json.dumps(row, ensure_ascii=False, sort_keys=True,
                                     separators=(",", ":")) + "\n").encode("utf-8")
                       for row in rows)
    atomic_write(output, payload)
    report = {
        "schema": 1,
        "normalizer": "jeff-fro-class-spellbooks-1",
        "inputs": {
            Path(spellbooks).name: sha256(spellbooks),
            Path(name_map).name: sha256(name_map),
        },
        "output": {
            "name": Path(output).name,
            "sha256": hashlib.sha256(payload).hexdigest(),
            "bytes": len(payload),
            "records": len(rows),
            "classes": len({row["class_code"] for row in rows}),
        },
        "privacy": "Character names, level, race, skills, glyphs and talent state are not selected.",
    }
    atomic_write(receipt, (json.dumps(report, ensure_ascii=False, indent=2,
                                      sort_keys=True) + "\n").encode("utf-8"))
    return report


def main():
    parser = argparse.ArgumentParser()
    subparsers = parser.add_subparsers(dest="command", required=True)
    spellbooks = subparsers.add_parser("spellbooks")
    spellbooks.add_argument("--spellbooks", type=Path, required=True)
    spellbooks.add_argument("--class-map", type=Path, required=True)
    for command in (spellbooks, subparsers.add_parser("changelog"),
                    subparsers.add_parser("tooltips")):
        command.add_argument("--out", type=Path, required=True)
        command.add_argument("--receipt", type=Path, required=True)
    for command in (subparsers.choices["changelog"], subparsers.choices["tooltips"]):
        command.add_argument("--input", type=Path, required=True)
        command.add_argument("--identity-patterns", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "spellbooks":
        report = build(args.spellbooks, args.class_map, args.out, args.receipt)
    elif args.command == "changelog":
        report = normalize_lines(args.input, args.out, args.receipt,
                                 "jeff-fro-changelog-1",
                                 ("id", "label", "category", "realm_type", "group_key",
                                  "description", "created_at", "updated_at"),
                                 args.identity_patterns)
    else:
        report = normalize_lines(args.input, args.out, args.receipt,
                                 "jeff-fro-spell-tooltips-1", ("id", "name", "tt"),
                                 args.identity_patterns)
    print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
