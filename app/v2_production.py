"""Accepted Scene -> immutable existing-engine candidate. Never enqueue a job."""
import json
from pathlib import Path
import uuid

from .editorial import Scene
from .operations import ProductionScript, ProductionBlock, ProductionVisualShot
from .registry import Registry, StudioError, content_hash, workspace_path
from .v2_acceptance import fingerprint
from .v2_contracts import Artifact
from .v2_ids import derived_id
from .v2_visuals import verify_mapping, prepare_edit_plan
from .production_bridge import ProductionBridge, _ReadOnlyRegistry
from .v2_narration import narration_descriptors, narration_identity, voice_asset_ids


class OfflineProductionBridge:
    def __init__(self, coordinator, engine_root, *, source_bundle=None):
        self.coordinator = coordinator
        self.root = coordinator.root
        self.engine_root = engine_root
        self.source_bundle = source_bundle

    def accepted_scene(self, run_id, task_id, scene_version):
        """Revalidate all ancestor completions; a cached or arbitrary Scene is insufficient."""
        with self.coordinator.queue._lock, Registry(self.root) as registry:
            verified, visiting = {}, set()
            def visit(identifier):
                if identifier in visiting:
                    raise StudioError('DEPENDENCY_CYCLE', 'Cyclic accepted dependencies')
                if identifier in verified:
                    return verified[identifier]
                row = registry.db.execute('SELECT * FROM ai_tasks WHERE id=?', (identifier,)).fetchone()
                if row is None or row['project_id'] != run_id:
                    raise StudioError('RESULT_RUN_MISMATCH', 'Scene/dependency belongs to another run')
                visiting.add(identifier)
                accepted = self.coordinator.result_gate.accepted_dependency(registry, row)
                task = self.coordinator.queue._task(row)
                for dep in task['depends_on']:
                    visit(dep)
                visiting.remove(identifier)
                verified[identifier] = (accepted, task)
                return verified[identifier]
            accepted, task = visit(task_id)
            if task['expected_output'] != 'scene':
                raise StudioError('SCHEMA_INVALID', 'The accepted task must produce a canonical Scene')
            scene = Scene.model_validate(accepted['payload'])
            if scene.project_id != run_id or scene.version != scene_version:
                raise StudioError('STALE_INPUTS', 'Scene project/version differs from the requested binding')
            if any(s.simulated is not scene.simulated for s in scene.narration_segments):
                raise StudioError('ARTIFACT_INVALID','Narration segment provenance differs from its Scene')
            current = registry.db.execute('SELECT * FROM ai_scenes WHERE project_id=? AND scene_id=? ORDER BY version DESC LIMIT 1',
                                          (run_id,scene.scene_id)).fetchone()
            if (not current or current['version'] != scene.version or current['sha256'] != fingerprint(scene.model_dump())
                    or json.loads(current['payload']) != scene.model_dump()):
                raise StudioError('STALE_INPUTS', 'A newer or manually edited Scene must be accepted before import')
            exact_file = False
            for artifact in accepted['artifacts']:
                path = self.coordinator.store.verify(Artifact.model_validate(artifact))
                if path.suffix == '.json' and json.loads(path.read_text(encoding='utf-8')) == scene.model_dump():
                    exact_file = True
            if not exact_file:
                raise StudioError('ARTIFACT_INVALID', 'Scene needs an immutable accepted JSON artifact')
            return scene, {'run_id': run_id, 'task_id': task_id,
                'attempt_id': accepted['acceptance']['acceptance_key'], 'scene_id': scene.scene_id,
                'scene_version': scene.version, 'input_hashes': task['input_versions'],
                'accepted': accepted['acceptance'], 'artifacts': accepted['artifacts'],
                'dependencies': {key: value[0]['acceptance'] for key, value in sorted(verified.items())}}

    def candidate(self, run_id, task_id, scene_version, mappings):
        with self.coordinator.queue._lock:
            scene, binding = self.accepted_scene(run_id, task_id, scene_version)
            source_evidence=None
            if self.source_bundle is not None:
                from .v2_sources import CanonBible, verify_sources
                bible=CanonBible.model_validate(self.source_bundle['canon'])
                if bible.simulated and not scene.simulated:
                    raise StudioError('ARTIFACT_INVALID','Simulated source research cannot establish a real Scene')
                source_evidence=verify_sources(self.source_bundle['root'],bible,self.source_bundle['manifest_sha256'])
                source_evidence['canon_sha256']=fingerprint(bible.model_dump())
                facts={f'{f.edition}:{f.fact_id}' for f in bible.facts}
                sources={f'{s.edition}:{s.source_id}':s.sha256 for s in bible.sources}
                if any(ref not in facts for ref in scene.fact_refs) or any(sources.get(k)!=v for k,v in scene.dependencies.items()):
                    raise StudioError('SOURCE_REQUIRED','Scene refers to missing or changed source evidence')
            elif scene.fact_refs or scene.dependencies:
                raise StudioError('SOURCE_REQUIRED','Factual Scene dependencies require a pinned source bundle')
            visuals, assets = verify_mapping(self.engine_root, run_id, scene, mappings)
            blocks, mapping = [], []
            for segment, visual in zip(scene.narration_segments, visuals):
                block_id = derived_id(scene.scene_id, segment.segment_id)
                blocks.append(ProductionBlock(id=block_id, text=segment.text, title=scene.scene_id,
                    visual_purpose=visual.shots[0].visual_purpose,
                    visual_shots=[ProductionVisualShot(asset_id=s.asset_id, motion='static',
                        weight=float(s.source_out_ms-s.source_in_ms), in_ms=s.source_in_ms) for s in visual.shots]))
                mapping.append({'scene_id': scene.scene_id, 'scene_version': scene.version,
                    'segment_id': segment.segment_id, 'block_id': block_id, 'text_sha256': fingerprint({'text': segment.text}),
                    'fact_refs': scene.fact_refs, 'assumption_refs': scene.assumption_refs,
                    'source_dependencies': scene.dependencies, 'emotional_intent': scene.emotional_intent})
                # Preserve old short-ID artifacts byte-for-byte. Record bounded
                # plan/voice IDs beside their complete originals only when needed.
                for field, prefix, original in (('edit_plan_id', 'edit', scene.scene_id),
                                                ('voice_placement_id', 'audio', segment.segment_id)):
                    identifier = derived_id(prefix, original)
                    if identifier != prefix + '_' + original:
                        mapping[-1][field] = identifier
            script = ProductionScript(project_id=run_id, title=scene.scene_id, scope='Offline accepted Scene candidate',
                                      segments=blocks, overlays_enabled=False)
            body = {'state': 'validated_not_registered', 'version': scene.version, 'binding': binding,
                    'script': script.model_dump(), 'scene_block_mapping': mapping,
                    'visual_mapping': [v.model_dump() for v in visuals],
                    'visual_assets': [assets[key] for key in sorted(assets)], 'simulated': scene.simulated,
                    'source_evidence':source_evidence,'production_started': False, 'creative_review': 'NOT_RUN'}
            return self.coordinator.store.put_json(run_id, {'content_sha256': fingerprint(body), **body},
                                                    simulated=scene.simulated).model_dump()

    def read_candidate(self, artifact):
        artifact = Artifact.model_validate(artifact)
        value = json.loads(self.coordinator.store.verify(artifact).read_text(encoding='utf-8'))
        body = {k: v for k, v in value.items() if k != 'content_sha256'}
        if (value['content_sha256'] != fingerprint(body) or value['simulated'] is not artifact.simulated
                or value['binding']['run_id'] != artifact.run_id or value['state'] != 'validated_not_registered'):
            raise StudioError('ARTIFACT_INVALID', 'Candidate integrity/provenance changed')
        scene, binding = self.accepted_scene(artifact.run_id, value['binding']['task_id'], value['version'])
        # Reconstruct from trusted inputs, rather than trusting self-authored JSON/hash.
        expected = self.candidate(artifact.run_id, binding['task_id'], scene.version, value['visual_mapping'])
        if expected != artifact.model_dump():
            raise StudioError('ARTIFACT_INVALID', 'Candidate differs from accepted Scene and registered visuals')
        return value

    def prepare_engine_request(self, artifact, script_asset_id, base_revision, idempotency_key):
        value = self.read_candidate(artifact)
        with _ReadOnlyRegistry(self.engine_root) as registry:
            asset = registry.asset(artifact['run_id'],script_asset_id,verify=True)
            registered = ProductionScript.model_validate_json(workspace_path(registry.root,asset['path']).read_text(encoding='utf-8'))
            if registered.model_dump() != value['script']:
                raise StudioError('STALE_INPUTS', 'Registered script differs from the accepted candidate')
        return ProductionBridge(self.engine_root).prepare_request(artifact['run_id'],'synthesize',
            {'script_asset_id':script_asset_id},base_revision,idempotency_key)

    def prepare_mlt(self, artifact, *, voice_assets=None):
        with self.coordinator.queue._lock:
            value = self.read_candidate(artifact)
            scene, _ = self.accepted_scene(artifact['run_id'],value['binding']['task_id'],value['version'])
            narration = narration_descriptors(value,self.engine_root)
            plan, assets = prepare_edit_plan(self.engine_root,artifact['run_id'],scene,value['visual_mapping'],
                voice_version=narration_identity(narration),voice_assets=voice_asset_ids(value,narration,voice_assets))
            key = fingerprint({'candidate':artifact, 'plan':plan.model_dump(),'narration':narration_identity(narration)})
            with Registry(self.root) as registry:
                for row in registry.db.execute("SELECT payload FROM ai_events WHERE kind='offline_mlt_prepared' AND task_id=?",
                                                (value['binding']['task_id'],)):
                    prior = json.loads(row[0])
                    if prior['key'] == key:
                        receipt = Artifact.model_validate(prior['artifact'])
                        self.read_mlt(receipt.model_dump())
                        return receipt.model_dump()
            with _ReadOnlyRegistry(self.engine_root) as registry:
                if registry.db.execute("SELECT 1 FROM sqlite_master WHERE name='jobs'").fetchone():
                    if registry.db.execute("SELECT 1 FROM jobs WHERE state IN ('pending','running') LIMIT 1").fetchone():
                        raise StudioError('ACTIVE_OPERATION','Cannot prepare a build during active production')
            # Interrupted folders are preserved. Only a completed event permits replay.
            compiled = ProductionBridge(self.engine_root).compile(plan,assets,
                'builds/offline_'+key[:24]+'_'+uuid.uuid4().hex[:12],
                scene_version=scene.version,voice_version=plan.voice_version)
            receipt = self.coordinator.store.put_json(artifact['run_id'],{
                'candidate':artifact,'edit_plan':plan.model_dump(),'compiled':compiled,'narration':narration,
                'voice_assets':voice_assets or {},
                'simulated':scene.simulated,'production_started':False,'render_status':'NOT_RUN',
                'audio_status':'SYNTHETIC_FIXTURE_ONLY' if voice_assets and scene.simulated else 'NOT_RUN'},simulated=scene.simulated)
            with Registry(self.root) as registry, registry.transaction():
                self.coordinator.queue._event(registry,'offline_mlt_prepared',
                    {'key':key,'artifact':receipt.model_dump()},value['binding']['task_id'])
            return receipt.model_dump()

    def read_mlt(self, artifact):
        receipt=Artifact.model_validate(artifact)
        saved=json.loads(self.coordinator.store.verify(receipt).read_text(encoding='utf-8'))
        candidate=self.read_candidate(saved['candidate'])
        if receipt.run_id!=saved['candidate']['run_id'] or receipt.simulated is not candidate['simulated']:
            raise StudioError('ARTIFACT_INVALID','MLT receipt provenance mismatch')
        with Registry(self.root) as registry:
            events=registry.db.execute("SELECT payload FROM ai_events WHERE kind='offline_mlt_prepared' AND task_id=?",
                                      (candidate['binding']['task_id'],)).fetchall()
            if not any(json.loads(row[0])['artifact']==receipt.model_dump() for row in events):
                raise StudioError('RESULT_NOT_ACCEPTED','Build is not bound to a host preparation checkpoint')
        scene,_=self.accepted_scene(receipt.run_id,candidate['binding']['task_id'],candidate['version'])
        recipe=narration_descriptors(candidate,self.engine_root)
        plan,_=prepare_edit_plan(self.engine_root,receipt.run_id,scene,candidate['visual_mapping'],
            voice_version=narration_identity(recipe),voice_assets=voice_asset_ids(candidate,recipe,saved['voice_assets']))
        if saved['edit_plan']!=plan.model_dump() or saved['simulated'] is not scene.simulated:
            raise StudioError('STALE_INPUTS','Prepared plan differs from current accepted inputs')
        self.verify_compiled(saved['compiled'])
        return saved

    def propose_revision(self, artifact, segment_id, text):
        value=self.read_candidate(artifact)
        scene,_=self.accepted_scene(artifact['run_id'],value['binding']['task_id'],value['version'])
        raw=scene.model_dump();found=False
        for segment in raw['narration_segments']:
            if segment['segment_id']==segment_id:
                segment['text']=text;found=True
        if not found:
            raise StudioError('SEGMENT_NOT_FOUND','Unknown narration segment')
        raw['version']+=1
        updated=Scene.model_validate(raw)
        return self.coordinator.store.put_json(artifact['run_id'],{
            'state':'requires_trusted_acceptance','prior_candidate':artifact,
            'scene':updated.model_dump(),'simulated':scene.simulated},simulated=scene.simulated).model_dump()

    def enqueue_fixture_revision(self, proposal, key):
        """Explicit offline fake revision through the SAME existing gate/queue."""
        from .v2_contracts import Task
        with self.coordinator.queue._lock:
            value=json.loads(self.coordinator.store.verify(Artifact.model_validate(proposal)).read_text(encoding='utf-8'))
            proposed_scene=Scene.model_validate(value['scene'])
            if (proposal['simulated'] is not True or value['simulated'] is not True or not proposed_scene.simulated
                    or not all(getattr(a,'is_simulated',False) for a in self.coordinator.queue.adapters.values())):
                raise StudioError('PRODUCTION_DISABLED','Revision replay also requires simulation provenance')
            from pydantic import TypeAdapter
            from .contracts import Identifier
            TypeAdapter(Identifier).validate_python(key)
            task_id=derived_id(proposal['run_id'],'revision',key)
            with Registry(self.root) as registry:
                existing=registry.db.execute('SELECT * FROM ai_tasks WHERE id=?',(task_id,)).fetchone()
                if existing:
                    task=self.coordinator.queue._task(existing)
                    if (task['context'].get('fixture_result')!=value['scene']
                            or task['input_versions'].get('prior_candidate')!=value['prior_candidate']['sha256']
                            or task['context']['v2_contract']['capability']!='offline_revision'
                            or task['context']['v2_contract']['idempotency_key']!=key
                            or task['project_id']!=proposal['run_id']):
                        raise StudioError('IDEMPOTENCY_CONFLICT','Revision key already binds different input')
                    if task['status']=='completed':
                        self.accepted_scene(proposal['run_id'],task_id,proposed_scene.version)
                    return {'task_id':task_id,'status':task['status'].upper(),'simulated':True,'production_started':False}
            prior=self.read_candidate(value['prior_candidate'])
            scene=Scene.model_validate(value['scene'])
            if (not scene.simulated or not prior['simulated'] or proposal['simulated'] is not True
                    or scene.project_id!=proposal['run_id'] or scene.scene_id!=prior['binding']['scene_id']
                    or scene.version!=prior['version']+1
                    or not all(getattr(a,'is_simulated',False) for a in self.coordinator.queue.adapters.values())):
                raise StudioError('PRODUCTION_DISABLED','Only explicit fake revisions are supported offline')
            with Registry(self.root) as registry:
                current=registry.db.execute('SELECT pinned FROM ai_scenes WHERE project_id=? AND scene_id=? ORDER BY version DESC LIMIT 1',
                    (scene.project_id,scene.scene_id)).fetchone()
                if current['pinned']:
                    raise StudioError('PINNED_CONFLICT','Pinned/manual scene needs separate owner approval')
            inputs={'prior_candidate':value['prior_candidate']['sha256'],
                    'prior_result':prior['binding']['accepted']['result_sha256']}
            dependencies=[prior['binding']['task_id']]
            contract=Task(task_id=task_id,run_id=scene.project_id,scene_id=scene.scene_id,role='python',
                capability='offline_revision',input_version=scene.version,input_hashes=inputs,depends_on=dependencies,
                assigned_worker='fake_python',provider_profile_id='simulation',expected_output_schema='scene',idempotency_key=key)
            # A revision is a new version, not an editorial check of the old version.
            # The pinned prior hash is host-authored; old Scene payload is not a worker instruction.
            self.coordinator.queue.enqueue({'task_id':task_id,'project_id':scene.project_id,'role':'director',
                'expected_output':'scene','input_versions':inputs,'context':{'project_id':scene.project_id,
                'scene_id':scene.scene_id,'scene_version':scene.version,'v2_contract':contract.model_dump(),
                'revision_candidate':value['prior_candidate'],
                'fixture_result':scene.model_dump()},'depends_on':dependencies})
            return {'task_id':task_id,'status':'PENDING','simulated':True,'production_started':False}

    def verify_compiled(self, compiled):
        root = Path(self.engine_root).resolve()
        project = Path(compiled['project_mlt']).resolve()
        if not project.is_relative_to(root):
            raise StudioError('PATH_UNSAFE','Compiled project is outside the engine workspace')
        if content_hash(project) != compiled['project_sha256']:
            raise StudioError('ASSET_CHANGED','Compiled MLT changed')
        for relative, sha in compiled['file_hashes'].items():
            path = workspace_path(project.parent,relative)
            if not path.is_file() or content_hash(path) != sha:
                raise StudioError('ASSET_CHANGED','Prepared media or output changed')
        return True


# Shared existing-engine primitives, extracted unchanged from PILOT-01.
import hashlib
import os
import re
from pathlib import PureWindowsPath
import subprocess
import time
import xml.etree.ElementTree as ET
from .jobs import JobManager
from .registry import write_generated_json
from .tts import wav_info
from .v2_narration import profile_spec

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


def verify_portable(folder, *, allowed_suffixes=None):
    folder = Path(folder).resolve()
    manifest = json.loads((folder/'manifest.json').read_text(encoding='utf-8'))
    if {p.relative_to(folder).as_posix() for p in folder.rglob('*') if p.is_file()} != set(manifest['files']) | {'manifest.json'}:
        raise StudioError('PORTABLE_INVALID', 'Unlisted files in portable package')
    for relative, meta in manifest['files'].items():
        path = workspace_path(folder, relative)
        if path.suffix.lower() not in (allowed_suffixes or {'.mp4', '.mlt', '.json', '.srt', '.png', '.wav'}):
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


class MeasuredEngine:
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
                idempotency_key=derived_id('register', identifier))
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
