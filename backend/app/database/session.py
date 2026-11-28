from __future__ import annotations
from contextlib import contextmanager
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker, Session

from app.core.config import settings
from .models import Base

_engine = None
_Session = None


def get_engine():
    global _engine, _Session
    if _engine is None:
        url = settings.database_url
        kw = {}
        if url.startswith("sqlite"):
            kw["connect_args"] = {"check_same_thread": False, "timeout": 30}
        else:
            kw["pool_pre_ping"] = True
        _engine = create_engine(url, future=True, **kw)
        if url.startswith("sqlite"):
            @event.listens_for(_engine, "connect")
            def _pragma(dbapi_con, _):
                cur = dbapi_con.cursor()
                cur.execute("PRAGMA journal_mode=WAL")
                cur.execute("PRAGMA foreign_keys=ON")
                cur.execute("PRAGMA busy_timeout=30000")
                cur.close()
        _Session = sessionmaker(bind=_engine, expire_on_commit=False, future=True)
    return _engine


def reset_engine():
    global _engine, _Session
    if _engine is not None:
        _engine.dispose()
    _engine = None
    _Session = None


def init_db():
    eng = get_engine()
    Base.metadata.create_all(eng)
    # Lightweight auto-migration for tables created before a column existed
    # (create_all does not alter existing tables). Additive only; both SQLite
    # and PostgreSQL accept this ALTER, and a duplicate column raises a
    # harmless "already exists" error we swallow.
    from sqlalchemy import text
    with eng.begin() as conn:
        for ddl in ("ALTER TABLE generation_providers ADD COLUMN key_url VARCHAR(500)",):
            try:
                conn.execute(text(ddl))
            except Exception:  # noqa: BLE001 - column already present
                pass
    return eng


def new_session() -> Session:
    get_engine()
    return _Session()


@contextmanager
def session_scope():
    s = new_session()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
