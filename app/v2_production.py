"""Accepted Scene -> immutable existing-engine candidate. Never enqueue a job."""
import json
from pathlib import Path
import uuid

from .editorial import Scene
from .operations import ProductionScript, ProductionBlock, ProductionVisualShot
from .registry import Registry, StudioError, content_hash, workspace_path
from .v2_acceptance import fingerprint
from .v2_contracts import Artifact
from .v2_visuals import verify_mapping, prepare_edit_plan
from .production_bridge import ProductionBridge, _ReadOnlyRegistry
from .v2_narration import narration_descriptors, narration_identity, voice_asset_ids


class OfflineProductionBridge:
    def __init__(self, coordinator, engine_root):
        self.coordinator = coordinator
        self.root = coordinator.root
        self.engine_root = engine_root

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
            visuals, assets = verify_mapping(self.engine_root, run_id, scene, mappings)
            blocks, mapping = [], []
            for segment, visual in zip(scene.narration_segments, visuals):
                block_id = scene.scene_id + '_' + segment.segment_id
                blocks.append(ProductionBlock(id=block_id, text=segment.text, title=scene.scene_id,
                    visual_purpose=visual.shots[0].visual_purpose,
                    visual_shots=[ProductionVisualShot(asset_id=s.asset_id, motion='static',
                        weight=float(s.source_out_ms-s.source_in_ms), in_ms=s.source_in_ms) for s in visual.shots]))
                mapping.append({'scene_id': scene.scene_id, 'scene_version': scene.version,
                    'segment_id': segment.segment_id, 'block_id': block_id, 'text_sha256': fingerprint({'text': segment.text}),
                    'fact_refs': scene.fact_refs, 'assumption_refs': scene.assumption_refs,
                    'source_dependencies': scene.dependencies, 'emotional_intent': scene.emotional_intent})
            script = ProductionScript(project_id=run_id, title=scene.scene_id, scope='Offline accepted Scene candidate',
                                      segments=blocks, overlays_enabled=False)
            body = {'state': 'validated_not_registered', 'version': scene.version, 'binding': binding,
                    'script': script.model_dump(), 'scene_block_mapping': mapping,
                    'visual_mapping': [v.model_dump() for v in visuals],
                    'visual_assets': [assets[key] for key in sorted(assets)], 'simulated': scene.simulated,
                    'production_started': False, 'creative_review': 'NOT_RUN'}
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
            from pydantic import TypeAdapter
            from .contracts import Identifier
            TypeAdapter(Identifier).validate_python(key)
            task_id=proposal['run_id']+'_revision_'+key
            with Registry(self.root) as registry:
                existing=registry.db.execute('SELECT * FROM ai_tasks WHERE id=?',(task_id,)).fetchone()
                if existing:
                    task=self.coordinator.queue._task(existing)
                    if (task['context'].get('fixture_result')!=value['scene']
                            or task['input_versions']!={'prior_candidate':value['prior_candidate']['sha256']}):
                        raise StudioError('IDEMPOTENCY_CONFLICT','Revision key already binds different input')
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
            inputs={'prior_candidate':value['prior_candidate']['sha256']}
            contract=Task(task_id=task_id,run_id=scene.project_id,scene_id=scene.scene_id,role='python',
                capability='offline_revision',input_version=scene.version,input_hashes=inputs,depends_on=[],
                assigned_worker='fake_python',provider_profile_id='simulation',expected_output_schema='scene',idempotency_key=key)
            # A revision is a new version, not an editorial check of the old version.
            # The pinned prior hash is host-authored; old Scene payload is not a worker instruction.
            self.coordinator.queue.enqueue({'task_id':task_id,'project_id':scene.project_id,'role':'director',
                'expected_output':'scene','input_versions':inputs,'context':{'project_id':scene.project_id,
                'scene_id':scene.scene_id,'scene_version':scene.version,'v2_contract':contract.model_dump(),
                'fixture_result':scene.model_dump()},'depends_on':[]})
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
