"""Real cuts within one narration block, exact timing, and honest review rights."""
from copy import deepcopy
import json
import math
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
import xml.etree.ElementTree as ET

from pydantic import ValidationError

from app.contracts import ProjectBrief, Timeline
from app.operations import ProductionScript, allocate_shot_frames, build, package, read_script, render_thread_count, video_encoder
from app.registry import Registry, StudioError, content_hash
from app.timeline import build_timeline
from scripts.render_project import render_project
from tests.test_timeline import available_renderer


class MultiShotContractTests(unittest.TestCase):
    def test_allocation_covers_audio_without_dropped_or_zero_length_shots(self):
        for frames,weights in ((137,[1.,1.,1.]),(9001,[2.,.5,4.,1.]),(91,[1e308,1e308,1e-100])):
            result=allocate_shot_frames(frames,weights)
            self.assertEqual(sum(result),frames)
            self.assertTrue(all(value>=1 for value in result))
        self.assertEqual(allocate_shot_frames(300,[1.,1.,1.]),[100,100,100])
        for frames,weights in ((1,[1.,1.]),(100,[]),(100,[float('inf')]),(100,[0.])):
            with self.assertRaises(StudioError):allocate_shot_frames(frames,weights)

    def test_old_and_new_scripts_validate_without_an_unused_fallback(self):
        base={'project_id':'demo','title':'Demo','scope':'Test','segments':[
            {'id':'block','text':'Narration','title':'','visual_asset_id':'first'}]}
        self.assertEqual(ProductionScript.model_validate(base).segments[0].shots()[0].asset_id,'first')
        base['segments'][0].pop('visual_asset_id')
        base['segments'][0]['visual_shots']=[{'asset_id':'first','weight':1.},{'asset_id':'second','weight':2.}]
        self.assertEqual(len(ProductionScript.model_validate(base).segments[0].shots()),2)
        for bad in (0.,-1.,float('nan'),float('inf')):
            data=deepcopy(base);data['segments'][0]['visual_shots'][0]['weight']=bad
            with self.assertRaises(ValidationError):ProductionScript.model_validate(data)

    def test_encoder_remains_opt_in_and_allowlisted(self):
        self.assertEqual(video_encoder({}),'libx264')
        self.assertEqual(video_encoder({'video_encoder':'h264_nvenc'}),'h264_nvenc')
        with self.assertRaises(StudioError):video_encoder({'video_encoder':'arbitrary'})

    def test_render_threads_default_and_invalid_drop_mode_settings(self):
        self.assertEqual(render_thread_count({}),1)
        self.assertEqual(render_thread_count({'render_threads':4}),4)
        for value in (0,-4,True,'4',17):
            with self.assertRaises(StudioError):render_thread_count({'render_threads':value})

    def test_timeline_rejects_frame_gaps_and_escapes(self):
        timeline={'project_id':'demo','revision':1,'fps':30,'width':640,'height':360,'music':'music.wav',
                  'segments':[{'id':'one','text':'One','title':'','visual':'a.png','audio':'voice.wav','duration':3.,
                               'visual_shots':[{'visual':'a.png','frames':30},{'visual':'b.png','frames':60}]}]}
        Timeline.model_validate(timeline)
        for replacement in ({'visual':'b.png','frames':59},{'visual':'../b.png','frames':60}):
            data=deepcopy(timeline);data['segments'][0]['visual_shots'][1]=replacement
            with self.assertRaises(ValidationError):Timeline.model_validate(data)


