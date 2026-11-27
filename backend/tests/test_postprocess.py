"""Post-process stage: denoise + upscale + sharpen between download and QA,
with honest provenance (source resolution recorded, no score inflation)."""
import subprocess
from pathlib import Path

from app.media.ffmpeg import probe
from app.media.postprocess import postprocess
from app.quality.qa import evaluate_clip


def make_clip(path: Path, w: int, h: int, seconds: float = 2.0, black: bool = False):
    src = f"color=c=black:duration={seconds}:size={w}x{h}:rate=24" if black else f"testsrc2=duration={seconds}:size={w}x{h}:rate=24"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi",
                    "-i", src, "-pix_fmt", "yuv420p", str(path)], check=True)
    return path


def test_upscales_to_target_and_records_source(tmp_path):
    raw = make_clip(tmp_path / "raw.mp4", 704, 384)
    out = tmp_path / "up.mp4"
    meta = postprocess(raw, out, width=1280, height=720, fps=24)
    assert meta["applied"] is True
    assert meta["source_width"] == 704 and meta["source_height"] == 384
    assert "interpolated, not native" in meta["note"]
    info = probe(out)
    assert (info["width"], info["height"]) == (1280, 720)


def test_passthrough_when_already_at_target(tmp_path):
    raw = make_clip(tmp_path / "raw.mp4", 1280, 720)
    out = tmp_path / "up.mp4"
    meta = postprocess(raw, out, width=1280, height=720, fps=24)
    assert meta["applied"] is False
    assert not out.exists()          # nothing written; the raw clip is used as-is


def test_qa_records_source_resolution_for_upscaled_clip(tmp_path):
    raw = make_clip(tmp_path / "raw.mp4", 704, 384)
    out = tmp_path / "up.mp4"
    postprocess(raw, out, width=1280, height=720, fps=24)
    rep = evaluate_clip(out, expected={"width": 1280, "height": 720, "duration": 2.0, "palette_colors": [], "style_colors": [],
                                       "require_no_watermark": False},
                        previous_frame=None, provider_caps={}, source_path=str(raw))
    assert rep["probe"]["native_width"] == 704 and rep["probe"]["native_height"] == 384
    assert rep["probe"]["width"] == 1280 and rep["probe"]["height"] == 720
    # honesty: the graded clip is at target (composition needs no upscale), and
    # the native source resolution is recorded so the router can see it
    assert rep["upscale_needed"] is False
    assert rep["native_resolution_ok"] is True


def test_black_clip_still_rejected_after_postprocess(tmp_path):
    """Post-processing must never inflate a degenerate clip into acceptance."""
    raw = make_clip(tmp_path / "raw.mp4", 704, 384, black=True)
    out = tmp_path / "up.mp4"
    postprocess(raw, out, width=1280, height=720, fps=24)
    rep = evaluate_clip(out, expected={"width": 1280, "height": 720, "duration": 2.0, "palette_colors": [], "style_colors": [],
                                       "require_no_watermark": False},
                        previous_frame=None, provider_caps={}, source_path=str(raw))
    assert rep["decision"] == "rejected"
    assert any("black" in r for r in rep["reasons"])
