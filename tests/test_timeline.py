"""Native MLT integration and workspace-preservation checks."""

from __future__ import annotations

import hashlib
import json
import math
from array import array
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from app.timeline import build_timeline
from scripts.render_project import find_qmelt, render_project


ROOT = Path(__file__).resolve().parents[1]


def available_renderer():
    try:
        return find_qmelt().is_file() and shutil.which("ffmpeg") and shutil.which("ffprobe")
    except FileNotFoundError:
        return False


class TimelineTests(unittest.TestCase):
    def test_traversal_and_preserving_existing_builds(self):
        with tempfile.TemporaryDirectory(prefix="studio_timeline_") as folder:
            root = Path(folder)
            source = root / "source"
            source.mkdir()
            (source / "image.png").write_bytes(b"fixture")
            (source / "audio.wav").write_bytes(b"fixture sound")
            outside = root / "outside.wav"
            outside.write_bytes(b"outside")
            segment = dict(id="one", text="test", title="test", visual="image.png", audio="audio.wav", duration=1.0)
            with self.assertRaises(ValueError):
                build_timeline([segment], "../outside.wav", root / "bad", base_dir=source)
            with self.assertRaises(ValueError):
                build_timeline([segment], "audio.wav", root / "manual" / "bad", base_dir=source)
            result = build_timeline([segment], "audio.wav", root / "build", base_dir=source)
            original = Path(result["project_mlt"]).read_bytes()
            with self.assertRaises(FileExistsError):
                build_timeline([segment], "audio.wav", root / "build", base_dir=source)
            self.assertEqual(Path(result["project_mlt"]).read_bytes(), original)

    @unittest.skipUnless(available_renderer(), "Official Shotcut portable and FFmpeg are needed")
    def test_actual_native_render_portable_reexport_and_tracks(self):
        with tempfile.TemporaryDirectory(prefix="studio_native_") as folder:
            root = Path(folder)
            source = root / "fixtures"
            source.mkdir()
            subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", "color=c=0x173042:s=1920x1080:d=2",
                            "-frames:v", "1", "-update", "1", str(source / "image.png")], check=True)
            for name, frequency in (("voice", 440), ("music", 220)):
                subprocess.run(["ffmpeg", "-v", "error", "-f", "lavfi", "-i", f"sine=frequency={frequency}:duration=2",
                                "-c:a", "pcm_s16le", str(source / f"{name}.wav")], check=True)
            segment = dict(id="one", text="Demo", title="Проверка монтажа", visual="image.png", audio="voice.wav", duration=2.0)
            result = build_timeline([segment], "music.wav", root / "build", base_dir=source, heading="ТЕСТ", footer="Исходные материалы теста")
            tree = ET.parse(result["project_mlt"])
            self.assertEqual(tree.find(".//tractor/property[@name='shotcut']").text, "1")
            self.assertEqual(len(tree.findall(".//tractor/track")), 5)
            self.assertEqual(len(tree.findall(".//filter/property[@name='mlt_service'][.='dynamictext']")), 3)
            export = render_project(result["project_mlt"], preset="ultrafast", timeout=120)
            self.assertEqual(export["project_sha256"], hashlib.sha256(Path(result["project_mlt"]).read_bytes()).hexdigest())
            probe = json.loads(subprocess.check_output(["ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", export["output"]]))
            video = next(stream for stream in probe["streams"] if stream["codec_type"] == "video")
            self.assertEqual((video["width"], video["height"], video["r_frame_rate"]), (1920, 1080, "30/1"))
            self.assertAlmostEqual(float(probe["format"]["duration"]), 2.0, delta=.04)
            self.assertTrue(any(stream["codec_type"] == "audio" for stream in probe["streams"]))
            raw_audio = subprocess.check_output(["ffmpeg", "-v", "error", "-i", export["output"],
                                                "-map", "0:a", "-ac", "1", "-ar", "48000", "-f", "s16le", "-"])
            samples = array("h", raw_audio)[:48000]
            def magnitude(frequency):
                cosine = sum(value * math.cos(2 * math.pi * frequency * index / 48000) for index, value in enumerate(samples))
                sine = sum(value * math.sin(2 * math.pi * frequency * index / 48000) for index, value in enumerate(samples))
                return 2 * math.hypot(cosine, sine) / len(samples)
            self.assertGreater(magnitude(440), 1000, "Narrator track must contain audible signal")
            self.assertGreater(magnitude(220), 100, "Separate music track must survive mixing")
            moved = root / "Перенос с пробелами"
            shutil.copytree(root / "build", moved)
            reexport = render_project(moved / "project.mlt", moved / "reexport.mp4", preset="ultrafast", timeout=120)
            def frames(path):
                return subprocess.check_output(["ffmpeg", "-v", "error", "-i", str(path), "-map", "0:v", "-f", "framemd5", "-"])
            self.assertEqual(frames(export["output"]), frames(reexport["output"]))
            with self.assertRaises(FileExistsError):
                render_project(result["project_mlt"], preset="ultrafast")


if __name__ == "__main__":
    unittest.main()
