"""Workflow state machine (README section 24).

Transitions are validated against an explicit allow-list and persisted. Project
and scene states are tracked separately. Work resumes from durable checkpoints
after restarts, so the machine is pure and side-effect free; persistence lives
in the orchestrator.
"""

from __future__ import annotations

from app.core.state import ProjectStatus, SceneStatus

# Legal project-state transitions (section 24).
PROJECT_TRANSITIONS: dict[ProjectStatus, set[ProjectStatus]] = {
    ProjectStatus.DRAFT: {ProjectStatus.PLANNING, ProjectStatus.CANCELLED},
    ProjectStatus.PLANNING: {
        ProjectStatus.STORYBOARD_READY,
        ProjectStatus.FAILED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.STORYBOARD_READY: {
        ProjectStatus.AWAITING_USER_APPROVAL,
        ProjectStatus.READY,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.AWAITING_USER_APPROVAL: {
        ProjectStatus.READY,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.READY: {
        ProjectStatus.SELECTING_PROVIDER,
        ProjectStatus.PAUSED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.SELECTING_PROVIDER: {
        ProjectStatus.GENERATING,
        ProjectStatus.SWITCHING_PROVIDER,
        ProjectStatus.WAITING_FOR_PROVIDER,
        ProjectStatus.WAITING_FOR_CREDITS,
        ProjectStatus.MANUAL_ACTION_REQUIRED,
        ProjectStatus.FAILED,
        ProjectStatus.PAUSED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.GENERATING: {
        ProjectStatus.DOWNLOADING,
        ProjectStatus.RETRYING,
        ProjectStatus.SWITCHING_PROVIDER,
        ProjectStatus.WAITING_FOR_CREDITS,
        ProjectStatus.WAITING_FOR_PROVIDER,
        ProjectStatus.MANUAL_ACTION_REQUIRED,
        ProjectStatus.FAILED,
        ProjectStatus.PAUSED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.DOWNLOADING: {
        ProjectStatus.VALIDATING,
        ProjectStatus.RETRYING,
        ProjectStatus.SWITCHING_PROVIDER,
        ProjectStatus.FAILED,
        ProjectStatus.PAUSED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.VALIDATING: {
        ProjectStatus.GENERATING,  # more scenes remain
        ProjectStatus.RETRYING,
        ProjectStatus.SWITCHING_PROVIDER,
        ProjectStatus.COMPOSING,  # all scenes accepted
        ProjectStatus.MANUAL_ACTION_REQUIRED,
        ProjectStatus.FAILED,
        ProjectStatus.PAUSED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.RETRYING: {
        ProjectStatus.GENERATING,
        ProjectStatus.SELECTING_PROVIDER,
        ProjectStatus.SWITCHING_PROVIDER,
        ProjectStatus.FAILED,
        ProjectStatus.PAUSED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.SWITCHING_PROVIDER: {
        ProjectStatus.SELECTING_PROVIDER,
        ProjectStatus.WAITING_FOR_PROVIDER,
        ProjectStatus.MANUAL_ACTION_REQUIRED,
        ProjectStatus.FAILED,
        ProjectStatus.PAUSED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.WAITING_FOR_CREDITS: {
        ProjectStatus.SELECTING_PROVIDER,
        ProjectStatus.GENERATING,
        ProjectStatus.MANUAL_ACTION_REQUIRED,
        ProjectStatus.PAUSED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.WAITING_FOR_PROVIDER: {
        ProjectStatus.SELECTING_PROVIDER,
        ProjectStatus.GENERATING,
        ProjectStatus.MANUAL_ACTION_REQUIRED,
        ProjectStatus.PAUSED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.MANUAL_ACTION_REQUIRED: {
        ProjectStatus.SELECTING_PROVIDER,
        ProjectStatus.GENERATING,
        ProjectStatus.PAUSED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.COMPOSING: {
        ProjectStatus.FINAL_VALIDATION,
        ProjectStatus.FAILED,
        ProjectStatus.PAUSED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.FINAL_VALIDATION: {
        ProjectStatus.COMPLETED,
        ProjectStatus.FAILED,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.COMPLETED: set(),
    ProjectStatus.FAILED: {ProjectStatus.PLANNING, ProjectStatus.CANCELLED},
    ProjectStatus.PAUSED: {
        ProjectStatus.SELECTING_PROVIDER,
        ProjectStatus.GENERATING,
        ProjectStatus.CANCELLED,
    },
    ProjectStatus.CANCELLED: set(),
}

# Legal scene-state transitions.
SCENE_TRANSITIONS: dict[SceneStatus, set[SceneStatus]] = {
    SceneStatus.PENDING: {SceneStatus.SELECTING_PROVIDER, SceneStatus.FAILED},
    SceneStatus.SELECTING_PROVIDER: {
        SceneStatus.GENERATING,
        SceneStatus.SWITCHING_PROVIDER,
        SceneStatus.MANUAL_ACTION_REQUIRED,
        SceneStatus.FAILED,
    },
    SceneStatus.GENERATING: {
        SceneStatus.DOWNLOADING,
        SceneStatus.RETRYING,
        SceneStatus.SWITCHING_PROVIDER,
        SceneStatus.FAILED,
    },
    SceneStatus.DOWNLOADING: {SceneStatus.VALIDATING, SceneStatus.RETRYING, SceneStatus.FAILED},
    SceneStatus.VALIDATING: {
        SceneStatus.ACCEPTED,
        SceneStatus.REJECTED,
        SceneStatus.RETRYING,
        SceneStatus.SWITCHING_PROVIDER,
        SceneStatus.AWAITING_APPROVAL,
    },
    SceneStatus.RETRYING: {SceneStatus.GENERATING, SceneStatus.SWITCHING_PROVIDER, SceneStatus.FAILED},
    SceneStatus.SWITCHING_PROVIDER: {SceneStatus.SELECTING_PROVIDER, SceneStatus.GENERATING},
    SceneStatus.AWAITING_APPROVAL: {SceneStatus.ACCEPTED, SceneStatus.REJECTED},
    SceneStatus.REJECTED: {SceneStatus.SELECTING_PROVIDER, SceneStatus.GENERATING},
    SceneStatus.ACCEPTED: set(),
    SceneStatus.MANUAL_ACTION_REQUIRED: {SceneStatus.SELECTING_PROVIDER, SceneStatus.GENERATING},
    SceneStatus.FAILED: {SceneStatus.SELECTING_PROVIDER},
}


class InvalidTransition(Exception):
    def __init__(self, current, target, kind: str) -> None:
        self.current = current
        self.target = target
        super().__init__(f"illegal {kind} transition: {current} -> {target}")


def can_transition(current: ProjectStatus, target: ProjectStatus) -> bool:
    return target in PROJECT_TRANSITIONS.get(current, set())


def assert_project_transition(current: ProjectStatus, target: ProjectStatus) -> None:
    if not can_transition(current, target):
        raise InvalidTransition(current, target, "project")


def can_transition_scene(current: SceneStatus, target: SceneStatus) -> bool:
    return target in SCENE_TRANSITIONS.get(current, set())


def assert_scene_transition(current: SceneStatus, target: SceneStatus) -> None:
    if not can_transition_scene(current, target):
        raise InvalidTransition(current, target, "scene")
