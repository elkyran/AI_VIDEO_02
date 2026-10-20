"""Allowlisted production operations shared by the CLI, MCP and local workers."""
from __future__ import annotations

import hashlib
from fractions import Fraction
import json
import math
import os
from pathlib import Path
import shutil
import subprocess
import time
from typing import Literal
import wave
import zipfile

from pydantic import BaseModel, ConfigDict, Field, ValidationError, field_validator, model_validator

from .contracts import Identifier, ProjectBrief, QAReport
from .registry import Registry, StudioError, canonical_json, content_hash, workspace_path


class ProductionVisualShot(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    asset_id: Identifier
    weight: float=Field(default=1.0,gt=0,allow_inf_nan=False)
    label: str=Field(default='',max_length=200)
    motion: Literal['auto','static','zoom_in','zoom_out','pan_left','pan_right']='auto'
    fit: Literal['cover','contain']='cover'
    focal_x: float=Field(default=.5,ge=0,le=1,allow_inf_nan=False)
    focal_y: float=Field(default=.5,ge=0,le=1,allow_inf_nan=False)
    in_ms: int=Field(default=0,ge=0)


class ProductionBlock(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    id: Identifier
    text: str=Field(min_length=1,max_length=3000)
    pronunciation_text: str|None=None
    title: str=Field(max_length=120)
    visual_asset_id: Identifier|None=None
    visual_shots: list[ProductionVisualShot]=Field(default_factory=list,max_length=80)
    visual_purpose: Literal['illustration','atmosphere','explanation']='illustration'
    source_asset_ids: list[Identifier]=Field(default_factory=list)
    claim_ids: list[Identifier]=Field(default_factory=list)
    interpretation: bool=True

    @model_validator(mode='after')
    def needs_visual(self):
        if not self.visual_asset_id and not self.visual_shots:
            raise ValueError('A block needs visual_asset_id or visual_shots')
        return self

    def shots(self):
        return self.visual_shots or [ProductionVisualShot(asset_id=self.visual_asset_id)]


def allocate_shot_frames(frames: int, weights: list[float]) -> list[int]:
    """Largest-remainder allocation; every shot survives and the audio stays intact."""
    if not weights or frames<len(weights) or any(not math.isfinite(w) or w<=0 for w in weights):
        raise StudioError('SHOT_TIMING_INVALID','Need positive weights and at least one frame per shot')
    remaining=frames-len(weights)
    scale=max(weights)
    normalized=[weight/scale for weight in weights]
    total=sum(normalized)
    exact=[remaining*weight/total for weight in normalized]
    result=[1+math.floor(value) for value in exact]
    order=sorted(range(len(weights)),key=lambda i: (-(exact[i]-math.floor(exact[i])),i))
    for index in order[:frames-sum(result)]:result[index]+=1
    return result


def video_encoder(config: dict) -> str:
    encoder=config.get('video_encoder','libx264')
    if encoder not in ('libx264','h264_nvenc'):
        raise StudioError('CAPABILITY_MISSING','video_encoder must be libx264 or h264_nvenc')
    return encoder


def render_thread_count(config: dict) -> int:
    count=config.get('render_threads',1)
    if type(count) is not int or not 1<=count<=16:
        raise StudioError('SCHEMA_INVALID','render_threads must be an integer from 1 to 16')
    return count


class ReviewSource(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    asset_id: Identifier
    source_url: str=Field(min_length=1,max_length=2000)
    attribution: str=Field(min_length=1,max_length=1000)

    @field_validator('source_url')
    @classmethod
    def public_url(cls,value):
        from urllib.parse import urlsplit
        url=urlsplit(value)
        if url.scheme not in ('https','http') or not url.hostname or url.username or url.password:
            raise ValueError('A review source needs an HTTP(S) source page URL without credentials')
        return value

    @field_validator('attribution')
    @classmethod
    def actual_credit(cls,value):
        if not value.strip():raise ValueError('Attribution cannot be blank')
        return value


class ProductionScript(BaseModel):
    model_config=ConfigDict(extra='forbid',strict=True)
    schema_version: Literal['1.0']='1.0'
    project_id: Identifier
    title: str=Field(min_length=1,max_length=200)
    heading: str=''
    footer: str=''
    title_style: Literal['legacy','lower_third']='legacy'
    overlays_enabled: bool=True
    local_review: bool=False
    review_sources: list[ReviewSource]=Field(default_factory=list,max_length=2000)
    scope: str=Field(min_length=1)
    segments: list[ProductionBlock]=Field(min_length=1,max_length=150)

    @model_validator(mode='after')
    def unique_review_sources(self):
        if len({source.asset_id for source in self.review_sources})!=len(self.review_sources):
            raise ValueError('Review source asset IDs must be unique')
        return self


def read_json(path: Path):
    if path.stat().st_size>8_000_000:raise StudioError('RESOURCE_LIMIT','JSON input exceeds 8 MB')
    return json.loads(path.read_text(encoding='utf-8-sig'))


def save_json(path: Path,value) -> None:
    path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_suffix(path.suffix+'.tmp')
    temporary.write_text(json.dumps(value,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    os.replace(temporary,path)


def read_script(registry: Registry,project_id: str,asset_id: str) -> tuple[ProductionScript,dict]:
    asset=registry.asset(project_id,asset_id,verify=True)
    if asset['rights']=='unknown':raise StudioError('RIGHTS_UNCONFIRMED','Script rights are unknown')
    script=ProductionScript.model_validate(read_json(workspace_path(registry.root,asset['path'])))
    if script.project_id!=project_id:raise StudioError('PROJECT_MISMATCH','Script belongs to another project')
    ids=[s.id for s in script.segments]
    if len(ids)!=len(set(ids)):raise StudioError('SCHEMA_INVALID','Script block IDs must be unique')
    review_sources={source.asset_id:source for source in script.review_sources}
    brief=json.loads(registry._project(project_id)['brief'])
    if brief['milestone']=='editorial_review':
        if not script.local_review or brief['source_status'] not in ('partial','analyzed'):
            raise StudioError('REVIEW_SCOPE_REQUIRED','Editorial review requires local_review and partial/analyzed source status')
    for block in script.segments:
        for shot in block.shots():
            visual=registry.asset(project_id,shot.asset_id,verify=True)
            if visual['rights']=='unknown' and (not script.local_review or shot.asset_id not in review_sources):
                raise StudioError('RIGHTS_UNCONFIRMED','Unknown visual rights require explicit local_review and source URL/attribution')
            if visual['kind'] not in ('image','video'):raise StudioError('ASSET_KIND_INVALID','Visual must be image/video')
        if not block.interpretation and not block.source_asset_ids:
            raise StudioError('SOURCE_REQUIRED','Factual script blocks require registered source references')
        for source_id in block.source_asset_ids:
            source_asset=registry.asset(project_id,source_id,verify=True)
            if source_asset['kind']!='text':raise StudioError('SOURCE_REQUIRED','A factual source must be registered text')
        if brief['milestone']=='full_episode' and not block.interpretation:
            if not block.claim_ids:raise StudioError('SOURCE_REQUIRED','Full episode facts require verified library claims')
            from .library import Library
            library=Library(registry)
            for claim_id in block.claim_ids:
                row=registry.db.execute('SELECT payload FROM claims WHERE project_id=? AND id=?',(project_id,claim_id)).fetchone()
                if row is None:raise StudioError('CLAIM_NOT_FOUND','Unknown factual claim')
                claim=json.loads(row['payload'])
                if claim['verification']!='passed':raise StudioError('CLAIM_UNVERIFIED','Claim needs a source check')
                from .contracts import SourceRef
                for reference in claim['source_refs']:library.validate_ref(project_id,SourceRef.model_validate(reference))
    return script,asset


def validate_request(registry: Registry, project_id: str, operation: str,parameters: dict) -> dict:
    from .jobs import JobManager
    required={'synthesize':{'script_asset_id'},'build':{'script_asset_id','audio_job_id'},
              'render':{'build_job_id'},'qa':{'render_job_id'},'package':{'qa_job_id'}}
    if operation not in required:raise StudioError('OPERATION_NOT_ALLOWED','Unknown production operation')
    if not isinstance(parameters,dict) or set(parameters)!=required[operation] or not all(isinstance(v,str) and v for v in parameters.values()):
        raise StudioError('SCHEMA_INVALID',f'{operation} requires exactly {sorted(required[operation])}')
    registry._project(project_id)
    if 'script_asset_id' in parameters:read_script(registry,project_id,parameters['script_asset_id'])
    expected={'audio_job_id':'synthesize','build_job_id':'build','render_job_id':'render','qa_job_id':'qa'}
    for key,value in parameters.items():
        if key in expected:
            parent=JobManager(registry.root).status(value)
            if parent['project_id']!=project_id:raise StudioError('PROJECT_MISMATCH','Dependency belongs to another project')
            if parent['operation']!=expected[key] or parent['state']!='done':
                raise StudioError('DEPENDENCY_PENDING','Dependency must be a completed operation of the required type')
    return parameters


class Context:
    def __init__(self,root,job_id):
        from .jobs import JobManager
        self.root=Path(root).resolve();self.job_id=job_id
        self.manager=JobManager(root);self.job=self.manager.status(job_id)
        self.directory=workspace_path(self.root,f'jobs/{job_id}',for_write=True)
        self.directory.mkdir(parents=True,exist_ok=True)
        config=self.root/'runtime.json'
        self.config=read_json(config) if config.exists() else {}

    def check(self):
        if self.manager.status(self.job_id)['cancel_requested']:
            raise StudioError('CANCELLED','Operation cancelled; completed intermediates remain available')

    def progress(self,value,message):
        self.check();self.manager.update(self.job_id,value,message)

    def tool(self,name):
        value=self.config.get(name) or shutil.which(name.removesuffix('_path'))
        if not value or not Path(value).is_file():raise StudioError('CAPABILITY_MISSING',f'Configure installed runtime {name} in runtime.json')
        return str(Path(value).resolve())

    def relative(self,path):return Path(path).resolve().relative_to(self.root).as_posix()

    def run(self,arguments,*,log_name='process.log',cwd=None,timeout=1800):
        self.check()
        flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
        environment=os.environ.copy();environment['QT_QPA_PLATFORM']='offscreen'
        environment['PATH']=str(Path(arguments[0]).parent)+os.pathsep+environment.get('PATH','')
        log_path=self.directory/log_name
        started=time.monotonic()
        with log_path.open('w',encoding='utf-8') as log:
            process=subprocess.Popen(arguments,cwd=cwd or self.root,env=environment,stdout=log,stderr=subprocess.STDOUT,
                                     creationflags=flags,stdin=subprocess.DEVNULL)
            try:
                while process.poll() is None:
                    self.check()
                    if time.monotonic()-started>timeout:raise StudioError('TIMEOUT','Operation exceeded its configured time limit')
                    time.sleep(.3)
            except BaseException:
                process.terminate()
                try:process.wait(timeout=10)
                except subprocess.TimeoutExpired:process.kill();process.wait()
                raise
        if process.returncode:
            tail=log_path.read_text(encoding='utf-8',errors='replace')[-1600:]
            raise StudioError('PROCESS_FAILED',f'Local tool exit {process.returncode}: {tail}')
        return log_path

    def parent(self,identifier,operation):
        parent=self.manager.status(identifier)
        if parent['state']!='done' or parent['operation']!=operation or parent['project_id']!=self.job['project_id']:
            raise StudioError('DEPENDENCY_PENDING','Required job is no longer ready')
        return parent['result']


def synthesize(ctx,parameters):
    with Registry(ctx.root) as registry:
        script,asset=read_script(registry,ctx.job['project_id'],parameters['script_asset_id'])
    python=ctx.tool('tts_python');model=ctx.tool('tts_model')
    model_hash=content_hash(Path(model))
    engine=ctx.config.get('tts_engine','piper')
    if engine not in ('piper','silero'):
        raise StudioError('CAPABILITY_MISSING','tts_engine must be piper or silero')
    settings={'model_path':model,'workspace_root':str(ctx.root),'voice_id':Path(model).stem,'language':'ru-RU'}
    if engine=='piper':
        settings['length_scale']=float(ctx.config.get('tts_length_scale',1.08))
        # Forward explicit local voice preferences without changing legacy defaults.
        for key in ('normalization_scope','sentence_silence','leading_silence',
                    'trailing_silence','normalize_audio','volume','noise_scale','noise_w_scale'):
            if 'tts_'+key in ctx.config:
                settings[key]=ctx.config['tts_'+key]
    else:
        settings.update(engine='silero',speaker=ctx.config.get('tts_speaker','aidar'),
                        sample_rate=ctx.config.get('tts_sample_rate',48000),
                        cpu_threads=ctx.config.get('tts_cpu_threads',4),
                        max_chars=ctx.config.get('tts_max_chars',950),
                        paragraph_silence=ctx.config.get('tts_paragraph_silence',.14),
                        normalize_audio=True,volume=.85)
    runner=Path(__file__).resolve().parents[1]/'scripts/synthesize_script.py'
    records=[]
    for i,block in enumerate(script.segments):
        ctx.progress(i/len(script.segments),f'Synthesizing {block.id}')
        text=block.pronunciation_text or block.text
        lexicon=ctx.config.get('pronunciation_lexicon',{})
        if not isinstance(lexicon,dict) or not all(isinstance(k,str) and isinstance(v,str) and len(k)<100 and len(v)<150 for k,v in lexicon.items()):
            raise StudioError('SCHEMA_INVALID','Invalid local pronunciation dictionary')
        if not block.pronunciation_text:
            for name,spoken in lexicon.items():text=text.replace(name,spoken)
        key=hashlib.sha256(canonical_json({'text':text,'model_hash':model_hash,'settings':settings}).encode()).hexdigest()
        output=ctx.root/'cache/audio'/f'{key}.wav'
        request=ctx.directory/f'{block.id}.request.json'
        save_json(request,{'text':text,'output':str(output),'config':settings})
        log=ctx.run([python,str(runner),'--job',str(request)],log_name=f'{block.id}.log',timeout=300)
        # Worker stdout is one JSON record, optional dependency diagnostics precede it.
        result=json.loads(log.read_text(encoding='utf-8').strip().splitlines()[-1])
        result.update({'block_id':block.id,'id':'audio_'+block.id,'text':block.text,'path':ctx.relative(output)})
        records.append(result)
    report={'schema_version':'1.0','project_id':script.project_id,'script_asset_id':asset['id'],
            'script_sha256':asset['sha256'],'segments':records,
            'total_duration_seconds':sum(r['duration_seconds'] for r in records),'listening_review':'not_checked'}
    manifest=ctx.directory/'audio_manifest.json';save_json(manifest,report)
    return {'manifest':ctx.relative(manifest),'manifest_sha256':content_hash(manifest),
            'script_asset_id':asset['id'],'script_sha256':asset['sha256'],
            'duration_seconds':report['total_duration_seconds'],'cache_hits':sum(r['cache_hit'] for r in records)}


def build(ctx,parameters):
    from .media import original_music,camera_filter,framing_filter
    from .timeline import build_project
    with Registry(ctx.root) as registry:
        script,asset=read_script(registry,ctx.job['project_id'],parameters['script_asset_id'])
        brief=ProjectBrief.model_validate(json.loads(registry._project(script.project_id)['brief']))
        visuals={shot.asset_id:registry.asset(script.project_id,shot.asset_id,verify=True)
                 for block in script.segments for shot in block.shots()}
    audio=ctx.parent(parameters['audio_job_id'],'synthesize')
    manifest_path=workspace_path(ctx.root,audio['manifest'])
    if content_hash(manifest_path)!=audio['manifest_sha256']:raise StudioError('ASSET_CHANGED','Audio manifest changed')
    if audio['script_sha256']!=asset['sha256']:raise StudioError('STALE_AUDIO','Narration belongs to another script revision')
    manifest=read_json(manifest_path);measured={r['block_id']:r for r in manifest['segments']}
    if brief.milestone=='full_episode':
        with Registry(ctx.root) as registry:
            try:
                from .library import Library
                coverage=Library(registry).coverage(script.project_id)
            except (AttributeError,TypeError):
                coverage={}
        if brief.source_status!='analyzed' or not coverage.get('analysis_complete'):
            raise StudioError('SOURCE_INCOMPLETE','Full episode requires verified full-source chapter analysis')
    source_dir=ctx.directory/'sources';source_dir.mkdir(exist_ok=True)
    segments=[];shot_durations=[];kind_frames={'image':0,'video':0};encoder=video_encoder(ctx.config)
    for i,block in enumerate(script.segments):
        ctx.progress(i/(len(script.segments)+1),f'Preparing shot {block.id}')
        record=measured[block.id];wav=workspace_path(ctx.root,record['path'])
        if content_hash(wav)!=record['wav_hash']:raise StudioError('ASSET_CHANGED','Narration WAV was modified')
        frames=math.ceil((record['duration_seconds']+.24)*brief.fps)
        duration=frames/brief.fps
        shots=block.shots();allocated=allocate_shot_frames(frames,[shot.weight for shot in shots]);prepared=[]
        for shot_index,(shot,shot_frames) in enumerate(zip(shots,allocated)):
            visual=visuals[shot.asset_id];source=workspace_path(ctx.root,visual['path'])
            shot_duration=shot_frames/brief.fps;shot_durations.append(shot_duration)
            kind_frames[visual['kind']]+=shot_frames
            if visual['kind']=='image':
                if shot.in_ms:raise StudioError('ASSET_RANGE_INVALID','Image shots cannot have a source time offset')
                vf=camera_filter(shot_frames,i+shot_index,fps=brief.fps,width=brief.width,height=brief.height,
                                 motion=shot.motion,fit=shot.fit,focal_x=shot.focal_x,focal_y=shot.focal_y)
                if i==0 and shot_index==0:vf+=',fade=t=in:st=0:d=0.35'
                if i==len(script.segments)-1 and shot_index==len(shots)-1:
                    fade=min(.6,shot_duration);vf+=f',fade=t=out:st={shot_duration-fade:.3f}:d={fade:.3f}'
                arguments=[ctx.tool('ffmpeg_path'),'-hide_banner','-loglevel','error','-y','-i',str(source),'-vf',vf,'-frames:v',str(shot_frames)]
            else:
                if not visual['duration_ms'] or visual['duration_ms']/1000<(shot.in_ms/1000+shot_duration):
                    raise StudioError('ASSET_RANGE_INVALID','Source clip is shorter than the selected shot range')
                vf=framing_filter(brief.width,brief.height,fit=shot.fit,focal_x=shot.focal_x,focal_y=shot.focal_y)+f',fps={brief.fps}'
                if i==0 and shot_index==0:vf+=',fade=t=in:st=0:d=0.35'
                if i==len(script.segments)-1 and shot_index==len(shots)-1:
                    fade=min(.6,shot_duration);vf+=f',fade=t=out:st={shot_duration-fade:.3f}:d={fade:.3f}'
                arguments=[ctx.tool('ffmpeg_path'),'-hide_banner','-loglevel','error','-y','-ss',str(shot.in_ms/1000),'-i',str(source),'-t',str(shot_duration),'-vf',vf,'-frames:v',str(shot_frames)]
            cache_key=hashlib.sha256(canonical_json({'source':visual['sha256'],'filter':vf,'frames':shot_frames,'in_ms':shot.in_ms,'encoder':encoder,'preset_version':2}).encode()).hexdigest()
            clip=ctx.root/'cache/video'/f'{cache_key}.mp4';clip.parent.mkdir(parents=True,exist_ok=True)
            cache_record=clip.with_suffix('.json')
            encoding=['-preset','fast','-crf','18'] if encoder=='libx264' else ['-preset','p4','-cq','18','-rc','vbr','-b:v','0']
            arguments+=['-an','-c:v',encoder,*encoding,'-pix_fmt','yuv420p','-threads','4',str(clip)]
            if not (clip.exists() and cache_record.exists() and read_json(cache_record).get('sha256')==content_hash(clip)):
                ctx.run(arguments,log_name=f'{block.id}.shot_{shot_index+1:02d}.video.log')
                save_json(cache_record,{'sha256':content_hash(clip)})
            prepared.append({'visual':ctx.relative(clip),'frames':shot_frames,'label':shot.label})
        segment={'id':block.id,'text':block.text,'title':block.title,'visual':prepared[0]['visual'],
                 'audio':ctx.relative(wav),'duration':duration}
        if block.visual_shots:segment['visual_shots']=prepared
        segments.append(segment)
    total=sum(s['duration'] for s in segments)
    if brief.milestone=='pilot' and not 45<=total<=90:
        raise StudioError('DURATION_INVALID',f'Pilot is {total:.2f}s; revise content for 45–90s')
    if brief.milestone=='full_episode' and total<480:
        raise StudioError('DURATION_INVALID','Full episode is shorter than 480 seconds')
    if brief.milestone=='editorial_review' and total<45:
        raise StudioError('DURATION_INVALID','Editorial review must be at least 45 seconds')
    music=source_dir/'music.wav';original_music(music,total)
    timeline={'schema_version':'1.0','project_id':script.project_id,'revision':ctx.job['input_revision'],
              'fps':brief.fps,'width':brief.width,'height':brief.height,'music':ctx.relative(music),'segments':segments,
              'heading':script.heading,'footer':script.footer,'title_style':script.title_style,
              'overlays_enabled':script.overlays_enabled}
    result=build_project(timeline,ctx.directory/f"project_attempt_{ctx.job['attempts']}",base_dir=ctx.root)
    project=Path(result['project_mlt']);save_json(project.parent/'script.json',script.model_dump())
    (project.parent/'script.txt').write_text('\n\n'.join(b.text for b in script.segments),encoding='utf-8')
    review={'local_review':script.local_review,'publication_allowed':False if script.local_review else None,
            'rights_status':'Unknown rights remain unknown; no permission or legal exception is asserted.',
            'restriction':'Local private review only; do not publish or distribute.' if script.local_review else '',
            'visual_sources':[source.model_dump() for source in script.review_sources]}
    save_json(project.parent/'review_sources.json',review)
    return {'project_mlt':ctx.relative(project),'project_sha256':content_hash(project),
            'timeline_json':ctx.relative(Path(result['timeline_json'])),'duration_seconds':total,
            'file_hashes':{p.relative_to(project.parent).as_posix():content_hash(p) for p in project.parent.rglob('*') if p.is_file()},
            'script_asset_id':asset['id'],'script_sha256':asset['sha256'],'audio_job_id':parameters['audio_job_id'],
            'assets':[{k:v for k,v in a.items() if k in ('id','sha256','rights','kind')} for a in visuals.values()],
            'local_review':script.local_review,'review_sources':review['visual_sources'],
            'video_encoder':encoder,
            'visual_pacing':{'shots':len(shot_durations),'mean_seconds':total/len(shot_durations),
                             'min_seconds':min(shot_durations),'max_seconds':max(shot_durations),
                             'source_kind_seconds':{kind:frames/brief.fps for kind,frames in kind_frames.items()},
                             'video_source_fraction':kind_frames['video']/sum(kind_frames.values()),
                             'measurement_note':'Video source kind measures source duration, not perceptual action or absence of held animation frames.'},
            'same_engine_for_editor_and_export':True}


def render(ctx,parameters):
    result=ctx.parent(parameters['build_job_id'],'build');project=workspace_path(ctx.root,result['project_mlt'])
    verify_build(ctx,result)
    destination=ctx.directory/f"final_attempt_{ctx.job['attempts']}.mp4"
    ctx.progress(.05,'Rendering editable MLT with the bundled Shotcut engine')
    encoder=video_encoder(ctx.config)
    render_threads=render_thread_count(ctx.config)
    encoding=['crf=20','preset=fast'] if encoder=='libx264' else ['preset=p4','cq=20','rc=vbr','vb=0']
    ctx.run([ctx.tool('melt_path'),str(project),'-consumer',f'avformat:{destination}',
             f'vcodec={encoder}',*encoding,'pix_fmt=yuv420p','acodec=aac','ab=192k','ar=48000',
             'ac=2','movflags=+faststart',f'real_time=-{render_threads}','threads=4','progressive=1','-progress'],
            cwd=project.parent,log_name='render.log')
    verify_build(ctx,result)
    if not destination.exists() or destination.stat().st_size<1024:raise StudioError('EXPORT_EMPTY','Renderer did not create a video')
    return {'video':ctx.relative(destination),'sha256':content_hash(destination),
            'build_job_id':parameters['build_job_id'],'project_sha256':result['project_sha256'],
            'video_encoder':encoder,
            'render_threads':render_threads,
            'duration_seconds':result['duration_seconds']}


def verify_build(ctx,result):
    directory=workspace_path(ctx.root,result['project_mlt']).parent
    if content_hash(directory/'project.mlt')!=result['project_sha256']:
        raise StudioError('ASSET_CHANGED','MLT changed after build')
    for relative,digest in result['file_hashes'].items():
        path=workspace_path(directory,relative)
        if not path.is_file() or content_hash(path)!=digest:
            raise StudioError('ASSET_CHANGED',f'Build media changed: {relative}')


def subtitle_timing_issues(path: Path, duration: float, ranges: list[tuple[float,float]]) -> list[str]:
    """Check the actual SRT boundaries and coverage, independently of ASR success."""
    import re
    issues=[];cues=[];previous_end=0.0
    pattern=r'(\d{2,}):(\d{2}):(\d{2}),(\d{3}) --> (\d{2,}):(\d{2}):(\d{2}),(\d{3})'
    raw=path.read_text(encoding='utf-8-sig').strip()
    for index,chunk in enumerate(re.split(r'\r?\n\s*\r?\n',raw) if raw else [],1):
        lines=chunk.splitlines()
        match=re.fullmatch(pattern,lines[1]) if len(lines)>=3 else None
        if not match or lines[0]!=str(index) or not ''.join(lines[2:]).strip():
            issues.append(f'Cue {index}: invalid SRT structure');continue
        values=[int(value) for value in match.groups()]
        if any(values[i]>=60 for i in (1,2,5,6)):
            issues.append(f'Cue {index}: invalid clock fields');continue
        start=values[0]*3600+values[1]*60+values[2]+values[3]/1000
        end=values[4]*3600+values[5]*60+values[6]+values[7]/1000
        if not 0<=start<end<=duration+.04:issues.append(f'Cue {index}: outside video bounds')
        if start<previous_end-.002:issues.append(f'Cue {index}: overlapping or unordered')
        previous_end=end;cues.append((start,end))
    if not cues:issues.append('No subtitle cues')
    for index,(start,end) in enumerate(ranges,1):
        if not any(a<end and b>start for a,b in cues):issues.append(f'Block {index}: no subtitle coverage')
    return issues


def qa(ctx,parameters):
    result=ctx.parent(parameters['render_job_id'],'render');video=workspace_path(ctx.root,result['video'])
    if content_hash(video)!=result['sha256']:raise StudioError('ASSET_CHANGED','Export hash mismatch')
    build_result=ctx.parent(result['build_job_id'],'build')
    verify_build(ctx,build_result)
    timeline=read_json(workspace_path(ctx.root,build_result['timeline_json']))
    probe_log=ctx.run([ctx.tool('ffprobe_path'),'-v','error','-show_streams','-show_format','-of','json',str(video)],log_name='probe.json')
    probe=read_json(probe_log);streams=probe['streams']
    videos=[s for s in streams if s['codec_type']=='video'];audios=[s for s in streams if s['codec_type']=='audio']
    checks=[]
    def record(name,passed,evidence,severity='error'):
        checks.append({'name':name,'status':'passed' if passed else 'failed','evidence':evidence,'reviewer':'local_tools','severity':severity})
    record('video_profile',len(videos)==1 and videos[0]['width']==timeline['width'] and videos[0]['height']==timeline['height'],json.dumps(videos[:1]))
    try:frame_rate=Fraction(videos[0].get('avg_frame_rate','0/1')) if videos else Fraction(0)
    except (ValueError,ZeroDivisionError):frame_rate=Fraction(0)
    record('frame_rate',frame_rate==Fraction(timeline['fps']),f'measured={frame_rate}; timeline={timeline["fps"]}')
    sar=videos[0].get('sample_aspect_ratio','unknown') if videos else 'unknown'
    record('square_pixels',sar=='1:1',f'sample_aspect_ratio={sar}')
    record('audio_stream',bool(audios),json.dumps(audios[:1]))
    actual=float(probe['format']['duration']);expected=sum(s['duration'] for s in timeline['segments'])
    record('duration',abs(actual-expected)<.15,f'measured={actual:.3f}; timeline={expected:.3f}')
    ctx.progress(.2,'Decoding the complete export and measuring sound')
    decode=ctx.run([ctx.tool('ffmpeg_path'),'-hide_banner','-v','error','-xerror','-i',str(video),'-f','null','-'],log_name='decode.log')
    record('complete_decode',not decode.read_text(encoding='utf-8').strip(),'Full video/audio decoded with -xerror; no errors')
    metrics=ctx.run([ctx.tool('ffmpeg_path'),'-hide_banner','-i',str(video),'-af','volumedetect','-vn','-f','null','-'],log_name='volume.log')
    volume_text=metrics.read_text(encoding='utf-8',errors='replace')
    import re
    match=re.search(r'mean_volume:\s*(-?[\d.]+)',volume_text);peak=re.search(r'max_volume:\s*(-?[\d.]+)',volume_text)
    mean=float(match.group(1)) if match else -100
    peak_value=float(peak.group(1)) if peak else 100
    record('audio_level',-40<mean<-5 and peak_value<0,f'mean={mean} dBFS, max={peak_value} dBFS')
    preview_dir=ctx.directory/'frames';preview_dir.mkdir(exist_ok=True)
    starts=[];position=0
    for index,segment in enumerate(timeline['segments']):
        starts.append(position);shot_position=position
        shots=segment.get('visual_shots') or [{'frames':round(segment['duration']*timeline['fps'])}]
        for shot_index,shot in enumerate(shots):
            shot_duration=shot['frames']/timeline['fps'];at=shot_position+shot_duration/2
            stem=f'{index+1:03d}_{shot_index+1:02d}'
            ctx.run([ctx.tool('ffmpeg_path'),'-hide_banner','-loglevel','error','-y','-ss',str(at),'-i',str(video),'-frames:v','1',str(preview_dir/f'{stem}.png')],log_name=f'frame_{stem}.log')
            shot_position+=shot_duration
        position+=segment['duration']
    subtitles=None;asr_path=None;raw_subtitles=None;speech_cache=None
    if ctx.config.get('asr_python'):
        ctx.progress(.6,'Checking local speech recognition and word timestamps')
        from .speech_review import review_build_speech
        speech=review_build_speech(ctx,build_result)
        asr_path=speech['report'];subtitles=speech['subtitles'];raw_subtitles=speech['raw_subtitles']
        speech_cache={'key':speech['cache_key'],'hit':speech['cache_hit']}
        record('speech_cache_integrity',True,json.dumps(speech_cache)+'; input WAVs, model, worker and all speech artifacts verified','info')
        recognition=read_json(asr_path)
        negations_ok=all(r['expected_negations']==r['recognized_negations'] for r in recognition['segments'])
        record('speech_negations',negations_ok,'Expected and recognized negation sequences compared per block')
        record('speech_transcript',recognition['status']=='automated_pass',
               json.dumps({r['block_id']:r['wer'] for r in recognition['segments']}),'warning')
        timing_issues=subtitle_timing_issues(subtitles,actual,[(start,start+segment['duration']) for start,segment in zip(starts,timeline['segments'])])
        record('subtitle_timing',not timing_issues,json.dumps(timing_issues) if timing_issues else
               'Actual SRT: ordered, nonoverlapping, within video bounds, all blocks covered. Script text uses ASR word anchors; interpolated timing remains in speech QA')
    else:
        from .speech_review import prepare_speech_manifest
        prepare_speech_manifest(ctx,build_result)
        checks.append({'name':'speech_and_subtitles','status':'not_checked','evidence':'No local ASR runtime configured','reviewer':'','severity':'warning'})
    media_paths=[s[k] for s in timeline['segments'] for k in ('visual','audio')]
    media_paths += [shot['visual'] for s in timeline['segments'] for shot in s.get('visual_shots',[])]
    record('all_media_present',all((workspace_path(ctx.root,build_result['project_mlt']).parent/path).is_file() for path in media_paths),'All MLT narration and visual-shot media resolve in the packaged project directory')
    if build_result.get('visual_pacing'):
        record('visual_pacing',True,json.dumps(build_result['visual_pacing'])+'; measured cadence only, editorial relevance requires visual review','info')
    with Registry(ctx.root) as registry:
        brief=ProjectBrief.model_validate(json.loads(registry._project(ctx.job['project_id'])['brief']))
    checks += [{'name':name,'status':'not_checked','evidence':reason,'reviewer':'','severity':'warning'} for name,reason in (
        ('human_listening','Awaiting human listening review'),('visual_editor_review','Frames exported for inspection'),
        ('source_content_review',f'Saved source status: {brief.source_status}; technical QA does not re-review every literary claim'),('other_ai_clients','External Codex/Claude/Gemini clients not tested'),
        ('user_acceptance','Only the user can accept the completed video'))]
    limitations=list(brief.limitations)
    if brief.milestone=='editorial_review':
        limitations.append('Editorial review based on selected sources; whole-book analysis and complete edition reconciliation remain unfinished.')
        checks.append({'name':'source_scope','status':'not_checked','evidence':'Selected-source editorial review; does not satisfy the full_episode complete-source gate.','reviewer':'','severity':'warning'})
    if build_result.get('local_review'):
        limitations.append('Local private review only; source URLs and credits do not establish publication permission. Unknown visual rights remain unknown.')
    report={'schema_version':'1.0','project_id':ctx.job['project_id'],'build_id':result['build_job_id'],
            'build_sha256':result['sha256'],'status':'blocked' if any(c['status']=='failed' for c in checks) else 'ready_for_review',
            'checks':checks,'limitations':limitations}
    report['status']='blocked' if any(c['status']=='failed' and c['severity']=='error' for c in checks) else 'ready_for_review'
    QAReport.model_validate(report)
    save_json(ctx.directory/'qa_report.json',report)
    qa_files=[p for p in (subtitles,asr_path,raw_subtitles) if p is not None]
    return {'report':ctx.relative(ctx.directory/'qa_report.json'),'status':report['status'],
            'render_job_id':parameters['render_job_id'],'report_sha256':content_hash(ctx.directory/'qa_report.json'),
            'duration_seconds':actual,'subtitles':ctx.relative(subtitles) if subtitles else None,
            'asr_report':ctx.relative(asr_path) if asr_path else None,
            'raw_subtitles':ctx.relative(raw_subtitles) if raw_subtitles else None,
            'speech_cache':speech_cache,
            'artifact_hashes':{ctx.relative(p):content_hash(p) for p in qa_files},
            'frames':[ctx.relative(p) for p in sorted(preview_dir.glob('*.png'))]}


def package(ctx,parameters):
    qa_result=ctx.parent(parameters['qa_job_id'],'qa');report_path=workspace_path(ctx.root,qa_result['report'])
    if content_hash(report_path)!=qa_result['report_sha256']:raise StudioError('ASSET_CHANGED','QA report changed')
    report=read_json(report_path)
    if report['status']!='ready_for_review':raise StudioError('QA_FAILED','Correct failed checks before packaging')
    for relative,digest in qa_result.get('artifact_hashes',{}).items():
        checked=workspace_path(ctx.root,relative)
        if not checked.is_file() or content_hash(checked)!=digest:raise StudioError('ASSET_CHANGED','QA artifact changed after review')
    render_result=ctx.parent(qa_result['render_job_id'],'render')
    if content_hash(workspace_path(ctx.root,render_result['video']))!=render_result['sha256']:
        raise StudioError('ASSET_CHANGED','Video changed after QA')
    build_result=ctx.parent(render_result['build_job_id'],'build')
    verify_build(ctx,build_result)
    source=workspace_path(ctx.root,build_result['project_mlt']).parent
    target=ctx.directory/f"package_attempt_{ctx.job['attempts']}"
    if target.exists():raise StudioError('OUTPUT_EXISTS','Package folder already exists')
    target.mkdir()
    # Explicit allowlist excludes registry, runtime credentials, logs and full source books.
    for name in ('project.mlt','timeline.json','script.json','script.txt'):
        shutil.copy2(source/name,target/name)
    if (source/'review_sources.json').is_file():shutil.copy2(source/'review_sources.json',target/'review_sources.json')
    shutil.copytree(source/'media',target/'media')
    shutil.copy2(workspace_path(ctx.root,render_result['video']),target/'final.mp4')
    shutil.copy2(report_path,target/'qa_report.json')
    if qa_result.get('subtitles'):
        shutil.copy2(workspace_path(ctx.root,qa_result['subtitles']),target/'subtitles.srt')
        speech_report=read_json(workspace_path(ctx.root,qa_result['asr_report']))
        # Machine-specific loader paths belong to the local runtime, not the portable copy.
        speech_report.pop('dll_directories',None)
        for entry in speech_report['segments']:entry['path']=f"audio/{entry['block_id']}.wav"
        speech_report['subtitles']['path']='subtitles.srt'
        if qa_result.get('raw_subtitles'):
            shutil.copy2(workspace_path(ctx.root,qa_result['raw_subtitles']),target/'subtitles_asr.srt')
            speech_report['raw_subtitles']['path']='subtitles_asr.srt'
        save_json(target/'speech_qa.json',speech_report)
    audio_result=ctx.parent(build_result['audio_job_id'],'synthesize')
    manifest=read_json(workspace_path(ctx.root,audio_result['manifest']))
    audio_dir=target/'audio';audio_dir.mkdir()
    for record in manifest['segments']:
        original=workspace_path(ctx.root,record['path'])
        if content_hash(original)!=record['wav_hash']:raise StudioError('ASSET_CHANGED','Narration changed after QA')
        shutil.copy2(original,audio_dir/f"{record['block_id']}.wav")
        record['path']=f"audio/{record['block_id']}.wav"
        record.pop('model_path',None)
    save_json(target/'audio_manifest.json',manifest)
    source_records=[]
    with Registry(ctx.root) as registry:
        script,_=read_script(registry,ctx.job['project_id'],build_result['script_asset_id'])
        brief=json.loads(registry._project(ctx.job['project_id'])['brief'])
        for identifier in sorted({s for block in script.segments for s in block.source_asset_ids}):
            record=registry.asset(ctx.job['project_id'],identifier,verify=True)
            original=workspace_path(ctx.root,record['path'])
            entry={key:record[key] for key in ('id','sha256','kind','rights')}
            # Small registered research notes are permitted; whole source books are excluded.
            if record['rights'] in ('owned','permission','public_domain') and original.stat().st_size<=32000 and original.suffix.lower()=='.txt':
                copied=target/'sources'/f'{identifier}.txt';copied.parent.mkdir(exist_ok=True)
                shutil.copy2(original,copied);entry['file']=copied.relative_to(target).as_posix()
            source_records.append(entry)
        for entry in build_result['assets']:
            record=registry.asset(ctx.job['project_id'],entry['id'],verify=True)
            if record['kind']=='image':
                original=workspace_path(ctx.root,record['path'])
                copied=target/'artwork'/f"{record['id']}{original.suffix}";copied.parent.mkdir(exist_ok=True)
                if not copied.exists():shutil.copy2(original,copied)
    save_json(target/'sources.json',{'scope':script.scope,'references':source_records,
                                    'source_analysis_status':brief['source_status'],'milestone':brief['milestone'],
                                    'visual_sources':[source.model_dump() for source in script.review_sources]})
    save_json(target/'provenance.json',{'visuals':build_result['assets'],'music':'Original procedural ambient score from app.media',
        'narration':manifest['segments'][0].get('voice_id'),
        'publication':'Local private review only; do not publish or distribute.' if script.local_review else 'Not published; waiting for user review',
        'local_review':script.local_review,'visual_sources':[source.model_dump() for source in script.review_sources],
        'rights_note':'Unknown rights remain unknown; credits are not permission and no legal exception is asserted.',
        'voice_license_note':ctx.config.get('voice_license_note','Consult the model card before distribution')})
    paths=[p for p in target.rglob('*') if p.is_file()]
    save_json(target/'manifest.json',{'schema_version':'1.0','files':{p.relative_to(target).as_posix():content_hash(p) for p in paths}})
    archive=ctx.directory/f"{ctx.job['project_id']}_package.zip"
    with zipfile.ZipFile(archive,'w',zipfile.ZIP_DEFLATED,compresslevel=1) as bundle:
        for path in target.rglob('*'):
            if path.is_file():bundle.write(path,path.relative_to(target))
    return {'package_directory':ctx.relative(target),'archive':ctx.relative(archive),
            'video':ctx.relative(target/'final.mp4'),'project':ctx.relative(target/'project.mlt'),
            'status':'ready_for_review'}


def execute(root,job_id,operation,parameters):
    ctx=Context(root,job_id)
    with Registry(root) as registry:validate_request(registry,ctx.job['project_id'],operation,parameters)
    if shutil.disk_usage(ctx.root).free<700_000_000:raise StudioError('RESOURCE_LIMIT','Less than 700 MB free disk space')
    return {'synthesize':synthesize,'build':build,'render':render,'qa':qa,'package':package}[operation](ctx,parameters)


def patch_script(registry,project_id,script_asset_id,block_id,text,*,base_revision,idempotency_key):
    from .jobs import JobManager
    JobManager(registry.root)
    script,asset=read_script(registry,project_id,script_asset_id)
    selected=next((b for b in script.segments if b.id==block_id),None)
    if selected is None:raise StudioError('BLOCK_NOT_FOUND','Unknown script block')
    no_change=selected.text==text
    if not no_change:
        selected.text=text;selected.pronunciation_text=None
    # Validate the edited text before any write or invalidation.
    script=ProductionScript.model_validate(script.model_dump())
    encoded=canonical_json(script.model_dump());digest=hashlib.sha256(encoded.encode()).hexdigest()
    new_id='script_'+digest[:20];relative=f'generated/scripts/{new_id}.json'
    def mutate(_):
        if no_change:
            return {'script_asset_id':script_asset_id,'changed_block':None,'stale_jobs':[]},False
        existing=registry.db.execute("SELECT id FROM assets WHERE project_id=? AND sha256=? AND kind='text'",(project_id,digest)).fetchone()
        resolved_id=existing['id'] if existing else new_id
        target=workspace_path(registry.root,relative,for_write=True)
        if target.exists() and content_hash(target)!=digest:raise StudioError('ASSET_CHANGED','Version path already contains different content')
        target.parent.mkdir(parents=True,exist_ok=True)
        if not target.exists():target.write_text(encoded,encoding='utf-8')
        if not existing:registry.db.execute('INSERT INTO assets VALUES(?,?,?,?,?,?,?)',(project_id,new_id,relative,digest,'text',asset['rights'],None))
        stale=[]
        rows=registry.db.execute('SELECT id,parameters,state FROM jobs WHERE project_id=?',(project_id,)).fetchall()
        for row in rows:
            params=json.loads(row['parameters'])
            if params.get('script_asset_id')==script_asset_id:stale.append(row['id'])
        changed=True
        while changed:
            changed=False
            for row in rows:
                if row['id'] not in stale and any(v in stale for v in json.loads(row['parameters']).values()):
                    stale.append(row['id']);changed=True
        for identifier in stale:
            registry.db.execute("UPDATE jobs SET invalidated=1,state=CASE WHEN state='running' THEN state ELSE 'stale' END,cancel_requested=CASE WHEN state='running' THEN 1 ELSE cancel_requested END WHERE id=?",(identifier,))
        return {'script_asset_id':resolved_id,'previous_script_asset_id':script_asset_id,'changed_block':block_id,
                'stale_jobs':stale,'unchanged_audio':'Reusable by verified content hash'},True
    return registry._change(project_id,base_revision,idempotency_key,
        {'op':'patch_script','source':script_asset_id,'block_id':block_id,'text':text,'base_revision':base_revision},mutate)
