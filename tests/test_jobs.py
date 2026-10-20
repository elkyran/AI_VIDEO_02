"""Persistent jobs, revisions, cancellation and real native-render boundaries."""

from __future__ import annotations

from array import array
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import struct
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch
import uuid
import wave
import zlib

from app.contracts import ProjectBrief
from app.jobs import JobManager, process_alive
from app.operations import patch_script
from app.registry import Registry, StudioError, canonical_json, content_hash
from app.timeline import build_timeline
from scripts.render_project import find_qmelt


ROOT = Path(__file__).resolve().parents[1]


def native_available():
    try:
        return find_qmelt().is_file() and shutil.which("ffmpeg") and shutil.which("ffprobe")
    except FileNotFoundError:
        return False


def png_bytes(width=64, height=36):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xffffffff)
    row = b"\0" + bytes((23, 48, 66)) * width
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)) + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b"")


class JobTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="studio_jobs_")
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        assets = self.root / "assets"
        assets.mkdir()
        (assets / "image.png").write_bytes(png_bytes())
        samples = array("h", (int(8000 * math.sin(2 * math.pi * 440 * index / 48000)) for index in range(96000)))
        with wave.open(str(assets / "voice.wav"), "wb") as sound:
            sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(48000); sound.writeframes(samples.tobytes())
        self.script = {"schema_version": "1.0", "project_id": "demo", "title": "Original test",
                       "scope": "Original integration test", "segments": [
                           {"id": "block_one", "text": "Первый собственный фрагмент.", "title": "Первый", "visual_asset_id": "image_demo"},
                           {"id": "block_two", "text": "Второй собственный фрагмент.", "title": "Второй", "visual_asset_id": "image_demo"}]}
        (assets / "script.json").write_text(json.dumps(self.script, ensure_ascii=False), encoding="utf-8")
        with Registry(self.root) as registry:
            registry.initialize(ProjectBrief(project_id="demo", title="Queue integration", work_title="Original test", character="Narrator"))
            registry.register_asset("demo", "image_demo", "assets/image.png", kind="image", rights="owned", base_revision=1, idempotency_key="image")
            registry.register_asset("demo", "script_demo", "assets/script.json", kind="text", rights="owned", base_revision=2, idempotency_key="script")
        self.manager = JobManager(self.root)

    def revision(self):
        with Registry(self.root) as registry:
            return registry.status("demo")["revision"]

    def insert_job(self, operation, parameters, result=None, state="done"):
        identifier = "job_" + uuid.uuid4().hex[:20]
        with Registry(self.root) as registry:
            registry.db.execute("INSERT INTO jobs(id,project_id,operation,parameters,state,result,created,input_revision) VALUES(?,?,?,?,?,?,?,?)",
                                (identifier, "demo", operation, canonical_json(parameters), state,
                                 canonical_json(result) if result is not None else None, time.time(), self.revision()))
        return identifier

    def build_fixture(self):
        segments = [{"id": "block_one", "text": "Original fixture", "title": "Проверка",
                     "visual": "assets/image.png", "audio": "assets/voice.wav", "duration": 2.0}]
        build = build_timeline(segments, "assets/voice.wav", self.root / "fixture_build", base_dir=self.root)
        project_directory = Path(build["project_mlt"]).parent
        (project_directory / "script.json").write_text(json.dumps(self.script, ensure_ascii=False), encoding="utf-8")
        (project_directory / "script.txt").write_text("Original tone-backed integration fixture", encoding="utf-8")
        manifest_path = self.root / "audio_manifest.json"
        manifest_path.write_text(json.dumps({"schema_version": "1.0", "project_id": "demo", "segments": [
            {"block_id": "block_one", "text": segments[0]['text'], "path": "assets/voice.wav", "wav_hash": content_hash(self.root / "assets" / "voice.wav"),
             "duration_seconds": 2.0, "voice_id": "original-test-tone"}]}), encoding="utf-8")
        audio_job = self.insert_job("synthesize", {"script_asset_id": "script_demo"},
                                   {"manifest": "audio_manifest.json", "manifest_sha256": content_hash(manifest_path)})
        result = {"project_mlt": Path(build["project_mlt"]).relative_to(self.root).as_posix(),
                  "project_sha256": content_hash(Path(build["project_mlt"])),
                  "timeline_json": Path(build["timeline_json"]).relative_to(self.root).as_posix(),
                  "file_hashes": {path.relative_to(Path(build["project_mlt"]).parent).as_posix(): content_hash(path)
                                  for path in Path(build["project_mlt"]).parent.rglob("*") if path.is_file()},
                  "duration_seconds": 2.0, "audio_job_id": audio_job,
                  "assets": [{"id": "image_demo", "rights": "owned", "kind": "image", "sha256": content_hash(self.root / "assets" / "image.png")}]}
        return self.insert_job("build", {"script_asset_id": "script_demo", "audio_job_id": audio_job}, result), result

    def assert_error(self, code, operation):
        with self.assertRaises(StudioError) as caught:
            operation()
        self.assertEqual(caught.exception.code, code)

    def wait_terminal(self, job_id, timeout=60):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            value = self.manager.status(job_id)
            if value["state"] not in {"pending", "running"}:
                # DB commit precedes worker exit; wait for Windows log handles to close.
                worker = value.get("worker_pid")
                while worker and worker != os.getpid() and process_alive(worker) and time.monotonic() < deadline:
                    time.sleep(.05)
                return value
            time.sleep(.15)
        self.fail(f"Job did not finish: {self.manager.status(job_id)}")

    def test_idempotency_revision_and_operation_validation(self):
        arguments = ("demo", "synthesize", {"script_asset_id": "script_demo"}, 3, "voice_test")
        first = self.manager.enqueue(*arguments, start=False)
        self.assertEqual(first, self.manager.enqueue(*arguments, start=False))
        self.assertEqual(len(self.manager.list("demo")), 1)
        self.assertEqual(self.revision(), 4)
        self.assert_error("REVISION_CONFLICT", lambda: self.manager.enqueue("demo", "synthesize", {"script_asset_id": "script_demo"}, 3, "new_key", start=False))
        self.assert_error("OPERATION_NOT_ALLOWED", lambda: self.manager.enqueue("demo", "shell", {}, 4, "shell", start=False))
        self.assert_error("SCHEMA_INVALID", lambda: self.manager.enqueue("demo", "synthesize", {"script_asset_id": "script_demo", "output": "../outside"}, 4, "extra", start=False))
        self.assert_error("ASSET_NOT_FOUND", lambda: self.manager.enqueue("demo", "synthesize", {"script_asset_id": "../secret"}, 4, "path", start=False))
        self.assert_error("JOB_NOT_FOUND", lambda: self.manager.enqueue("demo", "render", {"build_job_id": "missing"}, 4, "dependency", start=False))

    def test_render_replay_after_patch_preserves_receipt_and_hash_conflict(self):
        build_job, _ = self.build_fixture()
        arguments = ("demo", "render", {"build_job_id": build_job}, 3, "render-original")
        receipt = self.manager.enqueue(*arguments, start=False)
        with Registry(self.root) as registry:
            patch_script(registry, "demo", "script_demo", "block_one", "Исправленный сценарий.",
                         base_revision=4, idempotency_key="patch-after-render")
        self.assertEqual(self.manager.status(build_job)["state"], "stale")
        self.assertEqual(self.manager.status(receipt["job_id"])["state"], "stale")
        count_before = len(self.manager.list("demo"))
        self.assertEqual(receipt, self.manager.enqueue(*arguments, start=False))
        self.assertEqual(count_before, len(self.manager.list("demo")))
        self.assert_error("IDEMPOTENCY_CONFLICT", lambda: self.manager.enqueue(
            "demo", "render", {"build_job_id": "different_dependency"}, 3, "render-original", start=False))
        self.assert_error("DEPENDENCY_PENDING", lambda: self.manager.enqueue(
            "demo", "render", {"build_job_id": build_job}, self.revision(), "new-render", start=False))

    def test_running_patch_is_persistently_stale_and_cannot_resume(self):
        receipt = self.manager.enqueue("demo", "synthesize", {"script_asset_id": "script_demo"},
                                       3, "running-voice", start=False)
        job_id = receipt["job_id"]

        def interrupted_operation(*_):
            self.assertEqual(self.manager.status(job_id)["state"], "running")
            with Registry(self.root) as registry:
                patch_script(registry, "demo", "script_demo", "block_one", "Новый текст во время работы.",
                             base_revision=self.revision(), idempotency_key="patch-running")
            during_cancel = self.manager.status(job_id)
            self.assertEqual(during_cancel["state"], "running")
            self.assertEqual(during_cancel["cancel_requested"], 1)
            self.assertEqual(during_cancel["invalidated"], 1)
            raise StudioError("CANCELLED", "Operation observed the cancellation request")

        with patch("app.operations.execute", side_effect=interrupted_operation):
            self.manager.work(job_id)
        final = self.manager.status(job_id)
        self.assertEqual(final["state"], "stale")
        self.assertEqual(final["error"]["code"], "STALE_INPUTS")
        self.assertIsNone(final["result"])
        reopened = JobManager(self.root)
        self.assert_error("JOB_STALE", lambda: reopened.resume(job_id))
        reopened.work(job_id)
        self.assertEqual(reopened.status(job_id)["attempts"], 1)
        # Simulate a process exit after invalidation but before worker finalization.
        with Registry(self.root) as registry:
            registry.db.execute("UPDATE jobs SET state='running',worker_pid=NULL WHERE id=?", (job_id,))
        self.assert_error("JOB_STALE", lambda: reopened.resume(job_id))
        self.assertEqual(reopened.status(job_id)["state"], "stale")
        self.assertEqual(reopened.status(job_id)["attempts"], 1)

    def test_existing_job_schema_migrates_without_losing_saved_jobs(self):
        receipt = self.manager.enqueue("demo", "synthesize", {"script_asset_id": "script_demo"},
                                       3, "legacy-job", start=False)
        with Registry(self.root) as registry:
            registry.db.execute("ALTER TABLE jobs DROP COLUMN invalidated")
        migrated = JobManager(self.root).status(receipt["job_id"])
        self.assertEqual(migrated["state"], "pending")
        self.assertEqual(migrated["invalidated"], 0)
        self.assertEqual(migrated["parameters"], {"script_asset_id": "script_demo"})

    def test_pending_cancel_retry_limit_and_active_process(self):
        job = self.manager.enqueue("demo", "synthesize", {"script_asset_id": "script_demo"}, 3, "voice", start=False)
        cancelled = self.manager.cancel(job["job_id"])
        self.assertEqual(cancelled["state"], "blocked")
        self.assertEqual(cancelled["cancel_requested"], 1)
        self.manager.work(job["job_id"])
        self.assertEqual(self.manager.status(job["job_id"])["attempts"], 0)
        with Registry(self.root) as registry:
            registry.db.execute("UPDATE jobs SET state='failed',attempts=3 WHERE id=?", (job["job_id"],))
        self.assert_error("RETRY_LIMIT", lambda: self.manager.resume(job["job_id"]))
        with Registry(self.root) as registry:
            registry.db.execute("UPDATE jobs SET state='running',attempts=1,worker_pid=? WHERE id=?", (os.getpid(), job["job_id"]))
        self.assertTrue(process_alive(os.getpid()))
        self.assertFalse(process_alive(None))
        self.assert_error("JOB_RUNNING", lambda: self.manager.resume(job["job_id"]))

    def test_missing_capability_is_saved_and_resume_rechecks(self):
        job = self.manager.enqueue("demo", "synthesize", {"script_asset_id": "script_demo"}, 3, "voice", start=False)
        self.manager.work(job["job_id"])
        failed = self.manager.status(job["job_id"])
        self.assertEqual(failed["state"], "blocked")
        self.assertEqual(failed["error"]["code"], "CAPABILITY_MISSING")
        resumed = self.manager.resume(job["job_id"])
        self.assertEqual(resumed["job_id"], job["job_id"])
        again = self.wait_terminal(job["job_id"])
        self.assertEqual(again["state"], "blocked")
        self.assertEqual(again["attempts"], 2)

    def test_changed_mlt_is_blocked_before_renderer(self):
        build_job, result = self.build_fixture()
        job = self.manager.enqueue("demo", "render", {"build_job_id": build_job}, 3, "render", start=False)
        project = self.root / result["project_mlt"]
        project.write_bytes(project.read_bytes() + b"\n<!-- later edit -->")
        self.manager.work(job["job_id"])
        failed = self.manager.status(job["job_id"])
        self.assertEqual(failed["state"], "failed")
        self.assertEqual(failed["error"]["code"], "ASSET_CHANGED")
        self.assertFalse((self.root / "jobs" / job["job_id"] / "final.mp4").exists())

    def test_patch_stales_dependency_chain_preserves_original_cache_and_manual(self):
        audio = self.insert_job("synthesize", {"script_asset_id": "script_demo"}, {"fixture": "real WAV in assets"})
        build = self.insert_job("build", {"script_asset_id": "script_demo", "audio_job_id": audio})
        render = self.insert_job("render", {"build_job_id": build}, state="pending")
        cache = self.root / "cache" / "audio"
        cache.mkdir(parents=True)
        shutil.copy2(self.root / "assets" / "voice.wav", cache / "unchanged.wav")
        manual = self.root / "manual" / "user_project.mlt"
        manual.parent.mkdir(); manual.write_text("User-owned manual project sentinel", encoding="utf-8")
        originals = {path: content_hash(path) for path in (self.root / "assets" / "script.json", cache / "unchanged.wav", manual)}
        with Registry(self.root) as registry:
            patched = patch_script(registry, "demo", "script_demo", "block_one", "Исправлена только первая реплика.", base_revision=3, idempotency_key="patch")
            same = patch_script(registry, "demo", "script_demo", "block_one", "Исправлена только первая реплика.", base_revision=3, idempotency_key="patch")
            self.assertEqual(patched, same)
            asset = registry.asset("demo", patched["script_asset_id"], verify=True)
        self.assertEqual(set(patched["stale_jobs"]), {audio, build, render})
        self.assertTrue(all(self.manager.status(job)["state"] == "stale" for job in (audio, build, render)))
        changed = json.loads((self.root / asset["path"]).read_text(encoding="utf-8"))
        self.assertEqual(changed["segments"][0]["text"], "Исправлена только первая реплика.")
        self.assertEqual(changed["segments"][1]["text"], self.script["segments"][1]["text"])
        self.assertEqual(originals, {path: content_hash(path) for path in originals})

    @unittest.skipUnless(native_available(), "Requires installed native Shotcut renderer")
    def test_detached_native_render_survives_calling_client_exit(self):
        build_job, _ = self.build_fixture()
        runtime = {"melt_path": str(find_qmelt()), "ffmpeg_path": shutil.which("ffmpeg"), "ffprobe_path": shutil.which("ffprobe")}
        (self.root / "runtime.json").write_text(json.dumps(runtime), encoding="utf-8")
        request = {"root": str(self.root), "build_job": build_job}
        # The short-lived client exits immediately after the actual worker is detached.
        client = "import json,sys; from app.jobs import JobManager; p=json.load(sys.stdin); print(json.dumps(JobManager(p['root']).enqueue('demo','render',{'build_job_id':p['build_job']},3,'render_detached',start=True)))"
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        completed = subprocess.run([sys.executable, "-c", client], input=json.dumps(request), cwd=ROOT,
                                   text=True, capture_output=True, check=True, timeout=15, creationflags=flags)
        job = json.loads(completed.stdout)
        final = self.wait_terminal(job["job_id"])
        self.assertEqual(final["state"], "done", final)
        video = self.root / final["result"]["video"]
        probe = json.loads(subprocess.check_output([runtime["ffprobe_path"], "-v", "error", "-show_streams", "-of", "json", str(video)]))
        self.assertTrue(any(stream["codec_type"] == "video" and stream["width"] == 1920 for stream in probe["streams"]))
        self.assertTrue(any(stream["codec_type"] == "audio" for stream in probe["streams"]))
        self.assertEqual(final["attempts"], 1)
        self.assertEqual(content_hash(video), final["result"]["sha256"])
        # QA performs a real full decode and sound measurement of that actual export.
        qa = self.manager.enqueue("demo", "qa", {"render_job_id": job["job_id"]}, self.revision(), "qa_native", start=False)
        self.manager.work(qa["job_id"])
        qa_result = self.manager.status(qa["job_id"])
        self.assertEqual(qa_result["state"], "done", qa_result)
        self.assertEqual(qa_result["result"]["status"], "ready_for_review")
        video.write_bytes(video.read_bytes() + b"post-QA modification")
        package = self.manager.enqueue("demo", "package", {"qa_job_id": qa["job_id"]}, self.revision(), "package_changed", start=False)
        self.manager.work(package["job_id"])
        rejected = self.manager.status(package["job_id"])
        self.assertEqual(rejected["state"], "failed")
        self.assertEqual(rejected["error"]["code"], "ASSET_CHANGED")


if __name__ == "__main__":
    unittest.main()
