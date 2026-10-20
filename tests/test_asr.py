import unittest
from pathlib import Path
import tempfile

from scripts.transcribe_audio import (normalized_words, word_error_rate, make_cues, timestamp,
                                     align_script_words, nonoverlapping_cues, validate_caption_cues,
                                     valid_block_id, write_srt)


class ASRMetricTests(unittest.TestCase):
    def test_accents_do_not_destroy_russian_letters(self):
        self.assertEqual(normalized_words("Ру́деус Гре́йрат, ещё."), ["рудеус", "грейрат", "еще"])
        self.assertNotEqual(normalized_words("мой"), normalized_words("мои"))

    def test_missing_negation_is_deletion(self):
        result = word_error_rate("Он не согласен.", "Он согласен")
        self.assertEqual(result["deletions"], 1)
        self.assertAlmostEqual(result["wer"], 1/3, places=5)
        self.assertEqual(result["edits"][0]["expected"], "не")

    def test_cues_keep_measured_timing(self):
        cues = make_cues([
            {"word": "Первая", "start_seconds": 2.1, "end_seconds": 2.7},
            {"word": "фраза.", "start_seconds": 2.8, "end_seconds": 3.4},
            {"word": "Вторая.", "start_seconds": 4.2, "end_seconds": 5.0},
        ], "b01")
        self.assertEqual(len(cues), 2)
        self.assertEqual((cues[0]["start_seconds"], cues[0]["end_seconds"]), (2.1, 3.4))
        self.assertEqual(timestamp(3602.123), "01:00:02,123")


def observed(word, begin, finish):
    return {"word": word, "start_seconds": begin, "end_seconds": finish}


class ScriptSubtitleTests(unittest.TestCase):
    def assert_timing(self, alignment, start, end):
        previous_end = start
        for word in alignment["words"]:
            self.assertGreaterEqual(word["start_seconds"], previous_end)
            self.assertGreater(word["end_seconds"], word["start_seconds"])
            self.assertLessEqual(word["end_seconds"], end)
            previous_end = word["end_seconds"]

    def test_substitution_keeps_script_names_and_punctuation(self):
        source = "«Рудеус Грейрат» — второй шанс!"
        raw = [observed("Рудиус", 2.1, 2.6), observed("Грейрад", 2.6, 3.2),
               observed("второй", 3.3, 3.7), observed("шанс.", 3.7, 4.1)]
        aligned = align_script_words(source, raw, 2, 2.2)
        cues = make_cues(aligned["words"], "block.one")
        self.assertEqual(" ".join(cue["text"].replace("\n", " ") for cue in cues), source)
        self.assertEqual(aligned["words"][0]["match_kind"], "substitution")
        self.assertEqual(aligned["words"][0]["start_seconds"], 2.1)
        self.assertEqual(raw[0]["word"], "Рудиус")  # Raw evidence is not rewritten.
        self.assertFalse(aligned["exact_forced_alignment"])
        self.assertEqual(word_error_rate(source, "Рудиус Грейрад второй шанс")["substitutions"], 2)

    def test_missing_source_word_is_interpolated_without_overlap(self):
        aligned = align_script_words("Он не согласен.", [observed("Он", 0, .4), observed("согласен.", .4, 1.2)], 0, 1.4)
        self.assertEqual(aligned["words"][1]["word"], "не")
        self.assertTrue(aligned["words"][1]["interpolated"])
        self.assertIn(1, aligned["interpolated_token_indices"])
        self.assert_timing(aligned, 0, 1.4)
        cues = make_cues(aligned["words"], "block.one")
        self.assertEqual(cues[0]["text"], "Он не согласен.")
        self.assertEqual(validate_caption_cues(cues, {"block.one": (0, 1.4)})["status"], "pass")

    def test_extra_asr_word_is_retained_only_in_raw_track(self):
        raw = [observed("Он", 0, .3), observed("конечно", .3, .7), observed("согласен.", .7, 1.3)]
        aligned = align_script_words("Он согласен.", raw, 0, 1.4)
        self.assertEqual(aligned["unmatched_asr_word_indices"], [1])
        self.assertEqual(make_cues(aligned["words"], "b01")[0]["text"], "Он согласен.")
        self.assertIn("конечно", make_cues(raw, "b01")[0]["text"])
        self.assert_timing(aligned, 0, 1.4)

    def test_empty_speech_never_fabricates_timed_script_captions(self):
        aligned = align_script_words("Он не согласен.", [], 2, 4)
        self.assertEqual(aligned["status"], "unavailable")
        self.assertEqual(aligned["words"], [])
        self.assertEqual(aligned["unaligned_source_token_indices"], [0, 1, 2])
        validation = validate_caption_cues([], {"b01": (2, 6)})
        self.assertEqual(validation["status"], "fail")
        self.assertEqual(validation["missing_caption_block_ids"], ["b01"])
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "subtitles.srt"
            write_srt([], path)
            self.assertEqual(path.read_text(encoding="utf-8"), "")

    def test_overlapping_anchor_intervals_are_marked_and_ordered(self):
        raw = [observed("Раз.", 0, .8), observed("Два.", .6, 1.2), observed("Три.", .9, 1.3)]
        aligned = align_script_words("Раз. Два. Три.", raw, 0, 1.4)
        self.assert_timing(aligned, 0, 1.4)
        self.assertTrue(aligned["interpolated_token_indices"])
        cues = nonoverlapping_cues(make_cues(aligned["words"], "b01"))
        self.assertEqual(validate_caption_cues(cues, {"b01": (0, 1.4)})["status"], "pass")
        self.assertEqual([cue["text"] for cue in cues], ["Раз.", "Два.", "Три."])

    def test_zero_asr_intervals_have_explicit_estimated_fallback(self):
        raw = [observed("Раз", 1, 1), observed("два", 1, 1)]
        aligned = align_script_words("Раз, два!", raw, .5, 1.5)
        self.assert_timing(aligned, .5, 2.0)
        self.assertTrue(all(w["timestamp_method"] == "interpolated_nonmonotonic_asr" for w in aligned["words"]))

    def test_validation_rejects_bad_geometry_and_missing_block(self):
        cues = [dict(observed("", 0, 1), block_id="a", text="Раз."),
                dict(observed("", .5, 3), block_id="a", text="Два."),
                dict(observed("", float("nan"), 4), block_id="a", text="Три.")]
        report = validate_caption_cues(cues, {"a": (0, 2), "b": (2, 2.5)})
        self.assertEqual(report["status"], "fail")
        self.assertFalse(report["finite_timestamps"])
        self.assertFalse(report["nonoverlap"])
        self.assertFalse(report["within_final_end"])
        self.assertEqual(report["missing_caption_block_ids"], ["b"])

    def test_period_is_valid_in_block_identifier(self):
        self.assertTrue(valid_block_id("block.one"))
        self.assertFalse(valid_block_id("block/one"))


if __name__ == "__main__":
    unittest.main()
