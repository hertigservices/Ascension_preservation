"""Publisher ownership and supervisor retry regressions; uses disposable repos."""
import io
import hashlib
import os
from pathlib import Path
import subprocess
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

import publish
import tray_app
import sweep_inbox


class PublishOwnershipTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='publish-test-')
        self.addCleanup(self.tmp.cleanup)
        self.repo = Path(self.tmp.name)
        for name, value in [('WORK', str(self.repo / 'work')), ('INBOX', str(self.repo / 'inbox'))]:
            context = patch.object(publish.config, name, value)
            context.start()
            self.addCleanup(context.stop)
        (self.repo / 'cachedata').mkdir()
        (self.repo / 'tools').mkdir()
        (self.repo / 'docs').mkdir()
        (self.repo / 'README.md').write_text('base\n')
        (self.repo / 'cachedata' / 'data.txt').write_text('before\n')
        (self.repo / 'tools' / 'example.py').write_text('pass\n')
        self.git('init', '-q')
        self.git('config', 'user.name', 'Test')
        self.git('config', 'user.email', 'test' + chr(64) + 'invalid')
        self.git('add', '-A')
        self.git('commit', '-qm', 'base')
        self.before = self.git('rev-parse', 'HEAD')
        self.patch_value('REPO', str(self.repo))
        self.patch_value('DATA', str(self.repo / 'cachedata'))
        self.consolidate = self.patch_value('consolidate', Mock(return_value=True))
        self.audit = self.patch_value('audit', Mock(return_value=True))
        self.patch_value('sync_tools', Mock(return_value=[]))
        self.patch_value('sync_data', Mock(return_value=0))
        self.push = self.patch_value('stream', Mock(return_value=(0, [])))
        self.patch_value('git', publish.git)

    def patch_value(self, name, value):
        p = patch.object(publish, name, value)
        self.addCleanup(p.stop)
        return p.start()

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args],
                              capture_output=True, text=True, check=True).stdout.strip()

    def test_untracked_draft_survives_and_is_not_committed(self):
        draft = self.repo / 'docs' / 'AGENT-PROMPT.md'
        draft.write_text('unfinished draft\n')
        (self.repo / 'cachedata' / 'data.txt').write_text('after\n')
        self.assertTrue(publish._publish(False))
        self.audit.assert_called_once()
        self.assertEqual(draft.read_text(), 'unfinished draft\n')
        self.assertEqual(self.git('show', '--format=', '--name-only', 'HEAD'),
                         'cachedata/data.txt')
        self.assertEqual(self.git('status', '--porcelain'), '?? docs/')
        self.assertEqual(self.git('ls-files', 'docs'), '')

    def test_tracked_edit_stops_before_consolidating(self):
        (self.repo / 'README.md').write_text('my unfinished edit\n')
        self.assertFalse(publish._publish(True))
        self.consolidate.assert_not_called()
        self.push.assert_not_called()
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.before)

    def test_staged_tool_stops_even_with_no_data_changes(self):
        (self.repo / 'tools' / 'example.py').write_text('print(1)\n')
        self.git('add', 'tools/example.py')
        self.assertFalse(publish._publish(True))
        self.consolidate.assert_not_called()
        self.push.assert_not_called()
        self.assertEqual(self.git('diff', '--cached', '--name-only'), 'tools/example.py')

    def test_staged_draft_is_not_treated_as_untracked(self):
        (self.repo / 'docs' / 'AGENT-PROMPT.md').write_text('draft\n')
        self.git('add', 'docs/AGENT-PROMPT.md')
        self.assertFalse(publish._publish(True))
        self.consolidate.assert_not_called()
        self.push.assert_not_called()

    def test_failed_audit_prevents_commit_and_push(self):
        (self.repo / 'cachedata' / 'data.txt').write_text('not audited\n')
        self.audit.return_value = False
        self.assertFalse(publish._publish(True))
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.before)
        self.push.assert_not_called()

    def test_mid_run_staged_edit_still_blocks_commit(self):
        (self.repo / 'cachedata' / 'data.txt').write_text('after\n')
        def edit_during_audit():
            (self.repo / 'tools' / 'example.py').write_text('print(2)\n')
            self.git('add', 'tools/example.py')
            return True
        self.audit.side_effect = edit_during_audit
        self.assertFalse(publish._publish(True))
        self.assertEqual(self.git('rev-parse', 'HEAD'), self.before)
        self.push.assert_not_called()



