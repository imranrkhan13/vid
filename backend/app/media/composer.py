"""Video processing and final composition (README section 21).

Builds an FFmpeg command to normalize and concatenate accepted clips. If the
``ffmpeg`` binary is not available, composition degrades to a documented no-op
that still writes a manifest describing exactly what would be produced, so the
pipeline and its tests never hard-depend on FFmpeg being installed.

Watermarks are never removed. Visible watermarks are rejected upstream by QA
when the project prohibits them.
"""

from __future__ import annotations

import json
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path


@dataclass
class CompositionClip:
    scene_id: str
    uri: str
    duration_seconds: float


@dataclass
class CompositionResult:
    output_uri: str
    manifest: dict = field(default_factory=dict)
    ffmpeg_used: bool = False
    audio_muxed: bool = False
    command: list[str] = field(default_factory=list)


# A small, explicit grade vocabulary. "none"/unknown adds no filter — we never
# guess a look; the style sheet chooses one.
_GRADE_FILTERS: dict[str, str] = {
    "warm": "eq=gamma_r=1.05:gamma_b=0.95",
    "cool": "eq=gamma_r=0.95:gamma_b=1.05",
    "neutral": "",
}


class Composer:
    def __init__(self, *, ffmpeg_bin: str = "ffmpeg", storage_root: str | Path = "./.vgoh_media") -> None:
        self.ffmpeg_bin = ffmpeg_bin
        self.storage_root = Path(storage_root)

    def ffmpeg_available(self) -> bool:
        return shutil.which(self.ffmpeg_bin) is not None

    def build_command(
        self,
        clips: list[CompositionClip],
        *,
        output_path: Path,
        resolution: str,
        fps: int,
        audio_path: str | None = None,
        grade: str = "",
    ) -> list[str]:
        """Build a concat-demuxer FFmpeg command normalizing resolution and fps."""
        width, height = (resolution.split("x") + ["1920"])[:2]
        cmd = [self.ffmpeg_bin, "-y"]
        # concat demuxer input list is supplied via a manifest file by the caller
        cmd += [
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(output_path.with_suffix(".concat.txt")),
        ]
        if audio_path:
            cmd += ["-i", audio_path]
        vf = (
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,"
            f"pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,fps={fps}"
        )
        grade_filter = _GRADE_FILTERS.get((grade or "").lower(), "")
        if grade_filter:
            vf += f",{grade_filter}"
        cmd += ["-vf", vf]
        if audio_path:
            cmd += ["-map", "0:v:0", "-map", "1:a:0", "-c:a", "aac", "-shortest"]
        cmd += [
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(output_path),
        ]
        return cmd

    def compose(
        self,
        project_id: str,
        clips: list[CompositionClip],
        *,
        resolution: str,
        fps: int,
        aspect_ratio: str,
        audio_path: str | None = None,
        grade: str = "",
    ) -> CompositionResult:
        out_dir = self.storage_root / project_id
        out_dir.mkdir(parents=True, exist_ok=True)
        output_path = out_dir / "final.mp4"
        total = round(sum(c.duration_seconds for c in clips), 6)
        manifest = {
            "project_id": project_id,
            "resolution": resolution,
            "aspect_ratio": aspect_ratio,
            "fps": fps,
            "total_duration_seconds": total,
            "clips": [
                {"scene_id": c.scene_id, "uri": c.uri, "duration_seconds": c.duration_seconds}
                for c in clips
            ],
            "audio": audio_path,
            "grade": grade or "none",
        }

        if not self.ffmpeg_available():
            # Documented no-op: produce the manifest only. Never claim a video
            # was produced when it was not.
            manifest["ffmpeg_available"] = False
            manifest["note"] = "ffmpeg not installed; composition skipped (manifest only)"
            (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
            return CompositionResult(
                output_uri="", manifest=manifest, ffmpeg_used=False, command=[]
            )

        # Write the concat list and run FFmpeg.
        concat_list = output_path.with_suffix(".concat.txt")
        lines = []
        for c in clips:
            # concat demuxer expects file paths; mock:// URIs are not real files.
            lines.append(f"file '{c.uri}'")
            lines.append(f"duration {c.duration_seconds}")
        concat_list.write_text("\n".join(lines), encoding="utf-8")

        command = self.build_command(
            clips,
            output_path=output_path,
            resolution=resolution,
            fps=fps,
            audio_path=audio_path,
            grade=grade,
        )
        subprocess.run(command, check=True, capture_output=True)
        (out_dir / "manifest.json").write_text(json.dumps(manifest, indent=2), encoding="utf-8")
        manifest["ffmpeg_available"] = True
        return CompositionResult(
            output_uri=str(output_path),
            manifest=manifest,
            ffmpeg_used=True,
            audio_muxed=bool(audio_path),
            command=command,
        )
