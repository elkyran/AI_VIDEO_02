"""Behavioral queue tests: temporary workspaces, no models or media execution."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from app.ai_adapters import FakeAdapter, CodexAdapter
from app.editorial import demo_result
from app.multi_ai import Coordinator, ACTIVE_PROVIDERS as PROVIDERS
from app.registry import Registry, StudioError, canonical_json, content_hash


class LocalStub:
    """A local adapter contract double; never launches a process."""
    is_simulated = False
    is_local = True

    def __init__(self, recovery="unknown"):
        self.started = 0
        self.recovery = recovery

    def start(self, task, profile, attempt_dir):
        self.started += 1
        return task["attempt_id"]

    def poll(self, handle):
        return []

    def cancel(self, handle):
        return {"status": "unknown"}

    def recover(self, attempt_dir):
        return {"status": self.recovery, "result": {"text": "Recovered local contract fixture", "simulated": True}}


class LiveContractStub:
    """A process-free live-boundary double; no account or model is contacted."""
    is_simulated = False
    is_live = True

    def __init__(self, complete=False, failure=None):
        self.complete = complete
        self.failure = failure
        self.started = []
        self.runs = {}
        self.recovery = "unknown"
        self.raise_poll = False

    def start(self, task, profile, attempt_dir):
        self.started.append((task, profile))
        self.runs[task["attempt_id"]] = task
        return task["attempt_id"]

    def poll(self, handle):
        if self.raise_poll:
            raise OSError("Observation is unavailable")
        if self.failure:
            return [{"type": "failed", "failure": self.failure}]
        if not self.complete:
            return []
        task = self.runs[handle]
        def unsimulate(value):
            if isinstance(value, dict):
                return {k: False if k == "simulated" else unsimulate(v) for k, v in value.items()}
            return [unsimulate(v) for v in value] if isinstance(value, list) else value
        return [{"type": "completed", "result": unsimulate(demo_result(task["expected_output"], task["context"]))}]

    def cancel(self, handle):
        return {"status": "unknown"}

    def recover(self, attempt_dir):
        return {"status": self.recovery, "result": {"text": "Recovered boundary test", "simulated": False}}


class MultiAITests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="multi_ai_test_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.coordinators = []

    def coordinator(self, adapters=None, profile_source=None, result_observer=None):
        coordinator = Coordinator(self.root, adapters, profile_source=profile_source, result_observer=result_observer)
        self.coordinators.append(coordinator)
        self.addCleanup(coordinator.close)
        return coordinator

    def task(self, identifier, role="writer", **extra):
        return {"task_id": identifier, "project_id": "test", "role": role, **extra}

    def advance(self, coordinator, count=10):
        for _ in range(count):
            snapshot = coordinator.tick()
        return snapshot

    def by_id(self, coordinator, identifier):
        return next(task for task in coordinator.snapshot()["tasks"] if task["task_id"] == identifier)

    def assert_code(self, code, operation):
        with self.assertRaises(StudioError) as caught:
            operation()
        self.assertEqual(code, caught.exception.code)

    def live_profile(self, profile_id):
        provider = profile_id.rsplit("-", 1)[0]
        return {"profile_id": profile_id, "provider": provider, "actual_auth_state": "authenticated",
                "access_state": "verified", "billing_safe": True, "can_probe": True, "state": "ready",
                "account_binding": {"identity_hash": "a" * 64, "auth_generation": 1},
                "simulated": False}

    def test_demo_dependencies_persistence_and_unknown_quotas(self):
        coordinator = self.coordinator()
        demo = coordinator.demo()
        coordinator.resume()
        first = coordinator.tick()
        self.assertEqual(first["counts"]["running"], 1)
        self.assertEqual(self.by_id(coordinator, demo["task_ids"][1])["status"], "pending")
        complete = self.advance(coordinator, 4)
        self.assertEqual(complete["counts"]["completed"], 3)
        self.assertEqual(len(complete["artifacts"]), 3)
        self.assertEqual(complete["tasks"][-1]["result"]["payload"]["verdict"], "needs_review")
        self.assertEqual(complete["quality_status"], "not_measured")
        self.assertEqual(len(complete["profiles"]), 8)
        self.assertTrue(all(p["quota_remaining"] is None and p["actual_auth_state"] == "not_configured" for p in complete["profiles"]))
        cursor = coordinator.events(limit=2)
        self.assertTrue(cursor["truncated"])
        self.assertGreater(coordinator.events(cursor["last_event_id"])["events"][0]["sequence"], cursor["last_event_id"])
        coordinator.close()
        restored = self.coordinator().snapshot()
        self.assertEqual(restored["counts"]["completed"], 3)
        self.assertEqual(restored["artifacts"], complete["artifacts"])
        for artifact in complete["artifacts"]:
            self.assertEqual(content_hash(self.root / artifact["path"]), artifact["sha256"])
        request = next(self.root / a["result_path"] for a in complete["attempts"] if a["task_id"] == demo["task_ids"][2])
        self.assertEqual(set(json.loads(request.with_name("request.json").read_text(encoding="utf-8"))["context"]["dependency_results"]), set(demo["task_ids"][:2]))

    def test_live_capacity_includes_explicit_reserve_probes(self):
        adapters = {p: LiveContractStub() for p in PROVIDERS}
        coordinator = self.coordinator(adapters, self.live_profile)
        for p in PROVIDERS:
            coordinator.enqueue_access_probe(p + "-2")
            coordinator.enqueue_access_probe(p + "-3")
        coordinator.enqueue(self.task("creative_codex", "director"))
        coordinator.enqueue(self.task("false_local", resource_class="local_heavy"))
        coordinator.resume()
        state = coordinator.tick()
        self.assertEqual(state["counts"]["running"], 2)
        self.assertEqual(state["limits"]["max_workers_per_provider"], 1)
        self.assertEqual({a["profile_id"] for a in state["attempts"]}, {p + "-2" for p in PROVIDERS})
        self.assertEqual(state["selected_profiles"], {p: p + "-1" for p in PROVIDERS})
        self.assertEqual(state["mode"], "live")
        self.assertEqual(self.by_id(coordinator, "creative_codex")["status"], "pending")
        self.assert_code("SCHEMA_INVALID", lambda: coordinator.enqueue(self.task("reserve_creative", requested_profile_id="codex-2")))
        self.assertEqual(self.by_id(coordinator, "false_local")["error"]["kind"], "local_executor_required")

    def test_live_access_probe_gate_and_creative_access_are_separate(self):
        adapters = {p: LiveContractStub(complete=True) for p in PROVIDERS}
        source = lambda profile_id: {**self.live_profile(profile_id), "access_state": "unverified", "state": "unverified"}
        coordinator = self.coordinator(adapters, source)
        coordinator.enqueue(self.task("creative"))
        probe = coordinator.enqueue_access_probe("codex-2")
        coordinator.resume()
        state = self.advance(coordinator, 3)
        self.assertEqual(self.by_id(coordinator, "creative")["status"], "waiting_access")
        self.assertEqual(self.by_id(coordinator, probe["task_id"])["status"], "completed")
        self.assertFalse(self.by_id(coordinator, probe["task_id"])["result"]["simulated"])
        self.assertEqual(len(adapters["codex"].started), 1)
        self.assertEqual(state["selected_profiles"]["codex"], "codex-1")
        self.assert_code("LIVE_ACCESS_REQUIRED", coordinator.live_chain)

    def test_duplicate_access_checks_share_pending_running_and_stopping_attempt(self):
        adapters = {p: LiveContractStub() for p in PROVIDERS}
        coordinator = self.coordinator(adapters, self.live_profile)
        first = coordinator.enqueue_access_probe("codex-1")
        self.assertEqual(first["task_id"], coordinator.enqueue_access_probe("codex-1")["task_id"])
        self.assertEqual(len(coordinator.snapshot()["tasks"]), 1)
        coordinator.resume()
        coordinator.tick()
        running = coordinator.enqueue_access_probe("codex-1")
        self.assertEqual(first["task_id"], running["task_id"])
        self.assertEqual(running["status"], "running")
        self.assertEqual(len(adapters["codex"].started), 1)
        coordinator.stop()
        self.assertEqual(first["task_id"], coordinator.enqueue_access_probe("codex-1")["task_id"])
        self.assertEqual(len(coordinator.snapshot()["tasks"]), 1)
        adapters["codex"].failure = {"kind": "interrupted", "retryable": False}
        coordinator.tick()
        replacement = coordinator.enqueue_access_probe("codex-1")
        self.assertNotEqual(first["task_id"], replacement["task_id"])
        self.assertEqual(len(coordinator.snapshot()["tasks"]), 2)

    def test_access_probe_reuses_unstarted_waiting_request_but_allows_explicit_recheck(self):
        adapters = {p: LiveContractStub(complete=True) for p in PROVIDERS}
        coordinator = self.coordinator(adapters, lambda key: {**self.live_profile(key), "billing_safe": False})
        first = coordinator.enqueue_access_probe("codex-1")
        coordinator.resume()
        coordinator.tick()
        self.assertEqual(self.by_id(coordinator, first["task_id"])["status"], "waiting_access")
        self.assertEqual(first["task_id"], coordinator.enqueue_access_probe("codex-1")["task_id"])
        coordinator.profile_source = self.live_profile
        coordinator.resume()
        self.advance(coordinator, 3)
        self.assertEqual(self.by_id(coordinator, first["task_id"])["status"], "completed")
        replacement = coordinator.enqueue_access_probe("codex-1")
        self.assertNotEqual(first["task_id"], replacement["task_id"])

    def test_live_unknown_billing_and_source_identity_fail_closed(self):
        adapter = LiveContractStub()
        coordinator = self.coordinator({p: adapter for p in PROVIDERS},
                                       lambda profile_id: {**self.live_profile(profile_id), "billing_safe": False})
        probe = coordinator.enqueue_access_probe("codex-1")
        coordinator.resume()
        coordinator.tick()
        self.assertEqual(self.by_id(coordinator, probe["task_id"])["error"]["kind"], "billing")
        self.assertEqual(adapter.started, [])
        coordinator.profile_source = lambda profile_id: self.live_profile("codex-3")
        coordinator.resume()
        coordinator.tick()
        self.assertEqual(self.by_id(coordinator, probe["task_id"])["error"]["kind"], "authentication")
        self.assertEqual(adapter.started, [])

    def test_switch_to_live_does_not_execute_old_fake_pending_or_rotation(self):
        coordinator = self.coordinator()
        coordinator.enqueue(self.task("old_fake"))
        coordinator.set_rotation(True)
        coordinator.close()
        adapters = {p: LiveContractStub() for p in PROVIDERS}
        restored = self.coordinator(adapters, self.live_profile)
        restored.resume()
        state = restored.tick()
        self.assertFalse(state["rotation_on_quota"])
        self.assertEqual(self.by_id(restored, "old_fake")["error"]["kind"], "execution_mode_changed")
        self.assertEqual(adapters["codex"].started, [])
        self.assert_code("SIMULATOR_REQUIRED", restored.demo)

    def test_live_quota_never_rotates_and_access_probe_never_retries(self):
        adapters = {p: LiveContractStub(failure={"kind": "quota_exhausted", "quota_confirmed": True}) for p in PROVIDERS}
        coordinator = self.coordinator(adapters, self.live_profile)
        probe = coordinator.enqueue_access_probe("gemini-2")
        coordinator.resume()
        state = self.advance(coordinator, 5)
        self.assertEqual(self.by_id(coordinator, probe["task_id"])["status"], "waiting_access")
        self.assertEqual(len(state["attempts"]), 1)
        self.assertEqual(state["attempts"][0]["profile_id"], "gemini-2")
        self.assertEqual(state["selected_profiles"]["gemini"], "gemini-1")
        self.assert_code("ROTATION_UNVERIFIED", lambda: coordinator.set_rotation(True))

    def test_live_recovery_and_observation_errors_retain_reservations(self):
        adapters = {p: LiveContractStub() for p in PROVIDERS}
        coordinator = self.coordinator(adapters, self.live_profile)
        first = coordinator.enqueue_access_probe("codex-1")
        second = coordinator.enqueue_access_probe("codex-2")
        coordinator.resume()
        coordinator.tick()
        adapters["codex"].raise_poll = True
        state = coordinator.tick()
        self.assertEqual(len(state["attempts"]), 1)
        self.assertEqual(self.by_id(coordinator, first["task_id"])["status"], "running")
        coordinator.close()
        restored = self.coordinator(adapters, self.live_profile)
        restored.tick()
        self.assertEqual(self.by_id(restored, second["task_id"])["status"], "pending")
        adapters["codex"].recovery = "completed"
        restored.tick()
        self.assertEqual(self.by_id(restored, first["task_id"])["status"], "completed")
        self.assertEqual(self.by_id(restored, second["task_id"])["status"], "running")

    def test_live_chain_delivers_both_real_boundary_results_to_critic(self):
        adapters = {p: LiveContractStub(complete=True) for p in PROVIDERS}
        coordinator = self.coordinator(adapters, self.live_profile)
        chain = coordinator.live_chain()
        coordinator.resume()
        state = self.advance(coordinator, 5)
        self.assertEqual(state["counts"]["completed"], 4)
        self.assertTrue(all(not t["result"]["simulated"] for t in state["tasks"]))
        critic = adapters["gemini"].started[0][0]
        self.assertEqual(set(critic["context"]["dependency_results"]), set(chain["task_ids"][:2]))
        self.assertTrue(all(p["profile_id"].endswith("-1") for adapter in adapters.values() for _, p in adapter.started))

    def test_account_feedback_is_durable_after_commit_and_does_not_reexecute(self):
        adapters = {p: LiveContractStub(complete=True) for p in PROVIDERS}
        seen = []
        def unavailable(profile_id, task, result, failure):
            with Registry(self.root) as registry:
                self.assertEqual(registry.db.execute("SELECT status FROM ai_tasks WHERE id=?", (task["task_id"],)).fetchone()[0], "completed")
            raise OSError("Account state is temporarily unavailable")
        coordinator = self.coordinator(adapters, self.live_profile, unavailable)
        first = coordinator.enqueue_access_probe("codex-1")
        second = coordinator.enqueue_access_probe("codex-2")
        coordinator.resume()
        self.advance(coordinator, 3)
        self.assertEqual(self.by_id(coordinator, first["task_id"])["status"], "completed")
        self.assertEqual(self.by_id(coordinator, second["task_id"])["status"], "pending")
        self.assertEqual(len(adapters["codex"].started), 1)
        coordinator.close()
        restored = self.coordinator(adapters, self.live_profile, lambda *args: seen.append(args))
        self.assertEqual(len(seen), 1)
        self.assertFalse(seen[0][2]["simulated"])
        restored.tick()
        self.assertEqual(self.by_id(restored, second["task_id"])["status"], "running")
        self.assertEqual(len(adapters["codex"].started), 2)

    def test_deferred_old_account_success_and_failure_are_acknowledged_after_reconnect(self):
        from tests.test_ai_accounts import AccountsTests
        for old_outcome in ("completed", "billing_failure"):
            with self.subTest(old_outcome=old_outcome):
                fixture = AccountsTests()
                fixture.setUp()
                self.addCleanup(fixture.doCleanups)
                manager = fixture.manager
                original = fixture.confirm("codex-1")["account_binding"]
                adapters = {p: LiveContractStub(complete=True) for p in PROVIDERS}
                if old_outcome == "billing_failure":
                    adapters["codex"].failure = {"kind": "billing", "message": "Old account boundary failure", "retryable": False}
                def unavailable(*args):
                    raise OSError("Hold account report delivery for the restart test")
                coordinator = self.coordinator(adapters, manager.queue_profile, unavailable)
                old_task = coordinator.enqueue_access_probe("codex-1")
                coordinator.resume()
                self.advance(coordinator, 3)
                attempt = next(a for a in coordinator.snapshot()["attempts"] if a["task_id"] == old_task["task_id"])
                request = json.loads((self.root / attempt["result_path"]).with_name("request.json").read_text(encoding="utf-8"))
                self.assertEqual(request["account_binding"], original)
                manager.connect("codex-1", reconnect=True)
                fixture.finish_login()
                fixture.codex_responses["codex-1"] = fixture.codex_diagnostic("replacement@example.invalid")
                replacement = fixture.confirm("codex-1")
                self.assertNotEqual(replacement["account_binding"], original)
                self.assertEqual(replacement["access_state"], "unverified")
                coordinator.close()
                restored = self.coordinator(adapters, manager.queue_profile, manager.observe_result)
                after = manager.queue_profile("codex-1")
                self.assertEqual(after["access_state"], "unverified")
                self.assertTrue(after["can_probe"])
                self.assertEqual(after["ignored_result_evidence"][-1]["attempt_id"], attempt["id"])
                with Registry(self.root) as registry:
                    self.assertEqual(registry.db.execute("SELECT COUNT(*) FROM ai_profile_reports WHERE delivered=0").fetchone()[0], 0)
                adapters["codex"].failure = None
                fresh_task = restored.enqueue_access_probe("codex-1")
                restored.resume()
                self.advance(restored, 3)
                self.assertEqual(self.by_id(restored, fresh_task["task_id"])["status"], "completed")
                self.assertEqual(manager.queue_profile("codex-1")["access_state"], "verified")
                restored.close()

    def test_live_tasks_reject_simulated_dependency_and_profile_evidence(self):
        coordinator = self.coordinator()
        coordinator.enqueue(self.task("fake_source"))
        coordinator.resume()
        self.advance(coordinator, 3)
        coordinator.close()
        adapters = {p: LiveContractStub() for p in PROVIDERS}
        restored = self.coordinator(adapters, self.live_profile)
        restored.enqueue(self.task("live_dependent", depends_on=["fake_source"]))
        restored.resume()
        restored.tick()
        self.assertEqual(self.by_id(restored, "live_dependent")["error"]["kind"], "simulated_dependency")
        restored.profile_source = lambda profile_id: {**self.live_profile(profile_id), "simulated": True}
        probe = restored.enqueue_access_probe("codex-2")
        restored.tick()
        self.assertEqual(self.by_id(restored, probe["task_id"])["status"], "waiting_access")
        self.assertEqual(adapters["codex"].started, [])

    def test_same_registry_preserves_existing_data_and_idempotency(self):
        with Registry(self.root) as registry:
            registry.db.execute("CREATE TABLE jobs(id TEXT PRIMARY KEY,state TEXT)")
            registry.db.execute("INSERT INTO jobs VALUES('existing','done')")
        coordinator = self.coordinator()
        task = self.task("one")
        first = coordinator.enqueue(task)
        self.assertEqual(first, coordinator.enqueue(task))
        self.assert_code("IDEMPOTENCY_CONFLICT", lambda: coordinator.enqueue({**task, "context": {"changed": True}}))
        self.assert_code("DEPENDENCY_INVALID", lambda: coordinator.enqueue(self.task("wrong", depends_on=["missing"])))
        coordinator.resume()
        self.advance(coordinator, 2)
        with Registry(self.root) as registry:
            self.assertEqual(tuple(registry.db.execute("SELECT * FROM jobs").fetchone()), ("existing", "done"))

    def test_cloud_and_profile_capacity_and_soft_pause(self):
        coordinator = self.coordinator()
        for role in ("writer", "director", "critic"):
            coordinator.enqueue(self.task(role, role, simulation={"delay_polls": 2}))
            coordinator.enqueue(self.task(role + "_next", role))
        coordinator.resume()
        first = coordinator.tick()
        self.assertEqual(first["counts"]["running"], 2)
        active = [a for a in first["attempts"] if a["status"] == "running"]
        self.assertEqual(len({a["profile_id"] for a in active}), 2)
        coordinator.pause()
        paused = self.advance(coordinator, 4)
        self.assertEqual(paused["counts"]["completed"], 2)
        self.assertEqual(paused["counts"]["pending"], 4)
        coordinator.resume()
        self.assertEqual(self.advance(coordinator, 7)["counts"]["completed"], 6)

    def test_heavy_capacity_independent_of_cloud_and_unknown_local_recovery(self):
        local = LocalStub()
        adapters = {p: FakeAdapter(p) for p in PROVIDERS}
        adapters["local"] = local
        coordinator = self.coordinator(adapters)
        coordinator.enqueue(self.task("heavy1", "director", resource_class="local_heavy"))
        coordinator.enqueue(self.task("heavy2", "writer", resource_class="local_heavy"))
        for role in ("writer", "director", "critic"):
            coordinator.enqueue(self.task(role, role, simulation={"delay_polls": 5}))
        coordinator.resume()
        state = coordinator.tick()
        self.assertEqual(state["counts"]["running"], 3)
        self.assertEqual(local.started, 1)
        coordinator.close()
        resumed = self.coordinator(adapters)
        self.advance(resumed, 2)
        self.assertEqual(self.by_id(resumed, "heavy1")["status"], "running")
        self.assertEqual(self.by_id(resumed, "heavy2")["status"], "pending")
        self.assertEqual(local.started, 1)

    def test_completed_local_recovery_does_not_restart_worker(self):
        local = LocalStub()
        adapters = {p: FakeAdapter(p) for p in PROVIDERS}
        adapters["local"] = local
        coordinator = self.coordinator(adapters)
        coordinator.enqueue(self.task("heavy", resource_class="local_heavy"))
        coordinator.resume()
        coordinator.tick()
        coordinator.close()
        local.recovery = "completed"
        resumed = self.coordinator(adapters)
        self.assertEqual(self.by_id(resumed, "heavy")["status"], "completed")
        self.assertEqual(local.started, 1)

    def test_group_switch_drains_before_new_profiles_dispatch(self):
        coordinator = self.coordinator()
        for role in ("writer", "director", "critic"):
            coordinator.enqueue(self.task(role, role, simulation={"delay_polls": 2}))
            coordinator.enqueue(self.task(role + "_next", role))
        coordinator.resume()
        coordinator.tick()
        selected = coordinator.select_profiles("group", 2)
        self.assertIsNotNone(selected["pending_selection"])
        self.assertTrue(all(value.endswith("-1") for value in selected["selected_profiles"].values()))
        before = coordinator.tick()
        self.assertEqual(len(before["attempts"]), 2)
        after = self.advance(coordinator, 3)
        self.assertTrue(all(value.endswith("-2") for value in after["selected_profiles"].values()))
        for attempt in after["attempts"]:
            if attempt["task_id"].endswith("_next"):
                self.assertTrue(attempt["profile_id"].endswith("-2"))

    def test_per_provider_switch_keeps_other_provider_working(self):
        coordinator = self.coordinator()
        coordinator.configure_role("writer", "gemini")
        coordinator.enqueue(self.task("long", "writer", simulation={"delay_polls": 4}))
        coordinator.enqueue(self.task("codex1", "director"))
        coordinator.enqueue(self.task("codex2", "director"))
        coordinator.resume()
        coordinator.tick()
        coordinator.select_profiles("per_provider", {"gemini": 2})
        after = coordinator.tick()
        self.assertEqual(self.by_id(coordinator, "codex2")["status"], "running")
        self.assertEqual(after["selected_profiles"]["gemini"], "gemini-1")

    def test_quota_rotation_is_explicit_and_bounded(self):
        coordinator = self.coordinator()
        coordinator.set_rotation(True)
        coordinator.enqueue(self.task("quota", simulation=[{"outcome": "quota_exhausted"}] * 3))
        coordinator.resume()
        state = self.advance(coordinator, 8)
        task = self.by_id(coordinator, "quota")
        self.assertEqual(task["status"], "waiting_access")
        self.assertEqual(task["transitions"], 2)
        self.assertEqual(task["attempt"], 3)
        self.assertEqual([attempt["profile_id"] for attempt in state["attempts"]], ["codex-1", "codex-2", "codex-3"])
        coordinator.select_profiles("per_provider", {"codex": 1})
        self.advance(coordinator, 2)
        self.assertEqual(self.by_id(coordinator, "quota")["attempt"], 3)

    def test_transient_retry_and_local_turn_limit_are_distinct(self):
        coordinator = self.coordinator()
        coordinator.configure_role("director", "gemini")
        coordinator.clock = lambda: 1000.0
        coordinator.enqueue(self.task("transient", simulation=[{"outcome": "network"}, {"outcome": "completed"}]))
        coordinator.enqueue(self.task("turn_limit", "director", simulation={"outcome": "local_turn_limit"}))
        coordinator.resume()
        self.advance(coordinator, 2)
        self.assertEqual(self.by_id(coordinator, "transient")["attempt"], 1)
        self.assertEqual(self.by_id(coordinator, "transient")["status"], "pending")
        self.assertEqual(self.by_id(coordinator, "turn_limit")["status"], "failed")
        coordinator.clock = lambda: 1010.0
        self.advance(coordinator, 3)
        self.assertEqual(self.by_id(coordinator, "transient")["status"], "completed")
        self.assertEqual(self.by_id(coordinator, "transient")["attempt"], 2)

    def test_stop_late_result_cannot_replace_retry_and_slot_is_held(self):
        coordinator = self.coordinator()
        coordinator.enqueue(self.task("late", simulation=[{"delay_polls": 3, "late_after_cancel": True}, {"result": {"text": "new"}}]))
        coordinator.resume()
        coordinator.tick()
        coordinator.stop()
        coordinator.resume()
        state = coordinator.tick()
        self.assertEqual(len(state["attempts"]), 1)
        self.assertEqual(state["attempts"][0]["status"], "stopping")
        result = self.advance(coordinator, 6)
        task = self.by_id(coordinator, "late")
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["result"]["payload"]["text"], "new")
        self.assertEqual(len(result["artifacts"]), 1)
        self.assertTrue(any(event["kind"] == "late_result_rejected" for event in coordinator.events()["events"]))

    def test_attempt_budget_is_preserved_by_manual_resume(self):
        coordinator = self.coordinator()
        coordinator.enqueue(self.task("once", max_attempts=1, simulation={"delay_polls": 1}))
        coordinator.resume()
        coordinator.tick()
        coordinator.stop()
        coordinator.tick()
        coordinator.resume()
        self.advance(coordinator, 3)
        self.assertEqual(self.by_id(coordinator, "once")["attempt"], 1)
        self.assertEqual(self.by_id(coordinator, "once")["status"], "interrupted")

    def test_completed_file_before_commit_is_recovered_once(self):
        coordinator = self.coordinator()
        coordinator.enqueue(self.task("saved", simulation={"delay_polls": 20}))
        coordinator.resume()
        coordinator.tick()
        with Registry(self.root) as registry, registry.transaction():
            attempt = registry.db.execute("SELECT * FROM ai_attempts").fetchone()
            coordinator._write_completion(registry, attempt, {"text": "durable completed file"})
        coordinator.close()
        recovered = self.coordinator()
        task = self.by_id(recovered, "saved")
        self.assertEqual(task["status"], "completed")
        self.assertEqual(task["attempt"], 1)
        self.assertEqual(len(recovered.snapshot()["artifacts"]), 1)
        self.advance(recovered, 3)
        self.assertEqual(len(recovered.snapshot()["artifacts"]), 1)

    def test_legacy_fake_completion_recovered_in_live_workspace_never_reports_access(self):
        coordinator = self.coordinator()
        coordinator.enqueue(self.task("legacy_fake", simulation={"delay_polls": 20}))
        coordinator.resume()
        coordinator.tick()
        with Registry(self.root) as registry, registry.transaction():
            attempt = registry.db.execute("SELECT * FROM ai_attempts").fetchone()
            coordinator._write_completion(registry, attempt, {"text": "Saved simulated boundary fixture"})
            row = registry.db.execute("SELECT payload FROM ai_tasks WHERE id='legacy_fake'").fetchone()
            payload = json.loads(row[0])
            payload.pop("execution_mode")
            registry.db.execute("UPDATE ai_tasks SET payload=? WHERE id='legacy_fake'", (canonical_json(payload),))
        coordinator.close()
        observed = []
        def accepted_only(profile_id, task, result, failure):
            self.assertEqual(task.get("execution_mode"), "live")
            self.assertIs(result["simulated"], False)
            observed.append(task["task_id"])
        adapters = {p: LiveContractStub(complete=True) for p in PROVIDERS}
        restored = self.coordinator(adapters, self.live_profile, accepted_only)
        saved = self.by_id(restored, "legacy_fake")
        self.assertEqual(saved["status"], "completed")
        self.assertTrue(saved["result"]["simulated"])
        self.assertEqual(observed, [])
        with Registry(self.root) as registry:
            self.assertEqual(registry.db.execute("SELECT COUNT(*) FROM ai_profile_reports").fetchone()[0], 0)
        probe = restored.enqueue_access_probe("codex-1")
        restored.resume()
        self.advance(restored, 3)
        self.assertEqual(self.by_id(restored, probe["task_id"])["status"], "completed")
        self.assertEqual(observed, [probe["task_id"]])

    def test_recovery_rejects_disagreeing_simulation_flags(self):
        coordinator = self.coordinator()
        coordinator.enqueue(self.task("inconsistent", simulation={"delay_polls": 20}))
        coordinator.resume()
        coordinator.tick()
        with Registry(self.root) as registry, registry.transaction():
            attempt = registry.db.execute("SELECT * FROM ai_attempts").fetchone()
            coordinator._write_completion(registry, attempt, {"text": "Saved simulated boundary fixture"})
            path = self.root / attempt["result_path"]
        envelope = json.loads(path.read_text(encoding="utf-8"))
        envelope["simulated"] = False
        path.write_text(canonical_json(envelope), encoding="utf-8")
        coordinator.close()
        restored = self.coordinator()
        self.assertEqual(self.by_id(restored, "inconsistent")["status"], "failed")
        self.assertEqual(len(restored.snapshot()["artifacts"]), 0)
        self.assertIn("simulation provenance", self.by_id(restored, "inconsistent")["error"]["message"])

    def test_partial_file_is_not_accepted_and_restart_requires_resume(self):
        coordinator = self.coordinator()
        coordinator.enqueue(self.task("partial", simulation={"delay_polls": 50}))
        coordinator.resume()
        state = coordinator.tick()
        path = self.root / state["attempts"][0]["result_path"]
        path.with_suffix(".tmp").write_text('{"partial":', encoding="utf-8")
        coordinator.close()
        recovered = self.coordinator()
        task = self.by_id(recovered, "partial")
        self.assertEqual(task["status"], "interrupted")
        self.assertEqual(len(recovered.snapshot()["artifacts"]), 0)
        recovered.tick()
        self.assertEqual(self.by_id(recovered, "partial")["attempt"], 1)

    def test_scene_cas_pinned_conflict_and_partial_invalidation(self):
        coordinator = self.coordinator()
        demo = coordinator.demo()
        coordinator.enqueue(self.task("other_scene", context={"scene_id": "other"}))
        coordinator.resume()
        before = self.advance(coordinator, 6)
        scene = before["scenes"][0]
        artifact_hashes = {item["path"]: item["sha256"] for item in before["artifacts"]}
        coordinator.update_scene(scene["project_id"], scene["scene_id"], scene["text"], 1, pinned=True)
        self.assert_code("PINNED_CONFLICT", lambda: coordinator.update_scene(scene["project_id"], scene["scene_id"], "Новая реплика.", 1))
        changed = coordinator.update_scene(scene["project_id"], scene["scene_id"], "Новая реплика.", 1, pinned=False)
        self.assertEqual(changed["version"], 2)
        self.assertEqual(set(changed["invalidated_tasks"]), set(demo["task_ids"]))
        self.assertEqual(self.by_id(coordinator, "other_scene")["status"], "completed")
        self.assertEqual(len(coordinator.snapshot()["scenes"]), 2)
        self.assert_code("REVISION_CONFLICT", lambda: coordinator.update_scene(scene["project_id"], scene["scene_id"], "Еще правка.", 1))
        self.assertEqual(artifact_hashes, {path: content_hash(self.root / path) for path in artifact_hashes})

    def test_conflicting_writers_cannot_publish_same_scene_version(self):
        coordinator = self.coordinator()
        context = {"project_id": "test", "scene_id": "s1", "scene_version": 1}
        payload = demo_result("scene", context)
        coordinator.enqueue(self.task("writer1", expected_output="scene", context=context, simulation={"result": payload}))
        coordinator.enqueue(self.task("writer2", expected_output="scene", context=context, simulation={"result": {**payload, "goal": "Different decision"}}))
        coordinator.resume()
        state = self.advance(coordinator, 5)
        self.assertEqual(self.by_id(coordinator, "writer1")["status"], "completed")
        self.assertEqual(self.by_id(coordinator, "writer2")["status"], "failed")
        self.assertEqual(len(state["scenes"]), 1)
        self.assertEqual(len(state["artifacts"]), 1)

    def test_real_adapter_cannot_inherit_fake_readiness_or_rotate(self):
        coordinator = self.coordinator()
        coordinator.close()
        adapters = {p: FakeAdapter(p) for p in PROVIDERS}
        adapters["codex"] = CodexAdapter()
        coordinator = self.coordinator(adapters)
        coordinator.enqueue(self.task("real", "director"))
        coordinator.resume()
        state = coordinator.tick()
        self.assertEqual(self.by_id(coordinator, "real")["status"], "waiting_access")
        self.assertFalse(any(p["simulated"] for p in state["profiles"] if p["provider"] == "codex"))
        self.assert_code("ROTATION_UNVERIFIED", lambda: coordinator.set_rotation(True))

    def test_secrets_are_redacted_and_fake_provenance_cannot_be_overridden(self):
        coordinator = self.coordinator()
        coordinator.enqueue(self.task("redacted", context={"api_key": "SENTINEL_SECRET"},
                                      simulation={"result": {"text": "fixture", "simulated": False}}))
        coordinator.resume()
        state = self.advance(coordinator, 3)
        self.assertNotIn("SENTINEL_SECRET", canonical_json(state))
        for path in (self.root / "ai").rglob("*.json*"):
            self.assertNotIn("SENTINEL_SECRET", path.read_text(encoding="utf-8"))
        self.assertTrue(self.by_id(coordinator, "redacted")["result"]["payload"]["simulated"])

    def test_role_updates_apply_only_to_new_tasks(self):
        coordinator = self.coordinator()
        first = coordinator.enqueue(self.task("before"))
        coordinator.configure_role("writer", "gemini", "explicit-test-model", "Concise rules")
        second = coordinator.enqueue(self.task("after"))
        self.assertEqual(first["assignment"]["provider"], "codex")
        self.assertEqual(second["assignment"]["provider"], "gemini")
        self.assertEqual(second["assignment"]["rules_version"], 2)

    def test_invalid_fixture_does_not_stall_independent_task(self):
        coordinator = self.coordinator()
        coordinator.enqueue(self.task("invalid", expected_output="edit_plan", context={"voice_version": 4}))
        coordinator.enqueue(self.task("valid", "director"))
        coordinator.resume()
        self.advance(coordinator, 3)
        self.assertEqual(self.by_id(coordinator, "invalid")["status"], "failed")
        self.assertEqual(self.by_id(coordinator, "valid")["status"], "completed")

    def test_new_task_cannot_reference_old_scene_after_edit(self):
        coordinator = self.coordinator()
        demo = coordinator.demo()
        coordinator.resume()
        self.advance(coordinator, 4)
        scene = coordinator.snapshot()["scenes"][0]
        coordinator.update_scene(scene["project_id"], scene["scene_id"], "Обновленный текст.", 1)
        coordinator.enqueue({"task_id": "stale_new", "project_id": demo["project_id"], "role": "director",
                             "expected_output": "edit_plan", "input_versions": {"scene": "scene_001:v1"}})
        coordinator.tick()
        self.assertEqual(self.by_id(coordinator, "stale_new")["status"], "blocked")
        self.assertEqual(self.by_id(coordinator, "stale_new")["attempt"], 0)

    def test_malformed_retry_delay_fails_closed(self):
        class BadDelay(FakeAdapter):
            def poll(self, handle):
                return [{"type": "failed", "failure": {"kind": "network", "retryable": True, "retry_after": "not-seconds"}}]
        adapters = {p: FakeAdapter(p) for p in PROVIDERS}
        adapters["codex"] = BadDelay("codex")
        coordinator = self.coordinator(adapters)
        coordinator.enqueue(self.task("delay"))
        coordinator.resume()
        self.advance(coordinator, 3)
        task = self.by_id(coordinator, "delay")
        self.assertEqual(task["status"], "failed")
        self.assertEqual(task["error"]["kind"], "invalid_input")

    def test_exclusive_lock_blocks_another_process(self):
        coordinator = self.coordinator()
        script = "from app.multi_ai import Coordinator; from app.registry import StudioError; import sys\ntry: Coordinator(sys.argv[1])\nexcept StudioError as e: print(e.code)"
        completed = subprocess.run([sys.executable, "-c", script, str(self.root)], capture_output=True, text=True, timeout=10,
                                   creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "COORDINATOR_RUNNING")
        self.assert_code("COORDINATOR_RUNNING", lambda: Coordinator(self.root))
        coordinator.close()
        self.coordinator()

    def test_production_workspace_is_rejected_before_open(self):
        root = Path(__file__).resolve().parents[1] / "workspace" / "studio"
        self.assert_code("OFFLINE_WORKSPACE_REQUIRED", lambda: Coordinator(root))


if __name__ == "__main__":
    unittest.main()
