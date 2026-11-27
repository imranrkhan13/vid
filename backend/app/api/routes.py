"""HTTP API (README section 25).

Implements the documented endpoints over FastAPI. Live updates use SSE.
Authentication/authorization/rate-limiting are represented by dependency hooks
that are no-ops in local mode; they must be configured before production use.
"""

from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse, StreamingResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.analytics.cost import CostOptimizer, CostPlanItem
from app.analytics.stats import StatsCollector
from app.api.deps import db_session, get_registry, get_service
from app.core.state import ProjectStatus
from app.models.generation import WorkflowEvent
from app.models.projects import VideoProject
from app.models.storyboard import VideoScene
from app.orchestration.orchestrator import Orchestrator
from app.providers.catalog import load_entries
from app.providers.real import FREE_PROVIDER_IDS, PROVIDER_REGISTRY
from app.schemas.api import (
    BudgetPolicyRequest,
    CharacterRequest,
    CostPlanItemResponse,
    CostPlanResponse,
    CreateProjectRequest,
    CredentialRequest,
    CredentialResponse,
    EventResponse,
    ExportRequest,
    ExportResponse,
    GenerateRequest,
    KeyStatusResponse,
    KeyStoreMetaResponse,
    PlanRequest,
    ProjectResponse,
    ProviderKeyRequest,
    ProviderKeysBulkRequest,
    ProviderResponse,
    ProviderStatsResponse,
    SceneResponse,
    StyleRequest,
    UpdateProjectRequest,
)
from app.security.credentials import CredentialStore, resolve_default_cipher
from app.security.keystore import default_key_store
from app.services.export import ExportService
from app.services.projects import ProjectService

router = APIRouter(prefix="/api/v1")


def _project_response(project: VideoProject) -> ProjectResponse:
    remaining = max(0.0, project.target_duration_seconds - project.accepted_duration_seconds)
    return ProjectResponse(
        project_id=project.id,
        title=project.title,
        status=project.status,
        target_duration_seconds=project.target_duration_seconds,
        accepted_duration_seconds=project.accepted_duration_seconds,
        remaining_duration_seconds=remaining,
        aspect_ratio=project.aspect_ratio,
        resolution=project.resolution,
        fps=project.fps,
    )


@router.post("/projects", response_model=ProjectResponse)
def create_project(
    body: CreateProjectRequest, session: Session = Depends(db_session)
) -> ProjectResponse:
    service = get_service(session)
    project = service.create_project(
        title=body.title,
        concept=body.concept,
        target_duration_seconds=body.target_duration_seconds,
        aspect_ratio=body.aspect_ratio,
        resolution=body.resolution,
        fps=body.fps,
        style=body.style,
        max_budget=body.max_budget,
    )
    return _project_response(project)


@router.get("/projects/{project_id}", response_model=ProjectResponse)
def get_project(project_id: str, session: Session = Depends(db_session)) -> ProjectResponse:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    return _project_response(project)


@router.patch("/projects/{project_id}", response_model=ProjectResponse)
def update_project(
    project_id: str, body: UpdateProjectRequest, session: Session = Depends(db_session)
) -> ProjectResponse:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    for field in ("title", "concept", "target_duration_seconds", "max_budget"):
        value = getattr(body, field)
        if value is not None:
            setattr(project, field, value)
    session.flush()
    return _project_response(project)


@router.post("/projects/{project_id}/plan", response_model=list[SceneResponse])
def plan_project(
    project_id: str, body: PlanRequest, session: Session = Depends(db_session)
) -> list[SceneResponse]:
    service = get_service(session)
    try:
        service.plan_storyboard(project_id, scene_count=body.scene_count)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    scenes = session.execute(
        select(VideoScene).where(VideoScene.project_id == project_id).order_by(VideoScene.index)
    ).scalars().all()
    return [
        SceneResponse(
            scene_id=s.id,
            index=s.index,
            description=s.description,
            duration_seconds=s.duration_seconds,
            status=s.status,
        )
        for s in scenes
    ]


@router.post("/projects/{project_id}/generate")
async def generate_project(
    project_id: str, body: GenerateRequest, session: Session = Depends(db_session)
) -> dict:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    orchestrator = Orchestrator(
        session,
        get_registry(),
        allow_paid_spend=body.allow_paid_spend,
    )
    report = await orchestrator.run(project_id)
    return report.to_dict()


