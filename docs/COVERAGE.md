# Spec coverage (honest, per section)

Spec: [SPEC.md](SPEC.md) (the original README, 33 sections). Status key: **Done** = implemented and tested; **Partial** = works with stated gaps; **Not done** = absent. "Real" means verified against a real third party. Mock providers are simulations (labeled MOCK in the UI), not AI generation.

**The full spec is not complete.** Deviations: the frontend is Vite + React (JavaScript), not Next.js/TypeScript/Tailwind (section 5).

| # | Section | Status | Notes |
|---|---|---|---|
| 1 | Objective | Partial | End to end pipeline works with mock providers. Real AI generation through the app is not yet verified. |
| 2 | Core philosophy | Partial | Provider independence, honesty about credits (no hardcoded balances), QA gate kept. |
| 3 | Provider research | Partial | docs/PROVIDER_RESEARCH.md: 17 providers, attempted vs docs-only. Two real adapters exist. |
| 4 | Integration categories | Partial | native_api (Replicate, Pollinations), custom_http, manual, mock. No GPU worker category. |
| 5 | Technology stack | Partial | FastAPI, SQLAlchemy, PostgreSQL/SQLite, FFmpeg, OpenCV, Vite/React JS. **Deviation: not Next.js/TS/Tailwind.** |
| 6 | AI Director engine | Partial | Deterministic template planner by default. Optional BYOK planner via any OpenAI-compatible endpoint, contract-tested against a local stub only (never a real model). Falls back to the template planner on failure. |
| 7 | Creative memory | Done (basic) | Style, character bible, versions, handoff history. |
| 8 | Canonical project state | Done | Served with server paths removed. |
| 9 | Scene decomposition, timeline | Done | Storyboard approval, timeline, snapping to provider durations. |
| 10 | Adapter interface | Done | All methods, normalized error codes. |
| 11 | Provider selection | Partial | Modes, constraints, shared quota pool families. No learned scoring. |
| 12 | Credits and quotas | Done | Ledger with reserve/consume/release, balance source (verified_api, user_entered, unknown). Unknown cost fails closed. |
| 13 | Failover and continuation | Done | Tested: exhausted A to B, reconcile after timeout, C continues, final length exact, paid spend 0. Real-provider failover not tested. |
| 14 | Cross-model context transfer | Done | Handoff packages with semantic and visual context (last frame, style, characters). Mock providers only. |
| 15 | Character consistency | Partial | Reference assets and prompts. No identity-match test. |
| 16 | Motion continuity | Partial | Continuity state, last-frame handoff, end-position tracking on mocks. |
| 17 | Audio and narration | Partial | Upload narration, alignment, subtitles. No TTS provider. |
| 18 | Quality assurance | Partial | OpenCV heuristics (sharpness, motion, resolution, duration, palette). Not a human or model judgment. |
| 19 | Error handling | Done | 14 error codes, retries, bounded QA retry. |
| 20 | Open-source GPU workers | Not done | |
| 21 | Video processing | Done | Segmented low-memory compose, validation, MP4 and SRT export. |
| 22 | Frontend modules | Partial | Dashboard, new project, storyboard, timeline, console, handoffs, manual tasks, export, providers, characters. |
| 23 | Database architecture | Done | Per-user isolation; media mirrored in PostgreSQL with quotas. |
| 24 | Workflow state machine | Done | Durable worker, kill -9 resume tested. |
| 25 | API design | Done | REST and SSE, CSRF header on writes. |
| 26 | Benchmarking | Not done | Package exists, empty. |
| 27 | Cost optimization | Partial | Mode-based routing only. |
| 28 | Security | Partial | Keys encrypted server-side, per-user isolation, CSRF header, SSRF guard, upload limits. No third-party audit. |
| 29 | Repo structure | Partial | Close to the suggestion with the Vite deviation. |
| 30 | Roadmap | n/a | |
| 31 | End-to-end acceptance test | Partial | Passes with mock providers (SQLite and PostgreSQL 16). Not run with real providers. |
| 32 | Deliverables | Partial | Code, docs, tests, deploy. |
| 33 | Development principles | Partial | Followed; see limitations. |

