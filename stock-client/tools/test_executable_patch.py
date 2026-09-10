#!/usr/bin/env python3
"""Verify the portable startup patcher without launching or altering a client.

With --stock, validates the exact real stock input and expected patched output.
Filesystem alias tests use disposable synthetic files only.
"""
import argparse
import hashlib
import importlib.util
import os
from pathlib import Path
import struct
import tempfile
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('portable_patch_wow',ROOT/'client/patch_wow.py')
patch=importlib.util.module_from_spec(spec);spec.loader.exec_module(patch)
EXPECTED='edc571952f1bc833a9dd262a9266803324420e57c06a4392b98ba56201860e89'


class OutputSafetyTests(unittest.TestCase):
    def test_invalid_sources_are_refused(self):
        for raw in (b'',b'MZ'+b'\0'*100,b'\0'*patch.STOCK_SIZE):
            with self.assertRaises(ValueError):patch.patch_bytes(raw)
    def test_source_and_source_hardlink_refused(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';alias=root/'alias'
            source.write_bytes(b'original');os.link(source,alias)
            for target in (source,alias):
                with self.assertRaises(ValueError):patch.write_output(source,target,b'changed')
            self.assertEqual(source.read_bytes(),b'original')
            self.assertEqual(alias.read_bytes(),b'original')
    def test_replacing_destination_hardlink_preserves_other_file(self):
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory);source=root/'source';other=root/'other';output=root/'output'
            source.write_bytes(b'source');other.write_bytes(b'other');os.link(other,output)
            patch.write_output(source,output,b'new output')
            self.assertEqual(source.read_bytes(),b'source')
            self.assertEqual(other.read_bytes(),b'other')
            self.assertEqual(output.read_bytes(),b'new output')
            self.assertFalse(os.path.samefile(output,other))
            self.assertEqual(sorted(p.name for p in root.iterdir()),['other','output','source'])


def verify_stock(path):
    source=path.read_bytes()
    before=hashlib.sha256(source).hexdigest()
    output=patch.patch_bytes(source)
    assert hashlib.sha256(output).hexdigest()==EXPECTED,'output differs from the startup-tested build'
    assert patch.patch_bytes(source,signature=False,laa=False,archive_capacity=False)==source
    pe,sections=patch.sections(source)
    allowed={pe+22,pe+23}
    for va,size,target in ((0x4DA7E5,12,0x4DA835),(0x52ABD9,12,0x52AC1C)):
        offset=patch.va2off(sections,va)
        assert output[offset]==0xE9
        assert va+5+struct.unpack_from('<i',output,offset+1)[0]==target
        assert output[offset+5:offset+size]==b'\x90'*(size-5)
        allowed.update(range(offset,offset+size))
    for va,size in ((0x405E2E,5),(0x405E84,12)):
        offset=patch.va2off(sections,va);allowed.update(range(offset,offset+size))
    differences={n for n,(left,right) in enumerate(zip(source,output)) if left!=right}
    assert len(source)==len(output) and differences<=allowed
    reserve=patch.va2off(sections,0x405E2E);branch=patch.va2off(sections,0x405E84)
    assert struct.unpack_from('<I',output,reserve+1)[0]==128
    assert struct.unpack_from('<I',output,branch+1)[0]==64
    assert output[branch+5]==0x76 and 0x405E8B+output[branch+6]==0x405EDA
    assert output[branch+7]==0xEB and 0x405E8D+output[branch+8]==0x405E90
    for raw in (source[:-1],source[:100]+bytes([source[100]^1])+source[101:],output):
        try:patch.patch_bytes(raw)
        except ValueError:pass
        else:raise AssertionError('unknown or already-patched executable accepted')
    assert hashlib.sha256(path.read_bytes()).hexdigest()==before==patch.STOCK_SHA256
    print('PASS: exact stock input unchanged; output matches startup-tested SHA256 '+EXPECTED+'; dispatch targets, capacity/priority and change boundaries verified. No executable was written or launched.')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--stock',type=Path)
    args=parser.parse_args()
    if not unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(OutputSafetyTests)).wasSuccessful():return 1
    if args.stock:verify_stock(args.stock)
    else:print('Real executable check not run; supply --stock with the known stock 12340 file.')
    return 0


if __name__=='__main__':raise SystemExit(main())
