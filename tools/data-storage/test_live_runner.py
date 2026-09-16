import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from live_runner import input_fingerprint


class FingerprintTests(unittest.TestCase):
    def test_receipt_and_release_url_do_not_republish_but_payload_and_source_changes_do(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            def git(*args):return subprocess.check_output(['git','-C',str(root),*args],stderr=subprocess.DEVNULL)
            git('init');git('config','user.email','fixture@example.invalid');git('config','user.name','Fixture')
            (root/'datasets').mkdir();(root/'cachedata/contributions').mkdir(parents=True)
            manifest={'schema':'ascension-dataset-1','release':'first','files':{'cachedata/one.tsv':{'bytes':3,'sha256':'a'*64,'parts':[{'url':'first'}]}}}
            path=root/'datasets/cache.json';path.write_text(json.dumps(manifest))
            git('add','.');git('commit','-m','first');first=input_fingerprint(root,'a'*40)
            (root/'cachedata/contributions/receipt.json').write_text('{}');manifest['release']='second';manifest['files']['cachedata/one.tsv']['parts']=[{'url':'second'}];path.write_text(json.dumps(manifest))
            git('add','.');git('commit','-m','receipt');self.assertEqual(first,input_fingerprint(root,'a'*40))
            manifest['files']['cachedata/one.tsv']['sha256']='b'*64;path.write_text(json.dumps(manifest));git('add','.');git('commit','-m','content')
            self.assertNotEqual(first,input_fingerprint(root,'a'*40));self.assertNotEqual(input_fingerprint(root,'a'*40),input_fingerprint(root,'b'*40))


if __name__=='__main__':unittest.main()
