"""Generated public-profile proof inputs; never reads the working studio."""
from copy import deepcopy
import json
from pathlib import Path

from app.registry import content_hash,write_generated_json
from app.v2_narration import profile_spec


def profile_fixture(folder):
    folder=Path(folder).resolve()
    if (folder/'config/production_profile.json').exists():return folder
    profile=deepcopy(profile_spec());voice=profile['voice'];origin=profile['provenance']
    segment={'engine':voice['engine'],'voice_id':voice['voice_id'],'model_hash':voice['model_sha256'],
        'model_config_hash':voice['model_config_sha256'],'engine_version':voice['engine_version'],
        'generation_settings':voice['settings'],'sample_rate_hz':voice['sample_rate_hz'],
        'normalization_scope':voice['normalization_scope'],'punctuation_transform':voice['punctuation_transform'],
        'use_cuda':voice['use_cuda']}
    values={'base_manifest':{'segments':[segment]},'final_manifest':{'segments':[{'voice_id':voice['voice_id']}]},
        'timeline':{k:profile['video'][k] for k in ('width','height','fps')}}
    for name,value in values.items():
        ref='evidence/'+name+'.json';path=write_generated_json(folder,ref,value)
        origin[name+'_ref']=ref;origin[name+'_sha256']=content_hash(path)
    write_generated_json(folder,'runtime_reference.json',{'scope':'synthetic test reference; no native runtime'})
    write_generated_json(folder,'style_reference.json',{'scope':'synthetic metadata; no style change'})
    origin.update(runtime_source_ref='runtime_reference.json',style_refs=['style_reference.json'],
        approval_basis='Synthetic offline fixture; NOT historical approval or owner media review')
    write_generated_json(folder,'config/production_profile.json',profile)
    return folder
