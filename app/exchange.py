"""Bounded, compact exchange over the existing persistent job API.

This client does not own a queue. A timed-out or interrupted wait leaves the
worker and its saved output alone; repeating a chain reuses its exact receipts.
Short responses are at most 4000 JSON characters (ensure_ascii=False); counts
and truncated disclose omitted details, which remain in the full saved log.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
from pathlib import Path
import sys
import time
import traceback
from typing import Callable
import uuid

from . import __version__
from .jobs import JobManager
from .registry import Registry, StudioError, canonical_json, workspace_path, write_generated_json


STAGES = ("synthesize", "build", "render", "qa", "package")
ACTIVE_STATES = {"pending", "running"}
ARTIFACT_FIELDS = ("manifest", "project_mlt", "timeline_json", "video", "report",
                   "archive", "package_directory", "project", "subtitles",
                   "asr_report", "raw_subtitles", "frames")
SHORT_LIMIT = 4000
REPORT_LIMIT = 2_000_000
# Deliberate source allowlist: no Git command, directory walk, archives or models.
CODE_FILES = ("app/__init__.py", "app/contracts.py", "app/registry.py", "app/jobs.py",
              "app/worker.py", "app/operations.py", "app/exchange.py", "app/production_profile.py",
              "app/timeline.py", "app/media.py", "app/tts.py", "app/speech_review.py",
              "scripts/render_project.py", "scripts/synthesize_script.py",
              "scripts/transcribe_audio.py", "scripts/speech_model_identity.py")


def code_revision(root: Path | None = None) -> str:
    """Fingerprint the local source bytes, including uncommitted changes."""
    source = root or Path(__file__).resolve().parents[1]
    digest = hashlib.sha256()
    for relative in CODE_FILES:
        path = source / relative
        digest.update(relative.encode("utf-8") + b"\0")
        digest.update(hashlib.sha256(path.read_bytes()).digest() if path.is_file() else b"MISSING")
    return digest.hexdigest()


def compact(response: dict) -> dict:
    """Keep one visible warning/error when reducing arrays; never hide their counts."""
    def size():
        return len(json.dumps(response, ensure_ascii=False, allow_nan=False))

    for field, minimum in (("artifacts", 0), ("warnings", 1), ("errors", 1)):
        while size() > SHORT_LIMIT and len(response[field]) > minimum:
            response[field].pop()
            response["truncated"] = True
    if size() > SHORT_LIMIT:
        response["truncated"] = True
        response["artifacts"] = []
        for field in ("warnings", "errors"):
            response[field] = [{"code": str(item.get("code", "DETAIL_IN_LOG"))[:80],
                                "message": str(item.get("message", item))[:160]}
                               if isinstance(item, dict) else str(item)[:160]
                              for item in response[field][:1]]
        for key, value in response.items():
            if isinstance(value, str) and len(value) > 160:
                response[key] = value[:157] + "..."
        for key, value in response["metrics"].items():
            if isinstance(value, str) and len(value) > 160:
                response["metrics"][key] = value[:157] + "..."
        cache = response["metrics"].get("speech_cache")
        if cache is not None:
            response["metrics"]["speech_cache"] = {"hit": cache.get("hit") is True} if isinstance(cache, dict) else None
    return response


def chain_key(key: str, stage: str) -> str:
    """Bounded keys share the existing registry's project-scoped namespace."""
    return "exchange-chain:" + hashlib.sha256(key.encode("utf-8")).hexdigest() + ":" + stage


