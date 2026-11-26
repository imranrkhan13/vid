# Provider research (free / trial access for text-to-video)

Dates: 2026-11-27. Goal: find providers with a real, no-card, no-paid-commitment route. "Attempted" means an account step or API/UI call was actually made. Sources are official pages unless marked.

| Provider | Official free/trial claim | Attempted | Result |
|---|---|---|---|
| Hugging Face Inference Providers | Free users have no included credits; PRO gets $2/mo ([pricing](https://huggingface.co/docs/inference-providers/main/pricing)) | docs only | Not free. Entitlement of the owner's account not yet checked. |
| Pollinations | Keyed API, pollen credits; all 31 video models flagged paid_only in the public listing (https://gen.pollinations.ai/video/models) | listing read, adapter written | Adapter `pollinations` implemented from the OpenAPI doc, not live-verified (no funded key). |
| WaveSpeedAI | "New accounts receive $1 trial credit" ([docs](https://doc.wavespeed.ai/docs-quick-start)) | Yes: account created via GitHub sign-in | Dashboard balance after signup was $0. The trial credit was not granted. Blocked without adding credits (paid). |
| Replicate | "Try for Free" collection: limited free runs, no credit purchase ([page](https://replicate.com/collections/try-for-free)); lists minimax/video-01 | Yes: account signed in, one run of minimax/video-01 started from the web UI with no billing set up | See status in the report. |
| Google Gemini API (Veo) | Pricing page lists no free tier for Veo ([pricing](https://ai.google.dev/gemini-api/docs/pricing), [video docs](https://ai.google.dev/gemini-api/docs/video)) | docs only | Not free. |
| fal.ai | Prepaid credits, no standing free tier ([pricing](https://fal.ai/pricing)) | docs only | Paid. |
| Runway API | Credits at $0.01 each; add credits required ([docs](https://docs.dev.runwayml.com/guides/setup/)) | docs only | Paid. (Web app free plan has 125 one-time credits, not API.) |
| Luma API | Pay-as-you-go ([pricing](https://docs.agents.lumalabs.ai/guides/pricing/)) | docs only | Paid. |
| Kling API | Resource package purchase required ([quick start](https://kling.ai/document-api/guides/get-started/quick-start.md)) | docs only | Paid. |
| Stability AI | Buy credits to start ([docs](https://docs.stability.ai/getting-started)) | docs only | Paid, no video model verified. |
| MiniMax platform | Pay-as-you-go keys ([docs](https://platform.minimax.io/docs/guides/quickstart-preparation)) | docs only | Paid. |
| Together AI | No free trial, $5 minimum purchase ([support](https://support.together.ai/articles/1862638756-changes-to-free-tier-and-billing-july-2025)) | docs only | Paid. |
| Leonardo API | API credits are purchased separately ([docs](https://docs.leonardo.ai/docs/getting-started)) | docs only | Paid. |
| Hedra | Prepaid USD wallet for API ([docs](https://www.hedra.com/docs/pages/developer/v3/billing)) | docs only | Paid. |
| Segmind | Pay as you go ([pricing](https://www.segmind.com/pricing)) | docs only | Paid. |
| BytePlus ModelArk (Seedance) | New users get free token quota ([page](https://www.byteplus.com/en/activity/free)) | page read; signup not attempted | Lead. Eligibility and video quota unverified. |
| Alibaba Model Studio (Wan) | Free quota stated by third parties only | not verified | Unverified. |
