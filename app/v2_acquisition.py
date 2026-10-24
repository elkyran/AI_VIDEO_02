"""Bounded source extraction and host validation of accepted creative dependencies."""
from __future__ import annotations

import json
import math
from pathlib import Path

from .editorial import Scene
from .registry import StudioError
from .v2_acceptance import fingerprint
from .v2_acquisition_contracts import ACQUISITION_OUTPUTS, SourceExtraction, SourcePassage, StoryOutline, VisualMappingResult
from .v2_ids import derived_id
from .v2_sources import CanonBible, source_manifest


def _strict_json(text):
    def obj(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError('Duplicate JSON key')
            result[key] = value
        return result
    value = json.loads(text, object_pairs_hook=obj,
        parse_constant=lambda _: (_ for _ in ()).throw(ValueError('Nonfinite JSON')))
    pending = [(value, 0)]
    while pending:
        value, depth = pending.pop()
        if depth > 32 or isinstance(value, float) and not math.isfinite(value):
            raise ValueError('JSON depth/nonfinite limit')
        if isinstance(value, (dict, list)):
            if len(value) > 4096:
                raise ValueError('JSON container limit')
            pending.extend((v, depth+1) for v in (value.values() if isinstance(value, dict) else value))


def extract_sources(config):
    from .v2_run_config import no_links, source_inventory, verify_inputs
    policy = config.acquisition
    if policy is None:
        raise StudioError('ACQUISITION_POLICY_REQUIRED', 'Explicit bounded extraction policy is required')
    bible = source_inventory(config)
    if any(s.size>policy.max_source_bytes for s in bible.sources):
        raise StudioError('SOURCE_BUDGET', 'A permitted source exceeds the explicit extraction bound')
    verify_inputs(config)
    passages, total = [], 0
    for source in bible.sources:
        if source.size > policy.max_source_bytes:
            raise StudioError('SOURCE_BUDGET', 'A permitted source exceeds the explicit extraction bound')
        with no_links(Path(config.input_root)/source.path).open('rb') as stream:
            raw = stream.read(min(source.size,policy.max_source_bytes)+1)
        import hashlib
        if len(raw) != source.size or hashlib.sha256(raw).hexdigest() != source.sha256:
            raise StudioError('SOURCE_CHANGED', 'Read source bytes differ from the permitted size/hash')
        try:
            text = raw.decode('utf-8-sig')
            if not text.strip():
                raise ValueError('Empty text')
            if Path(source.path).suffix.lower() == '.json':
                _strict_json(text)
        except (ValueError, UnicodeError, RecursionError):
            raise StudioError('SOURCE_INVALID', 'Allowed source is not bounded strict UTF-8 text/JSON') from None
        total += len(text.encode('utf-8'))
        if total > policy.max_text_bytes:
            raise StudioError('SOURCE_BUDGET', 'Total extracted text exceeds the explicit bound; nothing truncated')
        start = 3 if raw.startswith(b'\xef\xbb\xbf') else 0
        offset, chunk, count = start, [], 0
        def add():
            nonlocal start, chunk, count
            if not chunk:
                return
            value = ''.join(chunk)
            if raw[start:offset] != value.encode('utf-8'):
                raise StudioError('SOURCE_CHANGED', 'Extraction locator is not byte-exact')
            passages.append(SourcePassage(passage_id=derived_id('passage', source.source_id, source.edition,
                'h'+fingerprint({'sha256':source.sha256,'start':start,'end':offset})),
                source_id=source.source_id, edition=source.edition, raw_sha256=source.sha256,
                start_byte=start, end_byte=offset, text=value))
            if len(passages) > policy.max_passages:
                raise StudioError('SOURCE_BUDGET', 'Passage count exceeds host policy')
            start, chunk, count = offset, [], 0
        for char in text:
            width = len(char.encode('utf-8'))
            if count+width > policy.passage_bytes:
                add()
            chunk.append(char); count += width; offset += width
        add()
    return SourceExtraction(run_id=config.run_id, version=config.inputs.version,
        simulated=config.mode == 'simulation', source_manifest_sha256=fingerprint(source_manifest(bible)),
        passages=passages).model_dump()


def validate_citations(bible, extraction):
    bible = CanonBible.model_validate(bible)
    extraction = SourceExtraction.model_validate(extraction)
    binding = []
    for fact in bible.facts:
        if fact.status == 'owner_verified':
            raise StudioError('OWNER_REVIEW_REQUIRED', 'Creative output cannot claim owner verification')
        if fact.kind not in {'assumption','divergence'} and not fact.citations:
            raise StudioError('CITATION_REQUIRED', 'Acquired factual assertions need traceable source passages')
        for cite in fact.citations:
            found = [p for p in extraction.passages if (p.source_id,p.edition)==(cite.source_id,cite.edition)
                and p.start_byte <= cite.start_byte < cite.end_byte <= p.end_byte]
            if len(found) != 1:
                raise StudioError('CITATION_INVALID', 'Citation must be inside one accepted source passage')
            passage = found[0]
            try:
                quote = passage.text.encode('utf-8')[cite.start_byte-passage.start_byte:cite.end_byte-passage.start_byte].decode('utf-8')
            except UnicodeError:
                raise StudioError('CITATION_INVALID', 'Citation cuts a UTF-8 code point') from None
            if not quote.strip():
                raise StudioError('CITATION_INVALID', 'Citation is empty whitespace')
            binding.append({'work_id':bible.work_id,'fact_id':fact.fact_id,'edition':fact.edition,
                'source_id':cite.source_id,'source_sha256':passage.raw_sha256,'passage_id':passage.passage_id,
                'start_byte':cite.start_byte,'end_byte':cite.end_byte})
    return binding


def resolve_refs(bible, refs, *, assumptions=False):
    """Legacy plain / edition:fact accepted only when unambiguous; host sidecar is qualified."""
    bible = CanonBible.model_validate(bible)
    result = []
    for ref in refs:
        matches = [f for f in bible.facts if ref in {f.fact_id, f.edition+':'+f.fact_id,
            bible.work_id+':'+f.edition+':'+f.fact_id}]
        if len(matches) != 1 or (matches[0].kind in {'assumption','divergence'}) != assumptions:
            raise StudioError('CANON_REFERENCE_INVALID', 'Reference is absent, ambiguous or has another fact/assumption kind')
        fact = matches[0]
        result.append({'work_id':bible.work_id,'edition':fact.edition,'fact_id':fact.fact_id,
                       'kind':fact.kind,'sources':sorted({c.source_id for c in fact.citations})})
    return result


def unwrapped(value):
    return json.loads(value['text']) if set(value) <= {'schema_version','simulated','text'} and 'text' in value else value


def acquisition_scope(config, role):
    return {'run_id':config.run_id,'version':config.inputs.version,'simulated':config.mode=='simulation',
        'sources':[s.model_dump() for s in config.inputs.sources], 'media':[m.model_dump() for m in config.inputs.media],
        'brief':config.brief.model_dump(),'scene_count':config.inputs.requested_scene_count,
        'duration_tolerance_ms':config.acquisition.duration_tolerance_ms,'role':role}


def validate_acquisition(kind, payload, context):
    """Same canonical validator for parser, gate, manual completion and replay."""
    scope = context.get('acquisition_scope')
    if scope is None:
        raise StudioError('ACQUISITION_SCOPE_REQUIRED', 'New outputs require pinned host scope')
    allowed = {'gemini_story':{'canon_bible','story_outline','scene'},'gemini_visual':{'segment_visuals','review'}}
    if kind not in allowed.get(scope['role'],set()):
        raise StudioError('ROLE_MISMATCH', 'Acquisition output belongs to another fixed worker')
    values = [unwrapped(v) for v in context.get('dependency_results', {}).values()]
    canon = next((v for v in values if 'facts' in v and 'work_id' in v), None)
    extraction = next((v for v in values if 'passages' in v and 'locator' in v), None)
    outline = next((v for v in values if 'brief_sha256' in v and 'scenes' in v), None)
    scene = next((v for v in values if 'narration_segments' in v), None)
    model = Scene if kind == 'scene' else ACQUISITION_OUTPUTS[kind]
    result = model.model_validate(payload)
    version = context.get('entity_version',scope['version'])
    if result.simulated != scope['simulated'] or result.version != version:
        raise StudioError('STALE_INPUTS', 'Acquisition provenance/version differs from the host request')
    if kind == 'canon_bible':
        if (result.work_id != scope['run_id'] or [s.model_dump() for s in result.sources] != scope['sources']
                or extraction is None):
            raise StudioError('SOURCE_MANIFEST_CHANGED', 'Canon changed host inventory or lacks accepted extraction')
        validate_citations(result, extraction)
    elif kind == 'story_outline':
        if (canon is None or result.run_id != scope['run_id'] or result.work_id != canon['work_id']
                or result.canon_sha256 != fingerprint(canon) or result.brief_sha256 != fingerprint(scope['brief'])
                or result.requested_duration_ms != scope['brief']['target_minutes']*60000
                or result.style != scope['brief']['style'] or len(result.scenes) != scope['scene_count']
                or abs(sum(s.estimated_duration_ms for s in result.scenes)-result.requested_duration_ms) > scope['duration_tolerance_ms']):
            raise StudioError('OUTLINE_INVALID', 'Outline differs from accepted Canon/Brief/count/timing policy')
        for index, item in enumerate(result.scenes,1):
            if item.scene_id != derived_id(scope['run_id'],'scene','n'+str(index)):
                raise StudioError('OUTLINE_INVALID', 'Outline must keep the stable host scene slots')
            resolve_refs(canon,item.fact_refs); resolve_refs(canon,item.assumption_refs,assumptions=True)
    elif kind == 'scene':
        if any(n.simulated != scope['simulated'] for n in result.narration_segments):
            raise StudioError('STALE_INPUTS', 'Narration provenance differs from the host request')
        if canon is None or outline is None or extraction is None or result.project_id != scope['run_id']:
            raise StudioError('CANON_REFERENCE_INVALID', 'Scene requires accepted Canon/outline/source passages')
        slot = next((s for s in outline['scenes'] if s['scene_id'] == result.scene_id), None)
        if slot is None or result.scene_id != context.get('scene_id'):
            raise StudioError('SCENE_INVALID', 'Scene differs from its accepted outline slot')
        if len(result.narration_segments)>100 or sum(len(n.text) for n in result.narration_segments)>30000:
            raise StudioError('SCENE_INVALID', 'Narration exceeds bounded acquisition output')
        if set(result.fact_refs)-set(slot['fact_refs']) or set(result.assumption_refs)-set(slot['assumption_refs']):
            raise StudioError('CANON_REFERENCE_INVALID', 'Scene refs exceed its accepted outline scope')
        resolve_refs(canon,result.fact_refs); resolve_refs(canon,result.assumption_refs,assumptions=True)
        sources = {s['edition']+':'+s['source_id']:s['sha256'] for s in scope['sources']}
        if any(sources.get(k)!=v for k,v in result.dependencies.items()):
            raise StudioError('SOURCE_CHANGED', 'Scene source dependencies differ from pinned inputs')
    else:
        if (scene is None or result.project_id != scope['run_id'] or result.scene_id != scene['scene_id']
                or result.scene_sha256 != fingerprint(scene)):
            raise StudioError('VISUAL_REQUIRED', 'Mappings require their accepted exact Scene')
        by_segment = {m.segment_id:m for m in result.mappings}
        if len(by_segment)!=len(result.mappings) or set(by_segment)!={n['segment_id'] for n in scene['narration_segments']}:
            raise StudioError('VISUAL_REQUIRED', 'Mappings must cover each accepted narration segment exactly once')
        media = {m['asset_id']:m for m in scope['media']}
        shot_ids, used = set(), []
        for mapping in result.mappings:
            if (mapping.scene_id != scene['scene_id'] or mapping.scene_version != scene['version']
                    or sum(s.source_out_ms-s.source_in_ms for s in mapping.shots)!=mapping.duration_ms):
                raise StudioError('VISUAL_REQUIRED', 'Mapping version/ranges do not cover the segment')
            for shot in mapping.shots:
                if shot.shot_id in shot_ids:
                    raise StudioError('SCHEMA_INVALID', 'Duplicate acquired shot ID')
                shot_ids.add(shot.shot_id)
                if any(p.asset_id == shot.asset_id and p.source_in_ms < shot.source_out_ms
                        and shot.source_in_ms < p.source_out_ms for p in used) and not (shot.reuse_reason or '').strip():
                    raise StudioError('REUSE_REASON_REQUIRED', 'Repeated source range needs explicit explanation')
                used.append(shot)
                asset=media.get(shot.asset_id)
                if not asset or asset['kind']=='image' and shot.source_in_ms or asset['kind']=='video' and shot.source_out_ms>asset['duration_ms']:
                    raise StudioError('ASSET_RANGE_INVALID', 'Mapping exceeds host media inventory/ranges')
    value=result.model_dump()
    if context.get('scoped_revision'):
        from .v2_scoped_fixes import validate_delta
        validate_delta(value,context)
    return value
