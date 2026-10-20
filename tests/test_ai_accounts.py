from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import subprocess
import re
import shutil
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from app.ai_accounts import (AccountError, AccountManager, ORDER, isolated_environment, _stop_login_child,
                             GEMINI_SETTINGS, GEMINI_SETTINGS_LEGACY_HOOKS, LEGACY_ORDER, HISTORICAL_ORDER)


class DummyLogin:
    pid = os.getpid()

    def __init__(self):
        self.returncode = None

    def poll(self):
        return self.returncode


class AccountsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.calls = []
        self.launches = []
        self.responses = {}
        self.codex_responses = {}
        self.clients = {provider: {"command": [sys.executable], "version": "fixture-v1",
                                  "supported": True, "isolation_verified": True}
                        for provider in ("codex", "claude", "gemini")}
        self.manager = AccountManager(self.root / "workspace", self.root / "private" / "profiles",
                                      self.clients, runner=self.runner, launcher=self.launcher, codex_diagnoser=self.codex_diagnoser)

    @staticmethod
    def codex_diagnostic(identity, **changes):
        return {"authenticated": True, "auth_mode": "chatgpt_subscription", "account_type": "chatgpt",
                "identity_hash": hashlib.sha256(("codex:" + identity).encode()).hexdigest(),
                "identity_source": "codex_app_server", "identity_verified": True, "plan_type": "plus",
                "ordinary_usage_allowed": True, "rate_limits": None, "rate_limits_by_id": None,
                "credits": {"hasCredits": False, "unlimited": False, "balance": "0"},
                "checked_at": datetime.now(timezone.utc).isoformat(), "error": None, "model_requests": 0, **changes}

    def codex_diagnoser(self, command, **kwargs):
        self.calls.append((command, kwargs))
        profile_id = Path(kwargs["env"]["CODEX_HOME"]).name
        response = self.codex_responses.get(profile_id)
        if isinstance(response, Exception):
            raise response
        return deepcopy(response) if response is not None else self.codex_diagnostic(profile_id + "@example.invalid")

    def runner(self, command, **kwargs):
        self.calls.append((command, kwargs))
        env = kwargs["env"]
        home = env.get("CODEX_HOME") or env.get("CLAUDE_CONFIG_DIR")
        profile_id = Path(home).name
        if profile_id in self.responses:
            return self.responses[profile_id]
        if profile_id.startswith("codex"):
            raise AssertionError("Codex diagnostics must use the explicitly injected app-server fixture.")
        return subprocess.CompletedProcess(command, 0, json.dumps({"loggedIn": True, "authMethod": "claude.ai",
                    "email": profile_id + "@example.invalid", "configDirectory": home}), "")

    def launcher(self, command, **kwargs):
        process = DummyLogin()
        self.launches.append((command, kwargs, process))
        return process

    def finish_login(self, exit_code=0):
        self.launches[-1][2].returncode = exit_code
        request = json.loads(Path(self.launches[-1][0][-1]).read_text(encoding="utf-8"))
        Path(request["receipt"]).write_text(json.dumps({"exit_code": exit_code, "process_state": "stopped"}), encoding="utf-8")

    def confirm(self, profile_id, label=None):
        self.manager.check(profile_id)
        return self.manager.confirm(profile_id, label or profile_id + "-account", True, True,
                                    auth_confirmed=profile_id.startswith("gemini"))

    def bound_task(self, profile_id, attempt_id, **changes):
        return {"task_id": "probe", "attempt_id": attempt_id, "kind": "access_check",
                "account_binding": deepcopy(self.manager.queue_profile(profile_id)["account_binding"]), **changes}

    def test_eight_slots_order_no_auth_and_external_homes(self):
        data = self.manager.snapshot()
        self.assertEqual([p["profile_id"] for p in data["profiles"]], list(ORDER))
        self.assertEqual(list(ORDER), [f"{provider}-{slot}" for provider in ("codex", "gemini") for slot in range(1, 5)])
        self.assertEqual(data["historical_profiles"], [])
        self.assertTrue(all(not p["can_probe"] and p["state"] == "blocked" for p in data["profiles"]))
        self.assertEqual(data["wizard"]["current"], "codex-1")
        self.assertFalse(self.manager.profile_root.exists())

    def test_environment_allowlist_no_mutation_or_cross_account_defaults(self):
        source = {"PATH": "safe", "SystemRoot": "windows", "OPENAI_API_KEY": "bad", "GEMINI_API_KEY": "bad",
                  "ANTHROPIC_AUTH_TOKEN": "bad", "GOOGLE_CLOUD_PROJECT": "bad", "CODEX_HOME": "active",
                  "CLAUDE_CONFIG_DIR": "active", "GEMINI_FORCE_ENCRYPTED_FILE_STORAGE": "true",
                  "NODE_OPTIONS": "--require hostile", "HTTPS_PROXY": "bad", "USERPROFILE": "original"}
        before = deepcopy(source)
        env = isolated_environment("gemini", self.root / "gemini-2", source)
        self.assertEqual(source, before)
        self.assertEqual(set(env), {"PATH", "SystemRoot", "HOME", "USERPROFILE", "GEMINI_CLI_HOME", "GEMINI_TELEMETRY_ENABLED"})
        self.assertEqual(Path(env["GEMINI_CLI_HOME"]).resolve(), (self.root / "gemini-2").resolve())

    def test_missing_client_does_not_launch_and_wizard_can_skip_resume(self):
        self.manager.update_clients({})
        snapshot = self.manager.start_wizard()
        self.assertEqual(snapshot["wizard"]["status"], "blocked")
        self.assertEqual(self.launches, [])
        self.manager.skip_profile("Installation unavailable")
        again = AccountManager(self.root / "workspace", self.root / "private" / "profiles")
        self.assertEqual(again.snapshot()["wizard"]["current"], "codex-2")
        self.assertEqual(again.snapshot()["wizard"]["skipped"]["codex-1"]["reason"], "Installation unavailable")

    def test_single_visible_login_no_automatic_next(self):
        self.manager.start_wizard()
        self.assertEqual(len(self.launches), 1)
        request = json.loads(Path(self.launches[0][0][-1]).read_text(encoding="utf-8"))
        self.assertEqual(request["profile_id"], "codex-1")
        self.assertEqual(request["command"][-1], "login")
        with self.assertRaisesRegex(AccountError, "уже открытое"):
            self.manager.connect("codex-2")
        self.manager.pause_wizard()
        self.manager.resume_wizard()
        self.assertEqual(len(self.launches), 1)
        self.finish_login()
        self.manager.check("codex-1")
        profile = self.manager.confirm("codex-1", "first@example.invalid", True, True)
        self.assertTrue(profile["can_probe"])
        self.assertEqual(self.manager.snapshot()["wizard"]["current"], "codex-1")
        self.assertEqual(self.manager.snapshot()["wizard"]["status"], "awaiting_access")
        self.manager.resume_wizard()
        self.assertEqual(len(self.launches), 1)
        self.manager.observe_result("codex-1", self.bound_task("codex-1", "probe1"),
                                    {"simulated": False, "path": "completed.json", "sha256": "a" * 64}, None)
        self.assertEqual(self.manager.snapshot()["wizard"]["current"], "codex-2")
        self.manager.resume_wizard()
        self.assertEqual(len(self.launches), 2)

    def test_restart_does_not_relaunch_active_login(self):
        self.manager.start_wizard()
        again = AccountManager(self.root / "workspace", self.root / "private" / "profiles",
                               runner=self.runner, launcher=self.launcher)
        again.resume_wizard()
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(again.snapshot()["wizard"]["status"], "awaiting_login")

    def test_prepare_all_profiles_and_pause_access_does_not_open_next(self):
        self.manager.prepare_profiles()
        self.assertTrue(all(Path(p["profile_ref"]).is_dir() for p in self.manager.snapshot()["profiles"]))
        self.confirm("codex-1")
        self.manager.pause_wizard()
        self.manager.resume_wizard()
        self.assertEqual(self.manager.snapshot()["wizard"]["status"], "awaiting_access")
        self.assertEqual(self.launches, [])

    def test_finished_login_restart_waits_confirmation(self):
        self.manager.start_wizard()
        state = json.loads(self.manager.state_path.read_text(encoding="utf-8"))
        Path(state["wizard"]["active_login"]["receipt"]).write_text('{"exit_code": 0}', encoding="utf-8")
        again = AccountManager(self.root / "workspace", self.root / "private" / "profiles", runner=self.runner, launcher=self.launcher)
        again.resume_wizard()
        self.assertEqual(len(self.launches), 1)
        self.assertEqual(again.snapshot()["wizard"]["status"], "awaiting_confirmation")

    def test_authentication_does_not_imply_access_or_credit_safety(self):
        checked = self.manager.check("codex-1")
        self.assertEqual(checked["auth_state"], "authenticated")
        self.assertFalse(checked["can_probe"])
        self.assertEqual(checked["access_state"], "unverified")
        profile = self.manager.confirm("codex-1", "first", True, False)
        self.assertFalse(profile["can_probe"])
        profile = self.manager.confirm("codex-1", "first", True, True)
        self.assertTrue(profile["can_probe"])
        self.assertTrue(profile["identity_verified"])
        self.assertEqual(profile["identity_verification"], "verified")
        with self.assertRaises(AccountError):
            self.manager.launch_spec("codex-1")
        spec = self.manager.launch_spec("codex-1", require_access=False)
        self.assertEqual(spec["profile"]["client_version"], "fixture-v1")

    def test_codex_identity_hash_from_official_status_only(self):
        profile = self.confirm("codex-1", "writer")
        self.assertTrue(profile["identity_verified"])
        self.assertEqual(profile["identity_source"], "codex_app_server")
        persisted = self.manager.state_path.read_text(encoding="utf-8")
        self.assertNotIn("codex-1@example.invalid", persisted)
        self.assertEqual(profile["account_label"], "writer")

    def test_duplicate_official_identity_cannot_be_another_access(self):
        self.confirm("codex-1")
        self.codex_responses["codex-2"] = self.codex_diagnostic("codex-1@example.invalid")
        profile = self.confirm("codex-2", "another display name")
        self.assertEqual(profile["duplicate_of"], "codex-1")
        self.assertFalse(profile["can_probe"])

    def test_duplicate_user_identity_is_labeled_and_blocked(self):
        self.confirm("gemini-1", "user@example.invalid")
        profile = self.confirm("gemini-2", "USER@example.invalid")
        self.assertEqual(profile["duplicate_of"], "gemini-1")
        self.assertFalse(profile["identity_verified"])

    def test_gemini_never_invents_auth_command_or_reads_tokens(self):
        self.manager.check("gemini-1")
        self.assertEqual(self.calls, [])
        with self.assertRaises(AccountError):
            self.manager.confirm("gemini-1", "google account", True, True)
        profile = self.manager.confirm("gemini-1", "google account", True, True, auth_confirmed=True)
        self.assertTrue(profile["can_probe"])
        self.assertFalse(profile["identity_verified"])
        self.assertEqual(profile["auth_source"], "official_ui_user_confirmation")
        self.assertTrue((self.manager.profile_root / "gemini-1" / ".gemini" / "settings.json").exists())
        self.assertFalse((self.manager.profile_root / "gemini-1" / "settings.json").exists())

    def test_api_status_or_configuration_injection_blocks_without_overwriting(self):
        self.codex_responses["codex-1"] = self.codex_diagnostic("unused", authenticated=False, account_type="apiKey",
            auth_mode="unknown", identity_verified=False, identity_hash=None)
        profile = self.manager.check("codex-1")
        self.assertEqual(profile["reason_code"], "billing")
        self.assertNotIn("sk-test", self.manager.state_path.read_text())
        self.confirm("gemini-1")
        path = self.manager.profile_root / "gemini-1" / ".gemini" / "settings.json"
        text = path.read_text()
        path.write_text(text.replace("oauth-personal", "gemini-api-key"))
        with self.assertRaises(AccountError):
            self.manager.launch_spec("gemini-1", False)
        self.assertIn("gemini-api-key", path.read_text())

    def test_env_and_project_config_injections_fail_closed(self):
        self.confirm("gemini-1")
        path = self.manager.profile_root / "gemini-1" / ".gemini" / ".env"
        path.write_text("GEMINI_API_KEY=secret")
        self.assertFalse(self.manager.queue_profile("gemini-1")["can_probe"])
        self.assertNotIn("secret", self.manager.state_path.read_text())

    def test_access_verified_only_after_accepted_real_probe_observer(self):
        self.confirm("codex-1")
        task = self.bound_task("codex-1", "attempt1")
        result = {"simulated": False, "path": "ai/attempts/attempt1/completed.json", "sha256": "a" * 64}
        with self.assertRaises(AccountError):
            self.manager.observe_result("codex-1", task, {**result, "simulated": True}, None)
        self.manager.observe_result("codex-1", task, result, None)
        self.assertEqual(self.manager.queue_profile("codex-1")["state"], "ready")
        generation = self.manager.snapshot()["generation"]
        self.manager.observe_result("codex-1", task, result, None)
        self.assertEqual(self.manager.snapshot()["generation"], generation)
        self.manager.observe_result("codex-1", self.bound_task("codex-1", "attempt2"), None,
                                    {"kind": "quota_exhausted", "message": "Quota reached"})
        self.assertEqual(self.manager.queue_profile("codex-1")["state"], "blocked")
        self.assertEqual(self.manager.queue_profile("codex-1")["reason_code"], "quota_exhausted")

    def test_recheck_ready_account_does_not_repeat_billing_confirmation(self):
        self.confirm("codex-1")
        self.manager.observe_result(
            "codex-1", self.bound_task("codex-1", "verified-before-recheck"),
            {"simulated": False, "path": "completed.json", "sha256": "a" * 64}, None,
        )
        checked = self.manager.check("codex-1")
        self.assertEqual(checked["state"], "ready")
        self.assertTrue(checked["billing_confirmed"])
        self.assertIsNone(checked["reason_code"])
        self.assertIsNone(checked["reason"])

    def test_billing_attestation_expiry_blocks_launch(self):
        self.confirm("codex-1")
        state = json.loads(self.manager.state_path.read_text(encoding="utf-8"))
        state["profiles"][0]["billing_confirmation_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.manager.state_path.write_text(json.dumps(state), encoding="utf-8")
        profile = self.manager.queue_profile("codex-1")
        self.assertFalse(profile["can_probe"])
        self.assertEqual(profile["reason_code"], "billing_confirmation_expired")

    def test_expired_billing_renewal_preserves_verified_access_without_reprobe(self):
        self.confirm("codex-1")
        self.manager.observe_result("codex-1", self.bound_task("codex-1", "verified-for-renewal"),
                                    {"simulated": False, "path": "completed.json", "sha256": "a" * 64}, None)
        before = self.manager.queue_profile("codex-1")
        wizard_before = self.manager.snapshot()["wizard"]
        state = json.loads(self.manager.state_path.read_text(encoding="utf-8"))
        state["profiles"][0]["billing_confirmation_expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.manager.state_path.write_text(json.dumps(state), encoding="utf-8")
        self.assertFalse(self.manager.queue_profile("codex-1")["can_probe"])
        with self.assertRaises(AccountError):
            self.manager.launch_spec("codex-1")
        declined = self.manager.confirm("codex-1", billing_confirmed=False)
        self.assertFalse(declined["can_probe"])
        self.assertFalse(declined["billing_confirmed"])
        renewed = self.manager.confirm("codex-1", billing_confirmed=True)
        self.assertEqual(renewed["state"], "ready")
        self.assertEqual(renewed["access_state"], "verified")
        self.assertTrue(renewed["can_probe"])
        self.assertEqual(renewed["account_binding"], before["account_binding"])
        self.assertEqual(renewed["access_evidence"], before["access_evidence"])
        self.assertEqual(renewed["last_successful_run"], before["last_successful_run"])
        self.assertEqual(self.manager.snapshot()["wizard"], wizard_before)
        self.assertEqual(self.launches, [])

    def test_creative_validation_error_preserves_verified_access_and_records_task_error(self):
        self.confirm("codex-1")
        self.manager.observe_result("codex-1", self.bound_task("codex-1", "access-before-content-error"),
                                    {"simulated": False, "path": "completed.json", "sha256": "a" * 64}, None)
        before = self.manager.queue_profile("codex-1")
        wizard_before = self.manager.snapshot()["wizard"]
        task = self.bound_task("codex-1", "creative-error", kind="live_director")
        failure = {"kind": "invalid_input", "message": "Edit plan does not match the accepted schema"}
        self.manager.observe_result("codex-1", task, None, failure)
        after = self.manager.queue_profile("codex-1")
        for field in ("access_state", "access_evidence", "account_binding", "auth_state", "billing_confirmed", "state", "reason_code", "last_successful_run"):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(after["last_task_error"]["kind"], "invalid_input")
        self.assertEqual(after["last_task_error"]["attempt_id"], "creative-error")
        self.assertEqual(self.manager.snapshot()["wizard"], wizard_before)
        generation = self.manager.snapshot()["generation"]
        self.manager.observe_result("codex-1", task, None, failure)
        self.assertEqual(self.manager.snapshot()["generation"], generation)

    def test_content_failure_does_not_grant_access_and_probe_failures_still_revoke(self):
        self.confirm("codex-1")
        failure = {"kind": "invalid_input", "message": "Invalid result"}
        self.manager.observe_result("codex-1", self.bound_task("codex-1", "unverified-content", kind="live_director"), None, failure)
        self.assertEqual(self.manager.queue_profile("codex-1")["access_state"], "unverified")
        self.manager.observe_result("codex-1", self.bound_task("codex-1", "access-valid"),
                                    {"simulated": False, "path": "completed.json", "sha256": "a" * 64}, None)
        self.manager.observe_result("codex-1", self.bound_task("codex-1", "access-invalid"), None, failure)
        self.assertEqual(self.manager.queue_profile("codex-1")["access_state"], "unavailable")
        self.assertEqual(self.manager.queue_profile("codex-1")["reason_code"], "invalid_input")

    def test_renewal_after_reconnect_or_identity_change_still_requires_access_probe(self):
        self.confirm("codex-1")
        self.manager.observe_result("codex-1", self.bound_task("codex-1", "verified-before-reconnect"),
                                    {"simulated": False, "path": "completed.json", "sha256": "a" * 64}, None)
        original_binding = self.manager.queue_profile("codex-1")["account_binding"]
        self.manager.connect("codex-1", reconnect=True)
        self.finish_login()
        self.manager.check("codex-1")
        reconnected = self.manager.confirm("codex-1", billing_confirmed=True)
        self.assertEqual(reconnected["access_state"], "unverified")
        self.assertIsNone(reconnected["access_evidence"])
        self.assertGreater(reconnected["auth_generation"], original_binding["auth_generation"])
        self.manager.observe_result("codex-1", self.bound_task("codex-1", "verified-before-identity-change"),
                                    {"simulated": False, "path": "new.json", "sha256": "b" * 64}, None)
        self.codex_responses["codex-1"] = self.codex_diagnostic("replacement@example.invalid")
        changed = self.manager.confirm("codex-1", billing_confirmed=True)
        self.assertFalse(changed["billing_confirmed"])
        self.assertFalse(changed["can_probe"])
        renewed = self.manager.confirm("codex-1", billing_confirmed=True)
        self.assertTrue(renewed["can_probe"])
        self.assertEqual(renewed["access_state"], "unverified")
        self.assertIsNone(renewed["access_evidence"])

    def test_inactive_claude_operations_do_not_start_clients(self):
        for operation in (self.manager.check, self.manager.connect, self.manager.confirm,
                          self.manager.queue_profile, self.manager.launch_spec,
                          self.manager.pause_profile, self.manager.resume_profile):
            for profile_id in HISTORICAL_ORDER:
                with self.subTest(operation=operation.__name__, profile=profile_id):
                    with self.assertRaises(AccountError) as error:
                        operation(profile_id)
                    self.assertEqual(error.exception.code, "profile_inactive")
        self.assertEqual(self.calls, [])
        self.assertEqual(self.launches, [])
        self.assertFalse(self.manager.profile_root.exists())

    def test_failed_gemini_reconnect_requires_new_auth_confirmation(self):
        self.confirm("gemini-1")
        self.manager.connect("gemini-1", reconnect=True)
        self.finish_login(1)
        checked = self.manager.check("gemini-1")
        self.assertNotEqual(checked["auth_state"], "authenticated")
        self.assertIsNone(checked["auth_source"])
        with self.assertRaises(AccountError):
            self.manager.confirm("gemini-1", "same account", True, True, auth_confirmed=False)

    def test_ancestor_gemini_env_rejected_without_reading_secret(self):
        path = self.root / ".gemini" / ".env"
        path.parent.mkdir()
        path.write_text("SECRET=never inspect")
        result = self.manager.check("gemini-1")
        self.assertEqual(result["reason_code"], "billing_unverified")
        self.assertNotIn("never inspect", self.manager.state_path.read_text())

    def test_peer_identity_change_blocks_both_homes(self):
        self.confirm("codex-1")
        self.codex_responses["codex-1"] = self.codex_diagnostic("unexpected@example.invalid")
        profile = self.confirm("codex-2")
        self.assertFalse(profile["can_probe"])
        self.assertEqual(profile["reason_code"], "isolation_failed")
        self.assertFalse(self.manager.queue_profile("codex-1")["can_probe"])

    def test_dead_wrapper_without_receipt_keeps_single_login_reservation(self):
        self.manager.start_wizard()
        self.launches[0][2].returncode = 130
        with self.assertRaises(AccountError):
            self.manager.connect("codex-2")

    def test_interrupted_owned_child_is_terminated_and_waited(self):
        class Child:
            pid = 123456
            stopped = False
            def poll(self): return 0 if self.stopped else None
            def terminate(self): self.stopped = True
            def wait(self, timeout): self.stopped = True; return 0
        child = Child()
        with patch("app.ai_accounts.subprocess.run") as run:
            self.assertTrue(_stop_login_child(child))
            if os.name == "nt":
                self.assertEqual(run.call_args.args[0][-4:], ["/PID", "123456", "/T", "/F"])

    def test_redirect_below_profile_root_rejected_before_writes(self):
        home = self.manager.profile_root / "codex-1"
        other = self.root / "outside"
        other.mkdir()
        original = Path.resolve
        def resolve(path, *args, **kwargs):
            if path == home or home in path.parents:
                return other / path.relative_to(home)
            return original(path, *args, **kwargs)
        with patch.object(Path, "resolve", resolve):
            with self.assertRaises(AccountError):
                self.manager._prepare("codex-1")
        self.assertEqual(list(other.iterdir()), [])

    def test_saved_and_current_profile_root_aliases_compare_canonical(self):
        self.manager.prepare_profiles()
        alias = self.root / "msix-alias"
        state = json.loads(self.manager.state_path.read_text(encoding="utf-8"))
        state["profile_root"] = str(alias)
        self.manager.state_path.write_text(json.dumps(state), encoding="utf-8")
        original = Path.resolve
        physical = self.manager.profile_root
        def resolve(path, *args, **kwargs):
            return physical if path == alias else original(path, *args, **kwargs)
        with patch.object(Path, "resolve", resolve):
            again = AccountManager(self.root / "workspace", physical)
        self.assertEqual(again.profile_root, physical)

    def test_codex_official_identity_deduplicates_despite_different_labels(self):
        self.confirm("codex-1", "director")
        self.codex_responses["codex-2"] = self.codex_diagnostic("codex-1@example.invalid")
        profile = self.confirm("codex-2", "different display name")
        self.assertTrue(profile["identity_verified"])
        self.assertEqual(profile["duplicate_of"], "codex-1")
        self.assertFalse(profile["can_probe"])
        self.assertNotIn("codex-1@example.invalid", self.manager.state_path.read_text())

    def test_official_identity_confirmation_needs_no_manual_identity_fields(self):
        self.manager.check("codex-1")
        profile = self.manager.confirm("codex-1", billing_confirmed=True)
        self.assertTrue(profile["can_probe"])
        self.assertEqual(profile["account_label"], "GPT-1")
        self.assertTrue(profile["distinct_account"])

    def test_codex_identity_change_invalidates_prior_access_and_billing(self):
        self.confirm("codex-1")
        self.manager.observe_result("codex-1", self.bound_task("codex-1", "one"),
                                    {"simulated": False, "path": "completed.json", "sha256": "b" * 64}, None)
        self.codex_responses["codex-1"] = self.codex_diagnostic("changed@example.invalid")
        profile = self.manager.check("codex-1")
        self.assertEqual(profile["access_state"], "unverified")
        self.assertFalse(profile["billing_confirmed"])
        self.assertFalse(profile["can_probe"])
        self.assertIsNone(profile["billing_confirmation_expires_at"])
        self.assertIsNone(profile["billing_identity_hash"])
        self.assertIsNone(profile["access_evidence"])

    def test_identity_change_during_confirm_rejects_stale_owner_confirmation(self):
        self.confirm("codex-1")
        self.codex_responses["codex-1"] = self.codex_diagnostic("changed@example.invalid")
        profile = self.manager.confirm("codex-1", billing_confirmed=True)
        self.assertFalse(profile["billing_confirmed"])
        self.assertEqual(profile["reason_code"], "identity_changed")
        self.assertFalse(profile["can_probe"])
        self.assertTrue(self.manager.confirm("codex-1", billing_confirmed=True)["can_probe"])

    def test_codex_diagnostic_failure_never_falls_back_to_manual_identity(self):
        self.confirm("codex-1")
        self.codex_responses["codex-1"] = subprocess.TimeoutExpired("official diagnostic", 20)
        checked = self.manager.check("codex-1")
        self.assertFalse(checked["identity_verified"])
        self.assertFalse(checked["can_probe"])
        self.assertEqual(checked["reason_code"], "diagnostic_unavailable")
        with self.assertRaises(AccountError):
            self.manager.confirm("codex-1", "manual fallback", True, True, auth_confirmed=True)

    def test_codex_zero_balance_is_observation_not_billing_confirmation(self):
        profile = self.manager.check("codex-1")
        self.assertEqual(profile["codex_diagnostics"]["credits"], {"has_credits": False, "unlimited": False, "balance": "0"})
        self.assertFalse(profile["billing_confirmed"])
        self.assertFalse(profile["can_probe"])
        self.codex_responses["codex-1"] = self.codex_diagnostic("codex-1@example.invalid", credits=None,
            ordinary_usage_allowed=None, error={"code": "quota_unavailable", "reason": "Quota unavailable"})
        checked = self.manager.check("codex-1")
        self.assertIsNone(checked["codex_diagnostics"]["credits"])
        self.assertIsNone(checked["codex_diagnostics"]["ordinary_usage_allowed"])
        self.assertFalse(checked["can_probe"])

    def test_ordinary_usage_false_does_not_claim_quota_exhaustion(self):
        self.codex_responses["codex-1"] = self.codex_diagnostic("codex-1@example.invalid", ordinary_usage_allowed=False)
        checked = self.manager.check("codex-1")
        self.assertEqual(checked["reason_code"], "usage_unavailable")
        profile = self.manager.confirm("codex-1", billing_confirmed=True)
        self.assertFalse(profile["can_probe"])
        self.assertEqual(profile["reason_code"], "usage_unavailable")

    def test_late_probe_preserves_foreign_login_reservation(self):
        self.confirm("codex-1")
        self.manager.connect("gemini-1")
        self.manager.observe_result("codex-1", self.bound_task("codex-1", "late"),
                                    {"simulated": False, "path": "completed.json", "sha256": "a" * 64}, None)
        wizard = self.manager.snapshot()["wizard"]
        self.assertEqual(wizard["current"], "gemini-1")
        self.assertEqual(wizard["active_login"]["profile_id"], "gemini-1")
        with self.assertRaises(AccountError):
            self.manager.connect("codex-1")

    def test_probe_completion_preserves_wizard_pause(self):
        self.confirm("codex-1")
        self.manager.pause_wizard()
        self.manager.observe_result("codex-1", self.bound_task("codex-1", "paused"),
                                    {"simulated": False, "path": "completed.json", "sha256": "a" * 64}, None)
        wizard = self.manager.snapshot()["wizard"]
        self.assertEqual(wizard["status"], "paused")
        self.assertEqual(wizard["current"], "codex-2")
        self.assertEqual(wizard["paused_from"], "awaiting_next")

    def test_last_verified_result_completes_wizard_with_existing_skips(self):
        for slot in (1, 2, 3, 4):
            self.manager.skip_profile("Provider unavailable", f"gemini-{slot}")
        for slot in (1, 2, 3, 4):
            profile_id = f"codex-{slot}"
            self.confirm(profile_id)
            self.manager.observe_result(
                profile_id, self.bound_task(profile_id, f"verified-{slot}"),
                {"simulated": False, "path": f"completed-{slot}.json", "sha256": "a" * 64}, None,
            )
        wizard = self.manager.snapshot()["wizard"]
        self.assertEqual(wizard["status"], "complete_with_skips")
        self.assertIsNone(wizard["current"])
        self.assertEqual(wizard["completed"], ["codex-1", "codex-2", "codex-3", "codex-4"])
        self.assertEqual(len(wizard["skipped"]), 4)

    def test_stale_success_and_failure_acknowledged_after_identity_change(self):
        self.confirm("codex-1")
        stale_success = self.bound_task("codex-1", "old-success")
        stale_failure = self.bound_task("codex-1", "old-failure")
        self.codex_responses["codex-1"] = self.codex_diagnostic("new@example.invalid")
        self.manager.check("codex-1")
        self.manager.confirm("codex-1", billing_confirmed=True)
        before = self.manager.queue_profile("codex-1")
        wizard = self.manager.snapshot()["wizard"]
        self.manager.observe_result("codex-1", stale_success,
            {"simulated": False, "path": "completed.json", "sha256": "a" * 64}, None)
        self.manager.observe_result("codex-1", stale_failure, None, {"kind": "billing", "message": "Old account error"})
        after = self.manager.queue_profile("codex-1")
        for field in ("access_state", "billing_confirmed", "auth_state", "can_probe", "identity_hash", "account_binding", "reason_code"):
            self.assertEqual(after[field], before[field], field)
        self.assertEqual(self.manager.snapshot()["wizard"], wizard)
        self.assertEqual(len(after["ignored_result_evidence"]), 2)
        self.assertTrue(all(item["reason"] == "account_binding_changed" for item in after["ignored_result_evidence"]))
        generation = self.manager.snapshot()["generation"]
        self.manager.observe_result("codex-1", stale_failure, None, {"kind": "billing", "message": "Retry"})
        self.assertEqual(self.manager.snapshot()["generation"], generation)

    def test_reconnect_same_identity_changes_generation_and_discards_old_probe(self):
        self.confirm("codex-1")
        stale = self.bound_task("codex-1", "before-reconnect")
        original = stale["account_binding"]
        self.manager.connect("codex-1", reconnect=True)
        self.finish_login()
        self.manager.check("codex-1")
        self.manager.confirm("codex-1", billing_confirmed=True)
        current = self.manager.queue_profile("codex-1")["account_binding"]
        self.assertEqual(current["identity_hash"], original["identity_hash"])
        self.assertGreater(current["auth_generation"], original["auth_generation"])
        self.manager.observe_result("codex-1", stale, {"simulated": False, "path": "old.json", "sha256": "a" * 64}, None)
        self.assertEqual(self.manager.queue_profile("codex-1")["access_state"], "unverified")
        again = AccountManager(self.root / "workspace", self.root / "private" / "profiles",
                               codex_diagnoser=self.codex_diagnoser)
        self.assertEqual(again.queue_profile("codex-1")["account_binding"], current)

    def test_legacy_result_without_account_binding_is_acknowledged_not_applied(self):
        self.confirm("codex-1")
        task = {"task_id": "legacy", "attempt_id": "old-unbound", "kind": "access_check"}
        self.manager.observe_result("codex-1", task, {"simulated": False, "path": "old.json", "sha256": "a" * 64}, None)
        profile = self.manager.queue_profile("codex-1")
        self.assertEqual(profile["access_state"], "unverified")
        self.assertEqual(profile["ignored_result_evidence"][-1]["reason"], "account_binding_missing")
        self.assertIn("old-unbound", profile["observed_attempts"])

    def test_gemini_settings_migration_is_explicit_exact_and_preserves_unknown_keys(self):
        self.manager.prepare_profiles()
        settings = self.manager.profile_root / "gemini-1" / ".gemini" / "settings.json"
        old = json.dumps(GEMINI_SETTINGS_LEGACY_HOOKS, indent=2) + "\n"
        settings.write_text(old, encoding="utf-8")
        result = self.manager.migrate_gemini_settings("gemini-1")
        self.assertEqual(result["status"], "would_migrate")
        self.assertEqual(settings.read_text(encoding="utf-8"), old)
        result = self.manager.migrate_gemini_settings("gemini-1", apply=True)
        self.assertEqual(result["status"], "migrated")
        self.assertEqual(Path(result["backup_path"]).read_text(encoding="utf-8"), old)
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), GEMINI_SETTINGS)
        self.assertEqual(self.manager.migrate_gemini_settings("gemini-1", apply=True)["status"], "unchanged")
        changed = {**GEMINI_SETTINGS_LEGACY_HOOKS, "user_setting": "preserve this"}
        settings.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaises(AccountError):
            self.manager.migrate_gemini_settings("gemini-1", apply=True)
        self.assertEqual(json.loads(settings.read_text(encoding="utf-8")), changed)

    def test_skip_finished_login_does_not_leave_a_waiting_or_authenticated_claim(self):
        self.manager.connect("gemini-1")
        self.finish_login(1)
        self.manager.skip_profile("Вход не завершён", "gemini-1")
        profile = self.manager.queue_profile("gemini-1")
        self.assertEqual(profile["auth_state"], "not_authenticated")
        self.assertEqual(profile["reason_code"], "skipped")
        self.assertFalse(profile["can_probe"])

    def test_explicitly_unsupported_access_route_blocks_without_installation_or_auth_claims(self):
        self.confirm("gemini-1")
        before = self.manager.queue_profile("gemini-1")
        self.assertTrue(before["can_probe"])
        clients = deepcopy(self.clients)
        clients["gemini"].update(access_route_supported=False, access_route_reason="Google sign-in route retired",
                                 access_route_source="https://developers.google.com/gemini-code-assist/docs/deprecations/code-assist-individuals")
        self.manager.update_clients(clients)
        profile = self.manager.queue_profile("gemini-1")
        self.assertFalse(profile["can_probe"])
        self.assertEqual(profile["auth_state"], before["auth_state"])
        self.assertEqual(profile["install_state"], "verified")
        self.assertEqual(profile["access_state"], "unavailable")
        self.assertEqual(profile["reason_code"], "unsupported_client")
        with self.assertRaises(AccountError):
            self.manager.launch_spec("gemini-1", require_access=False)
        for profile_id in ("gemini-1", "gemini-2", "gemini-3"):
            self.assertEqual(self.manager.connect(profile_id)["reason_code"], "unsupported_client")
        self.assertEqual(self.launches, [])

    def legacy_state(self):
        state = json.loads(self.manager.state_path.read_text(encoding="utf-8"))
        state.update(schema_version=1, generation=17,
                     profiles=[self.manager._new_profile(item) for item in LEGACY_ORDER])
        state.pop("historical_profiles", None)
        state["wizard"].update(status="complete_with_skips", current=None,
                              completed=["codex-1", "codex-2", "codex-3"],
                              skipped={item: {"reason": "Historical blocker", "at": "before"}
                                       for item in LEGACY_ORDER if not item.startswith("codex")})
        for profile in state["profiles"]:
            if profile["provider"] == "codex":
                profile.update(auth_state="authenticated", auth_generation=3, access_state="verified",
                               identity_hash=hashlib.sha256(profile["profile_id"].encode()).hexdigest(),
                               access_evidence={"task_id": profile["profile_id"] + "-probe", "sha256": "b" * 64},
                               observed_attempts=[profile["profile_id"] + "-attempt"])
        self.manager.state_path.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
        return state

    def reopen(self, **kwargs):
        return AccountManager(self.root / "workspace", self.root / "private" / "profiles", **kwargs)

    def test_legacy_migration_preserves_auth_evidence_history_and_is_idempotent(self):
        legacy = self.legacy_state()
        original = self.manager.state_path.read_bytes()
        credential = self.manager.profile_root / "codex-1" / "auth.json"
        credential.parent.mkdir(parents=True)
        credential.write_text("credential-fixture-never-copy", encoding="utf-8")
        again = self.reopen()
        state = json.loads(again.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state["schema_version"], 2)
        self.assertEqual(state["generation"], 18)
        self.assertEqual([p["profile_id"] for p in state["profiles"]], list(ORDER))
        old_profiles = {p["profile_id"]: p for p in legacy["profiles"]}
        for profile in state["profiles"]:
            if profile["profile_id"] in old_profiles:
                self.assertEqual(profile, old_profiles[profile["profile_id"]])
        self.assertEqual(state["historical_profiles"], [old_profiles[item] for item in HISTORICAL_ORDER])
        self.assertEqual(state["wizard"]["current"], "codex-4")
        self.assertEqual(set(state["wizard"]["historical_skipped"]), set(HISTORICAL_ORDER))
        self.assertFalse(set(state["wizard"]["skipped"]) & set(HISTORICAL_ORDER))
        self.assertEqual(state["profile_migration"]["previous_wizard"], legacy["wizard"])
        self.assertEqual(Path(state["profile_migration"]["backup_path"]).read_bytes(), original)
        self.assertEqual(credential.read_text(), "credential-fixture-never-copy")
        self.assertNotIn("credential-fixture-never-copy", again.state_path.read_text())
        saved = again.state_path.read_bytes()
        self.reopen().snapshot()
        self.assertEqual(again.state_path.read_bytes(), saved)
        self.assertEqual(len(list(again.state_path.parent.glob("accounts.v1.*.backup.json"))), 1)
        snapshot = again.snapshot()
        self.assertEqual(len(snapshot["profiles"]), 8)
        self.assertEqual(len(snapshot["historical_profiles"]), 3)
        self.assertTrue(all(p["state"] == "inactive" and not p["can_probe"] for p in snapshot["historical_profiles"]))
        self.assertFalse((again.profile_root / "claude-1").exists())
        self.assertEqual(self.calls, [])
        self.assertEqual(self.launches, [])

    def test_migration_refuses_active_or_unverified_login_without_writes(self):
        legacy = self.legacy_state()
        legacy["wizard"]["active_login"] = {"id": "old", "profile_id": "claude-1", "pid": None,
                                               "receipt": str(self.root / "missing.done.json")}
        self.manager.state_path.write_text(json.dumps(legacy), encoding="utf-8")
        before = self.manager.state_path.read_bytes()
        with self.assertRaises(AccountError) as error:
            self.reopen()
        self.assertEqual(error.exception.code, "login_active")
        self.assertEqual(self.manager.state_path.read_bytes(), before)
        self.assertEqual(list(self.manager.state_path.parent.glob("*.backup.json")), [])

    def test_migration_refuses_running_or_stopping_tasks_and_live_coordinator(self):
        from app.development import locked
        self.legacy_state()
        before = self.manager.state_path.read_bytes()
        connection = sqlite3.connect(self.manager.workspace / "registry.sqlite")
        self.addCleanup(connection.close)
        connection.execute("CREATE TABLE ai_attempts (status TEXT)")
        for status in ("running", "stopping"):
            connection.execute("DELETE FROM ai_attempts")
            connection.execute("INSERT INTO ai_attempts VALUES (?)", (status,))
            connection.commit()
            with self.assertRaises(AccountError) as error:
                self.reopen()
            self.assertEqual(error.exception.code, "migration_busy")
            self.assertEqual(self.manager.state_path.read_bytes(), before)
        connection.execute("DELETE FROM ai_attempts")
        connection.commit()
        with locked(self.manager.workspace / "ai" / "coordinator.lock"):
            with self.assertRaises(AccountError) as error:
                self.reopen()
            self.assertEqual(error.exception.code, "migration_busy")
        self.reopen()
        self.assertEqual(json.loads(self.manager.state_path.read_text())["schema_version"], 2)

    def test_duplicate_registry_ids_refused_without_reset(self):
        state = self.legacy_state()
        state["profiles"].append(deepcopy(state["profiles"][0]))
        self.manager.state_path.write_text(json.dumps(state), encoding="utf-8")
        before = self.manager.state_path.read_bytes()
        with self.assertRaises(AccountError) as error:
            self.reopen()
        self.assertEqual(error.exception.code, "state_invalid")
        self.assertEqual(self.manager.state_path.read_bytes(), before)

    def test_pause_blocks_new_launches_preserves_auth_and_accepts_inflight_result(self):
        self.confirm("codex-1")
        task = self.bound_task("codex-1", "already-running")
        before = self.manager.queue_profile("codex-1")
        paused = self.manager.pause_profile("codex-1")
        self.assertTrue(paused["paused"])
        self.assertEqual(paused["state"], "paused")
        for field in ("auth_state", "identity_hash", "auth_generation", "access_state"):
            self.assertEqual(paused[field], before[field])
        calls = len(self.calls)
        for action in (self.manager.connect, self.manager.launch_spec):
            with self.assertRaises(AccountError) as error:
                action("codex-1")
            self.assertEqual(error.exception.code, "profile_paused")
        self.assertEqual(len(self.calls), calls)
        self.manager.observe_result("codex-1", task, {"simulated": False, "path": "done.json", "sha256": "c" * 64}, None)
        self.assertEqual(self.manager.queue_profile("codex-1")["access_state"], "verified")
        self.assertEqual(self.manager.queue_profile("codex-1")["state"], "paused")
        again = self.reopen(codex_diagnoser=self.codex_diagnoser)
        self.assertTrue(again.queue_profile("codex-1")["paused"])
        resumed = again.resume_profile("codex-1")
        self.assertFalse(resumed["paused"])
        self.assertEqual(resumed["state"], "ready")
        self.assertEqual(self.launches, [])

    def test_all_paused_profiles_do_not_complete_wizard_or_launch(self):
        for item in ORDER:
            self.manager.pause_profile(item)
        snapshot = self.manager.resume_wizard()
        self.assertIsNone(snapshot["wizard"]["current"])
        self.assertEqual(snapshot["wizard"]["status"], "waiting_profiles")
        self.assertEqual(self.launches, [])
        self.manager.resume_profile("codex-4")
        self.assertEqual(self.manager.snapshot()["wizard"]["current"], "codex-4")

    def test_prepare_fourth_homes_and_preserve_historical_client_metadata(self):
        historical = deepcopy(self.manager.clients["claude"])
        clients = {key: value for key, value in self.clients.items() if key != "claude"}
        clients["codex"]["installation"] = "installed"
        self.manager.update_clients(clients)
        self.assertEqual(self.manager.clients["claude"], historical)
        self.manager.prepare_profiles()
        self.assertTrue((self.manager.profile_root / "codex-4" / "config.toml").is_file())
        self.assertTrue((self.manager.profile_root / "gemini-4" / ".gemini" / "settings.json").is_file())
        self.assertFalse((self.manager.profile_root / "claude-1").exists())
        self.assertEqual(self.manager.queue_profile("codex-4")["installation"], "installed")

    def prepare_free_route(self, profile_id="gemini-1", project="free-project"):
        clients = deepcopy(self.clients)
        clients["gemini"].update(version="0.51.0", access_route_supported=False,
                                 access_route_reason="Consumer OAuth retired")
        self.manager.update_clients(clients)
        return self.manager.prepare_gemini_free_route(profile_id, "gemini-3.8-flash", project,
                                                     free_tier_confirmed=True, no_billing_confirmed=True)

    def finish_free_key_setup(self, exit_code=0):
        home = self.manager.profile_root / "gemini-1-api-free" / ".gemini"
        (home / "gemini-credentials.json").write_bytes(b"opaque-official-encrypted-fixture")
        self.finish_login(exit_code)
        return self.manager.check("gemini-1")

    def test_free_route_keeps_oauth_history_and_rejects_stale_oauth_evidence(self):
        self.confirm("gemini-1")
        stale = self.bound_task("gemini-1", "oauth-attempt")
        old = self.manager.queue_profile("gemini-1")
        old_home = self.manager.profile_root / "gemini-1" / ".gemini"
        credential = old_home / "oauth_creds.json"
        credential.write_bytes(b"oauth-credential-fixture-do-not-copy")
        old_files = {path.name: path.read_bytes() for path in old_home.iterdir()}
        prepared = self.prepare_free_route()
        self.assertEqual(prepared["access_route"], "gemini_api_free")
        self.assertEqual(prepared["model"], "gemini-3.8-flash")
        self.assertTrue(prepared["route_configuration_valid"])
        self.assertFalse(prepared["can_probe"])
        self.assertGreater(prepared["auth_generation"], old["auth_generation"])
        self.assertEqual({path.name: path.read_bytes() for path in old_home.iterdir()}, old_files)
        self.assertEqual(prepared["route_history"][-1]["profile"]["identity_hash"], old["identity_hash"])
        self.assertNotIn("oauth-credential-fixture-do-not-copy", self.manager.state_path.read_text())
        self.assertEqual(self.launches, [])
        self.manager.observe_result("gemini-1", stale, {"simulated": False, "path": "old.json", "sha256": "a" * 64}, None)
        after = self.manager.queue_profile("gemini-1")
        self.assertEqual(after["access_state"], "unverified")
        self.assertEqual(after["ignored_result_evidence"][-1]["reason"], "account_binding_changed")
        self.assertEqual(self.manager.queue_profile("gemini-2")["reason_code"], "unsupported_client")

    def test_free_key_setup_requires_receipt_owner_confirmation_and_billing_then_explicit_probe(self):
        self.prepare_free_route()
        with self.assertRaises(AccountError):
            self.manager.confirm("gemini-1", auth_confirmed=True, billing_confirmed=True)
        self.manager.connect("gemini-1")
        command, kwargs, _ = self.launches[-1]
        request = json.loads(Path(command[-1]).read_text(encoding="utf-8"))
        self.assertEqual(request["command"][-2:], ["--model", "gemini-3.8-flash"])
        self.assertEqual(request["access_route"], "gemini_api_free")
        self.assertTrue(request["home"].endswith("gemini-1-api-free"))
        self.assertEqual(kwargs["env"]["GEMINI_FORCE_FILE_STORAGE"], "true")
        self.assertNotIn("GEMINI_API_KEY", kwargs["env"])
        checked = self.finish_free_key_setup()
        self.assertFalse(checked["can_probe"])
        with self.assertRaises(AccountError):
            self.manager.confirm("gemini-1", auth_confirmed=False, billing_confirmed=True)
        declined = self.manager.confirm("gemini-1", auth_confirmed=True, billing_confirmed=False)
        self.assertFalse(declined["can_probe"])
        confirmed = self.manager.confirm("gemini-1", auth_confirmed=True, billing_confirmed=True)
        self.assertTrue(confirmed["can_probe"])
        self.assertEqual(confirmed["access_state"], "unverified")
        self.assertEqual(confirmed["identity_source"], "owner_attested_project")
        self.assertFalse(confirmed["identity_verified"])
        self.assertFalse(confirmed["quota_isolation_verified"])
        with self.assertRaises(AccountError):
            self.manager.launch_spec("gemini-1")
        spec = self.manager.launch_spec("gemini-1", require_access=False)
        self.assertEqual(spec["model"], "gemini-3.8-flash")
        self.assertEqual(spec["gemini_settings"]["security"]["auth"]["enforcedType"], "gemini-api-key")
        self.assertEqual(spec["env"]["GEMINI_FORCE_FILE_STORAGE"], "true")
        self.manager.observe_result("gemini-1", self.bound_task("gemini-1", "api-free-valid"),
                                    {"simulated": False, "path": "free.json", "sha256": "b" * 64}, None)
        ready = self.manager.queue_profile("gemini-1")
        self.assertEqual(ready["state"], "ready")
        again = self.prepare_free_route()
        self.assertEqual(again["account_binding"], ready["account_binding"])
        self.assertEqual(again["access_evidence"], ready["access_evidence"])
        self.assertEqual(len(again["route_history"]), 1)

    def test_free_route_failed_login_or_missing_key_store_cannot_confirm(self):
        self.prepare_free_route()
        self.manager.connect("gemini-1")
        self.finish_free_key_setup(exit_code=1)
        with self.assertRaises(AccountError):
            self.manager.confirm("gemini-1", auth_confirmed=True, billing_confirmed=True)
        self.manager.connect("gemini-1", reconnect=True)
        self.finish_free_key_setup()
        store = self.manager.profile_root / "gemini-1-api-free" / ".gemini" / "gemini-credentials.json"
        store.unlink()
        with self.assertRaises(AccountError):
            self.manager.confirm("gemini-1", auth_confirmed=True, billing_confirmed=True)

    def test_free_route_expiry_and_manually_changed_settings_block_launch(self):
        self.prepare_free_route()
        self.manager.connect("gemini-1")
        self.finish_free_key_setup()
        self.manager.confirm("gemini-1", auth_confirmed=True, billing_confirmed=True)
        state = json.loads(self.manager.state_path.read_text())
        profile = next(p for p in state["profiles"] if p["profile_id"] == "gemini-1")
        profile["gemini_free_route"]["expires_at"] = (datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat()
        self.manager.state_path.write_text(json.dumps(state), encoding="utf-8")
        self.assertFalse(self.manager.queue_profile("gemini-1")["can_probe"])
        with self.assertRaises(AccountError):
            self.manager.launch_spec("gemini-1", require_access=False)
        self.prepare_free_route()
        settings = self.manager.profile_root / "gemini-1-api-free" / ".gemini" / "settings.json"
        changed = json.loads(settings.read_text())
        changed["security"]["auth"]["selectedType"] = "vertex-ai"
        settings.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaises(AccountError):
            self.manager.launch_spec("gemini-1", require_access=False)
        self.assertEqual(json.loads(settings.read_text())["security"]["auth"]["selectedType"], "vertex-ai")

    def test_changed_api_store_invalidates_binding_and_reused_store_requires_official_auth(self):
        self.prepare_free_route()
        self.manager.connect("gemini-1")
        self.finish_free_key_setup()
        self.manager.confirm("gemini-1", auth_confirmed=True, billing_confirmed=True)
        stale = self.bound_task("gemini-1", "before-key-replacement")
        self.manager.observe_result("gemini-1", self.bound_task("gemini-1", "api-free-verified"),
                                    {"simulated": False, "path": "free.json", "sha256": "b" * 64}, None)
        store = self.manager.profile_root / "gemini-1-api-free" / ".gemini" / "gemini-credentials.json"
        store.write_bytes(b"different-opaque-store-fixture-size")
        self.assertFalse(self.manager.queue_profile("gemini-1")["can_probe"])
        checked = self.manager.check("gemini-1")
        self.assertEqual(checked["reason_code"], "key_store_changed")
        self.assertFalse(checked["billing_confirmed"])
        self.assertEqual(checked["access_state"], "unverified")
        self.assertIsNone(checked["access_evidence"])
        self.assertGreater(checked["auth_generation"], stale["account_binding"]["auth_generation"])
        self.manager.observe_result("gemini-1", stale, {"simulated": False, "path": "old.json", "sha256": "c" * 64}, None)
        self.assertEqual(self.manager.queue_profile("gemini-1")["access_state"], "unverified")
        self.manager.connect("gemini-1")
        request = json.loads(Path(self.launches[-1][0][-1]).read_text())
        self.assertEqual(request["command"][-2:], ["--prompt-interactive", "/auth"])
        self.finish_login()
        self.manager.check("gemini-1")
        with self.assertRaises(AccountError):
            self.manager.confirm("gemini-1", auth_confirmed=True, billing_confirmed=True)

    def test_changing_project_never_reuses_prior_api_access(self):
        self.prepare_free_route()
        self.manager.connect("gemini-1")
        self.finish_free_key_setup()
        self.manager.confirm("gemini-1", auth_confirmed=True, billing_confirmed=True)
        old = self.manager.queue_profile("gemini-1")
        changed = self.prepare_free_route(project="different-free-project")
        self.assertFalse(changed["can_probe"])
        self.assertGreater(changed["auth_generation"], old["auth_generation"])
        self.assertIsNone(changed["free_route_login_receipt"])
        self.manager.connect("gemini-1")
        request = json.loads(Path(self.launches[-1][0][-1]).read_text())
        self.assertIn("/auth", request["command"])

    def test_late_free_probe_after_key_replacement_is_acknowledged_without_access(self):
        self.prepare_free_route()
        self.manager.connect("gemini-1")
        self.finish_free_key_setup()
        self.manager.confirm("gemini-1", auth_confirmed=True, billing_confirmed=True)
        stale = self.bound_task("gemini-1", "key-replaced-during-probe")
        store = self.manager.profile_root / "gemini-1-api-free" / ".gemini" / "gemini-credentials.json"
        store.write_bytes(b"replaced-opaque-key-store")
        self.manager.observe_result("gemini-1", stale, {"simulated": False, "path": "stale.json", "sha256": "c" * 64}, None)
        current = self.manager.queue_profile("gemini-1")
        self.assertFalse(current["can_probe"])
        self.assertEqual(current["access_state"], "unverified")
        self.assertIn(stale["attempt_id"], current["observed_attempts"])
        self.assertEqual(current["ignored_result_evidence"][-1]["reason"], "account_binding_changed")

    def test_free_route_preparation_blocks_active_task_and_requires_pinned_client(self):
        with self.assertRaises(AccountError):
            self.manager.prepare_gemini_free_route("gemini-1", "gemini-3.8-flash", "free-project", True, True)
        connection = sqlite3.connect(self.manager.workspace / "registry.sqlite")
        connection.execute("CREATE TABLE ai_attempts (status TEXT)")
        connection.execute("INSERT INTO ai_attempts VALUES ('running')")
        connection.commit()
        connection.close()
        before = self.manager.state_path.read_bytes()
        with self.assertRaises(AccountError) as error:
            self.prepare_free_route()
        self.assertEqual(error.exception.code, "operation_active")
        self.assertEqual(self.manager.state_path.read_bytes(), before)
        self.assertFalse((self.manager.profile_root / "gemini-1-api-free").exists())

    def test_gemini_settings_against_installed_client_schema_offline(self):
        """Run the installed CLI's actual validator, not a copy of our dict."""
        node = shutil.which("node.exe") or shutil.which("node")
        bundle = Path(os.environ.get("APPDATA", "")) / "npm/node_modules/@google/gemini-cli/bundle"
        entry = bundle / "gemini.js"
        if not node or not entry.exists():
            self.skipTest("Installed Gemini schema is unavailable; no client is installed for this test.")
        main_match = re.search(r'await import\("\./(gemini-[A-Z0-9]+\.js)"\)', entry.read_text(encoding="utf-8"))
        self.assertIsNotNone(main_match, "Installed Gemini entrypoint structure changed; review schema discovery.")
        main_source = (bundle / main_match.group(1)).read_text(encoding="utf-8")
        schema_match = re.search(r'import\s*\{[^}]*\bloadSettings\b[^}]*\}\s*from\s*"\./([^"/]+\.js)"', main_source)
        self.assertIsNotNone(schema_match, "Installed Gemini settings export was not found.")
        module_uri = (bundle / schema_match.group(1)).as_uri()
        script = self.root / "validate_gemini_settings.mjs"
        script.write_text("""
globalThis.fetch = () => { throw new Error('Network forbidden in offline settings validation'); };
const { loadSettings, getSettingsSchema } = await import(process.argv[2]);
const loaded = loadSettings(process.cwd());
process.stdout.write(JSON.stringify({ errors: loaded.errors, hooksEnabled: loaded.merged.hooksConfig.enabled,
  hookToggleType: getSettingsSchema().hooksConfig.properties.enabled.type }));
""", encoding="utf-8")
        observations = []
        for label, settings in (("legacy", GEMINI_SETTINGS_LEGACY_HOOKS), ("fixed", GEMINI_SETTINGS)):
            home = self.root / "schema" / label
            (home / ".gemini").mkdir(parents=True)
            (home / ".gemini" / "settings.json").write_text(json.dumps(settings), encoding="utf-8")
            environment = isolated_environment("gemini", home)
            # These are test fixture paths, never live-client policy overrides.
            environment["GEMINI_CLI_SYSTEM_SETTINGS_PATH"] = str(home / "absent-system-settings.json")
            environment["GEMINI_CLI_SYSTEM_DEFAULTS_PATH"] = str(home / "absent-system-defaults.json")
            run = subprocess.run([node, str(script), module_uri], cwd=home, env=environment,
                                 capture_output=True, text=True, encoding="utf-8", timeout=30, check=False,
                                 creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            self.assertEqual(run.returncode, 0, run.stderr)
            observations.append(json.loads(run.stdout))
        self.assertTrue(any("hooks.enabled" in error["message"] for error in observations[0]["errors"]))
        self.assertEqual(observations[1]["errors"], [])
        self.assertIs(observations[1]["hooksEnabled"], False)
        self.assertEqual(observations[1]["hookToggleType"], "boolean")


if __name__ == "__main__":
    unittest.main()
