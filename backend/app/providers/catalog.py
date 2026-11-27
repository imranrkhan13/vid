"""Machine-readable provider capability catalog (sections 3, 26).

Facts come from a JSON file — never hardcoded in source. Unknown facts are
represented as ``null`` and must never be assumed True. The catalog is loaded
into ``ProviderCatalogEntry`` records so the router can score providers and the
credit engine can schedule against daily/monthly/trial grants.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import ValidationError

from app.schemas.provider import (
    CreditGrant,
    ProviderCapabilities,
    ProviderCatalogEntry,
)

# Default catalog location, relative to the repository root (docs/).
DEFAULT_CATALOG_PATH = Path(__file__).resolve().parents[3] / "docs" / "provider_catalog.json"


class CatalogError(ValueError):
    """Raised when the catalog JSON is structurally invalid."""


def load_catalog(path: str | Path | None = None) -> dict:
    """Load the raw catalog JSON, returning an empty catalog if absent."""
    p = Path(path) if path is not None else DEFAULT_CATALOG_PATH
    if not p.exists():
        return {"providers": []}
    return json.loads(p.read_text(encoding="utf-8"))


def _grants(raw: object) -> list[CreditGrant]:
    if not isinstance(raw, list):
        return []
    grants: list[CreditGrant] = []
    for item in raw:
        if not isinstance(item, dict):
            raise CatalogError(f"credit grant must be an object, got {type(item).__name__}")
        grants.append(CreditGrant(**item))
    return grants


def parse_catalog(raw: dict) -> list[ProviderCatalogEntry]:
    """Validate raw catalog JSON into typed entries.

    Raises ``CatalogError`` on malformed structure so bad facts fail loudly
    instead of being silently dropped.
    """
    providers = raw.get("providers", [])
    if not isinstance(providers, list):
        raise CatalogError("'providers' must be a list")

    entries: list[ProviderCatalogEntry] = []
    seen: set[str] = set()
    for item in providers:
        if not isinstance(item, dict):
            raise CatalogError(f"provider entry must be an object, got {type(item).__name__}")
        payload = dict(item)
        payload["credits"] = _grants(payload.get("credits", []))
        try:
            entry = ProviderCatalogEntry(**payload)
        except ValidationError as exc:
            pid = payload.get("provider_id", "<unknown>")
            raise CatalogError(f"invalid catalog entry {pid!r}: {exc}") from exc
        if entry.provider_id in seen:
            raise CatalogError(f"duplicate provider_id: {entry.provider_id}")
        seen.add(entry.provider_id)
        entries.append(entry)
    return entries


def load_entries(path: str | Path | None = None) -> list[ProviderCatalogEntry]:
    """Load and validate the catalog into typed entries."""
    return parse_catalog(load_catalog(path))


def provider_capabilities_from_entry(entry: dict) -> ProviderCapabilities:
    """Build capabilities from a raw catalog entry, preserving explicit unknowns."""
    return ProviderCatalogEntry(**{
        **entry,
        "credits": _grants(entry.get("credits", [])),
    }).capabilities()

