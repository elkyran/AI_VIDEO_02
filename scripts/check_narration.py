"""Offline ASR spot check. Recognition is evidence, not a listening review."""
from __future__ import annotations
import argparse
import difflib
import json
from pathlib import Path
import re
import time


def words(text: str) -> list[str]:
    return re.findall(r"[а-яёa-z0-9]+", text.lower().replace("ё", "е"))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--script", type=Path, required=True)
    parser.add_argument("--audio-dir", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--model", default="small")
    args = parser.parse_args()
    from faster_whisper import WhisperModel
    started = time.perf_counter()
    model = WhisperModel(args.model, device="cpu", compute_type="int8", cpu_threads=4, local_files_only=True)
    script = json.loads(args.script.read_text(encoding="utf-8-sig"))
    rows = []
    for block in script.get("segments", script.get("blocks", [])):
        segments, _ = model.transcribe(str(args.audio_dir / f"{block['id']}.wav"), language="ru", beam_size=5, condition_on_previous_text=False)
        transcript = " ".join(segment.text.strip() for segment in segments)
        expected = block["text"]
        expected_words, recognized_words = words(expected), words(transcript)
        diff = [ {"operation": tag, "expected": expected_words[a:b], "recognized": recognized_words[c:d]}
                for tag,a,b,c,d in difflib.SequenceMatcher(a=expected_words,b=recognized_words).get_opcodes() if tag != "equal" ]
        row = {"block_id": block["id"], "expected": expected, "recognized": transcript,
               "word_sequence_match": round(difflib.SequenceMatcher(a=expected_words,b=recognized_words).ratio(), 4),
               "differences": diff,
               "expected_negations": sum(w in ("не", "нельзя", "нет") for w in expected_words),
               "recognized_negations": sum(w in ("не", "нельзя", "нет") for w in recognized_words)}
        rows.append(row)
        print(json.dumps({"block_id": block["id"], "word_sequence_match": row["word_sequence_match"]}), flush=True)
    report = {"engine": "faster-whisper", "model": args.model, "device": "cpu", "manual_listening": False,
              "limitations": "Automatic recognition can miss or normalize proper names and punctuation. It does not prove pronunciation quality.",
              "elapsed_seconds": round(time.perf_counter()-started, 3), "segments": rows}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")


if __name__ == "__main__":
    main()
