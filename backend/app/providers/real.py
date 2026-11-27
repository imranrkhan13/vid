"""Real provider adapters (sections 3, 10, 29).

These adapters are **real HTTP code**, not mocks: they speak the provider's async
task API over ``httpx``. They are still *inert* until a credential is present —
constructing one performs no network I/O, and every call fails closed with
``AUTHENTICATION_FAILED`` when no credential is configured. Capabilities and
pricing are read from the machine-readable catalog, never hardcoded here; the
only provider-specific knowledge an adapter holds is *how to talk to its own
API*, which is exactly what an adapter is for.

The HTTP endpoint details are adapter-level defaults and are marked as requiring
confirmation against the provider's official documentation before production use.
Nothing here invents capabilities or fabricates results.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

import httpx

from app.core.errors import ErrorCode, NormalizedError
from app.providers.base import VideoProvider
from app.providers.catalog import DEFAULT_CATALOG_PATH, load_entries
from app.providers.registry import ProviderRegistry
from app.security.keystore import default_key_store
from app.schemas.provider import (
    CostEstimate,
    JobState,
    JobStatus,
    ProviderCapabilities,
    ProviderRequest,
    ProviderResult,
    QuotaInfo,
)

# --- catalog-driven adapter registry ---------------------------------------
#
# These facts describe *how to talk to each provider's own API* — endpoint paths,
# auth scheme and env var name — which is exactly what an adapter owns. The
# provider's *capabilities and pricing* still come from the catalog, never here.
# All endpoints require confirmation against official documentation before
# production use. Providers absent from this table are served by a generic
# adapter built entirely from catalog data (endpoint empty until documented).

PROVIDER_REGISTRY: dict[str, dict] = {
    "runway": {
        "name": "Runway",
        "category": "native_api",
        "base_url": "https://api.dev.runwayml.com",
        "submit_path": "/v1/image_to_video",
        "status_path": "/v1/tasks/{job_id}",
        "api_key_env": "RUNWAY_API_KEY",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
        "extra_headers": {"X-Runway-Version": "2024-11-06"},
    },
    "replicate": {
        "name": "Replicate",
        "category": "aggregator",
        "base_url": "https://api.replicate.com",
        "submit_path": "/v1/predictions",
        "status_path": "/v1/predictions/{job_id}",
        "api_key_env": "REPLICATE_API_TOKEN",
    },
    "fal": {
        "name": "fal.ai",
        "category": "aggregator",
        "base_url": "https://queue.fal.run",
        "submit_path": "/{model}",
        "status_path": "/{model}/requests/{job_id}/status",
        "result_path": "/{model}/requests/{job_id}",
        "api_key_env": "FAL_KEY",
        "auth_header": "Authorization",
        "auth_prefix": "Key ",
    },
    "luma": {
        "name": "Luma Dream Machine (Ray 2)",
        "category": "native_api",
        "base_url": "https://api.lumalabs.ai",
        "submit_path": "/dream-machine/v1/generations",
        "status_path": "/dream-machine/v1/generations/{job_id}",
        "api_key_env": "LUMAAI_API_KEY",
    },
    "kling": {
        "name": "Kling AI (official API)",
        "category": "native_api",
        "base_url": "https://api.klingai.com",
        "submit_path": "/v1/videos/text2video",
        "status_path": "/v1/videos/text2video/{job_id}",
        "api_key_env": "KLING_API_KEY",
    },
    "minimax_hailuo": {
        "name": "MiniMax Hailuo",
        "category": "native_api",
        "base_url": "https://api.minimax.io",
        "submit_path": "/v1/video_generation",
        "status_path": "/v1/query/video_generation/{job_id}",
        "api_key_env": "MINIMAX_API_KEY",
    },
    "huggingface_inference": {
        "name": "Hugging Face Inference Providers",
        "category": "aggregator",
        "base_url": "https://router.huggingface.co",
        "submit_path": "/fal-ai/{model}",
        "status_path": "/fal-ai/{model}/requests/{job_id}/status",
        "api_key_env": "HF_TOKEN",
        "auth_header": "Authorization",
        "auth_prefix": "Bearer ",
    },
    "google_flow": {
        "name": "Google Veo (Gemini/Vertex API)",
        "category": "native_api",
        "base_url": "https://generativelanguage.googleapis.com",
        "submit_path": "/v1beta/models/{model}:predictLongRunning",
        "status_path": "/v1beta/{job_id}",
        "api_key_env": "GOOGLE_API_KEY",
        "auth_header": "x-goog-api-key",
        "auth_prefix": "",
    },
    # Providers with no confirmed video API: key can still be stored, adapter inert.
    "pika": {"name": "Pika", "category": "native_api", "api_key_env": "PIKA_API_KEY"},
    "stability": {"name": "Stability AI", "category": "open_source_gpu", "api_key_env": "STABILITY_API_KEY"},
    "openrouter": {"name": "OpenRouter", "category": "aggregator", "api_key_env": "OPENROUTER_API_KEY"},
}

# Providers that are genuinely free / self-host (no paid key). These are always
# usable and never require an API key.
FREE_PROVIDER_IDS = {"manual_assisted", "local_diffusion", "comfyui", "huggingface_spaces"}


def map_http_error(status_code: int, body: str, *, provider: str) -> NormalizedError:
    """Translate an HTTP failure into a normalized, provider-independent error."""
    text = (body or "")[:500]
    if status_code in (401, 403):
        code = ErrorCode.AUTHENTICATION_FAILED
    elif status_code == 402:
        code = ErrorCode.CREDITS_EXHAUSTED
    elif status_code == 429:
        code = ErrorCode.RATE_LIMITED
    elif status_code == 404:
        code = ErrorCode.MODEL_UNAVAILABLE
    elif status_code == 400:
        code = ErrorCode.UNSUPPORTED_REQUEST
    elif 500 <= status_code < 600:
        code = ErrorCode.PROVIDER_UNAVAILABLE
    else:
        code = ErrorCode.GENERATION_FAILED
    return NormalizedError(code, f"HTTP {status_code}: {text}", provider=provider)


class CatalogBackedProvider(VideoProvider):
    """Base for real adapters: identity and capabilities come from the catalog."""

    provider_id = "catalog"
    is_mock = False

    def __init__(self, *, catalog_path: str | Path | None = None) -> None:
        self._catalog_path = catalog_path or DEFAULT_CATALOG_PATH
        self._entry = self._load_entry()

    def _load_entry(self):
        for entry in load_entries(self._catalog_path):
            if entry.provider_id == self.provider_id:
                return entry
        raise NormalizedError(
            ErrorCode.MODEL_UNAVAILABLE,
            f"{self.provider_id} is absent from the catalog; refusing to invent facts",
            provider=self.provider_id,
        )

    @property
    def entry(self):
        return self._entry

    async def get_capabilities(self) -> ProviderCapabilities:
        # Straight from the catalog — unknown stays unknown.
        return self._entry.capabilities()

    async def estimate_cost(self, request: ProviderRequest) -> CostEstimate:
        cps = self._entry.cost_per_second
        if cps is None:
            # Cost unknown: report zero credits but mark it as paid-unknown so the
            # selector never treats an unmeasured provider as free.
            return CostEstimate(credits=0.0, is_paid=True, note="cost unknown")
        return CostEstimate(
            credits=round(cps * request.duration_seconds, 6),
            is_paid=cps > 0,
            currency="USD",
            note=self._entry.api_pricing,
        )

    async def get_quota(self) -> QuotaInfo:
        # The catalog does not expose a live balance; balance is managed by the
        # ledger. Unknown is never reported as unlimited.
        return QuotaInfo(balance_source="unknown", available_credits=None)


class AsyncTaskHTTPProvider(CatalogBackedProvider):
    """Adapter for providers that expose a submit/poll/download task API.

    Subclasses supply the endpoint paths, payload mapping and status mapping.
    """

    base_url: str = ""
    submit_path: str = ""
    status_path: str = ""  # formatted with {job_id}
    result_path: str = ""  # optional; formatted with {model}/{job_id}
    api_key_env: str = ""
    auth_header: str = "Authorization"
    auth_prefix: str = "Bearer "
    extra_headers: dict = {}
    model_template: str = ""
    _last_model: str = ""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        catalog_path: str | Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
        timeout: float = 30.0,
    ) -> None:
        super().__init__(catalog_path=catalog_path)
        # No network at construction; a client is built but no request is sent.
        # Key resolution order: explicit arg -> env var -> unified key store.
        if api_key is not None:
            self._api_key = api_key
        else:
            self._api_key = default_key_store().resolve(self.provider_id, self.api_key_env)
        self._client = httpx.AsyncClient(
            base_url=self.base_url, transport=transport, timeout=timeout
        )
        for key, value in self.extra_headers.items():
            self._client.headers[key] = value
        # Remembered so a later poll can rebuild the per-model status path.
        self._last_model = self._entry.model_name or ""

    # --- credentials -----------------------------------------------------------
    def _headers(self) -> dict:
        if not self._api_key:
            raise NormalizedError(
                ErrorCode.AUTHENTICATION_FAILED,
                f"no credential configured (enter it in the key store or set {self.api_key_env})",
                provider=self.provider_id,
            )
        if not self.auth_header:  # e.g. key passed as a query param
            return {}
        return {self.auth_header: f"{self.auth_prefix}{self._api_key}"}

    def has_credentials(self) -> bool:
        return bool(self._api_key)

    async def check_health(self) -> bool:
        # Reachability cannot be asserted without a network call; report health as
        # "credentials present". A missing credential is unhealthy.
        return self.has_credentials()

    # --- adapter hooks ---------------------------------------------------------
    def build_payload(self, request: ProviderRequest) -> dict:  # pragma: no cover
        raise NotImplementedError

    def map_status(self, payload: dict) -> JobStatus:  # pragma: no cover
        raise NotImplementedError

    # --- request helpers -------------------------------------------------------
    def _model_name(self, request: ProviderRequest) -> str:
        return request.extra.get("model") or self._entry.model_name or ""

    def _format_path(self, template: str, *, job_id: str = "", model: str = "") -> str:
        return template.format(job_id=job_id, model=model)

    def _auth_params(self) -> dict:
        # When no auth header is used, the key travels as a query parameter.
        if self.auth_header:
            return {}
        return {"key": self._api_key} if self._api_key else {}

    # --- task API --------------------------------------------------------------
    async def submit_generation(self, request: ProviderRequest) -> str:
        path = self._format_path(self.submit_path, model=self._model_name(request))
        try:
            resp = await self._client.post(
                path,
                json=self.build_payload(request),
                headers=self._headers(),
                params=self._auth_params() or None,
            )
        except httpx.TimeoutException as exc:
            raise NormalizedError(
                ErrorCode.GENERATION_TIMEOUT, str(exc), provider=self.provider_id
            ) from exc
        except httpx.HTTPError as exc:
            raise NormalizedError(
                ErrorCode.PROVIDER_UNAVAILABLE, str(exc), provider=self.provider_id
            ) from exc
        if resp.status_code >= 400:
            raise map_http_error(resp.status_code, resp.text, provider=self.provider_id)
        data = resp.json()
        job_id = data.get("id") or data.get("task_id") or data.get("job_id") or data.get("request_id")
        if not job_id:
            raise NormalizedError(
                ErrorCode.GENERATION_FAILED,
                "provider response had no job id",
                provider=self.provider_id,
            )
        return str(job_id)

    async def get_job_status(self, job_id: str) -> JobStatus:
        path = self._format_path(self.status_path, job_id=job_id, model=self._last_model)
        try:
            resp = await self._client.get(
                path, headers=self._headers(), params=self._auth_params() or None
            )
        except httpx.HTTPError as exc:
            raise NormalizedError(
                ErrorCode.AMBIGUOUS_JOB_STATE, str(exc), provider=self.provider_id
            ) from exc
        if resp.status_code >= 400:
            raise map_http_error(resp.status_code, resp.text, provider=self.provider_id)
        return self.map_status(resp.json())

    async def download_result(self, job_id: str) -> ProviderResult:
        status = await self.get_job_status(job_id)
        if status.state != JobState.SUCCEEDED:
            raise NormalizedError(
                ErrorCode.GENERATION_FAILED,
                f"cannot download job in state {status.state.value}",
                provider=self.provider_id,
            )
        # The result URL is carried in the status message by map_status.
        url = status.message or ""
        if not url:
            raise NormalizedError(
                ErrorCode.DOWNLOAD_FAILED, "no result URL in status", provider=self.provider_id
            )
        return ProviderResult(job_id=job_id, clip_uri=url, is_mock=False)

    async def cancel_job(self, job_id: str) -> None:
        # Best-effort; providers differ in cancellation support.
        return None

    async def normalize_error(self, error: Exception) -> NormalizedError:
        if isinstance(error, NormalizedError):
            return error
        return NormalizedError(
            ErrorCode.GENERATION_FAILED, str(error), provider=self.provider_id
        )

    async def aclose(self) -> None:
        await self._client.aclose()


class RunwayProvider(AsyncTaskHTTPProvider):
    """Runway Gen-4 / Gen-3 async task API.

    Endpoint paths follow the documented async-task pattern (submit then poll
    ``/v1/tasks/{id}``). Confirm against official docs before enabling.
    """

    provider_id = "runway"
    name = "Runway"
    category = "native_api"
    base_url = "https://api.dev.runwayml.com"
    submit_path = "/v1/image_to_video"
    status_path = "/v1/tasks/{job_id}"
    api_key_env = "RUNWAY_API_KEY"

    def __init__(self, **kwargs) -> None:
        super().__init__(**kwargs)
        self._client.headers["X-Runway-Version"] = "2024-11-06"

    def build_payload(self, request: ProviderRequest) -> dict:
        model = self._entry.model_name or "gen4_turbo"
        payload: dict = {
            "model": model,
            "promptText": request.prompt,
            "duration": int(request.duration_seconds),
            "ratio": request.aspect_ratio.replace(":", ":"),
        }
        if request.negative_prompt:
            payload["promptText"] = f"{request.prompt}\nAvoid: {request.negative_prompt}"
        if request.previous_frame_uri:
            payload["promptImage"] = request.previous_frame_uri
        return payload

    def map_status(self, payload: dict) -> JobStatus:
        state = str(payload.get("status", "")).lower()
        mapping = {
            "pending": JobState.PENDING,
            "throttled": JobState.PENDING,
            "running": JobState.RUNNING,
            "succeeded": JobState.SUCCEEDED,
            "failed": JobState.FAILED,
            "cancelled": JobState.CANCELLED,
        }
        job_state = mapping.get(state, JobState.AMBIGUOUS)
        output = payload.get("output") or []
        url = output[0] if isinstance(output, list) and output else ""
        failure = payload.get("failure") or payload.get("failureCode") or ""
        return JobStatus(
            job_id=str(payload.get("id", "")),
            state=job_state,
            error_code=ErrorCode.GENERATION_FAILED.value if job_state == JobState.FAILED else None,
            message=url or (str(failure) if failure else ""),
        )


class ReplicateProvider(AsyncTaskHTTPProvider):
    """Replicate predictions API (submit a model version, poll, download).

    Replicate is an aggregator; the model is chosen per request. Endpoint paths
    follow the documented predictions API and require confirmation.
    """

    provider_id = "replicate"
    name = "Replicate"
    category = "aggregator"
    base_url = "https://api.replicate.com"
    submit_path = "/v1/predictions"
    status_path = "/v1/predictions/{job_id}"
    api_key_env = "REPLICATE_API_TOKEN"

    def build_payload(self, request: ProviderRequest) -> dict:
        model = request.extra.get("model") or self._entry.model_name or ""
        payload: dict = {
            "input": {
                "prompt": request.prompt,
                "duration": int(request.duration_seconds),
                "aspect_ratio": request.aspect_ratio,
            }
        }
        if request.negative_prompt:
            payload["input"]["negative_prompt"] = request.negative_prompt
        if request.previous_frame_uri:
            payload["input"]["image"] = request.previous_frame_uri
        if model and "/" in model:
            payload["version"] = model
        return payload

    def map_status(self, payload: dict) -> JobStatus:
        state = str(payload.get("status", "")).lower()
        mapping = {
            "starting": JobState.PENDING,
            "processing": JobState.RUNNING,
            "succeeded": JobState.SUCCEEDED,
            "failed": JobState.FAILED,
            "canceled": JobState.CANCELLED,
        }
        job_state = mapping.get(state, JobState.AMBIGUOUS)
        output = payload.get("output")
        if isinstance(output, list):
            url = output[0] if output else ""
        else:
            url = output or ""
        return JobStatus(
            job_id=str(payload.get("id", "")),
            state=job_state,
            error_code=ErrorCode.GENERATION_FAILED.value if job_state == JobState.FAILED else None,
            message=str(url or payload.get("error") or ""),
        )


def _first_url(value: object) -> str:
    """Return the first http(s) URL found anywhere in a nested payload."""
    if isinstance(value, str):
        return value if value.startswith("http") else ""
    if isinstance(value, list):
        for item in value:
            found = _first_url(item)
            if found:
                return found
    if isinstance(value, dict):
        for item in value.values():
            found = _first_url(item)
            if found:
                return found
    return ""


_GENERIC_STATUS_MAP = {
    "pending": JobState.PENDING,
    "queued": JobState.PENDING,
    "starting": JobState.PENDING,
    "created": JobState.PENDING,
    "running": JobState.RUNNING,
    "processing": JobState.RUNNING,
    "in_progress": JobState.RUNNING,
    "succeeded": JobState.SUCCEEDED,
    "completed": JobState.SUCCEEDED,
    "success": JobState.SUCCEEDED,
    "failed": JobState.FAILED,
    "error": JobState.FAILED,
    "canceled": JobState.CANCELLED,
    "cancelled": JobState.CANCELLED,
}


class GenericCatalogProvider(AsyncTaskHTTPProvider):
    """Catalog-driven adapter for every provider in ``PROVIDER_REGISTRY``.

    Endpoints, auth scheme and env var come from the registry table; capabilities
    and pricing come from the catalog. A provider with no confirmed endpoints
    (e.g. Pika, OpenRouter) stays inert — it can hold a stored key but reports
    itself unhealthy and refuses to run until its API is documented.
    """

    is_mock = False

    def __init__(
        self,
        provider_id: str,
        *,
        api_key: str | None = None,
        catalog_path: str | Path | None = None,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        meta = PROVIDER_REGISTRY.get(provider_id)
        if meta is None:
            raise NormalizedError(
                ErrorCode.MODEL_UNAVAILABLE,
                f"no adapter metadata for {provider_id}",
                provider=provider_id,
            )
        self.provider_id = provider_id
        self.name = meta.get("name", provider_id)
        self.category = meta.get("category", "native_api")
        self.base_url = meta.get("base_url", "")
        self.submit_path = meta.get("submit_path", "")
        self.status_path = meta.get("status_path", "")
        self.result_path = meta.get("result_path", "")
        self.api_key_env = meta.get("api_key_env", "")
        self.auth_header = meta.get("auth_header", "Authorization")
        self.auth_prefix = meta.get("auth_prefix", "Bearer ")
        self.extra_headers = dict(meta.get("extra_headers", {}))
        # base_url may be empty for undocumented providers; httpx accepts "".
        super().__init__(api_key=api_key, catalog_path=catalog_path, transport=transport)

    def has_endpoints(self) -> bool:
        return bool(self.base_url and self.submit_path and self.status_path)

    async def check_health(self) -> bool:
        # Both a key AND confirmed endpoints are required to be usable.
        return self.has_credentials() and self.has_endpoints()

    def build_payload(self, request: ProviderRequest) -> dict:
        payload: dict = {
            "prompt": request.prompt,
            "duration": int(request.duration_seconds),
            "aspect_ratio": request.aspect_ratio,
        }
        if request.negative_prompt:
            payload["negative_prompt"] = request.negative_prompt
        if request.previous_frame_uri:
            payload["image_url"] = request.previous_frame_uri
        return payload

    def map_status(self, payload: dict) -> JobStatus:
        state = str(payload.get("status") or payload.get("state") or "").lower()
        job_state = _GENERIC_STATUS_MAP.get(state, JobState.AMBIGUOUS)
        url = _first_url(payload.get("output")) or _first_url(payload.get("data")) or _first_url(payload)
        job_id = payload.get("id") or payload.get("request_id") or payload.get("task_id") or ""
        return JobStatus(
            job_id=str(job_id),
            state=job_state,
            error_code=ErrorCode.GENERATION_FAILED.value if job_state == JobState.FAILED else None,
            message=url or str(payload.get("error") or ""),
        )


def build_default_registry(
    *,
    catalog_path: str | Path | None = None,
    work_dir: str | Path | None = None,
    transport: httpx.AsyncBaseTransport | None = None,
) -> "ProviderRegistry":
    """Construct the default registry: labelled mock + all catalog adapters.

    Real adapters are registered even without keys (so keys can be added later
    through the key store); they simply report themselves unhealthy until a key
    is present. The mock is always registered so the harness runs end-to-end.
    """
    from app.providers.mock import MockProvider
    from app.providers.registry import ProviderRegistry

    # Providers with a bespoke payload/status mapping use their dedicated class.
    specialized = {
        "runway": RunwayProvider,
        "replicate": ReplicateProvider,
    }
    registry = ProviderRegistry()
    registry.register(MockProvider("mock_a"))
    for provider_id in PROVIDER_REGISTRY:
        cls = specialized.get(provider_id)
        if cls is not None:
            registry.register(
                cls(catalog_path=catalog_path, transport=transport)  # type: ignore[call-arg]
            )
        else:
            registry.register(
                GenericCatalogProvider(
                    provider_id, catalog_path=catalog_path, transport=transport
                )
            )
    registry.register(
        ManualAssistedProvider(
            work_dir=work_dir or "./.vgoh_manual", catalog_path=catalog_path
        )
    )
    return registry


class ManualAssistedProvider(VideoProvider):
    """Manual-assisted consumer interface (sections 4, 20).

    Fully functional and network-free: the harness writes a *generation brief*
    (prompt + references + target spec) into a watched directory. A human runs
    the prompt in whatever consumer UI they have access to and drops the result
    clip back into the import directory named after the job. The adapter then
    reports the job as succeeded and serves the imported file.

    This is real, testable behaviour — no scraping, no automation of a consumer
    UI, no fake results. Until a clip is imported, the job is legitimately
    ``RUNNING`` (awaiting human action).
    """

    provider_id = "manual_assisted"
    name = "Manual-Assisted Consumer Interface"
    category = "manual_assisted"
    is_mock = False

    def __init__(
        self,
        *,
        work_dir: str | Path = "./.vgoh_manual",
        catalog_path: str | Path | None = None,
    ) -> None:
        self.work_dir = Path(work_dir)
        self.requests_dir = self.work_dir / "requests"
        self.imports_dir = self.work_dir / "imports"
        self._catalog_path = catalog_path or DEFAULT_CATALOG_PATH
        self._entry = None
        for entry in load_entries(self._catalog_path):
            if entry.provider_id == self.provider_id:
                self._entry = entry
                break
        self._jobs: dict[str, ProviderRequest] = {}

    async def get_capabilities(self) -> ProviderCapabilities:
        if self._entry is not None:
            return self._entry.capabilities()
        # Manual-assisted with no catalog entry: declare nothing, assume nothing.
        return ProviderCapabilities(model_name="manual")

    async def check_health(self) -> bool:
        # Always available: it depends only on a human and the local filesystem.
        return True

    async def get_quota(self) -> QuotaInfo:
        # No harness credits are consumed; the human's own account applies.
        # Unknown balance is never reported as unlimited.
        return QuotaInfo(balance_source="unknown", available_credits=None)

    async def estimate_cost(self, request: ProviderRequest) -> CostEstimate:
        return CostEstimate(credits=0.0, is_paid=False, currency="manual",
                            note="no harness credit; human-operated")

    async def submit_generation(self, request: ProviderRequest) -> str:
        job_id = f"manual-{uuid.uuid4().hex[:12]}"
        self._jobs[job_id] = request
        self.requests_dir.mkdir(parents=True, exist_ok=True)
        brief = {
            "job_id": job_id,
            "project_id": request.project_id,
            "scene_id": request.scene_id,
            "prompt": request.prompt,
            "negative_prompt": request.negative_prompt,
            "duration_seconds": request.duration_seconds,
            "aspect_ratio": request.aspect_ratio,
            "resolution": request.resolution,
            "references": [r.model_dump() for r in request.references],
            "previous_frame_uri": request.previous_frame_uri,
            "instructions": (
                "Run this prompt in your consumer UI, then place the resulting "
                f"clip at imports/{job_id}.mp4 to continue."
            ),
        }
        (self.requests_dir / f"{job_id}.json").write_text(
            json.dumps(brief, indent=2), encoding="utf-8"
        )
        return job_id

    def _imported_clip(self, job_id: str) -> Path | None:
        for suffix in (".mp4", ".mov", ".webm", ".mkv"):
            candidate = self.imports_dir / f"{job_id}{suffix}"
            if candidate.exists():
                return candidate
        return None

    async def get_job_status(self, job_id: str) -> JobStatus:
        clip = self._imported_clip(job_id)
        if clip is not None:
            return JobStatus(job_id=job_id, state=JobState.SUCCEEDED, message=str(clip))
        # Not yet imported: the job is genuinely still in progress (awaiting a human).
        return JobStatus(job_id=job_id, state=JobState.RUNNING, message="awaiting import")

    async def download_result(self, job_id: str) -> ProviderResult:
        clip = self._imported_clip(job_id)
        if clip is None:
            raise NormalizedError(
                ErrorCode.DOWNLOAD_FAILED,
                "no imported clip for this manual job yet",
                provider=self.provider_id,
            )
        request = self._jobs.get(job_id)
        return ProviderResult(
            job_id=job_id,
            clip_uri=str(clip),
            duration_seconds=request.duration_seconds if request is not None else 0.0,
            is_mock=False,
        )

    async def cancel_job(self, job_id: str) -> None:
        self._jobs.pop(job_id, None)

    async def normalize_error(self, error: Exception) -> NormalizedError:
        if isinstance(error, NormalizedError):
            return error
        return NormalizedError(
            ErrorCode.GENERATION_FAILED, str(error), provider=self.provider_id
        )
