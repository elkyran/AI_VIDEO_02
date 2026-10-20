"""Bounded, read-only account diagnostics through the official Codex protocol.

Only initialize, account/read and account/rateLimits/read are sent. No thread,
turn, login, token refresh, credit purchase or reset is requested. Email is used
in memory solely to compute a profile identity hash; raw responses are not saved.
"""
from __future__ import annotations

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import threading
import time

MAX_LINE_BYTES = 64 * 1024
MAX_TOTAL_BYTES = 512 * 1024
MAX_MESSAGES = 128
_LABEL = re.compile(r"^[A-Za-z0-9_.-]{1,100}$")


class _DiagnosticError(Exception):
    def __init__(self, code, reason):
        super().__init__(reason)
        self.code = code
        self.reason = reason


def _label(value):
    if isinstance(value, str) and _LABEL.fullmatch(value) and not value.startswith(("sk-", "AIza", "ya29.")):
        return value
    return None


def _number(value, *, integer=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    if not math.isfinite(value) or value < 0 or (integer and not isinstance(value, int)):
        return None
    return value


def _credits(value):
    if not isinstance(value, dict):
        return None
    balance = value.get("balance")
    if isinstance(balance, str) and len(balance) <= 64:
        try:
            number = Decimal(balance)
            balance = str(number) if number.is_finite() and number >= 0 else None
        except InvalidOperation:
            balance = None
    else:
        balance = None
    return {"hasCredits": value.get("hasCredits") if type(value.get("hasCredits")) is bool else None,
            "unlimited": value.get("unlimited") if type(value.get("unlimited")) is bool else None,
            "balance": balance}


def _window(value):
    if not isinstance(value, dict):
        return None
    return {"usedPercent": _number(value.get("usedPercent")),
            "windowDurationMins": _number(value.get("windowDurationMins"), integer=True),
            "resetsAt": _number(value.get("resetsAt"), integer=True)}


def _bucket(value):
    if not isinstance(value, dict):
        return None
    return {"limitId": _label(value.get("limitId")), "planType": _label(value.get("planType")),
            "primary": _window(value.get("primary")), "secondary": _window(value.get("secondary")),
            "credits": _credits(value.get("credits")), "rateLimitReachedType": _label(value.get("rateLimitReachedType"))}


def _initial():
    return {"authenticated": False, "auth_mode": "unknown", "account_type": None,
            "identity_hash": None, "identity_source": "codex_app_server", "identity_verified": False,
            "plan_type": None, "ordinary_usage_allowed": None, "rate_limits": None,
            "rate_limits_by_id": None, "credits": None, "checked_at": datetime.now(timezone.utc).isoformat(),
            "error": None, "model_requests": 0,
            "billing_limitation": "Credit balances are observations, not proof that automatic purchases or future charges are disabled."}


def _windows_job(process):
    """Own descendant lifetimes, including a CLI which exits before its child."""
    if os.name != "nt":
        return None
    import ctypes
    from ctypes import wintypes
    class Basic(ctypes.Structure):
        _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                    ("flags", wintypes.DWORD), ("min_working", ctypes.c_size_t), ("max_working", ctypes.c_size_t),
                    ("active", wintypes.DWORD), ("affinity", ctypes.c_size_t),
                    ("priority", wintypes.DWORD), ("scheduling", wintypes.DWORD)]
    class IO(ctypes.Structure):
        _fields_ = [(name, ctypes.c_ulonglong) for name in ("reads", "writes", "other", "read_bytes", "write_bytes", "other_bytes")]
    class Extended(ctypes.Structure):
        _fields_ = [("basic", Basic), ("io", IO), ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                    ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
    kernel.CreateJobObjectW.restype = wintypes.HANDLE
    kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.CreateJobObjectW(None, None)
    limits = Extended()
    limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not handle or not kernel.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)) or not kernel.AssignProcessToJobObject(handle, wintypes.HANDLE(int(process._handle))):
        if handle:
            kernel.CloseHandle(handle)
        raise _DiagnosticError("cleanup_unverified", "Unable to establish ownership of the diagnostic process tree.")
    return kernel, handle


