"""SQLAlchemy ORM models (README section 23).

Normalized tables with foreign keys, unique constraints and indexes. Media bytes
live in object storage; only metadata lives here.
"""

from app.models.projects import (
    Character,
    CharacterReferenceAsset,
    ProjectCheckpoint,
    ProjectVersion,
    StyleProfile,
    User,
    VideoProject,
)
from app.models.storyboard import (
    MediaTimeline,
    SceneDependency,
    SceneVersion,
    Storyboard,
    VideoScene,
)
from app.models.providers import (
    ProviderCapability,
    ProviderCredential,
    ProviderModel,
    ProviderQuota,
    QuotaPool,
)
from app.models.credits import CreditTransaction
from app.models.generation import (
    ContextHandoff,
    ExportJob,
    GenerationArtifact,
    GenerationAttempt,
    GenerationJob,
    QualityReport,
    WorkflowEvent,
    AuditLog,
)
from app.models.audio import AudioTrack, NarrationSegment

__all__ = [
    "User",
    "VideoProject",
    "ProjectVersion",
    "Character",
    "CharacterReferenceAsset",
    "StyleProfile",
    "ProjectCheckpoint",
    "Storyboard",
    "VideoScene",
    "SceneDependency",
    "SceneVersion",
    "MediaTimeline",
    "ProviderModel",
    "ProviderCapability",
    "ProviderCredential",
    "QuotaPool",
    "ProviderQuota",
    "CreditTransaction",
    "GenerationJob",
    "GenerationAttempt",
    "GenerationArtifact",
    "ContextHandoff",
    "QualityReport",
    "ExportJob",
    "WorkflowEvent",
    "AuditLog",
    "AudioTrack",
    "NarrationSegment",
]
