"""Durable workflow engine (README sections 8, 13, 19, 24).

Every transition is persisted before the next side effect. A job row (with an idempotency key and a credit
reservation) is committed BEFORE the provider is called, so a crash can never lose or double-submit work:
on restart `recover()` reconciles by idempotency key and the engine continues from the database.
One scene is in flight per project because each scene continues from the previous accepted ending frame."""
from __future__ import annotations
import asyncio
import os
import json
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from sqlalchemy.orm import Session

from app.audio.align import segments_from_scenes, to_srt
from app.continuity.handoff import build_handoff, reference_images, store_handoff
from app.credits import ledger
from app.database.models import (AudioTrack, ExportJob, GenerationArtifact, GenerationAttempt, GenerationJob, MediaTimeline,
                                 NarrationSegment, ProjectCheckpoint, ProviderCredential, QualityReport, QuotaPool, VideoProject,
                                 VideoScene, WorkflowEvent, utcnow)
from app.database.session import new_session
from app.media import ffmpeg as fx
from app.media.compose import compose, validate_output
from app.providers.base import GenerationRequest, PROVIDER_LEVEL_ERRORS, ProviderError
from app.providers.registry import build_provider, context_for
from app.quality.qa import evaluate_clip
from app.routing.router import select_provider
from app.storage.local import project_dir, persist_file, restore_file, headroom, StorageBudgetError

PROJECT_ACTIVE = {"READY", "SELECTING_PROVIDER", "GENERATING", "DOWNLOADING", "VALIDATING", "RETRYING", "SWITCHING_PROVIDER",
                  "WAITING_FOR_CREDITS", "WAITING_FOR_PROVIDER", "MANUAL_ACTION_REQUIRED", "COMPOSING", "FINAL_VALIDATION"}
TERMINAL = {"COMPLETED", "FAILED", "CANCELLED"}
WAIT_SECONDS = 4.0


def iso(dt: datetime | None = None) -> str:
    return (dt or datetime.now(timezone.utc)).isoformat()


def emit(db: Session, project_id: str, kind: str, message: str, data: dict | None = None, scene_id: str | None = None) -> int:
    ev = WorkflowEvent(project_id=project_id, scene_id=scene_id, kind=kind, message=message, data=data)
    db.add(ev)
    db.commit()
    return ev.id


def set_project(db: Session, p: VideoProject, status: str, detail: str | None = None):
    if p.status != status:
        emit(db, p.id, "project_state", f"{p.status} -> {status}" + (f": {detail}" if detail else ""), {"from": p.status, "to": status})
    p.status = status
    p.status_detail = detail
    db.commit()


def set_scene(db: Session, s: VideoScene, status: str, note: str | None = None):
    if s.status != status:
        emit(db, s.project_id, "scene_state", f"scene {s.idx + 1}: {s.status} -> {status}" + (f" ({note})" if note else ""), {"from": s.status, "to": status}, s.id)
    s.status = status
    db.commit()


def _wait_until(seconds: float) -> str:
    return iso(datetime.now(timezone.utc) + timedelta(seconds=seconds))


def _due(s: VideoScene) -> bool:
    return not s.not_before or s.not_before <= iso()


def checkpoint(db: Session, p: VideoProject, label: str):
    scenes = db.query(VideoScene).filter(VideoScene.project_id == p.id).order_by(VideoScene.idx).all()
    state = {"project_id": p.id, "title": p.title, "target_duration_seconds": p.target_duration, "accepted_duration_seconds": round(p.accepted_duration, 3),
             "remaining_duration_seconds": round(p.target_duration - p.accepted_duration, 3), "aspect_ratio": p.aspect_ratio, "resolution": f"{p.width}x{p.height}",
             "fps": p.fps, "style_version": p.style_version, "character_bible_version": p.character_bible_version,
             "completed_scenes": [s.id for s in scenes if s.status == "ACCEPTED"],
             "next_scene": next((s.id for s in scenes if s.status != "ACCEPTED"), None), "last_accepted_provider": p.last_accepted_provider,
             "last_accepted_frame": p.last_accepted_frame, "status": p.status}
    db.add(ProjectCheckpoint(project_id=p.id, label=label, state=state))
    db.commit()


def canonical_state(db: Session, p: VideoProject) -> dict[str, Any]:
    scenes = db.query(VideoScene).filter(VideoScene.project_id == p.id).order_by(VideoScene.idx).all()
    acc = sum(s.duration for s in scenes if s.status == "ACCEPTED")
    return {"project_id": p.id, "title": p.title, "target_duration_seconds": p.target_duration, "accepted_duration_seconds": round(acc, 3),
            "remaining_duration_seconds": round(p.target_duration - acc, 3), "aspect_ratio": p.aspect_ratio, "resolution": f"{p.width}x{p.height}",
            "fps": p.fps, "style_version": p.style_version, "character_bible_version": p.character_bible_version,
            "audio_strategy": (p.brief or {}).get("audio_strategy", "external_master_narration"),
            "completed_scenes": [s.id for s in scenes if s.status == "ACCEPTED"],
            "next_scene": next((s.id for s in scenes if s.status != "ACCEPTED"), None), "last_accepted_provider": p.last_accepted_provider,
            "last_accepted_frame": p.last_accepted_frame, "status": p.status, "status_detail": p.status_detail,
            "progress_basis": "accepted clips on the final timeline (not completed API requests)"}


