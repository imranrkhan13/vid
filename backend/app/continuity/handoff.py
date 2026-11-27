"""Cross-model context transfer (README section 14).

Builds the handoff package used to continue a scene on a different provider.
Only semantic and visual context transfers: neural latents, hidden memory and
random seeds do not transfer across unrelated models, so they are never included.
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class ContinuityContext(BaseModel):
    previous_frame: str | None = None
    character_references: list[str] = Field(default_factory=list)
    motion: str = ""
    camera: str = ""
    lighting: str = ""
    environment: str = ""
    style: str = ""
    emotion: str = ""
    negative_prompts: list[str] = Field(default_factory=list)


class HandoffPackage(BaseModel):
    """The structured handoff described in section 14."""

    project_id: str
    scene_id: str
    instruction_version: int = 1
    scene_description: str = ""
    handoff_reason: str = ""
    previous_provider: str = ""
    target_provider: str | None = None
    remaining_project_seconds: float = 0.0
    requested_clip_seconds: float = 0.0
    dialogue: str = ""
    narration: str = ""
    timeline_placement_seconds: float = 0.0
    continuity: ContinuityContext = Field(default_factory=ContinuityContext)
    qa_requirements: list[str] = Field(default_factory=list)
    # Creative memory carried across the switch (sections 7, 15, 16). These are
    # semantic/visual only — never provider latents, seeds or hidden state.
    style_contract: str = ""
    character_bible: list[dict] = Field(default_factory=list)
    style_signature: dict = Field(default_factory=dict)
    last_accepted_artifact_uri: str | None = None

    def to_json(self) -> str:
        return self.model_dump_json()


def build_handoff(
    *,
    project_id: str,
    scene_id: str,
    scene_description: str,
    previous_provider: str,
    handoff_reason: str,
    remaining_project_seconds: float,
    requested_clip_seconds: float,
    previous_frame: str | None,
    character_references: list[str],
    motion: str = "",
    camera: str = "",
    lighting: str = "",
    environment: str = "",
    style: str = "",
    emotion: str = "",
    negative_prompts: list[str] | None = None,
    instruction_version: int = 1,
    timeline_placement_seconds: float = 0.0,
    target_provider: str | None = None,
    style_contract: str = "",
    character_bible: list[dict] | None = None,
    style_signature: dict | None = None,
    last_accepted_artifact_uri: str | None = None,
) -> HandoffPackage:
    """Assemble a handoff package from the durable creative context."""
    return HandoffPackage(
        project_id=project_id,
        scene_id=scene_id,
        instruction_version=instruction_version,
        scene_description=scene_description,
        handoff_reason=handoff_reason,
        previous_provider=previous_provider,
        target_provider=target_provider,
        remaining_project_seconds=remaining_project_seconds,
        requested_clip_seconds=requested_clip_seconds,
        timeline_placement_seconds=timeline_placement_seconds,
        style_contract=style_contract,
        character_bible=list(character_bible or []),
        style_signature=dict(style_signature or {}),
        last_accepted_artifact_uri=last_accepted_artifact_uri,
        continuity=ContinuityContext(
            previous_frame=previous_frame,
            character_references=list(character_references),
            motion=motion,
            camera=camera,
            lighting=lighting,
            environment=environment,
            style=style,
            emotion=emotion,
            negative_prompts=negative_prompts or [],
        ),
    )
