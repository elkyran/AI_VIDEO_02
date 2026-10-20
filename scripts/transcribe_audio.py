"""Offline narration QA and timestamped ASR subtitles for a generic manifest.

Manifest paths are relative to the manifest's directory. The optional
workspace_root bounds all audio inputs. No models are downloaded by this worker.
Primary subtitles align the original script to ASR word timing; raw recognition
is retained separately. Inferred/interpolated timing is reported explicitly.
This is text-to-timestamp alignment, not exact acoustic forced alignment.
"""
from __future__ import annotations
import argparse
from contextlib import contextmanager
import json
import math
import os
from pathlib import Path
import re
import sys
import textwrap
import time
import unicodedata
import wave


def normalized_words(text: str) -> list[str]:
    text = "".join(c for c in unicodedata.normalize("NFD", text.lower().replace("ё", "е")) if c not in ("\u0301", "\u0341"))
    text = unicodedata.normalize("NFC", text)
    return re.findall(r"[а-яa-z0-9]+", text)


def word_error_rate(expected: str, recognized: str) -> dict:
    reference, hypothesis = normalized_words(expected), normalized_words(recognized)
    rows, cols = len(reference), len(hypothesis)
    dp = [[0] * (cols + 1) for _ in range(rows + 1)]
    for i in range(rows + 1): dp[i][0] = i
    for j in range(cols + 1): dp[0][j] = j
    for i in range(1, rows + 1):
        for j in range(1, cols + 1):
            dp[i][j] = min(dp[i-1][j] + 1, dp[i][j-1] + 1, dp[i-1][j-1] + (reference[i-1] != hypothesis[j-1]))
    i, j = rows, cols
    edits = []
    while i or j:
        if i and j and reference[i-1] == hypothesis[j-1] and dp[i][j] == dp[i-1][j-1]:
            i, j = i-1, j-1
        elif i and j and dp[i][j] == dp[i-1][j-1] + 1:
            edits.append({"operation": "substitution", "expected": reference[i-1], "recognized": hypothesis[j-1]})
            i, j = i-1, j-1
        elif i and dp[i][j] == dp[i-1][j] + 1:
            edits.append({"operation": "deletion", "expected": reference[i-1], "recognized": ""})
            i -= 1
        else:
            edits.append({"operation": "insertion", "expected": "", "recognized": hypothesis[j-1]})
            j -= 1
    edits.reverse()
    return {"wer": round(dp[rows][cols] / rows, 6) if rows else (0.0 if not cols else 1.0),
            "reference_word_count": rows, "recognized_word_count": cols,
            "substitutions": sum(e["operation"] == "substitution" for e in edits),
            "deletions": sum(e["operation"] == "deletion" for e in edits),
            "insertions": sum(e["operation"] == "insertion" for e in edits), "edits": edits}


def measure_wav(path: Path) -> dict:
    import numpy as np
    with wave.open(str(path), "rb") as source:
        rate, count, channels, width = source.getframerate(), source.getnframes(), source.getnchannels(), source.getsampwidth()
        if width != 2 or not count:
            raise ValueError("QA requires nonempty 16-bit PCM WAV")
        samples = np.frombuffer(source.readframes(count), dtype="<i2").astype(np.float64) / 32768
    peak = float(np.max(np.abs(samples)))
    rms = float(np.sqrt(np.mean(samples * samples)))
    return {"duration_seconds": count/rate, "sample_rate_hz": rate, "channels": channels,
            "peak_dbfs": 20 * math.log10(peak) if peak else -120.0,
            "rms_dbfs": 20 * math.log10(rms) if rms else -120.0,
            "clipped_sample_fraction": float(np.mean(np.abs(samples) >= 32767/32768))}


def timestamp(seconds: float) -> str:
    total = max(0, round(seconds * 1000))
    hours, total = divmod(total, 3600000)
    minutes, total = divmod(total, 60000)
    seconds, ms = divmod(total, 1000)
    return f"{hours:02}:{minutes:02}:{seconds:02},{ms:03}"


