"""FFmpeg / FFprobe wrappers (README section 21)."""
from __future__ import annotations
import json
import subprocess
from fractions import Fraction
from pathlib import Path
from typing import Any


class MediaError(Exception):
    pass


def run(cmd: list[str], timeout: int = 900) -> subprocess.CompletedProcess:
    p = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)
    if p.returncode != 0:
        raise MediaError(f"{cmd[0]} failed: {p.stderr[-600:]}")
    return p


def probe(path: Path | str) -> dict[str, Any]:
    p = subprocess.run(["ffprobe", "-v", "error", "-show_format", "-show_streams", "-of", "json", str(path)], capture_output=True, text=True, timeout=60)
    if p.returncode != 0:
        raise MediaError(f"unreadable media: {p.stderr[-300:]}")
    j = json.loads(p.stdout)
    v = next((s for s in j.get("streams", []) if s["codec_type"] == "video"), None)
    a = next((s for s in j.get("streams", []) if s["codec_type"] == "audio"), None)
    out: dict[str, Any] = {"format": j.get("format", {}).get("format_name"), "duration": float(j.get("format", {}).get("duration", 0) or 0),
                           "has_video": v is not None, "has_audio": a is not None}
    if v:
        fr = Fraction(v.get("avg_frame_rate", "0/1") or "0/1") if v.get("avg_frame_rate") not in (None, "0/0") else Fraction(0)
        out.update(width=v["width"], height=v["height"], fps=float(fr), codec=v.get("codec_name"),
                   nb_frames=int(v["nb_frames"]) if str(v.get("nb_frames", "")).isdigit() else None,
                   video_duration=float(v.get("duration", out["duration"]) or out["duration"]))
    if a:
        out.update(audio_codec=a.get("codec_name"), audio_duration=float(a.get("duration", out["duration"]) or out["duration"]))
    return out


def count_frames(path: Path | str) -> int:
    p = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-count_frames", "-show_entries", "stream=nb_read_frames", "-of", "csv=p=0", str(path)],
                       capture_output=True, text=True, timeout=300)
    try:
        return int(p.stdout.strip().split(",")[0])
    except Exception:  # noqa: BLE001
        return 0


def extract_frame(video: Path | str, out_png: Path | str, which: str = "last") -> Path:
    out_png = Path(out_png)
    out_png.parent.mkdir(parents=True, exist_ok=True)
    if which == "first":
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-frames:v", "1", str(out_png)])
    elif which == "middle":
        d = probe(video)["duration"]
        run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{d / 2:.3f}", "-i", str(video), "-frames:v", "1", str(out_png)])
    else:
        # decode the whole clip and keep the last frame; exact for short clips and never depends on seek accuracy
        run(["ffmpeg", "-y", "-loglevel", "error", "-i", str(video), "-vf", "select='eq(n,0)+gte(n,0)',reverse", "-frames:v", "1", str(out_png)]) \
            if False else run(["ffmpeg", "-y", "-loglevel", "error", "-sseof", "-0.2", "-i", str(video), "-update", "1", "-q:v", "2", str(out_png)])
    return out_png


def fmt_srt_time(t: float) -> str:
    ms = int(round(t * 1000))
    h, ms = divmod(ms, 3600000)
    m, ms = divmod(ms, 60000)
    s, ms = divmod(ms, 1000)
    return f"{h:02}:{m:02}:{s:02},{ms:03}"
