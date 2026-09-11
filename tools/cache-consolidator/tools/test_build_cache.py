"""Incremental/full equivalence and stale-output regressions; disposable data only."""
import contextlib
import gzip
import hashlib
import io
import json
import os
from pathlib import Path
import struct
import tempfile
import unittest
from unittest.mock import patch

import build_cache
import config
import export
import export_stock_client as stock
import merge
import rebuild


class IncrementalTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix='incremental-test-')
        self.addCleanup(tmp.cleanup)
        self.work = Path(tmp.name)
        self.store, self.out = self.work/'merged', self.work/'out'
        self.store.mkdir()
        self.out.mkdir()
        for module, values in [(config, dict(WORK=str(self.work), STORE=str(self.store),
             SOURCES=str(self.store/'sources.tsv'), OUT=str(self.out))),
             (merge, dict(STORE=str(self.store), SOURCES=str(self.store/'sources.tsv'))),
             (export, dict(STORE=str(self.store), OUT=str(self.out))),
             (rebuild, dict(OUT=str(self.out/'wdb')))]:
            for key, value in values.items():
                p = patch.object(module, key, value)
                p.start(); self.addCleanup(p.stop)
        p = patch.dict(os.environ, {'ASCENSION_FORCE_REBUILD': '0'})
        p.start(); self.addCleanup(p.stop)
        self.sources = []
        self.add_cache('itemnamecache', b'WNDB', b'Name\0'+struct.pack('<I', 1))
        self.add_cache('pagetextcache', b'WPTX', b'Page\0'+struct.pack('<I', 0))
        self.save_sources()

    def save_sources(self):
        merge.write_tsv(str(self.store/'sources.tsv'), merge.SRC_COLS, self.sources)

    def add_cache(self, cache, magic, payload):
        sid = str(len(self.sources)+1)
        donor = self.work/(cache+'.wdb')
        donor.write_bytes(magic[::-1]+struct.pack('<I',12340)+b'SUne'+b'\0'*12)
        self.sources.append(dict(zip(merge.SRC_COLS,
            [sid, 'a'*64, cache, cache+'.wdb', 'g', 'realm', 'Free-Pick',
             'free-pick', 'folder', '1', '2026-01-01', str(donor)])))
        folder = self.store/cache
        folder.mkdir()
        (folder/'pack.bin').write_bytes(struct.pack('<II', 1, len(payload))+payload)
        row = dict(zip(merge.IDX_COLS, ['1',hashlib.sha1(payload).hexdigest(),str(len(payload)),
                   '0','free-pick',sid,'2026-01-01','2026-01-01']))
        merge.write_tsv(str(folder/'index.tsv'), merge.IDX_COLS, [row])

    def run_all(self, force=False):
        with patch.dict(os.environ, {'ASCENSION_FORCE_REBUILD': '1' if force else '0'}), contextlib.redirect_stdout(io.StringIO()) as log:
            export.main()
            self.assertEqual(rebuild.main(), 0)
        return log.getvalue()

    def snapshot(self):
        return {p.relative_to(self.out).as_posix():p.read_bytes()
                for p in self.out.rglob('*') if p.is_file()}

    def equal_full(self):
        incremental = self.snapshot()
        self.run_all(force=True)
        self.assertEqual(incremental, self.snapshot())

    def test_second_run_reuses_and_preserves_every_file(self):
        self.run_all()
        expected = self.snapshot()
        with patch.object(export,'load_cache',side_effect=AssertionError('unexpected decode')):
            log = self.run_all()
        self.assertEqual(log.count('reused verified'), 4)
        self.assertEqual(expected,self.snapshot())
        self.equal_full()

    def test_changed_category_keeps_other_category(self):
        self.run_all()
        # Provenance alone is a real change, even without a new record.
        p = self.store/'itemnamecache/index.tsv'
        rows = merge.read_tsv(str(p),merge.IDX_COLS)
        rows[0]['last_captured'] = '2026-02-01'
        merge.write_tsv(str(p),merge.IDX_COLS,rows)
        log = self.run_all()
        self.assertEqual(log.count('reused verified'),2)
        self.equal_full()

    def test_source_only_change_and_header_change(self):
        self.run_all()
        self.sources[0]['captured'] = ''
        self.save_sources()
        log = self.run_all()
        self.assertEqual(log.count('reused verified'),2)
        self.equal_full()
        donor = Path(self.sources[0]['path'])
        b = bytearray(donor.read_bytes()); b[16] = 2; donor.write_bytes(b)
        log = self.run_all()
        self.assertEqual(log.count('reused verified'),3)
        self.equal_full()

    def test_missing_corrupt_output_and_manifest_rebuild(self):
        self.run_all()
        (self.out/'union/itemnamecache.tsv.gz').unlink()
        (self.out/'wdb/free-pick/pagetextcache.wdb.gz').write_bytes(b'broken')
        self.assertEqual(self.run_all().count('reused verified'),2)
        self.equal_full()
        cache = build_cache.BuildCache('export',self.out)
        cache.path('itemnamecache').write_text('{broken')
        self.assertEqual(self.run_all().count('reused verified'),3)
        self.equal_full()

    def test_removed_mode_prunes_and_recreates_docs(self):
        self.run_all()
        for row in self.sources: row['slug']='renamed'
        self.save_sources()
        for p in self.store.glob('*/index.tsv'):
            rows=merge.read_tsv(str(p),merge.IDX_COLS)
            for row in rows: row['modes']='renamed'
            merge.write_tsv(str(p),merge.IDX_COLS,rows)
        self.run_all()
        self.assertFalse((self.out/'by-mode/free-pick').exists())
        self.assertFalse((self.out/'wdb/free-pick').exists())
        self.equal_full()

    def test_rules_config_and_force_invalidate(self):
        self.run_all()
        (self.work/'config.json').write_text('{"changed": true}')
        self.assertNotIn('reused verified',self.run_all())
        self.assertNotIn('reused verified',self.run_all(force=True))
        with patch.object(build_cache,'rules',return_value='changed-generator'):
            self.assertNotIn('reused verified',self.run_all())

    def test_missing_header_is_failure_not_cached_success(self):
        self.run_all()
        Path(self.sources[0]['path']).unlink()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(rebuild.main(),1)
            self.assertEqual(rebuild.main(),1)

    def test_partial_generation_cannot_replace_evidence(self):
        self.run_all()
        cache=build_cache.BuildCache('export',self.out)
        before=cache.path('itemnamecache').read_bytes()
        with patch.dict(os.environ, {'ASCENSION_FORCE_REBUILD':'1'}), patch.object(export,'write_view',side_effect=RuntimeError('interrupted')):
            with self.assertRaises(RuntimeError): export.main()
        self.assertEqual(before,cache.path('itemnamecache').read_bytes())
        self.equal_full()

    def test_cross_category_source_context_invalidation(self):
        self.run_all()
        p=self.store/'itemnamecache/index.tsv'
        rows=merge.read_tsv(str(p),merge.IDX_COLS)
        rows[0]['srcs']='1,2'; merge.write_tsv(str(p),merge.IDX_COLS,rows)
        self.run_all()
        self.sources[1]['captured']=''
        self.save_sources()
        self.assertNotIn('reused verified',self.run_all())
        self.equal_full()

    def stock_view(self, cache, text):
        p=self.out/'union'/(cache+'.tsv.gz'); p.parent.mkdir(exist_ok=True)
        export.write_gz(str(p),text.encode())

    def run_stock(self, force=False):
        with patch.dict(os.environ, {'ASCENSION_FORCE_REBUILD':'1' if force else '0'}),contextlib.redirect_stdout(io.StringIO()) as log:
            self.assertEqual(stock.main(['--data',str(self.out)]),0)
        return log.getvalue()

    def stock_equal_full(self):
        before=self.snapshot();self.run_stock(force=True);self.assertEqual(before,self.snapshot())

    def test_conflicting_variant_source_date_changes_selected_value(self):
        folder = self.store/'itemnamecache'
        rows = merge.read_tsv(str(folder/'index.tsv'), merge.IDX_COLS)
        payload = b'New name\0' + struct.pack('<I', 1)
        offset = (folder/'pack.bin').stat().st_size
        with (folder/'pack.bin').open('ab') as f:
            f.write(struct.pack('<II', 1, len(payload))+payload)
        rows.append(dict(rows[0], sha1=hashlib.sha1(payload).hexdigest(),
                         size=str(len(payload)), offset=str(offset), srcs='3',
                         last_captured='2026-02-01'))
        merge.write_tsv(str(folder/'index.tsv'), merge.IDX_COLS, rows)
        self.sources.append(dict(self.sources[0], id='3', captured=''))
        self.save_sources()
        self.run_all()
        target = self.out/'by-mode/free-pick/itemnamecache.tsv.gz'
        with gzip.open(target,'rt') as f: self.assertNotIn('New name', f.read())
        self.sources[-1]['captured'] = '2026-02-01'
        self.save_sources()
        self.run_all()
        with gzip.open(target,'rt') as f: self.assertIn('New name', f.read())
        self.equal_full()

    def test_checksum_consistent_malformed_metadata_misses(self):
        self.run_all()
        cache = build_cache.BuildCache('export',self.out)
        p = cache.path('itemnamecache')
        obj = json.loads(p.read_text()); obj['entry']['metadata'] = None
        obj['sha256'] = build_cache.digest(obj['entry'])
        p.write_text(json.dumps(obj))
        self.assertEqual(self.run_all().count('reused verified'),3)
        self.equal_full()

    def test_donor_changes_between_snapshot_and_signature(self):
        original = rebuild.header_for
        calls = 0
        def changing(cache, slug, sources):
            nonlocal calls
            header, source = original(cache, slug, sources)
            if cache == 'itemnamecache':
                calls += 1
                if calls > 1:
                    header = header[:16] + b'\x02' + header[17:]
            return header, source
        with patch.object(rebuild, 'header_for', side_effect=changing), contextlib.redirect_stdout(io.StringIO()):
            with self.assertRaisesRegex(RuntimeError, 'header donors changed'):
                rebuild.main()
        self.assertFalse(build_cache.BuildCache('rebuild', self.out/'wdb').path('itemnamecache').exists())

    def test_missing_stock_input_is_not_hidden_by_cache(self):
        for cache in ('itemcache', 'creaturecache', 'questcache'):
            self.stock_view(cache, 'entry\tname\t_captured\n1\tName\t\n')
        self.run_stock()
        (self.out/'union/itemcache.tsv.gz').unlink()
        with self.assertRaises(SystemExit): self.run_stock()

    def test_stock_hits_icons_fallback_and_pruning(self):
        self.stock_view('itemcache','entry\tname\tdisplayid\t_captured\n1\tSword\t1\t2026-01-01\n2\tAxe\t2\t2026-01-01\n')
        self.stock_view('creaturecache','entry\tname\t_captured\n1\tWolf\t\n')
        self.stock_view('questcache','entry\tTitle\t_captured\n1\tQuest\t\n')
        with patch.object(stock,'CHUNK_BYTES',180):
            self.run_stock()
            with patch.object(stock,'merged_view',side_effect=AssertionError('unexpected TSV decode')):
                self.assertEqual(self.run_stock().count('reused verified'),3)
            self.stock_equal_full()
            # Shrinking chunks must remove old files, including on subsequent hits.
            self.stock_view('itemcache','entry\tname\tdisplayid\t_captured\n1\tSword\t1\t2026-02-01\n')
            self.assertNotIn('reused verified',self.run_stock())
            self.stock_equal_full()
            (self.out/'lua/stock-client/items-999.lua').write_text('stale')
            self.run_stock()
            self.assertFalse((self.out/'lua/stock-client/items-999.lua').exists())
            export.write_gz(str(self.out/stock.ICON_TSV),b'displayid\ticon\tstock_displayid\n1\tINV_Sword\t1\n')
            self.assertNotIn('reused verified',self.run_stock())
            self.stock_equal_full()


if __name__ == '__main__':
    unittest.main()
