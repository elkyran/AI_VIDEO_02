"""Compact exchange tests: temporary registries and simulated workers only."""
from __future__ import annotations

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.contracts import ProjectBrief
from app.exchange import CODE_FILES, Exchange, SHORT_LIMIT, STAGES, code_revision, main, parser
from app.jobs import JobManager
from app.registry import Registry, StudioError


class ExchangeTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="exchange_test_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        (self.root / "image.bin").write_bytes(b"hash-only visual fixture; never decoded")
        script = {"schema_version": "1.0", "project_id": "demo", "title": "Test", "scope": "Simulated only",
                  "segments": [{"id": "one", "text": "Тест.", "title": "Test", "visual_asset_id": "image"}]}
        (self.root / "script.json").write_text(json.dumps(script), encoding="utf-8")
        with Registry(self.root) as registry:
            registry.initialize(ProjectBrief(project_id="demo", title="Test", work_title="Test", character="Narrator"))
            registry.register_asset("demo", "image", "image.bin", kind="image", rights="owned", base_revision=1, idempotency_key="image")
            registry.register_asset("demo", "script", "script.json", kind="text", rights="owned", base_revision=2, idempotency_key="script")
        self.client = Exchange(self.root)
        self.manager = JobManager(self.root)

    def pending(self):
        return self.manager.enqueue("demo", "synthesize", {"script_asset_id": "script"}, 3, "pending", start=False)["job_id"]

    def chain(self, **kwargs):
        return self.client.chain("demo", "script", 3, "chain-one", task_id="T03", mode="development", **kwargs)

    def log(self, result):
        self.assertIsNotNone(result["log_ref"])
        self.assertFalse(Path(result["log_ref"]).is_absolute())
        return json.loads((self.root / result["log_ref"]).read_text(encoding="utf-8"))

    @staticmethod
    def execute(root, job_id, operation, parameters):
        if operation == "qa":
            relative = f"{job_id}_qa.json"
            (Path(root) / relative).write_text(json.dumps({"status": "ready_for_review", "checks": [], "limitations": []}), encoding="utf-8")
            return {"status": "ready_for_review", "report": relative}
        return {"status": "ready_for_review"} if operation == "package" else {"cache_hits": 1, "manifest": "simulated.json"}

    def workers(self):
        return patch.object(JobManager, "_spawn", autospec=True, side_effect=lambda manager, job_id: manager.work(job_id))

    def test_chain_completes_and_replay_never_executes_twice(self):
        with self.workers() as spawn, patch("app.operations.execute", side_effect=self.execute) as execute:
            result = self.chain(repair_reason="T03 simulated acceptance")
            again = self.chain()
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["quality_status"], "ready_for_review")
        self.assertEqual(result["wait_status"], "completed")
        self.assertEqual(result["job_id"], again["job_id"])
        self.assertEqual(execute.call_count, 5)
        self.assertEqual(spawn.call_count, 5)
        self.assertEqual(len(self.manager.list("demo")), 5)
        self.assertTrue(self.log(again)["metrics"]["repeat"])
        log = self.log(result)
        self.assertEqual([item["operation"] for item in log["stages"]], list(STAGES))
        self.assertEqual(log["metrics"]["repair_reason"], "T03 simulated acceptance")
        self.assertTrue(all(value is None for value in log["metrics"]["tokens"].values()))
        self.assertTrue(all(value is None for value in result["metrics"]["tokens"].values()))
        self.assertIsNone(result["metrics"]["model"])
        self.assertFalse(result["truncated"])
        self.assertEqual(log["stages"][0]["job"]["result"]["cache_hits"], 1)

    def test_changed_input_with_same_key_is_conflict(self):
        with self.workers(), patch("app.operations.execute", side_effect=self.execute):
            self.chain()
        changed = self.client.chain("demo", "different-script", 3, "chain-one", task_id="T03", mode="development")
        self.assertEqual(changed["errors"][0]["code"], "IDEMPOTENCY_CONFLICT")
        changed_revision = self.client.chain("demo", "script", 8, "chain-one", task_id="T03", mode="development")
        self.assertEqual(changed_revision["errors"][0]["code"], "IDEMPOTENCY_CONFLICT")

    def test_pending_timeout_and_replay_keep_one_job(self):
        with patch.object(JobManager, "_spawn") as spawn:
            result = self.chain(timeout=.02, poll_interval=.01)
            again = self.chain(timeout=.02, poll_interval=.01)
        self.assertEqual(result["wait_status"], "timeout")
        self.assertEqual(result["state"], "pending")
        self.assertEqual(result["job_id"], again["job_id"])
        self.assertEqual(spawn.call_count, 1)
        self.assertEqual(len(self.manager.list("demo")), 1)
        self.assertEqual(self.manager.status(result["job_id"])["cancel_requested"], 0)

    def test_wait_timeout_and_cancel_never_cancel_job(self):
        job_id = self.pending()
        timed = self.client.wait(job_id, timeout=0)
        cancelled = self.client.wait(job_id, timeout=1, cancel_wait=lambda: True)
        with patch("app.exchange.time.sleep", side_effect=KeyboardInterrupt):
            interrupted = self.client.wait(job_id, timeout=1)
        self.assertEqual(timed["wait_status"], "timeout")
        self.assertEqual(cancelled["wait_status"], "cancelled")
        self.assertEqual(interrupted["wait_status"], "cancelled")
        self.assertEqual(self.manager.status(job_id)["state"], "pending")
        self.assertEqual(self.manager.status(job_id)["cancel_requested"], 0)
        self.log(cancelled)

    def test_timeout_before_first_stage_does_not_claim_failure_or_start_work(self):
        with patch.object(JobManager, "_spawn") as spawn:
            result = self.chain(timeout=0)
            cancelled = self.chain(cancel_wait=lambda: True)
        self.assertEqual(result["state"], "not_started")
        self.assertEqual(result["wait_status"], "timeout")
        self.assertEqual(cancelled["state"], "not_started")
        self.assertEqual(cancelled["wait_status"], "cancelled")
        self.assertEqual(result["errors"], [])
        self.assertEqual(self.manager.list("demo"), [])
        spawn.assert_not_called()

    def test_chain_continues_after_timeout_using_original_receipt(self):
        with patch.object(JobManager, "_spawn"):
            timed = self.chain(timeout=.02, poll_interval=.01)
        with patch("app.operations.execute", side_effect=self.execute):
            self.manager.work(timed["job_id"])
        with self.workers() as spawn, patch("app.operations.execute", side_effect=self.execute) as execute:
            result = self.chain()
        self.assertEqual(result["state"], "done")
        self.assertEqual(execute.call_count, 4)
        self.assertEqual(spawn.call_count, 4)
        self.assertEqual(len(self.manager.list("demo")), 5)
        self.assertEqual(self.log(result)["stages"][0]["receipt"]["job_id"], timed["job_id"])

    def test_stale_receipt_is_reported_without_retry_or_new_stage(self):
        with patch.object(JobManager, "_spawn"):
            timed = self.chain(timeout=.02, poll_interval=.01)
        with Registry(self.root) as registry:
            registry.db.execute("UPDATE jobs SET state='stale',invalidated=1 WHERE id=?", (timed["job_id"],))
        with patch.object(JobManager, "_spawn") as spawn:
            result = self.chain()
        self.assertEqual(result["state"], "stale")
        self.assertEqual(result["job_id"], timed["job_id"])
        self.assertEqual(result["next_action"], "inspect_error_and_saved_log")
        self.assertEqual(len(self.manager.list("demo")), 1)
        spawn.assert_not_called()

    def test_worker_error_is_saved_and_replay_does_not_retry(self):
        message = "Actual simulated failure " + "x" * 1200
        with self.workers(), patch("app.operations.execute", side_effect=StudioError("FIXTURE_ERROR", message)) as execute:
            result = self.chain()
            again = self.chain()
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(result["state"], "failed")
        self.assertEqual(result["errors"][0]["code"], "FIXTURE_ERROR")
        self.assertTrue(result["truncated"])
        self.assertEqual(self.log(result)["errors"][0]["message"], message)
        self.assertEqual(again["job_id"], result["job_id"])

    def test_qa_blocked_stops_before_package(self):
        def blocked(root, job_id, operation, parameters):
            return {"status": "blocked", "report": "simulated_qa.json"} if operation == "qa" else self.execute(root, job_id, operation, parameters)
        with self.workers(), patch("app.operations.execute", side_effect=blocked) as execute:
            result = self.chain()
        self.assertEqual(execute.call_count, 4)
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["quality_status"], "blocked")
        self.assertEqual(result["errors"][0]["code"], "QA_BLOCKED")
        self.assertEqual(len(self.manager.list("demo")), 4)

    def test_production_checks_profile_before_every_new_enqueue(self):
        with self.workers(), patch("app.operations.execute", side_effect=self.execute), patch.object(Exchange, "_profile_check") as check:
            result = self.client.chain("demo", "script", 3, "production")
            self.client.chain("demo", "script", 3, "production")
        self.assertEqual(result["state"], "done")
        self.assertEqual(check.call_count, 5)

    def test_profile_failure_and_missing_development_task_start_nothing(self):
        with patch.object(Exchange, "_profile_check", side_effect=ValueError("runtime does not match fixed profile")), patch.object(JobManager, "_spawn") as spawn:
            result = self.client.chain("demo", "script", 3, "production")
            missing = self.client.chain("demo", "script", 3, "development", mode="development")
        self.assertIn("runtime", result["errors"][0]["message"])
        self.assertEqual(missing["errors"][0]["code"], "TASK_REQUIRED")
        self.assertEqual(self.manager.list("demo"), [])
        spawn.assert_not_called()
        self.assertIn("ValueError", self.log(result)["exception_trace"])

    def test_concurrent_revision_change_does_not_silently_accept_new_inputs(self):
        def changed(root, job_id, operation, parameters):
            if operation == "synthesize":
                with Registry(root) as registry:
                    registry.db.execute("UPDATE projects SET revision=revision+1 WHERE id='demo'")
            return self.execute(root, job_id, operation, parameters)
        with self.workers(), patch("app.operations.execute", side_effect=changed) as execute:
            result = self.chain()
        self.assertEqual(execute.call_count, 1)
        self.assertEqual(result["errors"][0]["code"], "REVISION_CONFLICT")
        self.assertEqual(len(self.manager.list("demo")), 1)

    def test_long_artifacts_and_warnings_are_bounded_but_log_is_complete(self):
        job_id = self.pending()
        payload = {"frames": [f"frames/{number}.png" for number in range(30)],
                   "warnings": ["long warning " + "z" * 900 for _ in range(15)]}
        with patch("app.operations.execute", return_value=payload):
            self.manager.work(job_id)
        result = self.client.job(job_id)
        self.assertTrue(result["truncated"])
        self.assertLessEqual(len(result["artifacts"]), 10)
        self.assertLessEqual(len(result["warnings"]), 10)
        self.assertGreaterEqual(len(result["warnings"]), 1)
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), SHORT_LIMIT)
        self.assertEqual(result["counts"], {"warnings": 15, "errors": 0, "artifacts": 30})
        self.assertEqual(len(self.log(result)["artifacts"]), 30)
        self.assertEqual(len(self.log(result)["warnings"]), 15)

    def test_invalid_limits_and_unknown_job_are_logged(self):
        for timeout in (float("inf"), float("nan"), -1):
            result = self.client.wait("missing", timeout=timeout)
            self.assertEqual(result["errors"][0]["code"], "SCHEMA_INVALID")
            self.log(result)
        result = self.client.job("missing")
        self.assertEqual(result["errors"][0]["code"], "JOB_NOT_FOUND")
        self.log(result)

    def test_log_failure_is_visible(self):
        job_id = self.pending()
        with patch("app.exchange.write_generated_json", side_effect=OSError("simulated disk failure")):
            result = self.client.job(job_id)
        self.assertIsNone(result["log_ref"])
        self.assertEqual(result["errors"][0]["code"], "LOG_WRITE_FAILED")
        self.assertEqual(result["next_action"], "fix_log_storage")

    def test_qa_report_warnings_survive_chain_and_standalone_package(self):
        def with_reports(root, job_id, operation, parameters):
            result = self.execute(root, job_id, operation, parameters)
            if operation == "synthesize":
                result["warnings"] = ["Earlier synthesis warning must survive"]
            if operation == "qa":
                report = {"status": "ready_for_review", "checks": [
                    {"name": "human_listening", "status": "not_checked", "severity": "warning", "evidence": "Awaiting human listening"},
                    {"name": "speech_transcript", "status": "failed", "severity": "warning", "evidence": "ASR mismatch"},
                    {"name": "video_profile", "status": "passed", "severity": "error", "evidence": "Profile passed"}],
                    "limitations": ["Local review only"]}
                (Path(root) / result["report"]).write_text(json.dumps(report), encoding="utf-8")
                speech_path = f"{job_id}_speech.json"
                speech = {"status": "automated_warning", "segments": [{"block_id": "one", "warnings": ["cached pronunciation warning"], "missing_critical_terms": ["name"]}]}
                (Path(root) / speech_path).write_text(json.dumps(speech), encoding="utf-8")
                result.update(asr_report=speech_path, speech_cache={"hit": True, "key": "saved-cache-key"})
            return result
        with self.workers(), patch("app.operations.execute", side_effect=with_reports):
            result = self.chain()
        self.assertEqual(result["state"], "done")
        self.assertEqual(result["quality_status"], "ready_for_review")
        self.assertEqual(result["errors"], [])
        full = self.log(result)
        self.assertIn("Earlier synthesis warning must survive", full["warnings"])
        self.assertTrue(any(isinstance(item, dict) and item.get("name") == "human_listening" for item in result["warnings"]))
        self.assertTrue(any(isinstance(item, dict) and item.get("code") == "SPEECH_CACHE_REUSED" for item in full["warnings"]))
        self.assertTrue(any(isinstance(item, dict) and item.get("message") == "cached pronunciation warning" for item in full["warnings"]))
        self.assertLessEqual(len(json.dumps(result, ensure_ascii=False)), SHORT_LIMIT)
        standalone = self.client.job(result["job_id"])
        self.assertTrue(any(item.get("code") == "SPEECH_CACHE_REUSED" for item in self.log(standalone)["warnings"]))
        self.assertEqual(standalone["quality_status"], "ready_for_review")

    def test_report_status_and_error_are_authoritative_before_package(self):
        def blocked_report(root, job_id, operation, parameters):
            result = self.execute(root, job_id, operation, parameters)
            if operation == "qa":
                report = {"status": "blocked", "checks": [{"name": "sound", "status": "failed", "severity": "error", "evidence": "Clipped sound"}]}
                (Path(root) / result["report"]).write_text(json.dumps(report), encoding="utf-8")
            return result
        with self.workers(), patch("app.operations.execute", side_effect=blocked_report) as execute:
            result = self.chain()
        self.assertEqual(execute.call_count, 4)
        self.assertEqual(result["quality_status"], "blocked")
        self.assertTrue(any(error.get("message") == "Clipped sound" for error in result["errors"]))
        self.assertTrue(any(error.get("code") == "QA_BLOCKED" for error in result["errors"]))

    def test_unreadable_or_outside_report_is_an_explicit_error(self):
        def missing(root, job_id, operation, parameters):
            if operation == "qa":
                return {"status": "ready_for_review", "report": "../outside.json"}
            return self.execute(root, job_id, operation, parameters)
        with self.workers(), patch("app.operations.execute", side_effect=missing) as execute:
            result = self.chain()
        self.assertEqual(execute.call_count, 4)
        self.assertEqual(result["quality_status"], "not_checked")
        error = next(item for item in result["errors"] if item["code"] == "REPORT_UNAVAILABLE")
        self.assertEqual(error["cause"], "PATH_UNSAFE")

    def test_code_revision_tracks_current_allowlisted_source_only(self):
        source = self.root / "code"
        (source / "app").mkdir(parents=True)
        (source / "app" / "jobs.py").write_text("version = 1", encoding="utf-8")
        first = code_revision(source)
        self.assertEqual(len(first), 64)
        self.assertEqual(first, code_revision(source))
        (source / "archive").mkdir()
        (source / "archive" / "jobs.py").write_text("unrelated", encoding="utf-8")
        self.assertEqual(first, code_revision(source))
        (source / "app" / "jobs.py").write_text("version = 2", encoding="utf-8")
        self.assertNotEqual(first, code_revision(source))
        result = self.client.job(self.pending())
        self.assertEqual(result["metrics"]["code_revision"], code_revision())
        self.assertEqual(self.log(result)["code_revision_files"], list(CODE_FILES))

    def test_cli_machine_output_and_production_default(self):
        job_id = self.pending()
        output = io.StringIO()
        with redirect_stdout(output):
            code = main(["--workspace", str(self.root), "wait", job_id, "--timeout", "0"])
        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        required = {"task_id", "job_id", "state", "quality_status", "artifacts", "warnings", "errors", "log_ref", "next_action", "truncated"}
        self.assertTrue(required <= result.keys())
        parsed = parser().parse_args(["chain", "demo", "script", "--revision", "3", "--key", "key"])
        self.assertEqual(parsed.mode, "production")


if __name__ == "__main__":
    unittest.main()
