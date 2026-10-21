"""General host-authorized orchestration over Coordinator, V2ResultGate and the engine.

There is no second queue: accepted checkpoints are the existing ai_tasks results.
Unaccepted action outputs are retained in ai_events and always pass the same gate.
"""
from __future__ import annotations

from copy import deepcopy
import json
from pathlib import Path
import shutil
import time
import uuid
import wave

from .contracts import ProjectBrief
from .editorial import EditPlan, Placement, Review, Shot, SubtitleEvent, validate_review
from .jobs import JobManager
from .production_bridge import ProductionBridge
from .registry import Registry, StudioError, canonical_json, content_hash, workspace_path, write_generated_json
from .tts import wav_info
from .v2_acceptance import fingerprint
from .v2_contracts import Artifact, Result, Task
from .v2_ids import derived_id
from .v2_production import MeasuredEngine, OfflineProductionBridge, verify_audio, verify_portable
from .v2_run_config import HostRunConfig, graph, isolated_root, no_links, plan, storage, verify_inputs

REQUIRED_OPERATION = {'wav': 'synthesize', 'compile': 'compile', 'render': 'render',
                      'qa': 'qa', 'portable': 'portable'}
PACKAGE_SUFFIXES = {'.mp4', '.mlt', '.json', '.srt', '.png', '.jpg', '.jpeg', '.wav'}


def delivery_node(config):
    parent = graph(config)[-1]['task_id']
    value = {'drive': config.drive.model_dump() if config.drive else None, 'version': config.version}
    return {'task_id': derived_id(config.run_id, 'deliver', 'h'+fingerprint({'parent': parent, **value})),
        'stage': 'deliver', 'value': value, 'dependencies': [parent], 'profile': config.profile_sha256,
        'mode': config.mode, 'run_id': config.run_id, 'scene_id': None, 'version': config.version, 'schema': 'note'}


class SystemPermit:
    """Constructed only by an explicit host invocation, never a model Result."""
    def __init__(self, config, *, opt_in=False, authorize=False):
        if opt_in is not True:
            raise StudioError('SYSTEM_PERMISSION_REQUIRED', 'Explicit --execute-system is required')
        self.config = config
        self.document = config.pinned()
        self.root = isolated_root(config.root)
        self.sha256 = fingerprint(self.document)
        report = plan(config)
        if report['blockers']:
            raise StudioError('BLOCKED_UNSUPPORTED', '; '.join(report['blockers']))
        storage(config)
        file = self.root/'authorizations'/(self.sha256+'.json')
        if not file.exists():
            if not authorize:
                raise StudioError('SYSTEM_PERMISSION_REQUIRED', 'New inputs require an explicit --authorize')
            if config.version == 1:
                if self.root.exists():
                    raise StudioError('ROOT_NOT_EMPTY', 'Choose a new isolated root; existing data is preserved')
                self.root.mkdir(parents=True, exist_ok=False)
            else:
                parent_file = self.root/'authorizations'/(config.parent_sha256+'.json')
                parent = HostRunConfig.model_validate_json(no_links(parent_file).read_text(encoding='utf-8'))
                if (fingerprint(parent.pinned()) != config.parent_sha256 or parent.version+1 != config.version
                        or (parent.run_id, parent.key, parent.mode, parent.root) !=
                           (config.run_id, config.key, config.mode, str(self.root))):
                    raise StudioError('IDEMPOTENCY_CONFLICT', 'Revision must extend its exact host configuration')
                old = {s.scene_id: s for s in parent.inputs.scenes}
                for scene in config.inputs.scenes:
                    previous = old.get(scene.scene_id)
                    if previous and scene != previous and scene.version != previous.version+1:
                        raise StudioError('STALE_INPUTS', 'A changed Scene requires the next explicit version')
            write_generated_json(self.root, 'authorizations/'+file.name, self.document)
        self.configs = []
        current = self.sha256
        while current:
            path = no_links(self.root/'authorizations'/(current+'.json'))
            value = HostRunConfig.model_validate_json(path.read_text(encoding='utf-8'))
            if fingerprint(value.pinned()) != current or value.root != str(self.root):
                raise StudioError('PERMIT_CHANGED', 'Immutable host authorization changed')
            self.configs.append((current, value))
            current = value.parent_sha256
            if len(self.configs) > 100:
                raise StudioError('PERMIT_CHANGED', 'Authorization chain exceeds its bounded depth')
        # Oldest authority wins for identical nodes. A revision never expands it.
        self.nodes = {}
        for authority, value in reversed(self.configs):
            for node in graph(value):
                self.nodes.setdefault(node['task_id'], (authority, value, node))
            node = delivery_node(value)
            self.nodes.setdefault(node['task_id'], (authority, value, node))
        self.validate()

    def validate(self):
        if fingerprint(self.config.pinned()) != self.sha256:
            raise StudioError('PERMIT_CHANGED', 'In-memory host configuration changed')
        for authority, value in self.configs:
            file = no_links(self.root/'authorizations'/(authority+'.json'))
            if json.loads(file.read_text(encoding='utf-8')) != value.pinned():
                raise StudioError('PERMIT_CHANGED', 'Host authorization bytes changed')
        verify_inputs(self.config)
        no_links(self.root/'coordinator')
        no_links(self.root/'engine')

    def node(self, identifier):
        try:
            return self.nodes[identifier]
        except KeyError as exc:
            raise StudioError('SYSTEM_PERMISSION_REQUIRED', 'Node is outside the host approved plan') from exc

    def validate_admission(self, coordinator_root, contract, task, requested, result):
        self.validate()
        authority, config, node = self.node(contract.task_id)
        expected = {'node': fingerprint(node), 'authority': authority, 'profile': config.profile_sha256}
        if (Path(coordinator_root) != self.root/'coordinator' or contract.run_id != config.run_id
                or contract.input_hashes != expected or contract.input_version != node['version']
                or contract.depends_on != node['dependencies'] or contract.capability != 'system_'+node['stage']
                or result.simulated != (config.mode == 'simulation')
                or any(v['context'].get('system_authority') != authority for v in (task, requested))):
            raise StudioError('SYSTEM_PERMISSION_REQUIRED', 'Attempt differs from the approved root/run/input/operations')
        receipt = no_links(self.root/'host_receipts'/contract.task_id/(result.acceptance_key+'.json'))
        if json.loads(receipt.read_text(encoding='utf-8')) != result.model_dump():
            raise StudioError('HOST_RECEIPT_INVALID', 'Completion has no exact host execution receipt')
        operation = 'deliver' if node['stage'] == 'deliver' else REQUIRED_OPERATION.get(node['stage'])
        if config.mode != 'simulation' and operation and operation not in config.operations:
            raise StudioError('SYSTEM_PERMISSION_REQUIRED', 'Operation was not authorized')
        if result.payload != node['value'] and node['stage'] == 'scene':
            raise StudioError('STALE_INPUTS', 'Scene payload differs from approved inputs')
        if node['stage'] != 'scene':
            output = json.loads(result.payload['text'])
            verify_output(self.root, output, simulated=result.simulated)


