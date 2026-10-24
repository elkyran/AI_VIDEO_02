"""Strict creative contracts. Structural validity is never creative acceptance."""
from __future__ import annotations

import hashlib
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

from .contracts import Identifier, Sha256
from .registry import StudioError, canonical_json


EvidenceMode = Literal['metadata_only', 'contact_sheet', 'frame_sequence', 'video_audio', 'human_review']


class EditorialContract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, validate_assignment=True)
    schema_version: Literal[1] = 1
    simulated: bool = False


class NarrationSegment(EditorialContract):
    segment_id: Identifier
    text: str = Field(min_length=1, max_length=3000)


class Scene(EditorialContract):
    project_id: Identifier
    scene_id: Identifier
    version: int = Field(ge=1)
    start_state: str = Field(min_length=1)
    cause: str = Field(min_length=1)
    goal: str = Field(min_length=1)
    obstacle: str = Field(min_length=1)
    decision_or_action: str = Field(min_length=1)
    consequence: str = Field(min_length=1)
    end_state: str = Field(min_length=1)
    characters: list[str] = Field(min_length=1)
    emotional_intent: str = Field(min_length=1)
    narration_segments: list[NarrationSegment] = Field(min_length=1)
    fact_refs: list[str] = Field(default_factory=list)
    assumption_refs: list[str] = Field(default_factory=list)
    dependencies: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def unique_segments(self):
        ids = [part.segment_id for part in self.narration_segments]
        if len(ids) != len(set(ids)):
            raise ValueError('Narration segment IDs must be unique')
        return self


class Coverage(EditorialContract):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    tracks: list[Literal['video', 'audio']] = Field(min_length=1)

    @model_validator(mode='after')
    def ordered(self):
        if self.end_ms <= self.start_ms or len(self.tracks) != len(set(self.tracks)):
            raise ValueError('Coverage requires an ordered interval and unique tracks')
        return self


class Shot(EditorialContract):
    shot_id: Identifier
    scene_id: Identifier
    beat_id: Identifier
    visual_function: str = Field(min_length=1)
    asset_id: Identifier
    source_in_ms: int = Field(ge=0)
    source_out_ms: int = Field(gt=0)
    observed_content: str = Field(min_length=1)
    evidence_mode: EvidenceMode = 'metadata_only'
    coverage: list[Coverage] = Field(default_factory=list)
    continuity_notes: str = ''
    reuse_reason: str | None = None

    @model_validator(mode='after')
    def ordered(self):
        if self.source_out_ms <= self.source_in_ms:
            raise ValueError('Shot source range must be positive')
        return self


class Placement(EditorialContract):
    placement_id: Identifier
    asset_id: Identifier
    timeline_start_ms: int = Field(ge=0)
    timeline_end_ms: int = Field(gt=0)
    source_in_ms: int = Field(ge=0)
    source_out_ms: int = Field(gt=0)
    track: Literal['video', 'voice', 'music', 'sfx']
    gain_db: float = Field(default=0.0, ge=-96, le=12, allow_inf_nan=False)
    fade_in_ms: int = Field(default=0, ge=0)
    fade_out_ms: int = Field(default=0, ge=0)
    transforms: list[dict[str, Any]] = Field(default_factory=list)
    shot: Shot | None = None

    @model_validator(mode='after')
    def consistent(self):
        if self.timeline_end_ms <= self.timeline_start_ms or self.source_out_ms <= self.source_in_ms:
            raise ValueError('Placement source/timeline intervals must be positive')
        if self.source_out_ms - self.source_in_ms != self.timeline_end_ms - self.timeline_start_ms:
            raise ValueError('Placement duration differs from source duration; speed changes require a supported capability')
        if self.shot and (self.shot.asset_id != self.asset_id or self.shot.source_in_ms != self.source_in_ms
                          or self.shot.source_out_ms != self.source_out_ms):
            raise ValueError('Shot provenance differs from its placement')
        if self.fade_in_ms + self.fade_out_ms > self.timeline_end_ms - self.timeline_start_ms:
            raise ValueError('Audio fades exceed placement duration')
        return self


class SubtitleEvent(EditorialContract):
    start_ms: int = Field(ge=0)
    end_ms: int = Field(gt=0)
    text: str = Field(min_length=1)

    @model_validator(mode='after')
    def ordered(self):
        if self.end_ms <= self.start_ms:
            raise ValueError('Subtitle interval must be positive')
        return self


