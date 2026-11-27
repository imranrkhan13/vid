"""Normalized error taxonomy (README section 19).

Every provider failure is translated into one of these codes so the orchestrator
can make provider-independent decisions (retry, switch, pause, escalate).
"""

from __future__ import annotations

from enum import Enum


class ErrorCode(str, Enum):
    QUOTA_EXHAUSTED = "QUOTA_EXHAUSTED"
    CREDITS_EXHAUSTED = "CREDITS_EXHAUSTED"
    RATE_LIMITED = "RATE_LIMITED"
    AUTHENTICATION_FAILED = "AUTHENTICATION_FAILED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    MODEL_UNAVAILABLE = "MODEL_UNAVAILABLE"
    UNSUPPORTED_REQUEST = "UNSUPPORTED_REQUEST"
    GENERATION_TIMEOUT = "GENERATION_TIMEOUT"
    AMBIGUOUS_JOB_STATE = "AMBIGUOUS_JOB_STATE"
    GENERATION_FAILED = "GENERATION_FAILED"
    CONTENT_POLICY_REJECTION = "CONTENT_POLICY_REJECTION"
    QUALITY_VALIDATION_FAILED = "QUALITY_VALIDATION_FAILED"
    DOWNLOAD_FAILED = "DOWNLOAD_FAILED"
    GPU_OUT_OF_MEMORY = "GPU_OUT_OF_MEMORY"


# Errors after which it is safe/meaningful to try the *same* provider again
# (transient), versus errors that mean this provider cannot serve this request.
RETRYABLE: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.RATE_LIMITED,
        ErrorCode.PROVIDER_UNAVAILABLE,
        ErrorCode.MODEL_UNAVAILABLE,
        ErrorCode.GENERATION_TIMEOUT,
        ErrorCode.AMBIGUOUS_JOB_STATE,
        ErrorCode.GENERATION_FAILED,
        ErrorCode.DOWNLOAD_FAILED,
        ErrorCode.GPU_OUT_OF_MEMORY,
    }
)

# Errors that must cause a switch to another provider rather than a retry.
SWITCH_PROVIDER: frozenset[ErrorCode] = frozenset(
    {
        ErrorCode.QUOTA_EXHAUSTED,
        ErrorCode.CREDITS_EXHAUSTED,
        ErrorCode.AUTHENTICATION_FAILED,
        ErrorCode.UNSUPPORTED_REQUEST,
        ErrorCode.CONTENT_POLICY_REJECTION,
    }
)

# Errors that require a human decision — never auto-retried, never re-routed to
# evade safety (section 19: policy rejections are terminal for automation).
TERMINAL_FOR_AUTOMATION: frozenset[ErrorCode] = frozenset(
    {ErrorCode.CONTENT_POLICY_REJECTION}
)


class NormalizedError(Exception):
    """A provider-agnostic error carrying a stable code and metadata."""

    def __init__(
        self,
        code: ErrorCode,
        message: str = "",
        *,
        provider: str | None = None,
        retry_after_seconds: float | None = None,
        detail: dict | None = None,
    ) -> None:
        self.code = code
        self.message = message or code.value
        self.provider = provider
        self.retry_after_seconds = retry_after_seconds
        self.detail = detail or {}
        super().__init__(f"[{code.value}] {self.message}")

    @property
    def retryable(self) -> bool:
        return self.code in RETRYABLE

    @property
    def requires_switch(self) -> bool:
        return self.code in SWITCH_PROVIDER

    def to_dict(self) -> dict:
        return {
            "code": self.code.value,
            "message": self.message,
            "provider": self.provider,
            "retry_after_seconds": self.retry_after_seconds,
            "detail": self.detail,
        }

    def __repr__(self) -> str:  # pragma: no cover - debug helper
        return f"NormalizedError(code={self.code.value!r}, provider={self.provider!r})"
