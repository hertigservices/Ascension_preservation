"""The world catalogue must say which entries are stock from the stock table,
never from an id range, and may only fill a type or model a dump left blank."""
import collections, csv, io, os, sys, tempfile, unittest

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import ingest_gameobjects as ig


def entry(types=(), displays=()):
    return {"types": collections.Counter(types), "displays": collections.Counter(displays),
            "cache_type": "", "cache_display": "", "origin": ""}


class StockReferenceTests(unittest.TestCase):
    def write(self, text):
        t = tempfile.NamedTemporaryFile("w", suffix=".txt", delete=False, encoding="utf-8")
        t.write(text); t.close(); self.addCleanup(os.unlink, t.name)
        return t.name

    def test_ranges_and_single_ids(self):
        s = ig.load_stock(self.write("# c\n[gameobject_template]\n4\n30-38\n\n"
                                     "[creature_template]\n68\n"))
        r = s["gameobject_template"]
        for gid, want in ((3, False), (4, True), (5, False), (29, False), (30, True),
                          (34, True), (38, True), (39, False)):
            self.assertEqual(ig.is_stock(r, gid), want, gid)

    def test_missing_table_refuses_rather_than_calling_everything_custom(self):
        with self.assertRaises(SystemExit):
            ig.load_stock(self.write("[gameobject_template]\n4\n"))

    def test_id_before_any_table_refuses(self):
        with self.assertRaises(SystemExit):
            ig.load_stock(self.write("4\n[gameobject_template]\n5\n[creature_template]\n6\n"))

    def test_shipped_reference_contradicts_the_old_id_range(self):
        # Both halves of the retired "stock below 200000" rule, as named rows.
        s = ig.load_stock()
        go, cr = s["gameobject_template"], s["creature_template"]
        self.assertFalse(ig.is_stock(go, 90636))     # Forgotten Sack, worldforged
        self.assertFalse(ig.is_stock(go, 101911))    # Drowned Adventurer
        self.assertTrue(ig.is_stock(go, 200296))     # Washing Tub, stock WotLK
        self.assertTrue(ig.is_stock(go, 31))         # Old Lion Statue
        self.assertTrue(ig.is_stock(cr, 68))         # Stormwind City Guard
        self.assertFalse(ig.is_stock(cr, 161791))    # Uneasy Citizen

    def test_origins_are_assigned_per_entry(self):
        folded = {31: entry(), 90636: entry()}
        ig.set_origins(folded, [(31, 31)])
        self.assertEqual((folded[31]["origin"], folded[90636]["origin"]), ("stock", "ascension"))


class CacheFillTests(unittest.TestCase):
    def test_dump_value_stands_and_is_compared(self):
        f = {1: entry(["Chest"], ["7678"])}
        st = ig.apply_cache(f, {1: (3, 7679)})
        self.assertEqual((ig.final_type(f[1]), f[1]["cache_display"]), ("Chest", ""))
        self.assertEqual((st["type_agree"], st["type_compared"]), (1, 1))
        self.assertEqual((st["display_agree"], st["display_compared"]), (0, 1))

    def test_blank_is_filled_with_the_dump_spelling(self):
        f = {1: entry(), 2: entry()}
        st = ig.apply_cache(f, {1: (2, 6), 2: (15, 0)})
        self.assertEqual((ig.final_type(f[1]), f[1]["cache_display"]), ("Questgiver", "6"))
        self.assertEqual((ig.final_type(f[2]), f[2]["cache_display"]), ("MOTransport", "0"))
        self.assertEqual((st["type_filled"], st["display_filled"]), (2, 2))

    def test_untyped_door_is_unknown_until_the_cache_says_otherwise(self):
        t = ig.trusted_type("Door", "")
        f = {1628: entry([t] if t else [])}
        ig.apply_cache(f, {1628: (3, 357)})
        self.assertEqual(ig.final_type(f[1628]), "Chest")    # Grave Moss, a herb

    def test_unnamed_type_number_is_left_blank_and_counted(self):
        f = {1: entry()}
        st = ig.apply_cache(f, {1: (99, 5)})
        self.assertEqual((ig.final_type(f[1]), st["type_unknown_number"]), ("", 1))

    def test_no_cache_record_changes_nothing(self):
        f = {1: entry()}
        ig.apply_cache(f, {})
        self.assertEqual((ig.final_type(f[1]), f[1]["cache_display"]), ("", ""))


class WriteTests(unittest.TestCase):
    def test_origin_is_last_and_leading_quotes_survive(self):
        rows = [{"id": 518155, "name": '"Evidence"', "type": "", "display_id": "",
                 "zone": "Dustwallow", "map_id": 1, "x": 1.0, "y": 2.0, "z": 3.0,
                 "map_x": 29.6, "map_y": 48.6, "lock_id": None, "lock_type": "",
                 "submission": "s", "file": "f"}]
        folded = ig.fold(rows)
        ig.set_origins(folded, [])
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "g.tsv")
            ig.write_tsv(p, folded)
            with io.open(p, encoding="utf-8", newline="") as fh:
                r = list(csv.reader(fh, delimiter="\t", quoting=csv.QUOTE_NONE))
        self.assertEqual(r[0][-1], "origin")
        self.assertEqual((r[1][1], r[1][-1]), ('"Evidence"', "ascension"))


if __name__ == "__main__":
    unittest.main()