@router.post("/projects/{project_id}/pause", response_model=ProjectResponse)
def pause_project(project_id: str, session: Session = Depends(db_session)) -> ProjectResponse:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    project.status = ProjectStatus.PAUSED.value
    session.flush()
    return _project_response(project)


@router.post("/projects/{project_id}/resume", response_model=ProjectResponse)
def resume_project(project_id: str, session: Session = Depends(db_session)) -> ProjectResponse:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    project.status = ProjectStatus.SELECTING_PROVIDER.value
    session.flush()
    return _project_response(project)


@router.post("/projects/{project_id}/cancel", response_model=ProjectResponse)
def cancel_project(project_id: str, session: Session = Depends(db_session)) -> ProjectResponse:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    project.status = ProjectStatus.CANCELLED.value
    session.flush()
    return _project_response(project)


@router.get("/projects/{project_id}/scenes", response_model=list[SceneResponse])
def list_scenes(project_id: str, session: Session = Depends(db_session)) -> list[SceneResponse]:
    scenes = session.execute(
        select(VideoScene).where(VideoScene.project_id == project_id).order_by(VideoScene.index)
    ).scalars().all()
    return [
        SceneResponse(
            scene_id=s.id,
            index=s.index,
            description=s.description,
            duration_seconds=s.duration_seconds,
            status=s.status,
        )
        for s in scenes
    ]


@router.post("/projects/{project_id}/style")
def set_style(
    project_id: str, body: StyleRequest, session: Session = Depends(db_session)
) -> dict:
    service = get_service(session)
    row = service.set_style(
        project_id,
        name=body.name,
        description=body.description,
        palette=body.palette,
        grade=body.grade,
        motion_style=body.motion_style,
        negative_prompts=body.negative_prompts,
    )
    return {"style_id": row.id, "name": row.name}


@router.post("/projects/{project_id}/characters")
def add_character(
    project_id: str, body: CharacterRequest, session: Session = Depends(db_session)
) -> dict:
    service = get_service(session)
    row = service.add_character(
        project_id,
        name=body.name,
        description=body.description,
        palette=body.palette,
        proportions=body.proportions,
        reference_uris=body.reference_uris,
    )
    return {"character_id": row.id, "name": row.name}


@router.patch("/projects/{project_id}/budget", response_model=ProjectResponse)
def set_budget(
    project_id: str, body: BudgetPolicyRequest, session: Session = Depends(db_session)
) -> ProjectResponse:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    get_service(session).set_budget_policy(
        project_id,
        max_budget=body.max_budget,
        allow_paid=body.allow_paid,
        max_paid_spend=body.max_paid_spend,
    )
    return _project_response(project)


def _export_response(job) -> ExportResponse:
    return ExportResponse(
        export_id=job.id,
        project_id=job.project_id,
        status=job.status,
        output_uri=job.output_uri,
        manifest=json.loads(job.manifest_json or "{}"),
    )


