# AI Video Generation Harness (VGOH)

A provider-independent platform that orchestrates multiple AI video generation models and services to produce a complete, downloadable video from a creative brief.

> **Status: specification only.** This README describes the planned design. Nothing below is claimed as built, tested or benchmarked. Provider facts (credits, pricing, limits, capabilities) must come from verified official documentation and must never be hardcoded. Install and run commands are intentionally absent until an implementation exists.

---

## 1. Objective

Build a production-ready platform with a central intelligence layer that:

- understands the whole creative project
- preserves character consistency
- tracks credits and quotas
- handles failures and resumes incomplete scenes
- validates output
- composes the final video

It should make the best use of legitimate free credits, permitted inference resources, open-source models and optional paid APIs. New providers must be addable without rewriting the core.

## 2. Core philosophy

Providers are execution engines. The platform owns:

- creative intelligence, project and character memory
- storyline, storyboard, scene instructions, visual style, camera directions
- audio timeline
- provider selection and credit management
- progress, quality control, recovery and final composition

The platform never depends on a provider-specific conversation or session.

## 3. Provider research phase (planned)

Research Google Flow, Gemini and Vertex AI, Hugging Face Inference Providers and permitted Spaces, Adobe Firefly, Wan, HunyuanVideo, LTX Video, CogVideoX, Runway, fal.ai, Replicate, Kling, Luma, MiniMax/Hailuo, Pika, WaveSpeedAI, ComfyUI, Colab, Kaggle and other legitimate APIs.

- Distinguish model developers, hosts and applications. Do not double-count shared quotas.
- Use official documentation. Consumer credits are not necessarily API credits. Mark unknowns explicitly.

Per provider, record: official website, docs, endpoint, authentication, models, text/image/video-to-video, extension, first/last frame conditioning, multiple references, character references, motion control, audio, lip sync, durations, resolutions, aspect ratios, daily/weekly/monthly/trial credits, reset schedule, cost per second, API pricing, concurrency, rate limits, commercial rights, watermarks, download rights, retention, GPU/VRAM, feasibility and verification date.

Outputs: a machine-readable capability catalog and a human-readable comparison.

## 4. Integration categories

| Category | Description |
| --- | --- |
| Native API | Documented submission, asynchronous monitoring, secure downloads |
| Open-source GPU inference | Local/remote workers, ComfyUI, supported endpoints |
| Manual-assisted | Generate prompts and references for official consumer interfaces, import clips, resume |
| Custom/enterprise | Configurable private HTTP endpoints |

**Not allowed:** unauthorized scraping, CAPTCHA bypass, account restriction bypass, watermark removal, abusive credit farming. Use only permitted quotas and automation.

## 5. Proposed technology stack

Inspect any existing code first. Preferred choices:

- **Frontend:** Next.js, TypeScript, Tailwind, accessible components, timeline UI
- **Backend:** Python, FastAPI, Pydantic, SQLAlchemy, PostgreSQL
- **Workflows:** Celery/Redis with persistent PostgreSQL workflow state; evaluate Temporal if justified
- **Storage:** S3-compatible, MinIO, local filesystem adapter
- **Media:** FFmpeg, FFprobe, OpenCV, Pillow
- **AI:** provider-independent LLM adapter, multimodal analysis, structured JSON, configurable vision, optional embeddings
- **Deployment:** independent frontend/backend/workers, optional Docker Compose, native installation, remote GPU workers, environment configuration

Orchestration must not require a powerful local GPU.

## 6. AI Director engine

**Inputs:** title, concept, script, target duration, visual style, audience, characters, character and scene references, narration, music, aspect ratio, resolution, FPS, camera and animation requirements, commercial-use and watermark requirements, maximum budget.

**Outputs:** story, character and style bibles, storyboard, scene graph, shot descriptions, dependencies, camera/motion instructions, audio timeline, prompts, negative prompts, acceptance criteria. Total scene duration is validated.

## 7. Persistent creative memory