def recompute_accepted(db: Session, p: VideoProject):
    scenes = db.query(VideoScene).filter(VideoScene.project_id == p.id).all()
    p.accepted_duration = round(sum(s.duration for s in scenes if s.status == "ACCEPTED"), 6)
    db.commit()


def _artifact_dir(p: VideoProject) -> Path:
    return project_dir(p.user_id, p.id)


async def _provider_for(db: Session, credential_id: str):
    cred = db.get(ProviderCredential, credential_id)
    if cred is None:
        raise ProviderError("PROVIDER_UNAVAILABLE", "credential was removed")
    return cred, build_provider(cred.provider_id, context_for(cred))


def breaker_open(db: Session, user_id: str, credential_id: str) -> bool:
    """Circuit breaker: three consecutive provider-level failures in the last 5 minutes open it."""
    cutoff = utcnow() - timedelta(minutes=5)
    rows = (db.query(GenerationAttempt).join(GenerationJob, GenerationJob.id == GenerationAttempt.job_id)
            .filter(GenerationJob.credential_id == credential_id, GenerationAttempt.started_at >= cutoff)
            .order_by(GenerationAttempt.started_at.desc()).limit(3).all())
    return len(rows) == 3 and all(r.status == "failed" and r.error_code in ("PROVIDER_UNAVAILABLE", "RATE_LIMITED", "GENERATION_TIMEOUT") for r in rows)


async def refresh_quota(db: Session, user_id: str):
    for pool in db.query(QuotaPool).filter(QuotaPool.user_id == user_id, QuotaPool.balance_source == "verified_api").all():
        if pool.reserved > 1e-9:
            continue
        cred = db.get(ProviderCredential, pool.credential_id)
        if not cred or not cred.enabled:
            continue
        try:
            q = await build_provider(cred.provider_id, context_for(cred)).get_quota()
        except Exception:  # noqa: BLE001
            continue
        if q.available is not None:
            pool.available = q.available
            pool.confidence = "high"
            if pool.exhausted and q.available > 0 and (not pool.exhausted_until or pool.exhausted_until <= iso()):
                pool.exhausted, pool.exhausted_until = False, None
            db.commit()


def scene_request(db: Session, p: VideoProject, s: VideoScene, attempt_no: int) -> GenerationRequest:
    return GenerationRequest(
        idempotency_key=f"{s.id}:v{s.scene_version}:a{attempt_no}", project_id=p.id, scene_id=s.id, scene_index=s.idx, prompt=s.prompt,
        negative_prompt=s.negative_prompt or "", duration=s.duration, needed_seconds=s.duration, aspect_ratio=p.aspect_ratio,
        width=p.width, height=p.height, fps=p.fps)