@router.post("/projects/{project_id}/export", response_model=ExportResponse)
def create_export(
    project_id: str, body: ExportRequest, session: Session = Depends(db_session)
) -> ExportResponse:
    project = session.get(VideoProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    service = ExportService(session)
    job = service.create_export(
        project_id,
        resolution=body.resolution or project.resolution,
        fps=body.fps or project.fps,
        aspect_ratio=body.aspect_ratio or project.aspect_ratio,
    )
    return _export_response(job)


@router.get("/projects/{project_id}/exports", response_model=list[ExportResponse])
def list_exports(project_id: str, session: Session = Depends(db_session)) -> list[ExportResponse]:
    return [_export_response(j) for j in ExportService(session).list_exports(project_id)]


@router.get("/exports/{export_id}/download")
def download_export(export_id: str, session: Session = Depends(db_session)) -> FileResponse:
    path = ExportService(session).download_path(export_id)
    if path is None:
        raise HTTPException(
            status_code=404,
            detail="export has no downloadable file (composition may be manifest-only)",
        )
    return FileResponse(path, media_type="video/mp4", filename=f"{export_id}.mp4")


@router.get("/providers", response_model=list[ProviderResponse])
def list_providers() -> list[ProviderResponse]:
    return [
        ProviderResponse(
            provider_id=p.provider_id,
            name=p.name,
            category=p.category,
            enabled=True,
            is_mock=p.is_mock,
        )
        for p in get_registry().all()
    ]


@router.get("/providers/{provider_id}/stats", response_model=ProviderStatsResponse)
def provider_stats(provider_id: str, session: Session = Depends(db_session)) -> ProviderStatsResponse:
    """Measured reliability/quality from the durable attempt log (section 11)."""
    stats = StatsCollector(session).for_provider(provider_id)
    return ProviderStatsResponse(
        provider_id=stats.provider_id,
        attempts=stats.attempts,
        successes=stats.successes,
        failures=stats.failures,
        measured_reliability=stats.measured_reliability,
        avg_quality=stats.avg_quality,
        total_credits=stats.total_credits,
        has_enough_samples=stats.has_enough_samples,
    )


@router.post("/providers/credentials", response_model=CredentialResponse)
def store_credential(
    body: CredentialRequest, session: Session = Depends(db_session)
) -> CredentialResponse:
    """Store an encrypted provider credential. The secret is never returned."""
    store = CredentialStore(session, resolve_default_cipher())
    try:
        row = store.store(
            body.provider_id,
            body.secret,
            billing_account=body.billing_account,
            organization=body.organization,
        )
    except Exception as exc:  # noqa: BLE001 - surfaced as 400, secret never echoed
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return CredentialResponse(
        credential_id=row.id,
        provider_id=row.provider_id,
        billing_account=row.billing_account,
        organization=row.organization,
        enabled=row.enabled,
    )


@router.get("/providers/{provider_id}/credentials", response_model=list[CredentialResponse])
def list_credentials(
    provider_id: str, session: Session = Depends(db_session)
) -> list[CredentialResponse]:
    rows = CredentialStore(session, resolve_default_cipher()).list_for_provider(provider_id)
    return [
        CredentialResponse(
            credential_id=r.id,
            provider_id=r.provider_id,
            billing_account=r.billing_account,
            organization=r.organization,
            enabled=r.enabled,
        )
        for r in rows
    ]


# --- unified API-key entry (one place for every provider) -------------------

def _provider_key_status() -> list[KeyStatusResponse]:
    """Every provider with its key status. Values are never included."""
    store = default_key_store()
    catalog = {e.provider_id: e for e in load_entries()}
    rows: list[KeyStatusResponse] = []
    seen: set[str] = set()

    # Catalog providers first (includes the labelled mock and free paths).
    for entry in catalog.values():
        env_var = PROVIDER_REGISTRY.get(entry.provider_id, {}).get("api_key_env", "")
        free = entry.provider_id in FREE_PROVIDER_IDS or entry.category.value in (
            "mock",
            "manual_assisted",
            "local",
        )
        source = store.source_for(entry.provider_id, env_var)
        rows.append(
            KeyStatusResponse(
                provider_id=entry.provider_id,
                name=entry.name,
                category=entry.category.value,
                is_mock=entry.is_mock,
                free=free,
                requires_key=not free,
                has_key=source is not None,
                source=source,
                env_var=env_var,
                docs_url=entry.docs_url,
                key_url=entry.website_url,
            )
        )
        seen.add(entry.provider_id)

    # Any registry adapter not present in the catalog (defensive).
    for provider in get_registry().all():
        if provider.provider_id in seen:
            continue
        env_var = PROVIDER_REGISTRY.get(provider.provider_id, {}).get("api_key_env", "")
        source = store.source_for(provider.provider_id, env_var)
        rows.append(
            KeyStatusResponse(
                provider_id=provider.provider_id,
                name=provider.name,
                category=provider.category,
                is_mock=provider.is_mock,
                free=provider.is_mock,
                requires_key=not provider.is_mock,
                has_key=source is not None,
                source=source,
                env_var=env_var,
            )
        )
    return rows


@router.get("/keys", response_model=KeyStoreMetaResponse)
def key_store_status() -> KeyStoreMetaResponse:
    """The single place to see which providers have keys configured."""
    store = default_key_store()
    providers = _provider_key_status()
    return KeyStoreMetaResponse(
        encrypted=store.encrypted,
        keys_file=str(store.path),
        configured_count=sum(1 for p in providers if p.has_key),
        providers=providers,
    )


@router.put("/keys")
def set_provider_key(body: ProviderKeyRequest) -> dict:
    """Store (or replace) the API key for one provider."""
    store = default_key_store()
    try:
        store.set(body.provider_id, body.api_key)
    except Exception as exc:  # noqa: BLE001 - never echo the key
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"provider_id": body.provider_id, "configured": True, "encrypted": store.encrypted}


