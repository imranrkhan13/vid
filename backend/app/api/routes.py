from __future__ import annotations
import asyncio
import json
import shutil
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.audio.align import segments_from_scenes, to_srt
from app.auth import auth as A
from app.core.config import settings
from app.core.security import encrypt_secret, redact
from app.credits import ledger
from app.database.models import (AudioTrack, Character, CharacterReferenceAsset, ContextHandoff, ExportJob, GenerationArtifact, GenerationJob,
                                 GenerationProvider, MediaTimeline, NarrationSegment, ProjectCharacter, ProviderCredential, QualityReport,
                                 QuotaPool, SceneVersion, Storyboard, StyleProfile, User, VideoProject, VideoScene, WorkflowEvent, ProjectVersion, AuditLog)
from app.directors import director as D
from app.media import ffmpeg as fx
from app.providers.base import ProviderError
from app.providers.registry import build_provider, context_for
from app.storage.local import project_dir, safe_name, persist_file as _persist, restore_file, StorageBudgetError
from app.workflows import engine as E

router = APIRouter(prefix="/api/v1")
ASPECTS = {"9:16": (1080, 1920), "16:9": (1920, 1080), "1:1": (1080, 1080)}
RES_720 = {"9:16": (720, 1280), "16:9": (1280, 720), "1:1": (720, 720)}


def audit(db, user, action, detail=None, req: Request | None = None):
    db.add(AuditLog(user_id=user.id if user else None, action=action, detail=(detail or "")[:400], ip=A.client_ip(req) if req else None))
    db.commit()


# ------------------------------------------------------------------ auth
class Cred(BaseModel):
    email: str
    password: str = Field(min_length=1, max_length=200)
    name: str | None = None


def _set_cookie(resp: Response, tok: str, req: Request):
    secure = settings.cookie_secure or req.headers.get("x-forwarded-proto") == "https"
    resp.set_cookie(A.COOKIE, tok, httponly=True, samesite="lax", secure=secure, max_age=14 * 86400, path="/")


@router.post("/auth/register")
def register(body: Cred, req: Request, resp: Response, db: Session = Depends(A.get_db)):
    A.csrf_guard(req)
    if not settings.registration_open:
        raise HTTPException(403, "registration is closed")
    A.rate_limit("reg:" + A.client_ip(req), 20, 3600)
    u = A.register_user(db, body.email, body.password, body.name)
    _set_cookie(resp, A.create_session(db, u), req)
    audit(db, u, "register", None, req)
    return {"id": u.id, "email": u.email, "name": u.name}


@router.post("/auth/login")
def login(body: Cred, req: Request, resp: Response, db: Session = Depends(A.get_db)):
    A.csrf_guard(req)
    A.rate_limit("login-ip:" + A.client_ip(req), 30, 600)
    A.rate_limit("login-email:" + body.email.lower(), 10, 600)
    u = A.login_user(db, body.email, body.password)
    _set_cookie(resp, A.create_session(db, u), req)
    audit(db, u, "login", None, req)
    return {"id": u.id, "email": u.email, "name": u.name}


@router.post("/auth/logout")
def logout(req: Request, resp: Response, db: Session = Depends(A.get_db)):
    from app.core.security import sha256
    from app.database.models import UserSession
    tok = req.cookies.get(A.COOKIE)
    if tok:
        s = db.get(UserSession, sha256(tok))
        if s:
            db.delete(s)
            db.commit()
    resp.delete_cookie(A.COOKIE, path="/")
    return {"ok": True}


@router.get("/auth/me")
def me(user: User = Depends(A.current_user)):
    return {"id": user.id, "email": user.email, "name": user.name}


# ------------------------------------------------------------------ helpers
def project_of(db: Session, user: User, pid: str) -> VideoProject:
    return A.owned(db, VideoProject, pid, user)


def scene_of(db: Session, user: User, sid: str) -> tuple[VideoScene, VideoProject]:
    s = db.get(VideoScene, sid)
    if s is None:
        raise HTTPException(404, "not found")
    return s, project_of(db, user, s.project_id)


def ser_scene(s: VideoScene) -> dict[str, Any]:
    return {c.name: getattr(s, c.name) for c in s.__table__.columns if c.name not in ("updated_at",)}


def persist_file(dest):
    try:
        _persist(dest)
    except StorageBudgetError:
        Path(dest).unlink(missing_ok=True)
        raise HTTPException(413, "storage budget reached; delete old projects or media first")


def public_state(cs: dict[str, Any]) -> dict[str, Any]:
    """Never expose server filesystem paths to the browser."""
    cs = dict(cs)
    if cs.get("last_accepted_frame"):
        cs["last_accepted_frame"] = "available"
    return cs


def ser_project(db: Session, p: VideoProject) -> dict[str, Any]:
    d = {c.name: getattr(p, c.name) for c in p.__table__.columns if c.name not in ("last_accepted_frame",)}
    d["canonical_state"] = public_state(E.canonical_state(db, p))
    return d


async def read_upload(req: Request, dest: Path, limit: int | None = None) -> int:
    limit = limit or settings.max_upload_bytes
    dest.parent.mkdir(parents=True, exist_ok=True)
    total = 0
    with open(dest, "wb") as f:
        async for chunk in req.stream():
            total += len(chunk)
            if total > limit:
                f.close()
                dest.unlink(missing_ok=True)
                raise HTTPException(413, "upload too large")
            f.write(chunk)
    if total == 0:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, "empty upload")
    return total


# ------------------------------------------------------------------ characters
class CharacterIn(BaseModel):
    name: str = Field(min_length=1, max_length=100)
    appearance: str = ""
    clothing: str = ""
    proportions: str = ""
    personality: str = ""
    palette: dict[str, str] | None = None


