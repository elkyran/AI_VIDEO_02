"""Host-owned Brief acquisition plan and typed reconciliation, with no execution.

This module does not implement missing provider capabilities. Its caller must use
the existing queue/result gate to accept creative artifacts before reconciliation.
In particular, no source text or model response is an executable host config.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from .editorial import Scene
from .registry import StudioError, canonical_json
from .v2_acceptance import fingerprint
from .v2_ids import derived_id
from .v2_sources import CanonBible, source_manifest
from .v2_visuals import SegmentVisuals


def acquisition_graph(config):
    """Persistable acquisition nodes; accepted scene content is not known yet.

    Scene count is an explicit host request, not a hidden three-scene scenario.
    Production expansion requires typed, accepted Canon/Scenes/visual mappings;
    there are no production permits or placeholder WAV/render results here.
    """
    from .v2_run_config import BriefInputs, source_inventory
    if not isinstance(config.inputs, BriefInputs):
        raise StudioError('ACQUISITION_INPUT_REQUIRED', 'This plan requires a Brief source inventory')
    nodes = []

    def add(stage, value, dependencies=(), *, role='python', schema='note', scene_id=None):
        policy = {'operations': config.operations, 'budget': config.budget.model_dump(),
                  'runtime': config.runtime, 'renderer': config.renderer}
        if config.worker_routes is not None:
            policy['worker_routes'] = config.worker_routes.model_dump()
        binding = {'stage': stage, 'value': value, 'dependencies': list(dependencies),
                   'profile': config.profile_sha256, 'mode': config.mode, 'run_id': config.run_id,
                   'host_execution': fingerprint(policy), 'role': role}
        identifier = derived_id(config.run_id, stage, 'h'+fingerprint(binding))
        nodes.append({'task_id': identifier, **binding, 'scene_id': scene_id,
            'version': config.inputs.version, 'schema': schema, 'acquisition': True})
        return identifier

    source = add('sources', source_manifest(source_inventory(config)))
    canon = add('canon', {'source_manifest_sha256': fingerprint(source_manifest(source_inventory(config)))},
        [source], role='gemini_story', schema='canon_bible')
    outline = add('story_outline', {'brief': config.brief.model_dump(),
        'requested_scene_count': config.inputs.requested_scene_count}, [canon],
        role='gemini_story', schema='story_outline')
    drafts = []
    for index in range(config.inputs.requested_scene_count):
        scene_id = derived_id(config.run_id, 'scene', 'n'+str(index+1))
        scene = add('scene', {'ordinal': index+1, 'scene_id': scene_id}, [outline],
            role='gemini_story', schema='scene', scene_id=scene_id)
        drafts.append(add('visual_mapping', {'allowed_media': [m.model_dump() for m in config.inputs.media]},
            [scene], role='gemini_visual', schema='segment_visuals', scene_id=scene_id))
    add('production_expansion', {'requires': 'accepted_typed_canon_story_scenes_visuals',
        'production_started': False}, drafts)
    return nodes


def acquisition_blockers(config):
    """Exact missing capabilities; no inference from a Scene/Review JSON parser."""
    blockers = [
        'CANON_CAPABILITY_UNVERIFIED: CanonBible acquisition is not supported by the current provider transport',
        'STORY_OUTLINE_CAPABILITY_UNVERIFIED: dynamic story outline acquisition has no verified provider schema',
        'VISUAL_MAPPING_CAPABILITY_UNVERIFIED: SegmentVisuals selection is not implemented by metadata-only Review',
    ]
    if config.mode == 'simulation':
        blockers.append('SYNTHETIC_RESULTS_REQUIRED: Brief execution requires explicitly injected synthetic acquisition results; no fixture fallback')
    else:
        blockers.append('BLOCKED_ELIGIBILITY: Story and Visual remain AGE-GATED / DISABLED')
    if not config.inputs.media:
        blockers.append('APPROVED_MEDIA_REQUIRED: choose an explicit permitted local media inventory')
    elif any(m.kind == 'image' for m in config.inputs.media):
        blockers.append('IMAGE_DELIVERY_UNVERIFIED: a local image inventory does not prove worker image delivery')
    if any(m.kind == 'video' for m in config.inputs.media):
        blockers.append('FULL_VIDEO_DELIVERY_UNVERIFIED: an MP4 path does not prove full-video delivery or review')
    return blockers


def reconcile_inputs(config, *, canon, story, scenes, visuals):
    """Validate gate-accepted creative values against original host allowlists.

    Return only ApprovedInputs, never a runtime, permission, provider route or a
    full authoritative config. Source inventory, media paths, identity, profile
    and execution permissions remain owned by the original host configuration.
    Model-authored claims of owner verification cannot promote Canon facts.
    """
    from .v2_run_config import ApprovedInputs, BriefInputs
    if not isinstance(config.inputs, BriefInputs):
        raise StudioError('ACQUISITION_INPUT_REQUIRED', 'Reconciliation requires original Brief inputs')
    bible = CanonBible.model_validate(canon)
    parsed_scenes = [Scene.model_validate(scene) for scene in scenes]
    mappings = [SegmentVisuals.model_validate(value) for value in visuals]
    simulation = config.mode == 'simulation'
    if (bible.version != config.inputs.version or bible.work_id != config.run_id
            or bible.simulated != simulation
            or [s.model_dump() for s in bible.sources] != [s.model_dump() for s in config.inputs.sources]):
        raise StudioError('SOURCE_MANIFEST_CHANGED', 'Generated Canon changed the host source allowlist or identity')
    if any(f.status == 'owner_verified' for f in bible.facts):
        raise StudioError('OWNER_REVIEW_REQUIRED', 'Provider output cannot declare an owner-verified fact')
    if len(parsed_scenes) != config.inputs.requested_scene_count:
        raise StudioError('SCENE_COUNT_CHANGED', 'Scene count differs from the explicit host acquisition request')
    if any(scene.project_id != config.run_id or scene.simulated != simulation
            or any(n.simulated != simulation for n in scene.narration_segments) for scene in parsed_scenes):
        raise StudioError('RUN_IDENTITY_CHANGED', 'Generated scenes changed run identity or simulation provenance')
    from .v2_acquisition import resolve_refs
    for scene in parsed_scenes:
        resolve_refs(bible,scene.fact_refs)
        resolve_refs(bible,scene.assumption_refs,assumptions=True)
    return ApprovedInputs(version=config.inputs.version, canon=bible, story=story,
        scenes=parsed_scenes, media=config.inputs.media, visuals=mappings)


def persist_plan(config, *, opt_in=False):
    """Explicit host prepare only; read-only plan/doctor must never call this.

    The file is immutable and content addressed. No Registry, production permit,
    account resolver or provider is initialized, even for blocked autonomous runs.
    """
    from .v2_run_config import isolated_root, no_links, plan, storage
    if opt_in is not True:
        raise StudioError('PLAN_PERMISSION_REQUIRED', 'Saving a plan requires an explicit prepare action')
    document = plan(config)
    disk = storage(config)
    value = {'schema_version': 1, 'kind': 'host_plan_not_execution_authorization',
        'configuration': config.pinned(), 'plan': document}
    root = isolated_root(config.root)
    relative = Path('plans')/(document['config_sha256']+'.json')
    file = no_links(root/relative)
    expected = (canonical_json(value)+'\n').encode('utf-8')
    if file.exists():
        if file.read_bytes() != expected:
            raise StudioError('PLAN_CHANGED', 'Existing content-addressed plan bytes changed')
    else:
        if disk['persistent_bytes']+len(expected) > config.budget.max_output_bytes:
            raise StudioError('BLOCKED_STORAGE', 'The immutable plan would exceed the host output budget')
        file.parent.mkdir(parents=True, exist_ok=True)
        # Exclusive creation preserves existing evidence on concurrent prepare.
        try:
            with file.open('xb') as stream:
                stream.write(expected)
        except FileExistsError:
            if file.read_bytes() != expected:
                raise StudioError('PLAN_CHANGED', 'Concurrent plan bytes differ') from None
    return {'status': document['status'], 'plan_path': str(file),
        'plan_sha256': hashlib.sha256(expected).hexdigest(), 'plan_binding_sha256': fingerprint(value),
        'config_sha256': document['config_sha256'],
        'blockers': document['blockers'], 'production_started': False,
        'model_requests': 0, 'paid_api_requests': 0, 'uploaded': False}
