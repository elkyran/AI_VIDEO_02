"""Real local subprocess tests. No provider login or network/model call is made."""
from __future__ import annotations

from copy import deepcopy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

import jsonschema

from app.ai_adapters import child_environment
from app.ai_accounts import GEMINI_SETTINGS
from app.ai_cli_runtime import (LiveCLIAdapter, build_command, classify_cli_failure,
                                decode_output, make_prompt, output_schema, transport_schema, _safe_log)
from app.editorial import demo_result


class AccountsFixture:
    def __init__(self, root):
        self.root = root
        self.safe = True
        self.access = True
        self.calls = []
        self.free = False

    def queue_profile(self, profile_id):
        return {"profile_id": profile_id, "billing_safe": self.safe, "can_probe": self.safe,
                "access_state": "verified" if self.access else "unverified",
                **({"provider":"gemini", "auth_mode":"gemini_api_free", "access_route":"gemini_api_free",
                    "route_configuration_valid":True, "model":"gemini-3.8-flash"} if self.free else {})}

    def launch_spec(self, profile_id, require_access=True):
        self.calls.append(require_access)
        if require_access and not self.access:
            raise ValueError("Access not verified")
        provider = profile_id.split("-")[0]
        return {"command": [sys.executable], "cli_args": [], "env": child_environment(),
                "cwd": str(self.root / profile_id), "profile": {
                    "profile_id": profile_id, "provider": provider, "billing_safe": self.safe,
                    "client_version": "local-process-test", "auth_mode": "subscription"}}


GEMINI_SETUP_STDERR = """Warning: Basic terminal detected (TERM=dumb).
Error authenticating: IneligibleTierError: This client is no longer supported for Gemini Code Assist for individuals. To continue using Gemini, please migrate to the Antigravity suite of products: https://antigravity.google
    at throwIneligibleOrProjectIdError (file:///C:/Users/example/AppData/Roaming/npm/node_modules/@google/gemini-cli/bundle/chunk-DHQ53XVO.js:301064:11)
    at _doSetupUser (file:///C:/Users/example/AppData/Roaming/npm/node_modules/@google/gemini-cli/bundle/chunk-DHQ53XVO.js:301053:5)
    at process.processTicksAndRejections (node:internal/process/task_queues:104:5) {
  ineligibleTiers: [
    {
      reasonCode: 'UNSUPPORTED_CLIENT',
      reasonMessage: 'This client is no longer supported for Gemini Code Assist for individuals. To continue using Gemini, please migrate to the Antigravity suite of products: https://antigravity.google',
      tierId: 'free-tier',
      tierName: 'Gemini Code Assist for individuals'
    }
  ]
}
Ripgrep is not available. Falling back to GrepTool.
An unexpected critical error occurred:IneligibleTierError: This client is no longer supported for Gemini Code Assist for individuals. To continue using Gemini, please migrate to the Antigravity suite of products: https://antigravity.google
    at throwIneligibleOrProjectIdError (file:///C:/Users/example/AppData/Roaming/npm/node_modules/@google/gemini-cli/bundle/chunk-DHQ53XVO.js:301064:11)
    at _doSetupUser (file:///C:/Users/example/AppData/Roaming/npm/node_modules/@google/gemini-cli/bundle/chunk-DHQ53XVO.js:301053:5)
    at process.processTicksAndRejections (node:internal/process/task_queues:104:5)
"""


STUB = '''import json, sys, time
from pathlib import Path
provider, mode = sys.argv[1:3]
if mode == "sleep":
    time.sleep(60)
    raise SystemExit(0)
prompt = sys.stdin.read()
if mode == "gemini_unsupported":
    print(Path(__file__).with_name("gemini_setup_stderr.txt").read_text(encoding="utf-8"), file=sys.stderr)
    raise SystemExit(1)
if mode == "failure":
    print(json.dumps({"error":{"code":"rate_limit_exceeded", "access_token":"private-value"}}))
    raise SystemExit(1)
payload = {"schema_version":1,"simulated":False,"text":"Локальный тест процесса"}
if mode == "invalid": payload["extra"] = True
if provider == "codex":
    print(json.dumps({"type":"item.completed","item":{"type":"agent_message","text":json.dumps(payload)}}))
elif provider == "claude":
    print(json.dumps({"type":"result","is_error":False,"structured_output":payload}))
else:
    print(json.dumps({"response":json.dumps(payload)}))
'''


class CLIRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.stub = self.root / "stub.py"
        self.stub.write_text(STUB, encoding="utf-8")
        (self.root / "gemini_setup_stderr.txt").write_text(GEMINI_SETUP_STDERR, encoding="utf-8")
        self.accounts = AccountsFixture(self.root / "accounts")

    def tearDown(self):
        self.temp.cleanup()

    def adapter(self, provider="codex", mode="ok"):
        def command(p, prefix, args, folder, task):
            return [sys.executable, str(self.stub), p, mode]
        return LiveCLIAdapter(provider, self.accounts, command_builder=command)

    def task(self, **kwargs):
        return {"task_id": "task_1", "kind": "write_note", "role": "writer", "expected_output": "note",
                "context": {}, "input_versions": {}, "assignment": {"provider": "codex", "model": None}, **kwargs}

    def start(self, adapter, task=None):
        folder = self.root / "workspace" / "ai" / "attempts" / (adapter.provider + str(time.time_ns()))
        profile = {"profile_id": adapter.provider + "-1", "provider": adapter.provider}
        return adapter.start(task or self.task(), profile, folder), folder

    def finish(self, adapter, handle, seconds=10):
        terminal = []
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            events = adapter.poll(handle)
            terminal = [e for e in events if e["kind"] in {"completed", "failed"}]
            if terminal:
                break
            time.sleep(.05)
        self.assertTrue(terminal, "Local subprocess did not finish within test bound")
        # Reap the Python supervisor to avoid ResourceWarning/Windows file handles.
        process = adapter._runs[handle].get("process")
        if process:
            process.wait(timeout=5)
        return terminal[-1]

    def test_start_is_durable_but_does_not_launch(self):
        adapter = self.adapter()
        with patch("app.ai_cli_runtime.subprocess.Popen") as launch:
            handle, folder = self.start(adapter)
            launch.assert_not_called()
            self.assertTrue((folder / "cli_request.json").is_file())
            self.assertEqual(adapter.recover(folder)["status"], "interrupted")
            adapter.cancel(handle)
            self.assertEqual(self.finish(adapter, handle)["failure"]["kind"], "interrupted")

    def test_three_provider_envelopes_real_process_and_recovery(self):
        for provider in ("codex", "claude", "gemini"):
            with self.subTest(provider=provider):
                adapter = self.adapter(provider)
                handle, folder = self.start(adapter)
                event = self.finish(adapter, handle)
                self.assertEqual(event["kind"], "completed")
                self.assertFalse(event["result"]["simulated"])
                self.assertEqual(adapter.recover(folder)["result"], event["result"])
                self.assertTrue((folder / "checkpoint.json").is_file())
                manifest = json.loads((folder / "cli_request.json").read_text(encoding="utf-8"))
                self.assertNotIn("env", manifest)
                self.assertEqual(manifest["task"]["input_versions"], {})
                if provider == "gemini":
                    settings = json.loads((Path(manifest["cwd"]) / ".gemini" / "settings.json").read_text(encoding="utf-8"))
                    self.assertEqual(settings, GEMINI_SETTINGS)
                    self.assertEqual(settings["hooksConfig"], {"enabled": False})
                    self.assertNotIn("enabled", settings.get("hooks", {}))
                    self.assertEqual(settings["security"]["auth"]["enforcedType"], "oauth-personal")

    def test_cancellation_and_recovered_handle(self):
        adapter = self.adapter(mode="sleep")
        handle, folder = self.start(adapter)
        adapter.poll(handle)
        # Wait for supervisor ownership evidence, never infer an account/provider identity.
        deadline = time.monotonic() + 5
        while not (folder / "worker.json").exists() and time.monotonic() < deadline:
            time.sleep(.02)
        replacement = self.adapter()
        recovered = replacement.recover(folder)
        self.assertEqual(recovered["status"], "running")
        replacement.cancel(recovered["handle"])
        event = self.finish(adapter, handle)
        self.assertEqual(event["failure"]["kind"], "interrupted")

    def test_timeout_even_when_child_does_not_read_large_stdin(self):
        adapter = self.adapter(mode="sleep")
        task = self.task(context={"timeout_seconds": 1, "brief": "x" * 100000})
        handle, folder = self.start(adapter, task)
        event = self.finish(adapter, handle)
        self.assertEqual(event["failure"]["code"], "timeout")
        self.assertEqual(adapter.recover(folder)["status"], "failed")

    def test_billing_and_access_gate_rechecked_before_launch(self):
        adapter = self.adapter()
        self.accounts.safe = False
        with self.assertRaisesRegex(ValueError, "billing"):
            self.start(adapter)
        self.accounts.safe = True
        handle, folder = self.start(adapter)
        self.accounts.safe = False
        with patch("app.ai_cli_runtime.subprocess.Popen") as launch:
            event = self.finish(adapter, handle)
            launch.assert_not_called()
        self.assertEqual(event["kind"], "failed")

    def test_access_check_is_the_only_unverified_access_route(self):
        adapter = self.adapter()
        self.accounts.access = False
        with self.assertRaisesRegex(ValueError, "Access"):
            self.start(adapter)
        handle, folder = self.start(adapter, self.task(kind="access_check"))
        self.assertEqual(self.finish(adapter, handle)["kind"], "completed")
        self.assertFalse(self.accounts.calls[-1])
        with self.assertRaisesRegex(ValueError, "single bounded note"):
            self.start(adapter, self.task(kind="access_check", expected_output="scene"))

    def free_route_spec(self):
        from app.gemini_free_route import route_settings
        spec = self.accounts.launch_spec("gemini-1", require_access=False)
        self.accounts.free = True
        spec["profile"].update(access_route="gemini_api_free", auth_mode="gemini_api_free",
                               route_configuration_valid=True, model="gemini-3.8-flash")
        spec["model"] = "gemini-3.8-flash"
        spec["gemini_settings"] = route_settings()
        spec["env"]["GEMINI_FORCE_FILE_STORAGE"] = "true"
        return spec

    def test_free_route_preserves_approved_model_and_native_api_settings(self):
        spec = self.free_route_spec()
        adapter = self.adapter("gemini")
        task = self.task(kind="access_check", assignment={"provider":"gemini", "model":"gemini-3.8-flash"})
        with patch.object(self.accounts, "launch_spec", return_value=spec):
            handle, folder = self.start(adapter, task)
            self.assertEqual(self.finish(adapter, handle)["kind"], "completed")
        saved = json.loads((folder / "cli_request.json").read_text(encoding="utf-8"))
        settings = json.loads((Path(saved["cwd"]) / ".gemini/settings.json").read_text(encoding="utf-8"))
        self.assertEqual(settings, spec["gemini_settings"])
        self.assertEqual(saved["task"]["assignment"]["model"], "gemini-3.8-flash")
        self.assertEqual(saved["access_route"], "gemini_api_free")
        self.assertNotIn("env", saved)
        command = build_command("gemini", [sys.executable], [], folder, task)
        self.assertEqual(command[-2:], ["--model", "gemini-3.8-flash"])

    def test_free_route_blocks_missing_or_changed_model_before_process_launch(self):
        for model in (None, "auto", "gemini-3.1-flash-lite"):
            with self.subTest(model=model):
                spec = self.free_route_spec()
                adapter = self.adapter("gemini")
                task = self.task(kind="access_check", assignment={"provider":"gemini", "model":model})
                with patch.object(self.accounts, "launch_spec", return_value=spec), patch("app.ai_cli_runtime.subprocess.Popen") as launch:
                    handle, folder = self.start(adapter, task)
                    event = self.finish(adapter, handle)
                    launch.assert_not_called()
                self.assertEqual(event["kind"], "failed")
                self.assertIn("exact approved model", event["failure"]["message"])
                self.assertFalse((folder / "launch_intent.json").exists())

    def test_free_route_blocks_oauth_settings_or_shared_storage_before_launch(self):
        for invalid in ("settings", "storage"):
            with self.subTest(invalid=invalid):
                spec = self.free_route_spec()
                if invalid == "settings":
                    spec["gemini_settings"] = deepcopy(GEMINI_SETTINGS)
                else:
                    spec["env"].pop("GEMINI_FORCE_FILE_STORAGE")
                adapter = self.adapter("gemini")
                task = self.task(kind="access_check", assignment={"provider":"gemini", "model":"gemini-3.8-flash"})
                with patch.object(self.accounts, "launch_spec", return_value=spec), patch("app.ai_cli_runtime.subprocess.Popen") as launch:
                    handle, folder = self.start(adapter, task)
                    event = self.finish(adapter, handle)
                    launch.assert_not_called()
                self.assertEqual(event["kind"], "failed")
                self.assertFalse((folder / "launch_intent.json").exists())

    def test_invalid_model_result_is_not_saved_as_success(self):
        adapter = self.adapter(mode="invalid")
        handle, folder = self.start(adapter)
        self.assertEqual(self.finish(adapter, handle)["kind"], "failed")

    def test_sanitized_error_log_and_conservative_rate_limit(self):
        adapter = self.adapter(mode="failure")
        handle, folder = self.start(adapter)
        event = self.finish(adapter, handle)
        self.assertEqual(event["failure"]["kind"], "rate_limited")
        self.assertFalse(event["failure"]["quota_confirmed"])
        self.assertNotIn("private-value", (folder / "cli.log").read_text(encoding="utf-8"))
        self.assertIn("[REDACTED]", _safe_log('{"refresh_token":"private-value"}'))
        self.assertNotIn("private-value", _safe_log('  "refresh_token": "private-value",'))

    def test_gemini_native_setup_error_reaches_durable_failure(self):
        adapter = self.adapter("gemini", mode="gemini_unsupported")
        handle, folder = self.start(adapter, self.task(kind="access_check"))
        event = self.finish(adapter, handle)
        detail = event["failure"]
        self.assertEqual(detail["kind"], "unsupported_client")
        self.assertFalse(detail["retryable"])
        self.assertFalse(detail["quota_confirmed"])
        self.assertEqual(detail["evidence"]["structured_codes"], ["UNSUPPORTED_CLIENT"])
        self.assertEqual(adapter.recover(folder)["failure"], {k: v for k, v in detail.items() if k != "is_simulated"})
        self.assertIs(detail["is_simulated"], False)
        saved = json.loads((folder / "cli_result.json").read_text(encoding="utf-8"))
        self.assertNotIn("Antigravity", json.dumps(saved))
        self.assertNotIn("example/AppData", json.dumps(saved))
        self.assertFalse(json.loads((folder / "checkpoint.json").read_text(encoding="utf-8"))["raw_provider_output_saved"])

    def test_gemini_setup_code_requires_exact_native_stderr_shape(self):
        actual = classify_cli_failure("gemini", [], 1, stderr=GEMINI_SETUP_STDERR)
        self.assertEqual(actual["kind"], "unsupported_client")
        self.assertEqual(classify_cli_failure("gemini", [], 1, stderr=GEMINI_SETUP_STDERR.replace("\n", "\r\n"))["kind"], "unsupported_client")
        for text in (GEMINI_SETUP_STDERR.replace("UNSUPPORTED_CLIENT", "RATE_LIMIT_EXCEEDED"),
                     GEMINI_SETUP_STDERR.replace("IneligibleTierError", "AuthenticationError"),
                     GEMINI_SETUP_STDERR.replace("throwIneligibleOrProjectIdError", "someModelFunction"),
                     GEMINI_SETUP_STDERR.replace("  ineligibleTiers: [", "  modelText: ["),
                     "reasonCode: 'UNSUPPORTED_CLIENT'",
                     "Error authenticating: UNSUPPORTED_CLIENT",
                     json.dumps({"response": GEMINI_SETUP_STDERR}),
                     "\n".join("> " + line for line in GEMINI_SETUP_STDERR.splitlines())):
            with self.subTest(text=text[:100]):
                detail = classify_cli_failure("gemini", [], 1, stderr=text)
                self.assertEqual(detail["kind"], "unknown")
                self.assertFalse(detail["quota_confirmed"])

    def test_model_output_cannot_supply_gemini_auth_evidence(self):
        envelope = {"response": json.dumps({"text": GEMINI_SETUP_STDERR})}
        for provider, events, exit_code, stdout in (
                ("gemini", [envelope], 1, json.dumps(envelope)),
                ("gemini", [], 1, GEMINI_SETUP_STDERR),
                ("gemini", [], 0, ""), ("claude", [], 1, ""), ("codex", [], 1, "")):
            with self.subTest(provider=provider, exit_code=exit_code, stdout=bool(stdout)):
                detail = classify_cli_failure(provider, events, exit_code,
                                              stderr=GEMINI_SETUP_STDERR, stdout=stdout)
                self.assertEqual(detail["kind"], "unknown")
                self.assertFalse(detail["quota_confirmed"])

    def test_request_tampering_and_launch_gap_fail_closed(self):
        adapter = self.adapter()
        handle, folder = self.start(adapter)
        self.finish(adapter, handle)
        request = folder / "cli_request.json"
        request.write_text(request.read_text(encoding="utf-8") + "\n", encoding="utf-8")
        self.assertEqual(adapter.recover(folder)["status"], "failed")
        handle2, folder2 = self.start(adapter)
        (folder2 / "launch_intent.json").write_text("{}", encoding="utf-8")
        self.assertEqual(adapter.recover(folder2)["status"], "unknown")

    def test_provider_launch_gap_keeps_reservation(self):
        adapter = self.adapter()
        handle, folder = self.start(adapter)
        (folder / "worker.json").write_text(json.dumps({"pid": 99999999, "identity": "old"}), encoding="utf-8")
        (folder / "child_launch_intent.json").write_text("{}", encoding="utf-8")
        with patch("app.ai_cli_runtime.process_identity", return_value=False):
            self.assertEqual(adapter.recover(folder)["status"], "unknown")

    def test_real_payload_bound_to_current_scene_and_not_simulator(self):
        task = self.task(expected_output="scene", context={"scene_id": "scene_001", "scene_version": 2})
        payload = demo_result("scene", {"scene_id": "scene_001", "scene_version": 1})
        with self.assertRaisesRegex(ValueError, "non-simulated"):
            decode_output("gemini", json.dumps({"response": json.dumps(payload)}), task)
        def unsimulated(value):
            if isinstance(value, dict):
                return {k: False if k == "simulated" else unsimulated(v) for k, v in value.items()}
            return [unsimulated(v) for v in value] if isinstance(value, list) else value
        with self.assertRaises(Exception):
            decode_output("gemini", json.dumps({"response": json.dumps(unsimulated(payload))}), task)

    def test_commands_pin_subscription_and_disable_tools(self):
        task = self.task()
        codex = build_command("codex", ["C:/codex.exe"], [], self.root, task)
        self.assertIn('forced_login_method="chatgpt"', codex)
        self.assertIn('cli_auth_credentials_store="file"', codex)
        self.assertIn("features.shell_tool=false", codex)
        self.assertIn("--ignore-user-config", codex)
        claude = build_command("claude", ["C:/claude.exe"], [], self.root, task)
        self.assertEqual(claude[claude.index("--tools") + 1], "")
        self.assertNotIn("--bare", claude)  # bare explicitly disables subscription OAuth.
        self.assertEqual(classify_cli_failure("gemini", [], 53)["kind"], "local_turn_limit")
        self.assertEqual(classify_cli_failure("codex", [{"error": {"code": 429}}], 1)["kind"], "unknown")

    def test_prompt_includes_dependencies_and_preserves_dynamic_schema(self):
        task = self.task(context={"dependency_results": {"writer": {"text": "bound"}}})
        self.assertIn('"writer": {"text": "bound"}', make_prompt(task))
        schema = output_schema("note")
        self.assertFalse(schema["additionalProperties"])
        for kind in ("scene", "edit_plan", "review", "note"):
            payload = demo_result(kind)
            if kind == "scene":
                payload["dependencies"] = {"scene": "scene_001:v1", "voice": "voice:v1"}
            jsonschema.validate(payload, output_schema(kind))
            jsonschema.validate({"artifact_json": json.dumps(payload)}, transport_schema())

    def test_structured_transport_does_not_narrow_creative_contract(self):
        payload = {"schema_version": 1, "simulated": False, "text": "Actual JSON"}
        transport = {"artifact_json": json.dumps(payload)}
        for provider in ("codex", "claude"):
            text = (json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": json.dumps(transport)}})
                    if provider == "codex" else json.dumps({"structured_output": transport}))
            self.assertEqual(decode_output(provider, text, self.task()), payload)


if __name__ == "__main__":
    unittest.main()
