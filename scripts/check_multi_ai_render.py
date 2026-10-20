"""Three-second synthetic MLT acceptance check. No model, network or production DB.

The picture is trimmed from green and blue intervals of a red/green/blue source.
An independent generated tone is placed from 500 to 2500 ms. Decoded pixels and
audio, not just process exit status, establish the technical result.
"""
from __future__ import annotations

import argparse
from array import array
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import uuid
import wave

from app.editorial import demo_result
from app.production_bridge import ProductionBridge
from app.registry import StudioError, content_hash


def _tool(name: str) -> str:
    found = shutil.which(name)
    if not found:
        raise StudioError('capability_gap', f'Installed {name} is required for this synthetic preview')
    return found


def _run(arguments: list[str], *, timeout: int = 90) -> bytes:
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    result = subprocess.run(arguments, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, timeout=timeout, creationflags=flags, check=False)
    if result.returncode:
        raise StudioError('FIXTURE_PROCESS_FAILED', result.stderr.decode('utf-8', errors='replace')[-4000:])
    return result.stdout


def create_technical_fixture(workspace: str | Path) -> dict:
    """Create or verify generated assets; safe for a dedicated development workspace.

    Returns assets, scene_version, voice_version and an explicit simulated EditPlan.
    Existing files are never overwritten. This helper does not open a registry.
    """
    root = Path(workspace).resolve()
    folder = root / 'technical_fixture'
    manifest = folder / 'assets.json'
    if folder.exists():
        if not manifest.is_file():
            raise StudioError('OUTPUT_EXISTS', 'Incomplete fixture directory exists; use a new workspace')
        data = json.loads(manifest.read_text(encoding='utf-8'))
        for row in data['assets']:
            from app.registry import workspace_path
            path = workspace_path(root, row['path'])
            if not path.is_file() or content_hash(path) != row['sha256']:
                raise StudioError('ASSET_CHANGED', 'Technical fixture was modified; preserve it and use a new workspace')
        return data
    folder.mkdir(parents=True, exist_ok=False)
    video = folder / 'source.mp4'
    _run([_tool('ffmpeg'), '-hide_banner', '-loglevel', 'error', '-n',
          '-f', 'lavfi', '-i', 'color=c=red:s=320x180:r=30:d=2',
          '-f', 'lavfi', '-i', 'color=c=green:s=320x180:r=30:d=2',
          '-f', 'lavfi', '-i', 'color=c=blue:s=320x180:r=30:d=2',
          '-filter_complex', '[0:v][1:v][2:v]concat=n=3:v=1:a=0[v]', '-map', '[v]',
          '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18', '-pix_fmt', 'yuv420p',
          '-threads', '1', str(video)])
    audio = folder / 'voice.wav'
    rate = 48000
    samples = array('h', [round(10000 * math.sin(2 * math.pi * 440 * index / rate)) for index in range(rate * 2)])
    import sys
    if sys.byteorder != 'little':
        samples.byteswap()
    with wave.open(str(audio), 'wb') as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())
    assets = [{'asset_id': identifier, 'path': path.relative_to(root).as_posix(), 'sha256': content_hash(path),
               'kind': kind, 'duration_ms': duration, 'rights': 'owned'}
              for identifier, path, kind, duration in [('fixture_video', video, 'video', 6000),
                                                       ('fixture_voice', audio, 'audio', 2000)]]
    data = {'assets': assets, 'scene_version': 1, 'voice_version': 'voice:v1',
            'edit_plan': demo_result('edit_plan'), 'notice': 'Generated colors and tone; no creative-quality baseline.'}
    manifest.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
    return data


def prepare_preview(workspace: str | Path, plan: dict | None = None) -> dict:
    """Explicit heavy local action; caller must serialize it with other heavy jobs."""
    root = Path(workspace).resolve()
    fixture = create_technical_fixture(root)
    selected = plan or fixture['edit_plan']
    bridge = ProductionBridge(root)
    run_id = uuid.uuid4().hex[:12]
    compiled = bridge.compile(selected, fixture['assets'], f'previews/{run_id}',
                              scene_version=selected.get('scene_version'), voice_version=fixture['voice_version'])
    rendered = bridge.render(compiled, f'previews/{run_id}/preview.mp4')
    return {'compiled': compiled, 'rendered': rendered, 'preview_path': rendered['output'],
            'simulated': True, 'quality_status': 'not_checked', 'technical_fixture': True}


def _pixel(video: Path, at: float) -> tuple[float, float, float]:
    raw = _run([_tool('ffmpeg'), '-v', 'error', '-ss', str(at), '-i', str(video),
                '-frames:v', '1', '-vf', 'scale=1:1', '-pix_fmt', 'rgb24', '-f', 'rawvideo', '-'])
    if len(raw) != 3:
        raise StudioError('FIXTURE_CHECK_FAILED', 'Cannot decode expected RGB pixel')
    return tuple(raw)


def run_smoke(workspace: str | Path) -> dict:
    result = prepare_preview(workspace)
    video = Path(result['preview_path'])
    probe = json.loads(_run([_tool('ffprobe'), '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(video)]))
    picture = next(row for row in probe['streams'] if row['codec_type'] == 'video')
    green, blue = _pixel(video, .7), _pixel(video, 2.1)
    # Whole decoded mono PCM permits checking the intended silence and voice independently.
    sound = array('f')
    sound.frombytes(_run([_tool('ffmpeg'), '-v', 'error', '-i', str(video), '-vn', '-ac', '1', '-ar', '48000',
                         '-f', 'f32le', '-']))
    import sys
    if sys.byteorder != 'little':
        sound.byteswap()
    def rms(start, end):
        section = sound[int(start * 48000):int(end * 48000)]
        return math.sqrt(sum(value * value for value in section) / len(section)) if section else None
    levels = {'before': rms(.1, .35), 'voice': rms(.8, 1.2), 'after': rms(2.7, 2.9)}
    checks = {
        'profile': picture['width'] == 320 and picture['height'] == 180 and picture['avg_frame_rate'] == '30/1',
        'duration': abs(float(probe['format']['duration']) - 3.0) < .08,
        'source_in_green': green[1] > 80 and green[1] > green[0] * 3 and green[1] > green[2] * 3,
        'second_source_blue': blue[2] > 180 and blue[2] > blue[0] * 3 and blue[2] > blue[1] * 3,
        'separate_voice_timing': levels['before'] is not None and levels['before'] < .003
                                and levels['voice'] is not None and levels['voice'] > .05
                                and levels['after'] is not None and levels['after'] < .003,
        'source_registry_absent': not (Path(workspace) / 'registry.sqlite').exists(),
    }
    report = {**result, 'status': 'passed' if all(checks.values()) else 'failed', 'checks': checks,
              'measurements': {'green_rgb': green, 'blue_rgb': blue, 'audio_rms': levels,
                               'duration_seconds': float(probe['format']['duration'])},
              'quality_status': 'technical_fixture_only', 'creative_quality': 'not_checked'}
    (video.parent / 'smoke.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    return report


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output-dir', type=Path, help='New/dedicated development workspace; default is temporary')
    arguments = parser.parse_args(argv)
    if arguments.output_dir:
        report = run_smoke(arguments.output_dir)
    else:
        with tempfile.TemporaryDirectory(prefix='multi_ai_native_') as folder:
            report = run_smoke(folder)
            report['temporary_artifacts_removed'] = True
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return 0 if report['status'] == 'passed' else 2


if __name__ == '__main__':
    raise SystemExit(main())
