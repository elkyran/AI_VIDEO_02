"""Host inputs and explicit authority for SYSTEM-01; no tool execution in planning."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil

from pydantic import Field, model_validator
from typing import Literal

from .contracts import Identifier, Sha256, relative_path
from .editorial import Scene, Review
from .registry import StudioError, content_hash, workspace_path
from .v2_acceptance import fingerprint
from .v2_contracts import Brief, Contract
from .v2_ids import derived_id
from .v2_narration import profile_spec
from .v2_sources import CanonBible, Source, source_manifest, verify_sources
from .v2_visuals import SegmentVisuals
from .v2_routes import WorkerRoutes
from .v2_acquisition_contracts import AcquisitionPolicy

OPERATIONS = ('synthesize', 'compile', 'render', 'qa', 'portable', 'deliver')


def no_links(path):
    """Check the spelling before resolve, including Windows junction ancestors."""
    raw = Path(path).absolute()
    for part in (raw, *raw.parents):
        if part.is_symlink() or (part.exists() and getattr(part.lstat(), 'st_file_attributes', 0) & 0x400):
            raise StudioError('PATH_UNSAFE', 'Reparse points are not permitted in host paths')
    return raw.resolve()


def isolated_root(path):
    from .multi_ai_v2 import local_root
    root = local_root(no_links(path))
    repo = Path(__file__).resolve().parents[1]
    if repo.is_relative_to(root):
        raise StudioError('PATH_UNSAFE', 'Run root cannot contain the repository')
    return root


class Budget(Contract):
    max_output_bytes: int = Field(default=250*1024**2, ge=1)
    reserve_bytes: int = Field(default=4*1024**3, ge=0)
    max_input_bytes: int = Field(default=32*1024**2, ge=1, le=250*1024**2)
    seconds: int = Field(default=300, ge=1, le=3600)
    attempts: int = Field(default=2, ge=1, le=2)
    external_requests: int = Field(default=0, ge=0, le=10000)


class InputFile(Contract):
    asset_id: Identifier
    path: str
    sha256: Sha256
    size: int = Field(ge=1)
    version: int = Field(ge=1)
    kind: Literal['image', 'video']
    rights: Literal['owned', 'permission', 'public_domain']
    duration_ms: int | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def valid(self):
        relative_path(self.path)
        allowed = {'.png', '.jpg', '.jpeg'} if self.kind == 'image' else {'.mp4'}
        if Path(self.path).suffix.lower() not in allowed or (self.kind == 'video' and self.duration_ms is None):
            raise ValueError('Unsupported media kind or missing measured video duration')
        return self


class ApprovedInputs(Contract):
    version: int = Field(ge=1)
    canon: CanonBible
    story: str = Field(min_length=1, max_length=12000)
    scenes: list[Scene] = Field(min_length=1, max_length=100)
    media: list[InputFile] = Field(default_factory=list, max_length=500)
    visuals: list[SegmentVisuals] = Field(default_factory=list, max_length=500)

    @model_validator(mode='after')
    def unique(self):
        if len({s.scene_id for s in self.scenes}) != len(self.scenes):
            raise ValueError('Duplicate scene IDs')
        if len({m.asset_id for m in self.media}) != len(self.media):
            raise ValueError('Duplicate media IDs')
        if len({(m.sha256, m.kind) for m in self.media}) != len(self.media):
            raise ValueError('Identical media must share one explicit asset ID, matching the existing Registry')
        keys = [(m.scene_id, m.segment_id) for m in self.visuals]
        if len(set(keys)) != len(keys):
            raise ValueError('Duplicate visual mappings')
        if set(keys) != {(s.scene_id, n.segment_id) for s in self.scenes for n in s.narration_segments}:
            raise ValueError('Explicit visual mapping is required for every segment')
        media = {m.asset_id: m for m in self.media}
        for scene in self.scenes:
            for mapping in [m for m in self.visuals if m.scene_id == scene.scene_id]:
                if mapping.scene_version != scene.version:
                    raise ValueError('Stale visual mapping')
                if sum(s.source_out_ms-s.source_in_ms for s in mapping.shots) != mapping.duration_ms:
                    raise ValueError('Visual ranges must cover the declared segment')
                for shot in mapping.shots:
                    asset = media.get(shot.asset_id)
                    if (not asset or asset.kind == 'image' and shot.source_in_ms != 0
                            or asset.kind == 'video' and shot.source_out_ms > asset.duration_ms):
                        raise ValueError('Missing approved media or invalid trim')
        return self


class BriefInputs(Contract):
    """Host allowlist for acquisition; a Brief does not contain a written script.

    Sources/media are owner-selected inventory, never paths proposed by a model.
    This is intentionally distinct from already approved creative inputs.
    """
    version: int = Field(ge=1)
    sources: list[Source] = Field(min_length=1, max_length=128)
    media: list[InputFile] = Field(default_factory=list, max_length=500)
    requested_scene_count: int = Field(default=1, ge=1, le=100)

    @model_validator(mode='after')
    def unique(self):
        if len({(s.source_id, s.edition) for s in self.sources}) != len(self.sources):
            raise ValueError('Duplicate source IDs in the same edition')
        if len({m.asset_id for m in self.media}) != len(self.media):
            raise ValueError('Duplicate media IDs')
        if len({(m.sha256, m.kind) for m in self.media}) != len(self.media):
            raise ValueError('Identical media must share one explicit asset ID')
        return self


class DriveConfig(Contract):
    folder_id: str = Field(min_length=1, max_length=256, pattern=r'^[A-Za-z0-9_-]+$')
    credential_ref: Identifier
    network_write_authorized: bool = False


class HostRunConfig(Contract):
    run_id: Identifier
    key: Identifier
    version: int = Field(default=1, ge=1)
    parent_sha256: Sha256 | None = None
    mode: Literal['simulation', 'approved_input', 'autonomous']
    brief: Brief
    root: str
    input_root: str
    inputs: ApprovedInputs | BriefInputs
    profile_sha256: Sha256
    operations: list[Literal['synthesize', 'compile', 'render', 'qa', 'portable', 'deliver']]
    budget: Budget = Field(default_factory=Budget)
    runtime: dict = Field(default_factory=dict)
    renderer: Literal['existing_mlt_libx264_crf20_fast_4threads'] = 'existing_mlt_libx264_crf20_fast_4threads'
    review_policy: Literal['owner_required'] = 'owner_required'
    drive: DriveConfig | None = None
    worker_routes: WorkerRoutes | None = None
    acquisition: AcquisitionPolicy | None = Field(default=None, exclude_if=lambda value: value is None)

    @model_validator(mode='after')
    def consistent(self):
        simulation = self.mode == 'simulation'
        if (self.brief.production_mode == 'fake') != simulation:
            raise ValueError('Brief production_mode must explicitly match the execution mode')
        if self.brief.language != 'ru':
            raise ValueError('BLOCKED_UNSUPPORTED: approved voice language is ru')
        if self.brief.output_profile != 'ruslan-local-narration':
            raise ValueError('Unsupported output profile')
        if isinstance(self.inputs, ApprovedInputs):
            if self.inputs.canon.simulated != simulation or any(s.simulated != simulation or s.project_id != self.run_id
                    or any(n.simulated != simulation for n in s.narration_segments) for s in self.inputs.scenes):
                raise ValueError('Mixed run identity or simulation provenance')
        elif self.mode == 'approved_input':
            raise ValueError('Approved-input mode requires the complete owner-approved inputs')
        elif not simulation and any(s.rights == 'fixture' for s in self.inputs.sources):
            raise ValueError('Fixture source rights are limited to explicit simulation')
        if len(set(self.operations)) != len(self.operations):
            raise ValueError('Duplicate operations')
        if self.worker_routes and any(route.synthetic != simulation for route in self.worker_routes.routes):
            raise ValueError('Worker routes must match explicit simulation provenance')
        allowed_runtime = {'tts_python', 'tts_model', 'melt_path', 'ffmpeg_path', 'ffprobe_path', 'tts_engine',
            'tts_normalization_scope', 'pronunciation_lexicon', 'pronunciation_terms', 'video_encoder', 'render_threads',
            *('tts_'+k for k in profile_spec()['voice']['settings'] if k != 'speaker_id')}
        if set(self.runtime)-allowed_runtime:
            raise ValueError('Runtime accepts only existing engine locators/settings; no credentials, shell or provider configuration')
        if (self.version == 1) != (self.parent_sha256 is None):
            raise ValueError('A revision needs its exact previous configuration hash')
        return self

    def pinned(self):
        value = self.model_dump()
        # Preserve byte-for-byte authority hashes of configurations accepted
        # before optional worker routes existed.
        if self.worker_routes is None:
            value.pop('worker_routes')
        if self.acquisition is None:
            value.pop('acquisition', None)
        value['root'] = str(isolated_root(self.root))
        value['input_root'] = str(no_links(self.input_root))
        return value


def load_config(path):
    file = no_links(path)
    if file.stat().st_size > 2*1024**2:
        raise ValueError('Host configuration exceeds 2 MiB')
    value = json.loads(file.read_text(encoding='utf-8-sig'))
    if not Path(value['input_root']).is_absolute():
        value['input_root'] = str(no_links(file.parent/value['input_root']))
    return HostRunConfig.model_validate(value)


def verify_inputs(config):
    root = no_links(config.input_root)
    bible = source_inventory(config)
    total = sum(s.size for s in bible.sources) + sum(m.size for m in config.inputs.media)
    if total > config.budget.max_input_bytes:
        raise StudioError('INPUT_BUDGET_EXCEEDED', 'Allowed input inventory exceeds read budget')
    for source in bible.sources:
        if Path(source.path).suffix.lower() not in {'.txt', '.md', '.json'}:
            raise StudioError('SOURCE_KIND_UNSUPPORTED', 'Only approved text/JSON sources may be read')
        no_links(root/source.path)
    manifest_hash = fingerprint(source_manifest(bible))
    evidence = verify_sources(root, bible, manifest_hash)
    for asset in config.inputs.media:
        path = no_links(root/asset.path)
        if (not path.is_relative_to(root) or not path.is_file() or path.stat().st_size != asset.size
                or content_hash(path) != asset.sha256):
            raise StudioError('SOURCE_CHANGED', 'Approved media missing or changed')
    if fingerprint(profile_spec()) != config.profile_sha256:
        raise StudioError('PROFILE_CHANGED', 'Host configuration must pin the current complete production profile')
    return evidence


def source_inventory(config):
    """Reuse canonical source verification without claiming generated Canon facts."""
    if isinstance(config.inputs, ApprovedInputs):
        return config.inputs.canon
    return CanonBible(version=config.inputs.version, work_id=config.run_id,
        simulated=config.mode == 'simulation', sources=config.inputs.sources)


def storage(config):
    root = isolated_root(config.root)
    existing = next(p for p in (root, *root.parents) if p.exists())
    size = 0
    if root.exists():
        for directory, dirs, files in os.walk(root, followlinks=False):
            for name in dirs+files:
                path = Path(directory)/name
                # Parents have already been inspected by this top-down walk.
                # One lstat per entry avoids resolving every ancestor repeatedly.
                meta = path.lstat()
                import stat
                if stat.S_ISLNK(meta.st_mode) or getattr(meta, 'st_file_attributes', 0) & 0x400:
                    raise StudioError('PATH_UNSAFE', 'Reparse points are not permitted in the run tree')
                if stat.S_ISREG(meta.st_mode):
                    size += meta.st_size
    free = shutil.disk_usage(existing).free
    if size > config.budget.max_output_bytes or free-config.budget.max_output_bytes < config.budget.reserve_bytes:
        raise StudioError('BLOCKED_STORAGE', 'Output limit or remaining disk reserve cannot be satisfied')
    return {'persistent_bytes': size, 'free_bytes': free, **config.budget.model_dump()}


def graph(config, *, acquired=False, acquisition_parent=None):
    """IDs bind complete input identities; unaffected scenes retain their exact nodes."""
    if isinstance(config.inputs, BriefInputs):
        from .v2_release_plan import acquisition_graph
        return acquisition_graph(config)
    nodes = []
    def add(stage, value, dependencies=(), scene=None, schema='note', provider_role=None):
        policy = {'operations': config.operations, 'budget': config.budget.model_dump(),
                  'runtime': config.runtime, 'renderer': config.renderer}
        if config.worker_routes is not None:
            policy['worker_routes'] = config.worker_routes.model_dump()
        if config.acquisition is not None:
            policy['acquisition'] = config.acquisition.model_dump()
        binding = {'stage': stage, 'value': value, 'dependencies': list(dependencies),
                   'profile': config.profile_sha256, 'mode': config.mode, 'run_id': config.run_id,
                   'host_execution': fingerprint(policy)}
        if provider_role is not None:
            binding['provider_role'] = provider_role
        identifier = derived_id(config.run_id, stage, 'h'+fingerprint(binding))
        nodes.append({'task_id': identifier, **binding, 'scene_id': scene.scene_id if scene else None,
                      'version': scene.version if scene else 1, 'schema': schema})
        return identifier
    sources = add('sources', config.inputs.canon.model_dump(), [acquisition_parent] if acquisition_parent else [])
    story = add('story', {'story': config.inputs.story}, [sources])
    finals = []
    for scene in config.inputs.scenes:
        admission_deps = [story]
        if config.worker_routes is not None and not acquired:
            worker_scene = add('worker_scene', scene.model_dump(), [story], scene, 'scene', 'gemini_story')
            review = Review(project_id=config.run_id, review_id=derived_id('review', scene.scene_id, 'v'+str(scene.version)),
                subject_type='scene', subject_id=scene.scene_id, subject_version=scene.version,
                subject_hash=fingerprint(scene.model_dump()), evidence_mode='metadata_only',
                verdict='needs_review', simulated=config.mode == 'simulation').model_dump()
            worker_review = add('worker_review', review, [worker_scene], scene, 'review', 'gemini_visual')
            admission_deps = [worker_review]
        admission = add('scene', scene.model_dump(), admission_deps, scene, 'scene')
        mappings = [m.model_dump() for m in config.inputs.visuals if m.scene_id == scene.scene_id]
        used = {s['asset_id'] for m in mappings for s in m['shots']}
        candidate = add('candidate', {'mappings': mappings,
            'media': [m.model_dump() for m in config.inputs.media if m.asset_id in used]}, [admission], scene)
        audio = add('wav', {}, [candidate], scene)
        edit = add('edit', {}, [candidate, audio], scene)
        compiled = add('compile', {}, [edit], scene)
        rendered = add('render', {}, [compiled], scene)
        finals.append(add('qa', {}, [rendered, audio], scene))
    assembly = add('assembly', {'order': [s.scene_id for s in config.inputs.scenes]}, finals)
    final_compile = add('compile', {}, [assembly])
    render = add('render', {}, [final_compile])
    qa = add('qa', {}, [render])
    add('portable', {}, [qa])
    return nodes


def plan(config):
    config = HostRunConfig.model_validate(config) if isinstance(config, dict) else config
    root = isolated_root(config.root)
    inputs = no_links(config.input_root)
    if inputs == root or inputs.is_relative_to(root) or root.is_relative_to(inputs):
        raise StudioError('PATH_UNSAFE', 'Input originals and run outputs must be separate trees')
    evidence = verify_inputs(config)
    blockers = []
    if isinstance(config.inputs, BriefInputs) and config.acquisition and config.worker_routes is None:
        blockers.append('HOST_ROUTES_REQUIRED: acquisition requires fixed Story and Visual routes')
    if config.worker_routes is not None:
        roles = {r.role for r in config.worker_routes.routes}
        if roles != {'gemini_story', 'gemini_visual'} and not (config.acquisition and
                {'gemini_story','gemini_visual'} <= roles <= {'gemini_story','gemini_visual','codex'}):
            blockers.append('HOST_ROUTES_REQUIRED: SYSTEM workers require exactly Story and Visual; production remains in the host engine')
        if config.mode != 'simulation':
            blockers.append('BLOCKED_ELIGIBILITY: routed Antigravity workers remain AGE-GATED / DISABLED')
    if isinstance(config.inputs, BriefInputs):
        from .v2_release_plan import acquisition_blockers
        blockers.extend(acquisition_blockers(config))
    if config.worker_routes is not None:
        roles = {route.role for route in config.worker_routes.routes}
        if not {'gemini_story', 'gemini_visual'} <= roles:
            blockers.append('HOST_ROUTES_REQUIRED: pin both Story and Visual host worker/account bindings')
        if config.mode != 'simulation':
            blockers.append('BLOCKED_ELIGIBILITY: configured Story/Visual routes remain disabled outside simulation')
    if config.mode == 'autonomous':
        blockers += ['Story/Visual: Antigravity AGE-GATED / DISABLED; no supported verified route',
                     'Codex director: end-to-end provider capability is not live verified']
        roles = {route.role for route in config.worker_routes.routes} if config.worker_routes else set()
        if not {'gemini_story', 'gemini_visual'} <= roles:
            blockers.append('HOST_ROUTES_REQUIRED: pin both Story and Visual host worker/account bindings')
    if config.mode != 'simulation' and not config.runtime:
        blockers.append('Explicit isolated engine runtime configuration is required')
    return {'status': 'BLOCKED_UNSUPPORTED' if blockers else 'PLANNED', 'run_id': config.run_id,
        'config_sha256': fingerprint(config.pinned()), 'nodes': graph(config), 'blockers': blockers,
        'sources': evidence, 'mode': config.mode, 'production_started': False, 'model_requests': 0,
        'paid_api_requests': 0, 'uploaded': False, 'system_ready_for_autonomous_episode': False}
