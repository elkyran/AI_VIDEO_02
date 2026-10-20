"""Opt-in Gemini Developer API Free Tier preparation, with no secret access.

The official 0.51.0 CLI owns key entry/storage. This module only prepares a
separate home and verifies metadata. It cannot establish model entitlement or
prove a project's billing tier; those remain explicit owner attestations.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta, timezone
import hashlib
import json
from pathlib import Path
import re


ROUTE_ID = "gemini_api_free"
CLIENT_VERSION = "0.51.0"
FREE_MODELS = frozenset({"gemini-3.8-flash", "gemini-3.1-flash-lite"})
BILLING_SOURCE = "https://ai.google.dev/gemini-api/docs/billing"
MODEL_SOURCE = "https://ai.google.dev/gemini-api/docs/pricing"
_PROFILE = re.compile(r"^gemini-[1-4]$")
_EMPTY_ENV = "# AI_VIDEO_02: isolated client work folder; no API configuration.\n"


class FreeRouteError(ValueError):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _profile(profile_id: str) -> None:
    if not isinstance(profile_id, str) or not _PROFILE.fullmatch(profile_id):
        raise FreeRouteError("invalid_profile", "Free Tier route requires one Gemini-1..4 profile.")


def make_route(profile_id: str, model: str, project_label: str, *,
               free_tier_confirmed: bool, no_billing_confirmed: bool) -> dict:
    """Record an explicit choice and short-lived project billing attestation.

    ``project_label`` is a nonsecret owner label, never an API key. Billing
    attestations are scoped to this exact project/model, not other profiles.
    """
    _profile(profile_id)
    if not isinstance(model, str) or model not in FREE_MODELS:
        raise FreeRouteError("free_model_unverified", "Выберите явно проверенную модель с бесплатным Standard Tier.")
    if free_tier_confirmed is not True or no_billing_confirmed is not True:
        raise FreeRouteError("billing_unverified", "Подтвердите Free Tier и отсутствие привязанного биллинга у этого проекта AI Studio.")
    if (not isinstance(project_label, str) or not 1 <= len(project_label.strip()) <= 120
            or any(character in project_label for character in "\r\n\t")
            or re.search(r"AIza|api.?key|token|password|secret|bearer", project_label, flags=re.I)):
        raise FreeRouteError("invalid_project_label", "Введите только краткую несекретную метку проекта; ключ вводится в официальном CLI.")
    now = datetime.now(timezone.utc)
    label = project_label.strip()
    identity = hashlib.sha256((ROUTE_ID + ":" + label.casefold()).encode("utf-8")).hexdigest()
    return {"route_id": ROUTE_ID, "profile_id": profile_id, "model": model, "project_label": label,
            "project_identity_hash": identity, "identity_source": "owner_attested_project",
            "free_tier_confirmed": True, "no_billing_confirmed": True,
            "billing_confirmation_source": "owner_attested_aistudio_project_free_tier_no_billing",
            "confirmed_at": now.isoformat(), "expires_at": (now + timedelta(hours=12)).isoformat(),
            "billing_source": BILLING_SOURCE, "model_source": MODEL_SOURCE,
            "client_version": CLIENT_VERSION, "storage": "official_cli_isolated_encrypted_file",
            "quota_isolation_verified": False, "rotation_on_quota": False}


def validate_route(route: dict, profile_id: str, *, require_current: bool = True) -> None:
    _profile(profile_id)
    if (not isinstance(route, dict) or route.get("route_id") != ROUTE_ID
            or route.get("profile_id") != profile_id or not isinstance(route.get("model"), str)
            or route.get("model") not in FREE_MODELS
            or route.get("client_version") != CLIENT_VERSION):
        raise FreeRouteError("free_route_invalid", "Настройка бесплатного подключения не подтверждена.")
    if route.get("free_tier_confirmed") is not True or route.get("no_billing_confirmed") is not True:
        raise FreeRouteError("billing_unverified", "Проект Free Tier без привязанного биллинга не подтверждён.")
    label = route.get("project_label")
    if not isinstance(label, str) or route.get("project_identity_hash") != hashlib.sha256(
            (ROUTE_ID + ":" + label.strip().casefold()).encode("utf-8")).hexdigest():
        raise FreeRouteError("billing_identity_unverified", "Подтверждение оплаты не связано с указанным проектом.")
    if require_current:
        try:
            expires = datetime.fromisoformat(route["expires_at"])
            current = expires > datetime.now(timezone.utc)
        except (KeyError, TypeError, ValueError):
            current = False
        if not current:
            raise FreeRouteError("billing_confirmation_expired", "Повторно подтвердите Free Tier и отсутствие биллинга у проекта в AI Studio.")


def route_paths(profile_root: Path | str, profile_id: str) -> tuple[Path, Path, Path]:
    _profile(profile_id)
    root = Path(profile_root).resolve()
    name = profile_id + "-api-free"
    home = root / name
    cwd = root.parent / "work" / name
    return home, cwd, home / ".gemini" / "settings.json"


def route_settings() -> dict:
    # Import lazily so AccountManager may use this helper without an import cycle.
    from .ai_accounts import GEMINI_SETTINGS
    settings = deepcopy(GEMINI_SETTINGS)
    settings["security"]["auth"] = {"selectedType": "gemini-api-key", "enforcedType": "gemini-api-key"}
    return settings


def validate_paths(profile_root: Path | str, profile_id: str) -> tuple[Path, Path, Path]:
    root = Path(profile_root).resolve()
    repo = Path(__file__).resolve().parents[1]
    if root == repo or repo in root.parents:
        raise FreeRouteError("unsafe_profile_root", "Credential homes must stay outside the repository.")
    home, cwd, settings = route_paths(root, profile_id)
    credential = home / ".gemini" / "gemini-credentials.json"
    for path in (home, home / ".gemini", home / ".env", home / ".gemini" / ".env", settings,
                 credential, cwd, cwd / ".env", cwd / ".git"):
        if path.resolve() != path:
            raise FreeRouteError("unsafe_profile", "Free Tier home contains a redirected path; no files changed.")
    if credential.exists() and (not credential.is_file() or credential.stat().st_nlink != 1):
        raise FreeRouteError("unsafe_profile", "Official credential store is not an independent local file.")
    ancestors = {home, *home.parents, cwd, *cwd.parents}
    if any((parent / ".gemini" / ".env").exists() for parent in ancestors):
        raise FreeRouteError("billing_unverified", "Обнаружен унаследованный .gemini/.env; запуск остановлен.")
    if (home / ".env").exists():
        raise FreeRouteError("billing_unverified", "Обнаружен .env в новом профиле; запуск остановлен.")
    for candidate in (cwd / ".codex", cwd / ".claude", cwd / ".gemini", cwd / "CLAUDE.md", cwd / "GEMINI.md"):
        if candidate.exists():
            raise FreeRouteError("configuration", "Unexpected project configuration in the isolated work directory.")
    return home, cwd, settings


def prepare_home(profile_root: Path | str, profile_id: str, route: dict) -> dict:
    """Create exact native settings in the NEW home; never copy credentials."""
    validate_route(route, profile_id)
    home, cwd, settings = validate_paths(profile_root, profile_id)
    required = route_settings()
    if settings.exists():
        try:
            if json.loads(settings.read_text(encoding="utf-8")) != required:
                raise FreeRouteError("configuration", "Existing Free Tier settings differ; no automatic overwrite.")
        except (ValueError, OSError) as exc:
            if isinstance(exc, FreeRouteError):
                raise
            raise FreeRouteError("configuration", "Existing Free Tier settings could not be verified.") from exc
    env_file = cwd / ".env"
    if env_file.exists() and env_file.read_text(encoding="utf-8") != _EMPTY_ENV:
        raise FreeRouteError("billing_unverified", "Existing work environment differs; no automatic overwrite.")
    home.mkdir(parents=True, exist_ok=True)
    cwd.mkdir(parents=True, exist_ok=True)
    validate_paths(profile_root, profile_id)
    (cwd / ".git").mkdir(exist_ok=True)
    if not env_file.exists():
        with env_file.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(_EMPTY_ENV)
    settings.parent.mkdir(parents=True, exist_ok=True)
    if not settings.exists():
        with settings.open("x", encoding="utf-8", newline="\n") as stream:
            stream.write(json.dumps(required, ensure_ascii=False, indent=2) + "\n")
    return {"profile_id": profile_id, "route_id": ROUTE_ID, "home": str(home), "cwd": str(cwd),
            "settings_path": str(settings), "model": route["model"], "credentials_created": False}


def child_environment(home: Path | str, source: dict | None = None) -> dict[str, str]:
    """Force the official CLI's per-home encrypted store, never shared keychain."""
    from .ai_accounts import isolated_environment
    result = isolated_environment("gemini", Path(home), source)
    result["GEMINI_FORCE_FILE_STORAGE"] = "true"
    return result


def credential_metadata(profile_root: Path | str, profile_id: str) -> dict:
    """Presence is setup evidence only. Never read or decrypt the key store."""
    home, _, _ = validate_paths(profile_root, profile_id)
    path = home / ".gemini" / "gemini-credentials.json"
    present = path.is_file()
    metadata = path.stat() if present else None
    return {"present": present, "size": metadata.st_size if metadata else None,
            "modified_ns": metadata.st_mtime_ns if metadata else None,
            "authentication_verified": False, "model_access_verified": False,
            "billing_verified": False}