def verify_output(root, value, *, simulated):
    """Every action output pins its complete immutable local file inventory."""
    if value.get('simulated') is not simulated:
        raise StudioError('ARTIFACT_INVALID', 'Stage provenance changed')
    for relative, meta in value.get('files', {}).items():
        path = no_links(workspace_path(root, relative))
        if not path.is_file() or path.stat().st_size != meta['size'] or content_hash(path) != meta['sha256']:
            raise StudioError('ARTIFACT_INVALID', 'Accepted stage bytes changed')
    if not simulated and value.get('audio') and value.get('candidate'):
        from .v2_storage import ArtifactStore
        candidate = Artifact.model_validate(value['candidate'])
        raw = json.loads(ArtifactStore(Path(root)/'coordinator').verify(candidate).read_text(encoding='utf-8'))
        if verify_audio(Path(root)/'engine', candidate.run_id, raw, value['audio']['job_id']) != value['audio']:
            raise StudioError('AUDIO_RECEIPT_INVALID', 'Accepted WAV job/request binding changed on replay')
    if value.get('portable'):
        folder = workspace_path(root, value['portable'])
        manifest = verify_portable(folder, allowed_suffixes=PACKAGE_SUFFIXES)
        if content_hash(folder/'manifest.json') != value['portable_manifest_sha256'] or manifest['run_id'] != value['run_id']:
            raise StudioError('PORTABLE_INVALID', 'Portable identity or manifest changed')
    return value


class StageAdapter:
    is_local = True
    def __init__(self, pipeline):
        self.pipeline = pipeline
        self.is_simulated = pipeline.config.mode == 'simulation'
        self.runs = {}
        self.cancelled = set()

    def start(self, task, profile, attempt_dir=None):
        handle = uuid.uuid4().hex
        self.runs[handle] = deepcopy(task)
        return handle

    def perform(self, task):
        identifier = task['task_id']
        authority, config, node = self.pipeline.permit.node(identifier)
        self.pipeline.permit.validate()
        storage(config)
        operation = REQUIRED_OPERATION.get(node['stage'])
        if config.mode != 'simulation' and operation and operation not in config.operations:
            raise StudioError('SYSTEM_PERMISSION_REQUIRED', 'This host authorization excludes '+operation)
        current = self.pipeline.config
        try:
            # Pending reused nodes retain their original runtime and budgets;
            # a new host policy produces new IDs, never expands the old permit.
            self.pipeline.config = config
            output = self.pipeline.execute(node)
            storage(config)
        finally:
            self.pipeline.config = current
        payload = node['value'] if node['stage'] == 'scene' else {'schema_version': 1,
            'simulated': self.is_simulated, 'text': canonical_json(output)}
        artifact = self.pipeline.q.store.put_json(config.run_id, payload, simulated=self.is_simulated)
        contract = Task.model_validate(task['context']['v2_contract'])
        result = Result(task_id=identifier, run_id=config.run_id, acceptance_key=task['attempt_id'],
            expected_output_schema=contract.expected_output_schema, input_version=contract.input_version,
            input_hashes=contract.input_hashes, payload=payload, artifacts=[artifact], simulated=self.is_simulated)
        write_generated_json(self.pipeline.root, f'host_receipts/{identifier}/{task["attempt_id"]}.json', result.model_dump())
        return result.model_dump()

    def poll(self, handle):
        if handle in self.cancelled:
            self.cancelled.remove(handle)
            return [{'type': 'interrupted', 'failure': {'kind': 'interrupted',
                'message': 'Host stop preserved all existing outputs', 'retryable': False}}]
        task = self.runs.pop(handle, None)
        if not task:
            return []
        try:
            result = self.perform(task)
            return [{'type': 'completed', 'run_id': handle, 'result': result['payload'], 'host_metadata': result,
                     'model': None, 'simulated': self.is_simulated}]
        except Exception as exc:
            import traceback
            relative = 'failures/'+task['attempt_id']+'.json'
            write_generated_json(self.pipeline.root, relative, {'exception': type(exc).__name__,
                'message': str(exc), 'traceback': traceback.format_exc(), 'truncated': False})
            code = getattr(exc, 'code', type(exc).__name__)
            kind = {'DRIVE_AUTH': 'authentication', 'DRIVE_QUOTA': 'quota_exhausted', 'LOCAL_JOB_TIMEOUT': 'interrupted'}.get(code, 'invalid_input')
            return [{'type': 'failed', 'failure': {'kind': kind, 'code': code,
                'message': str(exc)[:1000], 'log_ref': relative, 'truncated': len(str(exc)) > 1000, 'retryable': False}}]

    def cancel(self, handle):
        self.runs.pop(handle, None)
        self.cancelled.add(handle)

    def recover(self, attempt_dir):
        request = json.loads((Path(attempt_dir)/'request.json').read_text(encoding='utf-8'))
        file = self.pipeline.root/'host_receipts'/request['task_id']/(request['attempt_id']+'.json')
        if file.is_file():
            result = json.loads(file.read_text(encoding='utf-8'))
            return {'status': 'completed', 'result': result['payload'], 'host_metadata': result}
        # This adapter owns no child provider process. Reattach the same durable
        # attempt; existing production JobManager keys and action checkpoints
        # handle any unfinished local work without another acceptance path.
        handle = uuid.uuid4().hex
        self.runs[handle] = request
        return {'status': 'running', 'handle': handle}


