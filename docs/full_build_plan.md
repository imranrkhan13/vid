# VGOH — Full Multi-Provider Build Plan

**Goal:** a harness that turns one creative brief into one finished, downloadable
video by orchestrating 20+ independent video-generation resources — and keeps
going when any one of them runs out of credits.

**The twist (the thing this plan is really about):** Flow (or Runway, or any
provider) is mid-video when its credits hit zero. A different provider picks up
**the exact same video, at the exact scene, in the exact style**, with full
knowledge of: what has been built, what remains, the target duration left, the
narration, the character look, and the visual style. The user never sees a seam.

> This plan is a handoff document. It states what exists today, what to build,
> in what order, and how each phase is proven done. It never invents provider
> facts, never fakes a result, and never automates a restricted interface.

---

## 0. The one-sentence invariant

> **A provider is a swappable engine. The project — its plan, style, characters,
> accepted clips, remaining duration and credits — is the durable truth that
> outlives every engine.**

Every design decision below serves that invariant.

---

## 1. Baseline — what already exists

Built and passing (133 tests) under `backend/`:

| Area | Module | State |
| --- | --- | --- |
| Error taxonomy (§19) | `app/core/errors.py` | done — 14 normalized codes, retryable/switch/terminal sets |
| State model (§8, §24) | `app/core/state.py`, `app/orchestration/state_machine.py` | done — project + scene enums, legal transitions, BFS routing |
| DB schema (§23) | `app/models/*` | done — projects, storyboard, providers, credits, generation, audio |
| Provider interface (§10) | `app/providers/base.py`, `mock.py`, `real.py`, `registry.py` | done — ABC + labeled mock + real HTTP adapters for every catalog provider |
| Provider catalog (§3) | `docs/provider_catalog.json` | seeded — unknowns = `null`, unverified flagged |
| Credits ledger (§12) | `app/credits/ledger.py` | done — reserve/reconcile/release; unknown ≠ unlimited; paid needs approval |
| Selection (§11) | `app/routing/selector.py` | done — hard filters + weighted score |
| Orchestrator + failover (§13) | `app/orchestration/orchestrator.py` | done — retries, circuit breaker, handoffs, checkpoints |
| Context handoff (§14) | `app/continuity/handoff.py` | done — semantic/visual context only |
| QA (§18) | `app/quality/qa.py` | done — basic checks |
| Timeline (§9) | `app/timeline/engine.py` | done — canonical ordered timeline |
| Composition (§21) | `app/media/composer.py` | done — FFmpeg path + manifest fallback |
| API (§25) | `app/api/*` | done — REST + SSE |
| Acceptance (§31) | `backend/tests/test_e2e_acceptance.py` | passing |

**Gaps this plan closes:** real adapters, 20+ provider research, full credit
lifecycle (daily/monthly/trial), durable task execution, richer consistency,
real audio alignment, benchmarking/cost optimizer, frontend, security hardening.

---

## 2. Provider research & catalog (20+ resources) — Phase 1 deliverable

**Method (per §3):** for each resource record — official site, docs URL, endpoint,
auth, models, capabilities (t2v/i2v/v2v/extension/first-last-frame/multi-ref/
character-ref/motion/audio/lip-sync), durations, resolutions, aspect ratios,
credit grants (daily/weekly/monthly/trial + reset schedule), cost/sec, API
pricing, concurrency, rate limits, commercial rights, watermark, download rights,
retention, GPU/VRAM (self-host), feasibility, **verification date**.

**Target list (categories matter — do not double-count shared quotas):**

| # | Resource | Category | Notes |
| --- | --- | --- | --- |
| 1 | Google Flow / Gemini / Vertex Veo | native_api | consumer credits ≠ API credits |
| 2 | Hugging Face Inference Providers | open_source_gpu | hosts + models, quota shared |
| 3 | Hugging Face Spaces (permitted) | open_source_gpu | per-Space limits |
| 4 | Runway | native_api | |
| 5 | fal.ai | native_api | |
| 6 | Replicate | native_api | |
| 7 | Luma Dream Machine | native_api | |
| 8 | Pika | native_api | |
| 9 | Kling (official API) | native_api | |
| 10 | MiniMax Hailuo | native_api | |
| 11 | Stability AI | native_api | |
| 12 | Lightricks LTX | open_source_gpu | self-host weights |
| 13 | Wan / Wan2.x | open_source_gpu | self-host |
| 14 | HunyuanVideo | open_source_gpu | self-host |
| 15 | Mochi 1 | open_source_gpu | self-host |
| 16 | CogVideoX | open_source_gpu | self-host |
| 17 | Open-Sora | open_source_gpu | self-host |
| 18 | ComfyUI worker | open_source_gpu | needs GPU registry (§20) |
| 19 | Local diffusion (your GPU) | local | cost = electricity |
| 20 | Manual-assisted consumer UI | manual_assisted | prompt/asset export + import + resume |
| 21 | (buffer) partner/aggregator APIs | native_api | e.g. aggregators that front multiple models |

