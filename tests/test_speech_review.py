"""Speech-cache identity, tamper rejection, paths and overlapping callers."""
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch
import wave

from app.registry import StudioError, content_hash
from app.speech_review import review_build_speech
from scripts.transcribe_audio import validate_runtime_options


class SpeechCacheTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='studio_speech_cache_')
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.directory=self.root/'caller';self.directory.mkdir()
        with wave.open(str(self.root/'voice.wav'),'wb') as sound:
            sound.setnchannels(1);sound.setsampwidth(2);sound.setframerate(16000);sound.writeframes(b'\0\0'*32000)
        (self.root/'project.mlt').write_text('<mlt/>',encoding='utf-8')
        self.timeline={'segments':[{'id':'one','text':'Рокси рядом.','duration':2.24}]}
        self.manifest={'schema_version':'1.0','project_id':'demo','segments':[{'block_id':'one','id':'audio_one',
            'path':'voice.wav','text':'Рокси рядом.','duration_seconds':2.,'wav_hash':content_hash(self.root/'voice.wav')}]}
        self.audio={'manifest':'audio_manifest.json','script_sha256':'a'*64}
        self.build={'project_mlt':'project.mlt','project_sha256':content_hash(self.root/'project.mlt'),
                    'timeline_json':'timeline.json','audio_job_id':'audio_job','script_sha256':'a'*64}
        self.refresh_inputs()
        self.identity={'name':'medium','files':{'model.bin':'b'*64}}
        self.model_patch=patch('app.speech_review._model_identity',side_effect=lambda ctx:deepcopy(self.identity))
        self.model_patch.start();self.addCleanup(self.model_patch.stop)
        owner=self
        class Context:
            root=owner.root
            directory=owner.directory
            config={'asr_python':sys.executable,'asr_model':'medium','pronunciation_terms':['Рокси']}
            calls=0
            delay=0
            def tool(self,name):return sys.executable
            def parent(self,identifier,operation):return owner.audio
            def run(self,args,**kwargs):
                self.calls+=1
                if self.delay:time.sleep(self.delay)
                data=json.loads(Path(args[args.index('--manifest')+1]).read_text(encoding='utf-8'))
                report=Path(args[args.index('--output')+1]);subtitle=Path(args[args.index('--subtitles')+1])
                raw=subtitle.with_name(subtitle.stem+'_asr.srt')
                for path in (subtitle,raw):path.write_text('1\n00:00:00,000 --> 00:00:01,500\nРокси рядом.\n',encoding='utf-8')
                rows=[{'block_id':entry['block_id'],'path':entry['path'],'text':entry['text'],'start_seconds':entry['start_seconds'],
                       'expected_negations':[],'recognized_negations':[],'wer':.25,'warnings':['critical_terms_mismatch']} for entry in data['segments']]
                execution=validate_runtime_options(self.config.get('asr_device','cpu'),
                    self.config.get('asr_compute_type','int8'),self.config.get('asr_dll_directories',[]))
                report.write_text(json.dumps({'model':self.config['asr_model'],'status':'automated_warning','segments':rows,
                    **execution,
                    'subtitles':{'path':str(subtitle)},'raw_subtitles':{'path':str(raw)}}),encoding='utf-8')
        self.ctx=Context()

    def refresh_inputs(self):
        (self.root/'timeline.json').write_text(json.dumps(self.timeline),encoding='utf-8')
        (self.root/'audio_manifest.json').write_text(json.dumps(self.manifest),encoding='utf-8')
        self.audio['manifest_sha256']=content_hash(self.root/'audio_manifest.json')
        self.build['file_hashes']={name:content_hash(self.root/name) for name in ('project.mlt','timeline.json')}

    def run_review(self):return review_build_speech(self.ctx,self.build)

    def test_identical_preflight_is_reused_by_another_caller_and_keeps_warnings(self):
        first=self.run_review();self.assertFalse(first['cache_hit'])
        self.ctx.directory=self.root/'qa';self.ctx.directory.mkdir()
        second=self.run_review();self.assertTrue(second['cache_hit'])
        self.assertEqual(self.ctx.calls,1)
        self.assertEqual(first['report'],second['report'])
        self.assertEqual(json.loads(second['report'].read_text())['status'],'automated_warning')
        self.assertEqual(set(first['artifact_hashes']),{'report','subtitles','raw_subtitles'})

    def test_text_timing_terms_model_and_wave_identity_each_invalidate(self):
        previous=self.run_review()['cache_key']
        mutations=[lambda:self.ctx.config.update(pronunciation_terms=['Рудеус']),
                   lambda:self.timeline['segments'][0].update(duration=2.5),
                   lambda:(self.timeline['segments'][0].update(text='Новый текст.'),self.manifest['segments'][0].update(text='Новый текст.')),
                   lambda:self.identity['files'].update({'model.bin':'c'*64})]
        for mutation in mutations:
            mutation();self.refresh_inputs();current=self.run_review()
            self.assertFalse(current['cache_hit']);self.assertNotEqual(current['cache_key'],previous);previous=current['cache_key']
        with (self.root/'voice.wav').open('ab') as stream:stream.write(b'changed')
        self.manifest['segments'][0]['wav_hash']=content_hash(self.root/'voice.wav');self.refresh_inputs()
        current=self.run_review();self.assertNotEqual(current['cache_key'],previous)

    def test_worker_code_change_invalidate(self):
        first=self.run_review()
        real_hash=content_hash
        def changed_hash(path):
            return 'd'*64 if Path(path).name=='transcribe_audio.py' else real_hash(path)
        with patch('app.speech_review.content_hash',side_effect=changed_hash):second=self.run_review()
        self.assertFalse(second['cache_hit']);self.assertNotEqual(first['cache_key'],second['cache_key'])

    def test_device_compute_and_dll_configuration_are_part_of_the_cache_identity(self):
        previous=self.run_review()['cache_key']
        for changes in ({'asr_device':'cuda'},{'asr_compute_type':'int8_float16'},
                        {'asr_dll_directories':[str(self.root)]}):
            self.ctx.config.update(changes);result=self.run_review()
            self.assertFalse(result['cache_hit']);self.assertNotEqual(result['cache_key'],previous)
            previous=result['cache_key']

    def test_changed_wav_or_audio_manifest_cannot_reuse_old_evidence(self):
        self.run_review()
        with (self.root/'voice.wav').open('ab') as stream:stream.write(b'changed')
        with self.assertRaises(StudioError) as caught:self.run_review()
        self.assertEqual(caught.exception.code,'ASSET_CHANGED');self.assertEqual(self.ctx.calls,1)
        self.manifest['segments'][0]['wav_hash']=content_hash(self.root/'voice.wav');self.refresh_inputs()
        (self.root/'audio_manifest.json').write_text('{}')
        with self.assertRaises(StudioError) as caught:self.run_review()
        self.assertEqual(caught.exception.code,'ASSET_CHANGED')

    def test_each_corrupted_output_is_regenerated_without_overwriting_old_files(self):
        for artifact in ('report','subtitles','raw_subtitles'):
            result=self.run_review();old=result[artifact];old.write_text('corrupted',encoding='utf-8')
            before=self.ctx.calls;fresh=self.run_review()
            self.assertFalse(fresh['cache_hit']);self.assertEqual(self.ctx.calls,before+1)
            self.assertNotEqual(fresh[artifact],old);self.assertEqual(old.read_text(),'corrupted')

    def test_escaping_wav_and_receipt_paths_are_not_followed(self):
        result=self.run_review()
        receipt_path=self.root/'cache'/'speech_review'/result['cache_key']/'receipt.json'
        receipt=json.loads(receipt_path.read_text());receipt['artifacts']['subtitles']['path']='../outside.srt'
        receipt_path.write_text(json.dumps(receipt))
        self.assertFalse(self.run_review()['cache_hit'])
        self.manifest['segments'][0]['path']='../outside.wav';self.refresh_inputs()
        with self.assertRaises(StudioError) as caught:self.run_review()
        self.assertEqual(caught.exception.code,'PATH_UNSAFE')

    def test_mismatched_block_order_or_text_fails_before_asr(self):
        self.manifest['segments'][0]['text']='Different';self.refresh_inputs()
        with self.assertRaises(StudioError) as caught:self.run_review()
        self.assertEqual(caught.exception.code,'STALE_AUDIO');self.assertEqual(self.ctx.calls,0)

    def test_overlapping_preflight_and_qa_only_transcribe_once(self):
        self.ctx.delay=.35;results=[];errors=[]
        def run():
            try:results.append(self.run_review())
            except BaseException as error:errors.append(error)
        threads=[threading.Thread(target=run) for _ in range(2)]
        for thread in threads:thread.start()
        for thread in threads:thread.join(timeout=10)
        self.assertFalse(errors,errors);self.assertEqual(len(results),2)
        self.assertEqual(self.ctx.calls,1)
        self.assertEqual(sorted(item['cache_hit'] for item in results),[False,True])


if __name__=='__main__':unittest.main()
