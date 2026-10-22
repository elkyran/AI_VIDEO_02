"""Exact spawned object, not a PID observed later. No production worker runs."""
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.jobs import JobManager


class SpawnReceiptTests(unittest.TestCase):
    def test_actual_spawn_observes_intent_then_exact_popen_object_and_legacy_none(self):
        with tempfile.TemporaryDirectory(prefix='spawn_receipt_') as temp:
            process=object();events=[]
            manager=JobManager(temp,spawn_observer=lambda *args:events.append(args))
            with patch('app.jobs.subprocess.Popen',return_value=process) as native:
                manager._spawn('fixture')
            self.assertEqual(events,[('intent','fixture',None),('spawned','fixture',process)])
            self.assertEqual(native.call_count,1)
            with patch('app.jobs.subprocess.Popen',return_value=process) as native:
                JobManager(Path(temp)/'legacy')._spawn('legacy')
            self.assertEqual(native.call_count,1)

    def test_failed_observer_never_retries_or_kills_the_spawned_object(self):
        with tempfile.TemporaryDirectory(prefix='spawn_failure_') as temp:
            events=[]
            def observer(phase,job,process):
                events.append(phase)
                if phase=='spawned':raise RuntimeError('durable receipt interrupted')
            manager=JobManager(temp,spawn_observer=observer)
            with patch('app.jobs.subprocess.Popen') as native:
                with self.assertRaisesRegex(RuntimeError,'durable receipt'):manager._spawn('fixture')
            self.assertEqual(events,['intent','spawned']);native.assert_called_once()
            native.return_value.kill.assert_not_called()

    def test_birth_comes_from_live_owned_process_handle_and_early_exit_is_unknown(self):
        from app.v2_spawn_receipts import spawned_identity
        # Explicit bounded no-tools child, never app.worker or user runtime.
        child=subprocess.Popen([sys.executable,'-I','-c','import time; time.sleep(3)'],
            stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
        try:
            identity=spawned_identity(child)
            self.assertIsInstance(identity,str)
            self.assertTrue(identity)
        finally:
            if child.poll() is None:child.terminate()
            child.wait(timeout=5)
        self.assertIsNone(spawned_identity(child))


class GuardedReceiptTests(unittest.TestCase):
    def setUp(self):
        from tests.test_v2_closure_handlers import GenericRecoveryTests
        self.fixture=GenericRecoveryTests('test_dead_running_same_request_reaches_existing_resume_once')
        self.fixture.setUp();self.addCleanup(self.fixture.doCleanups)
        self.p=self.fixture.p;self.manager=self.fixture.manager;self.job_id=self.fixture.receipt['job_id']

    def test_actual_observer_pending_running_dead_resume_and_replay_use_one_exact_child(self):
        from app.v2_spawn_receipts import SpawnObserver
        from app.v2_job_recovery import guarded_resume,checkpoint
        observer=SpawnObserver(self.p);manager=JobManager(self.p.engine,spawn_observer=observer)
        process=SimpleNamespace(pid=23456)
        with patch('app.jobs.subprocess.Popen',return_value=process) as native, \
             patch('app.v2_spawn_receipts.spawned_identity',return_value='native-birth'):
            manager._spawn(self.job_id)
        receipt=checkpoint(self.p,'system_spawn_receipt',self.job_id)
        self.assertEqual(receipt['worker_pid'],process.pid);self.assertEqual(receipt['birth'],'native-birth')
        with patch('app.multi_ai_preview.process_identity',return_value='native-birth'),patch.object(JobManager,'resume') as resume:
            self.assertFalse(guarded_resume(self.p,manager,manager.status(self.job_id)));resume.assert_not_called()
        self.fixture.state(state='running',worker_pid=process.pid,attempts=1)
        with patch('app.multi_ai_preview.process_identity',return_value=False),patch('app.jobs.process_alive',return_value=False), \
             patch('app.jobs.subprocess.Popen',return_value=process) as native, \
             patch('app.v2_spawn_receipts.spawned_identity',return_value='second-birth'):
            self.assertTrue(guarded_resume(self.p,manager,manager.status(self.job_id)))
            native.assert_called_once()
        self.fixture.state(state='running',worker_pid=process.pid,attempts=2)
        with patch('app.multi_ai_preview.process_identity',return_value='second-birth'),patch.object(JobManager,'_spawn') as native:
            self.assertFalse(guarded_resume(self.p,manager,manager.status(self.job_id)));native.assert_not_called()

    def test_ambiguous_spawn_unknown_birth_foreign_pid_and_historical_jobs_hold(self):
        from app.v2_spawn_receipts import SpawnObserver
        from app.v2_job_recovery import guarded_resume,checkpoint
        from app.registry import StudioError
        observer=SpawnObserver(self.p);manager=JobManager(self.p.engine,spawn_observer=observer)
        with patch('app.jobs.subprocess.Popen',side_effect=RuntimeError('spawn crash')) as native:
            with self.assertRaises(RuntimeError):manager._spawn(self.job_id)
        self.assertIsNotNone(checkpoint(self.p,'system_spawn_intent',self.job_id))
        with patch('app.jobs.subprocess.Popen') as native:
            with self.assertRaises(StudioError):manager._spawn(self.job_id)
            with self.assertRaises(StudioError):guarded_resume(self.p,manager,manager.status(self.job_id))
            native.assert_not_called()
        self.fixture.state(state='running',worker_pid=23456,attempts=1);self.fixture.ownership('native')
        for identity in (None,'reused-pid-other-birth'):
            with self.subTest(identity=identity),patch('app.multi_ai_preview.process_identity',return_value=identity), \
                 patch.object(JobManager,'_spawn') as native:
                with self.assertRaises(StudioError):guarded_resume(self.p,manager,manager.status(self.job_id))
                native.assert_not_called()

    def test_single_job_invariant_blocks_competing_pending_work_and_default_continuation_has_no_ownership(self):
        from app.v2_spawn_receipts import SpawnObserver
        from app.registry import Registry,StudioError
        from app.v2_job_recovery import guarded_resume
        with Registry(self.p.engine) as db:
            row=db.db.execute('SELECT * FROM jobs WHERE id=?',(self.job_id,)).fetchone()
            columns=list(row.keys());values=list(row);values[columns.index('id')]='foreign_pending'
            db.db.execute('INSERT INTO jobs('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',values)
        with patch('app.jobs.subprocess.Popen') as native:
            with self.assertRaises(StudioError):JobManager(self.p.engine,spawn_observer=SpawnObserver(self.p))._spawn(self.job_id)
            native.assert_not_called()
        self.fixture.state(state='running',worker_pid=23456,attempts=1)
        with patch.object(JobManager,'_spawn') as native:
            with self.assertRaises(StudioError):guarded_resume(self.p,self.manager,self.manager.status(self.job_id))
            native.assert_not_called()

    def test_unknown_created_birth_and_crash_after_durable_receipt_hold_or_poll_without_duplicate(self):
        from app.v2_spawn_receipts import SpawnObserver,spawned_identity
        from app.v2_job_recovery import guarded_resume,checkpoint
        from app.registry import StudioError
        bad=SimpleNamespace(pid=23456,_handle=0,poll=lambda:None)
        self.assertIsNone(spawned_identity(bad))
        manager=JobManager(self.p.engine,spawn_observer=SpawnObserver(self.p))
        with patch('app.jobs.subprocess.Popen',return_value=bad) as native, \
             patch('app.v2_spawn_receipts.spawned_identity',return_value=None):
            with self.assertRaises(StudioError):manager._spawn(self.job_id)
            native.assert_called_once()
        self.assertIsNotNone(checkpoint(self.p,'system_spawn_unknown',self.job_id))
        self.assertIsNone(checkpoint(self.p,'system_spawn_receipt',self.job_id))
        with patch('app.jobs.subprocess.Popen') as native:
            with self.assertRaises(StudioError):guarded_resume(self.p,manager,manager.status(self.job_id))
            native.assert_not_called()
