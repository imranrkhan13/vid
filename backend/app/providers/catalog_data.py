"""Research catalog rows for real providers. Populated from official documentation with verification dates.
Rows with implemented=False have no adapter yet and cannot be connected for generation."""
CATALOG: list[dict] = [
    dict(id="pollinations", name="Pollinations (gen.pollinations.ai)", category="native_api", implemented=True, verification="adapter-untested-live",
         host_family="pollinations", docs_url="https://gen.pollinations.ai/api/docs/open-api/generate-schema",
         notes="Async media jobs with per-second pollen pricing. On 2026-11-27 every video model in its public listing was flagged paid_only, so the pool is treated as paid and blocked unless paid spend is approved for the project."),
    dict(id="llm_openai_compat", name="Planner LLM (OpenAI-compatible endpoint)", category="llm", implemented=True, verification="contract-tested",
         host_family="llm", docs_url="",
         notes="Optional storyboard planner. Bring any OpenAI-compatible /chat/completions endpoint and key (config base_url, model). Not a video generator. Falls back to the built-in template planner on any failure."),
    dict(id="replicate", name="Replicate (minimax/video-01)", category="native_api", implemented=True, verification="web-ui-verified",
         host_family="replicate", docs_url="https://replicate.com/collections/try-for-free",
         notes="Paid by default: connect with a key that may be billed. Free-trial mode is restricted to accounts the operator has verified. A web-UI free run produced a real 6s clip, but API-side free eligibility is unverified and the allowance is not readable. HTTP 402 means rejected."),
    dict(id="hf_space_ltx", name="Free: Hugging Face Space (LTX-Video distilled)", category="native_api", implemented=True, verification="live-verified-2026-11-27",
         host_family="hf_space_ltx", docs_url="https://huggingface.co/spaces/Lightricks/ltx-video-distilled",
         notes="REAL AI video, no key and no money: a shared community ZeroGPU Space. Small clips (up to 8s, about 704x384), no SLA, free GPU allowance is daily and unreadable, refusals are detected and the project hands off. Optional free Hugging Face token uses your own allowance."),
    dict(id="huggingface", name="Hugging Face Inference Providers", category="native_api", implemented=False, verification="docs-only",
         host_family="huggingface", docs_url="https://huggingface.co/docs/inference-providers/main/pricing",
         notes="Free accounts have no included Inference Providers credits; PRO gets $2/month, otherwise pay-as-you-go. Not a free route."),
]
