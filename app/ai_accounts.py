"""Official-client account homes and a resumable, single-login wizard.

Credential contents are never read here. Only the official client's status
command may diagnose authentication. Metadata and identity fingerprints are
separate from the native credential stores, which live outside the repository.
"""
from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
from typing import Any
import uuid

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    from app.ai_adapters import sanitize, unknown_usage
    from app.development import atomic_write, locked
else:
    from .ai_adapters import sanitize, unknown_usage
    from .development import atomic_write, locked

from app import gemini_free_route as free_routes


PROVIDERS = ("codex", "claude", "gemini")
ACTIVE_PROVIDERS = ("codex", "gemini")
ORDER = tuple(f"{provider}-{slot}" for provider in ACTIVE_PROVIDERS for slot in (1, 2, 3, 4))
LEGACY_ORDER = tuple(f"{provider}-{slot}" for slot in (1, 2, 3) for provider in PROVIDERS)
HISTORICAL_ORDER = tuple(f"claude-{slot}" for slot in (1, 2, 3))
_PROFILE = re.compile(r"^(?:(codex|gemini)-([1234])|claude-([123]))$")
_ENV_NAMES = frozenset({"PATH", "PATHEXT", "SYSTEMROOT", "WINDIR", "SYSTEMDRIVE", "COMSPEC",
                        "TEMP", "TMP", "TMPDIR", "LOCALAPPDATA", "APPDATA", "PROGRAMFILES",
                        "PROGRAMFILES(X86)", "PROGRAMW6432", "ALLUSERSPROFILE", "PROGRAMDATA",
                        "USERNAME", "USERDOMAIN", "LANG", "LC_ALL", "TERM", "COLORTERM"})
CODEX_CONFIG = 'forced_login_method = "chatgpt"\ncli_auth_credentials_store = "file"\ncheck_for_update_on_startup = false\n'
CLAUDE_SETTINGS = {"forceLoginMethod": "claudeai", "disableAllHooks": True,
                   "permissions": {"defaultMode": "default"}}
GEMINI_SETTINGS = {
    "security": {"auth": {"selectedType": "oauth-personal", "enforcedType": "oauth-personal"}},
    "advanced": {"ignoreLocalEnv": True}, "telemetry": {"enabled": False},
    "general": {"enableAutoUpdate": False}, "tools": {"core": ["__studio_no_tools__"]},
    "experimental": {"enableAgents": False}, "hooksConfig": {"enabled": False}, "mcpServers": {},
}
# Exact template previously written by this package. Only this known version
# can be explicitly migrated; unknown user keys are never overwritten.
GEMINI_SETTINGS_LEGACY_HOOKS = deepcopy(GEMINI_SETTINGS)
GEMINI_SETTINGS_LEGACY_HOOKS.pop("hooksConfig")
GEMINI_SETTINGS_LEGACY_HOOKS["hooks"] = {"enabled": False}


class AccountError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _json(path: Path, payload: dict) -> None:
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2, allow_nan=False) + "\n")


def _identity(provider: str, value: str) -> str:
    return hashlib.sha256((provider + ":" + value.strip().casefold()).encode("utf-8")).hexdigest()


def isolated_environment(provider: str, profile_home: Path, source: dict | None = None) -> dict[str, str]:
    """Allowlisted child environment; never changes the calling Codex session."""
    if provider not in PROVIDERS:
        raise AccountError("invalid_provider", "Unknown provider.")
    env = {str(k): str(v) for k, v in (os.environ if source is None else source).items()
           if str(k).upper() in _ENV_NAMES}
    home = str(Path(profile_home).resolve())
    env.update({"HOME": home, "USERPROFILE": home})
    env[{"codex": "CODEX_HOME", "claude": "CLAUDE_CONFIG_DIR", "gemini": "GEMINI_CLI_HOME"}[provider]] = home
    if provider == "claude":
        env["DISABLE_AUTOUPDATER"] = "1"
    if provider == "gemini":
        env["GEMINI_TELEMETRY_ENABLED"] = "false"
    return env


def _process_token(pid: int) -> str | None:
    """Birth identity avoids treating a recycled PID as an active login."""
    if not isinstance(pid, int) or pid <= 0:
        return None
    if os.name != "nt":
        try:
            return Path(f"/proc/{pid}/stat").read_text().split(") ", 1)[1].split()[19]
        except (OSError, IndexError):
            return None
    import ctypes
    from ctypes import wintypes
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel.OpenProcess.restype = wintypes.HANDLE
    kernel.GetProcessTimes.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    kernel.CloseHandle.argtypes = [wintypes.HANDLE]
    handle = kernel.OpenProcess(0x1000, False, pid)
    if not handle:
        return None
    try:
        values = [wintypes.FILETIME() for _ in range(4)]
        if not kernel.GetProcessTimes(handle, *(ctypes.byref(v) for v in values)):
            return None
        return str((values[0].dwHighDateTime << 32) | values[0].dwLowDateTime)
    finally:
        kernel.CloseHandle(handle)


