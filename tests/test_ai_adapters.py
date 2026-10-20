"""No network, installed-provider execution, authentication or production media."""
from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from app.ai_adapters import (
    BudgetGuardError, CAPABILITIES, ClaudeAdapter, CodexAdapter, FAILURE_KINDS,
    FakeAdapter, GeminiAdapter, child_environment, classify_failure,
    default_profiles, guard_budget, sanitize,
)


class AdapterTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix="ai_adapter_test_")
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.profile = default_profiles()[0]

    def test_nine_positions_do_not_claim_auth_identity_model_or_quota(self):
        profiles = default_profiles()
        self.assertEqual(len(profiles), 9)
        self.assertEqual(len({p["profile_id"] for p in profiles}), 9)
        for provider in ("codex", "claude", "gemini"):
            self.assertEqual([p["slot"] for p in profiles if p["provider"] == provider], [1, 2, 3])
        for profile in profiles:
            self.assertIsNone(profile["model"])
            self.assertEqual(profile["auth_mode"], "unknown")
            self.assertFalse(profile["identity_verified"])
            self.assertIsNone(profile["observed_identity_label"])
            self.assertFalse(profile["rotation_on_quota"])
            for field in ("used", "remaining", "limit", "reset_at"):
                self.assertIsNone(profile["usage"][field])

    def test_real_adapters_fail_closed_even_with_forged_ready_profile(self):
        profile = {**self.profile, "state": "ready", "auth_mode": "subscription",
                   "identity_verified": True, "model": "unverified-selection",
                   "rotation_support": "allowed", "rotation_on_quota": True}
        with patch("app.ai_adapters.subprocess.run") as process:
            for adapter in (CodexAdapter(), ClaudeAdapter(), GeminiAdapter()):
                auth = adapter.check_auth(profile)
                self.assertEqual(auth["status"], "unknown")
                handle = adapter.start({"task_id": "one"}, profile)
                events = adapter.poll(handle)
                self.assertEqual(events[-1]["failure"]["code"], "capability_unavailable")
                self.assertFalse(events[-1]["is_simulated"])
                self.assertFalse(events[-1]["failure"]["retryable"])
                self.assertEqual(adapter.poll(handle), [])
            process.assert_not_called()

    def test_probe_only_discovers_paths_by_default(self):
        with patch("app.ai_adapters.shutil.which", return_value="C:/fake/codex.exe"), patch("app.ai_adapters.subprocess.run") as process:
            probe = CodexAdapter().probe()
        process.assert_not_called()
        self.assertTrue(probe["installed"])
        self.assertIsNone(probe["version"])
        self.assertEqual(probe["capabilities"], {name: "unverified" for name in CAPABILITIES})
        self.assertFalse(probe["rotation_enabled"])

    def test_optional_version_probe_uses_argument_array_and_clean_env(self):
        completed = subprocess.CompletedProcess([], 0, "fake-client 0.1\n", "")
        environment = {"PATH": "safe", "OPENAI_API_KEY": "do-not-inherit", "HOME": "preserved"}
        with patch.dict(os.environ, environment, clear=True), patch("app.ai_adapters.shutil.which", return_value="C:/fake/codex.exe"), patch("app.ai_adapters.subprocess.run", return_value=completed) as process:
            probe = CodexAdapter().probe(include_version=True)
        args, options = process.call_args
        self.assertEqual(args[0], ["C:/fake/codex.exe", "--version"])
        self.assertFalse(options["shell"])
        self.assertEqual(options["env"], {"PATH": "safe", "HOME": "preserved"})
        self.assertEqual(options["timeout"], 5)
        self.assertEqual(probe["version"], "fake-client 0.1")
        self.assertEqual(probe["auth"], "unknown")

    def test_version_probe_does_not_execute_windows_script_shims(self):
        for extension in (".ps1", ".cmd", ".bat"):
            with self.subTest(extension=extension), patch("app.ai_adapters.shutil.which", return_value="C:/fake/gemini" + extension), patch("app.ai_adapters.subprocess.run") as process:
                probe = GeminiAdapter().probe(include_version=True)
                process.assert_not_called()
                self.assertIsNone(probe["version"])

    def test_budget_rejects_paid_modes_and_explicit_overrides(self):
        for mode in ("api_key", "vertex", "console", "gateway", "pay-as-you-go"):
            with self.subTest(mode=mode), self.assertRaises(BudgetGuardError):
                guard_budget({**self.profile, "auth_mode": mode})
        for field in ("env", "environment", "provider_overrides"):
            with self.subTest(field=field), self.assertRaises(BudgetGuardError):
                guard_budget({**self.profile, field: {"OPENAI_BASE_URL": "local-api"}})
        forbidden = {**self.profile, "auth_mode": "api"}
        self.assertEqual(CodexAdapter().check_auth(forbidden)["status"], "wrong_auth_mode")
        adapter = CodexAdapter()
        self.assertEqual(adapter.poll(adapter.start({}, forbidden))[-1]["failure"]["kind"], "billing")

    def test_child_environment_does_not_mutate_global_or_source(self):
        source = {"HOME": "original", "PATH": "system-path", "OPENAI_API_KEY": "a",
                  "ANTHROPIC_BASE_URL": "b", "GOOGLE_APPLICATION_CREDENTIALS": "c",
                  "GEMINI_API_KEY": "d", "HTTPS_PROXY": "e", "AWS_PROFILE": "f"}
        before = deepcopy(source)
        env_before = dict(os.environ)
        self.assertEqual(child_environment(source), {"HOME": "original", "PATH": "system-path"})
        self.assertEqual(source, before)
        self.assertEqual(dict(os.environ), env_before)

    def test_free_route_budget_requires_current_explicit_account_gates(self):
        profile = {"provider":"gemini", "auth_mode":"gemini_api_free", "access_route":"gemini_api_free",
                   "route_configuration_valid":True, "billing_safe":True, "model":"gemini-3.8-flash"}
        guard_budget(profile)
        for field in profile:
            with self.subTest(missing=field), self.assertRaises(BudgetGuardError):
                invalid = dict(profile)
                invalid.pop(field)
                # Missing auth_mode is not an explicit request for API access.
                if field == "auth_mode":
                    invalid["auth_mode"] = "api_key"
                guard_budget(invalid)
        for overrides in ({"provider":"codex"}, {"model":"auto"}, {"model":"gemini-pro"},
                          {"auth_mode":"GEMINI-API-FREE"},
                          {"route_configuration_valid":False}, {"billing_safe":False},
                          {"backend":"vertex"}, {"billing_mode":"pay-as-you-go"},
                          {"allow_paid_api":True}, {"purchase_credits":True},
                          {"env":{"GEMINI_API_KEY":"test-only"}}):
            with self.subTest(overrides=overrides), self.assertRaises(BudgetGuardError):
                guard_budget({**profile, **overrides})

    def test_raw_exit_codes_words_and_429_do_not_mean_quota(self):
        for code in (1, 53, 429):
            failure = classify_failure([{"type": "failed", "payload": {"error": "rate limit quota exceeded", "status": 429}}], code)
            self.assertEqual(failure["kind"], "unknown")
            self.assertFalse(failure["quota_confirmed"])
            self.assertFalse(failure["retryable"])

    def test_structured_simulated_failure_types_remain_distinct(self):
        for kind in FAILURE_KINDS:
            with self.subTest(kind=kind):
                adapter = FakeAdapter()
                events = adapter.poll(adapter.start({"simulation": {"outcome": kind}}, self.profile))
                failure = adapter.classify_failure(events)
                self.assertEqual(failure["kind"], kind)
                self.assertEqual(failure["quota_confirmed"], kind == "quota_exhausted")
                self.assertEqual(failure["retryable"], kind in {"network", "rate_limited", "overloaded"})

    def test_scripted_attempts_and_context_result_are_repeatable(self):
        adapter = FakeAdapter()
        task = {"task_id": "same", "context": {"fixture_result": {"scene": "one"}},
                "simulation": [{"outcome": "quota_exhausted"}, {"outcome": "completed", "delay_polls": 1}]}
        first = adapter.poll(adapter.start(task, self.profile))
        second_handle = adapter.start(task, {**self.profile, "profile_id": "codex-2"})
        self.assertEqual(first[-1]["failure"]["kind"], "quota_exhausted")
        self.assertEqual([e["type"] for e in adapter.poll(second_handle)], ["run_started", "progress"])
        completed = adapter.poll(second_handle)[-1]
        self.assertEqual(completed["result"], {"scene": "one"})
        self.assertTrue(completed["is_simulated"])
        self.assertEqual(completed["profile_id"], "codex-2")
        self.assertEqual(adapter.poll(second_handle), [])

    def test_fake_time_delay_requires_no_sleep_or_subprocess(self):
        now = [20.0]
        adapter = FakeAdapter(clock=lambda: now[0])
        with patch("app.ai_adapters.subprocess.run") as process:
            handle = adapter.start({"simulation": {"delay_seconds": 5}}, self.profile)
            self.assertEqual([e["type"] for e in adapter.poll(handle)], ["run_started"])
            self.assertEqual(adapter.poll(handle), [])
            now[0] += 5
            self.assertEqual(adapter.poll(handle)[-1]["type"], "completed")
        process.assert_not_called()

    def test_cancel_requires_terminal_observation_and_late_result_can_be_tested(self):
        adapter = FakeAdapter()
        ordinary = adapter.start({"simulation": {"delay_polls": 20}}, self.profile)
        self.assertEqual(adapter.cancel(ordinary)["status"], "stopping")
        self.assertEqual(adapter.poll(ordinary)[-1]["failure"]["kind"], "interrupted")
        self.assertEqual(adapter.cancel(ordinary)["status"], "stopped")
        late = adapter.start({"simulation": {"delay_polls": 1, "late_after_cancel": True, "result": {"old": True}}}, self.profile)
        self.assertEqual(adapter.cancel(late)["status"], "stopping")
        self.assertEqual(adapter.poll(late)[-1]["type"], "progress")
        self.assertEqual(adapter.poll(late)[-1]["result"], {"old": True})
        self.assertEqual(adapter.cancel("absent")["status"], "unknown")

    def test_checkpoint_is_copied_without_shared_chat_or_mutating_input(self):
        adapter = FakeAdapter()
        task = {"task_id": "scene", "context": {"fixture_result": {"version": 2}}}
        checkpoint = {"accepted_scene": "scene:v1", "result_ref": "artifacts/one.json"}
        before = deepcopy(task)
        handle = adapter.continue_from_checkpoint(task, self.profile, checkpoint=checkpoint)
        self.assertEqual(adapter._runs[handle]["task"]["context"]["checkpoint"], checkpoint)
        self.assertEqual(task, before)
        self.assertEqual(adapter.poll(handle)[-1]["result"], {"version": 2})

    def test_full_event_log_retains_sequence_and_redacts_secrets(self):
        adapter = FakeAdapter()
        task = {"task_id": "safe", "attempt_id": "attempt-1", "simulation": {
            "result": {"text": "Bearer abcdef.secret token: harmless", "api_key": "never-save",
                       "nested": {"password": "also-private", "ordinary": "useful"}}}}
        handle = adapter.start(task, self.profile, self.root / "attempt")
        events = adapter.poll(handle)
        path = self.root / "attempt" / "adapter_events.jsonl"
        content = path.read_text(encoding="utf-8")
        self.assertNotIn("never-save", content)
        self.assertNotIn("also-private", content)
        self.assertNotIn("abcdef.secret", content)
        self.assertIn("useful", content)
        self.assertEqual([json.loads(line) for line in content.splitlines()], events)
        self.assertEqual(len({e["event_id"] for e in events}), len(events))
        self.assertTrue(all(e["attempt_id"] == "attempt-1" for e in events))
        with self.assertRaises(FileExistsError):
            adapter.start(task, self.profile, self.root / "attempt")
        self.assertEqual(path.read_text(encoding="utf-8"), content)

    def test_sanitizer_handles_nested_tokens_without_dropping_useful_text(self):
        result = sanitize({"message": "api_key=abc123 sk-123456789abcd rest", "items": [{"authorization": "private"}], "count": 3})
        self.assertNotIn("abc123", result["message"])
        self.assertNotIn("sk-", result["message"])
        self.assertTrue(result["message"].endswith(" rest"))
        self.assertEqual(result["items"], [{"authorization": "[REDACTED]"}])
        self.assertEqual(result["count"], 3)


if __name__ == "__main__":
    unittest.main()
