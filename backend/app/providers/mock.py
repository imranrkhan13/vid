"""LABELED MOCK providers: deterministic simulations of a remote video API.

Isolation rules (README section 33): mocks are never selected unless the user connected one, the UI and
API mark them `is_mock`, every clip carries a visible MOCK badge, and credits here are simulated
`mock-credits` held in a simulated remote account (a directory of JSON files that survives backend restarts,
like a real remote service would). Nothing here claims to be real AI generation.
"""
from __future__ import annotations
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
import uuid
from pathlib import Path
from typing import Any

from app.core.config import settings
from .base import (CostEstimate, GenerationRequest, JobStatus, ProviderContext, ProviderError, QuotaInfo, VideoProvider)

VARIANTS: dict[str, dict[str, Any]] = {
    "mock_a": {"name": "MOCK Provider A", "min_duration": 2, "max_duration": 8, "max_w": 1080, "max_h": 1920,
               "first_frame": True, "multi_reference": True, "cost_per_second": 1.0, "latency_s": 1.0, "speed": 0.9, "quality_note": "declared high"},
    "mock_b": {"name": "MOCK Provider B", "min_duration": 4, "max_duration": 6, "max_w": 1080, "max_h": 1920,
               "first_frame": True, "multi_reference": True, "cost_per_second": 0.8, "latency_s": 1.5, "speed": 0.7, "quality_note": "declared high"},
    "mock_c": {"name": "MOCK Provider C (lower resolution, no reference conditioning)", "min_duration": 2, "max_duration": 10, "max_w": 720, "max_h": 1280,
               "first_frame": False, "multi_reference": False, "cost_per_second": 0.5, "latency_s": 0.8, "speed": 0.95, "quality_note": "declared medium"},
}


def _acct_dir(cred_id: str) -> Path:
    return settings.remote_dir / cred_id


def _load(p: Path) -> dict:
    return json.loads(p.read_text())


def _save(p: Path, d: dict):
    tmp = p.with_suffix(".tmp")
    tmp.write_text(json.dumps(d))
    os.replace(tmp, p)


