# Aggregators, API Gateways, and Open-Weight Inference Hosts — Free-Tier Landscape (checked 2026-09-28)

**All pages below were fetched on 2026-09-28.** Anything I could not read on a vendor's own page is marked `[unverified]` or moved to Gaps. Where a number came from a third-party page, I say so and treat it as unconfirmed.

**Taxonomy used throughout (the three mechanisms kept distinct):**

1. **Recurring credit** — a monthly allowance that resets (Vercel $5/mo, HF $0.10/mo, DeepInfra-reported $5/mo).
2. **One-time grant** — a signup voucher that does not refill (Fireworks $1, SiliconFlow $1, Baseten $30, Novita "a voucher", ModelsLab "free credits on signup").
3. **Rate-limited free models** — a set of model IDs priced $0, gated by request/day quotas rather than dollars (OpenRouter `:free`, Requesty 200 req/day, Cloudflare Workers AI 10,000 Neurons/day, ModelScope 2,000 req/day).

A vendor can be more than one of these. Vercel is recurring credit *and* a free-model subset. HF is recurring credit only. OpenRouter is rate-limited free models only.

---

## Q1. Which aggregators give genuinely free ongoing model access, and what exactly is the mechanic?

### Takeaway

Only three aggregators give a **recurring** free allowance large enough to matter today: **Vercel AI Gateway ($5/month)** and **Hugging Face Inference Providers ($0.10/month free / $2/month PRO)**. **Cloudflare Workers AI** gives a genuinely recurring *daily* allowance (10,000 Neurons/day, resets 00:00 UTC) which is the most usable free tier in this class in dollar terms. **OpenRouter, Requesty, SiliconFlow, ModelScope** are the large "rate-limited free models" players. **GitHub Models is dead** — retired July 30, 2026. Most of the long tail (Baseten, Fireworks, Novita, Together, W&B, Eden AI, AI/ML API) is a **one-time grant**, not a free tier, and several now have no free path at all.

### Cited Findings — the major players

