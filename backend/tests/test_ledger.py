"""Credit ledger invariants (section 12)."""

from __future__ import annotations

import pytest

from app.credits.ledger import CreditError, CreditLedger
from app.models.providers import ProviderQuota


def _quota(db, provider_id="p1", *, available=10.0, source="estimated", model="m"):
    db.add(
        ProviderQuota(
            provider_id=provider_id,
            model_name=model,
            available_credits=available,
            balance_source=source,
            confidence=0.5,
        )
    )
    db.flush()


def test_reserve_and_reconcile(db):
    _quota(db, available=10.0)
    ledger = CreditLedger(db)
    res = ledger.reserve("p1", 4.0, job_id="j1", model_name="m")
    assert ledger.remaining("p1", model_name="m") == 6.0
    ledger.reconcile(res, actual_amount=4.0)
    assert ledger.remaining("p1", model_name="m") == 6.0


def test_no_overspend(db):
    _quota(db, available=5.0)
    ledger = CreditLedger(db)
    ledger.reserve("p1", 5.0, job_id="j1", model_name="m")
    with pytest.raises(CreditError):
        ledger.reserve("p1", 1.0, job_id="j2", model_name="m")


def test_unknown_balance_is_not_unlimited(db):
    _quota(db, available=None, source="unknown")
    ledger = CreditLedger(db)
    # A positive request against an unknown balance must be refused.
    with pytest.raises(CreditError):
        ledger.reserve("p1", 1.0, job_id="j1", model_name="m")
    # A zero-credit (free/uncounted) request is allowed.
    ledger.reserve("p1", 0.0, job_id="j2", model_name="m")


def test_paid_spend_requires_approval(db):
    _quota(db, available=100.0)
    ledger = CreditLedger(db)
    with pytest.raises(CreditError):
        ledger.reserve("p1", 5.0, job_id="j1", is_paid=True, model_name="m")
    # With approval it succeeds.
    ledger.reserve("p1", 5.0, job_id="j2", is_paid=True, model_name="m", allow_paid_spend=True)


def test_release_frees_reservation(db):
    _quota(db, available=10.0)
    ledger = CreditLedger(db)
    res = ledger.reserve("p1", 4.0, job_id="j1", model_name="m")
    ledger.release(res)
    assert ledger.remaining("p1", model_name="m") == 10.0


def test_mark_exhausted(db):
    _quota(db, available=10.0)
    ledger = CreditLedger(db)
    ledger.mark_exhausted("p1", model_name="m")
    assert ledger.remaining("p1", model_name="m") == 0.0
