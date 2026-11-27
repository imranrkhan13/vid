"""Export service: compose the final video and produce a downloadable artifact.

Composition uses the :class:`Composer` (FFmpeg when available, manifest-only
otherwise). The export is recorded durably so it can be listed, re-fetched and
downloaded. When FFmpeg is absent the export is marked ``MANIFEST_ONLY`` and its
``output_uri`` is empty — the system never claims a video exists when it does not.
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.media.composer import Composer, CompositionClip
from app.models.audio import AudioTrack
from app.models.generation import ExportJob
from app.models.storyboard import MediaTimeline


class ExportService:
    def __init__(self, session: Session, *, composer: Composer | None = None) -> None:
        self.session = session
        self.composer = composer or Composer()

    def _clips(self, project_id: str) -> list[CompositionClip]:
        rows = self.session.execute(
            select(MediaTimeline)
            .where(MediaTimeline.project_id == project_id)
            .order_by(MediaTimeline.start_seconds)
        ).scalars().all()
        return [
            CompositionClip(
                scene_id=r.scene_id, uri=r.artifact_uri, duration_seconds=r.duration_seconds
            )
            for r in rows
        ]

    def _audio_path(self, project_id: str) -> str | None:
        track = self.session.execute(
            select(AudioTrack).where(
                AudioTrack.project_id == project_id, AudioTrack.is_master == 1
            )
        ).scalars().first()
        return track.uri or None if track else None

    def create_export(
        self,
        project_id: str,
        *,
        resolution: str = "1080x1920",
        fps: int = 30,
        aspect_ratio: str = "9:16",
        grade: str = "",
    ) -> ExportJob:
        clips = self._clips(project_id)
        result = self.composer.compose(
            project_id,
            clips,
            resolution=resolution,
            fps=fps,
            aspect_ratio=aspect_ratio,
            audio_path=self._audio_path(project_id),
            grade=grade,
        )
        job = ExportJob(
            id=f"export-{uuid.uuid4().hex[:8]}",
            project_id=project_id,
            status="READY" if result.ffmpeg_used else "MANIFEST_ONLY",
            output_uri=result.output_uri,
            manifest_json=json.dumps(result.manifest),
        )
        self.session.add(job)
        self.session.flush()
        return job

    def get_export(self, export_id: str) -> ExportJob | None:
        return self.session.get(ExportJob, export_id)

    def list_exports(self, project_id: str) -> list[ExportJob]:
        return list(
            self.session.execute(
                select(ExportJob)
                .where(ExportJob.project_id == project_id)
                .order_by(ExportJob.created_at)
            ).scalars().all()
        )

    def download_path(self, export_id: str) -> Path | None:
        """Return the on-disk path for a READY export, or None if not available."""
        job = self.get_export(export_id)
        if job is None or job.status != "READY" or not job.output_uri:
            return None
        path = Path(job.output_uri)
        return path if path.exists() else None
