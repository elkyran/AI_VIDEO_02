"""Compile checked editorial plans into the studio's existing Shotcut/MLT engine.

No production queue or preference is replaced. Compilation only writes a new
artifact directory; rendering is an explicit separate call.
"""
from __future__ import annotations

from collections import defaultdict
import json
from pathlib import Path
import sqlite3
import xml.etree.ElementTree as ET

from .editorial import EditPlan, Placement, validate_result
from .registry import Registry, StudioError, content_hash, workspace_path
from .timeline import _atomic_write, _playlist, _producer, _property, _stage_media


CAPABILITIES = frozenset({'source_trim', 'timeline_placements', 'cuts', 'black_gaps',
                          'independent_voice', 'audio_gain', 'audio_accents', 'subtitle_sidecar',
                          'editable_mlt', 'portable_media'})

PRODUCTION_PARAMETERS = {'synthesize': {'script_asset_id'}, 'build': {'script_asset_id', 'audio_job_id'},
                         'render': {'build_job_id'}, 'qa': {'render_job_id'}, 'package': {'qa_job_id'}}


class _ReadOnlyRegistry(Registry):
    """Reuse core asset/script validation without Registry's constructor/migrations."""
    def __init__(self, root):
        self.root = Path(root).resolve()
        path = self.root / 'registry.sqlite'
        if not path.is_file():
            raise StudioError('REGISTRY_NOT_FOUND', 'An existing studio registry is required')
        self.db = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        self.db.row_factory = sqlite3.Row
        self.db.execute('PRAGMA query_only=ON')


def _frame(milliseconds: int, fps: int) -> int:
    """Nearest frame boundary, half rounded up; the output records all conversions."""
    return (milliseconds * fps + 500) // 1000


def _srt_time(milliseconds: int) -> str:
    seconds, millis = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f'{hours:02}:{minutes:02}:{seconds:02},{millis:03}'


