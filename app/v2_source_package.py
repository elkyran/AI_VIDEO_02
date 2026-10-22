"""Explicit source-only release inventory. No models, accounts or studio data."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import zipfile

from .registry import StudioError, canonical_json, content_hash
from .contracts import relative_path
from .v2_run_config import isolated_root, no_links

SCRIPTS=('__init__.py','launch_multi_ai_v2.py','healthcheck_multi_ai_v2.py',
    'export_multi_ai_v2_schemas.py','synthesize_script.py','render_project.py',
    'transcribe_audio.py','speech_model_identity.py','check_multi_ai_render.py','package_multi_ai_v2.py')
INTERNAL_EDGES={'app/jobs.py':['app/worker.py'],
    'app/operations.py':['scripts/synthesize_script.py'],
    'app/production_bridge.py':['scripts/render_project.py'],
    'app/speech_review.py':['scripts/transcribe_audio.py','scripts/speech_model_identity.py'],
    'app/multi_ai_preview.py':['scripts/check_multi_ai_render.py']}
MANIFEST='SOURCE_INVENTORY.json'
INSTALL_ROOTS=('.venv-core','.venv-tts','tools/Shotcut')


def external_dependencies(root):
    """Describe required private/local inputs without reading or copying them."""
    profile=json.loads((Path(root)/'config/production_profile.json').read_text(encoding='utf-8'))
    voice=profile['voice'];origin=profile['provenance']
    files={voice['model_ref']:{'sha256':voice['model_sha256'],'validation':'check_runtime'},
           voice['model_ref']+'.json':{'sha256':voice['model_config_sha256'],'validation':'check_runtime'}}
    for name in ('base_manifest','final_manifest','timeline'):
        files[origin[name+'_ref']]={'sha256':origin[name+'_sha256'],'validation':'load_profile'}
    for ref in [origin['runtime_source_ref'],*origin['style_refs']]:
        files[ref]={'sha256':None,'validation':'load_profile existence; not a credential source'}
    for ref in files:relative_path(ref)
    return {'install_roots':list(INSTALL_ROOTS),'files':files,
        'scope':'external and excluded from ZIP; source integrity neither requires nor attests these dependencies'}


def inventory(root):
    root=Path(root).resolve()
    tracked=set(subprocess.check_output(['git','ls-files'],cwd=root,text=True,encoding='utf-8').splitlines())
    files={p for p in tracked if p.startswith('app/') and len(Path(p).parts)==2 and p.endswith('.py')}
    files.update('scripts/'+name for name in SCRIPTS)
    files.update({'pyproject.toml','studio-v2.cmd','README.md','config/production_profile.json'})
    files.update(p for p in tracked if p.startswith('config/multi_ai_v2_schemas/') and p.endswith('.schema.json'))
    files.update(p for p in tracked if p.startswith('docs/') and Path(p).name in {
        'SYSTEM_RUNBOOK.md','START_HERE.md','DUAL_PROVIDER_INTEGRATION.md','SYSTEM_CLOSURE_02.md'})
    for source,targets in INTERNAL_EDGES.items():
        if source not in files or not set(targets)<=files:raise StudioError('SOURCE_CLOSURE','Missing internal dependency')
    return sorted(files)


def verify(folder):
    folder=no_links(folder)
    file=no_links(folder/MANIFEST)
    if file.stat().st_size>1024**2:raise StudioError('SOURCE_CLOSURE','Source inventory exceeds its fixed bound')
    manifest=json.loads(file.read_text(encoding='utf-8'))
    for relative,meta in manifest['files'].items():
        relative_path(relative)
        path=no_links(folder/relative)
        if not path.is_relative_to(folder) or not path.is_file() or path.stat().st_size!=meta['bytes'] or content_hash(path)!=meta['sha256']:
            raise StudioError('SOURCE_CLOSURE','Source package internal bytes changed')
    digest=hashlib.sha256(canonical_json(manifest['files']).encode()).hexdigest()
    if digest!=manifest['inventory_sha256']:raise StudioError('SOURCE_CLOSURE','Source inventory hash changed')
    # Only the checksum-verified profile can declare exact local dependency paths.
    # Owner installations are a separate trust boundary, never a source exemption
    # chosen by the manifest. Do not scan their contents or private provenance bytes.
    dependencies=external_dependencies(folder)
    if manifest.get('external_dependencies')!=dependencies:
        raise StudioError('SOURCE_CLOSURE','External dependency declaration changed')
    files=set(manifest['files'])|{MANIFEST}|set(dependencies['files'])
    directories={parent.as_posix() for name in files|set(INSTALL_ROOTS)
                 for parent in Path(name).parents if parent!=Path('.')}
    directories.update(INSTALL_ROOTS)
    def walk_error(_):raise StudioError('SOURCE_CLOSURE','Source package directory could not be checked')
    for directory,dirs,names in os.walk(folder,followlinks=False,onerror=walk_error):
        for name in list(dirs):
            path=no_links(Path(directory)/name);relative=path.relative_to(folder).as_posix()
            if relative not in directories:
                raise StudioError('SOURCE_CLOSURE','Source package has an unlisted directory')
            if relative in INSTALL_ROOTS:dirs.remove(name)
        for name in names:
            path=no_links(Path(directory)/name)
            if path.relative_to(folder).as_posix() not in files:
                raise StudioError('SOURCE_CLOSURE','Source package has an unlisted file')
    return manifest


def build(root,output):
    root=Path(root).resolve();output=Path(output).absolute()
    isolated_root(output.parent)
    if output.exists():raise StudioError('SOURCE_CLOSURE','Source ZIP already exists; use a new external path')
    if subprocess.check_output(['git','status','--porcelain','--untracked-files=no'],cwd=root,text=True).strip():
        raise StudioError('SOURCE_CLOSURE','Commit the reviewed sources before building a release')
    names=inventory(root)
    files={name:{'sha256':content_hash(no_links(root/name)),'bytes':(root/name).stat().st_size} for name in names}
    size=sum(v['bytes'] for v in files.values())
    if size>250*1024**2 or shutil.disk_usage(output.parent).free<4*1024**3+size:
        raise StudioError('BLOCKED_STORAGE','Source ZIP needs the bounded output allowance plus 4 GiB reserve')
    def git(ref):return subprocess.check_output(['git','rev-parse',ref],cwd=root,text=True).strip()
    manifest={'format':1,'source_commit':git('HEAD'),'source_tree':git('HEAD^{tree}'),
        'files':files,'inventory_sha256':hashlib.sha256(canonical_json(files).encode()).hexdigest(),
        'internal_edges':INTERNAL_EDGES,'external_dependencies':external_dependencies(root),
        'models_included':False,'credentials_included':False,
        'scope':'runtime source; installed Python/dependencies/tools/Ruslan required; development CI is outside this ZIP'}
    with zipfile.ZipFile(output,'x',compression=zipfile.ZIP_DEFLATED) as archive:
        for name in names:
            info=zipfile.ZipInfo(name,date_time=(2026,1,1,0,0,0));info.compress_type=zipfile.ZIP_DEFLATED
            archive.writestr(info,(root/name).read_bytes())
        archive.writestr(MANIFEST,canonical_json(manifest)+'\n')
    return {**manifest,'zip_sha256':content_hash(output),'zip_bytes':output.stat().st_size,'path':str(output)}