def align_script_words(text: str, asr_words: list[dict], block_start: float, duration: float) -> dict:
    """Map script tokens to ASR anchors, explicitly estimating missing times.

    Substitutions retain the recognized word's interval. Source words without
    an ASR anchor share the gap between neighboring anchors. If no gap exists,
    the neighboring intervals are redistributed and marked as interpolated.
    With no speech timestamps, no timed words or captions are fabricated.
    """
    matches = list(re.finditer(r"[^\W_]+(?:[\u0301\u0341][^\W_]*)*", text, re.UNICODE))
    source = []
    for index, match in enumerate(matches):
        caption_start = 0 if index == 0 else match.start()
        caption_end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        source.append({"word": match.group(), "caption_text": text[caption_start:caption_end],
                       "source_token_index": index, "source_char_start": match.start(), "source_char_end": match.end(),
                       "normalized": "".join(normalized_words(match.group()))})
    observed = []
    lower, upper = math.ceil(block_start * 1000 - 1e-9), math.floor((block_start + duration) * 1000 + 1e-9)
    for index, word in enumerate(asr_words):
        tokens = normalized_words(word["word"])
        if not tokens: continue
        begin = min(upper, max(lower, round(float(word["start_seconds"]) * 1000)))
        finish = min(upper, max(begin, round(float(word["end_seconds"]) * 1000)))
        for part, token in enumerate(tokens):
            observed.append({"normalized": token, "asr_word_index": index,
                             "start_ms": round(begin + (finish - begin) * part / len(tokens)),
                             "end_ms": round(begin + (finish - begin) * (part + 1) / len(tokens)),
                             "split": len(tokens) > 1})
    result = {"method": "word_edit_distance", "exact_forced_alignment": False,
              "source_text": text, "words": [], "unmatched_asr_word_indices": [],
              "unaligned_source_token_indices": [], "interpolated_token_indices": [],
              "status": "aligned"}
    if not source or not observed or upper - lower < len(source):
        result.update(status="unavailable", unaligned_source_token_indices=list(range(len(source))))
        return result
    n, m = len(source), len(observed)
    cost = [[0] * (m + 1) for _ in range(n + 1)]
    for i in range(n + 1): cost[i][0] = i
    for j in range(m + 1): cost[0][j] = j
    for i in range(1, n + 1):
        for j in range(1, m + 1):
            cost[i][j] = min(cost[i-1][j] + 1, cost[i][j-1] + 1,
                             cost[i-1][j-1] + (source[i-1]["normalized"] != observed[j-1]["normalized"]))
    paired, extra = {}, []
    i, j = n, m
    while i or j:
        equal = bool(i and j and source[i-1]["normalized"] == observed[j-1]["normalized"])
        if i and j and cost[i][j] == cost[i-1][j-1] + (not equal):
            paired[i-1] = (j-1, "equal" if equal else "substitution")
            i, j = i-1, j-1
        elif i and cost[i][j] == cost[i-1][j] + 1:
            i -= 1
        else:
            extra.append(observed[j-1]["asr_word_index"])
            j -= 1
    aligned = []
    for index, token in enumerate(source):
        record = {k: v for k, v in token.items() if k != "normalized"}
        if index in paired:
            target, kind = paired[index]
            anchor = observed[target]
            record.update(start_ms=anchor["start_ms"], end_ms=anchor["end_ms"], match_kind=kind,
                          asr_word_index=anchor["asr_word_index"], interpolated=anchor["split"],
                          timestamp_method="asr_token_split" if anchor["split"] else f"asr_{kind}_anchor")
        else:
            record.update(start_ms=None, end_ms=None, match_kind="source_word_without_asr_anchor",
                          asr_word_index=None, interpolated=True, timestamp_method="interpolated_between_anchors")
        aligned.append(record)

    def distribute(first: int, stop: int, begin: int, finish: int, method: str) -> None:
        count = stop - first
        for offset, index in enumerate(range(first, stop)):
            aligned[index].update(start_ms=round(begin + (finish - begin) * offset / count),
                                  end_ms=round(begin + (finish - begin) * (offset + 1) / count),
                                  interpolated=True, timestamp_method=method)

    index = 0
    while index < n:
        if aligned[index]["start_ms"] is not None:
            index += 1
            continue
        stop = index + 1
        while stop < n and aligned[stop]["start_ms"] is None: stop += 1
        begin = aligned[index-1]["end_ms"] if index else lower
        finish = aligned[stop]["start_ms"] if stop < n else upper
        if finish - begin >= stop - index:
            distribute(index, stop, begin, finish, "interpolated_between_anchors")
        else:
            first_neighbor, after_neighbor = max(0, index-1), min(n, stop+1)
            begin = aligned[first_neighbor]["start_ms"] if index else lower
            finish = aligned[stop]["end_ms"] if stop < n else upper
            distribute(first_neighbor, after_neighbor, begin, finish, "redistributed_neighbor_intervals")
        index = stop
    # Repair overlapping or zero-duration ASR intervals without dropping text.
    # Severe/nonmonotonic timestamps fall back to explicitly marked estimates.
    for index in range(1, n):
        previous, current = aligned[index-1], aligned[index]
        if previous["end_ms"] > current["start_ms"]:
            boundary = round((previous["end_ms"] + current["start_ms"]) / 2)
            previous.update(end_ms=boundary, interpolated=True, timestamp_method="overlap_boundary_adjusted")
            current.update(start_ms=boundary, interpolated=True, timestamp_method="overlap_boundary_adjusted")
    invalid = any(w["start_ms"] < lower or w["end_ms"] > upper or w["end_ms"] <= w["start_ms"] for w in aligned)
    invalid = invalid or any(aligned[i-1]["end_ms"] > aligned[i]["start_ms"] for i in range(1, n))
    if invalid:
        begin = max(lower, min(w["start_ms"] for w in observed))
        finish = min(upper, max(w["end_ms"] for w in observed))
        if finish - begin < n: begin, finish = lower, upper
        distribute(0, n, begin, finish, "interpolated_nonmonotonic_asr")
    for record in aligned:
        record.update(start_seconds=record.pop("start_ms") / 1000, end_seconds=record.pop("end_ms") / 1000)
        record["local_start_seconds"] = round(record["start_seconds"] - block_start, 3)
        record["local_end_seconds"] = round(record["end_seconds"] - block_start, 3)
    result.update(words=aligned, unmatched_asr_word_indices=sorted(set(extra)),
                  interpolated_token_indices=[w["source_token_index"] for w in aligned if w["interpolated"]],
                  status="aligned_with_estimates" if any(w["interpolated"] for w in aligned) else "aligned_to_asr_anchors")
    return result


