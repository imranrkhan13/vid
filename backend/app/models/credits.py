"""Credit transaction ledger (section 12)."""

from __future__ import annotations

from datetime import datetime, timezone

from sqlalchemy import DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class CreditTransaction(Base):
    """Append-only record of every reserve / release / consume / reconcile event."""

    __tablename__ = "credit_transactions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    project_id: Mapped[str | None] = mapped_column(
        ForeignKey("video_projects.id"), nullable=True, index=True
    )
    provider_id: Mapped[str] = mapped_column(ForeignKey("generation_providers.id"), index=True)
    credential_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    quota_id: Mapped[int | None] = mapped_column(ForeignKey("provider_quotas.id"), nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    # reserve / release / consume / reconcile / adjust
    kind: Mapped[str] = mapped_column(String(16))
    amount: Mapped[float] = mapped_column(Float)
    is_paid: Mapped[bool] = mapped_column(Integer, default=0)  # 1 = real money
    balance_source: Mapped[str] = mapped_column(String(32), default="unknown")
    note: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=_utcnow)