class ReviewRightsTests(unittest.TestCase):
    def setUp(self):
        self.temporary=tempfile.TemporaryDirectory(prefix='studio_review_')
        self.addCleanup(self.temporary.cleanup)
        self.root=Path(self.temporary.name)
        (self.root/'image.png').write_bytes(b'fixture image')
        (self.root/'sources.txt').write_text('Selected source research notes',encoding='utf-8')
        self.base={'project_id':'review','title':'Review','scope':'Selected sources, not full-source analysis',
                   'segments':[{'id':'one','text':'A checked event.','title':'','interpretation':False,
                                'source_asset_ids':['sources'],
                                'visual_shots':[{'asset_id':'image'}]}]}
        with Registry(self.root) as registry:
            registry.initialize(ProjectBrief(project_id='review',title='Review',work_title='Test',character='Test',
                                              milestone='editorial_review',target_duration_seconds=600,source_status='partial'))
            registry.register_asset('review','image','image.png',kind='image',rights='unknown',base_revision=1,idempotency_key='image')
            registry.register_asset('review','sources','sources.txt',kind='text',rights='owned',base_revision=2,idempotency_key='sources')

    def read_variant(self,data):
        with Registry(self.root) as registry:
            revision=registry.status('review')['revision'];identifier=f'script_{revision}'
            path=self.root/f'{identifier}.json';path.write_text(json.dumps(data),encoding='utf-8')
            registry.register_asset('review',identifier,path.name,kind='text',rights='owned',base_revision=revision,idempotency_key=identifier)
            return read_script(registry,'review',identifier)

    def test_unknown_rights_need_explicit_review_and_credit_and_are_never_upgraded(self):
        with self.assertRaises(StudioError) as caught:self.read_variant(self.base)
        self.assertEqual(caught.exception.code,'REVIEW_SCOPE_REQUIRED')
        data={**deepcopy(self.base),'local_review':True}
        with self.assertRaises(StudioError) as caught:self.read_variant(data)
        self.assertEqual(caught.exception.code,'RIGHTS_UNCONFIRMED')
        data['review_sources']=[{'asset_id':'image','source_url':'https://example.org/official/episode','attribution':'Original credited animation owner'}]
        self.read_variant(data)
        with Registry(self.root) as registry:self.assertEqual(registry.asset('review','image')['rights'],'unknown')
        data['segments'][0]['source_asset_ids']=[]
        with self.assertRaises(StudioError) as caught:self.read_variant(data)
        self.assertEqual(caught.exception.code,'SOURCE_REQUIRED')

    def test_blank_or_non_web_credit_is_not_a_review_source(self):
        for url,credit in (('file:///secret','Owner'),('https://example.org','  '),('https://user:password@example.org','Owner')):
            data={**deepcopy(self.base),'local_review':True,'review_sources':[{'asset_id':'image','source_url':url,'attribution':credit}]}
            with self.assertRaises(ValidationError):self.read_variant(data)

    def test_full_episode_gate_survives_local_review(self):
        data={**deepcopy(self.base),'local_review':True,'review_sources':[{'asset_id':'image','source_url':'https://example.org','attribution':'Owner'}]}
        # Interpretation avoids unrelated claim setup; source coverage must still block the full episode build.
        data['segments'][0]['interpretation']=True
        with Registry(self.root) as registry:
            brief=json.loads(registry._project('review')['brief']);brief['milestone']='full_episode'
            registry.db.execute('UPDATE projects SET brief=? WHERE id=?',(json.dumps(brief),'review'))
        script,asset=self.read_variant(data)
        manifest=self.root/'audio_manifest.json';manifest.write_text(json.dumps({'segments':[]}),encoding='utf-8')
        class Context:
            root=self.root
            job={'project_id':'review'}
            def parent(self,identifier,operation):
                return {'manifest':manifest.name,'manifest_sha256':content_hash(manifest),'script_sha256':asset['sha256']}
        with self.assertRaises(StudioError) as caught:build(Context(),{'script_asset_id':asset['id'],'audio_job_id':'fixture'})
        self.assertEqual(caught.exception.code,'SOURCE_INCOMPLETE')


