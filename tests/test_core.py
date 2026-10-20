"""Failure-focused tests for real state, file containment, and exchanged answers."""

from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest

from pydantic import ValidationError

from app.contracts import CONTRACTS, ProjectBrief, Task, TaskSubmission, validate_contract
from app.registry import Registry, StudioError, workspace_path, write_generated_json


HASH = "a" * 64
BRIEF = {"project_id": "pilot", "title": "Пилот", "work_title": "Тест", "character": "Рудеус"}
SHOT = {"id": "shot01", "project_id": "pilot", "block_id": "block01", "asset_id": "visual01",
        "in_ms": 0, "out_ms": 5000, "purpose": "explanation", "reason": "Авторская схема"}
SAMPLES = {
    "ProjectBrief": BRIEF,
    "SourceRef": {"source_id": "source01", "work_id": "work01", "text_revision": 1,
                  "chapter": "1", "start_char": 0, "end_char": 10, "source_sha256": HASH},
    "Task": {"id": "choose01", "project_id": "pilot", "instruction": "Выбрать визуал",
             "result_contract": "ShotPlan"},
    "ScriptBlock": {"id": "block01", "project_id": "pilot", "revision": 1,
                    "purpose": "Вопрос", "text": "Что меняет второй шанс?", "interpretation": True},
    "AudioSegment": {"id": "audio01", "project_id": "pilot", "block_id": "block01",
                     "text_hash": HASH, "voice_id": "denis", "language": "ru", "path": "audio/01.wav",
                     "duration_ms": 5000, "sample_rate_hz": 22050, "channels": 1},
    "ShotPlan": SHOT,
    "Timeline": {"project_id": "pilot", "revision": 1, "music": "media/music.wav",
                 "segments": [{"id": "s01", "text": "Текст", "title": "Вопрос",
                               "visual": "media/01.mp4", "audio": "audio/01.wav", "duration": 5.0}]},
    "QAReport": {"project_id": "pilot", "build_id": "build01", "build_sha256": HASH,
                 "checks": [{"name": "listening", "status": "not_checked"}]},
    "TaskSubmission": {"project_id": "pilot", "base_revision": 1, "task_id": "choose01",
                       "idempotency_key": "reply01", "result": SHOT},
}
SAMPLES["Claim"] = {"id": "claim01", "project_id": "pilot", "work_id": "work01", "kind": "event",
                    "statement": "Тестовое событие", "source_refs": [SAMPLES["SourceRef"]]}
SAMPLES["ChapterAnalysis"] = {"id": "analysis01", "project_id": "pilot", "source_id": "source01",
                              "chapter_id": "chapter01", "summary": "Разбор тестового раздела", "claims": [SAMPLES["Claim"]]}
SAMPLES["Dossier"] = {"id": "dossier01", "project_id": "pilot", "work_id": "work01", "character_name": "Герой",
                      "goals": "Цель", "first_meeting": "Встреча", "relationships": "Связь",
                      "decisions": "Выбор", "plot_role": "Роль", "claim_ids": ["claim01"]}


