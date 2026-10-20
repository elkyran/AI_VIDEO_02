"""Explicit CPU/CUDA options, live DLL handles, and rejection of fallback."""
import json
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
import wave
import numpy  # Load once before patch.dict(sys.modules) snapshots compiled extensions.

from scripts.transcribe_audio import asr_dll_environment, transcribe_manifest, validate_runtime_options


class ASRRuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(prefix='studio_asr_runtime_');self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        with wave.open(str(self.root/'voice.wav'),'wb') as audio:
            audio.setnchannels(1);audio.setsampwidth(2);audio.setframerate(16000);audio.writeframes(b'\0\0'*32000)
        self.manifest=self.root/'manifest.json'
        self.manifest.write_text(json.dumps({'workspace_root':str(self.root),'segments':[
            {'block_id':'one','path':'voice.wav','text':'Он не ушёл.','duration_seconds':2.}]}),encoding='utf-8')
        self.calls=[]

    def fake_modules(self,actual_device=None,supported=None):
        owner=self
        class Model:
            def __init__(self,name,**kwargs):
                owner.calls.append(kwargs)
                self.model=SimpleNamespace(device=actual_device or kwargs['device'],
                    compute_type='int8_float32' if kwargs['compute_type']=='int8' else kwargs['compute_type'])
            def transcribe(self,path,**kwargs):
                owner.calls.append(kwargs)
                words=[SimpleNamespace(word=word,start=i*.4,end=i*.4+.3,probability=.9) for i,word in enumerate(('Он','не','ушёл.'))]
                return iter([SimpleNamespace(text='Он не ушёл.',words=words)]),None
        return {'faster_whisper':SimpleNamespace(WhisperModel=Model),
                'ctranslate2':SimpleNamespace(get_supported_compute_types=lambda device: supported if supported is not None else {'int8','int8_float16'})}

    def test_cpu_int8_defaults_and_negation_check_remain(self):
        self.assertEqual(validate_runtime_options(),{'device':'cpu','compute_type':'int8','dll_directories':[]})
        with patch.dict('sys.modules',self.fake_modules()):
            report=transcribe_manifest(self.manifest,self.root/'report.json',self.root/'captions.srt')
        self.assertEqual((report['device'],report['compute_type'],report['actual_compute_type']),('cpu','int8','int8_float32'))
        self.assertEqual(report['segments'][0]['expected_negations'],['не'])
        self.assertEqual(report['segments'][0]['recognized_negations'],['не'])
        self.assertTrue(self.calls[0]['local_files_only'])
        self.assertEqual(self.calls[1]['beam_size'],5)
        self.assertTrue(self.calls[1]['word_timestamps'])

    def test_explicit_cuda_int8_float16_and_no_cpu_fallback(self):
        with patch.dict('sys.modules',self.fake_modules()):
            report=transcribe_manifest(self.manifest,self.root/'report.json',self.root/'captions.srt',
                                        device='cuda',compute_type='int8_float16')
        self.assertEqual((report['device'],report['compute_type']),('cuda','int8_float16'))
        self.assertEqual(self.calls[0]['device'],'cuda')
        for modules in (self.fake_modules(supported={'int8'}),self.fake_modules(actual_device='cpu')):
            with self.subTest(modules=modules),patch.dict('sys.modules',modules),self.assertRaises(ValueError):
                transcribe_manifest(self.manifest,self.root/'bad.json',self.root/'bad.srt',device='cuda',compute_type='int8_float16')

    def test_auto_device_precision_and_invalid_dll_paths_are_rejected(self):
        for changes in ({'device':'auto'},{'compute_type':'auto'},{'dll_directories':['relative/path']},
                        {'dll_directories':[str(self.root/'missing')]},{'dll_directories':'a string'}):
            with self.subTest(changes=changes),self.assertRaises(ValueError):validate_runtime_options(**changes)

    @unittest.skipUnless(os.name=='nt','Windows loader lifetime test')
    def test_dll_handles_stay_live_and_environment_is_restored_after_errors(self):
        closed=[];previous=os.environ.get('PATH')
        handles=[SimpleNamespace(close=lambda:closed.append('closed'))]
        with patch('os.add_dll_directory',return_value=handles[0]) as add:
            with self.assertRaises(RuntimeError):
                with asr_dll_environment([str(self.root)]):
                    self.assertFalse(closed)
                    self.assertTrue(os.environ['PATH'].startswith(str(self.root)+os.pathsep))
                    raise RuntimeError('Inference failed')
            add.assert_called_once_with(str(self.root))
        self.assertEqual(closed,['closed']);self.assertEqual(os.environ.get('PATH'),previous)


if __name__=='__main__':unittest.main()