async def start_generation(db: Session, p: VideoProject, s: VideoScene):
    max_attempts = int((p.brief or {}).get("max_attempts_per_scene", 8))
    if s.attempt_counter >= max_attempts:
        s.last_error = f"retry budget exhausted after {s.attempt_counter} attempts"
        set_scene(db, s, "REVIEW", "retry budget exhausted")
        set_project(db, p, "MANUAL_ACTION_REQUIRED", f"scene {s.idx + 1} needs review: {s.last_error}")
        return
    await refresh_quota(db, p.user_id)
    n = s.attempt_counter + 1
    req = scene_request(db, p, s, n)
    restore_file(p.last_accepted_frame)
    prev_ok = db.query(VideoScene).filter(VideoScene.project_id == p.id, VideoScene.idx < s.idx, VideoScene.status == "ACCEPTED").count() > 0
    has_refs = bool(reference_images(build_handoff(db, p, s, reason="probe", previous_provider=p.last_accepted_provider)))
    exclude = {c.id for c in db.query(ProviderCredential).filter(ProviderCredential.user_id == p.user_id).all() if breaker_open(db, p.user_id, c.id)}
    set_project(db, p, "SELECTING_PROVIDER")
    sel = await select_provider(db, p, s, req, exclude=exclude, has_previous_frame=prev_ok, needs_references=False)
    if sel.chosen is None:
        reasons = "; ".join(f"{r.get('provider')}: {r['reason']}" for r in sel.rejected[:6]) or "no provider connected"
        if sel.wait_reason == "MANUAL_ACTION_REQUIRED":
            await _manual_job(db, p, s)
            return
        s.not_before = _wait_until(WAIT_SECONDS)
        set_scene(db, s, sel.wait_reason or "WAITING_FOR_PROVIDER", reasons)
        set_project(db, p, sel.wait_reason or "WAITING_FOR_PROVIDER", reasons[:500])
        emit(db, p.id, "no_provider", f"no eligible provider for scene {s.idx + 1}", {"rejected": sel.rejected, "accepted_seconds": p.accepted_duration,
             "remaining_seconds": round(p.target_duration - p.accepted_duration, 3)}, s.id)
        return
    c = sel.chosen
    prev_provider = p.last_accepted_provider
    if s.last_error and "QUOTA" in (s.last_error or ""):
        reason = "quota_exhausted"
    elif prev_provider and prev_provider != c.provider_id and any(
            r.get("provider") == prev_provider and ("insufficient credits" in r["reason"] or "exhausted" in r["reason"]) for r in sel.rejected):
        reason = "quota_exhausted"
        emit(db, p.id, "provider_exhausted", f"{prev_provider} has no usable credits; {p.accepted_duration:.1f}s of {p.target_duration:.1f}s accepted and preserved, "
             f"{p.target_duration - p.accepted_duration:.1f}s remain; continuing scene {s.idx + 1} on {c.provider_id}", None, s.id)
    elif s.attempt_counter > 0:
        reason = "retry_or_provider_switch"
    else:
        reason = "initial" if prev_provider in (None, c.provider_id) else "provider_change_between_scenes"
    pkg = build_handoff(db, p, s, reason=reason, previous_provider=prev_provider, to_caps=c.caps, requested_seconds=c.request_seconds)
    store_handoff(db, p, s, pkg, c.provider_id)
    req.duration = c.request_seconds
    req.handoff = pkg
    req.reference_images = reference_images(pkg) if c.caps.get("multi_reference") else []
    req.previous_frame = pkg["continuity"]["previous_frame"] if c.caps.get("first_frame") else None
    job = GenerationJob(user_id=p.user_id, project_id=p.id, scene_id=s.id, credential_id=c.credential.id, provider_id=c.provider_id, pool_id=c.pool.id if c.pool else None,
                        status="SUBMITTING", idempotency_key=req.idempotency_key, requested_seconds=c.request_seconds, reserved=c.estimate.amount,
                        request={"selection": c.breakdown, "handoff_reason": reason, "estimate": {"amount": c.estimate.amount, "unit": c.estimate.unit}})
    db.add(job)
    s.attempt_counter = n
    db.commit()
    if c.pool and not ledger.reserve(db, c.pool.id, c.estimate.amount, p.user_id, job.id):
        job.status = "ABANDONED"
        job.reserved = 0
        s.attempt_counter = n - 1
        db.commit()
        return
    emit(db, p.id, "provider_selected", f"scene {s.idx + 1} -> {c.provider_id} ({c.credential.label}), score {c.score:.3f}, reserve {c.estimate.amount} {c.estimate.unit}",
         {"scores": [{"provider": x.provider_id, "score": round(x.score, 4)} for x in sel.candidates], "rejected": sel.rejected, "reason": reason,
          "handoff_degraded": pkg["continuity"]["degraded"]}, s.id)
    set_scene(db, s, "GENERATING", f"via {c.provider_id}")
    set_project(db, p, "GENERATING", f"scene {s.idx + 1} on {c.provider_id}")
    started = time.time()
    att = GenerationAttempt(job_id=job.id, provider_id=c.provider_id, n=n, status="submitting")
    db.add(att)
    db.commit()
    prov = build_provider(c.provider_id, context_for(c.credential))
    try:
        pj = await prov.submit_generation(req)
        job.provider_job_id = pj
        job.status = "SUBMITTED"
        job.submitted_at = utcnow()
        att.status = "submitted"
        db.commit()
    except Exception as e:  # noqa: BLE001
        err = await prov.normalize_error(e)
        await handle_error(db, p, s, job, att, err, latency=int((time.time() - started) * 1000))


async def _manual_job(db: Session, p: VideoProject, s: VideoScene):
    cred = db.query(ProviderCredential).filter(ProviderCredential.user_id == p.user_id, ProviderCredential.provider_id == "manual", ProviderCredential.enabled.is_(True)).first()
    pkg = build_handoff(db, p, s, reason="manual_assisted", previous_provider=p.last_accepted_provider, to_caps={"first_frame": True, "multi_reference": True})
    store_handoff(db, p, s, pkg, "manual")
    n = s.attempt_counter + 1
    s.attempt_counter = n
    job = GenerationJob(user_id=p.user_id, project_id=p.id, scene_id=s.id, credential_id=cred.id, provider_id="manual", status="MANUAL",
                        idempotency_key=f"{s.id}:v{s.scene_version}:a{n}", requested_seconds=s.duration, request={"handoff_reason": "manual_assisted"})
    db.add(job)
    db.commit()
    s.last_error = "waiting for a manually generated clip"
    set_scene(db, s, "MANUAL_ACTION_REQUIRED", "no automatic provider eligible; manual task created")
    set_project(db, p, "MANUAL_ACTION_REQUIRED", f"scene {s.idx + 1}: generate the clip with the supplied prompt and import it")