def make_cues(words: list[dict], block_id: str) -> list[dict]:
    cues, group = [], []
    def surface(word): return word.get("caption_text", word["word"])
    def flush() -> None:
        if group:
            script_text = all("caption_text" in word for word in group)
            text = ("".join(surface(word) for word in group) if script_text else " ".join(surface(word).strip() for word in group)).strip()
            cues.append({"block_id": block_id, "start_seconds": group[0]["start_seconds"],
                         "end_seconds": max(group[0]["start_seconds"] + 0.001, group[-1]["end_seconds"]),
                         "text": "\n".join(textwrap.wrap(text, width=44, break_long_words=False, break_on_hyphens=False)),
                         "source_token_indices": [w["source_token_index"] for w in group if "source_token_index" in w],
                         "interpolated_timestamps": any(w.get("interpolated", False) for w in group)})
            group.clear()
    for word in words:
        if group and (word["end_seconds"] - group[0]["start_seconds"] > 5.5 or sum(len(surface(w).strip()) + 1 for w in group) + len(surface(word)) > 78):
            flush()
        group.append(word)
        if re.search(r"[.!?…][»\"]?$", surface(word).strip()):
            flush()
    flush()
    return cues


def nonoverlapping_cues(cues: list[dict]) -> list[dict]:
    """Keep the full cue text while resolving coincident/overlapping intervals."""
    cleaned = []
    for original in sorted(cues, key=lambda c: (c["start_seconds"], c["end_seconds"])):
        cue = dict(original)
        if cleaned and cleaned[-1]["end_seconds"] > cue["start_seconds"]:
            previous = cleaned[-1]
            if cue["start_seconds"] > previous["start_seconds"]:
                previous["end_seconds"] = cue["start_seconds"]
                previous["cue_boundary_adjusted"] = True
            else:
                previous["text"] += "\n" + cue["text"]
                previous["end_seconds"] = max(previous["end_seconds"], cue["end_seconds"])
                previous["source_token_indices"] = previous.get("source_token_indices", []) + cue.get("source_token_indices", [])
                previous["interpolated_timestamps"] = previous.get("interpolated_timestamps", False) or cue.get("interpolated_timestamps", False)
                previous["cue_boundary_adjusted"] = True
                continue
        cleaned.append(cue)
    return cleaned


def write_srt(cues: list[dict], path: Path) -> None:
    text = "\n\n".join(f"{index}\n{timestamp(cue['start_seconds'])} --> {timestamp(cue['end_seconds'])}\n{cue['text']}" for index, cue in enumerate(cues, 1))
    path.write_text(text + ("\n" if text else ""), encoding="utf-8")


def valid_block_id(value) -> bool:
    return isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9_.-]+", value) is not None


