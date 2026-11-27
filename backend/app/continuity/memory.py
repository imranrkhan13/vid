"""Persistent creative memory (README sections 7, 15, 16).

The durable truth of *what the project looks like* — the style sheet and the
character bible — lives in the project, never in a provider. When a provider is
swapped mid-project, this memory is re-applied to the new provider's prompt so
the look and characters survive the switch. Providers are interchangeable;
the project's creative memory is not.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.continuity.consistency import StyleSignature
from app.models.projects import Character, CharacterReferenceAsset, StyleProfile


@dataclass
class StyleSheet:
    name: str = ""
    description: str = ""
    palette: list[str] = field(default_factory=list)
    grade: str = ""
    motion_style: str = ""
    negative_prompts: list[str] = field(default_factory=list)
    version: int = 1

    def signature(self) -> StyleSignature:
        return StyleSignature(
            palette=list(self.palette),
            grade=self.grade,
            motion_style=self.motion_style,
        )


@dataclass
class CharacterBibleEntry:
    id: str
    name: str
    description: str = ""
    palette: list[str] = field(default_factory=list)
    proportions: str = ""
    reference_uris: list[str] = field(default_factory=list)
    version: int = 1


@dataclass
class CreativeMemory:
    """Everything a new provider needs to match an in-progress project."""

    project_id: str
    style: StyleSheet | None = None
    characters: list[CharacterBibleEntry] = field(default_factory=list)
    style_version: int = 1
    character_bible_version: int = 1

    def reference_uris(self) -> list[str]:
        uris: list[str] = []
        for ch in self.characters:
            uris.extend(ch.reference_uris)
        return uris

    def style_contract(self) -> str:
        """A compact, provider-neutral description of the look and cast."""
        parts: list[str] = []
        if self.style is not None and not (
            not self.style.name
            and not self.style.description
            and not self.style.palette
        ):
            bits = [b for b in (self.style.name, self.style.description) if b]
            if self.style.palette:
                bits.append("palette " + ", ".join(self.style.palette))
            if self.style.grade:
                bits.append(f"grade {self.style.grade}")
            if self.style.motion_style:
                bits.append(f"motion {self.style.motion_style}")
            parts.append("STYLE: " + "; ".join(bits))
        for ch in self.characters:
            bits = [ch.name]
            if ch.description:
                bits.append(ch.description)
            if ch.palette:
                bits.append("palette " + ", ".join(ch.palette))
            if ch.proportions:
                bits.append(ch.proportions)
            parts.append("CHARACTER: " + "; ".join(bits))
        return "\n".join(parts)

    def is_empty(self) -> bool:
        return self.style is None and not self.characters


def load_creative_memory(session: Session, project_id: str) -> CreativeMemory:
    """Read the durable style sheet and character bible for a project."""
    style_row = session.execute(
        select(StyleProfile).where(StyleProfile.project_id == project_id)
    ).scalars().first()
    style: StyleSheet | None = None
    if style_row is not None:
        style = StyleSheet(
            name=style_row.name,
            description=style_row.description,
            version=style_row.version,
            # Palette/grade/motion are stored in the description as a JSON-ish
            # contract when set by the API; absent means "unknown", never assumed.
            **(_parse_style_payload(style_row.description)),
        )

    characters: list[CharacterBibleEntry] = []
    rows = session.execute(
        select(Character).where(Character.project_id == project_id).order_by(Character.name)
    ).scalars().all()
    for ch in rows:
        refs = session.execute(
            select(CharacterReferenceAsset)
            .where(CharacterReferenceAsset.character_id == ch.id)
            .order_by(CharacterReferenceAsset.view)
        ).scalars().all()
        characters.append(
            CharacterBibleEntry(
                id=ch.id,
                name=ch.name,
                description=ch.description,
                palette=_split_palette(ch.palette),
                proportions=ch.proportions,
                reference_uris=[r.uri for r in refs],
                version=ch.version,
            )
        )

    return CreativeMemory(
        project_id=project_id,
        style=style,
        characters=characters,
    )


def _split_palette(value: str) -> list[str]:
    if not value:
        return []
    return [p.strip() for p in value.replace(";", ",").split(",") if p.strip()]


def _parse_style_payload(description: str) -> dict:
    """Extract optional structured style fields from a description string.

    Recognises ``palette: a,b  grade: x  motion: y`` fragments; anything absent
    stays at its default (empty), i.e. unknown.
    """
    out: dict = {"palette": [], "grade": "", "motion_style": "", "negative_prompts": []}
    if not description:
        return out
    for line in description.replace(";", "\n").splitlines():
        if ":" not in line:
            continue
        key, _, val = line.partition(":")
        key = key.strip().lower()
        val = val.strip()
        if key == "palette":
            out["palette"] = _split_palette(val)
        elif key == "grade":
            out["grade"] = val
        elif key == "motion":
            out["motion_style"] = val
        elif key in ("negative", "negative_prompts"):
            out["negative_prompts"] = _split_palette(val)
    return out


class ContinuityManager:
    """Builds continuity-aware prompts and references from creative memory."""

    def __init__(self, session: Session) -> None:
        self.session = session

    def memory(self, project_id: str) -> CreativeMemory:
        return load_creative_memory(self.session, project_id)

    def compose_prompt(
        self,
        base_prompt: str,
        memory: CreativeMemory,
        *,
        extra_negative: str = "",
    ) -> tuple[str, str]:
        """Return ``(prompt, negative_prompt)`` with the style contract applied."""
        contract = memory.style_contract()
        prompt = base_prompt
        if contract:
            prompt = f"{base_prompt}\n\n{contract}"
        negatives: list[str] = []
        if memory.style is not None:
            negatives.extend(memory.style.negative_prompts)
        if extra_negative:
            negatives.append(extra_negative)
        return prompt, ", ".join(negatives)

    def reference_uris(self, memory: CreativeMemory) -> list[str]:
        return memory.reference_uris()
