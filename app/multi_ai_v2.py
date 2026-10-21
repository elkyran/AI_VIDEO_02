"""Run facade over the existing durable Coordinator, not another scheduler.

The initial executable slice is explicitly fake. Production stays blocked until
the Story, Visual, production-import and remote-storage boundaries are verified.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from functools import wraps
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from .ai_adapters import sanitize
from .multi_ai import Coordinator
from .registry import Registry, StudioError, canonical_json, content_hash, workspace_path, write_generated_json
from .v2_contracts import Brief, Task, Result, Review, TrustedVideoProbe
from .v2_storage import ArtifactStore
from .v2_video import VideoEvidence
from .v2_acceptance import V2ResultGate

STAGES = (
    ('SOURCE_INDEX', 'gemini_story'), ('CANON_BUILD', 'gemini_story'),
    ('STORY_OUTLINE', 'gemini_story'), ('STORY_REVIEW', 'gemini_story'),
    ('SCENE_SCRIPT', 'gemini_story'), ('VOICE_SYNTHESIS', 'python'),
    ('VISUAL_COVERAGE', 'gemini_visual'), ('EDIT_PLAN', 'codex'),
    ('EDIT_VALIDATE', 'python'), ('BUILD', 'python'), ('PREVIEW_RENDER', 'python'),
    ('TECHNICAL_QA', 'python'), ('EDITORIAL_QA', 'gemini_visual'),
    ('ISSUE_ROUTING', 'python'), ('TARGETED_FIX', 'codex'), ('FINAL_RENDER', 'python'),
    ('FINAL_QA', 'python'), ('PACKAGE', 'python'), ('DRIVE_UPLOAD', 'python'))
SCHEMAS = {'SCENE_SCRIPT': 'scene', 'EDIT_PLAN': 'edit_plan', 'EDITORIAL_QA': 'review'}
BLOCKERS = ['Antigravity: AGE-GATED / DISABLED; no login, smoke or auth migration authorized',
            'gemini_visual: logical future Antigravity role; verified media delivery required',
            'production_import: offline bridge exists; live script registration/submission not authorized',
            'runner: registration and live workflow_dispatch required',
            'drive: configured destination and cloud delivery verification required']
TERMINAL = frozenset({'SUCCEEDED', 'SIMULATION_SUCCEEDED', 'FAILED_FINAL', 'STALE',
                      'BLOCKED_UNSUPPORTED', 'CANCELLED'})
# JSON/CLI protocol: only completed success is exit 0. Pause/stop remain resumable.
EXIT_CODES = {'SUCCEEDED': 0, 'SIMULATION_SUCCEEDED': 0, 'IN_PROGRESS': 2,
              'WAITING_AUTH': 3, 'WAITING_QUOTA': 4, 'BLOCKED_UNSUPPORTED': 5,
              'FAILED_RETRYABLE': 6, 'FAILED_FINAL': 7, 'STALE': 8, 'ERROR': 9,
              'CANCELLED': 10}


def run_locked(method):
    """Keep ownership check and global-queue action atomic under the existing lock."""
    @wraps(method)
    def call(self, *args, **kwargs):
        with self.queue._lock:
            return method(self, *args, **kwargs)
    return call


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def local_root(path):
    root = Path(path).resolve()
    repo = Path(__file__).resolve().parents[1]
    if root == repo or root.is_relative_to(repo) or root == root.parent:
        raise ValueError('LOCAL_RUN_ROOT must be a separate local directory outside the repo/production workspace')
    forbidden = {'onedrive', 'googledrive', 'google drive', 'my drive', 'мой диск'}
    if any(any(name in part.casefold() for name in forbidden) for part in root.parts):
        raise ValueError('LOCAL_RUN_ROOT must be outside OneDrive/Google Drive sync folders')
    for key in ('OneDrive', 'OneDriveConsumer', 'OneDriveCommercial', 'GOOGLE_DRIVE_ROOT'):
        if os.getenv(key) and root.is_relative_to(Path(os.environ[key]).resolve()):
            raise ValueError('LOCAL_RUN_ROOT must be outside configured sync folders')
    return root


def legacy_queue_conflict(root):
    db = Path(root).resolve() / 'registry.sqlite'
    if not db.exists(): return False
    with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as conn:
        tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        return bool(('projects' in tables and conn.execute('SELECT 1 FROM projects LIMIT 1').fetchone()) or
            ('ai_tasks' in tables and 'ai_v2_runs' not in tables and conn.execute('SELECT 1 FROM ai_tasks LIMIT 1').fetchone()))


def task_status(task, tasks):
    state = task['status']
    error = task.get('error') or {}
    kind = error.get('kind')
    if state == 'completed': return 'SUCCEEDED'
    if state == 'running': return 'RUNNING'
    if state == 'stale' or kind == 'stale_inputs': return 'STALE'
    if state == 'waiting_access':
        return 'WAITING_QUOTA' if kind == 'quota_exhausted' else 'WAITING_AUTH'
    if state == 'interrupted': return 'FAILED_RETRYABLE'
    if state == 'blocked': return 'BLOCKED_UNSUPPORTED' if kind == 'unverified_access' else 'FAILED_FINAL'
    if state == 'failed': return 'FAILED_FINAL'
    if state == 'pending':
        if kind in {'network', 'overloaded', 'rate_limited'}: return 'FAILED_RETRYABLE'
        return 'READY' if all(tasks[d]['status'] == 'completed' for d in task['depends_on']) else 'PENDING'
    raise ValueError('Unknown legacy queue state')


class RunCoordinator:
    def __init__(self, root, *, adapters=None, video_evidence_root=None, clock=None, local_pilot=None, system_permit=None):
        self.root = local_root(root)
        if local_pilot is not None:
            from .v2_local_pilot import PilotPermit
            if type(local_pilot) is not PilotPermit or self.root != local_pilot.root/'coordinator':
                raise ValueError('Local pilot requires its exact host permit and coordinator root')
            local_pilot.validate()
        if system_permit is not None:
            from .v2_pipeline import SystemPermit
            if type(system_permit) is not SystemPermit or self.root != system_permit.root/'coordinator' or local_pilot is not None:
                raise ValueError('SYSTEM-01 requires its exact host permit and isolated coordinator root')
            system_permit.validate()
        if legacy_queue_conflict(self.root):
            raise ValueError('Existing legacy/production queue must not be repurposed as a v2 workspace')
        if video_evidence_root is not None:
            video_evidence_root = local_root(video_evidence_root)
            if video_evidence_root.is_relative_to(self.root) or self.root.is_relative_to(video_evidence_root):
                raise ValueError('Host video evidence must be separate from model-writable run storage')
        self.store = ArtifactStore(self.root)
        self.video_evidence = VideoEvidence(video_evidence_root, self.store, clock)
        # Gate is ready before Coordinator recovery can inspect persisted completions.
        self.result_gate = V2ResultGate(self.root, self.store, self.video_evidence)
        self.result_gate.local_pilot = local_pilot
        self.result_gate.system_permit = system_permit
        self.queue = Coordinator(self.root, adapters=adapters, result_gate=self.result_gate)
        try:
            with Registry(self.root) as registry:
                registry.db.execute('''CREATE TABLE IF NOT EXISTS ai_v2_runs (
                    run_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
                    brief TEXT NOT NULL, mode TEXT NOT NULL, created REAL NOT NULL)''')
                registry.db.execute('''CREATE TABLE IF NOT EXISTS ai_v2_control (
                    singleton INTEGER PRIMARY KEY CHECK(singleton=1), active_run_id TEXT)''')
                registry.db.execute('INSERT OR IGNORE INTO ai_v2_control VALUES(1,NULL)')
                registry.db.execute('''CREATE TABLE IF NOT EXISTS ai_v2_capabilities (
                    capability_key TEXT PRIMARY KEY, evidence TEXT NOT NULL)''')
                registry.db.execute('''CREATE TABLE IF NOT EXISTS ai_v2_capability_revocations (
                    evidence_id TEXT PRIMARY KEY, reason TEXT NOT NULL)''')
                registry.db.execute('''CREATE TABLE IF NOT EXISTS ai_v2_delivery_revocations (
                    delivery_id TEXT PRIMARY KEY, reason TEXT NOT NULL)''')
                with registry.transaction():
                    self._refresh_active(registry)
            self.queue.set_rotation(False)
            self.queue.set_role('writer', provider='gemini')
            self.queue.set_role('director', provider='codex')
            self.queue.set_role('critic', provider='gemini')
        except BaseException:
            self.queue.close()
            raise

    @run_locked
    def create(self, brief, key):
        brief = Brief.model_validate(brief)
        if brief.production_mode == 'fake' and any(not getattr(a, 'is_simulated', False) for a in self.queue.adapters.values()):
            raise ValueError('Fake mode cannot dispatch live/local production adapters')
        # Identifier validation occurs before any write or path construction.
        Task(task_id=key, run_id=key, role='python', capability='create', input_version=1,
             input_hashes={}, depends_on=[], assigned_worker='python', provider_profile_id='local',
             expected_output_schema='note', idempotency_key=key)
        run_id = 'run_' + hashlib.sha256(key.encode()).hexdigest()[:24]
        clean = sanitize(brief.model_dump())
        if clean.get('additional_requirements') is None:
            clean.pop('additional_requirements',None)  # Preserve hashes/idempotency of historical briefs.
        request_hash = digest(clean)
        with Registry(self.root) as registry, registry.transaction():
            active = self._refresh_active(registry)
            prior = registry.db.execute('SELECT request_hash FROM ai_v2_runs WHERE run_id=?', (run_id,)).fetchone()
            if prior and prior[0] != request_hash:
                raise StudioError('IDEMPOTENCY_CONFLICT', 'Run key already refers to another brief')
            if active and active != run_id and not prior:
                raise StudioError('ACTIVE_RUN_EXISTS', f'Unfinished run already owns this root: {active}')
            if prior:
                # A terminal historical run is readable/idempotent but never reconciled
                # into the queue controlled by a newer run.
                existing = self._state(registry, run_id)
                if existing['status'] in TERMINAL:
                    return existing
            registry.db.execute('INSERT OR IGNORE INTO ai_v2_runs VALUES(?,?,?,?,?)',
                                (run_id, request_hash, canonical_json(clean), brief.production_mode, time.time()))
            if not active:
                registry.db.execute('UPDATE ai_v2_control SET active_run_id=? WHERE singleton=1', (run_id,))
        if brief.production_mode == 'fake':
            # Reconcile an interrupted creation deterministically; enqueue itself is idempotent.
            previous = []
            for stage, role in STAGES:
                task_id = run_id + '_' + stage.lower()
                contract = Task(task_id=task_id, run_id=run_id, scene_id='scene_001', role=role,
                                capability=stage.lower(), input_version=1, input_hashes={'brief': request_hash},
                                depends_on=previous, assigned_worker='fake_' + role,
                                provider_profile_id='simulation', requested_model=None,
                                expected_output_schema=SCHEMAS.get(stage, 'note'), idempotency_key=task_id)
                context = {'project_id': run_id, 'scene_id': 'scene_001', 'scene_version': 1,
                           'v2_contract': contract.model_dump(), 'brief': clean,
                           'constraints': {'simulated': True, 'no_production': True, 'no_account_rotation': True}}
                self._context_pack(contract, context)
                self.queue.enqueue({'task_id': task_id, 'project_id': run_id,
                                    'role': {'gemini_story': 'writer', 'gemini_visual': 'critic'}.get(role, 'director'),
                                    'expected_output': contract.expected_output_schema,
                                    'depends_on': previous, 'input_versions': {'brief': request_hash},
                                    'context': context, 'max_attempts': 2})
                previous = [task_id]
        return self.status(run_id)

    def _context_pack(self, task, context):
        base = f'runs/{task.run_id}/context/{task.task_id}'
        values = {'constraints.json': context['constraints'], 'accepted_state.json': {},
                  'relevant_files.json': [], 'input_artifacts.json': [], 'previous_errors.json': [],
                  'expected_output.schema.json': __import__('app.ai_cli_runtime', fromlist=['output_schema']).output_schema(task.expected_output_schema)}
        text = '# Task\n' + canonical_json(task.model_dump()) + '\nBrief: ' + canonical_json(context['brief'])
        if len(text.encode()) > 12000:
            raise ValueError('Context budget exceeded')
        for name, value in values.items():
            p = workspace_path(self.root, base + '/' + name, for_write=True)
            if p.exists():
                if json.loads(p.read_text(encoding='utf-8')) != value: raise ValueError('Context pack conflict')
            else: write_generated_json(self.root, base + '/' + name, value)
        for name, value in {'task.md': text, 'decisions.md': 'Simulation; no creative decision or source analysis claimed.\n'}.items():
            p = workspace_path(self.root, base + '/' + name, for_write=True)
            if p.exists():
                if p.read_text(encoding='utf-8') != value: raise ValueError('Context text conflict')
            else:
                p.parent.mkdir(parents=True, exist_ok=True)
                with p.open('x', encoding='utf-8') as stream: stream.write(value)

    def _state(self, registry, run_id):
        row = registry.db.execute('SELECT * FROM ai_v2_runs WHERE run_id=?', (run_id,)).fetchone()
        if not row: raise StudioError('RUN_NOT_FOUND', 'Unknown run')
        tasks = {t['id']: self.queue._task(t) for t in registry.db.execute(
            'SELECT * FROM ai_tasks WHERE project_id=? ORDER BY created,id', (run_id,))}
        normalized = [{'task_id': t['task_id'], 'status': task_status(t, tasks),
                       'attempt': t['attempt'], 'error': t.get('error'), 'simulated': True,
                       'result_ref': t['result']['path'] if t.get('result') else None} for t in tasks.values()]
        stage_ids = {run_id+'_'+stage.lower() for stage,_ in STAGES}
        done = stage_ids.issubset(tasks) and all(t['status'] == 'SUCCEEDED' for t in normalized)
        failure = any(t['status'] in {'FAILED_FINAL', 'STALE'} for t in normalized)
        return {'run_id': run_id, 'mode': row['mode'], 'status': 'SIMULATION_SUCCEEDED' if done else
                'BLOCKED_UNSUPPORTED' if row['mode'] == 'production' else
                'STALE' if any(t['status']=='STALE' for t in normalized) else 'FAILED_FINAL' if failure else
                'WAITING_QUOTA' if any(t['status']=='WAITING_QUOTA' for t in normalized) else
                'WAITING_AUTH' if any(t['status']=='WAITING_AUTH' for t in normalized) else
                'FAILED_RETRYABLE' if any(t['status']=='FAILED_RETRYABLE' for t in normalized) else 'IN_PROGRESS',
                'simulated': row['mode'] == 'fake', 'ready_for_user': False,
                'tasks': normalized, 'blockers': BLOCKERS if row['mode'] == 'production' else [],
                'model_requests': 0, 'tokens': None, 'video_qa': 'NOT_RUN', 'editorial_qa': 'NOT_RUN'}

    def _refresh_active(self, registry):
        """Persist ownership and retire terminal queue tails, without another scheduler."""
        unfinished = []
        for row in registry.db.execute('SELECT run_id FROM ai_v2_runs ORDER BY created').fetchall():
            run_id = row['run_id']
            live = registry.db.execute("SELECT 1 FROM ai_attempts a JOIN ai_tasks t ON t.id=a.task_id "
                "WHERE t.project_id=? AND a.status IN ('running','stopping') LIMIT 1", (run_id,)).fetchone()
            if self._state(registry, run_id)['status'] not in TERMINAL or live:
                unfinished.append(run_id)
            else:
                # Global legacy resume must never reactivate a retired run's tails.
                registry.db.execute("UPDATE ai_tasks SET status='blocked' WHERE project_id=? "
                    "AND status IN ('pending','waiting_access','interrupted')", (run_id,))
        if len(unfinished) > 1:
            raise StudioError('ACTIVE_RUN_CONFLICT', 'Multiple unfinished historical v2 runs require explicit reconciliation')
        active = unfinished[0] if unfinished else None
        registry.db.execute('UPDATE ai_v2_control SET active_run_id=? WHERE singleton=1', (active,))
        return active

    @run_locked
    def status(self, run_id):
        with Registry(self.root) as registry, registry.transaction():
            result = self._state(registry, run_id)
            self._refresh_active(registry)
            return result

    def _require_owner(self, run_id):
        with Registry(self.root) as registry, registry.transaction():
            state = self._state(registry, run_id)
            active = self._refresh_active(registry)
            if active and active != run_id:
                raise StudioError('RUN_NOT_ACTIVE', 'This run cannot control another run\'s queue')
            return state

    @run_locked
    def advance(self, run_id, ticks=1):
        if not 1 <= ticks <= 100: raise ValueError('ticks must be 1..100')
        initial = self._require_owner(run_id)
        if initial['status'] in TERMINAL: return initial
        # Explicit resume is the only restart path; no automatic quota rotation.
        self.queue.resume()
        for _ in range(ticks): self.queue.tick()
        return self.status(run_id)

    @run_locked
    def pause(self, run_id):
        state = self._require_owner(run_id)
        if state['status'] not in TERMINAL: self.queue.pause()
        return self.status(run_id)

    @run_locked
    def stop(self, run_id):
        state = self._require_owner(run_id)
        if state['status'] not in TERMINAL: self.queue.stop()
        return self.status(run_id)

    @run_locked
    def accept_result(self, value):
        """Host API: use exactly the same gate and queue CAS as automatic completion."""
        result = Result.model_validate(value)
        with Registry(self.root) as registry, registry.transaction():
            row = registry.db.execute('SELECT * FROM ai_tasks WHERE id=?', (result.task_id,)).fetchone()
            if not row:
                raise StudioError('TASK_NOT_FOUND', 'Result task is not registered')
            task = self.queue._task(row)
            attempt = registry.db.execute('SELECT * FROM ai_attempts WHERE id=?', (result.acceptance_key,)).fetchone()
            if not attempt:
                raise StudioError('RESULT_NOT_CURRENT', 'Unknown acceptance attempt')
            if (task.get('result') or {}).get('v2_acceptance'):
                return self.result_gate(registry, row, task, attempt, result.payload, result.model_dump())['result_metadata']['v2_acceptance']
            if not self.queue._complete(registry, attempt, result.payload, result.model_dump()):
                raise StudioError('RESULT_NOT_CURRENT', 'Queue rejected result CAS')
            return json.loads(registry.db.execute('SELECT result FROM ai_tasks WHERE id=?', (result.task_id,)).fetchone()[0])['v2_acceptance']

    def record_video_probe(self, evidence):
        """Trusted host API only; no model-output path calls this method."""
        with Registry(self.root) as registry, registry.transaction():
            return self.video_evidence.record(registry, evidence)

    @run_locked
    def accepted_artifacts(self, task_id):
        with Registry(self.root) as registry:
            row = registry.db.execute('SELECT * FROM ai_tasks WHERE id=?', (task_id,)).fetchone()
            return self.result_gate.accepted_artifacts(registry, row)

    def revoke_video_probe(self, evidence_id, reason):
        """Append-only host revocation; the original evidence stays immutable."""
        if not reason:
            raise ValueError('Revocation reason is required')
        with Registry(self.root) as registry, registry.transaction():
            registry.db.execute('INSERT OR IGNORE INTO ai_v2_capability_revocations VALUES(?,?)',
                                (evidence_id, reason))

    def accept_review(self, value, *, evidence_id=None, video_artifact=None, **binding):
        """Read-only compatibility helper. Never accepts a result or completes a task."""
        review = Review.model_validate(value)
        if review.subject_type == 'render' and (review.evidence_mode == 'video_audio' or review.verdict == 'ready_for_review'):
            with Registry(self.root) as registry:
                self.video_evidence.accept(registry, evidence_id, binding, video_artifact,
                                           [video_artifact.path] if hasattr(video_artifact, 'path') else [])
            if review.render_hash != video_artifact.sha256 or review.render_id != video_artifact.artifact_id:
                raise StudioError('VIDEO_CAPABILITY_UNVERIFIED', 'Review refers to another video artifact')
        return review

    def revoke_video_delivery(self, delivery_id, reason):
        if not reason:
            raise ValueError('Revocation reason is required')
        with Registry(self.root) as registry, registry.transaction():
            registry.db.execute('INSERT OR IGNORE INTO ai_v2_delivery_revocations VALUES(?,?)', (delivery_id, reason))

    @run_locked
    def manifest(self, run_id):
        state = self.status(run_id)
        return self.store.put_json(run_id, state, simulated=state['simulated']).model_dump()

    def close(self): self.queue.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def main(argv=None):
    # SYSTEM-01 extends this entrypoint; historical commands keep their protocol.
    arguments = list(argv) if argv is not None else __import__('sys').argv[1:]
    if any(a == '--config' or a.startswith('--config=') for a in arguments):
        from .v2_pipeline import system_main
        return system_main(arguments)
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=os.environ.get('LOCAL_RUN_ROOT'))
    subs = parser.add_subparsers(dest='command', required=True)
    create = subs.add_parser('create'); create.add_argument('--brief', required=True); create.add_argument('--key', required=True)
    prepare = subs.add_parser('prepare'); prepare.add_argument('--brief',required=True); prepare.add_argument('--key',required=True)
    prepare.add_argument('--inputs'); prepare.add_argument('--fixture-demo',action='store_true')
    for command in ('resume','inspect-artifacts','report'):
        sub=subs.add_parser(command);sub.add_argument('--run-id',required=True)
    for command in ('status', 'advance', 'pause', 'stop', 'manifest'):
        sub = subs.add_parser(command); sub.add_argument('--run-id', required=True)
        if command == 'advance': sub.add_argument('--ticks', type=int, default=30)
    args = parser.parse_args(argv)
    try:
        if not args.root: raise ValueError('Set LOCAL_RUN_ROOT to a dedicated local folder outside sync')
        with RunCoordinator(args.root) as coordinator:
            if args.command == 'create':
                result = coordinator.create(json.loads(Path(args.brief).read_text(encoding='utf-8-sig')), args.key)
            elif args.command in {'prepare','resume','inspect-artifacts','report'}:
                from .v2_offline import prepare_offline, resume_offline, inspect_offline
                if args.command=='prepare':
                    brief=json.loads(Path(args.brief).read_text(encoding='utf-8-sig'))
                    inputs=json.loads(Path(args.inputs).read_text(encoding='utf-8-sig')) if args.inputs else None
                    result=prepare_offline(coordinator,brief,args.key,inputs=inputs,fixture_demo=args.fixture_demo)
                elif args.command=='resume':result=resume_offline(coordinator,args.run_id)
                else:result=inspect_offline(coordinator,args.run_id)
            else:
                result = coordinator.status(args.run_id)
                if args.command == 'status':
                    from .v2_offline import inspect_offline
                    result=inspect_offline(coordinator,args.run_id)
                elif args.command == 'advance': result = coordinator.advance(args.run_id, args.ticks)
                elif args.command in {'pause', 'stop'}:
                    result = getattr(coordinator, args.command)(args.run_id)
                elif args.command == 'manifest': result = {**result, 'artifact': coordinator.manifest(args.run_id)}
        print(json.dumps(sanitize(result), ensure_ascii=False, allow_nan=False))
        return EXIT_CODES.get(result.get('status'), EXIT_CODES['ERROR'])
    except (ValueError, OSError, StudioError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'ERROR', 'error_code': getattr(exc, 'code', 'ERROR'), 'error': sanitize(str(exc))}, ensure_ascii=False))
        return EXIT_CODES['ERROR']


if __name__ == '__main__': raise SystemExit(main())
