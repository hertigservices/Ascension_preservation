#!/usr/bin/env python3
"""Regression tests for compile-only UI dependency discovery."""
import tempfile
from pathlib import Path
import unittest
from analyze_ui_dependencies import Compiler, ChunkReader, scan_tree, index_records, closure


class DependencyTests(unittest.TestCase):
    def names(self, source, kind='reads'):
        return {r['name'] for r in Compiler().scan(source)[kind]}

    def test_lexical_scope_and_nested_functions(self):
        source = '''local Fake=1; local text="Bogus()" -- CommentCall()
        function Provider(argument)
            local LocalCall=function() return argument end
            return function() return ActualGlobal(LocalCall()), Fake end
        end'''
        self.assertEqual(self.names(source), {'ActualGlobal'})
        self.assertEqual(self.names(source, 'writes'), {'Provider'})

    def test_members_and_local_shadowing(self):
        source = 'C_Native.Foo(); local C_Native={}; C_Native.Fake(); return RealTable.Child.Value'
        self.assertEqual(self.names(source, 'members'), {'C_Native.Foo', 'RealTable.Child', 'RealTable.Child.Value'})
        self.assertNotIn('Fake', self.names(source))

    def test_compiles_but_never_executes(self):
        self.assertEqual(self.names('error("must not run"); while true do RealCall() end'), {'error', 'RealCall'})

    def test_invalid_lua_and_chunks(self):
        with self.assertRaises(ValueError): Compiler().scan('local = nope')
        with self.assertRaises(ValueError): ChunkReader(b'not lua').scan()
        compiler = Compiler()
        chunk = compiler.dump(b'return Global')
        with self.assertRaises(ValueError): ChunkReader(chunk[:-1]).scan()
        with self.assertRaises(ValueError): ChunkReader(chunk + b'x').scan()

    def test_extended_setlist_data_not_opcode(self):
        # More than 511 SETLIST blocks forces its following raw-data word.
        source = 'local t={' + ','.join('1' for _ in range(26000)) + '}; return Genuine'
        self.assertEqual(self.names(source), {'Genuine'})

    def test_xml_and_dependency_closure(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'AddOns/Demo').mkdir(parents=True)
            (root/'SharedXML').mkdir()
            (root/'AddOns/Demo/Demo.toc').write_text('Demo.xml\n',encoding='utf-8')
            (root/'AddOns/Demo/Demo.xml').write_text('''<Ui><Frame name="Panel" inherits="BaseTemplate"><Scripts><OnLoad>Helper(self); C_Native.Foo()</OnLoad></Scripts></Frame></Ui>''',encoding='utf-8')
            (root/'SharedXML/Base.xml').write_text('<Ui><Frame name="BaseTemplate" virtual="true"/></Ui>',encoding='utf-8')
            (root/'SharedXML/Helper.lua').write_text('function Helper(self) return StockHelper(self) end',encoding='utf-8')
            records = scan_tree(root)
            result = closure(records,['AddOns/Demo'],index_records([{'file':'stock.lua','writes':[{'name':'StockHelper'}],'frames':[],'templates':[],'reads':[],'members':[]}]))
            self.assertEqual(set(result['files']), {'AddOns/Demo/Demo.toc','AddOns/Demo/Demo.xml','SharedXML/Base.xml','SharedXML/Helper.lua'})
            self.assertEqual(set(result['unresolved']), {'global:C_Native'})
            self.assertEqual(set(result['stockDefined']), {'global:StockHelper'})
            self.assertFalse(any(r['errors'] for r in records))


if __name__ == '__main__': unittest.main()