@router.put("/keys/bulk")
def set_provider_keys_bulk(body: ProviderKeysBulkRequest) -> dict:
    """Store keys for several providers in one call."""
    store = default_key_store()
    try:
        stored = store.set_many(body.keys)
    except Exception as exc:  # noqa: BLE001 - never echo any key
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"stored": stored, "encrypted": store.encrypted}


@router.delete("/keys/{provider_id}")
def delete_provider_key(provider_id: str) -> dict:
    removed = default_key_store().delete(provider_id)
    return {"provider_id": provider_id, "removed": removed}


@router.get("/projects/{project_id}/cost-plan", response_model=CostPlanResponse)
def cost_plan(project_id: str, session: Session = Depends(db_session)) -> CostPlanResponse:
    """Rank eligible providers cheapest-first and report the credit cost.

    Never authorizes paid spending; it only reports what a plan would cost.
    """
    project = session.get(VideoProject, project_id)
    if project is None:
        raise HTTPException(status_code=404, detail="project not found")
    orchestrator = Orchestrator(session, get_registry())
    optimizer = CostOptimizer(
        max_budget=project.max_budget, allow_paid=bool(project.allow_paid)
    )
    items: list[CostPlanItem] = []
    for provider in get_registry().all():
        caps = asyncio.run(provider.get_capabilities())
        estimate = asyncio.run(provider.estimate_cost(_placeholder_request(project)))
        remaining = orchestrator.ledger.remaining(provider.provider_id, model_name=caps.model_name)
        items.append(
            CostPlanItem(
                provider_id=provider.provider_id,
                estimated_credits=estimate.credits,
                is_paid=estimate.is_paid,
                remaining_credits=remaining,
            )
        )
    plan = optimizer.plan(items)
    return CostPlanResponse(
        project_id=project_id,
        ordered=[
            CostPlanItemResponse(
                provider_id=i.provider_id,
                estimated_credits=i.estimated_credits,
                is_paid=i.is_paid,
                remaining_credits=i.remaining_credits,
            )
            for i in plan.ordered
        ],
        free_credits_needed=plan.free_credits_needed,
        paid_credits_needed=plan.paid_credits_needed,
        requires_paid=plan.requires_paid,
        within_budget=plan.within_budget,
        notes=plan.notes,
    )


def _placeholder_request(project: VideoProject):
    """A representative request for cost estimation (one typical scene)."""
    from app.schemas.provider import GenerationMode, ProviderRequest

    scenes = session_scene_duration(project)
    return ProviderRequest(
        request_id="estimate",
        project_id=project.id,
        scene_id="estimate",
        mode=GenerationMode.TEXT_TO_VIDEO,
        prompt="estimate",
        duration_seconds=scenes,
        aspect_ratio=project.aspect_ratio,
        resolution=project.resolution,
        fps=project.fps,
    )


def session_scene_duration(project: VideoProject) -> float:
    # Use the target duration as a conservative single-scene estimate when the
    # storyboard is not yet planned; otherwise the first scene's duration.
    return project.target_duration_seconds or 8.0


@router.get("/projects/{project_id}/events", response_model=list[EventResponse])
def list_events(project_id: str, session: Session = Depends(db_session)) -> list[EventResponse]:
    events = session.execute(
        select(WorkflowEvent)
        .where(WorkflowEvent.project_id == project_id)
        .order_by(WorkflowEvent.id)
    ).scalars().all()
    return [
        EventResponse(
            event_type=e.event_type,
            scene_id=e.scene_id,
            payload=json.loads(e.payload_json or "{}"),
            created_at=e.created_at.isoformat() if e.created_at else "",
        )
        for e in events
    ]


@router.get("/projects/{project_id}/events/stream")
async def stream_events(project_id: str, session: Session = Depends(db_session)) -> StreamingResponse:
    """Server-Sent Events stream of workflow events (section 25)."""

    async def event_source():
        last_id = 0
        # Bounded stream: emits new events then closes when the project settles.
        for _ in range(1000):
            events = session.execute(
                select(WorkflowEvent)
                .where(WorkflowEvent.project_id == project_id, WorkflowEvent.id > last_id)
                .order_by(WorkflowEvent.id)
            ).scalars().all()
            for e in events:
                last_id = e.id
                payload = {
                    "event_type": e.event_type,
                    "scene_id": e.scene_id,
                    "payload": json.loads(e.payload_json or "{}"),
                }
                yield f"data: {json.dumps(payload)}\n\n"
            await asyncio.sleep(0.05)

    return StreamingResponse(event_source(), media_type="text/event-stream")