**Rules:** consumer credits are not API credits; distinguish developer vs host vs
app; mark unknowns explicitly; **never hardcode facts in source** — the catalog is
the single source of truth and the app reads it at runtime.

**Done when:** every entry has a `verified` flag + date; at least 8 providers have
at least one capability `true`; unknowns are `null`; a loader test asserts no
provider fact is hardcoded in `app/`.

**Status — DONE (2026-11-26).** `docs/provider_catalog.json` now holds **22
resources** with the full §3 field set (capabilities, durations, aspect ratios,
pricing, rate limits, retention, credit grants, GPU/VRAM, license, verification).
14 real providers are `verified` against official docs this pass (Veo, HF, Runway,
fal, Replicate, Luma, Kling, MiniMax, SVD, LTX, Wan, Hunyuan, CogVideoX, Mochi).
`app/providers/catalog.py` validates the JSON into `ProviderCatalogEntry` and
fails loudly on malformed/duplicate entries. `tests/test_catalog.py` (19 tests)
enforces: 20+ entries, unique ids, unknowns stay `null`, verified entries cite a
source+date, and **no provider facts or ids are hardcoded in decision code**.

---

## 3. Credit & quota engine — the fuel gauge

Today: single `available_credits` per provider. Extend to a full lifecycle.

**Quota model per provider:**

```
{ daily_limit, daily_used, daily_reset_at,
  monthly_limit, monthly_used, monthly_reset_at,
  trial_limit, trial_used, trial_expires_at,
  api_paid_balance, unit_cost_per_second, currency,
  balance_source: verified | estimated | unknown }
```

**Rules (carry over and extend §12):**
- **Unknown ≠ unlimited.** Unknown balance ⇒ ineligible until verified/estimated.
- **Paid spend requires explicit approval** — a project-level `allow_paid` flag.
- **Reserve → reconcile → release.** Never act on an unreconciled reservation.
- **Reset-aware scheduling:** a provider that is exhausted *now* but resets at
  00:00 UTC is a *scheduled* candidate, not a dead one — the planner can defer a
  scene rather than burn a paid credit.
- **Aggregate budget:** project-level cap (`max_credits`, `max_paid_spend`) across
  all providers; the optimizer (§9) chooses the cheapest eligible path.

**Done when:** a provider whose daily credits run out mid-run is skipped, and a
provider whose daily credits reset mid-run becomes eligible again; tests cover
daily, monthly and trial exhaustion + reset.

---

## 4. Seamless failover + creative memory (the twist) — the core deliverable

**Trigger:** credits exhausted, hard failure, policy block, or rate limit — at
scene *N* of *M*.

**What must survive (persistent creative memory):**
- The storyboard plan and scene list with durations.
- **Remaining duration** = target − Σ accepted clip durations.
- Accepted clip artifacts + canonical timeline order (§9).
- **Style sheet** (palette, lighting, lens, grade, motion feel).
- **Character bible** (identity embeddings/refs, wardrobe, voice).
- Narration script + per-scene timing.
- All provider attempts, ledger state, and the handoff history.

**Handoff package (§14) — what crosses the boundary, and what never does:**
- ✅ Semantic: scene intent, style sheet, character refs, last accepted frame,
  narration cue, remaining duration, aspect/res/fps.
- ❌ Never: hidden model memory, latents, seeds, another provider's private state.

**The switch algorithm (already prototyped in the orchestrator, hardened here):**
1. Reconcile the dying provider's in-flight job (no double-charge).
2. Mark it exhausted/failed in the ledger; open its circuit breaker.
3. Build candidates from remaining balance + capabilities; exclude `tried`.
4. Score → pick next best (prefer *continuity*: a provider whose engine is
   closest to the previous one, to minimize visual drift).
5. Emit a handoff package; resume at scene *N*; keep accepted scenes untouched.
6. If none eligible: pause in `WAITING_FOR_PROVIDER`, notify, auto-resume on reset.

**Continuity anchoring (reduces drift across engines):**
- Feed the **last accepted frame** as first-frame conditioning (i2v) where supported.
- Re-assert the **style sheet + character bible** verbatim in the new prompt.
- Post-process the first new clip to match the previous clip's color/grade (LUT).
- QA compares new clip vs neighbors for palette/motion continuity (§18).

**Done when:** the §31 test shows Flow-style exhaustion → next provider takes over
at the same scene with accepted clips preserved, remaining duration correct, and a
continuity QA pass — all asserted.

---

## 5. Consistency systems (character / motion / visual style)

| Layer | Mechanism | Where |
| --- | --- | --- |
| Character | identity refs + embedding, wardrobe lock, per-scene char sheet | `continuity/` + storyboard |
| Motion | motion descriptors per scene; continuity from last frame | `continuity/handoff.py` |
| Style | project style sheet, applied to every prompt; LUT grade match | `directors/planner.py` + `media/` |
| Continuity QA | palette + motion diff vs neighbors; reject & retry on drift | `quality/qa.py` |

**Done when:** a deliberate style drift injected into a mock clip is caught by QA
and triggers a retry/switch.

---

## 6. Audio & narration synchronization (§17)

