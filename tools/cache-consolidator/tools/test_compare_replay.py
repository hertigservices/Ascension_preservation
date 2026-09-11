import unittest, tempfile
from unittest.mock import patch
from pathlib import Path
import compare_replay

class CompareReplayTests(unittest.TestCase):
    def test_missing_singleton_and_missing_file_fail_direct_baseline(self):
        baseline={'files':{'a.json':{'sha256':'a'},'b.lua':{'sha256':'b'}},'counts':{'a.json:x':1},'unreadable':[]}
        candidate={'files':{'a.json':{'sha256':'c'}},'counts':{},'unreadable':[]}
        result=compare_replay.compare(baseline,candidate)
        self.assertFalse(result['no_coverage_loss'])
        self.assertEqual(result['missing_artifacts'],['b.lua'])
        self.assertIn('a.json:x',result['decreased_counts'])
        self.assertEqual(result['changed_artifacts_requiring_review'],['a.json'])

    def test_inventory_reads_real_artifacts_and_reports_bad_encoding(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);(p/'good.lua').write_text('DATA={x=1,y=2}',encoding='utf-8')
            (p/'good.json').write_text('{"values":{"a":1}}',encoding='utf-8')
            (p/'bad.json').write_bytes(b'{"x":"'+bytes([255])+b'"}')
            (p/'addons').mkdir();(p/'addons/code.lua').write_text('function f() return 1 end')
            result=compare_replay.inventory(p)
            self.assertEqual(result['counts']['good.lua:DATA:leaves'],2)
            self.assertEqual([v[1] for v in result['unreadable']],['bad.json'])
            self.assertIn('addons/code.lua',result['files'])

    def test_malformed_inputs_and_read_errors_are_reported(self):
        for filename,data in [('bad.json',b'{'),('bad.lua',b'DATA={'),('bad.json.gz',b'bad gzip')]:
            with tempfile.TemporaryDirectory() as d:
                (Path(d)/filename).write_bytes(data)
                self.assertTrue(compare_replay.inventory(d)['unreadable'])
        with tempfile.TemporaryDirectory() as d:
            (Path(d)/'gone.json').write_text('{}')
            with patch.object(Path,'read_bytes',side_effect=PermissionError('denied')):
                result=compare_replay.inventory(d)
            self.assertTrue(result['unreadable'])
            self.assertIn('gone.json',result['files'])

    def test_equal_counts_changed_values_require_review(self):
        old={'files':{'a':{'sha256':'a'}},'counts':{'x':1},'unreadable':[]}
        new={'files':{'a':{'sha256':'b'}},'counts':{'x':1},'unreadable':[]}
        result=compare_replay.compare(old,new)
        self.assertTrue(result['no_coverage_loss'])
        self.assertTrue(result['review_required'])

    def test_addition_alone_requires_review(self):
        old={'files':{},'counts':{},'unreadable':[]}
        new={'files':{'added':{'sha256':'x'}},'counts':{},'unreadable':[]}
        self.assertTrue(compare_replay.compare(old,new)['review_required'])

    def test_growth_passes_coverage_but_cannot_authorize_publication(self):
        old={'files':{'a':{'sha256':'a'}},'counts':{'x':1},'unreadable':[]}
        new={'files':{'a':{'sha256':'b'}},'counts':{'x':2},'unreadable':[]}
        result=compare_replay.compare(old,new)
        self.assertTrue(result['no_coverage_loss'])
        self.assertFalse(result['publication_allowed'])
        self.assertTrue(result['review_required'])
        self.assertEqual(result['changed_artifacts_requiring_review'],['a'])

if __name__=='__main__': unittest.main()
