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
            if sys.platform=='win32':
                self.assertIsNone(spawned_identity(SimpleNamespace(pid=child.pid+1,_handle=child._handle,poll=child.poll)))
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

    def test_atomic_admission_rolls_back_enqueue_and_resume_with_foreign_pending_job(self):
        from app.v2_spawn_receipts import SpawnObserver
        from app.registry import Registry,StudioError
        manager=JobManager(self.p.engine,spawn_observer=SpawnObserver(self.p))
        def state():
            with Registry(self.p.engine) as db:
                return {table:[tuple(r) for r in db.db.execute('SELECT * FROM '+table+' ORDER BY 1')]
                    for table in ('jobs','requests','projects')}
        before=state()
        with Registry(self.p.engine) as db:base=db._project(self.p.run_id)['revision']
        with patch('app.jobs.subprocess.Popen') as native:
            with self.assertRaises(StudioError):manager.enqueue(self.p.run_id,'synthesize',{'script_asset_id':self.fixture.sid},base,'competing')
            native.assert_not_called()
        self.assertEqual(before,state())
        with Registry(self.p.engine) as db:
            row=db.db.execute('SELECT * FROM jobs WHERE id=?',(self.job_id,)).fetchone()
            columns=list(row.keys());values=list(row);values[columns.index('id')]='foreign_pending'
            db.db.execute('INSERT INTO jobs('+','.join(columns)+') VALUES('+','.join('?' for _ in columns)+')',values)
        self.fixture.state(state='failed',attempts=1)
        before=state()
        with patch('app.jobs.subprocess.Popen') as native:
            with self.assertRaises(StudioError):manager.resume(self.job_id)
            native.assert_not_called()
        self.assertEqual(before,state())

    def test_two_observers_compete_for_one_atomic_intent_and_only_one_native_spawn(self):
        from concurrent.futures import ThreadPoolExecutor
        import threading
        from app.v2_spawn_receipts import SpawnObserver
        from app.registry import Registry,StudioError
        barrier=threading.Barrier(2);managers=[]
        for _ in range(2):
            observer=SpawnObserver(self.p)
            def enter(phase,job,process,observe=observer):
                if phase=='intent':barrier.wait(timeout=5)
                return observe(phase,job,process)
            managers.append(JobManager(self.p.engine,spawn_observer=enter))
        process=SimpleNamespace(pid=23456,args=[sys.executable,'-m','app.worker','--workspace',str(self.p.engine),'--job',self.job_id])
        def invoke(manager):
            try:manager._spawn(self.job_id);return 'created'
            except StudioError as error:return error.code
        with patch('app.jobs.subprocess.Popen',return_value=process) as native, \
             patch('app.v2_spawn_receipts.spawned_identity',return_value='native'):
            with ThreadPoolExecutor(max_workers=2) as pool:outcomes=list(pool.map(invoke,managers))
            native.assert_called_once()
        self.assertCountEqual(outcomes,['created','JOB_RECOVERY_HOLD'])
        with Registry(self.p.q.root) as db:
            self.assertEqual(db.db.execute("SELECT count(*) FROM ai_events WHERE kind='system_spawn_intent'").fetchone()[0],1)

    def test_deadline_root_and_fixed_argv_fail_closed_before_ownership(self):
        from app.v2_spawn_receipts import SpawnObserver
        from app.v2_job_recovery import guarded_resume,checkpoint
        from app.registry import StudioError
        observer=SpawnObserver(self.p,deadline=1)
        with patch('app.v2_spawn_receipts.time.monotonic',side_effect=[0,2]),patch('app.jobs.subprocess.Popen') as native:
            with self.assertRaises(StudioError) as error:JobManager(self.p.engine,spawn_observer=observer)._spawn(self.job_id)
            self.assertEqual(error.exception.code,'TIME_BUDGET');native.assert_not_called()
        self.assertIsNotNone(checkpoint(self.p,'system_spawn_intent',self.job_id))
        foreign=JobManager(self.p.root/'foreign')
        with self.assertRaises(StudioError) as error:guarded_resume(self.p,foreign,self.manager.status(self.job_id))
        self.assertEqual(error.exception.code,'JOB_OWNERSHIP_UNVERIFIED')
        # A different claimed next attempt cannot legitimize arbitrary argv.
        self.fixture.state(attempts=1)
        wrong=SimpleNamespace(pid=23456,args=['arbitrary','command'])
        with patch('app.jobs.subprocess.Popen',return_value=wrong),patch('app.v2_spawn_receipts.spawned_identity') as identity:
            with self.assertRaises(StudioError):JobManager(self.p.engine,spawn_observer=SpawnObserver(self.p))._spawn(self.job_id)
            identity.assert_not_called()
        self.assertIsNone(checkpoint(self.p,'system_spawn_receipt',self.job_id))

    def test_crash_after_durable_spawn_receipt_polls_then_resume_crash_holds_without_repeating(self):
        from app.v2_spawn_receipts import SpawnObserver
        from app.v2_job_recovery import guarded_resume,checkpoint
        from app.registry import StudioError
        observer=SpawnObserver(self.p)
        def interrupt(phase,job,process):
            observer(phase,job,process)
            if phase=='spawned':raise RuntimeError('after durable receipt')
        manager=JobManager(self.p.engine,spawn_observer=interrupt)
        process=SimpleNamespace(pid=23456,args=[sys.executable,'-m','app.worker','--workspace',str(self.p.engine),'--job',self.job_id])
        with patch('app.jobs.subprocess.Popen',return_value=process) as native,patch('app.v2_spawn_receipts.spawned_identity',return_value='native'):
            with self.assertRaisesRegex(RuntimeError,'after durable'):manager._spawn(self.job_id)
            native.assert_called_once()
        self.assertIsNotNone(checkpoint(self.p,'system_spawn_receipt',self.job_id))
        with patch('app.multi_ai_preview.process_identity',return_value='native'),patch('app.jobs.subprocess.Popen') as native:
            self.assertFalse(guarded_resume(self.p,manager,manager.status(self.job_id)));native.assert_not_called()
        self.fixture.state(state='running',worker_pid=process.pid,attempts=1)
        with patch('app.multi_ai_preview.process_identity',return_value=False),patch('app.jobs.process_alive',return_value=False), \
             patch('app.jobs.subprocess.Popen',return_value=process) as native,patch('app.v2_spawn_receipts.spawned_identity',return_value='new'):
            with self.assertRaisesRegex(RuntimeError,'after durable'):guarded_resume(self.p,manager,manager.status(self.job_id))
            native.assert_called_once()
        self.fixture.state(state='running',worker_pid=process.pid,attempts=2)
        with patch('app.multi_ai_preview.process_identity',return_value=False),patch('app.jobs.subprocess.Popen') as native:
            with self.assertRaises(StudioError):guarded_resume(self.p,manager,manager.status(self.job_id))
            native.assert_not_called()

    def test_actual_observer_pending_running_dead_resume_and_replay_use_one_exact_child(self):
        from app.v2_spawn_receipts import SpawnObserver
        from app.v2_job_recovery import guarded_resume,checkpoint
        observer=SpawnObserver(self.p);manager=JobManager(self.p.engine,spawn_observer=observer)
        process=SimpleNamespace(pid=23456,args=[sys.executable,'-m','app.worker','--workspace',str(self.p.engine.resolve()),'--job',self.job_id])
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
        bad.args=[sys.executable,'-m','app.worker','--workspace',str(self.p.engine.resolve()),'--job',self.job_id]
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
