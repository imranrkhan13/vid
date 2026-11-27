# Provider capability catalog (human-readable)

Human-readable companion to `provider_catalog.json` (README §3). The JSON is the
**single source of truth**; this page is a summary. Facts come from **official
documentation**; `?` means unknown — never assume an unknown capability is
present. Prices/limits change often, so re-verify before relying on them.

> **Research pass:** 2026-11-26. `verified` in the JSON means a human confirmed
> the fact against official docs on `verification_date`.

| Provider | Category | Verified | Duration | Price (verified) |
| --- | --- | --- | --- | --- |
| Mock Provider A | mock | yes (test double) | 10s | free (mock) |
| Google Veo (Gemini/Vertex API) | native_api | yes | 4/6/8s | $0.40/video w/ audio (720p/1080p); $0.20 w/o; 4K $0.60/$0.40 |
| Hugging Face Inference Providers | aggregator | yes | per model | provider price, no HF markup; PRO $2/mo credits |
| Hugging Face Spaces (permitted) | open_source_gpu | no | ? | per-Space queue |
| Runway | native_api | yes | 5/10s | gen4_turbo 5 credits/s ($0.05/s); gen4 12 credits/s |
| fal.ai | aggregator | yes | per model | per output second (~$0.03–$0.60/s) |
| Replicate | aggregator | yes | up to 8s | per output second (~$0.05–$0.40/s) |
| Luma Dream Machine (Ray 2) | native_api | yes | 5/9s | usage-based; up to 4K |
| Pika | native_api | no | ? | subscription credits |
| Kling AI (official API) | native_api | yes | 3–30s | Kling 4.0: 1.0 unit/s (720p) … 3.5 (4k) |
| MiniMax Hailuo | native_api | yes | 6/10s | $0.28 per 768P 6s; $0.49 per 1080P 6s |
| Stability AI (SVD, self-host) | open_source_gpu | yes | 2s | hosted API deprecated 2025-07-24; self-host |
| LTX-Video (Lightricks) | open_source_gpu | yes | ? | self-host; ~18GB VRAM (v0.9.x) |
| Wan (Alibaba) | open_source_gpu | yes | 5s | self-host; Apache-2.0; 24GB VRAM (TI2V-5B) |
| HunyuanVideo (Tencent) | open_source_gpu | yes | ? | self-host; 45–60GB VRAM (13B) |
| CogVideoX (Zhipu/THUDM) | open_source_gpu | yes | 6s | self-host; 2B Apache-2.0, ~5–16GB |
| Mochi 1 (Genmo) | open_source_gpu | yes | ? | self-host; Apache-2.0; ~60GB (BF16) |
| Open-Sora | open_source_gpu | no | ? | self-host |
| ComfyUI worker | open_source_gpu | no | ? | self-host; needs GPU registry (§20) |
| Local GPU (own hardware) | local | no | ? | electricity + hardware |
| Manual-assisted | manual_assisted | n/a | ? | free (human-in-the-loop) |
| OpenRouter | aggregator | no (video) | ? | pay-as-you-go |

**Count:** 22 resources across native_api, open_source_gpu, aggregator, local,
manual_assisted and mock.

## Per-provider facts to record (§3)

For each provider, record and date: official website, docs, endpoint,
authentication, models, text/image/video-to-video support, extension, first/last
frame conditioning, multiple references, character references, motion control,
audio, lip sync, durations, resolutions, aspect ratios, daily/weekly/monthly/trial
credits, reset schedule, cost per second, API pricing, concurrency, rate limits,
commercial rights, watermarks, download rights, retention, GPU/VRAM, feasibility,
and verification date.

## Rules

- Consumer credits are not API credits (e.g. Flow vs. Vertex; Kling app vs. API).
- Distinguish model developers, hosts and applications; do not double-count
  shared quotas (HF is an aggregator over multiple hosts).
- Mark unknowns explicitly (`null` / `?`); never assume a capability is present.
- Never hardcode these facts in source — the JSON is authoritative and is read at
  runtime. A test enforces this for the decision-making modules.