class AccountManager:
    """One manager per panel; persisted state remains usable after restart.

    ``clients`` is trusted setup evidence from actual --version/--help probes,
    not browser input. A path found on PATH alone never enables a client.
    """

    def __init__(self, workspace: str | Path, profile_root: str | Path | None = None,
                 clients: dict | None = None, *, runner=None, launcher=None, codex_diagnoser=None):
        self.workspace = Path(workspace).resolve()
        repo = Path(__file__).resolve().parents[1]
        if self.workspace == repo / "workspace" / "studio":
            raise AccountError("production_workspace", "Account setup needs a development workspace.")
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / ".local" / "share"))
        self.profile_root = Path(profile_root or base / "AI_VIDEO_02" / "profiles").resolve()
        if self.profile_root == repo or repo in self.profile_root.parents:
            raise AccountError("unsafe_profile_root", "Credential homes must be outside the repository.")
        self.state_path = self.workspace / "ai" / "accounts.json"
        self.clients_path = self.workspace / "ai" / "client_capabilities.json"
        self._thread_lock = threading.RLock()
        self._runner = runner or subprocess.run
        self._launcher = launcher or subprocess.Popen
        self._codex_diagnoser = codex_diagnoser
        self._processes: dict[str, Any] = {}
        self.clients = deepcopy(clients or {})
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        with self._thread_lock, locked(self.state_path.with_suffix(".lock")):
            if not self.state_path.exists():
                _json(self.state_path, self._initial())
            state = self._read(allow_legacy=True)
            # Desktop MSIX can redirect LocalAppData after the directory is
            # first created. Compare both resolved paths; never move credentials.
            if not state.get("profile_root") or Path(state["profile_root"]).resolve() != self.profile_root:
                raise AccountError("profile_root_changed", "Saved account homes differ; refusing to move credentials.")
            if state["schema_version"] == 1:
                self._migrate_profiles(state)
            if clients is not None:
                self.update_clients(clients)
            elif self.clients_path.exists():
                self.clients = json.loads(self.clients_path.read_text(encoding="utf-8"))

    @staticmethod
    def _new_profile(profile_id: str) -> dict:
        provider, slot = profile_id.split("-")
        return {"profile_id": profile_id, "id": profile_id, "provider": provider, "slot": int(slot),
                             "label": f"{'GPT' if provider == 'codex' else provider.title()}-{slot}",
                             "auth_state": "not_authenticated", "auth_mode": "unknown", "auth_source": None,
                             "access_state": "unverified", "billing_confirmed": False, "billing_safe": False,
                             "billing_confirmation_source": None, "billing_confirmed_at": None,
                             "billing_confirmation_expires_at": None, "billing_identity_hash": None,
                             "identity_verified": False, "identity_source": None, "identity_hash": None,
                             "auth_generation": 0,
                             "account_label": None, "distinct_account": False, "duplicate_of": None,
                             "last_checked": None, "last_successful_run": None, "reason_code": "authentication",
                             "reason": "Требуется вход в отдельный профиль.", "setup_complete": False,
                             "isolation_state": "unverified", "rotation_on_quota": False, "observed_attempts": []}

    def _initial(self) -> dict:
        return {"schema_version": 2, "generation": 0, "profile_root": str(self.profile_root),
                "profiles": [self._new_profile(item) for item in ORDER], "historical_profiles": [],
                "wizard": {"status": "idle", "current": ORDER[0], "completed": [],
                "skipped": {}, "historical_completed": [], "historical_skipped": {},
                "paused_profiles": [], "active_login": None, "updated_at": _now()}}

    def _read(self, *, allow_legacy: bool = False) -> dict:
        data = json.loads(self.state_path.read_text(encoding="utf-8"))
        version = data.get("schema_version")
        profiles = data.get("profiles", [])
        expected = LEGACY_ORDER if allow_legacy and version == 1 else ORDER
        ids = [p.get("profile_id") for p in profiles if isinstance(p, dict)]
        valid = (version == 2 or (allow_legacy and version == 1)) and len(ids) == len(expected) and set(ids) == set(expected)
        valid = valid and all(p.get("provider") == p["profile_id"].split("-")[0] for p in profiles)
        historical = data.get("historical_profiles", [])
        history_ids = [p.get("profile_id") for p in historical if isinstance(p, dict)]
        valid = valid and len(history_ids) == len(historical) and len(set(history_ids)) == len(history_ids) and set(history_ids) <= set(HISTORICAL_ORDER)
        if not valid:
            raise AccountError("state_invalid", "Account registry schema is invalid; no automatic reset.")
        return data

    def _migrate_profiles(self, data: dict) -> None:
        """Migrate metadata only, with the scheduler stopped and no live login.

        Existing identity, access evidence, native homes and original wizard
        state remain available. A retry after interruption uses the same backup.
        """
        try:
            with locked(self.workspace / "ai" / "coordinator.lock"):
                if self._active(deepcopy(data)):
                    raise AccountError("login_active", "Завершите официальный вход перед миграцией профилей.")
                database = self.workspace / "registry.sqlite"
                if database.exists():
                    try:
                        connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                        try:
                            has_attempts = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_attempts'").fetchone()
                            busy = has_attempts and connection.execute("SELECT 1 FROM ai_attempts WHERE status IN ('running','stopping') LIMIT 1").fetchone()
                        finally:
                            connection.close()
                    except sqlite3.Error as exc:
                        raise AccountError("migration_busy", "Не удалось подтвердить отсутствие активных задач; миграция отложена.") from exc
                    if busy:
                        raise AccountError("migration_busy", "Завершите активные задачи перед миграцией профилей.")
                original = self.state_path.read_bytes()
                digest = hashlib.sha256(original).hexdigest()
                backup = self.state_path.with_name(f"accounts.v1.{digest[:16]}.backup.json")
                if backup.resolve() != self.state_path.parent.resolve() / backup.name:
                    raise AccountError("unsafe_backup", "Резервный файл метаданных перенаправлен; миграция остановлена.")
                if backup.exists():
                    if backup.read_bytes() != original:
                        raise AccountError("migration_refused", "Существующая резервная копия отличается; она сохранена.")
                else:
                    with backup.open("xb") as stream:
                        stream.write(original)
                existing = {p["profile_id"]: p for p in data["profiles"]}
                previous_wizard = deepcopy(data["wizard"])
                data["profiles"] = [existing[item] if item in existing else self._new_profile(item) for item in ORDER]
                data["historical_profiles"] = [existing[item] for item in HISTORICAL_ORDER]
                wizard = data["wizard"]
                wizard["historical_completed"] = [item for item in wizard["completed"] if item not in ORDER]
                wizard["historical_skipped"] = {key: value for key, value in wizard["skipped"].items() if key not in ORDER}
                wizard["completed"] = [item for item in wizard["completed"] if item in ORDER]
                wizard["skipped"] = {key: value for key, value in wizard["skipped"].items() if key in ORDER}
                wizard["paused_profiles"] = []
                active = wizard.get("active_login")
                if active and active.get("profile_id") not in ORDER:
                    wizard["active_login"] = None
                if wizard.get("current") not in ORDER or wizard.get("status") in {"complete", "complete_with_skips"}:
                    wizard["current"] = self._next(wizard)
                    if wizard.get("status") == "paused":
                        wizard["paused_from"] = "awaiting_next"
                    else:
                        wizard["status"] = "awaiting_next"
                wizard["updated_at"] = _now()
                data.update(schema_version=2, profile_migration={"id": "MAI05-eight-active-v1", "at": _now(),
                    "backup_path": str(backup), "before_sha256": digest, "previous_wizard": previous_wizard})
                self._save(data)
        except AccountError:
            raise
        except ValueError as exc:
            raise AccountError("migration_busy", "Остановите координатор перед миграцией профилей.") from exc

    def _save(self, data: dict) -> None:
        data["generation"] += 1
        _json(self.state_path, data)

    @staticmethod
    def _profile(data: dict, profile_id: str) -> dict:
        if not isinstance(profile_id, str) or not _PROFILE.fullmatch(profile_id):
            raise AccountError("invalid_profile", "Unknown profile.")
        if profile_id not in ORDER:
            raise AccountError("profile_inactive", "Claude сохранён в истории и исключён из активной команды.")
        return next(p for p in data["profiles"] if p["profile_id"] == profile_id)

    def update_clients(self, clients: dict) -> None:
        for provider, descriptor in clients.items():
            if provider not in PROVIDERS or not isinstance(descriptor, dict):
                raise AccountError("invalid_client", "Invalid official client descriptor.")
            command = descriptor.get("command", [])
            if not isinstance(command, list) or not all(isinstance(a, str) for a in command):
                raise AccountError("invalid_client", "Client command must be an argument array.")
        saved = json.loads(self.clients_path.read_text(encoding="utf-8")) if self.clients_path.exists() else self.clients
        historical = {provider: deepcopy(descriptor) for provider, descriptor in saved.items()
                      if provider in PROVIDERS and provider not in ACTIVE_PROVIDERS and provider not in clients}
        self.clients = {**historical, **deepcopy(clients)}
        _json(self.clients_path, sanitize(self.clients))

    def _client(self, provider: str) -> dict:
        return deepcopy(self.clients.get(provider, {}))

    def _free_route(self, profile_id: str) -> dict | None:
        if not profile_id.startswith("gemini-"):
            return None
        profile = self._profile(self._read(), profile_id)
        return profile.get("gemini_free_route") if profile.get("access_route") == free_routes.ROUTE_ID else None

    @staticmethod
    def _validate_free_route(route: dict, profile_id: str) -> None:
        try:
            free_routes.validate_route(route, profile_id)
        except free_routes.FreeRouteError as exc:
            raise AccountError(exc.code, str(exc)) from exc

    def _effective_client(self, profile: dict) -> dict:
        client = self._client(profile["provider"])
        if profile.get("access_route") == free_routes.ROUTE_ID:
            try:
                self._validate_free_route(profile.get("gemini_free_route"), profile["profile_id"])
                if client.get("version") != free_routes.CLIENT_VERSION:
                    raise AccountError("client_unverified", "Изоляция API-ключа подтверждена только для Gemini CLI 0.51.0.")
            except AccountError as exc:
                client.update(access_route_supported=False, access_route_reason=str(exc), route_error=exc.code)
            else:
                client.update(access_route_supported=True, access_route_reason=None,
                              access_route=free_routes.ROUTE_ID, route_configuration_valid=True)
        return client

    def _paths(self, profile_id: str) -> tuple[Path, Path, Path]:
        if not _PROFILE.fullmatch(profile_id):
            raise AccountError("invalid_profile", "Unknown profile.")
        if self._free_route(profile_id):
            return free_routes.route_paths(self.profile_root, profile_id)
        home = self.profile_root / profile_id
        cwd = self.profile_root.parent / "work" / profile_id
        settings = home / ({"codex": "config.toml", "claude": "settings.json", "gemini": ".gemini/settings.json"}[profile_id.split("-")[0]])
        return home, cwd, settings

    def _prepare(self, profile_id: str) -> None:
        if profile_id not in ORDER:
            raise AccountError("profile_inactive", "Профиль исключён из активной команды.")
        route = self._free_route(profile_id)
        if route:
            try:
                free_routes.prepare_home(self.profile_root, profile_id, route)
            except free_routes.FreeRouteError as exc:
                raise AccountError(exc.code, str(exc)) from exc
            self._config_guard(profile_id)
            return
        home, cwd, config = self._paths(profile_id)
        self._validate_paths(profile_id)
        home.mkdir(parents=True, exist_ok=True)
        cwd.mkdir(parents=True, exist_ok=True)
        self._validate_paths(profile_id)
        # Stop project/.env discovery above this intentionally empty work folder.
        (cwd / ".git").mkdir(exist_ok=True)
        env_file = cwd / ".env"
        if not env_file.exists():
            env_file.write_text("# AI_VIDEO_02: isolated client work folder; no API configuration.\n", encoding="utf-8")
        if not config.exists():
            config.parent.mkdir(parents=True, exist_ok=True)
            provider = profile_id.split("-")[0]
            with config.open("x", encoding="utf-8") as stream:
                stream.write(CODEX_CONFIG if provider == "codex" else json.dumps(
                    CLAUDE_SETTINGS if provider == "claude" else GEMINI_SETTINGS, indent=2) + "\n")
        self._config_guard(profile_id)

    def _validate_paths(self, profile_id: str) -> None:
        """Before writes: allow the MSIX root alias, reject redirects below it."""
        if self._free_route(profile_id):
            try:
                free_routes.validate_paths(self.profile_root, profile_id)
            except free_routes.FreeRouteError as exc:
                raise AccountError(exc.code, str(exc)) from exc
            return
        home, cwd, config = self._paths(profile_id)
        canonical_root = self.profile_root.resolve()
        expected_home = canonical_root / profile_id
        expected_cwd = self.profile_root.parent.resolve() / "work" / profile_id
        paths = [(home, expected_home), (cwd, expected_cwd),
                 (cwd / ".env", expected_cwd / ".env"), (cwd / ".git", expected_cwd / ".git"),
                 (config, expected_home / config.relative_to(home)),
                 (home / "auth.json", expected_home / "auth.json"),
                 (home / ".credentials.json", expected_home / ".credentials.json"),
                 (home / ".gemini" / "oauth_creds.json", expected_home / ".gemini" / "oauth_creds.json")]
        if any(path.resolve() != expected for path, expected in paths):
            raise AccountError("unsafe_profile", "Каталог профиля или его файлы перенаправлены за пределы изолированного хранилища; запись остановлена.")

    def _config_guard(self, profile_id: str) -> None:
        """Read owned settings, never credentials; reject unreviewed overrides."""
        home, cwd, config = self._paths(profile_id)
        self._validate_paths(profile_id)
        if not config.is_file():
            raise AccountError("configuration", "Managed profile settings have not been prepared.")
        provider = profile_id.split("-")[0]
        if provider == "gemini":
            # This installed client walks ancestor .gemini/.env even across a
            # .git boundary and with ignoreLocalEnv enabled. Presence is enough
            # to block; never read a file which could contain credentials.
            ancestors = {cwd, *cwd.parents, home, *home.parents}
            if any((parent / ".gemini" / ".env").exists() for parent in ancestors):
                raise AccountError("billing_unverified", "Обнаружен .gemini/.env в родительском каталоге; требуется проверка изоляции окружения.")
        self._managed_configuration_guard(provider)
        text = config.read_text(encoding="utf-8")
        if provider == "codex":
            if text != CODEX_CONFIG:
                raise AccountError("billing_unverified", "Codex profile configuration differs from its verified ChatGPT-only settings.")
        else:
            try:
                actual = json.loads(text)
            except ValueError as exc:
                raise AccountError("configuration", "Profile settings are not valid JSON.") from exc
            required = CLAUDE_SETTINGS if provider == "claude" else free_routes.route_settings() if self._free_route(profile_id) else GEMINI_SETTINGS
            # CLI may save harmless display preferences. Every unknown setting
            # remains blocked until explicitly reviewed, rather than overwritten.
            if actual != required:
                raise AccountError("billing_unverified", "Profile settings changed; verify the selected official sign-in and billing configuration.")
        for candidate in (home / ".env", home / ".gemini" / ".env", cwd / ".gemini" / ".env"):
            if candidate.exists():
                raise AccountError("billing_unverified", "Unexpected .env file in isolated client directories.")
        env_file = cwd / ".env"
        if env_file.exists() and any(line.strip() and not line.lstrip().startswith("#") for line in env_file.read_text(encoding="utf-8").splitlines()):
            raise AccountError("billing_unverified", "Unexpected environment configuration in the client work folder.")
        for candidate in (cwd / ".codex", cwd / ".claude", cwd / ".gemini", cwd / "CLAUDE.md", cwd / "GEMINI.md"):
            if candidate.exists():
                raise AccountError("configuration", "Unexpected project configuration in the isolated client work folder.")

    @staticmethod
    def _managed_configuration_guard(provider: str) -> None:
        if os.name != "nt":
            return
        if provider == "gemini":
            root = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "gemini-cli"
            candidates = (root / "settings.json", root / "system-defaults.json")
        elif provider == "claude":
            root = Path(os.environ.get("PROGRAMFILES", r"C:\Program Files")) / "ClaudeCode"
            candidates = (root / "managed-settings.json", root / "managed-settings.d", root / "managed-mcp.json")
            import winreg
            for registry_root in (winreg.HKEY_LOCAL_MACHINE, winreg.HKEY_CURRENT_USER):
                try:
                    with winreg.OpenKey(registry_root, r"SOFTWARE\Policies\ClaudeCode"):
                        raise AccountError("managed_settings_unverified", "Настройки политики Claude требуют отдельной проверки режима оплаты; политика не обходится.")
                except FileNotFoundError:
                    pass
                except PermissionError as exc:
                    raise AccountError("managed_settings_unverified", "Не удалось проверить наличие политики Claude.") from exc
        else:
            return
        if any(path.exists() for path in candidates):
            raise AccountError("managed_settings_unverified", "Найдена системная конфигурация клиента; требуется проверка режима оплаты. Настройки не изменены.")

    def _require_client(self, provider: str, profile_id: str | None = None) -> dict:
        if provider not in ACTIVE_PROVIDERS:
            raise AccountError("profile_inactive", "Claude сохранён в истории и исключён из активной команды.")
        client = self._effective_client(self._profile(self._read(), profile_id)) if profile_id else self._client(provider)
        command = client.get("command") or []
        if not command or not Path(command[0]).is_file():
            raise AccountError("client_missing", "Официальный клиент не установлен или не найден.")
        if client.get("access_route_supported") is False:
            raise AccountError(client.get("route_error") or "unsupported_client", client.get("access_route_reason") or "Этот способ подписочного входа больше не поддерживается провайдером.")
        if not client.get("supported") or not client.get("version"):
            raise AccountError("client_unverified", "Запуск и параметры установленного клиента ещё не проверены.")
        if not client.get("isolation_verified"):
            raise AccountError("isolation_unverified", "Поддержка отдельного каталога профиля этой версией не подтверждена.")
        return client

    def prepare_profiles(self) -> dict:
        """Prepare eight active native homes; preserve existing settings/history."""
        with self._thread_lock, locked(self.state_path.with_suffix(".lock")):
            data = self._read()
            for profile in data["profiles"]:
                try:
                    self._prepare(profile["profile_id"])
                except (AccountError, OSError) as error:
                    profile.update(billing_safe=False, reason_code=getattr(error, "code", "configuration"), reason=str(error))
            self._save(data)
        return self.snapshot()

    def prepare_gemini_free_route(self, profile_id: str, model: str, project_label: str,
                                 free_tier_confirmed: bool = False, no_billing_confirmed: bool = False) -> dict:
        """Explicit opt-in; caller holds scheduler lock while checking idle state.

        Only metadata and a separate native home are prepared. The official CLI
        owns key entry; this operation never launches it or reads credentials.
        """
        try:
            route = free_routes.make_route(profile_id, model, project_label,
                free_tier_confirmed=free_tier_confirmed, no_billing_confirmed=no_billing_confirmed)
        except free_routes.FreeRouteError as exc:
            raise AccountError(exc.code, str(exc)) from exc
        with self._thread_lock, locked(self.state_path.with_suffix(".lock")):
            data = self._read()
            profile = self._profile(data, profile_id)
            if self._active(data):
                raise AccountError("login_active", "Завершите текущий официальный вход.")
            database = self.workspace / "registry.sqlite"
            if database.exists():
                try:
                    connection = sqlite3.connect(database.as_uri() + "?mode=ro", uri=True)
                    try:
                        has_attempts = connection.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='ai_attempts'").fetchone()
                        busy = has_attempts and connection.execute("SELECT 1 FROM ai_attempts WHERE status IN ('running','stopping') LIMIT 1").fetchone()
                    finally:
                        connection.close()
                except sqlite3.Error as exc:
                    raise AccountError("operation_active", "Не удалось подтвердить отсутствие активных заданий.") from exc
                if busy:
                    raise AccountError("operation_active", "Завершите активные задания перед настройкой подключения.")
            client = self._client("gemini")
            if client.get("version") != free_routes.CLIENT_VERSION or not client.get("supported") or not client.get("isolation_verified"):
                raise AccountError("client_unverified", "Требуется проверенный официальный Gemini CLI 0.51.0 с изолированным хранилищем.")
            try:
                free_routes.prepare_home(self.profile_root, profile_id, route)
            except free_routes.FreeRouteError as exc:
                raise AccountError(exc.code, str(exc)) from exc
            old_route = profile.get("gemini_free_route") or {}
            unchanged = (profile.get("access_route") == free_routes.ROUTE_ID
                         and old_route.get("model") == route["model"]
                         and old_route.get("project_identity_hash") == route["project_identity_hash"])
            if unchanged:
                # Refreshing the same explicit project attestation never erases
                # existing login/access evidence or launches another process.
                profile["gemini_free_route"] = route
                self._save(data)
                return self._public(profile)
            original = self.state_path.read_bytes()
            digest = hashlib.sha256(original).hexdigest()
            backup = self.state_path.with_name(f"accounts.pre-api-free.{digest[:16]}.backup.json")
            if backup.resolve() != self.state_path.parent.resolve() / backup.name:
                raise AccountError("unsafe_backup", "Резервный файл метаданных перенаправлен.")
            if backup.exists():
                if backup.read_bytes() != original:
                    raise AccountError("migration_refused", "Резервная копия метаданных отличается; она сохранена.")
            else:
                with backup.open("xb") as stream:
                    stream.write(original)
            history = profile.setdefault("route_history", [])
            history.append({"at": _now(), "metadata_backup": str(backup),
                            "profile": deepcopy({key: value for key, value in profile.items() if key != "route_history"})})
            profile.update(access_route=free_routes.ROUTE_ID, gemini_free_route=route, model=model,
                           auth_state="not_authenticated", auth_source=None, auth_mode="unknown",
                           access_state="unverified", access_evidence=None, auth_generation=profile.get("auth_generation", 0) + 1,
                           identity_verified=False, identity_source=None, identity_hash=None, distinct_account=False,
                           account_label=project_label.strip(), duplicate_of=None, billing_safe=False, billing_confirmed=False,
                           billing_identity_hash=None, billing_confirmed_at=None, billing_confirmation_expires_at=None,
                           free_route_login_receipt=None, setup_complete=False, quota_isolation_verified=False,
                           reason_code="authentication", reason="Новый профиль API Free Tier подготовлен. Введите ключ только в официальном окне Gemini CLI.")
            wizard = data["wizard"]
            wizard["completed"] = [item for item in wizard["completed"] if item != profile_id]
            wizard["skipped"].pop(profile_id, None)
            wizard.update(current=profile_id, active_login=None, status="awaiting_next", updated_at=_now())
            self._save(data)
            return self._public(profile)

    def migrate_gemini_settings(self, profile_id: str, *, apply: bool = False) -> dict:
        """Explicit, exact-template migration; no authentication files touched.

        Default is a dry run. Caller must ensure there are no running queue
        tasks; the manager additionally rejects an active official login.
        """
        with self._thread_lock, locked(self.state_path.with_suffix(".lock")):
            data = self._read()
            profile = self._profile(data, profile_id)
            if profile["provider"] != "gemini":
                raise AccountError("invalid_provider", "Эта миграция применяется только к Gemini.")
            if self._active(data):
                raise AccountError("login_active", "Завершите официальный вход перед изменением настроек профиля.")
            self._validate_paths(profile_id)
            _, _, path = self._paths(profile_id)
            original = path.read_text(encoding="utf-8")
            try:
                settings = json.loads(original)
            except ValueError as exc:
                raise AccountError("migration_refused", "Настройки не совпадают с известным шаблоном студии.") from exc
            if settings == GEMINI_SETTINGS:
                return {"profile_id": profile_id, "status": "unchanged", "applied": False}
            if settings != GEMINI_SETTINGS_LEGACY_HOOKS:
                raise AccountError("migration_refused", "Найдены отличающиеся настройки; автоматическая замена запрещена.")
            updated = json.dumps(GEMINI_SETTINGS, ensure_ascii=False, indent=2) + "\n"
            result = {"profile_id": profile_id, "status": "would_migrate", "applied": False,
                      "before_sha256": hashlib.sha256(original.encode()).hexdigest(),
                      "after_sha256": hashlib.sha256(updated.encode()).hexdigest()}
            if not apply:
                return result
            backup = path.with_name("settings.mai05-hooks-v1.backup.json")
            if backup.resolve() != path.parent.resolve() / backup.name:
                raise AccountError("unsafe_profile", "Резервный файл настроек перенаправлен; запись остановлена.")
            if backup.exists():
                if backup.read_text(encoding="utf-8") != original:
                    raise AccountError("migration_refused", "Существующая копия настроек отличается; она сохранена.")
            else:
                with backup.open("x", encoding="utf-8", newline="\n") as stream:
                    stream.write(original)
            atomic_write(path, updated)
            result.update(status="migrated", applied=True, backup_path=str(backup))
            profile["last_config_migration"] = {**result, "at": _now()}
            self._save(data)
            return result

    def _public(self, profile: dict) -> dict:
        result = deepcopy(profile)
        result["active"] = profile["profile_id"] in ORDER
        result["paused"] = bool(profile.get("paused", False))
        result["auth_generation"] = profile.get("auth_generation", 0)
        result["account_binding"] = self._account_binding(profile)
        client = self._effective_client(profile)
        installed = bool(client.get("command") and Path(client["command"][0]).is_file())
        result.update({"install_state": "verified" if installed and client.get("supported") and client.get("version") else "unverified" if installed else "missing",
                       "installation": client.get("installation"),
                       "client_version": client.get("version"), "client_path": (client.get("command") or [None])[0],
                       "profile_ref": str(self._paths(profile["profile_id"])[0]), "simulated": False, "is_simulated": False,
                       "actual_auth_state": profile["auth_state"], "usage": unknown_usage(profile["provider"], profile["profile_id"]),
                       "last_auth_check": profile["last_checked"], "observed_identity_label": profile.get("account_label"),
                       "identity_verification": "verified" if profile["identity_verified"] else "user_confirmed_not_automatically_verified" if profile.get("distinct_account") else "unverified",
                       "active_role": None, "active_task": None})
        if not result["active"]:
            result.update(state="inactive", can_probe=False, billing_safe=False,
                          historical_reason_code=profile.get("reason_code"), historical_reason=profile.get("reason"),
                          reason_code="profile_inactive", reason="Claude сохранён в истории и исключён из активной команды.")
            return result
        if profile.get("access_route") == free_routes.ROUTE_ID:
            route = profile.get("gemini_free_route") or {}
            result.update(model=route.get("model"), route_configuration_valid=client.get("route_configuration_valid", False),
                          quota_isolation_verified=False, identity_scope="owner_attested_project_not_google_account")
        expiry = profile.get("billing_confirmation_expires_at")
        billing_current = bool(expiry and datetime.fromisoformat(expiry) > datetime.now(timezone.utc))
        safe = bool(profile.get("billing_safe") and profile.get("billing_confirmed") and billing_current and
                    profile.get("identity_hash") and profile.get("billing_identity_hash") == profile.get("identity_hash") and
                    profile.get("distinct_account") and not profile.get("duplicate_of") and
                    profile["auth_state"] == "authenticated" and result["install_state"] == "verified")
        if profile["provider"] == "codex" and not (profile.get("identity_verified") and profile.get("identity_source") == "codex_app_server"):
            safe = False
        if profile.get("access_route") == free_routes.ROUTE_ID and safe:
            try:
                if not self._free_login_evidence(profile):
                    safe = False
                    result.update(reason_code="key_store_changed", reason="Хранилище ключа изменилось; повторите официальный вход и подтверждение проекта.")
            except free_routes.FreeRouteError as exc:
                safe = False
                result.update(reason_code=exc.code, reason=str(exc))
        try:
            self._config_guard(profile["profile_id"])
        except (AccountError, OSError) as error:
            safe = False
            result["reason_code"] = getattr(error, "code", "configuration")
            result["reason"] = str(error)
        result["can_probe"] = safe
        result["billing_safe"] = safe
        result["state"] = "ready" if safe and profile["access_state"] == "verified" else "blocked"
        if not installed:
            result.update(reason_code="client_missing", reason="Официальный клиент не установлен или не найден.")
        elif profile.get("billing_confirmed") and not billing_current:
            result.update(reason_code="billing_confirmation_expired", reason="Повторно подтвердите в официальных настройках отсутствие платных кредитов и Extra usage (подтверждение действует 12 часов).")
        elif profile.get("billing_confirmed") and profile.get("billing_identity_hash") != profile.get("identity_hash"):
            result.update(reason_code="billing_identity_unverified", reason="Подтверждение оплаты не связано с текущей проверенной личностью; подтвердите настройки этого аккаунта.")
        result["billing_verification"] = "owner_attested_official_settings" if profile.get("billing_confirmed") else "unverified"
        result["billing_limitation"] = "CLI не доказывает отключение платных кредитов; используется ограниченное по времени подтверждение владельца из официальных настроек."
        result.update(access_route_supported=client.get("access_route_supported"),
                      access_route_reason=client.get("access_route_reason"), access_route_source=client.get("access_route_source"))
        if client.get("access_route_supported") is False:
            result.update(can_probe=False, billing_safe=False, state="blocked", access_state="unavailable",
                          reason_code=client.get("route_error") or "unsupported_client", reason=client.get("access_route_reason") or "Этот способ подписочного входа больше не поддерживается провайдером.")
        if result["state"] == "ready" and result.get("reason_code") == "billing_confirmation_required":
            result.update(reason_code=None, reason=None)
        if result["paused"]:
            result.update(state="paused", can_probe=False, billing_safe=False, reason_code="profile_paused",
                          reason="Профиль на паузе. Вход и прежние результаты проверки сохранены.")
        return result

    def snapshot(self) -> dict:
        with self._thread_lock:
            data = self._read()
            wizard = deepcopy(data["wizard"])
            active = wizard.get("active_login")
            if active:
                wizard["active_login"] = {k: active.get(k) for k in ("profile_id", "started_at", "status", "pid")}
            return {"schema_version": data["schema_version"], "profiles": [self._public(p) for p in data["profiles"]],
                    "historical_profiles": [self._public(p) for p in data.get("historical_profiles", [])], "wizard": wizard,
                    "generation": data["generation"], "profile_root": str(self.profile_root), "truncated": False}

    def queue_profile(self, profile_id: str) -> dict:
        with self._thread_lock:
            return self._public(self._profile(self._read(), profile_id))

    def _active(self, data: dict) -> bool:
        active = data["wizard"].get("active_login")
        if not active:
            return False
        receipt = Path(active["receipt"])
        if receipt.exists():
            result = json.loads(receipt.read_text(encoding="utf-8"))
            if result.get("process_state") == "unknown" or (result.get("process_state") is None and result.get("exit_code") is None):
                active["status"] = "termination_unverified"
                return True
            active["status"] = "finished"
            active["exit_code"] = result.get("exit_code")
            return False
        process = self._processes.get(active["id"])
        if process is not None:
            if process.poll() is not None:
                active["status"] = "termination_unverified"
                return True
            return True
        token = active.get("process_token")
        current = _process_token(active.get("pid", 0))
        child_path = active.get("child_identity")
        if child_path and Path(child_path).exists():
            child = json.loads(Path(child_path).read_text(encoding="utf-8"))
            if child.get("process_token") and _process_token(child.get("pid")) == child["process_token"]:
                active["status"] = "child_running"
                return True
        # A dead wrapper is not proof that its tree exited. Missing receipt,
        # launch gap or PID reuse retains the reservation for explicit recovery.
        if token and current and token != current:
            active["status"] = "termination_unverified"
        if token and not current:
            active["status"] = "termination_unverified"
        return True

    def connect(self, profile_id: str, reconnect: bool = False) -> dict:
        with self._thread_lock, locked(self.state_path.with_suffix(".lock")):
            data = self._read()
            profile = self._profile(data, profile_id)
            if profile.get("paused"):
                raise AccountError("profile_paused", "Сначала возобновите профиль.")
            if self._active(data):
                raise AccountError("login_active", "Завершите уже открытое официальное окно входа; второй вход пока не запускается.")
            try:
                client = self._require_client(profile["provider"], profile_id)
                self._prepare(profile_id)
            except AccountError as error:
                profile.update(reason_code=error.code, reason=str(error), last_checked=_now())
                data["wizard"].update(current=profile_id, status="blocked")
                self._save(data)
                return self._public(profile)
            home, cwd, _ = self._paths(profile_id)
            provider = profile["provider"]
            free_route = profile.get("gemini_free_route") if profile.get("access_route") == free_routes.ROUTE_ID else None
            login_args = client.get("login_args", {"codex": ["login"], "claude": ["auth", "login"], "gemini": []}[provider])
            # No logout is needed: official login reselects only this isolated
            # account; Gemini starts its official /auth selector when reconnecting.
            if reconnect and provider == "gemini":
                login_args = ["--prompt-interactive", "/auth"]
            if free_route:
                key_store_before = free_routes.credential_metadata(self.profile_root, profile_id)
                login_args = ["--model", free_route["model"]] + (["--prompt-interactive", "/auth"] if reconnect or key_store_before["present"] else [])
            request_id = uuid.uuid4().hex
            request_path = self.profile_root.parent / "login_requests" / f"{request_id}.json"
            receipt = request_path.with_suffix(".done.json")
            child_identity = request_path.with_suffix(".child.json")
            request = {"profile_id": profile_id, "label": profile["label"], "provider": provider,
                       "home": str(home), "cwd": str(cwd), "command": client["command"] + login_args,
                       "receipt": str(receipt), "child_identity": str(child_identity)}
            if free_route:
                request.update(access_route=free_routes.ROUTE_ID, model=free_route["model"])
            _json(request_path, request)
            active = {"id": request_id, "profile_id": profile_id, "started_at": _now(), "status": "launching",
                      "pid": None, "process_token": None, "receipt": str(receipt), "child_identity": str(child_identity)}
            if free_route:
                active["key_store_before"] = key_store_before
            data["wizard"].update(current=profile_id, status="awaiting_login", active_login=active, updated_at=_now())
            profile.update(auth_state="awaiting_login", access_state="unverified", billing_safe=False,
                           auth_generation=profile.get("auth_generation", 0) + 1,
                           auth_source=None, auth_mode="unknown", identity_source=None, access_evidence=None,
                           billing_confirmed=False, billing_identity_hash=None, distinct_account=False, identity_verified=False,
                           identity_hash=None, account_label=None, duplicate_of=None, setup_complete=False,
                           free_route_login_receipt=None,
                           reason_code="authentication", reason="Завершите вход в официальном окне, затем нажмите «Проверить».")
            self._save(data)  # Commit the launch intent before creating a process.
            try:
                process = self._launcher([sys.executable, str(Path(__file__).resolve()), "--login-worker", str(request_path)],
                                         cwd=str(self.profile_root.parent), env=free_routes.child_environment(home) if free_route else isolated_environment(provider, home),
                                         creationflags=getattr(subprocess, "CREATE_NEW_CONSOLE", 0) if os.name == "nt" else 0)
                self._processes[request_id] = process
                active.update(pid=process.pid, process_token=_process_token(process.pid), status="running")
            except OSError as error:
                active.update(status="failed")
                _json(receipt, {"exit_code": None, "reason": type(error).__name__, "finished_at": _now()})
                profile.update(reason_code="client_start_failed", reason=f"Не удалось запустить окно: {type(error).__name__}.")
                data["wizard"]["status"] = "blocked"
            self._save(data)
            return self._public(profile)

    def _diagnose(self, profile: dict) -> dict:
        client = self._require_client(profile["provider"], profile["profile_id"])
        self._config_guard(profile["profile_id"])
        provider = profile["provider"]
        home, cwd, _ = self._paths(profile["profile_id"])
        if provider == "gemini":
            if profile.get("access_route") == free_routes.ROUTE_ID:
                route = profile["gemini_free_route"]
                self._validate_free_route(route, profile["profile_id"])
                if (profile.get("auth_source") == "official_cli_key_setup_owner_confirmation"
                        and not self._free_login_evidence(profile)):
                    return self._changed_free_store(profile)
                configured = (profile.get("auth_source") == "official_cli_key_setup_owner_confirmation"
                              and self._free_login_evidence(profile))
                return {"auth_state": "authenticated" if configured else "unverified", "auth_mode": "gemini_api_free",
                        "identity_verified": False, "identity_source": "owner_attested_project",
                        "identity_hash": route["project_identity_hash"] if configured else None,
                        "billing_safe": bool(configured), "quota_isolation_verified": False,
                        "reason_code": "billing_confirmation_required" if configured else "key_setup_confirmation_required",
                        "reason": "Подтвердите Free Tier без привязанного биллинга для указанного проекта." if configured else
                                  "Введите ключ в официальном CLI, закройте его через /quit и подтвердите настройку проекта."}
            # Gemini has /auth, not a documented standalone auth-status command.
            # Do not invent a command or parse private token/account files.
            return {"auth_state": "authenticated" if profile.get("auth_source") == "official_ui_user_confirmation" else "unverified",
                    "auth_mode": "google_oauth", "identity_verified": False,
                    "reason_code": "user_confirmation_required", "reason": "Gemini: подтвердите вход Google в официальном /auth; автоматической проверки личности нет.",
                    "billing_safe": profile.get("auth_source") == "official_ui_user_confirmation"}
        if provider == "codex":
            return self._diagnose_codex(client, home, cwd)
        args = client.get("status_args", ["login", "status"] if provider == "codex" else ["auth", "status"])
        try:
            run = self._runner(client["command"] + args, cwd=str(cwd), env=isolated_environment(provider, home),
                               stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                               text=True, encoding="utf-8", errors="replace", timeout=20, check=False,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if os.name == "nt" else 0)
        except subprocess.TimeoutExpired:
            return {"auth_state": "unknown", "billing_safe": False, "reason_code": "transient", "reason": "Диагностика входа не завершилась за 20 секунд."}
        except OSError as error:
            return {"auth_state": "unknown", "billing_safe": False, "reason_code": "client_start_failed", "reason": type(error).__name__}
        try:
            status = json.loads(run.stdout)
        except (ValueError, TypeError):
            return {"auth_state": "unknown", "billing_safe": False, "reason_code": "diagnostic_unknown", "reason": "Claude вернул неизвестный формат диагностики.", "diagnostic_exit_code": run.returncode}
        if status.get("configDirectory") and Path(status["configDirectory"]).resolve() != home:
            return {"auth_state": "unknown", "billing_safe": False, "reason_code": "isolation_failed", "reason": "Claude использует другой каталог профиля."}
        if run.returncode != 0 or not status.get("loggedIn"):
            return {"auth_state": "not_authenticated", "billing_safe": False, "reason_code": "authentication", "reason": "Claude требует входа.", "diagnostic_exit_code": run.returncode}
        if status.get("authMethod") != "claude.ai":
            return {"auth_state": "wrong_auth_mode", "billing_safe": False, "reason_code": "billing", "reason": "Claude не подтвердил подписочный вход claude.ai."}
        result = {"auth_state": "authenticated", "auth_mode": "claude_subscription", "auth_source": "claude_auth_status",
                  "billing_safe": True, "reason_code": "billing_confirmation_required", "reason": "Вход claude.ai подтверждён; подтвердите отсутствие платного Extra usage."}
        identity = status.get("accountId") or status.get("email")
        if isinstance(identity, str) and identity.strip():
            result.update(identity_verified=True, identity_hash=_identity(provider, identity), identity_source="claude_auth_status")
        else:
            result.update(identity_verified=False)
        return result

    def _record_free_login(self, profile: dict, active: dict | None) -> None:
        if (profile.get("access_route") != free_routes.ROUTE_ID or not active
                or active.get("profile_id") != profile["profile_id"] or active.get("status") != "finished"
                or active.get("exit_code") != 0):
            return
        metadata = free_routes.credential_metadata(self.profile_root, profile["profile_id"])
        before = active.get("key_store_before")
        if metadata["present"] and metadata["size"] > 0 and metadata != before:
            profile["free_route_login_receipt"] = {"request_id": active["id"], "auth_generation": profile["auth_generation"],
                                                   "finished_at": _now(), "credential_metadata": metadata}

    def _free_login_evidence(self, profile: dict) -> bool:
        receipt = profile.get("free_route_login_receipt") or {}
        if receipt.get("auth_generation") != profile.get("auth_generation") or not receipt.get("request_id"):
            return False
        metadata = free_routes.credential_metadata(self.profile_root, profile["profile_id"])
        return bool(metadata["present"] and metadata["size"] > 0 and metadata == receipt.get("credential_metadata"))

    @staticmethod
    def _changed_free_store(profile: dict) -> dict:
        return {"auth_state": "unverified", "auth_source": None, "auth_mode": "gemini_api_free",
                "auth_generation": profile.get("auth_generation", 0) + 1,
                "access_state": "unverified", "access_evidence": None, "billing_safe": False,
                "billing_confirmed": False, "billing_identity_hash": None, "identity_hash": None,
                "identity_verified": False, "free_route_login_receipt": None,
                "reason_code": "key_store_changed", "reason": "Хранилище ключа изменилось; повторите официальный вход и подтвердите проект Free Tier без биллинга."}

    def _diagnose_codex(self, client: dict, home: Path, cwd: Path) -> dict:
        diagnoser = self._codex_diagnoser
        if diagnoser is None:
            from app.ai_codex_diagnostics import diagnose_codex
            diagnoser = diagnose_codex
        try:
            observed = diagnoser(client["command"], cwd=str(cwd), env=isolated_environment("codex", home), timeout=20.0)
        except (OSError, subprocess.TimeoutExpired, ValueError) as error:
            return {"auth_state": "unknown", "identity_verified": False, "billing_safe": False,
                    "reason_code": "diagnostic_unavailable", "reason": f"Официальная диагностика Codex недоступна: {type(error).__name__}.",
                    "codex_diagnostics": {"plan_type": None, "ordinary_usage_allowed": None, "rate_limits": None,
                                          "rate_limits_by_id": None, "credits": None, "checked_at": _now()}}
        if not isinstance(observed, dict):
            raise AccountError("diagnostic_invalid", "Codex вернул неизвестный формат диагностики.")
        credits = observed.get("credits")
        diagnostics = sanitize({"plan_type": observed.get("plan_type"),
                                "ordinary_usage_allowed": observed.get("ordinary_usage_allowed"),
                                "rate_limits": observed.get("rate_limits"), "rate_limits_by_id": observed.get("rate_limits_by_id"),
                                "credits": ({"has_credits": credits.get("hasCredits"), "unlimited": credits.get("unlimited"),
                                             "balance": credits.get("balance")} if isinstance(credits, dict) else None),
                                "checked_at": observed.get("checked_at"), "error": observed.get("error")})
        result = {"codex_diagnostics": diagnostics, "auth_source": "codex_app_server", "identity_verified": False,
                  "billing_safe": False, "auth_state": "unknown", "auth_mode": "unknown"}
        if observed.get("account_type") not in {None, "chatgpt"}:
            return {**result, "auth_state": "wrong_auth_mode", "reason_code": "billing", "reason": "Codex выбрал неподдерживаемый способ входа; требуется ChatGPT."}
        fingerprint = observed.get("identity_hash")
        official_identity = (observed.get("identity_verified") is True and isinstance(fingerprint, str)
                             and re.fullmatch(r"[0-9a-f]{64}", fingerprint) is not None)
        if not (observed.get("authenticated") is True and observed.get("auth_mode") == "chatgpt_subscription" and official_identity):
            error = observed.get("error") or {}
            result.update(reason_code=error.get("code", "identity_unavailable"),
                          reason=str(sanitize(error.get("reason", "Официальный клиент не подтвердил личность аккаунта ChatGPT."))))
            return result
        result.update(auth_state="authenticated", auth_mode="chatgpt_subscription", identity_verified=True,
                      identity_hash=fingerprint, identity_source="codex_app_server",
                      billing_safe=observed.get("ordinary_usage_allowed") is not False,
                      reason_code="billing_confirmation_required",
                      reason="Личность ChatGPT подтверждена официальным клиентом; подтвердите отключение дополнительных платных расходов.")
        if observed.get("ordinary_usage_allowed") is False:
            result.update(reason_code="usage_unavailable", reason="Официальный клиент сообщает ordinaryUsageAllowed=false; причина не подтверждена, запуск остановлен.")
        return result

    @staticmethod
    def _apply_diagnosis(profile: dict, diagnosis: dict) -> bool:
        profile.setdefault("auth_generation", 0)
        previous_hash = profile.get("identity_hash")
        previous_source = profile.get("identity_source")
        current_hash = diagnosis.get("identity_hash")
        identity_changed = bool(previous_hash and current_hash and
                                (previous_hash != current_hash or (previous_source == "user_confirmation" and diagnosis.get("identity_verified"))))
        profile.update(diagnosis, last_checked=_now())
        if identity_changed:
            profile.update(access_state="unverified", access_evidence=None, billing_safe=False, billing_confirmed=False,
                           auth_generation=profile["auth_generation"] + 1,
                           billing_confirmation_source=None, billing_confirmed_at=None, billing_confirmation_expires_at=None,
                           billing_identity_hash=None,
                           distinct_account=False, setup_complete=False, isolation_state="identity_changed", account_label=None,
                           reason_code="identity_changed", reason="Официальная личность аккаунта изменилась или впервые проверена вместо ручной метки; прежнее подтверждение оплаты снято.")
        return identity_changed

    def check(self, profile_id: str) -> dict:
        with self._thread_lock, locked(self.state_path.with_suffix(".lock")):
            data = self._read()
            profile = self._profile(data, profile_id)
            active = data["wizard"].get("active_login")
            if active and active.get("profile_id") == profile_id and not self._active(data):
                self._record_free_login(profile, active)
            try:
                self._prepare(profile_id)
                diagnosis = self._diagnose(profile)
            except AccountError as error:
                diagnosis = {"billing_safe": False, "reason_code": error.code, "reason": str(error)}
            self._apply_diagnosis(profile, diagnosis)
            self._duplicates(data, profile)
            active = data["wizard"].get("active_login")
            if active and active["profile_id"] == profile_id:
                self._active(data)
                if active.get("status") in {"finished", "interrupted", "failed"}:
                    data["wizard"].update(active_login=None, status="awaiting_confirmation")
            self._save(data)
            return self._public(profile)

    @staticmethod
    def _duplicates(data: dict, profile: dict) -> None:
        profile["duplicate_of"] = None
        fingerprint = profile.get("identity_hash")
        if not fingerprint:
            return
        for other in data["profiles"]:
            if other["profile_id"] != profile["profile_id"] and other.get("identity_hash") == fingerprint:
                profile.update(duplicate_of=other["profile_id"], billing_safe=False, distinct_account=False,
                               access_state="unavailable", reason_code="duplicate_identity",
                               reason=f"Этот аккаунт уже указан в {other['label']}; он не считается отдельным доступом.")
                break

    def confirm(self, profile_id: str, account_label: str = "", distinct_account: bool = False,
                billing_confirmed: bool = False, auth_confirmed: bool = False) -> dict:
        label = str(account_label).strip()
        if len(label) > 160 or "\n" in label or sanitize(label) != label:
            raise AccountError("invalid_label", "Введите только короткую метку аккаунта, без паролей, кодов и секретов.")
        with self._thread_lock, locked(self.state_path.with_suffix(".lock")):
            data = self._read()
            profile = self._profile(data, profile_id)
            previous_binding = self._account_binding(profile)
            previous_access_verified = profile.get("access_state") == "verified"
            active = data["wizard"].get("active_login")
            if active and self._active(data):
                raise AccountError("login_active", "Закройте официальный клиент после входа, затем подтвердите результат.")
            self._require_client(profile["provider"], profile_id)
            self._config_guard(profile_id)
            # Explicit fallback is available only for Gemini's interactive /auth.
            if profile.get("access_route") == free_routes.ROUTE_ID:
                self._record_free_login(profile, active)
                if auth_confirmed:
                    if not self._free_login_evidence(profile):
                        raise AccountError("key_setup_unverified", "Нужны завершённый официальный вход и подтверждение ввода ключа только в CLI.")
                    profile["auth_source"] = "official_cli_key_setup_owner_confirmation"
                label = profile["gemini_free_route"]["project_label"]
                distinct_account = True  # Owner-attested project identity; never a separate-quota claim.
            elif profile["provider"] == "gemini" and auth_confirmed:
                profile.update(auth_source="official_ui_user_confirmation")
            changed = self._apply_diagnosis(profile, self._diagnose(profile))
            if changed:
                self._save(data)
                return self._public(profile)
            if profile["auth_state"] != "authenticated":
                self._save(data)
                raise AccountError("authentication", "Официальный вход ещё не подтверждён.")
            if profile.get("identity_verified"):
                label = label or profile.get("account_label") or profile["label"]
                distinct_account = True  # The actual fingerprint still has to pass duplicate checks below.
            elif not label:
                raise AccountError("invalid_label", "Введите метку аккаунта для ручного подтверждения личности.")
            profile.update(account_label=label, distinct_account=distinct_account is True,
                           billing_confirmed=billing_confirmed is True,
                           billing_confirmation_source="owner_attested_official_settings" if billing_confirmed else None,
                           billing_confirmed_at=_now() if billing_confirmed else None,
                           billing_confirmation_expires_at=(datetime.now(timezone.utc) + timedelta(hours=12)).isoformat() if billing_confirmed else None)
            if not profile.get("identity_verified"):
                if profile.get("access_route") == free_routes.ROUTE_ID:
                    profile.update(identity_hash=profile["gemini_free_route"]["project_identity_hash"], identity_source="owner_attested_project")
                else:
                    profile.update(identity_hash=_identity(profile["provider"], label), identity_source="user_confirmation")
            profile["billing_identity_hash"] = profile.get("identity_hash") if billing_confirmed else None
            self._duplicates(data, profile)
            if not profile["duplicate_of"]:
                self._check_peers(data, profile)
            if profile.get("reason_code") == "isolation_failed":
                self._save(data)
                return self._public(profile)
            if not profile["duplicate_of"] and distinct_account and billing_confirmed and profile.get("billing_safe"):
                # Renewing the billing attestation does not invalidate a real
                # access result for the same identity and login generation.
                # Reconnect and identity changes already revoke that evidence.
                preserve_access = (previous_access_verified and bool(previous_binding["identity_hash"])
                                   and previous_binding == self._account_binding(profile))
                profile.update(setup_complete=True, access_state="verified" if preserve_access else "unverified",
                               isolation_state="directories_verified_identity_" + ("verified" if profile["identity_verified"] else "user_confirmed"),
                               reason_code=None if preserve_access else "access_unverified",
                               reason=None if preserve_access else "Вход настроен. Доступ к модели ещё не проверен коротким реальным заданием.")
                if previous_binding != self._account_binding(profile):
                    profile["access_evidence"] = None
                wizard = data["wizard"]
                wizard["skipped"].pop(profile_id, None)
                if preserve_access:
                    self._advance_verified(data, profile_id)
                else:
                    wizard.update(active_login=None, current=profile_id, status="awaiting_access", updated_at=_now())
            self._save(data)
            return self._public(profile)

    def _check_peers(self, data: dict, profile: dict) -> None:
        """Recheck earlier independent homes using official status only.

        Gemini identity remains explicitly user-attested. Codex and Claude use
        the fingerprint returned by their official diagnostic interface.
        """
        evidence = []
        for other in data["profiles"]:
            if other["profile_id"] == profile["profile_id"] or other["provider"] != profile["provider"] or not other.get("setup_complete"):
                continue
            try:
                diagnosed = self._diagnose(other)
            except (AccountError, OSError) as error:
                diagnosed = {"auth_state": "unknown", "reason": str(error)}
            auth_unchanged = diagnosed.get("auth_state") == "authenticated"
            identity_unchanged = (diagnosed.get("identity_hash") == other.get("identity_hash")) if other.get("identity_verified") else None
            evidence.append({"profile_id": other["profile_id"], "auth_unchanged": auth_unchanged,
                             "identity_unchanged": identity_unchanged, "checked_at": _now()})
            if not auth_unchanged or identity_unchanged is False:
                other.update(access_state="unavailable", billing_safe=False, distinct_account=False,
                             reason_code="isolation_failed", reason="Повторная проверка после другого входа изменила подтверждённое состояние этого профиля.")
                profile.update(access_state="unavailable", billing_safe=False, distinct_account=False,
                               reason_code="isolation_failed", reason=f"Не подтверждена сохранность входа {other['label']} после подключения нового профиля.")
        profile["peer_isolation_checks"] = evidence

    @staticmethod
    def _next(wizard: dict) -> str | None:
        return next((item for item in ORDER if item not in wizard["completed"] and item not in wizard["skipped"]
                     and item not in wizard.get("paused_profiles", [])), None)

    def start_wizard(self) -> dict:
        return self.resume_wizard()

    def resume_wizard(self) -> dict:
        with self._thread_lock, locked(self.state_path.with_suffix(".lock")):
            data = self._read()
            if self._active(data):
                data["wizard"]["status"] = "awaiting_login"
                self._save(data)
                return self.snapshot()
            previous = data["wizard"].get("active_login")
            if previous:
                data["wizard"].update(current=previous["profile_id"], active_login=None, status="awaiting_confirmation", updated_at=_now())
                self._save(data)
                return self.snapshot()
            if data["wizard"].get("status") == "paused":
                data["wizard"]["status"] = data["wizard"].pop("paused_from", "running")
                self._save(data)
            if data["wizard"].get("status") in {"awaiting_confirmation", "awaiting_access"}:
                return self.snapshot()
            current = self._next(data["wizard"])
            if current and self._profile(data, current)["auth_state"] == "authenticated":
                data["wizard"].update(current=current, status="awaiting_confirmation", updated_at=_now())
                self._save(data)
                return self.snapshot()
            exhausted = "waiting_profiles" if data["wizard"].get("paused_profiles") else "complete_with_skips" if data["wizard"]["skipped"] else "complete"
            data["wizard"].update(current=current, status=exhausted if current is None else "running", updated_at=_now())
            self._save(data)
        if current:
            self.connect(current)
        return self.snapshot()

    def pause_wizard(self) -> dict:
        with self._thread_lock, locked(self.state_path.with_suffix(".lock")):
            data = self._read()
            previous = data["wizard"]["status"]
            data["wizard"].update(status="paused", paused_from=previous, updated_at=_now())
            self._save(data)
        return self.snapshot()

    def set_profile_paused(self, profile_id: str, paused: bool) -> dict:
        """Suspend new work without clearing authentication or access evidence."""
        if not isinstance(paused, bool):
            raise AccountError("invalid_pause", "Pause must be a boolean.")
        with self._thread_lock, locked(self.state_path.with_suffix(".lock")):
            data = self._read()
            profile = self._profile(data, profile_id)
            active = data["wizard"].get("active_login")
            if active and active.get("profile_id") == profile_id and self._active(data):
                raise AccountError("login_active", "Завершите официальный вход перед паузой профиля.")
            if bool(profile.get("paused")) == paused:
                return self._public(profile)
            profile.update(paused=paused, paused_at=_now() if paused else None)
            wizard = data["wizard"]
            wizard["paused_profiles"] = [item["profile_id"] for item in data["profiles"] if item.get("paused")]
            if paused and wizard.get("current") == profile_id:
                wizard.update(current=self._next(wizard), active_login=None, updated_at=_now())
                status = "awaiting_next" if wizard["current"] else "waiting_profiles"
                if wizard["status"] == "paused":
                    wizard["paused_from"] = status
                else:
                    wizard["status"] = status
            elif not paused and wizard.get("current") is None:
                wizard.update(current=self._next(wizard), updated_at=_now())
                if wizard["current"]:
                    if wizard["status"] == "paused":
                        wizard["paused_from"] = "awaiting_next"
                    else:
                        wizard["status"] = "awaiting_next"
            self._save(data)
            return self._public(profile)

    def pause_profile(self, profile_id: str) -> dict:
        return self.set_profile_paused(profile_id, True)

    def resume_profile(self, profile_id: str) -> dict:
        return self.set_profile_paused(profile_id, False)

    def skip_profile(self, reason: str, profile_id: str | None = None) -> dict:
        reason = str(sanitize(str(reason))).strip()
        if not reason or len(reason) > 500:
            raise AccountError("skip_reason", "Укажите краткую причину пропуска.")
        with self._thread_lock, locked(self.state_path.with_suffix(".lock")):
            data = self._read()
            if self._active(data):
                raise AccountError("login_active", "Сначала закройте текущее официальное окно входа.")
            profile_id = profile_id or data["wizard"].get("current")
            profile = self._profile(data, profile_id)
            profile.update(reason_code="skipped", reason=reason)
            if profile["auth_state"] == "awaiting_login":
                profile.update(auth_state="not_authenticated", auth_source=None, billing_safe=False,
                               billing_confirmed=False, billing_identity_hash=None)
            wizard = data["wizard"]
            wizard["skipped"][profile_id] = {"reason": reason, "at": _now()}
            wizard.update(active_login=None, current=self._next(wizard), status="awaiting_next", updated_at=_now())
            if wizard["current"] is None:
                wizard["status"] = "waiting_profiles" if wizard.get("paused_profiles") else "complete_with_skips"
            self._save(data)
        return self.snapshot()

    def launch_spec(self, profile_id: str, require_access: bool = True) -> dict:
        with self._thread_lock:
            if self._profile(self._read(), profile_id).get("paused"):
                raise AccountError("profile_paused", "Сначала возобновите профиль.")
            # Official status may change after setup. It exposes authentication
            # method/identity, never a model-generated claim about its account.
            self.check(profile_id)
            profile = self.queue_profile(profile_id)
            if not profile["can_probe"] or (require_access and profile["access_state"] != "verified"):
                raise AccountError(profile.get("reason_code") or "access_unverified", profile.get("reason") or "Profile has not passed access checks.")
            client = self._require_client(profile["provider"], profile_id)
            self._config_guard(profile_id)
            home, cwd, _ = self._paths(profile_id)
            free_route = self._free_route(profile_id)
            return {"command": client["command"], "env": free_routes.child_environment(home) if free_route else isolated_environment(profile["provider"], home),
                    "cwd": str(cwd), "profile": profile, "cli_args": client.get("cli_args", []),
                    "client": client, "model": free_route["model"] if free_route else None,
                    "gemini_settings": free_routes.route_settings() if free_route else GEMINI_SETTINGS if profile["provider"] == "gemini" else None}

    def record_access(self, profile_id: str, success: bool, failure: dict | None = None,
                      task_id: str | None = None, result_ref: str | None = None) -> dict:
        with self._thread_lock, locked(self.state_path.with_suffix(".lock")):
            data = self._read()
            profile = self._profile(data, profile_id)
            if success and not self._public({**profile, "paused": False})["can_probe"]:
                raise AccountError("access_unverified", "Cannot accept access evidence without verified launch gates.")
            profile.update(access_state="verified" if success else "unavailable", last_checked=_now(),
                           access_evidence={"task_id": task_id, "result_ref": result_ref, "checked_at": _now(), "simulated": False})
            if success:
                profile.update(last_successful_run=_now(), reason_code=None, reason=None)
            else:
                error = sanitize(failure or {})
                profile.update(reason_code=error.get("kind", "unknown"), reason=error.get("message", "Реальная проверка доступа не завершилась."))
            self._save(data)
            return self._public(profile)

    record_result = record_access

    @staticmethod
    def _account_binding(profile: dict) -> dict:
        return {"identity_hash": profile.get("identity_hash"), "auth_generation": profile.get("auth_generation", 0)}

    @staticmethod
    def _advance_verified(data: dict, profile_id: str) -> None:
        wizard = data["wizard"]
        if profile_id not in wizard["completed"]:
            wizard["completed"].append(profile_id)
        wizard["skipped"].pop(profile_id, None)
        # A late access result must never release a newer login reservation or
        # change the profile the owner is currently connecting.
        if wizard.get("current") != profile_id or wizard.get("active_login"):
            return
        wizard.update(current=AccountManager._next(wizard), updated_at=_now())
        next_status = ("waiting_profiles" if wizard.get("paused_profiles") else "complete_with_skips" if wizard["skipped"] else "complete") if wizard["current"] is None else "awaiting_next"
        if wizard["status"] == "paused":
            wizard["paused_from"] = next_status
        else:
            wizard["status"] = next_status

    def observe_result(self, profile_id: str, task: dict, result: dict | None,
                       failure: dict | None) -> None:
        """Idempotent post-commit queue callback; fake or raw answers cannot pass."""
        attempt_id = task.get("attempt_id")
        if not attempt_id:
            raise AccountError("evidence_missing", "An accepted queue attempt ID is required.")
        with self._thread_lock, locked(self.state_path.with_suffix(".lock")):
            data = self._read()
            profile = self._profile(data, profile_id)
            observed = profile.setdefault("observed_attempts", [])
            if attempt_id in observed:
                return
            if (profile.get("access_route") == free_routes.ROUTE_ID
                    and profile.get("auth_source") == "official_cli_key_setup_owner_confirmation"
                    and not self._free_login_evidence(profile)):
                self._apply_diagnosis(profile, self._changed_free_store(profile))
            binding = task.get("account_binding")
            current_binding = self._account_binding(profile)
            if not current_binding["identity_hash"] or not isinstance(binding, dict) or binding != current_binding:
                # This delivery is acknowledged, not retried forever. A prior
                # account/login cannot verify or block its replacement, even
                # when the same account signs in again under a new generation.
                evidence = {"task_id": task.get("task_id"), "attempt_id": attempt_id, "ignored_at": _now(),
                            "reason": "account_binding_missing" if not isinstance(binding, dict) else "account_binding_changed",
                            "auth_generation": current_binding["auth_generation"]}
                profile.setdefault("ignored_result_evidence", []).append(evidence)
                observed.append(attempt_id)
                self._save(data)
                return
            if result is not None and (result.get("simulated") is not False or not result.get("sha256") or not result.get("path")):
                raise AccountError("evidence_invalid", "Only an accepted nonsimulated artifact can verify access.")
            if failure is None and task.get("kind") == "access_check" and result is not None:
                if not self._public({**profile, "paused": False})["can_probe"]:
                    raise AccountError("access_unverified", "Account gates changed before accepting access evidence.")
                profile.update(access_state="verified", last_successful_run=_now(), last_checked=_now(), reason_code=None, reason=None,
                               access_evidence={"task_id": task.get("task_id"), "attempt_id": attempt_id,
                                                "result_ref": result["path"], "sha256": result["sha256"], "simulated": False})
                self._advance_verified(data, profile_id)
            elif failure is not None:
                error = sanitize(failure)
                kind = error.get("kind", "unknown")
                if kind == "invalid_input" and task.get("kind") not in {None, "access_check"}:
                    # A rejected creative artifact is task evidence, not an
                    # entitlement failure. Keep the existing access state;
                    # this cannot promote an unverified account to verified.
                    profile["last_task_error"] = {**error, "task_id": task.get("task_id"),
                                                  "attempt_id": attempt_id, "at": _now()}
                else:
                    profile.update(access_state="unavailable", last_checked=_now(), reason_code=kind,
                                   reason=error.get("message", "Реальное задание не выполнено."))
                    if kind in {"authentication", "billing"}:
                        profile.update(auth_state="unknown", billing_safe=False, billing_confirmed=False)
                    if data["wizard"].get("current") == profile_id:
                        if data["wizard"]["status"] == "paused":
                            data["wizard"]["paused_from"] = "blocked"
                        else:
                            data["wizard"]["status"] = "blocked"
            observed.append(attempt_id)
            self._save(data)


