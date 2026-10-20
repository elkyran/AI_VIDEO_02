"""A small CLI shared by automated clients and human file exchange."""

from __future__ import annotations

import argparse
import ctypes
import json
import os
from pathlib import Path
import platform
import shutil
import sqlite3
import subprocess
import sys

import pydantic
from pydantic import ValidationError

from . import __version__
from .contracts import CONTRACTS, ProjectBrief, Task, TaskSubmission, validate_contract
from .registry import Registry, StudioError, workspace_path, write_generated_json


def doctor(root: Path):
    runtime_file = root / "runtime.json"
    runtime = json.loads(runtime_file.read_text(encoding="utf-8-sig")) if runtime_file.is_file() else {}

    def tool_info(name, env_name, arguments):
        configured = os.environ.get(env_name) or runtime.get(f"{name}_path")
        bundled = Path(__file__).resolve().parents[1] / "tools" / "Shotcut" / (name + (".exe" if os.name == "nt" else ""))
        executable = configured if configured and Path(configured).is_file() else shutil.which(name)
        if not executable and bundled.is_file():
            executable = str(bundled)
        if not executable:
            return {"available": False, "configuration_variable": env_name}
        try:
            result = subprocess.run([executable, *arguments], capture_output=True, text=True,
                                    encoding="utf-8", errors="replace", timeout=15)
            output = (result.stdout or result.stderr).strip().splitlines()
            return {"available": result.returncode == 0, "path": executable,
                    "returncode": result.returncode, "version": output[:3]}
        except (OSError, subprocess.TimeoutExpired) as exc:
            return {"available": False, "path": executable, "error": str(exc)}

    memory = None
    if os.name == "nt":
        class MemoryStatus(ctypes.Structure):
            _fields_ = [("length", ctypes.c_ulong), ("load", ctypes.c_ulong),
                        ("total_phys", ctypes.c_ulonglong), ("avail_phys", ctypes.c_ulonglong),
                        ("total_page", ctypes.c_ulonglong), ("avail_page", ctypes.c_ulonglong),
                        ("total_virtual", ctypes.c_ulonglong), ("avail_virtual", ctypes.c_ulonglong),
                        ("avail_extended", ctypes.c_ulonglong)]
        status = MemoryStatus()
        status.length = ctypes.sizeof(MemoryStatus)
        if ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
            memory = {"total_bytes": status.total_phys, "available_bytes": status.avail_phys}
    disk_root = root if root.exists() else root.parent
    while not disk_root.exists():
        disk_root = disk_root.parent
    disk = shutil.disk_usage(disk_root)
    return {"studio_version": __version__, "python": sys.version.split()[0],
            "pydantic": pydantic.__version__, "os": platform.platform(),
            "processor": platform.processor(), "cpu_count": os.cpu_count(), "memory": memory,
            "free_disk_bytes": disk.free, "workspace": str(root.resolve()),
            "tools": {"ffmpeg": tool_info("ffmpeg", "FFMPEG_PATH", ["-version"]),
                      "ffprobe": tool_info("ffprobe", "FFPROBE_PATH", ["-version"]),
                      "nvidia_smi": tool_info("nvidia-smi", "NVIDIA_SMI_PATH", ["--query-gpu=name,memory.total,driver_version", "--format=csv,noheader"]),
                      "melt": tool_info("melt", "MELT_PATH", ["-version"])},
            "tts_configuration": {key: {"configured": bool(runtime.get(key)),
                                          "file_exists": bool(runtime.get(key)) and Path(runtime[key]).is_file()}
                                  for key in ("tts_python", "tts_model")},
            "capability_notice": "Tool discovery is not a successful synthesis, editor, render, or listening test."}