class EditPlan(EditorialContract):
    project_id: Identifier
    plan_id: Identifier
    scene_id: Identifier
    version: int = Field(ge=1)
    scene_version: int = Field(ge=1)
    voice_version: str = Field(min_length=1)
    timebase: Literal['milliseconds'] = 'milliseconds'
    fps: int = Field(default=30, ge=1, le=120)
    width: int = Field(default=1920, ge=16, le=7680)
    height: int = Field(default=1080, ge=16, le=4320)
    duration_ms: int = Field(gt=0, le=3_600_000)
    shot_placements: list[Placement] = Field(min_length=1, max_length=500)
    voice_placements: list[Placement] = Field(default_factory=list, max_length=500)
    audio_events: list[Placement] = Field(default_factory=list, max_length=500)
    subtitle_events: list[SubtitleEvent] = Field(default_factory=list, max_length=2000)
    transitions: list[dict[str, Any]] = Field(default_factory=list)
    transforms: list[dict[str, Any]] = Field(default_factory=list)
    important_decisions: list[str] = Field(min_length=1)
    required_capabilities: list[str] = Field(default_factory=list)

    @model_validator(mode='after')
    def timelines(self):
        placements = self.shot_placements + self.voice_placements + self.audio_events
        ids = [item.placement_id for item in placements]
        if len(ids) != len(set(ids)):
            raise ValueError('Placement IDs must be unique')
        for rows, allowed in ((self.shot_placements, {'video'}), (self.voice_placements, {'voice'}),
                              (self.audio_events, {'music', 'sfx'})):
            for item in rows:
                if item.track not in allowed or item.timeline_end_ms > self.duration_ms:
                    raise ValueError('Placement track or scene bounds invalid')
                if item.shot and item.shot.scene_id != self.scene_id:
                    raise ValueError('Shot belongs to another scene')
        # Independent audio accents may overlap; narration and picture must not silently overlap.
        for rows in (self.shot_placements, self.voice_placements):
            ordered = sorted(rows, key=lambda item: item.timeline_start_ms)
            if any(a.timeline_end_ms > b.timeline_start_ms for a, b in zip(ordered, ordered[1:])):
                raise ValueError('Overlapping placements on the same picture/voice track')
        if any(item.end_ms > self.duration_ms for item in self.subtitle_events):
            raise ValueError('Subtitle lies outside scene')
        return self


class ReviewIssue(EditorialContract):
    issue_id: Identifier
    category: Literal['story_causality', 'motivation', 'visual_relevance', 'continuity', 'repetition',
                      'pacing', 'sound', 'subtitles', 'technical']
    severity: Literal['info', 'warning', 'critical']
    location: str = Field(min_length=1)
    observation: str = Field(min_length=1)
    effect_on_viewer: str = Field(min_length=1)
    suggested_change: str = Field(min_length=1)
    required_evidence: EvidenceMode
    status: Literal['open', 'resolved', 'accepted_uncertainty'] = 'open'


class Review(EditorialContract):
    project_id: Identifier
    review_id: Identifier
    subject_type: Literal['scene', 'edit_plan', 'render']
    subject_id: Identifier
    subject_version: int = Field(ge=1)
    subject_hash: Sha256
    evidence_mode: EvidenceMode = 'metadata_only'
    provided_coverage: list[Coverage] = Field(default_factory=list)
    reviewed_coverage: list[Coverage] = Field(default_factory=list)
    issues: list[ReviewIssue] = Field(default_factory=list)
    verdict: Literal['needs_review', 'blocked', 'ready_for_review'] = 'needs_review'
    render_id: Identifier | None = None
    render_hash: Sha256 | None = None
    edit_plan_version: int | None = Field(default=None, ge=1)

    @model_validator(mode='after')
    def honest(self):
        if self.subject_type == 'render' and (not self.render_id or not self.render_hash or not self.edit_plan_version):
            raise ValueError('A render review must name the exact render and edit plan version')
        if self.subject_type == 'render' and (self.subject_id != self.render_id or self.subject_hash != self.render_hash):
            raise ValueError('Render review subject identity must match its render identity')
        if any(issue.severity == 'critical' and issue.status == 'open' for issue in self.issues) and self.verdict != 'blocked':
            raise ValueError('Unresolved critical issues require blocked verdict')
        if self.simulated and self.verdict == 'ready_for_review':
            raise ValueError('Simulation cannot establish creative readiness')
        return self


class Note(EditorialContract):
    text: str = Field(min_length=1)


def object_hash(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode('utf-8')).hexdigest()


def _covered(item: Coverage, supplied: list[Coverage]) -> bool:
    # Union adjacent intervals per track, rather than claiming coverage across a gap.
    for track in item.tracks:
        position = item.start_ms
        for interval in sorted((x for x in supplied if track in x.tracks), key=lambda x: x.start_ms):
            if interval.start_ms > position:
                break
            if interval.end_ms > position:
                position = interval.end_ms
            if position >= item.end_ms:
                break
        if position < item.end_ms:
            return False
    return True


