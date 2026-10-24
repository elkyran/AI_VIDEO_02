"""Actual scheduler reservations and identical host gate paths, synthetic only."""
from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from app.registry import Registry, StudioError, canonical_json
from app.multi_ai_v2 import RunCoordinator
from app.v2_acceptance import fingerprint
from app.v2_contracts import Brief, Result, Task
from app.v2_routes import WorkerRoute, WorkerRoutes, AccountBinding
from tests.test_v2_provider_protocol import scene
from scripts.check_multi_ai_v2 import unittest_counts


def routes(same_identity=False, generation=1):
    return WorkerRoutes(routes=[WorkerRoute(role=role, worker_id=worker, provider='gemini',
        profile_id='gemini-'+str(index), synthetic=True,
        account_binding=AccountBinding(identity_hash=('a' if same_identity else char)*64, auth_generation=generation))
        for index, role, worker, char in [(1, 'gemini_story', 'antigravity_story', 'a'),
                                        (2, 'gemini_visual', 'antigravity_visual', 'b')]])


class HeldSynthetic:
    is_local = True  # Host receipt recovery only, never a production resource.
    is_simulated = True
    is_live = False
    def __init__(self): self.handles = {}; self.events = {}; self.cancelled = []
    def start(self, task, profile, attempt_dir=None):
        handle = task['attempt_id']; self.handles[handle] = deepcopy(task)
        return handle
    def poll(self, handle): return self.events.pop(handle, [])
    def cancel(self, handle): self.cancelled.append(handle)
    def recover(self, directory): return {'status': 'interrupted'}