def parser():
    result = argparse.ArgumentParser(prog="studio", description="Local video studio foundation")
    result.add_argument("--workspace", default=".", help="Workspace containing registry.sqlite and project assets")
    result.add_argument("--json", action="store_true", help="Machine-readable output (also the default)")
    commands = result.add_subparsers(dest="command", required=True)

    def add(name, help_text):
        command = commands.add_parser(name, help=help_text)
        command.add_argument("--json", action="store_true", help=argparse.SUPPRESS)
        return command

    add("doctor", "Read local runtime capabilities")
    schema = add("schema", "Print a versioned JSON schema")
    schema.add_argument("contract", choices=sorted(CONTRACTS))
    init = add("init", "Initialize a registry from a ProjectBrief JSON file")
    init.add_argument("--input", required=True)
    brief_update = add("brief-update", "Version an updated project brief; analyzed status requires library coverage")
    brief_update.add_argument("--input", required=True)
    brief_update.add_argument("--revision", type=int, required=True)
    brief_update.add_argument("--key", required=True)
    status = add("status", "Read authoritative project revision, assets, and tasks")
    status.add_argument("project_id")
    validate = add("validate", "Validate structure only; does not certify truth or media quality")
    validate.add_argument("contract", choices=sorted(CONTRACTS))
    validate.add_argument("--input", required=True)
    register = add("register", "Register an existing immutable asset inside the workspace")
    register.add_argument("project_id")
    register.add_argument("asset_id")
    register.add_argument("path")
    register.add_argument("--kind", required=True, choices=["image", "video", "audio", "text", "other"])
    register.add_argument("--rights", required=True, choices=["owned", "permission", "public_domain", "unknown"])
    register.add_argument("--duration-ms", type=int)
    register.add_argument("--revision", type=int, required=True)
    register.add_argument("--key", required=True)
    create = add("task-create", "Queue a bounded ScriptBlock or ShotPlan decision")
    create.add_argument("--input", required=True)
    create.add_argument("--revision", type=int, required=True)
    create.add_argument("--key", required=True)
    export = add("task-export", "Export instructions, current revision, and response schemas")
    export.add_argument("project_id")
    export.add_argument("task_id")
    export.add_argument("--output", required=True)
    submit = add("task-import", "Validate and atomically accept an external task response")
    submit.add_argument("--input", required=True)
    submit_alias = add("submit", "Submit a schema-validated result (same operation as task-import)")
    submit_alias.add_argument("--input", required=True)
    next_task = add("next-task", "Read the next bounded task whose dependencies are complete")
    next_task.add_argument("project_id")
    source_import = add("source-import", "Preserve and index a UTF-8 TXT or text EPUB source")
    source_import.add_argument("project_id")
    source_import.add_argument("source_id")
    source_import.add_argument("work_id")
    source_import.add_argument("path")
    source_import.add_argument("--edition", default="unspecified")
    source_import.add_argument("--translation", default="unspecified")
    source_import.add_argument("--rights", default="unknown", choices=["owned", "permission", "public_domain", "unknown"])
    source_import.add_argument("--revision", type=int, required=True)
    source_import.add_argument("--key", required=True)
    source_import.add_argument("--entire-work", action="store_true", help="Explicitly declare this file contains the entire work; do not set for excerpts")
    coverage = add("coverage", "Inspect full-source coverage without inferring it from import alone")
    coverage.add_argument("project_id")
    queue = add("analysis-queue", "Create sequential chapter-analysis tasks, without marking them analyzed")
    queue.add_argument("project_id")
    queue.add_argument("source_id")
    queue.add_argument("--revision", type=int, required=True)
    queue.add_argument("--key", required=True)
    search = add("search", "Search registered extracted text through SQLite FTS5")
    search.add_argument("project_id")
    search.add_argument("query")
    search.add_argument("--limit", type=int, default=10)
    read = add("read", "Read a registered object or bounded chapter range by ID")
    read.add_argument("project_id")
    read.add_argument("object_id")
    read.add_argument("--start", type=int, default=0)
    read.add_argument("--length", type=int, default=4000)
    job_run = add("run", "Enqueue a validated local technical operation")
    job_run.add_argument("project_id")
    job_run.add_argument("operation", choices=["synthesize", "build", "render", "qa", "package"])
    job_run.add_argument("--input", required=True, help="Workspace-relative operation parameters JSON")
    job_run.add_argument("--revision", type=int, required=True)
    job_run.add_argument("--key", required=True)
    for name, description in (("job", "Read a persisted technical job"), ("cancel", "Request cancellation of a job"), ("resume", "Resume an interrupted technical job")):
        command = add(name, description)
        command.add_argument("job_id")
    patch = add("patch", "Create a new version with one script block changed")
    patch.add_argument("project_id")
    patch.add_argument("script_asset_id")
    patch.add_argument("block_id")
    patch.add_argument("--text", required=True)
    patch.add_argument("--revision", type=int, required=True)
    patch.add_argument("--key", required=True)
    return result