def validate_review(review: Review | dict, context: dict | None = None) -> dict:
    review = Review.model_validate(review) if isinstance(review, dict) else review
    context = context or {}
    for key in ('project_id', 'subject_type', 'subject_id', 'subject_version', 'subject_hash', 'render_id', 'render_hash', 'edit_plan_version'):
        if key in context and getattr(review, key) != context[key]:
            raise StudioError('STALE_INPUTS', f'Review does not refer to current {key}')
    provided_mode = context.get('provided_evidence_mode', 'metadata_only')
    ranks = {'metadata_only': 0, 'contact_sheet': 1, 'frame_sequence': 2, 'video_audio': 3}
    if review.evidence_mode == 'human_review':
        allowed = provided_mode == 'human_review' and context.get('human_review_authorized') is True
    else:
        allowed = review.evidence_mode in ranks and ranks[review.evidence_mode] <= ranks.get(provided_mode, -1)
    if not allowed:
        raise StudioError('EVIDENCE_OVERCLAIM', 'Review exceeds the media actually provided by the adapter')
    supplied = [Coverage.model_validate(row) for row in context.get('provided_coverage', [])]
    if review.subject_type == 'render' and review.verdict == 'ready_for_review' and (not supplied or not review.reviewed_coverage):
        raise StudioError('EVIDENCE_OVERCLAIM', 'Render readiness requires delivered and reviewed intervals')
    if any(not _covered(row, supplied) for row in review.provided_coverage + review.reviewed_coverage):
        raise StudioError('EVIDENCE_OVERCLAIM', 'Review coverage exceeds adapter delivery evidence')
    if review.evidence_mode in ('metadata_only', 'contact_sheet', 'frame_sequence'):
        if any('audio' in row.tracks for row in review.reviewed_coverage):
            raise StudioError('EVIDENCE_OVERCLAIM', 'Metadata or still frames do not establish listening')
        if review.subject_type == 'render' and review.verdict == 'ready_for_review':
            raise StudioError('EVIDENCE_OVERCLAIM', 'Limited visual evidence cannot complete render review')
    return review.model_dump()


def _dependency_context(context: dict) -> dict:
    result = dict(context)
    def bind(key, value):
        if key in result and result[key] != value:
            raise StudioError('STALE_INPUTS', f'Current dependency conflicts with requested {key}')
        result[key] = value
    for item in context.get('dependency_results', {}).values():
        if not isinstance(item, dict):
            continue
        if 'plan_id' in item:
            bind('project_id', item['project_id'])
            bind('subject_type', 'edit_plan')
            bind('subject_id', item['plan_id'])
            bind('subject_version', item['version'])
            bind('subject_hash', object_hash(item))
            bind('edit_plan_version', item['version'])
        if 'scene_id' in item and 'narration_segments' in item:
            bind('project_id', item['project_id'])
            bind('scene_id', item['scene_id'])
            bind('scene_version', item['version'])
    versions = context.get('input_versions', {})
    if 'voice' in versions:
        bind('voice_version', versions['voice'])
    if 'scene' in versions:
        import re
        match = re.fullmatch(r'([A-Za-z][A-Za-z0-9_.-]{0,95}):v([1-9][0-9]*)', versions['scene'])
        if not match:
            raise StudioError('SCHEMA_INVALID', 'Scene input version must use scene_id:vN')
        bind('scene_id', match[1])
        bind('scene_version', int(match[2]))
    return result


def validate_result(expected_output: str, payload: dict, context: dict | None = None) -> dict:
    """Return a normalized payload or raise ValidationError/StudioError; no storage or execution."""
    context = _dependency_context(context or {})
    from .v2_acquisition_contracts import ACQUISITION_OUTPUTS
    if expected_output in ACQUISITION_OUTPUTS or expected_output == 'scene' and context.get('acquisition_scope'):
        from .v2_acquisition import validate_acquisition
        return validate_acquisition(expected_output, payload, context)
    classes = {'scene': Scene, 'edit_plan': EditPlan, 'review': Review, 'note': Note}
    if expected_output not in classes:
        raise StudioError('SCHEMA_INVALID', f'Unsupported expected output: {expected_output}')
    result = classes[expected_output].model_validate(payload)
    if isinstance(result, Scene) and 'scene_version' in context and result.version != context['scene_version']:
        raise StudioError('STALE_INPUTS', 'Scene version differs from the requested version')
    for key in ('project_id', 'scene_id', 'scene_version', 'voice_version'):
        if key in context and hasattr(result, key) and getattr(result, key) != context[key]:
            raise StudioError('STALE_INPUTS', f'Result does not match current {key}')
    if isinstance(result, Review):
        return validate_review(result, context)
    if isinstance(result, EditPlan):
        ranks = {'metadata_only': 0, 'contact_sheet': 1, 'frame_sequence': 2, 'video_audio': 3}
        deliveries = context.get('asset_delivery', {})
        for placement in result.shot_placements:
            shot = placement.shot
            if shot is None:
                continue
            delivery = deliveries.get(shot.asset_id, {})
            supplied_mode = delivery.get('evidence_mode', 'metadata_only')
            if shot.evidence_mode == 'human_review':
                allowed = supplied_mode == 'human_review' and delivery.get('human_review_authorized') is True
            else:
                allowed = ranks[shot.evidence_mode] <= ranks.get(supplied_mode, -1)
            supplied = [Coverage.model_validate(row) for row in delivery.get('coverage', [])]
            if not allowed or any(not _covered(row, supplied) for row in shot.coverage):
                raise StudioError('EVIDENCE_OVERCLAIM', 'Shot observation exceeds verified asset delivery')
    return result.model_dump()


