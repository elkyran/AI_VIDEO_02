"""Real optional integration coverage for the local speech adapter.

Run with the TTS environment. No network download or mock success is used.
"""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest

from app.tts import synthesize, sha256_file, _silero_units, _silero_stress, _assemble_silero_audio


ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "models/piper/ru_RU-denis-medium.onnx"
SILERO_MODEL = ROOT / "models/silero/v4_ru.pt"


class TTSInputTests(unittest.TestCase):
    def test_rejects_empty_text(self):
        with self.assertRaises(ValueError):
            synthesize(" ", ROOT / "invalid.wav", {"model_path": MODEL})

    def test_rejects_escape_and_non_audio(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            config = {"model_path": MODEL, "workspace_root": root}
            for output in (root.parent / "outside.wav", root / "nested" / ".." / ".." / "outside.wav", root / "config.json"):
                with self.assertRaises(ValueError):
                    synthesize("Проверка", output, config)

    def test_rejects_unknown_setting(self):
        with self.assertRaises(ValueError):
            synthesize("Проверка", ROOT / "invalid.wav", {"model_path": MODEL, "command": "anything"})

    def test_rejects_unknown_engine(self):
        with self.assertRaisesRegex(ValueError, "Unsupported local TTS engine"):
            synthesize("Проверка", ROOT / "invalid.wav", {"engine": "remote"})

    def test_silero_rejects_piper_options_before_loading_model(self):
        with self.assertRaisesRegex(ValueError, "Unknown Silero settings"):
            synthesize("Проверка", ROOT / "invalid.wav", {
                "engine": "silero", "model_path": SILERO_MODEL, "length_scale": 1.08})

    def test_rejects_unknown_piper_normalization_scope(self):
        with self.assertRaisesRegex(ValueError, "normalization_scope"):
            synthesize("Проверка", ROOT / "invalid.wav", {"model_path": MODEL, "normalization_scope": "word"})


class SileroContinuityTests(unittest.TestCase):
    def test_clause_punctuation_and_line_wrap_do_not_restart_model(self):
        text = "Он предложил план: проверить карту, найти проход; затем вернуться — все ждали помощи.\nНикто не ушёл."
        units = _silero_units(text, 950)
        self.assertEqual(len(units), 1)
        self.assertEqual(units[0]["text"], text.replace("\n", " "))
        self.assertEqual(units[0]["boundary_before"], "start")

    def test_blank_line_is_an_explicit_paragraph_boundary(self):
        units = _silero_units("Первый абзац.\n \nВторой абзац.", 950)
        self.assertEqual([u["text"] for u in units], ["Первый абзац.", "Второй абзац."])
        self.assertEqual([u["boundary_before"] for u in units], ["start", "paragraph"])

    def test_budget_only_splits_at_sentence_end_and_keeps_punctuation(self):
        sentences = ["Длина пути была 3.14 километра.", "Он спросил: «Все готовы?»", "Они ответили: «Да!»"]
        units = _silero_units(" ".join(sentences), 35)
        self.assertEqual([u["text"] for u in units], sentences)
        self.assertEqual([u["boundary_before"] for u in units], ["start", "sentence_budget", "sentence_budget"])

    def test_oversized_single_sentence_is_rejected_not_cut(self):
        with self.assertRaisesRegex(ValueError, "Audio is never truncated"):
            _silero_units("Очень длинная фраза, " * 12 + "которая продолжается до конца.", 100)

    def test_abbreviations_and_initials_do_not_create_budget_boundary(self):
        text = "Проводник А. Б. Иванов вышел из г. Москвы. Потом он вернулся домой."
        units = _silero_units(text, 43)
        self.assertEqual([u["text"] for u in units], [
            "Проводник А. Б. Иванов вышел из г. Москвы.", "Потом он вернулся домой."])

    def test_preserves_all_end_samples_and_one_gain_across_units(self):
        import numpy as np
        settings = {"leading_silence": .02, "trailing_silence": .03, "paragraph_silence": .01,
                    "volume": .8, "normalize_audio": True}
        # A loud first unit and quiet final unit must keep their relative level.
        pcm, boundaries, gain = _assemble_silero_audio(
            [np.array([.5, -.5, .25]), np.array([.1, -.1, .125])], 100, settings)
        self.assertAlmostEqual(gain, 1.6)
        self.assertEqual(len(pcm), 12)
        self.assertEqual([(b["start_sample"], b["end_sample"]) for b in boundaries], [(2, 5), (6, 9)])
        self.assertEqual(int(pcm[8]), int(.125 * gain * 32767))
        self.assertLess(abs(int(pcm[6])), abs(int(pcm[2])))
        self.assertTrue((pcm[9:] == 0).all())

    def test_single_unit_has_no_inserted_internal_silence(self):
        import numpy as np
        settings = {"leading_silence": 0, "trailing_silence": 0, "paragraph_silence": 3,
                    "volume": 1, "normalize_audio": False}
        pcm, boundaries, _ = _assemble_silero_audio([np.full(50, .25)], 100, settings)
        self.assertEqual(len(pcm), 50)
        self.assertTrue((pcm > 0).all())
        self.assertEqual(boundaries[0]["added_silence_before_samples"], 0)

    def test_invalid_generated_audio_is_not_published(self):
        import numpy as np
        settings = {"leading_silence": 0, "trailing_silence": 0, "paragraph_silence": 0,
                    "volume": .85, "normalize_audio": True}
        for invalid in ([float("nan")], [], [[.1]], [0, 0]):
            with self.subTest(invalid=invalid), self.assertRaises(ValueError):
                _assemble_silero_audio([np.array(invalid)], 100, settings)

    def test_stress_notation_preserves_words_and_existing_stress(self):
        self.assertEqual(_silero_stress("Руде\u0301ус, Ро\u0301кси, всё ещё, пош+ёл"),
                         "Руд+еус, Р+окси, всё ещё, пош+ёл")


@unittest.skipUnless(SILERO_MODEL.is_file() and importlib.util.find_spec("torch"),
                     "Local Silero/model not installed; no download during tests")
class SileroRealIntegrationTests(unittest.TestCase):
    def test_complete_paragraph_cache_settings_and_corruption(self):
        with tempfile.TemporaryDirectory(prefix="ai-video-silero-test-") as temp:
            root = Path(temp)
            config = {"engine": "silero", "model_path": SILERO_MODEL, "workspace_root": root,
                      "speaker": "aidar", "cpu_threads": 4}
            output = root / "block.one.wav"
            text = "Вот наш план: найти дорогу, помочь людям и вернуться домой."
            first = synthesize(text, output, config)
            self.assertEqual(first["engine"], "silero")
            self.assertEqual(first["synthesis_unit_count"], 1)
            self.assertEqual(first["samples_trimmed"], 0)
            self.assertGreater(first["duration_ms"], 500)
            self.assertEqual(first["clipped_samples"], 0)
            self.assertTrue(synthesize(text, output, config)["cache_hit"])
            changed_config = {**config, "volume": .7}
            changed = synthesize(text, output, changed_config)
            self.assertFalse(changed["cache_hit"])
            self.assertNotEqual(changed["request_hash"], first["request_hash"])
            revised = synthesize("Все вернулись домой.", output, changed_config)
            self.assertFalse(revised["cache_hit"])
            self.assertNotEqual(revised["text_hash"], first["text_hash"])
            with output.open("ab") as stream:
                stream.write(b"corrupt cache")
            repaired = synthesize("Все вернулись домой.", output, changed_config)
            self.assertFalse(repaired["cache_hit"])
            self.assertEqual(repaired["wav_hash"], sha256_file(output))
            self.assertTrue(synthesize("Все вернулись домой.", output, changed_config)["cache_hit"])


@unittest.skipUnless(MODEL.is_file() and importlib.util.find_spec("piper"), "Local Piper/model not installed; no download during tests")
class TTSRealIntegrationTests(unittest.TestCase):
    def test_opt_in_block_gain_keeps_sentences_samples_and_cache_identity(self):
        with tempfile.TemporaryDirectory(prefix="ai-video-piper-block-test-") as temp:
            root = Path(temp)
            config = {"model_path": MODEL, "workspace_root": root, "length_scale": 1.08}
            output = root / "whole.wav"
            text = "Проверим план: сначала помочь людям. Потом вернуться домой."
            legacy = synthesize(text, output, config)
            explicit_default = synthesize(text, output, {**config, "normalization_scope": "sentence"})
            self.assertTrue(explicit_default["cache_hit"])
            block_config = {**config, "normalization_scope": "block", "sentence_silence": .08}
            result = synthesize(text, output, block_config)
            self.assertFalse(result["cache_hit"])
            self.assertNotEqual(legacy["request_hash"], result["request_hash"])
            self.assertEqual(result["normalization_scope"], "block")
            self.assertEqual(result["punctuation_transform"], "none")
            self.assertEqual(result["synthesis_chunk_count"], 2)
            self.assertEqual(result["samples_trimmed"], 0)
            self.assertEqual(result["clipped_samples"], 0)
            chunks = result["synthesis_chunks"]
            rate = result["sample_rate_hz"]
            self.assertEqual(sum(c["generated_samples"] for c in chunks)
                             + sum(c["added_silence_before_samples"] for c in chunks)
                             + round(.1*rate) + round(.18*rate), result["frames"])
            self.assertEqual(chunks[-1]["end_sample"], result["frames"]-round(.18*rate))
            self.assertTrue(synthesize(text, output, block_config)["cache_hit"])

    def test_cache_text_settings_and_audio_corruption(self):
        with tempfile.TemporaryDirectory(prefix="ai-video-tts-test-") as temp:
            root = Path(temp)
            config = {"model_path": MODEL, "workspace_root": root}
            first = root / "segment_a.wav"
            second = root / "segment_b.wav"
            a = synthesize("Это первая реплика.", first, config)
            synthesize("Это вторая реплика.", second, config)
            self.assertGreater(a["duration_ms"], 200)
            self.assertEqual(a["channels"], 1)
            self.assertEqual(a["clipped_samples"], 0)
            self.assertFalse(a["cache_hit"])
            self.assertTrue(synthesize("Это первая реплика.", first, config)["cache_hit"])
            before_second = sha256_file(second)
            changed = synthesize("Первая реплика теперь изменена.", first, config)
            self.assertFalse(changed["cache_hit"])
            self.assertNotEqual(a["text_hash"], changed["text_hash"])
            self.assertEqual(before_second, sha256_file(second))
            self.assertTrue(synthesize("Это вторая реплика.", second, config)["cache_hit"])
            slower = synthesize("Первая реплика теперь изменена.", first, {**config, "length_scale": 1.3})
            self.assertFalse(slower["cache_hit"])
            self.assertNotEqual(slower["request_hash"], changed["request_hash"])
            with first.open("ab") as stream:
                stream.write(b"invalidated")
            repaired = synthesize("Первая реплика теперь изменена.", first, {**config, "length_scale": 1.3})
            self.assertFalse(repaired["cache_hit"])
            self.assertEqual(repaired["wav_hash"], sha256_file(first))
            metadata = json.loads(first.with_suffix(".wav.tts.json").read_text(encoding="utf-8"))
            self.assertEqual(metadata["request_hash"], repaired["request_hash"])


if __name__ == "__main__":
    unittest.main()
