"""A short native fixture adapter, serialized by the coordinator's local-heavy slot.

start only reserves a handle; poll launches after the durable attempt commits.
Only our child process tree may be stopped. This never invokes a provider or TTS.
"""
from __future__ import annotations

import argparse
import ctypes
import hashlib
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import uuid

from .development import atomic_write, locked
from .registry import workspace_path


def process_identity(pid):
    """Return running process creation identity, False if gone, None if unknowable."""
    if os.name == 'nt':
        from ctypes import wintypes
        kernel = ctypes.WinDLL('kernel32', use_last_error=True)
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
        kernel.GetProcessTimes.argtypes = [wintypes.HANDLE, *([ctypes.POINTER(wintypes.FILETIME)]*4)]
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        handle = kernel.OpenProcess(0x1000, False, int(pid))
        if not handle:
            return False if ctypes.get_last_error() == 87 else None
        try:
            exit_code = wintypes.DWORD()
            if not kernel.GetExitCodeProcess(handle, ctypes.byref(exit_code)):
                return None
            if exit_code.value != 259:
                return False
            times = [wintypes.FILETIME() for _ in range(4)]
            if not kernel.GetProcessTimes(handle, *(ctypes.byref(item) for item in times)):
                return None
            return str((times[0].dwHighDateTime << 32) | times[0].dwLowDateTime)
        finally:
            kernel.CloseHandle(handle)
    try:
        fields = Path(f'/proc/{int(pid)}/stat').read_text().split(') ',1)[1].split()
        return False if fields[0] == 'Z' else fields[19]
    except FileNotFoundError:
        return False
    except (OSError, IndexError):
        return None


def _write(path, value):
    atomic_write(Path(path), json.dumps(value,ensure_ascii=False,indent=2)+'\n')


class PreviewAdapter:
    is_simulated = False
    is_local = True

    def __init__(self, workspace):
        self.workspace = Path(workspace).resolve()
        self.runs = {}

    def check_auth(self, profile):
        return {'status':'ready','is_local':True,'provider_requests':0}

    def start(self, task, profile, attempt_dir=None):
        if task.get('kind') != 'render_fixture' or not attempt_dir:
            raise ValueError('Local adapter only supports an explicit render_fixture task')
        folder = Path(attempt_dir).resolve()
        if not folder.is_relative_to(self.workspace/'ai'/'attempts'):
            raise ValueError('Attempt directory is outside the coordinator workspace')
        plan_task = task.get('context',{}).get('plan_task_id')
        if plan_task:
            plan=task['context'].get('dependency_results',{}).get(plan_task)
            if not isinstance(plan,dict):
                raise ValueError('The requested director plan is not an accepted dependency')
            _write(folder/'selected_plan.json',plan)
        handle = uuid.uuid4().hex
        self.runs[handle]={'folder':folder,'process':None,'cancelled':False,'terminal':False,'log':None}
        return handle

    def _command(self, folder):
        return [sys.executable,'-X','utf8','-m','app.multi_ai_preview','--worker',str(folder),'--workspace',str(self.workspace)]

    def poll(self, handle):
        run = self.runs[handle]
        if run['terminal']:
            return []
        process = run['process']
        if process is None:
            if run['cancelled']:
                run['terminal']=True
                return [self._failed('interrupted','Stopped before local launch')]
            run['log']=(run['folder']/'native.log').open('wb')
            options={'creationflags':subprocess.CREATE_NO_WINDOW} if os.name=='nt' else {'start_new_session':True}
            process=subprocess.Popen(self._command(run['folder']),cwd=Path(__file__).resolve().parents[1],
                                     stdout=run['log'],stderr=subprocess.STDOUT,stdin=subprocess.DEVNULL,**options)
            run['process']=process
            _write(run['folder']/'native_process.json',{'pid':process.pid,'identity':process_identity(process.pid)})
            return [{'kind':'run_started','payload':{'is_local':True,'synthetic_input':True,'pid':process.pid}}]
        code=process.poll()
        if code is None:
            return []
        run['log'].close()
        run['terminal']=True
        if run['cancelled']:
            return [self._failed('interrupted','Managed local process tree stopped')]
        result=run['folder']/'native_result.json'
        if code!=0 or not result.is_file():
            return [self._failed('unknown',f'Local fixture failed (exit {code}); see native.log')]
        return [{'kind':'completed','result':json.loads(result.read_text(encoding='utf-8')),
                 'payload':{'is_local':True,'synthetic_input':True}}]

    @staticmethod
    def _failed(kind,message):
        return {'kind':'failed','failure':{'kind':kind,'message':message,'retryable':False},'payload':{'message':message}}

    def cancel(self, handle):
        run=self.runs.get(handle)
        if not run:
            return {'status':'unknown'}
        run['cancelled']=True
        process=run['process']
        if process is None or process.poll() is not None:
            return {'status':'stopped'}
        # Popen still owns this PID; never kill a PID recovered from untrusted text.
        if os.name=='nt':
            stopped=subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.PIPE,
                                   stderr=subprocess.STDOUT,timeout=10,creationflags=subprocess.CREATE_NO_WINDOW)
            with (run['folder']/'cancel.log').open('ab') as stream:
                stream.write(stopped.stdout)
        else:
            os.killpg(process.pid,signal.SIGTERM)
        return {'status':'stopping' if process.poll() is None else 'stopped'}

    def recover(self, attempt_dir):
        folder=Path(attempt_dir)
        identity_path=folder/'native_process.json'
        if not identity_path.is_file():
            # Crash in the launch/identity gap cannot prove there is no child.
            return {'status':'unknown'}
        saved=json.loads(identity_path.read_text(encoding='utf-8'))
        identity=process_identity(saved['pid'])
        if identity is None or saved.get('identity') in (None,False):
            return {'status':'unknown'}
        if identity==saved['identity']:
            return {'status':'running'}
        path=folder/'native_result.json'
        if path.is_file():
            return {'status':'completed','result':json.loads(path.read_text(encoding='utf-8'))}
        return {'status':'interrupted'}


