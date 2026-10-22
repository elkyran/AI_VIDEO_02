"""Durable editorial queue beside the studio's existing Registry.

Only ai_* tables are owned here. Production jobs, audio and timelines are never
dispatched by this coordinator. Fake adapters have explicitly simulated slots;
an existing configuration directory is not evidence of real authentication.
"""

from __future__ import annotations

from contextlib import contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import threading
import time
import uuid
import weakref

from .registry import Registry, StudioError, canonical_json, content_hash, workspace_path


PROVIDERS = ("codex", "claude", "gemini")
ACTIVE_PROVIDERS = ("codex", "gemini")
PROFILE_NUMBERS = (1, 2, 3, 4)
DEFAULT_ROLES = {"writer": "codex", "director": "codex", "critic": "gemini"}
ACTIVE_ATTEMPTS = ("running", "stopping")
_LIVE = weakref.WeakValueDictionary()
_IDENTIFIER = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$")


def _hash(value):
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


def _identifier(value, name):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise StudioError("SCHEMA_INVALID", f"Invalid {name}")
    return value


class Coordinator:
    """Single-process poll scheduler, with durable attempts and CAS acceptance.

    Calling tick is nonblocking: adapters must return promptly from start/poll.
    Active attempt leases are not proof that a process has stopped. A live owner
    keeps its reservations; live access comes from a separate account manager.
    """

    def __init__(self, workspace, adapters=None, profile_source=None, result_observer=None, result_gate=None, worker_routes=None):
        from .v2_routes import WorkerRoutes
        self.worker_routes = WorkerRoutes.model_validate_json(worker_routes.model_dump_json()) if worker_routes is not None else None
        self.workspace = Path(workspace).resolve()
        production = Path(__file__).resolve().parents[1] / "workspace" / "studio"
        if self.workspace == production.resolve():
            raise StudioError("OFFLINE_WORKSPACE_REQUIRED", "Use a separate development workspace, not workspace/studio")
        if adapters is None:
            from .ai_adapters import FakeAdapter
            adapters = {provider: FakeAdapter(provider=provider) for provider in ACTIVE_PROVIDERS}
        self.adapters = dict(adapters)
        self.profile_source = profile_source
        self.result_observer = result_observer
        self.result_gate = result_gate
        if result_gate is not None:
            result_gate.route_validator = self.validate_worker_route
        from .development import locked
        self._workspace_lock = locked(self.workspace / "ai" / "coordinator.lock")
        try:
            self._workspace_lock.__enter__()
        except ValueError as exc:
            raise StudioError("COORDINATOR_RUNNING", "This workspace already has an active coordinator") from exc
        self.owner_id = uuid.uuid4().hex
        self._lock = threading.RLock()
        self._runs = {}
        self._report_backoff = {}
        self.clock = time.time
        self._closed = False
        _LIVE[self.owner_id] = self
        try:
            with Registry(self.workspace) as registry:
                registry.db.executescript("""
                    CREATE TABLE IF NOT EXISTS ai_tasks (
                        id TEXT PRIMARY KEY, project_id TEXT NOT NULL, role TEXT NOT NULL,
                        status TEXT NOT NULL, payload TEXT NOT NULL, request_hash TEXT NOT NULL,
                        current_attempt TEXT, attempts INTEGER NOT NULL DEFAULT 0,
                        transitions INTEGER NOT NULL DEFAULT 0, result TEXT, error TEXT,
                        pinned INTEGER NOT NULL DEFAULT 0, not_before REAL NOT NULL DEFAULT 0,
                        created REAL NOT NULL, updated REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS ai_attempts (
                        id TEXT PRIMARY KEY, task_id TEXT NOT NULL REFERENCES ai_tasks(id),
                        number INTEGER NOT NULL, profile_id TEXT NOT NULL, provider TEXT NOT NULL,
                        resource_class TEXT NOT NULL, status TEXT NOT NULL, owner_id TEXT NOT NULL,
                        owner_pid INTEGER NOT NULL, run_id TEXT, lease_until REAL NOT NULL,
                        input_versions TEXT NOT NULL, result_path TEXT NOT NULL,
                        created REAL NOT NULL, ended REAL, error TEXT);
                    CREATE TABLE IF NOT EXISTS ai_artifacts (
                        id TEXT PRIMARY KEY, task_id TEXT NOT NULL, attempt_id TEXT NOT NULL UNIQUE,
                        kind TEXT NOT NULL, path TEXT NOT NULL, sha256 TEXT NOT NULL,
                        input_versions TEXT NOT NULL, created REAL NOT NULL);
                    CREATE TABLE IF NOT EXISTS ai_events (
                        sequence INTEGER PRIMARY KEY AUTOINCREMENT, timestamp REAL NOT NULL,
                        kind TEXT NOT NULL, task_id TEXT, attempt_id TEXT, profile_id TEXT,
                        payload TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS ai_profiles (id TEXT PRIMARY KEY, payload TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS ai_settings (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                    CREATE TABLE IF NOT EXISTS ai_profile_reports (
                        attempt_id TEXT PRIMARY KEY, payload TEXT NOT NULL, delivered INTEGER NOT NULL DEFAULT 0);
                    CREATE TABLE IF NOT EXISTS ai_scenes (
                        project_id TEXT NOT NULL, scene_id TEXT NOT NULL, version INTEGER NOT NULL,
                        payload TEXT NOT NULL, sha256 TEXT NOT NULL, pinned INTEGER NOT NULL DEFAULT 0,
                        source_task_id TEXT, created REAL NOT NULL,
                        PRIMARY KEY(project_id,scene_id,version));
                """)
                with registry.transaction():
                    defaults = {"control": "paused", "selection_mode": "per_provider",
                                "selected_profiles": {p: f"{p}-1" for p in ACTIVE_PROVIDERS},
                                "pending_selection": None, "rotation_on_quota": False,
                                "roles": {r: {"role": r, "provider": p, "model": None,
                                              "rules": "", "rules_version": 1} for r, p in DEFAULT_ROLES.items()}}
                    for key, value in defaults.items():
                        registry.db.execute("INSERT OR IGNORE INTO ai_settings VALUES (?,?)", (key, canonical_json(value)))
                    self._migrate_active_configuration(registry)
                    if any(not getattr(self.adapters.get(p), "is_simulated", False) for p in ACTIVE_PROVIDERS):
                        # An old simulator preference never authorizes live rotation.
                        self._setting(registry, "rotation_on_quota", False)
                    for provider in ACTIVE_PROVIDERS:
                        simulated = bool(getattr(self.adapters.get(provider), "is_simulated", False))
                        for number in PROFILE_NUMBERS:
                            profile = {"profile_id": f"{provider}-{number}", "provider": provider,
                                       "number": number, "label": f"{'GPT' if provider == 'codex' else 'Gemini'}-{number}",
                                       "profile_ref": None, "identity_verified": False,
                                       "observed_identity_label": None, "last_auth_check": None,
                                       "last_successful_run": None, "actual_auth_state": "not_configured",
                                       "state": "ready" if simulated else "not_configured",
                                       "simulated": simulated, "model": None,
                                       "rotation_support": "simulator_only" if simulated else "unverified",
                                       "rotation_evidence": None, "quota_remaining": None,
                                       "quota_limit": None, "quota_reset_at": None, "quota_source": None}
                            registry.db.execute("INSERT OR IGNORE INTO ai_profiles VALUES (?,?)",
                                                (profile["profile_id"], canonical_json(profile)))
                if self.result_gate and hasattr(self.result_gate, 'initialize'):
                    self.result_gate.initialize(registry)
            self._recover()
            self._flush_profile_reports()
        except BaseException:
            self.close()
            raise

    def _settings(self, registry):
        return {row["key"]: json.loads(row["value"]) for row in registry.db.execute("SELECT * FROM ai_settings")}

    def _route(self, task):
        return self.worker_routes.for_task(task) if self.worker_routes else None

    def validate_worker_route(self, registry, task, attempt, request, *, historical=False):
        """Same request binding for poll, manual, recovery and historical replay."""
        from .v2_routes import WorkerRoute
        pinned, requested = task.get('worker_route'), request.get('worker_route')
        if pinned is None and requested is None:
            return
        if pinned is None or requested != pinned:
            raise StudioError('WORKER_ROUTE_MISMATCH', 'Task and immutable request routes differ')
        route = WorkerRoute.model_validate(pinned)
        if (attempt['provider'] != route.provider or attempt['profile_id'] != route.profile_id
                or request.get('account_binding') != route.account_binding.model_dump()
                or request.get('task_id') != task['task_id'] or request.get('attempt_id') != attempt['id']
                or request.get('project_id') != task['project_id']
                or request.get('input_versions') != task['input_versions']):
            raise StudioError('WORKER_ROUTE_MISMATCH', 'Attempt does not bind this account/task/input')
        contract = task.get('context', {}).get('v2_contract')
        if contract and (contract['role'], contract['assigned_worker'], contract['provider_profile_id']) != (
                route.role, route.worker_id, route.profile_id):
            raise StudioError('WORKER_ROUTE_MISMATCH', 'Canonical Task differs from host worker binding')
        if not historical:
            current = self._route(task)
            if current != route:
                raise StudioError('WORKER_ROUTE_STALE', 'Host route/account generation changed; accepted history is preserved')
            adapter = self.adapters.get(route.provider)
            if route.synthetic != (getattr(adapter, 'is_simulated', False) is True):
                raise StudioError('WORKER_ROUTE_MISMATCH', 'Route provenance changed')
            if not route.synthetic:
                profile = self._profile(registry, route.provider, route.profile_id)
                if profile.get('account_binding') != route.account_binding.model_dump():
                    raise StudioError('WORKER_ROUTE_STALE', 'Account manager binding changed')

    def _active_route(self, registry, attempt):
        path = workspace_path(self.workspace, Path(attempt['result_path']).with_name('request.json').as_posix())
        from .v2_routes import WorkerRoute
        try:
            request = json.loads(path.read_text(encoding='utf-8'))
            row = registry.db.execute('SELECT * FROM ai_tasks WHERE id=?', (attempt['task_id'],)).fetchone()
            task = self._task(row)
            self.validate_worker_route(registry, task, attempt, request, historical=True)
            value = task.get('worker_route')
            return WorkerRoute.model_validate(value) if value is not None else None
        except (ValueError, OSError, TypeError, AttributeError, KeyError, StudioError):
            return None  # Unknown reservation remains held conservatively.

    def _pending_report(self, registry, provider, route):
        attempts = registry.db.execute('SELECT a.* FROM ai_profile_reports r JOIN ai_attempts a ON a.id=r.attempt_id '
            'WHERE r.delivered=0 AND a.provider=?', (provider,)).fetchall()
        if route is None:
            return bool(attempts)
        for attempt in attempts:
            pending = self._active_route(registry, attempt)
            if pending is None or pending.reservation == route.reservation:
                return True
        return False

    def _setting(self, registry, key, value):
        registry.db.execute("INSERT OR REPLACE INTO ai_settings VALUES (?,?)", (key, canonical_json(value)))

    def _migrate_active_configuration(self, registry):
        """Preserve historical assignments; only the current roster is migrated."""
        settings = self._settings(registry)
        if settings.get("active_configuration_version") == 2:
            return
        historical = {"selected_profiles": settings["selected_profiles"],
                      "pending_selection": settings["pending_selection"], "roles": settings["roles"]}
        self._setting(registry, "historical_configuration", historical)
        selected = {provider: settings["selected_profiles"].get(provider, f"{provider}-1")
                    for provider in ACTIVE_PROVIDERS}
        self._setting(registry, "selected_profiles", selected)
        pending = settings["pending_selection"]
        if pending:
            self._setting(registry, "pending_selection", {**pending, "selected_profiles": {
                provider: pending["selected_profiles"].get(provider, selected[provider])
                for provider in ACTIVE_PROVIDERS}})
        roles = json.loads(canonical_json(settings["roles"]))
        migrated = []
        for role, item in roles.items():
            if item["provider"] not in ACTIVE_PROVIDERS:
                item["provider"] = DEFAULT_ROLES[role]
                if item.get("model"):
                    item["requires_model_confirmation"] = True
                migrated.append(role)
        self._setting(registry, "roles", roles)
        self._setting(registry, "active_configuration_version", 2)
        self._event(registry, "active_configuration_migrated", {
            "active_providers": list(ACTIVE_PROVIDERS), "profiles_per_provider": 4,
            "migrated_roles": migrated, "historical_tasks_preserved": True})

    def _event(self, registry, kind, payload=None, task_id=None, attempt_id=None, profile_id=None):
        from .ai_adapters import sanitize
        registry.db.execute("INSERT INTO ai_events(timestamp,kind,task_id,attempt_id,profile_id,payload) VALUES(?,?,?,?,?,?)",
                            (self.clock(), kind, task_id, attempt_id, profile_id, canonical_json(sanitize(payload or {}))))

    def _task(self, row):
        value = json.loads(row["payload"])
        value.update(status=row["status"], attempt=row["attempts"], current_attempt_id=row["current_attempt"],
                     transitions=row["transitions"], pinned=bool(row["pinned"]),
                     result=json.loads(row["result"]) if row["result"] else None,
                     error=json.loads(row["error"]) if row["error"] else None,
                     not_before=row["not_before"], created=row["created"], updated=row["updated"])
        return value

    def _profile(self, registry, provider, profile_id):
        """Read fresh, public account state; never import client credentials."""
        from .ai_adapters import sanitize
        row = registry.db.execute("SELECT payload FROM ai_profiles WHERE id=?", (profile_id,)).fetchone()
        stored = json.loads(row[0]) if row else {}
        if getattr(self.adapters.get(provider), "is_simulated", False):
            return stored
        # Readiness and successful runs from old fake slots are not live evidence.
        result = {**stored, "state": "not_configured", "simulated": False,
                  "actual_auth_state": "not_configured", "access_state": "unverified",
                  "billing_safe": False, "can_probe": False, "last_successful_run": None,
                  "rotation_support": "disabled", "rotation_evidence": None}
        if not callable(self.profile_source):
            result["error"] = {"kind": "unverified_access", "message": "Account manager is not connected"}
            return result
        try:
            fresh = self.profile_source(profile_id)
            if not isinstance(fresh, dict) or fresh.get("profile_id") != profile_id or fresh.get("provider") != provider:
                raise ValueError("Account manager returned a different profile")
            if fresh.get("simulated") is True or fresh.get("is_simulated") is True:
                raise ValueError("A simulated profile is not real authentication evidence")
            # Launch environment/commands belong to the adapter, not SQLite/UI.
            public = {"profile_id", "provider", "number", "slot", "label", "profile_ref", "state",
                      "actual_auth_state", "auth_state", "access_state", "billing_safe", "can_probe",
                      "auth_mode", "identity_verified", "identity_verification", "observed_identity_label",
                      "identity_duplicate_of", "last_auth_check", "last_successful_run", "last_check",
                      "checked_at", "last_checked", "installation_state", "install_state", "client_version",
                      "error", "reason", "reason_code", "model", "billing_verification", "billing_limitation", "account_binding", "paused",
                      "access_route", "route_configuration_valid"}
            result.update(sanitize({key: value for key, value in fresh.items() if key in public}))
            result["simulated"] = False
            return result
        except Exception as exc:
            result["error"] = {"kind": "unverified_access", "message": sanitize(str(exc))}
            return result

    @staticmethod
    def _access_failure(profile, access_check=False):
        if profile.get("paused") or profile.get("state") == "paused" or profile.get("reason_code") == "profile_paused":
            return {"kind": "profile_paused", "message": "Resume this profile before starting new work"}
        if profile.get("reason_code") == "unsupported_client":
            return {"kind": "unsupported_client", "message": profile.get("reason") or "This provider access route is unsupported", "quota_confirmed": False}
        if profile.get("actual_auth_state") != "authenticated":
            return {"kind": "authentication", "message": "Official login has not been verified for this profile"}
        if profile.get("billing_safe") is not True:
            return {"kind": "billing", "message": "Subscription/free OAuth execution mode is not verified"}
        binding = profile.get("account_binding")
        if (not isinstance(binding, dict) or not isinstance(binding.get("identity_hash"), str)
                or not re.fullmatch(r"[0-9a-f]{64}", binding["identity_hash"])
                or type(binding.get("auth_generation")) is not int or binding["auth_generation"] < 0):
            return {"kind": "authentication", "message": "Account identity and login generation must be bound before execution"}
        if access_check and profile.get("can_probe") is True:
            return None
        if profile.get("access_state") != "verified" or profile.get("state") != "ready":
            return {"kind": "unverified_access", "message": "A short explicit access check is required before creative tasks"}
        return None

    @staticmethod
    def _approved_route_model(profile):
        """The opt-in Free Tier route must use its explicit approved model."""
        if profile.get("access_route") != "gemini_api_free":
            return None
        from .gemini_free_route import FREE_MODELS
        if (profile.get("provider") != "gemini" or profile.get("route_configuration_valid") is not True
                or not isinstance(profile.get("model"), str) or profile["model"] not in FREE_MODELS):
            raise StudioError("FREE_ROUTE_MODEL_REQUIRED", "Confirm the current Free Tier route and its explicitly approved model")
        return profile["model"]

    def _queue_profile_report(self, registry, attempt, task, result=None, failure=None):
        if task.get("execution_mode") != "live" or not getattr(self.adapters.get(attempt["provider"]), "is_live", False):
            return
        from .ai_adapters import sanitize
        # Authentication may have changed while a result waited for delivery.
        # Only the immutable request records which account actually ran it.
        binding = None
        try:
            request_path = workspace_path(self.workspace, Path(attempt["result_path"]).with_name("request.json").as_posix())
            binding = json.loads(request_path.read_text(encoding="utf-8")).get("account_binding")
        except (OSError, ValueError, StudioError):
            self._event(registry, "profile_binding_unavailable", {"account_state_unchanged": True}, task["task_id"], attempt["id"], attempt["profile_id"])
        report = {"profile_id": attempt["profile_id"], "task": {**task, "attempt_id": attempt["id"], "account_binding": binding},
                  "result": result, "failure": failure}
        registry.db.execute("INSERT OR IGNORE INTO ai_profile_reports(attempt_id,payload) VALUES (?,?)",
                            (attempt["id"], canonical_json(sanitize(report))))

    def _flush_profile_reports(self):
        """Deliver committed outcomes; callback errors never replay a model run."""
        if not callable(self.result_observer):
            return
        with Registry(self.workspace) as registry:
            pending = registry.db.execute("SELECT * FROM ai_profile_reports WHERE delivered=0").fetchall()
        for row in pending:
            report = json.loads(row["payload"])
            if report["profile_id"].rsplit("-", 1)[0] not in ACTIVE_PROVIDERS:
                # Historical results remain durable here; inactive accounts are
                # outside the current observer API and must not retry forever.
                with Registry(self.workspace) as registry, registry.transaction():
                    registry.db.execute("UPDATE ai_profile_reports SET delivered=1 WHERE attempt_id=?", (row["attempt_id"],))
                    self._event(registry, "historical_profile_report_retained", {"active_account_state_unchanged": True},
                                report["task"]["task_id"], row["attempt_id"], report["profile_id"])
                continue
            retry_at, failures = self._report_backoff.get(row["attempt_id"], (0, 0))
            if self.clock() < retry_at:
                continue
            try:
                self.result_observer(report["profile_id"], report["task"], report["result"], report["failure"])
            except Exception as exc:
                self._report_backoff[row["attempt_id"]] = (self.clock() + min(2 ** min(failures + 1, 6), 60), failures + 1)
                with Registry(self.workspace) as registry, registry.transaction():
                    self._event(registry, "profile_report_error", {"message": str(exc), "pending_delivery": True},
                                report["task"]["task_id"], row["attempt_id"], report["profile_id"])
                continue
            with Registry(self.workspace) as registry, registry.transaction():
                registry.db.execute("UPDATE ai_profile_reports SET delivered=1 WHERE attempt_id=?", (row["attempt_id"],))
            self._report_backoff.pop(row["attempt_id"], None)

    def enqueue(self, task, *, _assignment=None):
        from .ai_adapters import sanitize
        spec = sanitize(dict(task))
        spec.setdefault("schema_version", 1)
        spec.setdefault("task_id", "task_" + uuid.uuid4().hex[:20])
        spec.setdefault("project_id", "offline_demo")
        spec.setdefault("kind", "editorial")
        spec.setdefault("role", "writer")
        spec.setdefault("input_versions", {})
        spec.setdefault("depends_on", [])
        spec.setdefault("expected_output", "note")
        spec.setdefault("context", {})
        spec.setdefault("resource_class", "cloud")
        spec.setdefault("max_attempts", 3)
        spec.setdefault("pinned", False)
        for key in ("task_id", "project_id"):
            _identifier(spec[key], key)
        if spec["role"] not in DEFAULT_ROLES or spec["resource_class"] not in {"cloud", "local_heavy"}:
            raise StudioError("SCHEMA_INVALID", "Unknown role or resource class")
        from .v2_acquisition_contracts import ACQUISITION_OUTPUTS
        if spec["expected_output"] not in {"scene", "edit_plan", "review", "note", *ACQUISITION_OUTPUTS}:
            raise StudioError("SCHEMA_INVALID", "Unknown expected_output")
        if not isinstance(spec["input_versions"], dict) or not isinstance(spec["context"], dict):
            raise StudioError("SCHEMA_INVALID", "input_versions and context must be objects")
        if any(not isinstance(value, str) for value in spec["input_versions"].values()):
            raise StudioError("SCHEMA_INVALID", "input_versions values must be version references")
        scene_version = spec["context"].get("scene_version")
        if scene_version is not None and (type(scene_version) is not int or scene_version < 1):
            raise StudioError("SCHEMA_INVALID", "scene_version must be a positive integer")
        if not isinstance(spec["depends_on"], list) or any(not isinstance(item, str) for item in spec["depends_on"]):
            raise StudioError("SCHEMA_INVALID", "depends_on must be task ids")
        if type(spec["max_attempts"]) is not int or not 1 <= spec["max_attempts"] <= 3:
            raise StudioError("SCHEMA_INVALID", "max_attempts must be between 1 and 3")
        if spec["kind"] == "access_check":
            provider = spec.get("probe_provider")
            if provider not in ACTIVE_PROVIDERS or spec.get("requested_profile_id") not in {f"{provider}-{n}" for n in PROFILE_NUMBERS}:
                raise StudioError("SCHEMA_INVALID", "Access check must name exactly one provider profile")
            if spec["expected_output"] != "note" or spec["max_attempts"] != 1 or spec["depends_on"] or spec["resource_class"] != "cloud":
                raise StudioError("SCHEMA_INVALID", "Access checks are one bounded note request without dependencies")
        elif "requested_profile_id" in spec or "probe_provider" in spec:
            raise StudioError("SCHEMA_INVALID", "Creative work uses the selected profiles only")
        worker_task = (spec['resource_class'] == 'cloud' and spec['kind'] != 'access_check'
                       and spec['context'].get('v2_contract', {}).get('role') != 'python')
        route = self._route(spec) if worker_task else None
        if 'worker_route' in spec and (route is None or spec['worker_route'] != route.model_dump()):
            raise StudioError('WORKER_ROUTE_MISMATCH', 'Only explicit host configuration can pin worker routes')
        if self.worker_routes is not None and worker_task:
            if route is None:
                raise StudioError('WORKER_ROUTE_REQUIRED', 'No unique host route for this task role')
            spec['worker_route'] = route.model_dump()
            contract = spec['context'].get('v2_contract')
            if contract and (contract['role'], contract['assigned_worker'], contract['provider_profile_id']) != (
                    route.role, route.worker_id, route.profile_id):
                raise StudioError('WORKER_ROUTE_MISMATCH', 'Task must name its fixed host worker/profile')
        if _assignment is not None:
            if _assignment.get("role") != spec["role"] or _assignment.get("provider") not in ACTIVE_PROVIDERS:
                raise StudioError("SCHEMA_INVALID", "Unknown active role assignment")
            spec["assignment"] = sanitize(dict(_assignment))
        request_hash = _hash(spec)
        with self._lock, Registry(self.workspace) as registry, registry.transaction():
            prior = registry.db.execute("SELECT * FROM ai_tasks WHERE id=?", (spec["task_id"],)).fetchone()
            if prior:
                if prior["request_hash"] != request_hash:
                    raise StudioError("IDEMPOTENCY_CONFLICT", "Task id already has different input")
                return self._task(prior)
            for dependency in spec["depends_on"]:
                row = registry.db.execute("SELECT project_id FROM ai_tasks WHERE id=?", (dependency,)).fetchone()
                if not row or row["project_id"] != spec["project_id"]:
                    raise StudioError("DEPENDENCY_INVALID", "Dependency must be an existing task in the same project")
            spec["assignment"] = (spec["assignment"] if _assignment is not None
                                  else self._settings(registry)["roles"][spec["role"]])
            if route:
                spec['assignment'] = {**spec['assignment'], 'provider': route.provider}
            if spec["kind"] == "access_check":
                spec["assignment"] = {**spec["assignment"], "provider": spec["probe_provider"], "model": None,
                                      "rules": "Return only a short technical access-check note."}
                spec["assignment"].pop("requires_model_confirmation", None)
                spec["assignment"]["model"] = self._approved_route_model(
                    self._profile(registry, spec["probe_provider"], spec["requested_profile_id"]))
            adapter = self.adapters.get("local" if spec["resource_class"] == "local_heavy" else spec["assignment"]["provider"])
            spec["execution_mode"] = ("local" if spec["resource_class"] == "local_heavy" else
                                      "simulated" if getattr(adapter, "is_simulated", False) else
                                      "local" if getattr(adapter, "is_local", False) else "live")
            now = self.clock()
            registry.db.execute("INSERT INTO ai_tasks(id,project_id,role,status,payload,request_hash,pinned,created,updated) VALUES(?,?,?,?,?,?,?,?,?)",
                                (spec["task_id"], spec["project_id"], spec["role"], "pending", canonical_json(spec),
                                 request_hash, int(bool(spec["pinned"])), now, now))
            self._event(registry, "task_enqueued", {"simulated": spec["execution_mode"] == "simulated"}, spec["task_id"])
            return self._task(registry.db.execute("SELECT * FROM ai_tasks WHERE id=?", (spec["task_id"],)).fetchone())

    def snapshot(self):
        with self._lock, Registry(self.workspace) as registry:
            settings = self._settings(registry)
            tasks = [self._task(row) for row in registry.db.execute("SELECT * FROM ai_tasks ORDER BY created,id")]
            attempts = [dict(row) for row in registry.db.execute("SELECT * FROM ai_attempts ORDER BY created,id")]
            profiles = [self._profile(registry, p, f"{p}-{n}") for p in ACTIVE_PROVIDERS for n in PROFILE_NUMBERS]
            historical_profiles = [{**json.loads(row["payload"]), "active": False, "selected": False}
                                   for row in registry.db.execute("SELECT * FROM ai_profiles ORDER BY id")
                                   if json.loads(row["payload"]).get("provider") not in ACTIVE_PROVIDERS]
            for profile in profiles:
                profile["active"] = True
                active = next((a for a in attempts if a["profile_id"] == profile["profile_id"] and a["status"] in ACTIVE_ATTEMPTS), None)
                profile["selected"] = settings["selected_profiles"].get(profile["provider"]) == profile["profile_id"]
                profile["active_task_id"] = active["task_id"] if active else None
                profile["active_role"] = next((t["role"] for t in tasks if active and t["task_id"] == active["task_id"]), None)
                profile["state"] = "running" if active else profile["state"]
            counts = {status: sum(t["status"] == status for t in tasks) for status in
                      ("pending", "running", "completed", "waiting_access", "blocked", "failed", "interrupted", "stale")}
            scenes = []
            for row in registry.db.execute("SELECT * FROM ai_scenes ORDER BY project_id,scene_id,version"):
                payload = json.loads(row["payload"])
                scenes.append({**payload, "text": "\n".join(part["text"] for part in payload["narration_segments"]),
                               "pinned": bool(row["pinned"]), "sha256": row["sha256"], "source_task_id": row["source_task_id"]})
            simulated_count = sum(bool(getattr(self.adapters.get(p), "is_simulated", False)) for p in ACTIVE_PROVIDERS)
            mode = "offline_simulation" if simulated_count == len(ACTIVE_PROVIDERS) else "mixed_execution" if simulated_count else "live"
            return {"schema_version": 2, "mode": mode, "simulated": simulated_count == len(ACTIVE_PROVIDERS),
                    "workspace": str(self.workspace), **settings, "roles": list(settings["roles"].values()),
                    "profiles": profiles, "historical_profiles": historical_profiles,
                    "active_providers": list(ACTIVE_PROVIDERS), "tasks": tasks, "attempts": attempts, "scenes": scenes,
                    "artifacts": [dict(row) for row in registry.db.execute("SELECT * FROM ai_artifacts ORDER BY created")],
                    "counts": counts, "limits": {"max_cloud_workers": 3, "max_workers_per_provider": 1 if self.worker_routes is None else None,
                                                   **({'legacy_max_workers_per_provider': 1, 'max_workers_per_account': 1,
                                                       'max_workers_per_worker': 1} if self.worker_routes else {}),
                                                   "max_workers_per_profile": 1, "max_local_heavy_jobs": 1},
                    "quality_status": "not_measured", "truncated": False}

    def events(self, after=0, limit=200):
        limit = min(max(int(limit), 1), 1000)
        with self._lock, Registry(self.workspace) as registry:
            rows = registry.db.execute("SELECT * FROM ai_events WHERE sequence>? ORDER BY sequence LIMIT ?", (int(after), limit + 1)).fetchall()
            values = [{**dict(row), "payload": json.loads(row["payload"])} for row in rows[:limit]]
            return {"events": values, "last_event_id": values[-1]["sequence"] if values else int(after), "truncated": len(rows) > limit}

    def _control(self, value):
        with self._lock, Registry(self.workspace) as registry, registry.transaction():
            self._setting(registry, "control", value)
            self._event(registry, "control_changed", {"control": value})
        return self.snapshot()

    def pause(self):
        return self._control("paused")

    def resume(self, worker_id=None):
        with self._lock, Registry(self.workspace) as registry, registry.transaction():
            pauses = self._settings(registry).get('worker_pauses', {})
            requested = None
            if worker_id is not None:
                match = next((r for r in self.worker_routes.routes if r.worker_id == worker_id), None) if self.worker_routes else None
                if match is None:
                    raise StudioError('WORKER_ROUTE_REQUIRED', 'Resume requires a configured host worker')
                requested = match.reservation
            pauses = {key: value for key, value in pauses.items()
                      if value.get('resume_after', 0) > self.clock() or requested is not None and key != requested}
            self._setting(registry, 'worker_pauses', pauses)
            for row in registry.db.execute("SELECT * FROM ai_tasks WHERE status IN ('interrupted','waiting_access')").fetchall():
                task = self._task(row)
                route = self._route(task) if task.get('worker_route') else None
                if route and (route.reservation in pauses or requested is not None and route.reservation != requested):
                    continue
                if requested is not None and route is None:
                    continue
                if row["attempts"] < task["max_attempts"]:
                    registry.db.execute("UPDATE ai_tasks SET status='pending',error=NULL WHERE id=?", (row["id"],))
        return self._control("running")

    start = resume

    def wake_profile(self, profile_id):
        """Resume only requests held by this profile's explicit pause.

        This does not resume the global queue, change selected accounts or
        replenish a task's attempt budget.
        """
        provider = next((p for p in ACTIVE_PROVIDERS if profile_id in {f"{p}-{n}" for n in PROFILE_NUMBERS}), None)
        if provider is None:
            raise StudioError("SCHEMA_INVALID", "Unknown active profile slot")
        with self._lock, Registry(self.workspace) as registry, registry.transaction():
            settings = self._settings(registry)
            failure = self._access_failure(self._profile(registry, provider, profile_id), access_check=True)
            awakened = []
            if not failure or failure.get("kind") != "profile_paused":
                for row in registry.db.execute("SELECT * FROM ai_tasks WHERE status='waiting_access'").fetchall():
                    task = self._task(row)
                    if (not task["error"] or task["error"].get("kind") != "profile_paused"
                            or task["attempt"] >= task["max_attempts"] or task["resource_class"] != "cloud"):
                        continue
                    task_provider = task["assignment"]["provider"]
                    task_profile = (task.get("requested_profile_id") if task["kind"] == "access_check"
                                    else settings["selected_profiles"].get(task_provider))
                    if task_profile != profile_id:
                        continue
                    registry.db.execute("UPDATE ai_tasks SET status='pending',error=NULL,updated=? WHERE id=?",
                                        (self.clock(), row["id"]))
                    awakened.append(row["id"])
            self._event(registry, "profile_queue_woken", {"task_ids": awakened, "control": settings["control"]}, profile_id=profile_id)
        return self.snapshot()

    def stop(self):
        with self._lock:
            self._control("stopped")
            with Registry(self.workspace) as registry, registry.transaction():
                for attempt in registry.db.execute("SELECT * FROM ai_attempts WHERE status IN ('running','stopping')").fetchall():
                    registry.db.execute("UPDATE ai_tasks SET status='interrupted',current_attempt=NULL,updated=? WHERE id=? AND current_attempt=?",
                                        (self.clock(), attempt["task_id"], attempt["id"]))
                    registry.db.execute("UPDATE ai_attempts SET status='stopping' WHERE id=?", (attempt["id"],))
                    self._event(registry, "stop_requested", {}, attempt["task_id"], attempt["id"], attempt["profile_id"])
            self._cancel_running()
        return self.snapshot()

    def _cancel_running(self):
        with Registry(self.workspace) as registry:
            for attempt in registry.db.execute("SELECT * FROM ai_attempts WHERE status='stopping'").fetchall():
                run = self._runs.get(attempt["id"])
                if run:
                    try:
                        self.adapters[run[0]].cancel(run[1])
                    except Exception as exc:
                        self._event(registry, "cancel_error", {"message": str(exc)}, attempt["task_id"], attempt["id"])

    def select_profiles(self, mode, selection):
        if mode not in {"group", "per_provider"}:
            raise StudioError("SCHEMA_INVALID", "mode must be group or per_provider")
        with self._lock, Registry(self.workspace) as registry, registry.transaction():
            settings = self._settings(registry)
            selected = dict(settings["selected_profiles"])
            if mode == "group":
                number = selection.get("group", selection.get("number")) if isinstance(selection, dict) else selection
                if type(number) is not int or number not in PROFILE_NUMBERS:
                    raise StudioError("SCHEMA_INVALID", "group must be 1, 2, 3 or 4")
                selected = {provider: f"{provider}-{number}" for provider in ACTIVE_PROVIDERS}
            else:
                if not isinstance(selection, dict) or any(p not in ACTIVE_PROVIDERS for p in selection):
                    raise StudioError("SCHEMA_INVALID", "selection must map providers to slots")
                for provider, value in selection.items():
                    identifier = f"{provider}-{value}" if type(value) is int else value
                    if identifier not in {f"{provider}-{number}" for number in PROFILE_NUMBERS}:
                        raise StudioError("SCHEMA_INVALID", "Unknown profile slot")
                    selected[provider] = identifier
            self._setting(registry, "pending_selection", {"mode": mode, "selected_profiles": selected})
            self._event(registry, "selection_requested", {"mode": mode, "selected_profiles": selected})
            self._apply_selection(registry)
        return self.snapshot()

    def _apply_selection(self, registry):
        settings = self._settings(registry)
        pending = settings["pending_selection"]
        if not pending:
            return set()
        affected = {p for p in ACTIVE_PROVIDERS if settings["selected_profiles"][p] != pending["selected_profiles"][p]}
        if pending["mode"] == "group":
            affected = set(ACTIVE_PROVIDERS)
        active = {row[0] for row in registry.db.execute("SELECT provider FROM ai_attempts WHERE status IN ('running','stopping')")}
        if active & affected:
            return affected
        self._setting(registry, "selection_mode", pending["mode"])
        self._setting(registry, "selected_profiles", pending["selected_profiles"])
        self._setting(registry, "pending_selection", None)
        for row in registry.db.execute("SELECT * FROM ai_tasks WHERE status='waiting_access'").fetchall():
            if row["attempts"] < json.loads(row["payload"])["max_attempts"]:
                registry.db.execute("UPDATE ai_tasks SET status='pending',error=NULL WHERE id=?", (row["id"],))
        self._event(registry, "selection_applied", pending)
        return set()

    def set_rotation(self, enabled):
        if type(enabled) is not bool:
            raise StudioError("SCHEMA_INVALID", "rotation flag must be boolean")
        with self._lock, Registry(self.workspace) as registry, registry.transaction():
            if enabled and any(not getattr(self.adapters.get(provider), "is_simulated", False) for provider in ACTIVE_PROVIDERS):
                raise StudioError("ROTATION_UNVERIFIED", "Automatic rotation is implemented only for FakeAdapter")
            self._setting(registry, "rotation_on_quota", enabled)
            self._event(registry, "rotation_changed", {"enabled": enabled, "scope": "simulator_only"})
        return self.snapshot()

    def set_role(self, role, provider=None, model=None, rules=None):
        if role not in DEFAULT_ROLES or provider is not None and provider not in ACTIVE_PROVIDERS:
            raise StudioError("SCHEMA_INVALID", "Unknown role or provider")
        with self._lock, Registry(self.workspace) as registry, registry.transaction():
            roles = self._settings(registry)["roles"]
            item = roles[role]
            if provider is not None:
                item["provider"] = provider
            item["model"] = model
            item.pop("requires_model_confirmation", None)
            if rules is not None:
                if not isinstance(rules, str) or len(rules) > 12000:
                    raise StudioError("SCHEMA_INVALID", "Rules must be text, at most 12000 characters")
                item["rules"] = rules
            item["rules_version"] += 1
            self._setting(registry, "roles", roles)
            self._event(registry, "role_changed", item)
        return self.snapshot()

    configure_role = set_role

    def tick(self):
        with self._lock:
            if self._closed:
                raise StudioError("COORDINATOR_CLOSED", "Coordinator is closed")
            self._poll()
            self._recover()
            self._flush_profile_reports()
            with Registry(self.workspace) as registry, registry.transaction():
                held = self._apply_selection(registry)
                settings = self._settings(registry)
                pending = (registry.db.execute("SELECT * FROM ai_tasks WHERE status='pending' AND not_before<=? ORDER BY created,id", (self.clock(),)).fetchall()
                           if settings["control"] == "running" else [])
                for row in pending:
                    task = self._task(row)
                    if row["attempts"] >= task["max_attempts"]:
                        registry.db.execute("UPDATE ai_tasks SET status='blocked',error=? WHERE id=?", (canonical_json({"kind": "retry_limit", "message": "Task attempt budget exhausted"}), row["id"]))
                        continue
                    dependencies = [registry.db.execute("SELECT * FROM ai_tasks WHERE id=?", (key,)).fetchone() for key in task["depends_on"]]
                    if any(dep["status"] in {"failed", "blocked", "stale"} for dep in dependencies):
                        registry.db.execute("UPDATE ai_tasks SET status='blocked',error=? WHERE id=?", (canonical_json({"kind": "dependency", "message": "Dependency needs attention"}), row["id"]))
                        continue
                    if any(dep["status"] != "completed" for dep in dependencies):
                        continue
                    scene_id = task["context"].get("scene_id")
                    scene_version = task["context"].get("scene_version")
                    reference = task["input_versions"].get("scene")
                    if isinstance(reference, str) and ":v" in reference:
                        identifier, version = reference.rsplit(":v", 1)
                        scene_id = scene_id or identifier
                        scene_version = scene_version if scene_version is not None else int(version) if version.isdigit() else None
                    latest = registry.db.execute("SELECT MAX(version) FROM ai_scenes WHERE project_id=? AND scene_id=?", (task["project_id"], scene_id)).fetchone()[0] if scene_id else None
                    if latest is not None and scene_version is not None and (scene_version < latest or task["expected_output"] != "scene" and scene_version != latest):
                        registry.db.execute("UPDATE ai_tasks SET status='blocked',error=? WHERE id=?", (canonical_json({"kind": "stale_inputs", "message": "Task does not reference the current scene version"}), row["id"]))
                        continue
                    if task["resource_class"] == "local_heavy" and not getattr(self.adapters.get("local"), "is_local", False):
                        registry.db.execute("UPDATE ai_tasks SET status='waiting_access',error=? WHERE id=?", (canonical_json({"kind": "local_executor_required", "message": "Local work requires a separate local executor"}), row["id"]))
                        continue
                    route = None
                    if task.get('worker_route') is not None:
                        from .v2_routes import WorkerRoute
                        route = WorkerRoute.model_validate(task['worker_route'])
                        if self._route(task) != route:
                            registry.db.execute("UPDATE ai_tasks SET status='waiting_access',error=? WHERE id=?",
                                (canonical_json({'kind': 'unverified_access', 'code': 'WORKER_ROUTE_STALE',
                                    'message': 'Restore the exact host route or enqueue a new task'}), row['id']))
                            continue
                        paused = settings.get('worker_pauses', {}).get(route.reservation)
                        if paused:
                            registry.db.execute("UPDATE ai_tasks SET status='waiting_access',error=? WHERE id=?",
                                (canonical_json(paused), row['id']))
                            continue
                    provider = "local" if task["resource_class"] == "local_heavy" else task["assignment"]["provider"]
                    if provider != "local" and provider not in ACTIVE_PROVIDERS:
                        registry.db.execute("UPDATE ai_tasks SET status='waiting_access',error=? WHERE id=?",
                                            (canonical_json({"kind": "provider_inactive", "message": "This historical provider is inactive; its original task and artifacts are preserved"}), row["id"]))
                        continue
                    if task["assignment"].get("requires_model_confirmation"):
                        registry.db.execute("UPDATE ai_tasks SET status='waiting_access',error=? WHERE id=?",
                                            (canonical_json({"kind": "role_model_confirmation", "message": "Confirm the preserved model for the role's new provider before creating a new task"}), row["id"]))
                        continue
                    if provider in held and route is None:
                        continue
                    adapter = self.adapters.get(provider)
                    is_local = provider == "local" and getattr(adapter, "is_local", False)
                    is_simulated = bool(getattr(adapter, "is_simulated", False))
                    is_live = bool(getattr(adapter, "is_live", False)) and not is_simulated
                    if route and route.synthetic != is_simulated:
                        registry.db.execute("UPDATE ai_tasks SET status='waiting_access',error=? WHERE id=?",
                            (canonical_json({'kind': 'unverified_access', 'code': 'WORKER_ROUTE_MISMATCH',
                                'message': 'Host route provenance differs from adapter'}), row['id']))
                        continue
                    if route and not is_simulated and not is_live:
                        registry.db.execute("UPDATE ai_tasks SET status='waiting_access',error=? WHERE id=?",
                            (canonical_json({'kind': 'unverified_access', 'code': 'CLOUD_EXECUTOR_REQUIRED',
                                'message': 'is_local cannot authorize a routed cloud worker'}), row['id']))
                        continue
                    if not adapter or not (is_simulated or is_local or is_live):
                        registry.db.execute("UPDATE ai_tasks SET status='waiting_access',error=? WHERE id=?", (canonical_json({"kind": "unverified_access", "message": "A verified live executor is not connected"}), row["id"]))
                        continue
                    current_mode = "simulated" if is_simulated else "local" if is_local else "live"
                    original_mode = task.get("execution_mode", "local" if is_local else "simulated")
                    if original_mode != current_mode:
                        registry.db.execute("UPDATE ai_tasks SET status='waiting_access',error=? WHERE id=?", (canonical_json({"kind": "execution_mode_changed", "message": "Task was created for a different execution mode; enqueue a new explicit task"}), row["id"]))
                        continue
                    if is_live and callable(self.result_observer) and self._pending_report(registry, provider, route):
                        # A failed account-state update must not dispatch using
                        # stale entitlement/quota evidence from before that run.
                        continue
                    if is_live and any(json.loads(dep["result"]).get("simulated") for dep in dependencies):
                        registry.db.execute("UPDATE ai_tasks SET status='blocked',error=? WHERE id=?", (canonical_json({"kind": "simulated_dependency", "message": "A live chain requires non-simulated dependency results"}), row["id"]))
                        continue
                    profile_id = (route.profile_id if route else "local-heavy" if is_local else task["requested_profile_id"] if task["kind"] == "access_check"
                                  else settings["selected_profiles"][provider])
                    active = registry.db.execute("SELECT * FROM ai_attempts WHERE status IN ('running','stopping')").fetchall()
                    resource = task["resource_class"]
                    if any(a["profile_id"] == profile_id for a in active):
                        continue
                    if resource == 'cloud':
                        provider_active = [a for a in active if a['provider'] == provider and a['resource_class'] == 'cloud']
                        if route:
                            active_routes = [self._active_route(registry, a) for a in provider_active]
                            if any(r is None or r.reservation == route.reservation or r.worker_id == route.worker_id
                                   for r in active_routes):
                                continue
                        elif provider_active:
                            continue
                    if sum(a["resource_class"] == resource for a in active) >= (3 if resource == "cloud" else 1):
                        continue
                    profile = ({"profile_id": "local-heavy", "provider": "local", "state": "ready", "simulated": False}
                               if is_local else self._profile(registry, provider, profile_id))
                    if route:
                        profile = self._profile(registry, provider, profile_id)
                        if not route.synthetic and profile.get('account_binding') != route.account_binding.model_dump():
                            registry.db.execute("UPDATE ai_tasks SET status='waiting_access',error=? WHERE id=?",
                                (canonical_json({'kind': 'unverified_access', 'code': 'WORKER_ROUTE_STALE',
                                    'message': 'Host account binding is no longer current'}), row['id']))
                            continue
                    access_failure = self._access_failure(profile, task["kind"] == "access_check") if is_live else None
                    if access_failure or not is_live and profile["state"] != "ready":
                        registry.db.execute("UPDATE ai_tasks SET status='waiting_access',error=? WHERE id=?",
                                            (canonical_json(access_failure) if access_failure else None, row["id"]))
                        continue
                    if is_live and profile.get("access_route") == "gemini_api_free":
                        try:
                            approved_model = self._approved_route_model(profile)
                            if task["assignment"].get("model") != approved_model:
                                raise StudioError("ROLE_MODEL_CONFLICT", "Queued model differs from the currently approved Free Tier model; create a new explicit task")
                        except StudioError as exc:
                            registry.db.execute("UPDATE ai_tasks SET status='waiting_access',error=? WHERE id=?",
                                                (canonical_json({"kind": "route_model_conflict", "code": exc.code, "message": str(exc)}), row["id"]))
                            continue
                    context = dict(task["context"])
                    if self.result_gate and hasattr(self.result_gate, 'dependency_context'):
                        try:
                            context.update(self.result_gate.dependency_context(registry, task))
                        except (ValueError, TypeError, KeyError, OSError, StudioError) as exc:
                            failure = {'kind': 'invalid_input', 'code': getattr(exc, 'code', type(exc).__name__),
                                       'message': str(exc), 'retryable': False}
                            registry.db.execute("UPDATE ai_tasks SET status='failed',error=? WHERE id=?",
                                                (canonical_json(failure), row['id']))
                            self._event(registry, 'dependency_rejected', failure, row['id'])
                            continue
                    else:
                        context["dependency_results"] = {dep["id"]: json.loads(dep["result"])["payload"] for dep in dependencies}
                    context.setdefault("project_id", task["project_id"])
                    context.setdefault("input_versions", task["input_versions"])
                    context["provided_evidence_mode"] = "metadata_only"
                    task["context"] = context
                    self._dispatch(registry, task, profile, adapter)
            self._flush_profile_reports()
        return self.snapshot()

    def _dispatch(self, registry, task, profile, adapter):
        from .editorial import demo_result
        attempt_id = "attempt_" + uuid.uuid4().hex
        attempt_dir = workspace_path(self.workspace, f"ai/attempts/{attempt_id}", for_write=True)
        attempt_dir.mkdir(parents=True, exist_ok=False)
        simulated = bool(getattr(adapter, "is_simulated", False))
        task["attempt_id"] = attempt_id
        task["run_id"] = attempt_id
        if task.get('worker_route'):
            task['account_binding'] = task['worker_route']['account_binding']
        if getattr(adapter, "is_live", False) and not simulated:
            task["account_binding"] = json.loads(canonical_json(profile["account_binding"]))
        now = self.clock()
        registry.db.execute("INSERT INTO ai_attempts(id,task_id,number,profile_id,provider,resource_class,status,owner_id,owner_pid,lease_until,input_versions,result_path,created) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                            (attempt_id, task["task_id"], task["attempt"] + 1, profile["profile_id"], profile["provider"], task["resource_class"],
                             "running", self.owner_id, os.getpid(), now + 30, canonical_json(task["input_versions"]),
                             f"ai/attempts/{attempt_id}/completed.json", now))
        registry.db.execute("UPDATE ai_tasks SET status='running',current_attempt=?,attempts=attempts+1,updated=?,error=NULL WHERE id=? AND status='pending'",
                            (attempt_id, now, task["task_id"]))
        try:
            if self.result_gate and hasattr(self.result_gate, 'prepare_context'):
                task['context'].update(self.result_gate.prepare_context(registry, task, attempt_id))
            if simulated and task['expected_output'] in {'scene','edit_plan','review','note'}:
                task["context"].setdefault("fixture_result", demo_result(task["expected_output"], task["context"]))
            # The exact bounded context is durable and independent of clients' chats.
            self._atomic_json(attempt_dir / "request.json", task)
            handle = adapter.start(task, profile, attempt_dir)
            self._runs[attempt_id] = (profile["provider"], handle)
            registry.db.execute("UPDATE ai_attempts SET run_id=? WHERE id=?", (str(handle), attempt_id))
            self._event(registry, "run_started", {"simulated": simulated, "resource_class": task["resource_class"]}, task["task_id"], attempt_id, profile["profile_id"])
        except Exception as exc:
            self._failure(registry, attempt_id, {"kind": "invalid_input" if isinstance(exc, (ValueError, StudioError)) else "unknown", "message": str(exc), "retryable": False})

    def _poll(self):
        for attempt_id, (provider, handle) in list(self._runs.items()):
            try:
                events = self.adapters[provider].poll(handle)
            except Exception as exc:
                if getattr(self.adapters[provider], "is_local", False) or getattr(self.adapters[provider], "is_live", False):
                    with Registry(self.workspace) as registry:
                        self._event(registry, "observation_error", {"message": str(exc), "reservation_retained": True}, attempt_id=attempt_id)
                    continue
                events = [{"type": "failed", "failure": {"kind": "unknown", "message": str(exc), "retryable": False}}]
            with Registry(self.workspace) as registry, registry.transaction():
                attempt = registry.db.execute("SELECT * FROM ai_attempts WHERE id=?", (attempt_id,)).fetchone()
                if not attempt or attempt["status"] not in ACTIVE_ATTEMPTS:
                    self._runs.pop(attempt_id, None)
                    continue
                registry.db.execute("UPDATE ai_attempts SET lease_until=? WHERE id=?", (self.clock() + 30, attempt_id))
                for event in events:
                    kind = event.get("type", event.get("kind", "message"))
                    payload = event.get("payload", {})
                    self._event(registry, kind, payload, attempt["task_id"], attempt_id, attempt["profile_id"])
                    if kind == "completed":
                        result = event.get("result", payload.get("result"))
                        try:
                            self._complete(registry, attempt, result, event.get('host_metadata'))
                        except (ValueError, TypeError, OSError, StudioError) as exc:
                            self._failure(registry, attempt_id, {"kind": "invalid_input", "message": str(exc), "retryable": False})
                        self._runs.pop(attempt_id, None)
                        break
                    if kind in {"failed", "interrupted"}:
                        self._failure(registry, attempt_id, event.get("failure", payload))
                        self._runs.pop(attempt_id, None)
                        break

    def _atomic_json(self, path, payload):
        encoded = (canonical_json(payload) + "\n").encode("utf-8")
        if path.exists():
            if path.read_bytes() != encoded:
                raise StudioError("OUTPUT_EXISTS", "Attempt output is immutable")
            return
        temporary = path.with_suffix(path.suffix + "." + uuid.uuid4().hex + ".tmp")
        with temporary.open("xb") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)

    def _gate_result(self, registry, attempt, payload, host_metadata):
        try:
            row = registry.db.execute('SELECT * FROM ai_tasks WHERE id=?', (attempt['task_id'],)).fetchone()
            task = self._task(row)
            request_path = workspace_path(self.workspace, Path(attempt['result_path']).with_name('request.json').as_posix())
            self.validate_worker_route(registry, task, attempt, json.loads(request_path.read_text(encoding='utf-8')),
                historical=row['status'] == 'completed')
            gated = self.result_gate(registry, row, self._task(row), attempt, payload, host_metadata)
            if not isinstance(gated, dict) or any(not isinstance(gated.get(k), dict) for k in
                    ('payload', 'context', 'host_metadata', 'result_metadata')):
                raise StudioError('RESULT_GATE_REJECTED', 'Result gate returned invalid completion metadata')
            return gated
        except (ValueError, OSError, StudioError):
            raise
        except Exception as exc:
            raise StudioError('RESULT_GATE_REJECTED', f'Result gate failed: {type(exc).__name__}') from exc

    def _complete(self, registry, attempt, payload, host_metadata=None):
        """One completion path; a configured host gate runs before any publication."""
        if self.result_gate:
            with self._completion_savepoint(registry):
                gated = self._gate_result(registry, attempt, payload, host_metadata)
                self._write_completion(registry, attempt, gated['payload'], gated=gated)
                if not self._accept(registry, attempt['id']):
                    raise StudioError('RESULT_NOT_CURRENT', 'Queue acceptance CAS rejected completion')
                return True
        gated = self._gate_result(registry, attempt, payload, host_metadata) if self.result_gate else None
        self._write_completion(registry, attempt, gated['payload'] if gated else payload, gated=gated)
        return self._accept(registry, attempt['id'])

    @contextmanager
    def _completion_savepoint(self, registry):
        # The poll loop catches completion failures inside its outer transaction.
        # Roll back all completion SQL before that loop persists failure status.
        name = 'completion_' + uuid.uuid4().hex
        registry.db.execute('SAVEPOINT ' + name)
        try:
            yield
        except BaseException:
            registry.db.execute('ROLLBACK TO ' + name)
            registry.db.execute('RELEASE ' + name)
            raise
        else:
            registry.db.execute('RELEASE ' + name)

    def _write_completion(self, registry, attempt, result, *, gated=None):
        from .editorial import validate_result
        from .ai_adapters import sanitize
        task_row = registry.db.execute("SELECT * FROM ai_tasks WHERE id=?", (attempt["task_id"],)).fetchone()
        task = self._task(task_row)
        request_path = workspace_path(self.workspace, Path(attempt["result_path"]).with_name("request.json").as_posix())
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if self.result_gate and gated is None:
            raise StudioError('RESULT_GATE_REQUIRED', 'Completion requires the configured host gate')
        if not isinstance(result, dict):
            raise StudioError("SCHEMA_INVALID", "Adapter result must be an object")
        result = sanitize(dict(result))
        if getattr(self.adapters.get(attempt["provider"]), "is_simulated", False):
            result["simulated"] = True
        elif getattr(self.adapters.get(attempt["provider"]), "is_live", False) and result.get("simulated") is not False:
            raise StudioError("SCHEMA_INVALID", "Live results must explicitly declare simulated=false")
        result = validate_result(task["expected_output"], result, gated['context'] if gated else request["context"])
        envelope = {"task_id": attempt["task_id"], "attempt_id": attempt["id"],
                    "input_versions": json.loads(attempt["input_versions"]), "payload": result,
                    "payload_sha256": _hash(result), "simulated": bool(result.get("simulated"))}
        if task.get('worker_route'):
            self.validate_worker_route(registry, task, attempt, request)
            envelope['worker_route'] = task['worker_route']
        if gated:
            envelope['host_metadata'] = gated['host_metadata']
        self._atomic_json(workspace_path(self.workspace, attempt["result_path"], for_write=True), envelope)

    def _accept(self, registry, attempt_id):
        if self.result_gate:
            with self._completion_savepoint(registry):
                if not self._accept_completion(registry, attempt_id):
                    raise StudioError('RESULT_NOT_CURRENT', 'Queue acceptance CAS rejected completion')
                return True
        return self._accept_completion(registry, attempt_id)

    def _accept_completion(self, registry, attempt_id):
        from .editorial import validate_result
        attempt = registry.db.execute("SELECT * FROM ai_attempts WHERE id=?", (attempt_id,)).fetchone()
        task_row = registry.db.execute("SELECT * FROM ai_tasks WHERE id=?", (attempt["task_id"],)).fetchone()
        task = self._task(task_row)
        path = workspace_path(self.workspace, attempt["result_path"])
        envelope = json.loads(path.read_text(encoding="utf-8"))
        required = {'task_id', 'attempt_id', 'input_versions', 'payload', 'payload_sha256', 'simulated'}
        if (not isinstance(envelope, dict) or not required.issubset(envelope)
                or not isinstance(envelope['payload'], dict) or not isinstance(envelope['input_versions'], dict)):
            raise StudioError('ARTIFACT_INVALID', 'Persisted completion must be an object with required envelope fields')
        if self.result_gate and not isinstance(envelope.get('host_metadata'), dict):
            raise StudioError('HOST_ENVELOPE_REQUIRED', 'Persisted v2 completion requires host metadata')
        request = json.loads(path.with_name("request.json").read_text(encoding="utf-8"))
        if not isinstance(request, dict) or not isinstance(request.get('context'), dict):
            raise StudioError('ARTIFACT_INVALID', 'Persisted request must be an object with context')
        self.validate_worker_route(registry, task, attempt, request)
        if envelope.get('worker_route') != task.get('worker_route'):
            raise StudioError('WORKER_ROUTE_MISMATCH', 'Persisted completion route differs from request')
        # Revalidate on recovery too. A persisted completion is never a gate bypass.
        gated = self._gate_result(registry, attempt, envelope.get('payload'), envelope.get('host_metadata')) if self.result_gate else None
        if gated and (gated['payload'] != envelope.get('payload') or gated['host_metadata'] != envelope.get('host_metadata')):
            raise StudioError('ARTIFACT_INVALID', 'Completion differs from normalized host gate output')
        if envelope.get("attempt_id") != attempt_id or envelope.get("task_id") != task["task_id"] or envelope.get("payload_sha256") != _hash(envelope.get("payload")):
            raise StudioError("ARTIFACT_INVALID", "Completed result identity/hash mismatch")
        simulated = envelope.get("simulated")
        payload = envelope.get("payload")
        if type(simulated) is not bool or not isinstance(payload, dict) or payload.get("simulated") is not simulated:
            raise StudioError("ARTIFACT_INVALID", "Completed result has inconsistent simulation provenance")
        if task.get("execution_mode") == "live" and simulated:
            raise StudioError("ARTIFACT_INVALID", "A live attempt cannot accept a simulated artifact")
        validate_result(task["expected_output"], envelope["payload"], gated['context'] if gated else request["context"])
        if task_row["current_attempt"] != attempt_id or task_row["status"] != "running" or envelope["input_versions"] != task["input_versions"]:
            registry.db.execute("UPDATE ai_attempts SET status='discarded',ended=? WHERE id=?", (self.clock(), attempt_id))
            self._event(registry, "late_result_rejected", {"path": attempt["result_path"]}, task["task_id"], attempt_id)
            return False
        if task["expected_output"] == "scene":
            scene = envelope["payload"]
            prior = registry.db.execute("SELECT sha256 FROM ai_scenes WHERE project_id=? AND scene_id=? AND version=?",
                                        (scene["project_id"], scene["scene_id"], scene["version"])).fetchone()
            if prior and prior["sha256"] != _hash(scene):
                raise StudioError("REVISION_CONFLICT", "This scene version already has different content")
        result = {"path": attempt["result_path"], "sha256": content_hash(path), "payload": envelope["payload"], "simulated": envelope["simulated"]}
        if gated:
            metadata = gated['result_metadata']
            if set(metadata) & set(result):
                raise StudioError('ARTIFACT_INVALID', 'Gate metadata cannot replace queue identity')
            result.update(metadata)
        changed = registry.db.execute("UPDATE ai_tasks SET status='completed',result=?,updated=? WHERE id=? AND current_attempt=? AND status='running'",
                                      (canonical_json(result), self.clock(), task["task_id"], attempt_id)).rowcount
        if not changed:
            return False
        registry.db.execute("INSERT OR IGNORE INTO ai_artifacts VALUES(?,?,?,?,?,?,?,?)",
                            ("artifact_" + attempt_id, task["task_id"], attempt_id, task["expected_output"], attempt["result_path"], result["sha256"], attempt["input_versions"], self.clock()))
        registry.db.execute("UPDATE ai_attempts SET status='completed',ended=? WHERE id=?", (self.clock(), attempt_id))
        if task["expected_output"] == "scene":
            scene = envelope["payload"]
            registry.db.execute("INSERT OR IGNORE INTO ai_scenes VALUES(?,?,?,?,?,?,?,?)",
                                (scene["project_id"], scene["scene_id"], scene["version"], canonical_json(scene),
                                 _hash(scene), task_row["pinned"], task["task_id"], self.clock()))
        profile_row = registry.db.execute("SELECT payload FROM ai_profiles WHERE id=?", (attempt["profile_id"],)).fetchone()
        if profile_row:
            profile = json.loads(profile_row[0])
            profile["last_successful_run"] = self.clock()
            registry.db.execute("UPDATE ai_profiles SET payload=? WHERE id=?", (canonical_json(profile), attempt["profile_id"]))
        self._event(registry, "result_accepted", {"sha256": result["sha256"], "simulated": result["simulated"]}, task["task_id"], attempt_id, attempt["profile_id"])
        self._queue_profile_report(registry, attempt, task, result=result)
        return True

    def _failure(self, registry, attempt_id, failure):
        from .ai_adapters import sanitize
        attempt = registry.db.execute("SELECT * FROM ai_attempts WHERE id=?", (attempt_id,)).fetchone()
        row = registry.db.execute("SELECT * FROM ai_tasks WHERE id=?", (attempt["task_id"],)).fetchone()
        task = self._task(row)
        failure = sanitize(dict(failure)) if isinstance(failure, dict) else {"kind": "unknown", "message": "Malformed adapter failure"}
        retry_after = failure.get("retry_after")
        if retry_after is not None and (type(retry_after) not in (float, int) or not math.isfinite(retry_after)):
            failure = {"kind": "invalid_input", "message": "Adapter retry_after must be finite seconds", "retryable": False}
        kind = failure.get("kind", "unknown")
        failure.setdefault("kind", kind)
        registry.db.execute("UPDATE ai_attempts SET status='failed',ended=?,error=? WHERE id=?", (self.clock(), canonical_json(failure), attempt_id))
        if row["current_attempt"] != attempt_id or row["status"] != "running":
            return
        status, delay = "failed", 0
        settings = self._settings(registry)
        if task.get('worker_route') and (kind in {'quota_exhausted', 'authentication', 'permissions', 'unverified_access', 'billing'}):
            from .v2_routes import WorkerRoute
            route = WorkerRoute.model_validate(task['worker_route'])
            pauses = settings.get('worker_pauses', {})
            pauses[route.reservation] = {**failure, 'worker_id': route.worker_id,
                'profile_id': route.profile_id, 'paused_at': self.clock(),
                'resume_after': self.clock()+min(max(float(retry_after or 0), 0), 60)}
            self._setting(registry, 'worker_pauses', pauses)
            status = 'waiting_access'
        elif kind == "quota_exhausted" and attempt["provider"] in PROVIDERS:
            status = "waiting_access"
            profile = json.loads(registry.db.execute("SELECT payload FROM ai_profiles WHERE id=?", (attempt["profile_id"],)).fetchone()[0])
            profile["state"] = "waiting_reset"
            registry.db.execute("UPDATE ai_profiles SET payload=? WHERE id=?", (canonical_json(profile), attempt["profile_id"]))
            adapter = self.adapters.get(attempt["provider"])
            if settings["rotation_on_quota"] and getattr(adapter, "is_simulated", False) and failure.get("quota_confirmed") is True and row["transitions"] < 2:
                number = int(attempt["profile_id"].rsplit("-", 1)[1])
                if number < 3 and row["attempts"] < task["max_attempts"]:
                    selected = dict(settings["selected_profiles"])
                    if settings["selection_mode"] == "group":
                        selected = {p: f"{p}-{number + 1}" for p in ACTIVE_PROVIDERS}
                    else:
                        selected[attempt["provider"]] = f"{attempt['provider']}-{number + 1}"
                    self._setting(registry, "pending_selection", {"mode": settings["selection_mode"], "selected_profiles": selected})
                    registry.db.execute("UPDATE ai_tasks SET transitions=transitions+1 WHERE id=?", (row["id"],))
                    status = "pending"
        elif kind in {"network", "overloaded", "rate_limited"} and failure.get("retryable") and row["attempts"] < task["max_attempts"]:
            status = "pending"
            supplied = failure.get("retry_after")
            if supplied is not None and (type(supplied) not in (float, int) or not math.isfinite(supplied)):
                status = "failed"
                failure = {"kind": "invalid_input", "message": "Adapter retry_after must be finite seconds"}
            else:
                delay = min(max(float(supplied if supplied is not None else 2 ** row["attempts"]), 0), 60)
            self._event(registry, "retry_scheduled", {"delay_seconds": delay, "attempts_remaining": task["max_attempts"] - row["attempts"]}, row["id"], attempt_id)
        elif kind in {"authentication", "billing", "permissions", "unverified_access", "client_missing"}:
            status = "waiting_access"
        elif kind == "interrupted":
            status = "interrupted"
        registry.db.execute("UPDATE ai_tasks SET status=?,current_attempt=NULL,error=?,not_before=?,updated=? WHERE id=?",
                            (status, canonical_json(failure), self.clock() + delay, self.clock(), row["id"]))
        self._queue_profile_report(registry, attempt, task, failure=failure)

    def _reject_recovery(self, registry, attempt, exc):
        failure = {'kind': 'invalid_input', 'code': getattr(exc, 'code', type(exc).__name__),
                   'message': str(exc), 'retryable': False}
        self._failure(registry, attempt['id'], failure)
        self._event(registry, 'recovery_rejected', {**failure, 'path': attempt['result_path'],
                    'evidence_preserved': True}, attempt['task_id'], attempt['id'])

    def _recover(self):
        with self._lock, Registry(self.workspace) as registry, registry.transaction():
            for attempt in registry.db.execute("SELECT * FROM ai_attempts WHERE status IN ('running','stopping')").fetchall():
                if attempt["id"] in self._runs:
                    continue
                path = workspace_path(self.workspace, attempt["result_path"])
                if path.exists():
                    try:
                        self._accept(registry, attempt["id"])
                        continue
                    except (ValueError, TypeError, KeyError, OSError, StudioError) as exc:
                        self._reject_recovery(registry, attempt, exc)
                        continue
                if attempt["owner_id"] in _LIVE:
                    continue
                adapter = self.adapters.get(attempt["provider"])
                if (getattr(adapter, "is_local", False) or getattr(adapter, "is_live", False)) and callable(getattr(adapter, "recover", None)):
                    try:
                        recovered = adapter.recover(path.parent)
                    except Exception as exc:
                        self._event(registry, "recovery_error", {"message": str(exc), "reservation_retained": True}, attempt["task_id"], attempt["id"])
                        continue
                    if not isinstance(recovered, dict):
                        self._reject_recovery(registry, attempt, StudioError('ADAPTER_RECOVERY_INVALID',
                            'Adapter recovery must return an object, got ' + type(recovered).__name__))
                        continue
                    if recovered.get("status") == "completed":
                        try:
                            self._complete(registry, attempt, recovered["result"], recovered.get('host_metadata'))
                        except (ValueError, TypeError, KeyError, OSError, StudioError) as exc:
                            self._reject_recovery(registry, attempt, exc)
                        continue
                    if recovered.get("status") == "interrupted":
                        self._failure(registry, attempt["id"], {"kind": "interrupted", "message": "Managed worker is confirmed stopped without a complete result", "retryable": False})
                        continue
                    if recovered.get("status") == "failed":
                        self._failure(registry, attempt["id"], recovered.get("failure", {"kind": "unknown", "message": "Recovered worker failed"}))
                        continue
                    if recovered.get("status") == "running" and recovered.get("handle") is not None:
                        self._runs[attempt["id"]] = (attempt["provider"], recovered["handle"])
                        if attempt["status"] == "stopping":
                            try:
                                adapter.cancel(recovered["handle"])
                            except Exception as exc:
                                self._event(registry, "cancel_error", {"message": str(exc), "reservation_retained": True}, attempt["task_id"], attempt["id"])
                        continue
                # Fake runs have no external worker. Real process recovery is blocked,
                # never inferred from lease expiry alone.
                if not getattr(self.adapters.get(attempt["provider"]), "is_simulated", False):
                    continue
                self._failure(registry, attempt["id"], {"kind": "interrupted", "message": "Offline simulator owner ended before a complete result", "retryable": False})
                self._event(registry, "recovered_interruption", {}, attempt["task_id"], attempt["id"])

    def invalidate_scene(self, project_id, scene_id, version, force=False):
        """Preserve old artifacts; invalidate only this scene and its dependants."""
        affected = set()
        with self._lock, Registry(self.workspace) as registry, registry.transaction():
            rows = registry.db.execute("SELECT * FROM ai_tasks WHERE project_id=?", (project_id,)).fetchall()
            scene = registry.db.execute("SELECT pinned FROM ai_scenes WHERE project_id=? AND scene_id=? ORDER BY version DESC LIMIT 1", (project_id, scene_id)).fetchone()
            if scene and scene["pinned"] and not force:
                raise StudioError("PINNED_CONFLICT", "Unpin this scene explicitly before invalidating it")
            for row in rows:
                task = self._task(row)
                scene = str(task["input_versions"].get("scene", ""))
                if task["context"].get("scene_id") == scene_id or scene == scene_id or scene.startswith(scene_id + ":"):
                    affected.add(row["id"])
            while True:
                more = {row["id"] for row in rows if set(json.loads(row["payload"])["depends_on"]) & affected}
                if more <= affected:
                    break
                affected |= more
            if not force and any(row["pinned"] and row["id"] in affected for row in rows):
                raise StudioError("PINNED_CONFLICT", "Change affects a pinned scene or dependent result; resolve explicitly")
            for identifier in affected:
                registry.db.execute("UPDATE ai_tasks SET status='stale',current_attempt=NULL,updated=? WHERE id=?", (self.clock(), identifier))
                registry.db.execute("UPDATE ai_attempts SET status='stopping' WHERE task_id=? AND status='running'", (identifier,))
            self._event(registry, "scene_invalidated", {"project_id": project_id, "scene_id": scene_id, "new_version": version, "tasks": sorted(affected)})
        self._cancel_running()
        return {"scene_id": scene_id, "version": version, "invalidated_tasks": sorted(affected), "artifacts_preserved": True}

    def update_scene(self, project_id, scene_id, text, expected_version, pinned=None):
        """CAS edit creates a new scene version and invalidates only dependants.

        Pinned text changes require an explicit unpin (pinned=False). Previous
        scene payloads and all accepted attempt files remain immutable.
        """
        from .editorial import validate_result
        if not isinstance(text, str) or not text.strip() or len(text) > 3000:
            raise StudioError("SCHEMA_INVALID", "Scene text must contain 1–3000 characters")
        with self._lock, Registry(self.workspace) as registry, registry.transaction():
            row = registry.db.execute("SELECT * FROM ai_scenes WHERE project_id=? AND scene_id=? ORDER BY version DESC LIMIT 1", (project_id, scene_id)).fetchone()
            if row is None:
                raise StudioError("SCENE_NOT_FOUND", "Complete or import a scene before editing")
            if row["version"] != expected_version:
                raise StudioError("REVISION_CONFLICT", f"Current scene version is {row['version']}")
            old = json.loads(row["payload"])
            old_text = "\n".join(part["text"] for part in old["narration_segments"])
            is_pinned = bool(row["pinned"]) if pinned is None else bool(pinned)
            if text != old_text and row["pinned"] and pinned is not False:
                raise StudioError("PINNED_CONFLICT", "Unpin this scene explicitly before editing")
            tasks = registry.db.execute("SELECT * FROM ai_tasks WHERE project_id=?", (project_id,)).fetchall()
            affected = {item["id"] for item in tasks if json.loads(item["payload"])["context"].get("scene_id") == scene_id
                        or str(json.loads(item["payload"])["input_versions"].get("scene", "")).startswith(scene_id + ":")}
            while True:
                more = {item["id"] for item in tasks if set(json.loads(item["payload"])["depends_on"]) & affected}
                if more <= affected:
                    break
                affected |= more
            if text != old_text and any(item["pinned"] and item["id"] in affected for item in tasks) and pinned is not False:
                raise StudioError("PINNED_CONFLICT", "This edit affects a pinned dependent result")
            if text == old_text:
                registry.db.execute("UPDATE ai_scenes SET pinned=? WHERE project_id=? AND scene_id=? AND version=?", (int(is_pinned), project_id, scene_id, expected_version))
                return {"project_id": project_id, "scene_id": scene_id, "version": expected_version, "pinned": is_pinned, "invalidated_tasks": []}
            updated = dict(old)
            updated["version"] = expected_version + 1
            updated["narration_segments"] = [{"segment_id": old["narration_segments"][0]["segment_id"], "text": text}]
            updated = validate_result("scene", updated, {"project_id": project_id, "scene_id": scene_id})
            registry.db.execute("INSERT INTO ai_scenes VALUES(?,?,?,?,?,?,?,?)", (project_id, scene_id, updated["version"], canonical_json(updated), _hash(updated), int(is_pinned), None, self.clock()))
            for identifier in affected:
                registry.db.execute("UPDATE ai_tasks SET status='stale',current_attempt=NULL,updated=? WHERE id=?", (self.clock(), identifier))
                registry.db.execute("UPDATE ai_attempts SET status='stopping' WHERE task_id=? AND status='running'", (identifier,))
            self._event(registry, "scene_updated", {"project_id": project_id, "scene_id": scene_id, "version": updated["version"], "invalidated_tasks": sorted(affected)})
        self._cancel_running()
        return {"project_id": project_id, "scene_id": scene_id, "version": updated["version"], "pinned": is_pinned,
                "invalidated_tasks": sorted(affected), "artifacts_preserved": True}

    def pin(self, task_id, pinned=True):
        with self._lock, Registry(self.workspace) as registry, registry.transaction():
            if not registry.db.execute("UPDATE ai_tasks SET pinned=? WHERE id=?", (int(bool(pinned)), task_id)).rowcount:
                raise StudioError("TASK_NOT_FOUND", "Unknown task")
            self._event(registry, "pin_changed", {"pinned": bool(pinned)}, task_id)
        return self.snapshot()

    def demo(self):
        if any(not getattr(self.adapters.get(p), "is_simulated", False) for p in ACTIVE_PROVIDERS):
            raise StudioError("SIMULATOR_REQUIRED", "The fixture demo requires simulated active providers")
        prefix = "demo_" + uuid.uuid4().hex[:10]
        tasks = []
        for role, output in (("writer", "scene"), ("director", "edit_plan"), ("critic", "review")):
            task = {"task_id": f"{prefix}_{role}", "project_id": prefix, "kind": "offline_" + role,
                    "role": role, "expected_output": output, "input_versions": {"scene": "scene_001:v1", "voice": "voice:v1"},
                    "context": {"scene_id": "scene_001", "scene_version": 1, "voice_version": "voice:v1"},
                    "depends_on": [item["task_id"] for item in tasks]}
            tasks.append(self.enqueue(task))
        return {"project_id": prefix, "task_ids": [task["task_id"] for task in tasks], "simulated": True, "quality_status": "not_measured"}

    def enqueue_access_probe(self, profile_id):
        """One explicitly requested diagnostic, including a reserved profile.

        It shares provider/total capacity with creative work and never switches
        the selected account or retries the request automatically.
        """
        provider = next((p for p in ACTIVE_PROVIDERS if profile_id in {f"{p}-{n}" for n in PROFILE_NUMBERS}), None)
        if provider is None:
            raise StudioError("SCHEMA_INVALID", "Unknown profile slot")
        if not getattr(self.adapters.get(provider), "is_live", False):
            raise StudioError("LIVE_EXECUTOR_REQUIRED", "An access check requires an official live executor")
        with self._lock:
            with Registry(self.workspace) as registry:
                probe_model = self._approved_route_model(self._profile(registry, provider, profile_id))
                # HTTP retries, double clicks and confirm→check must share the
                # same bounded request until its process is confirmed terminal.
                for row in registry.db.execute("SELECT * FROM ai_tasks WHERE status IN ('pending','running','waiting_access','interrupted') ORDER BY created DESC,id"):
                    task = self._task(row)
                    if (task.get("kind") != "access_check" or task.get("requested_profile_id") != profile_id
                            or task.get("execution_mode") != "live" or task.get("probe_provider") != provider
                            or task.get("assignment", {}).get("model") != probe_model):
                        continue
                    active = registry.db.execute("SELECT 1 FROM ai_attempts WHERE task_id=? AND status IN ('running','stopping') LIMIT 1", (row["id"],)).fetchone()
                    if active or (row["status"] in {"pending", "waiting_access"} and row["attempts"] == 0):
                        return task
            return self.enqueue({"project_id": "access_checks", "role": next((r for r, p in DEFAULT_ROLES.items() if p == provider), "writer"),
                                 "kind": "access_check", "probe_provider": provider, "requested_profile_id": profile_id,
                                 "expected_output": "note", "max_attempts": 1,
                                 "context": {"prompt": "Return a JSON note with text exactly OK and simulated=false. No tools, files, searches or account claims.",
                                             "purpose": "explicit_short_access_check"}})

    def live_chain(self, brief="Короткая вымышленная сцена: путник ищет безопасный путь через реку.", mode="auto"):
        """Bounded GPT scene/plan, review and optional GPT feedback response.

        These explicit checks use temporary assignments. The editable role
        configuration and all its model choices remain unchanged.
        """
        if not isinstance(brief, str) or not 1 <= len(brief.strip()) <= 1200:
            raise StudioError("SCHEMA_INVALID", "The short scene brief must contain 1–1200 characters")
        if mode not in {"auto", "gpt_only", "gemini"}:
            raise StudioError("SCHEMA_INVALID", "mode must be auto, gpt_only or gemini")
        with self._lock, Registry(self.workspace) as registry:
            settings = self._settings(registry)
            requested_mode = mode
            gemini = self.adapters.get("gemini")
            gemini_profile = self._profile(registry, "gemini", settings["selected_profiles"]["gemini"])
            gemini_failure = (self._access_failure(gemini_profile)
                              if getattr(gemini, "is_live", False) and not getattr(gemini, "is_simulated", False)
                              else {"kind": "live_executor_required", "message": "Gemini live executor is unavailable"})
            if mode == "auto":
                mode = "gpt_only" if gemini_failure else "gemini"
            if mode == "gemini" and gemini_failure:
                raise StudioError("LIVE_ACCESS_REQUIRED", f"gemini: {gemini_failure['message']}")
            assignments = {}
            for role, provider in {"writer": "codex", "director": "codex",
                                   "critic": "gemini" if mode == "gemini" else "codex"}.items():
                original = settings["roles"][role]
                if original.get("requires_model_confirmation"):
                    raise StudioError("ROLE_MODEL_CONFIRMATION", "Confirm the preserved model for the new provider before running the chain")
                item = {**original, "provider": provider}
                if original["provider"] != provider:
                    # A Gemini/Claude model name cannot silently become a Codex
                    # model name. Borrow only an explicitly configured Codex
                    # role model, without changing the persistent role mapping.
                    same_provider = next((settings["roles"][r] for r in ("director", "writer", "critic")
                                          if settings["roles"][r]["provider"] == provider
                                          and not settings["roles"][r].get("requires_model_confirmation")), None)
                    item["model"] = same_provider.get("model") if same_provider else None
                if provider == "gemini":
                    approved_model = self._approved_route_model(gemini_profile)
                    if approved_model is not None:
                        if original.get("model") is not None and original["model"] != approved_model:
                            raise StudioError("ROLE_MODEL_CONFLICT", "Configured role model differs from the explicitly approved Free Tier model")
                        item["model"] = approved_model
                assignments[role] = item
                adapter = self.adapters.get(provider)
                if not getattr(adapter, "is_live", False) or getattr(adapter, "is_simulated", False):
                    raise StudioError("LIVE_EXECUTOR_REQUIRED", "Every role in the live chain needs a live executor")
                failure = self._access_failure(self._profile(registry, provider, settings["selected_profiles"][provider]))
                if failure:
                    raise StudioError("LIVE_ACCESS_REQUIRED", f"{provider}: {failure['message']}")
        prefix = "live_" + uuid.uuid4().hex[:10]
        context = {"scene_id": "scene_001", "scene_version": 1, "voice_version": "not_recorded:v1",
                   "brief": brief.strip(), "purpose": "short_text_chain_check",
                   "chain_mode": mode, "review_mode": "gpt_self_review" if mode == "gpt_only" else "gemini_critic",
                   "asset_catalog": [{"asset_id": "planning_card", "duration_ms": 3000,
                                      "description": "Условная карточка для текстового плана; медиафайл отсутствует.",
                                      "evidence_mode": "metadata_only"}],
                   "constraints": "No file access or tools. Fictional text only. One narration segment, at most 30 words. "
                                  "Plan exactly 3000 ms with one video placement planning_card from 0 to 3000; no voice/audio/subtitles/transitions. "
                                  "voice_version is not_recorded:v1; no recording exists. Metadata evidence only. "
                                  "Critic must consider both supplied scene and edit plan, at most two issues."}
        if mode == "gpt_only":
            context["constraints"] += " This is GPT-only text self-review, not an independent Gemini review. Label the review accordingly."
        tasks = []
        for role, output in (("writer", "scene"), ("director", "edit_plan"), ("critic", "review")):
            tasks.append(self.enqueue({"task_id": f"{prefix}_{role}", "project_id": prefix, "kind": "live_" + role,
                                       "role": role, "expected_output": output, "max_attempts": 1,
                                       "input_versions": {"scene": "scene_001:v1", "voice": "not_recorded:v1"},
                                       "context": context, "depends_on": [item["task_id"] for item in tasks]},
                                      _assignment=assignments[role]))
        if mode == "gemini":
            tasks.append(self.enqueue({"task_id": f"{prefix}_feedback", "project_id": prefix, "kind": "live_feedback",
                                       "role": "director", "expected_output": "note", "max_attempts": 1,
                                       "input_versions": {"scene": "scene_001:v1", "voice": "not_recorded:v1"},
                                       "context": {**context, "purpose": "respond_to_gemini_feedback",
                                                   "prompt": "Read the supplied Gemini critique and respond in at most 60 words: what to change, or why no change is required. This is a text planning check; no media exists."},
                                       "depends_on": [item["task_id"] for item in tasks]},
                                      _assignment=assignments["director"]))
        return {"project_id": prefix, "task_ids": [task["task_id"] for task in tasks], "simulated": False,
                "quality_status": "not_measured", "scope": "text_planning_only", "mode": mode,
                "requested_mode": requested_mode, "review_mode": context["review_mode"],
                "fallback_reason": gemini_failure if requested_mode == "auto" and mode == "gpt_only" else None,
                "assignments": assignments}

    def close(self):
        """Release observation without pretending an external process stopped."""
        with self._lock:
            if self._closed:
                return
            self._closed = True
            _LIVE.pop(self.owner_id, None)
            self._runs.clear()
            self._workspace_lock.__exit__(None, None, None)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()
