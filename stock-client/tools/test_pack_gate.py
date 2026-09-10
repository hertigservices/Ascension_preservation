#!/usr/bin/env python3
"""Adversarial fixtures for the compiled pack gate."""
import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from luacheck_pack import check


class PackGateTests(unittest.TestCase):
    def run_pack(self,code,toc='Main.lua\n',tamper=False):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory)
            files={'Demo.toc':toc,'Main.lua':code}
            for name,text in files.items():(root/name).write_bytes(text.encode('utf-8'))
            (root/'PACK-MANIFEST.json').write_text(json.dumps({'scope':'test fixture','files':{name:hashlib.sha256(text.encode()).hexdigest() for name,text in files.items()}}),encoding='utf-8')
            (root/'contracts.json').write_text('{"format":1}',encoding='utf-8')
            if tamper:(root/'Main.lua').write_text(code+'\n-- changed',encoding='utf-8')
            return check(root,root/'contracts.json')
    def test_valid_pack_is_compile_only(self):
        result=self.run_pack('local s="FakeGlobal()" -- Bogus()\n local function neverRun() while true do end end')
        self.assertTrue(result['passed'],result['errors'])
    def test_host_only_libraries_not_whitelisted(self):
        for name in ('python','io','os','package','require'):
            result=self.run_pack('return '+name)
            self.assertFalse(result['passed']);self.assertIn(name,result['unresolvedGlobals'])
    def test_integrity_failure(self):
        result=self.run_pack('local a=1',tamper=True)
        self.assertTrue(any('hash mismatch' in error for error in result['errors']))
    def test_missing_and_escaping_include(self):
        for toc,expected in (('Missing.lua\n','missing include'),('../Outside.lua\n','escapes pack')):
            result=self.run_pack('',toc)
            self.assertTrue(any(expected in error for error in result['errors']))
    def test_syntax_error(self):
        result=self.run_pack('local = broken')
        self.assertFalse(result['passed'])


if __name__=='__main__':unittest.main()
