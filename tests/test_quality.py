"""Quality gates validate exported evidence, not just an ASR cue count."""
from pathlib import Path
import tempfile
import unittest

from app.operations import subtitle_timing_issues


class SubtitleTimingTests(unittest.TestCase):
    def check_srt(self, text, duration=4.0, ranges=None):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'subtitles.srt'
            path.write_text(text,encoding='utf-8')
            return subtitle_timing_issues(path,duration,ranges or [(0,2),(2,4)])

    def test_valid_multiline_cues_with_silence_and_frame_rounding(self):
        self.assertEqual(self.check_srt('1\n00:00:00,120 --> 00:00:01,900\nПервая\nреплика.\n\n2\n00:00:02,100 --> 00:00:04,010\nВторая.\n'),[])

    def test_count_does_not_hide_overlap_out_of_bounds_or_missing_block(self):
        result=self.check_srt('1\n00:00:00,100 --> 00:00:01,600\nПервая.\n\n2\n00:00:01,500 --> 00:00:01,800\nПовтор.\n')
        self.assertTrue(any('overlapping' in issue for issue in result))
        self.assertIn('Block 2: no subtitle coverage',result)
        result=self.check_srt('1\n00:00:00,100 --> 00:00:05,000\nСлишком долго.\n')
        self.assertTrue(any('outside video bounds' in issue for issue in result))

    def test_empty_and_malformed_srt_do_not_pass(self):
        for text in ('','1\n00:00:00,000 --> 00:61:02,000\nОшибка.\n','1\ninvalid\nТекст.'):
            self.assertTrue(self.check_srt(text))


if __name__=='__main__':unittest.main()
