"""Archive normalization must preserve extracted source identities."""
import hashlib
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch,Mock
import intake
import tray_app

class ArchiveNamingTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory(prefix='archive-names-');self.addCleanup(t.cleanup)
        self.root=Path(t.name);self.inbox=self.root/'inbox';self.extracted=self.root/'extracted'
        self.inbox.mkdir();self.extracted.mkdir()
        for obj,name,value in [(intake,'EXTRACT',str(self.extracted)),(intake,'SCAN_ROOTS',[str(self.inbox)]),(tray_app.config,'INBOX',str(self.inbox)),(tray_app,'DONE_DIR',str(self.inbox/'archive'))]:
            p=patch.object(obj,name,value);p.start();self.addCleanup(p.stop)
    def mark(self,folder,digest,source='WDB.rar'):
        p=self.extracted/folder;p.mkdir();(p/intake.SRCHASH).write_text(digest)
        (p/intake.SRCMARK).write_text(source);(p/'gameobjectcache.wdb').write_bytes(b'original payload')
        return p
    def test_startup_archive_renames_but_reuses_original_extraction(self):
        a=self.inbox/'WDB.rar';a.write_bytes(b'archive')
        sha=intake.sha256(a);old=self.mark('original',sha)
        app=SimpleNamespace(state={'file':True},consumed=set(),log=Mock())
        with patch('sweep_inbox.fix_collisions',return_value=[]):
            tray_app.App.prepare_inbox(app,{'WDB.rar'})
        name='WDB__'+sha[:8]+'.rar';self.assertTrue((self.inbox/name).exists())
        before={p.relative_to(self.extracted):p.read_bytes() for p in self.extracted.rglob('*') if p.is_file()}
        with patch.object(intake.subprocess,'run',side_effect=AssertionError('must not extract twice')):
            self.assertIn((name,'cached (same archive content)'),intake.extract_all())
        self.assertEqual(before,{p.relative_to(self.extracted):p.read_bytes() for p in self.extracted.rglob('*') if p.is_file()})
    def test_reused_startup_filename_gets_new_hash(self):
        a=self.inbox/'WDB(1).zip';a.write_bytes(b'old');oldhash=intake.sha256(a)
        self.mark('old',oldhash,source=a.name);a.write_bytes(b'new')
        app=SimpleNamespace(state={'file':True},consumed=set(),log=Mock())
        with patch('sweep_inbox.fix_collisions',return_value=[]):tray_app.App.prepare_inbox(app,{'WDB(1).zip'})
        self.assertTrue((self.inbox/('WDB(1)__'+hashlib.sha256(b'new').hexdigest()[:8]+'.zip')).exists())
    def test_same_short_hash_and_size_does_not_overwrite_other_content(self):
        a=self.inbox/'WDB.rar';a.write_bytes(b'aaaa')
        sha=intake.sha256(a);dest=self.inbox/('WDB__'+sha[:8]+'.rar');dest.write_bytes(b'bbbb')
        new=tray_app.file_arrival(a.name,Mock())
        self.assertEqual(dest.read_bytes(),b'bbbb');self.assertEqual((self.inbox/new).read_bytes(),b'aaaa')
        self.assertEqual(new,'WDB__'+sha[:16]+'.rar')
    def test_legacy_extraction_without_hash_keeps_archive_identity(self):
        (self.inbox/'WDB.rar').write_bytes(b'archive')
        folder=self.extracted/'WDB';folder.mkdir();(folder/'data.wdb').write_bytes(b'old')
        self.assertIsNone(tray_app.file_arrival('WDB.rar',Mock()))
        self.assertTrue((self.inbox/'WDB.rar').exists())
        (folder/intake.SRCHASH).write_text('malformed')
        self.assertIsNone(tray_app.file_arrival('WDB.rar',Mock()))
        self.assertTrue((self.inbox/'WDB.rar').exists())
    def test_tag_alone_cannot_skip_new_archive(self):
        a=self.inbox/'WDB__deadbeef.zip';a.write_bytes(b'new')
        self.mark('old','a'*64)
        def extract(command,**kwargs):
            dest=Path(next(x[2:] for x in command if x.startswith('-o')))
            (dest/'data.wdb').write_bytes(b'new payload')
            return SimpleNamespace(returncode=0)
        with patch.object(intake.subprocess,'run',side_effect=extract) as run:
            self.assertIn((a.name,'ok'),intake.extract_all());self.assertEqual(run.call_count,1)
    def test_markers_without_payload_do_not_prove_completed_extraction(self):
        p=self.extracted/'empty';p.mkdir();(p/intake.SRCHASH).write_text('a'*64)
        (p/intake.SRCMARK).write_text('empty.zip');self.assertEqual(intake.extraction_hashes(),{})
    def test_loose_lua_name_and_rename_disabled_preserved(self):
        p=self.inbox/'AIO_Client.lua';p.write_bytes(b'lua')
        self.assertIsNone(tray_app.file_arrival(p.name,Mock()))
        a=self.inbox/'WDB.zip';a.write_bytes(b'archive')
        tray_app.App.prepare_inbox(SimpleNamespace(state={'file':False},consumed=set(),log=Mock()),set())
        self.assertTrue(a.exists());self.assertTrue(p.exists())

if __name__=='__main__':unittest.main()
