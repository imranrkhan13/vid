"""A clip shorter than requested must not be credited as the requested duration."""
import subprocess

from app.quality.qa import evaluate_clip


def clip(path, secs):
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size=384x704:rate=30:duration={secs}", "-pix_fmt", "yuv420p", "-c:v", "libx264", str(path)], check=True)
    return path


def test_short_clip_is_rejected_and_matching_clip_passes(tmp_path):
    exp = {"width": 720, "height": 1280, "duration": 4.0, "palette_colors": [], "style_colors": []}
    short = evaluate_clip(clip(tmp_path / "s.mp4", 1.9), expected=exp, previous_frame=None, provider_caps={})
    assert short["decision"] == "rejected" and any("differs from requested" in r for r in short["reasons"])
    ok = evaluate_clip(clip(tmp_path / "o.mp4", 4.0), expected=exp, previous_frame=None, provider_caps={})
    assert not any("differs from requested" in r for r in ok["reasons"])