class ContractTests(unittest.TestCase):
    def test_each_contract_accepts_example_and_rejects_extra_and_version(self):
        self.assertEqual(set(SAMPLES), set(CONTRACTS))
        for name, data in SAMPLES.items():
            with self.subTest(contract=name):
                value = validate_contract(name, data)
                self.assertEqual(value.schema_version, "1.0")
                for addition in ({"invented": True}, {"schema_version": "2.0"}):
                    with self.assertRaises(ValidationError):
                        validate_contract(name, {**data, **addition})

    def test_invalid_id_negative_duration_and_coerced_integer_rejected(self):
        for name, changes in (("ShotPlan", {"asset_id": "../../oops"}),
                              ("ShotPlan", {"out_ms": -1}),
                              ("ShotPlan", {"out_ms": "5000"}),
                              ("AudioSegment", {"duration_ms": 0})):
            with self.subTest(name=name, changes=changes), self.assertRaises(ValidationError):
                validate_contract(name, {**SAMPLES[name], **changes})

    def test_timeline_rejects_nonfinite_negative_duplicate_and_traversal(self):
        for duration in (-1.0, 0.0, float("nan"), float("inf")):
            data = deepcopy(SAMPLES["Timeline"])
            data["segments"][0]["duration"] = duration
            with self.assertRaises(ValidationError):
                validate_contract("Timeline", data)
        data = deepcopy(SAMPLES["Timeline"])
        data["segments"] *= 2
        with self.assertRaises(ValidationError):
            validate_contract("Timeline", data)
        data = deepcopy(SAMPLES["Timeline"])
        data["segments"][0]["audio"] = "../secret.wav"
        with self.assertRaises(ValidationError):
            validate_contract("Timeline", data)

    def test_qa_cannot_claim_checked_without_evidence_or_human_acceptance(self):
        for changes in ({"status": "accepted"},
                        {"checks": [{"name": "listening", "status": "passed"}]},
                        {"status": "ready_for_review", "checks": [{"name": "decode", "status": "failed",
                          "severity": "error", "evidence": "decoder exit 1"}]}):
            with self.assertRaises(ValidationError):
                validate_contract("QAReport", {**SAMPLES["QAReport"], **changes})

    def test_full_episode_cannot_use_short_pilot_target(self):
        with self.assertRaises(ValidationError):
            validate_contract("ProjectBrief", {**BRIEF, "milestone": "full_episode"})

    def test_editorial_review_is_distinct_from_full_novel_acceptance(self):
        brief = validate_contract("ProjectBrief", {**BRIEF, "milestone": "editorial_review",
                                                   "target_duration_seconds": 600,
                                                   "source_status": "partial"})
        self.assertEqual(brief.milestone, "editorial_review")
        self.assertEqual(brief.source_status, "partial")
        with self.assertRaises(ValidationError):
            validate_contract("ProjectBrief", {**BRIEF, "milestone": "pilot",
                                                "target_duration_seconds": 600})


class RegistryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix="studio test ")
        self.root = Path(self.temporary.name)
        (self.root / "media").mkdir()
        (self.root / "media/visual.dat").write_bytes(b"original asset")
        self.registry = Registry(self.root)
        self.registry.initialize(ProjectBrief.model_validate(BRIEF))

    def tearDown(self):
        self.registry.close()
        self.temporary.cleanup()

    def assert_error(self, code, callable_, *args, **kwargs):
        with self.assertRaises(StudioError) as context:
            callable_(*args, **kwargs)
        self.assertEqual(context.exception.code, code)

    def register(self, **overrides):
        arguments = dict(project_id="pilot", asset_id="visual01", path="media/visual.dat", kind="video",
                         rights="owned", duration_ms=6000, base_revision=1, idempotency_key="asset01")
        arguments.update(overrides)
        return self.registry.register_asset(**arguments)

    def prepare_task(self, **asset_overrides):
        self.register(**asset_overrides)
        task = Task(id="choose01", project_id="pilot", instruction="Выбрать кадр", result_contract="ShotPlan",
                    candidate_asset_ids=["visual01"])
        self.registry.create_task(task, base_revision=2, idempotency_key="task01")

    def submission(self, **result_changes):
        return TaskSubmission(project_id="pilot", base_revision=3, task_id="choose01",
                              idempotency_key="reply01", result={**SHOT, **result_changes})

    def test_persistent_status_and_content_hash(self):
        result = self.register()
        self.assertEqual(result["revision"], 2)
        self.assertEqual(len(result["sha256"]), 64)
        with Registry(self.root) as reopened:
            self.assertEqual(reopened.status("pilot")["assets"][0]["id"], "visual01")
            self.assertEqual(reopened.status("pilot")["revision"], 2)

    def test_idempotency_replays_original_response_before_revision_check(self):
        first = self.register()
        self.assertEqual(first, self.register())
        self.assertEqual(len(self.registry.status("pilot")["assets"]), 1)
        self.assert_error("IDEMPOTENCY_CONFLICT", self.register, rights="unknown")

    def test_duplicate_content_does_not_create_asset_or_revision(self):
        self.register()
        result = self.register(asset_id="duplicate", base_revision=2, idempotency_key="duplicate01")
        self.assertEqual(result["asset_id"], "visual01")
        self.assertFalse(result["created"])
        self.assertEqual(result["revision"], 2)

    def test_stale_revision_is_rejected_without_modification(self):
        self.register()
        self.assert_error("REVISION_CONFLICT", self.register,
                          asset_id="different", idempotency_key="stale01")
        self.assertEqual(self.registry.status("pilot")["revision"], 2)

    def test_unknown_asset_cannot_be_candidate(self):
        task = Task(id="choose01", project_id="pilot", instruction="test", result_contract="ShotPlan",
                    candidate_asset_ids=["invented"])
        self.assert_error("ASSET_NOT_FOUND", self.registry.create_task, task,
                          base_revision=1, idempotency_key="task01")
        self.assertEqual(self.registry.status("pilot")["tasks"], [])
        self.assertEqual(self.registry.status("pilot")["revision"], 1)

    def test_import_checks_unknown_asset_rights_ranges_and_changed_files(self):
        self.prepare_task()
        self.assert_error("ASSET_NOT_FOUND", self.registry.submit, self.submission(asset_id="invented"))
        self.assert_error("ASSET_RANGE_INVALID", self.registry.submit, self.submission(out_ms=7000))
        self.assertEqual(self.registry.status("pilot")["revision"], 3)
        (self.root / "media/visual.dat").write_bytes(b"tampered")
        self.assert_error("ASSET_CHANGED", self.registry.submit, self.submission())

    def test_unknown_rights_cannot_be_selected(self):
        self.prepare_task(rights="unknown")
        self.assert_error("RIGHTS_UNCONFIRMED", self.registry.submit, self.submission())

    def test_export_import_roundtrip_and_replay(self):
        self.prepare_task()
        package = self.registry.export_task("pilot", "choose01")
        response = package["response_template"]
        response["result"] = SHOT
        submission = TaskSubmission.model_validate(json.loads(json.dumps(response)))
        result = self.registry.submit(submission)
        self.assertEqual(result["revision"], 4)
        self.assertEqual(result, self.registry.submit(submission))
        self.assertEqual(self.registry.status("pilot")["object_versions"], 1)
        self.assertEqual(self.registry.read_task("pilot", "choose01").state, "done")
        self.assertEqual(self.registry.status("pilot")["tasks"][0]["result_id"], "shot01")
        with Registry(self.root) as reopened:
            self.assertEqual(reopened.read_object("pilot", "choose01")["accepted_result"]["object_id"], "shot01")

    def test_stale_file_response_is_rejected(self):
        self.prepare_task()
        response = self.submission()
        another = Task(id="another", project_id="pilot", instruction="test", result_contract="ScriptBlock")
        self.registry.create_task(another, base_revision=3, idempotency_key="task02")
        self.assert_error("REVISION_CONFLICT", self.registry.submit, response)
        self.assertEqual(self.registry.read_task("pilot", "choose01").state, "pending")

    def test_dependency_blocks_submission_and_then_allows_it(self):
        first = Task(id="first", project_id="pilot", instruction="Write", result_contract="ScriptBlock")
        second = Task(id="second", project_id="pilot", instruction="Write", result_contract="ScriptBlock", dependencies=["first"])
        self.registry.create_task(first, base_revision=1, idempotency_key="create-first")
        self.registry.create_task(second, base_revision=2, idempotency_key="create-second")
        response = TaskSubmission(project_id="pilot", base_revision=3, task_id="second",
                                  idempotency_key="second-response", result=SAMPLES["ScriptBlock"])
        self.assert_error("DEPENDENCY_PENDING", self.registry.submit, response)
        first_response = TaskSubmission(project_id="pilot", base_revision=3, task_id="first",
                                  idempotency_key="first-response", result=SAMPLES["ScriptBlock"])
        self.registry.submit(first_response)
        response.base_revision = 4
        response.result = {**SAMPLES["ScriptBlock"], "id": "block02"}
        self.assertEqual(self.registry.submit(response)["state"], "done")

    def test_path_traversal_absolute_drive_ads_and_manual_protection(self):
        for value in ("../outside", "/absolute", "C:/file", "C:file", "file:stream", "a/../../b", "a\\b", "a//b",
                      "manual./file", "originals /file", "CON.txt", "a/.. /b"):
            with self.subTest(value=value):
                self.assert_error("PATH_UNSAFE", workspace_path, self.root, value)
        for value in ("manual/project.mlt", "projects/pilot/Manual/x.json", "library/originals/source.txt"):
            self.assert_error("PROTECTED_PATH", write_generated_json, self.root, value, {})

    def test_generated_export_never_overwrites_existing_manual_changes(self):
        path = write_generated_json(self.root, "builds/task.json", {"original": True})
        path.write_text("my manual changes", encoding="utf-8")
        self.assert_error("OUTPUT_EXISTS", write_generated_json, self.root, "builds/task.json", {"replacement": True})
        self.assertEqual(path.read_text(encoding="utf-8"), "my manual changes")


if __name__ == "__main__":
    unittest.main()
