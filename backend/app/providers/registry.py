"""Provider registry — resolves enabled adapters by id.

The registry holds live ``VideoProvider`` instances. Real adapters are
registered even without credentials (so a key can be added later through the key
store) but report themselves unhealthy until one is present; the mock provider is
always clearly labelled.
"""

from __future__ import annotations

from app.providers.base import VideoProvider


class ProviderRegistry:
    def __init__(self) -> None:
        self._providers: dict[str, VideoProvider] = {}

    def register(self, provider: VideoProvider) -> None:
        self._providers[provider.provider_id] = provider

    def unregister(self, provider_id: str) -> None:
        self._providers.pop(provider_id, None)

    def get(self, provider_id: str) -> VideoProvider | None:
        return self._providers.get(provider_id)

    def all(self) -> list[VideoProvider]:
        return list(self._providers.values())

    def ids(self) -> list[str]:
        return list(self._providers.keys())
