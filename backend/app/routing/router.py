"""Provider selection (README section 11).

Hard filters decide eligibility. Scoring uses ONLY measured data (accepted-clip QA scores, attempt outcomes,
latency) plus capabilities the adapter declares. With no measurements a factor is neutral (0.5) and is
reported as such. Free credits alone never select a provider."""
from __future__ import annotations
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.credits import ledger
from app.database.models import (GenerationArtifact, GenerationAttempt, GenerationJob, ProviderCredential, QualityReport,
                                 QuotaPool, VideoProject, VideoScene)
from app.providers.base import GenerationRequest, ProviderError, capability_supports
from app.providers.registry import build_provider, context_for

BASE_WEIGHTS = {"consistency": 0.25, "quality": 0.25, "free_credits": 0.20, "cost": 0.10, "reliability": 0.10, "speed": 0.05, "resolution": 0.05}

MODE_WEIGHTS = {
    "free_only": BASE_WEIGHTS,
    "open_source_only": BASE_WEIGHTS,
    "lowest_cost": {**BASE_WEIGHTS, "cost": 0.30, "free_credits": 0.25, "quality": 0.15, "consistency": 0.15, "reliability": 0.10, "speed": 0.03, "resolution": 0.02},
    "balanced": BASE_WEIGHTS,
    "max_quality": {**BASE_WEIGHTS, "quality": 0.35, "consistency": 0.30, "free_credits": 0.05, "cost": 0.05, "reliability": 0.12, "speed": 0.05, "resolution": 0.08},
}
PROJECT_TYPE_ADJUST = {
    "children_animation": {"consistency": 0.05, "quality": -0.02, "speed": -0.03},
    "documentary": {"quality": 0.05, "resolution": 0.03, "consistency": -0.04, "free_credits": -0.04},
    "promotion": {"quality": 0.03, "consistency": 0.03, "free_credits": -0.06},
}


def weights_for(project: VideoProject) -> dict[str, float]:
    if project.weights:
        w = dict(project.weights)
    else:
        w = dict(MODE_WEIGHTS.get(project.mode, BASE_WEIGHTS))
        adj = PROJECT_TYPE_ADJUST.get((project.brief or {}).get("project_type", ""), {})
        for k, v in adj.items():
            w[k] = max(0.0, w.get(k, 0) + v)
    s = sum(w.values()) or 1.0
    return {k: v / s for k, v in w.items()}


@dataclass
class Candidate:
    credential: ProviderCredential
    pool: QuotaPool | None
    provider_id: str
    request_seconds: float
    estimate: Any
    caps: dict[str, Any]
    score: float
    breakdown: dict[str, Any] = field(default_factory=dict)


@dataclass
class Selection:
    chosen: Candidate | None
    candidates: list[Candidate]
    rejected: list[dict[str, Any]]
    wait_reason: str | None = None   # WAITING_FOR_CREDITS | WAITING_FOR_PROVIDER | MANUAL_ACTION_REQUIRED


def measured(db: Session, user_id: str, provider_id: str) -> dict[str, float | None]:
    atts = db.query(GenerationAttempt.status, func.count()).join(GenerationJob, GenerationJob.id == GenerationAttempt.job_id)\
        .filter(GenerationJob.user_id == user_id, GenerationAttempt.provider_id == provider_id).group_by(GenerationAttempt.status).all()
    d = dict(atts)
    done = d.get("succeeded", 0) + d.get("failed", 0)
    reliability = d.get("succeeded", 0) / done if done else None
    lat = db.query(func.avg(GenerationAttempt.latency_ms)).join(GenerationJob, GenerationJob.id == GenerationAttempt.job_id)\
        .filter(GenerationJob.user_id == user_id, GenerationAttempt.provider_id == provider_id, GenerationAttempt.status == "succeeded").scalar()
    reports = db.query(QualityReport.report).join(GenerationArtifact, GenerationArtifact.id == QualityReport.artifact_id)\
        .filter(GenerationArtifact.user_id == user_id, GenerationArtifact.provider_id == provider_id).all()
    q = [r[0].get("motion_quality") for r in reports if r[0].get("motion_quality") is not None]
    c = [r[0].get("character_consistency") for r in reports if r[0].get("character_consistency") is not None]
    return {"reliability": reliability, "latency_ms": float(lat) if lat else None,
            "quality": (sum(q) / len(q) / 100) if q else None, "consistency": (sum(c) / len(c) / 100) if c else None, "samples": len(q)}