class PushRecoveryTests(unittest.TestCase):
    def test_normal_success_uses_one_efficient_push(self):
        with patch.object(publish, 'stream', return_value=(0, ['ok'])) as transfer:
            self.assertEqual(publish.push_branch('main'), (0, ['ok']))
        self.assertEqual(transfer.call_count, 1)
        self.assertNotIn('--no-thin', transfer.call_args.args[0])

    def test_unresolved_bases_retry_without_pipeline_or_history_changes(self):
        error = ['remote: fatal: pack has 92 unresolved deltas', 'error: failed to push']
        with patch.object(publish, 'stream', side_effect=[(1, error), (0, ['ok'])]) as transfer, \
             patch.object(publish, 'consolidate') as consolidate, patch.object(publish, 'git') as git:
            self.assertEqual(publish.push_branch('main'), (0, ['ok']))
        self.assertEqual(transfer.call_count, 2)
        self.assertEqual(transfer.call_args.args[0],
                         ['git', 'push', '--progress', '--no-thin', 'origin', 'main'])
        consolidate.assert_not_called()
        git.assert_not_called()

    def test_fallback_failure_remains_failure_and_is_bounded(self):
        error = ['remote: fatal: pack has 2 unresolved deltas']
        with patch.object(publish, 'stream', return_value=(1, error)) as transfer:
            self.assertEqual(publish.push_branch('main'), (1, error))
        self.assertEqual(transfer.call_count, 2)

    def test_other_rejections_are_not_retried(self):
        for error in ['non-fast-forward', 'Permission denied', 'remote unpack failed: index-pack failed']:
            with self.subTest(error=error), patch.object(publish, 'stream', return_value=(1, [error])) as transfer:
                self.assertEqual(publish.push_branch('main'), (1, [error]))
                self.assertEqual(transfer.call_count, 1)

class BusyTests(unittest.TestCase):
    def test_contended_publish_never_runs_pipeline(self):
        with patch.object(publish, 'Lock') as lock, patch.object(publish, '_publish') as run:
            lock.return_value.__enter__.return_value.held = False
            self.assertIsNone(publish.publish(True))
            run.assert_not_called()

    def test_cli_busy_has_distinct_exit_status(self):
        with patch.object(publish, 'publish', return_value=None):
            self.assertEqual(publish.main([]), publish.BUSY_EXIT)
        with patch.object(publish, 'publish', return_value=False):
            self.assertEqual(publish.main([]), 1)
        with patch.object(publish, 'publish', return_value=True):
            self.assertEqual(publish.main([]), 0)

    def test_runner_describes_busy_as_waiting(self):
        log = Mock()
        runner = tray_app.Runner(log, Mock())
        proc = SimpleNamespace(stdout=io.StringIO('lock held\n'), wait=lambda: publish.BUSY_EXIT)
        with tempfile.TemporaryDirectory(prefix='runner-test-') as work, \
             patch.object(tray_app.config, 'WORK', work), \
             patch.object(tray_app.subprocess, 'Popen', return_value=proc):
            self.assertEqual(runner.run(False, 'test'), publish.BUSY_EXIT)
        self.assertIsNone(runner.last_ok)
        self.assertIn('waiting', runner.last_result)
        self.assertNotIn('FAILED', runner.last_result)
        self.assertFalse(runner.busy.is_set())

    def test_watcher_keeps_busy_submission_pending_without_failure_backoff(self):
        app = SimpleNamespace(state={'push': True, 'tidy': True}, log=Mock(),
                              runner=SimpleNamespace(run=Mock(return_value=publish.BUSY_EXIT)))
        watcher = tray_app.Watcher(app)
        watcher.failures = 3
        with patch.object(tray_app.time, 'time', return_value=1000), \
             patch.object(tray_app, 'tidy_inbox') as tidy:
            watcher.consolidate_and_settle_up([], {'pending.zip': (1, 2)})
        self.assertEqual(watcher.failures, 3)
        self.assertEqual(watcher.next_attempt, 1000 + tray_app.POLL_SECONDS)
        self.assertEqual(watcher.handled, {})
        tidy.assert_not_called()

    def test_cli_watcher_retries_unchanged_submission_after_failure(self):
        with patch.object(publish, 'inbox_fingerprint', return_value=['submission']), \
             patch.object(publish, 'settled', return_value=True), \
             patch.object(publish, 'publish', side_effect=[False, True]) as run, \
             patch.object(publish.time, 'sleep', side_effect=[None, KeyboardInterrupt]):
            with self.assertRaises(KeyboardInterrupt):
                publish.main(['--watch'])
        self.assertEqual(run.call_count, 2)



