"""Shared test fixtures.

All tests run against in-memory SQLite with labelled deterministic mocks — no
network, no Docker, no FFmpeg, no real provider credentials.
"""

from __future__ import annotations

import pytest

from app.database.base import configure, create_all, get_session
from app.providers.mock import MockProvider, MockScript
from app.providers.registry import ProviderRegistry
from app.services.projects import ProjectService


@pytest.fixture
def db():
    configure("sqlite+pysqlite:///:memory:")
    create_all()
    session = get_session()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def registry():
    return ProviderRegistry()


def make_provider(
    provider_id: str,
    *,
    script: MockScript | None = None,
    quality: float = 85.0,
    consistency: float = 85.0,
    reliability: float = 95.0,
    speed: float = 80.0,
    cost_per_second: float = 0.0,
    commercial_rights: bool = True,
    watermark_free: bool = True,
) -> MockProvider:
    return MockProvider(
        provider_id,
        script=script or MockScript(),
        quality_score=quality,
        consistency_score=consistency,
        reliability_score=reliability,
        speed_score=speed,
        cost_per_second=cost_per_second,
        commercial_rights=commercial_rights,
        watermark_free=watermark_free,
    )


@pytest.fixture
def make_32s_project(db, registry):
    """Create the section-31 project: 32s, 8 scenes, 9:16, 1080x1920."""

    def _make(*, narration: str = "A short educational narration.") -> str:
        service = ProjectService(db, registry)
        project = service.create_project(
            title="Educational Animation",
            concept="A friendly robot walks through a colorful city",
            target_duration_seconds=32.0,
            aspect_ratio="9:16",
            resolution="1080x1920",
            fps=30,
        )
        service.plan_storyboard(project.id, scene_count=8, narration=narration)
        return project.id

    return _make
