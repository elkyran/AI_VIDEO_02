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
from .registry import Registry, StudioError, canonical_json, workspace_path, write_generated_json
from .v2_contracts import Brief, Task, Review, TrustedVideoProbe
from .v2_storage import ArtifactStore

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
BLOCKERS = ['gemini_story: installed authenticated agy CLI and JSON smoke required',
            'gemini_visual: independent identity, billing, media probes required',
            'production_import: approved Scene-to-existing-script bridge required',
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
    def __init__(self, root, *, adapters=None):
        self.root = local_root(root)
        if legacy_queue_conflict(self.root):
            raise ValueError('Existing legacy/production queue must not be repurposed as a v2 workspace')
        self.queue = Coordinator(self.root, adapters=adapters)
        self.store = ArtifactStore(self.root)
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
        done = len(tasks) == len(STAGES) and all(t['status'] == 'SUCCEEDED' for t in normalized)
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

    def record_video_probe(self, evidence):
        """Trusted host API only; no model-output path calls this method."""
        probe = TrustedVideoProbe.model_validate(evidence)
        key = digest([probe.provider, probe.client_version, probe.model, probe.control_video_hash])
        with Registry(self.root) as registry, registry.transaction():
            registry.db.execute('INSERT OR REPLACE INTO ai_v2_capabilities VALUES(?,?)',
                                (key, canonical_json(probe.model_dump())))
        return key

    def accept_review(self, value, *, provider, client_version, model, control_video_hash):
        review = Review.model_validate(value)
        if review.review_mode == 'full_video':
            key = digest([provider, client_version, model, control_video_hash])
            with Registry(self.root) as registry:
                row = registry.db.execute('SELECT evidence FROM ai_v2_capabilities WHERE capability_key=?', (key,)).fetchone()
            if not row or TrustedVideoProbe.model_validate_json(row[0]).status != 'VERIFIED':
                raise StudioError('VIDEO_CAPABILITY_UNVERIFIED', 'Full video requires matching trusted control-video evidence')
        return review

    @run_locked
    def manifest(self, run_id):
        state = self.status(run_id)
        return self.store.put_json(run_id, state, simulated=state['simulated']).model_dump()

    def close(self): self.queue.close()
    def __enter__(self): return self
    def __exit__(self, *_): self.close()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--root', default=os.environ.get('LOCAL_RUN_ROOT'))
    subs = parser.add_subparsers(dest='command', required=True)
    create = subs.add_parser('create'); create.add_argument('--brief', required=True); create.add_argument('--key', required=True)
    for command in ('status', 'advance', 'pause', 'stop', 'manifest'):
        sub = subs.add_parser(command); sub.add_argument('--run-id', required=True)
        if command == 'advance': sub.add_argument('--ticks', type=int, default=30)
    args = parser.parse_args(argv)
    try:
        if not args.root: raise ValueError('Set LOCAL_RUN_ROOT to a dedicated local folder outside sync')
        with RunCoordinator(args.root) as coordinator:
            if args.command == 'create':
                result = coordinator.create(json.loads(Path(args.brief).read_text(encoding='utf-8-sig')), args.key)
            else:
                result = coordinator.status(args.run_id)
                if args.command == 'advance': result = coordinator.advance(args.run_id, args.ticks)
                elif args.command in {'pause', 'stop'}:
                    result = getattr(coordinator, args.command)(args.run_id)
                elif args.command == 'manifest': result = {**result, 'artifact': coordinator.manifest(args.run_id)}
        print(json.dumps(sanitize(result), ensure_ascii=False, allow_nan=False))
        return EXIT_CODES.get(result.get('status'), EXIT_CODES['ERROR'])
    except (ValueError, OSError, StudioError, sqlite3.Error) as exc:
        print(json.dumps({'status': 'ERROR', 'error_code': getattr(exc, 'code', 'ERROR'), 'error': sanitize(str(exc))}, ensure_ascii=False))
        return EXIT_CODES['ERROR']


if __name__ == '__main__': raise SystemExit(main())