def run(args):
    root = Path(args.workspace).resolve()

    def read_input():
        return json.loads(workspace_path(root, args.input).read_text(encoding="utf-8-sig"))

    if args.command == "doctor":
        return doctor(root)
    if args.command == "schema":
        return CONTRACTS[args.contract].model_json_schema()
    if args.command == "validate":
        value = validate_contract(args.contract, read_input())
        return {"valid": True, "validation": "structure_only", "contract": args.contract, "data": value.model_dump()}
    if args.command in {"run", "job", "cancel", "resume"}:
        from .jobs import JobManager
        manager = JobManager(root)
        if args.command == "run":
            return manager.enqueue(args.project_id, args.operation, read_input(),
                                   base_revision=args.revision, idempotency_key=args.key)
        return getattr(manager, "status" if args.command == "job" else args.command)(args.job_id)
    with Registry(root) as registry:
        if args.command == "init":
            return registry.initialize(ProjectBrief.model_validate(read_input()))
        if args.command == "brief-update":
            return registry.update_brief(ProjectBrief.model_validate(read_input()), base_revision=args.revision, idempotency_key=args.key)
        if args.command == "status":
            return registry.status(args.project_id)
        if args.command == "register":
            return registry.register_asset(args.project_id, args.asset_id, args.path, kind=args.kind,
                rights=args.rights, duration_ms=args.duration_ms,
                base_revision=args.revision, idempotency_key=args.key)
        if args.command == "task-create":
            return registry.create_task(Task.model_validate(read_input()),
                base_revision=args.revision, idempotency_key=args.key)
        if args.command == "task-export":
            package = registry.export_task(args.project_id, args.task_id)
            path = write_generated_json(root, args.output, package)
            return {"exported": str(path.relative_to(root)), "base_revision": package["base_revision"]}
        if args.command in {"task-import", "submit"}:
            return registry.submit(TaskSubmission.model_validate(read_input()))
        if args.command == "next-task":
            return registry.next_task(args.project_id)
        if args.command == "patch":
            from .operations import patch_script
            return patch_script(registry, args.project_id, args.script_asset_id, args.block_id, args.text,
                                base_revision=args.revision, idempotency_key=args.key)
        if args.command in {"source-import", "analysis-queue", "search", "read", "coverage"}:
            from .library import Library
            library = Library(registry)
            if args.command == "source-import":
                return library.import_source(args.project_id, args.source_id, args.work_id, args.path,
                    edition=args.edition, translation=args.translation, rights=args.rights,
                    entire_work=args.entire_work,
                    base_revision=args.revision, idempotency_key=args.key)
            if args.command == "analysis-queue":
                return library.queue_analysis(args.project_id, args.source_id,
                    base_revision=args.revision, idempotency_key=args.key)
            if args.command == "search":
                return library.search(args.project_id, args.query, limit=args.limit)
            if args.command == "coverage":
                return library.coverage(args.project_id)
            return library.read(args.project_id, args.object_id, start=args.start, length=args.length)
    raise StudioError("COMMAND_UNKNOWN", "Unsupported command")


def main(argv=None):
    # CLI/MCP file exchange must use stable UTF-8 even on legacy Windows consoles.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    try:
        args = parser().parse_args(argv)
        result = run(args)
        print(json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False))
        return 0
    except ValidationError as exc:
        result = {"error": {"code": "SCHEMA_INVALID", "message": "The JSON does not match the contract",
                            "details": exc.errors(include_url=False, include_context=False)}}
    except StudioError as exc:
        result = exc.as_dict()
    except (ValueError, OSError, sqlite3.Error) as exc:
        result = {"error": {"code": "INPUT_OR_STORAGE_ERROR", "message": str(exc)}}
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str), file=sys.stderr)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
