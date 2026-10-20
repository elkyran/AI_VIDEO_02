"""Bounded local Graphify adapter; no model, global skill, hook, watcher or cloud."""
from __future__ import annotations

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import tempfile
import time

from .development import atomic_write, digest, locked, now


def safe(root, ref):
    from .project_memory import safe_path
    return safe_path(root, ref)


def corpus(root):
    result = {}
    for folder in ("app", "scripts", "tests"):
        for path in sorted((root / folder).rglob("*")):
            if path.suffix not in {".py", ".ps1"} or not path.is_file() or "__pycache__" in path.parts:
                continue
            ref = path.relative_to(root).as_posix()
            safe(root, ref)
            if any(part.lower().startswith(("secrets", "credentials", ".env")) for part in path.parts):
                continue
            result[ref] = digest(path.read_bytes())
    return result


def fingerprint(files):
    return digest(json.dumps(files, sort_keys=True).encode())


def graph_status(root):
    manifest = root / "graphify-out/corpus.json"
    graph = root / "graphify-out/graph.json"
    if not manifest.exists() or not graph.exists():
        return {"state": "missing", "reason": "No verified Graphify corpus; use current code_refs"}
    try:
        saved = json.loads(manifest.read_text(encoding="utf-8"))
        current = corpus(root)
        old = saved["files"]
        changes = sorted(k for k in set(old) | set(current) if old.get(k) != current.get(k))
        graph_changed = digest(graph.read_bytes()) != saved["graph_sha256"]
        return {"state": "stale" if changes or graph_changed else "current", "indexed_at": saved["indexed_at"],
                "graphify_version": saved["graphify_version"], "schema_version": saved["graph_schema_version"],
                "corpus_sha256": saved["corpus_sha256"], "changed_count": len(changes), "changed_refs": changes[:8],
                "truncated": len(changes) > 8, "graph_changed": graph_changed,
                "manifest_ref": "graphify-out/corpus.json", "warnings": saved.get("warnings", [])}
    except (KeyError, ValueError, OSError) as exc:
        return {"state": "unavailable", "reason": str(exc), "manifest_ref": "graphify-out/corpus.json"}


def graph_results(root, refs, *, limit=8, terms=None):
    status = graph_status(root)
    if status["state"] != "current":
        return status, []
    graph = json.loads((root / "graphify-out/graph.json").read_text(encoding="utf-8"))
    if graph.get("graph", {}).get("schema_version") != 1:
        return {"state": "unsupported", "reason": "Unknown graph schema"}, []
    terms = terms or []
    nodes = [n for n in graph["nodes"] if n.get("source_file") in refs and n.get("file_type") == "code" and n.get("source_location")]
    nodes.sort(key=lambda n: (not any(t.casefold() in n.get("label", "").casefold() for t in terms), n.get("source_file", ""), int(re.search(r"\d+", n.get("source_location", "0"))[0])))
    results = [{"id": n["id"], "label": n.get("label"), "source_file": n["source_file"],
                "source_location": n["source_location"], "note_ref": f"generated/code/nodes/{n['id']}.md"} for n in nodes[:limit]]
    for result in results:
        if not (root / "knowledge" / result["note_ref"]).is_file():
            result["note_ref"] = None
    status["results_truncated"] = len(nodes) > limit
    return status, results


def stage_export(stage):
    """Relocate native output into its own vault namespace; never import .obsidian."""
    canvas = json.loads((stage / "graph.canvas").read_text(encoding="utf-8"))
    mapping = {}
    for node in canvas["nodes"]:
        if node.get("type") == "file":
            identifier = node["id"].removeprefix("n_")
            if not re.fullmatch(r"[A-Za-z0-9_.-]+", identifier):
                raise ValueError(f"Unsafe native Graphify ID: {identifier}")
            mapping[node["file"]] = f"nodes/{identifier}.md"
    for path in stage.glob("*.md"):
        mapping.setdefault(path.name, "communities/" + path.name)
    output, errors = {}, []
    def replace_link(match):
        raw = match.group(1)
        name, sep, alias = raw.partition("|")
        target, sep2, heading = name.partition("#")
        filename = target if target.endswith(".md") else target + ".md"
        if filename not in mapping:
            errors.append(f"Missing native export target: {filename}")
            return match.group(0)
        label = alias if sep else target
        return "[[generated/code/" + mapping[filename] + ("#" + heading if sep2 else "") + "|" + label + "]]"
    for filename, destination in mapping.items():
        text = safe(stage, filename).read_text(encoding="utf-8")
        output[destination] = re.sub(r"\[\[([^\]]+)\]\]", replace_link, text)
    for node in canvas["nodes"]:
        if node.get("type") == "file":
            node["file"] = "generated/code/" + mapping[node["file"]]
    output["graph.canvas"] = json.dumps(canvas, ensure_ascii=False, indent=2) + "\n"
    if errors:
        raise ValueError("; ".join(errors[:8]))
    return output


