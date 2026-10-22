"""Canonical acquisition outputs; existing Canon/Scene/mapping meanings are reused."""
from typing import Literal

from pydantic import Field, model_validator

from .contracts import Identifier, Sha256
from .v2_contracts import Contract
from .v2_sources import CanonBible
from .v2_visuals import SegmentVisuals


class AcquisitionPolicy(Contract):
    duration_tolerance_ms: int = Field(ge=0, le=1_800_000)
    max_source_bytes: int = Field(default=262144, ge=1, le=8_000_000)
    max_text_bytes: int = Field(default=32768, ge=1, le=262144)
    passage_bytes: int = Field(default=4096, ge=8, le=16384)
    max_passages: int = Field(default=128, ge=1, le=512)
    director_required: bool = True
    auto_fix_authorized: bool = False
    retryable_job_errors: list[Literal['TIMEOUT', 'PROCESS_FAILED']] = Field(default_factory=list, max_length=2)


class SourcePassage(Contract):
    passage_id: Identifier
    source_id: Identifier
    edition: Identifier
    raw_sha256: Sha256
    extraction_version: Literal[1] = 1
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)
    text: str = Field(min_length=1, max_length=16384)

    @model_validator(mode='after')
    def exact_utf8_length(self):
        if self.end_byte-self.start_byte != len(self.text.encode('utf-8')):
            raise ValueError('Passage raw UTF-8 byte length differs from its locator')
        return self


class SourceExtraction(Contract):
    run_id: Identifier
    version: int = Field(ge=1)
    simulated: bool
    source_manifest_sha256: Sha256
    locator: Literal['raw_utf8_byte_offsets_bom_counted_crlf_preserved'] = 'raw_utf8_byte_offsets_bom_counted_crlf_preserved'
    passages: list[SourcePassage] = Field(min_length=1, max_length=512)


class OutlineScene(Contract):
    scene_id: Identifier
    ordinal: int = Field(ge=1, le=100)
    title: str = Field(min_length=1, max_length=300)
    goal: str = Field(min_length=1, max_length=1000)
    cause: str = Field(min_length=1, max_length=1000)
    consequence: str = Field(min_length=1, max_length=1000)
    after: list[Identifier] = Field(default_factory=list, max_length=100)
    fact_refs: list[str] = Field(default_factory=list, max_length=64)
    assumption_refs: list[str] = Field(default_factory=list, max_length=64)
    estimated_duration_ms: int = Field(gt=0, le=1_800_000)
    word_budget: int = Field(gt=0, le=6000)


class StoryOutline(Contract):
    run_id: Identifier
    work_id: Identifier
    version: int = Field(ge=1)
    simulated: bool
    brief_sha256: Sha256
    canon_sha256: Sha256
    requested_duration_ms: int = Field(ge=60000, le=1_800_000)
    style: str = Field(max_length=500)
    summary: str = Field(min_length=1, max_length=12000)
    scenes: list[OutlineScene] = Field(min_length=1, max_length=100)

    @model_validator(mode='after')
    def ordered(self):
        seen = set()
        for index, scene in enumerate(self.scenes, 1):
            if scene.ordinal != index or scene.scene_id in seen or set(scene.after)-seen:
                raise ValueError('Outline order/causal links must name unique preceding scenes')
            seen.add(scene.scene_id)
        return self


class VisualMappingResult(Contract):
    """One scene result wrapping the existing SegmentVisuals, not another shot schema."""
    project_id: Identifier
    scene_id: Identifier
    version: int = Field(ge=1)
    simulated: bool
    scene_sha256: Sha256
    mappings: list[SegmentVisuals] = Field(min_length=1, max_length=500)


ACQUISITION_OUTPUTS = {'canon_bible': CanonBible, 'story_outline': StoryOutline,
                       'segment_visuals': VisualMappingResult}
