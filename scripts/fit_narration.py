"""Fit completed Piper narration to an existing build using native synthesis only.

The source jobs and media remain immutable. A new directory contains at most
three native length_scale attempts per block and a measured audio_manifest.json.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from copy import deepcopy
import hashlib
import json
import math
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import wave

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

TARGET_MARGIN = .35
MIN_MARGIN = .20
MAX_ATTEMPTS = 3
SCALE_MIN, SCALE_MAX = .85, 1.40


def digest(path):
    h = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            h.update(chunk)
    return h.hexdigest()


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def relative(root, path):
    return Path(path).resolve().relative_to(root).as_posix()


def guarded(root, value):
    path = Path(value)
    if path.is_absolute() or '..' in path.parts:
        raise ValueError('Expected a relative path inside workspace')
    result = (root / path).resolve()
    if not result.is_relative_to(root) or result == root:
        raise ValueError('Path leaves workspace')
    return result


def checked_file(root, value, expected_hash):
    path = guarded(root, value)
    if not isinstance(expected_hash, str) or digest(path) != expected_hash:
        raise ValueError(f'File hash mismatch: {value}')
    return path


def measure_wav(path):
    with wave.open(str(path), 'rb') as stream:
        frames, rate = stream.getnframes(), stream.getframerate()
        if frames <= 0 or rate <= 0 or stream.getsampwidth() != 2:
            raise ValueError('Expected a nonempty PCM16 WAV')
        return frames / rate


def write_json(path, data):
    with Path(path).open('x', encoding='utf-8') as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
        stream.write('\n')


def fixed_silence(record):
    """Use exact chunk accounting when available; never remove those samples."""
    chunks = record.get('synthesis_chunks')
    if chunks:
        frames = record['frames'] - sum(c['generated_samples'] for c in chunks)
        if frames < 0:
            raise ValueError('Invalid synthesis chunk accounting')
        return frames / record['sample_rate_hz'], 'measured_chunk_accounting'
    settings = record['generation_settings']
    return (settings.get('leading_silence', .1) + settings.get('trailing_silence', .18),
            'known_edge_silence_only_legacy_metadata')


def validate_inputs(workspace, audio_job_id, build_job_id):
    root = Path(workspace).resolve()
    for jid in (audio_job_id, build_job_id):
        if not re.fullmatch(r'[A-Za-z0-9_-]+', jid):
            raise ValueError('Invalid job id')
    uri = (root / 'registry.sqlite').as_uri() + '?mode=ro'
    with closing(sqlite3.connect(uri, uri=True)) as db:
        db.row_factory = sqlite3.Row

        def job(jid, operation):
            row = db.execute('SELECT * FROM jobs WHERE id=?', (jid,)).fetchone()
            if not row or row['operation'] != operation or row['state'] != 'done':
                raise ValueError(f'{jid} must be a completed {operation} job')
            return dict(row), json.loads(row['result'])

        audio_job, audio = job(audio_job_id, 'synthesize')
        build_job, build = job(build_job_id, 'build')
        if audio_job['project_id'] != build_job['project_id']:
            raise ValueError('Source jobs belong to different projects')
        project_id = audio_job['project_id']

        def script(result):
            row = db.execute('SELECT * FROM assets WHERE project_id=? AND id=?',
                             (project_id, result['script_asset_id'])).fetchone()
            if not row or row['sha256'] != result['script_sha256']:
                raise ValueError('Script registry hash mismatch')
            return read_json(checked_file(root, row['path'], row['sha256']))

        audio_script, build_script = script(audio), script(build)
    manifest_path = checked_file(root, audio['manifest'], audio['manifest_sha256'])
    manifest = read_json(manifest_path)
    if (manifest['script_sha256'] != audio['script_sha256'] or
            manifest['script_asset_id'] != audio['script_asset_id'] or
            manifest['project_id'] != project_id):
        raise ValueError('Audio manifest/script identity mismatch')
    timeline_path = guarded(root, build['timeline_json'])
    expected_timeline = build['file_hashes'].get(timeline_path.name)
    if digest(timeline_path) != expected_timeline:
        raise ValueError('Build timeline hash mismatch')
    checked_file(root, build['project_mlt'], build['project_sha256'])
    timeline = read_json(timeline_path)
    records = manifest['segments']
    groups = [records, audio_script['segments'], build_script['segments'], timeline['segments']]
    ids = [[s.get('block_id', s.get('id')) for s in group] for group in groups]
    if not ids[0] or any(value != ids[0] for value in ids[1:]) or len(set(ids[0])) != len(ids[0]):
        raise ValueError('Block ids/order differ between source scripts, manifest and timeline')
    if timeline['project_id'] != project_id:
        raise ValueError('Timeline project mismatch')
    prepared = []
    model_hashes = {}
    start_frame = 0
    for index, record in enumerate(records):
        bid = ids[0][index]
        if not re.fullmatch(r'[A-Za-z0-9_-]+', bid):
            raise ValueError('Unsafe block id')
        if any(group[index]['text'] != record['text'] for group in groups[1:]):
            raise ValueError(f'Unchanged text check failed: {bid}')
        if record.get('engine') != 'piper':
            raise ValueError('Native fitting requires a Piper source synthesis job')
        source = checked_file(root, record['path'], record['wav_hash'])
        duration = measure_wav(source)
        if abs(duration - record['duration_seconds']) > 1e-8:
            raise ValueError(f'Source WAV duration mismatch: {bid}')
        request_path = guarded(root, f'jobs/{audio_job_id}/{bid}.request.json')
        request = read_json(request_path)
        if set(request) != {'text', 'output', 'config'}:
            raise ValueError('Invalid source TTS request')
        if hashlib.sha256(request['text'].encode('utf-8')).hexdigest() != record['text_hash']:
            raise ValueError(f'Source request text hash mismatch: {bid}')
        if Path(request['output']).resolve() != source:
            raise ValueError('Source request output does not match manifest')
        config = request['config']
        if config.get('engine', 'piper') != 'piper' or Path(config['workspace_root']).resolve() != root:
            raise ValueError('Source request engine/workspace mismatch')
        model = Path(config['model_path']).resolve()
        model_config = Path(config.get('config_path', str(model) + '.json')).resolve()
        for path, key in ((model, 'model_hash'), (model_config, 'model_config_hash')):
            if path not in model_hashes:
                model_hashes[path] = digest(path)
            if model_hashes[path] != record[key]:
                raise ValueError('Source Piper model/config hash mismatch')
        scale = float(record['generation_settings']['length_scale'])
        if not SCALE_MIN <= scale <= SCALE_MAX or scale != float(config.get('length_scale', 1.04)):
            raise ValueError('Baseline length_scale outside fitting limits or request mismatch')
        segment = timeline['segments'][index]
        frames = sum(s['frames'] for s in segment.get('visual_shots', [])) or round(segment['duration'] * timeline['fps'])
        slot = frames / timeline['fps']
        if not math.isfinite(slot) or slot <= TARGET_MARGIN or abs(slot-segment['duration']) > 1e-8:
            raise ValueError('Invalid fixed narration slot')
        prepared.append({'record': record, 'request': request, 'source': source,
                         'source_request': relative(root, request_path), 'source_request_sha256': digest(request_path),
                         'slot': slot, 'start_frame': start_frame, 'frames': frames})
        start_frame += frames
    return root, manifest, prepared, {
        'audio_job_id': audio_job_id, 'audio_manifest': relative(root, manifest_path),
        'audio_manifest_sha256': digest(manifest_path), 'build_job_id': build_job_id,
        'build_script_sha256': build['script_sha256'], 'timeline': relative(root, timeline_path),
        'timeline_sha256': digest(timeline_path), 'project_mlt_sha256': build['project_sha256'],
        'fps': timeline['fps'], 'total_visual_frames': start_frame,
        'fixed_video_duration_seconds': start_frame / timeline['fps']}


def fit_block(root, output, item, synthesize):
    baseline = deepcopy(item['record'])
    bid, slot = baseline['block_id'], item['slot']
    target, limit = slot - TARGET_MARGIN, slot - MIN_MARGIN
    baseline['path'] = relative(root, item['source'])
    attempts = [dict(baseline, fitting_attempt=0, baseline=True)]
    gap = slot - baseline['duration_seconds']
    if not (MIN_MARGIN <= gap < .8):
        current = baseline
        tried = {float(current['generation_settings']['length_scale'])}
        for attempt in range(1, MAX_ATTEMPTS + 1):
            silence, method = fixed_silence(current)
            if target <= silence or current['duration_seconds'] <= silence:
                break
            scale = float(current['generation_settings']['length_scale']) * (
                (target - silence) / (current['duration_seconds'] - silence))
            scale = round(min(SCALE_MAX, max(SCALE_MIN, scale)), 6)
            if scale in tried:
                break
            tried.add(scale)
            config = {**item['request']['config'], 'length_scale': scale}
            wav = output / f'{bid}.attempt_{attempt}.wav'
            result = synthesize(item['request']['text'], wav, config)
            duration = measure_wav(wav)
            if (digest(wav) != result['wav_hash'] or
                    abs(duration - result['duration_seconds']) > 1e-8):
                raise ValueError('Native synthesis result does not match actual WAV')
            for key in ('engine', 'voice_id', 'model_hash', 'model_config_hash', 'text_hash', 'use_cuda'):
                if result.get(key) != baseline.get(key):
                    raise ValueError(f'Native synthesis unexpectedly changed {key}')
            old_settings = {k: v for k, v in baseline['generation_settings'].items() if k != 'length_scale'}
            new_settings = {k: v for k, v in result['generation_settings'].items() if k != 'length_scale'}
            if new_settings != old_settings or result['generation_settings']['length_scale'] != scale:
                raise ValueError('Native synthesis changed settings besides length_scale')
            current = {**result, 'path': relative(root, wav), 'block_id': bid, 'text': baseline['text'],
                       'fitting_attempt': attempt, 'baseline': False,
                       'scale_prediction_fixed_silence_seconds': silence,
                       'scale_prediction_silence_method': method}
            attempts.append(current)
            if .25 <= slot - duration <= .55:
                break
    fitting = [r for r in attempts if r['duration_seconds'] <= limit + 1e-10]
    audit = {'block_id': bid, 'slot_duration_seconds': slot, 'target_speech_seconds': target,
             'maximum_speech_seconds': limit, 'attempts': attempts}
    write_json(output / f'{bid}.fitting.json', audit)
    if not fitting:
        raise ValueError(f'{bid}: no native attempt fits the slot; audio was not trimmed')
    chosen = min(fitting, key=lambda r: abs(r['duration_seconds'] - target))
    return {**chosen, 'id': baseline.get('id', 'audio_' + bid),
            'source_request': item['source_request'], 'source_request_sha256': item['source_request_sha256'],
            'source_wav_sha256': baseline['wav_hash'], 'source_request_hash': baseline['request_hash'],
            'start_frame': item['start_frame'], 'original_visual_frames': item['frames'],
            'target_slot_seconds': slot, 'target_speech_seconds': target,
            'remaining_slot_seconds': slot-chosen['duration_seconds'],
            'fit_verified': True, 'samples_trimmed': 0, 'pitch_shifted': False,
            'native_config': {'engine': 'piper', 'generation_settings': chosen['generation_settings'],
                              'normalization_scope': chosen.get('normalization_scope'), 'use_cuda': chosen['use_cuda']},
            'fitting_attempts': relative(root, output / f'{bid}.fitting.json')}


def fit_narration(workspace, audio_job_id, build_job_id, output_dir, *, synthesize=None):
    root, manifest, prepared, source = validate_inputs(workspace, audio_job_id, build_job_id)
    output = Path(output_dir)
    if output.is_absolute():
        output = output.resolve()
        if not output.is_relative_to(root):
            raise ValueError('Output directory must be inside workspace')
        output = guarded(root, relative(root, output))
    else:
        output = guarded(root, output)
    if any(part.casefold() in {'manual', 'originals'} for part in output.relative_to(root).parts):
        raise ValueError('Protected output path')
    if output.exists():
        raise ValueError('Output directory already exists; choose a new directory')
    if synthesize is None:
        from app.tts import synthesize
    output.mkdir(parents=True, exist_ok=False)
    records = []
    try:
        for item in prepared:
            record = fit_block(root, output, item, synthesize)
            records.append(record)
            print(json.dumps({'block_id': record['block_id'], 'duration_seconds': record['duration_seconds'],
                              'remaining_slot_seconds': record['remaining_slot_seconds'],
                              'length_scale': record['generation_settings']['length_scale']}), flush=True)
    except Exception as exc:
        write_json(output / 'failure.json', {'status': 'failed', 'message': str(exc),
                   'completed_blocks': [r['block_id'] for r in records], 'source': source,
                   'audio_trimmed': False})
        raise
    result = {**{k: v for k, v in manifest.items() if k != 'segments'}, 'segments': records,
              'total_duration_seconds': sum(r['duration_seconds'] for r in records),
              'listening_review': 'not_checked', 'fitting': {'status': 'fit_verified', 'source': source,
              'target_margin_seconds': TARGET_MARGIN, 'minimum_margin_seconds': MIN_MARGIN,
              'max_new_syntheses_per_block': MAX_ATTEMPTS, 'length_scale_bounds': [SCALE_MIN, SCALE_MAX],
              'method': 'native_piper_length_scale_only', 'video_modified': False}}
    target = output / 'audio_manifest.json'
    write_json(target, result)
    return {'manifest': relative(root, target), 'manifest_sha256': digest(target),
            'blocks': len(records), 'fixed_video_duration_seconds': source['fixed_video_duration_seconds']}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--audio-job-id', required=True)
    parser.add_argument('--build-job-id', required=True)
    parser.add_argument('--output-dir', type=Path, required=True,
                        help='New directory; relative paths are relative to workspace')
    args = parser.parse_args()
    root = args.workspace.resolve()
    python = Path(read_json(root / 'runtime.json')['tts_python']).resolve()
    if not python.is_file():
        raise ValueError('Configured TTS Python is unavailable')
    if python != Path(sys.executable).resolve():
        result = subprocess.run([str(python), str(Path(__file__).resolve()), *sys.argv[1:]],
                                cwd=Path.cwd(), check=False)
        raise SystemExit(result.returncode)
    print(json.dumps(fit_narration(root, args.audio_job_id, args.build_job_id, args.output_dir)))


if __name__ == '__main__':
    try:
        main()
    except (ValueError, OSError, KeyError, sqlite3.Error) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=True), file=sys.stderr)
        raise SystemExit(2)
