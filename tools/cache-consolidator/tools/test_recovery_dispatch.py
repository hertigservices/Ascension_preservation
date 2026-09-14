import unittest,tempfile,time,subprocess,sys
from pathlib import Path
from unittest.mock import patch
import intake_jobs as jobs
class RecoveryTests(unittest.TestCase):
 def test_owner_lock_and_recovery_history(self):
  with tempfile.TemporaryDirectory() as d:
   p=jobs.enqueue(d);row=jobs.read(p);row.update(status='failed',attempts=5);jobs.write(p,row)
   with jobs.DispatchLock(d) as first:
    self.assertTrue(first.held)
    with jobs.DispatchLock(d) as second:self.assertFalse(second.held)
    self.assertEqual(jobs.recover_manual(d),[p.stem])
    self.assertEqual(jobs.recover_manual(d),[])
   row=jobs.read(p);self.assertEqual(row['status'],'queued');self.assertEqual(row['recovery_history'][0]['attempts'],5)
 def test_spawn_failure_does_not_leave_processing(self):
  with tempfile.TemporaryDirectory() as d:
   p=jobs.enqueue(d);cfg=dict(work=d,out=d,publish_repo=d,publisher='bad')
   with patch.object(jobs.subprocess,'run',side_effect=OSError('spawn')):self.assertTrue(jobs.process_manual(cfg))
   self.assertEqual(jobs.read(p)['status'],'queued');self.assertEqual(jobs.read(p)['attempts'],1)
 def test_clock_rollback_and_stale_heartbeat(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'shared-collector.json';jobs.write(p,dict(enabled=True,heartbeat=time.time()+86400,session='a',sequence=1))
   self.assertFalse(jobs.collector_available(d))
   jobs.write(p,dict(enabled=True,heartbeat=time.time(),session='a',sequence=1))
   with patch.object(jobs.time,'monotonic',return_value=10):self.assertTrue(jobs.collector_available(d))
   with patch.object(jobs.time,'monotonic',return_value=101):self.assertFalse(jobs.collector_available(d))
   jobs.write(p,dict(enabled=True,heartbeat=time.time()+86400,session='b',sequence=2))
   self.assertFalse(jobs.collector_available(d))
   jobs.write(p,dict(enabled=True,heartbeat=time.time()-86400,session='a',sequence=2))
   with patch.object(jobs.time,'monotonic',return_value=102):self.assertTrue(jobs.collector_available(d))
 def test_recovery_request_is_idempotent(self):
  with tempfile.TemporaryDirectory() as d:
   p=jobs.request_recovery(d);first=jobs.read(p);jobs.request_recovery(d);self.assertEqual(jobs.read(p),first)
if __name__=='__main__':unittest.main()
