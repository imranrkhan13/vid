from __future__ import annotations
from sqlalchemy.orm import Session

from app.core.security import decrypt_secret
from app.database.models import GenerationProvider, ProviderCredential
from .base import ProviderContext, VideoProvider
from .custom_http import CustomHttpProvider
from .manual import ManualProvider
from .mock import MockProvider, VARIANTS

# Catalog seeds. `verification` records what is actually known; nothing here asserts capabilities,
# prices or credits for third parties. Research results are added in docs/PROVIDER_RESEARCH.md and
# data/provider_catalog.json (see providers/catalog_data.py) with a verification date and source URL.
CORE_PROVIDERS = [
    dict(id="mock_a", name="MOCK Provider A", category="mock", implemented=True, verification="simulation", host_family="mock_a",
         notes="Deterministic simulation for testing orchestration. Not AI generation."),
    dict(id="mock_b", name="MOCK Provider B", category="mock", implemented=True, verification="simulation", host_family="mock_b",
         notes="Deterministic simulation for testing orchestration. Not AI generation."),
    dict(id="mock_c", name="MOCK Provider C", category="mock", implemented=True, verification="simulation", host_family="mock_c",
         notes="Deterministic simulation (lower resolution, no reference conditioning)."),
    dict(id="manual", name="Manual import", category="manual", implemented=True, verification="n/a", host_family="manual",
         notes="Platform builds prompts and references; the user generates in an official interface and imports the clip."),
    dict(id="custom_http", name="Custom HTTP endpoint", category="custom", implemented=True, verification="contract-tested",
         host_family="custom", notes="Any private endpoint implementing docs/PROVIDER_CONTRACT.md."),
]


def build_provider(provider_id: str, ctx: ProviderContext) -> VideoProvider:
    from .base import register_secret
    register_secret(getattr(ctx, "secret", None))
    if provider_id in VARIANTS:
        return MockProvider(ctx, provider_id)
    if provider_id == "manual":
        return ManualProvider(ctx)
    if provider_id == "custom_http":
        return CustomHttpProvider(ctx)
    from .catalog_adapters import build_real_provider  # real native adapters
    p = build_real_provider(provider_id, ctx)
    if p is None:
        raise ValueError(f"provider {provider_id} has no adapter")
    return p


def context_for(cred: ProviderCredential) -> ProviderContext:
    secret = None
    if cred.secret_enc:
        secret = decrypt_secret(cred.secret_enc, f"{cred.user_id}|{cred.provider_id}|{cred.id}")
    from .base import register_secret
    register_secret(secret)
    return ProviderContext(user_id=cred.user_id, credential_id=cred.id, provider_id=cred.provider_id, config=dict(cred.config or {}), secret=secret)


def seed_catalog(db: Session):
    from .catalog_data import CATALOG
    existing = {p.id: p for p in db.query(GenerationProvider).all()}
    for row in CORE_PROVIDERS + CATALOG:
        p = existing.get(row["id"])
        if p is None:
            db.add(GenerationProvider(**row))
        else:
            for k, v in row.items():
                setattr(p, k, v)
    db.commit()
