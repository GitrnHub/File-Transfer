#!/usr/bin/env python3
from __future__ import annotations

import csv
import json
import math
import os
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path


def run(cmd: list[str], check: bool = True, capture: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, capture_output=capture)


def utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def github_output(name: str, value: str | int | float) -> None:
    path = os.environ.get("GITHUB_OUTPUT")
    if not path:
        return
    with open(path, "a", encoding="utf-8") as f:
        f.write(f"{name}={value}\n")


def locate_video() -> Path:
    files = [p for p in Path("payload").iterdir() if p.is_file() and p.name != "transfer.json"]
    if len(files) != 1:
        raise RuntimeError(f"Expected exactly one payload file, found {len(files)}")
    return files[0]


def ffprobe(video: Path, out_dir: Path) -> dict:
    cp = run([
        "ffprobe", "-v", "error", "-show_format", "-show_streams",
        "-of", "json", str(video)
    ], capture=True)
    data = json.loads(cp.stdout)
    (out_dir / "ffprobe.json").write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return data


def video_duration(info: dict) -> float:
    try:
        return float(info.get("format", {}).get("duration") or 0)
    except Exception:
        return 0.0


def has_audio(info: dict) -> bool:
    return any(s.get("codec_type") == "audio" for s in info.get("streams", []))


def extract_frames(video: Path, out_dir: Path, duration: float) -> tuple[int, int, float]:
    uniform_dir = out_dir / "frames" / "uniform"
    scene_dir = out_dir / "frames" / "scene"
    uniform_dir.mkdir(parents=True, exist_ok=True)
    scene_dir.mkdir(parents=True, exist_ok=True)

    target = 64
    interval = max(duration / target, 0.5) if duration > 0 else 2.0
    vf_uniform = f"fps=1/{interval:.6f},scale=min(1280\\,iw):-2"
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
        "-vf", vf_uniform, "-frames:v", str(target), "-q:v", "3",
        str(uniform_dir / "uniform_%04d.jpg")
    ])

    vf_scene = "select=gt(scene\\,0.12),scale=min(1280\\,iw):-2"
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-i", str(video),
        "-vf", vf_scene, "-fps_mode", "vfr", "-frames:v", "64", "-q:v", "3",
        str(scene_dir / "scene_%04d.jpg")
    ], check=False)

    uniform = sorted(uniform_dir.glob("*.jpg"))
    scene = sorted(scene_dir.glob("*.jpg"))
    with (out_dir / "frames_manifest.csv").open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["file", "kind", "approx_seconds"])
        for i, p in enumerate(uniform):
            w.writerow([str(p.relative_to(out_dir)), "uniform", round(i * interval, 3)])
        for p in scene:
            w.writerow([str(p.relative_to(out_dir)), "scene", ""])
    return len(uniform), len(scene), interval


def make_contact_sheets(out_dir: Path) -> int:
    from PIL import Image, ImageDraw

    frames = sorted((out_dir / "frames" / "uniform").glob("*.jpg")) + sorted((out_dir / "frames" / "scene").glob("*.jpg"))
    if not frames:
        return 0
    sheet_dir = out_dir / "contact_sheets"
    sheet_dir.mkdir(parents=True, exist_ok=True)
    cols, rows = 4, 3
    cell_w, cell_h = 320, 210
    per_sheet = cols * rows
    pages = 0

    for start in range(0, len(frames), per_sheet):
        chunk = frames[start:start + per_sheet]
        canvas = Image.new("RGB", (cols * cell_w, rows * cell_h), "white")
        draw = ImageDraw.Draw(canvas)
        for idx, path in enumerate(chunk):
            with Image.open(path) as im:
                im = im.convert("RGB")
                im.thumbnail((cell_w, cell_h - 24))
                x = (idx % cols) * cell_w + (cell_w - im.width) // 2
                y = (idx // cols) * cell_h
                canvas.paste(im, (x, y))
                draw.text(((idx % cols) * cell_w + 4, y + cell_h - 20), path.name, fill="black")
        pages += 1
        canvas.save(sheet_dir / f"contact_{pages:03d}.jpg", quality=88)
    return pages


def transcribe(video: Path, out_dir: Path, info: dict) -> tuple[str, str]:
    if not has_audio(info):
        (out_dir / "transcript.txt").write_text("[no audio stream]\n", encoding="utf-8")
        return "no_audio", ""

    audio = Path("/tmp/file-transfer-audio.wav")
    run([
        "ffmpeg", "-hide_banner", "-loglevel", "error", "-y", "-i", str(video),
        "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(audio)
    ])

    model_name = os.environ.get("WHISPER_MODEL", "small").strip() or "small"
    try:
        from faster_whisper import WhisperModel
        model = WhisperModel(model_name, device="cpu", compute_type="int8")
        segments, meta = model.transcribe(str(audio), beam_size=5, vad_filter=True)
        rows = []
        text_lines = []
        for seg in segments:
            item = {"start": round(seg.start, 3), "end": round(seg.end, 3), "text": seg.text.strip()}
            rows.append(item)
            if item["text"]:
                text_lines.append(f"[{item['start']:.1f}-{item['end']:.1f}] {item['text']}")
        (out_dir / "transcript.json").write_text(json.dumps({
            "model": model_name,
            "language": getattr(meta, "language", ""),
            "language_probability": getattr(meta, "language_probability", None),
            "segments": rows,
        }, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        (out_dir / "transcript.txt").write_text("\n".join(text_lines) + "\n", encoding="utf-8")
        return "success", getattr(meta, "language", "") or ""
    except Exception as exc:
        (out_dir / "transcript_error.txt").write_text(f"{type(exc).__name__}: {exc}\n", encoding="utf-8")
        return "failed", ""
    finally:
        audio.unlink(missing_ok=True)


def main() -> int:
    video = locate_video()
    out_dir = Path("analysis")
    out_dir.mkdir(parents=True, exist_ok=True)
    if Path("payload/transfer.json").exists():
        shutil.copy2("payload/transfer.json", out_dir / "source_transfer.json")

    info = ffprobe(video, out_dir)
    duration = video_duration(info)
    uniform_count, scene_count, interval = extract_frames(video, out_dir, duration)
    sheets = make_contact_sheets(out_dir)
    transcript_status, language = transcribe(video, out_dir, info)

    summary = {
        "protocol": "file-transfer-video-analysis/v1",
        "generated_at": utc_now(),
        "source_filename": video.name,
        "duration_seconds": duration,
        "uniform_frames": uniform_count,
        "scene_frames": scene_count,
        "uniform_interval_seconds": interval,
        "contact_sheets": sheets,
        "transcript_status": transcript_status,
        "transcript_language": language,
        "whisper_model": os.environ.get("WHISPER_MODEL", "small"),
    }
    (out_dir / "analysis_summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    github_output("duration_seconds", round(duration, 3))
    github_output("uniform_frames", uniform_count)
    github_output("scene_frames", scene_count)
    github_output("transcript_status", transcript_status)
    github_output("language", language)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
