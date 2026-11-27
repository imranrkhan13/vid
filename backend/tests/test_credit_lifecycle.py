"""Credit lifecycle: resets, expiry, deferral and budget caps (sections 12, 27)."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from sqlalchemy import select

from app.credits.ledger import BudgetError, CreditLedger
from app.models.providers import ProviderQuota
from app.orchestration.orchestrator import Orchestrator
from app.services.projects import ProjectService
from tests.conftest import make_provider


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _quota(db, provider_id, **kw):
    db.add(ProviderQuota(provider_id=provider_id, model_name="m", **kw))
    db.flush()


# --- reset / expiry --------------------------------------------------------

def test_daily_reset_refills_bucket(db):
    _quota(
        db,
        "p1",
        period="daily",
        limit_credits=10.0,
        available_credits=0.0,
        balance_source="estimated",
        reset_at=_utcnow() - timedelta(minutes=1),
    )
    ledger = CreditLedger(db)
    assert ledger.remaining("p1", model_name="m") == 0.0
    refreshed = ledger.refresh_resets("p1")
    assert refreshed == 1
    assert ledger.remaining("p1", model_name="m") == 10.0
    # reset_at rolled forward into the future.
    assert ledger.next_reset_at("p1") is not None
    assert ledger.next_reset_at("p1") > _utcnow()


def test_future_reset_does_not_refill(db):
    _quota(
        db,
        "p1",
        period="daily",
        limit_credits=10.0,
        available_credits=0.0,
        balance_source="estimated",
        reset_at=_utcnow() + timedelta(hours=6),
    )
    ledger = CreditLedger(db)
    assert ledger.refresh_resets("p1") == 0
    assert ledger.remaining("p1", model_name="m") == 0.0


def test_missed_periods_roll_forward(db):
    _quota(
        db,
        "p1",
        period="daily",
        limit_credits=5.0,
        available_credits=0.0,
        reset_at=_utcnow() - timedelta(days=3),
    )
    ledger = CreditLedger(db)
    ledger.refresh_resets("p1")
    # reset_at must end up strictly in the future, not 3 days in the past.
    assert ledger.next_reset_at("p1") > _utcnow()


def test_trial_expiry_zeroes_bucket(db):
    _quota(
        db,
        "p1",
        period="trial",
        limit_credits=20.0,
        available_credits=20.0,
        expires_at=_utcnow() - timedelta(minutes=1),
    )
    ledger = CreditLedger(db)
    ledger.refresh_resets("p1")
    assert ledger.remaining("p1", model_name="m") == 0.0
    # A trial never refills.
    assert ledger.next_reset_at("p1") is None


def test_reset_writes_audit_transaction(db):
    from app.models.credits import CreditTransaction

    _quota(
        db,
        "p1",
        period="daily",
        limit_credits=7.0,
        available_credits=0.0,
        reset_at=_utcnow() - timedelta(minutes=1),
    )
    ledger = CreditLedger(db)
    ledger.refresh_resets("p1")
    kinds = [t.kind for t in db.execute(select(CreditTransaction)).scalars().all()]
    assert "reset" in kinds


# --- budget caps -----------------------------------------------------------

def test_total_budget_cap_blocks_reservation(db):
    from app.services.projects import ProjectService

    project = ProjectService(db).create_project(title="t", max_budget=1.0)
    _quota(db, "p1", available_credits=100.0, balance_source="estimated")
    ledger = CreditLedger(db)
    with pytest.raises(BudgetError):
        ledger.reserve("p1", 5.0, job_id="j1", project_id=project.id, model_name="m")


def test_paid_spend_cap_blocks_paid_reservation(db):
    project = ProjectService(db).create_project(title="t")
    ProjectService(db).set_budget_policy(project.id, allow_paid=True, max_paid_spend=1.0)
    _quota(db, "p1", available_credits=100.0, balance_source="estimated", is_paid=True)
    ledger = CreditLedger(db)
    with pytest.raises(BudgetError):
        ledger.reserve(
            "p1", 5.0, job_id="j1", project_id=project.id, is_paid=True,
            model_name="m", allow_paid_spend=True,
        )


def test_budget_cap_pauses_project(db, registry, make_32s_project):
    project_id = make_32s_project()
    service = ProjectService(db, registry)
    service.set_budget_policy(project_id, max_budget=1.0)
    a = make_provider("mock_a", cost_per_second=1.0)  # 4s scenes => 4 credits each
    service.register_provider(a)
    service.set_quota("mock_a", available_credits=1000.0, balance_source="estimated",
                      model_name="mock_a-model")

    report = _run(db, registry, project_id)
    assert report.final_status == "MANUAL_ACTION_REQUIRED"
    assert any("budget cap" in n for n in report.notes)
    assert report.scenes_accepted == 0


# --- deferral --------------------------------------------------------------

def test_exhausted_provider_with_future_reset_defers(db, registry, make_32s_project):
    project_id = make_32s_project()
    service = ProjectService(db, registry)
    a = make_provider("mock_a")
    service.register_provider(a)
    future = _utcnow() + timedelta(hours=4)
    service.set_quota("mock_a", available_credits=0.0, limit_credits=100.0,
                      period="daily", balance_source="estimated",
                      model_name="mock_a-model", reset_at=future)

    report = _run(db, registry, project_id)
    assert report.final_status == "WAITING_FOR_CREDITS"
    assert report.next_reset_at is not None
    assert report.scenes_accepted == 0


def test_project_resumes_after_reset(db, registry, make_32s_project):
    project_id = make_32s_project()
    service = ProjectService(db, registry)
    a = make_provider("mock_a")
    service.register_provider(a)
    service.set_quota("mock_a", available_credits=0.0, limit_credits=100.0,
                      period="daily", balance_source="estimated",
                      model_name="mock_a-model",
                      reset_at=_utcnow() + timedelta(hours=4))

    first = _run(db, registry, project_id)
    assert first.final_status == "WAITING_FOR_CREDITS"

    # Simulate the reset time arriving: the bucket is due and refills.
    quota = db.execute(
        select(ProviderQuota).where(ProviderQuota.provider_id == "mock_a")
    ).scalars().first()
    quota.reset_at = _utcnow() - timedelta(seconds=1)

    second = _run(db, registry, project_id)
    assert second.final_status == "COMPLETED"
    assert second.scenes_accepted == 8


def _run(db, registry, project_id):
    import asyncio

    async def _go():
        report = await Orchestrator(db, registry).run(project_id)
        db.commit()
        return report

    return asyncio.run(_go())
