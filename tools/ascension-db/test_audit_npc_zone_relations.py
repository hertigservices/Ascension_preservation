import csv
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from audit_npc_zone_relations import audit, write


class NpcZoneRelationAuditTests(unittest.TestCase):
    def fixture(self, root):
        def gz(name, text):
            path = root / name
            with gzip.open(path, 'wt', encoding='utf-8', newline='') as stream:
                stream.write(text)
            return path
        npcs = gz('npcs.jsonl.gz', ''.join(json.dumps(row) + '\n' for row in [
            {'id': '161705', 'name': 'Injured Northshire Guard', 'structure': {'references': []}},
            {'id': '161713', 'name': 'Wayward Theologian', 'structure': {'references': []}},
            {'id': '161904', 'name': 'Wayward Theologian', 'structure': {'references': []}},
            {'id': '99', 'name': 'Remote Target', 'structure': {'references': [{'type': 'area', 'key': '40'}]}},
        ]))
        quests = gz('questcache.tsv.gz', '\t'.join([
            'entry', 'ZoneOrSort', 'Title', 'Objectives', 'Details', 'EndText', 'CompletedText',
            'RequiredNpcOrGo1', 'RequiredNpcOrGo2', 'RequiredNpcOrGo3', 'RequiredNpcOrGo4',
            'ObjectiveText1', 'ObjectiveText2', 'ObjectiveText3', 'ObjectiveText4', '_modes', '_locales']) + '\n' +
            '\t'.join(['1660004', '9', 'Words', '', '', '', 'Return to Moroi.', '0', '0', '161713', '0', '', '', 'Wayward Theologian confronted', '', 'CoA', 'enUS']) + '\n' +
            '\t'.join(['1660005', '9', 'Threat', '', 'The wounded soldier asks for help.', '', 'Return to the injured guard.', '0', '0', '0', '0', '', '', '', '', 'CoA', 'enUS']) + '\n' +
            '\t'.join(['7', '12', 'Delivery', '', '', '', '', '99', '0', '0', '0', 'Remote Target visited', '', '', '', 'CoA', 'enUS']) + '\n')
        areas = gz('area.csv.gz', 'id,name\n9,Northshire Valley\n12,Elwynn Forest\n40,Westfall\n')
        spawns = gz('spawns.csv.gz', 'guid,entry,area_id\n')
        reviewed = root / 'reviewed.json'
        reviewed.write_text(json.dumps([
            {'npc_id': '161705', 'area_id': '9'}, {'npc_id': '161713', 'area_id': '9'}
        ]), encoding='utf-8')
        return quests, npcs, areas, spawns, reviewed

    def test_exact_ids_text_inference_duplicates_and_conflicts(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = audit(*self.fixture(root))
            keyed = {(row['npc_id'], row['quest_id'], row['relation']): row for row in rows}
            self.assertEqual(keyed[(161713, 1660004, 'objective-target')]['decision'], 'accepted-reviewed')
            self.assertEqual(keyed[(161705, 1660005, 'quest-text-zone-name')]['decision'], 'accepted-reviewed')
            self.assertNotIn((161904, 1660004, 'objective-target'), keyed)
            self.assertEqual(keyed[(99, 7, 'objective-target')]['decision'], 'review-required-conflict')

    def test_audit_output_is_deterministic(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            rows = audit(*self.fixture(root))
            first, second = root / 'first.tsv.gz', root / 'second.tsv.gz'
            write(first, rows)
            write(second, rows)
            self.assertEqual(first.read_bytes(), second.read_bytes())
            with gzip.open(first, 'rt', encoding='utf-8') as stream:
                self.assertEqual(len(list(csv.DictReader(stream, delimiter='\t'))), len(rows))


if __name__ == '__main__':
    unittest.main()
