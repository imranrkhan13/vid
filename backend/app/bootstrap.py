"""Idempotent startup bootstrap: seed provider rows and free-path quotas.

The registry already holds every adapter (mock + real, inert until keyed). This
module makes the *database* agree with it so a freshly served app can run the
mock end-to-end without manual setup, while real providers stay unselectable
until a credential **and** a known balance exist.

Rules (deliberately conservative):
  * Every registered provider gets a ``provider_models`` row (idempotent merge).
  * The labelled mock gets an estimated free quota so the harness runs today.
  * Free paths (manual-assisted, self-host) are registered with an *unknown*
    balance — never assumed unlimited — and cost zero, so they are selectable.
  * Real paid providers get **no** quota here: unknown balance is never treated
    as sufficient, and paid spend is off by default.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from app.providers.real import FREE_PROVIDER_IDS
from app.providers.registry import ProviderRegistry
from app.services.projects import ProjectService


def seed_defaults(session: Session, registry: ProviderRegistry) -> None:
    service = ProjectService(session, registry)
    for provider in registry.all():
        service.register_provider(provider)

        if provider.is_mock:
            # The mock is free and always available: give it a working balance so
            # the served app can complete a project with no external setup.
            service.set_quota(
                provider.provider_id,
                available_credits=1000.0,
                balance_source="estimated",
                model_name=f"{provider.provider_id}-model",
                period="none",
            )
        elif provider.provider_id in FREE_PROVIDER_IDS:
            # Free / self-hosted: selectable with an unknown balance and no cost.
            service.set_quota(
                provider.provider_id,
                available_credits=None,
                balance_source="unknown",
                model_name="",
                period="none",
            )
    session.flush()
