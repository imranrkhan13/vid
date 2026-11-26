# VGOH: AI Video Generation Harness

Multi-user web app that plans a video from a brief, generates scenes through the providers each user connects (bring your own key), switches to the next provider when credits run out while carrying the full creative context, and exports one MP4.

- Live demo: https://vgoh.onrender.com (free host: cold starts, small CPU, short-lived disk)
- Original spec: [docs/SPEC.md](docs/SPEC.md). Honest per-section status: [docs/COVERAGE.md](docs/COVERAGE.md). Providers tried: [docs/PROVIDER_RESEARCH.md](docs/PROVIDER_RESEARCH.md).

## Honest status

- Mock providers (MOCK A/B/C) are simulations for testing orchestration. They are not AI generation and are labeled MOCK everywhere.
- Real adapters: Replicate (minimax/video-01) and Pollinations. Neither has been verified end to end through the app with real credits.
- Not complete versus the spec. The frontend is Vite + React (JavaScript), not Next.js/TypeScript/Tailwind.
- Quality checks are OpenCV heuristics, not a judgment of artistic quality or character identity.

## Run locally

```
cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
cd ../frontend && npm i && npm run build      # outputs frontend/out, served by the backend
cd ../backend && .venv/bin/uvicorn app.main:app --port 8000
```
Tests: `cd backend && .venv/bin/python -m pytest tests -q` (PostgreSQL: `python tests/run_on_postgres.py`).

## Security model

Each user's provider keys are encrypted on the server, never returned to the browser, and used only for that user's jobs. Writes need an `X-VGOH` header. Projects, media and credits are isolated per user. Free-trial pool mode is limited to operator-verified user IDs (env `VGOH_FREE_TRIAL_VERIFIED_USER_IDS`); any other connection of a possibly paid provider stays blocked until paid spend is approved.

## Known limitations

- Free hosting: ephemeral disk (media is mirrored into PostgreSQL within per-user and total budgets; over budget the project pauses with a clear message), free database expires after 30 days, 512MB RAM.
- Replicate does not expose its free allowance by API; exhaustion shows up as HTTP 402 and hands off to the next provider.
- No GPU workers, no benchmarking, no LLM director, no TTS.