## Verification split (not double counted)

- **Real third party verified:** Replicate minimax/video-01 produced a 6s clip from the owner's free account in Replicate's web UI. API path through this app: not verified.
- **Real, not verified live:** Pollinations adapter (needs a funded key; all its video models are flagged paid-only).
- **Mock only:** acceptance tests, failover, handoff, live smoke on https://vgoh.onrender.com.

## Status update (2026-11-27 03:20 IST)

Real vs mock, kept separate:

- Verified live (real infrastructure): web app on Render free, state in an isolated Neon free Postgres project (schema and rows confirmed in the Neon console), multi-user signup and login, per-user isolation, server-side secret storage.
- Verified with MOCK providers only (orchestration, not video quality): provider failover and context handoff, credit exhaustion, compose, export, crash recovery (kill -9), storage-budget pause and resume, bounded provider wait.
- Real AI generation: one real 6s clip was produced by hand in Replicate's web UI (account imranrkhan13, Try-for-Free model minimax/video-01). No API-driven real generation has been run. The Replicate API token has not been usable by the app: vault approval is pending, and API-side free eligibility is unverified.
- Free-trial mode for Replicate is restricted by server config to operator-verified immutable user IDs. Everyone else connects as a paid pool, which stays blocked until paid spend is approved.

Tests: SQLite and PostgreSQL 16 runs both pass (storage budget, tenant isolation, blob purge on project delete, storage-pause E2E, bounded wait, Replicate stub, security, durable media, acceptance, recovery).

Known gaps (what a user would still miss):

- Real provider breadth: only Replicate (minimax/video-01) has a real adapter, and it has never run through the app against the real API. Pollinations has an adapter that is not live-verified. Hugging Face, WaveSpeed, fal, Runway, Luma, Kling, Veo, MiniMax, Stability, Together, Leonardo, Hedra, Segmind, BytePlus and Alibaba are researched (docs/PROVIDER_RESEARCH.md) but have no adapter. Most are paid, so a free user cannot use them. Custom HTTP and manual import are the escape routes.
- Video quality: all end-to-end results so far are MOCK output. Real model quality, motion continuity and character consistency across real providers are unmeasured.
- AI planning and analysis: the default scene planner is a deterministic template; the optional LLM planner is tested only against a stub. There is no character identity matching, no model-based quality judgment, and no learned provider scoring.
- Audio: narration upload only. No text-to-speech provider, no music.
- Section 20 (open-source GPU workers) and section 26 (benchmarking) are not done.
- Hosting limits: free Render (cold starts, 0.1 CPU, 512MB RAM) and free Neon (storage cap). Long or high-resolution real videos may exceed them. The old Render Postgres expires in about 30 days and is no longer used.
- The full 33-section spec is not complete; see the table above.


## Update 04:00 (security + voice)
- Outbound DNS rebinding: custom HTTP provider and BYOK planner now use `app/core/netguard.py` (resolve per request, require all answers public, connect to the validated IP with original Host/SNI). Tested with a resolver that changes its answer.
- Secret canaries: provider errors scrub registered credentials (echoed upstream bodies); canary test checks error text, planner note, project, events and provider list.
- Narration voice: free local eSpeak NG (GPL-3.0, in-process, no network/key/cost). Robotic voice, NOT a neural voice. Per-segment timing mixed with ffmpeg into master narration; export includes it. "Add free voice" button.
- Still missing: neural/natural TTS, lip sync, music, real provider generation (no real clip verified yet).
- 04:05: speech verified by transcription of the live exported MP4 audio ("A friendly cloud holds tiny drops of water. When the drops grow heavy, they fall as rain."). This confirms intelligibility only, not voice quality.
- 04:05: scrubbing now also covers workflow events, job errors, the worker print, HTTP error details, a generic 500 handler (class name only) and every log record including exception tracebacks, for credentials the server has handled since boot. Limit: a secret is only known after it is first decrypted; a secret the server has never loaded cannot appear in its output.
- 04:05: THIRD_PARTY_NOTICES.md added (eSpeak NG GPL-3.0, source link, ctypes use, how to remove). GPL combined-work question is not legally reviewed.
