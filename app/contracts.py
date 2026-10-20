"""Versioned interchange contracts; validation establishes structure, not truth."""

from __future__ import annotations

import math
from pathlib import PurePosixPath, PureWindowsPath
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

Identifier = Annotated[str, Field(pattern=r"^[A-Za-z][A-Za-z0-9_.-]{0,95}$")]
Sha256 = Annotated[str, Field(pattern=r"^[a-f0-9]{64}$")]
PositiveInt = Annotated[int, Field(gt=0)]
NonNegativeInt = Annotated[int, Field(ge=0)]
NonEmpty = Annotated[str, Field(min_length=1)]
TaskState = Literal["pending", "running", "done", "failed", "blocked", "needs_review", "stale"]


def relative_path(value: str) -> str:
    """Reject platform-independent escapes, drives, ADS and ambiguous separators."""
    path = PurePosixPath(value)
    winpath = PureWindowsPath(value)
    parts = value.split("/")
    reserved = {"CON", "PRN", "AUX", "NUL", *(f"COM{i}" for i in range(1, 10)), *(f"LPT{i}" for i in range(1, 10))}
    if (not value or "\\" in value or ":" in value or any(ord(char) < 32 for char in value)
            or any(char in value for char in '<>"|?*')
            or path.is_absolute() or winpath.is_absolute() or winpath.drive
            or any(part in ("..", ".", "") or part.endswith((".", " "))
                   or part.split(".", 1)[0].upper() in reserved for part in parts)):
        raise ValueError("Expected a relative forward-slash path inside the workspace")
    return value


class Contract(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, validate_assignment=True)
    schema_version: Literal["1.0"] = "1.0"


class ProjectBrief(Contract):
    project_id: Identifier
    title: NonEmpty
    work_title: NonEmpty
    character: NonEmpty
    language: Literal["ru", "en"] = "ru"
    milestone: Literal["pilot", "editorial_review", "full_episode"] = "pilot"
    target_duration_seconds: Annotated[int, Field(ge=45)] = 75
    width: PositiveInt = 1920
    height: PositiveInt = 1080
    fps: Annotated[int, Field(ge=1, le=120)] = 30
    source_status: Literal["unavailable", "partial", "complete_import", "analyzed"] = "unavailable"
    limitations: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def duration_for_milestone(self):
        if self.milestone == "full_episode" and self.target_duration_seconds < 480:
            raise ValueError("Full episodes require a target of at least 480 seconds")
        if self.milestone == "pilot" and self.target_duration_seconds > 90:
            raise ValueError("The first pilot target must be 45–90 seconds")
        if self.width * 9 != self.height * 16:
            raise ValueError("The output profile must be 16:9")
        return self


class SourceRef(Contract):
    source_id: Identifier
    work_id: Identifier
    text_revision: PositiveInt
    chapter: NonEmpty
    start_char: NonNegativeInt
    end_char: PositiveInt
    source_sha256: Sha256

    @model_validator(mode="after")
    def ordered_range(self):
        if self.end_char <= self.start_char:
            raise ValueError("end_char must exceed start_char")
        return self


class ScriptBlock(Contract):
    id: Identifier
    project_id: Identifier
    revision: PositiveInt
    purpose: NonEmpty
    text: NonEmpty
    pronunciation_text: str | None = None
    claim_ids: list[Identifier] = Field(default_factory=list)
    source_refs: list[SourceRef] = Field(default_factory=list)
    interpretation: bool = False
    fact_check: Literal["passed", "failed", "not_checked"] = "not_checked"
    delivery: str = "neutral"
    visual_need: str = ""


class AudioSegment(Contract):
    id: Identifier
    project_id: Identifier
    block_id: Identifier
    text_hash: Sha256
    voice_id: NonEmpty
    language: Literal["ru", "en"]
    path: str
    duration_ms: PositiveInt
    sample_rate_hz: PositiveInt
    channels: Annotated[int, Field(ge=1, le=8)]
    generation_settings: dict[str, Any] = Field(default_factory=dict)
    wav_hash: Sha256 | None = None
    model_hash: Sha256 | None = None
    engine: str | None = None

    _safe_path = field_validator("path")(relative_path)


class ShotPlan(Contract):
    id: Identifier
    project_id: Identifier
    block_id: Identifier
    asset_id: Identifier
    in_ms: NonNegativeInt = 0
    out_ms: PositiveInt
    purpose: Literal["illustration", "atmosphere", "explanation"]
    reason: NonEmpty

    @model_validator(mode="after")
    def ordered_range(self):
        if self.out_ms <= self.in_ms:
            raise ValueError("out_ms must exceed in_ms")
        return self


