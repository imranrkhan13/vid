"""Provider adapters, isolated from core orchestration (section 29)."""

from app.providers.base import VideoProvider
from app.providers.mock import MockProvider

__all__ = ["VideoProvider", "MockProvider"]
