from __future__ import annotations
from .base import ProviderContext, VideoProvider


def build_real_provider(provider_id: str, ctx: ProviderContext) -> VideoProvider | None:
    return None