async def handle_error(db: Session, p: VideoProject, s: VideoScene, job: GenerationJob, att: GenerationAttempt | None, err: ProviderError, latency: int | None = None):
    code = err.code
    if att:
        att.status = "failed"
        att.error_code = code
        att.detail = err.message[:500]
        att.latency_ms = latency
        att.ended_at = utcnow()
    job.status = "FAILED"
    job.error_code, job.error_detail = code, err.message[:500]
    if job.pool_id and job.reserved:
        ledger.release(db, job.pool_id, job.reserved, p.user_id, job.id, f"released after {code}")
        job.reserved = 0
    db.commit()
    emit(db, p.id, "error", f"scene {s.idx + 1}: {code} {err.message[:200]}", {"code": code, "provider": job.provider_id}, s.id)
    s.last_error = f"{code}: {err.message[:300]}"
    cred = db.get(ProviderCredential, job.credential_id)
    if code in ("QUOTA_EXHAUSTED", "CREDITS_EXHAUSTED"):
        if job.pool_id:
            ledger.mark_exhausted(db, job.pool_id, err.reset_at, f"{job.provider_id}: {err.message[:120]}")
        emit(db, p.id, "provider_exhausted", f"{job.provider_id} exhausted; {p.accepted_duration:.1f}s of {p.target_duration:.1f}s accepted and preserved, "
             f"{p.target_duration - p.accepted_duration:.1f}s remain", {"reset_at": err.reset_at}, s.id)
        set_project(db, p, "SWITCHING_PROVIDER", f"{job.provider_id} exhausted")
        set_scene(db, s, "RETRYING", "switching provider (not counted as a retry)")
    elif code == "AUTHENTICATION_FAILED":
        s.avoid_providers = list(set((s.avoid_providers or []) + [job.provider_id]))
        if cred:
            cred.config = {**(cred.config or {}), "auth_failed_at": iso()}
        set_scene(db, s, "RETRYING", "credential rejected; trying another provider")
    elif code == "RATE_LIMITED":
        s.not_before = _wait_until(err.retry_after or 5)
        set_scene(db, s, "RETRYING", f"backoff {err.retry_after or 5}s")
    elif code in ("PROVIDER_UNAVAILABLE", "MODEL_UNAVAILABLE", "UNSUPPORTED_REQUEST"):
        s.avoid_providers = list(set((s.avoid_providers or []) + [job.provider_id]))
        set_scene(db, s, "RETRYING", "provider unavailable; switching")
    elif code == "CONTENT_POLICY_REJECTION":
        set_scene(db, s, "REVIEW", "content policy rejection: the scene text must be edited; not rerouted to another provider")
        set_project(db, p, "MANUAL_ACTION_REQUIRED", f"scene {s.idx + 1} was rejected by the provider's content policy; edit the scene")
    elif code in ("GENERATION_TIMEOUT", "AMBIGUOUS_JOB_STATE"):
        s.retries += 1
        s.avoid_providers = list(set((s.avoid_providers or []) + [job.provider_id]))
        set_scene(db, s, "RETRYING", "timed out; switching provider after reconciliation")
    else:  # GENERATION_FAILED, GPU_OUT_OF_MEMORY, DOWNLOAD_FAILED, QUALITY...
        s.retries += 1
        if s.retries >= 2:
            s.avoid_providers = list(set((s.avoid_providers or []) + [job.provider_id]))
        s.not_before = _wait_until(min(10, 0.5 * 2 ** s.retries))
        set_scene(db, s, "RETRYING", f"retry {s.retries}")
    db.commit()


async def poll_job(db: Session, p: VideoProject, s: VideoScene, job: GenerationJob):
    cred, prov = await _provider_for(db, job.credential_id)
    att = db.query(GenerationAttempt).filter(GenerationAttempt.job_id == job.id).order_by(GenerationAttempt.n.desc()).first()
    started = job.submitted_at or job.created_at
    if started.tzinfo is None:
        started = started.replace(tzinfo=timezone.utc)
    elapsed = (utcnow() - started).total_seconds()
    caps = await prov.get_capabilities()
    timeout_s = float((cred.config or {}).get("job_timeout_s", max(60.0, caps.get("est_latency_s", 60) * 5)))
    try:
        st = await prov.get_job_status(job.provider_job_id)
    except Exception as e:  # noqa: BLE001
        err = await prov.normalize_error(e)
        if err.code in ("PROVIDER_UNAVAILABLE", "RATE_LIMITED") and elapsed < timeout_s:
            return  # transient; keep polling
        await handle_error(db, p, s, job, att, err)
        return
    if st.state in ("queued", "running"):
        if elapsed > timeout_s:
            # reconcile BEFORE retrying: re-read status, then cancel so the provider cannot also finish a duplicate
            emit(db, p.id, "reconcile", f"scene {s.idx + 1}: {job.provider_id} job {job.provider_job_id} exceeded {timeout_s:.0f}s; reconciling", None, s.id)
            st2 = await prov.get_job_status(job.provider_job_id)
            if st2.state == "succeeded":
                st = st2
            else:
                try:
                    await prov.cancel_job(job.provider_job_id)
                except Exception:  # noqa: BLE001
                    pass
                await handle_error(db, p, s, job, att, ProviderError("GENERATION_TIMEOUT", f"no result after {elapsed:.0f}s; job cancelled at provider after reconciliation"), latency=int(elapsed * 1000))
                return
        else:
            return
    if st.state == "failed":
        await handle_error(db, p, s, job, att, ProviderError(st.error_code or "GENERATION_FAILED", st.detail or "provider reported failure"), latency=int(elapsed * 1000))
        return
    if st.state in ("cancelled", "unknown"):
        found = await prov.find_job_by_idempotency_key(job.idempotency_key)
        if found and found != job.provider_job_id:
            job.provider_job_id = found
            db.commit()
            return
        await handle_error(db, p, s, job, att, ProviderError("AMBIGUOUS_JOB_STATE", st.detail or f"provider reports {st.state}"), latency=int(elapsed * 1000))
        return
    # succeeded -> download
    set_project(db, p, "DOWNLOADING", f"scene {s.idx + 1}")
    d = _artifact_dir(p) / "clips"
    dest = d / f"{s.idx + 1:02d}_{job.id}.mp4"
    try:
        await prov.download_result(job.provider_job_id, dest)
    except Exception as e:  # noqa: BLE001
        n = int(job.request.get("download_failures", 0)) + 1
        job.request = {**job.request, "download_failures": n}
        db.commit()
        if n < 3:
            emit(db, p.id, "retry", f"download failed ({n}/3), retrying", None, s.id)
            return
        err = await prov.normalize_error(e)
        await handle_error(db, p, s, job, att, ProviderError("DOWNLOAD_FAILED", err.message))
        return
    actual = st.actual_cost if st.actual_cost is not None else job.reserved
    art = GenerationArtifact(user_id=p.user_id, project_id=p.id, scene_id=s.id, job_id=job.id, provider_id=job.provider_id, kind="clip", path=str(dest),
                             duration=job.requested_seconds, status="candidate", meta={"cost": actual, "latency_ms": int(elapsed * 1000)})
    db.add(art)
    if job.pool_id and job.reserved:
        ledger.consume(db, job.pool_id, job.reserved, actual, p.user_id, job.id, job.reserved)
        job.reserved = 0
    job.status = "SUCCEEDED"
    if att:
        att.status = "succeeded"
        att.latency_ms = int(elapsed * 1000)
        att.ended_at = utcnow()
    db.commit()
    emit(db, p.id, "clip_downloaded", f"scene {s.idx + 1} clip from {job.provider_id} ({actual} credits used)", {"artifact": art.id}, s.id)
    set_scene(db, s, "VALIDATING")
    await validate_and_decide(db, p, s, job, art, prov, caps)