class SystemPipeline(MeasuredEngine):
    def __init__(self, config, *, opt_in=False, authorize=False):
        from .multi_ai_v2 import RunCoordinator
        self.config = config
        self.permit = SystemPermit(config, opt_in=opt_in, authorize=authorize)
        self.root, self.run_id = self.permit.root, config.run_id
        self.engine = self.root/'engine'
        adapter = StageAdapter(self)
        adapters = {'codex': adapter, 'gemini': adapter} if config.mode == 'simulation' else {'local': adapter}
        self.q = RunCoordinator(self.root/'coordinator', adapters=adapters, system_permit=self.permit)
        self.bridge = OfflineProductionBridge(self.q, self.engine, source_bundle={
            'canon': config.inputs.canon.model_dump(), 'root': Path(config.input_root),
            'manifest_sha256': fingerprint(__import__('app.v2_sources', fromlist=['source_manifest']).source_manifest(config.inputs.canon))})
        try:
            with Registry(self.q.root) as registry, registry.transaction():
                versions = [json.loads(r[0]) for r in registry.db.execute(
                    "SELECT payload FROM ai_events WHERE kind='system_configuration' AND task_id=? ORDER BY sequence", (self.run_id,))]
                if versions and versions[-1]['sha256'] != self.permit.sha256 and config.parent_sha256 != versions[-1]['sha256']:
                    raise StudioError('IDEMPOTENCY_CONFLICT', 'Run key already has another current input version')
                initial = self.permit.configs[-1][0]
                prior = registry.db.execute('SELECT request_hash FROM ai_v2_runs WHERE run_id=?', (self.run_id,)).fetchone()
                binding = fingerprint({'key': config.key, 'initial': initial})
                if prior and prior[0] != binding:
                    raise StudioError('IDEMPOTENCY_CONFLICT', 'Run key/root already bind other inputs')
                registry.db.execute('INSERT OR IGNORE INTO ai_v2_runs VALUES(?,?,?,?,?)',
                    (self.run_id, binding, canonical_json(config.brief.model_dump()), config.brief.production_mode, time.time()))
                if not versions or versions[-1]['sha256'] != self.permit.sha256:
                    self.q.queue._event(registry, 'system_configuration', {'sha256': self.permit.sha256,
                        'version': config.version, 'key': config.key}, self.run_id)
            self.nodes = graph(config)
            for node in self.nodes:
                self.enqueue(node)
        except BaseException:
            self.q.close()
            raise

    def close(self):
        self.q.close()

    def enqueue(self, node):
        authority, config, original = self.permit.node(node['task_id'])
        hashes = {'node': fingerprint(node), 'authority': authority, 'profile': config.profile_sha256}
        contract = Task(task_id=node['task_id'], run_id=self.run_id, scene_id=node['scene_id'], role='python',
            capability='system_'+node['stage'], input_version=node['version'], input_hashes=hashes,
            depends_on=node['dependencies'], assigned_worker='host', provider_profile_id='simulation' if config.mode == 'simulation' else 'local-heavy',
            expected_output_schema=node['schema'], idempotency_key=node['task_id'])
        context = {'project_id': self.run_id, 'v2_contract': contract.model_dump(), 'system_authority': authority}
        if node['scene_id']:
            context.update(scene_id=node['scene_id'], scene_version=node['version'])
        self.q.queue.enqueue({'task_id': node['task_id'], 'project_id': self.run_id, 'role': 'director',
            'resource_class': 'cloud' if config.mode == 'simulation' else 'local_heavy',
            'expected_output': node['schema'], 'input_versions': hashes, 'depends_on': node['dependencies'],
            'context': context, 'max_attempts': config.budget.attempts})

    def accepted(self, identifier):
        with Registry(self.q.root) as registry:
            row = registry.db.execute('SELECT * FROM ai_tasks WHERE id=?', (identifier,)).fetchone()
            accepted = self.q.result_gate.accepted_dependency(registry, row)
        payload = accepted['payload']
        return payload if 'text' not in payload else json.loads(payload['text'])

    def output_files(self, value, paths):
        value = {**value, 'simulated': False, 'files': {}}
        for path in paths:
            path = no_links(path)
            relative = path.relative_to(self.root).as_posix()
            value['files'][relative] = {'sha256': content_hash(path), 'size': path.stat().st_size}
        return value

    def execute(self, node):
        self.permit.validate()
        dependencies = [self.accepted(d) for d in node['dependencies']]
        with Registry(self.q.root) as registry:
            old = registry.db.execute("SELECT payload FROM ai_events WHERE kind='system_output' AND task_id=? ORDER BY sequence LIMIT 1",
                                      (node['task_id'],)).fetchone()
        if old:
            return verify_output(self.root, json.loads(old[0]), simulated=self.config.mode == 'simulation')
        if self.config.mode == 'simulation':
            output = {'simulated': True, 'stage': node['stage'], 'node_sha256': fingerprint(node),
                      'dependency_hashes': [fingerprint(d) for d in dependencies], 'production_started': False,
                      'uploaded': False, 'files': {}}
        else:
            output = self.execute_engine(node, dependencies)
        with Registry(self.q.root) as registry, registry.transaction():
            self.q.queue._event(registry, 'system_output', output, node['task_id'])
        return output

    def engine_ready(self):
        self.permit.validate()
        from .production_profile import check_runtime
        path = self.engine/'runtime.json'
        if not path.exists():
            write_generated_json(self.engine, 'runtime.json', self.config.runtime)
        elif json.loads(path.read_text(encoding='utf-8')) != self.config.runtime:
            raise StudioError('PROFILE_CHANGED', 'Existing engine runtime differs from the host configuration')
        from .v2_narration import profile_spec
        check_runtime(profile_spec(), Path(__file__).resolve().parents[1], self.engine)
        with Registry(self.engine) as registry:
            if not registry.db.execute('SELECT 1 FROM projects WHERE id=?', (self.run_id,)).fetchone():
                registry.initialize(ProjectBrief(project_id=self.run_id, title=self.config.brief.topic,
                    work_title=self.config.brief.topic, character='Host approved'))

    def execute_engine(self, node, deps):
        stage = node['stage']
        if stage == 'deliver':
            from .v2_drive import deliver_package
            if not self.config.drive or not self.config.drive.network_write_authorized:
                raise StudioError('SYSTEM_PERMISSION_REQUIRED', 'Drive network write was not authorized')
            return {'simulated': False, 'files': {}, 'delivery': deliver_package(self, deps[0], self.drive_transport)}
        if stage in {'sources', 'story', 'scene'}:
            return {'simulated': False, 'stage': stage, 'approved_input_sha256': fingerprint(node['value']),
                    'origin': 'owner_supplied_not_model_research', 'files': {}}
        if stage == 'render':
            # The protected existing renderer has a fixed 180s deadline. Reserve
            # that complete slot rather than silently exceeding a smaller host budget.
            remaining = min(self.config.budget.seconds,
                getattr(self, 'invocation_deadline', time.monotonic()+self.config.budget.seconds)-time.monotonic())
            if remaining < 180:
                raise StudioError('TIME_BUDGET', 'Existing renderer needs a full 180s slot in the explicit host budget')
        self.engine_ready()
        if stage == 'candidate':
            for asset in node['value']['media']:
                source = no_links(Path(self.config.input_root)/asset['path'])
                relative = 'approved_media/'+asset['sha256']+source.suffix.lower()
                destination = workspace_path(self.engine, relative, for_write=True)
                if not destination.exists():
                    destination.parent.mkdir(parents=True, exist_ok=True)
                    with source.open('rb') as src, destination.open('xb') as dst:
                        shutil.copyfileobj(src, dst)
                if content_hash(destination) != asset['sha256']:
                    raise StudioError('SOURCE_CHANGED', 'Copied approved media changed')
                with Registry(self.engine) as registry:
                    prior = registry.db.execute('SELECT 1 FROM assets WHERE project_id=? AND id=?', (self.run_id, asset['asset_id'])).fetchone()
                    if not prior:
                        registry.register_asset(self.run_id, asset['asset_id'], relative, kind=asset['kind'],
                            rights=asset['rights'], duration_ms=asset['duration_ms'],
                            base_revision=registry._project(self.run_id)['revision'], idempotency_key=derived_id('media', asset['asset_id']))
                    current = registry.asset(self.run_id, asset['asset_id'], verify=True)
                    if current['sha256'] != asset['sha256'] or current['rights'] != asset['rights']:
                        raise StudioError('IDEMPOTENCY_CONFLICT', 'Media ID already binds another asset')
            artifact = self.bridge.candidate(self.run_id, node['dependencies'][0], node['version'], node['value']['mappings'])
            return self.output_files({'candidate': artifact}, [self.q.store.verify(Artifact.model_validate(artifact))])
        if stage == 'wav':
            candidate = deps[0]['candidate']
            value = self.bridge.read_candidate(candidate)
            audio = self.synthesize(value, node['task_id'])
            paths = [workspace_path(self.engine, a['path']) for a in audio['segments']]
            return self.output_files({'audio': audio, 'candidate': candidate}, paths)
        if stage == 'edit':
            candidate = self.bridge.read_candidate(deps[0]['candidate'])
            scene = self.accepted(candidate['binding']['task_id'])
            edit, assets = self.measured_edit(scene, deps[1]['audio'], node)
            return self.output_files({'edit_plan': edit.model_dump(), 'assets': assets,
                                      'audio': deps[1]['audio']}, [workspace_path(self.engine, a['path']) for a in assets])
        if stage == 'assembly':
            pictures, voices, subtitles, assets, cursor = [], [], [], {}, 0
            for index, dependency in enumerate(deps):
                edit = EditPlan.model_validate(dependency['edit_plan'])
                rendered = dependency['rendered']
                video = Path(rendered['path'])
                asset = self.register(derived_id('clip', 'h'+rendered['sha256']), video.relative_to(self.engine).as_posix(),
                    'video', duration_ms=edit.duration_ms)
                assets[asset['id']] = asset
                shot = Shot(shot_id=derived_id('picture', edit.scene_id), scene_id='assembly', beat_id=edit.scene_id,
                    asset_id=asset['id'], source_in_ms=0, source_out_ms=edit.duration_ms,
                    visual_function='Reuse accepted scene render', observed_content='Technical decode only')
                pictures.append(Placement(placement_id=shot.shot_id, asset_id=asset['id'], track='video', shot=shot,
                    timeline_start_ms=cursor, timeline_end_ms=cursor+edit.duration_ms, source_in_ms=0, source_out_ms=edit.duration_ms))
                for placement in edit.voice_placements:
                    voices.append(placement.model_copy(update={'timeline_start_ms': placement.timeline_start_ms+cursor,
                        'timeline_end_ms': placement.timeline_end_ms+cursor}))
                for subtitle in edit.subtitle_events:
                    subtitles.append(subtitle.model_copy(update={'start_ms': subtitle.start_ms+cursor, 'end_ms': subtitle.end_ms+cursor}))
                for item in dependency['assets']:
                    assets[item['id']] = item
                cursor += edit.duration_ms
            edit = EditPlan(project_id=self.run_id, plan_id=derived_id('assembly', node['task_id']), scene_id='assembly',
                version=self.config.version, scene_version=self.config.version, voice_version=fingerprint([d['edit_plan']['voice_version'] for d in deps]),
                duration_ms=cursor, shot_placements=pictures, voice_placements=voices, subtitle_events=subtitles,
                important_decisions=['Accepted scene picture; original separate WAV; creative review pending.'])
            return self.output_files({'edit_plan': edit.model_dump(), 'assets': list(assets.values())},
                                     [workspace_path(self.engine, a['path']) for a in assets.values()])
        if stage == 'compile':
            value = deps[0]
            compiled = ProductionBridge(self.engine).compile(EditPlan.model_validate(value['edit_plan']), value['assets'],
            'builds/'+node['stage']+'_'+fingerprint(node)[:24]+'_'+uuid.uuid4().hex[:8])
            folder = Path(compiled['project_mlt']).parent
            return self.output_files({**value, 'compiled': compiled}, [workspace_path(folder, name) for name in compiled['file_hashes']])
        if stage == 'render':
            value = deps[0]
            video = Path(value['compiled']['project_mlt']).with_name('final.mp4')
            rendered = ProductionBridge(self.engine).render(value['compiled'], video, qmelt=self.config.runtime['melt_path'])
            rendered = {**rendered, 'path': rendered['output']}
            return self.output_files({**value, 'rendered': rendered}, [video, *[workspace_path(self.root, p) for p in value['files']]])
        if stage == 'qa':
            value = deps[0]
            qa = self.qa(value['compiled'], value['rendered']['path'])
            return self.output_files({**value, 'qa': qa}, [workspace_path(self.root, p) for p in value['files']])
        if stage == 'portable':
            return self.portable(deps[0], node)
        raise StudioError('BLOCKED_UNSUPPORTED', 'No registered engine handler: '+stage)

    def synthesize(self, candidate, key):
        deadline = min(time.monotonic()+self.config.budget.seconds,
                       getattr(self, 'invocation_deadline', float('inf')))
        if deadline <= time.monotonic():
            raise StudioError('TIME_BUDGET', 'Host deadline expired before job admission')
        script_id = derived_id('script', 'h'+fingerprint(candidate['script']))
        relative = 'scripts/'+script_id+'.json'
        path = self.engine/relative
        if not path.exists():
            write_generated_json(self.engine, relative, candidate['script'])
        self.register(script_id, relative, 'text')
        manager = JobManager(self.engine)
        with Registry(self.engine) as registry:
            prior = registry.db.execute('SELECT response FROM requests WHERE project_id=? AND key=?', (self.run_id, key)).fetchone()
            base = manager.status(json.loads(prior[0])['job_id'])['input_revision']-1 if prior else registry._project(self.run_id)['revision']
        ProductionBridge(self.engine).prepare_request(self.run_id, 'synthesize', {'script_asset_id': script_id}, base, key)
        receipt = manager.enqueue(self.run_id, 'synthesize', {'script_asset_id': script_id}, base, key)
        while manager.status(receipt['job_id'])['state'] in {'pending', 'running'}:
            if time.monotonic() > deadline:
                raise StudioError('LOCAL_JOB_TIMEOUT', 'Existing job is preserved; resume the same run/key')
            time.sleep(.25)
        return verify_audio(self.engine, self.run_id, candidate, receipt['job_id'])

    def command(self, label, command, *, timeout=180):
        remaining = min(self.config.budget.seconds,
            getattr(self, 'invocation_deadline', time.monotonic()+self.config.budget.seconds)-time.monotonic())
        if remaining <= 0:
            raise StudioError('TIME_BUDGET', 'Host deadline expired before local tool admission')
        return super().command(label, command, timeout=min(timeout, remaining))

    def measured_edit(self, scene, audio, node):
        pictures, voices, subtitles, assets, cursor = [], [], [], {}, 0
        mapping = {m.segment_id: m for m in self.config.inputs.visuals if m.scene_id == scene['scene_id']}
        # Mapping is explicit; derived IDs are never decoded by stripping prefixes.
        candidate = self.bridge.read_candidate(self.accepted(node['dependencies'][0])['candidate'])
        by_segment = {m['segment_id']: m['block_id'] for m in candidate['scene_block_mapping']}
        by_block = {a['block_id']: a for a in audio['segments']}
        for segment in scene['narration_segments']:
            record = by_block[by_segment[segment['segment_id']]]
            wav = workspace_path(self.engine, record['path']); info = wav_info(wav)
            duration = int(info['duration_seconds']*10)*100
            if duration <= 0:
                raise StudioError('NARRATION_TRUNCATION', 'Narration is shorter than one alignment unit')
            with wave.open(str(wav), 'rb') as stream:
                stream.setpos(duration*stream.getframerate()//1000)
                if any(stream.readframes(stream.getnframes()-stream.tell())):
                    raise StudioError('NARRATION_TRUNCATION', 'Alignment would remove speech samples')
            voice = self.register(derived_id('wav', 'h'+record['wav_sha256']), record['path'], 'audio', duration_ms=info['duration_ms'])
            assets[voice['id']] = voice
            selected = mapping[segment['segment_id']]
            shots = selected.shots
            with Registry(self.engine) as registry:
                rows = [registry.asset(self.run_id, s.asset_id, verify=True) for s in shots]
            if len(shots) == 1 and rows[0]['kind'] == 'image':
                shots = [shots[0].model_copy(update={'source_out_ms': duration})]
            elif selected.duration_ms != duration:
                raise StudioError('VISUAL_TIMING_REQUIRES_APPROVAL', 'Measured WAV differs from approved video trim/multi-shot timing')
            start = cursor
            for selected_shot, asset in zip(shots, rows):
                length = selected_shot.source_out_ms-selected_shot.source_in_ms
                shot = Shot(shot_id=derived_id(scene['scene_id'], selected_shot.shot_id), scene_id=scene['scene_id'],
                    beat_id=segment['segment_id'], asset_id=asset['id'], source_in_ms=selected_shot.source_in_ms,
                    source_out_ms=selected_shot.source_out_ms, visual_function=selected_shot.emotional_task,
                    observed_content='Approved metadata only; no artistic observation', reuse_reason=selected_shot.reuse_reason)
                pictures.append(Placement(placement_id=shot.shot_id, asset_id=asset['id'], track='video', shot=shot,
                    timeline_start_ms=cursor, timeline_end_ms=cursor+length, source_in_ms=shot.source_in_ms, source_out_ms=shot.source_out_ms))
                assets[asset['id']] = asset; cursor += length
            voices.append(Placement(placement_id=derived_id('audio', scene['scene_id'], segment['segment_id']), asset_id=voice['id'],
                track='voice', timeline_start_ms=start, timeline_end_ms=cursor, source_in_ms=0, source_out_ms=duration))
            subtitles.append(SubtitleEvent(start_ms=start, end_ms=cursor, text=segment['text']))
        edit = EditPlan(project_id=self.run_id, plan_id=derived_id('edit', scene['scene_id'], 'v'+str(scene['version'])),
            scene_id=scene['scene_id'], version=scene['version'], scene_version=scene['version'], voice_version=fingerprint(audio),
            duration_ms=cursor, shot_placements=pictures, voice_placements=voices, subtitle_events=subtitles,
            important_decisions=['Measured verified WAV; only trailing zero PCM aligned; approved media only.',
                'Single still holds for measured speech; video/multi-shot timing changes need owner approval.',
                'Explicit existing_mlt_libx264_crf20_fast_4threads renderer; technical QA is separate from creative review.'])
        return edit, list(assets.values())

    def portable(self, value, node):
        folder = self.root/'packages'/('package_'+fingerprint(node)[:24])
        folder.mkdir(parents=True, exist_ok=True)
        source = Path(value['compiled']['project_mlt']).parent
        files = {}
        for name in sorted(set(value['compiled']['file_hashes']) | {'final.mp4'}):
            src = workspace_path(source, name); dst = workspace_path(folder, name, for_write=True)
            dst.parent.mkdir(parents=True, exist_ok=True)
            if not dst.exists():
                with src.open('rb') as a, dst.open('xb') as b:
                    shutil.copyfileobj(a, b)
            if content_hash(src) != content_hash(dst):
                raise StudioError('PORTABLE_INVALID', 'Partial package has conflicting bytes; preserved')
            files[name] = {'sha256': content_hash(dst), 'bytes': dst.stat().st_size}
        qa = {k: v for k, v in value['qa'].items() if not k.endswith('_log')}
        if not (folder/'qa.json').exists():
            write_generated_json(folder, 'qa.json', qa)
        if json.loads((folder/'qa.json').read_text(encoding='utf-8')) != qa:
            raise StudioError('PORTABLE_INVALID', 'Existing portable QA differs')
        files['qa.json'] = {'sha256': content_hash(folder/'qa.json'), 'bytes': (folder/'qa.json').stat().st_size}
        manifest = {'run_id': self.run_id, 'version': self.config.version, 'files': files,
            'uploaded': False, 'remote_sync': 'UNVERIFIED', 'editorial_review': 'PENDING_OWNER', 'full_video_creative_qa': 'NOT_RUN'}
        if not (folder/'manifest.json').exists():
            write_generated_json(folder, 'manifest.json', manifest)
        if verify_portable(folder, allowed_suffixes=PACKAGE_SUFFIXES) != manifest:
            raise StudioError('PORTABLE_INVALID', 'Existing package manifest differs')
        return self.output_files({'portable': folder.relative_to(self.root).as_posix(), 'run_id': self.run_id,
            'portable_manifest_sha256': content_hash(folder/'manifest.json'), 'rendered': value['rendered'],
            'qa': value['qa'], 'version': self.config.version, 'uploaded': False, 'remote_sync': 'UNVERIFIED'},
            [folder/'manifest.json', *[workspace_path(folder, n) for n in files]])

    def status(self):
        snapshot = self.q.queue.snapshot()
        tasks = {t['task_id']: t for t in snapshot['tasks']}
        selected = [tasks[n['task_id']] for n in self.nodes]
        completed = [t for t in selected if t['status'] == 'completed']
        current = next((t for t in selected if t['status'] != 'completed'), None)
        ready = current is None
        blocker = current.get('error') if current else None
        failures = {'authentication': 'WAITING_AUTH', 'quota_exhausted': 'WAITING_QUOTA', 'interrupted': 'FAILED_RETRYABLE'}
        status = ('SIMULATION_SUCCEEDED' if self.config.mode == 'simulation' else 'READY_FOR_OWNER_REVIEW') if ready else (
            failures.get(blocker.get('kind'), 'FAILED_FINAL') if blocker else 'CANCELLED' if snapshot['control'] == 'stopped' else 'IN_PROGRESS')
        package = self.accepted(self.nodes[-1]['task_id']) if ready else None
        owner_review, delivery = None, None
        if ready and self.config.mode != 'simulation':
            with Registry(self.q.root) as registry:
                for row in registry.db.execute("SELECT payload FROM ai_events WHERE kind='system_owner_review' AND task_id=? ORDER BY sequence DESC", (self.run_id,)):
                    value = json.loads(row[0])
                    if value['package_sha256'] == package['portable_manifest_sha256'] and value['version'] == package['version']:
                        owner_review = json.loads(self.q.store.verify(Artifact.model_validate(value['artifact'])).read_text(encoding='utf-8'))
                        break
                row = registry.db.execute('SELECT status,error FROM ai_tasks WHERE id=?', (delivery_node(self.config)['task_id'],)).fetchone()
            if row and row['status'] == 'completed':
                delivery = self.accepted(delivery_node(self.config)['task_id'])['delivery']
            elif row:
                delivery = {'status': row['status'], 'blocker': json.loads(row['error']) if row['error'] else None,
                            'uploaded': False}
                blocker = blocker or delivery['blocker']
        return {'status': status, 'run_id': self.run_id, 'version': self.config.version, 'mode': self.config.mode,
            'summary': f'{len(completed)}/{len(selected)} stages accepted; '+('technical path complete; owner review remains separate' if ready else 'resume the same inputs or inspect the recorded blocker'),
            'completed': len(completed), 'total': len(selected), 'current_stage': current['context']['v2_contract']['capability'] if current else 'system_deliver' if delivery and delivery['status'] in {'pending','running','waiting_access','blocked','failed','interrupted'} else None,
            'blocker': blocker, 'control': snapshot['control'], 'results': package, 'root': str(self.root),
            'safe_repeat': 'resume with the identical config, key and --execute-system', 'owner_action_required': ready and self.config.mode != 'simulation',
            'creative_review': 'OWNER_REVIEW_RECORDED' if owner_review else 'PENDING_OWNER',
            'owner_review': owner_review, 'model_video_review': 'NOT_RUN', 'uploaded': bool(delivery and delivery['uploaded']), 'delivery': delivery,
            'model_requests': 0, 'paid_api_requests': 0, 'production_started': self.production_started(),
            'system_ready_for_autonomous_episode': False, 'truncated': False}

    def production_started(self):
        import sqlite3
        from contextlib import closing
        file = self.engine/'registry.sqlite'
        if not file.exists():
            return False
        with closing(sqlite3.connect(file.as_uri()+'?mode=ro', uri=True)) as conn:
            tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            return bool('jobs' in tables and conn.execute('SELECT 1 FROM jobs LIMIT 1').fetchone())

    def run(self, *, ticks=None):
        self.permit.validate()
        self.q.queue.resume()
        deadline = time.monotonic()+self.config.budget.seconds
        self.invocation_deadline = deadline
        for _ in range(ticks if ticks is not None else len(self.nodes)*3):
            self.q.queue.tick()
            state = self.status()
            if state['status'] not in {'IN_PROGRESS'} or time.monotonic() > deadline:
                return state
        return self.status()

    def owner_review(self, value):
        status = self.status()
        if status['status'] != 'READY_FOR_OWNER_REVIEW':
            raise StudioError('REVIEW_NOT_READY', 'Only a technically accepted exact render can receive owner review')
        package = status['results']; rendered = package['rendered']
        review = Review.model_validate(value)
        expected = {'project_id': self.run_id, 'subject_type': 'render', 'subject_id': derived_id('render', 'h'+rendered['sha256']),
            'subject_version': package['version'], 'subject_hash': rendered['sha256'],
            'render_id': derived_id('render', 'h'+rendered['sha256']), 'render_hash': rendered['sha256'], 'edit_plan_version': package['version'],
            'provided_evidence_mode': 'human_review', 'human_review_authorized': True,
            'provided_coverage': [{'start_ms': 0, 'end_ms': int(package['qa']['duration_seconds']*1000),
                                   'tracks': ['video', 'audio']}]}
        validate_review(review, expected)
        if review.simulated or review.evidence_mode != 'human_review':
            raise StudioError('REVIEW_INVALID', 'Owner input must explicitly declare canonical human_review')
        if len(review.issues) > 32:
            raise StudioError('REVIEW_INVALID', 'Targeted fix plan is bounded to 32 explicit issues')
        artifact = self.q.store.put_json(self.run_id, review.model_dump(), simulated=False)
        binding = {'artifact': artifact.model_dump(), 'package_sha256': package['portable_manifest_sha256'],
                   'version': package['version'], 'review_id': review.review_id}
        with Registry(self.q.root) as registry, registry.transaction():
            previous = [json.loads(r[0]) for r in registry.db.execute(
                "SELECT payload FROM ai_events WHERE kind='system_owner_review' AND task_id=?", (self.run_id,))]
            matches = [v for v in previous if v.get('review_id') == review.review_id]
            if matches and any(v != binding for v in matches):
                raise StudioError('IDEMPOTENCY_CONFLICT', 'Review ID already binds another artifact or decision')
            if not matches:
                self.q.queue._event(registry, 'system_owner_review', binding, self.run_id)
        return {'status': 'OWNER_REVIEW_RECORDED', 'review': artifact.model_dump(), 'model_review': False,
            'targeted_fix_plan': [{'issue_id': i.issue_id, 'location': i.location, 'suggested_change': i.suggested_change}
                                  for i in review.issues if i.status == 'open'],
            'automatic_execution': False}

    def deliver(self, transport=None):
        status = self.status()
        if status['status'] != 'READY_FOR_OWNER_REVIEW':
            raise StudioError('DELIVERY_NOT_READY', 'A verified real local release is required')
        if 'deliver' not in self.config.operations or not self.config.drive or not self.config.drive.network_write_authorized:
            raise StudioError('SYSTEM_PERMISSION_REQUIRED', 'Drive requires explicit folder and network write authority')
        if transport is None:
            raise StudioError('WAITING_AUTH', 'No host-injected official Drive authorization; login is never automatic')
        self.drive_transport = transport
        node = delivery_node(self.config)
        self.enqueue(node)
        self.q.queue.resume()
        for _ in range(3):
            self.q.queue.tick()
        task = next(t for t in self.q.queue.snapshot()['tasks'] if t['task_id'] == node['task_id'])
        if task['status'] != 'completed':
            failure = task.get('error') or {}
            state = {'authentication': 'WAITING_AUTH', 'quota_exhausted': 'WAITING_QUOTA',
                     'interrupted': 'FAILED_RETRYABLE'}.get(failure.get('kind'), 'FAILED_FINAL' if failure else 'IN_PROGRESS')
            return {'status': state, 'blocker': failure, 'uploaded': False, 'local_release_preserved': True}
        return self.accepted(node['task_id'])['delivery']


def system_main(argv):
    """The existing app.multi_ai_v2 CLI selects this path only with --config."""
    import argparse
    from .ai_adapters import sanitize
    from .v2_run_config import load_config
    parser = argparse.ArgumentParser(description='SYSTEM-01: general host-approved pipeline')
    parser.add_argument('--config', required=True)
    parser.add_argument('--root')
    parser.add_argument('--execute-system', action='store_true')
    parser.add_argument('--authorize', action='store_true')
    parser.add_argument('command', choices=['plan', 'doctor', 'create', 'prepare', 'run', 'resume', 'pause', 'stop',
        'status', 'inspect-artifacts', 'report', 'approve', 'deliver'])
    parser.add_argument('--review')
    parser.add_argument('--ticks', type=int)
    args = parser.parse_args(argv)
    pipeline = None
    try:
        config = load_config(args.config)
        if args.root and isolated_root(args.root) != isolated_root(config.root):
            raise StudioError('SYSTEM_PERMISSION_REQUIRED', 'CLI root differs from pinned host configuration')
        if args.command in {'plan', 'doctor'}:
            result = plan(config)
            if args.command == 'doctor':
                result['storage'] = storage(config)
                result['write_probe'] = 'NOT_REQUESTED'
        else:
            pipeline = SystemPipeline(config, opt_in=args.execute_system, authorize=args.authorize)
            if args.command in {'run', 'resume'}:
                if args.ticks is not None and args.ticks < 1:
                    raise ValueError('ticks must be positive')
                result = pipeline.run(ticks=args.ticks)
            elif args.command in {'pause', 'stop'}:
                getattr(pipeline.q.queue, args.command)()
                result = pipeline.status()
            elif args.command == 'approve':
                if not args.review:
                    raise ValueError('An exact canonical owner --review JSON is required')
                file = no_links(args.review)
                if file.stat().st_size > 1024*1024:
                    raise ValueError('Review exceeds 1 MiB')
                result = pipeline.owner_review(json.loads(file.read_text(encoding='utf-8-sig')))
            elif args.command == 'deliver':
                result = pipeline.deliver()
            else:
                result = pipeline.status()
        print(json.dumps(sanitize(result), ensure_ascii=False, allow_nan=False))
        return {'PLANNED': 0, 'SIMULATION_SUCCEEDED': 0, 'READY_FOR_OWNER_REVIEW': 0, 'OWNER_REVIEW_RECORDED': 0,
            'UPLOADED_VERIFIED': 0, 'IN_PROGRESS': 2, 'WAITING_AUTH': 3, 'WAITING_QUOTA': 4, 'BLOCKED_UNSUPPORTED': 5, 'FAILED_RETRYABLE': 6,
            'FAILED_FINAL': 7, 'CANCELLED': 10}.get(result['status'], 9)
    except (ValueError, OSError, StudioError, KeyError) as exc:
        code = getattr(exc, 'code', 'ERROR')
        print(json.dumps({'status': 'WAITING_AUTH' if code == 'WAITING_AUTH' else 'BLOCKED_UNSUPPORTED' if code == 'BLOCKED_UNSUPPORTED' else 'ERROR',
            'error_code': code, 'error': sanitize(str(exc)), 'production_started': pipeline.production_started() if pipeline else None,
            'uploaded': False, 'truncated': False}, ensure_ascii=False))
        return 3 if code == 'WAITING_AUTH' else 5 if code == 'BLOCKED_UNSUPPORTED' else 9
    finally:
        if pipeline:
            pipeline.close()
