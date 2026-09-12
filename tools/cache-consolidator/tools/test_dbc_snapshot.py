"""Checks for import_dbc_snapshot.py, run against small synthetic DBCs.

    python -B test_dbc_snapshot.py

The load-bearing properties:
- a member byte-identical to the reference is named with its hash and not republished;
- a differing member is republished, and its diff names the rows only in either file and the changed fields;
- a string that only moved within the string block is not a change;
- a table keyed by row index reads as one missing row by content, however many rows its keys shift;
- a reference copy that does not match its holders row, an address-shaped string, or a path in a
  member name or the reference label stops the import;
- a tampered, padded or re-referenced snapshot fails verification.
"""
import gzip, io, json, os, shutil, struct, sys, tempfile, unittest, zipfile
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import import_dbc_snapshot as ds

LABEL = "test reference client"


def dbc(rows, strings=b"\0"):
    fields = len(rows[0]) if rows else 1
    body = b"".join(struct.pack("<%dI" % fields, *r) for r in rows)
    return struct.pack("<4s4I", b"WDBC", len(rows), fields, 4 * fields, len(strings)) + body + strings


SAME = dbc([(1, 5), (2, 6)])
# Keyed table with two string columns, the second sometimes naming an EMPTY string at a nonzero
# offset (as Spell.dbc's locale slots do). The snapshot's string block has an extra string in
# front, so every offset moved; only id 2's number changed, and id 3 is new.
KEYED_REF = dbc([(1, 1, 10, 11), (2, 6, 20, 1)], b"\0Wolf\0Bear\0\0")
KEYED_SNAP = dbc([(1, 4, 10, 14), (2, 9, 21, 4), (3, 4, 30, 14)], b"\0XX\0Wolf\0Bear\0\0")
# Keyed by row index: the reference has one more row in the middle, so every key after it shifts.
INDEX_REF = dbc([(1, 100), (2, 150), (3, 200), (4, 300)])
INDEX_SNAP = dbc([(1, 100), (2, 200), (3, 300)])
UNREFERENCED = dbc([(7, 7)])


class ImportDbcSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="dbcsnap-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.ref = self.tmp / "reference"
        self.ref.mkdir()
        self.reference({"Same.dbc": SAME, "Keyed.dbc": KEYED_REF, "Index.dbc": INDEX_REF})
        self.zip = self.make_zip({"Same.dbc": SAME, "Keyed.dbc": KEYED_SNAP, "Index.dbc": INDEX_SNAP,
                                  "Extra.dbc": UNREFERENCED, "Keyed.csv": b'"ID","Name"\n"1","Wolf"\n'})
        self.out = self.tmp / "out"

    def reference(self, files):
        rows = ["name\tarchive\tsha256"]
        for name, data in files.items():
            (self.ref / name).write_bytes(data)
            rows.append(f"{name}\tpatch-M.MPQ\t{ds.sha256(data)}")
        self.holders = self.tmp / "holders.tsv"
        self.holders.write_text("\n".join(rows) + "\n", encoding="utf-8")

    def make_zip(self, files, name="dump.zip"):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            for member, data in files.items():
                z.writestr(member, data)
        path = self.tmp / name
        path.write_bytes(buf.getvalue())
        return path

    def ingest(self, zip_path=None, label=LABEL):
        return ds.ingest(zip_path or self.zip, self.ref, self.holders, label, "2026-09-11", self.out)

    def diffs(self, target):
        return json.loads(gzip.decompress((target / "diff.json.gz").read_bytes()))

    def test_identical_members_are_named_not_republished(self):
        target = self.ingest()
        m = ds.verify(target, self.ref)
        self.assertEqual("identical", m["members"]["Same.dbc"]["verdict"])
        self.assertEqual({"diff.json.gz", "dbc/Keyed.dbc.gz", "dbc/Index.dbc.gz", "dbc/Extra.dbc.gz"}, set(m["artifacts"]))
        self.assertEqual({"identical": 1, "differs": 2, "no reference": 1, "not republished": 1}, m["summary"])
        self.assertEqual("not republished", m["members"]["Keyed.csv"]["verdict"])
        self.assertEqual(KEYED_SNAP, gzip.decompress((target / "dbc/Keyed.dbc.gz").read_bytes()))

    def test_diff_names_new_rows_and_changed_fields_but_not_moved_strings(self):
        d = self.diffs(self.ingest())["Keyed.dbc"]
        self.assertEqual([1, 3], d["string_columns"])
        by_id = d["by_first_field"]
        self.assertEqual([3], by_id["only_in_snapshot_ids"])
        self.assertEqual(0, by_id["only_in_reference"])
        self.assertEqual(1, by_id["changed"])
        self.assertEqual([{"id": 2, "fields_changed": 1, "fields": [{"field": 2, "snapshot": 21, "reference": 20}]}],
                         by_id["changed_examples"])
        self.assertEqual((2, 1), (d["by_content"]["only_in_snapshot"], d["by_content"]["only_in_reference"]))

    def test_row_index_table_reads_as_one_missing_row_by_content(self):
        d = self.diffs(self.ingest())["Index.dbc"]
        self.assertEqual(2, d["by_first_field"]["changed"])
        self.assertEqual([4], d["by_first_field"]["only_in_reference_ids"])
        self.assertEqual((0, 1), (d["by_content"]["only_in_snapshot"], d["by_content"]["only_in_reference"]))

    def test_rerun_is_a_no_op(self):
        target = self.ingest()
        before = (target / "manifest.json").read_bytes()
        self.assertEqual(target, self.ingest())
        self.assertEqual(before, (target / "manifest.json").read_bytes())

    def test_tampered_padded_or_rereferenced_snapshot_fails(self):
        target = self.ingest()
        extra = target / "dbc" / "Padding.dbc.gz"
        extra.write_bytes(gzip.compress(SAME))
        with self.assertRaises(ValueError):
            ds.verify(target)
        extra.unlink()
        ds.verify(target)
        (self.ref / "Keyed.dbc").write_bytes(KEYED_SNAP)
        with self.assertRaises(ValueError):
            ds.verify(target, self.ref)
        (self.ref / "Keyed.dbc").write_bytes(KEYED_REF)
        (target / "dbc/Index.dbc.gz").write_bytes(gzip.compress(INDEX_REF, mtime=0))
        with self.assertRaises(ValueError):
            ds.verify(target)

    def test_reference_copy_must_match_its_holders_row(self):
        (self.ref / "Index.dbc").write_bytes(INDEX_SNAP)
        with self.assertRaises(ValueError):
            self.ingest()

    def test_address_or_path_shapes_stop_the_import(self):
        bad = self.make_zip({"Keyed.dbc": dbc([(1, 1, 10), (2, 6, 20)], b"\0mail x@example.com\0Bear\0")}, "bad.zip")
        with self.assertRaises(ValueError):
            self.ingest(bad)
        ok = self.make_zip({"Keyed.dbc": dbc([(1, 1, 10), (2, 20, 20)], b"\0techbot@gnome.mail\0Bear\0")}, "ok.zip")
        ds.verify(self.ingest(ok))
        with self.assertRaises(ValueError):
            self.ingest(self.make_zip({"dir/Keyed.dbc": KEYED_SNAP}, "nested.zip"))
        with self.assertRaises(ValueError):
            self.ingest(self.make_zip({"Keyed.dbc": KEYED_SNAP}, "label.zip"), label="C:\\Users\\someone\\client")


if __name__ == "__main__":
    unittest.main()