async def validate_and_decide(db: Session, p: VideoProject, s: VideoScene, job: GenerationJob, art: GenerationArtifact, prov, caps: dict[str, Any]):
    set_project(db, p, "VALIDATING", f"scene {s.idx + 1}")
    ho = db.query(__import__("app.database.models", fromlist=["ContextHandoff"]).ContextHandoff).filter_by(project_id=p.id, scene_id=s.id).order_by(__import__("app.database.models", fromlist=["ContextHandoff"]).ContextHandoff.created_at.desc()).first()
    pkg = ho.package if ho else {}
    palette_cols = []
    for c in pkg.get("characters", []):
        pal = c.get("palette", {})
        palette_cols += [pal.get("body")] if pal.get("body") else []
    style_cols = [v for k, v in (pkg.get("style", {}).get("palette", {}) or {}).items() if k in ("sky",) and v]
    prev = pkg.get("continuity", {}).get("previous_frame")
    expected = {"width": p.width, "height": p.height, "duration": job.requested_seconds, "palette_colors": palette_cols, "style_colors": style_cols,
                "require_no_watermark": p.require_no_watermark}
    rep = await asyncio.to_thread(evaluate_clip, Path(art.path), expected=expected, previous_frame=Path(prev) if prev else None, provider_caps=caps,
                                  thresholds=(p.brief or {}).get("qa_thresholds"))
    db.add(QualityReport(artifact_id=art.id, scene_id=s.id, report=rep, decision=rep["decision"]))
    db.commit()
    emit(db, p.id, "qa", f"scene {s.idx + 1} QA {rep['decision']} (overall {rep.get('overall')})", {"reasons": rep.get("reasons")}, s.id)
    if rep["decision"] == "accepted":
        await accept_artifact(db, p, s, job, art)
        return
    art.status = "rejected"
    job.status = "DONE"
    s.retries += 1
    notes = list(s.corrective_notes or [])
    notes.append("Previous attempt rejected: " + "; ".join(rep.get("reasons", []))[:300])
    s.corrective_notes = notes
    s.last_error = f"{'QUALITY_VALIDATION_FAILED'}: " + "; ".join(rep.get("reasons", []))[:300]
    budget = int((p.brief or {}).get("max_qa_retries", 3))
    if s.retries >= budget:
        set_scene(db, s, "REVIEW", "QA retry budget used; manual review")
        set_project(db, p, "MANUAL_ACTION_REQUIRED", f"scene {s.idx + 1} failed QA {s.retries} times; review candidates")
    else:
        if s.retries >= 2:
            s.avoid_providers = list(set((s.avoid_providers or []) + [job.provider_id]))
        set_scene(db, s, "RETRYING", f"QA rejected; retry {s.retries}/{budget}")
    db.commit()