class Exchange:
    def __init__(self, workspace: str | Path, *, project_root: str | Path | None = None):
        self.root = Path(workspace).resolve()
        self.project_root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[1]

    @staticmethod
    def _limits(timeout: float, poll_interval: float) -> None:
        if not math.isfinite(timeout) or not 0 <= timeout <= 3600:
            raise StudioError("SCHEMA_INVALID", "timeout must be finite and between 0 and 3600 seconds")
        if not math.isfinite(poll_interval) or not .01 <= poll_interval <= 10:
            raise StudioError("SCHEMA_INVALID", "poll_interval must be between 0.01 and 10 seconds")

    def _wait(self, manager: JobManager, job_id: str, deadline: float,
              poll_interval: float, cancelled: Callable[[], bool] | None) -> tuple[dict, str]:
        job = manager.status(job_id)
        try:
            while job["state"] in ACTIVE_STATES:
                if cancelled and cancelled():
                    return job, "cancelled"
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    return job, "timeout"
                time.sleep(min(poll_interval, remaining))
                job = manager.status(job_id)
        except KeyboardInterrupt:
            return manager.status(job_id), "cancelled"
        return job, "completed"

    @staticmethod
    def _quality(job: dict | None) -> str:
        if not job:
            return "not_checked"
        result = job.get("result") or {}
        return result.get("status", "not_checked") if isinstance(result, dict) else "not_checked"

    def _report(self, relative: str, digest: str | None = None) -> dict:
        path = workspace_path(self.root, relative)
        with path.open("rb") as stream:
            encoded = stream.read(REPORT_LIMIT + 1)
        if len(encoded) > REPORT_LIMIT:
            raise StudioError("REPORT_LIMIT", f"Report exceeds {REPORT_LIMIT} bytes")
        if digest and hashlib.sha256(encoded).hexdigest() != digest:
            raise StudioError("ASSET_CHANGED", "Saved report no longer matches its recorded SHA-256")
        report = json.loads(encoded.decode("utf-8-sig"))
        if not isinstance(report, dict):
            raise StudioError("SCHEMA_INVALID", "Saved report must be a JSON object")
        return report

    def _diagnostics(self, job: dict) -> dict:
        result = job.get("result") or {}
        quality = self._quality(job)
        warnings = list(result.get("warnings", [])) if isinstance(result.get("warnings", []), list) else [result["warnings"]]
        errors = [job["error"]] if job.get("error") else []
        reports = {}
        if job["state"] == "done" and quality == "blocked":
            errors.append({"code": "QA_BLOCKED", "message": "Saved quality checks are blocked; inspect the report before continuing"})

        def notice(code, message, **extra):
            return {"code": code, "message": message, "job_id": job["job_id"], **extra}

        def read(kind, relative, digest=None):
            nonlocal quality
            try:
                if not relative:
                    raise StudioError("REPORT_MISSING", "Completed QA job has no saved report reference")
                report = self._report(relative, digest)
                fields = ("checks", "limitations") if kind == "qa" else ("segments", "warnings")
                if any(key in report and not isinstance(report[key], list) for key in fields):
                    raise StudioError("SCHEMA_INVALID", "Saved report contains a non-list diagnostic field")
                if kind == "qa" and report.get("status") not in ("ready_for_review", "blocked", "not_checked"):
                    raise StudioError("SCHEMA_INVALID", "Saved QA report has an unknown quality status")
                if kind == "speech" and any(isinstance(segment, dict) and
                        any(key in segment and not isinstance(segment[key], list) for key in ("warnings", "missing_critical_terms"))
                        for segment in report.get("segments", [])):
                    raise StudioError("SCHEMA_INVALID", "Saved speech segment contains a non-list diagnostic field")
                reports[kind] = {"path": relative, "content": report}
                return report
            except (OSError, ValueError, TypeError, StudioError) as exc:
                errors.append(notice("REPORT_UNAVAILABLE", str(exc), report=relative, cause=getattr(exc, "code", type(exc).__name__)))
                if quality != "blocked":
                    quality = "not_checked"
                return None

        if result.get("report") or job["operation"] == "qa" and job["state"] == "done":
            report = read("qa", result.get("report"), result.get("report_sha256"))
            if report is not None:
                quality = report.get("status", "not_checked")
                if quality == "blocked" and not any(item.get("code") == "QA_BLOCKED" for item in errors):
                    errors.append(notice("QA_BLOCKED", "Saved QA report is blocked"))
                for check in report.get("checks", []):
                    if not isinstance(check, dict) or not isinstance(check.get("status"), str):
                        warnings.append(notice("REPORT_CHECK_INVALID", "Saved QA check is not an object"))
                        continue
                    status, severity = check.get("status"), check.get("severity")
                    item = notice("QA_CHECK", check.get("evidence", ""), name=check.get("name"), status=status, severity=severity)
                    if status == "failed" and severity == "error":
                        errors.append(item)
                    elif status == "not_checked" or status in {"failed", "warning"}:
                        warnings.append(item)
                for limitation in report.get("limitations", []):
                    warnings.append(notice("QA_LIMITATION", str(limitation)))
        cache = result.get("speech_cache")
        if isinstance(cache, dict) and cache.get("hit"):
            warnings.append(notice("SPEECH_CACHE_REUSED", "Saved speech analysis was reused; cached warnings and human-review requirements still apply"))
        if result.get("asr_report"):
            relative = result["asr_report"]
            speech = read("speech", relative, result.get("artifact_hashes", {}).get(relative))
            if speech is not None:
                if speech.get("status") != "automated_pass":
                    warnings.append(notice("SPEECH_REVIEW", "Saved speech analysis needs review", status=speech.get("status", "not_checked")))
                for item in speech.get("warnings", []):
                    warnings.append(notice("SPEECH_WARNING", str(item)))
                for segment in speech.get("segments", []):
                    if not isinstance(segment, dict):
                        warnings.append(notice("SPEECH_SEGMENT_INVALID", "Saved speech segment is not an object"))
                        continue
                    for warning in segment.get("warnings", []):
                        warnings.append(notice("SPEECH_WARNING", str(warning), block_id=segment.get("block_id")))
                    if segment.get("missing_critical_terms"):
                        warnings.append(notice("SPEECH_CRITICAL_TERMS", "Critical terms need review", block_id=segment.get("block_id"), terms=segment["missing_critical_terms"]))
        return {"quality_status": quality, "warnings": warnings, "errors": errors, "reports": reports}

    def _finish(self, request: dict, started: float, *, job: dict | None = None,
                wait_status: str = "not_requested", repeated: bool = False,
                errors: list | None = None, stages: list | None = None,
                exception_trace: str | None = None) -> dict:
        operation_id = "exchange_" + uuid.uuid4().hex
        result = (job or {}).get("result") or {}
        if not isinstance(result, dict):
            result = {}
        all_errors = list(errors or [])
        all_warnings = []
        diagnostics = []
        candidates = [(stage["job"], stage.get("diagnostics")) for stage in stages or []]
        if job and job["job_id"] not in {candidate[0]["job_id"] for candidate in candidates}:
            candidates.append((job, None))
        if job and job["operation"] == "package" and not any(candidate[0]["operation"] == "qa" for candidate in candidates):
            try:
                parent = JobManager(self.root).status(job["parameters"]["qa_job_id"])
                if parent["project_id"] != job["project_id"] or parent["operation"] != "qa":
                    raise StudioError("DEPENDENCY_INVALID", "Package QA dependency does not match this project")
                candidates.insert(0, (parent, None))
            except (StudioError, KeyError) as exc:
                all_errors.append({"code": "QA_DEPENDENCY_UNAVAILABLE", "message": str(exc)})
        quality = self._quality(job)
        for candidate, saved in candidates:
            detail = saved if saved is not None else self._diagnostics(candidate)
            diagnostics.append({"job_id": candidate["job_id"], "operation": candidate["operation"], **detail})
            all_warnings.extend(detail["warnings"])
            all_errors.extend(detail["errors"])
            if candidate["operation"] == "qa":
                quality = detail["quality_status"]
        try:
            revision = code_revision()
        except OSError as exc:
            revision = None
            all_warnings.append({"code": "CODE_REVISION_UNAVAILABLE", "message": str(exc)})
        all_artifacts = []
        for name in ARTIFACT_FIELDS:
            values = result.get(name)
            for value in values if isinstance(values, list) else [values]:
                if isinstance(value, str) and value:
                    all_artifacts.append({"kind": name, "path": value})
        truncated = False

        def bounded(value, *, depth=0):
            nonlocal truncated
            if isinstance(value, str) and len(value) > 500:
                truncated = True
                return value[:497] + "..."
            if isinstance(value, list):
                if len(value) > 10:
                    truncated = True
                return [bounded(item, depth=depth + 1) for item in value[:10]]
            if isinstance(value, dict):
                if depth > 4:
                    truncated = True
                    return "See full log"
                keys = list(value)
                if len(keys) > 20:
                    truncated = True
                return {key: bounded(value[key], depth=depth + 1) for key in keys[:20]}
            return value

        job_duration = None
        if job and job.get("started") is not None and job.get("finished") is not None:
            job_duration = max(0, job["finished"] - job["started"])
        metrics = {"operation_id": operation_id, "operation": request["action"],
                   "version": __version__, "code_revision": revision, "task_id": request.get("task_id"),
                   "job_id": job.get("job_id") if job else request.get("job_id"),
                   "elapsed_seconds": round(time.monotonic() - started, 6),
                   "job_elapsed_seconds": job_duration, "repeat": repeated,
                   "attempts": job.get("attempts") if job else None,
                   "cache_hits": result.get("cache_hits"),
                   "speech_cache": result.get("speech_cache"),
                   "repair_reason": request.get("repair_reason"), "model": None,
                   "tokens": {name: None for name in ("input", "cached_input", "output", "reasoning", "total")}}
        state = job["state"] if job else "not_started" if not all_errors and wait_status in {"timeout", "cancelled"} else "failed"
        if all_errors or state in {"failed", "blocked", "stale"}:
            next_action = "inspect_error_and_saved_log"
        elif wait_status in {"timeout", "cancelled"}:
            next_action = "repeat_same_chain" if request["action"] == "chain" else "wait_for_same_job"
        elif state in ACTIVE_STATES:
            next_action = "wait_for_same_job"
        else:
            next_action = "review_saved_artifacts"
        log_ref = "exchange_logs/" + operation_id + ".json"
        response = {"task_id": request.get("task_id"), "job_id": metrics["job_id"],
                    "state": state, "quality_status": bounded(quality),
                    "artifacts": bounded(all_artifacts), "warnings": bounded(all_warnings),
                    "errors": bounded(all_errors), "log_ref": log_ref,
                    "next_action": next_action, "wait_status": wait_status,
                    "metrics": bounded(metrics)}
        response["truncated"] = truncated
        response["counts"] = {"errors": len(all_errors), "warnings": len(all_warnings), "artifacts": len(all_artifacts)}
        response = compact(response)
        full_log = {"request": request, "job": job, "stages": stages or [],
                    "diagnostics": diagnostics, "code_revision_files": list(CODE_FILES),
                    "metrics": metrics, "wait_status": wait_status,
                    "errors": all_errors, "warnings": all_warnings,
                    "artifacts": all_artifacts, "exception_trace": exception_trace,
                    "response": response}
        try:
            write_generated_json(self.root, log_ref, full_log)
        except (OSError, StudioError, ValueError) as exc:
            response["log_ref"] = None
            response["errors"].append({"code": "LOG_WRITE_FAILED", "message": str(exc)[:500]})
            response["counts"]["errors"] += 1
            response["next_action"] = "fix_log_storage"
        return compact(response)

    def job(self, job_id: str, *, task_id: str | None = None) -> dict:
        started = time.monotonic()
        request = {"action": "job", "job_id": job_id, "task_id": task_id}
        try:
            job = JobManager(self.root).status(job_id)
            return self._finish(request, started, job=job)
        except Exception as exc:
            return self._finish(request, started, errors=[self._error(exc)], exception_trace=traceback.format_exc())

    @staticmethod
    def _error(exc: Exception) -> dict:
        return {"code": getattr(exc, "code", "EXCHANGE_FAILED"), "message": str(exc)}

    def wait(self, job_id: str, *, timeout: float = 60, poll_interval: float = .25,
             task_id: str | None = None, cancel_wait: Callable[[], bool] | None = None) -> dict:
        started = time.monotonic()
        request = {"action": "wait", "job_id": job_id, "task_id": task_id,
                   "timeout": timeout, "poll_interval": poll_interval}
        try:
            self._limits(timeout, poll_interval)
            job, outcome = self._wait(JobManager(self.root), job_id, started + timeout,
                                      poll_interval, cancel_wait)
            return self._finish(request, started, job=job, wait_status=outcome)
        except Exception as exc:
            # Non-finite user input must not prevent writing the error record.
            request = {key: str(value) if isinstance(value, float) and not math.isfinite(value) else value
                       for key, value in request.items()}
            return self._finish(request, started, errors=[self._error(exc)], exception_trace=traceback.format_exc())

    def _profile_check(self) -> None:
        from .production_profile import load_profile, check_runtime
        profile = load_profile(self.project_root)
        check_runtime(profile, self.project_root, self.root)

    def _accepted(self, project_id: str, key: str, operation: str,
                  parameters: dict, revision: int) -> dict | None:
        """Check the original request hash without spawning on a replay."""
        request = {"op": "enqueue", "operation": operation, "parameters": parameters,
                   "base_revision": revision}
        digest = hashlib.sha256(canonical_json(request).encode("utf-8")).hexdigest()
        with Registry(self.root) as registry:
            row = registry.db.execute("SELECT request_hash,response FROM requests WHERE project_id=? AND key=?",
                                      (project_id, key)).fetchone()
        if not row:
            return None
        if digest != row["request_hash"]:
            raise StudioError("IDEMPOTENCY_CONFLICT", "This chain key was already used for different input or revision")
        return json.loads(row["response"])

    def chain(self, project_id: str, script_asset_id: str, base_revision: int, key: str, *,
              task_id: str | None = None, mode: str = "production", timeout: float = 60,
              poll_interval: float = .25, repair_reason: str | None = None,
              cancel_wait: Callable[[], bool] | None = None) -> dict:
        started = time.monotonic()
        request = {"action": "chain", "project_id": project_id, "script_asset_id": script_asset_id,
                   "base_revision": base_revision, "key": key, "task_id": task_id,
                   "mode": mode, "timeout": timeout, "poll_interval": poll_interval,
                   "repair_reason": repair_reason}
        job = None
        stages = []
        repeated = False
        outcome = "not_requested"
        try:
            self._limits(timeout, poll_interval)
            if mode not in {"production", "development"}:
                raise StudioError("SCHEMA_INVALID", "mode must be production or development")
            if mode == "development" and (not isinstance(task_id, str) or not task_id.strip()):
                raise StudioError("TASK_REQUIRED", "Development chain requires an explicit task_id")
            if not isinstance(key, str) or not key or len(key) > 128:
                raise StudioError("SCHEMA_INVALID", "An idempotency key of 1–128 characters is required")
            if type(base_revision) is not int or base_revision < 1:
                raise StudioError("SCHEMA_INVALID", "base_revision must be a positive integer")
            manager = JobManager(self.root)
            for index, operation in enumerate(STAGES):
                stage_started = time.monotonic()
                parameters = ({"script_asset_id": script_asset_id} if index == 0 else
                              {"script_asset_id": script_asset_id, "audio_job_id": job["job_id"]} if index == 1 else
                              {("build_job_id", "render_job_id", "qa_job_id")[index - 2]: job["job_id"]})
                stage_key = chain_key(key, operation)
                revision = base_revision + index
                receipt = self._accepted(project_id, stage_key, operation, parameters, revision)
                was_repeated = receipt is not None
                repeated = repeated or was_repeated
                if receipt is None:
                    if cancel_wait and cancel_wait():
                        outcome = "cancelled"
                        break
                    if time.monotonic() >= started + timeout:
                        outcome = "timeout"
                        break
                    if mode == "production":
                        self._profile_check()
                    receipt = manager.enqueue(project_id, operation, parameters, revision, stage_key)
                job = manager.status(receipt["job_id"])
                job, outcome = self._wait(manager, job["job_id"], started + timeout, poll_interval, cancel_wait)
                diagnostics = self._diagnostics(job)
                stages.append({"operation": operation, "receipt": receipt, "repeat": was_repeated,
                               "job": job, "wait_status": outcome, "diagnostics": diagnostics,
                               "elapsed_seconds": round(time.monotonic() - stage_started, 6),
                               "cache_hits": (job.get("result") or {}).get("cache_hits"),
                               "attempts": job["attempts"]})
                if job["state"] != "done" or diagnostics["errors"] or diagnostics["quality_status"] == "blocked" or outcome != "completed":
                    break
            return self._finish(request, started, job=job, wait_status=outcome,
                                repeated=repeated, stages=stages)
        except KeyboardInterrupt:
            return self._finish(request, started, job=job, wait_status="cancelled",
                                repeated=repeated, stages=stages)
        except Exception as exc:
            request = {name: str(value) if isinstance(value, float) and not math.isfinite(value) else value
                       for name, value in request.items()}
            return self._finish(request, started, job=job, wait_status=outcome,
                                repeated=repeated, stages=stages, errors=[self._error(exc)],
                                exception_trace=traceback.format_exc())


