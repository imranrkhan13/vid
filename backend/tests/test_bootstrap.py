"""Startup bootstrap: a served app can run the mock with no manual setup."""

from __future__ import annotations

from sqlalchemy import select

from app.bootstrap import seed_defaults
from app.database.base import configure, create_all, get_session
from app.models.providers import ProviderModel, ProviderQuota
from app.providers.real import build_default_registry


def _fresh_session():
    configure("sqlite+pysqlite:///:memory:")
    create_all()
    return get_session()


def test_seed_defaults_registers_every_adapter():
    session = _fresh_session()
    registry = build_default_registry()
    seed_defaults(session, registry)
    session.commit()

    rows = {r.id for r in session.execute(select(ProviderModel)).scalars()}
    assert "mock_a" in rows           # labelled mock
    assert "runway" in rows           # real adapter row (inert until keyed)
    assert "manual_assisted" in rows  # free path


def test_seed_defaults_gives_mock_a_working_balance_only():
    session = _fresh_session()
    registry = build_default_registry()
    seed_defaults(session, registry)
    session.commit()

    quotas = {q.provider_id: q for q in session.execute(select(ProviderQuota)).scalars()}
    # Mock is usable today.
    assert quotas["mock_a"].available_credits == 1000.0
    # A paid real provider is NOT given a balance: unknown never means sufficient.
    assert "runway" not in quotas


def test_seed_defaults_is_idempotent():
    session = _fresh_session()
    registry = build_default_registry()
    seed_defaults(session, registry)
    seed_defaults(session, registry)  # second call must not raise or duplicate
    session.commit()
    mock_quotas = session.execute(
        select(ProviderQuota).where(ProviderQuota.provider_id == "mock_a")
    ).scalars().all()
    assert len(mock_quotas) == 1