def detect_reused_ranges(shots: list[Shot | dict]) -> list[dict]:
    """Find source intersections independently of crop/mirroring; no semantic truth claim."""
    normalized = [Shot.model_validate(item) if isinstance(item, dict) else item for item in shots]
    findings = []
    for index, current in enumerate(normalized):
        for previous in normalized[:index]:
            if current.asset_id != previous.asset_id:
                continue
            start = max(current.source_in_ms, previous.source_in_ms)
            end = min(current.source_out_ms, previous.source_out_ms)
            if start < end:
                findings.append({'asset_id': current.asset_id, 'shot_ids': [previous.shot_id, current.shot_id],
                                 'source_in_ms': start, 'source_out_ms': end,
                                 'reuse_reason': current.reuse_reason, 'status': 'declared' if current.reuse_reason else 'needs_review'})
    return findings


def demo_result(expected_output: str, context: dict | None = None) -> dict:
    """Deterministic fixtures, explicitly simulated; no cloud or artistic claims."""
    context = _dependency_context(context or {})
    project = context.get('project_id', 'demo')
    scene = context.get('scene_id', 'scene_001')
    version = context.get('scene_version', 1)
    if expected_output == 'note':
        return Note(text='Offline simulator result', simulated=True).model_dump()
    if expected_output == 'scene':
        return Scene(project_id=project, scene_id=scene, version=version, simulated=True,
                     start_state='A traveler waits at a closed gate.', cause='The bridge is closed.',
                     goal='Reach the village.', obstacle='The river blocks the path.',
                     decision_or_action='The traveler asks a guide for help.', consequence='A safe route is found.',
                     end_state='The traveler follows the guide.', characters=['traveler', 'guide'],
                     emotional_intent='Uncertainty becomes relief.',
                     narration_segments=[NarrationSegment(segment_id='voice_001', text='Путник просит проводника показать безопасный путь.', simulated=True)],
                     assumption_refs=['Synthetic fixture; not source analysis.']).model_dump()
    if expected_output == 'edit_plan':
        return EditPlan(project_id=project, plan_id='plan_001', scene_id=scene, version=1,
                        scene_version=version, voice_version=context.get('voice_version', 'voice:v1'), simulated=True,
                        width=320, height=180, duration_ms=3000,
                        shot_placements=[Placement(placement_id='picture_001', asset_id='fixture_video',
                            timeline_start_ms=0, timeline_end_ms=1500, source_in_ms=2000, source_out_ms=3500, track='video', simulated=True),
                            Placement(placement_id='picture_002', asset_id='fixture_video',
                            timeline_start_ms=1500, timeline_end_ms=3000, source_in_ms=4000, source_out_ms=5500, track='video', simulated=True)],
                        voice_placements=[Placement(placement_id='voice_001', asset_id='fixture_voice', timeline_start_ms=500,
                            timeline_end_ms=2500, source_in_ms=0, source_out_ms=2000, track='voice', simulated=True)],
                        important_decisions=['Synthetic source trim test: green then blue; independent voice begins 500 ms later.']).model_dump()
    if expected_output == 'review':
        plan = demo_result('edit_plan', context)
        return Review(project_id=project, review_id='review_001', subject_type='edit_plan',
                      subject_id=context.get('subject_id', plan['plan_id']),
                      subject_version=context.get('subject_version', plan['version']),
                      subject_hash=context.get('subject_hash', object_hash(plan)), simulated=True,
                      edit_plan_version=context.get('edit_plan_version', plan['version']),
                      verdict='needs_review').model_dump()
    raise StudioError('SCHEMA_INVALID', 'Unsupported simulator result contract')