def validate_caption_cues(cues: list[dict], block_spans: dict[str, tuple[float, float]], tolerance: float = 1e-6) -> dict:
    """Describe actual emitted cue geometry and coverage, independent of ASR WER."""
    final_end = max((span[1] for span in block_spans.values()), default=0.0)
    checks = {"finite_timestamps": True, "positive_durations": True, "ordered": True,
              "nonoverlap": True, "within_segment_bounds": True, "within_final_end": True}
    violations, covered = [], set()
    previous_start, previous_end = -math.inf, -math.inf
    for index, cue in enumerate(cues):
        begin, finish = cue["start_seconds"], cue["end_seconds"]
        block_id = cue["block_id"]
        if not math.isfinite(begin) or not math.isfinite(finish):
            checks["finite_timestamps"] = False
            violations.append({"cue_index": index, "reason": "nonfinite_timestamp"})
            continue
        if finish <= begin:
            checks["positive_durations"] = False
            violations.append({"cue_index": index, "reason": "nonpositive_duration"})
        if begin < previous_start - tolerance:
            checks["ordered"] = False
        if begin < previous_end - tolerance:
            checks["nonoverlap"] = False
            violations.append({"cue_index": index, "reason": "overlapping_cues"})
        span = block_spans.get(block_id)
        if span is None or begin < span[0] - tolerance or finish > span[1] + tolerance:
            checks["within_segment_bounds"] = False
            violations.append({"cue_index": index, "reason": "outside_segment_bounds"})
        if finish > final_end + tolerance:
            checks["within_final_end"] = False
            violations.append({"cue_index": index, "reason": "after_final_end"})
        if cue.get("text", "").strip(): covered.add(block_id)
        else: violations.append({"cue_index": index, "reason": "empty_caption"})
        previous_start, previous_end = begin, finish
    missing = sorted(set(block_spans) - covered)
    return {"status": "pass" if all(checks.values()) and not missing and not violations else "fail",
            **checks, "missing_caption_block_ids": missing, "violations": violations,
            "final_end_seconds": final_end, "tolerance_seconds": tolerance, "cue_count": len(cues)}


def validate_runtime_options(device='cpu',compute_type='int8',dll_directories=None) -> dict:
    if device not in ('cpu','cuda'):raise ValueError('ASR device must be cpu or cuda; automatic fallback is disabled')
    if compute_type not in ('int8','int8_float16','int8_float32','int8_bfloat16','float16','float32','bfloat16'):
        raise ValueError('ASR compute type must be explicit; automatic fallback is disabled')
    if dll_directories is None:dll_directories=[]
    if not isinstance(dll_directories,(list,tuple)):raise ValueError('DLL directories must be a list')
    paths=[]
    for value in dll_directories:
        if not isinstance(value,(str,Path)):raise ValueError('A DLL directory must be a path')
        path=Path(value)
        if not path.is_absolute() or not path.is_dir():raise ValueError('DLL directories must be existing absolute paths')
        paths.append(str(path.resolve()))
    return {'device':device,'compute_type':compute_type,'dll_directories':paths}


@contextmanager
def asr_dll_environment(directories):
    """Keep Windows loader handles alive through model loading and inference."""
    if directories and os.name!='nt':raise ValueError('Explicit DLL directories are supported on Windows only')
    previous_path=os.environ.get('PATH');handles=[]
    try:
        if directories:
            os.environ['PATH']=os.pathsep.join(directories+[previous_path or ''])
            for directory in directories:handles.append(os.add_dll_directory(directory))
        yield
    finally:
        for handle in reversed(handles):handle.close()
        if previous_path is None:os.environ.pop('PATH',None)
        else:os.environ['PATH']=previous_path


def transcribe_manifest(manifest_path: Path, output: Path, subtitle_path: Path, model_name: str = "medium", cpu_threads: int = 4,
                        *,device='cpu',compute_type='int8',dll_directories=None) -> dict:
    options=validate_runtime_options(device,compute_type,dll_directories)
    if type(cpu_threads) is not int or not 1<=cpu_threads<=32:raise ValueError('cpu_threads must be between 1 and 32')
    with asr_dll_environment(options['dll_directories']):
        return _transcribe_manifest(manifest_path,output,subtitle_path,model_name,cpu_threads,**options)


