import json
from pathlib import Path
import tempfile
import unittest

import normalize_jeff_fro


class JeffFroNormalizerTests(unittest.TestCase):
    def test_text_normalizer_selects_fields_and_redacts_without_logging_values(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, patterns = root / "in.ndjson", root / "patterns.txt"
            source.write_text(
                '{"id":1,"name":"SecretName at person@example.com","tt":"C:/Users/Test/file","author":"held"}\n',
                encoding="utf-8")
            patterns.write_text("SecretName\n", encoding="utf-8")
            report = normalize_jeff_fro.normalize_lines(
                source, root / "out.ndjson", root / "receipt.json", "test",
                ("id", "name", "tt"), patterns)
            row = json.loads((root / "out.ndjson").read_text(encoding="utf-8"))
            self.assertEqual(row["name"], "[redacted identity] at [redacted email]")
            self.assertEqual(row["tt"], "[redacted local path]")
            self.assertNotIn("author", row)
            self.assertEqual(report["redactions"], {
                "email": 1, "local_path": 1, "private_identity": 1})

    def test_spellbook_rows_are_deterministic_and_character_fields_stay_private(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source = root / "by-class.json"
            mapping = root / "class-name-map.md"
            source.write_text(json.dumps({
                "source": "In-game capture, 2026-09-02.",
                "classes": {
                    "TEST": {
                        "class": "TEST", "level": 23, "race": "NightElf",
                        "realm_mode": "Voljin-CoA", "skills": [{"name": "Private state"}],
                        "spellbook": [{"name": "Path", "spells": [
                            {"id": 7, "name": "One", "rank": "Rank 1"},
                            {"id": 8, "name": "Two", "rank": ""},
                        ]}],
                    }
                },
            }), encoding="utf-8")
            mapping.write_text(
                "| In-game display name | Dump `class` code | `CharacterAdvancementData.json` key |\n"
                "|---|---|---|\n| Test Class | `TEST` | `TestClass` |\n", encoding="utf-8")
            first, second = root / "first.ndjson", root / "second.ndjson"
            report = normalize_jeff_fro.build(source, mapping, first, root / "first.json")
            normalize_jeff_fro.build(source, mapping, second, root / "second.json")
            self.assertEqual(first.read_bytes(), second.read_bytes())
            rows = [json.loads(line) for line in first.read_text(encoding="utf-8").splitlines()]
            self.assertEqual(report["output"]["records"], 2)
            self.assertEqual(rows[0]["id"], "TEST:000:0000:7")
            self.assertEqual(rows[0]["class_name"], "Test Class")
            self.assertFalse({"level", "race", "skills"} & rows[0].keys())

    def test_unknown_class_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            source, mapping = root / "by-class.json", root / "map.md"
            source.write_text(json.dumps({"source": "2026-09-02", "classes": {
                "MISSING": {"class": "MISSING", "realm_mode": "Voljin-CoA", "spellbook": []}
            }}), encoding="utf-8")
            mapping.write_text("| Name | `OTHER` | `Other` |\n", encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "absent from class-name map"):
                normalize_jeff_fro.records(source, normalize_jeff_fro.class_names(mapping))


if __name__ == "__main__":
    unittest.main()
