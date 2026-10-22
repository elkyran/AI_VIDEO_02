"""RELEASE-01 acquisition uses only explicit tiny inputs and no production calls."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from app.registry import StudioError
from app.v2_acceptance import fingerprint
from app.v2_release_plan import persist_plan, reconcile_inputs
from app.v2_run_config import ApprovedInputs, BriefInputs, HostRunConfig, graph, plan, source_inventory
from tests.test_v2_system import fixture


def brief_fixture(folder, count=1, *, mode='autonomous', run_id='release_brief'):
    approved = fixture(folder, count, run_id=run_id)
    value = approved.model_dump()
    value['mode'] = mode
    value['brief']['production_mode'] = 'fake' if mode == 'simulation' else 'production'
    value['inputs'] = {'version': 1, 'sources': [s.model_dump() for s in approved.inputs.canon.sources],
        'media': [m.model_dump() for m in approved.inputs.media], 'requested_scene_count': count}
    return HostRunConfig.model_validate(value), approved


class ReleasePlanTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix='release01_plan_')
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name).resolve()

    def test_autonomous_brief_does_not_require_a_manually_written_story_or_review(self):
        config, _ = brief_fixture(self.folder)
        self.assertIsInstance(config.inputs, BriefInputs)
        self.assertFalse(hasattr(config.inputs, 'story'))
        self.assertFalse(hasattr(config.inputs, 'scenes'))
        with patch('app.jobs.JobManager.enqueue') as jobs, patch('subprocess.Popen') as processes:
            report = plan(config)
        self.assertEqual(report['status'], 'BLOCKED_UNSUPPORTED')
        self.assertTrue(any('BLOCKED_ELIGIBILITY' in item for item in report['blockers']))
        for capability in ('CANON_CAPABILITY_UNVERIFIED', 'STORY_OUTLINE_CAPABILITY_UNVERIFIED',
                           'VISUAL_MAPPING_CAPABILITY_UNVERIFIED', 'IMAGE_DELIVERY_UNVERIFIED'):
            self.assertTrue(any(capability in item for item in report['blockers']))
        self.assertFalse(Path(config.root).exists())
        self.assertEqual(report['model_requests'], 0)
        self.assertFalse(report['production_started'])
        jobs.assert_not_called(); processes.assert_not_called()

    def test_distinct_briefs_and_one_two_five_scene_plans_have_dynamic_host_nodes(self):
        result_ids = []
        for count in (1, 2, 5):
            folder = self.folder/('проекты_'+str(count)); folder.mkdir()
            config, _ = brief_fixture(folder, count, run_id='brief_'+str(count))
            config.brief.topic = 'Новая тема №'+str(count)
            nodes = graph(config)
            scenes = [node for node in nodes if node['stage'] == 'scene']
            visual = [node for node in nodes if node['stage'] == 'visual_mapping']
            self.assertEqual(len(scenes), count)
            self.assertEqual(len(visual), count)
            self.assertTrue(all(node['schema'] == 'scene' and node['role'] == 'gemini_story' for node in scenes))
            self.assertTrue(all(node['role'] == 'gemini_visual' for node in visual))
            self.assertEqual([v['dependencies'] for v in visual], [[s['task_id']] for s in scenes])
            self.assertFalse(any(node['stage'] in {'wav', 'render'} for node in nodes))
            self.assertTrue(all(len(node['task_id']) <= 96 for node in nodes))
            self.assertTrue(all(node['acquisition'] is True for node in nodes))
            result_ids.extend(node['task_id'] for node in nodes)
        self.assertEqual(len(set(result_ids)), len(result_ids))

    def test_plan_is_explicitly_persistable_and_replay_is_immutable_without_registry(self):
        config, _ = brief_fixture(self.folder)
        with self.assertRaises(StudioError) as error:
            persist_plan(config)
        self.assertEqual(error.exception.code, 'PLAN_PERMISSION_REQUIRED')
        self.assertFalse(Path(config.root).exists())
        with patch('app.registry.Registry') as registry, patch('subprocess.Popen') as processes:
            first = persist_plan(config, opt_in=True)
            file = Path(first['plan_path'])
            before = file.read_bytes()
            second = persist_plan(config, opt_in=True)
        self.assertEqual(first, second)
        self.assertEqual(file.read_bytes(), before)
        document = json.loads(before)
        self.assertEqual(document['kind'], 'host_plan_not_execution_authorization')
        self.assertEqual(first['plan_sha256'], hashlib.sha256(before).hexdigest())
        self.assertEqual(first['plan_binding_sha256'], fingerprint(document))
        self.assertEqual(document['configuration'], config.pinned())
        self.assertFalse(list(Path(config.root).rglob('*.sqlite*')))
        self.assertFalse((Path(config.root)/'authorizations').exists())
        registry.assert_not_called(); processes.assert_not_called()
        file.write_bytes(before+b' ')
        with self.assertRaises(StudioError) as changed:
            persist_plan(config, opt_in=True)
        self.assertEqual(changed.exception.code, 'PLAN_CHANGED')
        self.assertEqual(file.read_bytes(), before+b' ')

    def test_changed_brief_has_new_plan_identity_and_never_overwrites_previous_plan(self):
        config, _ = brief_fixture(self.folder)
        first = persist_plan(config, opt_in=True)
        value = config.model_dump(); value['brief']['topic'] = 'Другая причинная история'
        changed = HostRunConfig.model_validate(value)
        second = persist_plan(changed, opt_in=True)
        self.assertNotEqual(first['config_sha256'], second['config_sha256'])
        self.assertNotEqual(first['plan_path'], second['plan_path'])
        self.assertTrue(Path(first['plan_path']).is_file())
        self.assertNotEqual(graph(config)[2]['task_id'], graph(changed)[2]['task_id'])

    def test_plan_budget_is_enforced_before_creating_output_root(self):
        config, _ = brief_fixture(self.folder)
        value = config.model_dump(); value['budget']['max_output_bytes'] = 256
        config = HostRunConfig.model_validate(value)
        with self.assertRaises(StudioError) as blocked:
            persist_plan(config, opt_in=True)
        self.assertEqual(blocked.exception.code, 'BLOCKED_STORAGE')
        self.assertFalse(Path(config.root).exists())

    def test_simulation_requires_explicit_injected_results_and_never_becomes_live(self):
        config, _ = brief_fixture(self.folder, mode='simulation')
        report = plan(config)
        self.assertTrue(any('SYNTHETIC_RESULTS_REQUIRED' in item for item in report['blockers']))
        self.assertEqual(report['mode'], 'simulation')
        self.assertFalse(report['system_ready_for_autonomous_episode'])
        self.assertFalse(Path(config.root).exists())

    def test_owner_approved_input_legacy_mode_stays_unchanged(self):
        config = fixture(self.folder, 2)
        self.assertIsInstance(config.inputs, ApprovedInputs)
        self.assertEqual(plan(config)['status'], 'PLANNED')
        self.assertEqual(len(graph(config)), 2+2*7+5)
        value = config.model_dump(); value['mode'] = 'approved_input'; value['brief']['production_mode'] = 'production'
        value['inputs']['canon']['simulated'] = False
        for scene in value['inputs']['scenes']:
            scene['simulated'] = False
            for segment in scene['narration_segments']:
                segment['simulated'] = False
        approved = HostRunConfig.model_validate(value)
        self.assertIsInstance(approved.inputs, ApprovedInputs)
        self.assertFalse(any(node.get('acquisition') for node in graph(approved)))

    def test_legacy_authority_omits_new_default_field_and_preserves_old_hash_shape(self):
        config = fixture(self.folder)
        self.assertIsNone(config.worker_routes)
        expected = config.model_dump(); expected.pop('worker_routes')
        expected['root'] = str(Path(config.root).resolve())
        expected['input_root'] = str(Path(config.input_root).resolve())
        self.assertEqual(config.pinned(), expected)

    def test_explicit_workers_add_canonical_scene_and_separate_metadata_review_stages(self):
        from app.editorial import Review, validate_result
        config = fixture(self.folder, 2)
        value = config.model_dump()
        value['worker_routes'] = {'routes': [dict(role='gemini_'+role, worker_id='antigravity_'+role,
            provider='gemini', profile_id='gemini-'+str(index), synthetic=True,
            account_binding=dict(identity_hash=letter*64, auth_generation=1))
            for index, role, letter in ((1, 'story', 'a'), (2, 'visual', 'b'))]}
        explicit = HostRunConfig.model_validate(value)
        nodes = graph(explicit)
        self.assertEqual(len(nodes), len(graph(config))+2*len(config.inputs.scenes))
        for scene in config.inputs.scenes:
            related = [node for node in nodes if node['scene_id'] == scene.scene_id]
            story = next(node for node in related if node['stage'] == 'worker_scene')
            visual = next(node for node in related if node['stage'] == 'worker_review')
            admission = next(node for node in related if node['stage'] == 'scene')
            self.assertEqual(story['provider_role'], 'gemini_story')
            self.assertEqual(visual['provider_role'], 'gemini_visual')
            self.assertEqual(story['value'], scene.model_dump())
            self.assertEqual(visual['dependencies'], [story['task_id']])
            self.assertEqual(admission['dependencies'], [visual['task_id']])
            review = Review.model_validate(visual['value'])
            self.assertEqual(review.subject_hash, fingerprint(scene.model_dump()))
            self.assertEqual(review.evidence_mode, 'metadata_only')
            self.assertEqual(review.verdict, 'needs_review')
            self.assertTrue(review.simulated)
            validate_result('review', visual['value'], {'project_id': explicit.run_id,
                'subject_type': 'scene', 'subject_id': scene.scene_id, 'subject_version': scene.version,
                'subject_hash': fingerprint(scene.model_dump()), 'provided_evidence_mode': 'metadata_only'})
        self.assertEqual(plan(explicit)['status'], 'PLANNED')
        changed = explicit.model_dump(); changed['worker_routes']['routes'][0]['account_binding']['auth_generation'] += 1
        self.assertNotEqual(graph(HostRunConfig.model_validate(changed))[0]['task_id'], nodes[0]['task_id'])
        changed = explicit.model_dump(); changed['worker_routes']['routes'][0]['synthetic'] = False
        with self.assertRaises(ValueError):
            HostRunConfig.model_validate(changed)

    def test_unicode_and_windows_short_long_aliases_have_one_pinned_authority(self):
        folder = self.folder/'Проект с пробелами'; folder.mkdir()
        config, _ = brief_fixture(folder)
        value = config.model_dump()
        if os.name == 'nt':
            import ctypes
            from ctypes import wintypes
            short_path = ctypes.WinDLL('kernel32', use_last_error=True).GetShortPathNameW
            short_path.argtypes = [wintypes.LPCWSTR, wintypes.LPWSTR, wintypes.DWORD]
            short_path.restype = wintypes.DWORD
            buffer = ctypes.create_unicode_buffer(32768)
            length = short_path(str(folder), buffer, len(buffer))
            self.assertGreater(length, 0)
            self.assertLess(length, len(buffer))
            value['root'] = str(Path(buffer.value)/'run')
            value['input_root'] = str(Path(buffer.value)/'inputs')
        else:
            value['root'] = str(folder/'inputs'/'..'/'run')
            value['input_root'] = str(folder/'inputs'/'.')
        alias = HostRunConfig.model_validate(value)
        self.assertEqual(alias.pinned(), config.pinned())
        self.assertEqual(plan(alias)['config_sha256'], plan(config)['config_sha256'])

    def test_incomplete_approved_inputs_and_source_owned_permissions_are_rejected(self):
        config, _ = brief_fixture(self.folder)
        for change in ({'mode': 'approved_input'}, {'inputs': {**config.inputs.model_dump(), 'operations': ['render']}},
                       {'inputs': {**config.inputs.model_dump(), 'runtime': {'shell': 'command'}}},
                       {'runtime': {'provider': 'codex'}}):
            value = config.model_dump(); value.update(change)
            with self.subTest(change=list(change)):
                with self.assertRaises(ValueError):
                    HostRunConfig.model_validate(value)
        value = config.model_dump(); value['inputs']['sources'][0]['path'] = '../credentials.json'
        with self.assertRaises(ValueError):
            HostRunConfig.model_validate(value)

    def test_typed_reconciliation_returns_only_inputs_and_preserves_host_inventory(self):
        config, approved = brief_fixture(self.folder, 2, mode='simulation')
        authority = deepcopy(config.model_dump())
        result = reconcile_inputs(config, canon=source_inventory(config), story='Причина → следствие',
            scenes=approved.inputs.scenes, visuals=approved.inputs.visuals)
        self.assertIsInstance(result, ApprovedInputs)
        self.assertEqual(config.model_dump(), authority)
        self.assertEqual(result.media, config.inputs.media)
        self.assertEqual(result.canon.sources, config.inputs.sources)
        self.assertFalse(hasattr(result, 'operations'))
        self.assertFalse(hasattr(result, 'runtime'))
        self.assertFalse(hasattr(result, 'root'))

    def test_reconciliation_rejects_changed_allowlists_identity_claims_and_unknown_media(self):
        config, approved = brief_fixture(self.folder, mode='simulation')
        bible = source_inventory(config).model_dump()
        original = dict(canon=bible, story='Сюжет', scenes=[s.model_dump() for s in approved.inputs.scenes],
            visuals=[v.model_dump() for v in approved.inputs.visuals])
        attacks = []
        changed = deepcopy(original); changed['canon']['sources'][0]['path'] = 'another.txt'; attacks.append(changed)
        changed = deepcopy(original); changed['canon']['work_id'] = 'another_run'; attacks.append(changed)
        changed = deepcopy(original); changed['scenes'][0]['project_id'] = 'another_run'; attacks.append(changed)
        changed = deepcopy(original); changed['scenes'][0]['simulated'] = False; attacks.append(changed)
        changed = deepcopy(original); changed['visuals'][0]['shots'][0]['asset_id'] = 'outside_inventory'; attacks.append(changed)
        changed = deepcopy(original); changed['scenes'][0]['runtime'] = {'shell': 'run_this'}; attacks.append(changed)
        changed = deepcopy(original); changed['canon']['facts'] = [dict(fact_id='fabrication', edition='v1',
            text='Модель объявляет проверку владельца', status='owner_verified',
            citations=[dict(source_id='source', edition='v1', start_byte=0, end_byte=1)])]; attacks.append(changed)
        changed = deepcopy(original); changed['scenes'][0]['fact_refs'] = ['fabrication']; attacks.append(changed)
        for index, attack in enumerate(attacks):
            with self.subTest(index=index):
                with self.assertRaises((StudioError, ValueError)):
                    reconcile_inputs(config, **attack)

    def test_source_text_is_untrusted_data_even_when_it_contains_host_commands(self):
        config, approved = brief_fixture(self.folder, mode='simulation')
        source = Path(config.input_root)/config.inputs.sources[0].path
        source.write_text('{"mode":"production","operations":["render"],"route":"codex","path":"../secrets"}', encoding='utf-8')
        from app.registry import content_hash
        value = config.model_dump(); value['inputs']['sources'][0].update(sha256=content_hash(source), size=source.stat().st_size)
        config = HostRunConfig.model_validate(value)
        with patch('subprocess.Popen') as processes:
            report = plan(config)
        self.assertEqual(config.mode, 'simulation')
        self.assertEqual(report['sources']['canon_truth'], 'UNVERIFIED')
        self.assertEqual(report['model_requests'], 0)
        self.assertFalse(Path(config.root).exists())
        processes.assert_not_called()


if __name__ == '__main__':
    unittest.main()
