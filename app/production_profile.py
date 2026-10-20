"""Validate the approved local profile without loading a speech model."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from .registry import StudioError, content_hash, workspace_path


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class SpeechSettings(StrictModel):
    length_scale: float = Field(gt=0, le=3)
    noise_scale: float = Field(ge=0, le=5)
    noise_w_scale: float = Field(ge=0, le=5)
    volume: float = Field(gt=0, le=1)
    normalize_audio: bool
    speaker_id: None
    sentence_silence: float = Field(ge=0, le=5)
    leading_silence: float = Field(ge=0, le=5)
    trailing_silence: float = Field(ge=0, le=5)


class Voice(StrictModel):
    engine: Literal["piper"]
    voice_id: Literal["ru_RU-ruslan-medium"]
    model_ref: str
    model_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    model_config_sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    engine_version: str
    settings: SpeechSettings
    sample_rate_hz: int = Field(gt=0)
    normalization_scope: Literal["block"]
    punctuation_transform: Literal["none"]
    use_cuda: Literal[False]


class Video(StrictModel):
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    fps: int = Field(gt=0)
    encoder: Literal["libx264", "h264_nvenc"]
    render_threads: int = Field(gt=0)


class Provenance(StrictModel):
    base_manifest_ref: str
    base_manifest_sha256: str
    final_manifest_ref: str
    final_manifest_sha256: str
    timeline_ref: str
    timeline_sha256: str
    runtime_source_ref: str
    style_refs: list[str] = Field(min_length=1)
    approval_basis: str
    fitting_note: str


class Profile(StrictModel):
    schema_version: Literal[1]
    profile_id: str = Field(min_length=1)
    version: int = Field(gt=0)
    provenance: Provenance
    voice: Voice
    pronunciation_lexicon: dict[str, str]
    pronunciation_terms: list[str]
    video: Video
    original_policy: Literal["immutable; new artifacts only; preserve manual edits"]
    required_checks: list[str] = Field(min_length=1)
    modes: dict[Literal["production", "development"], str]


def load_profile(project_root: Path, relative="config/production_profile.json") -> dict:
    root = Path(project_root).resolve()
    profile = Profile.model_validate_json(workspace_path(root, relative).read_text(encoding="utf-8-sig")).model_dump()
    origin = profile["provenance"]
    documents = {}
    for name in ("base_manifest", "final_manifest", "timeline"):
        path = workspace_path(root, origin[name + "_ref"])
        if content_hash(path) != origin[name + "_sha256"]:
            raise ValueError(f"Profile evidence changed: {name}")
        documents[name] = json.loads(path.read_text(encoding="utf-8-sig"))
    for ref in [origin["runtime_source_ref"], *origin["style_refs"]]:
        if not workspace_path(root, ref).is_file():
            raise ValueError(f"Missing profile reference: {ref}")
    voice = profile["voice"]
    workspace_path(root, voice["model_ref"])
    if not documents["base_manifest"].get("segments") or not documents["final_manifest"].get("segments"):
        raise ValueError("Profile requires nonempty narration evidence")
    for segment in documents["base_manifest"]["segments"]:
        expected = {"engine": voice["engine"], "voice_id": voice["voice_id"],
                    "model_hash": voice["model_sha256"], "model_config_hash": voice["model_config_sha256"],
                    "engine_version": voice["engine_version"], "generation_settings": voice["settings"],
                    "sample_rate_hz": voice["sample_rate_hz"], "normalization_scope": voice["normalization_scope"],
                    "punctuation_transform": voice["punctuation_transform"], "use_cuda": voice["use_cuda"]}
        if any(segment.get(key) != value for key, value in expected.items()):
            raise ValueError("Voice/settings are inconsistent with the recorded base synthesis")
    if any(s["voice_id"] != voice["voice_id"] for s in documents["final_manifest"]["segments"]):
        raise ValueError("Final narration contains a different voice")
    if any(documents["timeline"][key] != profile["video"][key] for key in ("width", "height", "fps")):
        raise ValueError("Video format differs from the recorded timeline")
    if set(profile["modes"]) != {"production", "development"}:
        raise ValueError("Both operating modes are required")
    return profile


def check_runtime(profile: dict, project_root: Path, workspace_root: Path) -> None:
    """Guard new production wrappers; legacy CLI intentionally retains its contract."""
    config = json.loads((Path(workspace_root) / "runtime.json").read_text(encoding="utf-8-sig"))
    voice = profile["voice"]
    model = Path(config.get("tts_model", ""))
    if not model.is_absolute():
        raise ValueError("Existing studio runtime requires an absolute model locator")
    if config.get("tts_engine", "piper") != voice["engine"] or model.resolve() != workspace_path(Path(project_root), voice["model_ref"]):
        raise ValueError("Unapproved voice/model in runtime")
    if content_hash(model) != voice["model_sha256"] or content_hash(Path(str(model) + ".json")) != voice["model_config_sha256"]:
        raise ValueError("Runtime model identity differs from the approved profile")
    # Effective defaults of the unchanged operations.py -> tts.py path.
    defaults = {"length_scale": 1.08, "noise_scale": .667, "noise_w_scale": .8,
                "volume": .85, "normalize_audio": True, "speaker_id": None,
                "sentence_silence": .22, "leading_silence": .1, "trailing_silence": .18}
    for key, value in voice["settings"].items():
        actual = None if key == "speaker_id" else config.get("tts_" + key, defaults[key])
        if actual != value:
            raise ValueError(f"Runtime/profile mismatch: {key}")
    if config.get("tts_normalization_scope", "sentence") != voice["normalization_scope"]:
        raise ValueError("Runtime/profile mismatch: normalization_scope")
    for key in ("pronunciation_lexicon", "pronunciation_terms"):
        if config.get(key, {} if key.endswith("lexicon") else []) != profile[key]:
            raise ValueError(f"Runtime/profile mismatch: {key}")
    for key, default in (("video_encoder", "libx264"), ("render_threads", 1)):
        profile_key = "encoder" if key == "video_encoder" else key
        if config.get(key, default) != profile["video"][profile_key]:
            raise ValueError(f"Runtime/profile mismatch: {key}")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path.cwd())
    parser.add_argument("--profile", default="config/production_profile.json")
    parser.add_argument("--check-runtime", action="store_true")
    parser.add_argument("--workspace", default="workspace/studio")
    args = parser.parse_args(argv)
    try:
        profile = load_profile(args.root, args.profile)
        if args.check_runtime:
            check_runtime(profile, args.root, workspace_path(args.root, args.workspace))
        print(json.dumps({"valid": True, "profile_id": profile["profile_id"], "version": profile["version"],
                          "runtime_checked": args.check_runtime}))
        return 0
    except (ValueError, OSError, StudioError) as exc:
        print(json.dumps({"valid": False, "error": str(exc)}, ensure_ascii=True))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
