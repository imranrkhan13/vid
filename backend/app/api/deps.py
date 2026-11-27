"""FastAPI dependencies: DB session, registry, orchestrator wiring."""

from __future__ import annotations

from collections.abc import Iterator

from sqlalchemy.orm import Session

from app.database.base import create_all, get_session
from app.providers.real import build_default_registry
from app.providers.registry import ProviderRegistry
from app.services.projects import ProjectService

# A process-wide registry seeded with the labelled mock and every catalog
# adapter. Real adapters are inert until a key is entered in the key store.
# Tests may replace this registry.
_REGISTRY: ProviderRegistry = build_default_registry()


def get_registry() -> ProviderRegistry:
    return _REGISTRY


def db_session() -> Iterator[Session]:
    create_all()
    session = get_session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_service(session: Session) -> ProjectService:
    return ProjectService(session, _REGISTRY)
