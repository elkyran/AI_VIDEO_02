from copy import deepcopy
import hashlib
import json
from pathlib import Path
import tempfile
import unittest

from app.production_profile import load_profile, check_runtime
from app.registry import StudioError


class ProfileTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="Профиль Ruslan ")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        source = Path(__file__).resolve().parents[1]
        self.profile = json.loads((source / "config/production_profile.json").read_text(encoding="utf-8"))
        voice = self.profile["voice"]
        self.write(voice["model_ref"], b"test model")
        self.write(voice["model_ref"] + ".json", b"{}")
        voice["model_sha256"] = self.sha(voice["model_ref"])
        voice["model_config_sha256"] = self.sha(voice["model_ref"] + ".json")
        segment = {"engine": voice["engine"], "voice_id": voice["voice_id"],
                   "model_hash": voice["model_sha256"], "model_config_hash": voice["model_config_sha256"],
                   "engine_version": voice["engine_version"], "generation_settings": deepcopy(voice["settings"]),
                   **{k: voice[k] for k in ("sample_rate_hz", "normalization_scope", "punctuation_transform", "use_cuda")}}
        for name, value in (("base_manifest", {"segments": [segment]}), ("final_manifest", {"segments": [segment]}),
                            ("timeline", {k: self.profile["video"][k] for k in ("width", "height", "fps")})):
            ref = self.profile["provenance"][name + "_ref"]
            self.write_json(ref, value)
            self.profile["provenance"][name + "_sha256"] = self.sha(ref)
        for ref in self.profile["provenance"]["style_refs"]:
            self.write(ref, b"historical evidence")
        self.runtime = {"tts_model": str(self.root / voice["model_ref"]), "tts_engine": "piper",
                        "tts_normalization_scope": "block", "video_encoder": self.profile["video"]["encoder"],
                        "render_threads": self.profile["video"]["render_threads"],
                        **{k: self.profile[k] for k in ("pronunciation_lexicon", "pronunciation_terms")},
                        **{"tts_" + k: v for k, v in voice["settings"].items()}}
        self.save()

    def write(self, ref, data):
        path = self.root / ref
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)

    def write_json(self, ref, data):
        self.write(ref, json.dumps(data, ensure_ascii=False).encode("utf-8"))

    def sha(self, ref):
        return hashlib.sha256((self.root / ref).read_bytes()).hexdigest()

    def save(self):
        self.write_json("config/production_profile.json", self.profile)
        self.write_json("workspace/studio/runtime.json", self.runtime)

    def test_load_and_runtime_match_without_synthesis(self):
        profile = load_profile(self.root)
        check_runtime(profile, self.root, self.root / "workspace/studio")

    def test_unknown_voice_and_unknown_fields_rejected(self):
        self.profile["voice"]["voice_id"] = "aidar"
        self.save()
        with self.assertRaises(ValueError):
            load_profile(self.root)
        self.profile["voice"]["voice_id"] = "ru_RU-ruslan-medium"
        self.profile["silent_override"] = True
        self.save()
        with self.assertRaises(ValueError):
            load_profile(self.root)

    def test_unapproved_setting_or_changed_evidence_rejected(self):
        self.profile["voice"]["settings"]["length_scale"] = 1.9
        self.save()
        with self.assertRaisesRegex(ValueError, "inconsistent"):
            load_profile(self.root)
        self.write(self.profile["provenance"]["base_manifest_ref"], b"changed evidence")
        with self.assertRaisesRegex(ValueError, "evidence changed"):
            load_profile(self.root)

    def test_runtime_drift_never_silently_changes_voice_or_settings(self):
        profile = load_profile(self.root)
        for key, value in (("tts_engine", "silero"), ("tts_length_scale", 1.5), ("video_encoder", "libx264")):
            old = self.runtime[key]
            self.runtime[key] = value
            self.save()
            with self.assertRaises(ValueError):
                check_runtime(profile, self.root, self.root / "workspace/studio")
            self.runtime[key] = old

    def test_evidence_cannot_escape_project_or_be_empty(self):
        ref = self.profile["provenance"]["base_manifest_ref"]
        self.write_json(ref, {"segments": []})
        self.profile["provenance"]["base_manifest_sha256"] = self.sha(ref)
        self.save()
        with self.assertRaisesRegex(ValueError, "nonempty"):
            load_profile(self.root)
        self.profile["provenance"]["base_manifest_ref"] = "../outside.json"
        self.save()
        with self.assertRaises(StudioError):
            load_profile(self.root)


if __name__ == "__main__":
    unittest.main()