class TimelineVisualShot(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    visual: str
    frames: PositiveInt
    label: str = ""

    _safe_path = field_validator("visual")(relative_path)


class TimelineSegment(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    id: Identifier
    text: NonEmpty
    title: str
    visual: str
    audio: str
    duration: Annotated[float, Field(gt=0, allow_inf_nan=False)]
    visual_shots: list[TimelineVisualShot] = Field(default_factory=list)

    _safe_paths = field_validator("visual", "audio")(relative_path)


class Timeline(Contract):
    project_id: Identifier
    revision: PositiveInt
    fps: Annotated[int, Field(ge=1, le=120)] = 30
    width: PositiveInt = 1920
    height: PositiveInt = 1080
    music: str
    heading: str = ""
    footer: str = ""
    title_style: Literal["legacy", "lower_third"] = "legacy"
    overlays_enabled: bool = True
    segments: Annotated[list[TimelineSegment], Field(min_length=1)]

    _safe_path = field_validator("music")(relative_path)

    @model_validator(mode="after")
    def unique_segments(self):
        ids = [segment.id for segment in self.segments]
        if len(set(ids)) != len(ids):
            raise ValueError("Timeline segment IDs must be unique")
        if self.width * 9 != self.height * 16:
            raise ValueError("The output profile must be 16:9")
        if not math.isfinite(sum(segment.duration for segment in self.segments)):
            raise ValueError("Total duration must be finite")
        for segment in self.segments:
            if segment.visual_shots and sum(shot.frames for shot in segment.visual_shots) != math.ceil(segment.duration * self.fps - 1e-7):
                raise ValueError("Visual shots must cover exactly the narration segment frames")
        return self


class QACheck(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    name: NonEmpty
    status: Literal["passed", "failed", "not_checked"]
    evidence: str = ""
    reviewer: str = ""
    severity: Literal["info", "warning", "error"] = "info"
    time_ms: NonNegativeInt | None = None

    @model_validator(mode="after")
    def require_evidence(self):
        if self.status != "not_checked" and not self.evidence.strip():
            raise ValueError("Checked results require concrete evidence")
        return self


class QAReport(Contract):
    project_id: Identifier
    build_id: Identifier
    build_sha256: Sha256
    status: Literal["draft", "blocked", "ready_for_review", "accepted"] = "draft"
    checks: list[QACheck]
    limitations: list[str] = Field(default_factory=list)
    accepted_by: str | None = None

    @model_validator(mode="after")
    def honest_status(self):
        if self.status in ("ready_for_review", "accepted") and any(
                check.status == "failed" and check.severity == "error" for check in self.checks):
            raise ValueError("Unresolved errors prevent readiness")
        if self.status == "accepted" and not self.accepted_by:
            raise ValueError("Acceptance requires an explicit human acceptor")
        return self


class Claim(Contract):
    id: Identifier
    project_id: Identifier
    work_id: Identifier
    kind: Literal["event", "speech", "thought", "interpretation", "unknown"]
    statement: NonEmpty
    source_refs: Annotated[list[SourceRef], Field(min_length=1)]
    verification: Literal["passed", "failed", "not_checked"] = "not_checked"
    caveats: list[str] = Field(default_factory=list)


class ChapterAnalysis(Contract):
    id: Identifier
    project_id: Identifier
    source_id: Identifier
    chapter_id: Identifier
    summary: NonEmpty
    claims: list[Claim]
    unresolved_questions: list[str] = Field(default_factory=list)

    @model_validator(mode="after")
    def unique_claims(self):
        if len({claim.id for claim in self.claims}) != len(self.claims):
            raise ValueError("Claim IDs must be unique within an analysis")
        return self


class Dossier(Contract):
    id: Identifier
    project_id: Identifier
    work_id: Identifier
    character_name: NonEmpty
    aliases: list[str] = Field(default_factory=list)
    goals: NonEmpty
    first_meeting: NonEmpty
    relationships: NonEmpty
    decisions: NonEmpty
    plot_role: NonEmpty
    claim_ids: Annotated[list[Identifier], Field(min_length=1)]
    unresolved_questions: list[str] = Field(default_factory=list)
    reconciliation: Literal["not_checked", "needs_review", "reviewed"] = "not_checked"


class Task(Contract):
    id: Identifier
    project_id: Identifier
    instruction: NonEmpty
    result_contract: Literal["ScriptBlock", "ShotPlan", "ChapterAnalysis", "Claim", "Dossier"]
    state: TaskState = "pending"
    candidate_asset_ids: list[Identifier] = Field(default_factory=list)
    context: dict[str, Any] = Field(default_factory=dict)
    dependencies: list[Identifier] = Field(default_factory=list)


class TaskSubmission(Contract):
    project_id: Identifier
    base_revision: NonNegativeInt
    task_id: Identifier
    idempotency_key: Annotated[str, Field(min_length=1, max_length=128)]
    result: dict[str, Any]


CONTRACTS = {model.__name__: model for model in (
    ProjectBrief, Task, SourceRef, ScriptBlock, AudioSegment, ShotPlan, Timeline,
    QAReport, TaskSubmission, Claim, ChapterAnalysis, Dossier,
)}


def validate_contract(name: str, data: dict[str, Any]) -> Contract:
    if name not in CONTRACTS:
        raise ValueError(f"Unknown contract: {name}")
    return CONTRACTS[name].model_validate(data)
