"""Local text-to-speech with measured output and content-aware reuse.

The caller supplies an already installed model. This module never downloads,
installs, invokes a shell, or calls a speech service.
"""
from __future__ import annotations

import hashlib
import importlib.metadata
import json
import math
import os
from pathlib import Path
import re
import shutil
import tempfile
import time
import wave
from typing import Any

_VOICES: dict[tuple[str, str, bool], Any] = {}
_SILERO_VOICES: dict[tuple[str, str, int], Any] = {}


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def wav_info(path: str | Path) -> dict[str, Any]:
    """Measure PCM audio; reject empty or invalid synthesized output."""
    import numpy as np
    with wave.open(str(path), "rb") as stream:
        channels = stream.getnchannels()
        rate = stream.getframerate()
        frames = stream.getnframes()
        width = stream.getsampwidth()
        if frames <= 0 or rate <= 0 or width != 2:
            raise ValueError("Expected nonempty 16-bit PCM WAV")
        samples = np.frombuffer(stream.readframes(frames), dtype="<i2").astype(np.float64) / 32768
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples * samples)))
    if rms <= 0:
        raise ValueError("Synthesized WAV is silent")
    return {
        "duration_ms": round(frames * 1000 / rate),
        "duration_seconds": frames / rate,
        "sample_rate_hz": rate,
        "channels": channels,
        "sample_width_bytes": width,
        "frames": frames,
        "peak_dbfs": 20 * math.log10(peak) if peak else -120.0,
        "rms_dbfs": 20 * math.log10(rms),
        "clipped_samples": int(np.count_nonzero(np.abs(samples) >= 32767 / 32768)),
    }