**OpenRouter** ([docs/faq](https://openrouter.ai/docs/faq), [docs/api_reference/limits](https://openrouter.ai/docs/api_reference/limits), both checked 2026-09-28)

- Mechanism: **rate-limited free models** only. "All new users receive a small free allowance to test out OpenRouter. There are many free models available on OpenRouter. These models have low rate limits (50 requests per day total)."
- Exact quotas, from constants in the limits doc: `FREE_MODEL_NO_CREDITS_RPD = 50`, `FREE_MODEL_HAS_CREDITS_RPD = 1000`, `FREE_MODEL_CREDITS_THRESHOLD = 10`, `FREE_MODEL_RATE_LIMIT_RPM = 20`.
- The tier is selected by **all-time credits purchased**: under $10 purchased → 50 req/day and 20 req/min; $10+ purchased → 1,000 req/day, still 20 req/min.
- Rounding note from the same page: "To absorb rounding and top-up fees, the higher daily ceiling is granted starting one credit below the table's threshold (currently 9 credits); an account that has purchased fewer credits than that reports `is_free_tier: false` together with the lower daily ceiling." So $9.99 in lifetime purchases already buys the 1,000/day tier.
- Quota is inspectable: `GET https://openrouter.ai/api/v1/key` returns `free_model_daily_requests: { used, limit, remaining }`.
- Additional caps that catch people out: Cloudflare DDoS protection blocks "requests that dramatically exceed reasonable usage" on all accounts; and an in-flight spending budget returns `402` for paid requests — but explicitly "does not apply to requests to free models."
- `:free` is a **catalog variant**, i.e. its own entry in `/api/v1/models` with its own pricing, context, and endpoints. "Free variants provide access to models without cost, but may have different rate limits or availability compared to paid versions." (This is a real trap: the `:free` variant can have a *smaller* context window than the paid model. `poolside/laguna-s-2.1:free` is listed at 256K while the paid `poolside/laguna-s-2.1` is 1M — see the Vercel catalog entry and the OpenRouter API context lengths below.)
- Free Models Router: `openrouter/free`, released **February 1, 2026**. "A router that selects free models at random from the models available on OpenRouter," filters for capability (image understanding, tool calling, structured outputs). Context 200,000 tokens, text+image in, text out. ([openrouter.ai/openrouter/free](https://openrouter.ai/openrouter/free), checked 2026-09-28)
- **The free-model list, read from the live `GET https://openrouter.ai/api/v1/models` API on 2026-09-28.** 458 models total in the response. Entries with `pricing.prompt == "0" && pricing.completion == "0"`:

  | Model ID | Context | Notes |
  |---|---|---|
  | `openrouter/free` | 200,000 | the router itself |
  | `stealth/space-bunny-alpha` | 1,000,000 | $0, **not** `:free`-suffixed; anonymous third-party model, 1M ctx, 14.9T tokens served |
  | `inclusionai/ling-3.0-flash-sante:free` | 262,144 | |
  | `inclusionai/ling-3.0-flash-fin:free` | 262,144 | finance-tuned MoE, 5.1B active / 124B total |
  | `qwen/qwen3.8-27b:free` | 262,144 | |
  | `dots-studio/dots-3-note-preview:free` | 512,000 | note-taking model |
  | `liquid/lfm-2.5-2.6b:free` | 65,536 | small model |
  | `nvidia/nemotron-3.5-lightning:free` | 1,000,000 | |
  | `nvidia/nemotron-3-ultra-550b-a55b:free` | 1,000,000 | 550B model, free |
  | `nvidia/nemotron-3-super-120b-a12b:free` | 262,144 | |
  | `nvidia/nemotron-3-nano-omni-30b-a3b-reasoning:free` | 256,000 | |
  | `nvidia/nemotron-3.5-content-safety:free` | 128,000 | classifier, not a chat model |
  | `thinkingmachines/inkling:free` | 1,048,576 | paid tier is 524.3K per Vercel — see conflict note |
  | `thinkingmachines/inkling-small:free` | 1,048,576 | |
  | `poolside/laguna-s-2.1:free` | 262,144 | paid version is 1M |
  | `poolside/laguna-xs-2.1:free` | 262,144 | |
  | `cohere/north-mini-code:free` | 256,000 | code model |
  | `google/gemma-4-26b-a4b-it:free` | 262,144 | |
  | `google/gemma-4-31b-it:free` | 262,144 | |
  | `google/lyria-3-pro-preview` | 1,048,576 | $0, music/audio |
  | `google/lyria-3-clip-preview` | 1,048,576 | $0 |

  So: **17 `:free` variants across 15 base models, plus 3 zero-priced entries that are not `:free` variants, plus the router.**
- **The list churns constantly.** The `created` timestamps on the free entries range from 1780551208 (Jun 3, 2026) to 1790174884 (Sep 23, 2026) — the free slate is largely a rolling 4-month window. Two of the 17 have existed since early June 2026; one was added three days before I checked. Third-party writeups still describe "OpenRouter has 20+ free models" ([openrouter.ai/blog/tutorials/kilo-code-openrouter](https://openrouter.ai/blog/tutorials/kilo-code-openrouter/), 2026-06-17); the live API count of 17 `:free` entries is lower and is the number I'd trust.
- **Version-churn flags on the free list:** every free text model on OpenRouter is currently a *preview*, *lite*, *nano*, *flash*, *small*, or *early-generation* SKU. `gemma-4-26b/31b-it` are Google's small open-weight models; `poolside/laguna-s-2.1` is two generations behind the current poolside line; `cohere/north-mini-code` is a "mini" code model. **There is no frontier-class model free on OpenRouter today.** Notably the list does *not* include any Qwen 3.8 Max, GLM 5.3, Kimi K3, or DeepSeek V4 Pro — the flagship open-weight models are all paid. Nvidia's `nemotron-3-ultra-550b-a55b:free` is the largest genuinely capable model in the free set, which suggests Nvidia is paying to be in the free tier.
- **Caveat on the two `stealth/*` and Google Lyria entries:** these are $0-priced but not `:free` variants, so they are **not** governed by the 50/1,000 req/day free-model cap (the limits doc scopes the cap to "a free model variant (with an ID ending in `:free`)"). `stealth/space-bunny-alpha`'s own card says "Prompts and completions may be retained by the provider but are not used for training; all other use is governed by the Stealth Model Terms." Treat as untested-catch-all capacity, not as a free quota.

**Hugging Face Inference Providers** ([huggingface.co/docs/inference-providers/pricing](https://huggingface.co/docs/inference-providers/pricing), checked 2026-09-28)

- Mechanism: **recurring monthly credit**, and it is tiny.

  | Account type | Monthly credits | Spendable on | Extra usage |
  |---|---|---|---|
  | Free users | **$0.10**, "subject to change" | Inference Providers only | yes (must purchase credits) |
  | PRO users | **$2.00** | all HF compute (Inference Endpoints, Spaces/ZeroGPU, Jobs) | yes |
  | Team/Enterprise | **$2.00 per seat** | all HF compute | yes |

- Credits "are credited every month and applied automatically before any pay-as-you-go usage is billed."
- HF passes provider prices through with no markup: "Hugging Face charges you the same rates as the provider, with no additional fees."
- **Two mechanics that are easy to get wrong:**
  1. **Routed vs custom provider key.** Credits apply only to **Hugging Face-routed** requests. If you supply your own provider key in HF settings, "Hugging Face won't charge for the call" and **your monthly credits do not apply** — you're billing that provider directly.
  2. **PRO credits are a different currency.** Free-tier $0.10 is restricted to Inference Providers; PRO/Team/Enterprise $2 is *general-purpose compute credit* that also covers Inference Endpoints, upgraded CPU/GPU Spaces including ZeroGPU overage, and Jobs.
- Org billing via `X-HF-Bill-To: <org>` header, or `bill_to=` on `InferenceClient`.
- `hf-inference` (the old serverless Inference API) is a *separate* provider: "As of July 2025, hf-inference focuses mostly on CPU inference (e.g. embedding, text-ranking, text-classification, or smaller LLMs that have historical importance like BERT or GPT-2)." Post-free-credit it bills compute-seconds (worked example: FLUX.1-dev, 10s at $0.00012/s = $0.0012).

**Cloudflare Workers AI** ([developers.cloudflare.com/workers-ai/platform/pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/), page "Last updated Sep 17, 2026", checked 2026-09-28)

- Mechanism: **rate-limited free allocation, recurring daily** (best-value free tier in this class).
- "Our free allocation allows anyone to use a total of **10,000 Neurons per day** at no charge. To use more than 10,000 Neurons per day, you need to sign up for the Workers Paid plan. On Workers Paid, you will be charged at **$0.011 / 1,000 Neurons**."
- "All limits reset daily at **00:00 UTC**. If you exceed any one of the above limits, further operations will fail with an error."
- $0.011/1,000 neurons ⇒ 10,000 neurons/day ≈ **$0.11/day ≈ $3.30/month of free compute**, and it does not require a card. That is ~3× Vercel's $5/month at similar value — but the unit is opaque.
- **Hard exclusion list (the trap):** "Some models require a paid billing method. This applies to `@cf/moonshotai/kimi-k2.6`, `@cf/moonshotai/kimi-k2.7-code`, `@cf/zai-org/glm-5.2`, `@cf/zai-org/glm-5.3`, `@cf/zai-org/glm-5.3-flash`, `@cf/deepseek-ai/deepseek-v4-flash-0731`, and `@cf/deepseek-ai/deepseek-v4-pro-0813`. You can access these models with either the Workers Paid plan or prepaid AI Gateway credits." So the most interesting open-weight models on Cloudflare (Kimi K2.6/K2.7-code, GLM 5.2/5.3, DeepSeek V4) are **all** paywalled off the free tier. What is actually free: Llama 3.1/3.2/3.3 variants, Mistral 7B, `llama-4-scout-17b-16e-instruct`, `gemma-3-12b-it`, `gemma-4-26b-a4b-it` ($0.10/M in, $0.30/M out — 9,091 neurons/M in, so ~1.1M input tokens/day free), `qwq-32b`, `qwen2.5-coder-32b-instruct`, `qwen3-30b-a3b-fp8`, `qwen3.8-27b` ($0.45/M in, $3.20/M out — expensive in neurons), `gpt-oss-120b` / `gpt-oss-20b`, `granite-4.0-h-micro`, `deepseek-r1-distill-qwen-32b`, `glm-4.7-flash`, `nemotron-3-120b-a12b`, plus BGE embeddings, FLUX 1-schnell / FLUX 2 dev/klein, Whisper, Deepgram, Melotts, DistilBERT, bge-reranker, m2m100, resnet-50, indictrans2, `moondream3.1-9B-A2B`.
- Cheap-in-neurons picks (best free-tier ROI): `granite-4.0-h-micro` (1,542 neurons/M in), `bge-reranker-base` (283), `qwen3-embedding-0.6b` (1,075), `bge-m3` (1,075), `flux-1-schnell` (4.80 per 512×512 tile), `qwen3-30b-a3b-fp8` (4,625/M in), `llama-3.2-1b-instruct` (2,457/M in), `qwen3-embedding`/`bge-m3` (1,075/M in), `gemma-4-26b-a4b-it` (9,091/M in).
- **AI Gateway unified billing is a distinct second path.** [developers.cloudflare.com/ai-gateway/features/unified-billing](https://developers.cloudflare.com/ai-gateway/features/unified-billing/) (updated Jun 22, 2026): "A **5% fee** is applied to all credits purchased through Unified Billing. For example, a $100 credit purchase will result in a $105 charge. Inference pricing from providers is passed through with no markup." Critically: "**Workers AI models (models prefixed with `@cf/`) routed through AI Gateway are not charged via Unified Billing.** These models are billed through Workers AI pricing instead. Unified Billing only applies to third-party provider models." So the paywalled Workers AI frontier models become reachable by preloading *AI Gateway* credits (not Workers Paid), and per the [Aug 7 2026 changelog](https://developers.cloudflare.com/changelog/post/2026-08-07-workers-ai-unified-billing/) that path also **raises the rate limit on those frontier models to 50 req/min per account per model, vs 20 req/min** under standard Workers AI billing.
- AI Gateway core features (dashboard analytics, caching, rate limiting) are "available to use on all plans… offered for free." Log storage: Workers Free 100,000 logs total across all gateways; Workers Paid 10,000,000 per gateway. DLP scanning free on all plans. Paid add-ons: Requests 10M/month +$0.05/million (Paid only). ([developers.cloudflare.com/ai-gateway/reference/pricing/](https://developers.cloudflare.com/ai-gateway/reference/pricing/), checked 2026-09-28)

**Vercel AI Gateway** ([vercel.com/docs/ai-gateway/pricing](https://vercel.com/docs/ai-gateway/pricing), page `last_updated: 2026-09-08`, checked 2026-09-28; raw markdown at [vercel.com/docs/ai-gateway/pricing.md](https://vercel.com/docs/ai-gateway/pricing.md), `last_updated: 2026-05-22`)

- Mechanism: **recurring credit**, $5/month.

  | | Free tier | Paid tier |
  |---|---|---|
  | Monthly credit | **$5/month included** | None — pay as you go |
  | Model access | *(see conflict below)* | All available models |
  | Commitment | None | No lock-in |

- **Free credits start on your first AI Gateway request**, not at account creation. Once you purchase credits you move to the paid tier and "the monthly free credit no longer applies."
- **A card is required to use the free credits.** The FAQ documents a `403` with type `customer_verification_required`: "The team must add a valid payment method before using free credits." ([vercel.com/docs/ai-gateway/faq](https://vercel.com/docs/ai-gateway/faq), checked 2026-09-28). This is a real trap: Vercel's $5/month is not a no-card free tier.
- Free-tier requests are rate-limited per model, lower than paid; exceeding returns `429`. Paid tier has "None from AI Gateway; provider limits still apply."
- Zero markup on tokens, both tiers, including BYOK.
- **CONFLICT — model access on the free tier.** The rendered page (last_updated 2026-09-08) says: "The free tier includes a **subset** of models, not the full catalog. To see which models you can use with free credits, browse the Free Tier models" and links `/ai-gateway/models?freeTier=true`. Its pricing table in the same rendered page does not exist. The `.md` variant (last_updated 2026-05-22) has the table saying "All available models" for both tiers. I fetched `/ai-gateway/models?freeTier=true` twice and the returned HTML header said "List of all **368 models** available through the Vercel AI Gateway" — i.e. the `freeTier=true` filter did **not** narrow the list in the fetched render. **I could not resolve which claim is current.** The September render is newer and more specific, and the FAQ's error table ("`403` whose message names the free tier — the model is not in the free-tier subset") corroborates a subset, so I would plan for a subset. Do not rely on "all 368 models are free-tier eligible."
- Vercel's catalog is otherwise readable: 368 models, 0% markup, providers listed per model, ZDR and HIPAA flags per model. Free-tagged models I could see in the catalog: `inclusionai/ling-3.0-flash-sante` (novita, tagged `free`), `poolside/laguna-s-2.1-free` (tagged `free`), plus many $0.00/1M **media** models (image/video/realtime entries show `$0.00/1M` because they're per-unit priced, not because they're free). Don't read `$0.00/1M` on an image model as "free."
- Purchased credits "expire one year after purchase."

**Requesty** ([requesty.ai/pricing](https://requesty.ai/pricing) and [requesty.ai/free-models](https://www.requesty.ai/free-models), both checked 2026-09-28)

- Mechanism: **rate-limited free models**, 200 requests/day.
- Free plan is "$0 — The full platform, on free models": "Access to all free models, **200 requests per day**, Routing, caching & fallbacks, Spend tracking & analytics, EU data residency. **No credit card required.**"
- Explicitly **not** a trial: "The free tier is pay-as-you-go restricted to free models and 200 requests per day… There is no credit card requirement and no trial expiry."
- Paid is a **5% markup**, "No per-seat pricing, no minimum spend." 600+ models, BYOK, MCP Gateway.
- Their own framing is aggressive: "**Not a sandbox.** The free tier is pay-as-you-go restricted to free models and 200 requests per day, free for coding agents, chat and production prototypes."
- **Base URL verified on the vendor page:** `https://router.requesty.ai/v1` (OpenAI-compatible), and `ANTHROPIC_BASE_URL=https://router.requesty.ai` + `ANTHROPIC_AUTH_TOKEN` for Claude Code / Cline / Cursor / Roo Code. Explicit claim of no "trial timer, no surprise bill."
- **Unknown:** Requesty does not publish *which* models are free. I could not enumerate the free model IDs from the vendor site. This is a significant gap for a "which model IDs are free" question.

**ModelScope (Alibaba)** — `[unverified from vendor; third-party only]`
- Mechanism: **rate-limited free models**. Reported 2,000 API inference calls/day per account across all models, with a per-model daily cap of up to 200, refreshed daily, 429 on exceed. Base URL reported as `https://api-inference.modelscope.cn/v1`. Sources: [jxxy.net guide, 2026-09-22](https://www.jxxy.net/ai/articles/modelscope-onboarding-api-setup-guide/) and [freellm.net provider profile](https://freellm.net/providers/modelscope) (last updated 2026-07-07). I fetched [modelscope.cn/docs/model-service/API-Inference/intro](https://www.modelscope.cn/docs/model-service/API-Inference/intro) and it rendered with essentially no body text — I could not verify the numbers on ModelScope's own page. **Treat the 2,000/day and 200/model figures as unverified.** Registration reportedly requires an Alibaba account or Chinese phone; international signup difficulty is a real reported constraint.

**SiliconFlow** ([siliconflow.com/pricing](https://siliconflow.com/pricing), checked 2026-09-28; [api-docs.siliconflow.cn rate limits](https://api-docs.siliconflow.cn/docs/userguide/faqs/rate-limit-and-upgradation))

- Mechanism: **both**. Rate-limited free models *plus* a one-time new-user grant.
- The vendor pricing page states plainly: "Flexible token pricing, high usage limits, and postpaid billing—**plus $1 in free credits to get you started**!" So the $1 is a **one-time grant**, not recurring.
- The official rate-limit doc (Chinese, the authoritative one) states: "The Rate limits for **free models are fixed values**, while those for paid models vary based on the account's usage level." And: "**After verifying your identity, you can use all free models.** Free model calls are free, and you will see the cost of these models as 0 in your account bill." (Chinese original: 使用全部免费模型需要实名认证; 免费模型调用免费，账单中显示为 0.)
- **The trap:** free-model limits are fixed and **do not** scale with paid usage. Accounts tier L0→L5 by monthly consumption (L0 <¥50, L1 ¥50–200, L2 ¥200–2000, L3 ¥2000–5000, L4 ¥5000–10000, L5 ≥¥10000), but that ladder governs **paid** models only. Real-name identity verification is required.
- Model ID trap reported by third parties: some models have both a free ID and a paid `Pro/` ID for the same underlying model (e.g. `BAAI/bge-m3` free vs `Pro/BAAI/bge-m3` at ¥0.07/M). Using the wrong one makes it look like the free tier vanished. `[third-party; unverified on vendor page]`
- Free model list `[unverified — I did not get siliconflow.cn's live pricing table]`. Third-party snapshots dated 2026-08-21 list: `THUDM/GLM-Z1-9B-0414`, `THUDM/GLM-4-9B-0414`, `Tencent-Hunyuan/Hunyuan-MT-7B`, `PaddlePaddle/PaddleOCR-VL-1.5`, four BAAI embedding/rerank IDs, `Kwai-Kolors/Kolors`, `Qwen/Qwen3-ASR-1.7B`, `TeleAI/TeleSpeechASR`, `FunAudioLLM/SenseVoiceSmall`. Re-verify before depending on any of these. Note these are all *legacy* 2024/2025-era models.
- Base URLs `[third-party]`: `https://api.siliconflow.com/v1` (international), `https://api.siliconflow.cn/v1` (China), same key accepted on both.

**The rest — mostly one-time grants, several with no free path at all**

| Vendor | Mechanism as stated on the vendor page | Exact number | Source |
|---|---|---|---|
| **Fireworks AI** | one-time grant | "$1 in free credits" (serverless inference); postpaid billing after | [fireworks.ai/pricing](https://fireworks.ai/pricing) |
| **Baseten** | one-time grant, **per workspace** | "**Up to $25,000** for Dedicated Deployments or Training and **up to $2,500** for Model APIs" via Startup Program (application required) | [baseten.co/talk-to-us/startup-program](https://www.baseten.co/talk-to-us/startup-program/) |
| **Together AI** | application-only program | "**Up to $15K / $30K / $50K** in free platform credits" by accelerator tier; "*Credits do not apply to Reserved GPU Clusters." No self-serve free tier stated. | [together.ai/startup-accelerator](https://www.together.ai/startup-accelerator), [together.ai/blog/announcing-together-ai-startup-accelerator](https://www.together.ai/blog/announcing-together-ai-startup-accelerator) |
| **Novita AI** | one-time grant, **amount not disclosed** | "We offer new users a voucher with some credit to try our products." Also: "please ensure your account has sufficient credit balance, and Setup Automatic Top-up is a recommended practice" — i.e. designed to auto-charge you. | [novita.ai/docs/guides/quickstart](https://novita.ai/docs/guides/quickstart) |
| **Novita AI** | batch discount | "Batch inference is available at an introductory **50% discount** on input and output tokens for supported models." | [novita.ai/pricing](https://novita.ai/pricing) |
| **W&B Inference** | time-limited recurring credit + $100/mo default cap | "Serverless Inference credits come with Free, Pro, and Academic plans **for a limited time**." Free tier **$0/mo** with "Free credits for a limited time"; Pro **$5/mo** with "$5/mo Free credit for a limited time". Default spending caps: **Free $100/month**, Pro $6,000/month, Enterprise $700,000/year. "Free accounts must activate pay-as-you-go inference on the Billing tab, or upgrade." | [docs.wandb.ai/inference/usage-limits](https://docs.wandb.ai/inference/usage-limits), [wandb.ai/site/pricing](https://wandb.ai/site/pricing/) |
| **DeepInfra** | reported recurring + one-time | `$5 one-time signup + $5/month recurring`, no card to claim, card only to exceed — **`[third-party: aicredits.dev, 2026-05-09 — NOT verified on deepinfra.com]`**. The vendor [deepinfra.com/pricing](https://deepinfra.com/pricing) page I fetched shows only per-token rates (DeepSeek-V4-Flash-0731 $0.06/$0.18, DeepSeek-V4-Pro $1.30/$2.60, Kimi-K3 $2.85/$14.25, DeepSeek-V3.2 $0.26/$0.38) and **no free allowance whatsoever**. | [deepinfra.com/pricing](https://deepinfra.com/pricing) (free tier not stated) |
| **Eden AI** | pay-per-use, **no free allowance stated** | The pricing page describes only pay-per-request, feature-based units (tokens / text length / files / pages / seconds / images) and "no upfront costs or fixed fees." No credit, no free tier. Third parties report $10 signup credit (2023 vintage) and affiliate sites claim $30. | [docs.edenai.co/docs/pricing](https://docs.edenai.co/docs/pricing) |
| **AI/ML API** | **no free plan** | Vendor OpenAI-alternative page: "we don't offer a free plan, pricing is usage-based, so you only pay for what you use." Prepaid top-up minimum **$20**, auto-renews (opt-out), funds don't expire, non-refundable. A marketing page claims a "free trial" via Discord — contradiction. | [aimlapi.com/openai-alternative](https://aimlapi.com/openai-alternative) (no free plan); `aicredits`-class sources `$20 minimum` |
| **Fal.ai** | per-unit, no free tier stated | Compute as low as $1.89/hr H100; video $0.05–0.4/second; image $0.03–0.04/image. No free allowance on the page. | [fal.ai/pricing](https://fal.ai/pricing) |
| **Replicate** | per-second/per-token, no free tier stated | "You only pay for what you use." flux-1.1-pro $0.04/image, flux-dev $0.025/image, flux-schnell $3.00/1000 images, deepseek-r1 $0.01/1K output tokens + $3.75/M input. No free allowance. | [replicate.com/pricing](https://replicate.com/pricing) |
| **Hyperbolic** | repositioned away from free | Docs now describe only On-Demand GPUs, Reserved, Private Cloud. No free model API described. | [docs.hyperbolic.xyz/docs/rest-api/pricing](https://docs.hyperbolic.xyz/docs/rest-api/pricing) |
| **Segmind** | gateway + PixelFlow; no free figure found | "AI Gateway for 200+ models, Async inference & webhooks, PixelFlow, Dedicated GPU endpoints." Sample call `https://api.segmind.com/v1/seedance-2.0`. No free allowance on the docs home. | [docs.segmind.com](https://docs.segmind.com/) |
| **Deno Deploy AI** | not an AI gateway | Deno Deploy Free is $0/month: 1M requests/month, 20 GiB egress, 10 CPU-hours, 150 GiB-hr memory, 10 apps, 1 GiB KV, 1M KV read units, 500K write units, 3 team members, 1-day log retention. Pro $20/mo (5M req, 200GB egress). **"All organizations fall back to Free and only get the full Free plan limits until they verify by linking a credit card";** apps **pause** (not overage-billed) when quota is exceeded. Third-party trackers note the free egress figure was cut from 100GB (GA) to 20GB with no published date, and CPU hours cut 15→10 within Aug–Sep 2026 — **free compute is being trimmed here**. | [deno.com/deploy/pricing](https://deno.com/deploy/pricing); [denoland.deno.dev/deploy/pricing](https://denoland.deno.dev/deploy/pricing) |
| **Pipedream** | workflow credits, with an AI-token component | "Free workspaces have a daily limit of free credits"; 1 credit per 30s of compute at 256MB; first 30s of a *public-registry source execution* is free; workflow builder and test events are free (no credit). Third-party: 100 credits/month, 1M AI tokens/month, 3 active workflows, 3 connected accounts, 300s max execution, no production Connect. | [pipedream.com/docs/pricing](https://pipedream.com/docs/pricing) (daily credit limit, no number); `freetier.co` (100 credits/mo, 1M AI tokens/mo — third-party) |
| **GitLab Duo** | credits model, free tier must *buy* | Free tier users **cannot** get AI for free: "Features available on the Free tier require the purchase of GitLab Credits." Free namespaces: on-demand capped at **$25,000/calendar month**, then auto-disabled. Included credits exist only on Premium (**$12/user/month**) and Ultimate (**$24/user/month**) as a *limited-time promotion*; "Included credits reset at the beginning of each month. **Unused credits do not roll over.**" 1 credit = $1 on-demand list price. Code Review Flow 0.25 credits/review; Duo Code Suggestions ~50 executions/credit. | [docs.gitlab.com/subscriptions/gitlab_credits/](https://docs.gitlab.com/subscriptions/gitlab_credits/), [about.gitlab.com/blog/introducing-gitlab-credits](https://about.gitlab.com/blog/introducing-gitlab-credits/), [about.gitlab.com/blog/gitlab-18-10](https://about.gitlab.com/blog/gitlab-18-10-agentic-ai-now-open-to-even-more-teams-on-gitlab/) |
| **Alibaba Cloud Model Studio (百炼)** | one-time grant with a **hard cliff** | New-user free quota, **Singapore region only** (and Beijing region for China-deployment models). "The free quota is valid for **90 days**." Each model has an **independent** quota (typically 1M tokens), non-transferable, non-combinable. Hard stop: unverified new users hitting the limit get **HTTP 403 `AllocationQuota.FreeTierOnly`** and cannot continue. OAuth auth has a *separate* allowance of 2,000 calls/day. | [help.aliyun.com/zh/model-studio/new-free-quota](https://help.aliyun.com/zh/model-studio/new-free-quota), [alibabacloud.com/help/zh/model-studio/new-free-quota](https://www.alibabacloud.com/help/zh/model-studio/new-free-quota) (updated Jul 15, 2026) |

**Hugging Face ZeroGPU Spaces** ([huggingface.co/docs/hub/spaces-zerogpu](https://huggingface.co/docs/hub/spaces-zerogpu), checked 2026-09-28) — not a gateway, but the free-GPU path most people actually want.
- "ZeroGPU Spaces are available to use **for free to all users**."
- "**PRO users get x8 more daily usage quota**, highest priority in GPU queues, and can go beyond their daily quota using pre-paid credits."
- Hosting: free personal accounts in good standing (verified email, **account older than 30 days**) can host up to **2** ZeroGPU Spaces free; PRO up to 10; Team/Enterprise for org members.
- Backed by NVIDIA **RTX Pro 6000 Blackwell**. Quota cost: `large` = half the card, 48GB VRAM, 1× quota; `xlarge` = full card, 96GB, 2× quota.
- **Gradio-only.** "Currently, ZeroGPU Spaces are **exclusively** compatible with the Gradio SDK" (Gradio 4+, PyTorch 2.8.0+). This is a hard blocker for non-Gradio stacks.

**Coding-agent routers (Cline / Roo / Kilo)** — checked via OpenRouter's own tutorial page and vendor pages
- **Kilo Gateway** is the only one of the three that ships its own first-party model router. Per [openrouter.ai/blog/tutorials/kilo-code-openrouter](https://openrouter.ai/blog/tutorials/kilo-code-openrouter/) (2026-06-17): "Kilo's `kilo-auto/free`, served through Kilo Gateway, doesn't touch your OpenRouter credits or routing rules." Kilo markets it as zero-markup, billed to a Kilo account, Kilo-managed routing. Auto-routing tiers: Frontier, Balanced, Free, Small.
- **Cline and Roo have no first-party router** — both consume OpenRouter/BYOK. Per the same page, Cline is "Hosted access + 200+ via OpenRouter + BYOK"; Roo's Cloud team plan has **ended** and its repo archives 2026-05-15. Cline is described as "free models" access, not free credit.
- Useful direct claim from the OpenRouter tutorial: "With no credits loaded, free models run at **50 requests per day and 20 per minute**. Add $10 or more in credits and that daily cap rises to **1,000**." This matches the limits doc, which is a good cross-check that the tutorial is current.

### Inferences

- **The free-tier landscape has consolidated around three durable designs** and everything else is a voucher. Recurring-dollar-credit is nearly extinct (Vercel $5, HF $0.10/$2, maybe DeepInfra $5 unverified). Recurring-request-quota is where the durable free capacity lives (OpenRouter 50/1000 per day, Requesty 200/day, ModelScope-reported 2000/day, Workers AI 10k neurons/day). One-time grants ($1–$30) are evaluation budgets, not infrastructure.
- **The most strategically interesting free tier is Cloudflare Workers AI's, and it is deliberately shaped to exclude the frontier.** Seven named models — Kimi K2.6, Kimi K2.7-code, GLM 5.2, GLM 5.3, GLM 5.3-flash, DeepSeek V4 Flash 0731, DeepSeek V4 Pro 0813 — require a paid billing method. That is exactly the set of models people would want. The free list is Llama-3.x-generation, small Gammas, QwQs, and gpt-oss. Treat the 10,000-neuron daily allowance as a way to run small models continuously, not to get frontier quality.
- **Vercel's $5/month is the best *usable* recurring credit but it is card-gated**, which quietly disqualifies it for the "no-card free tier" category most people are actually asking about. HF's $0.10 is the smallest genuine recurring credit in the industry and is explicitly "subject to change."
- **OpenRouter's free list is a rolling ~4-month window of preview/lite/nano SKUs.** Its 1,000/day tier is reachable at $9.99 lifetime spend, so "free" there is effectively a $10 one-time unlock of a 20× larger daily quota. If you are going to spend $10 anywhere, this is the highest-leverage $10 in the category.
- **`$0.00` in a pricing table is not "free."** Vercel's catalog shows `$0.00/1M` on every image/video/realtime model because those are per-unit priced. Only rows with an explicit `free` tag or `:free` variant are free.
- **Two vendors' positioning is worth noting because it is unusually candid:** Requesty says outright "Not a sandbox" and offers 200 req/day free forever with no card; SiliconFlow's own docs say free-model limits are fixed and explicitly do *not* improve with spend. Vendors that hide these mechanics behind marketing are the ones to be careful with.

### Gaps

- **Requesty's free model list: not published.** I read the pricing and free-models pages; neither enumerates which model IDs are free. Without an account I cannot enumerate them. This is the single biggest hole in the "which model IDs are free" question.
- **ModelScope's 2,000 req/day and 200 req/model/day are unverified.** The vendor doc page rendered empty. I have third-party Chinese and English secondary sources only.
- **SiliconFlow's live free-model ID list is unverified.** The vendor pricing page I fetched did not show a "免费/free" badge list; my list is a third-party 2026-08-21 snapshot. The $1 grant *is* verified on the vendor page.
- **DeepInfra's free tier.** The `$5 + $5/mo` claim comes from aicredits.dev (a user-submitted credit-claims site, 2026-05-09) and was contradicted in spirit by deepinfra.com/pricing showing no free allowance at all. I could not find a DeepInfra-owned page stating the credit. Needs confirmation.
- **Baseten's $30-per-workspace starter credit is third-party only.** Baseten's own page I read states the $25,000/$2,500 Startup Program. I did not find the $30 figure on baseten.co. e8.team and pricepertoken.com both assert it.
- **Eden AI, NanoGPT, Glif, Zeabur, Lepton, Predibase, ModelsLab, Chutes: not verified.** For Eden AI, AI/ML API, Fal, Replicate, Hyperbolic, Segmind I can state authoritatively that **no free allowance appears on the pricing page** — that is a verified negative. For NanoGPT (`nanoGPT.ai/pricing` returned 404/not found), Glif, Zeabur, Lepton, Predibase, and ModelsLab I have nothing from a vendor page. ModelsLab's own model pages (`modelslab.com/wan-2-1`, `/veo-3-1`) say "Every new ModelsLab account gets free credits — no credit card required" and "free credits on every new account" but **never state an amount**.
- **Together AI has no self-serve free tier** as far as I can tell — only an application-based accelerator. A third-party (getaiperks.com) claims "$25–$50 trial credits" on signup; I found no Together-owned page confirming it. Treat as unverified.
- **Vercel's free-tier model subset vs full catalog is unresolved** (see conflict above). The two versions of the vendor's own page disagree and the filter query did not work.
- **Pipedream's daily free-credit number is not on the vendor page** ("a daily limit of free credits that cannot be exceeded", no number). 100 credits/month and 1M AI tokens/month are third-party.
- **Deno Deploy's AI-specific offering is not a model gateway** and I did not investigate whether Deno Deploy AI is a separate product with its own free allowance.
- **I did not verify OpenRouter's `openrouter/free` router's model count against the API list.** The router page says "Models in this router 28" while the API shows 17 `:free` entries — a discrepancy I could not resolve (the router may include the non-`:free` $0 entries and the base-model aliases).

---

## Q2. What exactly does OpenRouter's free tier allow today?

### Takeaway

50 free-model requests/day (20/min) with no credits, rising to **1,000/day at $9.99–$10+ in lifetime purchases**, against a **17-model `:free` list that is all preview/lite/nano/legacy SKUs with no frontier model**. No card is needed for the free tier itself. Prompts and completions are **not logged** by default, and there is a separate opt-in that trades 1% of margin for logging — but free models can route to providers that *do* train unless you change the account-level toggle.

### Cited Findings

All from [openrouter.ai/docs/faq](https://openrouter.ai/docs/faq) and [openrouter.ai/docs/api_reference/limits](https://openrouter.ai/docs/api_reference/limits), checked 2026-09-28, plus the live `GET /api/v1/models`.

**The numbers, stated four ways across two official pages (all consistent):**
- `< $10` all-time credits purchased: **50 requests/day**, **20 requests/minute**.
- `≥ $10` all-time credits purchased: **1,000 requests/day**, **20 requests/minute** (RPM unchanged by tier).
- Threshold constant: `FREE_MODEL_CREDITS_THRESHOLD = 10`; effective threshold **9** because of top-up-fee rounding.
- Which tier you're in is reported by `GET /api/v1/key` as `free_model_daily_requests: {used, limit, remaining}` plus `is_free_tier: boolean`. Docs note: "The `limit` tier is selected by all-time credits purchased, **independently of `is_free_tier`**."

**"Requirement to BYO a key to get your free-tier quota counted" — this premise needs correcting.** There is no BYOK requirement for the free quota, and in fact **BYOK is exempt from it.** From the limits doc: "Accounts and endpoints exempt from free-model limits, **and BYOK requests, are not gated by it**, so `remaining` reflects the tier policy rather than an enforced ceiling for them." So bringing your own provider key does *not* count your BYOK traffic against the 50/1,000 — but it also means BYOK traffic does not count *toward* anything, and BYOK is billed under a completely separate scheme: "BYOK has a plan-dependent free allowance measured by list-price inference cost, not request count. Pay-as-you-go includes **$25,000 per month** with no BYOK fee, while Enterprise includes **$200,000**. Usage above the allowance has a fee of **5%** of what the same model and provider would normally cost on OpenRouter." (Constants: `BYOK_PAYG_MONTHLY_LIST_PRICE_THRESHOLD_USD = '$25,000'`, `BYOK_ENTERPRISE_MONTHLY_LIST_PRICE_THRESHOLD_USD = '$200,000'`, `BYOK_FEE_PERCENTAGE = '5'`.) The "BYO key" mechanism people mean is the *credit-purchase* unlock: buying $10 of OpenRouter credits — not attaching a provider key — is what raises 50→1,000/day.

**The `:free` model list** — see the table in Q1. 17 `:free` variants, 15 base models, 3 additional zero-priced non-`:free` entries, plus the `openrouter/free` router. 458 models total in the catalog.

**Data retention and privacy on free models** (this is the part the assignment specifically asked about, and it is stricter than expected):

- "We log basic request metadata (timestamps, model used, token counts). **Prompt and completion are not logged by default. We do zero logging of your prompts/completions, even if an error occurs**, unless you opt-in to logging them."
- There is an opt-in setting that trades a discount for logging: "an opt-in setting that lets users opt-in to log their prompts and completions **in exchange for a 1% discount on usage costs**." This is only relevant if you have usage costs, i.e. paid models.
- **Free models get their own training toggle.** "There are **separate settings for paid and free models**" on the privacy settings page, governing whether OpenRouter may route to providers that train on your data.
- "OpenRouter is a proxy that sends your requests to the model provider… We work with all providers to, when possible, ensure that prompts and completions are not logged or used for training. **Providers that do log, or where we have been unable to confirm their policy, will not be routed to unless the model training toggle is switched on.**"
- **Enforcement is hard, not advisory.** "If you specify provider routing in your request, but none of the providers match the level of privacy specified in your account settings, **you will get an error and your request will not complete**."
- Per [openrouter.ai/docs/guides/privacy/provider-logging](https://openrouter.ai/docs/guides/privacy/provider-logging): "OpenRouter **does not have routing rules that change based on data retention policies of providers**" — retention policy is shown in structured data per endpoint and the user filters on it. "This setting has no bearing on OpenRouter's own policies and what we do with your prompts."
- The known exception in the free set: `stealth/space-bunny-alpha`'s card says "Prompts and completions **may be retained by the provider** but are not used for training." The router page's own disclosure for `Owl Alpha` reads "**free to use during this testing … and prompts and completions are logged by the model creator for feedback and training.**" That is an explicit, vendor-stated training-on-your-prompts case inside a $0 offering.

**Credit expiry:** "Per our terms, we reserve the right to expire unused credits after one year of purchase." Refunds only within 24 hours; crypto never refundable.

**Other operational traps:**
- 429 handling: "Retry with exponential backoff… honor the `Retry-After` header." Successful responses carry **no** `X-RateLimit-*` headers at all — you must poll `GET /api/v1/key` or catch the error. Mid-stream limits arrive as an SSE event with `finish_reason: "error"`, not an HTTP status.
- "Making additional accounts or API keys will not affect your rate limits, as we govern capacity globally." Rate limits are per-model-different, so the only legitimate relief is spreading across models.
- 402s: "If your account has a negative credit balance, you may see 402 errors, **including for free models**." Adding credits to get above zero restores free-model access.
- 403 has three distinct causes bundled into one code: "Insufficient permissions, a guardrail block, or the input was flagged by moderation." A free-model 403 is often moderation, not auth.

### Inferences

- **The free tier is a funnel, not a resource.** 50/day is designed to be enough to evaluate a model and not enough to build on. The $9.99 unlock to 1,000/day is the actual product: OpenRouter converts free usage into credit purchases, and the 20× jump is the conversion incentive. Note this is *lifetime* spend, so the 1,000/day tier is permanent once earned — it is the single most cost-effective durable free allowance in the category at $10 one-time.
- **Privacy on OpenRouter's free tier is better than most, with one carve-out.** The separate free-model training toggle plus hard error enforcement is meaningfully better than a provider that silently trains. But the two `stealth/*` models and `Owl Alpha` are documented as logging for training, so the free tier is not uniformly clean. If prompts are sensitive, filter on `data_collection` per endpoint rather than trusting the free tier as a category.
- **The free list is strategically not interesting for capability work.** Every free model is a lite/nano/preview/legacy SKU. The largest capable model free is `nvidia/nemotron-3-ultra-550b-a55b:free`. For anything resembling frontier quality, free-tier model choice is the binding constraint, not the daily cap.
- **Free-model context windows can be smaller than paid.** `poolside/laguna-s-2.1` is 1M paid / 262,144 free; `thinkingmachines/inkling` is 1,048,576 free in OpenRouter's API but 524.3K in Vercel's catalog. Any code that assumes the paid context will break on the free variant, silently.

### Gaps

- I did not read OpenRouter's Terms of Service or Privacy Policy directly — I only have the FAQ's characterization of them, including the one-year credit expiry. The exact ToS language on training rights, commercial use, and indemnification for free-tier output is unread.
- I did not verify the free-model *provider* set per model (which upstream serves the `:free` variants), which determines their real latency and context. Available in `/api/v1/models` under `supported_parameters`/endpoints but I did not extract it.
- I could not reconcile the `openrouter/free` router page's "Models in this router 28" with the API's 17 `:free` entries. Unresolved.
- Whether the 50/1000 caps are enforced **per account or per API key** is stated only as "govern capacity globally"; the docs describe the daily counter as account-level but never say so in those words.

---

## Q3. What does Hugging Face's monthly credit amount to, and what are the rate limits on free vs PRO Inference Providers?

### Takeaway

**$0.10/month for free accounts, explicitly "subject to change"** — about 10–100 requests depending on model, i.e. enough to smoke-test and nothing more. **$2.00/month for PRO**, and that credit is a different, more valuable currency (general-purpose HF compute, not Inference-Providers-only). I could **not** find a published rate-limit table for free vs PRO Inference Providers; the rate-limits doc URL I tried returns the pricing page content instead.

### Cited Findings

All from [huggingface.co/docs/inference-providers/pricing](https://huggingface.co/docs/inference-providers/pricing), checked 2026-09-28:

- Free users: **$0.10/month**, marked "**subject to change**", spendable on **Inference Providers only**.
- PRO users: **$2.00/month**, spendable on **all HF compute services**.
- Team/Enterprise: **$2.00 per seat**, same scope. "For Team or Enterprise organizations, credits are shared among all members."
- "They are credited every month and applied automatically before any pay-as-you-go usage is billed."
- PRO/Team credits cover: Inference Endpoints, "upgraded CPU & GPU hardware for Spaces (including ZeroGPU usage beyond your quota)", and Jobs.
- **"All users can continue using the API after exhausting their monthly credits by purchasing additional credits."** So the $0.10 is a floor, not a ceiling, and there is no hard block at $0.10 — you just have to buy credits.
- No markup: "Hugging Face charges you the same rates as the provider, with no additional fees. We just pass through the provider costs directly."
- **The critical distinction, stated twice in the same doc:** the table "Free-tier included" is **Yes** for HF-routed requests and **No** for custom-provider-key requests. "If you supply your own provider key… **Hugging Face won't charge for the call**" and the "Monthly Credits ✅ Yes / ❌ No" row is unambiguous. Setting a custom key in HF settings silently removes your credit from the equation.
- Tracking: `https://huggingface.co/settings/inference-providers/overview` shows the past month broken down by model and provider. Billing: `https://huggingface.co/settings/billing`.
- Org billing requires explicitly specifying the org: `X-HF-Bill-To: my-org-name` header, or `bill_to=` on `InferenceClient`, or the `billTo` option in the JS client. Enterprise Resource Groups can be targeted instead.
- `hf-inference` is CPU-focused "As of July 2025" and bills compute-seconds beyond the credit.

Worked OpenAI-SDK example on the same page: `base_url="https://router.huggingface.co/v1"`, `api_key=os.environ["HF_TOKEN"]`, raw HTTP at `https://router.huggingface.co/v1/chat/completions`. This is a **verified base URL** and confirms HF is OpenAI-compatible.

### Inferences

- **$0.10/month is the smallest genuine recurring credit in the industry by roughly 50×.** At DeepSeek-V3.1-class pricing (~$0.25/M in, $0.95/M out per the Vercel catalog) that is a few hundred thousand input tokens — genuinely a few demo calls. HF's own framing is "to experiment with Inference Providers."
- **The PRO credit is the interesting one, and not for the reason it looks.** $2/month of *general-purpose compute* covers ZeroGPU overage, Inference Endpoints, and Jobs. If you already pay for HF PRO, the $2 is a bonus; if you don't, the decision is $9/mo for PRO versus $0.10 of free credit — a 20× jump, and the free path is not remotely close.
- **The custom-provider-key trap is the single most likely way to lose the credit without noticing.** Nothing errors; you simply start billing a third party and your HF balance stops draining. If you're debugging "why didn't my $0.10 get used," this is why.
- Because the free credit is "subject to change" and restricted to Inference Providers only, HF's free tier should not be treated as a planning input for anything.

### Gaps

- **I could not find HF's Inference Providers rate limits (RPM/TPM/RPD) for free vs PRO.** I fetched `huggingface.co/docs/inference-providers/rate-limits` and it returned the pricing page content, not a rate-limit table. No RPD/RPM numbers are reported here — HF may express limits as a credit-denominated soft cap only. **This is an unverified gap and directly answers half of the assigned question with "not found."**
- Whether the $0.10 is a hard daily allowance, a monthly pool subject to internal throttling, or purely a billing credit with separate throttling is not stated. There is no per-day reset language anywhere on the pricing page (unlike Cloudflare's explicit 00:00 UTC reset).
- Whether a card is required to create a free HF account or to purchase the extra credits: not stated on this page.
- Credit expiry: not stated on the pricing page.
- Commercial-use / training-on-output terms for HF Inference Providers: not read. Would need the HF Terms of Service and the individual model licenses.

---

## Q4. What does GitHub Models offer for free today, and what were its terms?

### Takeaway

**Nothing — GitHub Models no longer exists.** It was **fully retired as of July 30, 2026**: playground, model catalog, inference API, and BYOK are all gone for every customer. Any roundup, awesome-list, or tutorial recommending GitHub Models as a free tier is describing a dead service, and this is the single most important correction in this research.

### Cited Findings

- From [docs.github.com/en/github-models/prototyping-with-ai-models](https://docs.github.com/en/github-models/prototyping-with-ai-models), checked 2026-09-28, verbatim: "**GitHub Models has been retired. As of July 30, 2026, GitHub Models has been fully retired. The playground, model catalog, inference API, and bring your own key (BYOK) are no longer available to any customer.** GitHub Models was a separate service from GitHub Copilot and is unrelated to GitHub Copilot services."
- GitHub's own redirect guidance: "For new and existing projects that need AI model access, **Azure AI Foundry** offers a broad model catalog." And: "To build AI-powered workflows directly on GitHub, you can use GitHub Copilot, which gives you access to a range of models."

### Inferences

- **The GitHub Models free tier is not a shrunk offering — it is a shutdown.** Any inventory of "free LLM API options" that still lists GitHub Models is out of date by roughly two months.
- The retirement does not mean GitHub stopped shipping models; it means the free *inference* surface moved. Azure AI Foundry is a successor in the catalog sense, and Copilot covers the in-product sense. Neither is a free anonymous API.
- **Practical read for anyone building on this:** the retirement removes a "free tier with a card-optional, generous rate limit" option that was popular through 2024–2025. Nothing has replaced it at that generosity. OpenRouter, Requesty, and Cloudflare Workers AI are now the practical substitutes.

### Gaps

- I could not retrieve GitHub's retirement announcement blog post or changelog entry, only the docs page stating the date and scope. I did not read Copilot's own rate limits or terms, which are the nearest live successor.
- I cannot report the *historical* GitHub Models model catalog, rate limits, or commercial-use terms, because the catalog and API are gone and I have no archived primary source in hand. Third-party roundups describing the old catalog are not citable under this assignment's rules.
- Whether Copilot offers any free-tier model access for non-subscribers is unverified.

---

## Q5. Which of these are OpenAI-compatible, and which base URLs did I verify?

### Takeaway

Every aggregator in this class that has a modern API is OpenAI-compatible — that is now table stakes, not a differentiator. I verified base URLs on the **vendor's own page** for five vendors. The ones I could not verify are marked.

### Cited Findings — verified base URLs (vendor page, checked 2026-09-28)

| Vendor | Base URL | Where I read it | Compat surface |
|---|---|---|---|
| **Hugging Face** | `https://router.huggingface.co/v1` | [inference-providers/pricing](https://huggingface.co/docs/inference-providers/pricing) — Python + JS + raw HTTP examples | `chat.completions`, `completions`, `X-HF-Bill-To` header, `bill_to` param |
| **Requesty** | `https://router.requesty.ai/v1` | [requesty.ai/free-models](https://www.requesty.ai/free-models) | OpenAI + `ANTHROPIC_BASE_URL=https://router.requesty.ai` with `ANTHROPIC_AUTH_TOKEN` + `ANTHROPIC_MODEL`; claims compatibility with Cline, Cursor, Roo Code, Claude Code |
| **AI/ML API** | `https://api.aimlapi.com/v1` | [docs.aimlapi.com/readme-1](https://docs.aimlapi.com/readme-1) — "Base URL: `https://api.aimlapi.com`", endpoints `POST /v1/chat/completions`, `/v1/images/generations`, `/v1/embeddings`, `GET /v1/models`, `GET /v2/billing` | "OpenAI-compatible API, Anthropic-compatible API", streaming, multimodal, tool calling, JSON mode, `provider` routing override with source keys (`openai`, `openrouter`, `xai`, `google`, `alibaba`, `minimax`, `moonshot`, `baidu`, `togetherai`) or `auto` |
| **Segmind** | `https://api.segmind.com/v1/<model>` | [docs.segmind.com](https://docs.segmind.com/) — sample `POST https://api.segmind.com/v1/seedance-2.0` | REST, per-model paths (not `/chat/completions` shaped) |
| **OpenRouter** | `https://openrouter.ai/api/v1` | [docs/faq](https://openrouter.ai/docs/faq) — "OpenRouter implements the OpenAI API specification for `/completions` and `/chat/completions`"; models API at `/api/v1/models`; `GET /api/v1/key` for quota; `/api/v1/credits` for balance | "OpenRouter is a drop-in replacement for OpenAI. Therefore, any SDKs that support OpenAI by default also support OpenRouter." |

**Verified as OpenAI-compatible, base URL not read on the vendor page:**
- **Vercel AI Gateway** — the FAQ and pricing pages describe "compatible OpenAI, Anthropic Messages, OpenResponses, and Cohere" APIs and the AI SDK, but I did not read a base-URL string on a vendor page. `[unverified — commonly documented as https://ai-gateway.vercel.sh/v1/ai, but I did not confirm it]`
- **Fal.ai, Replicate, DeepInfra, Together, Baseten, SiliconFlow, ModelScope, Novita, Eden AI, Segmind AI Gateway, Kilo Gateway** — the aggregator/inference-host class is overwhelmingly OpenAI-compatible, but I only read base URLs for the five above. ModelScope's `https://api-inference.modelscope.cn/v1` and SiliconFlow's `https://api.siliconflow.com/v1` / `https://api.siliconflow.cn/v1` come from third-party guides and are **unverified**.
- **Cerebras, Groq, SambaNova, Nebius, Atlas Cloud, FriendliAI, Runware, Parasail, Wafer, RunInfra, DigitalOcean, Modal, Crusoe, Inceptron, StreamLake, Blackbox, Boundless, Relace, Particle, Relace, Morph, GMICLOUD** all appear as providers inside Vercel's and OpenRouter's catalogs (read 2026-09-28) — meaning they are reachable through those OpenAI-compatible aggregators even where their own SDKs differ. This is a useful workaround: reach an obscure host through OpenRouter/Vercel rather than integrating its native API.

**Native-SDK-only, or non-text, in this set:**
- **Cloudflare Workers AI** — callable via Workers `AI` binding and a REST API, and via AI Gateway. Not an OpenAI `/chat/completions` shape in its own right; model IDs are `@cf/...` namespaced. `[exact OpenAI-compat endpoint not verified on the vendor page]`
- **Fal.ai** — has its own `fal` client SDK and per-model endpoints; not a chat-completions gateway.
- **Replicate** — prediction/webhook model, `POST /v1/predictions` + polling or webhook, not chat completions.
- **Segmind** — per-model REST paths as shown above.
- **Hugging Face ZeroGPU Spaces** — Gradio-only, per the vendor page. Not an API surface at all.
- **Deno Deploy, Pipedream, Zeabur, Glif** — workflow/hosting platforms, not model APIs.

### Inferences

- **OpenAI compatibility is no longer a differentiator at the aggregator layer.** Five of five vendors whose base URLs I verified use it, including ones that also expose Anthropic/Cohere/OpenResponses shapes (Vercel, AI/ML API). The differentiators that remain are the free mechanics, the free-model list, and the privacy posture.
- **The interesting compat question is at the *provider* layer, not the aggregator layer.** The Vercel catalog shows 368 models served by ~50 named underlying providers. For a given model (say `zai/glm-5.3`, 18 providers on Vercel) the aggregator normalizes them all behind one OpenAI-shaped call. That is the practical reason to use an aggregator even when you already have a key for one of the providers: failover and normalization.
- **For obscure or new model hosts, the aggregator is the integration path.** Vercel's provider column is effectively a list of hosts that are reachable without a native SDK, as long as you go through the gateway.

### Gaps

- Vercel AI Gateway's actual base URL string — not read on a vendor page.
- Cloudflare Workers AI's OpenAI-compatible endpoint (if one exists) — not verified.
- No vendor page for Novita AI's base URL was successfully fetched (`novita.ai/docs/api-reference/quickstart` 404'd on crawl; only the quickstart and pricing pages rendered).
- DeepInfra's OpenAI-compat base URL (`api.deepinfra.com/v1/openai`) — not verified; the pricing page confirmed OpenAI-compat only via third-party sources.
- Together AI's `api.together.xyz/v1` — not verified; the docs pricing/overview page I read showed the Python/TS SDK usage, not the REST base URL.
- ModelScope and SiliconFlow base URLs are third-party only.

---

## Q6. Which free tiers forbid using output to train models, forbid commercial use, or require attribution?

### Takeaway

I found **no aggregator in this set with a free tier that imposes an explicit no-training-for-your-own-purposes clause, a non-commercial restriction, or an attribution requirement.** The restrictions that do exist run the *opposite* direction: they restrict the *vendor's* use of your data (training-on-your-prompts toggles), not your use of the output. GitHub Models' old commercial-use terms are moot (retired). The one genuinely alarming case is inside OpenRouter's own free catalog: models whose cards state prompts and completions **are logged by the model creator for feedback and training**.

### Cited Findings

**OpenRouter — vendor-side data controls, and separate free/paid toggles** ([docs/faq](https://openrouter.ai/docs/faq), [docs/guides/privacy/provider-logging](https://openrouter.ai/docs/guides/privacy/provider-logging), checked 2026-09-28):
- "Prompt and completion are not logged by default. We do zero logging of your prompts/completions, even if an error occurs, unless you opt-in."
- Opt-in logging pays you 1% off usage — only meaningful for paid models.
- "On your account settings page, you can set whether you would like to allow routing to providers that may train on your data… **There are separate settings for paid and free models.**"
- "Providers that do log, or where we have been unable to confirm their policy, **will not be routed to unless the model training toggle is switched on**." And violating your own setting produces a hard error: "you will get an error and your request will not complete."
- "Wherever possible, OpenRouter works with providers to ensure that prompts will not be trained on, **but there are exceptions**." OpenRouter disclaims that this setting "has no bearing on OpenRouter's own policies and what we do with your prompts."
- Retention: "OpenRouter **does not have routing rules that change based on data retention policies** of providers" — retention is exposed as structured data per endpoint for the *user* to filter on. So per-provider retention terms are visible but not enforced by the router.

**The training-on-your-prompts carve-outs inside the free tier**, both from the OpenRouter model pages and the `openrouter/free` router page (checked 2026-09-28):
- `stealth/space-bunny-alpha` (OpenRouter model card, verbatim): "Space Bunny Alpha is a stealth model… developed and operated by a third-party provider who has chosen to remain anonymous during this preview. OpenRouter routes requests to it and is not its developer, owner, or provider. **Prompts and completions may be retained by the provider but are not used for training; all other use is governed by the Stealth Model Terms.**" Note the trailing clause: other use is governed by a Stealth Model Terms document I did not read.
- `Owl Alpha` (on the `openrouter/free` router page): "**free to use during this testing** … and **prompts and completions are logged by the model creator for feedback and training**." This is a $0 model that explicitly trains on your prompts. It is not a `:free` variant and therefore is not covered by the free-model daily cap.
- The router page also documents a beta model: "Body Builder is in beta, and currently free. Pricing and functionality may change in the future."

**Vercel AI Gateway — ZDR is a paid add-on, and per-request ZDR is Pro/Enterprise only** ([vercel.com/docs/ai-gateway/pricing](https://vercel.com/docs/ai-gateway/pricing) and [/faq](https://vercel.com/docs/ai-gateway/faq), checked 2026-09-28):
- "**Zero Data Retention (ZDR)** routes requests to providers that have agreed not to retain or train on prompt data."
- Per-request ZDR: "No additional cost" but **availability: Pro and Enterprise**.
- Team-wide ZDR: **$0.10 per 1,000 requests**, **Pro and Enterprise**.
- This is a real limit on the free tier: **on a Vercel free-tier account you cannot select ZDR at all.** A `403` naming team restrictions means "A model or provider allowlist blocks the request."
- Per-model ZDR availability is flagged in the catalog (e.g. `alibaba/qwen-3-32b` — "Zero data retention: available; HIPAA compliant: available"), so which providers offer it is per-model, not global.
- Provider Allowlist: team-wide is $0.10/1,000 successful requests, Pro and Enterprise; the per-request `only` filter is free on all plans.

**AI/ML API — the strongest published data-position claim, on a service with no free tier** ([aimlapi.com/openai-alternative](https://aimlapi.com/openai-alternative), checked 2026-09-28): "Each application has its own specific requirements… we trust our clients to responsibly use our AI models in line with local laws and personal ethics. **We prioritize privacy, your data is not stored or used to improve our models.**" (Marketing copy, unverified against a ToS.)

**Cloudflare Workers AI** — I found **no** training/attribution/commercial clause on the pricing page. Guardrails is a separate chargeable feature: "Guardrails evaluates prompts and responses using `@cf/meta/llama-guard-3-8b` on Workers AI. **Usage is billed as Workers AI token-based inference.**" DLP scanning is "free on all plans," and accounts without Zero Trust get two predefined profiles (Financial Information; Social/Insurance/National ID).

**GitLab** — the one genuinely restrictive credit mechanic, though it is about credit consumption not usage rights: self-hosted models get "a **20% discount** on the credits consumed for an execution." And **included credits do not roll over**: "Unused credits do not roll over to the next month."

**Eden AI** — pay-per-use with no free tier and no usage restriction stated on the pricing page.

**SiliconFlow** — the free-model rules are about *limits*, not rights: free models are billed 0 with fixed rate limits, paid models are usage-tiered. No training/attribution/commercial clause found.

**Alibaba Model Studio** — free quota "仅抵扣模型实时推理（调用）产生的费用" (only offsets real-time inference charges); does not apply to certain scenarios. No rights clause found. There *is* a sharp operational hazard: if you have completed real-name verification and have not enabled "free quota stop-at-exhaustion," your Aliyun account **will be charged** past the free quota, and you can be left with an account balance owing.

### Inferences

- **"Forbid using the output to train models" does not appear as a free-tier restriction anywhere I read.** The concept is structurally absent: these are inference brokers selling tokens, and the model licenses (Apache-2.0, MIT, Llama community license, etc.) govern output use, not the broker's free tier. Anyone worried about this should be reading the *model's* license, not the gateway's free-tier terms.
- **The real restriction is ZDR gating, and it is a paid feature at Vercel.** "Pro and Enterprise" for per-request ZDR, and $0.10/1,000 for team-wide. So a privacy-conscious user on Vercel's $5/month free tier is *structurally unable* to assert ZDR, and OpenRouter's equivalent (the training toggle) is available on all accounts but only as strong as OpenRouter's per-provider knowledge — which the docs explicitly disclaim ("we have been unable to confirm their policy" for some providers).
- **The dangerous carve-out is the free-model-in-the-catalog-that-trains-on-you.** `Owl Alpha` is a $0 model whose card says your prompts and completions are logged for training. It is not `:free`, so it escapes the free-model daily cap, and the account-level toggle governs *routing to providers that train* — but this model's own disclosure suggests the toggle may not be what protects you here. The `stealth/*` models are governed by a "Stealth Model Terms" document I did not read. **If any prompt is sensitive, filter the free catalog by `data_collection` per endpoint and skip `stealth/*` and `Owl Alpha` entirely.**
- **No free tier in this set requires attribution.** This is worth stating positively: none of the free allowances I read carry an attribution or branding requirement. The nearest thing is Cloudflare's Guardrails feature being billed as normal Workers AI inference — that is a cost, not an attribution duty.

### Gaps

- **I did not read OpenRouter's Terms of Service or Privacy Policy.** The FAQ characterizes them; the ToS is the document that would state output rights, commercial-use terms, and any training prohibition. Unread.
- **I did not read the "Stealth Model Terms"** governing `stealth/space-bunny-alpha`, `stealth/pixel-canary`, and `Owl Alpha`. Given these are anonymous third-party preview models offered at $0, that document is the only place their actual terms live. **This is the highest-value unread document in this research** — a free, anonymous, opaque-preview model in the catalog is exactly where terms matter.
- **Model-level licenses were not audited.** Each free model's own license (Apache-2.0 vs Llama Community License with its acceptable-use policy vs research-only) governs output use. I noted `jaredpalmer/kev-4b` is described as "a compact, Apache-2.0 alternative" but did not systematically check the ~17 free models' licenses.
- **Cloudflare Workers AI's terms of use** (training, retention, commercial use) were not read — I read only the pricing page.
- **Vercel's data-handling page** was not read; I inferred the ZDR gating from the pricing and FAQ tables only.
- **Hugging Face's Terms of Service and the Inference Providers data-retention statement** were not read. Given HF is the incumbent here, its data policy for routed inference is a material gap.
- **SiliconFlow, ModelScope, and Alibaba Model Studio's terms** (all China-hosted, all with real-name verification) were not read. Cross-border data-transfer terms for a China-hosted free tier serving foreign developers are a genuine open question I did not answer.
- **GitLab Duo's GitLab-assistant-code-data usage terms** (GitLab has historically claimed a degree of usage rights over code suggested into its own models) — not read, and moot for Free-tier users who must buy credits anyway.

---

## Q7. What has changed in the last 12 months in aggregator free tiers?

### Takeaway

The dominant movement is **withdrawal, not expansion**: GitHub Models was fully retired (July 30, 2026), AI/ML API has no free plan at all, and the durable "free" options shifted from *credit grants* to *rate-limited free model slates* (OpenRouter's 17-model `:free` list, Requesty's 200/day). Free host compute is also being quietly trimmed (Deno Deploy's egress cut from 100GB to 20GB with no published date). The counter-movement is new entrants: Vercel's $5/month credit and a free-model router, plus a wave of small OpenAI-compatible gateways (Requesty, Kilo Gateway, and a long tail of unverifiable ones).

### Cited Findings

**Retirements and removals — the hard, citable cuts:**
- **GitHub Models: fully retired July 30, 2026.** Playground, catalog, inference API, and BYOK all gone. ([docs.github.com](https://docs.github.com/en/github-models/prototyping-with-ai-models), checked 2026-09-28) This is the only outright shutdown I found.
- **AI/ML API: no free plan.** "we don't offer a free plan, pricing is usage-based, so you only pay for what you use," minimum **$20** prepaid. A bonus-credit promotion is "currently marked 'temporarily unavailable'" `[third-party detail]`. ([aimlapi.com/openai-alternative](https://aimlapi.com/openai-alternative)) Note the *contradiction on the same vendor's site*: a marketing page claims "Yes, we offer a free trial… claim your free trial by getting an API key. Join Discord Community." Two vendor pages, opposite answers.
- **Hyperbolic has repositioned away from free model access** to GPU rental (On-Demand / Reserved / Private Cloud), with no free model API described. ([docs.hyperbolic.xyz](https://docs.hyperbolic.xyz/docs/rest-api/pricing))
- **Cloudflare locked the frontier models off the free tier.** Seven named models now "require a paid billing method": `@cf/moonshotai/kimi-k2.6`, `@cf/moonshotai/kimi-k2.7-code`, `@cf/zai-org/glm-5.2`, `@cf/zai-org/glm-5.3`, `@cf/zai-org/glm-5.3-flash`, `@cf/deepseek-ai/deepseek-v4-flash-0731`, `@cf/deepseek-ai/deepseek-v4-pro-0813`. The page was updated **Sep 17, 2026** — three weeks before I checked, so this is a *current-month* change. ([workers-ai/platform/pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/))
- **Deno Deploy free compute is being cut, quietly.** Third-party tracking `[third-party: billiem.uk, 2026-09-04; pocketlantern.dev, 2026-04-09]`: the Free plan went from 100GB egress (per Deno's GA announcement) to **20 GiB**, and from 15 CPU-hours / 350 GiB-hr memory / 20 apps to **10 CPU-hours / 150 GiB-hr / 10 apps**, with no published date for the reduction. The current pricing page confirms 20 GiB / 10 CPU-hours / 150 GiB-hr / 10 apps. Separately, Deno's changelog now says unverified orgs "can receive restricted limits until a payment method is linked," and exceeding quota **pauses apps** rather than billing overage.
- **GitLab's free tier got *worse* for AI, not better.** Free namespaces must now *purchase* a Monthly Commitment Pool of GitLab Credits to access any Duo Agent Platform feature. The promotional included credits ($12/user/mo Premium, $24/user/mo Ultimate) are explicitly "a limited-time promotion… at GitLab's discretion" and **do not roll over** month to month. ([docs.gitlab.com/subscriptions/gitlab_credits](https://docs.gitlab.com/subscriptions/gitlab_credits/), [about.gitlab.com/blog/gitlab-18-10](https://about.gitlab.com/blog/gitlab-18-10-agentic-ai-now-open-to-even-more-teams-on-gitlab/) dated 2026-03-19)
- **Roo's Cloud team plan has ended** and the Roo Code repo archives 2026-05-15, consolidating the coding-agent-router category onto Kilo. `[third-party: kilo.ai/compare, dated 2026-05-15]`

**New entrants and expansions:**
- **Vercel AI Gateway's $5/month free credit** is a recurring-credit offer that most roundups have not caught up to. ([vercel.com/docs/ai-gateway/pricing](https://vercel.com/docs/ai-gateway/pricing), updated 2026-09-08)
- **OpenRouter's Free Models Router `openrouter/free` shipped February 1, 2026** — a capability-aware random selector over the free set. ([openrouter.ai/openrouter/free](https://openrouter.ai/openrouter/free))
- **OpenRouter now has a $0 "Auto Router" and a "Pareto Code Router"** and a **Fusion** plugin (runs a panel plus a judge call, "priced as the sum of those underlying completions rather than a single model"). New routing vocabulary: `:nitro` (sort by throughput), `:floor` (sort by price), `:exacto` (quality-first, tuned for tool-calling reliability), `:online` (deprecated in favor of the `openrouter:web_search` server tool). ([docs/faq](https://openrouter.ai/docs/faq), [openrouter.ai/openrouter/free](https://openrouter.ai/openrouter/free))
- **OpenRouter restructured BYOK into plan-dependent allowances measured in list-price dollars, not requests** — $25,000/month PAYG, $200,000/month Enterprise, 5% fee above. That is a substantial change from the old request-count framing and it means BYOK is no longer a free-tier play. ([docs/faq](https://openrouter.ai/docs/faq))
- **Cloudflare unified Workers AI and AI Gateway billing (Aug 7, 2026)**, which both unlocks the paywalled frontier models via prepaid gateway credits and **raises their rate limit from 20 to 50 req/min**. ([developers.cloudflare.com/changelog/post/2026-08-07](https://developers.cloudflare.com/changelog/post/2026-08-07-workers-ai-unified-billing/))
- **Requesty's free tier is new and unusually generous**: 200 req/day, no card, no expiry, EU data residency, full platform. ([requesty.ai/pricing](https://requesty.ai/pricing))
- **Kilo Gateway** launched as the first-party zero-markup router from a coding agent, with `kilo-auto/free` as a free routing tier separate from OpenRouter's. ([openrouter.ai/blog/tutorials/kilo-code-openrouter](https://openrouter.ai/blog/tutorials/kilo-code-openrouter/), 2026-06-17)
- **Pipedream's free tier now includes an explicit AI-token component** (1M tokens/month `[third-party]`) rather than only workflow compute credits.
- **Alibaba Cloud extended its new-user free quota from 30–90 days to a flat 90 days** for accounts opened after 2025-09-08 / updated 2026-07-15. ([help.aliyun.com/zh/model-studio/new-free-quota](https://help.aliyun.com/zh/model-studio/new-free-quota))

**Churn in the free model lists themselves (the thing most roundups get wrong):**
- OpenRouter's free slate is a **rolling ~4-month window**: free-model `created` timestamps run from **Jun 3, 2026** to **Sep 23, 2026**, with a new free model added three days before I checked. 17 `:free` entries today. A June 17, 2026 OpenRouter tutorial says "**20+ free models**" — already stale. Any list older than about two months is wrong.
- **Every free text model across aggregators is a lite/nano/flash/small/preview/legacy SKU.** OpenRouter: no GLM 5.3, no Kimi K3, no DeepSeek V4 Pro, no Qwen 3.8 Max. Cloudflare's free tier: Llama 3.1/3.2/3.3 generation, small Gammas, QwQ-32B, gpt-oss. SiliconFlow's reported free list is entirely 2024/2025-era models (GLM-Z1-9B, GLM-4-9B, Hunyuan-MT-7B, Kolors). **The pattern is consistent: the free tier is where vendors park models they want advertised, not models they want sold.**
- `:free` variants can have **smaller context windows than their paid counterparts** (poolside Laguna S 2.1: 262,144 free vs 1M paid), and the variant is a separate catalog entry with separate endpoints — so a free-tier model can be a different deployment, not just a discount.

**On the long tail of unverified "free API" services:** a search surfaced numerous sites offering free unlimited access to frontier models (`completions.me` claiming "Unlimited free access to Claude Opus 4.6, GPT-5.2, Gemini 3.1 Pro… no rate limits, no credit card, no restrictions"), keyless resellers, and a "Lifetime plan" gateway. **I read none of these on a vendor page and I recommend against all of them.** A no-card, no-limit, no-authentication proxy to frontier models is not a business model — it is credential resale or an unpriced experiment, and your prompts are the product. I am listing them as a category, not as sources.

### Inferences

- **The direction of travel is that "free tier" is being replaced by "free models with a request cap."** Credit-based free tiers require the vendor to absorb cost per user; rate-limited free models let the vendor cap exposure precisely and gives them a reason to keep the account relationship alive. OpenRouter's 50→1,000 conversion mechanic and Requesty's "not a sandbox" framing are the same idea.
- **The frontier is being walled off, not opened up.** Cloudflare's September 2026 lockout of Kimi K2.6/2.7, GLM 5.2/5.3, and DeepSeek V4 from the free allocation, combined with Vercel gating ZDR behind Pro/Enterprise, and AI/ML API having no free plan at all, is a coherent picture: the free tier is now a small-model/lite-model marketing surface.
- **Hosting free compute is shrinking in real time, even as model free tiers hold.** Deno Deploy's silent egress cut is the clearest evidence, and it matters more than it looks: Deno/Cloudflare-style platforms are how you self-host the *inference* when the free model allowances run out, and that floor is moving.
- **GitHub Models' retirement is the loss of the category's most generous no-card tier, and nothing has replaced it.** For a year, "GitHub Models with a PAT" was the answer to "free GPT-4-class access without a card." That answer is now "OpenRouter at 50/day, Requesty at 200/day, or $9.99 to unlock OpenRouter's 1,000/day."
- **Practical consequence for planning: none of these are a foundation.** They are evaluation and prototyping capacity. The only one that could be a foundation is OpenRouter's 1,000/day tier at $9.99 lifetime, and even that is 17 lite models. Anyone building ongoing capability on a free tier is building on a list that rotates every few months.

### Gaps

- **I did not find or read vendor changelogs for most of these vendors**, so most of my "changes" are inferred from current-state pricing pages rather than read from dated announcements. The only dated change notices I read on vendor pages are Cloudflare's Aug 7 2026 changelog, Cloudflare's "Last updated Sep 17, 2026" on the Workers AI pricing page, and GitHub's July 30 2026 retirement date. **I could not read OpenRouter's, Vercel's, Requesty's, SiliconFlow's, or HF's changelogs**, so I cannot say what specifically changed in their free tiers over 12 months, only what they are today.
- **No historical snapshots.** I have no Wayback captures of any of these pricing pages, so every "changed in the last 12 months" claim is a comparison against the stale figures still circulating in third-party roundups, not against a verified prior state. The Deno Deploy and GitHub Models items are the only two I can state as dated vendor facts.
- **OpenRouter's "20+ free models" vs 17 `:free` vs router's "28":** I could not determine the correct current count, and I do not know whether free models were added or removed over the period.
- **I did not investigate whether Gemini / Gemini Developer API or GroqCloud free tiers changed** — both are named in a third-party free-tier survey as "model-dependent or rate-limited inference" with no quotable free table, and both are providers rather than aggregators so arguably out of scope, but a user asking "what changed" would reasonably want them.
- **I did not verify ModelScope's or SiliconFlow's free-model list churn**, only the current claimed snapshot from third parties.

---

## Appendix A — Quick reference: the three mechanisms, sorted

**Recurring credit (resets monthly):**
| Vendor | Amount | Card required? | Notes |
|---|---|---|---|
| Vercel AI Gateway | **$5/month** | **Yes** — `403 customer_verification_required` | Ends permanently once you purchase credits. Model access is a *subset* (conflicting vendor pages). |
| Hugging Face Inference Providers (free) | **$0.10/month**, "subject to change" | Not stated | Inference Providers only. Can be lost silently by setting a custom provider key. |
| Hugging Face PRO / Team | **$2.00/month** ($2/seat) | n/a (paid plan) | General-purpose HF compute, not Inference-Providers-only. |
| W&B Inference | "Free credits for a limited time" (amount not published) | n/a | Free tier default spend cap **$100/month**. Promotional and explicitly temporary. |
| DeepInfra | $5/month `[unverified, third-party]` | No to claim | Not found on deepinfra.com. |

**Rate-limited free models:**
| Vendor | Cap | Card? | Free model list published? |
|---|---|---|---|
| OpenRouter | 50/day (no credits) → **1,000/day at $9.99+ lifetime**; 20 rpm both tiers | No | **Yes** — 17 `:free` entries, listed above |
| Requesty | **200 requests/day** | **No** | **No** — gap |
| Cloudflare Workers AI | **10,000 Neurons/day**, resets 00:00 UTC (≈$0.11/day) | No | **Yes** — but 7 frontier models paywalled |
| SiliconFlow | Fixed per-model limits, not purchasable higher; **real-name verification required** | No to call free models | Third-party snapshot only |
| ModelScope | 2,000 calls/day total, ≤200/model/day `[unverified]` | No | No |
| Vercel (partial) | $5/mo against a *subset* | Yes | Conflicting |
| HF ZeroGPU Spaces | Free to use; PRO gets **8× daily quota**; host 2 free Spaces (acct >30 days) | No | N/A (Spaces, Gradio-only) |

**One-time grants (do not refill):**
| Vendor | Amount | Card? |
|---|---|---|
| Fireworks AI | **$1** | Not stated |
| SiliconFlow | **$1** international / ¥16 China (June 2026 announcement) | No |
| Baseten | **$30** per new workspace `[third-party]`; $2,500 Model APIs / $25,000 dedicated via Startup Program (vendor-verified) | No for the grant |
| Novita AI | "a voucher with some credit" — **amount undisclosed** | Undisclosed |
| ModelsLab | "free credits on signup" — **amount never stated** | No |
| Together AI | $15K/$30K/$50K, **application-only** | n/a |
| Eden AI | $10 `[2023 third-party only]`; **not on the vendor pricing page** | Unclear |
| Alibaba Model Studio | Per-model, **90 days, Singapore region only**, hard 403 `AllocationQuota.FreeTierOnly` at exhaustion | Real-name verification for pay-as-you-go |
| Fal.ai, Replicate, Hyperbolic, Segmind, AI/ML API, **GitHub Models** | **No free allowance** (AI/ML API: no free plan, $20 minimum) | n/a |
| GitLab Duo (Free tier) | **Must purchase credits** — no free AI on Free tier | Yes |

## Appendix B — Things I read that are worth knowing beyond free tiers

- **Cloudflare AI Gateway Unified Billing has a subtle carve-out:** "Workers AI models (models prefixed with `@cf/`) routed through AI Gateway are not charged via Unified Billing." So preloading gateway credits pays for third-party models (OpenAI, Anthropic, Google AI Studio) but not for `@cf/` models, and carries a **5% fee** on purchase ($100 → $105).
- **OpenRouter's in-flight spending budget** is a real operational trap for concurrent workloads: OpenRouter estimates a paid request's cost up front (input + `max_tokens` completion) and rejects it with `402` and `limit_source: openrouter_in_flight_budget` if the running total exceeds a fraction of your balance — **even with a positive balance**. It does not apply to free models, to pure-BYOK requests, or to enterprise/invoice accounts.
- **OpenRouter `:free` models carry no `X-RateLimit-*` headers on success.** Monitor via `GET /api/v1/key`; there is no other way to know your remaining daily quota.
- **HF's `$0.10` vs PRO's `$2.00` is a 20× gap, and PRO's credit buys a different thing.** Anyone modeling "how much free HF inference do I get" against the wrong number is off by an order of magnitude and possibly by currency.
- **Hugging Face ZeroGPU requires a 30-day-old account with a verified email to host even one free Space.** A new account cannot host ZeroGPU at all.
- **A useful catalog-level fact:** Vercel's model list shows which models are served by the widest set of providers — `zai/glm-5.3` (18), `moonshotai/kimi-k3` (19), `deepseek/deepseek-v4.1-flash` (16) — which is the practical proxy for reliability. A model on one provider is a single point of failure no matter what the gateway promises.