- Generate narration (TTS provider) → measure per-segment durations.
- Align scenes to narration beats; adjust clip durations within tolerance.
- Mix music/SFX; duck under narration.
- Export muxed A/V (FFmpeg).

**Done when:** a project with narration produces an A/V file whose scene
boundaries land within tolerance of the narration cues.

---

## 7. Backend, database & APIs (§23, §25)

- **DB:** SQLite default; PostgreSQL via `VGOH_DATABASE_URL`. Migrations
  (Alembic) when Postgres becomes primary.
- **Task execution:** in-process default; Redis + Celery/Temporal adapter for
  durable long-running jobs and restart-safety.
- **Storage:** local FS default; S3/MinIO adapter for media bytes.
- **REST/SSE (existing surface, extended):**
  - `POST /projects`, `GET /projects/{id}`, `POST /projects/{id}/plan`
  - `POST /projects/{id}/generate`, `GET /projects/{id}/status`
  - `GET /projects/{id}/events` (SSE progress)
  - `GET /projects/{id}/report`
  - `GET /providers`, `POST /providers/{id}/quota`
  - `GET /download/{artifact}`
- **Idempotency keys** on every mutating call; resumable runs.

**Done when:** a run can be killed and resumed via API with no duplicate
generation or double-charge.

---

## 8. Composition & export (§21)

- Normalize resolution/fps/codec; concat in timeline order; apply LUT grade.
- Mux audio; export MP4 (H.264) + optional ProRes.
- **FFmpeg optional:** when absent, emit a manifest (no fake file). Present here.

**Done when:** with FFmpeg installed, `GET /download` returns a playable MP4 of
the target duration; without it, a manifest is returned and clearly labeled.

---

## 9. Security, benchmarking & cost optimization (§28, §19)

**Security:** encrypt credentials at rest; never log/expose secrets; treat
provider responses as untrusted; enforce quota/policy; **no scraping, no
CAPTCHA/restriction bypass, no watermark removal, no credit farming.**

**Benchmarking:** track per-provider success rate, latency, cost/sec, QA pass
rate → feed the selector's scores (closes the loop from §11).

**Cost optimization:** cheapest-eligible-path routing; prefer free/trial credits;
defer to a reset instead of paying; cap total spend; report cost per finished video.

**Done when:** the report shows provider scores updated from real runs and a
cost figure derived from the catalog (never hardcoded).

---

## 10. Seven-phase roadmap

| Phase | Theme | Deliverable | Exit criteria |
| --- | --- | --- | --- |
| **1** | **Foundation** *(mostly done)* | schema, state machine, provider interface, ledger, selector, orchestrator, mock E2E | 31 tests green; §31 mock acceptance passes |
| **2** | **Provider research & catalog** | 20+ researched entries with verified flags | loader test: no hardcoded facts; ≥8 usable |
| **3** | **First real adapters** | 2 real providers + 1 manual-assisted, behind the ABC | contract tests; live smoke test (creds) |
| **4** | **Credit lifecycle & scheduler** | daily/monthly/trial + resets + deferral + spend caps | exhaustion/reset tests |
| **5** | **Continuity & consistency** | handoff hardening, style/character bible, continuity QA | drift caught + recovered |
| **6** | **Audio, composition, export** | TTS sync, mix, FFmpeg export + download | playable A/V within tolerance |
| **7** | **Scale, security, cost, frontend** | durable task runner, Postgres, S3, benchmarking, cost optimizer, §22 UI | full E2E on real providers; cost report |

Phases 2–4 are the critical path for "never stops when credits run out."

---

## 11. End-to-end acceptance testing (§31)

**Deterministic mock scenario (already passing):**
- 32s / 8-scene project; provider A does 12s then exhausts; B does 8s then times
  out (reconciled before retry); C finishes.
- Asserts: A's clips preserved, remaining duration correct, failover A→B→C,
  timeout reconciled once, duration within one frame of 32s, **accepted scenes
  never regenerated**, **restart recovery from durable state**, execution report
  emitted.

**Real-provider acceptance (Phase 7):** same scenario against ≥2 live providers
with tiny durations, asserting continuity QA passes across the handoff.

**Run:** `cd backend && python -m pytest -q` · `python scripts/run_demo.py`

---

## 12. Non-goals & guardrails

- ❌ No fake integrations or fabricated results (real adapters stay inert until
  verified credentials + official docs exist).
- ❌ No hardcoded provider facts — catalog is authoritative.
- ❌ No scraping, CAPTCHA/restriction bypass, watermark removal, or credit farming.
- ❌ No latency/seed/private-state transfer across providers.
- ✅ Mocks are always labeled (`is_mock=True`) and isolated.
- ✅ `README.md` stays unchanged.

---

## 13. Open decisions for you

1. **Task runner:** stay in-process for now, or adopt Celery/Temporal in Phase 7?
2. **Which 2 real providers** to implement first (I'd suggest one native API +
   one open-source/self-host to prove both paths)?
3. **Paid credits:** allowed with a cap, or free/trial only?
4. **Frontend (§22):** build in Phase 7, or bring it forward?