def parser() -> argparse.ArgumentParser:
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument("--workspace", default=".")
    result.add_argument("--project-root", default=str(Path(__file__).resolve().parents[1]))
    commands = result.add_subparsers(dest="command", required=True)
    for name in ("job", "wait", "chain"):
        command = commands.add_parser(name)
        command.add_argument("--task-id")
        if name == "chain":
            command.add_argument("project_id")
            command.add_argument("script_asset_id")
            command.add_argument("--revision", type=int, required=True)
            command.add_argument("--key", required=True)
            command.add_argument("--mode", choices=("production", "development"), default="production")
            command.add_argument("--repair-reason")
        else:
            command.add_argument("job_id")
        if name != "job":
            command.add_argument("--timeout", type=float, default=60)
            command.add_argument("--poll-interval", type=float, default=.25)
    return result


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    client = Exchange(args.workspace, project_root=args.project_root)
    if args.command == "job":
        result = client.job(args.job_id, task_id=args.task_id)
    elif args.command == "wait":
        result = client.wait(args.job_id, task_id=args.task_id, timeout=args.timeout, poll_interval=args.poll_interval)
    else:
        result = client.chain(args.project_id, args.script_asset_id, args.revision, args.key,
                              task_id=args.task_id, mode=args.mode, timeout=args.timeout,
                              poll_interval=args.poll_interval, repair_reason=args.repair_reason)
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False))
    return 2 if result["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