def enqueue_preview(coordinator, plan_task_id=None):
    snapshot=coordinator.snapshot()
    for task in snapshot['tasks']:
        if task['kind']=='render_fixture' and task['status'] in {'pending','running'}:
            return task
    plans=[t for t in snapshot['tasks'] if t['expected_output']=='edit_plan' and t['status']=='completed'
           and (plan_task_id is None or t['task_id']==plan_task_id)]
    if plan_task_id and not plans:
        raise ValueError('Choose a completed current director plan')
    plan=plans[-1] if plans else None
    context={'notice':'Generated colors and tone; technical test only.'}
    if plan:
        context['plan_task_id']=plan['task_id']
        context['scene_id']=plan['context'].get('scene_id','scene_001')
    task=coordinator.enqueue({'task_id':'preview_'+uuid.uuid4().hex[:12],
                              'project_id':plan['project_id'] if plan else 'technical_fixture',
                              'kind':'render_fixture','role':'director','resource_class':'local_heavy',
                              'expected_output':'note','max_attempts':1,
                              'depends_on':[plan['task_id']] if plan else [],
                              'input_versions':plan['input_versions'] if plan else {},'context':context})
    coordinator.resume()
    return task


def _worker(folder, workspace):
    folder=folder.resolve();workspace=workspace.resolve()
    if not folder.is_relative_to(workspace/'ai'/'attempts'):
        raise ValueError('Invalid local worker directory')
    from scripts.check_multi_ai_render import prepare_preview, run_smoke
    # A second child can never overlap heavy execution even if an owner crashes.
    with locked(workspace/'ai'/'native-render.lock'):
        selected=folder/'selected_plan.json'
        if selected.is_file():
            report=prepare_preview(folder/'technical',json.loads(selected.read_text(encoding='utf-8')))
            report.update(status='rendered',measurements=None,
                          checks={'actual_director_plan_compiled':True,'renderer_completed':True},
                          creative_quality=None)
            _write(Path(report['preview_path']).with_name('smoke.json'),report)
        else:
            report=run_smoke(folder/'technical')
            if report['status']!='passed':
                raise RuntimeError('Technical preview checks failed; see smoke.json')
        preview=Path(report['preview_path']).resolve()
        project=Path(report['rendered']['project']).resolve()
        checks=preview.with_name('smoke.json')
        payload={'technical_preview':True,'native_execution':report['status'],'creative_quality':None,
                 'preview_ref':preview.relative_to(workspace).as_posix(),
                 'mlt_ref':project.relative_to(workspace).as_posix(),
                 'checks_ref':checks.relative_to(workspace).as_posix(),
                 'preview_sha256':hashlib.sha256(preview.read_bytes()).hexdigest(),
                 'mlt_sha256':hashlib.sha256(project.read_bytes()).hexdigest(),
                 'checks_sha256':hashlib.sha256(checks.read_bytes()).hexdigest(),
                 'measurements':report['measurements']}
        _write(folder/'native_result.json',{'schema_version':1,'simulated':True,'text':json.dumps(payload,ensure_ascii=False)})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--worker',type=Path,required=True)
    parser.add_argument('--workspace',type=Path,required=True)
    args=parser.parse_args(argv)
    _worker(args.worker,args.workspace)
    return 0


if __name__=='__main__':
    raise SystemExit(main())
