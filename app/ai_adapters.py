"""Offline provider boundary; real clients are deliberately unavailable for execution.

These adapters never log in, read credentials or perform model requests. The poll
contract allows the persistent coordinator to hold a profile until a terminal
event, including a simulated late result after cancellation. A future live
implementation must be verified against its installed client before activation.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import time
from typing import Any, Callable, Iterable


PROVIDERS = ("codex", "claude", "gemini")
FAILURE_KINDS = frozenset({
    "quota_exhausted", "rate_limited", "authentication", "billing", "network",
    "overloaded", "permissions", "invalid_input", "local_turn_limit", "interrupted", "unknown",
})
CAPABILITIES = (
    "text_input", "structured_output", "events", "completion", "cancellation", "continuation",
    "images", "video", "audio", "local_materials", "quota_observation", "profile_isolation",
)
_SECRET_KEY = re.compile(r"(?:api.?key|access.?token|refresh.?token|oauth.?token|authorization|password|secret|cookie|credential)", re.I)
_SENSITIVE_ENV = re.compile(
    r"^(?:OPENAI_|CODEX_API_|ANTHROPIC_|CLAUDE_CODE_USE_|GEMINI_API_|GOOGLE_API_|GOOGLE_GENAI_|"
    r"GOOGLE_CLOUD_|GOOGLE_APPLICATION_CREDENTIALS$|VERTEX_|AZURE_OPENAI_|AWS_|HTTP_PROXY$|HTTPS_PROXY$|ALL_PROXY$)", re.I
)
_TOKEN_TEXT = re.compile(r"\b(?:sk-[A-Za-z0-9_-]{8,}|AIza[A-Za-z0-9_-]{12,}|ya29\.[A-Za-z0-9_.-]+)\b")
_ASSIGNED_SECRET = re.compile(
    r"((?:api[_-]?key|access[_-]?token|refresh[_-]?token|password|secret|authorization)\s*[:=]\s*)[^\s,;]+", re.I
)


def sanitize(value: Any) -> Any:
    """Remove credential-like fields and common tokens before local event storage.

    This is defense in depth, not permission to read or import credential files.
    """
    if isinstance(value, dict):
        return {str(key): "[REDACTED]" if _SECRET_KEY.search(str(key)) else sanitize(item)
                for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [sanitize(item) for item in value]
    if isinstance(value, str):
        value = re.sub(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*", "Bearer [REDACTED]", value)
        return _ASSIGNED_SECRET.sub(r"\1[REDACTED]", _TOKEN_TEXT.sub("[REDACTED]", value))
    if value is None or isinstance(value, (int, float, bool)):
        return value
    return sanitize(str(value))


def unknown_usage(provider: str, profile_id: str | None = None) -> dict:
    return {"provider": provider, "profile_id": profile_id, "model_scope": None,
            "observed_at": None, "source": None, "window_kind": None, "used": None,
            "remaining": None, "limit": None, "reset_at": None, "confidence": "unknown"}


def default_profiles() -> list[dict]:
    """Nine positions, not nine authenticated or independent accounts."""
    return [{"profile_id": f"{provider}-{slot}", "provider": provider, "slot": slot,
             "label": f"{'GPT' if provider == 'codex' else provider.title()}-{slot}",
             "client_config_ref": None, "model": None, "state": "not_configured",
             "auth_mode": "unknown", "identity_verified": False, "observed_identity_label": None,
             "last_auth_check": None, "last_successful_run": None,
             "rotation_on_quota": False, "rotation_support": "unverified", "rotation_evidence": None,
             "usage": unknown_usage(provider, f"{provider}-{slot}")}
            for provider in PROVIDERS for slot in (1, 2, 3)]


class BudgetGuardError(ValueError):
    code = "wrong_auth_mode"


def guard_budget(profile: dict) -> None:
    """Reject explicit paid routes; unknown authentication never implies ready."""
    for field in ("auth_mode", "billing_mode", "access_mode", "backend"):
        mode = str(profile.get(field) or "unknown").lower().replace("-", "_")
        if field == "auth_mode" and profile.get(field) == "gemini_api_free":
            from .gemini_free_route import FREE_MODELS, ROUTE_ID
            # These fields come from AccountManager's current validated public
            # profile. Runtime rechecks the full route and billing attestation
            # before launching; a mode label alone grants no API permission.
            if (profile.get("provider") == "gemini" and profile.get("access_route") == ROUTE_ID
                    and profile.get("route_configuration_valid") is True
                    and profile.get("billing_safe") is True
                    and isinstance(profile.get("model"), str) and profile["model"] in FREE_MODELS):
                continue
        if any(marker in mode for marker in ("api", "vertex", "console", "gateway", "pay_as_you_go")):
            raise BudgetGuardError(f"Paid/alternate access mode is prohibited ({field}).")
    if profile.get("allow_paid_api") or profile.get("purchase_credits"):
        raise BudgetGuardError("Paid API and credit purchases are prohibited.")
    for field in ("environment", "env", "provider_overrides"):
        values = profile.get(field, {})
        if values:
            if not isinstance(values, dict) or any(_SENSITIVE_ENV.search(str(key)) or _SECRET_KEY.search(str(key)) for key in values):
                raise BudgetGuardError(f"Conflicting provider overrides are prohibited ({field}).")


def child_environment(source: dict[str, str] | None = None) -> dict[str, str]:
    """Build an isolated copy without API/alternate-provider/proxy overrides.

    This helper changes no global environment and is not a live launch facility.
    """
    return {key: value for key, value in (os.environ if source is None else source).items()
            if not _SENSITIVE_ENV.search(key) and not _SECRET_KEY.search(key)}


def classify_failure(events: Iterable[dict], exit_code: int | None = None) -> dict:
    """Only our explicit normalized error kinds are trusted, never text or 429.

    Unverified provider-specific exit codes intentionally remain unknown. Live
    decoders must add versioned evidence before producing normalized errors.
    """
    items = list(events)
    for event in reversed(items):
        candidate = event.get("failure") or event.get("payload", {})
        if event.get("type", event.get("kind")) not in {"failed", "interrupted"}:
            continue
        if isinstance(candidate, dict) and candidate.get("kind") in FAILURE_KINDS:
            result = sanitize(deepcopy(candidate))
            result.setdefault("message", "Provider run failed.")
            result.setdefault("retryable", result["kind"] in {"network", "rate_limited", "overloaded"})
            result.setdefault("retry_after", None)
            result.setdefault("quota_reset_at", None)
            result.setdefault("certainty", "confirmed" if result.get("is_simulated") else "unknown")
            result.setdefault("evidence", [])
            result["quota_confirmed"] = bool(result["kind"] == "quota_exhausted" and
                                               (result.get("is_simulated") or result.get("quota_confirmed") is True))
            return result
    return {"kind": "unknown", "message": "No verified structured failure classification.",
            "retryable": False, "retry_after": None, "quota_reset_at": None,
            "quota_confirmed": False, "certainty": "unknown", "evidence": {"exit_code": exit_code}}


class ProviderAdapter:
    """Fail-closed placeholder for an official client; no live process starts."""

    is_simulated = False

    def __init__(self, provider: str):
        if provider not in PROVIDERS:
            raise ValueError("Unknown provider")
        self.provider = provider
        self._sequence = 0
        self._runs: dict[str, dict] = {}

    def probe(self, *, include_version: bool = False) -> dict:
        executable = shutil.which(self.provider)
        result = {"provider": self.provider, "executable": executable, "version": None,
                  "installed": executable is not None, "auth": "unknown", "model": None,
                  "capabilities": {name: "unverified" for name in CAPABILITIES},
                  "rotation_support": "unverified", "rotation_enabled": False,
                  "live_execution": "unavailable", "is_simulated": False,
                  "limitations": ["Client authentication, billing mode, identities and features are not verified offline."]}
        # Windows script shims need a shell and may perform arbitrary startup work.
        # They are inventoried only. No npm/npx fallback or login/status is run.
        if include_version and executable:
            if Path(executable).suffix.lower() in {".cmd", ".bat", ".ps1"}:
                result["limitations"].append("Script shim was not executed for version detection.")
            else:
                try:
                    run = subprocess.run([executable, "--version"], shell=False, stdin=subprocess.DEVNULL,
                                         stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                         timeout=5, env=child_environment(), check=False)
                    if run.returncode == 0:
                        result["version"] = sanitize(run.stdout.strip())[:512] or None
                    else:
                        result["limitations"].append(f"Local version probe failed with exit code {run.returncode}.")
                except (OSError, subprocess.TimeoutExpired) as error:
                    result["limitations"].append(f"Local version probe unavailable: {type(error).__name__}.")
        return result

    def check_auth(self, profile: dict) -> dict:
        try:
            guard_budget(profile)
        except BudgetGuardError as error:
            return {"status": "wrong_auth_mode", "reason": str(error), "identity_verified": False}
        return {"status": "unknown", "reason": "Official login and billing mode have not been verified.",
                "identity_verified": False, "observed_identity_label": None}

    def _new_run(self, task: dict, profile: dict, attempt_dir: str | Path | None) -> tuple[str, dict]:
        self._sequence += 1
        handle = f"{self.provider}-{'fake' if self.is_simulated else 'blocked'}-{self._sequence}"
        run = {"task": deepcopy(task), "profile": deepcopy(profile), "event_sequence": 0,
               "terminal": False, "cancelled": False, "event_file": None}
        if attempt_dir is not None:
            directory = Path(attempt_dir)
            directory.mkdir(parents=True, exist_ok=True)
            event_file = directory / "adapter_events.jsonl"
            # An attempt's existing log is evidence and must never be overwritten.
            with event_file.open("x", encoding="utf-8"):
                pass
            run["event_file"] = event_file
        self._runs[handle] = run
        return handle, run

    def _event(self, handle: str, kind: str, payload: dict) -> dict:
        run = self._runs[handle]
        run["event_sequence"] += 1
        event = {"event_id": f"{handle}:{run['event_sequence']}", "run_id": handle,
                 "task_id": run["task"].get("task_id", run["task"].get("id")),
                 "attempt_id": run["task"].get("attempt_id"), "provider": self.provider,
                 "profile_id": run["profile"].get("profile_id", run["profile"].get("id")),
                 "provider_session_id": None, "timestamp": datetime.now(timezone.utc).isoformat(),
                 "type": kind, "kind": kind, "is_simulated": self.is_simulated,
                 "payload": sanitize({**payload, "is_simulated": self.is_simulated})}
        if kind == "completed":
            event["result"] = event["payload"]["result"]
        elif kind in {"failed", "interrupted"}:
            event["failure"] = event["payload"]
        if run["event_file"]:
            with run["event_file"].open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(event, ensure_ascii=False, allow_nan=False) + "\n")
        return event

    def start(self, task: dict, profile: dict, attempt_dir: str | Path | None = None) -> str:
        handle, run = self._new_run(task, profile, attempt_dir)
        authentication = self.check_auth(profile)
        run["blocked"] = {"kind": "billing" if authentication["status"] == "wrong_auth_mode" else "unknown",
                          "code": "wrong_auth_mode" if authentication["status"] == "wrong_auth_mode" else "capability_unavailable",
                          "message": authentication["reason"], "retryable": False, "retry_after": None,
                          "quota_reset_at": None, "quota_confirmed": False, "certainty": "confirmed",
                          "evidence": ["Live adapter has not been enabled."]}
        return handle

    def poll(self, handle: str) -> list[dict]:
        run = self._runs.get(handle)
        if run is None:
            raise KeyError("Unknown adapter handle")
        if run["terminal"]:
            return []
        run["terminal"] = True
        return [self._event(handle, "failed", run["blocked"])]

    def cancel(self, handle: str) -> dict:
        run = self._runs.get(handle)
        return {"status": "stopped" if run is not None else "unknown", "process_started": False}

    def classify_failure(self, events: Iterable[dict], exit_code: int | None = None) -> dict:
        return classify_failure(events, exit_code)

    def continue_from_checkpoint(self, task: dict, profile: dict, attempt_dir: str | Path | None = None,
                                 checkpoint: dict | None = None) -> str:
        resumed = deepcopy(task)
        if checkpoint is not None:
            resumed.setdefault("context", {})["checkpoint"] = deepcopy(checkpoint)
        return self.start(resumed, profile, attempt_dir)


class CodexAdapter(ProviderAdapter):
    def __init__(self):
        super().__init__("codex")


class ClaudeAdapter(ProviderAdapter):
    def __init__(self):
        super().__init__("claude")


class GeminiAdapter(ProviderAdapter):
    def __init__(self):
        super().__init__("gemini")


class FakeAdapter(ProviderAdapter):
    """Deterministic poll-driven simulation, explicitly distinct from live output.

    ``simulation`` can be a dict or a list of dicts, selected once per task attempt.
    Supported fields: outcome (completed or a failure kind), result, delay_polls,
    delay_seconds, late_after_cancel, retry_after and quota_reset_at. Tests can
    inject a clock, avoiding sleep; scripts can also be keyed by task id.
    """

    is_simulated = True

    def __init__(self, provider: str = "codex", scripts: dict | None = None,
                 *, clock: Callable[[], float] = time.monotonic):
        super().__init__(provider)
        self.scripts = deepcopy(scripts or {})
        self._attempt_counts: dict[str, int] = {}
        self._clock = clock

    def probe(self, *, include_version: bool = False) -> dict:
        return {"provider": self.provider, "executable": None, "version": "fake-v1", "installed": True,
                "auth": "simulated", "model": None, "capabilities": {name: "unverified" for name in CAPABILITIES},
                "rotation_support": "simulation_only", "rotation_enabled": False,
                "live_execution": "unavailable", "is_simulated": True,
                "limitations": ["Simulator verifies local scheduling, never provider capabilities or media quality."]}

    def check_auth(self, profile: dict) -> dict:
        try:
            guard_budget(profile)
        except BudgetGuardError as error:
            return {"status": "wrong_auth_mode", "reason": str(error), "identity_verified": False}
        return {"status": "ready", "is_simulated": True, "identity_verified": False,
                "observed_identity_label": None}

    def start(self, task: dict, profile: dict, attempt_dir: str | Path | None = None) -> str:
        guard_budget(profile)
        task_id = str(task.get("task_id", task.get("id", "anonymous")))
        context = task.get("context") or {}
        script = task.get("simulation", context.get("simulation", self.scripts.get(task_id, {})))
        count = self._attempt_counts.get(task_id, 0)
        if isinstance(script, list):
            script = script[min(count, len(script) - 1)] if script else {}
        script = deepcopy(script)
        if not isinstance(script, dict):
            raise ValueError("Simulation must be a dictionary or list of dictionaries")
        outcome = script.get("outcome", "completed")
        if outcome not in FAILURE_KINDS | {"completed"}:
            raise ValueError("Unknown simulated outcome")
        polls = script.get("delay_polls", 0)
        seconds = script.get("delay_seconds", 0)
        if type(polls) is not int or polls < 0 or not isinstance(seconds, (int, float)) or seconds < 0:
            raise ValueError("Simulation delay must be nonnegative")
        handle, run = self._new_run(task, profile, attempt_dir)
        self._attempt_counts[task_id] = count + 1
        run.update({"script": script, "polls_left": polls, "ready_at": self._clock() + seconds,
                    "started": False, "outcome": outcome,
                    "result": deepcopy(script.get("result", context.get("fixture_result", {"text": "Simulated result."})))})
        return handle

    def poll(self, handle: str) -> list[dict]:
        run = self._runs.get(handle)
        if run is None:
            raise KeyError("Unknown adapter handle")
        if run["terminal"]:
            return []
        events = []
        if not run["started"]:
            run["started"] = True
            events.append(self._event(handle, "run_started", {"process_started": False, "model": None}))
        if run["cancelled"] and not run["script"].get("late_after_cancel", False):
            run["terminal"] = True
            events.append(self._event(handle, "failed", self._failure("interrupted", run["script"])))
            return events
        if run["polls_left"] > 0:
            run["polls_left"] -= 1
            events.append(self._event(handle, "progress", {"message": "Waiting for simulated completion."}))
            return events
        if self._clock() < run["ready_at"]:
            return events
        run["terminal"] = True
        if run["outcome"] == "completed":
            events.append(self._event(handle, "completed", {"result": run["result"], "model": None,
                                                           "usage": unknown_usage(self.provider)}))
        else:
            events.append(self._event(handle, "failed", self._failure(run["outcome"], run["script"])))
        return events

    @staticmethod
    def _failure(kind: str, script: dict) -> dict:
        return {"kind": kind, "message": script.get("message", f"Simulated {kind}."),
                "retryable": kind in {"network", "rate_limited", "overloaded"},
                "retry_after": script.get("retry_after"), "quota_reset_at": script.get("quota_reset_at"),
                "quota_confirmed": kind == "quota_exhausted", "certainty": "confirmed",
                "is_simulated": True, "evidence": [{"source": "offline_simulator", "kind": kind}]}

    def cancel(self, handle: str) -> dict:
        run = self._runs.get(handle)
        if run is None:
            return {"status": "unknown", "process_started": False}
        if run["terminal"]:
            return {"status": "stopped", "process_started": False}
        run["cancelled"] = True
        return {"status": "stopping", "process_started": False}