async def accept_artifact(db: Session, p: VideoProject, s: VideoScene, job: GenerationJob | None, art: GenerationArtifact):
    d = _artifact_dir(p) / "frames"
    first = await asyncio.to_thread(fx.extract_frame, art.path, d / f"{s.idx + 1:02d}_{art.id}_first.png", "first")
    last = await asyncio.to_thread(fx.extract_frame, art.path, d / f"{s.idx + 1:02d}_{art.id}_last.png", "last")
    meta = dict(art.meta or {})
    meta.update(first_frame=str(first), last_frame=str(last))
    try:
        for _f in (art.path, first, last):
            await asyncio.to_thread(persist_file, _f)
    except StorageBudgetError:
        art.status = "candidate"
        db.commit()
        set_project(db, p, "PAUSED", "STORAGE LIMIT: this clip was generated but cannot be stored durably, so it is not accepted. Delete old projects, then resume.")
        emit(db, p.id, "storage_limit", f"scene {s.idx + 1}: storage budget reached; clip kept as a candidate, not accepted", None, s.id)
        return
    # ending positions reported by the (mock) provider drive the next scene's start; real providers have none
    if job and job.provider_id.startswith("mock"):
        try:
            cred, prov = await _provider_for(db, job.credential_id)
            meta["end_positions"] = prov.end_positions(job.provider_job_id)
        except Exception:  # noqa: BLE001
            pass
    art.meta = meta
    art.status = "accepted"
    # an earlier accepted artifact for this scene (re-generation) is superseded, not deleted
    for old in db.query(GenerationArtifact).filter(GenerationArtifact.scene_id == s.id, GenerationArtifact.id != art.id, GenerationArtifact.status == "accepted").all():
        old.status = "rejected"
    s.accepted_artifact_id = art.id
    s.last_error = None
    s.not_before = None
    if job:
        job.status = "DONE"
    existing = db.query(MediaTimeline).filter_by(project_id=p.id, scene_id=s.id).first()
    if existing:
        existing.artifact_id, existing.duration, existing.start_time = art.id, s.duration, s.start_time
    else:
        db.add(MediaTimeline(project_id=p.id, scene_id=s.id, artifact_id=art.id, start_time=s.start_time, duration=s.duration))
    p.last_accepted_provider = art.provider_id
    p.last_accepted_frame = str(last)
    db.commit()
    set_scene(db, s, "ACCEPTED", f"clip from {art.provider_id}")
    recompute_accepted(db, p)
    checkpoint(db, p, f"scene_{s.idx + 1}_accepted")
    emit(db, p.id, "accepted", f"scene {s.idx + 1} accepted from {art.provider_id}; {p.accepted_duration:.1f}s of {p.target_duration:.1f}s accepted", None, s.id)
    set_project(db, p, "GENERATING", f"{p.accepted_duration:.1f}/{p.target_duration:.1f}s accepted")


async def advance(project_id: str):
    db = new_session()
    try:
        p = db.get(VideoProject, project_id)
        if p is None or p.status not in PROJECT_ACTIVE:
            return
        scenes = db.query(VideoScene).filter(VideoScene.project_id == p.id).order_by(VideoScene.idx).all()
        cur = next((s for s in scenes if s.status != "ACCEPTED"), None)
        if cur is None:
            await _start_compose(db, p)
            return
        if p.status == "READY":
            set_project(db, p, "GENERATING")
        job = (db.query(GenerationJob).filter(GenerationJob.scene_id == cur.id, GenerationJob.status.in_(["SUBMITTING", "SUBMITTED", "SUCCEEDED"]))
               .order_by(GenerationJob.created_at.desc()).first())
        if job is not None:
            if job.status == "SUBMITTED":
                await poll_job(db, p, cur, job)
            elif job.status == "SUCCEEDED":  # crashed between download and decision: re-validate
                art = db.query(GenerationArtifact).filter(GenerationArtifact.job_id == job.id).first()
                _, prov = await _provider_for(db, job.credential_id)
                await validate_and_decide(db, p, cur, job, art, prov, await prov.get_capabilities())
            elif job.status == "SUBMITTING":
                await _reconcile_submitting(db, p, cur, job)
            return
        if cur.status in ("MANUAL_ACTION_REQUIRED", "REVIEW"):
            return
        if not _due(cur):
            return
        room = headroom(p.user_id)
        if room is not None and room < int(os.environ.get("VGOH_STORAGE_RESERVE", str(12 * 1024 * 1024))):
            set_project(db, p, "PAUSED", "STORAGE LIMIT: not enough durable storage reserved for the next clip. Accepted clips are kept. Delete old projects, then resume.")
            emit(db, p.id, "storage_limit", "paused before generation: storage reserve unavailable", None, cur.id)
            return
        await start_generation(db, p, cur)
    finally:
        db.close()


