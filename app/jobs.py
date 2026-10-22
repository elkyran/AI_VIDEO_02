"""Persistent, bounded local job queue. Workers survive client disconnection."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time
import uuid

from .registry import Registry, StudioError, canonical_json


def process_alive(pid: int | None) -> bool:
    if not pid: return False
    if os.name == 'nt':
        import ctypes
        kernel = ctypes.WinDLL('kernel32',use_last_error=True)
        kernel.OpenProcess.argtypes = [ctypes.c_uint32,ctypes.c_int,ctypes.c_uint32]
        kernel.OpenProcess.restype = ctypes.c_void_p
        kernel.GetExitCodeProcess.argtypes = [ctypes.c_void_p,ctypes.POINTER(ctypes.c_uint32)]
        kernel.CloseHandle.argtypes = [ctypes.c_void_p]
        handle = kernel.OpenProcess(0x1000,False,pid)
        if not handle: return ctypes.get_last_error()==5
        try:
            code=ctypes.c_uint32()
            return bool(kernel.GetExitCodeProcess(handle,ctypes.byref(code))) and code.value==259
        finally: kernel.CloseHandle(handle)
    try: os.kill(pid,0)
    except ProcessLookupError: return False
    except PermissionError: return True
    return True


class JobManager:
    def __init__(self, root: str | Path, *, spawn_observer=None):
        self.spawn_observer = spawn_observer
        self.root = Path(root).resolve()
        with Registry(self.root) as registry:
            registry.db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                    id TEXT PRIMARY KEY, project_id TEXT NOT NULL REFERENCES projects(id),
                    operation TEXT NOT NULL, parameters TEXT NOT NULL, state TEXT NOT NULL,
                    progress REAL NOT NULL DEFAULT 0, message TEXT NOT NULL DEFAULT '',
                    result TEXT, error TEXT, created REAL NOT NULL, started REAL, finished REAL,
                    worker_pid INTEGER, cancel_requested INTEGER NOT NULL DEFAULT 0,
                    attempts INTEGER NOT NULL DEFAULT 0, input_revision INTEGER NOT NULL,
                    invalidated INTEGER NOT NULL DEFAULT 0);
            ''')
            # Preserve invalidation across worker exits and upgrades of existing workspaces.
            with registry.transaction():
                columns = {row['name'] for row in registry.db.execute('PRAGMA table_info(jobs)')}
                if 'invalidated' not in columns:
                    registry.db.execute('ALTER TABLE jobs ADD COLUMN invalidated INTEGER NOT NULL DEFAULT 0')

    def enqueue(self, project_id: str, operation: str, parameters: dict,
                base_revision: int, idempotency_key: str, start: bool = True) -> dict:
        from .operations import validate_request
        with Registry(self.root) as registry:
            # An accepted request keeps its original response even if later edits
            # invalidate its dependencies. _change still verifies the full hash.
            repeated = registry.db.execute('SELECT 1 FROM requests WHERE project_id=? AND key=?',
                                           (project_id, idempotency_key)).fetchone()
            if not repeated:
                parameters = validate_request(registry, project_id, operation, parameters)
            request = {'op':'enqueue','operation':operation,'parameters':parameters,
                       'base_revision':base_revision}
            def mutate(new_revision):
                identifier = 'job_' + uuid.uuid4().hex[:20]
                registry.db.execute('INSERT INTO jobs(id,project_id,operation,parameters,state,created,input_revision) VALUES(?,?,?,?,?,?,?)',
                    (identifier,project_id,operation,canonical_json(parameters),'pending',time.time(),new_revision))
                return {'job_id':identifier,'state':'pending'}, True
            answer = registry._change(project_id,base_revision,idempotency_key,request,mutate)
        if start and self.status(answer['job_id'])['state'] == 'pending':
            self._spawn(answer['job_id'])
        return answer

    def _spawn(self, job_id: str) -> None:
        # Only this installed worker is executable. Model responses cannot choose code.
        if self.spawn_observer is not None:
            self.spawn_observer('intent', job_id, None)
        directory = self.root/'jobs'/job_id
        directory.mkdir(parents=True, exist_ok=True)
        with (directory/'worker.log').open('a',encoding='utf-8') as log:
            flags = (subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP) if os.name == 'nt' else 0
            process = subprocess.Popen([sys.executable,'-m','app.worker','--workspace',str(self.root),'--job',job_id],
                cwd=Path(__file__).resolve().parents[1], stdout=log,stderr=log,stdin=subprocess.DEVNULL,
                close_fds=True,creationflags=flags,start_new_session=(os.name!='nt'))
        if self.spawn_observer is not None:
            self.spawn_observer('spawned', job_id, process)

    def status(self, job_id: str) -> dict:
        with Registry(self.root) as registry:
            row=registry.db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
            if row is None:
                raise StudioError('JOB_NOT_FOUND','Unknown job ID')
            answer=dict(row)
            for key in ('parameters','result','error'):
                if answer[key]: answer[key]=json.loads(answer[key])
            answer['job_id']=answer.pop('id')
            return answer

    def list(self, project_id: str) -> list[dict]:
        with Registry(self.root) as registry:
            registry._project(project_id)
            return [dict(row) for row in registry.db.execute(
                'SELECT id AS job_id,operation,state,progress,message FROM jobs WHERE project_id=? ORDER BY created', (project_id,))]

    def cancel(self, job_id: str) -> dict:
        job=self.status(job_id)
        if job['state'] not in ('pending','running'):
            raise StudioError('JOB_STATE_INVALID','Only pending or running work can be cancelled')
        with Registry(self.root) as registry:
            registry.db.execute("UPDATE jobs SET cancel_requested=1,state=CASE WHEN state='pending' THEN 'blocked' ELSE state END,message='Cancellation requested' WHERE id=?",(job_id,))
        return self.status(job_id)

    def resume(self, job_id: str) -> dict:
        job=self.status(job_id)
        if job['invalidated']:
            if job['state']=='running' and not process_alive(job['worker_pid']):
                # An invalidated worker may have exited before finalization. Free
                # its queue slot without ever restarting the obsolete inputs.
                with Registry(self.root) as registry:
                    registry.db.execute("UPDATE jobs SET state='stale',finished=?,message='Invalidated worker exited; create a new job' WHERE id=? AND invalidated=1 AND state='running'",(time.time(),job_id))
            raise StudioError('JOB_STALE','Inputs changed; create a new job from the current script instead of resuming this job')
        if job['state']=='running':
            if process_alive(job['worker_pid']):
                raise StudioError('JOB_RUNNING','The original worker is still running; inspect its status')
        elif job['state'] not in ('failed','blocked'):
            raise StudioError('JOB_STATE_INVALID','Only interrupted or failed work can resume')
        if job['attempts']>=3:
            raise StudioError('RETRY_LIMIT','Two retries exhausted; review the error before a new request')
        with Registry(self.root) as registry:
            changed = registry.db.execute("UPDATE jobs SET state='pending',cancel_requested=0,error=NULL,worker_pid=NULL,message='Resuming saved work' WHERE id=? AND invalidated=0",(job_id,))
            if not changed.rowcount:
                raise StudioError('JOB_STALE','Inputs changed while resuming; create a new job from the current script')
        self._spawn(job_id)
        return self.status(job_id)

    def update(self, job_id: str, progress: float, message: str) -> None:
        with Registry(self.root) as registry:
            registry.db.execute('UPDATE jobs SET progress=?,message=? WHERE id=?',(max(0,min(1,progress)),message,job_id))

    def work(self, job_id: str) -> None:
        from .operations import execute
        with Registry(self.root) as registry:
            with registry.transaction():
                job=registry.db.execute('SELECT * FROM jobs WHERE id=?',(job_id,)).fetchone()
                if not job or job['state']!='pending' or job['invalidated']: return
                # SQLite serializes the claim across all simultaneous detached workers.
                if registry.db.execute("SELECT 1 FROM jobs WHERE state='running' LIMIT 1").fetchone(): return
                registry.db.execute("UPDATE jobs SET state='running',started=?,worker_pid=?,attempts=attempts+1 WHERE id=?",(time.time(),os.getpid(),job_id))
        try:
            result=execute(self.root,job_id,job['operation'],json.loads(job['parameters']))
            if self.status(job_id)['cancel_requested']:
                raise StudioError('CANCELLED','Cancelled before publishing job completion; files preserved')
            state='done'
            error=None
        except Exception as exc:
            result=None
            state='blocked' if isinstance(exc,StudioError) and exc.code in ('CANCELLED','CAPABILITY_MISSING','SOURCE_INCOMPLETE','RESOURCE_LIMIT') else 'failed'
            error={'code':getattr(exc,'code','OPERATION_FAILED'),'message':str(exc)}
        with Registry(self.root) as registry:
            with registry.transaction():
                current=registry.db.execute('SELECT invalidated FROM jobs WHERE id=?',(job_id,)).fetchone()
                if current['invalidated']:
                    state='stale'
                    result=None
                    error={'code':'STALE_INPUTS','message':'Inputs changed during execution; create a new job from the current script'}
                registry.db.execute('UPDATE jobs SET state=?,result=?,error=?,finished=?,progress=?,message=? WHERE id=?',
                    (state,canonical_json(result) if result is not None else None,canonical_json(error) if error else None,
                     time.time(),1 if state=='done' else 0,'Completed' if state=='done' else error['message'],job_id))
                pending=registry.db.execute("SELECT id FROM jobs WHERE state='pending' AND invalidated=0 ORDER BY created LIMIT 1").fetchone()
        if pending: self._spawn(pending['id'])