Stores original requirements and instructions, character identity, appearance, facial features, proportions, clothing, palette, turnaround images, emotions, references, environments, lighting, camera language, animation style, story progression, completed/rejected scenes, accepted clips, ending frames, positions, actions, dialogue, narration timestamps, remaining duration, technical requirements and approved decisions.

- Important instructions are versioned.
- Supports project memory and reusable character libraries.
- Structured, versioned data is authoritative. Embeddings supplement retrieval and never replace it.

## 8. Canonical project state

```json
{
  "project_id": "video-project-001",
  "title": "Educational Animation",
  "target_duration_seconds": 32,
  "accepted_duration_seconds": 12,
  "remaining_duration_seconds": 20,
  "aspect_ratio": "9:16",
  "resolution": "1080x1920",
  "fps": 30,
  "style_version": 1,
  "character_bible_version": 1,
  "audio_strategy": "external_master_narration",
  "completed_scenes": ["scene_001", "scene_002", "scene_003"],
  "next_scene": "scene_004",
  "last_accepted_provider": "google_flow",
  "last_accepted_frame": "assets/scene_003/last_frame.png",
  "status": "generating"
}
```

Progress is calculated from accepted clips on the final timeline, not completed API requests.

## 9. Scene decomposition and timeline

A 32-second video may be eight four-second scenes, but durations are not fixed. The timeline engine supports dynamic durations, provider-compatible mapping, approved trimming, shot boundaries, dependencies, frame-accurate editing, transitions, entrances/exits, camera continuity, audio alignment, regeneration, candidate clips and user selection, all on one canonical media timeline.

## 10. Provider adapter interface

```python
class VideoProvider:
    async def get_capabilities(self): raise NotImplementedError
    async def check_health(self): raise NotImplementedError
    async def get_quota(self): raise NotImplementedError
    async def estimate_cost(self, request): raise NotImplementedError
    async def submit_generation(self, request): raise NotImplementedError
    async def get_job_status(self, job_id): raise NotImplementedError
    async def download_result(self, job_id): raise NotImplementedError
    async def cancel_job(self, job_id): raise NotImplementedError
    async def normalize_error(self, error): raise NotImplementedError
```

Adapters translate canonical requests into provider payloads. Provider formatting stays out of the scheduler. Documented webhooks or polling are supported, with contract tests.

## 11. Provider selection engine

Hard filters: health, valid credentials, generation mode, references, duration, aspect ratio, commercial rights, watermark requirements, sufficient quota/budget, privacy.

Baseline weights:

| Factor | Weight |
| --- | --- |
| Character consistency | 25% |
| Quality | 25% |
| Legitimate free credits | 20% |
| Cost efficiency | 10% |
| Reliability | 10% |
| Speed | 5% |
| Resolution | 5% |

Weights adapt by project type: children's animation (consistency, motion), documentary (realism, lighting, environment), promotions (brand and text accuracy). Never select on free credits alone.

## 12. Credit and quota management

A central ledger tracks credits by provider, credential, billing account, organization, shared quota pool and model: available, reserved and consumed credits, estimated vs actual cost, daily/monthly/trial limits, reset date and time zone, last success, restrictions, concurrency and confidence.

Balance sources: verified balance API, usage report, user-entered, estimated, unknown. **Unknown is not unlimited.** Credits are reserved atomically, reconciled against actual usage, and parallel overspending is prevented. Accounts are never rotated to circumvent restrictions.

## 13. Automatic failover and continuation

Example: Provider A completes 12 of 32 seconds, then exhausts credits. The platform:

1. preserves accepted clips and persists state
2. marks A exhausted and records the reset time if known
3. calculates the remaining 20 seconds and the next incomplete scene
4. retrieves creative context, previous ending frame, canonical references, style bible, positions and motion
5. builds a continuation package and selects an eligible provider
6. translates inputs, submits, validates and continues

Rules:

- Never restart or regenerate accepted scenes unnecessarily.
- Reconcile ambiguous job status before retrying.
- Use idempotency keys and durable job IDs.
- If no provider is eligible, pause in `WAITING_FOR_PROVIDER` and resume after a permitted reset, added credits or an enabled provider.
- Never spend paid credits without approval.

