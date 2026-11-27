"""Final composition (README section 21): normalize, trim to exact frame counts, concat, mix master narration,
burn optional subtitles, encode MP4, validate."""
from __future__ import annotations
import json
from pathlib import Path
from typing import Any

from . import ffmpeg as fx


def compose(*, clips: list[dict[str, Any]], width: int, height: int, fps: int, out_path: Path, audio_path: Path | None,
            srt_path: Path | None, burn_subtitles: bool, transition: str = "cut", workdir: Path) -> dict[str, Any]:
    """clips: [{path, duration}] in timeline order. Each clip is trimmed (or padded by holding its last frame, which is reported)
    to exactly round(duration*fps) frames so the total is frame accurate."""
    workdir.mkdir(parents=True, exist_ok=True)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    inputs: list[str] = []
    fparts: list[str] = []
    notes: list[str] = []
    total_frames = 0
    for i, c in enumerate(clips):
        info = fx.probe(c["path"])
        n = max(1, int(round(c["duration"] * fps)))
        have = int(round(info["video_duration"] * (info.get("fps") or fps)))
        if have < n - 1:
            notes.append(f"scene clip {i + 1} is {info['video_duration']:.2f}s, shorter than its {c['duration']:.2f}s slot: last frame held")
        if info["width"] < width or info["height"] < height:
            notes.append(f"scene clip {i + 1} native {info['width']}x{info['height']} upscaled to {width}x{height}")
        inputs += ["-i", str(c["path"])]
        f = (f"[{i}:v]scale={width}:{height}:force_original_aspect_ratio=decrease:flags=lanczos,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1,fps={fps},"
             f"tpad=stop_mode=clone:stop={n},trim=end_frame={n},setpts=PTS-STARTPTS")
        if transition == "fade":
            fd = min(0.15, c["duration"] / 4)
            f += f",fade=t=in:st=0:d={fd:.3f},fade=t=out:st={c['duration'] - fd:.3f}:d={fd:.3f}"
        fparts.append(f + f"[v{i}]")
        total_frames += n
    import os
    preset = os.environ.get("VGOH_X264_PRESET", "medium")
    threads = os.environ.get("VGOH_FFMPEG_THREADS")
    lowmem = os.environ.get("VGOH_COMPOSE_SEGMENTED", "0") == "1" and not (burn_subtitles and srt_path and srt_path.exists())
    if lowmem:
        # Memory-lean path: encode each scene to a normalized segment on its own, then stream-copy concat (+ audio mux).
        segs = []
        for i, c in enumerate(clips):
            sp = workdir / f"seg_{i}.mp4"
            fc = fparts[i].replace(f"[{i}:v]", "[0:v]", 1).replace(f"[v{i}]", "[vo]")
            cmd = ["ffmpeg", "-y", "-loglevel", "error", "-i", str(c["path"]), "-filter_complex", fc, "-map", "[vo]", "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p",
                   "-preset", preset, "-crf", "18", "-r", str(fps), "-frames:v", str(max(1, int(round(c["duration"] * fps))))]
            if threads:
                cmd += ["-threads", threads, "-filter_threads", threads]
            fx.run(cmd + [str(sp)], timeout=3600)
            segs.append(sp)
        lst = workdir / "concat.txt"
        lst.write_text("".join(f"file '{sp}'\n" for sp in segs))
        total_dur = total_frames / fps
        cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(lst)]
        if audio_path:
            cmd += ["-i", str(audio_path), "-filter_complex",
                    f"[1:a]aresample=48000,loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:st=0:d=0.3,apad,atrim=0:{total_dur:.4f},afade=t=out:st={max(0.0, total_dur - 0.5):.4f}:d=0.5[aout]",
                    "-map", "0:v", "-map", "[aout]", "-c:a", "aac", "-b:a", "160k"]
        else:
            cmd += ["-map", "0:v"]
        cmd += ["-c:v", "copy", "-frames:v", str(total_frames), "-movflags", "+faststart", str(out_path)]
        fx.run(cmd, timeout=3600)
        for sp in segs:
            sp.unlink(missing_ok=True)
        return {"expected_frames": total_frames, "expected_duration": total_dur, "notes": notes + ["composed segment-by-segment (low-memory mode)"]}
    concat = "".join(f"[v{i}]" for i in range(len(clips))) + f"concat=n={len(clips)}:v=1:a=0[vc]"
    last = "vc"
    if burn_subtitles and srt_path and srt_path.exists():
        esc = str(srt_path).replace("\\", "\\\\").replace(":", "\\:").replace("'", "\\'")
        fparts.append(concat)
        fparts.append(f"[vc]subtitles='{esc}':force_style='FontSize=18,Outline=2,Alignment=2,MarginV=60'[vs]")
        last = "vs"
    else:
        fparts.append(concat)
    total_dur = total_frames / fps
    cmd = ["ffmpeg", "-y", "-loglevel", "error", *inputs]
    if audio_path:
        cmd += ["-i", str(audio_path)]
        fparts.append(f"[{len(clips)}:a]aresample=48000,loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=in:st=0:d=0.3,apad,atrim=0:{total_dur:.4f},"
                      f"afade=t=out:st={max(0.0, total_dur - 0.5):.4f}:d=0.5[aout]")
    script = workdir / "filter.txt"
    script.write_text(";\n".join(fparts))
    cmd += ["-filter_complex_script", str(script), "-map", f"[{last}]"]
    if audio_path:
        cmd += ["-map", "[aout]", "-c:a", "aac", "-b:a", "160k"]
    cmd += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", preset, "-crf", "18", "-r", str(fps), "-frames:v", str(total_frames),
            "-movflags", "+faststart", str(out_path)]
    fx.run(cmd, timeout=3600)
    return {"expected_frames": total_frames, "expected_duration": total_dur, "notes": notes}


def validate_output(path: Path, *, width: int, height: int, fps: int, target_duration: float, expect_audio: bool) -> dict[str, Any]:
    info = fx.probe(path)
    frames = fx.count_frames(path)
    checks = {
        "resolution_ok": info.get("width") == width and info.get("height") == height,
        "fps_ok": abs((info.get("fps") or 0) - fps) < 0.01,
        "duration_within_one_frame": abs(frames / fps - target_duration) <= 1.0 / fps + 1e-6,
        "has_audio_when_expected": (info["has_audio"] if expect_audio else True),
        "codec_h264": info.get("codec") == "h264",
    }
    return {"ok": all(checks.values()), "checks": checks, "frames": frames, "duration_s": round(frames / fps, 4),
            "container_duration_s": info["duration"], "width": info.get("width"), "height": info.get("height"), "fps": info.get("fps"),
            "audio": info.get("audio_codec"), "size_bytes": path.stat().st_size}