def synthesize(text: str, output: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Select an installed local engine; Piper remains the compatible default.

    Silero is opt-in with ``engine='silero'`` and a local ``model_path`` (.pt).
    It receives complete paragraphs, not independently restarted clauses.
    Neither backend downloads a model or silently falls back to another voice.
    """
    if not isinstance(config, dict):
        raise ValueError("TTS config must be an object")
    engine = config.get("engine", "piper")
    if engine == "silero":
        return _synthesize_silero(text, output, config)
    if engine != "piper":
        raise ValueError(f"Unsupported local TTS engine: {engine!r}")
    return _synthesize_piper(text, output, {key: value for key, value in config.items() if key != "engine"})


def _synthesize_piper(text: str, output: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    """Synthesize UTF-8 text to a WAV and return measurements and provenance.

    Required config: ``model_path``. Optional: voice_id, language, use_cuda,
    length_scale, noise_scale, noise_w_scale, volume, normalize_audio,
    speaker_id, sentence_silence, leading_silence, trailing_silence,
    normalization_scope ('sentence' by default, or opt-in 'block').
    Adjacent .tts.json metadata allow unchanged audio to be reused. Both the
    inputs and cached WAV hash are verified before returning cache_hit=True.
    """
    if not isinstance(text, str) or not text.strip():
        raise ValueError("TTS text must be a nonempty string")
    if len(text) > 20000:
        raise ValueError("A TTS block may contain at most 20000 characters")
    allowed_keys = {"model_path", "config_path", "workspace_root", "voice_id", "language", "use_cuda",
                    "length_scale", "noise_scale", "noise_w_scale", "volume", "normalize_audio", "speaker_id",
                    "sentence_silence", "leading_silence", "trailing_silence", "espeak_data_dir", "normalization_scope"}
    unknown_keys = set(config) - allowed_keys
    if unknown_keys:
        raise ValueError(f"Unknown TTS settings: {sorted(unknown_keys)}")
    normalization_scope = config.get("normalization_scope", "sentence")
    if normalization_scope not in ("sentence", "block"):
        raise ValueError("Piper normalization_scope must be sentence or block")
    root = Path(config.get("workspace_root", Path.cwd())).resolve()
    output = Path(output).resolve()
    if not output.is_relative_to(root) or output.suffix.lower() != ".wav":
        raise ValueError("TTS output must be a .wav file within workspace_root")
    model = Path(config["model_path"])
    model_config = Path(config.get("config_path", str(model) + ".json"))
    if not model.is_file() or not model_config.is_file():
        raise FileNotFoundError(f"Missing local Piper model or config: {model}")
    language = config.get("language", "ru-RU")
    if language not in ("ru", "ru-RU", "ru_RU"):
        raise ValueError("This MVP voice adapter has been tested only for Russian")
    output.parent.mkdir(parents=True, exist_ok=True)
    sidecar = output.with_suffix(output.suffix + ".tts.json")
    model_hash = sha256_file(model)
    model_config_hash = sha256_file(model_config)
    engine_version = importlib.metadata.version("piper-tts")
    settings = {
        "length_scale": float(config.get("length_scale", 1.04)),
        "noise_scale": float(config.get("noise_scale", 0.667)),
        "noise_w_scale": float(config.get("noise_w_scale", 0.8)),
        "volume": float(config.get("volume", 0.85)),
        "normalize_audio": bool(config.get("normalize_audio", True)),
        "speaker_id": config.get("speaker_id"),
        "sentence_silence": float(config.get("sentence_silence", 0.22)),
        "leading_silence": float(config.get("leading_silence", 0.10)),
        "trailing_silence": float(config.get("trailing_silence", 0.18)),
    }
    for key in ("length_scale", "volume"):
        if not math.isfinite(settings[key]) or not 0 < settings[key] <= 3:
            raise ValueError(f"Invalid {key}")
    for key in ("noise_scale", "noise_w_scale", "sentence_silence", "leading_silence", "trailing_silence"):
        if not math.isfinite(settings[key]) or not 0 <= settings[key] <= 5:
            raise ValueError(f"Invalid {key}")
    if normalization_scope == "block" and settings["volume"] > 1:
        raise ValueError("Block-normalized Piper volume must be at most 1")
    text_hash = hashlib.sha256(text.encode("utf-8")).hexdigest()
    request = {
        "text_hash": text_hash,
        "model_hash": model_hash,
        "model_config_hash": model_config_hash,
        "engine": "piper",
        "engine_version": engine_version,
        "adapter_version": 1,
        "voice_id": config.get("voice_id", model.stem),
        "language": language,
        "generation_settings": settings,
        "use_cuda": bool(config.get("use_cuda", False)),
    }
    if normalization_scope == "block":
        # Preserve the original/default request identity; this new processing
        # mode invalidates only explicitly opted-in audio.
        request.update(adapter_version=2, normalization_scope="block", punctuation_transform="none")
    request_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    if output.is_file() and sidecar.is_file():
        try:
            cached = json.loads(sidecar.read_text(encoding="utf-8"))
            if cached.get("request_hash") == request_hash and sha256_file(output) == cached.get("wav_hash"):
                return {**cached, "path": str(output), "cache_hit": True}
        except (ValueError, OSError):
            pass
    from piper import PiperVoice, SynthesisConfig
    import numpy as np
    voice_key = (model_hash, model_config_hash, request["use_cuda"])
    started = time.perf_counter()
    if voice_key not in _VOICES:
        from piper.phonemize_espeak import ESPEAK_DATA_DIR
        espeak_data = Path(config.get("espeak_data_dir", ESPEAK_DATA_DIR))
        # The bundled espeak C library on Windows cannot open Unicode paths.
        # Stage only its installed data into the user's temporary directory.
        if os.name == "nt" and not str(espeak_data).isascii():
            staged = Path(tempfile.gettempdir()) / f"ai-video-espeak-{engine_version}" / "espeak-ng-data"
            if not str(staged).isascii():
                raise ValueError("Piper on Windows requires an ASCII espeak_data_dir")
            if not (staged / "phontab").is_file():
                shutil.copytree(espeak_data, staged, dirs_exist_ok=True)
            espeak_data = staged
        _VOICES[voice_key] = PiperVoice.load(model, config_path=model_config, use_cuda=request["use_cuda"], espeak_data_dir=espeak_data)
    voice = _VOICES[voice_key]
    synthesis_settings = {k: v for k, v in settings.items() if not k.endswith("silence")}
    if normalization_scope == "block":
        synthesis_settings.update(normalize_audio=False, volume=1.0)
    syn_config = SynthesisConfig(**synthesis_settings)
    fd, temporary = tempfile.mkstemp(prefix=output.stem + ".", suffix=".wav", dir=output.parent)
    os.close(fd)
    try:
        block_metadata = {}
        with wave.open(temporary, "wb") as stream:
            stream.setnchannels(1)
            stream.setsampwidth(2)
            stream.setframerate(voice.config.sample_rate)
            silence = lambda seconds: np.zeros(round(seconds * voice.config.sample_rate), dtype="<i2").tobytes()
            if normalization_scope == "block":
                # Keep Piper's real sentence boundaries. Do not join phonemes
                # across sentences or change punctuation/stress. Obtain raw
                # floats before Piper's per-sentence clipping/normalization.
                arrays = []
                for phonemes in voice.phonemize(text):
                    if not phonemes:
                        continue
                    raw = voice.phoneme_ids_to_audio(voice.phonemes_to_ids(phonemes), syn_config)
                    arrays.append(raw[0] if isinstance(raw, tuple) else raw)
                chunks = len(arrays)
                if not chunks:
                    raise ValueError("Piper produced no audio chunks")
                pcm, boundaries, gain = _assemble_silero_audio(arrays, voice.config.sample_rate,
                    {**settings, "paragraph_silence": settings["sentence_silence"]})
                stream.writeframes(pcm.tobytes())
                block_metadata = {"synthesis_chunk_count": chunks, "synthesis_chunks": boundaries,
                                  "applied_block_gain": gain, "samples_trimmed": 0}
            else:
                stream.writeframes(silence(settings["leading_silence"]))
                chunks = 0
                for chunk in voice.synthesize(text, syn_config=syn_config):
                    if chunks:
                        stream.writeframes(silence(settings["sentence_silence"]))
                    stream.writeframes(chunk.audio_int16_bytes)
                    chunks += 1
                stream.writeframes(silence(settings["trailing_silence"]))
        if not chunks:
            raise ValueError("Piper produced no audio chunks")
        result = {
            **request,
            **wav_info(temporary),
            "path": str(output),
            "request_hash": request_hash,
            "wav_hash": sha256_file(temporary),
            "cache_hit": False,
            "synthesis_seconds": round(time.perf_counter() - started, 3),
            "listening_review": "not_performed",
            **block_metadata,
        }
        os.replace(temporary, output)
        temp_json = sidecar.with_suffix(sidecar.suffix + ".tmp")
        temp_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temp_json, sidecar)
        return result
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def _silero_units(text: str, max_chars: int) -> list[dict[str, str]]:
    """Keep paragraph context; split an oversized paragraph at sentence ends.

    Commas, colons, semicolons, dashes and line wrapping are never boundaries.
    An oversized individual sentence is rejected instead of being cut or lost.
    """
    units: list[dict[str, str]] = []
    for paragraph in re.split(r"\n\s*\n", text):
        paragraph = re.sub(r"\s+", " ", paragraph).strip()
        if not paragraph:
            continue
        if len(paragraph) <= max_chars:
            units.append({"text": paragraph, "boundary_before": "paragraph" if units else "start"})
            continue
        sentences, start = [], 0
        for ending in re.finditer(r"[.!?…]+[»”\"')\]]*(?=\s|$)", paragraph):
            # Avoid common false sentence boundaries in Russian prose. The
            # budget splitter is conservative: a long unresolved sentence is
            # rejected below instead of restarting after an abbreviation.
            if ending.group() == ".":
                prefix_word = re.search(r"([\w.]+)$", paragraph[:ending.start()])
                token = prefix_word.group(1).lower() if prefix_word else ""
                abbreviations = {"г", "гг", "ул", "д", "им", "т", "е", "др", "пр", "см", "рис", "стр",
                                 "гл", "руб", "коп", "кв", "мин", "сек", "ч", "проф", "акад", "тов",
                                 "н", "э", "т.д", "т.п", "т.е", "т.к", "т.н"}
                if token in abbreviations or len(token) == 1 and token.isalpha() or token.isdigit():
                    continue
            sentence = paragraph[start:ending.end()].strip()
            if sentence:
                sentences.append(sentence)
            start = ending.end()
        if paragraph[start:].strip():
            sentences.append(paragraph[start:].strip())
        pending = ""
        reason = "paragraph" if units else "start"
        for sentence in sentences:
            if len(sentence) > max_chars:
                raise ValueError("Silero sentence exceeds max_chars; shorten the source sentence. Audio is never truncated.")
            combined = (pending + " " + sentence).strip()
            if len(combined) > max_chars:
                units.append({"text": pending, "boundary_before": reason})
                pending, reason = sentence, "sentence_budget"
            else:
                pending = combined
        if pending:
            units.append({"text": pending, "boundary_before": reason})
    return units


def _silero_stress(text: str) -> str:
    """Translate the existing Russian acute-accent notation to Silero's +."""
    return re.sub("([аеёиоуыэюяАЕЁИОУЫЭЮЯ])\u0301", r"+\1", text)


def _assemble_silero_audio(arrays: list[Any], rate: int, settings: dict[str, Any]):
    """Preserve every generated sample, then apply one gain to the full block."""
    import numpy as np
    pieces = [np.zeros(round(settings["leading_silence"] * rate), dtype=np.float32)]
    boundaries = []
    cursor = len(pieces[0])
    for index, raw in enumerate(arrays):
        audio = np.asarray(raw, dtype=np.float32)
        if audio.ndim != 1 or not audio.size or not np.isfinite(audio).all():
            raise ValueError("Local TTS produced empty, non-finite, or non-mono audio")
        pause = round(settings["paragraph_silence"] * rate) if index else 0
        if pause:
            pieces.append(np.zeros(pause, dtype=np.float32))
            cursor += pause
        boundaries.append({"index": index, "start_sample": cursor, "end_sample": cursor + len(audio),
                           "generated_samples": len(audio), "added_silence_before_samples": pause,
                           "raw_peak": float(np.max(np.abs(audio)))})
        pieces.append(audio)
        cursor += len(audio)
    pieces.append(np.zeros(round(settings["trailing_silence"] * rate), dtype=np.float32))
    complete = np.concatenate(pieces)
    peak = float(np.max(np.abs(complete)))
    if peak < 1e-8:
        raise ValueError("Local TTS produced silent audio")
    gain = settings["volume"] / peak if settings["normalize_audio"] else settings["volume"]
    complete = complete * gain
    # A single scalar changes level without destroying relative phrase dynamics.
    pcm = np.clip(complete * 32767, -32767, 32767).astype("<i2")
    return pcm, boundaries, gain


def _synthesize_silero(text: str, output: str | Path, config: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(text, str) or not text.strip():
        raise ValueError("TTS text must be a nonempty string")
    if len(text) > 20000:
        raise ValueError("A TTS block may contain at most 20000 characters")
    allowed = {"engine", "model_path", "workspace_root", "voice_id", "language", "use_cuda", "speaker",
               "sample_rate", "cpu_threads", "max_chars", "volume", "normalize_audio", "put_accent", "put_yo",
               "paragraph_silence", "leading_silence", "trailing_silence"}
    unknown = set(config) - allowed
    if unknown:
        raise ValueError(f"Unknown Silero settings: {sorted(unknown)}")
    root = Path(config.get("workspace_root", Path.cwd())).resolve()
    output = Path(output).resolve()
    if not output.is_relative_to(root) or output.suffix.lower() != ".wav":
        raise ValueError("TTS output must be a .wav file within workspace_root")
    model_path = Path(config["model_path"]).resolve()
    if not model_path.is_file() or model_path.suffix.lower() != ".pt":
        raise FileNotFoundError(f"Missing local Silero .pt model: {model_path}")
    language = config.get("language", "ru-RU")
    if language not in ("ru", "ru-RU", "ru_RU"):
        raise ValueError("This Silero adapter has been tested only for Russian")
    settings = {"speaker": config.get("speaker", "aidar"), "sample_rate": config.get("sample_rate", 48000),
                "cpu_threads": config.get("cpu_threads", 4), "max_chars": config.get("max_chars", 950),
                "volume": float(config.get("volume", .85)), "normalize_audio": config.get("normalize_audio", True),
                "put_accent": config.get("put_accent", True), "put_yo": config.get("put_yo", True),
                "paragraph_silence": float(config.get("paragraph_silence", .14)),
                "leading_silence": float(config.get("leading_silence", .10)),
                "trailing_silence": float(config.get("trailing_silence", .18))}
    for name in ("sample_rate", "cpu_threads", "max_chars"):
        if type(settings[name]) is not int:
            raise ValueError(f"{name} must be an integer")
    if settings["sample_rate"] not in (8000, 24000, 48000):
        raise ValueError("Silero sample_rate must be 8000, 24000, or 48000")
    if not 1 <= settings["cpu_threads"] <= 16 or not 100 <= settings["max_chars"] <= 1000:
        raise ValueError("Silero cpu_threads must be 1..16; max_chars must be 100..1000")
    if not isinstance(settings["speaker"], str) or not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", settings["speaker"]):
        raise ValueError("Invalid Silero speaker")
    for name in ("normalize_audio", "put_accent", "put_yo"):
        if type(settings[name]) is not bool:
            raise ValueError(f"{name} must be a boolean")
    if type(config.get("use_cuda", False)) is not bool:
        raise ValueError("use_cuda must be a boolean")
    if not math.isfinite(settings["volume"]) or not 0 < settings["volume"] <= 1:
        raise ValueError("Silero volume must be greater than 0 and at most 1")
    for name in ("paragraph_silence", "leading_silence", "trailing_silence"):
        if not math.isfinite(settings[name]) or not 0 <= settings[name] <= 5:
            raise ValueError(f"Invalid {name}")
    # Validate boundaries before model loading or creating output artifacts.
    units = _silero_units(_silero_stress(text), settings["max_chars"])
    model_hash = sha256_file(model_path)
    torch_version = importlib.metadata.version("torch")
    device = "cuda" if config.get("use_cuda", False) else "cpu"
    request = {"text_hash": hashlib.sha256(text.encode("utf-8")).hexdigest(), "model_hash": model_hash,
               "model_config_hash": None, "engine": "silero", "engine_version": f"torch-{torch_version}",
               "adapter_version": 2, "voice_id": config.get("voice_id", f"{model_path.stem}:{settings['speaker']}"),
               "language": language, "generation_settings": settings, "use_cuda": device == "cuda",
               "normalization_scope": "block", "segmentation_policy": "paragraph_then_sentence_budget_v1"}
    request_hash = hashlib.sha256(json.dumps(request, sort_keys=True).encode()).hexdigest()
    sidecar = output.with_suffix(output.suffix + ".tts.json")
    if output.is_file() and sidecar.is_file():
        try:
            cached = json.loads(sidecar.read_text(encoding="utf-8"))
            if cached.get("request_hash") == request_hash and sha256_file(output) == cached.get("wav_hash"):
                return {**cached, "path": str(output), "cache_hit": True}
        except (ValueError, OSError):
            pass
    import torch
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("Silero CUDA was requested but is unavailable; no CPU fallback")
    torch.set_num_threads(settings["cpu_threads"])
    voice_key = (model_hash, device, settings["cpu_threads"])
    started = time.perf_counter()
    if voice_key not in _SILERO_VOICES:
        # Python opens Unicode paths correctly on Windows; the C++ filename
        # reader in some PyTorch builds does not. Use its supported file object.
        with model_path.open("rb") as model_stream:
            voice = torch.package.PackageImporter(model_stream).load_pickle("tts_models", "model")
        voice.to(torch.device(device))
        _SILERO_VOICES[voice_key] = voice
    voice = _SILERO_VOICES[voice_key]
    if settings["speaker"] not in voice.speakers:
        raise ValueError(f"Speaker {settings['speaker']!r} is absent from this Silero model: {voice.speakers}")
    arrays = []
    with torch.inference_mode():
        for unit in units:
            audio = voice.apply_tts(text=unit["text"], speaker=settings["speaker"], sample_rate=settings["sample_rate"],
                                    put_accent=settings["put_accent"], put_yo=settings["put_yo"])
            arrays.append(audio.detach().cpu().numpy())
    pcm, boundaries, gain = _assemble_silero_audio(arrays, settings["sample_rate"], settings)
    for boundary, unit in zip(boundaries, units):
        boundary.update({"text_hash": hashlib.sha256(unit["text"].encode("utf-8")).hexdigest(),
                         "character_count": len(unit["text"]), "boundary_before": unit["boundary_before"],
                         "start_seconds": boundary["start_sample"] / settings["sample_rate"],
                         "end_seconds": boundary["end_sample"] / settings["sample_rate"]})
    output.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=output.stem + ".", suffix=".wav", dir=output.parent)
    os.close(fd)
    try:
        with wave.open(temporary, "wb") as stream:
            stream.setparams((1, 2, settings["sample_rate"], 0, "NONE", "not compressed"))
            stream.writeframes(pcm.tobytes())
        result = {**request, **wav_info(temporary), "path": str(output), "request_hash": request_hash,
                  "wav_hash": sha256_file(temporary), "cache_hit": False,
                  "synthesis_seconds": round(time.perf_counter() - started, 3), "synthesis_unit_count": len(units),
                  "synthesis_units": boundaries, "applied_block_gain": gain, "samples_trimmed": 0,
                  "listening_review": "not_performed"}
        os.replace(temporary, output)
        temporary_json = sidecar.with_suffix(sidecar.suffix + ".tmp")
        temporary_json.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        os.replace(temporary_json, sidecar)
        return result
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