def _login_worker(request_path: str) -> int:
    """Visible terminal; raw OAuth output remains in the official local UI only."""
    request = json.loads(Path(request_path).read_text(encoding="utf-8"))
    profile_id = request["profile_id"]
    if profile_id not in ORDER or request["provider"] != profile_id.split("-")[0]:
        raise AccountError("invalid_profile", "Invalid login request.")
    free_route = request.get("access_route") == free_routes.ROUTE_ID
    if free_route and (request["provider"] != "gemini" or request.get("model") not in free_routes.FREE_MODELS
                       or Path(request["home"]).name != profile_id + "-api-free"):
        raise AccountError("free_route_invalid", "Invalid isolated Free Tier login request.")
    print(f"AI_VIDEO_02 — {request['label']}", flush=True)
    print("Вход выполняется только в официальном клиенте. Пароль и MFA вводите только в его окне браузера.", flush=True)
    if free_route:
        print("Выберите Gemini API key. Введите ключ только в официальном диалоге CLI, затем /quit. Не вставляйте ключ в чат или панель.", flush=True)
        print("Используйте только подтверждённый проект AI Studio Free Tier без привязанного биллинга. Запрос к модели здесь не запускается.", flush=True)
    elif request["provider"] == "gemini":
        print("Выберите Sign in with Google. После входа проверьте /auth, затем /quit и вернитесь в панель.", flush=True)
    else:
        print("Выберите подписочный вход. После завершения вернитесь в панель и нажмите «Проверить».", flush=True)
    code = None
    process = None
    try:
        process = subprocess.Popen(request["command"], cwd=request["cwd"],
                                   env=free_routes.child_environment(request["home"]) if free_route else
                                       isolated_environment(request["provider"], Path(request["home"])))
        if request.get("child_identity"):
            _json(Path(request["child_identity"]), {"pid": process.pid, "process_token": _process_token(process.pid), "started_at": _now()})
        code = process.wait()
        return code
    except KeyboardInterrupt:
        return 130
    except OSError as error:
        print(f"Не удалось запустить клиент: {type(error).__name__}.", flush=True)
        return 1
    finally:
        stopped = _stop_login_child(process)
        _json(Path(request["receipt"]), {"exit_code": code, "finished_at": _now(),
                                         "process_state": "stopped" if stopped else "unknown"})


def _stop_login_child(process) -> bool:
    """Only this worker's child tree; a failed stop keeps the wizard blocked."""
    if process is None or process.poll() is not None:
        return True
    try:
        if os.name == "nt":
            system = Path(os.environ.get("SYSTEMROOT", r"C:\Windows")) / "System32" / "taskkill.exe"
            subprocess.run([str(system), "/PID", str(process.pid), "/T", "/F"],
                           stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                           timeout=10, check=False, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            process.terminate()
        process.wait(timeout=10)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return process.poll() is not None


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--login-worker", required=True)
    arguments = parser.parse_args()
    raise SystemExit(_login_worker(arguments.login_worker))
