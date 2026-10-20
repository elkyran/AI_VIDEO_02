"""Bounded native fitting, immutable inputs, and optional real Piper smoke."""
from copy import deepcopy
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
import wave

from scripts.fit_narration import (digest, fit_block, fit_narration, fixed_silence,
                                   measure_wav, validate_inputs)


def wav(path, seconds, rate=1000):
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), 'wb') as output:
        output.setnchannels(1)
        output.setsampwidth(2)
        output.setframerate(rate)
        output.writeframes(b'\x01\x01' * round(seconds * rate))


def dump(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding='utf-8')


class FitTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.output = self.root / 'new'
        self.output.mkdir()
        self.text = 'Первая реплика. Все вернулись домой.'
        self.source = self.root / 'source.wav'
        wav(self.source, 8)
        self.record = {'block_id': 'b01', 'id': 'audio_b01', 'text': self.text,
            'text_hash': hashlib.sha256(self.text.encode()).hexdigest(), 'path': 'source.wav',
            'wav_hash': digest(self.source), 'duration_seconds': 8, 'engine': 'piper',
            'engine_version': 'test', 'voice_id': 'ruslan', 'model_hash': 'model',
            'model_config_hash': 'config', 'use_cuda': False, 'request_hash': 'source-request',
            'sample_rate_hz': 1000, 'frames': 8000,
            'synthesis_chunks': [{'generated_samples': 7720}],
            'generation_settings': {'length_scale': 1.08, 'leading_silence': .1,
                                    'trailing_silence': .18, 'sentence_silence': .08}}
        self.item = {'record': self.record, 'request': {'text': self.text,
            'config': {'length_scale': 1.08, 'workspace_root': str(self.root)}},
            'source': self.source, 'source_request': 'jobs/source/b01.request.json',
            'source_request_sha256': 'request-sha', 'slot': 10., 'start_frame': 0, 'frames': 300}

    def synthesizer(self, duration_function, calls):
        def generate(text, output, config):
            calls.append((text, deepcopy(config)))
            duration = duration_function(config['length_scale'])
            wav(output, duration)
            duration = measure_wav(output)
            result = deepcopy(self.record)
            result.update(path=str(output), duration_seconds=duration, frames=round(duration*1000),
                          wav_hash=digest(output), request_hash='new-request')
            result['generation_settings']['length_scale'] = config['length_scale']
            result['synthesis_chunks'] = [{'generated_samples': round(duration*1000)-280}]
            return result
        return generate

    def test_native_scale_fits_without_touching_source_or_text(self):
        calls = []
        before = self.source.read_bytes()
        result = fit_block(self.root, self.output, self.item,
                           self.synthesizer(lambda scale: .28 + 7.72*scale/1.08, calls))
        self.assertTrue(.25 <= result['remaining_slot_seconds'] <= .55)
        self.assertEqual(result['samples_trimmed'], 0)
        self.assertFalse(result['pitch_shifted'])
        self.assertEqual(self.source.read_bytes(), before)
        self.assertEqual(calls[0][0], self.text)
        self.assertEqual(set(calls[0][1]), set(self.item['request']['config']))
        self.assertEqual(result['original_visual_frames'], 300)

    def test_baseline_with_small_gap_is_reused_without_synthesis(self):
        self.item['slot'] = 8.5
        result = fit_block(self.root, self.output, self.item,
                           lambda *args: self.fail('A fitting baseline must remain unchanged'))
        self.assertEqual(result['path'], 'source.wav')
        self.assertEqual(result['wav_hash'], digest(self.source))
        self.assertEqual(result['fitting_attempt'], 0)

    def test_native_failure_is_bounded_and_never_trims_to_slot(self):
        self.item['slot'] = 6.
        calls = []
        before = self.source.read_bytes()
        with self.assertRaisesRegex(ValueError, 'not trimmed'):
            fit_block(self.root, self.output, self.item,
                      self.synthesizer(lambda scale: 8., calls))
        self.assertLessEqual(len(calls), 3)
        self.assertTrue(all(.85 <= c[1]['length_scale'] <= 1.4 for c in calls))
        self.assertEqual(self.source.read_bytes(), before)
        self.assertFalse((self.output/'audio_manifest.json').exists())
        for path in self.output.glob('*.wav'):
            self.assertEqual(measure_wav(path), 8.)

    def test_measured_nonlinear_retry_chooses_closest_fitting_attempt(self):
        calls = []
        durations = iter([10.1, 9.7])
        result = fit_block(self.root, self.output, self.item,
                          self.synthesizer(lambda scale: next(durations), calls))
        self.assertEqual(len(calls), 2)
        self.assertAlmostEqual(result['duration_seconds'], 9.7)
        self.assertEqual(result['fitting_attempt'], 2)

    def test_rejects_unexpected_voice_or_setting_change(self):
        calls = []
        worker = self.synthesizer(lambda scale: 9.65, calls)
        def changed(text, output, config):
            result = worker(text, output, config)
            result['generation_settings']['sentence_silence'] = .9
            return result
        with self.assertRaisesRegex(ValueError, 'besides length_scale'):
            fit_block(self.root, self.output, self.item, changed)

    def test_exact_chunk_silence_accounting(self):
        record = {'frames': 1123, 'sample_rate_hz': 1000,
                  'synthesis_chunks': [{'generated_samples': 400}, {'generated_samples': 300}]}
        self.assertEqual(fixed_silence(record), (.423, 'measured_chunk_accounting'))


class ValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name).resolve()
        self.model = self.root/'model.onnx'; self.model.write_bytes(b'model')
        self.config = self.root/'model.onnx.json'; self.config.write_text('{}')
        self.audio = self.root/'cache/voice.wav'; wav(self.audio, 1)
        text = 'Все вернулись домой.'
        self.record = {'block_id':'b01','text':text,'text_hash':hashlib.sha256(text.encode()).hexdigest(),
                       'path':'cache/voice.wav','wav_hash':digest(self.audio),'duration_seconds':1.,
                       'engine':'piper','model_hash':digest(self.model),'model_config_hash':digest(self.config),
                       'generation_settings':{'length_scale':1.08}}
        self.script = {'segments':[{'id':'b01','text':text}]}
        dump(self.root/'script.json',self.script)
        self.script_sha = digest(self.root/'script.json')
        self.manifest = {'project_id':'p','script_asset_id':'s','script_sha256':self.script_sha,
                         'segments':[self.record]}
        dump(self.root/'jobs/a/audio_manifest.json',self.manifest)
        self.request = {'text':text,'output':str(self.audio), 'config':{
            'workspace_root':str(self.root),'model_path':str(self.model),'length_scale':1.08}}
        dump(self.root/'jobs/a/b01.request.json',self.request)
        timeline = {'project_id':'p','fps':30,'segments':[{'id':'b01','text':text,'duration':1.5,
                   'visual_shots':[{'frames':45}]}]}
        dump(self.root/'build/timeline.json',timeline)
        (self.root/'build/project.mlt').write_text('<mlt/>')
        self.audio_result = {'manifest':'jobs/a/audio_manifest.json','manifest_sha256':digest(self.root/'jobs/a/audio_manifest.json'),
                             'script_asset_id':'s','script_sha256':self.script_sha}
        self.build_result = {'timeline_json':'build/timeline.json','file_hashes':{'timeline.json':digest(self.root/'build/timeline.json')},
                             'project_mlt':'build/project.mlt','project_sha256':digest(self.root/'build/project.mlt'),
                             'script_asset_id':'s','script_sha256':self.script_sha}
        with closing(sqlite3.connect(self.root/'registry.sqlite')) as db:
            db.executescript('CREATE TABLE jobs (id TEXT, operation TEXT,state TEXT,project_id TEXT,result TEXT);'
                             'CREATE TABLE assets (project_id TEXT,id TEXT,path TEXT,sha256 TEXT);')
            db.execute('INSERT INTO assets VALUES (?,?,?,?)',('p','s','script.json',self.script_sha))
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',('a','synthesize','done','p',json.dumps(self.audio_result)))
            db.execute('INSERT INTO jobs VALUES (?,?,?,?,?)',('b','build','done','p',json.dumps(self.build_result)))
            db.commit()

    def test_matching_jobs_validate_and_count_exact_frames(self):
        _, _, items, source = validate_inputs(self.root,'a','b')
        self.assertEqual(items[0]['slot'],1.5)
        self.assertEqual(source['total_visual_frames'],45)

    def test_modified_manifest_or_request_text_is_rejected(self):
        self.request['text']='Другие слова.'
        dump(self.root/'jobs/a/b01.request.json',self.request)
        with self.assertRaisesRegex(ValueError,'request text hash'):
            validate_inputs(self.root,'a','b')
        dump(self.root/'jobs/a/audio_manifest.json',{**self.manifest,'extra':True})
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            validate_inputs(self.root,'a','b')

    def test_changed_timeline_or_wrong_job_state_is_rejected(self):
        (self.root/'build/timeline.json').write_text('{}')
        with self.assertRaisesRegex(ValueError,'timeline hash'):
            validate_inputs(self.root,'a','b')
        with closing(sqlite3.connect(self.root/'registry.sqlite')) as db:
            db.execute("UPDATE jobs SET state='running' WHERE id='a'")
            db.commit()
        with self.assertRaisesRegex(ValueError,'completed synthesize'):
            validate_inputs(self.root,'a','b')

    def test_output_escape_and_existing_directory_are_rejected(self):
        for path in ('../escape',self.root.parent/'escape',self.root/'cache'):
            with self.subTest(path=path), self.assertRaises(ValueError):
                fit_narration(self.root,'a','b',path)

    def test_source_wav_or_model_change_is_rejected(self):
        self.model.write_bytes(b'changed-model')
        with self.assertRaisesRegex(ValueError,'model/config hash'):
            validate_inputs(self.root,'a','b')
        self.audio.write_bytes(b'changed-wav')
        with self.assertRaisesRegex(ValueError,'hash mismatch'):
            validate_inputs(self.root,'a','b')


