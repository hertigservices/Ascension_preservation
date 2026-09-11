"""Checks for import_worldforged.py, run against throwaway git repositories.

    python -B test_worldforged.py

The load-bearing properties:
- the snapshot holds exactly the pinned commit's bytes, even when the working
  tree beside it has been edited or has an untracked file added;
- only data/ is republished, and every other file is named as omitted with a reason;
- address- and path-shaped content in a dataset stops the import;
- a tampered or padded snapshot fails verification.
"""
import gzip, json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import import_worldforged as wf

PINS = "item_id,item_name,server_map\n1,Ring,0\n2,Bootz,1\n3,Missing Item,0\n"
CHAINS = json.dumps({"_source": "LootCollector/Modules/WorldforgedUpgrades.lua", "data": {"1": {"ZG": 2}}})
BASE_REV = "a" * 40


def run(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class ImportWorldforgedTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="wf-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "upstream"
        self.write({"data/wf_pins_union.csv": PINS, "data/lootsmith/wf_upgrade_chains.json": CHAINS,
                    "tools/build.py": "print('x')\n", "README.md": "Built on D:\\project\n",
                    "docs/report.md": "report\n"})
        run(self.tmp, "init", "-q", str(self.repo))
        self.commit = self.commit_all("first")
        self.cache = self.tmp / "itemcache.tsv.gz"
        self.cache.write_bytes(gzip.compress(b"entry\tclass\tname\n1\t4\tRing\n2\t4\tBoots\n"))
        self.out = self.tmp / "out"

    def write(self, files):
        for rel, text in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text, encoding="utf-8", newline="\n")

    def commit_all(self, message):
        run(self.repo, "add", "-A")
        run(self.repo, "-c", "user.name=T", "-c", "user.email=t@example.invalid", "commit", "-q", "-m", message)
        return subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()

    def ingest(self, commit=None):
        return wf.ingest(self.repo, self.out, commit or self.commit, self.cache, BASE_REV)

    def test_only_datasets_are_republished_and_the_rest_is_named(self):
        m = wf.verify(self.ingest())
        self.assertEqual(["data/lootsmith/wf_upgrade_chains.json", "data/wf_pins_union.csv"], sorted(m["files"]))
        omitted = {o["path"]: o["reason"] for o in m["omitted"]}
        self.assertEqual({"README.md", "docs/report.md", "tools/build.py"}, set(omitted))
        self.assertIn("local paths", omitted["README.md"])
        self.assertNotIn("t@example.invalid", json.dumps(m))

    def test_comparison_names_missing_ids_and_conflicting_names(self):
        target = self.ingest()
        c = json.loads(gzip.decompress((target / "comparison.json.gz").read_bytes()))
        pins = c["files"]["data/wf_pins_union.csv"]
        self.assertEqual([3], pins["missing_ids"])
        self.assertEqual([{"item_id": 2, "source_name": "Bootz", "captured_name": "Boots"}], pins["name_conflicts"])
        self.assertEqual(2, c["files"]["data/lootsmith/wf_upgrade_chains.json"]["captured"])

    def test_snapshot_is_the_commit_not_the_working_tree(self):
        self.write({"data/wf_pins_union.csv": "item_id,item_name,server_map\n9,Edited,0\n",
                    "data/untracked.csv": "item_id,item_name\n7,New\n"})
        target = self.ingest()
        self.assertEqual(PINS.encode(), gzip.decompress((target / "data/wf_pins_union.csv.gz").read_bytes()))
        self.assertFalse((target / "data/untracked.csv.gz").exists())

    def test_rerun_is_a_no_op(self):
        target = self.ingest()
        before = (target / "manifest.json").read_bytes()
        self.assertEqual(target, self.ingest())
        self.assertEqual(before, (target / "manifest.json").read_bytes())

    def test_tampered_or_padded_snapshot_fails(self):
        target = self.ingest()
        extra = target / "data" / "extra.csv.gz"
        extra.write_bytes(gzip.compress(b"x\n"))
        with self.assertRaises(ValueError):
            wf.verify(target)
        extra.unlink()
        wf.verify(target)
        art = target / "data/wf_pins_union.csv.gz"
        art.write_bytes(gzip.compress(PINS.replace("Ring", "Rung").encode(), mtime=0))
        with self.assertRaises(ValueError):
            wf.verify(target)

    def test_address_or_path_in_a_dataset_stops_the_import(self):
        for text in ("item_id,item_name\n1,mail someone@example.com\n", "item_id,item_name\n1,C:\\Users\\x\n"):
            with self.subTest(text=text):
                self.write({"data/wf_pins_union.csv": text})
                commit = self.commit_all("bad")
                with self.assertRaises(ValueError):
                    self.ingest(commit)

    def test_commit_ids_must_be_full_and_real(self):
        for bad in (self.commit[:12], "b" * 40):
            with self.subTest(commit=bad):
                with self.assertRaises(ValueError):
                    self.ingest(bad)

    def test_manifest_blob_ids_match_git(self):
        m = wf.verify(self.ingest())
        blob = subprocess.run(["git", "-C", str(self.repo), "rev-parse", f"{self.commit}:data/wf_pins_union.csv"],
                              check=True, capture_output=True, text=True).stdout.strip()
        self.assertEqual(blob, m["files"]["data/wf_pins_union.csv"]["git_blob"])


if __name__ == "__main__":
    unittest.main()