def _transcribe_manifest(manifest_path: Path, output: Path, subtitle_path: Path, model_name: str, cpu_threads: int,
                         *,device: str,compute_type: str,dll_directories: list[str]) -> dict:
    started = time.perf_counter()
    manifest_path = manifest_path.resolve()
    manifest = json.loads(manifest_path.read_text(encoding="utf-8-sig"))
    entries = manifest.get("segments")
    if not isinstance(entries, list) or not entries:
        raise ValueError("Manifest requires nonempty segments")
    root = Path(manifest.get("workspace_root", manifest_path.parent))
    if not root.is_absolute(): root = manifest_path.parent / root
    root = root.resolve()
    output = output.resolve()
    subtitle_path = subtitle_path.resolve()
    raw_subtitle_path = subtitle_path.with_name(subtitle_path.stem + "_asr.srt")
    if not output.is_relative_to(root) or output.suffix.lower() != ".json":
        raise ValueError("QA output must be a .json file within workspace_root")
    if not subtitle_path.is_relative_to(root) or subtitle_path.suffix.lower() != ".srt":
        raise ValueError("Subtitles must be a .srt file within workspace_root")
    validated = []
    ids = set()
    for index, entry in enumerate(entries):
        block_id = entry.get("block_id", entry.get("id", str(index + 1)))
        if not valid_block_id(block_id) or block_id in ids:
            raise ValueError(f"Invalid or duplicate block id: {block_id!r}")
        ids.add(block_id)
        if not isinstance(entry.get("text"), str) or not entry["text"].strip():
            raise ValueError(f"Missing expected text for block {block_id}")
        audio = Path(entry["path"])
        if not audio.is_absolute(): audio = manifest_path.parent / audio
        audio = audio.resolve()
        if not audio.is_relative_to(root) or not audio.is_file() or audio.suffix.lower() != ".wav":
            raise ValueError(f"Audio must be a .wav file within workspace_root: {audio}")
        validated.append((block_id, entry, audio))
    # Explicitly offline even when the model cache is missing.
    os.environ["HF_HUB_OFFLINE"] = "1"
    from faster_whisper import WhisperModel
    import ctranslate2
    if compute_type not in ctranslate2.get_supported_compute_types(device):
        raise ValueError(f'{device} does not support requested compute type {compute_type}; fallback is disabled')
    model = WhisperModel(model_name, device=device, compute_type=compute_type, cpu_threads=cpu_threads, local_files_only=True)
    actual_device=model.model.device;actual_compute=model.model.compute_type
    if actual_device!=device or (actual_compute!=compute_type and not (compute_type=='int8' and actual_compute.startswith('int8_'))):
        raise ValueError(f'ASR loaded {actual_device}/{actual_compute} instead of requested {device}/{compute_type}')
    report_segments, all_cues, raw_cues = [], [], []
    block_spans = {}
    cursor = 0.0
    for block_id, entry, audio in validated:
        measurement = measure_wav(audio)
        start = float(entry.get("start_seconds", cursor))
        if not math.isfinite(start) or start < 0:
            raise ValueError(f"Invalid start_seconds for {block_id}")
        duration = measurement["duration_seconds"]
        block_spans[block_id] = (start, start + duration)
        generated, info = model.transcribe(str(audio), language="ru", beam_size=5, condition_on_previous_text=False, word_timestamps=True)
        transcript_parts, word_records = [], []
        for segment in generated:
            transcript_parts.append(segment.text.strip())
            for word in segment.words or []:
                local_start = min(duration, max(0.0, word.start))
                local_end = min(duration, max(local_start, word.end))
                word_records.append({"word": word.word.strip(), "local_start_seconds": round(local_start, 3),
                                     "local_end_seconds": round(local_end, 3), "start_seconds": round(start + local_start, 3),
                                     "end_seconds": round(start + local_end, 3), "probability": round(word.probability, 4)})
        recognized = " ".join(transcript_parts)
        comparison = word_error_rate(entry["text"], recognized)
        negations = {"не", "нет", "нельзя", "никогда", "никто", "ничто", "никак"}
        expected_negations = [w for w in normalized_words(entry["text"]) if w in negations]
        recognized_negations = [w for w in normalized_words(recognized) if w in negations]
        critical_terms = entry.get("critical_terms", manifest.get("critical_terms", []))
        if not isinstance(critical_terms, list) or any(not isinstance(term, str) for term in critical_terms):
            raise ValueError("critical_terms must be a list of strings")
        expected_joined = " " + " ".join(normalized_words(entry["text"])) + " "
        recognized_joined = " " + " ".join(normalized_words(recognized)) + " "
        missing_critical_terms = [term for term in critical_terms
                                  if (" " + " ".join(normalized_words(term)) + " ") in expected_joined
                                  and (" " + " ".join(normalized_words(term)) + " ") not in recognized_joined]
        warnings = []
        if comparison["wer"] > 0.15: warnings.append("word_error_rate_above_0.15")
        if expected_negations != recognized_negations: warnings.append("negations_mismatch")
        if missing_critical_terms: warnings.append("critical_terms_mismatch")
        if not word_records: warnings.append("no_word_timestamps")
        if measurement["clipped_sample_fraction"] > 0.001: warnings.append("clipped_audio")
        if measurement["rms_dbfs"] < -45: warnings.append("very_quiet_audio")
        if "duration_seconds" in entry and abs(float(entry["duration_seconds"]) - duration) > 0.1:
            warnings.append("manifest_duration_mismatch")
        alignment = align_script_words(entry["text"], word_records, start, duration)
        if alignment["status"] == "unavailable": warnings.append("script_subtitles_unaligned")
        if alignment["interpolated_token_indices"]: warnings.append("subtitle_timestamps_interpolated")
        row = {"id": entry.get("id", block_id), "block_id": block_id, "path": str(audio), "text": entry["text"],
               "recognized": recognized, "start_seconds": start, **measurement, **comparison,
               "expected_negations": expected_negations, "recognized_negations": recognized_negations,
               "critical_terms": critical_terms, "missing_critical_terms": missing_critical_terms,
               "subtitle_alignment": alignment,
               "words": word_records, "warnings": warnings, "status": "automated_warning" if warnings else "automated_pass",
               "human_review": "not_checked"}
        report_segments.append(row)
        all_cues.extend(make_cues(alignment["words"], block_id))
        raw_cues.extend(make_cues(word_records, block_id))
        cursor = start + duration
        print(json.dumps({"block_id": block_id, "wer": comparison["wer"], "status": row["status"]}), file=sys.stderr, flush=True)
    all_cues = nonoverlapping_cues(all_cues)
    raw_cues = nonoverlapping_cues(raw_cues)
    subtitle_validation = validate_caption_cues(all_cues, block_spans)
    report = {"schema_version": "1.0", "engine": "faster-whisper", "model": model_name,
              "device": device, "compute_type": compute_type,"actual_compute_type":actual_compute,
              "dll_directories":dll_directories,"offline": True, "human_review": "not_checked",
              "status": "automated_warning" if any(r["warnings"] for r in report_segments) or subtitle_validation["status"] != "pass" else "automated_pass",
              "limitations": "ASR can misrecognize names or short words. Primary captions preserve script text and use ASR anchors plus reported estimates; this is not exact acoustic forced alignment. Raw recognition and all discrepancies are retained. Human review remains required.",
              "segments": report_segments, "subtitles": {"path": str(subtitle_path), "kind": "script_aligned",
              "text_source": "script", "timestamp_source": "asr_word_anchors_with_interpolation",
              "alignment_method": "word_edit_distance", "exact_forced_alignment": False,
              "validation": subtitle_validation, "cues": all_cues},
              "raw_subtitles": {"path": str(raw_subtitle_path), "kind": "recognized_transcript",
              "timestamp_source": "faster_whisper_word_timestamps", "cues": raw_cues,
              "validation": validate_caption_cues(raw_cues, block_spans, tolerance=0.001)},
              "elapsed_seconds": round(time.perf_counter() - started, 3)}
    output.parent.mkdir(parents=True, exist_ok=True)
    subtitle_path.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    write_srt(all_cues, subtitle_path)
    write_srt(raw_cues, raw_subtitle_path)
    return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--subtitles", type=Path)
    parser.add_argument("--model", default="medium")
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument('--device',choices=('cpu','cuda'),default='cpu')
    parser.add_argument('--compute-type',default='int8')
    parser.add_argument('--dll-directory',type=Path,action='append',default=[])
    args = parser.parse_args()
    if not 1 <= args.cpu_threads <= 32:
        parser.error("--cpu-threads must be between 1 and 32")
    subtitles = args.subtitles or args.output.parent / "subtitles.srt"
    report = transcribe_manifest(args.manifest, args.output, subtitles, args.model, args.cpu_threads,
                                 device=args.device,compute_type=args.compute_type,dll_directories=args.dll_directory)
    print(json.dumps({"report": str(args.output), "subtitles": str(subtitles), "raw_subtitles": report["raw_subtitles"]["path"], "status": report["status"],
                      "segment_count": len(report["segments"]), "human_review": "not_checked"}, ensure_ascii=True))


if __name__ == "__main__":
    main()
