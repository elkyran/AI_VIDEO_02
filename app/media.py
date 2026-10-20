"""Deterministic source-asset preparation. Final editing belongs to Shotcut/MLT."""
from __future__ import annotations
import math
from pathlib import Path
import wave
import numpy as np


def original_music(path: Path, duration: float, rate: int = 44100) -> None:
    size = math.ceil(duration*rate)
    audio = np.zeros((size,2), dtype=np.float32)
    chords = [(50,57,62,65),(46,53,58,62),(53,60,65,69),(48,55,60,64)]
    def note(midi,start,length,gain,pan,pad=False):
        offset=int(start*rate)
        count=min(int(length*rate),size-offset)
        if count<=0:return
        t=np.arange(count,dtype=np.float32)/rate
        freq=440*2**((midi-69)/12)
        if pad:
            env=np.minimum(t/1.8,1)*np.minimum((length-t)/2.5,1)
            tone=(np.sin(2*np.pi*freq*t)+.28*np.sin(2*np.pi*freq*1.002*t))*env
        else:
            env=(1-np.exp(-t*75))*np.exp(-t*1.1)
            tone=(np.sin(2*np.pi*freq*t)+.3*np.sin(2*np.pi*freq*2*t)*np.exp(-t*1.4)
                  +.08*np.sin(2*np.pi*freq*3*t)*np.exp(-t*3))*env
        audio[offset:offset+count,0]+=tone*gain*math.sqrt((1-pan)/2)
        audio[offset:offset+count,1]+=tone*gain*math.sqrt((1+pan)/2)
    for bar in range(math.ceil(duration/4)):
        chord=chords[bar%4]
        for j,midi in enumerate(chord):note(midi-12,bar*4,6,.019,(j-1.5)/3,True)
        for beat,j in enumerate([0,2,1,3]):note(chord[j]+12,bar*4+beat,3.4,.052,[-.35,.25,-.1,.35][beat])
    dry=audio.copy()
    for delay,gain in [(.23,.18),(.47,.10),(.71,.05)]:
        lag=int(delay*rate)
        if size>lag:audio[lag:]+=dry[:-lag,::-1]*gain
    peak=float(np.abs(audio).max())
    if peak:audio*=.65/peak
    ramp=min(2*rate,size//2)
    audio[:ramp]*=np.linspace(0,1,ramp)[:,None]
    audio[-ramp:]*=np.linspace(1,0,ramp)[:,None]
    with wave.open(str(path),'wb') as stream:
        stream.setnchannels(2);stream.setsampwidth(2);stream.setframerate(rate)
        stream.writeframes((audio.clip(-.98,.98)*32767).astype('<i2').tobytes())


def framing_filter(width: int,height: int,*,fit='cover',focal_x=.5,focal_y=.5) -> str:
    """Keep square pixels and source geometry, including tall manga panels."""
    if fit=='contain':
        return (f'scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,'
                f'pad={width}:{height}:(ow-iw)/2:(oh-ih)/2:color=0x101318,setsar=1')
    if fit!='cover':raise ValueError('Unknown framing mode')
    return (f'scale={width}:{height}:force_original_aspect_ratio=increase:flags=lanczos,'
            f'crop={width}:{height}:(iw-ow)*{focal_x}:(ih-oh)*{focal_y},setsar=1')


def camera_filter(frames: int, index: int, *, fps=30,width=1920,height=1080,
                  motion='auto',fit='cover',focal_x=.5,focal_y=.5) -> str:
    if frames<1:raise ValueError('Camera movement needs at least one frame')
    if motion=='auto':motion=('zoom_in','zoom_out','pan_right')[index%3]
    denominator=max(frames-1,1)
    x,y=f'(iw-iw/zoom)*{focal_x}',f'(ih-ih/zoom)*{focal_y}'
    if motion=='zoom_in':zoom=f'1.015+0.045*on/{denominator}'
    elif motion=='zoom_out':zoom=f'1.06-0.045*on/{denominator}'
    elif motion in ('pan_left','pan_right'):
        zoom='1.06'
        progress=f'(0.2+0.6*on/{denominator})'
        x=f'(iw-iw/zoom)*{progress if motion=="pan_right" else "(1-"+progress+")"}'
    elif motion=='static':zoom='1'
    else:raise ValueError('Unknown camera movement')
    return (framing_filter(2560,1440,fit=fit,focal_x=focal_x,focal_y=focal_y)
            +f",zoompan=z='{zoom}':x='{x}':y='{y}':d={frames}:s={width}x{height}:fps={fps},format=yuv420p")
