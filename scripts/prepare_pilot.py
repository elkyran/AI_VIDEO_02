"""Prepare source clips and original music; final montage is always rendered by MLT.

No narration or titles are baked into source clips. Each source clip remains an
independently replaceable asset in the Shotcut project.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import shutil
import subprocess
import wave

import numpy as np


def run(args: list[str]) -> None:
    subprocess.run(args, check=True)


def original_music(path: Path, duration: float, rate: int = 44100) -> None:
    """Deterministic original D-minor ambient score, no samples or borrowed melody."""
    size = int(math.ceil(duration * rate))
    audio = np.zeros((size, 2), dtype=np.float32)
    chords = [(50, 57, 62, 65), (46, 53, 58, 62), (53, 60, 65, 69), (48, 55, 60, 64)]
    def note(midi: int, start: float, length: float, gain: float, pan: float, pad=False):
        offset = int(start * rate)
        count = min(int(length * rate), size - offset)
        if count <= 0:
            return
        t = np.arange(count, dtype=np.float32) / rate
        freq = 440 * 2 ** ((midi - 69) / 12)
        if pad:
            envelope = np.minimum(t / 1.8, 1) * np.minimum((length - t) / 2.5, 1)
            tone = (np.sin(2*np.pi*freq*t) + .28*np.sin(2*np.pi*(freq*1.002)*t)) * envelope
        else:
            envelope = (1-np.exp(-t*75)) * np.exp(-t*1.1)
            tone = (np.sin(2*np.pi*freq*t) + .3*np.sin(2*np.pi*freq*2*t) * np.exp(-t*1.4)
                    + .08*np.sin(2*np.pi*freq*3*t) * np.exp(-t*3)) * envelope
        tone = tone.astype(np.float32) * gain
        audio[offset:offset+count, 0] += tone * math.sqrt((1-pan)/2)
        audio[offset:offset+count, 1] += tone * math.sqrt((1+pan)/2)
    bar_length = 4.0
    for bar in range(math.ceil(duration / bar_length)):
        chord = chords[bar % len(chords)]
        for index, midi in enumerate(chord):
            note(midi - 12, bar*bar_length, 6, .019, (index-1.5)/3, pad=True)
        for beat, index in enumerate([0, 2, 1, 3]):
            note(chord[index]+12, bar*bar_length+beat, 3.4, .052, [-.35,.25,-.1,.35][beat])
    # Gentle finite stereo echo, without sample recursion or external effects.
    dry = audio.copy()
    for seconds, gain in [(0.23,.18),(.47,.10),(.71,.05)]:
        lag = int(seconds*rate)
        audio[lag:] += dry[:-lag, ::-1] * gain
    ramp = min(int(2*rate), size//2)
    audio[:ramp] *= np.linspace(0,1,ramp)[:,None]
    audio[-ramp:] *= np.linspace(1,0,ramp)[:,None]
    pcm = np.clip(audio, -.95, .95)
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(2)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes((pcm * 32767).astype('<i2').tobytes())


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--project', type=Path, default=Path('delivery/mushoku_pilot'))
    parser.add_argument('--script', type=Path, default=Path('research/pilot_script.json'))
    parser.add_argument('--ffmpeg', default=shutil.which('ffmpeg'))
    args = parser.parse_args()
    if not args.ffmpeg:
        raise RuntimeError('FFmpeg is required to prepare the source clips')
    project = args.project.resolve()
    story = json.loads(args.script.read_text(encoding='utf-8-sig'))
    manifest = json.loads((project/'audio/audio_manifest.json').read_text(encoding='utf-8-sig'))
    measured = {a['block_id']: a for a in manifest['segments']}
    fps = 30
    segments = []
    for i, block in enumerate(story['segments']):
        seconds = measured[block['id']]['duration_seconds']
        duration = math.ceil((seconds + .24) * fps) / fps
        frames = round(duration*fps)
        destination = project/'media'/f"{block['id']}.mp4"
        image = project/'media'/block['image']
        # Alternating restrained camera moves, always using the original full image.
        if i % 3 == 0:
            zoom = f"1.03+0.065*on/{frames}"
            x, y = 'iw/2-iw/zoom/2', 'ih/2-ih/zoom/2'
        elif i % 3 == 1:
            zoom = f"1.10-0.06*on/{frames}"
            x, y = '(iw-iw/zoom)*0.62', '(ih-ih/zoom)*0.45'
        else:
            zoom = '1.10'
            x, y = f"(iw-iw/zoom)*(0.25+0.5*on/{frames})", '(ih-ih/zoom)*0.5'
        vf = (f"scale=2560:1440:flags=lanczos,zoompan=z='{zoom}':x='{x}':y='{y}'"
              f":d={frames}:s=1920x1080:fps={fps},format=yuv420p")
        if i == 0:
            vf += ',fade=t=in:st=0:d=0.35'
        if i == len(story['segments'])-1:
            vf += f',fade=t=out:st={duration-.6:.3f}:d=0.6'
        fingerprint = hashlib.sha256((hashlib.sha256(image.read_bytes()).hexdigest()+vf).encode()).hexdigest()
        stamp = destination.with_suffix('.source.json')
        reusable = destination.exists() and stamp.exists() and json.loads(stamp.read_text()).get('sha256') == fingerprint
        if not reusable:
            run([args.ffmpeg,'-hide_banner','-loglevel','error','-y','-i',str(image),'-vf',vf,
                 '-frames:v',str(frames),'-an','-c:v','libx264','-preset','fast','-crf','18',
                 '-threads','4','-movflags','+faststart',str(destination)])
            stamp.write_text(json.dumps({'sha256':fingerprint,'duration':duration,'source':image.name,'filter':vf}),encoding='utf-8')
        segments.append({
            'id':block['id'], 'text':block['text'], 'title':block['title'],
            'visual':f"media/{block['id']}.mp4", 'audio':f"audio/{block['id']}.wav", 'duration':duration,
        })
        print(json.dumps({'segment':block['id'],'seconds':duration,'cached':reusable}),flush=True)
    total = sum(s['duration'] for s in segments)
    original_music(project/'media/music.wav', total)
    timeline = {'schema_version':'1.0','project_id':story['project_id'],'revision':1,
                'fps':fps,'width':1920,'height':1080,'music':'media/music.wav','segments':segments}
    (project/'timeline_input.json').write_text(json.dumps(timeline,ensure_ascii=False,indent=2),encoding='utf-8')
    (project/'script.json').write_text(json.dumps(story,ensure_ascii=False,indent=2),encoding='utf-8')
    (project/'script.txt').write_text('\n\n'.join(b['text'] for b in story['segments']),encoding='utf-8')
    print(json.dumps({'timeline':str(project/'timeline_input.json'),'duration':total}),flush=True)


if __name__ == '__main__':
    main()
