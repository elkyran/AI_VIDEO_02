from copy import deepcopy
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import xml.etree.ElementTree as ET

from app.editorial import demo_result
from app.contracts import ProjectBrief
from app.jobs import JobManager
from app.production_bridge import ProductionBridge
from app.registry import Registry, StudioError, content_hash


class ProductionBridgeTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory(prefix='studio_bridge_')
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.bridge = ProductionBridge(self.root)
        self.plan = demo_result('edit_plan')
        self.assets = []
        for identifier, kind, duration in (('fixture_video', 'video', 6000), ('fixture_voice', 'audio', 2000)):
            path = self.root / (identifier + ('.mp4' if kind == 'video' else '.wav'))
            path.write_bytes(b'Unit fixture; intentionally never rendered')
            self.assets.append({'asset_id': identifier, 'path': path.name, 'sha256': content_hash(path),
                                'kind': kind, 'duration_ms': duration, 'rights': 'owned'})

    def test_exact_source_ranges_and_independent_voice_placement_survive_mlt(self):
        result = self.bridge.compile(self.plan, self.assets, 'build', scene_version=1, voice_version='voice:v1')
        tree = ET.parse(result['project_mlt'])
        picture = tree.find("playlist[@id='track_video']")
        self.assertEqual([(x.attrib['in'], x.attrib['out']) for x in picture.findall('entry')], [('60', '104'), ('120', '164')])
        voice = tree.find("playlist[@id='track_voice']")
        self.assertEqual([x.tag for x in voice], ['property', 'property', 'property', 'blank', 'entry', 'blank'])
        self.assertEqual(voice.find('blank').attrib['length'], '15')
        self.assertEqual(voice.find('entry').attrib['out'], '59')
        self.assertEqual(result['frames'], 90)
        self.assertEqual(result['quality_status'], 'not_checked')
        self.assertFalse((self.root / 'registry.sqlite').exists())
        with self.assertRaises(FileExistsError):
            self.bridge.compile(self.plan, self.assets, 'build')

    def test_unsupported_effects_and_range_hash_version_errors_publish_nothing(self):
        for mutate in (lambda p: p.update(required_capabilities=['speed']),
                       lambda p: p.update(transitions=[{'kind': 'crossfade'}]),
                       lambda p: p['voice_placements'][0].update(fade_in_ms=200)):
            plan = deepcopy(self.plan)
            mutate(plan)
            with self.assertRaises(StudioError) as caught:
                self.bridge.compile(plan, self.assets, 'bad')
            self.assertEqual(caught.exception.code, 'capability_gap')
            self.assertFalse((self.root / 'bad').exists())
        with self.assertRaises(StudioError):
            self.bridge.compile(self.plan, self.assets, 'bad', scene_version=2)
        self.assets[0]['duration_ms'] = 5000
        with self.assertRaises(StudioError) as caught:
            self.bridge.compile(self.plan, self.assets, 'bad')
        self.assertEqual(caught.exception.code, 'ASSET_RANGE_INVALID')
        self.assets[0]['duration_ms'] = 6000
        (self.root / self.assets[0]['path']).write_bytes(b'changed')
        with self.assertRaises(StudioError) as caught:
            self.bridge.compile(self.plan, self.assets, 'bad')
        self.assertEqual(caught.exception.code, 'ASSET_CHANGED')

    def test_escape_manual_and_modified_build_are_rejected_before_execution(self):
        for destination in ('../escape', 'manual/new', 'originals/new'):
            with self.assertRaises(StudioError):
                self.bridge.compile(self.plan, self.assets, destination)
        result = self.bridge.compile(self.plan, self.assets, 'build')
        Path(result['project_mlt']).write_text('manual edit', encoding='utf-8')
        with self.assertRaises(StudioError) as caught:
            self.bridge.render(result, 'output.mp4')
        self.assertEqual(caught.exception.code, 'ASSET_CHANGED')
        self.assertFalse((self.root / 'output.mp4').exists())

    def test_picture_gap_is_an_explicit_blank_not_an_accidental_hold(self):
        plan = deepcopy(self.plan)
        plan['shot_placements'][0].update(timeline_start_ms=500, timeline_end_ms=1500, source_in_ms=2000, source_out_ms=3000)
        result = self.bridge.compile(plan, self.assets, 'gap')
        tree = ET.parse(result['project_mlt'])
        self.assertEqual(tree.find("playlist[@id='track_video']/blank").attrib['length'], '15')

    def registered_script(self):
        script = {'project_id': 'demo', 'title': 'Fixture', 'scope': 'Synthetic bridge test',
                  'segments': [{'id': 'scene_001', 'text': 'Fixture voice', 'title': 'Fixture',
                                'visual_asset_id': 'fixture_video'}]}
        (self.root / 'script.json').write_text(json.dumps(script), encoding='utf-8')
        with Registry(self.root) as registry:
            registry.initialize(ProjectBrief(project_id='demo', title='Test', work_title='Original fixture', character='Test'))
            registry.register_asset('demo', 'fixture_video', self.assets[0]['path'], kind='video', rights='owned',
                                    duration_ms=6000, base_revision=1, idempotency_key='fixture-video')
            registry.register_asset('demo', 'script', 'script.json', kind='text', rights='owned',
                                    base_revision=2, idempotency_key='fixture-script')

    def test_prepared_synthesis_uses_existing_script_and_profile_without_enqueuing(self):
        self.registered_script()
        profile = {'profile_id': 'approved-fixture', 'version': 3}
        before = content_hash(self.root / 'registry.sqlite')
        with patch('app.production_profile.load_profile', return_value=profile) as load, \
                patch('app.production_profile.check_runtime') as runtime, \
                patch.object(JobManager, 'enqueue') as enqueue:
            descriptor = self.bridge.prepare_request('demo', 'synthesize', {'script_asset_id': 'script'}, 3, 'prepared-voice',
                                                     project_root=self.root)
        self.assertEqual(descriptor['status'], 'validated_not_enqueued')
        self.assertEqual(descriptor['profile_id'], profile['profile_id'])
        self.assertEqual(descriptor['profile_version'], 3)
        self.assertFalse(descriptor['worker_started'])
        self.assertEqual(before, content_hash(self.root / 'registry.sqlite'))
        load.assert_called_once()
        runtime.assert_called_once()
        self.assertEqual(load.call_args.kwargs, {})
        self.assertEqual(len(load.call_args.args), 1)
        self.assertEqual(Path(load.call_args.args[0]).resolve(), self.root.resolve())
        self.assertEqual(runtime.call_args.kwargs, {})
        self.assertEqual(len(runtime.call_args.args), 3)
        self.assertEqual(runtime.call_args.args[0], profile)
        for path in runtime.call_args.args[1:]:
            self.assertEqual(Path(path).resolve(), self.root.resolve())
        enqueue.assert_not_called()
        with Registry(self.root) as registry:
            self.assertEqual(registry._project('demo')['revision'], 3)
            self.assertIsNone(registry.db.execute("SELECT name FROM sqlite_master WHERE name='jobs'").fetchone())

    def test_preparation_blocks_profile_drift_active_jobs_and_wrong_dependencies(self):
        self.registered_script()
        profile = {'profile_id': 'approved-fixture', 'version': 3}
        with patch('app.production_profile.load_profile', return_value=profile), \
                patch('app.production_profile.check_runtime', side_effect=ValueError('Runtime/profile mismatch: voice')):
            with self.assertRaisesRegex(ValueError, 'Runtime/profile mismatch'):
                self.bridge.prepare_request('demo', 'synthesize', {'script_asset_id': 'script'}, 3, 'voice')
        with patch('app.production_profile.load_profile', return_value=profile), patch('app.production_profile.check_runtime'), \
                patch.object(JobManager, '_spawn') as spawn:
            for operation, params in [('shell', {}), ('synthesize', {'script_asset_id': 'script', 'voice': 'different'})]:
                with self.assertRaises(StudioError):
                    self.bridge.prepare_request('demo', operation, params, 3, 'bad')
            manager = JobManager(self.root)
            pending = manager.enqueue('demo', 'synthesize', {'script_asset_id': 'script'}, 3, 'existing', start=False)
            with self.assertRaises(StudioError) as caught:
                self.bridge.prepare_request('demo', 'synthesize', {'script_asset_id': 'script'}, 4, 'next')
            self.assertEqual(caught.exception.code, 'ACTIVE_OPERATION')
            with Registry(self.root) as registry:
                registry.db.execute("UPDATE jobs SET state='done' WHERE id=?", (pending['job_id'],))
            with self.assertRaises(StudioError) as caught:
                self.bridge.prepare_request('demo', 'render', {'build_job_id': pending['job_id']}, 4, 'wrong-parent')
            self.assertEqual(caught.exception.code, 'DEPENDENCY_PENDING')
            self.assertEqual(len(manager.list('demo')), 1)
            spawn.assert_not_called()


if __name__ == '__main__':
    unittest.main()
