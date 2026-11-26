"""Central credit ledger (README section 12).

Rules: unknown is not unlimited; reservations are atomic (single conditional UPDATE, safe under parallel
workers on SQLite and PostgreSQL); actual usage is reconciled against the estimate; accounts are never
rotated to get around a restriction (a pool marked exhausted stays out until its reset time)."""
from __future__ import annotations
from datetime import datetime, timezone

from sqlalchemy import text, update
from sqlalchemy.orm import Session

from app.database.models import CreditTransaction, QuotaPool, utcnow


def iso_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def pool_is_usable(pool: QuotaPool) -> bool:
    if pool.exhausted:
        if pool.exhausted_until and pool.exhausted_until <= iso_now():
            return True  # permitted reset time has passed; re-verify before trusting
        return False
    return True


def free_credit(pool: QuotaPool) -> float | None:
    if pool.available is None:
        return None  # unknown is not unlimited
    return pool.available - pool.reserved


def reserve(db: Session, pool_id: str, amount: float, user_id: str, job_id: str) -> bool:
    """Atomically reserve `amount`. Returns False when the pool cannot cover it."""
    res = db.execute(
        update(QuotaPool)
        .where(QuotaPool.id == pool_id, QuotaPool.user_id == user_id, QuotaPool.exhausted.is_(False),
               QuotaPool.available.is_not(None), (QuotaPool.available - QuotaPool.reserved) >= amount - 1e-9)
        .values(reserved=QuotaPool.reserved + amount)
    )
    if res.rowcount != 1:
        db.rollback()
        return False
    db.add(CreditTransaction(pool_id=pool_id, user_id=user_id, job_id=job_id, kind="reserve", amount=amount, note="reserved before submit"))
    db.commit()
    return True


def release(db: Session, pool_id: str, amount: float, user_id: str, job_id: str, note: str = "released"):
    if amount <= 0:
        return
    db.execute(update(QuotaPool).where(QuotaPool.id == pool_id).values(reserved=QuotaPool.reserved - amount))
    db.add(CreditTransaction(pool_id=pool_id, user_id=user_id, job_id=job_id, kind="release", amount=amount, note=note))
    db.commit()


def consume(db: Session, pool_id: str, reserved: float, actual: float, user_id: str, job_id: str, estimated: float):
    """Settle a job: release the reservation and record what was actually used."""
    pool = db.get(QuotaPool, pool_id)
    pool.reserved = max(0.0, pool.reserved - reserved)
    pool.consumed += actual
    if pool.available is not None and pool.balance_source in ("user_entered", "estimated", "usage_report", "verified_api"):
        pool.available = max(0.0, pool.available - actual)
    pool.last_success = iso_now()
    db.add(CreditTransaction(pool_id=pool_id, user_id=user_id, job_id=job_id, kind="consume", amount=actual, estimated=estimated,
                             note="actual vs estimated reconciled" if abs(actual - estimated) > 1e-9 else "actual equals estimate"))
    db.commit()


def mark_exhausted(db: Session, pool_id: str, reset_at: str | None, note: str):
    pool = db.get(QuotaPool, pool_id)
    pool.exhausted = True
    pool.exhausted_until = reset_at
    pool.reserved = 0
    db.add(CreditTransaction(pool_id=pool_id, user_id=pool.user_id, kind="adjust", amount=0, note=f"marked exhausted: {note}"))
    db.commit()


def clear_exhausted(db: Session, pool_id: str):
    pool = db.get(QuotaPool, pool_id)
    pool.exhausted = False
    pool.exhausted_until = None
    db.commit()
