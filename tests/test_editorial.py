from copy import deepcopy
import unittest

from pydantic import ValidationError

from app.editorial import (EditPlan, Review, Shot, demo_result, detect_reused_ranges,
                           object_hash, validate_result, validate_review)
from app.registry import StudioError


class EditorialTests(unittest.TestCase):
    def test_demo_chain_is_valid_but_never_claims_creative_acceptance(self):
        scene = validate_result('scene', demo_result('scene'))
        context = {'project_id': 'demo', 'dependency_results': {'writer': scene}}
        plan = validate_result('edit_plan', demo_result('edit_plan', context), context)
        context['dependency_results']['director'] = plan
        review = validate_result('review', demo_result('review', context), context)
        self.assertEqual(review['subject_hash'], object_hash(plan))
        self.assertEqual(review['verdict'], 'needs_review')
        self.assertTrue(all(item['simulated'] for item in (scene, plan, review)))

    def test_invalid_partial_unknown_and_stale_results_fail(self):
        with self.assertRaises(ValidationError):
            validate_result('scene', {'scene_id': 'missing'})
        plan = demo_result('edit_plan')
        for mutate in (lambda x: x.update(arbitrary_command='anything'),
                       lambda x: x['shot_placements'][0].update(source_out_ms=2000),
                       lambda x: x['voice_placements'][0].update(timeline_end_ms=4000)):
            bad = deepcopy(plan)
            mutate(bad)
            with self.assertRaises(ValidationError):
                validate_result('edit_plan', bad)
        for context in ({'scene_version': 2}, {'voice_version': 'voice:v2'}, {'project_id': 'another'}):
            with self.assertRaises(StudioError) as caught:
                validate_result('edit_plan', plan, context)
            self.assertEqual(caught.exception.code, 'STALE_INPUTS')

    def test_wrong_render_version_and_self_asserted_media_delivery_fail(self):
        review = {'project_id': 'demo', 'review_id': 'r1', 'subject_type': 'render',
                  'subject_id': 'render1', 'subject_version': 1, 'subject_hash': 'a' * 64,
                  'render_id': 'render1', 'render_hash': 'a' * 64, 'edit_plan_version': 1,
                  'evidence_mode': 'video_audio', 'verdict': 'ready_for_review'}
        with self.assertRaises(StudioError):
            validate_review(review)
        mislabelled = {**review, 'subject_type': 'scene', 'evidence_mode': 'metadata_only'}
        with self.assertRaises(StudioError) as caught:
            validate_review(mislabelled, {'subject_type': 'render'})
        self.assertEqual(caught.exception.code, 'STALE_INPUTS')
        with self.assertRaises(StudioError) as caught:
            validate_review(review, {'provided_evidence_mode': 'video_audio', 'render_hash': 'b' * 64})
        self.assertEqual(caught.exception.code, 'STALE_INPUTS')
        review['reviewed_coverage'] = [{'start_ms': 0, 'end_ms': 3000, 'tracks': ['video', 'audio']}]
        supplied = [{'start_ms': 0, 'end_ms': 1000, 'tracks': ['video', 'audio']},
                    {'start_ms': 2000, 'end_ms': 3000, 'tracks': ['video', 'audio']}]
        with self.assertRaises(StudioError):
            validate_review(review, {'provided_evidence_mode': 'video_audio', 'provided_coverage': supplied})
        supplied.insert(1, {'start_ms': 1000, 'end_ms': 2000, 'tracks': ['video', 'audio']})
        self.assertEqual(validate_review(review, {'provided_evidence_mode': 'video_audio', 'provided_coverage': supplied})['verdict'], 'ready_for_review')
        review['evidence_mode'] = 'contact_sheet'
        with self.assertRaises(StudioError):
            validate_review(review, {'provided_evidence_mode': 'contact_sheet', 'provided_coverage': supplied})

    def test_scene_result_version_is_bound_to_requested_scene_version(self):
        scene = demo_result('scene')
        scene['version'] = 99
        with self.assertRaises(StudioError) as caught:
            validate_result('scene', scene, {'scene_version': 1})
        self.assertEqual(caught.exception.code, 'STALE_INPUTS')

    def test_dependency_versions_cannot_be_overridden_by_old_context(self):
        current_scene = demo_result('scene', {'scene_version': 2})
        with self.assertRaises(StudioError) as caught:
            validate_result('edit_plan', demo_result('edit_plan'),
                            {'scene_version': 1, 'dependency_results': {'writer': current_scene}})
        self.assertEqual(caught.exception.code, 'STALE_INPUTS')
        with self.assertRaises(StudioError):
            validate_result('edit_plan', demo_result('edit_plan'), {'input_versions': {'scene': 'scene_001:v2'}})

    def test_critical_issue_cannot_be_accepted_and_human_review_requires_authority(self):
        review = demo_result('review')
        review['issues'] = [{'issue_id': 'problem', 'category': 'sound', 'severity': 'critical',
                             'location': '00:01', 'observation': 'voice cut', 'effect_on_viewer': 'incomplete thought',
                             'suggested_change': 'restore ending', 'required_evidence': 'video_audio'}]
        with self.assertRaises(ValidationError):
            Review.model_validate(review)
        review['verdict'] = 'blocked'
        review['evidence_mode'] = 'human_review'
        with self.assertRaises(StudioError):
            validate_review(review, {'provided_evidence_mode': 'human_review'})

    def test_source_repetition_uses_intersections_and_keeps_explanation(self):
        shot = {'shot_id': 'one', 'scene_id': 'scene1', 'beat_id': 'beat1', 'visual_function': 'reaction',
                'asset_id': 'source', 'source_in_ms': 100, 'source_out_ms': 700, 'observed_content': 'metadata'}
        repeated = {**shot, 'shot_id': 'two', 'source_in_ms': 500, 'source_out_ms': 1000, 'reuse_reason': 'memory'}
        adjacent = {**shot, 'shot_id': 'three', 'source_in_ms': 1000, 'source_out_ms': 1300}
        result = detect_reused_ranges([shot, repeated, adjacent])
        self.assertEqual(len(result), 1)
        self.assertEqual((result[0]['source_in_ms'], result[0]['source_out_ms']), (500, 700))
        self.assertEqual(result[0]['status'], 'declared')

    def test_director_cannot_upgrade_a_description_to_a_watched_clip(self):
        plan = demo_result('edit_plan')
        row = plan['shot_placements'][0]
        row['shot'] = {'shot_id': 'shot1', 'scene_id': plan['scene_id'], 'beat_id': 'beat1',
                       'visual_function': 'reaction', 'asset_id': row['asset_id'],
                       'source_in_ms': row['source_in_ms'], 'source_out_ms': row['source_out_ms'],
                       'observed_content': 'A supplied description', 'evidence_mode': 'video_audio',
                       'coverage': [{'start_ms': 2000, 'end_ms': 3500, 'tracks': ['video']}]}
        with self.assertRaises(StudioError):
            validate_result('edit_plan', plan)
        validate_result('edit_plan', plan, {'asset_delivery': {'fixture_video': {'evidence_mode': 'video_audio',
                       'coverage': [{'start_ms': 2000, 'end_ms': 3500, 'tracks': ['video']}]}}})


if __name__ == '__main__':
    unittest.main()
