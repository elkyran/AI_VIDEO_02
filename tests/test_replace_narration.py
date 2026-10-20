"""Audio-only replacement preserves video packets, frames and complete WAV tails."""
from array import array
from copy import deepcopy
import json
import math
from pathlib import Path
import sqlite3
import subprocess
import tempfile
import unittest
import wave
import xml.etree.ElementTree as ET

from app.registry import content_hash, StudioError
from app.timeline import build_timeline
from scripts.replace_narration import (bounded, replace_narration, save_json, stage_project,
                                       validate_narration, verify_sources, run as run_local)
from scripts.render_project import render_project


ROOT = Path(__file__).resolve().parents[1]


def installed_runtime():
    path = ROOT/'workspace/studio/runtime.json'
    if not path.exists():
        return None
    config = json.loads(path.read_text(encoding='utf-8-sig'))
    return config if all(Path(config.get(k, '')).is_file() for k in ('ffmpeg_path','ffprobe_path','melt_path')) else None


def tone(path, duration, frequency, rate=48000, amplitude=8000):
    # A tone continues through the final sample: losing an ending is measurable.
    values = array('h', (round(amplitude*math.sin(2*math.pi*frequency*i/rate)) for i in range(round(duration*rate))))
    with wave.open(str(path), 'wb') as sound:
        sound.setnchannels(1); sound.setsampwidth(2); sound.setframerate(rate)
        sound.writeframes(values.tobytes())


class NarrationValidationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='audio_replacement_')
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        tone(self.root/'new.wav', 1.0, 880)
        self.timeline = {'project_id':'demo', 'fps':30, 'segments':[
            {'id':'a', 'text':'Complete ending.', 'duration':1.2}]}
        self.manifest = {'project_id':'demo', 'segments':[
            {'block_id':'a', 'text':'Complete ending.', 'path':'new.wav',
             'wav_hash':content_hash(self.root/'new.wav'), 'duration_seconds':1.0}]}

    def test_measured_whole_wav_and_slot(self):
        result = validate_narration(self.root, self.manifest, self.timeline)[0]
        self.assertEqual(result['frames'], 48000)
        self.assertEqual(result['slot_frames'], 36)
        self.assertAlmostEqual(result['unused_slot_seconds'], .2)

    def test_reject_changed_wav_or_text_or_block_order(self):
        for key, value in [('wav_hash','0'*64), ('text','Changed story.'), ('block_id','b')]:
            with self.subTest(key=key):
                manifest = deepcopy(self.manifest); manifest['segments'][0][key] = value
                with self.assertRaises(ValueError): validate_narration(self.root, manifest, self.timeline)

    def test_reject_overflow_even_if_only_one_sample(self):
        tone(self.root/'new.wav', 1.2+1/48000, 880)
        self.manifest['segments'][0].update(wav_hash=content_hash(self.root/'new.wav'), duration_seconds=1.2+1/48000)
        with self.assertRaisesRegex(ValueError, 'overflows'):
            validate_narration(self.root, self.manifest, self.timeline)

    def test_reject_wrong_duration_start_or_trimmed_input(self):
        for key, value in [('duration_seconds', .8), ('duration_seconds', float('nan')),
                           ('start_seconds', .1), ('start_seconds',float('nan')), ('samples_trimmed',1)]:
            with self.subTest(key=key, value=value):
                manifest = deepcopy(self.manifest); manifest['segments'][0][key] = value
                with self.assertRaises(ValueError): validate_narration(self.root, manifest, self.timeline)

    def test_reject_incomplete_block_list_and_project(self):
        for change in [{'segments':[]}, {'project_id':'other'}, {'segments':self.manifest['segments']*2}]:
            with self.subTest(change=change):
                with self.assertRaises(ValueError):
                    validate_narration(self.root, {**self.manifest, **change}, self.timeline)

    def test_reject_truncated_wav_even_with_current_hash(self):
        path = self.root/'new.wav'; path.write_bytes(path.read_bytes()[:-100])
        self.manifest['segments'][0]['wav_hash'] = content_hash(path)
        with self.assertRaisesRegex(ValueError, 'truncated'):
            validate_narration(self.root, self.manifest, self.timeline)

    def test_paths_cannot_escape_or_write_protected_directories(self):
        for path in ['../outside.wav', str(self.root.parent/'outside.wav')]:
            with self.subTest(path=path), self.assertRaises((ValueError,StudioError)):
                bounded(self.root, path)
        with self.assertRaises(StudioError): bounded(self.root, 'manual/new', write=True)

    def test_nested_relative_path_objects_from_cli_on_windows(self):
        # argparse(type=Path) produces this exact type; str(path) uses Windows
        # backslashes, which must become POSIX before workspace validation.
        relative = Path('narration_fit')/'ruslan_v1'/'audio_manifest.json'
        self.assertEqual(bounded(self.root,relative), (self.root/relative).resolve())
        output = Path('exports')/'ruslan_v1'
        self.assertEqual(bounded(self.root,output,write=True), (self.root/output).resolve())