async def _reconcile_submitting(db: Session, p: VideoProject, s: VideoScene, job: GenerationJob):
    """A job row was committed but the provider id never was (crash or ambiguous submit). Ask the provider by idempotency key."""
    cred, prov = await _provider_for(db, job.credential_id)
    found = await prov.find_job_by_idempotency_key(job.idempotency_key)
    if found:
        job.provider_job_id, job.status, job.submitted_at = found, "SUBMITTED", job.submitted_at or utcnow()
        db.commit()
        emit(db, p.id, "recovered", f"scene {s.idx + 1}: found provider job {found} by idempotency key; no duplicate submitted", None, s.id)
        return
    # provider has no record: safe to retry under the SAME key (idempotent), after releasing nothing (reservation stays)
    caps = await prov.get_capabilities()
    h = db.query(__import__("app.database.models", fromlist=["ContextHandoff"]).ContextHandoff).filter_by(project_id=p.id, scene_id=s.id).order_by(__import__("app.database.models", fromlist=["ContextHandoff"]).ContextHandoff.created_at.desc()).first()
    req = scene_request(db, p, s, s.attempt_counter)
    req.idempotency_key = job.idempotency_key
    req.duration = job.requested_seconds
    req.handoff = h.package if h else {}
    req.reference_images = reference_images(req.handoff) if caps.get("multi_reference") else []
    req.previous_frame = (req.handoff.get("continuity") or {}).get("previous_frame") if caps.get("first_frame") else None
    if not caps.get("idempotent_lookup", True) and caps.get("manual") is None and not hasattr(prov, "end_positions"):
        pass
    try:
        job.provider_job_id = await prov.submit_generation(req)
        job.status, job.submitted_at = "SUBMITTED", utcnow()
        db.commit()
    except Exception as e:  # noqa: BLE001
        att = db.query(GenerationAttempt).filter(GenerationAttempt.job_id == job.id).first()
        await handle_error(db, p, s, job, att, await prov.normalize_error(e))


async def _start_compose(db: Session, p: VideoProject):
    exp = db.query(ExportJob).filter(ExportJob.project_id == p.id, ExportJob.options["auto"].as_boolean() == True).first() if False else None  # noqa: E712
    exports = db.query(ExportJob).filter(ExportJob.project_id == p.id).all()
    if any((e.options or {}).get("auto") and e.status in ("QUEUED", "RUNNING", "DONE") for e in exports) and p.status in ("COMPOSING", "FINAL_VALIDATION"):
        return
    if p.status not in ("COMPOSING", "FINAL_VALIDATION"):
        set_project(db, p, "COMPOSING", "all scenes accepted")
    if not any((e.options or {}).get("auto") and e.status in ("QUEUED", "RUNNING") for e in exports):
        db.add(ExportJob(user_id=p.user_id, project_id=p.id, status="QUEUED", options={"auto": True, "burn_subtitles": bool((p.brief or {}).get("burn_subtitles", False)),
                                                                                   "transition": (p.brief or {}).get("transition", "cut")}))
        db.commit()


def ensure_narration(db: Session, p: VideoProject, scenes: list[VideoScene]):
    if db.query(NarrationSegment).filter_by(project_id=p.id).count() == 0:
        for seg in segments_from_scenes(scenes):
            db.add(NarrationSegment(project_id=p.id, **seg))
        db.commit()


async def process_export(export_id: str):
    db = new_session()
    try:
        ex = db.get(ExportJob, export_id)
        p = db.get(VideoProject, ex.project_id)
        ex.status = "RUNNING"
        db.commit()
        scenes = db.query(VideoScene).filter(VideoScene.project_id == p.id).order_by(VideoScene.idx).all()
        if any(s.status != "ACCEPTED" for s in scenes):
            ex.status, ex.error = "FAILED", "not all scenes are accepted"
            db.commit()
            return
        if (ex.options or {}).get("auto"):
            set_project(db, p, "COMPOSING", "FFmpeg composition")
        ensure_narration(db, p, scenes)
        segs = db.query(NarrationSegment).filter_by(project_id=p.id).order_by(NarrationSegment.idx).all()
        audio = db.query(AudioTrack).filter_by(project_id=p.id, kind="master_narration").order_by(AudioTrack.created_at.desc()).first()
        out_dir = _artifact_dir(p) / "exports" / ex.id
        out_dir.mkdir(parents=True, exist_ok=True)
        srt = out_dir / "subtitles.srt"
        srt.write_text(to_srt(segs))
        clips = []
        for s in scenes:
            a = db.get(GenerationArtifact, s.accepted_artifact_id)
            restore_file(a.path)
            clips.append({"path": a.path, "duration": s.duration})
        mp4 = out_dir / "final.mp4"
        if audio:
            restore_file(audio.path)
        opts = ex.options or {}
        cres = await asyncio.to_thread(compose, clips=clips, width=p.width, height=p.height, fps=p.fps, out_path=mp4, audio_path=Path(audio.path) if audio else None,
                                       srt_path=srt, burn_subtitles=bool(opts.get("burn_subtitles")), transition=opts.get("transition", "cut"), workdir=out_dir / "work")
        if (opts.get("auto")):
            set_project(db, p, "FINAL_VALIDATION")
        val = await asyncio.to_thread(validate_output, mp4, width=p.width, height=p.height, fps=p.fps, target_duration=p.target_duration, expect_audio=audio is not None)
        report = build_report(db, p, scenes, cres, val, audio)
        (out_dir / "report.json").write_text(json.dumps(report, indent=2, default=str))
        sb = [{"idx": s.idx, "title": s.title, "description": s.description, "prompt": s.prompt, "duration": s.duration, "start": s.start_time, "camera": s.camera,
               "narration": s.narration, "characters": s.characters} for s in scenes]
        (out_dir / "storyboard.json").write_text(json.dumps(sb, indent=2))
        for _f in (mp4, srt, out_dir / "report.json", out_dir / "storyboard.json"):
            await asyncio.to_thread(persist_file, _f)
        ex.files = {"video": str(mp4), "subtitles": str(srt), "report": str(out_dir / "report.json"), "storyboard": str(out_dir / "storyboard.json")}
        ex.report = {"validation": val, "notes": cres["notes"]}
        if val["ok"]:
            ex.status = "DONE"
            if opts.get("auto") or p.status in ("COMPOSING", "FINAL_VALIDATION"):
                set_project(db, p, "COMPLETED", f"final video {val['duration_s']}s validated")
            emit(db, p.id, "export", f"final video exported and validated ({val['frames']} frames, {val['duration_s']}s)", val)
        else:
            ex.status, ex.error = "FAILED", "output validation failed: " + json.dumps(val["checks"])
            set_project(db, p, "FAILED", "final validation failed")
        db.commit()
    except Exception as e:  # noqa: BLE001
        db.rollback()
        ex = db.get(ExportJob, export_id)
        ex.status, ex.error = "FAILED", str(e)[:500]
        p = db.get(VideoProject, ex.project_id)
        if (ex.options or {}).get("auto"):
            set_project(db, p, "FAILED", f"composition failed: {str(e)[:200]}")
        db.commit()
    finally:
        db.close()


