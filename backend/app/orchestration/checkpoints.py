"""Durable checkpoints (sections 13, 24).

A checkpoint snapshots the canonical project state plus the accepted-scene set,
so a restarted process can resume without regenerating accepted work.
"""

from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.state import CanonicalProjectState
from app.models.projects import ProjectCheckpoint


class CheckpointStore:
    def __init__(self, session: Session) -> None:
        self.session = session

    def save(self, state: CanonicalProjectState, *, label: str = "") -> ProjectCheckpoint:
        cp = ProjectCheckpoint(
            project_id=state.project_id,
            state_json=state.model_dump_json(),
            label=label,
        )
        self.session.add(cp)
        self.session.flush()
        return cp

    def latest(self, project_id: str) -> CanonicalProjectState | None:
        stmt = (
            select(ProjectCheckpoint)
            .where(ProjectCheckpoint.project_id == project_id)
            .order_by(ProjectCheckpoint.id.desc())
            .limit(1)
        )
        cp = self.session.execute(stmt).scalars().first()
        if cp is None:
            return None
        return CanonicalProjectState.model_validate(json.loads(cp.state_json))
