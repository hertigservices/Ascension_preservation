"""Checks for ingest_lootcollector.py: privacy first, then the arithmetic.

    python -B test_lootcollector.py

The fixture is a hand-written SavedVariables file shaped the way the addon
writes them. It plants the player's name, another sharer's name, a voter's name
and an e-mail address where the addon keeps those, plus a realm key shaped like
an address. The load-bearing checks:
- none of that can reach any output;
- a file that holds only zero bytes, or does not parse, contributes nothing and
  quotes nothing.

The coordinate check does not use a number this code produced. It uses a pin that
Tareksoh/Worldforged-data measured independently (item 132418 in Razorfen Kraul,
map (0.1273, 0.3504), is server 2178.0, 1965.2).
"""
import os, sys, shutil, tempfile, unittest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import ingest_lootcollector as lc

FIXTURE = '''
LootCollectorDB_Asc = {
    ["char"] = {
        ["Zelphina - Vol'jin - Conquest of Azeroth"] = {
            ["looted"] = { ["2-761-0-132418-0.1273-0.3504"] = 1759000000, },
        },
    },
    ["profileKeys"] = { ["Zelphina - Vol'jin - Conquest of Azeroth"] = "Default", },
    ["global"] = {
        ["realms"] = {
            ["Vol'jin - Conquest of Azeroth"] = {
                ["discoveries"] = {
                    ["2-761-0-132418-0.1273-0.3504"] = {
                        ["dt"] = 1, ["i"] = 132418, ["z"] = 762, ["iz"] = 0, ["c"] = 2, ["q"] = 4,
                        ["s"] = "CONFIRMED", ["t0"] = 1759500000, ["ls"] = 1760000000, ["mc"] = 3,
                        ["fp"] = "Zelphina", ["o"] = "Mortimer",
                        ["fp_votes"] = { ["Quixotica"] = { ["score"] = 1, ["t0"] = 1759000000, }, },
                        ["il"] = "|cff1eff00|Hitem:132418|h[Worldforged Scroll: Saber Slash]|h|r",
                        ["xy"] = { ["x"] = 0.1273, ["y"] = 0.3504, },
                    },
                    ["2-761-0-132418-0.1300-0.3530"] = {
                        ["dt"] = 1, ["i"] = 132418, ["z"] = 762, ["s"] = "FADING",
                        ["t0"] = 1759400000, ["ls"] = 1759600000, ["fp"] = "someone@example.com",
                        ["xy"] = { ["x"] = 0.13, ["y"] = 0.353, },
                    },
                    ["2-761-0-132418-0.9000-0.9000"] = {
                        ["dt"] = 1, ["i"] = 132418, ["z"] = 762, ["xy"] = { ["x"] = 0.9, ["y"] = 0.9, },
                    },
                    ["1-12-0-201537-0.5000-0.5000"] = {
                        ["dt"] = 2, ["i"] = 201537, ["z"] = 99999, ["xy"] = { ["x"] = 0.5, ["y"] = 0.5, },
                    },
                    ["Zelphina"] = {
                        ["dt"] = 1, ["i"] = 5, ["z"] = 762, ["xy"] = { ["x"] = 0.1, ["y"] = 0.1, },
                    },
                    ["2-761-0-7-0.1-0.1"] = {
                        ["dt"] = 1, ["i"] = 7, ["z"] = 762, ["xy"] = { ["x"] = 1.5, ["y"] = 0.1, },
                    },
                },
            },
            ["someone@example.com - Leak"] = {
                ["discoveries"] = {
                    ["2-761-0-9-0.1-0.1"] = {
                        ["dt"] = 1, ["i"] = 9, ["z"] = 762, ["xy"] = { ["x"] = 0.1, ["y"] = 0.1, },
                    },
                },
            },
        },
    },
}
'''
BOUNDS = {761: dict(map=47, area_id=491, zone="Razorfen Kraul", name="RazorfenKraul",
                    left=2059.0, right=1322.0, top=2350.0, bottom=1859.0)}
PLANTED = ("Zelphina", "Mortimer", "Quixotica", "someone@example.com", "person@example.com", "Leak")


class LootCollectorTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp(prefix="lc-test-")
        self.addCleanup(shutil.rmtree, self.tmp, True)

    def place(self, rel, data):
        p = os.path.join(self.tmp, rel)
        os.makedirs(os.path.dirname(p), exist_ok=True)
        with open(p, "wb") as f:
            f.write(data)
        return p

    def outputs(self, res):
        return {"pins.tsv": lc.tsv(lc.PIN_COLS, res["pins"]), "items.tsv": lc.tsv(lc.ITEM_COLS, res["items"]),
                "sources.tsv": lc.tsv(lc.SOURCE_COLS, res["sources"]), "README.md": lc.readme(res)}

    def run_fixture(self):
        self.place("person@example.com/SavedVariables/LootCollector.lua", FIXTURE.encode())
        return lc.build(lc.discover([self.tmp]), BOUNDS)

    def test_nothing_personal_reaches_any_output(self):
        texts = self.outputs(self.run_fixture())
        lc.check_outputs(texts)
        for name, text in texts.items():
            for planted in PLANTED + (self.tmp, os.path.basename(self.tmp)):
                self.assertNotIn(planted, text, f"{planted!r} leaked into {name}")

    def test_coordinates_match_an_independent_measurement(self):
        x, y = lc.to_server(BOUNDS[761], 0.1273, 0.3504)
        self.assertEqual(("2178.0", "1965.2"), (f"{x:.1f}", f"{y:.1f}"))

    def test_near_copies_fold_and_far_ones_do_not(self):
        pins = [p for p in self.run_fixture()["pins"] if p["item"] == 132418]
        self.assertEqual([2, 1], [p["records"] for p in pins])
        self.assertEqual("CONFIRMED", pins[0]["status"])
        # The mean of 0.1273 and 0.13 is stored as 0.128649..., so it prints as 0.1286.
        self.assertEqual(("0.1286", "0.3517"), (pins[0]["norm_x"], pins[0]["norm_y"]))
        self.assertEqual("conquest", pins[0]["modes"].split("-")[0])

    def test_bad_keys_and_positions_are_skipped(self):
        items = {p["item"] for p in self.run_fixture()["pins"]}
        self.assertEqual({132418, 201537}, items)

    def test_same_discovery_in_two_files_counts_once(self):
        self.place("a/SavedVariables/LootCollector.lua", FIXTURE.encode())
        self.place("b/SavedVariables/LootCollector.lua.bak", FIXTURE.encode() + b"\n-- older copy\n")
        res = lc.build(lc.discover([self.tmp]), BOUNDS)
        pin = next(p for p in res["pins"] if p["item"] == 132418)
        self.assertEqual((2, 2), (pin["records"], pin["files"]))
        self.assertEqual(["bak", "lua"], sorted(s["kind"] for s in res["sources"]))

    def test_identical_files_are_one_source(self):
        self.place("a/LootCollector.lua", FIXTURE.encode())
        self.place("b/LootCollector.lua", FIXTURE.encode())
        self.assertEqual(1, len(lc.discover([self.tmp])))

    def test_empty_and_broken_files_contribute_and_quote_nothing(self):
        self.assertEqual(("empty (no content)", []), lc.records_of(b"\x00" * 4096))
        status, recs = lc.records_of(b'LootCollectorDB_Asc = { ["fp"] = "Zelphina" = = }')
        self.assertEqual(("parse error", []), (status, recs))

    def test_unmapped_zone_is_reported_not_guessed(self):
        res = self.run_fixture()
        scroll = next(p for p in res["pins"] if p["item"] == 201537)
        self.assertEqual(("mystic_scroll", "", ""), (scroll["type"], scroll["map"], scroll["server_x"]))
        self.assertEqual([99999], res["unmapped"])

    def test_check_outputs_refuses_leaks(self):
        for bad in ("x@y.z", "C:/Users/a", "D:\\games", "WTF/Account/x", "Account/abc"):
            with self.assertRaises(ValueError):
                lc.check_outputs({"t": bad})

    def test_parse_cache_round_trips(self):
        p = self.place("c/LootCollector.lua", FIXTURE.encode())
        cache = os.path.join(self.tmp, "cache")
        first = lc.cached_records("f" * 64, p, cache)
        self.assertTrue(os.path.isfile(os.path.join(cache, "f" * 64 + ".json")))
        os.remove(p)
        self.assertEqual(first, lc.cached_records("f" * 64, p, cache))

    def test_shipped_bounds_hold_the_named_rows(self):
        b = lc.load_bounds()
        self.assertEqual((1, "Durotar", 1808.0, -1716.0, -1962.0, -7250.0),
                         (b[4]["map"], b[4]["zone"], b[4]["top"], b[4]["bottom"], b[4]["left"], b[4]["right"]))
        self.assertEqual((47, "RazorfenKraul"), (b[761]["map"], b[761]["name"]))


if __name__ == "__main__":
    unittest.main()
