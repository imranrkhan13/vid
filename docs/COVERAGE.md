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
| 6 | AI Director engine | Partial | Deterministic template planner, no LLM. |
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
