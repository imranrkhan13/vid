from __future__ import annotations
from .base import ProviderContext, VideoProvider


def build_real_provider(provider_id: str, ctx: ProviderContext) -> VideoProvider | None:
    if provider_id == "pollinations":
        from .pollinations import PollinationsProvider
        return PollinationsProvider(ctx)
    if provider_id == "replicate":
        from .replicate import ReplicateProvider
        return ReplicateProvider(ctx)
    if provider_id == "hf_space_ltx":
        from .hf_space_ltx import HFSpaceLTXProvider
        return HFSpaceLTXProvider(ctx)
    return None
