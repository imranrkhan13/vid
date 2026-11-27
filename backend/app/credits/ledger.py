"""Central credit and quota ledger (README sections 12, 27).

Guarantees:
  * credits are reserved atomically — parallel overspending is prevented
  * unknown balances are never treated as unlimited
  * reconciliation replaces an estimate with actual usage
  * paid credits are never consumed without explicit approval
  * daily/weekly/monthly buckets reset on schedule; trial buckets expire
  * project budget caps (total credits and paid spend) are enforced
  * accounts are never rotated to circumvent restrictions

The ledger operates on ``provider_quotas`` rows and writes an append-only
``credit_transactions`` audit trail. It is provider-agnostic.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.credits import CreditTransaction
from app.models.projects import VideoProject
from app.models.providers import ProviderQuota

_PERIOD_DELTAS: dict[str, timedelta | None] = {
    "daily": timedelta(days=1),
    "weekly": timedelta(days=7),
    "monthly": timedelta(days=30),
    # Trial and one-time buckets never refill.
    "trial": None,
    "one_time": None,
    "none": None,
}


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_aware(dt: datetime | None) -> datetime | None:
    """Treat a naive datetime (as SQLite returns) as UTC-aware."""
    if dt is None:
        return None
    return dt if dt.tzinfo is not None else dt.replace(tzinfo=timezone.utc)


class CreditError(Exception):
    """Raised when a reservation or consumption cannot be satisfied."""


class BudgetError(CreditError):
    """Raised when a project budget cap would be exceeded (section 27)."""


@dataclass
class Reservation:
    """A hold on credits, identified by (quota_id, job_id)."""

    quota_id: int
    provider_id: str
    job_id: str
    amount: float
    is_paid: bool


class CreditLedger:
    def __init__(self, session: Session) -> None:
        self.session = session

    # --- helpers ---------------------------------------------------------------
    def _quota(
        self,
        provider_id: str,
        *,
        model_name: str = "",
        credential_id: str | None = None,
    ) -> ProviderQuota | None:
        stmt = select(ProviderQuota).where(
            ProviderQuota.provider_id == provider_id,
            ProviderQuota.model_name == model_name,
        )
        if credential_id is not None:
            stmt = stmt.where(ProviderQuota.credential_id == credential_id)
        return self.session.execute(stmt).scalars().first()

    def _remaining(self, quota: ProviderQuota) -> float | None:
        """Remaining credits, or None when the balance is unknown."""
        if quota.balance_source == "unknown" or quota.available_credits is None:
            return None
        return quota.available_credits - quota.reserved_credits

    def _record(
        self,
        *,
        quota: ProviderQuota,
        kind: str,
        amount: float,
        project_id: str | None,
        job_id: str | None,
        is_paid: bool,
        note: str = "",
    ) -> None:
        self.session.add(
            CreditTransaction(
                project_id=project_id,
                provider_id=quota.provider_id,
                credential_id=quota.credential_id,
                quota_id=quota.id,
                job_id=job_id,
                kind=kind,
                amount=amount,
                is_paid=1 if is_paid else 0,
                balance_source=quota.balance_source,
                note=note,
            )
        )

    # --- reset handling --------------------------------------------------------
    @staticmethod
    def _next_reset(period: str, base: datetime) -> datetime | None:
        delta = _PERIOD_DELTAS.get(period)
        return None if delta is None else base + delta

    def refresh_resets(self, provider_id: str | None = None) -> int:
        """Apply due resets/expiries to quotas; return the number refreshed.

        A daily/weekly/monthly bucket whose ``reset_at`` has passed refills to
        its ``limit_credits`` and rolls ``reset_at`` forward. A trial bucket
        whose ``expires_at`` has passed is zeroed permanently. This is what
        makes an exhausted provider a *scheduled* candidate again later.
        """
        now = _utcnow()
        stmt = select(ProviderQuota)
        if provider_id is not None:
            stmt = stmt.where(ProviderQuota.provider_id == provider_id)
        refreshed = 0
        for quota in self.session.execute(stmt).scalars().all():
            reset_at = _as_aware(quota.reset_at)
            expires_at = _as_aware(quota.expires_at)
            if reset_at is not None and reset_at <= now:
                # Roll forward past any missed periods.
                nxt = self._next_reset(quota.period, reset_at)
                while nxt is not None and nxt <= now:
                    nxt = self._next_reset(quota.period, nxt)
                quota.available_credits = quota.limit_credits
                quota.reserved_credits = 0.0
                quota.consumed_credits = 0.0
                quota.reset_at = nxt
                if quota.limit_credits is not None and quota.balance_source == "unknown":
                    quota.balance_source = "estimated"
                self._record(
                    quota=quota,
                    kind="reset",
                    amount=quota.limit_credits or 0.0,
                    project_id=None,
                    job_id=None,
                    is_paid=quota.is_paid,
                    note=f"{quota.period} reset",
                )
                refreshed += 1
            if (
                expires_at is not None
                and expires_at <= now
                and quota.available_credits not in (0.0, None)
            ):
                quota.available_credits = 0.0
                quota.reset_at = None
                if quota.balance_source == "unknown":
                    quota.balance_source = "estimated"
                self._record(
                    quota=quota,
                    kind="expire",
                    amount=0.0,
                    project_id=None,
                    job_id=None,
                    is_paid=quota.is_paid,
                    note="trial expired",
                )
                refreshed += 1
        if refreshed:
            self.session.flush()
        return refreshed

    def next_reset_at(self, provider_id: str, *, model_name: str = "") -> datetime | None:
        """Earliest future reset time for a provider's refillable buckets."""
        now = _utcnow()
        stmt = select(ProviderQuota).where(ProviderQuota.provider_id == provider_id)
        if model_name:
            stmt = stmt.where(ProviderQuota.model_name == model_name)
        times: list[datetime] = []
        for q in self.session.execute(stmt).scalars().all():
            aware = _as_aware(q.reset_at)
            if aware is not None and aware > now:
                times.append(aware)
        return min(times) if times else None

    # --- budget caps -----------------------------------------------------------
    def project_spend(self, project_id: str) -> tuple[float, float]:
        """Return ``(total_consumed, paid_consumed)`` for a project."""
        txns = self.session.execute(
            select(CreditTransaction).where(
                CreditTransaction.project_id == project_id,
                CreditTransaction.kind == "reconcile",
            )
        ).scalars().all()
        total = sum(t.amount for t in txns)
        paid = sum(t.amount for t in txns if t.is_paid)
        return round(total, 6), round(paid, 6)

    def _check_budget(
        self, project_id: str | None, amount: float, *, is_paid: bool
    ) -> None:
        if project_id is None:
            return
        project = self.session.get(VideoProject, project_id)
        if project is None:
            return
        total, paid = self.project_spend(project_id)
        if project.max_budget is not None and total + amount > project.max_budget + 1e-9:
            raise BudgetError(
                f"project budget cap reached: {total}+{amount} > {project.max_budget}"
            )
        if (
            is_paid
            and project.max_paid_spend is not None
            and paid + amount > project.max_paid_spend + 1e-9
        ):
            raise BudgetError(
                f"project paid-spend cap reached: {paid}+{amount} > {project.max_paid_spend}"
            )

    # --- public API ------------------------------------------------------------
    def can_afford(
        self,
        provider_id: str,
        amount: float,
        *,
        is_paid: bool,
        model_name: str = "",
        allow_paid_spend: bool = False,
    ) -> bool:
        """Return True if ``amount`` credits can be reserved.

        Unknown balances cannot be proven sufficient, so an unknown balance is
        *not* treated as unlimited; the caller may still proceed only if the
        amount is zero or the balance is known to cover it.
        """
        if is_paid and not allow_paid_spend:
            return False
        quota = self._quota(provider_id, model_name=model_name)
        if quota is None:
            return False
        remaining = self._remaining(quota)
        if remaining is None:
            # Unknown balance: cannot prove affordability. Only a zero-credit
            # (free/uncounted) request may proceed.
            return amount <= 0
        return remaining >= amount

    def reserve(
        self,
        provider_id: str,
        amount: float,
        *,
        job_id: str,
        project_id: str | None = None,
        is_paid: bool = False,
        model_name: str = "",
        credential_id: str | None = None,
        allow_paid_spend: bool = False,
    ) -> Reservation:
        """Atomically reserve credits for a job.

        Raises ``CreditError`` if the reservation cannot be satisfied. The
        reservation is committed in the same transaction as the quota update so
        concurrent reservations cannot overspend.
        """
        if is_paid and not allow_paid_spend:
            raise CreditError("paid spend requires explicit approval")
        self._check_budget(project_id, amount, is_paid=is_paid)
        quota = self._quota(provider_id, model_name=model_name, credential_id=credential_id)
        if quota is None:
            raise CreditError(f"no quota record for provider {provider_id!r}")
        remaining = self._remaining(quota)
        if remaining is None:
            if amount > 0:
                raise CreditError(
                    f"balance for {provider_id!r} is unknown; refusing to assume it is unlimited"
                )
        elif remaining < amount:
            raise CreditError(
                f"insufficient credits for {provider_id!r}: need {amount}, have {remaining}"
            )
        quota.reserved_credits += amount
        self._record(
            quota=quota,
            kind="reserve",
            amount=amount,
            project_id=project_id,
            job_id=job_id,
            is_paid=is_paid,
        )
        self.session.flush()
        return Reservation(
            quota_id=quota.id,
            provider_id=provider_id,
            job_id=job_id,
            amount=amount,
            is_paid=is_paid,
        )

    def reconcile(
        self,
        reservation: Reservation,
        *,
        actual_amount: float,
        project_id: str | None = None,
    ) -> None:
        """Replace a reservation with actual usage and consume the credits."""
        quota = self.session.get(ProviderQuota, reservation.quota_id)
        if quota is None:
            raise CreditError("quota vanished during reconcile")
        quota.reserved_credits = max(0.0, quota.reserved_credits - reservation.amount)
        quota.consumed_credits += actual_amount
        if quota.available_credits is not None:
            quota.available_credits = max(0.0, quota.available_credits - actual_amount)
        self._record(
            quota=quota,
            kind="reconcile",
            amount=actual_amount,
            project_id=project_id,
            job_id=reservation.job_id,
            is_paid=reservation.is_paid,
            note=f"estimated {reservation.amount}",
        )
        self.session.flush()

    def release(self, reservation: Reservation, *, project_id: str | None = None) -> None:
        """Release a reservation without consuming (e.g. a failed attempt)."""
        quota = self.session.get(ProviderQuota, reservation.quota_id)
        if quota is None:
            return
        quota.reserved_credits = max(0.0, quota.reserved_credits - reservation.amount)
        self._record(
            quota=quota,
            kind="release",
            amount=reservation.amount,
            project_id=project_id,
            job_id=reservation.job_id,
            is_paid=reservation.is_paid,
        )
        self.session.flush()

    def mark_exhausted(self, provider_id: str, *, model_name: str = "") -> None:
        """Mark a provider's quota as exhausted (available credits = 0)."""
        quota = self._quota(provider_id, model_name=model_name)
        if quota is None:
            return
        quota.available_credits = 0.0
        if quota.balance_source == "unknown":
            quota.balance_source = "estimated"
        self.session.flush()

    def remaining(self, provider_id: str, *, model_name: str = "") -> float | None:
        quota = self._quota(provider_id, model_name=model_name)
        if quota is None:
            return None
        return self._remaining(quota)