class MultiShotNativeTests(unittest.TestCase):
    @unittest.skipUnless(available_renderer(),'Official Shotcut portable and FFmpeg are needed')
    def test_measured_45_second_block_builds_ten_shots_and_carries_unknown_rights(self):
        with tempfile.TemporaryDirectory(prefix='studio_build_cuts_') as folder:
            root=Path(folder)
            for name,color in (('a','red'),('b','blue')):
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color={color}:s=320x180',
                                '-frames:v','1','-update','1',str(root/f'{name}.png')],check=True)
            subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i','sine=frequency=440:duration=45',str(root/'voice.wav')],check=True)
            (root/'notes.txt').write_text('A factual selected-source research note',encoding='utf-8')
            script={'project_id':'review','title':'Measured shot build','scope':'Selected-source review',
                    'local_review':True,'title_style':'lower_third',
                    'review_sources':[{'asset_id':name,'source_url':f'https://example.org/{name}','attribution':'Original visual owner'} for name in ('a','b')],
                    'segments':[{'id':'one','text':'One continuous narration.','title':'A small caption',
                                 'interpretation':False,'source_asset_ids':['notes'],
                                 'visual_shots':[{'asset_id':'a' if i%2==0 else 'b','motion':'static','label':f'Shot {i+1}'} for i in range(10)]}]}
            (root/'script.json').write_text(json.dumps(script),encoding='utf-8')
            with Registry(root) as registry:
                registry.initialize(ProjectBrief(project_id='review',title='Review',work_title='Test',character='Test',
                                                  milestone='editorial_review',target_duration_seconds=600,
                                                  width=320,height=180,source_status='partial'))
                for revision,(name,kind,path,rights) in enumerate((('a','image','a.png','unknown'),('b','image','b.png','unknown'),
                           ('notes','text','notes.txt','owned'),('script','text','script.json','owned')),1):
                    registry.register_asset('review',name,path,kind=kind,rights=rights,base_revision=revision,idempotency_key=name)
                asset=registry.asset('review','script')
            manifest=root/'audio_manifest.json'
            manifest.write_text(json.dumps({'segments':[{'block_id':'one','path':'voice.wav','duration_seconds':45.,
                                                         'wav_hash':content_hash(root/'voice.wav')}]}),encoding='utf-8')
            class Context:
                config={}
                job={'project_id':'review','input_revision':5,'attempts':1}
                def parent(self,identifier,operation):
                    return {'manifest':manifest.name,'manifest_sha256':content_hash(manifest),'script_sha256':asset['sha256']}
                def progress(self,*args):pass
                def relative(self,path):return Path(path).resolve().relative_to(self.root).as_posix()
                def tool(self,name):return shutil.which(name.removesuffix('_path'))
                def run(self,args,**kwargs):subprocess.run(args,check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            ctx=Context();ctx.root=root;ctx.directory=root/'job';ctx.directory.mkdir()
            result=build(ctx,{'script_asset_id':'script','audio_job_id':'fixture'})
            timeline=json.loads((root/result['timeline_json']).read_text(encoding='utf-8'))
            self.assertEqual(len(timeline['segments']),1)
            shots=timeline['segments'][0]['visual_shots']
            self.assertEqual(len(shots),10)
            self.assertEqual(sum(shot['frames'] for shot in shots),1358)
            self.assertTrue(all(3<=shot['frames']/30<=6 for shot in shots))
            tree=ET.parse(root/result['project_mlt'])
            self.assertEqual(len(tree.findall(".//playlist[@id='visuals']/entry")),10)
            self.assertEqual(len(tree.findall(".//playlist[@id='narrator']/entry")),1)
            directory=(root/result['project_mlt']).parent
            for relative,expected in result['file_hashes'].items():self.assertEqual(content_hash(directory/relative),expected)
            for visual in {shot['visual'] for shot in shots}:
                self.assertTrue((directory/visual).is_file())
                subprocess.run(['ffmpeg','-v','error','-xerror','-i',str(directory/visual),'-f','null','-'],check=True,stdout=subprocess.PIPE,stderr=subprocess.PIPE)
            self.assertEqual([entry['rights'] for entry in result['assets']],['unknown','unknown'])
            provenance=json.loads((directory/'review_sources.json').read_text(encoding='utf-8'))
            self.assertTrue(provenance['local_review'])
            self.assertFalse(provenance['publication_allowed'])
            self.assertEqual(len(provenance['visual_sources']),2)
            # Packaging uses a real native export. The QA receipt is a controlled test fixture.
            export=render_project(directory/'project.mlt',root/'rendered.mp4',preset='ultrafast',timeout=90)
            video_path=Path(export['output'])
            qa_path=root/'qa_fixture.json'
            qa_path.write_text(json.dumps({'schema_version':'1.0','project_id':'review','build_id':'build_fixture',
                'build_sha256':content_hash(video_path),'status':'ready_for_review','checks':[],
                'limitations':['Controlled test receipt; no human review performed.']}),encoding='utf-8')
            # The ASR fixture exercises only portable copying, never a speech-quality verdict.
            speech_source=root/'speech_source.json';subtitle_source=root/'source.srt';raw_source=root/'source_asr.srt'
            for caption in (subtitle_source,raw_source):
                caption.write_text('1\n00:00:00,000 --> 00:00:02,000\nOne continuous narration.\n',encoding='utf-8')
            dll_path=str(root/'machine_specific'/'torch'/'lib')
            speech_source.write_text(json.dumps({'device':'cuda','compute_type':'int8_float16','dll_directories':[dll_path],
                'segments':[{'block_id':'one','path':str(root/'voice.wav')}],
                'subtitles':{'path':str(subtitle_source)},'raw_subtitles':{'path':str(raw_source)}}),encoding='utf-8')
            source_speech_hash=content_hash(speech_source)
            parents={'qa':{'report':qa_path.name,'report_sha256':content_hash(qa_path),'render_job_id':'render_fixture',
                           'subtitles':subtitle_source.name,'raw_subtitles':raw_source.name,'asr_report':speech_source.name,
                           'artifact_hashes':{p.name:content_hash(p) for p in (speech_source,subtitle_source,raw_source)}},
                     'render':{'video':video_path.name,'sha256':content_hash(video_path),'build_job_id':'build_fixture'},
                     'build':result,
                     'synthesize':{'manifest':manifest.name,'manifest_sha256':content_hash(manifest),'script_sha256':asset['sha256']}}
            ctx.parent=lambda identifier,operation:parents[operation]
            ctx.directory=root/'package_job';ctx.directory.mkdir()
            packaged=package(ctx,{'qa_job_id':'qa_fixture'})
            package_dir=root/packaged['package_directory']
            manifest_data=json.loads((package_dir/'manifest.json').read_text(encoding='utf-8'))
            for relative,expected in manifest_data['files'].items():self.assertEqual(content_hash(package_dir/relative),expected)
            for shot in shots:self.assertTrue((package_dir/shot['visual']).is_file())
            self.assertTrue((package_dir/'review_sources.json').is_file())
            packaged_provenance=json.loads((package_dir/'provenance.json').read_text(encoding='utf-8'))
            self.assertEqual(len(packaged_provenance['visual_sources']),2)
            self.assertTrue(all(item['rights']=='unknown' for item in packaged_provenance['visuals']))
            self.assertIn('do not publish',packaged_provenance['publication'])
            self.assertEqual(json.loads((package_dir/'sources.json').read_text(encoding='utf-8'))['source_analysis_status'],'partial')
            portable_speech=json.loads((package_dir/'speech_qa.json').read_text(encoding='utf-8'))
            self.assertNotIn('dll_directories',portable_speech)
            self.assertNotIn(dll_path,json.dumps(portable_speech))
            self.assertEqual(portable_speech['device'],'cuda')
            self.assertEqual(portable_speech['segments'][0]['path'],'audio/one.wav')
            self.assertEqual(content_hash(speech_source),source_speech_hash)
            self.assertEqual(json.loads(speech_source.read_text(encoding='utf-8'))['dll_directories'],[dll_path])

    @unittest.skipUnless(available_renderer(),'Official Shotcut portable and FFmpeg are needed')
    def test_three_visible_cuts_share_one_continuous_voice_and_survive_portable_render(self):
        with tempfile.TemporaryDirectory(prefix='studio_cuts_') as folder:
            root=Path(folder);source=root/'source';source.mkdir()
            for name,color in (('a','red'),('b','lime'),('c','blue')):
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'color={color}:s=640x360',
                                '-frames:v','1','-update','1',str(source/f'{name}.png')],check=True)
            for name,frequency in (('voice',440),('music',220)):
                subprocess.run(['ffmpeg','-v','error','-f','lavfi','-i',f'sine=frequency={frequency}:duration=3',
                                str(source/f'{name}.wav')],check=True)
            segment={'id':'one','text':'One uninterrupted narration','title':'','visual':'a.png','audio':'voice.wav','duration':3.,
                     'visual_shots':[{'visual':'a.png','frames':30,'label':'Opening'},
                                     {'visual':'b.png','frames':45,'label':'Middle'},
                                     {'visual':'c.png','frames':15,'label':'Ending'}]}
            result=build_timeline([segment],'music.wav',root/'build',base_dir=source,width=640,height=360,title_style='lower_third')
            tree=ET.parse(result['project_mlt'])
            self.assertEqual(len(tree.findall(".//playlist[@id='narrator']/entry")),1)
            self.assertEqual(len(tree.findall(".//playlist[@id='visuals']/entry")),3)
            self.assertEqual(len(tree.findall(".//playlist[@id='titles']/entry")),1)
            self.assertEqual(tree.find(".//filter/property[@name='size']").text,'12')
            export=render_project(result['project_mlt'],preset='ultrafast',timeout=90)
            for at,channel in ((.5,0),(1.75,1),(2.75,2)):
                pixel=subprocess.check_output(['ffmpeg','-v','error','-ss',str(at),'-i',export['output'],'-frames:v','1',
                                               '-vf','scale=1:1','-pix_fmt','rgb24','-f','rawvideo','-'])
                self.assertGreater(pixel[channel],180)
                self.assertLess(max(value for index,value in enumerate(pixel) if index!=channel),60)
            probe=json.loads(subprocess.check_output(['ffprobe','-v','error','-show_streams','-show_format','-of','json',export['output']]))
            video=next(stream for stream in probe['streams'] if stream['codec_type']=='video')
            self.assertEqual(int(video['nb_frames']),90)
            self.assertAlmostEqual(float(probe['format']['duration']),3.,delta=.04)
            self.assertTrue(any(stream['codec_type']=='audio' for stream in probe['streams']))
            moved=root/'Перенос монтажа';shutil.copytree(root/'build',moved)
            reexport=render_project(moved/'project.mlt',moved/'copied.mp4',preset='ultrafast',timeout=90)
            def hashes(path):
                return subprocess.check_output(['ffmpeg','-v','error','-i',str(path),'-map','0','-f','framemd5','-'])
            self.assertEqual(hashes(export['output']),hashes(reexport['output']))


if __name__=='__main__':unittest.main()
