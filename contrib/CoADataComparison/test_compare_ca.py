"""Synthetic-only CLI contract tests; no corpus or third-party imports."""
import hashlib
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

CLI = Path(__file__).resolve().with_name('compare_ca.py')


class ComparisonTests(unittest.TestCase):
    def setUp(self):
        # Retained temporary fixtures: the workspace forbids unapproved deletion.
        self.base = Path(tempfile.mkdtemp(prefix='comparison synthetic spaces '))
        self.ca = self.base / 'ca input'
        self.hub = self.base / 'hub input'
        self.ca.mkdir()
        self.hub.mkdir()
        self.out = self.base / 'report.json'
        self.entries = [dict(ID=i, SpellID=s, SpellID2=0, SpellID3=0,
                             SpellID4=0, SpellID5=0, ClassTypeID=c, TabTypeID=1,
                             Description='', Description2='', Name='PRIVATE SENTINEL')
                        for i, s, c in [(1, 10, 1), (2, 20, 1), (3, 30, 2),
                                        (4, 40, 1), (5, 40, 1)]]
        self.entries[0]['SpellID2'] = 11
        self.nodes = [dict(id='1-1-0-0', spellId=10, dependencies=['1-1-1-0', '1-1-2-0', '1-1-3-0', '1-1-9-0']),
                      dict(id='1-1-1-0', spellId=20, dependencies=['1-1-0-0']),
                      dict(id='1-1-2-0', spellId=30), dict(id='1-1-3-0', spellId=40),
                      dict(id='1-1-4-0a', spellId=11)]
        self.put(self.ca / 'entries.json', self.entries)
        self.put(self.ca / 'edges.json', {'1': [2]})
        self.put(self.hub / 'classes.json', [dict(id=1, slug='synthetic')])
        self.put(self.hub / 'skills/synthetic.json', dict(classId=1, count=1, skills=[dict(id=10)]))
        self.put(self.hub / 'talents/synthetic.json', dict(nodeCount=5, trees=[dict(tabId=1, nodeCount=5, nodes=self.nodes)]))
        self.put(self.hub / 'spells/master_index.json', [dict(id=i, description='PRIVATE SENTINEL') for i in [10, 11, 20, 99]])

    def put(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(data), encoding='utf-8')

    def run_cli(self, extra=()):
        return subprocess.run([sys.executable, str(CLI), '--ca-root', str(self.ca),
                               '--hub-root', str(self.hub), '--upstream-revision', 'a' * 40,
                               '--output', str(self.out), *extra], cwd=self.base,
                              capture_output=True, text=True)

    def test_numeric_comparison_privacy_and_determinism(self):
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        raw = self.out.read_bytes()
        report = json.loads(raw)
        self.assertEqual(report['summary']['master_ids'], 4)
        self.assertEqual(report['summary']['master_any_rank_overlap'], 3)
        self.assertEqual(report['summary']['master_absent_from_ca'], 1)
        self.assertEqual(report['summary']['description_enrichment_rows'], 2)
        self.assertEqual(report['master_absent_ids'], [99])
        self.assertEqual(report['description_candidates'], [{'ca_id': 1, 'spell_id': 10}, {'ca_id': 2, 'spell_id': 20}])
        self.assertEqual(report['summary']['community_edges'], dict(present_runtime_edge=1,
                         candidate_not_runtime_edge=2, candidates_same_ca_class_tab=1,
                         unmapped_or_ambiguous=1, dangling_community_node=1))
        self.assertEqual(report['upstream_revision'], {'value': 'a' * 40, 'status': 'asserted_not_verified'})
        self.assertEqual(len(report['edge_hypotheses']), 5)
        self.assertEqual(report['sources'][0]['sha256'], hashlib.sha256((self.ca / 'edges.json').read_bytes()).hexdigest())
        self.assertNotIn('PRIVATE SENTINEL', raw.decode() + result.stdout + result.stderr)
        self.assertNotIn(str(self.base), raw.decode() + result.stdout + result.stderr)
        self.out = self.base / 'second.json'
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(raw, self.out.read_bytes())

    def assert_rejected(self, extra=()):
        result = self.run_cli(extra)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.out.exists())
        self.assertNotIn('PRIVATE SENTINEL', result.stdout + result.stderr)
        self.assertNotIn(str(self.base), result.stdout + result.stderr)
        self.assertNotIn('Traceback', result.stderr)

    def test_reject_invalid_ids_schema_and_counts(self):
        cases = [
            ('entries.json', [dict(self.entries[0], ID=True)]),
            ('entries.json', [dict(self.entries[0], ID='1')]),
            ('entries.json', [dict(self.entries[0], SpellID=1.0)]),
            ('entries.json', [dict(self.entries[0], SpellID=-1)]),
            ('entries.json', self.entries + [self.entries[0]]),
            ('entries.json', {}),
            ('entries.json', [dict(ID=1)]),
            ('edges.json', {'01': [2]}),
            ('edges.json', {'1': ['2']}),
            ('edges.json', {'1': [True]}),
            ('edges.json', {'1': [2, 2]}),
            ('classes.json', [dict(id=1, slug='../PRIVATE SENTINEL')]),
            ('classes.json', [dict(id=1, slug='C:\\PRIVATE SENTINEL')]),
            ('classes.json', [dict(id=1, slug='synthetic'), dict(id=1, slug='synthetic')]),
            ('skills/synthetic.json', dict(classId=1, count=2, skills=[dict(id=10)])),
            ('skills/synthetic.json', dict(classId=1, count=2, skills=[dict(id=10), dict(id=10)])),
            ('talents/synthetic.json', dict(nodeCount=9, trees=[])),
            ('talents/synthetic.json', dict(nodeCount=1, trees=[dict(tabId=1, nodeCount=2, nodes=[self.nodes[0]])])),
            ('talents/synthetic.json', dict(nodeCount=2, trees=[dict(tabId=1, nodeCount=2, nodes=[self.nodes[0], self.nodes[0]])])),
            ('talents/synthetic.json', dict(nodeCount=1, trees=[dict(tabId=1, nodeCount=1, nodes=[dict(id='1-1-0-0A', spellId=10)])])),
            ('talents/synthetic.json', dict(nodeCount=1, trees=[dict(tabId=1, nodeCount=1, nodes=[dict(id='1-1-0-0aa', spellId=10)])])),
            ('spells/master_index.json', [dict(id=1), dict(id=1)]),
            ('spells/master_index.json', [dict(id=False)]),
            ('spells/master_index.json', [dict(id=1, description=42)]),
        ]
        for relative, data in cases:
            with self.subTest(relative=relative, case=cases.index((relative, data))):
                self.setUp()
                root = self.ca if relative in ('entries.json', 'edges.json') else self.hub
                self.put(root / relative, data)
                self.assert_rejected()

    def test_duplicate_json_keys_nonfinite_and_missing_files(self):
        for raw in ['[{"id":1,"id":2}]', '[{"id":1,"description":NaN}]', '{', '[' * 1100 + ']' * 1100]:
            with self.subTest(raw=raw):
                self.setUp()
                (self.hub / 'spells/master_index.json').write_text(raw, encoding='utf-8')
                self.assert_rejected()
        self.setUp()
        (self.hub / 'spells/master_index.json').rename(self.hub / 'spells/retained.json')
        self.assert_rejected()

    def test_revision_and_argument_privacy(self):
        for revision in ['a' * 39, 'g' * 40, 'PRIVATE SENTINEL']:
            with self.subTest(revision=revision):
                self.assert_rejected(['--upstream-revision', revision])
        self.assert_rejected(['--PRIVATE SENTINEL', str(self.base)])

    def test_runtime_empty_source_dangling_is_preserved(self):
        self.put(self.ca / 'edges.json', {'1': [99], '98': []})
        result = self.run_cli()
        self.assertEqual(result.returncode, 0, result.stderr)
        report = json.loads(self.out.read_text())
        self.assertEqual(report['runtime_dangling_source_ids'], [98])
        self.assertEqual(report['runtime_dangling_target_ids'], [99])

    def test_input_bytes_unchanged_and_unlisted_files_ignored(self):
        self.put(self.hub / 'unlisted.json', {'private': 'PRIVATE SENTINEL'})
        before = {str(p): p.read_bytes() for root in (self.ca, self.hub) for p in root.rglob('*.json')}
        self.assertEqual(self.run_cli().returncode, 0)
        self.assertEqual(before, {p: Path(p).read_bytes() for p in before})
        report = json.loads(self.out.read_text())
        ambiguous = [e for e in report['edge_hypotheses'] if e['status'] == 'unmapped_or_ambiguous']
        self.assertEqual(ambiguous[0]['target_ca_ids'], [4, 5])
        cross = [e for e in report['edge_hypotheses'] if e['status'] == 'candidate_not_runtime_edge' and not e['same_ca_class_tab']]
        self.assertEqual(cross[0]['target_ca_ids'], [3])
        self.assertEqual(len(report['sources']), 6)

    def test_output_symlink_is_not_a_new_file(self):
        target = self.base / 'absent-target.json'
        try:
            self.out.symlink_to(target)
        except OSError:
            self.skipTest('symlinks unavailable for this test account')
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(target.exists())
        self.assertTrue(self.out.is_symlink())

    def test_output_cannot_overwrite_or_enter_input_roots(self):
        self.out.write_text('preserve', encoding='utf-8')
        result = self.run_cli()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(self.out.read_text(), 'preserve')
        for root in [self.ca, self.hub]:
            self.out = root / 'new-output.json'
            self.assert_rejected()


if __name__ == '__main__':
    unittest.main()
