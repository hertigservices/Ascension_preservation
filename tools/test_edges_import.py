"""Synthetic regressions for edges_import; never use client/export data."""

import contextlib
import gc
import importlib.util
import io
import json
import os
import sys
import tempfile
import unittest
import warnings
from unittest import mock


# Load only the pure importer, not other tools or server modules.
spec = importlib.util.spec_from_file_location(
    "edges_import", os.path.join(os.path.dirname(__file__), "edges_import.py"))
edges_import = importlib.util.module_from_spec(spec)
spec.loader.exec_module(edges_import)


class EdgesImportTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        paths = {name: os.path.join(self.temp.name, filename) for name, filename in
                 (("SV", "synthetic.lua"), ("ENTRIES", "entries.csv"),
                  ("OUT", "edges.json"))}
        patcher = mock.patch.multiple(edges_import, **paths)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.write_edges({2: [1]})
        self.write_entries("1,0,1,1\n2,1,1,1\n")

    def write_edges(self, edges):
        with io.open(edges_import.SV, "w", encoding="utf-8") as f:
            f.write('CoAReaderDB = {\n\t["edges"] = {\n')
            for node, targets in edges.items():
                f.write('\t\t["%d"] = {\n' % node)
                for target in targets:
                    f.write('\t\t\t%d,\n' % target)
                f.write('\t\t},\n')
            f.write('\t},\n}\n')

    def write_entries(self, rows):
        with io.open(edges_import.ENTRIES, "w", encoding="utf-8") as f:
            f.write("ID,PositionY,ClassType,TabType\n" + rows)

    def run_main(self, check=False):
        output = io.StringIO()
        argv = ["edges_import.py"] + (["--check"] if check else [])
        with mock.patch.object(sys, "argv", argv), contextlib.redirect_stdout(output):
            result = edges_import.main()
        return result, output.getvalue()

    def test_valid_graph_writes_exact_edges(self):
        result, output = self.run_main()
        self.assertEqual(result, 0)
        with io.open(edges_import.OUT, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"2": [1]})
        self.assertIn("wrote ", output)

    def test_loading_edges_closes_source_file(self):
        with warnings.catch_warnings(record=True) as caught:
            warnings.simplefilter("always", ResourceWarning)
            self.assertEqual(edges_import.load_edges(), {2: [1]})
            gc.collect()
        self.assertFalse([w for w in caught if issubclass(w.category, ResourceWarning)])

    def test_check_never_creates_or_replaces_output(self):
        for rows, expected in (("1,0,1,1\n2,1,1,1\n", 0),
                               ("1,0,2,1\n2,1,1,1\n", 1)):
            with self.subTest(expected=expected):
                self.write_entries(rows)
                with mock.patch.object(edges_import, "OUT",
                                       os.path.join(self.temp.name, str(expected))):
                    result, output = self.run_main(check=True)
                    self.assertEqual(result, expected)
                    self.assertFalse(os.path.exists(edges_import.OUT))
                    with io.open(edges_import.OUT, "w", encoding="utf-8") as f:
                        f.write("unchanged")
                    result, output = self.run_main(check=True)
                    self.assertEqual(result, expected)
                    with io.open(edges_import.OUT, encoding="utf-8") as f:
                        self.assertEqual(f.read(), "unchanged")
                    self.assertIn("check only -- nothing written", output)

    def test_documented_dangling_targets_are_diagnostics_not_failure(self):
        # SCHEMA.md documents these targets as absent, not invalid sources.
        edges = {2: [1, 6451, 7181, 17567]}
        self.write_edges(edges)
        for check in (True, False):
            result, output = self.run_main(check)
            self.assertEqual(result, 0)
            self.assertIn("dangling targets (drop on import): [6451, 7181, 17567]",
                          output)
        with io.open(edges_import.OUT, encoding="utf-8") as f:
            self.assertEqual(json.load(f), {"2": edges[2]})

    def test_existing_invariants_still_reject_invalid_graphs(self):
        for name, edges, rows in (
                ("reciprocal", {2: [1], 1: [2, 3]}, "1,0,1,1\n2,0,1,1\n3,0,1,1\n"),
                ("downward", {2: [1], 3: [2]}, "1,0,1,1\n2,1,1,1\n3,0,1,1\n"),
                ("tab", {2: [1]}, "1,0,1,2\n2,1,1,1\n"),
                ("nonzero root", {2: [1]}, "1,1,1,1\n2,2,1,1\n")):
            with self.subTest(name=name):
                self.write_edges(edges)
                self.write_entries(rows)
                result, output = self.run_main()
                self.assertEqual(result, 1)
                self.assertFalse(os.path.exists(edges_import.OUT))

    def test_missing_input_file_fails_without_writing(self):
        with mock.patch.object(edges_import, "SV",
                               os.path.join(self.temp.name, "absent.lua")):
            with self.assertRaisesRegex(SystemExit, "missing "):
                self.run_main()
        self.assertFalse(os.path.exists(edges_import.OUT))

    def test_missing_source_row_fails_without_writing(self):
        self.write_edges({2: [1], 3: [1]})
        for check in (False, True):
            with self.subTest(check=check):
                result, output = self.run_main(check)
                self.assertEqual(result, 1)
                self.assertFalse(os.path.exists(edges_import.OUT))
                self.assertIn("missing source", output)

    def test_missing_or_empty_entries_fail_without_writing(self):
        for filename, contents in (("absent.csv", None), ("empty.csv", ""),
                                   ("header.csv", "ID,PositionY,ClassType,TabType\n")):
            with self.subTest(filename=filename):
                path = os.path.join(self.temp.name, filename)
                if contents is not None:
                    with io.open(path, "w", encoding="utf-8") as f:
                        f.write(contents)
                with mock.patch.object(edges_import, "ENTRIES", path):
                    for check in (False, True):
                        result, output = self.run_main(check)
                        self.assertEqual(result, 1)
                        self.assertFalse(os.path.exists(edges_import.OUT))

    def test_invalid_graph_does_not_create_or_replace_output(self):
        self.write_entries("1,0,2,1\n2,1,1,1\n")
        result, output = self.run_main()
        self.assertEqual(result, 1)
        self.assertFalse(os.path.exists(edges_import.OUT))
        with io.open(edges_import.OUT, "w", encoding="utf-8") as f:
            f.write("preserve existing bytes\n")
        result, output = self.run_main()
        self.assertEqual(result, 1)
        with io.open(edges_import.OUT, encoding="utf-8") as f:
            self.assertEqual(f.read(), "preserve existing bytes\n")
        self.assertNotIn("wrote ", output)


if __name__ == "__main__":
    unittest.main()
