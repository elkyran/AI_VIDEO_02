"""Relocation uses isolated Python and native-boundary spies, never a renderer."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

from app.registry import StudioError
from app.v2_source_package import build,verify,external_dependencies,MANIFEST

CHILD=r'''
import importlib,json,sys
from pathlib import Path
from unittest.mock import patch
root=Path(sys.argv[1]).resolve();work=Path(sys.argv[2]).resolve()
sys.path.insert(0,str(root))
from app.v2_source_package import verify
verify(root)
for name in ('app.worker','app.operations','app.multi_ai_preview','app.speech_review',
             'scripts.synthesize_script','scripts.render_project','scripts.speech_model_identity',
             'scripts.transcribe_audio','scripts.check_multi_ai_render'):
    module=importlib.import_module(name)
    assert Path(module.__file__).resolve().is_relative_to(root),(name,module.__file__)
from app.jobs import JobManager
from app.registry import Registry,content_hash,write_generated_json
from app.contracts import ProjectBrief
from app.operations import Context,ProductionScript,ProductionBlock,synthesize
from app.production_bridge import ProductionBridge
# Missing private profile proofs must block the unchanged general caller before
# enqueue/native IO; source integrity alone cannot certify a runnable installation.
with patch('app.jobs.subprocess.Popen') as native:
    try:ProductionBridge(work).prepare_request('relocated','synthesize',{'script_asset_id':'script'},1,'proof_probe',project_root=root)
    except FileNotFoundError as exc:
        profile=json.loads((root/'config/production_profile.json').read_text(encoding='utf-8'))
        assert Path(exc.filename)==root/profile['provenance']['base_manifest_ref']
    else:raise AssertionError('Missing profile provenance was accepted')
    native.assert_not_called()
manager=JobManager(work)
with patch('app.jobs.subprocess.Popen') as native:
    manager._spawn('relocated_probe')
argv=native.call_args.args[0]
assert argv[1:3]==['-m','app.worker']
assert Path(native.call_args.kwargs['cwd'])==root
with Registry(work) as db:
    db.initialize(ProjectBrief(project_id='relocated',title='Owned',work_title='Owned',character='Guide'))
    (work/'image.png').write_bytes(b'owned fixture')
    db.register_asset('relocated','image','image.png',kind='image',rights='owned',base_revision=1,idempotency_key='image')
    script=ProductionScript(project_id='relocated',title='Owned',scope='No tools',overlays_enabled=False,
        segments=[ProductionBlock(id='line',title='Line',text='Owned text',visual_asset_id='image')])
    write_generated_json(work,'script.json',script.model_dump())
    db.register_asset('relocated','script','script.json',kind='text',rights='owned',base_revision=2,idempotency_key='script')
write_generated_json(work,'runtime.json',{'tts_python':sys.executable,'tts_model':sys.executable,'tts_engine':'piper'})
receipt=manager.enqueue('relocated','synthesize',{'script_asset_id':'script'},3,'relocated_key',start=False)
class StopBeforeNative(Exception):pass
with patch.object(Context,'run',side_effect=StopBeforeNative) as native:
    try:synthesize(Context(work,receipt['job_id']),{'script_asset_id':'script'})
    except StopBeforeNative:pass
    else:raise AssertionError('Native boundary not reached')
assert Path(native.call_args.args[0][1])==root/'scripts/synthesize_script.py'
project=work/'project.mlt';project.write_text('<mlt/>')
compiled={'project_mlt':str(project),'project_sha256':content_hash(project),'file_hashes':{}}
with patch('scripts.render_project.subprocess.run',side_effect=StopBeforeNative) as native:
    try:ProductionBridge(work).render(compiled,work/'never_created.mp4',qmelt=sys.executable)
    except StopBeforeNative:pass
    else:raise AssertionError('Renderer boundary not reached')
assert '-consumer' in native.call_args.args[0]
assert native.call_args.kwargs['timeout']==180
assert not (work/'never_created.mp4').exists()
print(json.dumps({'status':'passed','isolated':True,'worker_cwd':str(root),'native_tools_started':0}))
'''


class SourcePackageTests(unittest.TestCase):
    def test_actual_zip_relocates_internal_worker_scripts_and_rejects_modified_or_missing_source(self):
        repo=Path(__file__).resolve().parents[1]
        with tempfile.TemporaryDirectory(prefix='closure_source_zip_') as temp:
            folder=Path(temp);archive=folder/'sources.zip'
            result=build(repo,archive)
            extracted=folder/'relocated';extracted.mkdir()
            with zipfile.ZipFile(archive) as zip_file:zip_file.extractall(extracted)
            self.assertEqual(verify(extracted)['source_tree'],result['source_tree'])
            child=subprocess.run([sys.executable,'-I','-B','-X','utf8','-c',CHILD,str(extracted),str(folder/'run')],
                cwd=folder,capture_output=True,text=True,encoding='utf-8',timeout=30)
            self.assertEqual(child.returncode,0,child.stdout+child.stderr)
            self.assertEqual(json.loads(child.stdout)['native_tools_started'],0)
            # Conventional installations and exact profile references are external,
            # optional to source verification, and never copied into the ZIP.
            dependencies=external_dependencies(extracted)
            for relative in dependencies['files']:
                path=extracted/relative;path.parent.mkdir(parents=True,exist_ok=True)
                path.write_bytes(b'fixture dependency, never used as production proof')
            for relative in dependencies['install_roots']:
                path=extracted/relative;path.mkdir(parents=True,exist_ok=True)
                (path/'fixture.bin').write_bytes(b'not an executable')
            self.assertEqual(verify(extracted)['external_dependencies'],dependencies)
            def unreadable(*args,**kwargs):kwargs['onerror'](PermissionError('fixture enumeration denied'))
            with patch('app.v2_source_package.os.walk',side_effect=unreadable):
                with self.assertRaises(StudioError):verify(extracted)
            for relative in ('app/extra.py','scripts/extra.py','config/extra.json','models/unknown.onnx',
                             'workspace/studio/registry.sqlite','tools/Other/extra.bin'):
                path=extracted/relative;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(b'unlisted')
                with self.assertRaises(StudioError):verify(extracted)
                path.unlink()
                if relative.startswith('tools/Other/'):path.parent.rmdir()
            manifest=extracted/MANIFEST;original_manifest=manifest.read_bytes()
            changed=json.loads(original_manifest);changed['external_dependencies']['install_roots'].append('app')
            manifest.write_text(json.dumps(changed),encoding='utf-8')
            with self.assertRaises(StudioError):verify(extracted)
            manifest.write_bytes(original_manifest)
            script=extracted/'scripts/synthesize_script.py';original=script.read_bytes();script.write_bytes(original+b'\n# changed\n')
            with self.assertRaises(StudioError):verify(extracted)
            script.write_bytes(original);script.unlink()
            with self.assertRaises(StudioError):verify(extracted)
