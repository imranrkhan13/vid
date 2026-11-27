"""Cross-model context transfer (README sections 13, 14).

The handoff carries semantic and visual context only (text, reference images, previous frame, positions, style).
Neural latents, hidden memory and random seeds do not transfer between unrelated models and are never claimed to.
When a provider cannot take a reference input the package records an intentional camera cut instead."""
from __future__ import annotations
from typing import Any

from sqlalchemy.orm import Session

from app.database.models import (Character, CharacterReferenceAsset, ContextHandoff, GenerationArtifact, ProjectCharacter,
                                 StyleProfile, VideoProject, VideoScene)


def build_handoff(db: Session, project: VideoProject, scene: VideoScene, *, reason: str, previous_provider: str | None,
                  to_caps: dict[str, Any] | None = None, requested_seconds: float | None = None) -> dict[str, Any]:
    style = db.get(StyleProfile, (project.id, project.style_version))
    chars = (db.query(Character).join(ProjectCharacter, ProjectCharacter.character_id == Character.id)
             .filter(ProjectCharacter.project_id == project.id).order_by(Character.created_at, Character.id).all())
    wanted = set(scene.characters or [])
    char_out, refs = [], []
    for c in chars:
        if wanted and c.name not in wanted:
            continue
        assets = db.query(CharacterReferenceAsset).filter(CharacterReferenceAsset.character_id == c.id).all()
        paths = [{"kind": a.kind, "path": a.path} for a in assets]
        refs += [a.path for a in assets]
        prof = c.profile or {}
        char_out.append({"id": c.id, "name": c.name, "version": c.version, "appearance": prof.get("appearance"), "clothing": prof.get("clothing"),
                         "proportions": prof.get("proportions"), "palette": prof.get("palette", {}), "expressions": prof.get("expressions"),
                         "scale": prof.get("scale", 1.0), "references": paths})
    prev = (db.query(VideoScene).filter(VideoScene.project_id == project.id, VideoScene.idx < scene.idx, VideoScene.status == "ACCEPTED")
            .order_by(VideoScene.idx.desc()).first())
    prev_frame, positions, prev_art = None, {}, None
    if prev and prev.accepted_artifact_id:
        prev_art = db.get(GenerationArtifact, prev.accepted_artifact_id)
        if prev_art:
            prev_frame = (prev_art.meta or {}).get("last_frame")
            positions = (prev_art.meta or {}).get("end_positions") or {}
    caps = to_caps or {}
    degraded = []
    if prev_frame and not caps.get("first_frame", True):
        degraded.append("provider cannot condition on a previous frame: using an intentional camera cut; style, characters and positions still passed as text")
    if refs and not caps.get("multi_reference", True):
        degraded.append("provider cannot take reference images: character bible passed as text only")
    remaining = max(0.0, project.target_duration - project.accepted_duration)
    pkg = {
        "project_id": project.id, "scene_id": scene.id, "scene_index": scene.idx, "scene_title": scene.title, "instruction_version": scene.scene_version,
        "handoff_reason": reason, "previous_provider": previous_provider, "remaining_project_seconds": round(remaining, 3),
        "requested_clip_seconds": requested_seconds or scene.duration, "timeline_placement": {"start": scene.start_time, "duration": scene.duration},
        "scene_description": scene.description, "prompt": scene.prompt, "negative_prompt": scene.negative_prompt,
        "characters": char_out, "motion": scene.motion, "camera": scene.camera, "lighting": scene.lighting, "environment": scene.environment,
        "emotion": scene.emotion, "narration": scene.narration, "corrective_notes": list(scene.corrective_notes or []),
        "style": {"version": project.style_version, **((style.data if style else {}) or {})},
        "continuity": {"previous_frame": prev_frame, "previous_scene_id": prev.id if prev else None,
                       "character_positions": positions, "first_frame_requirement": "match previous ending frame" if prev_frame else "free start",
                       "last_frame_requirement": "end in a pose that can continue into the next scene",
                       "camera_cut": bool(degraded and prev_frame and not caps.get("first_frame", True)), "degraded": degraded},
        "qa_requirements": scene.acceptance or {},
        "transfer_limits": "semantic and visual context only; latents, hidden memory and seeds do not transfer across unrelated models",
    }
    return pkg


def reference_images(pkg: dict[str, Any]) -> list[str]:
    out = []
    for c in pkg.get("characters", []):
        out += [r["path"] for r in c.get("references", [])]
    from app.storage.local import restore_file
    for _p in out:
        restore_file(_p)
    return out


def store_handoff(db: Session, project: VideoProject, scene: VideoScene, pkg: dict[str, Any], to_provider: str | None) -> ContextHandoff:
    h = ContextHandoff(project_id=project.id, scene_id=scene.id, reason=pkg["handoff_reason"], from_provider=pkg.get("previous_provider"),
                       to_provider=to_provider, package=pkg)
    db.add(h)
    db.commit()
    return h


def provider_prompt(pkg: dict[str, Any], base_prompt: str, limit: int) -> str:
    """Text-only providers receive just a prompt string, so the handoff context that has no other channel
    (previous scene, character positions, corrective notes, camera-cut note) is folded into it, within the provider's limit.
    The scene's own prompt always keeps priority: continuity text is trimmed first, never the scene prompt."""
    cont = pkg.get("continuity") or {}
    bits: list[str] = []
    if pkg.get("scene_index", 0) > 0 and cont.get("previous_scene_id"):
        bits.append("This continues directly from the previous scene of the same story.")
    pos = cont.get("character_positions") or {}
    if pos:
        bits.append("Character positions at the end of the previous scene: " + "; ".join(f"{k}: {v}" for k, v in list(pos.items())[:4]) + ".")
    if cont.get("camera_cut"):
        bits.append("Start with a clean camera cut; keep the same characters, clothing, colors and art style.")
    notes = [str(n) for n in (pkg.get("corrective_notes") or [])][:3]
    if notes:
        bits.append("Fix from earlier attempts: " + " ".join(notes))
    extra = " ".join(bits)
    base = (base_prompt or "")[:limit]
    room = limit - len(base) - 1
    if not extra or room < 40:
        return base
    return f"{base} {extra[:room]}".strip()