class ArrivalTests(unittest.TestCase):
    def test_late_files_and_folder_children_remain_pending(self):
        with tempfile.TemporaryDirectory(prefix='arrival-test-') as tmp:
            inbox = Path(tmp)
            (inbox / 'old.zip').write_text('old input')
            (inbox / 'folder').mkdir()
            (inbox / 'folder' / 'old.lua').write_text('old input')
            (inbox / 'archive').mkdir()
            with patch.object(tray_app.config, 'INBOX', str(inbox)), \
                 patch.object(tray_app, 'DONE_DIR', str(inbox / 'archive')):
                processed = tray_app.inbox_fingerprint()
                def pipeline(*args):
                    (inbox / 'new.zip').write_text('new input')
                    (inbox / 'folder' / 'new.lua').write_text('new input')
                    (inbox / 'archive' / 'late.zip').write_text('new input')
                    return 0
                app = SimpleNamespace(state={'push': False, 'tidy': True},
                                      log=Mock(), consumed=set(),
                                      runner=SimpleNamespace(run=pipeline))
                watcher = tray_app.Watcher(app)
                watcher.consolidate_and_settle_up([], processed)
                self.assertTrue((inbox / 'new.zip').exists())
                self.assertTrue((inbox / 'folder' / 'old.lua').exists())
                self.assertTrue((inbox / 'folder' / 'new.lua').exists())
                # GUI no longer files inputs: only the locked publisher has completion evidence.
                self.assertTrue((inbox / 'old.zip').exists())
                self.assertNotIn('new.zip', watcher.handled)
                self.assertNotIn(os.path.join('folder', 'new.lua'), watcher.handled)
                self.assertNotIn(os.path.join('archive', 'late.zip'), watcher.handled)
                self.assertIn(os.path.join('folder', 'old.lua'), watcher.handled)
                self.assertTrue(any(p.endswith('old.zip') for p in watcher.handled))

    def test_changed_input_is_not_archived_or_marked_done(self):
        with tempfile.TemporaryDirectory(prefix='arrival-test-') as tmp:
            inbox = Path(tmp)
            (inbox / 'changed.zip').write_text('before')
            with patch.object(tray_app.config, 'INBOX', str(inbox)), \
                 patch.object(tray_app, 'DONE_DIR', str(inbox / 'archive')):
                processed = tray_app.inbox_fingerprint()
                def pipeline(*args):
                    (inbox / 'changed.zip').write_text('changed during run')
                    return 0
                app = SimpleNamespace(state={'push': False, 'tidy': True},
                                      log=Mock(), consumed=set(),
                                      runner=SimpleNamespace(run=pipeline))
                watcher = tray_app.Watcher(app)
                watcher.consolidate_and_settle_up([], processed, push=False,
                                                  label='manual test')
                self.assertTrue((inbox / 'changed.zip').exists())
                self.assertNotIn('changed.zip', watcher.handled)


class SweepCollisionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='sweep-test-')
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.extracted = self.root / 'extracted'
        self.extracted.mkdir()
        self.inputs = []
        for name, data in [('one', b'first archive'), ('two', b'second archive')]:
            folder = self.root / name
            folder.mkdir()
            archive = folder / 'WDB.7z'
            archive.write_bytes(data)
            self.inputs.append(str(archive))
        p = patch.object(sweep_inbox.intake, 'EXTRACT', str(self.extracted))
        p.start()
        self.addCleanup(p.stop)

    def mark(self, name, archive):
        folder = self.extracted / name
        folder.mkdir()
        (folder / '.intake-source').write_text('WDB.7z')
        (folder / '.intake-sha256').write_text(sweep_inbox.sha256(archive))
        (folder / 'data.wdb').write_bytes(b'payload')
        return folder

    def test_two_hash_verified_7z_extractions_are_not_a_collision(self):
        self.mark('WDB', self.inputs[0])
        self.mark('WDB__' + sweep_inbox.sha256(self.inputs[1])[:8], self.inputs[1])
        self.assertEqual(sweep_inbox.collisions(self.inputs), [])

    def test_unprocessed_7z_still_reports_the_correct_original(self):
        self.mark('WDB', self.inputs[0])
        collisions = sweep_inbox.collisions(self.inputs)
        self.assertEqual(len(collisions), 1)
        self.assertEqual(collisions[0][2], self.inputs[0])

    def test_hash_suffix_alone_does_not_prove_extraction(self):
        self.mark('WDB', self.inputs[0])
        self.mark('WDB__' + sweep_inbox.sha256(self.inputs[1])[:8], self.inputs[0])
        self.assertEqual(len(sweep_inbox.collisions(self.inputs)), 1)

    def test_ambiguous_legacy_7z_stays_unresolved(self):
        folder = self.extracted / 'WDB'
        folder.mkdir()
        (folder / '.intake-source').write_text('WDB.7z')
        (folder / 'data.wdb').write_bytes(b'payload')
        collisions = sweep_inbox.collisions(self.inputs)
        self.assertEqual(len(collisions), 1)
        self.assertIsNone(collisions[0][2])

if __name__ == '__main__':
    unittest.main()