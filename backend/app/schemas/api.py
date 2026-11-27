"""API request/response DTOs (section 25)."""

from __future__ import annotations

from pydantic import BaseModel, Field


class CreateProjectRequest(BaseModel):
    title: str
    concept: str = ""
    target_duration_seconds: float = 32.0
    aspect_ratio: str = "9:16"
    resolution: str = "1080x1920"
    fps: int = 30
    style: str = ""
    max_budget: float | None = None


class UpdateProjectRequest(BaseModel):
    title: str | None = None
    concept: str | None = None
    target_duration_seconds: float | None = None
    max_budget: float | None = None


class PlanRequest(BaseModel):
    scene_count: int | None = None


class GenerateRequest(BaseModel):
    allow_paid_spend: bool = False


class ProjectResponse(BaseModel):
    project_id: str
    title: str
    status: str
    target_duration_seconds: float
    accepted_duration_seconds: float
    remaining_duration_seconds: float
    aspect_ratio: str
    resolution: str
    fps: int


class SceneResponse(BaseModel):
    scene_id: str
    index: int
    description: str
    duration_seconds: float
    status: str
    provider: str | None = None


class ProviderResponse(BaseModel):
    provider_id: str
    name: str
    category: str
    enabled: bool
    is_mock: bool
    verified: bool = False


class EventResponse(BaseModel):
    event_type: str
    scene_id: str | None = None
    payload: dict = Field(default_factory=dict)
    created_at: str = ""


class ExportRequest(BaseModel):
    resolution: str | None = None
    fps: int | None = None
    aspect_ratio: str | None = None


class ExportResponse(BaseModel):
    export_id: str
    project_id: str
    status: str
    output_uri: str = ""
    manifest: dict = Field(default_factory=dict)


class StyleRequest(BaseModel):
    name: str
    description: str = ""
    palette: list[str] = Field(default_factory=list)
    grade: str = ""
    motion_style: str = ""
    negative_prompts: list[str] = Field(default_factory=list)


class CharacterRequest(BaseModel):
    name: str
    description: str = ""
    palette: list[str] = Field(default_factory=list)
    proportions: str = ""
    reference_uris: list[str] = Field(default_factory=list)


class BudgetPolicyRequest(BaseModel):
    max_budget: float | None = None
    allow_paid: bool = False
    max_paid_spend: float | None = None


class ProviderStatsResponse(BaseModel):
    provider_id: str
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    measured_reliability: float | None = None
    avg_quality: float | None = None
    total_credits: float = 0.0
    has_enough_samples: bool = False


class CredentialRequest(BaseModel):
    provider_id: str
    secret: str
    billing_account: str = ""
    organization: str = ""


class CredentialResponse(BaseModel):
    credential_id: str
    provider_id: str
    billing_account: str = ""
    organization: str = ""
    enabled: bool = True
    # The secret is never returned; only its presence is acknowledged.


class CostPlanItemResponse(BaseModel):
    provider_id: str
    estimated_credits: float
    is_paid: bool
    remaining_credits: float | None = None


class CostPlanResponse(BaseModel):
    project_id: str
    ordered: list[CostPlanItemResponse] = Field(default_factory=list)
    free_credits_needed: float = 0.0
    paid_credits_needed: float = 0.0
    requires_paid: bool = False
    within_budget: bool = True
    notes: list[str] = Field(default_factory=list)


class ProviderKeyRequest(BaseModel):
    provider_id: str
    api_key: str


class ProviderKeysBulkRequest(BaseModel):
    """Enter keys for several providers at once (the single entry point)."""
    keys: dict[str, str] = Field(default_factory=dict)


class KeyStatusResponse(BaseModel):
    provider_id: str
    name: str
    category: str
    is_mock: bool = False
    free: bool = False
    requires_key: bool = True
    has_key: bool = False
    source: str | None = None
    env_var: str = ""
    docs_url: str = ""
    key_url: str = ""


class KeyStoreMetaResponse(BaseModel):
    encrypted: bool
    keys_file: str
    configured_count: int
    providers: list[KeyStatusResponse] = Field(default_factory=list)
