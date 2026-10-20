"""Replace fitted narration without rerendering the verified original video.

The registry is read only. A new portable project retains the original visual
MLT graph and music; only voice resources change. MLT renders its audio mix,
then FFmpeg copies the original video packets. No trimming or time stretching
is performed here. speech_manifest.json is input to scripts/transcribe_audio.py;
replacement_report.json records technical checks, not an editorial/ASR pass.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from copy import deepcopy
from fractions import Fraction
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import subprocess
import sys
import wave
import xml.etree.ElementTree as ET

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.registry import content_hash, workspace_path
from app.timeline import _validate


def read_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8-sig'))


def save_json(path, value):
    Path(path).write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)+'\n', encoding='utf-8')


def bounded(root: Path, value, *, write=False):
    path = Path(value)
    if path.is_absolute():
        try:
            value = path.resolve().relative_to(root.resolve()).as_posix()
        except ValueError as exc:
            raise ValueError('Path leaves workspace') from exc
    # argparse supplies Path objects; Windows renders str(Path) with backslashes,
    # while the workspace contract deliberately accepts portable POSIX paths.
    return workspace_path(root, Path(value).as_posix(), for_write=write)


def read_job(root, identifier, operation):
    with closing(sqlite3.connect((root/'registry.sqlite').resolve().as_uri()+'?mode=ro', uri=True)) as db:
        db.row_factory = sqlite3.Row
        row = db.execute('SELECT * FROM jobs WHERE id=?', (identifier,)).fetchone()
    if row is None or row['operation'] != operation or row['state'] != 'done':
        raise ValueError(f'{identifier} must be a completed {operation} job')
    return dict(row), json.loads(row['result'])


def verify_sources(root, build, render):
    project = bounded(root, build['project_mlt'])
    timeline = bounded(root, build['timeline_json'])
    video = bounded(root, render['video'])
    if timeline.parent != project.parent or timeline.name != 'timeline.json':
        raise ValueError('Timeline must belong to the original project')
    if (render['project_sha256'] != build['project_sha256']
            or content_hash(project) != build['project_sha256']
            or content_hash(video) != render['sha256']):
        raise ValueError('Original project or render hash mismatch')
    hashes = build['file_hashes']
    if not {'project.mlt', 'timeline.json'}.issubset(hashes):
        raise ValueError('Original build does not fingerprint its project and timeline')
    for relative, digest in hashes.items():
        if content_hash(bounded(project.parent, relative)) != digest:
            raise ValueError(f'Original build file hash mismatch: {relative}')
    data = read_json(timeline)
    _validate(data)
    media = [data['music']]
    for segment in data['segments']:
        media += [segment['audio'], segment['visual']]
        media += [s['visual'] for s in segment.get('visual_shots', [])]
    if any(path not in hashes for path in media):
        raise ValueError('Original build has unfingerprinted media')
    return project, video, data


def validate_narration(root, manifest, timeline):
    """Measure every complete WAV; reject stale text, paths, hashes and overflow."""
    entries = manifest.get('segments')
    if manifest.get('project_id') != timeline['project_id']:
        raise ValueError('Narration project does not match the original timeline')
    if not isinstance(entries, list) or len(entries) != len(timeline['segments']):
        raise ValueError('Narration block count differs from the original timeline')
    checked, cursor = [], 0
    for entry, segment in zip(entries, timeline['segments']):
        if entry.get('block_id') != segment['id'] or entry.get('text') != segment['text']:
            raise ValueError('Narration block order/id/text differs from the original timeline')
        path = bounded(root, entry['path'])
        if path.suffix.lower() != '.wav' or content_hash(path) != entry.get('wav_hash'):
            raise ValueError(f'New WAV hash mismatch: {segment["id"]}')
        with wave.open(str(path), 'rb') as sound:
            frames, rate = sound.getnframes(), sound.getframerate()
            channels, width = sound.getnchannels(), sound.getsampwidth()
            if sound.getcomptype() != 'NONE' or not frames or rate < 8000 or channels not in (1, 2):
                raise ValueError('Expected nonempty PCM mono/stereo narration WAV')
            byte_count = 0
            while chunk := sound.readframes(65536):
                byte_count += len(chunk)
            if byte_count != frames*channels*width:
                raise ValueError('Narration WAV is truncated')
        slot_frames = math.ceil(segment['duration']*timeline['fps']-1e-7)
        duration = Fraction(frames, rate)
        slot = Fraction(slot_frames, timeline['fps'])
        if duration > slot:
            raise ValueError(f'Narration overflows original slot: {segment["id"]}')
        claimed = entry.get('duration_seconds')
        if (isinstance(claimed, bool) or not isinstance(claimed, (int, float))
                or not math.isfinite(claimed) or abs(claimed-float(duration)) > 1/rate):
            raise ValueError('Narration duration metadata differs from measured WAV')
        if entry.get('samples_trimmed', 0) != 0:
            raise ValueError('Narration reports trimmed samples; provide a complete fitted WAV')
        start = cursor/timeline['fps']
        if 'start_seconds' in entry:
            supplied_start = entry['start_seconds']
            if (isinstance(supplied_start, bool) or not isinstance(supplied_start, (int, float))
                    or not math.isfinite(supplied_start) or abs(supplied_start-start) > 1e-7):
                raise ValueError('Narration start differs from original slot')
        checked.append({'path': path, 'block_id': segment['id'], 'frames': frames,
                        'sample_rate': rate, 'duration_seconds': float(duration),
                        'slot_frames': slot_frames, 'slot_duration_seconds': float(slot),
                        'start_seconds': start, 'unused_slot_seconds': float(slot-duration)})
        cursor += slot_frames
    return checked


def stage_project(source, destination, build, timeline, manifest, checked):
    """Copy original assets verbatim and change only narration resource paths."""
    destination.mkdir()
    for relative, digest in build['file_hashes'].items():
        source_file = bounded(source.parent, relative)
        target = bounded(destination, relative, write=True)
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(source_file, target)
        if content_hash(target) != digest:
            raise ValueError('Build file changed while staging')
    portable = deepcopy(timeline)
    tree = ET.parse(source)
    mlt = tree.getroot()
    voice_entries = mlt.findall("./playlist[@id='narrator']/entry")
    if len(voice_entries) != len(checked):
        raise ValueError('Original MLT narrator differs from timeline')
    for index, (entry, measured, segment, old_segment) in enumerate(zip(manifest['segments'], checked, portable['segments'], timeline['segments'])):
        voice = mlt.find(f"./producer[@id='voice_{index}']")
        resource = voice.find("property[@name='resource']") if voice is not None else None
        old_entry = voice_entries[index]
        if (resource is None or resource.text != old_segment['audio']
                or old_entry.attrib != {'producer': f'voice_{index}', 'in': '0', 'out': str(measured['slot_frames']-1)}):
            raise ValueError('Original MLT voice timing/resource differs from timeline')
        relative = f'media/narration_{index:03}_{entry["wav_hash"]}.wav'
        target = bounded(destination, relative, write=True)
        if target.exists():
            raise FileExistsError('New narration resource conflicts with original media')
        shutil.copy2(measured['path'], target)
        if content_hash(target) != entry['wav_hash']:
            raise ValueError('New narration changed while staging')
        resource.text = relative
        segment['audio'] = relative
    # This is the original editable graph, including original music gain, titles,
    # shot boundaries and filters. No montage is regenerated from approximate time.
    tree.write(destination/'project.mlt', encoding='utf-8', xml_declaration=True)
    save_json(destination/'timeline.json', portable)
    return portable


def audio_export_graph(project: Path) -> Path:
    """Derive a cheap export graph; preserve every audible node and frame rate.

    avformat vn=1 suppresses video encoding but still visits the visual graph.
    Keep the editable project untouched and omit only its explicitly muted
    visual tracks. The two original audio mix transitions keep track indices.
    """
    tree = ET.parse(project)
    mlt = tree.getroot()
    tractor = mlt.find('tractor')
    tracks = tractor.findall('track') if tractor is not None else []
    if ([track.get('producer') for track in tracks[:3]] != ['background','narrator','music']
            or any(track.get('producer') not in ('visuals','titles') or track.get('hide') != 'audio'
                   for track in tracks[3:])):
        raise ValueError('Audio-only optimization requires the known MVP track graph')
    transitions = tractor.findall('transition')
    mixes = []
    for transition in transitions:
        properties = {p.get('name'):p.text for p in transition.findall('property')}
        if properties.get('mlt_service') == 'mix':
            if properties.get('a_track') != '0' or properties.get('b_track') not in ('1','2'):
                raise ValueError('Unexpected audio mixing tracks')
            mixes.append(properties['b_track'])
        elif properties.get('mlt_service') == 'frei0r.cairoblend' and properties.get('b_track') in ('3','4'):
            tractor.remove(transition)
        else:
            raise ValueError('Unsupported transition in audio-only graph')
    if sorted(mixes) != ['1','2']:
        raise ValueError('Expected both original narrator/music mix transitions')
    for track in tracks[3:]:
        tractor.remove(track)
    for node in list(mlt):
        identifier = node.get('id','')
        if identifier in ('visuals','titles') or identifier.startswith(('visual_','title_')):
            mlt.remove(node)
    profile = mlt.find('profile')
    if profile is None:
        raise ValueError('Missing original frame-rate profile')
    # Audio timing and samples depend on fps, not this discarded black image.
    profile.set('width','16'); profile.set('height','16')
    profile.set('display_aspect_num','1'); profile.set('display_aspect_den','1')
    destination = project.with_name('audio_export.mlt')
    if destination.exists():
        raise FileExistsError('Audio export graph already exists')
    tree.write(destination, encoding='utf-8', xml_declaration=True)
    return destination


def run(command, directory, log_name, *, cwd=None, timeout=1800):
    environment = os.environ.copy()
    environment['QT_QPA_PLATFORM'] = 'offscreen'
    environment['PATH'] = str(Path(command[0]).parent)+os.pathsep+environment.get('PATH', '')
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    with (directory/log_name).open('w', encoding='utf-8') as log:
        result = subprocess.run(command, cwd=cwd or directory, env=environment, stdin=subprocess.DEVNULL,
                                stdout=log, stderr=subprocess.STDOUT, timeout=timeout, creationflags=flags)
    if result.returncode:
        raise RuntimeError(f'Local process failed; see {directory/log_name}')


def probe(ffprobe, path):
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    return json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', str(path)],
                                              creationflags=flags, timeout=120))


def video_packets(ffprobe, path):
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    result = json.loads(subprocess.check_output([ffprobe, '-v', 'error', '-select_streams', 'v:0',
        '-show_packets', '-show_data_hash', 'sha256', '-show_entries', 'packet=pts,dts,duration,size,flags,data_hash',
        '-of', 'json', str(path)], creationflags=flags, timeout=180))
    packets = result.get('packets', [])
    if not packets or any('data_hash' not in p for p in packets):
        raise ValueError('Video packet hashes are unavailable')
    return packets


def replace_narration(workspace, build_job_id, render_job_id, audio_manifest, output_dir, *, verify_decoded_frames=False):
    root = Path(workspace).resolve(strict=True)
    manifest_path = bounded(root, audio_manifest)
    output = bounded(root, output_dir, write=True)
    if output.exists():
        raise FileExistsError('Output directory already exists; choose a new version')
    build_row, build = read_job(root, build_job_id, 'build')
    render_row, render = read_job(root, render_job_id, 'render')
    if render['build_job_id'] != build_job_id or build_row['project_id'] != render_row['project_id']:
        raise ValueError('Original render does not belong to the specified build')
    source, video, timeline = verify_sources(root, build, render)
    if timeline['project_id'] != build_row['project_id']:
        raise ValueError('Original timeline project differs from registry')
    manifest_hash = content_hash(manifest_path)
    manifest = read_json(manifest_path)
    checked = validate_narration(root, manifest, timeline)
    config = read_json(root/'runtime.json')
    executables = {}
    for name in ('ffmpeg_path', 'ffprobe_path', 'melt_path'):
        path = Path(config[name]).resolve(strict=True)
        if not path.is_file():
            raise ValueError(f'Missing local executable {name}')
        executables[name] = str(path)
    ffmpeg, ffprobe, melt = (executables[k] for k in ('ffmpeg_path','ffprobe_path','melt_path'))
    original_probe = probe(ffprobe, video)
    streams = [s for s in original_probe['streams'] if s['codec_type'] == 'video']
    if len(streams) != 1:
        raise ValueError('Expected exactly one original video stream')
    original_stream = streams[0]
    frames = sum(m['slot_frames'] for m in checked)
    duration = frames/timeline['fps']
    if (original_stream['width'] != timeline['width'] or original_stream['height'] != timeline['height']
            or Fraction(original_stream['r_frame_rate']) != timeline['fps']
            or abs(float(original_stream['duration'])-duration) > 1e-5):
        raise ValueError('Original video dimensions/timing differ from timeline')
    time_base = Fraction(original_stream['time_base'])
    if time_base.numerator != 1:
        raise ValueError('Original MP4 video time base is unsupported for exact timestamp copying')
    before_packets = video_packets(ffprobe, video)
    if len(before_packets) != frames:
        raise ValueError('Original packet count differs from timeline frames')
    output.mkdir(parents=True)
    portable = stage_project(source, output/'project', build, timeline, manifest, checked)
    audio_project = audio_export_graph(output/'project/project.mlt')
    mixed = output/'narration_mix.wav'
    command = [melt, str(audio_project), '-consumer', f'avformat:{mixed}',
               'vn=1', 'acodec=pcm_s16le', 'ar=48000', 'ac=2', 'real_time=-1', 'threads=1', '-progress']
    run(command, output, 'audio_render.log', cwd=output/'project')
    with wave.open(str(mixed), 'rb') as sound:
        mixed_frames, mixed_rate = sound.getnframes(), sound.getframerate()
        if (sound.getnchannels() != 2 or mixed_rate != 48000
                or abs(Fraction(mixed_frames, mixed_rate)-Fraction(frames, timeline['fps'])) > Fraction(1, mixed_rate)):
            raise ValueError('MLT audio duration differs from original timeline; output not promoted')
    temporary = output/'replacement.partial.mp4'
    mux = [ffmpeg, '-v', 'error', '-nostdin', '-n', '-i', str(video), '-i', str(mixed),
           '-map', '0:v:0', '-map', '1:a:0', '-map_metadata', '0', '-map_chapters', '0',
           '-c:v', 'copy', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2',
           '-video_track_timescale', str(time_base.denominator), '-avoid_negative_ts', 'disabled',
           '-movflags', '+faststart', str(temporary)]
    run(mux, output, 'mux.log')
    after_packets = video_packets(ffprobe, temporary)
    if before_packets != after_packets:
        raise ValueError('Copied video packet contents or timestamps changed; output not promoted')
    final_probe = probe(ffprobe, temporary)
    audio_streams = [s for s in final_probe['streams'] if s['codec_type'] == 'audio']
    if (len(audio_streams) != 1 or abs(float(audio_streams[0].get('start_time', 0))) > .001
            or not duration-.001 <= float(audio_streams[0]['duration']) <= duration+1024/48000+.001):
        raise ValueError('Muxed audio timing is incomplete or overflowing')
    decoded = None
    if verify_decoded_frames:
        for label, path in (('original', video), ('replacement', temporary)):
            run([ffmpeg, '-v', 'error', '-i', str(path), '-map', '0:v:0', '-f', 'framemd5', '-'], output, f'{label}.framemd5', timeout=1800)
        decoded = content_hash(output/'original.framemd5') == content_hash(output/'replacement.framemd5')
        if not decoded:
            raise ValueError('Decoded video frames changed; output not promoted')
    # Revalidate inputs after subprocesses and staging: none may change mid-run.
    verify_sources(root, build, render)
    if content_hash(manifest_path) != manifest_hash:
        raise ValueError('New audio manifest changed during replacement')
    validate_narration(root, manifest, timeline)
    speech = deepcopy(manifest)
    speech['workspace_root'] = str(root)
    speech['timeline_duration_seconds'] = duration
    speech['timeline_segments'] = [{'id':s['id'], 'duration':s['duration']} for s in portable['segments']]
    speech['critical_terms'] = config.get('pronunciation_terms', [])
    for entry, segment, measured in zip(speech['segments'], portable['segments'], checked):
        entry['path'] = str(output/'project'/segment['audio'])
        entry['start_seconds'] = measured['start_seconds']
    save_json(output/'speech_manifest.json', speech)
    save_json(output/'audio_manifest.json', manifest)
    final = output/'final.mp4'
    temporary.rename(final)
    report = {
        'schema_version': '1.0', 'operation': 'replace_narration', 'project_id': timeline['project_id'],
        'original_build_job_id': build_job_id, 'original_render_job_id': render_job_id,
        'original_video': render['video'], 'original_video_sha256': render['sha256'],
        'original_project_sha256': build['project_sha256'], 'input_audio_manifest_sha256': manifest_hash,
        'script_and_visuals_unchanged': True, 'original_files_preserved': True,
        'video_packets_verified': len(after_packets), 'video_packet_payloads_and_timestamps_identical': True,
        'video_packet_records_sha256': hashlib.sha256(json.dumps(after_packets, sort_keys=True).encode()).hexdigest(),
        'decoded_frames_identical': decoded,
        'audio_mix_engine': 'Original Shotcut/MLT audio nodes and mix transitions; export-only graph omits muted visual tracks and uses 16x16 at original fps',
        'duration_seconds': duration, 'audio_mix_frames': mixed_frames, 'audio_mix_sample_rate': mixed_rate,
        'music_sha256': content_hash(output/'project'/portable['music']),
        'narration_slots': [{k:str(v) if isinstance(v, Path) else v for k,v in m.items()} for m in checked],
        'commands': {'audio_render': command, 'mux': mux},
        'qa_scope': 'Technical replacement integrity only. Run normal transcribe_audio.py on speech_manifest.json and review speech; this report does not assert an ASR or listening pass.',
        'artifacts': {name: {'path':(output/name).relative_to(root).as_posix(), 'sha256':content_hash(output/name)}
                      for name in ('final.mp4','narration_mix.wav','project/project.mlt','project/audio_export.mlt','project/timeline.json','speech_manifest.json','audio_manifest.json')},
    }
    save_json(output/'replacement_report.json', report)
    return report


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--workspace', type=Path, required=True)
    parser.add_argument('--build-job-id', required=True)
    parser.add_argument('--render-job-id', required=True)
    parser.add_argument('--audio-manifest', type=Path, required=True)
    parser.add_argument('--output-dir', type=Path, required=True)
    parser.add_argument('--verify-decoded-frames', action='store_true')
    args = parser.parse_args()
    result = replace_narration(**vars(args))
    print(json.dumps(result, ensure_ascii=True))


if __name__ == '__main__':
    main()