class MockProvider(VideoProvider):
    category = "mock"
    is_mock = True

    def __init__(self, ctx: ProviderContext, variant: str):
        super().__init__(ctx)
        self.variant = variant
        self.v = VARIANTS[variant]
        self.dir = _acct_dir(ctx.credential_id)
        self.dir.mkdir(parents=True, exist_ok=True)
        (self.dir / "jobs").mkdir(exist_ok=True)
        self.acct_file = self.dir / "account.json"
        if not self.acct_file.exists():
            _save(self.acct_file, {"credits": float(ctx.config.get("initial_credits", 0)), "fault_counts": {}})

    # --- helpers -----------------------------------------------------------------------------------
    def _acct(self) -> dict:
        return _load(self.acct_file)

    def _pending_cost(self) -> float:
        total = 0.0
        for jd in (self.dir / "jobs").iterdir():
            try:
                st = _load(jd / "state.json")
            except Exception:  # noqa: BLE001
                continue
            if st["state"] in ("queued", "running"):
                total += st.get("cost", 0)
        return total

    def _fault(self, scene_index: int) -> str | None:
        acct = self._acct()
        for f in self.ctx.config.get("faults", []):
            if f.get("scene_index") != scene_index:
                continue
            key = f"{f['kind']}:{scene_index}"
            used = acct["fault_counts"].get(key, 0)
            if used < int(f.get("times", 1)):
                acct["fault_counts"][key] = used + 1
                _save(self.acct_file, acct)
                return f["kind"]
        return None

    # --- interface ---------------------------------------------------------------------------------
    async def get_capabilities(self) -> dict[str, Any]:
        v = self.v
        return {
            "label": v["name"], "is_mock": True, "modes": ["text_to_video", "image_to_video"] if v["first_frame"] else ["text_to_video"],
            "min_duration": v["min_duration"], "max_duration": v["max_duration"],
            "aspect_ratios": ["9:16", "16:9", "1:1"], "max_width": v["max_w"], "max_height": v["max_h"],
            "first_frame": v["first_frame"], "last_frame": False, "multi_reference": v["multi_reference"],
            "character_reference": v["multi_reference"], "audio": False, "watermark": False, "commercial_ok": True,
            "cost_per_second": v["cost_per_second"], "unit": "mock-credits", "paid": False,
            "est_latency_s": v["latency_s"] + 2, "speed_score": v["speed"], "concurrency": 1,
            "source": "declared by the mock simulation, not a real provider",
        }

    async def check_health(self) -> dict[str, Any]:
        return {"ok": True, "detail": "mock simulation reachable"}

    async def get_quota(self) -> QuotaInfo:
        return QuotaInfo(available=max(0.0, self._acct()["credits"] - self._pending_cost()), unit="mock-credits", source="verified_api",
                         detail="simulated remote balance")

    async def estimate_cost(self, request: GenerationRequest) -> CostEstimate:
        return CostEstimate(amount=round(request.duration * self.v["cost_per_second"], 4), unit="mock-credits", is_paid=False,
                            confidence="high", basis=f"{self.v['cost_per_second']} mock-credits per second")

    async def find_job_by_idempotency_key(self, key: str) -> str | None:
        f = self.dir / f"idem_{hashlib.sha256(key.encode()).hexdigest()[:24]}.json"
        return _load(f)["job_id"] if f.exists() else None

    async def submit_generation(self, request: GenerationRequest) -> str:
        existing = await self.find_job_by_idempotency_key(request.idempotency_key)
        if existing:
            return existing
        cost = round(request.duration * self.v["cost_per_second"], 4)
        acct = self._acct()
        if acct["credits"] - self._pending_cost() < cost - 1e-9:
            raise ProviderError("QUOTA_EXHAUSTED", f"mock account has {acct['credits']:.2f} credits, job needs {cost:.2f}")
        if request.duration < self.v["min_duration"] or request.duration > self.v["max_duration"]:
            raise ProviderError("UNSUPPORTED_REQUEST", "duration outside provider range")
        fault = self._fault(request.scene_index)
        if fault == "unavailable":
            raise ProviderError("PROVIDER_UNAVAILABLE", "simulated outage")
        if fault == "rate_limited":
            raise ProviderError("RATE_LIMITED", "simulated rate limit", retry_after=2)
        job_id = "mj_" + uuid.uuid4().hex[:12]
        jd = self.dir / "jobs" / job_id
        jd.mkdir(parents=True)
        h = request.handoff
        pos = (h.get("continuity") or {}).get("character_positions") or {}
        rw = min(request.width, self.v["max_w"])
        rh = min(request.height, self.v["max_h"])
        if rw * request.height != request.width * rh:  # keep aspect ratio
            rh = int(round(rw * request.height / request.width))
        rw, rh = rw - rw % 2, rh - rh % 2
        if settings.mock_fast:
            rw, rh = max(2, rw // 3 - (rw // 3) % 2), max(2, rh // 3 - (rh // 3) % 2)
        style = h.get("style", {})
        render_req = {
            "latency_s": self.v["latency_s"] * (0.2 if settings.mock_fast else 1), "render_width": rw, "render_height": rh, "fps": request.fps,
            "duration": request.duration, "scene_index": request.scene_index, "caption": h.get("scene_title", request.prompt[:80]),
            "badge": f"MOCK · {self.v['name'].split(' (')[0].replace('MOCK ', '')}",
            "palette": style.get("palette", {}),
            "characters": [{"name": c["name"], "colors": c.get("palette", {}), "scale": c.get("scale", 1.0),
                            "walking": "walk" in (h.get("motion", "") + request.prompt).lower() or True, "travel": 0.28}
                           for c in h.get("characters", [])],
            "start_positions": pos if self.v["first_frame"] or True else {},
            "quality_fault": "black" if fault == "bad_quality" else None,
        }
        _save(jd / "request.json", render_req)
        state = {"state": "running", "cost": cost, "scene_index": request.scene_index, "created_at": time.time(),
                 "idempotency_key": request.idempotency_key, "received_handoff": h, "received_refs": request.reference_images,
                 "received_previous_frame": request.previous_frame}
        if fault == "timeout":
            state["hang"] = True
        elif fault == "fail":
            state.update(state="failed", error_code="GENERATION_FAILED", detail="simulated generation failure")
        elif fault == "policy":
            state.update(state="failed", error_code="CONTENT_POLICY_REJECTION", detail="simulated policy rejection")
        _save(jd / "state.json", state)
        _save(self.dir / f"idem_{hashlib.sha256(request.idempotency_key.encode()).hexdigest()[:24]}.json", {"job_id": job_id})
        if state["state"] == "running" and not state.get("hang"):
            subprocess.Popen([sys.executable, "-m", "app.providers.mock_render", str(jd)], start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, cwd=str(Path(__file__).resolve().parents[2]))
        return job_id

    async def get_job_status(self, job_id: str) -> JobStatus:
        jd = self.dir / "jobs" / job_id
        if not (jd / "state.json").exists():
            return JobStatus(state="unknown", detail="job not found at provider")
        st = _load(jd / "state.json")
        if st["state"] == "succeeded" and not st.get("billed"):
            acct = self._acct()
            acct["credits"] = round(acct["credits"] - st["cost"], 6)
            _save(self.acct_file, acct)
            st["billed"] = True
            _save(jd / "state.json", st)
        return JobStatus(state=st["state"], error_code=st.get("error_code"), detail=st.get("detail"),
                         actual_cost=st["cost"] if st["state"] == "succeeded" else None)

    async def download_result(self, job_id: str, dest: Path) -> Path:
        src = self.dir / "jobs" / job_id / "out.mp4"
        if not src.exists():
            raise ProviderError("DOWNLOAD_FAILED", "result file missing")
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(src, dest)
        return dest

    async def cancel_job(self, job_id: str) -> None:
        f = self.dir / "jobs" / job_id / "state.json"
        if f.exists():
            st = _load(f)
            if st["state"] in ("queued", "running"):
                st["state"] = "cancelled"
                _save(f, st)

    # test/inspection helper (used by tests to assert what context the "remote" received)
    def received(self, job_id: str) -> dict:
        return _load(self.dir / "jobs" / job_id / "state.json")

    def end_positions(self, job_id: str) -> dict:
        return _load(self.dir / "jobs" / job_id / "state.json").get("end_positions", {})
