"""Temporary fixtures only; no auth, models, production or cloud requests."""
import json
import os
from pathlib import Path
import tempfile
import subprocess
import sys
import unittest

from pydantic import ValidationError
from app.ai_adapters import FakeAdapter
from app.multi_ai_v2 import RunCoordinator, STAGES, local_root
from app.registry import Registry, StudioError
from app.v2_contracts import Artifact, Brief, Review, issue_owner
from app.v2_storage import ArtifactStore


class V2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='v2_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'local'

    def coordinator(self, **kwargs):
        q = RunCoordinator(self.root, **kwargs)
        self.addCleanup(q.close)
        return q

    def test_full_fake_graph_and_context(self):
        q = self.coordinator()
        s = q.create({'topic': 'Control fixture'}, 'one')
        done = q.advance(s['run_id'], 30)
        self.assertEqual(done['status'], 'SIMULATION_SUCCEEDED')
        self.assertEqual(len(done['tasks']), len(STAGES))
        self.assertFalse(done['ready_for_user'])
        self.assertEqual(done['video_qa'], 'NOT_RUN')
        self.assertEqual(done['model_requests'], 0)
        self.assertTrue(all(t['attempt'] == 1 for t in done['tasks']))
        pack = self.root / 'runs' / s['run_id'] / 'context' / done['tasks'][0]['task_id']
        self.assertEqual(len(list(pack.iterdir())), 8)
        self.assertLess((pack / 'task.md').stat().st_size, 12000)

    def test_idempotent_run_and_no_duplicate_dispatch(self):
        q = self.coordinator()
        s = q.create({'topic': 'x'}, 'one')
        q.advance(s['run_id'], 30)
        q.create({'topic': 'x'}, 'one')
        done = q.advance(s['run_id'], 10)
        self.assertTrue(all(t['attempt'] == 1 for t in done['tasks']))
        with self.assertRaises(StudioError): q.create({'topic': 'changed'}, 'one')

    def test_restart_reconciles_and_reuses_completed_results(self):
        q = self.coordinator()
        s = q.create({'topic': 'x'}, 'one')
        q.advance(s['run_id'], 6)
        prior = {t['task_id']: t['result_ref'] for t in q.status(s['run_id'])['tasks'] if t['status'] == 'SUCCEEDED'}
        q.close()
        q = self.coordinator()
        q.create({'topic': 'x'}, 'one')
        done = q.advance(s['run_id'], 30)
        self.assertEqual(done['status'], 'SIMULATION_SUCCEEDED')
        for t in done['tasks']:
            if t['task_id'] in prior:
                self.assertEqual(t['result_ref'], prior[t['task_id']])
                self.assertEqual(t['attempt'], 1)

    def test_quota_pause_no_rotation(self):
        run = 'run_' + __import__('hashlib').sha256(b'one').hexdigest()[:24]
        fake = FakeAdapter('gemini', scripts={run + '_source_index': {'outcome': 'quota_exhausted'}})
        q = self.coordinator(adapters={'gemini': fake, 'codex': FakeAdapter('codex')})
        s = q.create({'topic': 'x'}, 'one')
        done = q.advance(s['run_id'], 4)
        self.assertEqual(done['tasks'][0]['status'], 'WAITING_QUOTA')
        snapshot = q.queue.snapshot()
        self.assertFalse(snapshot['rotation_on_quota'])
        self.assertEqual(snapshot['selected_profiles']['gemini'], 'gemini-1')
        self.assertEqual(len(snapshot['attempts']), 1)

    def test_production_is_fail_closed(self):
        q = self.coordinator()
        s = q.create({'topic': 'x', 'production_mode': 'production'}, 'live')
        self.assertEqual(q.advance(s['run_id'])['status'], 'BLOCKED_UNSUPPORTED')
        self.assertEqual(q.queue.snapshot()['attempts'], [])

    def test_active_db_cannot_be_synced(self):
        with self.assertRaises(ValueError): local_root(self.root / 'OneDrive' / 'db')

    def test_legacy_queue_not_repurposed(self):
        from app.multi_ai import Coordinator
        with Coordinator(self.root) as q: q.enqueue({'task_id': 'old'})
        with self.assertRaises(ValueError): RunCoordinator(self.root)

    def test_no_traversal_or_secret_in_context(self):
        q = self.coordinator()
        with self.assertRaises(ValidationError): q.create({'topic': 'x'}, '../evil')
        s = q.create({'topic': 'Bearer synthetic.value'}, 'one')
        paths = list((self.root / 'runs' / s['run_id'] / 'context').glob('*/task.md'))
        self.assertTrue(all('synthetic.value' not in p.read_text(encoding='utf-8') for p in paths))

    def test_artifact_hash_and_drive_copy(self):
        store = ArtifactStore(self.root)
        artifact = store.put_json('run1', {'value': 1}, simulated=True)
        drive = Path(self.temp.name) / 'drive'; drive.mkdir()
        receipt = store.copy_to_drive(artifact, drive)
        self.assertFalse(receipt['uploaded'])
        self.assertEqual(receipt['remote_sync'], 'UNVERIFIED')
        self.assertEqual(store.copy_to_drive(artifact, drive), receipt)
        (drive / artifact.path).write_text('corrupt')
        with self.assertRaises(ValueError): store.copy_to_drive(artifact, drive)

    def test_corrupt_and_missing_artifacts(self):
        store = ArtifactStore(self.root)
        artifact = store.put_json('run1', {'x': 1}, simulated=True)
        p = store.verify(artifact); p.write_text('bad')
        with self.assertRaises(ValueError): store.verify(artifact)
        p.unlink()
        with self.assertRaises(ValueError): store.verify(artifact)

    def test_drive_unavailable_and_path_allowlist(self):
        store = ArtifactStore(self.root)
        a = store.put_json('run1', {}, simulated=True)
        with self.assertRaises(ValueError): store.copy_to_drive(a, Path(self.temp.name) / 'absent')
        with self.assertRaises(StudioError): store.put_json('../../outside', {}, simulated=True)

    def test_strict_contracts_and_honest_review(self):
        with self.assertRaises(ValidationError): Brief(topic='x', unexpected=1)
        with self.assertRaises(ValidationError): Brief(topic='x', auto_fix_cycles=2)
        with self.assertRaises(ValidationError):
            Review(review_mode='full_video', verdict='pass', materials_actually_reviewed=['frames'], issues=[])
        self.assertEqual(issue_owner('canon'), 'gemini_story')
        self.assertEqual(issue_owner('mlt'), 'codex')

    def test_remote_entrypoint_process_restart(self):
        repo = Path(__file__).resolve().parents[1]
        env = {**os.environ, 'LOCAL_RUN_ROOT': str(self.root), 'GITHUB_RUN_ID': '42',
               'VIDEO_REQUEST_JSON': json.dumps({'topic': 'Literal $(exit) ; no shell', 'production_mode': 'fake'})}
        summaries = []
        for _ in range(2):
            p = subprocess.run([sys.executable, '-X', 'utf8', 'scripts/dispatch_multi_ai_v2.py'],
                               cwd=repo, env=env, capture_output=True, text=True, encoding='utf-8', timeout=40)
            self.assertEqual(p.returncode, 0, p.stderr)
            summaries.append(json.loads(p.stdout))
        self.assertEqual(summaries[0], summaries[1])
        self.assertEqual(summaries[0]['status'], 'SIMULATION_SUCCEEDED')
        with Registry(self.root) as registry:
            self.assertEqual(registry.db.execute('SELECT COUNT(*) FROM ai_attempts').fetchone()[0], len(STAGES))


if __name__ == '__main__': unittest.main()
