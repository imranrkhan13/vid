#!/usr/bin/env python3
"""Run the section-31 end-to-end scenario with deterministic mocks.

Creates a 32-second, 8-scene project and drives it through provider failover,
credit exhaustion, a timeout and handoff, then prints an execution report.

This performs no network I/O and produces no real media — it exercises the
orchestration core only. Run from the repository root:

    python scripts/run_demo.py
"""

from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

# Make the backend package importable when run from the repo root.
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.errors import ErrorCode  # noqa: E402
from app.database.base import configure, create_all, get_session  # noqa: E402
from app.orchestration.orchestrator import Orchestrator  # noqa: E402
from app.providers.mock import MockProvider, MockScript  # noqa: E402
from app.providers.registry import ProviderRegistry  # noqa: E402
from app.services.projects import ProjectService  # noqa: E402


def _provider(pid, *, script, quality, consistency):
    return MockProvider(
        pid,
        script=script,
        quality_score=quality,
        consistency_score=consistency,
        reliability_score=95,
        speed_score=80,
        cost_per_second=1.0,
    )


async def main() -> int:
    configure("sqlite+pysqlite:///:memory:")
    create_all()
    session = get_session()
    registry = ProviderRegistry()
    service = ProjectService(session, registry)

    project = service.create_project(
        title="Educational Animation",
        concept="A friendly robot walks through a colorful city",
        target_duration_seconds=32.0,
        aspect_ratio="9:16",
        resolution="1080x1920",
        fps=30,
    )
    service.plan_storyboard(project.id, scene_count=8, narration="A short narration.")

    # A: 3 scenes then exhausted. B: 2 scenes then a timeout. C: finishes.
    a = _provider("mock_a", script=MockScript(succeed_first=3,
                  fail_forever=ErrorCode.CREDITS_EXHAUSTED), quality=96, consistency=96)
    b_script = MockScript(succeed_first=2)
    b_script.script_status("succeeded", "succeeded", "timeout", "failed")
    b = _provider("mock_b", script=b_script, quality=94, consistency=94)
    c = _provider("mock_c", script=MockScript(), quality=90, consistency=90)

    for p in (a, b, c):
        service.register_provider(p)
        service.set_quota(p.provider_id, available_credits=100.0,
                          balance_source="estimated", model_name=f"{p.provider_id}-model")

    print(
        f"Project: {project.id}  ({project.target_duration_seconds:.0f}s, "
        f"{project.aspect_ratio}, {project.resolution})\n"
    )

    report = await Orchestrator(session, registry).run(
        project.id, character_references=["character_front.png", "character_side.png"]
    )
    session.commit()

    print("Execution report")
    print("=" * 40)
    print(json.dumps(report.to_dict(), indent=2))

    ok = (
        report.final_status == "COMPLETED"
        and report.scenes_accepted == 8
        and report.duration_within_one_frame
        and report.scenes_regenerated == 0
    )
    print("\nResult:", "PASS" if ok else "FAIL")
    session.close()
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
