import gzip
import json
import tempfile
import unittest
from pathlib import Path
from atlas_metadata import enrich_atlas


class AtlasMetadataTests(unittest.TestCase):
    def build(self, rows, atlas=None):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            folder = root / 'records' / 'public123'
            folder.mkdir(parents=True)
            with gzip.open(folder / '3.json.gz', 'wt', encoding='utf8') as out:
                json.dump(rows, out)
            manifest = {'files': {'public123': {'path': 'supplemental/exiles-db/snapshot/entities.jsonl.gz'}}}
            return enrich_atlas(root, manifest, atlas or {'zones': []})

    @staticmethod
    def row(kind, identifier, name, refs=(), tables=(), headings=()):
        return [str(identifier), name, kind, 'Unspecified', 'Exiles DB',
                {'name': name, 'references': list(refs), 'tables': list(tables), 'headings': list(headings)}]

    def test_inventory_name_precedence_and_conflicts_have_source_records(self):
        atlas = {'zones': [{'key': 'captured', 'name': 'Captured Area', 'map': '900',
                            'inventory': {'map_id': '900', 'name': 'Preserved Realm'}}]}
        result = self.build([self.row('map', '900', 'Website Realm')], atlas)
        world = result['maps'][0]
        self.assertEqual(world['name'], 'Preserved Realm')
        self.assertEqual(world['alternate_names'], ['Website Realm'])
        self.assertEqual(world['records'], ['public123/3/0'])
        self.assertEqual(result['zones'][0]['map_name'], 'Preserved Realm')
        self.assertEqual(result['zones'][0]['region'], 'Ascension & other worlds')

    def test_zero_map_id_and_large_area_ids_keep_exact_identity_and_hierarchy(self):
        child = '9007199254740993'
        rows = [
            self.row('map', '0', 'Eastern Kingdoms', [{'type': 'area', 'key': '12', 'label': 'Elwynn Forest'}]),
            self.row('area', '12', 'Elwynn Forest', [
                {'type': 'map', 'key': '0', 'label': 'Eastern Kingdoms'},
                {'type': 'area', 'key': child, 'label': 'Custom Glade'}],
                [{'header': ['ID', 'Name'], 'rows': [[child, 'Custom Glade']]}], ['Elwynn Forest', 'Sub-zones']),
            self.row('area', child, 'Custom Glade', [
                {'type': 'map', 'key': '0', 'label': 'Eastern Kingdoms'},
                {'type': 'area', 'key': '12', 'label': 'Elwynn Forest'}],
                [{'header': [], 'rows': [['Parent zone', 'Elwynn Forest']]}]),
        ]
        result = self.build(rows)
        world = result['maps'][0]
        self.assertEqual(world['id'], '0')
        areas = {a['id']: a for a in world['areas']}
        self.assertEqual(areas[child]['parents'], ['12'])
        self.assertEqual(areas['12']['children'], [child])
        self.assertEqual(areas[child]['record'], 'public123/3/2')
        self.assertEqual(len(result['zones']), 1, 'area metadata must not create thousands of zones')
        self.assertEqual(result['zones'][0]['count'], 0)
        self.assertEqual(result['zones'][0]['image'], '')

    def test_unrelated_area_reference_does_not_invent_parent_or_location(self):
        result = self.build([self.row('area', '22', 'Crypt', [{'type': 'area', 'key': '33', 'label': 'Other Place'}])])
        areas = {a['id']: a for a in result['areas']}
        self.assertEqual(areas['22']['parents'], [])
        self.assertEqual(areas['33']['record'], '')
        self.assertEqual(result['maps'], [])
        self.assertEqual(result['zones'], [])

    def test_all_inventory_maps_and_unnamed_areas_remain_accessible(self):
        atlas = {'maps': [{'map_id': '1', 'name': 'Kalimdor'}, {'map_id': '999', 'name': 'Unknown Realm'}], 'zones': []}
        result = self.build([self.row('map', '1', 'Kalimdor', [{'type': 'area', 'key': '4', 'label': '\ufffd'}]), self.row('area', '4', 'coa-db —')], atlas)
        self.assertEqual({m['id'] for m in result['maps']}, {'1', '999'})
        self.assertEqual({z['map'] for z in result['zones']}, {'1', '999'})
        self.assertEqual(result['areas'][0]['name'], 'Area 4')
        self.assertEqual(result['areas'][0]['record'], 'public123/3/1')


if __name__ == '__main__':
    unittest.main()
