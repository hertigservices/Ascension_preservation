"""Checks for import_coa_databank.py, run against throwaway git repositories.

    python -B test_coa_databank.py

The load-bearing properties:
- a build keeps its data but loses its author, its comments and every similar build's author;
- those usernames are replaced in the build's strings, except that a name which is also game text
  is replaced only in titles, so a guide keeps its ordinary words;
- nothing outside the build files is touched by the username rule;
- a drive-rooted path loses its drive and any Users\\<name> segment, and the counts are recorded;
- every untransformed file is the pinned commit's bytes, even when the working tree was edited;
- code and raw build pages are omitted with a reason, and identical blobs share one artifact;
- an address, avatar URL or unknown author field left in published text stops the import;
- verify --checkout re-derives every file, and a tampered or padded snapshot fails.
"""
import gzip, json, os, shutil, subprocess, sys, tempfile, unittest
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import import_coa_databank as cd

UUID = "102d9cf7-095d-4fb4-80e7-c0949104d65c"
BUILD = {
    "build": {"id": UUID, "title": "Noobheal Invention Build", "description": "thanks barbastellus! zedd style",
              "author_id": "a1", "author": {"id": "a1", "username": "Noobheal",
                                           "avatar_url": "https://cdn.discordapp.com/avatars/123/x.png"}},
    "comments": [{"id": "c1", "author_id": "a2", "body": "hi \u00e9",
                  "author": {"id": "a2", "username": "barbastellus", "avatar_url": None}}],
    "similar": [{"id": "s1", "title": "Other build by Zedd", "author_id": "a3",
                 "author": {"id": "a3", "username": "Zedd", "avatar_url": None}}],
    "class": "tinker",
}
BUILD_MD = ("# Noobheal Invention Build\r\n\r\n- **Author:** Noobheal\r\n- **Patch:** 1.0\r\n\r\n"
            "## Guide\r\nthanks barbastellus\r\n\r\n## Comments\r\n\r\n**barbastellus:** hi\r\n")
PALETTE = b'{"path": "C:\\\\CoA\\\\dl\\\\x.blp"}\n'
# "zedd" is ordinary game text here, so the user named Zedd is replaced in titles only.
SKILLS = '{"tooltip": "zedd is not a word here"}'
# Outside the build files a username is left alone, even one replaced inside them.
PAGE = "<p>Noobheal and barbastellus wrote this</p>"


def crlf_json(obj):
    return json.dumps(obj, ensure_ascii=False, indent=1).replace("\n", "\r\n")


def run(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)


class ImportCoaDatabankTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="coadb-test-"))
        self.addCleanup(shutil.rmtree, self.tmp, True)
        self.repo = self.tmp / "upstream"
        self.write({
            f"coabuildhub/builds/tinker/{UUID}.json": crlf_json(BUILD),
            f"coabuildhub/builds/tinker/{UUID}.md": BUILD_MD,
            "coabuildhub/skills/tinker.json": SKILLS,
            "coabuildhub/raw/pages/about.html": PAGE,
            "coabuildhub/tools/scrape.py": "OUT = r'C:\\Users\\Byte\\x'\n",
            f"coabuildhub/raw/builds/{UUID}.html": '<img src="https://cdn.discordapp.com/avatars/123/x.png">',
            "provenance/source-manifest.json": '{"source": "C:\\\\TrinityBots\\\\a.md", "home": "C:\\\\Users\\\\Byte\\\\b"}',
            "provenance/palette/assets.jsonl.gz": gzip.compress(PALETTE, mtime=0),
            "databank/palette/assets.jsonl.gz": gzip.compress(PALETTE, mtime=0),
            "provenance/palette/plain.jsonl.gz": gzip.compress(b'{"id": 1}\n', mtime=0),
            # An FAQ label, not a drive: A: followed by an escaped quote must survive untouched.
            "provenance/web/page.html": '<script>{"email":"support@ascension.gg","t":"{\\"faq_answer_tag\\":\\"A:\\"}"}</script>',
            "provenance/archive/icons/a.jpg": b"\xff\xd8\xff\xe0 not really a jpeg C:\\Users\\x",
            ".gitignore": "__pycache__/\n",
        })
        run(self.tmp, "init", "-q", str(self.repo))
        self.commit = self.commit_all("first")
        self.out = self.tmp / "out"

    def write(self, files):
        for rel, content in files.items():
            p = self.repo / rel
            p.parent.mkdir(parents=True, exist_ok=True)
            if isinstance(content, str):
                content = content.encode("utf-8")
            p.write_bytes(content)

    def commit_all(self, message):
        # autocrlf off for add, not just commit: a machine-wide autocrlf=true turns the
        # fixture's CRLF build file into an LF blob, and the serialisation test means CRLF.
        run(self.repo, "-c", "core.autocrlf=false", "add", "-A")
        run(self.repo, "-c", "user.name=Byte", "-c", "user.email=b@example.invalid", "-c", "core.autocrlf=false",
            "commit", "-q", "-m", message)
        return subprocess.run(["git", "-C", str(self.repo), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()

    def ingest(self, commit=None):
        return cd.ingest(self.repo, self.out, commit or self.commit)

    def content(self, target, manifest, path):
        return gzip.decompress((target / manifest["files"][path]["artifact"]).read_bytes()).decode("utf-8")

    def test_builds_lose_identities_and_keep_their_data(self):
        target = self.ingest()
        m = cd.verify(target)
        text = self.content(target, m, f"coabuildhub/builds/tinker/{UUID}.json")
        obj = json.loads(text)
        self.assertNotIn("comments", obj)
        self.assertEqual({"id", "title", "description"}, set(obj["build"]))
        self.assertEqual("[user] Invention Build", obj["build"]["title"])
        self.assertEqual("thanks [user]! zedd style", obj["build"]["description"])
        self.assertEqual("Other build by [user]", obj["similar"][0]["title"])
        self.assertNotIn("author", obj["similar"][0])
        self.assertEqual("tinker", obj["class"])
        self.assertTrue("\r\n" in text and not text.endswith("\n"))
        md = self.content(target, m, f"coabuildhub/builds/tinker/{UUID}.md")
        self.assertEqual("# [user] Invention Build\r\n\r\n- **Patch:** 1.0\r\n\r\n## Guide\r\nthanks [user]\r\n", md)
        self.assertEqual({"distinct": 3, "title_only": 1}, m["usernames"])
        self.assertNotIn("Noobheal", (target / "manifest.json").read_text(encoding="utf-8"))

    def test_username_rule_touches_only_build_files(self):
        target = self.ingest()
        m = cd.verify(target)
        for path, text in (("coabuildhub/skills/tinker.json", SKILLS), ("coabuildhub/raw/pages/about.html", PAGE)):
            with self.subTest(path=path):
                self.assertEqual({}, m["files"][path]["transforms"])
                self.assertEqual(text, self.content(target, m, path))

    def test_local_paths_lose_their_drive_and_user_segment(self):
        target = self.ingest()
        m = cd.verify(target)
        f = m["files"]["provenance/source-manifest.json"]
        self.assertEqual({"local-paths": {"drives": 2, "user_segments": 1}}, f["transforms"])
        self.assertEqual('{"source": "<local>\\\\TrinityBots\\\\a.md", "home": "<local>\\\\Users\\\\<user>\\\\b"}',
                         self.content(target, m, "provenance/source-manifest.json"))
        self.assertEqual('{"path": "<local>\\\\CoA\\\\dl\\\\x.blp"}\n', self.content(target, m, "provenance/palette/assets.jsonl.gz"))

    def test_untransformed_files_are_the_upstream_bytes(self):
        target = self.ingest()
        m = cd.verify(target)
        blob = subprocess.run(["git", "-C", str(self.repo), "cat-file", "blob", f"{self.commit}:provenance/palette/plain.jsonl.gz"],
                              check=True, capture_output=True).stdout
        self.assertEqual(blob, (target / "provenance/palette/plain.jsonl.gz").read_bytes())
        self.assertEqual({}, m["files"]["provenance/web/page.html"]["transforms"])
        self.assertEqual({}, m["files"]["provenance/archive/icons/a.jpg"]["transforms"])

    def test_identical_blobs_share_one_artifact(self):
        m = cd.verify(self.ingest())
        dup = m["files"]["provenance/palette/assets.jsonl.gz"]
        self.assertEqual("databank/palette/assets.jsonl.gz", dup["duplicate_of"])
        self.assertEqual("databank/palette/assets.jsonl.gz", dup["artifact"])

    def test_code_and_raw_build_pages_are_omitted_with_reasons(self):
        m = cd.verify(self.ingest())
        omitted = {o["path"]: o["reason"] for o in m["omitted"]}
        self.assertEqual({".gitignore", "coabuildhub/tools/scrape.py", f"coabuildhub/raw/builds/{UUID}.html"}, set(omitted))
        self.assertIn("usernames", omitted[f"coabuildhub/raw/builds/{UUID}.html"])

    def test_snapshot_is_the_commit_not_the_working_tree(self):
        self.write({"provenance/web/page.html": "edited", "provenance/untracked.md": "new"})
        target = self.ingest()
        m = cd.verify(target)
        self.assertNotIn("provenance/untracked.md", m["files"])
        self.assertIn("support@ascension.gg", self.content(target, m, "provenance/web/page.html"))

    def test_rerun_is_a_no_op(self):
        target = self.ingest()
        before = (target / "manifest.json").read_bytes()
        self.assertEqual(target, self.ingest())
        self.assertEqual(before, (target / "manifest.json").read_bytes())

    def test_verify_checkout_rederives_and_tampering_fails(self):
        target = self.ingest()
        cd.verify(target, self.repo)
        extra = target / "provenance" / "extra.md.gz"
        extra.write_bytes(gzip.compress(b"x"))
        with self.assertRaises(ValueError):
            cd.verify(target)
        extra.unlink()
        art = target / "provenance/source-manifest.json.gz"
        art.write_bytes(gzip.compress(b'{"source": "<local>\\\\Elsewhere"}', mtime=0))
        with self.assertRaises(ValueError):
            cd.verify(target)

    def test_address_avatar_or_unknown_author_field_stops_the_import(self):
        cases = ({"provenance/notes.md": "mail someone@example.com"},
                 {"coabuildhub/pages/x.md": "![a](https://cdn.discordapp.com/avatars/99/z.png)"},
                 {"coabuildhub/raw/flight.txt": '8:{\\"author_id\\":\\"a1\\",\\"author\\":{\\"username\\":\\"someone\\"}}'},
                 {f"coabuildhub/builds/tinker/{UUID}.json": crlf_json(dict(BUILD, similar=[{"id": "s", "author_name": "x"}]))})
        for files in cases:
            with self.subTest(files=list(files)):
                self.write(files)
                commit = self.commit_all("bad")
                with self.assertRaises(ValueError):
                    self.ingest(commit)
                run(self.repo, "reset", "-q", "--hard", self.commit)

    def test_commit_ids_must_be_full_and_real(self):
        for bad in (self.commit[:12], "b" * 40):
            with self.subTest(commit=bad):
                with self.assertRaises(ValueError):
                    self.ingest(bad)


if __name__ == "__main__":
    unittest.main()
