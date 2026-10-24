"""Predict existing operations.synthesize cache identity; never synthesize audio."""
from pathlib import Path

from .production_profile import Profile
from .registry import content_hash, workspace_path
from .v2_acceptance import fingerprint
from .v2_contracts import Contract
from .contracts import Identifier, Sha256
from .registry import StudioError
from typing import Literal


class NarrationAsset(Contract):
    asset_id: Identifier
    cache_key: Sha256
    simulated: bool
    origin: Literal['synthetic_fixture', 'existing_cache']


def voice_asset_ids(candidate, recipe, bindings):
    if not bindings:
        return {}
    expected={mapping['segment_id']:segment for mapping,segment in zip(candidate['scene_block_mapping'],recipe['segments'])}
    if set(bindings)!=set(expected):
        raise StudioError('AUDIO_REQUIRED','Every segment needs an exact voice binding')
    result={}
    for segment_id,raw in bindings.items():
        binding=NarrationAsset.model_validate(raw)
        if binding.cache_key!=expected[segment_id]['cache_key'] or binding.simulated is not candidate['simulated']:
            raise StudioError('STALE_INPUTS','Audio belongs to another text/voice generation')
        if not candidate['simulated'] or binding.origin!='synthetic_fixture':
            # Production cache import needs a future host generation receipt, not a caller assertion.
            raise StudioError('LIVE_AUDIO_NOT_IMPLEMENTED','Live audio import requires separate verified host integration')
        result[segment_id]=binding.asset_id
    return result


def profile_spec():
    return Profile.model_validate_json((Path(__file__).resolve().parents[1]/'config/production_profile.json').read_text(encoding='utf-8')).model_dump()


def narration_descriptors(candidate, engine_root):
    profile = profile_spec()
    voice = profile['voice']
    model = workspace_path(Path(__file__).resolve().parents[1], voice['model_ref'])
    # Match the unchanged operations.synthesize request settings, including paths.
    settings = {'model_path': str(model), 'workspace_root': str(Path(engine_root).resolve()),
                'voice_id': model.stem, 'language': 'ru-RU',
                'length_scale': voice['settings']['length_scale'],
                'normalization_scope': voice['normalization_scope']}
    for name in ('sentence_silence','leading_silence','trailing_silence','normalize_audio','volume','noise_scale','noise_w_scale'):
        settings[name] = voice['settings'][name]
    segments = []
    for block in candidate['script']['segments']:
        text = block['pronunciation_text'] or block['text']
        if not block['pronunciation_text']:
            for word, spoken in profile['pronunciation_lexicon'].items():
                text = text.replace(word, spoken)
        key = fingerprint({'text': text, 'model_hash': voice['model_sha256'], 'settings': settings})
        cached = Path(engine_root)/'cache/audio'/f'{key}.wav'
        evidence = None
        if cached.is_file():
            from .tts import wav_info
            evidence = {**wav_info(cached), 'sha256': content_hash(cached)}
        segments.append({'block_id': block['id'], 'text': block['text'], 'spoken_text': text,
                         'segment_version': key, 'cache_key': key, 'cache_path': f'cache/audio/{key}.wav',
                         'cache_evidence': evidence, 'settings': settings,
                         'model_hash': voice['model_sha256'], 'simulated': candidate['simulated']})
    return {'status': 'validated_not_enqueued', 'runtime_status': 'BLOCKED_LOCAL_RUNTIME',
            'runtime_reason': 'Piper execution is not validated or authorized by offline preparation.',
            'profile_id': profile['profile_id'], 'profile_version': profile['version'],
            'profile_sha256': fingerprint(profile), 'segments': segments,
            'execution_interface': 'app.jobs.JobManager / app.operations.synthesize',
            'required_registered_parameter': 'script_asset_id', 'production_started': False}


def narration_identity(recipe):
    # Observing a newly available cached WAV must not invalidate an unchanged plan.
    return fingerprint({'profile':recipe['profile_sha256'],
                        'segments':[(s['block_id'],s['cache_key']) for s in recipe['segments']]})