class ReplacementIntegrationTests(unittest.TestCase):
    @unittest.skipUnless(installed_runtime(), 'Installed official MLT and FFmpeg required')
    def test_actual_mlt_audio_mux_packets_decoded_frames_and_full_endings(self):
        runtime = installed_runtime()
        with tempfile.TemporaryDirectory(prefix='actual_audio_replace_') as folder:
            root = Path(folder)
            source = root/'source'; source.mkdir()
            ffmpeg = runtime['ffmpeg_path']
            flags = subprocess.CREATE_NO_WINDOW if __import__('os').name == 'nt' else 0
            subprocess.run([ffmpeg,'-v','error','-f','lavfi','-i','testsrc2=s=320x180:r=30:d=1.2',
                            '-an','-c:v','libx264','-pix_fmt','yuv420p',str(source/'moving.mp4')],check=True,creationflags=flags)
            tone(source/'old.wav',1.2,440)
            tone(source/'music.wav',2.4,220,amplitude=3000)
            tone(root/'first.wav',1.2,880)
            tone(root/'last.wav',1.2,1320)
            segments = [dict(id=key,text=f'{key} complete ending.',title='',visual='moving.mp4',audio='old.wav',duration=1.2,
                             visual_shots=[{'visual':'moving.mp4','frames':18,'label':'moving1'},
                                           {'visual':'moving.mp4','frames':18,'label':'moving2'}]) for key in ['first','last']]
            built = build_timeline(segments,'music.wav',root/'original',base_dir=source,
                                   project_id='demo',width=320,height=180,overlays_enabled=False)
            project = Path(built['project_mlt'])
            render_project(project,root/'original.mp4',qmelt=runtime['melt_path'],preset='ultrafast',timeout=120)
            build = {'project_mlt':'original/project.mlt','timeline_json':'original/timeline.json',
                     'project_sha256':content_hash(project),
                     'file_hashes':{p.relative_to(project.parent).as_posix():content_hash(p) for p in project.parent.rglob('*') if p.is_file()}}
            render = {'video':'original.mp4','sha256':content_hash(root/'original.mp4'),
                      'build_job_id':'build','project_sha256':build['project_sha256']}
            with sqlite3.connect(root/'registry.sqlite') as db:
                db.execute('CREATE TABLE jobs (id TEXT,project_id TEXT,operation TEXT,state TEXT,result TEXT)')
                db.executemany('INSERT INTO jobs VALUES (?,?,?,?,?)',
                               [('build','demo','build','done',json.dumps(build)),('render','demo','render','done',json.dumps(render))])
            db.close()
            save_json(root/'runtime.json',runtime)
            timeline = json.loads((root/'original/timeline.json').read_text(encoding='utf-8'))
            manifest = {'project_id':'demo','segments':[
                {'block_id':key,'text':f'{key} complete ending.','path':f'{key}.wav',
                 'duration_seconds':1.2,'wav_hash':content_hash(root/f'{key}.wav')} for key in ['first','last']]}
            (root/'input').mkdir()
            manifest_argument = Path('input')/'new.json'
            save_json(root/manifest_argument,manifest)
            old_files = {p:content_hash(p) for p in root.rglob('*') if p.is_file()}
            report = replace_narration(root,'build','render',manifest_argument,Path('replacement'),verify_decoded_frames=True)
            self.assertTrue(report['decoded_frames_identical'])
            self.assertTrue(report['video_packet_payloads_and_timestamps_identical'])
            self.assertEqual(report['video_packets_verified'],72)
            for path,digest in old_files.items(): self.assertEqual(content_hash(path),digest,str(path))
            self.assertEqual(report['music_sha256'],content_hash(root/'original'/timeline['music']))
            new_timeline = json.loads((root/'replacement/project/timeline.json').read_text(encoding='utf-8'))
            for old,new in zip(timeline['segments'],new_timeline['segments']):
                self.assertEqual({k:v for k,v in old.items() if k!='audio'}, {k:v for k,v in new.items() if k!='audio'})
            original_tree = ET.parse(project)
            new_tree = ET.parse(root/'replacement/project/project.mlt')
            for index in range(2):
                location = f"./producer[@id='voice_{index}']/property[@name='resource']"
                new_tree.find(location).text = original_tree.find(location).text
            self.assertEqual(ET.tostring(original_tree.getroot()),ET.tostring(new_tree.getroot()))
            audio_graph = ET.parse(root/'replacement/project/audio_export.mlt')
            self.assertEqual(audio_graph.find('profile').get('width'),'16')
            self.assertEqual(audio_graph.find('profile').get('frame_rate_num'),'30')
            self.assertFalse(any(p.get('id','').startswith('visual_') for p in audio_graph.findall('producer')))
            for selector in ["./playlist[@id='narrator']", "./playlist[@id='music']", "./producer[@id='music_source']"]:
                self.assertEqual(ET.tostring(audio_graph.find(selector)), ET.tostring(new_tree.find(selector)))
            # Independently compare the optimized graph to the full editable
            # visual graph across two complete voices and their shared music.
            baseline = root/'replacement/full_graph_mix.wav'
            run_local([runtime['melt_path'],str(root/'replacement/project/project.mlt'),
                       '-consumer',f'avformat:{baseline}','vn=1','acodec=pcm_s16le','ar=48000','ac=2',
                       'real_time=-1','threads=1','-progress'],root/'replacement','baseline_audio.log',
                      cwd=root/'replacement/project',timeout=120)
            with wave.open(str(root/'replacement/narration_mix.wav'),'rb') as sound:
                self.assertEqual(sound.getnframes(),115200)
                optimized_pcm = sound.readframes(sound.getnframes())
                samples = array('h',optimized_pcm)[::2]
            with wave.open(str(baseline),'rb') as sound:
                self.assertEqual(sound.getnframes(),115200)
                self.assertEqual(sound.readframes(sound.getnframes()),optimized_pcm)
            def magnitude(values,frequency):
                return 2*abs(sum(value*complex(math.cos(2*math.pi*frequency*i/48000),
                                                math.sin(2*math.pi*frequency*i/48000)) for i,value in enumerate(values)))/len(values)
            for end,frequency in [(57600,880),(115200,1320)]:
                tail = samples[end-4800:end]
                self.assertGreater(magnitude(tail,frequency),4000,'Final 100ms of each narration must survive')
                self.assertGreater(magnitude(tail,220),100,'Original music must still be audible')
                self.assertLess(magnitude(tail,440),50,'Old narrator must not remain in the mix')
            decoded_audio = subprocess.check_output([ffmpeg,'-v','error','-i',str(root/'replacement/final.mp4'),
                '-map','0:a:0','-ac','1','-ar','48000','-f','s16le','-'],creationflags=flags)
            final_samples = array('h',decoded_audio)
            for end,frequency in [(57600,880),(115200,1320)]:
                self.assertGreater(magnitude(final_samples[end-4800:end],frequency),3500,
                                   'Final AAC must retain the last 100ms of every complete narration')
            speech = json.loads((root/'replacement/speech_manifest.json').read_text(encoding='utf-8'))
            self.assertEqual([e['start_seconds'] for e in speech['segments']],[0.0,1.2])
            for entry in speech['segments']: self.assertEqual(content_hash(Path(entry['path'])),entry['wav_hash'])
            with self.assertRaises(FileExistsError):
                replace_narration(root,'build','render',manifest_argument,Path('replacement'))
            # Original build/render hashes remain mandatory even with valid new WAVs.
            (root/'original.mp4').write_bytes(b'changed')
            with self.assertRaisesRegex(ValueError,'hash mismatch'):
                verify_sources(root,build,render)


if __name__ == '__main__':
    unittest.main()
