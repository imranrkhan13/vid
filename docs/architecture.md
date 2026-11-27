# VGOH Architecture

The AI Video Generation Harness orchestrates multiple, independent video
generation engines to produce one downloadable video from a creative brief. The
core never depends on a provider-specific session or conversation.

## Layers

```
                 +-------------------------------------------+
   HTTP/SSE ---> | app/api            (section 25)           |
                 +-------------------------------------------+
                                |
                 +-------------------------------------------+
                 | app/services       project/storyboard svc |
                 +-------------------------------------------+
                                |
                 +-------------------------------------------+
                 | app/orchestration  state machine + loop   |
                 |   orchestrator, checkpoints (13, 24)      |
                 +-------------------------------------------+
                     |            |            |            |
        +------------+   +--------+---+  +-----+------+  +--+-----------+
        | routing    |   | credits    |  | continuity |  | quality      |
        | selector   |   | ledger     |  | handoff    |  | qa           |
        | (11)       |   | (12)       |  | (14)       |  | (18)         |
        +------------+   +------------+  +------------+  +--------------+
                     |            |
        +------------+   +--------+-------------------+
        | directors  |   | providers (10)             |
        | planner(6) |   | base + mock + real adapters|
        +------------+   +----------------------------+
                     |
        +------------+   +------------+  +------------+
        | timeline   |   | media      |  | audio      |
        | engine (9) |   | composer21 |  | align (17) |
        +------------+   +------------+  +------------+
```

`app/core` holds cross-cutting types: the normalized error taxonomy (§19), the
canonical project state and workflow enums (§8, §24), and configuration.

## Key flows

### 1. Plan → storyboard
`ProjectService.plan_storyboard` calls `directors.Director.plan`, which
decomposes the brief into scenes with dynamic durations and validates that the
scene total equals the target duration.

### 2. Generation loop (orchestrator)
For each **incomplete** scene (accepted scenes are skipped):

1. Build candidates and run the **selector** (hard filters, then weighted score).
2. Persist a `generation_jobs` row with an idempotency key.
3. **Reserve** credits atomically; refuse unknown balances and unapproved paid spend.
4. Submit, then poll — **reconciling** timeouts/ambiguous states before acting.
5. Download, run **QA**; on failure retry or switch provider (recording a
   **handoff**), on success accept.
6. Accepting writes a `generation_artifacts` row and appends to the canonical
   `media_timeline` in scene order.

### 3. Failover & continuation (§13)
- Accepted clips and state are persisted before any failure can occur.
- Exhausted providers are marked in the ledger and skipped by the selector.
- A provider switch records a `context_handoffs` package (§14) carrying only
  semantic/visual context (never latents, hidden memory or seeds).
- If no provider is eligible, the project pauses in `WAITING_FOR_PROVIDER` and
  resumes on a later run.

### 4. Composition (§21)
Once all scenes are accepted, the composer normalizes resolution/fps and
concatenates clips via FFmpeg. Without FFmpeg it writes a manifest describing
the intended output rather than pretending a video exists.

## Persistence

- **Default:** SQLite (in-memory for tests) via SQLAlchemy 2.x.
- **Optional:** PostgreSQL via `VGOH_DATABASE_URL`; Redis/Celery for task
  execution; S3/MinIO via a storage adapter. None are required to run or test.
- Media bytes live in object storage; metadata lives in the database.

## Provider isolation (§29)

`app/providers/base.py` defines the interface. `mock.py` is a labelled
deterministic engine for tests. `real.py` implements **real HTTP adapters** for
every provider the catalog lists:

- `CatalogBackedProvider` — identity, capabilities and pricing come only from
  `docs/provider_catalog.json`; a provider absent from the catalog is refused
  (`MODEL_UNAVAILABLE`) rather than invented.
- `AsyncTaskHTTPProvider` — submit/poll/download over `httpx`, with
  `map_http_error` translating status codes to the normalized taxonomy
  (402 → `CREDITS_EXHAUSTED` triggers a switch, not a retry). **No network at
  construction**; every call fails closed with `AUTHENTICATION_FAILED` until a
  credential is present.
- `GenericCatalogProvider` — a single catalog-driven adapter covering every
  provider in `PROVIDER_REGISTRY` (fal, luma, kling, minimax_hailuo,
  huggingface_inference, google_flow, …). Endpoints, auth scheme and env var are
  adapter-owned facts; capabilities and cost stay catalog-owned. A provider with
  no confirmed endpoints (e.g. Pika, OpenRouter) stays inert even with a key.
- `RunwayProvider` / `ReplicateProvider` — bespoke payload/status mapping.
- `ManualAssistedProvider` — a **free, network-free** path: it writes a brief for
  a human and serves the clip they import. No scraping, no consumer-UI
  automation, no fake result.

`build_default_registry()` assembles the labelled mock plus every adapter, so a
single process can route across all of them. Real facts come only from
`docs/provider_catalog.json`.

## Unified API-key entry (§28)

One place to enter keys for **all** providers: `app/security/keystore.py`
(`KeyStore`) holds a single JSON file (`VGOH_KEYS_FILE`, mode `0600`). Each value
is encrypted at rest when a cipher is configured; without one the file is written
as plaintext but **explicitly labelled** `encrypted: false` — the harness never
pretends a file is encrypted when it is not. Adapters resolve keys
env-var-first, then the store.

API: `GET /api/v1/keys` (status for every provider, values never returned),
`PUT /api/v1/keys` (one provider), `PUT /api/v1/keys/bulk` (many at once),
`DELETE /api/v1/keys/{provider_id}`. The frontend "API keys" panel renders one
field per provider with a single **Save all** button. Free providers
(`manual_assisted`, `local_diffusion`, `comfyui`, `huggingface_spaces`) need no
key.

## Security & compliance (§28)

Credentials are stored encrypted and never placed in the frontend, git, prompts
or logs. Provider responses are treated as untrusted. Only permitted quotas and
automation are used; policy-rejected content is never re-routed to evade safety.
