"""Explicit host visual mapping; metadata never establishes artistic observation."""
from typing import Literal
import json
from pathlib import Path

from pydantic import Field, model_validator

from .contracts import Identifier
from .v2_contracts import Contract
from .v2_ids import derived_id
from .production_bridge import _ReadOnlyRegistry
from .registry import StudioError
from .editorial import EditPlan, Placement, Shot, SubtitleEvent
from .production_profile import Profile


class ShotCandidate(Contract):
    shot_id: Identifier
    asset_id: Identifier
    source_in_ms: int = Field(default=0, ge=0)
    source_out_ms: int = Field(gt=0)
    visual_purpose: Literal['illustration', 'atmosphere', 'explanation'] = 'illustration'
    emotional_task: str = Field(min_length=1, max_length=1000)
    continuity: str = Field(default='', max_length=1000)
    reuse_reason: str | None = Field(default=None, max_length=1000)
    evidence_mode: Literal['metadata_only'] = 'metadata_only'
    observed_content: None = None

    @model_validator(mode='after')
    def ordered(self):
        if self.source_out_ms <= self.source_in_ms:
            raise ValueError('Source range must be positive')
        return self


class SegmentVisuals(Contract):
    scene_id: Identifier
    scene_version: int = Field(ge=1)
    segment_id: Identifier
    duration_ms: int = Field(gt=0, le=3_600_000)
    shots: list[ShotCandidate] = Field(min_length=1, max_length=80)


def verify_mapping(root, project_id, scene, values):
    mappings = [SegmentVisuals.model_validate(v) for v in values]
    by_segment = {m.segment_id: m for m in mappings}
    if len(by_segment) != len(mappings):
        raise StudioError('SCHEMA_INVALID', 'Duplicate visual segment IDs')
    if set(by_segment) != {s.segment_id for s in scene.narration_segments}:
        raise StudioError('VISUAL_REQUIRED', 'Every narration segment needs explicit approved visuals')
    seen_shots, used, assets = set(), [], {}
    with _ReadOnlyRegistry(root) as registry:
        registry._project(project_id)
        for mapping in mappings:
            if mapping.scene_id != scene.scene_id or mapping.scene_version != scene.version:
                raise StudioError('STALE_INPUTS', 'Visual mapping names another scene/version')
            if sum(s.source_out_ms - s.source_in_ms for s in mapping.shots) != mapping.duration_ms:
                raise StudioError('SHOT_TIMING_INVALID', 'Shot durations must cover the narration segment exactly')
            for shot in mapping.shots:
                if shot.shot_id in seen_shots:
                    raise StudioError('SCHEMA_INVALID', 'Duplicate shot ID')
                seen_shots.add(shot.shot_id)
                asset = registry.asset(project_id, shot.asset_id, verify=True)
                if asset['rights'] not in {'owned', 'permission', 'public_domain'}:
                    raise StudioError('RIGHTS_UNCONFIRMED', 'Only explicitly permitted registered assets can be mapped')
                if asset['kind'] not in {'image', 'video'}:
                    raise StudioError('ASSET_KIND_INVALID', 'Visual must be an image or video')
                if asset['kind'] == 'image':
                    if shot.source_in_ms:
                        raise StudioError('ASSET_RANGE_INVALID', 'Still images cannot have an offset')
                elif (type(asset['duration_ms']) is not int or shot.source_out_ms > asset['duration_ms']):
                    raise StudioError('ASSET_RANGE_INVALID', 'Video trim exceeds measured source duration')
                for prior in used:
                    if (prior.asset_id == shot.asset_id and prior.source_in_ms < shot.source_out_ms
                            and shot.source_in_ms < prior.source_out_ms and not (shot.reuse_reason or '').strip()):
                        raise StudioError('REUSE_REASON_REQUIRED', 'Repeated source range needs explicit explanation')
                used.append(shot)
                assets[shot.asset_id] = asset
    return [by_segment[s.segment_id] for s in scene.narration_segments], assets


def prepare_edit_plan(root, project_id, scene, mappings, *, voice_version='not_synthesized', voice_assets=None):
    """Deterministic placement preparation; duration inputs remain estimates until audio exists."""
    visuals, assets = verify_mapping(root, project_id, scene, mappings)
    picture, voice, subtitles, cursor = [], [], [], 0
    voice_assets = voice_assets or {}
    if voice_assets and set(voice_assets) != {s.segment_id for s in scene.narration_segments}:
        raise StudioError('AUDIO_REQUIRED', 'Measured voice must cover every narration segment')
    with _ReadOnlyRegistry(root) as registry:
        for segment, mapping in zip(scene.narration_segments, visuals):
            start = cursor
            for row in mapping.shots:
                end = cursor + row.source_out_ms - row.source_in_ms
                shot = Shot(shot_id=row.shot_id, scene_id=scene.scene_id, beat_id=segment.segment_id,
                    visual_function=row.visual_purpose + ': ' + row.emotional_task, asset_id=row.asset_id,
                    source_in_ms=row.source_in_ms, source_out_ms=row.source_out_ms,
                    observed_content='Metadata only; no frame or video observation claimed.',
                    continuity_notes=row.continuity, reuse_reason=row.reuse_reason, simulated=scene.simulated)
                picture.append(Placement(placement_id=row.shot_id, asset_id=row.asset_id,
                    timeline_start_ms=cursor, timeline_end_ms=end, source_in_ms=row.source_in_ms,
                    source_out_ms=row.source_out_ms, track='video', shot=shot, simulated=scene.simulated))
                cursor = end
            subtitles.append(SubtitleEvent(start_ms=start, end_ms=cursor, text=segment.text, simulated=scene.simulated))
            if segment.segment_id in voice_assets:
                asset = registry.asset(project_id, voice_assets[segment.segment_id], verify=True)
                if asset['kind'] != 'audio' or asset['rights'] not in {'owned', 'permission', 'public_domain'}:
                    raise StudioError('AUDIO_REQUIRED', 'Voice must be a permitted registered audio asset')
                if asset['duration_ms'] != mapping.duration_ms:
                    raise StudioError('SHOT_TIMING_INVALID', 'Measured voice duration differs from planned segment')
                assets[asset['id']] = asset
                voice.append(Placement(placement_id=derived_id('audio',segment.segment_id), asset_id=asset['id'],
                    timeline_start_ms=start, timeline_end_ms=cursor, source_in_ms=0,
                    source_out_ms=mapping.duration_ms, track='voice', simulated=scene.simulated))
    profile = Profile.model_validate_json((Path(__file__).resolve().parents[1]/'config/production_profile.json').read_text(encoding='utf-8'))
    plan = EditPlan(project_id=project_id, plan_id=derived_id('edit',scene.scene_id), scene_id=scene.scene_id,
        version=scene.version, scene_version=scene.version, voice_version=voice_version, duration_ms=cursor,
        shot_placements=picture, voice_placements=voice, subtitle_events=subtitles, simulated=scene.simulated,
        width=profile.video.width, height=profile.video.height, fps=profile.video.fps,
        important_decisions=['Explicit host mapping; static shots only; no artistic suitability inferred.',
                             'Timing is estimated until measured narration assets are supplied.'])
    return plan, [assets[key] for key in sorted(assets)]
