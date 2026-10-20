"""Render the actual editable Shotcut MLT with Shotcut's bundled melt/qmelt."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
import uuid


def find_qmelt(explicit: str | Path | None = None) -> Path:
    if explicit:
        result = Path(explicit).resolve(strict=True)
        if result.is_dir():
            result = next((result / name for name in ("qmelt.exe", "melt.exe") if (result / name).is_file()), result / "melt.exe")
        if result.is_file():
            return result
        raise FileNotFoundError(result)
    root = Path(__file__).resolve().parents[1]
    candidates = sorted((root / "tools").glob("**/qmelt.exe")) + sorted((root / "tools").glob("**/melt.exe"))
    if candidates:
        return candidates[0]
    raise FileNotFoundError("Shotcut melt.exe is missing; install the official portable Shotcut in tools/")


def render_project(project: str | Path, output: str | Path | None = None, *, qmelt: str | Path | None = None,
                   crf: int = 20, preset: str = "fast", timeout: float = 1800) -> dict:
    source = Path(project).resolve(strict=True)
    destination = Path(output).resolve() if output else source.with_name("final.mp4")
    if destination.exists():
        raise FileExistsError(f"Output already exists; choose a new version: {destination}")
    if any(part.casefold() in {"manual", "originals"} for part in destination.parts):
        raise ValueError("Automatic exports cannot modify protected directories")
    destination.parent.mkdir(parents=True, exist_ok=True)
    project_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    temporary_output = destination.with_name(f"{destination.stem}.{uuid.uuid4().hex[:10]}.rendering.mp4")
    executable = find_qmelt(qmelt)
    log_path = destination.with_suffix(".render.log")
    environment = os.environ.copy()
    environment["QT_QPA_PLATFORM"] = "offscreen"
    environment["MLT_NO_VDPAU"] = "1"
    environment["PATH"] = str(executable.parent) + os.pathsep + environment.get("PATH", "")
    command = [str(executable), str(source), "-consumer", f"avformat:{temporary_output}",
               "vcodec=libx264", f"crf={crf}", f"preset={preset}", "pix_fmt=yuv420p",
               "acodec=aac", "ab=192k", "ar=48000", "ac=2", "movflags=+faststart",
               "real_time=-1", "threads=4", "progressive=1", "-progress"]
    start = time.monotonic()
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    with log_path.open("w", encoding="utf-8") as log:
        process = subprocess.run(command, cwd=source.parent, env=environment, stdout=log, stderr=subprocess.STDOUT,
                                 timeout=timeout, creationflags=flags, check=False)
    result = {"project": str(source), "output": str(destination), "renderer": str(executable),
              "elapsed_seconds": round(time.monotonic() - start, 3), "returncode": process.returncode,
              "log": str(log_path), "same_mlt_as_editor": True, "project_sha256": project_hash}
    if process.returncode != 0 or not temporary_output.exists() or temporary_output.stat().st_size < 1024:
        raise RuntimeError(f"Shotcut render failed: {json.dumps(result, ensure_ascii=False)}")
    if hashlib.sha256(source.read_bytes()).hexdigest() != project_hash:
        raise RuntimeError("The project changed during render; output was not promoted to final")
    temporary_output.rename(destination)
    destination.with_suffix(".render.json").write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    return result


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("project", type=Path)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--qmelt", type=Path)
    parser.add_argument("--crf", type=int, default=20)
    parser.add_argument("--preset", default="fast")
    parser.add_argument("--timeout", type=float, default=1800)
    arguments = parser.parse_args()
    print(json.dumps(render_project(arguments.project, arguments.output, qmelt=arguments.qmelt,
                                   crf=arguments.crf, preset=arguments.preset, timeout=arguments.timeout), ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
