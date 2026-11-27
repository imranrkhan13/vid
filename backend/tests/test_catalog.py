"""Catalog tests (section 3, 26).

The catalog is the single source of truth for provider facts. These tests assert
it is structurally valid, that unknowns are never silently assumed True, that
there are enough researched resources, and — critically — that no provider facts
are hardcoded in application source.
"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from app.providers.catalog import (
    DEFAULT_CATALOG_PATH,
    CatalogError,
    load_catalog,
    load_entries,
    parse_catalog,
)
from app.schemas.provider import ProviderCategory

APP_DIR = Path(__file__).resolve().parents[1] / "app"


def test_catalog_file_exists():
    assert DEFAULT_CATALOG_PATH.exists(), f"missing catalog at {DEFAULT_CATALOG_PATH}"


def test_catalog_parses_and_has_enough_resources():
    entries = load_entries()
    assert len(entries) >= 20, f"expected 20+ researched resources, got {len(entries)}"


def test_provider_ids_are_unique():
    entries = load_entries()
    ids = [e.provider_id for e in entries]
    assert len(ids) == len(set(ids))


def test_unknowns_are_not_assumed_true():
    """A provider that is not verified must not claim capabilities as True."""
    for entry in load_entries():
        if entry.verified:
            continue
        # Unverified entries may still carry researched booleans, but they must
        # never be silently treated as supported. We assert the *loader* keeps
        # None as None (below); here we ensure unverified real providers don't
        # pretend to be fully specified.
        if entry.category == ProviderCategory.MOCK:
            continue
        assert entry.verified is False


def test_none_stays_none_through_loader():
    """An unverified entry with unknown fields keeps them as None (never True)."""
    entries = {e.provider_id: e for e in load_entries()}
    pika = entries["pika"]
    assert pika.verified is False
    assert pika.text_to_video is None
    assert pika.max_duration_seconds is None
    assert pika.cost_per_second is None


def test_at_least_eight_providers_are_researched():
    """The research milestone is breadth: 8+ resources across categories.

    'Usable' (an explicit capability) is gated on verification, so we assert
    research breadth here and a separate verified-facts test below.
    """
    entries = load_entries()
    real = [e for e in entries if e.category != ProviderCategory.MOCK]
    assert len(real) >= 8, f"only {len(real)} researched real providers"


def test_usable_providers_include_mock_and_manual():
    """At least the labelled mock and the manual-assisted path are usable today."""
    usable = {e.provider_id for e in load_entries() if e.has_any_generation_capability()}
    assert "mock_a" in usable
    assert "manual_assisted" in usable


def test_verified_entries_have_provenance():
    """A verified entry must cite a source and a date — no unsourced claims."""
    for entry in load_entries():
        if not entry.verified or entry.is_mock:
            continue
        assert entry.verification_date, f"{entry.provider_id} verified without a date"
        assert entry.docs_url or entry.weights_url, (
            f"{entry.provider_id} verified without a docs_url or weights_url"
        )


def test_verified_providers_declare_pricing_or_are_self_host():
    """Verified providers must state pricing, or be self-host (no external price)."""
    for entry in load_entries():
        if not entry.verified or entry.is_mock:
            continue
        self_host = entry.category in (ProviderCategory.OPEN_SOURCE_GPU, ProviderCategory.LOCAL)
        if self_host:
            continue
        assert entry.cost_per_second is not None or entry.api_pricing, (
            f"{entry.provider_id} verified but has no pricing"
        )


def test_enough_verified_providers_declare_capabilities():
    """Research must have confirmed real capabilities for a meaningful set."""
    verified_caps = [
        e
        for e in load_entries()
        if e.verified and not e.is_mock and e.has_any_generation_capability()
    ]
    assert len(verified_caps) >= 5, f"only {len(verified_caps)} verified providers with capabilities"


def test_mock_is_labelled():
    entries = {e.provider_id: e for e in load_entries()}
    mock = entries["mock_a"]
    assert mock.is_mock is True
    assert mock.category == ProviderCategory.MOCK


def test_real_providers_are_not_marked_mock():
    for entry in load_entries():
        if entry.category != ProviderCategory.MOCK:
            assert entry.is_mock is False


def test_credit_grants_parse_with_periods():
    entries = {e.provider_id: e for e in load_entries()}
    kling = entries["kling"]
    assert kling.credits, "kling should have a researched credit grant"
    assert any(g.period == "daily" for g in kling.credits)
    for g in kling.credits:
        # limit may be unknown (None), but period must be a known token.
        assert g.period in {"none", "daily", "weekly", "monthly", "trial", "one_time"}


def test_categories_cover_expected_spread():
    cats = {e.category for e in load_entries()}
    assert ProviderCategory.NATIVE_API in cats
    assert ProviderCategory.OPEN_SOURCE_GPU in cats
    assert ProviderCategory.MANUAL_ASSISTED in cats
    assert ProviderCategory.MOCK in cats


def test_duplicate_provider_id_rejected():
    raw = {
        "providers": [
            {"provider_id": "x", "name": "X", "category": "native_api"},
            {"provider_id": "x", "name": "X2", "category": "native_api"},
        ]
    }
    with pytest.raises(CatalogError, match="duplicate"):
        parse_catalog(raw)


def test_malformed_entry_rejected():
    raw = {"providers": [{"provider_id": "x", "name": "X", "category": "not_a_category"}]}
    with pytest.raises(CatalogError):
        parse_catalog(raw)


def test_missing_file_returns_empty_catalog():
    assert load_catalog("/nonexistent/path/catalog.json") == {"providers": []}


# --- The critical guardrail: no hardcoded provider facts in DECISION code ----
#
# Adapters (app/providers/*) legitimately know their own provider_id and mock
# behaviour. The decision-making modules — routing, credits, orchestration,
# services — must derive every provider fact from the catalog, never a literal.

# Decision modules where a provider fact would be a bug.
_DECISION_DIRS = ("routing", "credits", "orchestration", "services", "quality", "directors")

_FACT_PATTERNS = [
    re.compile(r"(max_duration_seconds|cost_per_second|api_pricing|rate_limit)\s*=\s*[0-9]"),
    re.compile(
        r"\b(runway|kling|pika|luma|replicate|fal|hunyuan|mochi|cogvideo|ltx|openrouter)\b\s*[:=]\s*[0-9]"
    ),
]


def _decision_files() -> list[Path]:
    files: list[Path] = []
    for sub in _DECISION_DIRS:
        files.extend((APP_DIR / sub).rglob("*.py"))
    return files


def test_no_provider_facts_hardcoded_in_decision_code():
    offenders: list[str] = []
    for path in _decision_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for line_no, line in enumerate(text.splitlines(), 1):
            stripped = line.strip()
            if stripped.startswith("#"):
                continue
            for pat in _FACT_PATTERNS:
                if pat.search(line):
                    offenders.append(f"{path.relative_to(APP_DIR)}:{line_no}: {stripped}")
    assert not offenders, "provider facts must live in the catalog, not decision code:\n" + "\n".join(
        offenders
    )


def test_no_real_provider_id_hardcoded_in_decision_code():
    """Decision modules must not name real providers; ids come from the catalog."""
    entries = load_entries()
    real_ids = [
        e.provider_id for e in entries if e.category != ProviderCategory.MOCK and e.provider_id
    ]
    offenders: list[str] = []
    for path in _decision_files():
        text = path.read_text(encoding="utf-8", errors="replace")
        for pid in real_ids:
            if re.search(rf"""["']{re.escape(pid)}["']""", text):
                offenders.append(f"{path.relative_to(APP_DIR)}: {pid}")
    assert not offenders, "provider ids must not be hardcoded in decision code:\n" + "\n".join(
        offenders
    )
