import csv
import gzip
import json
import tempfile
import unittest
from pathlib import Path

from make_npc_zone_claims import claims, write


class NpcZoneClaimTests(unittest.TestCase):
    def test_direct_and_reviewed_claims_are_deterministic_and_distinct(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            spawns = root / 'creature_spawn.csv.gz'
            with gzip.open(spawns, 'wt', encoding='utf-8', newline='') as stream:
                writer = csv.writer(stream)
                writer.writerow(['guid', 'entry', 'area_id'])
                writer.writerows([['1', '94', '10138'], ['2', '94', '10138'], ['3', '95', '']])
            reviewed = root / 'reviewed.json'
            reviewed.write_text(json.dumps([{
                'npc_id': '161700', 'area_id': '10138',
                'evidence': 'reviewed-report', 'source': 'fixture',
            }]), encoding='utf-8')
            rows = claims(spawns, reviewed)
            self.assertEqual(rows, [
                (94, 10138, 'direct-spawn-area', 'exiles-db-export-2026-09-13'),
                (161700, 10138, 'reviewed-report', 'fixture'),
            ])
            first, second = root / 'first.tsv.gz', root / 'second.tsv.gz'
            write(first, rows)
            write(second, rows)
            self.assertEqual(first.read_bytes(), second.read_bytes())


if __name__ == '__main__':
    unittest.main()
