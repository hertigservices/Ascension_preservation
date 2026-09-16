import collections
import tempfile
import unittest
from pathlib import Path

from atlas import write_gz
from zone_filter import ZoneIndex, build_zone_filter


def record(kind, payload, eid='1', mode='Unspecified'):
    return [eid, 'Example', kind, mode, 'Source', payload]


class ZoneFilterTests(unittest.TestCase):
    def setUp(self):
        self.atlas = {'areas': [
            {'id': '9001', 'name': 'Shared Name', 'maps': ['900']},
            {'id': '9002', 'name': 'Shared Name', 'maps': ['901']},
        ]}
        self.index = ZoneIndex(self.atlas)

    def test_quest_zone_is_not_category_map_or_other_capture(self):
        for value in ['-12', '0', '', '4294967284']:
            self.assertEqual(self.index.memberships('questcache.tsv.gz', record('quest', {'ZoneOrSort': value, 'PointMapId': '12'})), set())
        self.assertEqual(self.index.memberships('questcache.tsv.gz', record('quest', {'ZoneOrSort': '12'})), {'area:12'})
        self.assertEqual(self.index.area('999999'), 'area:999999')
        self.assertEqual(self.index.labels['area:999999'], 'Area 999999')

    def test_all_sighting_zones_survive_missing_coordinates(self):
        payload = {'example_zone': 'Elwynn', 'example_map_id': '0', 'zone_list': 'Elwynn | BurningSteppes | SearingGorge', 'example_map_x': 'NaN'}
        self.assertEqual(self.index.memberships('cachedata/catalogue/gameobjects.tsv', record('world-object', payload)), {'area:12', 'area:46', 'area:51'})
        self.assertNotEqual(self.index.named('Aszhara', '1'), 'area:16')

    def test_ambiguous_names_and_map_namespace_remain_distinct(self):
        self.assertTrue(self.index.named('Shared Name').startswith('named:'))
        self.assertEqual(self.index.named('Shared Name', '900'), 'area:9001')
        self.assertTrue(self.index.named('Shared Name', '12').startswith('named:'))
        self.assertTrue(self.index.named('Elwynn', '999').startswith('named:'))

    def test_npc_location_claims_need_no_usable_coordinates(self):
        payload = {'structure': {'tables': [{'header': ['Map', 'Area', 'Coords', 'Spawns'], 'rows': [
            ['Eastern Kingdoms', 'Elwynn Forest', '', '2'],
            ['Eastern Kingdoms', 'Elwynn Forest', 'NaN', '1'],
            ['Eastern Kingdoms', 'Westfall', '', '1'],
        ]}]}}
        self.assertEqual(self.index.memberships('supplemental/exiles-db/hash/npcs.jsonl.gz', record('npc', payload)), {'area:12', 'area:40'})
        self.assertEqual(self.index.memberships('cachedata/union/creaturecache.tsv.gz', record('npc', {})), set())

    def test_exiles_quest_uses_explicit_area_references_only(self):
        payload = {'references': [{'type': 'area', 'key': '12'}, {'type': 'map', 'key': '0'}, {'type': 'npc', 'key': '1'}]}
        self.assertEqual(self.index.memberships('supplemental/exiles-db/hash/quests.jsonl.gz', record('quest', payload)), {'area:12'})

    def test_facets_keep_records_modes_and_unknowns_separate(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = [record('quest', {'ZoneOrSort': '12'}, mode='CoA'), record('quest', {'ZoneOrSort': '40'}, mode='Draft'), record('quest', {'ZoneOrSort': '-12'})]
            write_gz(root / 'records/f/0.json.gz', rows)
            write_gz(root / 'atlas-links.json.gz', {})
            manifest = {'files': {'f': {'path': 'cachedata/union/questcache.tsv.gz'}}, 'atlas': {'links': 'atlas-links.json.gz'}}
            class Buckets:
                def __init__(self): self.rows = collections.defaultdict(list)
                def add(self, key, row): self.rows[key].append(row)
            buckets = Buckets()
            build_zone_filter(root, manifest, self.atlas, buckets)
            self.assertEqual(buckets.rows['zone:area:12'][0][0], 'f/0/0')
            self.assertEqual(buckets.rows['zone:area:40'][0][3], 'Draft')
            self.assertEqual(buckets.rows['zone:unknown'][0][0], 'f/0/2')
            self.assertEqual(manifest['zoneFilter']['kinds'], {'quest': 3})
            self.assertEqual(sum(z['count'] for z in manifest['zoneFilter']['zones']), 3)

    def test_subzones_include_published_ancestors_and_cycles_terminate(self):
        index = ZoneIndex({'areas': [
            {'id': '87', 'name': 'Goldshire', 'parents': ['12']},
            {'id': '12', 'name': 'Elwynn Forest', 'parents': ['87']},
        ]})
        self.assertEqual(index.memberships('cachedata/catalogue/creatures.tsv', record('world-creature', {'zone_list': 'Goldshire'})), {'area:87', 'area:12'})

    def test_placeholder_labels_use_known_crosswalk_or_area_id(self):
        index = ZoneIndex({'areas': [{'id': '1', 'name': '—'}, {'id': '999', 'name': '—'}]})
        self.assertEqual(index.labels['area:1'], 'Dun Morogh')
        self.assertEqual(index.labels['area:999'], 'Area 999')

    def test_same_named_subareas_resolve_to_their_common_zone(self):
        index = ZoneIndex({'areas': [
            {'id': '40', 'name': 'Westfall', 'maps': ['0']},
            {'id': '5219', 'name': 'Westfall', 'parents': ['40'], 'maps': ['0']},
            {'id': '5220', 'name': 'Westfall', 'parents': ['40'], 'maps': ['0']},
            {'id': '5614', 'name': 'Westfall', 'maps': ['0']},
        ]})
        self.assertEqual(index.named('Westfall'), 'area:40')
        self.assertEqual(index.named('Westfall', '0'), 'area:40')


if __name__ == '__main__':
    unittest.main()
