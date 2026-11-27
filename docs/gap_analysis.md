# Gap analysis — existing project vs. the VGOH specification

## Existing state (before this build)

The repository contained **only `README.md`** — a 373-line specification for the
AI Video Generation Harness. There was no source code, no schema, no
architecture document, no provider catalog, and no tests.

`git log` confirmed a single commit: "Add README for AI video generation harness spec".

## What was missing

| Spec area | Status before | Status now |
| --- | --- | --- |
| Architecture doc (§29) | absent | `docs/architecture.md` |
| Provider catalog (§3) | absent | `docs/provider_catalog.json` + `.md` |
| Domain model / DB schema (§8, §23) | absent | `backend/app/models/*` |
| Provider adapter interface (§10) | absent | `backend/app/providers/base.py` |
| Provider selection (§11) | absent | `backend/app/routing/selector.py` |
| Credit ledger (§12) | absent | `backend/app/credits/ledger.py` |
| Orchestration / state machine (§13, §24) | absent | `backend/app/orchestration/*` |
| Cross-model handoff (§14) | absent | `backend/app/continuity/handoff.py` |
| QA pipeline (§18) | absent | `backend/app/quality/qa.py` |
| Timeline engine (§9) | absent | `backend/app/timeline/engine.py` |
| Composition (§21) | absent | `backend/app/media/composer.py` |
| Audio alignment (§17) | absent | `backend/app/audio/alignment.py` |
| API (§25) | absent | `backend/app/api/*` |
| End-to-end acceptance test (§31) | absent | `backend/tests/test_e2e_acceptance.py` |
| Frontend (§22) | absent | `backend/app/web/*` (vanilla-JS SPA, no build step) |
| Real provider integrations | absent | **real HTTP adapters for every catalog provider** (`app/providers/real.py`), inert until a key is entered |
| Unified API-key entry (§28) | absent | `app/security/keystore.py` + `/api/v1/keys` + frontend panel |

## Deliberate non-goals (per §33 and §4)

- No fake integrations: real adapters are **real HTTP code** that fail closed
  without a verified credential; a provider with no confirmed endpoints stays
  inert even with a key. No invented capabilities or fake results.
- No hardcoded credits, pricing or capabilities: all facts come from the
  machine-readable catalog; unknown facts are `null`.
- No scraping, CAPTCHA/restriction bypass, watermark removal or credit farming.
- Mocks are labelled (`is_mock=True`) and isolated.

## Recommended next phases

1. Confirm each adapter's endpoints/auth against official docs before production
   use (paths in `PROVIDER_REGISTRY` are marked "confirm against docs").
2. Implement per-provider payload/status mappings where a generic one is
   insufficient (start with the two providers the user picks).
3. Add Redis/Celery (or Temporal) as the durable task runner; wire PostgreSQL.
4. Extend the acceptance test to real providers once credentials exist.
