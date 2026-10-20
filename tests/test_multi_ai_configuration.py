"""MAI-05 roster migration and bounded chain tests, with no model processes."""
import json
from pathlib import Path
import tempfile
import unittest

from app.multi_ai import ACTIVE_PROVIDERS, Coordinator
from app.registry import Registry, StudioError, canonical_json
from tests.test_multi_ai import LiveContractStub


class ActiveConfigurationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="mai05_config_")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def coordinator(self, adapters=None, source=None):
        coordinator = Coordinator(self.root, adapters, profile_source=source)
        self.addCleanup(coordinator.close)
        return coordinator

    def test_provider_route_rejection_is_not_reported_as_billing_or_quota(self):
        failure = Coordinator._access_failure({"reason_code": "unsupported_client", "reason": "UNSUPPORTED_CLIENT", "billing_safe": False})
        self.assertEqual(failure["kind"], "unsupported_client")
        self.assertFalse(failure["quota_confirmed"])

    @staticmethod
    def profile(key):
        return {"profile_id": key, "provider": key.rsplit("-", 1)[0],
                "actual_auth_state": "authenticated", "access_state": "verified",
                "billing_safe": True, "can_probe": True, "state": "ready", "simulated": False,
                "account_binding": {"identity_hash": "a" * 64, "auth_generation": 1}}

    def legacy(self, model=None):
        queue = self.coordinator()
        queue.enqueue({"task_id": "historical_pending", "project_id": "history", "role": "writer"})
        queue.close()
        with Registry(self.root) as registry, registry.transaction():
            settings = queue._settings(registry)
            roles = settings["roles"]
            roles["writer"].update(provider="claude", model=model, rules="Owner's retained rules", rules_version=7)
            queue._setting(registry, "roles", roles)
            selected = {"codex": "codex-3", "gemini": "gemini-2", "claude": "claude-2"}
            queue._setting(registry, "selected_profiles", selected)
            queue._setting(registry, "pending_selection", {"mode": "per_provider", "selected_profiles": {
                "codex": "codex-3", "gemini": "gemini-3", "claude": "claude-1"}})
            registry.db.execute("DELETE FROM ai_settings WHERE key IN ('active_configuration_version','historical_configuration')")
            for number in (1, 2, 3):
                payload = {"profile_id": f"claude-{number}", "provider": "claude", "state": "unverified",
                           "last_successful_run": 123, "model": "historical-model"}
                registry.db.execute("INSERT INTO ai_profiles VALUES (?,?)", (payload["profile_id"], canonical_json(payload)))
            payload = json.loads(registry.db.execute("SELECT payload FROM ai_tasks WHERE id='historical_pending'").fetchone()[0])
            payload["assignment"] = roles["writer"]
            registry.db.execute("UPDATE ai_tasks SET payload=? WHERE id='historical_pending'", (canonical_json(payload),))
            saved_payload = registry.db.execute("SELECT payload FROM ai_tasks WHERE id='historical_pending'").fetchone()[0]
        return roles, selected, saved_payload

    def test_repeatable_migration_keeps_eight_active_and_full_historical_state(self):
        roles, selected, task_payload = self.legacy()
        queue = self.coordinator()
        first = queue.snapshot()
        self.assertEqual(len(first["profiles"]), 8)
        self.assertEqual(len({p["profile_id"] for p in first["profiles"]}), 8)
        self.assertEqual({p["profile_id"] for p in first["profiles"]},
                         {f"{provider}-{number}" for provider in ACTIVE_PROVIDERS for number in range(1, 5)})
        self.assertEqual(len(first["historical_profiles"]), 3)
        self.assertTrue(all(not p["active"] and p["last_successful_run"] == 123 for p in first["historical_profiles"]))
        self.assertEqual(first["selected_profiles"], {"codex": "codex-3", "gemini": "gemini-2"})
        self.assertEqual(first["historical_configuration"]["selected_profiles"], selected)
        self.assertEqual(first["historical_configuration"]["roles"], roles)
        writer = next(role for role in first["roles"] if role["role"] == "writer")
        self.assertEqual(writer, {**roles["writer"], "provider": "codex"})
        self.assertEqual(first["pending_selection"]["selected_profiles"], {"codex": "codex-3", "gemini": "gemini-3"})
        queue.close()
        restored = self.coordinator()
        self.assertEqual(restored.snapshot(), first)
        with Registry(self.root) as registry:
            self.assertEqual(registry.db.execute("SELECT COUNT(*) FROM ai_profiles").fetchone()[0], 11)
            self.assertEqual(registry.db.execute("SELECT payload FROM ai_tasks WHERE id='historical_pending'").fetchone()[0], task_payload)

    def test_historical_claude_task_never_dispatches_and_active_api_rejects_claude(self):
        _, _, task_payload = self.legacy()
        claude = LiveContractStub(complete=True)
        queue = self.coordinator({**{p: LiveContractStub(complete=True) for p in ACTIVE_PROVIDERS}, "claude": claude}, self.profile)
        for operation in (lambda: queue.set_role("writer", "claude"),
                          lambda: queue.select_profiles("per_provider", {"claude": 1}),
                          lambda: queue.enqueue_access_probe("claude-1")):
            with self.assertRaises(StudioError):
                operation()
        queue.resume()
        state = queue.tick()
        self.assertEqual(claude.started, [])
        self.assertEqual(state["tasks"][0]["error"]["kind"], "provider_inactive")
        self.assertEqual(state["tasks"][0]["attempt"], 0)
        with Registry(self.root) as registry:
            self.assertEqual(registry.db.execute("SELECT payload FROM ai_tasks WHERE id='historical_pending'").fetchone()[0], task_payload)

    def test_legacy_explicit_model_is_preserved_and_requires_confirmation(self):
        self.legacy(model="claude-owner-model")
        queue = self.coordinator({p: LiveContractStub() for p in ACTIVE_PROVIDERS}, self.profile)
        writer = next(role for role in queue.snapshot()["roles"] if role["role"] == "writer")
        self.assertEqual(writer["model"], "claude-owner-model")
        self.assertTrue(writer["requires_model_confirmation"])
        with self.assertRaises(StudioError) as error:
            queue.live_chain(mode="gpt_only")
        self.assertEqual(error.exception.code, "ROLE_MODEL_CONFIRMATION")
        probe = queue.enqueue_access_probe("codex-4")
        self.assertIsNone(probe["assignment"]["model"])
        self.assertNotIn("requires_model_confirmation", probe["assignment"])
        queue.resume()
        state = queue.tick()
        self.assertEqual(next(t for t in state["tasks"] if t["task_id"] == probe["task_id"])["status"], "running")
        queue.set_role("writer", "codex", "owner-confirmed-codex-model")
        self.assertNotIn("requires_model_confirmation", next(role for role in queue.snapshot()["roles"] if role["role"] == "writer"))

    def test_profile_resume_wakes_only_matching_pause_and_preserves_global_control_and_budget(self):
        paused = {"codex-1", "codex-2", "gemini-1"}
        adapters = {p: LiveContractStub(complete=True) for p in ACTIVE_PROVIDERS}
        def source(key):
            profile = self.profile(key)
            if key in paused:
                profile.update(paused=True, state="paused", billing_safe=False, can_probe=False)
            elif key == "codex-3":
                profile.update(actual_auth_state="not_authenticated", billing_safe=False)
            return profile
        queue = self.coordinator(adapters, source)
        for identifier, role in (("waiting_writer", "writer"), ("exhausted_writer", "writer"), ("waiting_critic", "critic")):
            queue.enqueue({"task_id": identifier, "project_id": "pause", "role": role, "max_attempts": 1})
        reserve = queue.enqueue_access_probe("codex-2")
        auth = queue.enqueue_access_probe("codex-3")
        queue.resume()
        state = queue.tick()
        self.assertEqual({t["error"]["kind"] for t in state["tasks"] if t["task_id"] != auth["task_id"]}, {"profile_paused"})
        self.assertEqual(state["counts"]["running"], 0)
        with Registry(self.root) as registry, registry.transaction():
            registry.db.execute("UPDATE ai_tasks SET attempts=1 WHERE id='exhausted_writer'")
        queue.pause()
        paused.remove("codex-1")
        state = queue.wake_profile("codex-1")
        self.assertEqual(state["control"], "paused")
        status = {t["task_id"]: t["status"] for t in state["tasks"]}
        self.assertEqual(status["waiting_writer"], "pending")
        for key in ("exhausted_writer", "waiting_critic", reserve["task_id"], auth["task_id"]):
            self.assertEqual(status[key], "waiting_access")
        self.assertEqual(queue.tick()["counts"]["running"], 0)
        queue.resume()
        queue.tick()
        state = queue.tick()
        self.assertEqual(next(t for t in state["tasks"] if t["task_id"] == "waiting_writer")["status"], "completed")
        self.assertEqual(len(adapters["codex"].started), 1)
        self.assertEqual(adapters["gemini"].started, [])

    def test_undelivered_historical_report_is_preserved_without_active_callback(self):
        queue = self.coordinator()
        queue.close()
        report = {"profile_id": "claude-1", "task": {"task_id": "historical"},
                  "result": {"simulated": False}, "failure": None}
        with Registry(self.root) as registry, registry.transaction():
            registry.db.execute("INSERT INTO ai_profile_reports VALUES (?,?,0)", ("old_attempt", canonical_json(report)))
        callbacks = []
        restored = Coordinator(self.root, result_observer=lambda *args: callbacks.append(args))
        self.addCleanup(restored.close)
        self.assertEqual(callbacks, [])
        with Registry(self.root) as registry:
            row = registry.db.execute("SELECT * FROM ai_profile_reports WHERE attempt_id='old_attempt'").fetchone()
            self.assertEqual(json.loads(row["payload"]), report)
            self.assertEqual(row["delivered"], 1)
        self.assertTrue(any(e["kind"] == "historical_profile_report_retained" for e in restored.events()["events"]))

    def test_fourth_slot_probes_and_group_selection_keep_existing_capacity(self):
        adapters = {p: LiveContractStub() for p in ACTIVE_PROVIDERS}
        queue = self.coordinator(adapters, self.profile)
        for provider in ACTIVE_PROVIDERS:
            queue.enqueue_access_probe(provider + "-4")
            queue.enqueue_access_probe(provider + "-3")
        queue.resume()
        state = queue.tick()
        self.assertEqual(state["counts"]["running"], 2)
        self.assertEqual({a["profile_id"] for a in state["attempts"]}, {"codex-4", "gemini-4"})
        self.assertEqual(state["limits"], {"max_cloud_workers": 3, "max_workers_per_provider": 1,
                                         "max_workers_per_profile": 1, "max_local_heavy_jobs": 1})
        pending = queue.select_profiles("group", 4)
        self.assertEqual(pending["selected_profiles"], {"codex": "codex-1", "gemini": "gemini-1"})
        self.assertEqual(pending["pending_selection"]["selected_profiles"], {"codex": "codex-4", "gemini": "gemini-4"})

    def test_gpt_only_chain_is_sequential_labeled_and_does_not_change_roles_or_models(self):
        adapters = {p: LiveContractStub(complete=True) for p in ACTIVE_PROVIDERS}
        queue = self.coordinator(adapters, self.profile)
        queue.set_role("director", "codex", "gpt-owner-model")
        queue.set_role("critic", "gemini", "gemini-owner-model", "Owner critic rules")
        before_roles = queue.snapshot()["roles"]
        chain = queue.live_chain(mode="gpt_only")
        self.assertEqual(chain["mode"], "gpt_only")
        self.assertEqual(chain["review_mode"], "gpt_self_review")
        self.assertEqual(chain["assignments"]["critic"]["model"], "gpt-owner-model")
        queue.resume()
        for _ in range(5):
            state = queue.tick()
            self.assertLessEqual(state["counts"]["running"], 1)
        self.assertEqual(state["counts"]["completed"], 3)
        self.assertEqual(adapters["gemini"].started, [])
        self.assertEqual(len(adapters["codex"].started), 3)
        review = adapters["codex"].started[2][0]
        self.assertEqual(review["context"]["review_mode"], "gpt_self_review")
        self.assertEqual(set(review["context"]["dependency_results"]), set(chain["task_ids"][:2]))
        self.assertEqual(queue.snapshot()["roles"], before_roles)

    def test_auto_fallback_uses_no_gemini_request_but_explicit_gemini_fails(self):
        adapters = {p: LiveContractStub(complete=True) for p in ACTIVE_PROVIDERS}
        def unavailable(key):
            value = self.profile(key)
            return {**value, "access_state": "unverified", "state": "blocked"} if key.startswith("gemini-") else value
        queue = self.coordinator(adapters, unavailable)
        chain = queue.live_chain()
        self.assertEqual(chain["mode"], "gpt_only")
        self.assertEqual(chain["fallback_reason"]["kind"], "unverified_access")
        with self.assertRaises(StudioError):
            queue.live_chain(mode="gemini")
        queue.resume()
        for _ in range(5):
            queue.tick()
        self.assertEqual(adapters["gemini"].started, [])
        self.assertEqual(queue.snapshot()["counts"]["completed"], 3)

    def test_gemini_feedback_returns_to_gpt_with_review_dependency(self):
        adapters = {p: LiveContractStub(complete=True) for p in ACTIVE_PROVIDERS}
        queue = self.coordinator(adapters, self.profile)
        chain = queue.live_chain(mode="gemini")
        self.assertEqual(chain["review_mode"], "gemini_critic")
        queue.resume()
        for _ in range(6):
            queue.tick()
        self.assertEqual(queue.snapshot()["counts"]["completed"], 4)
        self.assertEqual(len(adapters["gemini"].started), 1)
        feedback = adapters["codex"].started[-1][0]
        self.assertEqual(feedback["kind"], "live_feedback")
        self.assertEqual(feedback["context"]["purpose"], "respond_to_gemini_feedback")
        self.assertEqual(set(feedback["context"]["dependency_results"]), set(chain["task_ids"][:3]))

    def test_free_route_probe_uses_approved_model_without_changing_other_profiles(self):
        adapters = {p: LiveContractStub(complete=True) for p in ACTIVE_PROVIDERS}
        def source(key):
            profile = self.profile(key)
            return {**profile, "access_route": "gemini_api_free", "route_configuration_valid": True,
                    "model": "gemini-3.8-flash"} if key == "gemini-4" else profile
        queue = self.coordinator(adapters, source)
        before_roles = queue.snapshot()["roles"]
        probe = queue.enqueue_access_probe("gemini-4")
        self.assertEqual(probe["assignment"]["model"], "gemini-3.8-flash")
        self.assertEqual(queue.enqueue_access_probe("gemini-4")["task_id"], probe["task_id"])
        ordinary = queue.enqueue_access_probe("codex-4")
        self.assertIsNone(ordinary["assignment"]["model"])
        queue.resume()
        for _ in range(3):
            queue.tick()
        self.assertEqual(adapters["gemini"].started[0][0]["assignment"]["model"], "gemini-3.8-flash")
        self.assertEqual(queue.snapshot()["roles"], before_roles)

    def test_free_route_chain_model_is_explicit_and_conflicting_role_is_rejected(self):
        adapters = {p: LiveContractStub(complete=True) for p in ACTIVE_PROVIDERS}
        def source(key):
            profile = self.profile(key)
            return {**profile, "access_route": "gemini_api_free", "route_configuration_valid": True,
                    "model": "gemini-3.8-flash"} if key.startswith("gemini-") else profile
        queue = self.coordinator(adapters, source)
        before_roles = queue.snapshot()["roles"]
        chain = queue.live_chain(mode="gemini")
        self.assertEqual(chain["assignments"]["critic"]["model"], "gemini-3.8-flash")
        self.assertIsNone(chain["assignments"]["writer"]["model"])
        self.assertEqual(queue.snapshot()["roles"], before_roles)
        for provider, conflicting in (("gemini", "gemini-3.1-flash-lite"), ("codex", "gpt-configured-model")):
            queue.set_role("critic", provider, conflicting)
            count = len(queue.snapshot()["tasks"])
            with self.assertRaises(StudioError) as error:
                queue.live_chain(mode="gemini")
            self.assertEqual(error.exception.code, "ROLE_MODEL_CONFLICT")
            self.assertEqual(len(queue.snapshot()["tasks"]), count)
            self.assertEqual(next(r for r in queue.snapshot()["roles"] if r["role"] == "critic")["model"], conflicting)

    def test_free_route_model_change_blocks_old_request_and_unverified_route_cannot_supply_model(self):
        adapters = {p: LiveContractStub() for p in ACTIVE_PROVIDERS}
        route = {"access_route": "gemini_api_free", "route_configuration_valid": True, "model": "gemini-3.8-flash"}
        def source(key):
            return {**self.profile(key), **(route if key.startswith("gemini-") else {})}
        queue = self.coordinator(adapters, source)
        old = queue.enqueue_access_probe("gemini-1")
        route["model"] = "gemini-3.1-flash-lite"
        queue.resume()
        state = queue.tick()
        self.assertEqual(state["tasks"][0]["error"]["kind"], "route_model_conflict")
        self.assertEqual(adapters["gemini"].started, [])
        new = queue.enqueue_access_probe("gemini-1")
        self.assertNotEqual(old["task_id"], new["task_id"])
        self.assertEqual(new["assignment"]["model"], "gemini-3.1-flash-lite")
        route["route_configuration_valid"] = False
        with self.assertRaises(StudioError) as error:
            queue.enqueue_access_probe("gemini-2")
        self.assertEqual(error.exception.code, "FREE_ROUTE_MODEL_REQUIRED")
