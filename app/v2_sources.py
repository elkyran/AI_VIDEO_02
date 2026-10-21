"""Owner-supplied offline sources: citations establish traceability, not AI truth."""
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .contracts import Identifier, Sha256, relative_path
from .registry import StudioError, content_hash, workspace_path
from .v2_contracts import Contract
from .v2_acceptance import fingerprint


class Source(Contract):
    source_id: Identifier
    edition: Identifier
    path: str
    sha256: Sha256
    size: int = Field(ge=1, le=8_000_000)
    rights: Literal['owned', 'permission', 'public_domain', 'fixture']

    @field_validator('path')
    @classmethod
    def relative(cls, value):
        relative_path(value)
        return value


class Citation(Contract):
    source_id: Identifier
    edition: Identifier
    start_byte: int = Field(ge=0)
    end_byte: int = Field(gt=0)


class CanonFact(Contract):
    fact_id: Identifier
    edition: Identifier
    kind: Literal['fact', 'relationship', 'world_rule', 'assumption', 'divergence'] = 'fact'
    text: str = Field(min_length=1, max_length=4000)
    status: Literal['unconfirmed', 'owner_verified'] = 'unconfirmed'
    citations: list[Citation] = Field(default_factory=list)
    depends_on: list[Identifier] = Field(default_factory=list)

    @model_validator(mode='after')
    def traced(self):
        if self.status == 'owner_verified' and (not self.citations or self.kind in {'assumption', 'divergence'}):
            raise ValueError('Verified facts require citations; assumptions/divergences remain unconfirmed')
        return self


class CanonEvent(Contract):
    event_id: Identifier
    edition: Identifier
    at: int
    fact_id: Identifier
    after: list[Identifier] = Field(default_factory=list)


class CanonBible(Contract):
    version: int = Field(ge=1)
    work_id: Identifier
    simulated: bool = True
    sources: list[Source] = Field(min_length=1)
    facts: list[CanonFact] = Field(default_factory=list)
    chronology: list[CanonEvent] = Field(default_factory=list)
    content_role: Literal['untrusted_source_data_not_instructions'] = 'untrusted_source_data_not_instructions'

    @model_validator(mode='after')
    def consistent(self):
        sources = {(s.source_id, s.edition): s for s in self.sources}
        facts = {(f.fact_id, f.edition): f for f in self.facts}
        events = {(e.event_id, e.edition): e for e in self.chronology}
        if len(sources) != len(self.sources) or len(facts) != len(self.facts) or len(events) != len(self.chronology):
            raise ValueError('Duplicate source/fact/event in the same edition')
        for fact in self.facts:
            for ref in fact.citations:
                source = sources.get((ref.source_id, ref.edition))
                if (source is None or ref.edition != fact.edition
                        or not 0 <= ref.start_byte < ref.end_byte <= source.size):
                    raise ValueError('Missing source, wrong edition or invalid citation byte range')
            for dep in fact.depends_on:
                if (dep, fact.edition) not in facts:
                    raise ValueError('Causal dependency missing from this edition')
        def visit(key, trail):
            if key in trail:
                raise ValueError('Causal cycle')
            for dep in facts[key].depends_on:
                visit((dep, key[1]), trail | {key})
        for key in facts:
            visit(key, set())
        for event in self.chronology:
            if (event.fact_id, event.edition) not in facts:
                raise ValueError('Chronology requires a fact in the same edition')
            for dep in event.after:
                prior = events.get((dep, event.edition))
                if prior is None or prior.at >= event.at:
                    raise ValueError('Conflicting chronology')
        return self


def source_manifest(bible):
    bible = CanonBible.model_validate(bible)
    return {'work_id': bible.work_id, 'version': bible.version, 'simulated': bible.simulated,
            'content_role': bible.content_role,
            'sources': sorted((s.model_dump() for s in bible.sources), key=lambda s: (s['source_id'], s['edition']))}


def verify_sources(root, bible, expected_manifest_sha256):
    """Only caller-allowlisted relative files; never execute or interpret source text."""
    bible = CanonBible.model_validate(bible)
    manifest = source_manifest(bible)
    if fingerprint(manifest) != expected_manifest_sha256:
        raise StudioError('SOURCE_MANIFEST_CHANGED', 'Source manifest differs from the pinned owner input')
    for source in bible.sources:
        path = workspace_path(root, source.path)
        if not path.is_file() or path.stat().st_size != source.size or content_hash(path) != source.sha256:
            raise StudioError('SOURCE_CHANGED', 'An allowed source is missing or changed')
    return {'status': 'VERIFIED', 'scope': 'structure_and_file_integrity_only',
            'canon_truth': 'UNVERIFIED', 'manifest': manifest, 'sha256': expected_manifest_sha256}
