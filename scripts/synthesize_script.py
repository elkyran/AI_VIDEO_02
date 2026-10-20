"""Synthesize all script blocks using the local reusable TTS adapter.

Run: python scripts/synthesize_script.py --script projects/pilot/script.json
Input: {"project_id": "...", "blocks": [{"id": "b01", "text": "..."}]}.
Worker: --job request.json, with {"text": "...", "output": "...wav", "config": {...}}.
"""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from app.tts import synthesize


def main() -> None:
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--script", type=Path)
    source.add_argument("--job", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--engine", choices=("piper", "silero"), default="piper")
    parser.add_argument("--model", type=Path)
    parser.add_argument("--length-scale", type=float)
    parser.add_argument("--speaker", help="Silero speaker, e.g. aidar")
    parser.add_argument("--sample-rate", type=int, default=48000)
    parser.add_argument("--cpu-threads", type=int, default=4)
    parser.add_argument("--max-chars", type=int, default=950)
    args = parser.parse_args()
    if args.job:
        request = json.loads(args.job.read_text(encoding="utf-8-sig"))
        if not isinstance(request, dict) or set(request) != {"text", "output", "config"}:
            raise ValueError("A TTS job requires exactly text, output, and config")
        print(json.dumps(synthesize(request["text"], request["output"], request["config"]), ensure_ascii=True))
        return
    if args.engine == "silero" and args.length_scale is not None:
        parser.error("--length-scale is a Piper setting; Silero keeps its native speaking pace")
    if args.engine == "piper" and args.speaker is not None:
        parser.error("--speaker is a Silero setting")
    model = args.model or (ROOT / "models/silero/v4_ru.pt" if args.engine == "silero"
                           else ROOT / "models/piper/ru_RU-denis-medium.onnx")
    voice_config = {"engine": args.engine, "model_path": model, "language": "ru-RU"}
    if args.engine == "silero":
        voice_config.update({"speaker": args.speaker or "aidar", "sample_rate": args.sample_rate,
                             "cpu_threads": args.cpu_threads, "max_chars": args.max_chars})
    else:
        voice_config.update({"voice_id": model.stem,
                             "length_scale": args.length_scale if args.length_scale is not None else 1.04})
    script = json.loads(args.script.read_text(encoding="utf-8-sig"))
    blocks = script.get("blocks", script.get("segments")) if isinstance(script, dict) else script
    if not isinstance(blocks, list) or not blocks:
        raise ValueError("Script must contain a nonempty blocks or segments list")
    project_id = script.get("project_id", args.script.parent.name) if isinstance(script, dict) else args.script.parent.name
    output_dir = args.output_dir or args.script.parent / "audio"
    records = []
    ids = set()
    for index, block in enumerate(blocks):
        block_id = block.get("id", block.get("block_id", f"b{index + 1:02d}"))
        if not isinstance(block_id, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+", block_id) or block_id in {".", ".."} or block_id in ids:
            raise ValueError(f"Invalid or duplicate block id: {block_id!r}")
        ids.add(block_id)
        text = block.get("tts_text", block.get("text", block.get("narration")))
        result = synthesize(text, output_dir / f"{block_id}.wav", voice_config)
        result.update({"schema_version": "1.0", "id": f"audio_{block_id}", "project_id": project_id,
                       "block_id": block_id, "text": block.get("text", block.get("narration", text)), "tts_text": text})
        records.append(result)
        print(json.dumps({"block_id": block_id, "duration_seconds": result["duration_seconds"], "cache_hit": result["cache_hit"]}), flush=True)
    output_dir.mkdir(parents=True, exist_ok=True)
    report = {"schema_version": "1.0", "project_id": project_id, "workspace_root": str(Path.cwd()), "segments": records,
              "total_duration_seconds": sum(r["duration_seconds"] for r in records),
              "listening_review": "not_performed"}
    (output_dir / "audio_manifest.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"manifest": str(output_dir / "audio_manifest.json"), "total_duration_seconds": report["total_duration_seconds"]}, ensure_ascii=False))


if __name__ == "__main__":
    main()
