"""Workflow state machine (section 24)."""

from __future__ import annotations

import pytest

from app.core.state import ProjectStatus, SceneStatus
from app.orchestration import state_machine as sm


def test_legal_project_transition():
    sm.assert_project_transition(ProjectStatus.DRAFT, ProjectStatus.PLANNING)
    sm.assert_project_transition(ProjectStatus.PLANNING, ProjectStatus.STORYBOARD_READY)
    sm.assert_project_transition(ProjectStatus.READY, ProjectStatus.SELECTING_PROVIDER)
    sm.assert_project_transition(ProjectStatus.FINAL_VALIDATION, ProjectStatus.COMPLETED)


def test_illegal_project_transition():
    with pytest.raises(sm.InvalidTransition):
        sm.assert_project_transition(ProjectStatus.DRAFT, ProjectStatus.COMPLETED)
    with pytest.raises(sm.InvalidTransition):
        sm.assert_project_transition(ProjectStatus.COMPLETED, ProjectStatus.PLANNING)


def test_terminal_states_have_no_outgoing():
    assert sm.PROJECT_TRANSITIONS[ProjectStatus.COMPLETED] == set()
    assert sm.PROJECT_TRANSITIONS[ProjectStatus.CANCELLED] == set()


def test_scene_transitions():
    sm.assert_scene_transition(SceneStatus.PENDING, SceneStatus.SELECTING_PROVIDER)
    sm.assert_scene_transition(SceneStatus.VALIDATING, SceneStatus.ACCEPTED)
    with pytest.raises(sm.InvalidTransition):
        sm.assert_scene_transition(SceneStatus.ACCEPTED, SceneStatus.GENERATING)


def test_path_helpers_reach_all_states():
    # Every state must be reachable from DRAFT via some legal path.
    from collections import deque

    reachable = set()
    queue = deque([ProjectStatus.DRAFT])
    while queue:
        node = queue.popleft()
        if node in reachable:
            continue
        reachable.add(node)
        queue.extend(sm.PROJECT_TRANSITIONS.get(node, set()))
    assert reachable == set(ProjectStatus)
