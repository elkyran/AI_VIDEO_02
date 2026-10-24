"""Official CLI execution after AccountManager has verified an isolated login.

No credential is read here. The account manager supplies the child environment
in memory. Only validated creative data and sanitized process evidence enter the
project. A durable supervisor owns timeout/cancellation even if the panel exits.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import re
import signal
import subprocess
import sys
import threading
import time
import uuid

from .ai_adapters import ProviderAdapter, sanitize, guard_budget
from .ai_accounts import GEMINI_SETTINGS
from .development import atomic_write, locked
from .editorial import Scene, EditPlan, Review, Note, validate_result, _dependency_context
from .multi_ai_preview import process_identity

MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_PROMPT_BYTES = 256 * 1024
SCHEMAS = {"scene": Scene, "edit_plan": EditPlan, "review": Review, "note": Note}
from .v2_acquisition_contracts import ACQUISITION_OUTPUTS
SCHEMAS.update(ACQUISITION_OUTPUTS)


def _write(path, value):
    atomic_write(Path(path), json.dumps(sanitize(value), ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _safe_log(text):
    try:
        return json.dumps(sanitize(json.loads(text)), ensure_ascii=False)
    except (ValueError, TypeError):
        # A pretty-printed JSON object arrives one line at a time. Parse its
        # individual key/value lines too, so quoted secret keys are redacted.
        candidate = text.strip().rstrip(",")
        if candidate.startswith('"') and ":" in candidate:
            try:
                return json.dumps(sanitize(json.loads("{" + candidate + "}")), ensure_ascii=False)
            except ValueError:
                pass
        return sanitize(text)


def output_schema(kind):
    """The actual editorial schema, including open dependency/transform mappings."""
    return SCHEMAS[kind].model_json_schema()


def transport_schema():
    """Small strict envelope supported by both providers' structured-output modes.

    Their restricted schema dialect cannot represent every existing editorial
    mapping. Preserve that contract by transporting its JSON as a string, then
    applying the complete Pydantic and current-version checks locally.
    """
    return {"type": "object", "additionalProperties": False, "properties": {
        "artifact_json": {"type": "string", "description": "The complete editorial artifact encoded as a JSON object string."}},
        "required": ["artifact_json"]}


def make_prompt(task):
    context = _dependency_context(task.get("context", {}))
    brief = {
        "task_id": task.get("task_id"), "role": task.get("role"),
        "assignment": task.get("assignment", {}), "kind": task.get("kind"),
        "input_versions": task.get("input_versions", {}), "expected_output": task["expected_output"],
        "context": context,
    }
    text = ("AI_VIDEO_02 editorial task. Return exactly one JSON object conforming to the supplied schema. "
            "Do not run tools, read other files, browse, or change settings. Use only the supplied context. "
            "If the CLI requires an artifact_json transport envelope, put that exact artifact JSON in its string field. "
            "simulated must be false everywhere. Respect project/scene/version/subject/hash bindings exactly. "
            "Evidence is metadata_only unless explicitly supplied; never claim to have watched or heard media. "
            "Timeline/source ranges are plans, not evidence of observation. If no media delivery coverage is supplied, "
            "set every shot.coverage, review.provided_coverage, and review.reviewed_coverage to []; "
            "do not copy timeline ranges into these evidence fields. "
            "Provider/account/entitlement claims are not part of your task. A note access_check needs only a short acknowledgement. "
            "Write creative text in Russian unless the brief requests another language.\n"
            + json.dumps({"brief": brief, "output_schema": output_schema(task["expected_output"])}, ensure_ascii=False))
    if len(text.encode("utf-8")) > MAX_PROMPT_BYTES:
        raise ValueError("Shared context exceeds the bounded CLI prompt limit")
    return text


def build_command(provider, prefix, cli_args, folder, task):
    """Flags are supported by installed --help and the cited provider manuals."""
    command = list(prefix) + list(cli_args)
    schema = folder / "output_schema.json"
    if provider == "codex":
        command += ["exec", "--ignore-user-config", "--ignore-rules", "--skip-git-repo-check",
                    "--ephemeral", "--sandbox", "read-only", "--color", "never", "--json",
                    "-c", 'forced_login_method="chatgpt"', "-c", 'model_provider="openai"',
                    "-c", 'cli_auth_credentials_store="file"',
                    "-c", "features.shell_tool=false", "-c", 'web_search="disabled"',
                    "-c", "features.remote_plugin=false", "--output-schema", str(schema), "-"]
    elif provider == "claude":
        command += ["--print", "--output-format", "json", "--tools", "", "--strict-mcp-config",
                    "--mcp-config", str(folder / "empty_mcp.json"), "--setting-sources", "",
                    "--safe-mode", "--restricted", "--permission-prompts", "none",
                    "--settings", str(folder / "claude_runtime_settings.json"),
                    "--no-session-persistence", "--max-turns", "2", "--json-schema",
                    json.dumps(transport_schema(), ensure_ascii=False, separators=(",", ":"))]
    elif provider == "gemini":
        command += ["--prompt", "Complete the editorial JSON task supplied on stdin.",
                    "--output-format", "json", "--approval-mode", "default", "--extensions", "none"]
    else:
        raise ValueError("Unknown CLI provider")
    model = task.get("assignment", {}).get("model")
    if model:
        if not isinstance(model, str) or len(model) > 160 or model.startswith("-"):
            raise ValueError("Invalid configured model")
        # The model is an explicit role setting; the runtime never substitutes it.
        command += ["--model", model]
    return command


def _contains_simulated(value):
    if isinstance(value, dict):
        return value.get("simulated") is True or any(_contains_simulated(v) for v in value.values())
    return isinstance(value, list) and any(_contains_simulated(v) for v in value)


def decode_output(provider, stdout, task):
    """Parse provider envelopes; model prose can never establish access or identity."""
    if provider == "codex":
        events = [json.loads(line) for line in stdout.splitlines() if line.strip()]
        failures = [e for e in events if e.get("type") in {"error", "turn.failed"}]
        if failures:
            raise CLIResultError(classify_cli_failure(provider, failures, 0))
        answers = [e["item"].get("text") for e in events if e.get("type") == "item.completed"
                   and e.get("item", {}).get("type") == "agent_message"]
        if not answers:
            raise ValueError("Codex did not produce a final agent message")
        payload = json.loads(answers[-1])
    else:
        envelope = json.loads(stdout)
        if envelope.get("error") or envelope.get("is_error"):
            raise CLIResultError(classify_cli_failure(provider, [envelope], 0))
        payload = envelope.get("structured_output") if provider == "claude" else None
        if payload is None:
            answer = envelope.get("result" if provider == "claude" else "response")
            if not isinstance(answer, str):
                raise ValueError("CLI did not produce the documented response field")
            payload = json.loads(answer)
    if isinstance(payload, dict) and set(payload) == {"artifact_json"}:
        payload = json.loads(payload["artifact_json"])
    if not isinstance(payload, dict) or _contains_simulated(payload):
        raise ValueError("Real CLI result must be a non-simulated JSON object")
    if sanitize(payload) != payload:
        raise ValueError("Credential-shaped content is forbidden in creative results")
    return validate_result(task["expected_output"], payload, task.get("context", {}))


def failure(kind, message, **extra):
    return {"kind": kind, "message": sanitize(message), "retryable": False,
            "quota_confirmed": False, "certainty": "confirmed", **extra}


class CLIResultError(ValueError):
    def __init__(self, detail):
        super().__init__(detail["message"])
        self.failure = detail


def _gemini_setup_failure(stderr):
    """Recognize the installed CLI's pre-model auth exception, not model prose.

    Gemini 0.51 logs the native IneligibleTierError with Node's object inspector
    before any JSON response. Match that anchored exception and its typed field;
    never infer access, billing or quota from a reasonMessage or HTTP status.
    Unknown/future formats remain unknown. Raw stderr is kept only in memory.
    """
    if not isinstance(stderr, str) or len(stderr) > 64 * 1024:
        return None
    blocks = re.finditer(
        r"^Error authenticating: IneligibleTierError: [^\r\n]*\r?\n"
        r"(?P<stack>(?:    at [^\r\n]*\r?\n){1,12})"
        r"  ineligibleTiers: \[\r?\n(?P<tiers>(?:[^\r\n]*\r?\n){1,48}?)"
        r"  \]\r?\n\}\r?$", stderr, re.MULTILINE)
    for block in blocks:
        stack = block.group("stack")
        if not re.search(r"^    at throwIneligibleOrProjectIdError \(file:///[^\r\n)]+/"
                         r"@google/gemini-cli/bundle/[^/\r\n)]+\.js:\d+:\d+\)\r?$", stack, re.MULTILINE):
            continue
        if not re.search(r"^    at _doSetupUser \(", stack, re.MULTILINE) or not stack.rstrip().endswith(" {"):
            continue
        # Each reason belongs to an actual inspected tier object. Quoted lines,
        # JSON strings, other error classes, and arbitrary output do not match.
        tiers = re.findall(r"^    \{\r?\n(?P<fields>(?:      [^\r\n]*\r?\n){1,12})"
                           r"    \},?\r?$", block.group("tiers"), re.MULTILINE)
        codes = [match.group(1) for tier in tiers for match in re.finditer(
            r"^      reasonCode: ['\"]([A-Z_]+)['\"],?\r?$", tier, re.MULTILINE)]
        if "UNSUPPORTED_CLIENT" in codes:
            return failure("unsupported_client",
                           "Gemini отклонил этот клиент для Gemini Code Assist for individuals (UNSUPPORTED_CLIENT).",
                           evidence={"exit_code": 1, "structured_codes": ["UNSUPPORTED_CLIENT"],
                                     "source": "gemini_cli_auth_stderr", "phase": "authentication_setup"})
    return None


def classify_cli_failure(provider, events, exit_code, *, stderr="", stdout=""):
    """Only explicit provider error codes classify quota; never 429 or free prose."""
    # The native Gemini setup failure currently exits 1 without stdout. Once
    # response/model output exists, stderr cannot establish this classification.
    if provider == "gemini" and exit_code == 1 and not events and not stdout.strip():
        setup_failure = _gemini_setup_failure(stderr)
        if setup_failure:
            return setup_failure
    codes = []
    for event in events:
        if not isinstance(event, dict):
            continue
        error = event.get("error", {})
        if isinstance(error, dict):
            codes.extend([error.get("code"), error.get("type")])
        codes.extend([event.get("code"), event.get("subtype")])
    mapping = {"authentication_error": "authentication", "invalid_api_key": "authentication",
               "unauthenticated": "authentication", "permission_denied": "permissions",
               "permission_error": "permissions", "rate_limit_error": "rate_limited",
               "rate_limit_exceeded": "rate_limited", "overloaded_error": "overloaded",
               "billing_error": "billing", "insufficient_quota": "billing",
               "error_max_turns": "local_turn_limit", "ENOTFOUND": "network", "ECONNRESET": "network",
               "ETIMEDOUT": "network", "ECONNREFUSED": "network"}
    kind = next((mapping[str(code)] for code in codes if str(code) in mapping), "unknown")
    if provider == "gemini" and exit_code in (42, 53):
        kind = "invalid_input" if exit_code == 42 else "local_turn_limit"
    return failure(kind, f"{provider} CLI failed; inspect the sanitized cli.log.",
                   certainty="confirmed" if kind != "unknown" else "unknown",
                   evidence={"exit_code": exit_code, "structured_codes": [c for c in codes if c is not None]})


class LiveCLIAdapter(ProviderAdapter):
    is_live = True
    is_simulated = False

    def __init__(self, provider, accounts, *, command_builder=build_command):
        super().__init__(provider)
        self.accounts = accounts
        self.command_builder = command_builder

    def check_auth(self, profile):
        try:
            self.accounts.launch_spec(profile["profile_id"], require_access=True)
            return {"status": "ready", "is_simulated": False}
        except Exception as exc:
            return {"status": "unavailable", "reason": sanitize(str(exc)), "is_simulated": False}

    def start(self, task, profile, attempt_dir=None):
        if attempt_dir is None:
            raise ValueError("A durable attempt directory is required")
        if task.get("kind") == "access_check" and (task.get("expected_output") != "note" or task.get("depends_on")):
            raise ValueError("An access check must be a single bounded note")
        guard_budget(profile)
        # This method runs inside the coordinator's transaction. Only read local
        # account metadata here; official diagnosis belongs to poll after commit.
        current_profile = self.accounts.queue_profile(profile["profile_id"])
        guard_budget(current_profile)
        if current_profile.get("billing_safe") is not True:
            raise ValueError("Account billing route and disabled paid credits must be verified")
        if not current_profile.get("can_probe") or (task.get("kind") != "access_check" and current_profile.get("access_state") != "verified"):
            raise ValueError("Access not verified for the requested isolated account")
        folder = Path(attempt_dir).resolve()
        folder.mkdir(parents=True, exist_ok=True)
        if (folder / "cli_request.json").exists():
            raise ValueError("Existing CLI attempts cannot be overwritten")
        prompt = make_prompt(task)
        handle, run = self._new_run(task, profile, folder)
        timeout = task.get("context", {}).get("timeout_seconds", 120)
        if type(timeout) not in (int, float) or not 1 <= timeout <= 300:
            raise ValueError("CLI timeout must be between 1 and 300 seconds")
        request = {"provider": self.provider, "profile_id": profile["profile_id"], "task": deepcopy(task),
                   "timeout_seconds": timeout, "prompt": prompt, "created_at": time.time()}
        _write(folder / "cli_request.json", request)
        _write(folder / "output_schema.json", transport_schema())
        _write(folder / "empty_mcp.json", {"mcpServers": {}})
        _write(folder / "claude_runtime_settings.json", {"forceLoginMethod": "claudeai", "disableAllHooks": True})
        run.update(folder=folder, process=None, launched=False, delivered=False)
        return handle

    def _launch(self, handle):
        run = self._runs[handle]
        folder = run["folder"]
        task = run["task"]
        spec = self.accounts.launch_spec(run["profile"]["profile_id"], require_access=task.get("kind") != "access_check")
        guard_budget(spec["profile"])
        if spec["profile"].get("billing_safe") is not True:
            raise ValueError("Account billing safety is no longer verified")
        free_route = self.provider == "gemini" and spec["profile"].get("access_route") == "gemini_api_free"
        if free_route:
            from .gemini_free_route import FREE_MODELS, route_settings
            approved_model = spec.get("model")
            if approved_model not in FREE_MODELS or task.get("assignment", {}).get("model") != approved_model:
                raise ValueError("Gemini Free Tier requires the owner's exact approved model")
            if spec.get("gemini_settings") != route_settings() or spec["env"].get("GEMINI_FORCE_FILE_STORAGE") != "true":
                raise ValueError("Gemini Free Tier requires isolated official API-key settings and storage")
        prefix = spec["command"]
        if not isinstance(prefix, list) or not prefix or not Path(prefix[0]).is_absolute():
            raise ValueError("Account manager must return an absolute executable argv")
        if Path(prefix[0]).suffix.lower() in {".cmd", ".bat", ".ps1"}:
            raise ValueError("Resolve official script shims to their verified executable and entrypoint")
        work = Path(spec["cwd"]).resolve() / "runs" / folder.name
        work.mkdir(parents=True, exist_ok=False)
        if self.provider == "gemini":
            # Explicit no-tools allowlist, own workspace, no project .env loading.
            (work / ".gemini").mkdir()
            # Keep home and per-attempt settings on the same validated schema.
            _write(work / ".gemini" / "settings.json", deepcopy(spec["gemini_settings"] if free_route else GEMINI_SETTINGS))
        request = _json(folder / "cli_request.json")
        request.update(command=self.command_builder(self.provider, prefix, spec.get("cli_args", []), folder, task),
                       cwd=str(work), client_version=spec["profile"].get("client_version"),
                       billing_safe=bool(spec["profile"].get("billing_safe")),
                       access_route=spec["profile"].get("access_route"))
        _write(folder / "cli_request.json", request)
        _write(folder / "launch_intent.json", {"launch_id": uuid.uuid4().hex, "created_at": time.time()})
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        # No raw stdout/stderr file: supervisor sanitizes provider output before persistence.
        try:
            process = subprocess.Popen([sys.executable, "-X", "utf8", "-m", "app.ai_cli_runtime", "--worker", str(folder)],
                                       cwd=Path(__file__).resolve().parents[1], env=spec["env"], stdin=subprocess.DEVNULL,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **options)
        except OSError as exc:
            # Popen proved launch failed; preserve intent and a terminal receipt.
            _write(folder / "cli_result.json", {"status": "failed", "failure": failure("permissions", str(exc)),
                   "request_sha256": hashlib.sha256((folder / "cli_request.json").read_bytes()).hexdigest(),
                   "is_simulated": False, "completed_at": time.time()})
            run["launched"] = True
            return
        run.update(process=process, launched=True)
        _write(folder / "supervisor.json", {"pid": process.pid, "identity": process_identity(process.pid)})

    def poll(self, handle):
        run = self._runs[handle]
        if run["terminal"]:
            return []
        if not run["launched"]:
            if run["cancelled"]:
                run["terminal"] = True
                return [self._event(handle, "failed", failure("interrupted", "Cancelled before CLI launch"))]
            try:
                self._launch(handle)
            except Exception as exc:
                # A launch intent without confirmed process state is recovered conservatively.
                if (run["folder"] / "launch_intent.json").exists():
                    run["launched"] = True
                    raise
                run["terminal"] = True
                return [self._event(handle, "failed", failure("permissions", str(exc)))]
            return [self._event(handle, "run_started", {"process_started": True})]
        recovered = self.recover(run["folder"], attach=False)
        if recovered["status"] in {"running", "unknown"}:
            return []
        run["terminal"] = True
        if recovered["status"] == "completed":
            return [self._event(handle, "completed", {"result": recovered["result"]})]
        return [self._event(handle, "failed", recovered.get("failure", failure("interrupted", "CLI supervisor interrupted")))]

    def cancel(self, handle):
        run = self._runs.get(handle)
        if run is None:
            return {"status": "unknown"}
        run["cancelled"] = True
        if run["terminal"] or not run["launched"]:
            return {"status": "stopped"}
        _write(run["folder"] / "cancel.json", {"requested_at": time.time()})
        return {"status": "stopping"}

    def recover(self, attempt_dir, *, attach=True):
        folder = Path(attempt_dir)
        result_path = folder / "cli_result.json"
        if result_path.is_file():
            result = _json(result_path)
            request = _json(folder / "cli_request.json")
            if result.get("request_sha256") != hashlib.sha256((folder / "cli_request.json").read_bytes()).hexdigest():
                return {"status": "failed", "failure": failure("invalid_input", "Recovered request hash differs")}
            if result["status"] == "completed":
                if _contains_simulated(result["result"]):
                    return {"status": "failed", "failure": failure("invalid_input", "Recovered real result claims simulation")}
                result["result"] = validate_result(request["task"]["expected_output"], result["result"], request["task"].get("context", {}))
            return result
        identity_file = folder / "worker.json"
        if not identity_file.exists():
            identity_file = folder / "supervisor.json"
        if not identity_file.exists():
            return {"status": "unknown" if (folder / "launch_intent.json").exists() else "interrupted"}
        saved = _json(identity_file)
        identity = process_identity(saved["pid"])
        if identity is None or saved.get("identity") in (None, False):
            return {"status": "unknown"}
        if identity == saved["identity"]:
            if not attach:
                return {"status": "running"}
            request = _json(folder / "cli_request.json")
            handle = "recovered-" + uuid.uuid4().hex
            self._runs[handle] = {"task": request["task"], "profile": {"profile_id": request["profile_id"]},
                                  "folder": folder, "process": None, "launched": True, "terminal": False,
                                  "cancelled": False, "event_sequence": 0, "event_file": folder / "adapter_events.jsonl"}
            return {"status": "running", "handle": handle}
        # The supervisor has gone. A recorded provider child may still be running.
        child_file = folder / "child.json"
        if child_file.exists():
            child = _json(child_file)
            child_identity = process_identity(child["pid"])
            if child_identity is None or child.get("identity") in (None, False) or child_identity == child["identity"]:
                return {"status": "unknown"}
        elif (folder / "child_launch_intent.json").exists():
            # Supervisor may have died between creating the provider process and
            # recording its identity. Never release that unproven reservation.
            return {"status": "unknown"}
        return {"status": "interrupted"}


def _stop_child(process):
    if process.poll() is not None:
        return
    if os.name == "nt":
        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], stdin=subprocess.DEVNULL,
                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
                       creationflags=subprocess.CREATE_NO_WINDOW, check=False)
    else:
        os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait(timeout=5)


def _worker(folder):
    folder = folder.resolve()
    request_path = folder / "cli_request.json"
    request = _json(request_path)
    request_hash = hashlib.sha256(request_path.read_bytes()).hexdigest()
    _write(folder / "worker.json", {"pid": os.getpid(), "identity": process_identity(os.getpid())})
    result = None
    process = None
    try:
        # Also serializes providers across coordinator restarts in this workspace.
        with locked(folder.parent.parent / ("provider-" + request["provider"] + ".lock")):
            options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
            _write(folder / "child_launch_intent.json", {"created_at": time.time()})
            process = subprocess.Popen(request["command"], cwd=request["cwd"], stdin=subprocess.PIPE,
                                       stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
            _write(folder / "child.json", {"pid": process.pid, "identity": process_identity(process.pid)})
            buffers = {"stdout": bytearray(), "stderr": bytearray()}
            overflow = threading.Event()
            log_lock = threading.Lock()
            def drain(name, pipe):
                with pipe:
                    for line in iter(pipe.readline, b""):
                        text = _safe_log(line.decode("utf-8", errors="replace"))
                        with log_lock, (folder / "cli.log").open("a", encoding="utf-8") as log:
                            log.write(name + ": " + text.rstrip("\r\n") + "\n")
                        if len(buffers[name]) + len(line) <= MAX_OUTPUT_BYTES:
                            buffers[name].extend(line)
                        else:
                            overflow.set()
            readers = [threading.Thread(target=drain, args=(name, getattr(process, name)), daemon=True) for name in buffers]
            for reader in readers:
                reader.start()
            def supply_input():
                try:
                    process.stdin.write(request["prompt"].encode("utf-8"))
                    process.stdin.close()
                except (BrokenPipeError, OSError):
                    pass
            # A client which never reads stdin must still be cancellable/timed out.
            writer = threading.Thread(target=supply_input, daemon=True)
            writer.start()
            deadline = time.monotonic() + request["timeout_seconds"]
            stopped = None
            while process.poll() is None:
                if (folder / "cancel.json").exists():
                    stopped = failure("interrupted", "Managed CLI task cancelled")
                elif time.monotonic() >= deadline:
                    stopped = failure("interrupted", "CLI timeout expired", code="timeout")
                elif overflow.is_set():
                    stopped = failure("invalid_input", "CLI output exceeded the capture limit", code="output_limit")
                if stopped:
                    _stop_child(process)
                    break
                time.sleep(0.1)
            for reader in readers:
                reader.join(timeout=5)
            writer.join(timeout=1)
            stdout = buffers["stdout"].decode("utf-8", errors="replace")
            if stopped:
                result = {"status": "failed", "failure": stopped}
            elif process.returncode != 0:
                events = []
                try:
                    complete_error = json.loads(stdout)
                    if isinstance(complete_error, dict):
                        events.append(complete_error)
                except ValueError:
                    pass
                for line in stdout.splitlines() if not events else []:
                    try:
                        events.append(json.loads(line))
                    except ValueError:
                        pass
                result = {"status": "failed", "failure": classify_cli_failure(
                    request["provider"], events, process.returncode, stdout=stdout,
                    stderr=buffers["stderr"].decode("utf-8", errors="replace"))}
            else:
                result = {"status": "completed", "result": decode_output(request["provider"], stdout, request["task"])}
    except CLIResultError as exc:
        result = {"status": "failed", "failure": exc.failure}
    except Exception as exc:
        if process is not None and process.poll() is None:
            _stop_child(process)
        result = {"status": "failed", "failure": failure("invalid_input", str(exc), exception_type=type(exc).__name__)}
        with (folder / "cli.log").open("a", encoding="utf-8") as log:
            log.write(sanitize(f"supervisor: {type(exc).__name__}: {exc}") + "\n")
    result.update(request_sha256=request_hash, is_simulated=False, completed_at=time.time())
    _write(folder / "checkpoint.json", {"phase": "terminal", "profile_id": request["profile_id"],
                                        "input_versions": request["task"].get("input_versions", {}),
                                        "status": result["status"], "exit_code": process.returncode if process else None,
                                        "truncated": overflow.is_set() if process and 'overflow' in locals() else False,
                                        "raw_provider_output_saved": False})
    _write(folder / "cli_result.json", result)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--worker", type=Path, required=True)
    args = parser.parse_args(argv)
    _worker(args.worker)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
