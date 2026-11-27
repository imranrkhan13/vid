"""Visual consistency heuristics (README sections 15, 16, 18).

Deterministic, explainable checks used to detect *style drift* between an
accepted scene and a newly generated one. These are heuristics, not claims of
perceptual accuracy: they operate on the palette/grade a clip declares and on
measured QA scores. When a signal is missing the check reports "unknown" rather
than assuming consistency.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Sequence

_MAX_RGB_DISTANCE = (255 ** 2 * 3) ** 0.5


def _parse_hex(color: str) -> tuple[int, int, int] | None:
    c = color.strip().lstrip("#")
    if len(c) == 3:
        c = "".join(ch * 2 for ch in c)
    if len(c) != 6:
        return None
    try:
        return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)
    except ValueError:
        return None


def palette_similarity(reference: Sequence[str], candidate: Sequence[str]) -> float | None:
    """Return 0..100 similarity between two palettes, or None if unknown.

    Each candidate colour is matched to its nearest reference colour; the mean
    normalised RGB distance drives the score. Empty or unparseable palettes
    yield ``None`` — consistency is never asserted from missing data.
    """
    ref = [rgb for rgb in (_parse_hex(c) for c in reference) if rgb is not None]
    cand = [rgb for rgb in (_parse_hex(c) for c in candidate) if rgb is not None]
    if not ref or not cand:
        return None
    total = 0.0
    for cr, cg, cb in cand:
        best = min(
            ((cr - rr) ** 2 + (cg - rg) ** 2 + (cb - rb) ** 2) ** 0.5
            for rr, rg, rb in ref
        )
        total += best
    mean = total / len(cand)
    return round(100.0 * (1.0 - min(1.0, mean / _MAX_RGB_DISTANCE)), 3)


@dataclass
class StyleSignature:
    """The visual fingerprint carried between scenes (section 15)."""

    palette: list[str] = field(default_factory=list)
    grade: str = ""
    motion_style: str = ""
    line_weight: str = ""

    def is_empty(self) -> bool:
        return not self.palette and not self.grade and not self.motion_style


@dataclass
class DriftReport:
    drifted: bool
    reasons: list[str] = field(default_factory=list)
    palette_similarity: float | None = None


def detect_drift(
    reference: StyleSignature | None,
    candidate: StyleSignature | None,
    *,
    reference_style_score: float | None = None,
    candidate_style_score: float | None = None,
    max_style_drop: float = 15.0,
    min_palette_similarity: float = 70.0,
) -> DriftReport:
    """Detect style drift between the reference and a candidate scene.

    Returns ``drifted=False`` with no reasons when there is not enough signal to
    make a claim (missing signatures *and* missing scores) — absence of evidence
    is never treated as evidence of drift.
    """
    reasons: list[str] = []

    sim: float | None = None
    if reference is not None and candidate is not None:
        sim = palette_similarity(reference.palette, candidate.palette)
        if sim is not None and sim < min_palette_similarity:
            reasons.append(f"palette drift {sim:.1f} < {min_palette_similarity:.1f}")
        if (
            reference.motion_style
            and candidate.motion_style
            and reference.motion_style != candidate.motion_style
        ):
            reasons.append(
                f"motion style changed: {reference.motion_style!r} -> {candidate.motion_style!r}"
            )
        if reference.grade and candidate.grade and reference.grade != candidate.grade:
            reasons.append(f"grade changed: {reference.grade!r} -> {candidate.grade!r}")

    if (
        reference_style_score is not None
        and candidate_style_score is not None
        and reference_style_score - candidate_style_score > max_style_drop
    ):
        reasons.append(
            f"style score dropped {reference_style_score:.0f} -> {candidate_style_score:.0f}"
        )

    return DriftReport(drifted=bool(reasons), reasons=reasons, palette_similarity=sim)
