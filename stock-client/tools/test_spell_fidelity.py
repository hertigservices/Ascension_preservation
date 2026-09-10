#!/usr/bin/env python3
"""Focused safety/interpretation checks for the spell fidelity audit."""
import json
import tempfile
from pathlib import Path
import struct
import unittest
from audit_spell_fidelity import journal_changes,read_current_dbc,audit_entries


class FidelityTests(unittest.TestCase):
    def journal(self):
        return {'rules':{'Spell.dbc:TOTAL_SPELL_EFFECTS':{'action':'zero','limit':165,'entries':[[42,71,167]]},'Spell.dbc:TOTAL_AURAS':{'action':'zero','limit':317,'entries':[[43,95,345]]}}}
    def test_changed_field_not_wholly_inert(self):
        changes=journal_changes(json.dumps(self.journal()).encode())
        entries=[{'ID':1,'Name':'Example','Class':'Tinker','Tab':'Firearms','Spells':[42,43]}]
        current={42:{71:0,72:2,73:0,95:0,96:0,97:0},43:{71:6,72:0,73:0,95:0,96:0,97:0}}
        report=audit_entries(entries,changes,current)
        self.assertEqual(report['affectedEntryCount'],1)
        self.assertEqual(report['affectedSpellCount'],2)
        self.assertFalse(any(s['noNonzeroEffectSlots'] for s in report['affectedEntries'][0]['spells']))
        current[42][72]=0
        self.assertEqual(audit_entries(entries,changes,current)['perClass'][0]['entriesWithNoNonzeroEffectSpell'],1)
    def test_invalid_or_duplicate_journal_field(self):
        for entry in ([42,1,999],[42,71,1],[True,71,167]):
            journal=self.journal();journal['rules']['Spell.dbc:TOTAL_SPELL_EFFECTS']['entries']=[entry]
            with self.assertRaises(ValueError):journal_changes(json.dumps(journal).encode())
        journal=self.journal();journal['rules']['Spell.dbc:TOTAL_SPELL_EFFECTS']['entries']*=2
        with self.assertRaises(ValueError):journal_changes(json.dumps(journal).encode())
    def test_current_dbc_bounds_and_requested_rows(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'Spell.dbc'
            row=[0]*98;row[0]=42;row[71]=2
            raw=struct.pack('<4s4I',b'WDBC',1,98,392,1)+struct.pack('<98I',*row)+b'\0'
            path.write_bytes(raw)
            result,metadata=read_current_dbc(path,{42,43})
            self.assertEqual(result[42][71],2);self.assertNotIn(43,result)
            self.assertEqual(metadata['records'],1)
            path.write_bytes(raw[:-1])
            with self.assertRaises(ValueError):read_current_dbc(path,{42})


if __name__=='__main__':unittest.main()