def publish_export(root, content):
    """Hash-aware managed files only. Unknown/user-edited files remain and get proposals."""
    target = root / "knowledge/generated/code"
    manifest_path = root / "graphify-out/obsidian-files.json"
    old = json.loads(manifest_path.read_text(encoding="utf-8")) if manifest_path.exists() else {}
    new = dict(old)
    changed, proposed = [], []
    from .project_memory import obsidian_running
    active = obsidian_running()
    for ref, text in content.items():
        path = safe(target, ref)
        raw = text.encode("utf-8")
        current = path.read_bytes() if path.exists() else None
        if current == raw:
            new[ref] = digest(raw)
            continue
        if (current is not None and old.get(ref) != digest(current)) or (active and path.suffix == ".canvas"):
            proposal = path.with_name(path.stem + ".proposed" + path.suffix)
            atomic_write(proposal, text)
            proposed.append(proposal.relative_to(root).as_posix())
            continue
        if current is not None:
            backup = root / "development/memory-backups" / digest(current) / "generated/code" / ref
            backup.parent.mkdir(parents=True, exist_ok=True)
            if not backup.exists():
                backup.write_bytes(current)
        if (path.read_bytes() if path.exists() else None) != current:
            atomic_write(path.with_name(path.stem + ".proposed" + path.suffix), text)
            proposed.append(ref)
            continue
        atomic_write(path, text)
        changed.append(ref)
        new[ref] = digest(raw)
    # No deletion: obsolete files are reported for explicit archival.
    obsolete = sorted(set(old) - set(content))
    atomic_write(manifest_path, json.dumps(new, ensure_ascii=False, indent=2) + "\n")
    return {"written_count": len(changed), "proposed": proposed, "obsolete_for_explicit_archive": obsolete, "native_export": True, "obsidian_config_copied": False}


def record_corpus(root, *, warnings, log_ref):
    graph_path = root / "graphify-out/graph.json"
    graph = json.loads(graph_path.read_text(encoding="utf-8"))
    files = corpus(root)
    scanned = json.loads((root / "graphify-out/manifest.json").read_text(encoding="utf-8"))
    if set(scanned) != set(files):
        raise ValueError(f"Graphify manifest differs from allowed corpus: {sorted(set(scanned) ^ set(files))}")
    sources = {n.get("source_file") for n in graph["nodes"] if n.get("source_file")}
    if sources - set(files):
        raise ValueError(f"Graph contains unexpected source paths: {sorted(sources - set(files))[:8]}")
    data = {"schema_version": 1, "indexed_at": now(), "graphify_version": graph["graph"]["graphify_version"],
            "graph_schema_version": graph["graph"]["schema_version"], "backend": "none", "code_only": True,
            "files": files, "corpus_sha256": fingerprint(files), "graph_sha256": digest(graph_path.read_bytes()),
            "node_source_paths": sorted(sources), "scanned_without_nodes": sorted(set(files) - sources),
            "warnings": warnings, "log_ref": log_ref, "nodes": len(graph["nodes"]), "edges": len(graph["links"])}
    atomic_write(root / "graphify-out/corpus.json", json.dumps(data, ensure_ascii=False, indent=2) + "\n")
    return data


