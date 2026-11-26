"""Deterministic synthetic clip renderer used ONLY by the labeled mock providers.

It draws a simple cartoon scene frame by frame (cv2 + numpy) and pipes raw frames to ffmpeg.
It exists so the orchestration (failover, handoff, recovery, QA, composition) can be tested end to end
without paid credits. Output is not AI generated and every clip carries a visible MOCK badge.
Run as: python -m app.providers.mock_render <job_dir>
"""
from __future__ import annotations
import json
import math
import subprocess
import sys
import time
from pathlib import Path

import cv2
import numpy as np
from PIL import Image, ImageDraw, ImageFont

FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
]


def _font(size: int):
    for f in FONT_CANDIDATES:
        if Path(f).exists():
            return ImageFont.truetype(f, size)
    return ImageFont.load_default()


def hex_rgb(h: str):
    h = h.lstrip("#")
    return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))


def lerp(a, b, t):
    return a + (b - a) * t


def caption_layer(w, h, title, tag):
    img = Image.new("RGBA", (w, h), (0, 0, 0, 0))
    d = ImageDraw.Draw(img)
    f = _font(max(18, w // 22))
    small = _font(max(14, w // 40))
    pad = w // 24
    d.rounded_rectangle([pad, h - pad - w // 6, w - pad, h - pad], radius=w // 40, fill=(0, 0, 0, 120))
    # wrap title
    words, lines, cur = title.split(), [], ""
    for wd in words:
        t = (cur + " " + wd).strip()
        if d.textlength(t, font=f) > w - 3 * pad:
            lines.append(cur)
            cur = wd
        else:
            cur = t
    lines.append(cur)
    y = h - pad - w // 6 + pad // 2
    for ln in lines[:3]:
        d.text((pad * 1.5, y), ln, font=f, fill=(255, 255, 255, 255))
        y += f.size + 4
    badge = tag
    bw = d.textlength(badge, font=small) + pad
    d.rounded_rectangle([pad, pad, pad + bw, pad + small.size + pad // 2], radius=pad // 3, fill=(255, 80, 80, 210))
    d.text((pad * 1.5, pad + pad // 6), badge, font=small, fill=(255, 255, 255, 255))
    a = np.asarray(img).astype(np.float32)
    return a[..., :3], a[..., 3:4] / 255.0


def draw_character(frame, cx, cy, scale, colors, t, walking, facing=1):
    body = hex_rgb(colors.get("body", "#4F46E5"))[::-1]
    skin = hex_rgb(colors.get("skin", "#F2C9A0"))[::-1]
    hair = hex_rgb(colors.get("hair", "#3B2A20"))[::-1]
    s = scale
    bob = math.sin(t * 9) * 0.03 * s if walking else math.sin(t * 2) * 0.01 * s
    cy = cy + bob
    phase = math.sin(t * 9) if walking else 0
    # legs
    for side in (-1, 1):
        lx = cx + side * 0.12 * s + side * phase * 0.10 * s
        cv2.line(frame, (int(lx), int(cy + 0.55 * s)), (int(lx + side * phase * 0.08 * s), int(cy + 1.05 * s)), (60, 50, 60), max(2, int(0.1 * s)), cv2.LINE_AA)
    # body
    cv2.ellipse(frame, (int(cx), int(cy + 0.4 * s)), (int(0.34 * s), int(0.46 * s)), 0, 0, 360, body, -1, cv2.LINE_AA)
    # arms
    for side in (-1, 1):
        ax = cx + side * 0.34 * s
        cv2.line(frame, (int(ax), int(cy + 0.2 * s)), (int(ax + side * 0.1 * s), int(cy + 0.2 * s + 0.35 * s + side * phase * 0.12 * s)), skin, max(2, int(0.08 * s)), cv2.LINE_AA)
    # head
    cv2.circle(frame, (int(cx), int(cy - 0.25 * s)), int(0.36 * s), skin, -1, cv2.LINE_AA)
    cv2.ellipse(frame, (int(cx), int(cy - 0.42 * s)), (int(0.38 * s), int(0.26 * s)), 0, 180, 360, hair, -1, cv2.LINE_AA)
    for side in (-1, 1):
        cv2.circle(frame, (int(cx + side * 0.13 * s + facing * 0.04 * s), int(cy - 0.25 * s)), max(2, int(0.045 * s)), (30, 30, 30), -1, cv2.LINE_AA)
    cv2.ellipse(frame, (int(cx + facing * 0.03 * s), int(cy - 0.12 * s)), (int(0.1 * s), int(0.06 * s)), 0, 20, 160, (60, 40, 40), 2, cv2.LINE_AA)


def main(job_dir: Path):
    req = json.loads((job_dir / "request.json").read_text())
    st = json.loads((job_dir / "state.json").read_text())
    time.sleep(float(req.get("latency_s", 1.0)))
    w, h, fps = int(req["render_width"]), int(req["render_height"]), int(req["fps"])
    dur = float(req["duration"])
    n = max(1, int(round(dur * fps)))
    pal = req.get("palette", {})
    c1 = np.array(hex_rgb(pal.get("sky", "#BFE3FF"))[::-1], np.float32)
    c2 = np.array(hex_rgb(pal.get("ground", "#8ED081"))[::-1], np.float32)
    bg = np.zeros((h, w, 3), np.float32)
    horizon = int(h * 0.62)
    for y in range(h):
        if y < horizon:
            bg[y] = c1 * (1 - 0.25 * y / horizon) + np.array([255, 255, 255], np.float32) * 0.25 * y / horizon
        else:
            bg[y] = c2 * (1 - 0.3 * (y - horizon) / (h - horizon))
    bad = req.get("quality_fault") == "black"
    cap_rgb, cap_a = caption_layer(w, h, req.get("caption", ""), req.get("badge", "MOCK PROVIDER"))
    chars = req.get("characters", [])
    starts = req.get("start_positions", {})
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "rawvideo", "-pix_fmt", "bgr24", "-s", f"{w}x{h}", "-r", str(fps), "-i", "-",
           "-an", "-c:v", "libx264", "-pix_fmt", "yuv420p", "-preset", "veryfast", "-crf", "20", str(job_dir / "out.tmp.mp4")]
    p = subprocess.Popen(cmd, stdin=subprocess.PIPE)
    end_pos = {}
    for i in range(n):
        t = i / fps
        u = i / max(1, n - 1)
        frame = bg.copy()
        sun_x = int(w * (0.15 + 0.7 * ((req.get("scene_index", 0) * 0.13 + u * 0.1) % 1)))
        cv2.circle(frame, (sun_x, int(h * 0.14)), int(w * 0.07), (120, 220, 255), -1, cv2.LINE_AA)
        for ci in range(3):  # drifting clouds so every mock clip has real motion
            cxp = int(((0.1 + 0.33 * ci + u * (0.25 + 0.1 * ci)) % 1.2 - 0.1) * w)
            cyp = int(h * (0.22 + 0.08 * ci))
            for dx, dy, rr in ((0, 0, 0.05), (0.05, -0.01, 0.06), (0.1, 0, 0.045)):
                cv2.circle(frame, (cxp + int(dx * w), cyp + int(dy * h)), int(w * rr), (255, 255, 255), -1, cv2.LINE_AA)
        for k, ch in enumerate(chars):
            sx = starts.get(ch["name"], {}).get("x", 0.22 + 0.25 * k)
            ex = min(0.86, max(0.14, sx + ch.get("travel", 0.28))) if sx < 0.6 else max(0.14, sx - ch.get("travel", 0.28))
            xf = lerp(sx, ex, u)
            facing = 1 if ex >= sx else -1
            cy = horizon + h * 0.05 + k * h * 0.03
            draw_character(frame, xf * w, cy - w * 0.18, w * 0.30 * ch.get("scale", 1.0), ch.get("colors", {}), t, ch.get("walking", True), facing)
            end_pos[ch["name"]] = {"x": round(float(ex), 4), "y": 0.0}
        frame = frame * (1 - cap_a) + cap_rgb[..., ::-1] * cap_a
        if bad:
            frame = frame * 0.0
        p.stdin.write(np.clip(frame, 0, 255).astype(np.uint8).tobytes())
    p.stdin.close()
    rc = p.wait()
    if rc != 0:
        raise RuntimeError(f"ffmpeg exit {rc}")
    (job_dir / "out.tmp.mp4").rename(job_dir / "out.mp4")
    st.update(state="succeeded", end_positions=end_pos, finished_at=time.time())
    (job_dir / "state.json").write_text(json.dumps(st))


if __name__ == "__main__":
    jd = Path(sys.argv[1])
    try:
        main(jd)
    except Exception as e:  # noqa: BLE001
        s = json.loads((jd / "state.json").read_text())
        s.update(state="failed", error_code="GENERATION_FAILED", detail=str(e)[:300])
        (jd / "state.json").write_text(json.dumps(s))
