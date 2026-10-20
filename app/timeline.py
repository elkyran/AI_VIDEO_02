"""Portable Shotcut/MLT projects from the shared studio timeline contract.

The exact project.mlt opened by Shotcut is also consumed by qmelt for export.
No second montage or FFmpeg filter graph is maintained.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import xml.etree.ElementTree as ET


def _property(parent: ET.Element, name: str, value: object) -> None:
    ET.SubElement(parent, "property", {"name": name}).text = str(value)


def _atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, suffix=".tmp", delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(data)
    os.replace(temporary, path)


def _stage_media(source: str | Path, base_dir: Path, destination: Path) -> str:
    source_path = Path(source)
    if not source_path.is_absolute():
        source_path = base_dir / source_path
    source_path = source_path.resolve(strict=True)
    if not source_path.is_relative_to(base_dir.resolve()):
        raise ValueError("Media path leaves the configured source workspace")
    if not source_path.is_file():
        raise ValueError(f"Media is not a file: {source_path}")
    hasher = hashlib.sha256()
    with source_path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            hasher.update(chunk)
    digest = hasher.hexdigest()[:12]
    stem = re.sub(r"[^\w.-]+", "_", source_path.stem, flags=re.UNICODE)[:64]
    relative = Path("media") / f"{stem}_{digest}{source_path.suffix.lower()}"
    target = destination / relative
    target.parent.mkdir(parents=True, exist_ok=True)
    if source_path != target.resolve() and not target.exists():
        shutil.copy2(source_path, target)
    return relative.as_posix()


def _validate(timeline: dict) -> None:
    allowed = {"schema_version", "project_id", "revision", "fps", "width", "height", "music", "segments"}
    optional = {"heading", "footer", "title_style", "overlays_enabled"}
    if not allowed.issubset(timeline) or set(timeline) - allowed - optional:
        raise ValueError("Timeline fields differ from schema")
    for key in optional-{'overlays_enabled'}:
        if key in timeline and not isinstance(timeline[key], str):
            raise ValueError(f"{key} must be a string")
    if type(timeline.get('overlays_enabled',True)) is not bool:
        raise ValueError('overlays_enabled must be a boolean')
    if timeline.get('title_style','legacy') not in ('legacy','lower_third'):
        raise ValueError('Unknown title style')
    if timeline["schema_version"] != "1.0":
        raise ValueError("Unsupported timeline schema version")
    for key in ("revision", "fps", "width", "height"):
        if type(timeline[key]) is not int or timeline[key] <= 0:
            raise ValueError(f"{key} must be a positive integer")
    if not isinstance(timeline["project_id"], str) or not timeline["project_id"]:
        raise ValueError("project_id must be a nonempty string")
    if not isinstance(timeline["segments"], list) or not timeline["segments"]:
        raise ValueError("A timeline needs at least one segment")
    identifiers = set()
    for segment in timeline["segments"]:
        fields = {"id", "text", "title", "visual", "audio", "duration"}
        if not fields.issubset(segment) or set(segment)-fields-{'visual_shots'}:
            raise ValueError(f"Segment fields differ from schema: {set(segment) ^ fields}")
        if not isinstance(segment["id"], str) or not segment["id"] or segment["id"] in identifiers:
            raise ValueError("Segment ids must be unique nonempty strings")
        identifiers.add(segment["id"])
        for key in ("text", "visual", "audio"):
            if not isinstance(segment[key], str) or not segment[key]:
                raise ValueError(f"Segment {key} must be a nonempty string")
        if not isinstance(segment["title"], str):
            raise ValueError("Segment title must be a string")
        duration = segment["duration"]
        if isinstance(duration, bool) or not isinstance(duration, (int, float)) or not math.isfinite(duration) or duration <= 0:
            raise ValueError("Segment duration must be finite positive seconds")
        shots=segment.get('visual_shots',[])
        if not isinstance(shots,list):raise ValueError('visual_shots must be a list')
        for shot in shots:
            if (not isinstance(shot,dict) or not {'visual','frames'}.issubset(shot)
                    or set(shot)-{'visual','frames','label'}):
                raise ValueError('Invalid visual shot fields')
            if not isinstance(shot['visual'],str) or not shot['visual']:
                raise ValueError('A visual shot needs a media path')
            if type(shot['frames']) is not int or shot['frames']<1:
                raise ValueError('A visual shot needs positive integer frames')
            if not isinstance(shot.get('label',''),str):raise ValueError('A visual shot label must be a string')
        if shots and sum(shot['frames'] for shot in shots)!=math.ceil(duration*timeline['fps']-1e-7):
            raise ValueError('Visual shots must cover exactly the narration segment frames')


def _producer(root: ET.Element, identifier: str, resource: str, length: int, *, service: str = "avformat", caption: str = "") -> ET.Element:
    item = ET.SubElement(root, "producer", {"id": identifier, "in": "0", "out": str(length - 1)})
    _property(item, "resource", resource)
    _property(item, "mlt_service", service)
    _property(item, "length", length)
    _property(item, "eof", "pause")
    _property(item, "seekable", 1)
    if caption:
        _property(item, "shotcut:caption", caption)
    return item


def _playlist(root: ET.Element, identifier: str, name: str, *, audio: bool = False) -> ET.Element:
    item = ET.SubElement(root, "playlist", {"id": identifier})
    _property(item, "shotcut:name", name)
    _property(item, "shotcut:trackHeight", 80)
    _property(item, "shotcut:audio" if audio else "shotcut:video", 1)
    return item


def _entry(playlist: ET.Element, producer: str, frames: int) -> None:
    ET.SubElement(playlist, "entry", {"producer": producer, "in": "0", "out": str(frames - 1)})


def _xml(timeline: dict) -> bytes:
    fps, width, height = timeline["fps"], timeline["width"], timeline["height"]
    lengths = [math.ceil(segment["duration"] * fps - 1e-7) for segment in timeline["segments"]]
    total = sum(lengths)
    root = ET.Element("mlt", {"LC_NUMERIC": "C", "version": "7.40.0", "producer": "tractor", "shotcut": "1"})
    ET.SubElement(root, "profile", {
        "description": f"Studio {width}x{height} {fps} fps", "width": str(width), "height": str(height),
        "frame_rate_num": str(fps), "frame_rate_den": "1", "progressive": "1",
        "sample_aspect_num": "1", "sample_aspect_den": "1",
        "display_aspect_num": str(width), "display_aspect_den": str(height), "colorspace": "709",
    })
    black = _producer(root, "black", "0", total, service="color")
    _property(black, "mlt_image_format", "rgba")
    for index, (segment, length) in enumerate(zip(timeline["segments"], lengths)):
        shots=segment.get('visual_shots') or [{'visual':segment['visual'],'frames':length,'label':segment['title']}]
        for shot_index,shot in enumerate(shots):
            extension = Path(shot["visual"]).suffix.lower()
            service = "qimage" if extension in {".png", ".jpg", ".jpeg", ".webp", ".bmp", ".svg"} else "avformat"
            identifier=f'visual_{index}_{shot_index}' if segment.get('visual_shots') else f'visual_{index}'
            visual = _producer(root, identifier, shot['visual'], shot['frames'], service=service, caption=shot.get('label') or segment['title'])
            _property(visual, "set.test_audio", 1)
            _property(visual, "aspect_ratio", 1)
        audio = _producer(root, f"voice_{index}", segment["audio"], length, caption=segment["id"])
        _property(audio, "set.test_image", 1)
        if not timeline.get('overlays_enabled',True):
            continue
        title = _producer(root, f"title_{index}", "#00000000", length, service="color", caption=segment["title"])
        _property(title, "mlt_image_format", "rgba")
        lower_third=timeline.get('title_style')=='lower_third'
        text_filter = ET.SubElement(title, "filter", {"out": str(length - 1)})
        for key, value in {
            "mlt_service": "dynamictext", "shotcut:filter": "dynamicText",
            "argument": segment["title"].replace("#", "\\#"),
            "geometry": f"{int(width*100/1920)} {int(height*(850 if lower_third else 300)/1080)} {int(width*(1720 if lower_third else 900)/1920)} {int(height*(96 if lower_third else 420)/1080)}",
            "family": "Segoe UI Semibold", "size": int(height*(36 if lower_third else 72)/1080), "weight": 600,
            "fgcolour": "#fff6f0e5", "bgcolour": "#00000000", "olcolour": "#b0000000",
            "outline": 2, "pad": 0, "halign": "left", "valign": "middle", "opacity": 1,
        }.items():
            _property(text_filter, key, value)
        for label, y, font_size, color in (
            (timeline.get("heading", ""), 85, 26, "#ffebc48a"),
            (timeline.get("footer", ""), 980 if lower_third else 920, 22 if lower_third else 24, "#fff6f0e5"),
        ):
            if not label:
                continue
            label_filter = ET.SubElement(title, "filter", {"out": str(length - 1)})
            for key, value in {
                "mlt_service": "dynamictext", "shotcut:filter": "dynamicText", "argument": label.replace("#", "\\#"),
                "geometry": f"{int(width*100/1920)} {int(height*y/1080)} {int(width*1500/1920)} {int(height*80/1080)}",
                "family": "Segoe UI", "size": int(height*font_size/1080), "weight": 400,
                "fgcolour": color, "bgcolour": "#00000000", "olcolour": "#80000000", "outline": 1,
                "pad": 0, "halign": "left", "valign": "middle", "opacity": 1,
            }.items():
                _property(label_filter, key, value)
    music = _producer(root, "music_source", timeline["music"], total, caption="Музыка")
    _property(music, "set.test_image", 1)
    background = ET.SubElement(root, "playlist", {"id": "background"})
    _entry(background, "black", total)
    narrator_track = _playlist(root, "narrator", "A1 Диктор", audio=True)
    music_track = _playlist(root, "music", "A2 Музыка", audio=True)
    visual_track = _playlist(root, "visuals", "V1 Изображения")
    titles_track = _playlist(root, "titles", "V2 Титры") if timeline.get('overlays_enabled',True) else None
    for index, length in enumerate(lengths):
        _entry(narrator_track, f"voice_{index}", length)
        if timeline['segments'][index].get('visual_shots'):
            for shot_index,shot in enumerate(timeline['segments'][index]['visual_shots']):
                _entry(visual_track,f'visual_{index}_{shot_index}',shot['frames'])
        else:
            _entry(visual_track, f"visual_{index}", length)
        if titles_track is not None:_entry(titles_track, f"title_{index}", length)
    _entry(music_track, "music_source", total)
    volume = ET.SubElement(music_track, "filter", {"out": str(total - 1)})
    for key, value in {"mlt_service": "volume", "shotcut:filter": "volume", "shotcut:trackVolume": 1, "level": "-18dB"}.items():
        _property(volume, key, value)
    tractor = ET.SubElement(root, "tractor", {"id": "tractor", "shotcut": "1", "in": "0", "out": str(total - 1)})
    _property(tractor, "shotcut", 1)
    _property(tractor, "shotcut:name", "Timeline")
    _property(tractor, "shotcut:projectAudioChannels", 2)
    _property(tractor, "shotcut:projectNote", f"{timeline['project_id']} revision {timeline['revision']} · local MLT render")
    _property(tractor, "shotcut:projectFrameRate", fps)
    for identifier, hidden in (("background", None), ("narrator", "video"), ("music", "video"), ("visuals", "audio"), ("titles", "audio")):
        if identifier=='titles' and titles_track is None:continue
        attributes = {"producer": identifier}
        if hidden:
            attributes["hide"] = hidden
        ET.SubElement(tractor, "track", attributes)
    for track in (1, 2):
        transition = ET.SubElement(tractor, "transition", {"in": "0", "out": str(total - 1)})
        for key, value in {"a_track": 0, "b_track": track, "mlt_service": "mix", "always_active": 1, "sum": 1}.items():
            _property(transition, key, value)
    for track in ((3,4) if titles_track is not None else (3,)):
        transition = ET.SubElement(tractor, "transition", {"in": "0", "out": str(total - 1)})
        for key, value in {"a_track": 0, "b_track": track, "mlt_service": "frei0r.cairoblend", "disable": 0}.items():
            _property(transition, key, value)
    ET.indent(root, space="  ")
    return ET.tostring(root, encoding="utf-8", xml_declaration=True)


def build_project(timeline: dict, output_dir: str | Path, *, base_dir: str | Path | None = None) -> dict:
    """Copy required media and emit portable timeline.json and project.mlt.

    Relative input paths are resolved against base_dir (current directory by default).
    Existing manual projects are not touched. Use a new output directory per build.
    """
    _validate(timeline)
    destination = Path(output_dir).resolve()
    if any(part.casefold() in {"manual", "originals"} for part in destination.parts):
        raise ValueError("Automatic builds cannot modify manual or originals directories")
    if (destination / "project.mlt").exists() or (destination / "timeline.json").exists():
        raise FileExistsError("A build already exists here; use a new directory to preserve prior versions")
    destination.mkdir(parents=True, exist_ok=True)
    source_root = Path(base_dir or Path.cwd()).resolve()
    portable = json.loads(json.dumps(timeline))
    portable["music"] = _stage_media(timeline["music"], source_root, destination)
    for original, segment in zip(timeline["segments"], portable["segments"]):
        segment["audio"] = _stage_media(original["audio"], source_root, destination)
        segment["visual"] = _stage_media(original["visual"], source_root, destination)
        for original_shot,shot in zip(original.get('visual_shots',[]),segment.get('visual_shots',[])):
            shot['visual']=_stage_media(original_shot['visual'],source_root,destination)
    _atomic_write(destination / "timeline.json", (json.dumps(portable, ensure_ascii=False, indent=2) + "\n").encode("utf-8"))
    _atomic_write(destination / "project.mlt", _xml(portable))
    frames = sum(math.ceil(segment["duration"] * portable["fps"] - 1e-7) for segment in portable["segments"])
    return {"project_mlt": str(destination / "project.mlt"), "timeline_json": str(destination / "timeline.json"),
            "frames": frames, "duration": frames / portable["fps"], "engine": "Shotcut/MLT",
            "tracks": ["narrator", "music", "visuals"]+(["titles"] if portable.get('overlays_enabled',True) else [])}


def build_timeline(segments: list[dict], music: str | Path, output_dir: str | Path, *, project_id: str = "pilot", revision: int = 1,
                   fps: int = 30, width: int = 1920, height: int = 1080, base_dir: str | Path | None = None,
                   heading: str = "", footer: str = "", title_style: str = "legacy", overlays_enabled: bool = True) -> dict:
    return build_project({"schema_version": "1.0", "project_id": project_id, "revision": revision,
                          "fps": fps, "width": width, "height": height, "music": str(music), "segments": segments,
                          "heading": heading, "footer": footer,"title_style":title_style,"overlays_enabled":overlays_enabled},
                         output_dir, base_dir=base_dir)