async def select_provider(db: Session, project: VideoProject, scene: VideoScene, req: GenerationRequest, *, exclude: set[str] | None = None,
                          has_previous_frame: bool = False, needs_references: bool = False) -> Selection:
    exclude = exclude or set()
    creds = db.query(ProviderCredential).filter(ProviderCredential.user_id == project.user_id, ProviderCredential.enabled.is_(True)).all()
    rejected, cands = [], []
    saw_credit_block = False
    saw_manual = None
    w = weights_for(project)
    for cred in creds:
        pid = cred.provider_id
        why = None
        if cred.id in exclude or pid in (scene.avoid_providers or []):
            rejected.append({"provider": pid, "credential": cred.label, "reason": "excluded for this scene after an earlier failure or handoff"})
            continue
        try:
            prov = build_provider(pid, context_for(cred))
            caps = await prov.get_capabilities()
        except Exception as e:  # noqa: BLE001
            rejected.append({"provider": pid, "reason": f"adapter unavailable: {e}"})
            continue
        if caps.get("manual"):
            saw_manual = (cred, caps, prov)
            continue
        secs = capability_supports(caps, req.needed_seconds)
        checks = [
            (secs is not None, "duration not supported"),
            (req.aspect_ratio in (caps.get("aspect_ratios") or []), "aspect ratio not supported"),
            (not caps.get("max_width") or (caps["max_width"] > 0), "resolution unknown"),
            (not (project.require_no_watermark and caps.get("watermark") is not False), "watermark-free output not confirmed"),
            (not (project.require_commercial and caps.get("commercial_ok") is not True), "commercial use not confirmed"),
            (not (project.mode == "open_source_only" and pid not in ("custom_http",) and not caps.get("open_source")), "mode is open_source_only"),
            (not (needs_references and not caps.get("multi_reference")), "character references not supported"),
        ]
        bad = next((m for ok, m in checks if not ok), None)
        if bad:
            rejected.append({"provider": pid, "credential": cred.label, "reason": bad})
            continue
        h = await prov.check_health()
        if not h.get("ok"):
            rejected.append({"provider": pid, "credential": cred.label, "reason": f"health check failed: {h.get('detail')}"})
            continue
        r2 = GenerationRequest(**{**req.to_json(), "duration": secs})
        est = await prov.estimate_cost(r2)
        pool = db.query(QuotaPool).filter(QuotaPool.credential_id == cred.id).order_by(QuotaPool.created_at if hasattr(QuotaPool, "created_at") else QuotaPool.id).first()
        if pool is None or not ledger.pool_is_usable(pool):
            rejected.append({"provider": pid, "credential": cred.label, "reason": "no usable quota pool (exhausted or none connected)"})
            saw_credit_block = True
            continue
        fc = ledger.free_credit(pool)
        if fc is None:
            rejected.append({"provider": pid, "credential": cred.label, "reason": "balance unknown (unknown is not unlimited); enter a balance or connect a balance API"})
            saw_credit_block = True
            continue
        paid = bool(est.is_paid or pool.is_paid)
        if est.amount == float("inf"):
            rejected.append({"provider": pid, "credential": cred.label, "reason": "cost unknown; unknown is not free"})
            continue
        if paid:
            spent = db.query(func.coalesce(func.sum(QuotaPool.consumed), 0)).filter(QuotaPool.user_id == project.user_id, QuotaPool.is_paid.is_(True)).scalar() or 0
            if project.paid_budget <= 0 or est.amount > project.paid_budget - spent:
                rejected.append({"provider": pid, "credential": cred.label, "reason": "paid spending not approved for this project"})
                continue
        if project.mode == "free_only" and paid:
            rejected.append({"provider": pid, "credential": cred.label, "reason": "mode is free_only"})
            continue
        if fc < est.amount - 1e-9:
            rejected.append({"provider": pid, "credential": cred.label, "reason": f"insufficient credits ({fc:.2f} < {est.amount:.2f})"})
            saw_credit_block = True
            continue
        m = measured(db, project.user_id, pid)
        neutral = 0.5
        f_cons = m["consistency"] if m["consistency"] is not None else neutral
        f_q = m["quality"] if m["quality"] is not None else neutral
        f_rel = m["reliability"] if m["reliability"] is not None else neutral
        lat = m["latency_ms"] if m["latency_ms"] else (caps.get("est_latency_s", 60) * 1000)
        f_speed = 1.0 / (1.0 + lat / 60000.0)
        f_free = 0.0 if paid else 1.0
        f_cost = 1.0 if est.amount == 0 else 1.0 / (1.0 + est.amount / max(1.0, req.needed_seconds))
        mw, mh = caps.get("max_width") or 0, caps.get("max_height") or 0
        f_res = min(1.0, (mw * mh) / float(req.width * req.height)) if mw and mh else neutral
        score = (w["consistency"] * f_cons + w["quality"] * f_q + w["free_credits"] * f_free + w["cost"] * f_cost +
                 w["reliability"] * f_rel + w["speed"] * f_speed + w["resolution"] * f_res)
        # conditioning bonus: honoring previous-frame continuity is part of consistency
        if has_previous_frame and caps.get("first_frame"):
            score += 0.05 * w["consistency"] * 4
        cands.append(Candidate(cred, pool, pid, secs, est, caps, score, {
            "consistency": f_cons, "quality": f_q, "free_credits": f_free, "cost": f_cost, "reliability": f_rel, "speed": f_speed, "resolution": f_res,
            "measured_samples": m["samples"], "neutral_when_unmeasured": neutral, "weights": w, "estimate": {"amount": est.amount, "unit": est.unit, "paid": paid},
        }))
    cands.sort(key=lambda c: -c.score)
    if cands:
        return Selection(cands[0], cands, rejected)
    if saw_manual:
        return Selection(None, [], rejected, "MANUAL_ACTION_REQUIRED")
    return Selection(None, [], rejected, "WAITING_FOR_CREDITS" if saw_credit_block else "WAITING_FOR_PROVIDER")
