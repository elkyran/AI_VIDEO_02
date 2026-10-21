"""Accepted Scene -> immutable existing-engine candidate. Never enqueue a job."""
import json

from .editorial import Scene
from .operations import ProductionScript, ProductionBlock, ProductionVisualShot
from .registry import Registry, StudioError
from .v2_acceptance import fingerprint
from .v2_contracts import Artifact
from .v2_visuals import verify_mapping


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