def _stop(process, job=None):
    """Close only the process created here; never a PID supplied by a client."""
    if job:
        job[0].CloseHandle(job[1])
    elif os.name != "nt":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if process.poll() is None:
        if os.name == "nt":
            try:
                subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                               stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                               timeout=3, creationflags=subprocess.CREATE_NO_WINDOW, check=False)
            except (OSError, subprocess.TimeoutExpired):
                process.kill()
        else:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        try:
            process.wait(timeout=2)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait(timeout=2)
    for pipe in (process.stdin, process.stdout, process.stderr):
        if pipe and not pipe.closed:
            pipe.close()


def diagnose_codex(command: list[str], *, cwd, env, timeout: float = 20.0) -> dict:
    """Return safe account evidence, with unknown fields left null on failure.

    The caller supplies a verified executable prefix and the isolated CODEX_HOME
    environment. Missing identity fails closed. A failed quota read does not
    erase a successfully verified identity, and never fabricates a zero balance.
    """
    result = _initial()
    process = None
    job = None
    readers = []
    stopped = threading.Event()
    overflow = threading.Event()
    messages = queue.Queue(maxsize=MAX_MESSAGES)
    phase = "initialize"
    try:
        if (not isinstance(command, list) or not command or
                not all(isinstance(arg, str) for arg in command) or not Path(command[0]).is_absolute() or
                Path(command[0]).suffix.lower() in {".cmd", ".bat", ".ps1"}):
            raise _DiagnosticError("client_unverified", "An absolute, verified native Codex command is required.")
        if (not isinstance(env, dict) or not env.get("CODEX_HOME") or not Path(env["CODEX_HOME"]).is_absolute()):
            raise _DiagnosticError("isolation_unverified", "An explicit isolated CODEX_HOME is required.")
        if any(re.match(r"^(OPENAI_|CODEX_API_|CODEX_ACCESS_TOKEN$|ANTHROPIC_|GEMINI_API_|GOOGLE_API_|AWS_|AZURE_)", str(key), re.I) for key in env):
            raise _DiagnosticError("wrong_auth_mode", "Inherited provider credentials or alternate routes are prohibited.")
        if type(timeout) not in (int, float) or not math.isfinite(timeout) or not 0 < timeout <= 60:
            raise _DiagnosticError("invalid_timeout", "Account diagnosis timeout must be positive and at most 60 seconds.")
        options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {"start_new_session": True}
        args = [*command, "app-server", "--stdio", "-c", 'forced_login_method="chatgpt"',
                "-c", 'cli_auth_credentials_store="file"', "-c", 'model_provider="openai"']
        process = subprocess.Popen(args, cwd=str(cwd), env=env, stdin=subprocess.PIPE,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE, **options)
        job = _windows_job(process)
        deadline = time.monotonic() + timeout

        def stdout_reader():
            total = 0
            count = 0
            try:
                while not stopped.is_set():
                    line = process.stdout.readline(MAX_LINE_BYTES + 1)
                    if not line:
                        messages.put_nowait(None)
                        return
                    total += len(line)
                    count += 1
                    if len(line) > MAX_LINE_BYTES or total > MAX_TOTAL_BYTES or count > MAX_MESSAGES:
                        overflow.set()
                        return
                    try:
                        item = json.loads(line)
                    except (ValueError, UnicodeError):
                        item = {"_protocol_invalid": True}
                    messages.put_nowait(item)
            except queue.Full:
                overflow.set()
            except (ValueError, OSError):
                pass

        def stderr_reader():
            # Discard raw diagnostics, which may contain account details. They
            # are not needed to establish authentication and must not leak.
            total = 0
            try:
                while not stopped.is_set():
                    chunk = process.stderr.read(4096)
                    if not chunk:
                        return
                    total += len(chunk)
                    if total > MAX_TOTAL_BYTES:
                        overflow.set()
                        return
            except (ValueError, OSError):
                pass

        for name, target in (("stdout", stdout_reader), ("stderr", stderr_reader)):
            reader = threading.Thread(target=target, name=f"codex-diagnostic-{process.pid}-{name}", daemon=True)
            reader.start()
            readers.append(reader)

        def send(payload):
            try:
                process.stdin.write((json.dumps(payload, separators=(",", ":")) + "\n").encode("utf-8"))
                process.stdin.flush()
            except (OSError, ValueError):
                raise _DiagnosticError("client_failed", "Codex closed the account diagnostic connection.") from None

        def receive(wanted):
            while True:
                if overflow.is_set():
                    raise _DiagnosticError("output_limit", "Codex account diagnostics exceeded the bounded output limit.")
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    raise _DiagnosticError("timeout", "Codex account diagnostics timed out.")
                try:
                    item = messages.get(timeout=min(remaining, .05))
                except queue.Empty:
                    continue
                if item is None:
                    raise _DiagnosticError("client_failed", "Codex ended the account diagnostic connection.")
                if not isinstance(item, dict) or item.get("_protocol_invalid"):
                    raise _DiagnosticError("protocol_invalid", "Codex returned malformed account protocol data.")
                if item.get("id") != wanted:
                    continue
                if "error" in item:
                    raise _DiagnosticError("client_response_error", "The official Codex account endpoint rejected the diagnostic read.")
                response = item.get("result")
                if not isinstance(response, dict):
                    raise _DiagnosticError("protocol_invalid", "Codex returned an unexpected account response shape.")
                return response

        send({"id": 1, "method": "initialize", "params": {"clientInfo": {
            "name": "ai_video_02_account_check", "title": "AI_VIDEO_02 account verification", "version": "0.1.0"}}})
        receive(1)
        send({"method": "initialized", "params": {}})
        phase = "account"
        send({"id": 2, "method": "account/read", "params": {"refreshToken": False}})
        account_reply = receive(2)
        account = account_reply.get("account")
        if account is None:
            raise _DiagnosticError("not_authenticated", "The isolated Codex profile is not authenticated.")
        if not isinstance(account, dict):
            raise _DiagnosticError("protocol_invalid", "Codex returned an unexpected account record.")
        result["account_type"] = _label(account.get("type"))
        if account.get("type") != "chatgpt":
            raise _DiagnosticError("wrong_auth_mode", "Codex did not confirm the permitted ChatGPT authentication mode.")
        result["auth_mode"] = "chatgpt_subscription"
        result["plan_type"] = _label(account.get("planType"))
        email = account.get("email")
        if not isinstance(email, str) or not email.strip() or len(email) > 512 or "@" not in email:
            raise _DiagnosticError("identity_unavailable", "The official Codex endpoint did not provide an account identity.")
        result.update(authenticated=True, identity_verified=True,
                      identity_hash=hashlib.sha256(("codex:" + email.strip().casefold()).encode("utf-8")).hexdigest())
        del email
        del account
        del account_reply
        phase = "quota"
        send({"id": 3, "method": "account/rateLimits/read"})
        limits = receive(3)
        result["ordinary_usage_allowed"] = limits.get("ordinaryUsageAllowed") if type(limits.get("ordinaryUsageAllowed")) is bool else None
        result["rate_limits"] = _bucket(limits.get("rateLimits"))
        by_id = limits.get("rateLimitsByLimitId")
        if isinstance(by_id, dict):
            result["rate_limits_by_id"] = {key: _bucket(value) for key, value in by_id.items() if _label(key)}
        codex_bucket = (result["rate_limits_by_id"] or {}).get("codex") or result["rate_limits"]
        if codex_bucket:
            result["credits"] = codex_bucket["credits"]
        if result["rate_limits"] is None and result["rate_limits_by_id"] is None:
            raise _DiagnosticError("quota_unavailable", "Codex supplied no current quota or credit observation.")
    except _DiagnosticError as exc:
        result["error"] = {"code": "quota_unavailable" if phase == "quota" else exc.code,
                           "reason": exc.reason, "cause": exc.code}
    except (OSError, ValueError, TypeError):
        result["error"] = {"code": "quota_unavailable" if phase == "quota" else "client_start_failed",
                           "reason": "The official Codex account diagnostic process could not complete."}
    finally:
        stopped.set()
        if process is not None:
            _stop(process, job)
        for reader in readers:
            reader.join(timeout=2)
        if any(reader.is_alive() for reader in readers):
            result["error"] = {"code": "cleanup_unverified", "reason": "Account diagnostic stream cleanup could not be verified."}
            result["authenticated"] = False
            result["identity_verified"] = False
    return result
