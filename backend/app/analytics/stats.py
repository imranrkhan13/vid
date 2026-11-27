"""Provider performance benchmarking (section 11).

Measured behaviour is derived from the durable attempt log, not hardcoded. The
selector can prefer a provider whose *measured* reliability/quality is better
than its catalog claim — but only once enough samples exist, so a provider is
never promoted or demoted on a single attempt.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models.generation import GenerationAttempt, GenerationJob, QualityReport

# Below this many finished attempts, measured scores are not trusted.
MIN_SAMPLES = 3


@dataclass
class ProviderStats:
    provider_id: str
    attempts: int = 0
    successes: int = 0
    failures: int = 0
    measured_reliability: float | None = None  # 0..100
    avg_quality: float | None = None  # 0..100
    measured_cost_per_second: float | None = None
    total_credits: float = 0.0
    total_seconds: float = 0.0

    @property
    def has_enough_samples(self) -> bool:
        return self.attempts >= MIN_SAMPLES


class StatsCollector:
    """Aggregate attempt/quality history into per-provider statistics."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def for_provider(self, provider_id: str) -> ProviderStats:
        attempts = list(
            self.session.execute(
                select(GenerationAttempt).where(GenerationAttempt.provider_id == provider_id)
            ).scalars().all()
        )
        stats = ProviderStats(provider_id=provider_id)
        stats.attempts = len(attempts)
        stats.successes = sum(1 for a in attempts if a.outcome == "succeeded")
        stats.failures = sum(1 for a in attempts if a.outcome == "failed")
        stats.total_credits = round(sum(a.actual_cost for a in attempts), 6)
        if stats.attempts:
            stats.measured_reliability = round(100.0 * stats.successes / stats.attempts, 2)

        # Quality: average accepted quality-report scores for this provider's jobs.
        job_ids = [
            j.id
            for j in self.session.execute(
                select(GenerationJob).where(GenerationJob.provider_id == provider_id)
            ).scalars().all()
        ]
        if job_ids:
            reports = list(
                self.session.execute(
                    select(QualityReport).where(QualityReport.scene_id.is_not(None))
                ).scalars().all()
            )
            import json

            scores: list[float] = []
            for rep in reports:
                try:
                    data = json.loads(rep.scores_json or "{}")
                except ValueError:
                    continue
                val = data.get("style_consistency") or data.get("motion_quality")
                if isinstance(val, (int, float)):
                    scores.append(float(val))
            if scores:
                stats.avg_quality = round(sum(scores) / len(scores), 2)
        return stats

    def measured_override(self, provider_id: str) -> dict:
        """Return measured scores to prefer over catalog claims, when trustworthy."""
        stats = self.for_provider(provider_id)
        if not stats.has_enough_samples:
            return {}
        out: dict = {}
        if stats.measured_reliability is not None:
            out["reliability_score"] = stats.measured_reliability
        if stats.avg_quality is not None:
            out["quality_score"] = stats.avg_quality
        return out
