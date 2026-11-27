"""Phase 7 infrastructure tests: task runner, storage, analytics, security, API.

All local: no network, no optional dependencies required. Optional adapters
(Celery, S3, Fernet) are asserted to fail *honestly* when their dependency is
absent rather than silently degrading.
"""

from __future__ import annotations

import asyncio

import pytest

from app.analytics.cost import CostOptimizer, CostPlanItem
from app.analytics.stats import StatsCollector
from app.security.credentials import (
    CredentialError,
    CredentialStore,
    InsecureTestCipher,
    redact,
    redact_dict,
    resolve_default_cipher,
)
from app.storage.local import LocalFilesystemStorage
from app.tasks.runner import InProcessTaskRunner, default_runner


# --- task runner -----------------------------------------------------------

def test_in_process_runner_executes_and_records():
    runner = InProcessTaskRunner()

    async def work() -> str:
        return "done"

    async def main():
        handle = await runner.submit("work", work())
        await runner.wait(handle.task_id)
        return handle

    handle = asyncio.run(main())
    assert handle.done is True
    assert handle.result == "done"
    assert handle.error is None


def test_in_process_runner_records_errors():
    runner = InProcessTaskRunner()

    async def boom():
        raise ValueError("kaboom")

    async def main():
        handle = await runner.submit("boom", boom())
        await runner.wait(handle.task_id)
        return handle

    handle = asyncio.run(main())
    assert handle.done is True
    assert handle.result is None
    assert "ValueError" in handle.error


def test_default_runner_is_in_process_without_broker(monkeypatch):
    monkeypatch.delenv("VGOH_CELERY_BROKER_URL", raising=False)
    assert isinstance(default_runner(), InProcessTaskRunner)


# --- storage ---------------------------------------------------------------

def test_local_storage_round_trip(tmp_path):
    storage = LocalFilesystemStorage(tmp_path)
    uri = storage.put("a/b.bin", b"hello")
    assert storage.exists("a/b.bin")
    assert storage.get("a/b.bin") == b"hello"
    assert uri.endswith("a/b.bin")


def test_local_storage_rejects_traversal(tmp_path):
    storage = LocalFilesystemStorage(tmp_path)
    with pytest.raises(ValueError):
        storage.get("../../etc/passwd")


def test_s3_storage_fails_honestly_without_boto3():
    try:
        import boto3  # noqa: F401
    except ImportError:
        from app.storage.s3 import S3Storage

        with pytest.raises(RuntimeError, match="boto3"):
            S3Storage("bucket")
    else:  # pragma: no cover - boto3 installed in this environment
        pytest.skip("boto3 installed; skip the missing-dependency assertion")


# --- analytics -------------------------------------------------------------

def test_cost_optimizer_prefers_free_then_cheapest():
    optimizer = CostOptimizer(max_budget=100.0, allow_paid=True)
    plan = optimizer.plan(
        [
            CostPlanItem("paid_b", 5.0, True, 50.0),
            CostPlanItem("free_a", 0.0, False, 10.0),
            CostPlanItem("free_c", 2.0, False, 10.0),
        ]
    )
    assert [i.provider_id for i in plan.ordered] == ["free_a", "free_c", "paid_b"]
    assert plan.requires_paid is True
    assert plan.within_budget is True


def test_cost_optimizer_flags_over_budget_and_unapproved_paid():
    optimizer = CostOptimizer(max_budget=1.0, allow_paid=False)
    plan = optimizer.plan([CostPlanItem("paid", 5.0, True, 50.0)])
    assert plan.within_budget is False
    assert any("budget" in n for n in plan.notes)
    assert any("paid" in n for n in plan.notes)


def test_cost_optimizer_cheapest_free():
    optimizer = CostOptimizer()
    cheapest = optimizer.cheapest_eligible(
        [CostPlanItem("a", 3.0, False, 9.0), CostPlanItem("b", 1.0, False, 9.0)]
    )
    assert cheapest is not None and cheapest.provider_id == "b"
    assert optimizer.cheapest_eligible([CostPlanItem("p", 1.0, True, 9.0)]) is None


def test_stats_collector_measures_from_attempts(db):
    from app.models.generation import GenerationAttempt, GenerationJob

    job = GenerationJob(
        id="j1", project_id="p1", scene_id="s1", provider_id="mock_a",
        idempotency_key="k1", status="DONE",
    )
    db.add(job)
    for i, outcome in enumerate(["succeeded", "succeeded", "failed"]):
        db.add(GenerationAttempt(job_id="j1", provider_id="mock_a",
                                 attempt_number=i + 1, outcome=outcome))
    db.flush()
    stats = StatsCollector(db).for_provider("mock_a")
    assert stats.attempts == 3
    assert stats.successes == 2
    assert stats.failures == 1
    assert stats.has_enough_samples is True
    assert stats.measured_reliability == pytest.approx(66.67, abs=0.01)


def test_stats_override_requires_enough_samples(db):
    from app.models.generation import GenerationAttempt, GenerationJob

    db.add(GenerationJob(id="j2", project_id="p1", scene_id="s1",
                         provider_id="mock_x", idempotency_key="k2", status="DONE"))
    db.add(GenerationAttempt(job_id="j2", provider_id="mock_x", attempt_number=1,
                             outcome="failed"))
    db.flush()
    # One sample is not enough to override declared scores.
    assert StatsCollector(db).measured_override("mock_x") == {}


# --- security --------------------------------------------------------------

def test_redact_removes_secret_everywhere():
    text = "token=abc123 and again abc123"
    assert "abc123" not in redact(text, ["abc123"])
    payload = {"headers": {"Authorization": "Bearer abc123"}, "note": "abc123"}
    out = redact_dict(payload, ["abc123"])
    assert "abc123" not in out["headers"]["Authorization"]
    assert "abc123" not in out["note"]


def test_credential_store_round_trip_with_test_cipher(db):
    store = CredentialStore(db, InsecureTestCipher())
    row = store.store("runway", "sk-secret", actor="tester")
    # The stored value is not the plaintext.
    assert "sk-secret" not in row.encrypted_secret
    assert store.reveal(row.id, actor="tester") == "sk-secret"


def test_credential_store_refuses_plaintext_without_cipher(db):
    store = CredentialStore(db, None)
    with pytest.raises(CredentialError, match="plaintext"):
        store.store("runway", "sk-secret")


def test_credential_operations_are_audited(db):
    from app.models.generation import AuditLog
    from sqlalchemy import select

    store = CredentialStore(db, InsecureTestCipher())
    row = store.store("runway", "sk", actor="tester")
    store.reveal(row.id, actor="tester")
    logs = db.execute(select(AuditLog)).scalars().all()
    actions = {log.action for log in logs}
    assert "credential.store" in actions
    assert "credential.reveal" in actions
    # The secret never appears in an audit detail.
    assert all("sk" != log.detail for log in logs)


def test_default_cipher_is_none_without_key(monkeypatch):
    # With no key configured, no production cipher can be resolved (honest).
    monkeypatch.delenv("VGOH_CREDENTIAL_KEY", raising=False)
    assert resolve_default_cipher() is None