@router.post("/characters")
def create_character(body: CharacterIn, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    pal = body.palette or D.character_palette(body.name, body.appearance + " " + body.clothing)
    c = Character(user_id=user.id, name=body.name, profile={**body.model_dump(exclude={"name", "palette"}), "palette": pal, "scale": 1.0})
    db.add(c)
    db.commit()
    return ser_char(db, c)


def ser_char(db, c):
    refs = db.query(CharacterReferenceAsset).filter_by(character_id=c.id).all()
    return {"id": c.id, "name": c.name, "profile": c.profile, "version": c.version, "references": [{"id": r.id, "kind": r.kind} for r in refs]}


@router.get("/characters")
def list_characters(user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    return [ser_char(db, c) for c in db.query(Character).filter_by(user_id=user.id).order_by(Character.created_at).all()]


@router.patch("/characters/{cid}")
def update_character(cid: str, body: dict[str, Any], user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    c = A.owned(db, Character, cid, user)
    prof = dict(c.profile)
    for k in ("appearance", "clothing", "proportions", "personality", "palette", "expressions"):
        if k in body:
            prof[k] = body[k]
    c.profile = prof
    if "name" in body:
        c.name = str(body["name"])[:100]
    c.version += 1
    db.commit()
    return ser_char(db, c)


@router.delete("/characters/{cid}")
def delete_character(cid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    c = A.owned(db, Character, cid, user)
    db.query(ProjectCharacter).filter_by(character_id=c.id).delete()
    db.delete(c)
    db.commit()
    return {"ok": True}


@router.put("/characters/{cid}/references")
async def upload_reference(cid: str, req: Request, kind: str = "front", filename: str = "ref.png", user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    c = A.owned(db, Character, cid, user)
    fn = safe_name(filename)
    if Path(fn).suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        raise HTTPException(415, "reference must be png, jpg or webp")
    dest = project_dir(user.id, "characters") / f"{c.id}_{kind}_{fn}"
    await read_upload(req, dest, 15 * 1024 * 1024)
    persist_file(dest)
    try:
        import cv2
        if cv2.imread(str(dest)) is None:
            dest.unlink(missing_ok=True)
            raise HTTPException(415, "not a readable image")
    except ImportError:
        pass
    a = CharacterReferenceAsset(character_id=c.id, kind=safe_name(kind, "front"), path=str(dest))
    db.add(a)
    db.commit()
    return {"id": a.id, "kind": a.kind}


# ------------------------------------------------------------------ projects
class ProjectIn(BaseModel):
    title: str = Field(min_length=1, max_length=200)
    concept: str = ""
    script: str = ""
    narration: str = ""
    target_duration: float = Field(gt=1, le=600)
    aspect_ratio: str = "9:16"
    resolution: str = "1080p"       # 720p | 1080p
    fps: int = Field(30, ge=12, le=60)
    visual_style: str = "soft colorful 3D educational animation"
    audience: str = ""
    project_type: str = ""
    camera_notes: str = ""
    character_ids: list[str] = []
    scene_count: int | None = Field(None, ge=1, le=60)
    mode: str = "free_only"
    paid_budget: float = Field(0, ge=0)
    require_no_watermark: bool = False
    require_commercial: bool = False
    require_storyboard_approval: bool = True
    burn_subtitles: bool = False
    transition: str = "cut"


@router.post("/projects")
def create_project(body: ProjectIn, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    if body.aspect_ratio not in ASPECTS:
        raise HTTPException(422, "aspect_ratio must be 9:16, 16:9 or 1:1")
    if body.mode not in ("free_only", "open_source_only", "lowest_cost", "balanced", "max_quality"):
        raise HTTPException(422, "unknown mode")
    import os
    if db.query(VideoProject).filter(VideoProject.user_id == user.id).count() >= int(os.environ.get("VGOH_MAX_PROJECTS", "12")):
        raise HTTPException(429, "project limit reached for this account; delete an old project first")
    w, h = (RES_720 if body.resolution == "720p" else ASPECTS)[body.aspect_ratio]
    chars = []
    for cid in body.character_ids:
        chars.append(A.owned(db, Character, cid, user))
    brief = body.model_dump(exclude={"character_ids"})
    p = VideoProject(user_id=user.id, title=body.title, brief=brief, status="DRAFT", target_duration=body.target_duration, aspect_ratio=body.aspect_ratio,
                     width=w, height=h, fps=body.fps, mode=body.mode, paid_budget=body.paid_budget, require_no_watermark=body.require_no_watermark,
                     require_commercial=body.require_commercial, require_storyboard_approval=body.require_storyboard_approval)
    db.add(p)
    db.commit()
    for c in chars:
        db.add(ProjectCharacter(project_id=p.id, character_id=c.id))
    db.add(ProjectVersion(project_id=p.id, version=1, kind="instruction", data=brief))
    db.commit()
    E.emit(db, p.id, "created", f"project created: {p.title}")
    return ser_project(db, p)


@router.get("/projects")
def list_projects(user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    ps = db.query(VideoProject).filter_by(user_id=user.id).order_by(VideoProject.created_at.desc()).all()
    return [{**{k: v for k, v in ser_project(db, p).items() if k in ("id", "title", "status", "status_detail", "target_duration", "accepted_duration", "aspect_ratio", "created_at", "mode")}} for p in ps]


@router.get("/projects/{pid}")
def get_project(pid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    d = ser_project(db, p)
    d["scenes"] = [ser_scene(s) for s in db.query(VideoScene).filter_by(project_id=p.id).order_by(VideoScene.idx).all()]
    d["characters"] = [ser_char(db, c) for c in db.query(Character).join(ProjectCharacter, ProjectCharacter.character_id == Character.id).filter(ProjectCharacter.project_id == p.id).order_by(Character.created_at, Character.id).all()]
    exps = db.query(ExportJob).filter_by(project_id=p.id).order_by(ExportJob.created_at.desc()).all()
    d["exports"] = [{"id": e.id, "status": e.status, "error": e.error, "created_at": e.created_at, "files": list((e.files or {}).keys()), "report": e.report} for e in exps]
    return d


@router.patch("/projects/{pid}")
def patch_project(pid: str, body: dict[str, Any], user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    if p.status in ("GENERATING", "COMPOSING", "FINAL_VALIDATION"):
        raise HTTPException(409, "pause the project before editing it")
    allowed = {"title", "mode", "paid_budget", "require_no_watermark", "require_commercial", "require_storyboard_approval"}
    for k, v in body.items():
        if k in allowed:
            if k == "mode" and v not in ("free_only", "open_source_only", "lowest_cost", "balanced", "max_quality"):
                raise HTTPException(422, "unknown mode")
            if k == "paid_budget" and (not isinstance(v, (int, float)) or v < 0):
                raise HTTPException(422, "paid_budget must be >= 0")
            setattr(p, k, v)
    if "weights" in body:
        w = body["weights"]
        if w is not None and (not isinstance(w, dict) or set(w) - set(["consistency", "quality", "free_credits", "cost", "reliability", "speed", "resolution"]) or any(not isinstance(x, (int, float)) or x < 0 for x in w.values())):
            raise HTTPException(422, "invalid weights")
        p.weights = w
    db.commit()
    return ser_project(db, p)


@router.delete("/projects/{pid}")
def delete_project(pid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    if p.status in ("GENERATING", "COMPOSING"):
        raise HTTPException(409, "cancel the project first")
    from app.storage.local import purge_tree
    purge_tree(project_dir(user.id, p.id))
    shutil.rmtree(project_dir(user.id, p.id), ignore_errors=True)
    db.query(ProjectCharacter).filter_by(project_id=p.id).delete()
    for m in (WorkflowEvent,):
        db.query(m).filter_by(project_id=p.id).delete()
    db.delete(p)
    db.commit()
    return {"ok": True}


@router.post("/projects/{pid}/plan")
async def plan_project(pid: str, body: dict[str, Any] | None = None, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    if p.status not in ("DRAFT", "PLANNING", "STORYBOARD_READY", "AWAITING_USER_APPROVAL", "READY", "PAUSED"):
        raise HTTPException(409, f"cannot re-plan while {p.status}")
    if db.query(VideoScene).filter(VideoScene.project_id == p.id, VideoScene.status == "ACCEPTED").count():
        raise HTTPException(409, "accepted scenes exist; edit or regenerate individual scenes instead of re-planning")
    E.set_project(db, p, "PLANNING")
    chars = [{"name": c.name, "profile": c.profile} for c in db.query(Character).join(ProjectCharacter, ProjectCharacter.character_id == Character.id).filter(ProjectCharacter.project_id == p.id).order_by(Character.created_at, Character.id).all()]
    brief = dict(p.brief)
    brief["target_duration"] = p.target_duration
    plan = None
    llm_note = None
    cfg = (body or {}).get("llm")
    if cfg:
        cred = db.query(ProviderCredential).filter_by(user_id=user.id, provider_id="llm_openai_compat").first()
        try:
            from app.core.security import decrypt_secret
            key = decrypt_secret(cred.secret_enc, f"{cred.user_id}|{cred.provider_id}|{cred.id}") if cred and cred.secret_enc else None
            plan = await D.plan_with_llm({**(cred.config if cred else {}), **{k: v for k, v in cfg.items() if k in ("model",)}}, key, brief, chars) if cred else None
        except Exception as e:  # noqa: BLE001
            llm_note = f"LLM planner failed, used heuristic planner: {str(e)[:200]}"
    if plan is None:
        plan = D.plan_heuristic(brief, chars)
        if llm_note:
            plan["director_note"] = llm_note
    D.validate_plan(plan, p.target_duration)
    old = db.query(VideoScene).filter_by(project_id=p.id).all()
    for s in old:
        db.delete(s)
    db.commit()
    for sc in plan["scenes"]:
        db.add(VideoScene(project_id=p.id, idx=sc["idx"], title=sc["title"][:300], description=sc["description"], prompt=sc["prompt"], negative_prompt=sc.get("negative_prompt"),
                          camera=sc.get("camera"), motion=sc.get("motion"), lighting=sc.get("lighting"), environment=sc.get("environment"), emotion=sc.get("emotion"),
                          narration=sc.get("narration"), characters=sc.get("characters", []), acceptance=sc.get("acceptance"), duration=float(sc["duration"]), start_time=float(sc["start_time"])))
    ver = (db.query(Storyboard).filter_by(project_id=p.id).count()) + 1
    db.add(Storyboard(project_id=p.id, version=ver, data=plan, director=plan["director"]))
    sv = p.style_version + 1 if db.get(StyleProfile, (p.id, p.style_version)) else p.style_version
    p.style_version = sv
    db.add(StyleProfile(project_id=p.id, version=sv, data=plan["style_bible"]))
    db.commit()
    E.emit(db, p.id, "planned", f"storyboard v{ver} by {plan['director']}: {len(plan['scenes'])} scenes totalling {plan['total_seconds']}s")
    E.set_project(db, p, "AWAITING_USER_APPROVAL" if p.require_storyboard_approval else "STORYBOARD_READY")
    return get_project(pid, user, db)


@router.post("/projects/{pid}/approve")
def approve_storyboard(pid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    if p.status not in ("AWAITING_USER_APPROVAL", "STORYBOARD_READY"):
        raise HTTPException(409, f"nothing to approve while {p.status}")
    scenes = db.query(VideoScene).filter_by(project_id=p.id).all()
    total = sum(s.duration for s in scenes)
    if abs(total - p.target_duration) > 1e-3:
        raise HTTPException(422, f"scene durations add to {total}s but the target is {p.target_duration}s; adjust the scenes")
    db.add(ProjectVersion(project_id=p.id, version=db.query(ProjectVersion).filter_by(project_id=p.id).count() + 1, kind="approval", data={"storyboard_approved": True}))
    E.set_project(db, p, "READY", "storyboard approved")
    return ser_project(db, p)


@router.post("/projects/{pid}/generate")
def generate(pid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    if p.status == "AWAITING_USER_APPROVAL":
        raise HTTPException(409, "approve the storyboard first")
    if p.status in ("DRAFT", "PLANNING"):
        raise HTTPException(409, "plan the project first")
    if p.status in ("STORYBOARD_READY",):
        E.set_project(db, p, "READY")
    if p.status in ("READY", "FAILED", "WAITING_FOR_CREDITS", "WAITING_FOR_PROVIDER"):
        E.set_project(db, p, "GENERATING", "started by user")
    elif p.status not in E.PROJECT_ACTIVE:
        raise HTTPException(409, f"cannot start while {p.status}")
    for s in db.query(VideoScene).filter_by(project_id=p.id).all():
        s.not_before = None
    db.commit()
    return ser_project(db, p)


@router.post("/projects/{pid}/pause")
def pause(pid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    if p.status not in E.PROJECT_ACTIVE:
        raise HTTPException(409, f"cannot pause while {p.status}")
    p.paused_from = p.status
    E.set_project(db, p, "PAUSED", "paused by user; in-flight provider jobs keep running and are collected on resume")
    return ser_project(db, p)


@router.post("/projects/{pid}/resume")
def resume(pid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    if p.status != "PAUSED":
        raise HTTPException(409, "project is not paused")
    for s in db.query(VideoScene).filter_by(project_id=p.id).all():
        s.not_before = None
    E.set_project(db, p, "GENERATING", "resumed")
    return ser_project(db, p)


@router.post("/projects/{pid}/cancel")
async def cancel(pid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    if p.status in ("COMPLETED", "CANCELLED"):
        raise HTTPException(409, f"already {p.status}")
    for job in db.query(GenerationJob).filter(GenerationJob.project_id == p.id, GenerationJob.status.in_(["SUBMITTING", "SUBMITTED", "MANUAL"])).all():
        try:
            cred = db.get(ProviderCredential, job.credential_id)
            if job.provider_job_id and cred:
                await build_provider(cred.provider_id, context_for(cred)).cancel_job(job.provider_job_id)
        except Exception:  # noqa: BLE001
            pass
        if job.pool_id and job.reserved:
            ledger.release(db, job.pool_id, job.reserved, user.id, job.id, "released: project cancelled")
            job.reserved = 0
        job.status = "CANCELLED"
    db.commit()
    E.set_project(db, p, "CANCELLED", "cancelled by user; accepted clips are kept")
    return ser_project(db, p)


@router.get("/projects/{pid}/state")
def canonical(pid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    return public_state(E.canonical_state(db, project_of(db, user, pid)))


@router.get("/projects/{pid}/scenes")
def scenes(pid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    out = []
    for s in db.query(VideoScene).filter_by(project_id=p.id).order_by(VideoScene.idx).all():
        d = ser_scene(s)
        arts = db.query(GenerationArtifact).filter_by(scene_id=s.id, kind="clip").order_by(GenerationArtifact.created_at).all()
        d["candidates"] = []
        for a in arts:
            qr = db.query(QualityReport).filter_by(artifact_id=a.id).order_by(QualityReport.created_at.desc()).first()
            d["candidates"].append({"id": a.id, "provider": a.provider_id, "status": a.status, "duration": a.duration, "quality": qr.report if qr else None, "created_at": a.created_at})
        out.append(d)
    return out


@router.get("/projects/{pid}/timeline")
def timeline(pid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    scs = db.query(VideoScene).filter_by(project_id=p.id).order_by(VideoScene.idx).all()
    rows = []
    for s in scs:
        a = db.get(GenerationArtifact, s.accepted_artifact_id) if s.accepted_artifact_id else None
        qr = db.query(QualityReport).filter_by(artifact_id=a.id).order_by(QualityReport.created_at.desc()).first() if a else None
        rows.append({"scene_id": s.id, "idx": s.idx, "title": s.title, "start": s.start_time, "duration": s.duration, "status": s.status, "provider": a.provider_id if a else None,
                     "artifact_id": a.id if a else None, "overall": qr.report.get("overall") if qr else None})
    return {"total": p.target_duration, "accepted": p.accepted_duration, "fps": p.fps, "scenes": rows}


@router.get("/projects/{pid}/handoffs")
def handoffs(pid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    hs = db.query(ContextHandoff).filter_by(project_id=p.id).order_by(ContextHandoff.created_at).all()
    return [{"id": h.id, "scene_id": h.scene_id, "reason": h.reason, "from": h.from_provider, "to": h.to_provider, "created_at": h.created_at, "package": redact(h.package)} for h in hs]


@router.get("/projects/{pid}/events")
async def events(pid: str, req: Request, after: int = 0, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    pid_ = p.id
    wants_stream = "text/event-stream" in req.headers.get("accept", "")
    if not wants_stream:
        rows = db.query(WorkflowEvent).filter(WorkflowEvent.project_id == pid_, WorkflowEvent.id > after).order_by(WorkflowEvent.id).limit(500).all()
        return [{"id": e.id, "kind": e.kind, "message": e.message, "data": e.data, "scene_id": e.scene_id, "at": e.created_at} for e in rows]

    async def gen():
        last = after
        idle = 0
        while not await req.is_disconnected():
            from app.database.session import new_session
            s2 = new_session()
            try:
                rows = s2.query(WorkflowEvent).filter(WorkflowEvent.project_id == pid_, WorkflowEvent.id > last).order_by(WorkflowEvent.id).limit(100).all()
                for e in rows:
                    last = e.id
                    yield f"id: {e.id}\ndata: {json.dumps({'id': e.id, 'kind': e.kind, 'message': e.message, 'scene_id': e.scene_id, 'at': e.created_at.isoformat()})}\n\n"
                idle = 0 if rows else idle + 1
            finally:
                s2.close()
            if idle and idle % 20 == 0:
                yield ": keepalive\n\n"
            await asyncio.sleep(0.7)
    return StreamingResponse(gen(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})


# ------------------------------------------------------------------ scenes
class SceneEdit(BaseModel):
    title: str | None = None
    description: str | None = None
    prompt: str | None = None
    negative_prompt: str | None = None
    camera: str | None = None
    motion: str | None = None
    lighting: str | None = None
    narration: str | None = None
    duration: float | None = Field(None, gt=0.5, le=60)


@router.patch("/scenes/{sid}")
def edit_scene(sid: str, body: SceneEdit, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    s, p = scene_of(db, user, sid)
    if s.status == "GENERATING":
        raise HTTPException(409, "scene is generating")
    db.add(SceneVersion(scene_id=s.id, version=s.scene_version, data=ser_scene(s) | {"created_at": None}))
    for k, v in body.model_dump(exclude_none=True).items():
        setattr(s, k, v)
    s.scene_version += 1
    if body.duration is not None:
        t = 0.0
        for x in db.query(VideoScene).filter_by(project_id=p.id).order_by(VideoScene.idx).all():
            x.start_time = t
            t += x.duration
    if s.status == "ACCEPTED" and (body.prompt or body.description or body.duration):
        s.status, s.accepted_artifact_id = "PENDING", None
        E.recompute_accepted(db, p)
    db.commit()
    return ser_scene(s)


@router.post("/scenes/{sid}/regenerate")
def regenerate(sid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    s, p = scene_of(db, user, sid)
    if s.status == "GENERATING":
        raise HTTPException(409, "already generating")
    old = s.status
    s.status, s.retries, s.not_before, s.avoid_providers, s.last_error = "PENDING", 0, None, [], None
    s.scene_version += 1
    E.emit(db, p.id, "regenerate", f"scene {s.idx + 1} regeneration requested by user (was {old}); previous accepted clip stays until replaced", None, s.id)
    db.commit()
    E.recompute_accepted(db, p)
    if p.status in ("COMPLETED", "MANUAL_ACTION_REQUIRED", "FAILED", "PAUSED", "WAITING_FOR_CREDITS", "WAITING_FOR_PROVIDER", "READY"):
        E.set_project(db, p, "GENERATING", "regeneration requested")
    return ser_scene(s)


@router.post("/scenes/{sid}/approve")
async def approve_scene(sid: str, artifact_id: str | None = None, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    s, p = scene_of(db, user, sid)
    q = db.query(GenerationArtifact).filter_by(scene_id=s.id, kind="clip")
    if artifact_id:
        q = q.filter_by(id=artifact_id)
    art = q.order_by(GenerationArtifact.created_at.desc()).first()
    if not art or not Path(art.path).exists():
        raise HTTPException(404, "no candidate clip to approve")
    job = db.get(GenerationJob, art.job_id) if art.job_id else None
    meta = dict(art.meta or {})
    meta["was_accepted"] = True
    meta["approved_by_user"] = True
    art.meta = meta
    await E.accept_artifact(db, p, s, job, art)
    return ser_scene(s)


@router.post("/scenes/{sid}/reject")
def reject_scene(sid: str, body: dict[str, Any] | None = None, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    s, p = scene_of(db, user, sid)
    for a in db.query(GenerationArtifact).filter_by(scene_id=s.id, kind="clip", status="candidate").all():
        a.status = "rejected"
    note = (body or {}).get("note")
    if note:
        s.corrective_notes = list(s.corrective_notes or []) + [f"User rejected: {str(note)[:300]}"]
    s.status, s.retries, s.not_before = "PENDING", 0, None
    s.accepted_artifact_id = None if s.status == "ACCEPTED" else s.accepted_artifact_id
    db.commit()
    E.recompute_accepted(db, p)
    if p.status in ("MANUAL_ACTION_REQUIRED", "COMPLETED", "PAUSED", "FAILED"):
        E.set_project(db, p, "GENERATING", "scene rejected by user")
    return ser_scene(s)


@router.put("/scenes/{sid}/import")
async def import_clip(sid: str, req: Request, filename: str = "clip.mp4", user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    """Manual-assisted provider: import a clip generated elsewhere. It goes through the same QA as any provider output."""
    s, p = scene_of(db, user, sid)
    if s.status not in ("MANUAL_ACTION_REQUIRED", "REVIEW", "PENDING", "RETRYING", "WAITING_FOR_CREDITS", "WAITING_FOR_PROVIDER"):
        raise HTTPException(409, f"scene is {s.status}")
    fn = safe_name(filename, "clip.mp4")
    if Path(fn).suffix.lower() not in (".mp4", ".mov", ".webm", ".mkv"):
        raise HTTPException(415, "clip must be mp4, mov, webm or mkv")
    dest = project_dir(user.id, p.id) / "clips" / f"{s.idx + 1:02d}_import_{safe_name(user.id)[-6:]}_{fn}"
    await read_upload(req, dest)
    persist_file(dest)
    try:
        info = fx.probe(dest)
        if not info["has_video"]:
            raise fx.MediaError("no video stream")
    except fx.MediaError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, f"not a playable video: {e}")
    job = db.query(GenerationJob).filter_by(scene_id=s.id, status="MANUAL").order_by(GenerationJob.created_at.desc()).first()
    art = GenerationArtifact(user_id=user.id, project_id=p.id, scene_id=s.id, job_id=job.id if job else None, provider_id="manual", kind="clip", path=str(dest),
                             duration=info["video_duration"], status="candidate", meta={"imported": True, "cost": 0})
    db.add(art)
    if job:
        job.status = "SUCCEEDED"
    db.commit()
    E.set_scene(db, s, "VALIDATING", "manual import")
    cred = db.query(ProviderCredential).filter_by(user_id=user.id, provider_id="manual").first()
    prov = build_provider("manual", context_for(cred)) if cred else None
    caps = await prov.get_capabilities() if prov else {"watermark": None}
    if job is None:
        job = GenerationJob(user_id=user.id, project_id=p.id, scene_id=s.id, credential_id=cred.id if cred else "", provider_id="manual", status="SUCCEEDED",
                            idempotency_key=f"{s.id}:import:{art.id}", requested_seconds=info["video_duration"], request={})
        db.add(job)
        db.commit()
        art.job_id = job.id
        db.commit()
    job.requested_seconds = s.duration
    db.commit()
    await E.validate_and_decide(db, p, s, job, art, prov, caps)
    if p.status in ("MANUAL_ACTION_REQUIRED",) and s.status in ("ACCEPTED", "RETRYING"):
        E.set_project(db, p, "GENERATING")
    return ser_scene(s)


@router.get("/projects/{pid}/manual-tasks")
def manual_tasks(pid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    out = []
    for s in db.query(VideoScene).filter(VideoScene.project_id == p.id, VideoScene.status.in_(["MANUAL_ACTION_REQUIRED", "REVIEW"])).order_by(VideoScene.idx).all():
        h = db.query(ContextHandoff).filter_by(scene_id=s.id).order_by(ContextHandoff.created_at.desc()).first()
        out.append({"scene_id": s.id, "idx": s.idx, "status": s.status, "reason": s.last_error, "title": s.title, "duration": s.duration, "prompt": s.prompt,
                    "negative_prompt": s.negative_prompt, "handoff": redact(h.package) if h else None})
    return out


# ------------------------------------------------------------------ audio & narration
@router.put("/projects/{pid}/audio")
async def upload_audio(pid: str, req: Request, filename: str = "narration.wav", kind: str = "master_narration", user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    fn = safe_name(filename, "narration.wav")
    if Path(fn).suffix.lower() not in (".wav", ".mp3", ".m4a", ".aac", ".ogg", ".flac"):
        raise HTTPException(415, "audio must be wav, mp3, m4a, aac, ogg or flac")
    dest = project_dir(user.id, p.id) / "audio" / fn
    await read_upload(req, dest)
    persist_file(dest)
    try:
        info = fx.probe(dest)
        if not info["has_audio"]:
            raise fx.MediaError("no audio stream")
    except fx.MediaError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(422, f"not a playable audio file: {e}")
    t = AudioTrack(project_id=p.id, kind=kind, path=str(dest), duration=info["duration"], meta={"filename": fn})
    db.add(t)
    db.commit()
    warn = None
    if abs(info["duration"] - p.target_duration) > 1.0:
        warn = f"narration is {info['duration']:.1f}s but the video is {p.target_duration:.1f}s; it will be padded or trimmed at export"
    E.emit(db, p.id, "audio", f"master narration uploaded ({info['duration']:.1f}s)" + (f" - {warn}" if warn else ""))
    return {"id": t.id, "duration": info["duration"], "warning": warn}


@router.put("/projects/{pid}/narration")
def set_narration(pid: str, body: dict[str, Any], user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    segs = body.get("segments")
    db.query(NarrationSegment).filter_by(project_id=p.id).delete()
    if segs:
        for i, sg in enumerate(segs):
            if not (0 <= float(sg["start_time"]) < float(sg["end_time"]) <= p.target_duration + 0.01):
                raise HTTPException(422, f"segment {i} outside the timeline")
            db.add(NarrationSegment(project_id=p.id, idx=i, text=str(sg["text"])[:1000], start_time=float(sg["start_time"]), end_time=float(sg["end_time"]), alignment="user_supplied"))
    db.commit()
    if not segs:
        E.ensure_narration(db, p, db.query(VideoScene).filter_by(project_id=p.id).order_by(VideoScene.idx).all())
    rows = db.query(NarrationSegment).filter_by(project_id=p.id).order_by(NarrationSegment.idx).all()
    return [{"idx": r.idx, "text": r.text, "start_time": r.start_time, "end_time": r.end_time, "alignment": r.alignment} for r in rows]


# ------------------------------------------------------------------ export
@router.post("/projects/{pid}/export")
def export(pid: str, body: dict[str, Any] | None = None, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    if db.query(VideoScene).filter(VideoScene.project_id == p.id, VideoScene.status != "ACCEPTED").count():
        raise HTTPException(409, "all scenes must be accepted before export")
    o = {"burn_subtitles": bool((body or {}).get("burn_subtitles", False)), "transition": (body or {}).get("transition", "cut")}
    if o["transition"] not in ("cut", "fade"):
        raise HTTPException(422, "transition must be cut or fade")
    ex = ExportJob(user_id=user.id, project_id=p.id, status="QUEUED", options=o)
    db.add(ex)
    db.commit()
    return {"id": ex.id, "status": ex.status}


@router.get("/exports/{eid}")
def get_export(eid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    e = A.owned(db, ExportJob, eid, user)
    return {"id": e.id, "project_id": e.project_id, "status": e.status, "error": e.error, "options": e.options, "files": list((e.files or {}).keys()), "report": e.report}


@router.get("/exports/{eid}/files/{kind}")
def export_file(eid: str, kind: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    e = A.owned(db, ExportJob, eid, user)
    path = (e.files or {}).get(kind)
    restore_file(path)
    if not path or not Path(path).exists():
        raise HTTPException(404, "file not available")
    from app.storage.local import resolve_inside
    p = resolve_inside(path)
    mt = {"video": "video/mp4", "subtitles": "application/x-subrip", "report": "application/json", "storyboard": "application/json"}[kind]
    return FileResponse(p, media_type=mt, filename={"video": "video.mp4", "subtitles": "subtitles.srt", "report": "report.json", "storyboard": "storyboard.json"}[kind])


@router.get("/media/{artifact_id}")
def media(artifact_id: str, frame: str | None = None, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    a = A.owned(db, GenerationArtifact, artifact_id, user)
    from app.storage.local import resolve_inside
    path = (a.meta or {}).get(f"{frame}_frame") if frame in ("first", "last") else a.path
    restore_file(path)
    if not path or not Path(path).exists():
        raise HTTPException(404, "not available")
    return FileResponse(resolve_inside(path), media_type="image/png" if frame else "video/mp4")


# ------------------------------------------------------------------ providers
SENSITIVE = ("secret", "key", "token", "password")


def ser_pool(q: QuotaPool):
    return {"id": q.id, "unit": q.unit, "balance_source": q.balance_source, "available": q.available, "reserved": q.reserved, "consumed": q.consumed, "is_paid": q.is_paid,
            "exhausted": q.exhausted, "exhausted_until": q.exhausted_until, "confidence": q.confidence, "reset_at": q.reset_at, "last_success": q.last_success}


def ser_cred(db, c: ProviderCredential):
    pools = db.query(QuotaPool).filter_by(credential_id=c.id).all()
    cfg = {k: v for k, v in (c.config or {}).items() if not any(w in k.lower() for w in SENSITIVE)}
    return {"id": c.id, "provider_id": c.provider_id, "label": c.label, "enabled": c.enabled, "has_secret": bool(c.secret_enc), "secret_hint": c.secret_hint, "config": cfg, "pools": [ser_pool(q) for q in pools]}


@router.get("/providers")
def providers(user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    cat = db.query(GenerationProvider).order_by(GenerationProvider.category, GenerationProvider.name).all()
    creds = db.query(ProviderCredential).filter_by(user_id=user.id).all()
    return {"catalog": [{"id": g.id, "name": g.name, "category": g.category, "implemented": g.implemented, "verification": g.verification, "docs_url": g.docs_url, "notes": g.notes,
                         "verified_at": g.verified_at, "host_family": g.host_family, "is_mock": g.category == "mock"} for g in cat],
            "connections": [ser_cred(db, c) for c in creds]}


class Connect(BaseModel):
    label: str = Field("default", min_length=1, max_length=80)
    secret: str | None = Field(None, max_length=4000)
    config: dict[str, Any] = {}
    balance: float | None = Field(None, ge=0)
    unit: str = "credits"
    is_paid: bool = False


@router.post("/providers/{provider_id}/connect")
async def connect(provider_id: str, body: Connect, req: Request, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    g = db.get(GenerationProvider, provider_id)
    if g is None or (not g.implemented and provider_id != "llm_openai_compat"):
        raise HTTPException(404 if g is None else 422, "provider not available for connection" if g else "unknown provider")
    if db.query(ProviderCredential).filter_by(user_id=user.id, provider_id=provider_id, label=body.label).first():
        raise HTTPException(409, "a connection with this label exists; delete it first or use another label")
    if provider_id == "replicate" and body.config.get("free_trial_only"):
        # A user-typed flag cannot make a possibly paid pool free. Only operator-verified immutable user IDs (never an email string, which anyone can type at signup) (the owner, whose Replicate
        # account has no payment method on file) may use it; everyone else connects it as a paid pool that stays blocked.
        import os
        allowed = {e.strip() for e in os.environ.get("VGOH_FREE_TRIAL_VERIFIED_USER_IDS", "").split(",") if e.strip()}
        if user.id not in allowed:
            raise HTTPException(422, "free-trial mode is unverified for this account. Connect without it: the pool is treated as paid and stays blocked unless you approve paid spend.")
    if provider_id == "replicate" and body.config.get("free_trial_only") and (body.balance is None or body.balance <= 0):
        raise HTTPException(422, "free-trial mode needs a run budget (number of free runs you saw available); the provider does not report it")
    cred = ProviderCredential(user_id=user.id, provider_id=provider_id, label=body.label, config=body.config)
    db.add(cred)
    db.flush()
    if body.secret:
        cred.secret_enc = encrypt_secret(body.secret, f"{user.id}|{provider_id}|{cred.id}")
        cred.secret_hint = "…" + body.secret[-4:] if len(body.secret) >= 12 else "set"
    db.commit()
    audit(db, user, "provider_connect", provider_id, req)
    try:
        prov = build_provider(provider_id, context_for(cred))
        q = await prov.get_quota()
        health = await prov.check_health()
    except ProviderError as e:
        db.delete(cred)
        db.commit()
        raise HTTPException(422, f"could not verify the connection: {e.code}: {e.message}")
    except Exception as e:  # noqa: BLE001
        db.delete(cred)
        db.commit()
        raise HTTPException(422, f"could not verify the connection: {str(e)[:200]}")
    if provider_id != "manual":
        src, avail = q.source, q.available
        if avail is None and body.balance is not None:
            src, avail = "user_entered", body.balance
        db.add(QuotaPool(user_id=user.id, credential_id=cred.id, provider_id=provider_id, unit=q.unit if q.source != "unknown" else ("runs" if (provider_id == "replicate" and body.config.get("free_trial_only")) else body.unit), balance_source=src, available=avail,
                         is_paid=bool(body.is_paid or provider_id == "pollinations" or (provider_id == "replicate" and not body.config.get("free_trial_only")) or (provider_id == "custom_http" and body.config.get("paid", True))), confidence="high" if src == "verified_api" else "low", reset_at=q.reset_at))
        db.commit()
    for p in db.query(VideoProject).filter(VideoProject.user_id == user.id, VideoProject.status.in_(["WAITING_FOR_CREDITS", "WAITING_FOR_PROVIDER"])).all():
        for s in db.query(VideoScene).filter_by(project_id=p.id).all():
            s.not_before = None
    db.commit()
    return {**ser_cred(db, cred), "health": health}


@router.delete("/credentials/{cid}")
def delete_credential(cid: str, req: Request, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    c = A.owned(db, ProviderCredential, cid, user)
    if db.query(GenerationJob).filter(GenerationJob.credential_id == c.id, GenerationJob.status.in_(["SUBMITTING", "SUBMITTED"])).count():
        raise HTTPException(409, "jobs are in flight on this connection")
    db.query(QuotaPool).filter_by(credential_id=c.id).delete()
    db.delete(c)
    db.commit()
    audit(db, user, "credential_delete", c.provider_id, req)
    return {"ok": True}


@router.patch("/credentials/{cid}")
def patch_credential(cid: str, body: dict[str, Any], user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    c = A.owned(db, ProviderCredential, cid, user)
    if "enabled" in body:
        c.enabled = bool(body["enabled"])
    db.commit()
    for p in db.query(VideoProject).filter(VideoProject.user_id == user.id, VideoProject.status.in_(["WAITING_FOR_CREDITS", "WAITING_FOR_PROVIDER"])).all():
        for s in db.query(VideoScene).filter_by(project_id=p.id).all():
            s.not_before = None
    db.commit()
    return ser_cred(db, c)


@router.post("/credentials/{cid}/balance")
def set_balance(cid: str, body: dict[str, Any], user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    """User-entered balance (source 'user_entered'). Does not move money; it tells the ledger what the user says is available."""
    c = A.owned(db, ProviderCredential, cid, user)
    q = db.query(QuotaPool).filter_by(credential_id=c.id).first()
    if not q:
        raise HTTPException(404, "no quota pool for this connection")
    try:
        amt = float(body["available"])
    except Exception:  # noqa: BLE001
        raise HTTPException(422, "available must be a number")
    if amt < 0:
        raise HTTPException(422, "available must be >= 0")
    q.available = amt
    q.balance_source = "user_entered" if q.balance_source != "verified_api" else q.balance_source
    q.exhausted, q.exhausted_until = False, None
    db.add(__import__("app.database.models", fromlist=["CreditTransaction"]).CreditTransaction(pool_id=q.id, user_id=user.id, kind="adjust", amount=amt, note="balance set by user"))
    db.commit()
    for p in db.query(VideoProject).filter(VideoProject.user_id == user.id, VideoProject.status.in_(["WAITING_FOR_CREDITS", "WAITING_FOR_PROVIDER"])).all():
        for s in db.query(VideoScene).filter_by(project_id=p.id).all():
            s.not_before = None
    db.commit()
    return ser_pool(q)


@router.get("/credentials/{cid}/health")
async def credential_health(cid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    c = A.owned(db, ProviderCredential, cid, user)
    prov = build_provider(c.provider_id, context_for(c))
    try:
        return {"health": await prov.check_health(), "capabilities": await prov.get_capabilities()}
    except ProviderError as e:
        return {"health": {"ok": False, "detail": f"{e.code}: {e.message}"}}


@router.get("/credentials/{cid}/ledger")
def credential_ledger(cid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    from app.database.models import CreditTransaction
    c = A.owned(db, ProviderCredential, cid, user)
    ids = [q.id for q in db.query(QuotaPool).filter_by(credential_id=c.id).all()]
    tx = db.query(CreditTransaction).filter(CreditTransaction.pool_id.in_(ids)).order_by(CreditTransaction.created_at.desc()).limit(200).all() if ids else []
    return [{"kind": t.kind, "amount": t.amount, "estimated": t.estimated, "note": t.note, "job_id": t.job_id, "at": t.created_at} for t in tx]


@router.get("/projects/{pid}/report")
def project_report(pid: str, user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    p = project_of(db, user, pid)
    ex = db.query(ExportJob).filter_by(project_id=p.id, status="DONE").order_by(ExportJob.created_at.desc()).first()
    if not ex or not (ex.files or {}).get("report"):
        raise HTTPException(404, "no export report yet")
    return json.loads(Path(ex.files["report"]).read_text())


@router.get("/dashboard")
def dashboard(user: User = Depends(A.current_user), db: Session = Depends(A.get_db)):
    ps = db.query(VideoProject).filter_by(user_id=user.id).all()
    pools = db.query(QuotaPool).filter_by(user_id=user.id).all()
    creds = {c.id: c for c in db.query(ProviderCredential).filter_by(user_id=user.id).all()}
    return {"projects": {"total": len(ps), "by_status": {s: sum(1 for p in ps if p.status == s) for s in {p.status for p in ps}}},
            "credits": [{"provider": q.provider_id, "label": creds[q.credential_id].label if q.credential_id in creds else None, "available": q.available, "unit": q.unit,
                         "source": q.balance_source, "exhausted": q.exhausted, "reserved": q.reserved} for q in pools],
            "connections": len(creds)}
