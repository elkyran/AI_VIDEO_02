import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.development import Journal


class DevelopmentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Развитие студии ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / "plan.md").write_text("plan v1", encoding="utf-8")
        self.journal = Journal(self.root)
        self.journal.initialize("plan.md", "reports/run")

    def update(self, status, key="operation", **kwargs):
        return self.journal.update("T00", status, expected_hash=self.journal.read()[1], operation_key=key, **kwargs)

    def evidence(self):
        (self.root / "result.txt").write_text("result", encoding="utf-8")
        (self.root / "checks.json").write_text(json.dumps({"task_id": "T00", "status": "passed", "revision": "snapshot:1"}), encoding="utf-8")
        return {"result_refs": ["result.txt"], "test_refs": ["checks.json"], "revision": "snapshot:1"}

    def test_resume_after_process_exit_keeps_in_progress_and_releases_lock(self):
        self.update("in_progress")
        script = "from pathlib import Path; from app.development import locked; import os,sys\nwith locked(Path(sys.argv[1])): os._exit(19)"
        result = subprocess.run([sys.executable, "-c", script, str(self.journal.lock)], capture_output=True)
        self.assertEqual(result.returncode, 19)
        restarted = Journal(self.root)
        state = restarted.initialize("plan.md", "reports/other")
        self.assertEqual(state["tasks"][0]["status"], "in_progress")
        self.assertEqual(state["run_ref"], "reports/run")
        self.update("done", key="finish", **self.evidence())

    def test_atomic_write_failure_preserves_journal_and_retry_succeeds(self):
        before = self.journal.path.read_bytes()
        with patch("app.development.os.replace", side_effect=OSError("simulated interruption")):
            with self.assertRaises(OSError):
                self.update("in_progress")
        self.assertEqual(before, self.journal.path.read_bytes())
        self.update("in_progress")

    def test_repeat_is_idempotent_but_conflicting_writer_is_rejected(self):
        original_hash = self.journal.read()[1]
        self.update("in_progress")
        before = self.journal.path.read_bytes()
        self.journal.update("T00", "in_progress", expected_hash=original_hash, operation_key="operation")
        self.assertEqual(before, self.journal.path.read_bytes())
        with self.assertRaisesRegex(ValueError, "Revision conflict"):
            self.journal.update("T00", "needs_review", expected_hash=original_hash, operation_key="other")
        with self.assertRaisesRegex(ValueError, "Idempotency conflict"):
            self.update("needs_review")

    def test_done_requires_matching_passed_evidence_and_dependencies(self):
        with self.assertRaises(ValueError):
            self.update("done")
        args = self.evidence()
        with self.assertRaisesRegex(ValueError, "passed evidence"):
            self.update("done", **{**args, "revision": "wrong"})
        with self.assertRaisesRegex(ValueError, "Dependencies"):
            self.journal.update("T01", "in_progress", expected_hash=self.journal.read()[1], operation_key="early")
        self.update("done", **args)
        state, _ = self.journal.read()
        self.assertEqual(state["tasks"][0]["status"], "done")
        self.assertIn("| T00 | A | done |", (self.root / "development/STATUS.md").read_text(encoding="utf-8"))
        self.assertIn("Закрыты: T00.", (self.root / "development/HANDOFF.md").read_text(encoding="utf-8"))

    def test_changed_plan_and_outside_phase_cannot_overwrite_progress(self):
        self.update("in_progress")
        (self.root / "plan.md").write_text("unrelated plan", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "different plan"):
            self.journal.initialize("plan.md", "reports/other")
        with self.assertRaisesRegex(ValueError, "outside"):
            self.journal.update("T04", "in_progress", expected_hash=self.journal.read()[1], operation_key="next-phase")
        self.assertEqual(self.journal.read()[0]["tasks"][0]["status"], "in_progress")

    def test_sync_repairs_views_after_interrupted_projection(self):
        self.update("in_progress")
        (self.root / "development/STATUS.md").write_text("stale projection", encoding="utf-8")
        state, version = self.journal.read()
        self.journal.sync()
        self.assertEqual(self.journal.read()[1], version)
        self.assertIn(f"generation={state['generation']}", (self.root / "development/STATUS.md").read_text(encoding="utf-8"))

    def test_owner_map_amendment_preserves_b_and_dependency_checks(self):
        (self.root / "decision.md").write_text("Owner authorized T07–T10", encoding="utf-8")
        for i in range(4):
            task = f"T{i:02d}"
            (self.root / "checks.json").write_text(json.dumps({"task_id": task, "status": "passed", "revision": "test:1"}), encoding="utf-8")
            self.journal.update(task, "done", expected_hash=self.journal.read()[1], operation_key=f"finish-{task}",
                                result_refs=["decision.md"], test_refs=["checks.json"], revision="test:1")
        before = self.journal.read()[1]
        kwargs = dict(expected_hash=before, operation_key="owner-map", decision_ref="decision.md", run_ref="reports/map")
        self.journal.authorize_map_batch(**kwargs)
        version = self.journal.read()[1]
        self.journal.authorize_map_batch(**kwargs)
        self.assertEqual(self.journal.read()[1], version)
        for task in ("T04", "T05", "T06", "T11", "T12"):
            with self.assertRaisesRegex(ValueError, "outside"):
                self.journal.update(task, "in_progress", expected_hash=version, operation_key="no-" + task)
        with self.assertRaisesRegex(ValueError, "Dependencies"):
            self.journal.update("T08", "in_progress", expected_hash=version, operation_key="too-early")
        self.journal.update("T07", "in_progress", expected_hash=version, operation_key="map-start")
        self.assertEqual(self.journal.read()[0]["tasks"][6]["status"], "pending")
        self.assertIn("Продолжить T07", (self.root / "development/HANDOFF.md").read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