def refresh(root, *, apply=False):
    """Explicit staged rebuild; a fresh output prevents retaining deleted semantic/code nodes."""
    root = root.resolve()
    plan_path = root / "development/graph-sync-plan.json"
    with locked(root / "development/graph-sync.lock"):
        files = corpus(root)
        lock = json.loads((root / "development/tooling.lock.json").read_text(encoding="utf-8"))
        actual_version = importlib.metadata.version("graphifyy")
        if actual_version != lock["version"]:
            raise ValueError("Graphify version differs from tooling.lock.json")
        graph_path = root / "graphify-out/graph.json"
        plan = {"schema_version": 1, "files": files, "version": actual_version,
                "previous_graph_sha256": digest(graph_path.read_bytes()) if graph_path.exists() else None}
        if not apply:
            atomic_write(plan_path, json.dumps(plan, ensure_ascii=False, indent=2) + "\n")
            return {"mode": "dry-run", "files": len(files), "corpus_sha256": fingerprint(files), "backend": "none", "command": "graphify extract . --code-only --max-workers 1 --out <temporary-directory>", "plan_ref": "development/graph-sync-plan.json"}
        if not plan_path.exists() or json.loads(plan_path.read_text(encoding="utf-8")) != plan:
            raise ValueError("Graph refresh version conflict; repeat --dry-run")
        if graph_status(root)["state"] == "current":
            return {"status": "passed", "unchanged": True, "files": len(files), "backend": "none", "truncated": False}
        exe = root / ".venv-memory/Scripts/graphify.exe"
        if not exe.exists():
            exe = root / ".venv-memory/bin/graphify"
        env = {**os.environ, "GRAPHIFY_BACKEND": "none", "PYTHONUTF8": "1", "GRAPHIFY_MAX_WORKERS": "1"}
        started = time.monotonic()
        log_ref = "graphify-out/refresh.log"
        (root / "graphify-out").mkdir(exist_ok=True)
        with tempfile.TemporaryDirectory(prefix="graphify-stage-", dir=root / "graphify-out") as temp:
            stage = Path(temp)
            graph = stage / "graphify-out/graph.json"
            export = stage / "obsidian"
            commands = [[str(exe), "extract", ".", "--code-only", "--max-workers", "1", "--out", str(stage)],
                        [str(exe), "export", "html", "--graph", str(graph)],
                        [str(exe), "export", "obsidian", "--graph", str(graph), "--dir", str(export)]]
            logs = []
            for index, command in enumerate(commands):
                result = subprocess.run(command, cwd=root if index == 0 else stage, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=180)
                logs.append("COMMAND " + repr(command) + "\n" + result.stdout + result.stderr)
                atomic_write(root / log_ref, "\n\n".join(logs))
                if result.returncode:
                    raise ValueError(f"Graphify failed ({result.returncode}); full log: {log_ref}")
            if corpus(root) != files:
                raise ValueError("Sources changed during extraction; previous index preserved")
            staged_manifest = json.loads((stage / "graphify-out/manifest.json").read_text(encoding="utf-8"))
            staged_graph = json.loads(graph.read_text(encoding="utf-8"))
            if set(staged_manifest) != set(files) or {n.get('source_file') for n in staged_graph['nodes'] if n.get('source_file')} - set(files):
                raise ValueError("Staged graph violates the source allowlist")
            content = stage_export(export)
            current_graph = digest(graph_path.read_bytes()) if graph_path.exists() else None
            if current_graph != plan["previous_graph_sha256"]:
                raise ValueError("Graph changed during refresh; staged output not applied")
            for name in ("graph.json", "graph.html", "manifest.json", ".graphify_analysis.json", ".graphify_build.json"):
                source, target = stage / "graphify-out" / name, root / "graphify-out" / name
                if not source.exists():
                    continue
                if target.exists():
                    backup = root / "development/memory-backups" / digest(target.read_bytes()) / "graphify-out" / name
                    backup.parent.mkdir(parents=True, exist_ok=True)
                    if not backup.exists():
                        shutil.copy2(target, backup)
                atomic_write(target, source.read_text(encoding="utf-8"))
            exported = publish_export(root, content)
            warnings = [line.strip() for log in logs for line in log.splitlines() if "warning" in line.lower()]
            data = record_corpus(root, warnings=warnings, log_ref=log_ref)
        return {"status": "needs_review" if exported["proposed"] else "passed", "files": len(files), "nodes": data["nodes"], "edges": data["edges"], "elapsed_seconds": round(time.monotonic() - started, 3), "warnings": warnings, "export": exported, "log_ref": log_ref, "truncated": False}


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("command", choices=["status", "refresh"])
    p.add_argument("--root", type=Path, default=Path.cwd())
    group = p.add_mutually_exclusive_group()
    group.add_argument("--dry-run", action="store_true")
    group.add_argument("--apply", action="store_true")
    a = p.parse_args(argv)
    try:
        result = graph_status(a.root) if a.command == "status" else refresh(a.root, apply=a.apply)
        print(json.dumps(result, ensure_ascii=True))
        return 0
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(json.dumps({"error": str(exc), "truncated": False}, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
