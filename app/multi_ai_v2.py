"""Run facade over the existing durable Coordinator, not another scheduler.

The initial executable slice is explicitly fake. Production stays blocked until
the Story, Visual, production-import and remote-storage boundaries are verified.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import time

from .ai_adapters import sanitize
from .multi_ai import Coordinator
from .registry import Registry, StudioError, canonical_json, workspace_path, write_generated_json
from .v2_contracts import Brief, Task
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


def digest(value):
    return hashlib.sha256(canonical_json(value).encode()).hexdigest()


def local_root(path):
    root = Path(path).resolve()
    forbidden = {'onedrive', 'googledrive', 'google drive', 'my drive', 'мой диск'}
    if any(any(name in part.casefold() for name in forbidden) for part in root.parts):
        raise ValueError('LOCAL_RUN_ROOT must be outside OneDrive/Google Drive sync folders')
    return root


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
        db = self.root / 'registry.sqlite'
        if db.exists():
            with closing(sqlite3.connect(db.as_uri() + '?mode=ro', uri=True)) as conn:
                tables = {row[0] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
                if 'ai_tasks' in tables and 'ai_v2_runs' not in tables and conn.execute('SELECT 1 FROM ai_tasks LIMIT 1').fetchone():
                    raise ValueError('Existing legacy queue must not be repurposed as a v2 workspace')
        self.queue = Coordinator(self.root, adapters=adapters)
        self.store = ArtifactStore(self.root)
        try:
            with Registry(self.root) as registry:
                registry.db.execute('''CREATE TABLE IF NOT EXISTS ai_v2_runs (
                    run_id TEXT PRIMARY KEY, request_hash TEXT NOT NULL,
                    brief TEXT NOT NULL, mode TEXT NOT NULL, created REAL NOT NULL)''')
            self.queue.set_rotation(False)
            self.queue.set_role('writer', provider='gemini')
            self.queue.set_role('director', provider='codex')
            self.queue.set_role('critic', provider='gemini')
        except BaseException:
            self.queue.close()
            raise

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
            prior = registry.db.execute('SELECT request_hash FROM ai_v2_runs WHERE run_id=?', (run_id,)).fetchone()
            if prior and prior[0] != request_hash:
                raise StudioError('IDEMPOTENCY_CONFLICT', 'Run key already refers to another brief')
            registry.db.execute('INSERT OR IGNORE INTO ai_v2_runs VALUES(?,?,?,?,?)',
                                (run_id, request_hash, canonical_json(clean), brief.production_mode, time.time()))
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

    def status(self, run_id):
        with Registry(self.root) as registry:
            row = registry.db.execute('SELECT * FROM ai_v2_runs WHERE run_id=?', (run_id,)).fetchone()
        if not row: raise ValueError('Unknown run')
        all_tasks = self.queue.snapshot()['tasks']
        tasks = {t['task_id']: t for t in all_tasks if t['project_id'] == run_id}
        normalized = [{'task_id': t['task_id'], 'status': task_status(t, tasks),
                       'attempt': t['attempt'], 'error': t.get('error'), 'simulated': True,
                       'result_ref': t['result']['path'] if t.get('result') else None} for t in tasks.values()]
        done = len(tasks) == len(STAGES) and all(t['status'] == 'SUCCEEDED' for t in normalized)
        failure = any(t['status'] in {'FAILED_FINAL', 'STALE'} for t in normalized)
        waiting = any(t['status'] in {'WAITING_QUOTA', 'WAITING_AUTH'} for t in normalized)
        return {'run_id': run_id, 'mode': row['mode'], 'status': 'SIMULATION_SUCCEEDED' if done else
                'BLOCKED_UNSUPPORTED' if row['mode'] == 'production' else 'FAILED_FINAL' if failure else
                'WAITING_ACCESS' if waiting else 'IN_PROGRESS',
                'simulated': row['mode'] == 'fake', 'ready_for_user': False,
                'tasks': normalized, 'blockers': BLOCKERS if row['mode'] == 'production' else [],
                'model_requests': 0, 'tokens': None, 'video_qa': 'NOT_RUN', 'editorial_qa': 'NOT_RUN'}

    def advance(self, run_id, ticks=1):
        if not 1 <= ticks <= 100: raise ValueError('ticks must be 1..100')
        initial = self.status(run_id)
        if initial['mode'] == 'production': return initial
        # Explicit resume is the only restart path; no automatic quota rotation.
        self.queue.resume()
        for _ in range(ticks): self.queue.tick()
        return self.status(run_id)

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
    if not args.root: parser.error('Set LOCAL_RUN_ROOT to a dedicated local folder outside sync')
    try:
        with RunCoordinator(args.root) as coordinator:
            if args.command == 'create':
                result = coordinator.create(json.loads(Path(args.brief).read_text(encoding='utf-8-sig')), args.key)
            else:
                result = coordinator.status(args.run_id)
                if args.command == 'advance': result = coordinator.advance(args.run_id, args.ticks)
                elif args.command in {'pause', 'stop'}:
                    getattr(coordinator.queue, args.command)()
                    result = coordinator.status(args.run_id)
                elif args.command == 'manifest': result = coordinator.manifest(args.run_id)
        print(json.dumps(sanitize(result), ensure_ascii=False, allow_nan=False))
        return 2 if result.get('status') == 'BLOCKED_UNSUPPORTED' else 0
    except (ValueError, OSError, StudioError) as exc:
        print(json.dumps({'status': 'ERROR', 'error': sanitize(str(exc))}, ensure_ascii=False))
        return 2


if __name__ == '__main__': raise SystemExit(main())