class RouteSchedulerTests(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix='release01_routes_'); self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()/'queue'; self.adapter = HeldSynthetic()
        self.routes = routes(); self.open()
        self.addCleanup(lambda: self.q.close())
        with Registry(self.root) as registry:
            registry.db.execute('INSERT INTO ai_v2_runs VALUES(?,?,?,?,?)',
                ('fixture_run', 'a'*64, canonical_json(Brief(topic='routing fixture').model_dump()), 'fake', 0.0))
        self.q.queue.resume()

    def open(self, worker_routes=True):
        self.q = RunCoordinator(self.root, adapters={'gemini': self.adapter},
            worker_routes=self.routes if worker_routes else None)

    def enqueue(self, identifier, role='gemini_story', depends=()):
        route = next(r for r in self.routes.routes if r.role == role)
        payload = scene(); payload['scene_id'] = identifier
        # Scene as schema for both scheduler roles isolates routing from creative
        # role protocol. Actual DualWorkerBridge still restricts Visual to Review.
        task = Task(task_id=identifier, run_id='fixture_run', scene_id=identifier, role=role,
            capability='route_fixture', input_version=1, input_hashes={'source': fingerprint(payload)},
            depends_on=list(depends), assigned_worker=route.worker_id, provider_profile_id=route.profile_id,
            expected_output_schema='scene', idempotency_key=identifier)
        return self.q.queue.enqueue(dict(task_id=identifier, project_id='fixture_run',
            role='writer' if role == 'gemini_story' else 'critic', expected_output='scene',
            input_versions=task.input_hashes, depends_on=list(depends), max_attempts=2,
            context={'v2_contract': task.model_dump(), 'project_id': 'fixture_run',
                     'scene_id': identifier, 'scene_version': 1, 'fixture_result': payload}))

    def states(self): return {t['task_id']: t for t in self.q.queue.snapshot()['tasks']}
    def result(self, identifier):
        task = self.states()[identifier]
        contract = Task.model_validate(task['context']['v2_contract'])
        payload = task['context']['fixture_result']
        artifact = self.q.store.put_json('fixture_run', payload, simulated=True)
        return Result(task_id=identifier, run_id='fixture_run', acceptance_key=task['current_attempt_id'],
            expected_output_schema='scene', payload=payload, input_version=1, input_hashes=contract.input_hashes,
            artifacts=[artifact], simulated=True)

    def complete_poll(self, identifier, result=None):
        result = result or self.result(identifier)
        attempt = self.states()[identifier]['current_attempt_id']
        self.adapter.events[attempt] = [{'type': 'completed', 'result': result.payload, 'host_metadata': result.model_dump()}]
        self.q.queue.tick()

    def test_two_accounts_one_provider_have_two_active_handles_and_exact_requests(self):
        self.enqueue('story'); self.enqueue('visual', 'gemini_visual'); self.q.queue.tick()
        self.assertEqual([v['status'] for v in self.states().values()], ['running', 'running'])
        self.assertEqual(len(self.adapter.handles), 2)
        for task in self.adapter.handles.values():
            request = json.loads((self.root/'ai/attempts'/task['attempt_id']/'request.json').read_text())
            route = next(r for r in self.routes.routes if r.role == task['context']['v2_contract']['role'])
            self.assertEqual(request['worker_route'], route.model_dump())
            self.assertEqual(request['account_binding'], route.account_binding.model_dump())
        self.assertEqual(self.q.queue.snapshot()['limits']['max_cloud_workers'], 3)

    def test_same_identity_aliases_and_generation_share_reservation(self):
        self.routes = routes(same_identity=True); self.q.queue.worker_routes = self.routes
        self.enqueue('story'); self.enqueue('visual', 'gemini_visual'); self.q.queue.tick()
        self.assertEqual(self.states()['story']['status'], 'running')
        self.assertEqual(self.states()['visual']['status'], 'pending')
        changed = self.routes.model_dump(); changed['routes'][1]['account_binding']['auth_generation'] = 2
        self.q.queue.worker_routes = WorkerRoutes.model_validate(changed)
        self.q.queue.tick()
        self.assertEqual(len(self.adapter.handles), 1)
        self.assertNotEqual(self.states()['visual']['status'], 'running')

    def test_new_generation_alias_still_shares_live_account_reservation(self):
        self.routes = routes(same_identity=True); self.q.queue.worker_routes = self.routes
        self.enqueue('story'); self.q.queue.tick()
        changed = self.routes.model_dump(); changed['routes'][1]['account_binding']['auth_generation'] = 2
        self.routes = WorkerRoutes.model_validate(changed); self.q.queue.worker_routes = self.routes
        self.enqueue('visual', 'gemini_visual'); self.q.queue.tick()
        self.assertEqual(self.states()['visual']['status'], 'pending')
        self.assertEqual(len(self.adapter.handles), 1)

    def test_corrupt_active_request_keeps_reservation_conservatively(self):
        self.routes = routes(same_identity=True); self.q.queue.worker_routes = self.routes
        self.enqueue('story'); self.q.queue.tick()
        attempt = self.states()['story']['current_attempt_id']
        path = self.root/'ai/attempts'/attempt/'request.json'
        original = json.loads(path.read_text())
        self.enqueue('visual', 'gemini_visual')
        for value in ([], {**original, 'worker_route': routes().routes[1].model_dump()}):
            path.write_text(canonical_json(value), encoding='utf-8'); self.q.queue.tick()
            self.assertEqual(self.states()['visual']['status'], 'pending')
            self.assertEqual(len(self.adapter.handles), 1)

    def test_quota_deadline_survives_early_explicit_resume(self):
        clock = [1000.0]; self.q.queue.clock = lambda: clock[0]
        self.enqueue('story'); self.q.queue.tick()
        attempt = self.states()['story']['current_attempt_id']
        self.adapter.events[attempt] = [{'type': 'failed', 'failure': {'kind': 'quota_exhausted',
            'retry_after': 60, 'retryable': False}}]
        self.q.queue.tick(); self.q.queue.resume(worker_id='antigravity_story'); self.q.queue.tick()
        self.assertEqual(self.states()['story']['status'], 'waiting_access')
        self.assertEqual(self.states()['story']['attempt'], 1)
        self.assertEqual(self.states()['story']['error']['kind'], 'quota_exhausted')
        clock[0] = 1061; self.q.queue.resume(worker_id='antigravity_story'); self.q.queue.tick()
        self.assertEqual(self.states()['story']['status'], 'running')
        self.assertEqual(self.states()['story']['attempt'], 2)

    def test_pending_account_report_does_not_hold_other_routed_identity(self):
        self.enqueue('story'); self.q.queue.tick()
        attempt = self.states()['story']['current_attempt_id']
        with Registry(self.root) as r:
            r.db.execute('INSERT INTO ai_profile_reports VALUES(?,?,?)', (attempt, '{}', 0))
            self.assertTrue(self.q.queue._pending_report(r, 'gemini', self.routes.routes[0]))
            self.assertFalse(self.q.queue._pending_report(r, 'gemini', self.routes.routes[1]))
            self.assertTrue(self.q.queue._pending_report(r, 'gemini', None))

    def test_quota_pauses_account_tasks_without_rotation_and_independent_visual_runs(self):
        self.enqueue('story'); self.enqueue('story_next'); self.enqueue('visual', 'gemini_visual')
        self.enqueue('dependent_visual', 'gemini_visual', ['story'])
        selected = self.q.queue.snapshot()['selected_profiles']
        self.q.queue.tick()
        attempt = self.states()['story']['current_attempt_id']
        self.adapter.events[attempt] = [{'type': 'failed', 'failure': {'kind': 'quota_exhausted',
            'quota_confirmed': True, 'retryable': False, 'message': 'synthetic quota'}}]
        self.q.queue.tick()
        states = self.states()
        self.assertEqual(states['story']['status'], 'waiting_access')
        self.assertEqual(states['story_next']['status'], 'waiting_access')
        self.assertEqual(states['visual']['status'], 'running')
        self.assertEqual(states['dependent_visual']['status'], 'pending')
        self.assertEqual(self.q.queue.snapshot()['selected_profiles'], selected)
        self.assertFalse(self.q.queue.snapshot()['rotation_on_quota'])
        self.assertTrue(self.q.queue.snapshot()['worker_pauses'])
        self.complete_poll('visual'); self.assertEqual(self.states()['visual']['status'], 'completed')
        self.q.queue.resume(); self.q.queue.tick()
        self.assertEqual(self.states()['story']['attempt'], 2)

    def test_auth_pause_is_not_quota_and_has_no_rotation(self):
        self.enqueue('story'); self.enqueue('story_next'); self.q.queue.tick()
        attempt = self.states()['story']['current_attempt_id']
        self.adapter.events[attempt] = [{'type': 'failed', 'failure': {'kind': 'authentication', 'retryable': False}}]
        self.q.queue.tick(); states = self.states()
        self.assertEqual(states['story_next']['error']['kind'], 'authentication')
        self.assertNotIn('quota_remaining', states['story_next']['error'])
        self.assertEqual(states['story_next']['attempt'], 0)

    def test_generation_change_rejects_poll_and_manual_results(self):
        self.enqueue('story'); self.q.queue.tick(); result = self.result('story')
        self.q.queue.worker_routes = routes(generation=2)
        with self.assertRaises(StudioError): self.q.accept_result(result.model_dump())
        self.complete_poll('story', result)
        self.assertEqual(self.states()['story']['status'], 'failed')

    def test_swapped_result_cannot_cross_worker_attempt(self):
        self.enqueue('story'); self.enqueue('visual', 'gemini_visual'); self.q.queue.tick()
        self.complete_poll('story', self.result('visual'))
        self.assertEqual(self.states()['story']['status'], 'failed')
        self.assertEqual(self.states()['visual']['status'], 'running')

    def test_accepted_history_survives_new_generation_and_conflicting_replay_fails(self):
        self.enqueue('story'); self.q.queue.tick(); result = self.result('story')
        first = self.q.accept_result(result.model_dump())
        self.q.close(); self.routes = routes(generation=2); self.open()
        self.assertEqual(self.q.accept_result(result.model_dump()), first)
        self.assertEqual(len(self.q.accepted_artifacts('story')), 1)
        changed = result.model_dump(); changed['payload']['goal'] = 'Different'
        with self.assertRaises(StudioError): self.q.accept_result(changed)
        with Registry(self.root) as r:
            self.assertEqual(r.db.execute("SELECT count(*) FROM ai_events WHERE kind='result_accepted'").fetchone()[0], 1)

    def test_missing_current_route_is_hard_block_after_restart(self):
        self.enqueue('story'); self.q.close(); self.open(worker_routes=False)
        self.q.queue.tick()
        self.assertEqual(self.states()['story']['status'], 'waiting_access')
        self.assertEqual(self.states()['story']['attempt'], 0)

    def test_crash_after_completion_file_revalidates_route_and_recovers_once(self):
        self.enqueue('story'); self.q.queue.tick(); result = self.result('story')
        with Registry(self.root) as r, r.transaction():
            attempt = r.db.execute('SELECT * FROM ai_attempts WHERE id=?', (result.acceptance_key,)).fetchone()
            gated = self.q.queue._gate_result(r, attempt, result.payload, result.model_dump())
            self.q.queue._write_completion(r, attempt, result.payload, gated=gated)
        self.q.close(); self.open(); self.q.queue.tick()
        self.assertEqual(self.states()['story']['status'], 'completed')
        self.assertEqual(len(self.adapter.handles), 1)
        self.q.close(); self.open(); self.q.queue.tick()
        with Registry(self.root) as r:
            self.assertEqual(r.db.execute("SELECT count(*) FROM ai_events WHERE kind='result_accepted'").fetchone()[0], 1)

    def test_tampered_request_or_route_payload_is_rejected(self):
        value = self.enqueue('story'); self.q.queue.tick(); result = self.result('story')
        request = self.root/'ai/attempts'/result.acceptance_key/'request.json'
        data = json.loads(request.read_text()); data['worker_route']['account_binding']['auth_generation'] = 7
        request.write_text(canonical_json(data), encoding='utf-8')
        with self.assertRaises(StudioError): self.q.accept_result(result.model_dump())
        other = deepcopy(value); other['task_id'] = 'injected'; other['worker_route']['provider'] = 'codex'
        with self.assertRaises(StudioError): self.q.queue.enqueue(other)


class SummaryCountsTests(unittest.TestCase):
    def test_failed_summary_retains_skipped_count(self):
        self.assertEqual(unittest_counts('Ran 107 tests in 3.3s\n\nFAILED (errors=1, skipped=1)\n'), (107, 1))
    def test_success_and_missing_summary(self):
        self.assertEqual(unittest_counts('Ran 5 tests in 0.1s\n\nOK (skipped=2)\n'), (5, 2))
        self.assertEqual(unittest_counts('Ran 1 test in 0.1s\n\nFAILED (failures=1)\n'), (1, 0))
        self.assertEqual(unittest_counts('still running'), (None, 0))


if __name__ == '__main__': unittest.main()
