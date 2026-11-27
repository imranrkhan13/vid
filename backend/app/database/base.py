"""SQLAlchemy declarative base and engine/session factory.

Uses SQLAlchemy 2.x typed mappings. The default engine is SQLite in-memory for
local/test execution; a PostgreSQL URL can be supplied via ``VGOH_DATABASE_URL``.
"""

from __future__ import annotations

from sqlalchemy import create_engine
from sqlalchemy.orm import DeclarativeBase, sessionmaker
from sqlalchemy.pool import StaticPool

from app.core.config import Settings


class Base(DeclarativeBase):
    pass


_engine = None
_SessionLocal: sessionmaker | None = None


def configure(database_url: str, *, echo: bool = False) -> None:
    """(Re)configure the global engine and session factory."""
    global _engine, _SessionLocal
    kwargs: dict = {"echo": echo, "future": True}
    if database_url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            # Share one in-memory database across all sessions/connections.
            kwargs["poolclass"] = StaticPool
    _engine = create_engine(database_url, **kwargs)
    _SessionLocal = sessionmaker(bind=_engine, expire_on_commit=False, future=True)


def configure_from_settings(settings: Settings) -> None:
    configure(settings.database_url)


def get_session():
    """Return a new session bound to the configured engine."""
    if _SessionLocal is None:
        configure_from_settings(Settings.from_env())
    assert _SessionLocal is not None
    return _SessionLocal()


def engine():
    if _engine is None:
        configure_from_settings(Settings.from_env())
    return _engine


def create_all() -> None:
    # Import models so they register on the metadata before creating tables.
    from app import models  # noqa: F401

    Base.metadata.create_all(bind=engine())


def reset() -> None:
    """Drop and recreate all tables (test helper)."""
    from app import models  # noqa: F401

    Base.metadata.drop_all(bind=engine())
    Base.metadata.create_all(bind=engine())
