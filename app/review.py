"""Read verified review artifacts belonging to one completed QA job."""

from __future__ import annotations

import json
from pathlib import Path

from .contracts import QAReport
from .jobs import JobManager
from .registry import StudioError, content_hash, workspace_path


def read_review(root: str | Path, job_id: str) -> dict:
    """Return report content and bounded speech warnings, never arbitrary files."""
    root = Path(root).resolve()
    job = JobManager(root).status(job_id)
    if job["operation"] != "qa":
        raise StudioError("JOB_TYPE_INVALID", "Review requires a QA job ID")
    if job["state"] != "done" or not isinstance(job["result"], dict):
        raise StudioError("REVIEW_NOT_READY", "The QA job must complete before its report can be reviewed")
    result = job["result"]
    directory = workspace_path(root, f"jobs/{job_id}")

    def artifact(relative: str, suffixes: set[str]) -> Path:
        path = workspace_path(root, relative)
        if not path.is_relative_to(directory) or path.suffix.lower() not in suffixes:
            raise StudioError("PATH_UNSAFE", "Review artifact does not belong to this QA job")
        if not path.is_file():
            raise StudioError("ASSET_NOT_FOUND", "A saved QA artifact is missing")
        return path

    def verified_json(relative: str, digest: str) -> dict:
        path = artifact(relative, {".json"})
        if path.stat().st_size > 8_000_000:
            raise StudioError("RESOURCE_LIMIT", "QA JSON exceeds the 8 MB read limit")
        if not digest or content_hash(path) != digest:
            raise StudioError("ASSET_CHANGED", "QA artifact no longer matches its recorded hash")
        value = json.loads(path.read_text(encoding="utf-8-sig"))
        if not isinstance(value, dict):
            raise StudioError("SCHEMA_INVALID", "Expected a JSON report object")
        return value

    report = verified_json(result["report"], result["report_sha256"])
    validated = QAReport.model_validate(report)
    if validated.project_id != job["project_id"]:
        raise StudioError("PROJECT_MISMATCH", "QA report belongs to another project")
    frame_paths = result.get("frames", [])
    if not isinstance(frame_paths, list) or len(frame_paths) > 150:
        raise StudioError("RESOURCE_LIMIT", "Unexpected QA frame list")
    frames = []
    for relative in frame_paths:
        artifact(relative, {".png", ".jpg", ".jpeg", ".webp"})
        frames.append(relative)
    speech = {"status": "not_checked", "reason": "No verified speech report is attached"}
    asr_relative = result.get("asr_report")
    if asr_relative:
        digest = result.get("artifact_hashes", {}).get(asr_relative)
        if digest:
            raw = verified_json(asr_relative, digest)
            segments = raw.get("segments", [])
            if not isinstance(segments, list) or len(segments) > 150:
                raise StudioError("SCHEMA_INVALID", "Unexpected speech report segments")
            warnings = [{key: row.get(key) for key in ("block_id", "status", "wer", "warnings", "missing_critical_terms")}
                        for row in segments if row.get("warnings") or row.get("missing_critical_terms")]
            speech = {"status": raw.get("status", "not_checked"), "human_review": raw.get("human_review", "not_checked"),
                      "segment_count": len(segments), "warning_segment_count": len(warnings),
                      "warning_segments": warnings[:30], "omitted_warning_segments": max(0, len(warnings) - 30)}
    return {"job_id": job_id, "project_id": job["project_id"], "report": report,
            "report_path": result["report"], "report_sha256": result["report_sha256"],
            "frames": frames, "speech": speech,
            "notice": "Frame references are not a visual inspection; ASR results do not replace human listening."}