class ProductionBridge:
    def __init__(self, workspace: str | Path):
        self.root = Path(workspace).resolve()

    @staticmethod
    def capabilities() -> dict:
        return {'supported': sorted(CAPABILITIES), 'engine': 'Shotcut/MLT',
                'rounding': 'Each millisecond boundary rounds to nearest frame, half upward; conversions are returned.',
                'unsupported': ['speed', 'transforms', 'video_transitions', 'audio_fades', 'burned_subtitles'],
                'production_operations': ['synthesize', 'build', 'render', 'qa', 'package'],
                'production_note': 'prepare_request validates a descriptor without enqueueing. Existing JobManager/app.exchange execute it only after explicit submission.'}

    def prepare_request(self, project_id: str, operation: str, parameters: dict,
                        base_revision: int, idempotency_key: str, *, project_root: str | Path | None = None) -> dict:
        """Validate an existing production operation without enqueueing or spawning.

        An accepted request is still revalidated by JobManager when a caller
        explicitly submits it. A descriptor is not a reservation or authorization
        to run after the registry/profile has changed. In particular start=False
        would *not* be equivalent: another existing worker can drain pending jobs.
        """
        from .operations import read_script
        from .production_profile import load_profile, check_runtime
        from .editorial import object_hash
        if operation not in PRODUCTION_PARAMETERS:
            raise StudioError('OPERATION_NOT_ALLOWED', 'Use an existing allowlisted production operation')
        if (not isinstance(parameters, dict) or set(parameters) != PRODUCTION_PARAMETERS[operation]
                or not all(isinstance(value, str) and value for value in parameters.values())):
            raise StudioError('SCHEMA_INVALID', f'{operation} requires exactly {sorted(PRODUCTION_PARAMETERS[operation])}')
        if type(base_revision) is not int or base_revision < 1:
            raise StudioError('SCHEMA_INVALID', 'A positive integer base revision is required')
        if not isinstance(idempotency_key, str) or not 1 <= len(idempotency_key) <= 128:
            raise StudioError('SCHEMA_INVALID', 'A stable key of 1–128 characters is required')
        root = Path(project_root or Path(__file__).resolve().parents[1]).resolve()
        profile = load_profile(root)
        check_runtime(profile, root, self.root)
        request = {'op': 'enqueue', 'operation': operation, 'parameters': parameters, 'base_revision': base_revision}
        with _ReadOnlyRegistry(self.root) as registry:
            project = registry._project(project_id)
            tables = {row[0] for row in registry.db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if 'jobs' in tables and registry.db.execute("SELECT 1 FROM jobs WHERE state IN ('pending','running') LIMIT 1").fetchone():
                raise StudioError('ACTIVE_OPERATION', 'An existing production operation is pending/running; prepare after it finishes')
            prior = registry.db.execute('SELECT request_hash,response FROM requests WHERE project_id=? AND key=?',
                                        (project_id, idempotency_key)).fetchone()
            accepted = None
            if prior:
                if prior['request_hash'] != object_hash(request):
                    raise StudioError('IDEMPOTENCY_CONFLICT', 'This key belongs to different input or revision')
                accepted = json.loads(prior['response'])
            else:
                if project['revision'] != base_revision:
                    raise StudioError('REVISION_CONFLICT', 'Refresh the project revision before preparing a request')
                if 'script_asset_id' in parameters:
                    read_script(registry, project_id, parameters['script_asset_id'])
                expected = {'audio_job_id': 'synthesize', 'build_job_id': 'build', 'render_job_id': 'render', 'qa_job_id': 'qa'}
                for key, identifier in parameters.items():
                    if key not in expected:
                        continue
                    parent = registry.db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone() if 'jobs' in tables else None
                    if parent is None:
                        raise StudioError('JOB_NOT_FOUND', 'Unknown production dependency')
                    if parent['project_id'] != project_id:
                        raise StudioError('PROJECT_MISMATCH', 'Dependency belongs to another project')
                    if (parent['operation'] != expected[key] or parent['state'] != 'done'
                            or 'invalidated' in parent.keys() and parent['invalidated']):
                        raise StudioError('DEPENDENCY_PENDING', 'Dependency must be a current completed operation of the required type')
        return {'status': 'validated_not_enqueued', 'project_id': project_id, 'operation': operation,
                'parameters': dict(parameters), 'base_revision': base_revision, 'idempotency_key': idempotency_key,
                'profile_id': profile['profile_id'], 'profile_version': profile['version'], 'profile_sha256': object_hash(profile),
                'worker_started': False, 'accepted_receipt': accepted,
                'next_action': 'Explicitly submit through the existing JobManager/app.exchange after rechecking current state.'}

    def list_assets(self, project_id: str) -> list[dict]:
        """Inventory only; unlike Registry construction this cannot migrate production state."""
        path = self.root / 'registry.sqlite'
        if not path.is_file():
            raise StudioError('REGISTRY_NOT_FOUND', 'No existing registry at this workspace')
        database = sqlite3.connect(path.as_uri() + '?mode=ro', uri=True)
        try:
            database.row_factory = sqlite3.Row
            if not database.execute('SELECT 1 FROM projects WHERE id=?', (project_id,)).fetchone():
                raise StudioError('PROJECT_NOT_FOUND', 'Unknown registered project')
            return [dict(row) for row in database.execute(
                'SELECT id AS asset_id,path,sha256,kind,rights,duration_ms FROM assets WHERE project_id=? ORDER BY id',
                (project_id,))]
        finally:
            database.close()

    def compile(self, edit_plan: dict | EditPlan, assets: list[dict] | dict[str, dict], output_dir: str | Path,
                *, scene_version: int | None = None, voice_version: str | None = None,
                local_review: bool = False, asset_delivery: dict | None = None) -> dict:
        context = {key: value for key, value in {'scene_version': scene_version, 'voice_version': voice_version}.items()
                   if value is not None}
        context['asset_delivery'] = asset_delivery or {}
        raw = edit_plan.model_dump() if isinstance(edit_plan, EditPlan) else edit_plan
        plan = EditPlan.model_validate(validate_result('edit_plan', raw, context))
        missing = set(plan.required_capabilities) - CAPABILITIES
        placements = plan.shot_placements + plan.voice_placements + plan.audio_events
        if plan.transitions:
            missing.add('video_transitions')
        if plan.transforms or any(row.transforms for row in placements):
            missing.add('transforms')
        if any(row.fade_in_ms or row.fade_out_ms for row in placements):
            missing.add('audio_fades')
        if any(row.track == 'video' and row.gain_db for row in placements):
            missing.add('source_video_audio')
        if missing:
            raise StudioError('capability_gap', 'Unsupported operations: ' + ', '.join(sorted(missing)))
        rows = list(assets.values()) if isinstance(assets, dict) else assets
        catalog = {}
        for row in rows:
            identifier = row.get('asset_id', row.get('id'))
            if identifier in catalog:
                raise StudioError('SCHEMA_INVALID', 'Duplicate asset IDs')
            catalog[identifier] = row
        quantization = []
        source_paths = {}
        for item in placements:
            asset = catalog.get(item.asset_id)
            if not asset:
                raise StudioError('ASSET_NOT_FOUND', f'Unknown asset: {item.asset_id}')
            path = workspace_path(self.root, asset['path'])
            if not path.is_file() or content_hash(path) != asset.get('sha256'):
                raise StudioError('ASSET_CHANGED', f'Asset hash differs: {item.asset_id}')
            if asset.get('rights', 'unknown') == 'unknown' and not local_review:
                raise StudioError('RIGHTS_UNCONFIRMED', 'Unknown rights require explicit local review')
            if asset.get('kind') not in ({'image', 'video'} if item.track == 'video' else {'audio'}):
                raise StudioError('ASSET_KIND_INVALID', f'Wrong asset kind for {item.track}')
            if asset['kind'] != 'image':
                measured = asset.get('duration_ms')
                if type(measured) is not int or measured <= 0:
                    raise StudioError('DURATION_UNKNOWN', 'Measured asset duration is required')
                if item.source_out_ms > measured:
                    raise StudioError('ASSET_RANGE_INVALID', 'Source trim exceeds measured duration')
            elif item.source_in_ms:
                raise StudioError('ASSET_RANGE_INVALID', 'Still images have no source offset')
            converted = {key: _frame(getattr(item, key), plan.fps) for key in
                         ('timeline_start_ms', 'timeline_end_ms', 'source_in_ms', 'source_out_ms')}
            duration = converted['timeline_end_ms'] - converted['timeline_start_ms']
            if duration <= 0 or duration != converted['source_out_ms'] - converted['source_in_ms']:
                raise StudioError('SHOT_TIMING_INVALID', 'Frame rounding changed source and timeline durations differently')
            quantization.append({'placement_id': item.placement_id, 'requested_ms': {key: getattr(item, key) for key in converted},
                                 'frames': converted})
            source_paths[item.asset_id] = path
        total = _frame(plan.duration_ms, plan.fps)
        if total <= 0:
            raise StudioError('SHOT_TIMING_INVALID', 'Scene has no complete output frame')
        destination = Path(output_dir)
        if not destination.is_absolute():
            destination = self.root / destination
        destination = destination.resolve()
        if not destination.is_relative_to(self.root):
            raise StudioError('PATH_UNSAFE', 'Build output must remain inside this workspace')
        destination = workspace_path(self.root, destination.relative_to(self.root).as_posix(), for_write=True)
        # Exclusive directory creation also protects hand-edited partial builds.
        destination.mkdir(parents=True, exist_ok=False)
        media = {identifier: _stage_media(path, self.root, destination) for identifier, path in source_paths.items()}
        xml = self._xml(plan, catalog, media, total)
        _atomic_write(destination / 'project.mlt', xml)
        _atomic_write(destination / 'edit_plan.json', (json.dumps(plan.model_dump(), ensure_ascii=False, indent=2) + '\n').encode())
        timing = {'timebase': plan.timebase, 'fps': plan.fps, 'rounding': self.capabilities()['rounding'],
                  'requested_duration_ms': plan.duration_ms, 'output_frames': total, 'placements': quantization}
        _atomic_write(destination / 'timing.json', (json.dumps(timing, ensure_ascii=False, indent=2) + '\n').encode())
        if plan.subtitle_events:
            subtitles = '\n\n'.join(f'{index}\n{_srt_time(row.start_ms)} --> {_srt_time(row.end_ms)}\n{row.text}'
                                    for index, row in enumerate(plan.subtitle_events, 1)) + '\n'
            _atomic_write(destination / 'subtitles.srt', subtitles.encode('utf-8'))
        result = {'project_mlt': str(destination / 'project.mlt'), 'edit_plan': str(destination / 'edit_plan.json'),
                  'timing': timing, 'project_sha256': content_hash(destination / 'project.mlt'),
                  'frames': total, 'duration_seconds': total / plan.fps, 'simulated': plan.simulated,
                  'local_review': local_review, 'quality_status': 'not_checked', 'plan_id': plan.plan_id,
                  'plan_version': plan.version, 'scene_id': plan.scene_id,
                  'file_hashes': {path.relative_to(destination).as_posix(): content_hash(path)
                                  for path in destination.rglob('*') if path.is_file()}}
        _atomic_write(destination / 'bridge_manifest.json', (json.dumps(result, ensure_ascii=False, indent=2) + '\n').encode())
        return result

    @staticmethod
    def _xml(plan: EditPlan, catalog: dict, media: dict, total: int) -> bytes:
        root = ET.Element('mlt', {'LC_NUMERIC': 'C', 'version': '7.40.0', 'producer': 'tractor', 'shotcut': '1'})
        ET.SubElement(root, 'profile', {'description': 'Editorial plan', 'width': str(plan.width), 'height': str(plan.height),
                      'frame_rate_num': str(plan.fps), 'frame_rate_den': '1', 'progressive': '1',
                      'sample_aspect_num': '1', 'sample_aspect_den': '1', 'display_aspect_num': str(plan.width),
                      'display_aspect_den': str(plan.height), 'colorspace': '709'})
        _producer(root, 'black', '0', total, service='color')
        rows = plan.shot_placements + plan.voice_placements + plan.audio_events
        for index, row in enumerate(rows):
            image = catalog[row.asset_id]['kind'] == 'image'
            source_end = _frame(row.source_out_ms, plan.fps)
            producer = _producer(root, f'clip_{index}', media[row.asset_id], source_end,
                                 service='qimage' if image else 'avformat', caption=row.placement_id)
            _property(producer, 'set.test_audio' if row.track == 'video' else 'set.test_image', 1)
            if row.track != 'video':
                volume = ET.SubElement(producer, 'filter', {'in': '0', 'out': str(source_end - 1)})
                _property(volume, 'mlt_service', 'volume')
                _property(volume, 'level', f'{row.gain_db}dB')
        background = ET.SubElement(root, 'playlist', {'id': 'background'})
        ET.SubElement(background, 'entry', {'producer': 'black', 'in': '0', 'out': str(total - 1)})
        grouped = defaultdict(list)
        for index, row in enumerate(rows):
            # One picture track, one voice track; accents can overlap independently.
            group = row.track if row.track in ('video', 'voice') else f'{row.track}_{index}'
            grouped[group].append((index, row))
        tracks = []
        for name, group_rows in grouped.items():
            audio = name != 'video'
            track = _playlist(root, f'track_{name}', name, audio=audio)
            position = 0
            for index, row in sorted(group_rows, key=lambda pair: pair[1].timeline_start_ms):
                start, end = _frame(row.timeline_start_ms, plan.fps), _frame(row.timeline_end_ms, plan.fps)
                if start > position:
                    ET.SubElement(track, 'blank', {'length': str(start - position)})
                ET.SubElement(track, 'entry', {'producer': f'clip_{index}',
                              'in': str(_frame(row.source_in_ms, plan.fps)), 'out': str(_frame(row.source_out_ms, plan.fps) - 1)})
                position = end
            if position < total:
                ET.SubElement(track, 'blank', {'length': str(total - position)})
            tracks.append((f'track_{name}', audio))
        tractor = ET.SubElement(root, 'tractor', {'id': 'tractor', 'shotcut': '1', 'in': '0', 'out': str(total - 1)})
        for key, value in {'shotcut': 1, 'shotcut:name': 'Editorial Timeline', 'shotcut:projectAudioChannels': 2,
                           'shotcut:projectFrameRate': plan.fps, 'shotcut:projectNote': f'{plan.plan_id} v{plan.version}'}.items():
            _property(tractor, key, value)
        ET.SubElement(tractor, 'track', {'producer': 'background'})
        for index, (name, audio) in enumerate(tracks, 1):
            ET.SubElement(tractor, 'track', {'producer': name, 'hide': 'video' if audio else 'audio'})
            transition = ET.SubElement(tractor, 'transition', {'in': '0', 'out': str(total - 1)})
            props = {'a_track': 0, 'b_track': index, 'mlt_service': 'mix' if audio else 'frei0r.cairoblend'}
            props.update({'always_active': 1, 'sum': 1} if audio else {'disable': 0})
            for key, value in props.items():
                _property(transition, key, value)
        ET.indent(root, space='  ')
        return ET.tostring(root, encoding='utf-8', xml_declaration=True)

    def render(self, compiled: dict, output: str | Path, *, qmelt: str | Path | None = None) -> dict:
        from scripts.render_project import render_project
        project = Path(compiled['project_mlt']).resolve()
        if not project.is_relative_to(self.root):
            raise StudioError('PATH_UNSAFE', 'Compiled project is outside workspace')
        def verify():
            if not project.is_file() or content_hash(project) != compiled['project_sha256']:
                raise StudioError('ASSET_CHANGED', 'Compiled MLT was modified')
            for relative, expected in compiled['file_hashes'].items():
                path = workspace_path(project.parent, relative)
                if not path.is_file() or content_hash(path) != expected:
                    raise StudioError('ASSET_CHANGED', f'Compiled artifact changed: {relative}')
        verify()
        target = Path(output)
        if not target.is_absolute():
            target = self.root / target
        target = target.resolve()
        if not target.is_relative_to(self.root):
            raise StudioError('PATH_UNSAFE', 'Preview output must remain inside workspace')
        workspace_path(self.root, target.relative_to(self.root).as_posix(), for_write=True)
        result = render_project(project, target, qmelt=qmelt, timeout=180)
        verify()
        return {**result, 'sha256': content_hash(target), 'quality_status': 'not_checked',
                'plan_id': compiled['plan_id'], 'plan_version': compiled['plan_version'], 'simulated': compiled['simulated']}

    def export(self, compiled: dict, output: str | Path, *, qmelt: str | Path | None = None) -> dict:
        """Export the same editable MLT; no independent montage or packaging path."""
        return self.render(compiled, output, qmelt=qmelt)