## 14. Cross-model context transfer

A handoff includes project and scene IDs, instruction version, scene description, character profiles and images, previous frame or optional clip, first/last frame requirements, position/motion, emotion, camera, lighting, environment, style, dialogue/narration, negative prompts, duration, timeline placement, remaining duration and QA requirements.

```json
{
  "scene_id": "scene_004",
  "handoff_reason": "quota_exhausted",
  "previous_provider": "google_flow",
  "remaining_project_seconds": 20,
  "requested_clip_seconds": 4,
  "continuity": {
    "previous_frame": "scene_003_last_frame.png",
    "character_references": ["character_front.png", "character_side.png"],
    "motion": "Continue walking forward",
    "camera": "Smooth side-tracking shot",
    "lighting": "Warm afternoon light",
    "style": "Soft colorful 3D educational animation"
  }
}
```

Semantic and visual context transfers. Neural latents, hidden memory and random seeds do not transfer across unrelated models. If references are unsupported, select another provider or use an intentional camera cut.

## 15. Character consistency engine

Reusable profiles hold a stable ID, front/side/back/three-quarter views, close-up, expressions, clothing, palette, proportions and approved style. Frames are compared with references using configurable visual similarity, color analysis and multimodal analysis. Detects wrong identity, changed face/clothes/colors/proportions, extra or missing limbs or characters, disappearance and temporal inconsistency. Provider success alone is not acceptance.

## 16. Motion and temporal continuity

Detects foot sliding, floating, unnatural walking, teleportation, speed changes, flicker, morphing, camera jumps, inconsistent background, incorrect interactions and abrupt transitions. Extracts first/middle/final frames, using more frames or optical flow for difficult motion. Conditions on previous ending frames where supported, and uses deliberate camera cuts when needed. Interpolation cannot substitute for correct animation.

## 17. Audio and narration

Audio stays independent where possible. Supports uploaded MP3/WAV, TTS, consistent voice, music, effects, dialogue timing, normalization and lip sync. One master audio timeline; actions align to narration timestamps (forced alignment or speech timestamps); unwanted provider audio is muted where appropriate; narration is preserved across provider switches. A dedicated lip-sync stage is used when required. No independently generated narration per scene.

## 18. Quality assurance pipeline

Evaluates prompt adherence, identity, style, temporal coherence, motion, continuity, resolution, aspect ratio, audio alignment, unexpected text, visible watermark, corrupt media, duration and safety.

```json
{
  "character_consistency": 91,
  "style_consistency": 94,
  "motion_quality": 87,
  "scene_continuity": 92,
  "prompt_adherence": 95,
  "audio_alignment": 90,
  "visible_watermark_detected": false,
  "decision": "accepted"
}
```

Scores are heuristic and must be calibrated with approved/rejected clips. On failure: record the reason, adjust instructions, check the retry budget, retry or switch provider, then escalate to manual review. No unlimited loops.

## 19. Error handling and resilience

Normalized errors: `QUOTA_EXHAUSTED`, `CREDITS_EXHAUSTED`, `RATE_LIMITED`, `AUTHENTICATION_FAILED`, `PROVIDER_UNAVAILABLE`, `MODEL_UNAVAILABLE`, `UNSUPPORTED_REQUEST`, `GENERATION_TIMEOUT`, `AMBIGUOUS_JOB_STATE`, `GENERATION_FAILED`, `CONTENT_POLICY_REJECTION`, `QUALITY_VALIDATION_FAILED`, `DOWNLOAD_FAILED`, `GPU_OUT_OF_MEMORY`.

Mechanisms: backoff, retry budgets, circuit breakers, health checks, recovery, checkpoints, cancellation, reconciliation, duplicate prevention, dead-letter queues. Policy-rejected content is never rerouted to evade safety. GPU OOM recovery: smaller models, reduced resolution, memory optimization or alternate resources.

## 20. Open-source GPU worker management

