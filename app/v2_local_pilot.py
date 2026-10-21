"""Explicit host-only PILOT-01 over the existing gate, queue, jobs and MLT bridge.

No provider clients, model identities, account access or default production unlock.
Only the separately authorized local root can execute the approved original inputs.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
import re
from pathlib import Path, PureWindowsPath
import shutil
import subprocess
import sys
import time
import uuid
import wave
import xml.etree.ElementTree as ET

from pydantic import TypeAdapter

from .contracts import Identifier, ProjectBrief
from .editorial import Scene, EditPlan, Placement, Shot, SubtitleEvent
from .jobs import JobManager
from .production_bridge import ProductionBridge, _ReadOnlyRegistry
from .registry import Registry, StudioError, content_hash, workspace_path, write_generated_json
from .tts import wav_info
from .v2_acceptance import fingerprint
from .v2_contracts import Artifact, Brief, Result, Task
from .v2_ids import derived_id
from .v2_narration import narration_descriptors, profile_spec
from .v2_storage import ArtifactStore

REPO = Path(__file__).resolve().parents[1]
GIB = 1024**3
OPERATIONS = ['synthesize', 'compile', 'render', 'qa', 'portable']


def safe_root(value):
    from .multi_ai_v2 import local_root
    raw = Path(value).absolute()
    for part in (raw, *raw.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & 0x400):
            raise StudioError('PATH_UNSAFE', 'Pilot paths cannot contain reparse points')
    root = local_root(raw)
    if REPO.is_relative_to(root):
        raise StudioError('PATH_UNSAFE', 'Pilot root overlaps the repository')
    return root


def storage_check(root, *, planned_bytes=GIB):
    root = Path(root)
    existing = next(p for p in (root, *root.parents) if p.exists())
    size = sum(p.stat().st_size for p in root.rglob('*') if p.is_file()) if root.exists() else 0
    free = shutil.disk_usage(existing).free
    if size > GIB or free - planned_bytes < 4*GIB:
        raise StudioError('BLOCKED_STORAGE', 'Pilot requires <=1 GiB outputs and >=4 GiB remaining reserve')
    return {'persistent_bytes': size, 'free_bytes': free, 'planned_bytes': planned_bytes,
            'max_persistent_bytes': GIB, 'reserve_bytes': 4*GIB}


def original_inputs(run_id):
    texts = [
        ['Представь видеостудию, которая собирает историю по частям. Сначала появляется короткий сценарий. Затем каждая сцена получает собственный голос, изображения и место на монтажной линии.',
         'Все эти части связаны между собой, но сохраняются отдельно. Поэтому готовый ролик остаётся не только видеофайлом, но и проектом, который можно открыть и изменить.'],
        ['Теперь в середине истории обнаружилась неточная фраза. Обычный длинный звуковой файл пришлось бы переделывать целиком. Здесь меняется только нужный фрагмент.',
         'Первая и третья сцены остаются на своих местах. Система проверяет, какие файлы зависят от исправленного текста, обновляет их и сохраняет предыдущую версию, чтобы результат можно было сравнить.'],
        ['После исправления собирается итоговый ролик. Проверка подтверждает, что звук присутствует, изображения открываются, а субтитры не выходят за границы сцен.',
         'Проект вместе с необходимыми файлами можно перенести в другую папку. Но последнее решение остаётся за человеком: понятна ли история, подходит ли монтаж и хочется ли досмотреть видео до конца.']]
    scenes = []
    for index, segments in enumerate(texts, 1):
        scenes.append(Scene(project_id=run_id, scene_id=f'scene_{index:03}', version=1,
            start_state='Original host-authored technical story', cause='Explain a local editable pipeline',
            goal='Demonstrate a bounded change', obstacle='A phrase needs correction',
            decision_or_action='Preserve independent media and trusted input hashes',
            consequence='Only the affected scene changes', end_state='Owner reviews the technical result',
            characters=['Студия', 'Редактор'], emotional_intent='Clarity; no artistic acceptance claimed',
            narration_segments=[{'segment_id': f'voice_{n:03}', 'text': text, 'simulated': False}
                                for n, text in enumerate(segments, 1)], simulated=False).model_dump())
    revised = deepcopy(scenes[1]); revised['version'] = 2
    revised['narration_segments'][0]['text'] = revised['narration_segments'][0]['text'].replace(
        'неточная фраза', 'фраза, требующая уточнения')
    return {'origin': 'original_host_authored_manual_assisted', 'scenes': [*scenes, revised]}


class PilotPermit:
    def __init__(self, root, *, opt_in=False):
        if opt_in is not True:
            raise StudioError('PILOT_PERMISSION_REQUIRED', 'Explicit local pilot opt-in is required')
        self.root = safe_root(root)
        self.path = workspace_path(self.root, 'authorization.json')
        self.sha256 = content_hash(self.path)
        self.document = json.loads(self.path.read_text(encoding='utf-8'))
        self.validate()

    def validate(self):
        # Check children before any local worker can follow cache/output paths.
        # Walk never enters a link: inspect all names before descending.
        safe_root(self.root)
        for directory, dirs, files in os.walk(self.root, followlinks=False):
            for name in dirs+files:
                child=Path(directory)/name
                if child.is_symlink() or getattr(child.lstat(),'st_file_attributes',0) & 0x400:
                    raise StudioError('PATH_UNSAFE','Pilot descendants cannot be symlinks or reparse points')
        doc = self.document
        TypeAdapter(Identifier).validate_python(doc['run_id'])
        if (content_hash(self.path) != self.sha256 or json.loads(self.path.read_text(encoding='utf-8')) != doc
                or doc['scope'] != 'PILOT-01'
                or doc['root'] != str(safe_root(self.root)) or doc['operations'] != OPERATIONS
                or doc['profile_sha256'] != fingerprint(profile_spec())
                or doc['inputs'] != original_inputs(doc['run_id'])
                or doc['input_sha256'] != fingerprint(doc['inputs'])):
            raise StudioError('STALE_INPUTS', 'Pilot root, original inputs or approved profile changed')
        return doc

    def validate_admission(self, coordinator_root, contract, task, requested, result):
        doc = self.validate()
        scene = Scene.model_validate(result.payload)
        hashes = {'manual_scene': fingerprint(scene.model_dump()), 'permit': self.sha256, 'profile': doc['profile_sha256']}
        if (Path(coordinator_root).resolve() != self.root/'coordinator' or contract.run_id != doc['run_id']
                or scene.project_id != doc['run_id'] or scene.model_dump() not in doc['inputs']['scenes']
                or scene.simulated or any(s.simulated for s in scene.narration_segments) or result.simulated
                or contract.input_hashes != hashes or result.input_hashes != hashes
                or task['context'].get('pilot_permit_sha256') != self.sha256
                or requested['context'].get('manual_scene') != scene.model_dump()
                or task['context'].get('manual_scene') != scene.model_dump()
                or task['execution_mode'] != 'local' or contract.input_version != scene.version
                or len(result.artifacts) != 1):
            raise StudioError('PILOT_ADMISSION_INVALID', 'Not this explicitly approved original host input')
        artifact = result.artifacts[0]
        path = ArtifactStore(coordinator_root).verify(artifact)
        if artifact.simulated or json.loads(path.read_text(encoding='utf-8')) != scene.model_dump():
            raise StudioError('PILOT_ADMISSION_INVALID', 'Host input artifact does not match admission')


class HostAdmissionAdapter:
    """Admit an approved manual Scene; performs no TTS, rendering or model work."""
    is_local = True
    is_simulated = False

    def __init__(self, permit):
        self.permit = permit
        self.pending = {}

    def start(self, task, profile, attempt_dir=None):
        contract = Task.model_validate(task['context']['v2_contract'])
        payload = task['context']['manual_scene']
        store = ArtifactStore(self.permit.root/'coordinator')
        artifact = store.put_json(contract.run_id, payload, simulated=False)
        result = Result(task_id=contract.task_id, run_id=contract.run_id, acceptance_key=task['attempt_id'],
            expected_output_schema='scene', payload=payload, input_version=contract.input_version,
            input_hashes=contract.input_hashes, artifacts=[artifact], simulated=False)
        self.permit.validate_admission(store.root, contract, task, task, result)
        handle = uuid.uuid4().hex
        self.pending[handle] = {'type': 'completed', 'result': payload, 'host_metadata': result.model_dump()}
        return handle

    def poll(self, handle):
        event = self.pending.pop(handle, None)
        return [event] if event else []

    def cancel(self, handle):
        self.pending.pop(handle, None)
        return {'status': 'cancelled'}


def initialize(root, run_id, *, opt_in=False, tts_python=None):
    if not opt_in:
        raise StudioError('PILOT_PERMISSION_REQUIRED', 'Explicit local pilot opt-in is required')
    root = safe_root(root)
    if root.exists():
        raise FileExistsError('Choose a new unique pilot root; existing data is preserved')
    TypeAdapter(Identifier).validate_python(run_id)
    space = storage_check(root)
    profile = profile_spec(); voice = profile['voice']
    model = workspace_path(REPO, voice['model_ref'])
    python = Path(tts_python or REPO/'.venv-tts/Scripts/python.exe').resolve()
    from scripts.render_project import find_qmelt
    tools = {'tts_python': str(python), 'tts_model': str(model), 'melt_path': str(find_qmelt()),
             'ffmpeg_path': shutil.which('ffmpeg'), 'ffprobe_path': shutil.which('ffprobe')}
    if not all(value and Path(value).is_file() for value in tools.values()):
        raise StudioError('BLOCKED_LOCAL_RUNTIME', 'Installed Piper Python, MLT, FFmpeg and ffprobe are required')
    if content_hash(model) != voice['model_sha256'] or content_hash(Path(str(model)+'.json')) != voice['model_config_sha256']:
        raise StudioError('BLOCKED_LOCAL_RUNTIME', 'Installed Ruslan model/config hashes differ from profile')
    probe = subprocess.run([str(python), '-c', 'import piper; import importlib.metadata; print(importlib.metadata.version("piper-tts"))'],
                           capture_output=True, text=True, timeout=20)
    if probe.returncode or probe.stdout.strip() != voice['engine_version']:
        raise StudioError('BLOCKED_LOCAL_RUNTIME', 'Installed Piper version differs from profile')
    root.mkdir(parents=True, exist_ok=False)
    write_generated_json(root, 'write_probe.json', {'scope': 'new_pilot_root_only'})
    inputs = original_inputs(run_id)
    write_generated_json(root, 'authorization.json', {'scope': 'PILOT-01', 'root': str(root), 'run_id': run_id,
        'operations': OPERATIONS, 'profile_sha256': fingerprint(profile), 'inputs': inputs,
        'input_sha256': fingerprint(inputs), 'initial_storage': space})
    config = {**tools, 'tts_engine': 'piper', 'tts_normalization_scope': voice['normalization_scope'],
              'pronunciation_lexicon': profile['pronunciation_lexicon'], 'pronunciation_terms': profile['pronunciation_terms'],
              'video_encoder': profile['video']['encoder'], 'render_threads': profile['video']['render_threads']}
    config.update({'tts_'+key: value for key, value in voice['settings'].items() if key != 'speaker_id'})
    write_generated_json(root, 'engine/runtime.json', config)
    with Registry(root/'engine') as registry:
        registry.initialize(ProjectBrief(project_id=run_id, title='Как студия меняет одну сцену',
            work_title='Оригинальный технический пилот', character='Редактор'))
    return {'root': str(root), 'run_id': run_id, 'status': 'AUTHORIZED_LOCAL_PILOT', 'storage': space,
            'model_requests': 0, 'paid_api_requests': 0, 'production_started': False}


def verify_audio(engine_root, run_id, candidate, job_id):
    """Accept bytes only from a completed existing synthesize job and exact requests."""
    root = Path(engine_root).resolve()
    job = JobManager(root).status(job_id)
    recipe = narration_descriptors(candidate, root)
    profile = profile_spec(); voice = profile['voice']
    if job['project_id'] != run_id or job['operation'] != 'synthesize' or job['state'] != 'done' or job['invalidated']:
        raise StudioError('AUDIO_RECEIPT_INVALID', 'Audio requires a current completed synthesize job for this run')
    result = job['result']
    script_id = job['parameters']['script_asset_id']
    with _ReadOnlyRegistry(root) as registry:
        script = registry.asset(run_id, script_id, verify=True)
        raw = json.loads(workspace_path(root, script['path']).read_text(encoding='utf-8'))
    manifest_path = workspace_path(root, result['manifest'])
    if (not manifest_path.is_relative_to(root/'jobs'/job_id) or content_hash(manifest_path) != result['manifest_sha256']
            or raw != candidate['script'] or result['script_sha256'] != script['sha256']):
        raise StudioError('AUDIO_RECEIPT_INVALID', 'Job script or manifest binding changed')
    manifest = json.loads(manifest_path.read_text(encoding='utf-8'))
    if (manifest['project_id'] != run_id or manifest['script_asset_id'] != script_id
            or manifest['script_sha256'] != script['sha256'] or len(manifest['segments']) != len(recipe['segments'])):
        raise StudioError('AUDIO_RECEIPT_INVALID', 'WAV manifest belongs to another input')
    verified = []
    for expected, record in zip(recipe['segments'], manifest['segments']):
        request = json.loads((root/'jobs'/job_id/(expected['block_id']+'.request.json')).read_text(encoding='utf-8'))
        wav = workspace_path(root, record['path'])
        predicted = root/expected['cache_path']
        info = wav_info(wav)
        if (request != {'text': expected['spoken_text'], 'output': str(predicted), 'config': expected['settings']}
                or wav != predicted or record['block_id'] != expected['block_id']
                or record['text'] != expected['text'] or record['text_hash'] != hashlib.sha256(expected['spoken_text'].encode()).hexdigest()
                or record['wav_hash'] != content_hash(wav) or record['model_hash'] != voice['model_sha256']
                or record['model_config_hash'] != voice['model_config_sha256']
                or record['generation_settings'] != voice['settings'] or record['engine_version'] != voice['engine_version']
                or record['sample_rate_hz'] != voice['sample_rate_hz']
                or abs(info['duration_seconds']-record['duration_seconds']) > 1/voice['sample_rate_hz']):
            raise StudioError('AUDIO_RECEIPT_INVALID', 'WAV text/model/settings/bytes do not match completed request')
        verified.append({'block_id': expected['block_id'], 'cache_key': expected['cache_key'], 'path': record['path'],
            'wav_sha256': record['wav_hash'], 'duration_seconds': info['duration_seconds'], 'cache_hit': record['cache_hit']})
    return {'job_id': job_id, 'script_sha256': script['sha256'], 'manifest_sha256': result['manifest_sha256'],
            'profile_sha256': recipe['profile_sha256'], 'segments': verified, 'production_started': True}


def verify_portable(folder):
    folder = Path(folder).resolve()
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()} != set(manifest['files']) | {'manifest.json'}:
        raise StudioError('PORTABLE_INVALID', 'Unlisted files in portable package')
    for relative, meta in manifest['files'].items():
        path = workspace_path(folder, relative)
        if path.suffix.lower() not in {'.mp4', '.mlt', '.json', '.srt', '.png', '.wav'}:
            raise StudioError('PORTABLE_INVALID', 'Forbidden file in portable package')
        if not path.is_file() or content_hash(path) != meta['sha256'] or path.stat().st_size != meta['bytes']:
            raise StudioError('PORTABLE_INVALID', 'Portable file hash/size mismatch')
    for prop in ET.parse(folder/'project.mlt').findall(".//property[@name='resource']"):
        resource = prop.text
        if resource == '0':
            continue
        if not resource or Path(resource).is_absolute() or PureWindowsPath(resource).is_absolute():
            raise StudioError('PORTABLE_INVALID', 'MLT media must have package-relative paths')
        path = workspace_path(folder, resource)
        if not path.is_file() or path.relative_to(folder).as_posix() not in manifest['files']:
            raise StudioError('PORTABLE_INVALID', 'Referenced media missing from package manifest')
    return manifest


class LocalPilot:
    def __init__(self, root, *, opt_in=False):
        from .multi_ai_v2 import RunCoordinator
        from .v2_production import OfflineProductionBridge
        self.permit = PilotPermit(root, opt_in=opt_in)
        self.root = self.permit.root
        self.run_id = self.permit.document['run_id']
        self.engine = workspace_path(self.root,'engine',for_write=True)
        storage_check(self.root)
        self.q = RunCoordinator(self.root/'coordinator', adapters={'local': HostAdmissionAdapter(self.permit)},
                                local_pilot=self.permit)
        self.bridge = OfflineProductionBridge(self.q, self.engine)
        brief = Brief(topic='Как студия меняет одну сцену', production_mode='production').model_dump()
        with Registry(self.q.root) as registry:
            prior = registry.db.execute('SELECT request_hash FROM ai_v2_runs WHERE run_id=?',(self.run_id,)).fetchone()
            if prior and prior[0] != self.permit.sha256:
                self.q.close()
                raise StudioError('IDEMPOTENCY_CONFLICT', 'Run already binds another local authorization')
            registry.db.execute('INSERT OR IGNORE INTO ai_v2_runs VALUES(?,?,?,?,?)',
                (self.run_id,self.permit.sha256,json.dumps(brief),'production',time.time()))

    def close(self):
        self.q.close()

    def events(self, kind, key):
        with Registry(self.q.root) as registry:
            return [json.loads(row[0]) for row in registry.db.execute(
                'SELECT payload FROM ai_events WHERE kind=? AND task_id=? ORDER BY sequence',(kind,key))]

    def record(self, kind, key, value):
        with Registry(self.q.root) as registry, registry.transaction():
            self.q.queue._event(registry,kind,value,key)

    def admit(self, scene_id, version):
        self.permit.validate()
        scene = next(s for s in self.permit.document['inputs']['scenes']
                     if s['scene_id']==scene_id and s['version']==version)
        task_id = derived_id(self.run_id,scene_id,'v'+str(version))
        hashes = {'manual_scene':fingerprint(scene),'permit':self.permit.sha256,
                  'profile':self.permit.document['profile_sha256']}
        contract = Task(task_id=task_id,run_id=self.run_id,scene_id=scene_id,role='python',
            capability='local_pilot_scene',input_version=version,input_hashes=hashes,depends_on=[],
            assigned_worker='host_manual_admission',provider_profile_id='local-heavy',
            expected_output_schema='scene',idempotency_key=task_id)
        task = self.q.queue.enqueue({'task_id':task_id,'project_id':self.run_id,'role':'director',
            'resource_class':'local_heavy','expected_output':'scene','depends_on':[],'input_versions':hashes,
            'max_attempts':1,'context':{'project_id':self.run_id,'scene_id':scene_id,'scene_version':version,
                'manual_scene':scene,'pilot_permit_sha256':self.permit.sha256,'v2_contract':contract.model_dump()}})
        if task['status'] != 'completed':
            self.q.queue.resume()
            for _ in range(3):
                self.q.queue.tick()
            with Registry(self.q.root) as registry:
                row=registry.db.execute('SELECT status,error FROM ai_tasks WHERE id=?',(task_id,)).fetchone()
                if row['status']!='completed':
                    raise StudioError('PILOT_ADMISSION_INVALID',str(dict(row)))
        self.bridge.accepted_scene(self.run_id,task_id,version)
        return scene,task_id

    def register(self, identifier, path, kind, *, duration_ms=None):
        path=workspace_path(self.engine,path)
        with Registry(self.engine) as registry:
            prior=registry.db.execute('SELECT id FROM assets WHERE project_id=? AND id=?',(self.run_id,identifier)).fetchone()
            if prior:
                asset=registry.asset(self.run_id,identifier,verify=True)
                if asset['path']!=path.relative_to(self.engine).as_posix() or asset['sha256']!=content_hash(path):
                    raise StudioError('IDEMPOTENCY_CONFLICT','Registered pilot asset changed')
                return asset
            registry.register_asset(self.run_id,identifier,path.relative_to(self.engine).as_posix(),kind=kind,
                rights='owned',duration_ms=duration_ms,base_revision=registry._project(self.run_id)['revision'],
                idempotency_key='register_'+identifier)
            return registry.asset(self.run_id,identifier,verify=True)

    def command(self, label, command, *, timeout=180):
        folder=workspace_path(self.root,'logs',for_write=True);folder.mkdir(exist_ok=True)
        prefix=folder/(label+'_'+uuid.uuid4().hex[:10])
        started=time.monotonic()
        with prefix.with_suffix('.log').open('x',encoding='utf-8') as stream:
            result=subprocess.run([str(c) for c in command],stdout=stream,stderr=subprocess.STDOUT,
                                  timeout=timeout,creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
        write_generated_json(self.root,prefix.relative_to(self.root).as_posix()+'.json',
            {'command':[str(c) for c in command],'exit_code':result.returncode,'elapsed_seconds':time.monotonic()-started})
        if result.returncode:
            raise StudioError('LOCAL_TOOL_FAILED',f'{label}: exit {result.returncode}; full log {prefix}.log')
        return prefix.with_suffix('.log')

    def graphic(self, scene_id):
        relative='visuals/'+scene_id+'.png'
        path=workspace_path(self.engine,relative,for_write=True)
        if not path.exists():
            folder=path.parent;folder.mkdir(parents=True,exist_ok=True)
            # Fixed host-authored drawing code, no imported media or model output.
            script=self.root/'draw_card.ps1'
            source=r'''param([string]$OutputPath,[int]$Scene)
Add-Type -AssemblyName System.Drawing
$bitmap=[System.Drawing.Bitmap]::new(1920,1080)
$g=[System.Drawing.Graphics]::FromImage($bitmap)
$g.Clear([System.Drawing.Color]::FromArgb(18,27,46))
$title=[System.Drawing.Font]::new('Segoe UI',56,[System.Drawing.FontStyle]::Bold)
$body=[System.Drawing.Font]::new('Segoe UI',32)
$white=[System.Drawing.Brushes]::White
$g.DrawString('HOW A STUDIO CHANGES ONE SCENE',$body,$white,110,100)
$g.DrawString(('SCENE 0'+$Scene),$title,$white,110,205)
for($i=1;$i -le 3;$i++) {
 $color=if($i -eq $Scene){[System.Drawing.Color]::FromArgb(245,169,61)}else{[System.Drawing.Color]::FromArgb(47,118,156)}
 $brush=[System.Drawing.SolidBrush]::new($color)
 $x=110+($i-1)*575
 $g.FillRectangle($brush,$x,390,530,300)
 $g.DrawString(('0'+$i),$title,$white,($x+50),420)
 $g.DrawString('TEXT -> WAV',$body,$white,($x+50),545)
 $brush.Dispose()
}
$g.DrawString('Independent files / one precise change / editable timeline',$body,$white,110,820)
$bitmap.Save($OutputPath,[System.Drawing.Imaging.ImageFormat]::Png)
$g.Dispose();$bitmap.Dispose();$title.Dispose();$body.Dispose()
'''
            if script.exists() and script.read_text(encoding='utf-8')!=source:
                raise StudioError('ARTIFACT_INVALID','Preserve modified drawing script')
            if not script.exists():
                script.write_text(source,encoding='utf-8')
            self.command('original_graphic',['powershell.exe','-NoProfile','-NonInteractive','-File',script,
                                            '-OutputPath',path,'-Scene',int(scene_id[-3:])],timeout=30)
        return self.register('image_'+scene_id,relative,'image')

    def candidate(self, scene_id, version):
        scene,task_id=self.admit(scene_id,version)
        image=self.graphic(scene_id)
        mappings=[dict(scene_id=scene_id,scene_version=version,segment_id=s['segment_id'],duration_ms=1000,
            shots=[dict(shot_id=derived_id('shot',scene_id,s['segment_id']),asset_id=image['id'],source_out_ms=1000,
                emotional_task='Original technical diagram',reuse_reason='Same scene card across two narration segments')])
            for s in scene['narration_segments']]
        return self.bridge.candidate(self.run_id,task_id,version,mappings)

    def synthesize(self, candidate, key, *, smoke=False):
        self.permit.validate();storage_check(self.root)
        value=self.bridge.read_candidate(candidate)
        if smoke:
            value=deepcopy(value);value['script']['segments']=value['script']['segments'][:1]
        script_id=derived_id('script',value['binding']['scene_id'],'v'+str(value['version']), 'smoke' if smoke else 'full')
        relative='scripts/'+script_id+'.json'
        if not (self.engine/relative).exists():
            write_generated_json(self.engine,relative,value['script'])
        asset=self.register(script_id,relative,'text')
        if json.loads(workspace_path(self.engine,asset['path']).read_text(encoding='utf-8'))!=value['script']:
            raise StudioError('STALE_INPUTS','Registered script differs from approved manual Scene')
        manager=JobManager(self.engine)
        with Registry(self.engine) as registry:
            prior=registry.db.execute('SELECT response FROM requests WHERE project_id=? AND key=?',(self.run_id,key)).fetchone()
            base=(manager.status(json.loads(prior[0])['job_id'])['input_revision']-1 if prior
                  else registry._project(self.run_id)['revision'])
        ProductionBridge(self.engine).prepare_request(self.run_id,'synthesize',{'script_asset_id':script_id},base,key)
        receipt=manager.enqueue(self.run_id,'synthesize',{'script_asset_id':script_id},base,key)
        deadline=time.monotonic()+300
        while manager.status(receipt['job_id'])['state'] in {'pending','running'}:
            if time.monotonic()>deadline:
                raise StudioError('LOCAL_JOB_TIMEOUT','Existing job preserved for explicit inspection/resume')
            time.sleep(.3)
        return verify_audio(self.engine,self.run_id,value,receipt['job_id'])

    def checkpoint(self, kind, key):
        events=self.events(kind,key)
        if not events:return None
        artifact=Artifact.model_validate(events[0]['artifact'])
        return json.loads(self.q.store.verify(artifact).read_text(encoding='utf-8'))

    def save_checkpoint(self, kind, key, value):
        prior=self.checkpoint(kind,key)
        if prior is not None:
            if prior!=value:raise StudioError('IDEMPOTENCY_CONFLICT','Checkpoint key already binds another result')
            return prior
        artifact=self.q.store.put_json(self.run_id,value,simulated=False)
        self.record(kind,key,{'artifact':artifact.model_dump()})
        return value

    def smoke(self):
        candidate=self.candidate('scene_001',1)
        audio=self.synthesize(candidate,'pilot_smoke',smoke=True)
        return self.save_checkpoint('pilot_smoke','smoke',audio)

    def plan(self, units, *, identifier, version):
        """Use a 100ms/three-frame grid; trim only verified trailing zero PCM."""
        pictures=[];voices=[];subtitles=[];assets={};cursor=0
        for unit in units:
            audio=unit['audio'];wav=workspace_path(self.engine,audio['path'])
            info=wav_info(wav);duration=int(info['duration_seconds']*10)*100
            with wave.open(str(wav),'rb') as stream:
                stream.setpos(duration*stream.getframerate()//1000)
                if any(stream.readframes(stream.getnframes()-stream.tell())):
                    raise StudioError('NARRATION_TRUNCATION','Frame alignment would remove nonzero speech samples')
            voice=self.register('wav_'+audio['wav_sha256'][:32],audio['path'],'audio',duration_ms=info['duration_ms'])
            image=unit['visual'];assets[voice['id']]=voice;assets[image['id']]=image
            start=cursor;cursor+=duration
            block=audio['block_id']
            shot=Shot(shot_id=derived_id('shot',block),scene_id=identifier,beat_id=block,asset_id=image['id'],
                visual_function='Original host technical diagram',source_in_ms=0,source_out_ms=duration,
                observed_content='Host-authored drawing; no model review',simulated=False)
            pictures.append(Placement(placement_id=shot.shot_id,asset_id=image['id'],timeline_start_ms=start,
                timeline_end_ms=cursor,source_in_ms=0,source_out_ms=duration,track='video',shot=shot))
            voices.append(Placement(placement_id=derived_id('audio',block),asset_id=voice['id'],timeline_start_ms=start,
                timeline_end_ms=cursor,source_in_ms=0,source_out_ms=duration,track='voice'))
            subtitles.append(SubtitleEvent(start_ms=start,end_ms=cursor,text=unit['text']))
        return EditPlan(project_id=self.run_id,plan_id=derived_id('edit',identifier,'v'+str(version)),scene_id=identifier,
            version=version,scene_version=version,voice_version=fingerprint([u['audio'] for u in units]),
            duration_ms=cursor,shot_placements=pictures,voice_placements=voices,subtitle_events=subtitles,
            important_decisions=['Original host inputs, actual Piper WAV; only zero trailing PCM trimmed to three-frame grid.',
                'Existing MLT renderer uses libx264; profile voice and 1920x1080/30 are unchanged. Creative review pending.']),list(assets.values())

    def qa(self, compiled, video):
        from .operations import subtitle_timing_issues
        config=json.loads((self.engine/'runtime.json').read_text(encoding='utf-8'))
        probe=self.command('ffprobe',[config['ffprobe_path'],'-v','error','-show_streams','-show_format','-of','json',video])
        data=json.loads(probe.read_text(encoding='utf-8'))
        video_stream=next((s for s in data['streams'] if s['codec_type']=='video'),None)
        audio_stream=next((s for s in data['streams'] if s['codec_type']=='audio'),None)
        if video_stream is None or audio_stream is None:
            raise StudioError('TECHNICAL_QA_FAILED','Decoded MP4 must contain both video and audio')
        expected=compiled['duration_seconds'];duration=float(data['format']['duration'])
        if (video_stream['width'],video_stream['height'],video_stream['r_frame_rate'])!=(1920,1080,'30/1'):
            raise StudioError('TECHNICAL_QA_FAILED','Decoded format differs from 1920x1080/30')
        if abs(duration-expected)>.1 or abs(float(video_stream.get('start_time',0))-float(audio_stream.get('start_time',0)))>.1:
            raise StudioError('TECHNICAL_QA_FAILED','Measured duration or stream alignment differs from timeline')
        log=self.command('decode_analysis',[config['ffmpeg_path'],'-hide_banner','-nostdin','-i',video,
            '-vf','blackdetect=d=0.2:pix_th=0.1:pic_th=0.98','-af','silencedetect=noise=-45dB:d=0.5,volumedetect',
            '-f','null','-'],timeout=180)
        text=log.read_text(encoding='utf-8',errors='replace')
        if 'black_start:' in text or 'Error while decoding' in text:
            raise StudioError('TECHNICAL_QA_FAILED','Black interval or decoding error; inspect full log')
        levels={name:float(value) for name,value in re.findall(r'(mean_volume|max_volume):\s*([-\d.]+) dB',text)}
        if set(levels)!={'mean_volume','max_volume'} or levels['max_volume']<=-60:
            raise StudioError('TECHNICAL_QA_FAILED','Decoded audio missing or effectively silent')
        project=Path(compiled['project_mlt'])
        for prop in ET.parse(project).findall(".//property[@name='resource']"):
            if prop.text!='0' and not workspace_path(project.parent,prop.text).is_file():
                raise StudioError('TECHNICAL_QA_FAILED','MLT has missing media')
        plan=json.loads((project.parent/'edit_plan.json').read_text(encoding='utf-8'))
        issues=subtitle_timing_issues(project.parent/'subtitles.srt',duration,
            [(s['start_ms']/1000,s['end_ms']/1000) for s in plan['subtitle_events']])
        if issues:raise StudioError('TECHNICAL_QA_FAILED',str(issues))
        return {'status':'VERIFIED','duration_seconds':duration,'timeline_duration_seconds':expected,
            'width':1920,'height':1080,'fps':30,'audio_present':True,'full_decode':True,'black_intervals':[],
            'subtitle_issues':issues,'stream_start_delta_seconds':abs(float(video_stream.get('start_time',0))-float(audio_stream.get('start_time',0))),
            'probe_log':str(probe),'decode_analysis_log':str(log),'speech_sync_scope':'timeline and decoded stream starts; phoneme alignment not measured',
            'audio_levels_dbfs':levels,'silence_detection_log':str(log),
            'editorial_review':'PENDING_OWNER','full_video_creative_qa':'NOT_RUN'}

    def verify_prepared(self, value):
        self.bridge.verify_compiled(value['compiled'])
        if content_hash(Path(value['video']))!=value['video_sha256']:
            raise StudioError('ARTIFACT_INVALID','Prepared MP4 changed; preserve it, do not overwrite')
        for binding in value['audio_receipts']:
            candidate=binding['candidate']
            # Old accepted scene versions remain immutable evidence; revision
            # does not change their scripts, job receipts or WAV bytes.
            raw=json.loads(self.q.store.verify(Artifact.model_validate(candidate)).read_text(encoding='utf-8'))
            verified=verify_audio(self.engine,self.run_id,raw,binding['audio']['job_id'])
            if verified!=binding['audio']:raise StudioError('ARTIFACT_INVALID','WAV receipt changed')
        return value

    def scene(self, scene_id, version):
        key=scene_id+'_v'+str(version)
        prior=self.checkpoint('pilot_scene',key)
        if prior:return self.verify_prepared(prior)
        candidate=self.candidate(scene_id,version);raw=self.bridge.read_candidate(candidate)
        audio=self.synthesize(candidate,'voice_'+key)
        image=self.graphic(scene_id)
        units=[{'audio':a,'visual':image,'text':block['text']} for a,block in zip(audio['segments'],raw['script']['segments'])]
        plan,assets=self.plan(units,identifier=scene_id,version=version)
        compiled=ProductionBridge(self.engine).compile(plan,assets,'builds/'+key+'_'+uuid.uuid4().hex[:8])
        video=Path(compiled['project_mlt']).with_name('scene.mp4')
        rendered=ProductionBridge(self.engine).render(compiled,video,qmelt=json.loads((self.engine/'runtime.json').read_text())['melt_path'])
        return self.save_checkpoint('pilot_scene',key,{'scene_id':scene_id,'version':version,'compiled':compiled,
            'video':str(video),'video_sha256':rendered['sha256'],'qa':self.qa(compiled,video),
            'audio_receipts':[{'candidate':candidate,'audio':audio}],'units':units,'production_started':True})

    def film(self, version):
        key='film_v'+str(version)
        prior=self.checkpoint('pilot_film',key)
        if prior:return self.verify_prepared(prior)
        scenes=[self.scene('scene_001',1),self.scene('scene_002',version),self.scene('scene_003',1)]
        plan,assets=self.plan([u for scene in scenes for u in scene['units']],identifier='pilot_film',version=version)
        # Reassembly uses accepted per-scene MP4 picture, with original separate
        # narration tracks. Scene 1/3 rendering is never repeated for the fix.
        pictures=[];cursor=0
        for scene in scenes:
            duration=scene['compiled']['frames']*1000//30
            asset=self.register('video_'+scene['video_sha256'][:32],Path(scene['video']).relative_to(self.engine).as_posix(),
                                'video',duration_ms=duration)
            assets.append(asset)
            shot=Shot(shot_id='clip_'+scene['scene_id'],scene_id='pilot_film',beat_id=scene['scene_id'],asset_id=asset['id'],
                source_in_ms=0,source_out_ms=duration,visual_function='Reuse accepted scene render',
                observed_content='Actual locally decoded scene MP4',simulated=False)
            pictures.append(Placement(placement_id=shot.shot_id,asset_id=asset['id'],timeline_start_ms=cursor,
                timeline_end_ms=cursor+duration,source_in_ms=0,source_out_ms=duration,track='video',shot=shot))
            cursor+=duration
        plan=plan.model_copy(update={'shot_placements':pictures})
        if not 60000<=plan.duration_ms<=90000:
            raise StudioError('PILOT_DURATION_OUT_OF_RANGE',f'Measured narration timeline {plan.duration_ms/1000}s; needs 60–90s')
        compiled=ProductionBridge(self.engine).compile(plan,assets,'builds/'+key+'_'+uuid.uuid4().hex[:8])
        video=Path(compiled['project_mlt']).with_name('preview.mp4' if version==1 else 'final.mp4')
        config=json.loads((self.engine/'runtime.json').read_text(encoding='utf-8'))
        rendered=ProductionBridge(self.engine).export(compiled,video,qmelt=config['melt_path'])
        return self.save_checkpoint('pilot_film',key,{'version':version,'compiled':compiled,'video':str(video),
            'video_sha256':rendered['sha256'],'scenes':scenes,'qa':self.qa(compiled,video),
            'audio_receipts':[r for s in scenes for r in s['audio_receipts']],'production_started':True})

    def counts(self):
        with Registry(self.engine) as registry,Registry(self.q.root) as queue:
            return {'jobs':registry.db.execute('SELECT count(*) FROM jobs').fetchone()[0],
                'job_attempts':registry.db.execute('SELECT coalesce(sum(attempts),0) FROM jobs').fetchone()[0],
                'queue_attempts':queue.db.execute('SELECT count(*) FROM ai_attempts').fetchone()[0],
                'scene_renders':queue.db.execute("SELECT count(*) FROM ai_events WHERE kind='pilot_scene'").fetchone()[0],
                'film_renders':queue.db.execute("SELECT count(*) FROM ai_events WHERE kind='pilot_film'").fetchone()[0]}

    def prepare(self):
        prior=self.checkpoint('pilot_snapshot','before')
        if prior:
            self.verify_prepared(prior['film'])
            return prior
        value=self.film(1)
        return self.save_checkpoint('pilot_snapshot','before',{'film':value,'counts':self.counts()})

    def fix(self):
        prior=self.checkpoint('pilot_snapshot','after')
        if prior:
            self.verify_prepared(prior['film'])
            return prior
        before=self.checkpoint('pilot_snapshot','before')
        if not before:raise StudioError('PILOT_ORDER_INVALID','Prepare preview first')
        value=self.film(2)
        for index in (0,2):
            if before['film']['scenes'][index]!=value['scenes'][index]:
                raise StudioError('NEIGHBOR_CHANGED','Unaffected scene was changed')
        old=before['film']['scenes'][1]['audio_receipts'][0]['audio']['segments']
        new=value['scenes'][1]['audio_receipts'][0]['audio']['segments']
        if old[1]!=new[1] and old[1]['wav_sha256']!=new[1]['wav_sha256']:
            raise StudioError('NEIGHBOR_CHANGED','Unaffected second narration segment changed')
        if old[0]['wav_sha256']==new[0]['wav_sha256'] or not new[1]['cache_hit'] or new[0]['cache_hit']:
            raise StudioError('TARGET_FIX_INVALID','Exactly one fresh WAV and one cache reuse are required')
        return self.save_checkpoint('pilot_snapshot','after',{'film':value,'counts':self.counts(),
            'unchanged_scenes':[1,3],'changed_segment':'scene_002.voice_001','neighbor_wav_sha256':new[1]['wav_sha256']})

    def replay(self):
        before=self.counts()
        value=self.film(2)
        after=self.counts()
        if before!=after:raise StudioError('REPLAY_DUPLICATED','Replay created jobs, attempts or renders')
        return self.save_checkpoint('pilot_snapshot','replay',{'before':before,'after':after,
            'final_sha256':value['video_sha256'],'production_started':True})

    def deliver(self):
        value=self.film(2)
        destination=workspace_path(self.root,'portable',for_write=True)
        copy=workspace_path(self.root,'portable_copy',for_write=True)
        prior=self.checkpoint('pilot_delivery','local')
        if prior:
            manifest=verify_portable(destination)
            if (verify_portable(copy)!=manifest
                    or content_hash(destination/'manifest.json')!=prior['portable_manifest_sha256']):
                raise StudioError('PORTABLE_INVALID','Accepted portable delivery changed')
            return prior
        if not destination.exists():
            destination.mkdir()
            source=Path(value['compiled']['project_mlt']).parent
            names=set(value['compiled']['file_hashes'])|{'final.mp4'}
            files={}
            for relative in sorted(names):
                src=workspace_path(source,relative);dst=workspace_path(destination,relative,for_write=True)
                dst.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(src,dst)
                files[relative]={'sha256':content_hash(dst),'bytes':dst.stat().st_size}
            write_generated_json(destination,'manifest.json',{'run_id':self.run_id,'files':files,
                'origin':'original_host_authored_manual_assisted','production_started':True,
                'uploaded':False,'remote_sync':'UNVERIFIED','editorial_review':'PENDING_OWNER','full_video_creative_qa':'NOT_RUN'})
        manifest=verify_portable(destination)
        if not copy.exists():shutil.copytree(destination,copy)
        if verify_portable(copy)!=manifest:raise StudioError('PORTABLE_INVALID','Relocated package differs')
        relocated={**value['compiled'],'project_mlt':str(copy/'project.mlt')}
        qa=self.qa(relocated,copy/'final.mp4')
        # Parse and decode the relocated MLT with the same installed engine;
        # output goes outside the package, keeping the portable manifest exact.
        probe=self.root/'relocated_probe.wav'
        if not probe.exists():
            config=json.loads((self.engine/'runtime.json').read_text(encoding='utf-8'))
            env=os.environ.copy();env['QT_QPA_PLATFORM']='offscreen';env['MLT_NO_VDPAU']='1'
            env['PATH']=str(Path(config['melt_path']).parent)+os.pathsep+env.get('PATH','')
            command=[config['melt_path'],str(copy/'project.mlt'),'-consumer','avformat:'+str(probe),
                     'vn=1','acodec=pcm_s16le','ar=22050','ac=1','real_time=-1']
            log=self.root/'relocated_mlt.log'
            with log.open('x',encoding='utf-8') as stream:
                result=subprocess.run(command,cwd=copy,env=env,stdout=stream,stderr=subprocess.STDOUT,timeout=180,
                                      creationflags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0)
            write_generated_json(self.root,'relocated_mlt_command.json',{'command':command,'exit_code':result.returncode})
            if result.returncode:raise StudioError('PORTABLE_INVALID','Relocated MLT did not execute')
        measured=wav_info(probe)
        if abs(measured['duration_seconds']-value['compiled']['duration_seconds'])>.1:
            raise StudioError('PORTABLE_INVALID','Relocated MLT audio duration differs')
        return self.save_checkpoint('pilot_delivery','local',{'status':'LOCAL_TECHNICAL_PILOT_VERIFIED',
            'preview':self.checkpoint('pilot_film','film_v1')['video'],'final':str(destination/'final.mp4'),
            'mlt':str(destination/'project.mlt'),'relocated_mlt':str(copy/'project.mlt'),
            'portable_manifest_sha256':content_hash(destination/'manifest.json'),'final_sha256':value['video_sha256'],
            'duration_seconds':qa['duration_seconds'],'relocated_mlt_duration_seconds':measured['duration_seconds'],
            'counts':self.counts(),'storage':storage_check(self.root,planned_bytes=0),'production_started':True,
            'model_requests':0,'paid_api_requests':0,'uploaded':False,'remote_sync':'UNVERIFIED',
            'editorial_review':'PENDING_OWNER','full_video_creative_qa':'NOT_RUN'})


def main(argv=None):
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('phase',choices=['init','admit','smoke','prepare','fix','replay','deliver'])
    parser.add_argument('--root',type=Path,required=True)
    parser.add_argument('--run-id',default='pilot_01')
    parser.add_argument('--execute-local-pilot',action='store_true')
    args=parser.parse_args(argv)
    pilot=None
    try:
        if args.phase=='init':
            value=initialize(args.root,args.run_id,opt_in=args.execute_local_pilot)
        else:
            pilot=LocalPilot(args.root,opt_in=args.execute_local_pilot)
            value=pilot.admit('scene_001',1) if args.phase=='admit' else getattr(pilot,args.phase)()
        print(json.dumps(value,ensure_ascii=False))
        return 0
    except (StudioError,ValueError,OSError,subprocess.SubprocessError) as exc:
        print(json.dumps({'status':getattr(exc,'code','BLOCKED_LOCAL_RUNTIME'),'error':str(exc)},ensure_ascii=False))
        return 1
    finally:
        if pilot:pilot.close()


if __name__=='__main__':
    # Keep one class identity for the exact host permit guard when invoked by -m.
    from app.v2_local_pilot import main as canonical_main
    raise SystemExit(canonical_main())