MODEL = Path(__file__).resolve().parents[1]/'models/piper/ru_RU-ruslan-medium.onnx'


@unittest.skipUnless(MODEL.is_file() and importlib.util.find_spec('piper'), 'Requires installed local Piper')
class RealPiperTests(unittest.TestCase):
    def test_native_real_wav_fit_preserves_spoken_request_and_source(self):
        from app.tts import synthesize
        with tempfile.TemporaryDirectory(prefix='native-fit-') as temp:
            root=Path(temp).resolve();output=root/'new';output.mkdir()
            config={'model_path':str(MODEL),'workspace_root':str(root),'length_scale':1.08,
                    'normalization_scope':'block','sentence_silence':.08}
            text='Все вернулись домой.'
            baseline=synthesize(text,root/'source.wav',config)
            baseline.update(block_id='b01',id='audio_b01',text=text)
            before=digest(root/'source.wav')
            slot=baseline['duration_seconds']+.85
            item={'record':baseline,'request':{'text':text,'config':config},'source':root/'source.wav',
                  'source_request':'original.request.json','source_request_sha256':'unused',
                  'slot':slot,'start_frame':0,'frames':round(slot*30)}
            result=fit_block(root,output,item,synthesize)
            self.assertLessEqual(result['duration_seconds'],slot-.2)
            self.assertEqual(result['text_hash'],baseline['text_hash'])
            self.assertEqual(digest(root/'source.wav'),before)
            self.assertEqual(result['samples_trimmed'],0)
            self.assertTrue(any(output.glob('*.attempt_*.wav')))


if __name__=='__main__':
    unittest.main()