Workers report GPU type, total/free VRAM, installed models, disk, jobs, precision, quantization and health. Memory is estimated before execution and compatible resources are chosen. Free Colab/Kaggle GPUs are not assumed to run every model. Only permitted interactive sessions and authorized endpoints are used. Results persist outside temporary notebooks.

## 21. Video processing and final composition

FFmpeg pipeline: codec, resolution, FPS and aspect normalization, trimming, approved transitions, color correction, audio sync/mixing/fades, subtitles, MP4 output. Aspect ratios 9:16, 16:9, 1:1. Resolutions 720p/1080p initially, higher where supported. Upscaling is labeled accurately. Watermarks are never removed; visible watermarks are rejected if the project prohibits them. Output is validated before export.

## 22. Frontend modules

- **Dashboard:** projects, credits, health, progress
- **New project:** prompts, scripts, references, audio, duration, resolution, style
- **Character Studio:** reusable profiles
- **Storyboard editor:** review and edit scenes
- **Timeline:** duration, status, provider, preview, quality
- **Provider management:** credentials, enable/disable, capabilities, balances
- **Router configuration:** quality, cost, speed, consistency
- **Live console:** events, transitions, retries, remaining duration
- **Quality review:** compare, approve, reject
- **Export center:** previews, MP4, storyboard, subtitles, reports
- **Manual tasks:** prompts and assets, import and resume

## 23. Database architecture

Normalized PostgreSQL tables: `users`, `video_projects`, `project_versions`, `characters`, `character_reference_assets`, `style_profiles`, `storyboards`, `video_scenes`, `scene_dependencies`, `scene_versions`, `generation_providers`, `provider_models`, `provider_capabilities`, `provider_credentials`, `quota_pools`, `provider_quotas`, `credit_transactions`, `generation_jobs`, `generation_attempts`, `generation_artifacts`, `project_checkpoints`, `context_handoffs`, `quality_reports`, `audio_tracks`, `narration_segments`, `media_timeline`, `export_jobs`, `workflow_events`, `audit_logs`.

Use foreign keys, unique constraints, indexes and transactions. Media lives in object storage, metadata in PostgreSQL. Credentials are encrypted and never exposed in the frontend, Git, prompts or logs.

## 24. Workflow state machine

States: `DRAFT`, `PLANNING`, `STORYBOARD_READY`, `AWAITING_USER_APPROVAL`, `READY`, `SELECTING_PROVIDER`, `GENERATING`, `DOWNLOADING`, `VALIDATING`, `RETRYING`, `SWITCHING_PROVIDER`, `MANUAL_ACTION_REQUIRED`, `WAITING_FOR_CREDITS`, `WAITING_FOR_PROVIDER`, `COMPOSING`, `FINAL_VALIDATION`, `COMPLETED`, `FAILED`, `PAUSED`, `CANCELLED`.

Transitions are persisted. Project and scene states are separate. Work resumes from durable checkpoints after restarts.

## 25. API design (planned)

```
POST   /api/v1/projects
GET    /api/v1/projects/{id}
PATCH  /api/v1/projects/{id}
POST   /api/v1/projects/{id}/plan
POST   /api/v1/projects/{id}/generate
POST   /api/v1/projects/{id}/pause
POST   /api/v1/projects/{id}/resume
POST   /api/v1/projects/{id}/cancel
GET    /api/v1/projects/{id}/scenes
POST   /api/v1/scenes/{id}/regenerate
POST   /api/v1/scenes/{id}/approve
POST   /api/v1/scenes/{id}/reject
POST   /api/v1/scenes/{id}/import
GET    /api/v1/providers
POST   /api/v1/providers/{id}/connect
GET    /api/v1/providers/{id}/health
GET    /api/v1/providers/{id}/quota
GET    /api/v1/projects/{id}/events
GET    /api/v1/projects/{id}/timeline
POST   /api/v1/projects/{id}/export
GET    /api/v1/exports/{id}
```

Live updates via SSE or WebSockets. Authentication, authorization, validation and rate limiting are required.