def build_report(db: Session, p: VideoProject, scenes: list[VideoScene], cres: dict, val: dict, audio) -> dict[str, Any]:
    jobs = db.query(GenerationJob).filter(GenerationJob.project_id == p.id).order_by(GenerationJob.created_at).all()
    scene_rows = []
    for s in scenes:
        a = db.get(GenerationArtifact, s.accepted_artifact_id)
        sj = [j for j in jobs if j.scene_id == s.id]
        scene_rows.append({"scene": s.idx + 1, "duration": s.duration, "accepted_provider": a.provider_id if a else None, "attempts": len(sj),
                           "providers_tried": [j.provider_id for j in sj], "errors": [j.error_code for j in sj if j.error_code]})
    regenerated = 0
    for s in scenes:
        accepted_arts = db.query(GenerationArtifact).filter(GenerationArtifact.scene_id == s.id, GenerationArtifact.kind == "clip").all()
        if len([x for x in accepted_arts if (x.meta or {}).get("was_accepted")]) > 1:
            regenerated += 1
    spent = db.query(__import__("app.database.models", fromlist=["CreditTransaction"]).CreditTransaction).filter_by(user_id=p.user_id, kind="consume").all()
    proj_job_ids = {j.id for j in jobs}
    cost = sum(t.amount for t in spent if t.job_id in proj_job_ids)
    handoffs = db.query(__import__("app.database.models", fromlist=["ContextHandoff"]).ContextHandoff).filter_by(project_id=p.id).order_by(__import__("app.database.models", fromlist=["ContextHandoff"]).ContextHandoff.created_at).all()
    events = db.query(WorkflowEvent).filter_by(project_id=p.id).order_by(WorkflowEvent.id).all()
    return {"project": canonical_state(db, p), "scenes": scene_rows, "providers_used": sorted({r["accepted_provider"] for r in scene_rows if r["accepted_provider"]}),
            "provider_switches": [{"scene": db.get(VideoScene, h.scene_id).idx + 1, "from": h.from_provider, "to": h.to_provider, "reason": h.reason} for h in handoffs if h.from_provider != h.to_provider and h.from_provider],
            "total_jobs": len(jobs), "credits_consumed_by_unit": cost, "paid_spend": 0 if p.paid_budget == 0 else "see ledger",
            "accepted_scenes_regenerated_unnecessarily": regenerated, "composition_notes": cres["notes"], "output_validation": val,
            "narration": {"present": audio is not None, "alignment": "even_split unless user supplied timestamps (not forced alignment)"},
            "events_count": len(events), "recoveries": [e.message for e in events if e.kind == "recovered"], "generated_at": iso()}


def recover():
    """Run once at startup: reconcile work that was in flight when the process died."""
    db = new_session()
    try:
        n = 0
        for ex in db.query(ExportJob).filter(ExportJob.status == "RUNNING").all():
            ex.status = "QUEUED"
            n += 1
        for job in db.query(GenerationJob).filter(GenerationJob.status == "SUBMITTING").all():
            emit(db, job.project_id, "recovered", f"backend restarted while submitting job {job.id}; reconciling with the provider by idempotency key", None, job.scene_id)
            n += 1
        for p in db.query(VideoProject).filter(VideoProject.status.in_(PROJECT_ACTIVE)).all():
            emit(db, p.id, "recovered", f"backend restarted; resuming from durable state ({p.accepted_duration:.1f}/{p.target_duration:.1f}s accepted)")
            n += 1
        db.commit()
        return n
    finally:
        db.close()


async def tick():
    db = new_session()
    try:
        pids = [r[0] for r in db.query(VideoProject.id).filter(VideoProject.status.in_(PROJECT_ACTIVE)).all()]
        eids = [r[0] for r in db.query(ExportJob.id).filter(ExportJob.status == "QUEUED").all()]
    finally:
        db.close()
    for pid in pids:
        try:
            await advance(pid)
        except Exception as e:  # noqa: BLE001
            d2 = new_session()
            try:
                emit(d2, pid, "engine_error", f"engine error: {type(e).__name__}: {str(e)[:300]}")
            finally:
                d2.close()
    for eid in eids:
        await process_export(eid)
