"""The provider adapter interface (README section 10).

Every provider — native API, open-source GPU worker, manual-assisted or custom —
implements this interface. Adapters translate canonical requests into provider
payloads; provider formatting stays out of the scheduler.
"""

from __future__ import annotations

import abc

from app.core.errors import NormalizedError
from app.schemas.provider import (
    CostEstimate,
    ProviderCapabilities,
    ProviderRequest,
    ProviderResult,
    JobStatus,
    QuotaInfo,
)


class VideoProvider(abc.ABC):
    """Abstract base class for all video generation providers."""

    #: Stable identifier used throughout the system.
    provider_id: str = "abstract"
    #: Human-readable name.
    name: str = "Abstract Provider"
    #: Integration category (section 4).
    category: str = "native_api"
    #: Mock providers must set this True and be clearly labelled (section 33).
    is_mock: bool = False

    @abc.abstractmethod
    async def get_capabilities(self) -> ProviderCapabilities:
        """Return the provider's declared capabilities.

        Unknown facts must be ``None``, never assumed True (section 3).
        """
        raise NotImplementedError

    @abc.abstractmethod
    async def check_health(self) -> bool:
        """Return True if the provider is currently reachable and usable."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_quota(self) -> QuotaInfo:
        """Return current credit/quota state. Unknown is not unlimited."""
        raise NotImplementedError

    @abc.abstractmethod
    async def estimate_cost(self, request: ProviderRequest) -> CostEstimate:
        """Estimate cost for a request before submission (section 27)."""
        raise NotImplementedError

    @abc.abstractmethod
    async def submit_generation(self, request: ProviderRequest) -> str:
        """Submit a generation job and return a durable provider job id."""
        raise NotImplementedError

    @abc.abstractmethod
    async def get_job_status(self, job_id: str) -> JobStatus:
        """Poll job status. Must reconcile ambiguous states before retrying."""
        raise NotImplementedError

    @abc.abstractmethod
    async def download_result(self, job_id: str) -> ProviderResult:
        """Download a completed result into managed storage."""
        raise NotImplementedError

    @abc.abstractmethod
    async def cancel_job(self, job_id: str) -> None:
        """Request cancellation of a job (best effort)."""
        raise NotImplementedError

    @abc.abstractmethod
    async def normalize_error(self, error: Exception) -> NormalizedError:
        """Translate a provider-specific error into a normalized error."""
        raise NotImplementedError