## 26. Provider benchmarking

Use identical references and comparable instructions. Test scenarios: cartoon walking, two-character interaction, speech, animal motion, documentary, camera tracking, object interaction, image-to-video, previous-frame continuation, multi-character consistency.

Record latency, cost per generated and accepted second, acceptance/failure rates, identity, motion, visual quality, continuity and commercial eligibility. Maintain model and provider scores and route using measured results. Datasets and scoring methods are versioned.

## 27. Cost optimization

Modes: free credits only, open-source only, lowest cost, balanced, maximum quality, custom budget. **Default is zero paid spending until approved.** Estimate before, reconcile after. Track project cost, rejected-generation cost and cost per accepted second. Reuse validated clips and cached assets, and avoid redundant requests.

## 28. Security and compliance

Encrypted credentials, redaction, project access control, signed URLs, validation, safe uploads, size limits, rate limits, audit logs, secure downloads, network restrictions, respect for provider terms, model licenses and commercial rights, deletion controls. External responses are untrusted and embedded instructions are never executed. Assets are not exposed publicly without authorization.

## 29. Suggested repository structure

```
video-harness/
  frontend/
  backend/
    app/
      api/ core/ database/ auth/ directors/
      creative_memory/ storyboards/ characters/
      orchestration/ routing/
      providers/
        base/ google/ huggingface/ runway/ fal/
        replicate/ manual/ comfyui/ custom/
      credits/ continuity/ quality/ audio/ media/
      storage/ workflows/ benchmarks/ workers/
    migrations/ tests/
  gpu-workers/ infrastructure/ docs/ scripts/
```

Keep integrations isolated from core orchestration.

## 30. Roadmap

| Phase | Scope |
| --- | --- |
| 1 | Inspect code, verify providers, catalog, database/frontend/backend architecture, interface, persistent state |
| 2 | Two verified API providers, one manual provider, mock provider, projects, storyboard, scenes, assets, credits |
| 3 | Routing, errors, retries, reservations, handoff, pause/resume, crash recovery |
| 4 | Character/style memory, ending frames, reference conditioning, QA, manual approval |
| 5 | Narration alignment, timeline, FFmpeg, validation, MP4 |
| 6 | Additional verified providers, GPU workers, benchmarks, router optimization, motion QA |
| 7 | Concurrency, outages, exhaustion, ambiguous requests, crashes, credential isolation, media security, monitoring, deployment docs |

## 31. Mandatory end-to-end acceptance test

Create a 32-second educational animation: eight scenes, consistent characters and style, uploaded master narration, smooth movement, 9:16, 1080x1920.

1. Provider A completes 12 accepted seconds.
2. Simulate exhaustion. Preserve clips. Verify 20 seconds remain.
3. Select provider B. Transfer context and references. Continue.
4. Simulate B timeout. Reconcile before retry. Select C if necessary.
5. Complete scenes. Validate identity and style.
6. Align narration. Compose.
7. Verify duration within one output frame. Verify budget.
8. Verify accepted scenes were not regenerated unnecessarily.
9. Restart the backend during execution and verify recovery.
10. Produce an execution report.

Use deterministic mocks first, real verified providers when credentials and permitted credits are available.

## 32. Deliverables

Working application, source, migrations, provider research report, capability catalog, architecture and API docs, workflow diagrams, configuration examples, native installation, optional Docker, GPU worker instructions, automated tests, benchmarks, demo project, recovery docs, security docs and known limitations.

## 33. Development principles

- No frontend-only prototype. No fake integrations or fake results.
- Do not hardcode credits, pricing or unsupported capabilities.
- Label and isolate mocks.
- Do not modify unrelated functionality.
- Inspect the existing project and produce an architecture and gap analysis first, then build in incremental, testable phases.
- Prioritize durable creative memory, adapters, quota management, handoff and recovery over provider count.

**Goal:** a stateful AI video production harness that completes creative projects across independent engines, preserves approved decisions and recovers from failures with minimal user intervention.
