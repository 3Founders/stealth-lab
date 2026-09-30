# First-party model-lab API free tiers, late September 2026

**All pages checked 2026-09-28.** Every number below is quoted from the vendor page named in
the citation. Where I could not reach or could not read a vendor page, the entry says
**unverified** and is listed under Gaps rather than asserted.

Scope note: this is *first-party* lab APIs and dev platforms only. Aggregators (OpenRouter,
Cloudflare Workers AI, GitHub free-endpoint lists) appear only where a vendor's own docs
name them; they are not treated as lab offerings.

---

## Which labs have a genuine ongoing free tier on their own API?

### Takeaway

Only a small number of first-party labs offer a *recurring, no-card, free-by-cost* tier that
a developer can rely on today: **Google AI Studio / Gemini API**, **Groq**, **NVIDIA
build.nvidia.com**, **SambaNova (documented, but see the 402 contradiction below)**, and
**Cohere's trial key** (capped by calls, not cost). Everyone else is either a one-time signup
credit (Cerebras $5/30 days, AI21 $10/7 days, Baidu, Tencent TokenHub, Alibaba), a
free-by-cost tier restricted to specific small/free models (Z.AI GLM-*-Flash), a recurring
*paid* credit allowance (OpenAI $100/mo row, HF $0.10–$2.00/mo credits), or has no free tier
at all (Anthropic, xAI, DeepSeek, Perplexity, Databricks, MiniMax, Kimi).

### Cited Findings

- **Google AI Studio / Gemini API** — Free tier exists with "Free input & output tokens" and
  "Content used to improve our products*"; Paid tier flips that to "No" —
  [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing).
  Limits are RPM / input-TPM / RPD, applied **per project not per API key**, and RPD resets at
  **midnight Pacific** — [ai.google.dev/gemini-api/docs/rate-limits](https://ai.google.dev/gemini-api/docs/rate-limits).
  Free tier is "Active project or free trial" with no billing-tier cap; the spend-based rate
  limit table lists **Free = N/A**, Tier 1 = $10/10min, Tier 2 = $50, Tier 3 = $200 (rolling
  10-minute window) — same page. The page does **not** publish a numeric free-tier RPM table
  in the text I could read; it says "View your active rate limits in AI Studio" and "Specified
  rate limits are not guaranteed and actual capacity may vary."
- **Groq** — publishes a full per-model table on the "Developer plan" base limits; free-plan
  rows include `openai/gpt-oss-120b` **30 RPM / 1,000 RPD / 8K TPM / 200K TPD**,
  `openai/gpt-oss-20b` 30/1K/8K/200K, `qwen/qwen3.6-27b` 30/1K/8K/200K,
  `qwen/qwen3.8-27b` 30/1K/8K/200K, `groq/compound` 30 RPM / 250 RPD / 70K TPM,
  `whisper-large-v3(-turbo)` 20 RPM / 2,000 RPD / 7.2K ASH / 28.8K ASD,
  `meta-llama/llama-prompt-guard-2-*` 30 RPM / 14.4K RPD —
  [console.groq.com/docs/rate-limits](https://console.groq.com/docs/rate-limits).
  "Rate limits apply at the organization level, not individual users"; "Cached tokens do not
  count towards your rate limits."
- **NVIDIA build.nvidia.com (NVIDIA API Catalog / NIM)** — "The free tier runs on rate
  limits - up to **40 requests per minute (RPM)** for most models, with no per-token
  billing. Your personal rate limit is shown in the top-right of the dashboard." and "All
  models on build.nvidia.com are free to prototype with" —
  [build.nvidia.com/faq](https://build.nvidia.com/faq) (the same text renders on
  [/terms-of-use](https://build.nvidia.com/terms-of-use)). Getting a key requires "Create and
  verify your account to unlock full access"; NIM *container* download requires joining the
  free NVIDIA Developer Program or an AI Enterprise license, and NVIDIA AI Enterprise offers
  a "free 90-day license" — [docs.nvidia.com/nim/large-language-models/latest/getting-started.html](https://docs.nvidia.com/nim/large-language-models/latest/getting-started.html).
- **Cohere** — evaluation/trial keys are "free but limited in usage"; "Trial keys (and prod
  keys on newer Chat model variants) are limited to **1,000 API calls a month**". Per-model
  trial rate limit is **20 req/min** for Command A+/A Reasoning/A Translate/A Vision/A,
  Command R+/R/R7B, North Mini Code. Rerank 10 req/min, Embed 2,000 inputs/min, Tokenize
  100 req/min, Audio Transcriptions 5 req/min, EmbedJob 5 req/min, Parse and default
  500 req/min — [docs.cohere.com/docs/rate-limits](https://docs.cohere.com/docs/rate-limits).
  Not free-by-cost: it is a hard monthly *call* cap, and production keys are what you need
  above it.
- **Cerebras** — explicitly *not* a free tier: "**Is there a permanently free tier? No.** The
  Free Trial is time- and credit-bounded: $5 in credits that expire 30 days after they're
  granted." Free Trial rate limits: `gpt-oss-120b`, `zai-glm-4.7`, `gemma-4-31b` all
  **5 RPM / 30K TPM / 1M TPH / 1M TPD**; a verified payment method is required and without
  one "Playground and API access remain inactive." Dual token bucket: uncached TPM is the
  primary limit, total TPM = 3× uncached —
  [inference-docs.cerebras.ai/support/rate-limits](https://inference-docs.cerebras.ai/support/rate-limits).
- **SambaNova** — the official rate-limits page still defines tiers as "Free Tier: Applied
  when there is **no payment method** linked with your account" vs "Developer Tier: Applied
  when a payment method is linked", and publishes a Free Tier table: `DeepSeek-R1` **20 RPM /
  400 RPD**, `DeepSeek-R1-Distill-Llama-70B` 80/1,600, `DeepSeek-V3-0324` 20/400,
  `Deepseek-V3.1` 20/400, `Meta-Llama-3.3-70B-Instruct` 80/1,600,
  `Meta-Llama-3.1-8B-Instruct` 480/9,600, `Llama-4-Maverick-17B-128E-Instruct` 20/400,
  `Whisper-Large-v3` 150/3,000, `Qwen3-32B` 10/200, `E5-Mistral-7B-Instruct` 20/400 —
  [docs-prod.sambanova.ai/docs/en/models/rate-limits](https://docs-prod.sambanova.ai/docs/en/models/rate-limits)
  (note the page also says TPD is a "Free tier only" measure in its units list). **This is
  directly contradicted by third-party reports** — see the SambaNova contradiction note below.
- **Z.AI (Zhipu / GLM)** — free-by-cost, model-restricted: `GLM-4.7-Flash`, `GLM-4.5-Flash`
  and `GLM-4.6V-Flash` are listed **Free** for input, cached input, cached-input storage and
  output. Everything else (GLM-5.3, GLM-5.2, GLM-5.1, GLM-4.7, GLM-4.6, GLM-4.5, vision
  non-Flash) is priced per 1M tokens, with cached-input storage "Limited-time Free" —
  [docs.z.ai/guides/overview/pricing](https://docs.z.ai/guides/overview/pricing). No RPM/RPD/
  TPM figure is published on that page.
- **Hugging Face Inference Providers** — this is a *recurring monthly credit*, not a
  free-by-cost tier: "Every Hugging Face user receives **monthly credits**"; **Free Users
  $0.10** ("subject to change"), **PRO $2.00**, **Team/Enterprise $2.00 per seat**. Free-user
  credits can be spent on Inference Providers and "extra usage (pay-as-you-go)" requires
  credits purchase. HF charges provider rates with no markup —
  [huggingface.co/docs/inference-providers/pricing](https://huggingface.co/docs/inference-providers/pricing).
- **OpenAI** — the rate-limits page's usage-tier table still lists a **Free** row: "Free |
  User must be in an allowed geography | $100 / month", then Tier 1 ($5 paid → $100/mo)
  through Tier 5 ($1,000 paid → $200,000/mo) —
  [platform.openai.com/docs/guides/rate-limits](https://platform.openai.com/docs/guides/rate-limits).
  The pricing page I read lists only per-token prices for every model and **no free tier
  block** — [platform.openai.com/docs/pricing](https://platform.openai.com/docs/pricing).
  Whether the Free row means a live free allowance or a legacy "usage limit" ceiling is
  **ambiguous**; see Gaps. I could not read help.openai.com's free-tier article (HTTP 403).
- **Anthropic** — no free tier. The only way to get spend limits is to buy credit: "Tier 1 |
  $5 credit purchase | $100 max usage per month" through Tier 4 ($400 → $5,000) —
  [docs.anthropic.com/en/api/rate-limits](https://docs.anthropic.com/en/api/rate-limits)
  and the current equivalent at
  [platform.claude.com/docs/en/api/rate-limits](https://platform.claude.com/docs/en/api/rate-limits),
  which shows the same deposit ladder plus Start/Build/Scale spend caps ($500 / $1,000 /
  $200,000).
- **xAI** — no free credit documented. Onboarding is "Sign up for an account at console.x.ai,
  then **load it with credits** to start using the API" —
  [docs.x.ai/docs/tutorial](https://docs.x.ai/docs/tutorial). Rate-limit **tiers** start at
  "Tier 0 | $0 (default)" and are per-model RPS/TPM (e.g. `grok-4.6` T0 150 RPS / 50M TPM;
  `grok-4.3` T0 37 RPS / 10M TPM) — [docs.x.ai/developers/rate-limits](https://docs.x.ai/developers/rate-limits).
  A $0 tier with rate limits is not a free tier if you cannot make a request without a
  positive balance.
- **DeepSeek** — paid only. `deepseek-flash` and `deepseek-v4-pro` are priced per 1M tokens
  with off-peak/peak rates (peak 01:00–04:00 and 06:00–10:00 UTC, Mon–Fri) —
  [api-docs.deepseek.com/quick_start/pricing](https://api-docs.deepseek.com/quick_start/pricing).
  The only "free" mechanics documented are **concurrency limits** (500 for v4-pro, 2,500 for
  v4-flash) that a capacity-expansion request can raise at "no additional cost" —
  [api-docs.deepseek.com/quick_start/rate_limit](https://api-docs.deepseek.com/quick_start/rate_limit).
  No free token allowance is documented.
- **Together AI** — no published free tier; pricing is per-token for every serverless model
  except one, `Prism-ML/Ternary-Bonsai-27B`, listed **Free** for input and output —
  [docs.together.ai/docs/serverless-models](https://docs.together.ai/docs/serverless-models).
  Rate limits are **dynamic, not published**: "there are no fixed per-model limits
  published"; `503` = capacity, `429` with `dynamic_request_limited` / `dynamic_token_limited`
  = above your rate — [docs.together.ai/docs/rate-limits](https://docs.together.ai/docs/rate-limits).
- **Perplexity** — Tier 0 is "$0 | New accounts, limited access", and rate limits by model
  at Tier 0: `sonar`, `sonar-pro`, `sonar-reasoning-pro` **50 RPM**,
  `sonar-deep-research` **5 RPM**, `POST /v1/async/sonar` 5 RPM. Tiers unlock on *cumulative
  credits purchased* (Tier 1 = $50+, … Tier 5 = $5,000+) —
  [docs.perplexity.ai/guides/rate-limits](https://docs.perplexity.ai/guides/rate-limits).
  All models are priced per token (Sonar $1/$1, Sonar Pro $3/$15, etc.) —
  [docs.perplexity.ai/getting-started/pricing](https://docs.perplexity.ai/getting-started/pricing).
  Tier 0 gives rate limits, not free tokens.
- **AI21 Labs** — one-time credit only: "**$10 credits for 7 days. No credit card needed**" —
  [ai21.com/pricing](https://www.ai21.com/pricing). Jamba Mini $0.2/$0.4, Jamba Large
  $2/$8 per 1M in/out.
- **IBM watsonx.ai** — a real recurring monthly allowance on the Free plan: "**Up to 300,000
  tokens per month**" for Foundation Models, plus 20 CUH/month of ML tools and 100
  documents/month of Text Extraction —
  [ibm.com/products/watsonx-ai/pricing](https://www.ibm.com/products/watsonx-ai/pricing).
  The model tables on that page are all pay-as-you-go or deploy-on-demand; the 300k figure is
  the plan-level inclusion.
- **Databricks Foundation Model Serving** — no free tier; everything is DBU-denominated
  (e.g. GPT-OSS-120B 2.143/8.571 DBU per 1M in/out) —
  [databricks.com/product/pricing/foundation-model-serving](https://www.databricks.com/product/pricing/foundation-model-serving).
- **Writer** — product trial, not an API free tier: "14-day free trial. No credit card
  required", up to 5 users, 5 playbooks, 1 GB knowledge graph —
  [writer.com/pricing](https://www.writer.com/pricing/).
- **Alibaba Cloud Model Studio (DashScope / Qwen)** — I could not read a free-quota page
  (404 on the help URL; the models page rendered as a nav list with no quota data —
  [help.aliyun.com/zh/model-studio/models](https://help.aliyun.com/zh/model-studio/models)).
  What I did verify: rate limits are **account-level across all RAM users, workspaces and API
  keys**, per-model, and enforced at per-second granularity as well as RPM/TPM, with a
  separate "Request rate increased too quickly" burst guard that can trip *even below* RPM/TPM
  — [alibabacloud.com/help/en/model-studio/rate-limit](https://alibabacloud.com/help/en/model-studio/rate-limit).
- **Baidu Qianfan** — one-time credit, per model, expiring: automatic new-user grants of
  **1,000,000 tokens valid 3 months** each for ERNIE-4.5-Turbo-128K, ERNIE-4.5-Turbo-32K,
  ERNIE-4.5-Turbo-VL, ERNIE-X1-Turbo-32K, DeepSeek-R1 / R1-250528 / V3-250324 /
  V3.1-250821 / V3.1-Think-250821, Kimi-K2-Instruct, several Qwen3 variants, bge-large-en/zh,
  qianfan-sug-8k. Credits only cover online inference, not batch —
  [cloud.baidu.com/doc/qianfan/s/Imi2rpirg](https://cloud.baidu.com/doc/qianfan/s/Imi2rpirg).
  A separate paid "Token Plan 个人版" exists at ¥9.9/month for 10M tokens etc. —
  [cloud.baidu.com/doc/qianfan/s/Dmrabu8b6](https://cloud.baidu.com/doc/qianfan/s/Dmrabu8b6).
- **Tencent** — two distinct offers. (a) **TokenHub new-user free pack**: "所有语言模型均提供
  **100 万 Tokens** 的免费体验额度，有效期 **1 年**" and the same for multimodal
  understanding models; per main account, per model, claimable once, quota shared across
  models, unused balance auto-expires; "免费额度用尽后，如果未开启后付费，服务将自动停止" —
  [cloud.tencent.cn/document/product/1823/130053](https://cloud.tencent.cn/document/product/1823/130053)
  and [tencentcloud.com/act/pro/tokenhub](https://www.tencentcloud.com/act/pro/tokenhub), which
  states 1M tokens per model valid 90 days from claim, promo running to 2026-12-31.
  (b) **Hunyuan global**: "one-time free resource package", Hunyuan-translation **100M
  tokens, valid 1 year** — [tencentcloud.com/document/product/1284/77186](https://www.tencentcloud.com/document/product/1284/77186).
- **MiniMax** — the pricing page I read is audio-subscription only ($5–$999/month, audio
  points, voice slots, RPM tiers) and shows no free API tier —
  [minimax.io/platform/document/pricing](https://www.minimax.io/platform/document/pricing).
- **Liquid AI** — open-weight models distributed via HF/GGUF/MLX/ONNX and consumed through
  OpenRouter; the docs describe no first-party hosted free API —
  [docs.liquid.ai](https://docs.liquid.ai/).
- **Nous Research** — homepage positions Hermes/Hermes Agent; no public free inference API
  documented on the page I read — [nousresearch.com](https://nousresearch.com/).

### Inferences

- The free-by-cost set is narrowing to *open-weight hosts* (Groq, NVIDIA, Google, SambaNova-on-paper)
  plus *specific free model SKUs* (Z.AI GLM-*-Flash, Together's Ternary-Bonsai). Frontier
  closed labs (Anthropic, xAI, DeepSeek, Perplexity, AI21, Databricks) are all credit- or
  deposit-gated.
- "Free tier" is doing three different jobs in this landscape: (1) rate-limited no-cost
  inference, (2) a monthly dollar/token allowance, (3) a time-boxed signup credit. Only (1)
  and (2) survive the month; (3) is the dominant pattern among the Chinese labs and the
  Western enterprise labs.
- Google is the only major lab where the free tier is *worse on privacy* than the paid tier
  (see the training/licensing section) — the price of the free tier is your data.

### Gaps

- **SambaNova: vendor docs and observed behavior disagree.** The official rate-limits page
  still documents a no-card Free Tier (above), but a third-party writeup dated 2026-07-03
  reports the API now returns `402 PAYMENT_METHOD_REQUIRED` on a zero balance and the
  pricing page shows per-token rates with no free row —
  [toolfreebie.com/sambanova-cloud-free-api](https://toolfreebie.com/sambanova-cloud-free-api/).
  SambaNova's own community shows paid-tier users hitting a `200000` TPD (free-tier) cap in
  May 2026 —
  [community.sambanova.ai/t/i-keep-getting-429-rate-limit-error-on-developer-account/1654](https://community.sambanova.ai/t/i-keep-getting-429-rate-limit-error-on-developer-account/1654).
  **Treat SambaNova's free tier as probably dead; verify by making one real call.**
- **OpenAI Free tier**: is there a live free credit or a vestigial table row? Unresolved.
- **Google's numeric free-tier RPM/RPD/TPM per model**: not published in the doc text; only
  visible in-product in AI Studio. The commonly-cited "Gemini 2.5 Flash 10 RPM / 250 RPD"
  figure appears only in third-party comparisons, not on ai.google.dev.
- **Alibaba DashScope free quota**: page 404s for me; quota mechanism unverified.
- **Reka, AI Labs, Sakana AI, Mistral's free-tier numbers**: pages were unreachable
  (CRAWL_NOT_FOUND / 403). Mistral's page confirms a free tier exists ("including a **free
  API tier** with restrictive rate limits... designed to allow you to try and explore our
  API", limits at workspace level, RPS + tokens/minute/month, actual numbers only in the
  console at admin.mistral.ai/plateforme/limits — [docs.mistral.ai/deployment/laplateforme/tier](https://docs.mistral.ai/deployment/laplateforme/tier))
  but the numbers themselves are **unverified**.
- **Moonshot / Kimi**: pricing and quickstart pages read; every model (Kimi K3, K2.7 Code,
  K2.6) is priced per token with no free allowance documented. **Unverified** whether a
  signup credit exists.
- **Indian labs** (Sarvam, Krutrim, Bhashini/AI4Bharat): pages unreachable this pass. No
  first-party free API tier verified.
- **Hugging Face Inference Endpoints** (dedicated, not Inference Providers) pricing page
  returned an error; only the Inference Providers credit table is confirmed.
- **AWS Bedrock / Amazon Q free tier**: I read the Bedrock pricing page and it is entirely
  per-token/on-demand; no free tier for foundation-model inference appears there. **Amazon Q**
  Developer Tier free access is unverified.

---

## What are the OpenAI-compatible base URLs for the free tiers?

### Takeaway

Six base URLs were confirmed directly on vendor pages. Groq, DeepSeek, xAI, Together,
SambaNova and Alibaba's international DashScope endpoint are the confirmed ones; the
Cerebras, Mistral, Google-compat and Z.AI URLs are widely used but were **not** read on a
vendor page in this pass.

### Cited Findings

- Groq: `https://api.groq.com/openai/v1` — "OpenAI base URL: https://api.groq.com/openai/v1" —
  [console.groq.com/docs/overview](https://console.groq.com/docs/overview) (and
  [docs.groq.com/guides/free-tier](https://docs.groq.com/guides/free-tier)).
- DeepSeek: `https://api.deepseek.com` (OpenAI format) and `https://api.deepseek.com/anthropic`
  (Anthropic format) — [api-docs.deepseek.com/quick_start/pricing](https://api-docs.deepseek.com/quick_start/pricing).
  DeepSeek also exposes the Anthropic Messages shape, with `user_id` under `metadata` —
  [api-docs.deepseek.com/quick_start/rate_limit](https://api-docs.deepseek.com/quick_start/rate_limit).
- xAI: `https://api.x.ai/v1` — shown in the OpenAI-SDK example —
  [docs.x.ai/docs/tutorial](https://docs.x.ai/docs/tutorial).
- Together: `https://api.together.ai/v1` (from the curl example) —
  [docs.together.ai/docs/pricing](https://docs.together.ai/docs/pricing).
- SambaNova: `https://api.sambanova.ai/v1` — `servers: - url: https://api.sambanova.ai/v1`
  in the published OpenAPI document —
  [docs.sambanova.ai/docs/api-reference](https://docs.sambanova.ai/docs/api-reference/).
- Alibaba Model Studio international: `https://dashscope-intl.aliyuncs.com/compatible-mode/v1`
  in the rate-limit mitigation sample —
  [alibabacloud.com/help/en/model-studio/rate-limit](https://alibabacloud.com/help/en/model-studio/rate-limit).
- Kimi/Moonshot: "Kimi API is compatible with both the OpenAI and Anthropic API formats" —
  [platform.moonshot.ai/docs/guide/start-using-kimi-api](https://platform.moonshot.ai/docs/guide/start-using-kimi-api).
  The literal base URL string was in the truncated part of that page — **unverified**.
- Baidu Token Plan: OpenAI-compatible `https://qianfan.baidubce.com/v2/tokenplan/personal`
  and Anthropic-compatible `https://qianfan.baidubce.com/anthropic/tokenplan/personal` —
  [cloud.baidu.com/doc/qianfan/s/Dmrabu8b6](https://cloud.baidu.com/doc/qianfan/s/Dmrabu8b6).
  (This is the *paid* personal plan's endpoint, not the free-credit one.)

### Inferences

- An OpenAI SDK can point at Groq, DeepSeek, xAI, Together, SambaNova and DashScope-intl
  without a shim; those are the only ones I can assert. For Gemini, Mistral, Cerebras and
  Z.AI the compatibility endpoints are almost certainly live but I will not publish a URL I
  did not read.

### Gaps

- Gemini OpenAI-compat endpoint, Mistral `api.mistral.ai/v1`, Cerebras
  `api.cerebras.ai/v1`, Z.AI base URL, Kimi base URL, Perplexity, Cohere, NVIDIA
  `integrate.api.nvidia.com/v1` — **unverified** in this pass (pages truncated or
  unreachable). The NVIDIA one in particular should be read off a model page before use.

---

## Which free tiers forbid using outputs to train models, or forbid commercial use?

### Takeaway

Google is the one major lab where the free tier is explicitly worse: the pricing table's
"Used to improve our products" row is **Yes** on Free and **No** on Paid. I found no first-party
free tier that forbids commercial use; the restrictions that do exist are "professional or
business use only" framing, plus mainland-China real-name and content-moderation regimes.

### Cited Findings

- **Google Gemini API** — the pricing page's per-model table carries a row
  "**Used to improve our products | Yes | No**" (Free Tier = Yes, Paid Tier = No), and the Free
  plan bullet list includes "Content used to improve our products*" while the Paid plan
  explicitly says "Content not used to improve our products*" —
  [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing).
  The asterisk links to the terms page, which has a dedicated "**Unpaid Services**" section
  with its own "How Google Uses Your Data" — [ai.google.dev/gemini-api/terms](https://ai.google.dev/gemini-api/terms)
  (effective March 23, 2026). The "How Google Uses Your Data" body text under Unpaid Services
  was behind a collapsed section I could not expand — **partially unverified**, but the
  pricing table's Yes/No is unambiguous.
- **Google, use restrictions** — "Use of Google AI Studio and Gemini API is for developers
  building with Google AI models **for professional or business purposes, not for consumer
  use**"; "You may only access the Services (or make API Clients available to users) within
  an available region. **You may use only Paid Services when making API Clients available to
  users in the European Economic Area, Switzerland, or the United Kingdom**"; 18+ only; no
  use to develop competing models; no reverse engineering of models or weights —
  [ai.google.dev/gemini-api/terms](https://ai.google.dev/gemini-api/terms). **This is the
  sharpest regional restriction found in the whole survey: the free tier cannot be used to
  ship an end-user-facing API client in the EEA/CH/UK.**
- **Google, data retention** — "Google retains the following data for **fifty-five (55) days**
  ... Prompts ... Contextual Information ... Output"; and abuse-monitoring logs "are not used
  to train or fine-tune any AI/ML models besides those used specifically for policy
  enforcement" — [ai.google.dev/gemini-api/docs/usage-policies](https://ai.google.dev/gemini-api/docs/usage-policies).
- **NVIDIA** — build.nvidia.com is framed as prototyping only: "All models on
  build.nvidia.com are **free to prototype with**. Once you hit rate limits, see the Deploy
  section to use partner endpoints or deploy a NIM locally" —
  [build.nvidia.com/faq](https://build.nvidia.com/faq). Commercial-use terms for the hosted
  endpoint are not spelled out on that page — **partially unverified**.
- **Cohere** — evaluation keys are named as an evaluation artifact, and production keys are
  what "if you intend to use those models in production" means; contact-sales is required for
  Command A Reasoning on prod keys — [docs.cohere.com/docs/rate-limits](https://docs.cohere.com/docs/rate-limits).
  No explicit non-commercial clause found on the page — **unverified**.
- **SambaNova** — its own API spec is published under Apache 2.0 and the free tier is defined
  purely by "no payment method linked" with no non-commercial clause —
  [docs.sambanova.ai/docs/api-reference](https://docs.sambanova.ai/docs/api-reference/) and
  [docs-prod.sambanova.ai/docs/en/models/rate-limits](https://docs-prod.sambanova.ai/docs/en/models/rate-limits).
- **Baidu** — real-name verification is required for API use and content moderation follows
  Chinese law; both claims come from a **third-party** summary, not a Baidu page I read —
  [getfreeai.net/en/services/api/baidu/](https://getfreeai.net/en/services/api/baidu/)
  (dated 2026-01-28). Treat as **unverified** until the Qianfan terms are read directly.

### Inferences

- The "no training on your data" guarantee is the single strongest practical difference
  between Google's free and paid tiers, and it is the one that most affects whether a free
  tier is usable for anything resembling production work.
- The EEA/CH/UK Paid-Services-only rule means Google's free tier is effectively unavailable
  for the single largest regulated developer market in Europe. That is a bigger deal than any
  RPM number.

### Gaps

- The "How Google Uses Your Data" text under Unpaid Services (training specifics, opt-out
  mechanics) — collapsed section, not read.
- Whether Groq, Cerebras, Z.AI, Tencent, Baidu, Alibaba or the Chinese labs use free-tier
  prompts/outputs for training: **no vendor statement found**. Most are silent, which is not
  the same as permission.
- Terms-of-service / commercial-use text for: Groq, Cerebras, Z.AI, Together, Perplexity,
  AI21, Hugging Face, Databricks, Writer, IBM, and the Indian labs — **unread**.
- Whether NVIDIA's hosted free endpoint permits commercial use — **unverified**.

---

## Which free tiers are unreliable, throttled beyond stated limits, or prone to suspension?

### Takeaway

The documented enforcement ladder is consistent (429s and credit exhaustion), and I found
**no** vendor statement about key-sharing or multi-account bans in this pass — that part of
the brief is largely unverified. What I did find is: providers shipping several throttle
classes simultaneously (429/503/402), Alibaba's burst guard that trips below the published
limit, a SambaNova case of a paid account being served a free-tier cap, and Cerebras charging
a free credit against your prompt *before* processing.

### Cited Findings

- **Google** — enforcement ladder is explicit: "Get in touch... **Temporary usage limits: We
  may limit your access** to the Gemini API by adjusting rate limits or changing which model
  answers a specific request... **Temporary suspension**... **Account closure**" —
  [ai.google.dev/gemini-api/docs/usage-policies](https://ai.google.dev/gemini-api/docs/usage-policies).
  Automated + manual detection, 55-day retention of prompts/context/output.
- **Cerebras** — "If this estimated token consumption would exceed your available token
  quota, the request is **rate limited before processing begins**", estimated from
  `max_completion_tokens`; "Set `max_completion_tokens` appropriately for your use case to
  avoid **overestimating token usage and triggering unnecessary rate limits**" —
  [inference-docs.cerebras.ai/support/rate-limits](https://inference-docs.cerebras.ai/support/rate-limits).
  A trial whose $5 simply expires: "Once your credit[s]…" (page truncated mid-sentence).
- **Together** — two distinct failure codes that developers routinely conflate: `503` means
  *platform* capacity, not your usage; `429` with
  `dynamic_request_limited` / `dynamic_token_limited` means you exceeded your rate. "Sudden
  spikes far above your recent usage may be throttled" —
  [docs.together.ai/docs/rate-limits](https://docs.together.ai/docs/rate-limits).
- **Alibaba Model Studio** — four separate throttle triggers documented, including
  "**Request rate increased too quickly**: A sudden surge in call frequency triggered system
  stability protection, **even if Requests Per Minute (RPM) or Tokens per Minute (TPM) limits
  were not reached**" — [alibabacloud.com/help/en/model-studio/rate-limit](https://alibabacloud.com/help/en/model-studio/rate-limit).
  This is the clearest documented case of throttling beyond stated limits.
- **OpenRouter** (not first-party, but the mechanism generalizes): "Making **additional
  accounts or API keys will not affect your rate limits, as we govern capacity globally**" —
  [openrouter.ai/docs/api-reference/limits](https://openrouter.ai/docs/api-reference/limits).
  Groq says the same thing in substance: "Rate limits apply at the organization level, not
  individual users" — [console.groq.com/docs/rate-limits](https://console.groq.com/docs/rate-limits).
  Cerebras: "Rate limits apply at the organization level, not the user level" —
  [inference-docs.cerebras.ai](https://inference-docs.cerebras.ai/support/rate-limits).
- **SambaNova, paid users served free-tier caps** — a Developer-Tier account in May 2026 hit
  `429 ... Violated duration_s: 86400, current usage: 796942, limit: 200000` where the free
  TPD is 200,000; SambaNova staff acknowledged "You seem to be hitting the Free tier TPD vs the
  Dev Tier TPD which is 20000000" —
  [community.sambanova.ai/t/i-keep-getting-429-rate-limit-error-on-developer-account/1654](https://community.sambanova.ai/t/i-keep-getting-429-rate-limit-error-on-developer-account/1654).
  A second thread has staff saying tier changes are being **manually toggled** during a
  billing-system migration, so paid accounts were stuck on free limits —
  [community.sambanova.ai/t/rate-limits-for-developer-tier-still-only-20-requests-per-minute/1614](https://community.sambanova.ai/t/rate-limits-for-developer-tier-still-only-20-requests-per-minute/1614).
  A March 2026 thread titled "Rate limits are a deal breaker" is representative of
  dissatisfaction with the free tier's ceilings —
  [community.sambanova.ai/t/rate-limits-are-a-deal-breaker.../1602](https://community.sambanova.ai/t/rate-limits-are-a-deal-breaker-too-many-requests-retrying-in-3m-8s-attempt-8/1602).
- **Baidu Token Plan** — an unusually explicit anti-abuse clause for a nominally free-ish
  plan: "**仅限在兼容的AI编程和智能体工具中交互式使用，不可用于自动化脚本或应用后端。违规使用可能导致订阅暂停或API Key封禁**"
  ("interactive use only in compatible AI coding/agent tools; **not for automated scripts or
  application backends**; violation may result in subscription suspension or **API key
  ban**") — [cloud.baidu.com/doc/qianfan/s/Dmrabu8b6](https://cloud.baidu.com/doc/qianfan/s/Dmrabu8b6).
  Its own docs also warn "为保障所有用户服务体验，平台可能在短时间高频调用、异常并发调用等
  场景下进行流量调度". This is the clearest documented key-ban policy found.
- **Tencent TokenHub** — "免费额度用尽后，如果未开启后付费，服务将自动停止" (service stops
  dead at quota exhaustion unless post-paid is pre-enabled) —
  [cloud.tencent.cn/document/product/1823/130053](https://cloud.tencent.cn/document/product/1823/130053).
  Same for Hunyuan global: "If you have not activated the postpaid service, a billing exception
  error will occur" —
  [tencentcloud.com/document/product/1284/77186](https://www.tencentcloud.com/document/product/1284/77186).
- **Anthropic** — an explicit burst-cancellation caveat that bites in practice: "You may hit
  rate limits over shorter time intervals. For instance, a rate of 60 requests per minute (RPM)
  may be enforced as 1 request per second. Short bursts of requests at a high volume can
  surpass the rate limit" — [docs.anthropic.com/en/api/rate-limits](https://docs.anthropic.com/en/api/rate-limits).
- **Cerebras and Groq both exempt cached tokens** from rate limits (Groq: "Cached tokens do
  not count towards your rate limits"; Cerebras: separate uncached bucket, total = 3× uncached)
  — [console.groq.com/docs/rate-limits](https://console.groq.com/docs/rate-limits),
  [inference-docs.cerebras.ai](https://inference-docs.cerebras.ai/support/rate-limits). SambaNova
  makes no equivalent statement, so assume cached tokens count against its pool.

### Inferences

- "Rate-limited rather than free-by-cost" is true of almost every free tier here, and several
  additionally throttle on *shape* of traffic (Alibaba's burst guard, Anthropic's
  RPM-as-RPS, Cerebras's pre-flight `max_completion_tokens` estimate) rather than on volume.
  A client that retries aggressively is the most common way to make a free tier look broken.
- Alibaba's and Anthropic's burst guards mean **a naive retry loop converts a healthy free
  tier into a 429 stream**. The fix is smoothing plus jitter, not more retries.
- I found **no** vendor-published policy on API-key sharing or multiple-account suspension
  from Google, Groq, Cerebras, xAI, DeepSeek, OpenAI, Anthropic, Cohere, Z.AI, Tencent, Baidu
  or Alibaba. The only explicit key-ban language I read is Baidu's, and it attaches to
  *automation misuse* rather than to key sharing. Community knowledge that Gemini/Groq ban
  key resale is **unverified** and I am not asserting it.

### Gaps

- **Key-sharing / multi-account suspension policies: unverified across all providers.** No
  vendor ToS page was read on this point.
- Whether Google actually de-rates or suspends free-tier projects for multi-key/multi-account
  use — anecdotal only, not claimed here.
- Cerebras's behaviour when the $5 credit expires: page truncated mid-sentence.
- Actual observed reliability of the free tiers (I ran no calls). Everything in this section
  is either vendor-documented enforcement or vendor-community threads.

---

## What has changed in the last 12 months?

### Takeaway

One confirmed cut (SambaNova's standing free tier), two confirmations that the
signup-credit model is now the norm rather than an event (Cerebras, AI21), and Google's free
tier quietly *widened* (Gemini 3.x Flash models all priced "Free of charge" on the free tier).
Model churn has outpaced quota churn — several "free" model IDs cited in older guides
(gemma-3-27b, llama-3.3-70b on Groq) are gone from the current tables.

### Cited Findings

- **SambaNova free tier cut** — announced 2025-02-08: the Developer Tier replaced the Free
  Tier, with "$5 of free credit available to consume that expires in 3 months" as the
  transition, and RPM increases on 70B/405B models —
  [sambanova.ai/blog/sambanova-cloud-developer-tier-is-live](https://sambanova.ai/blog/sambanova-cloud-developer-tier-is-live).
  SambaNova staff wrote in January 2025: "**We do not have any current plans to maintain the
  free tier in any state**" —
  [community.sambanova.ai/t/is-free-tier-going-away/847](https://community.sambanova.ai/t/is-free-tier-going-away/847).
  A third-party check dated 2026-07-03 reports the API returning `402 PAYMENT_METHOD_REQUIRED`
  — [toolfreebie.com](https://toolfreebie.com/sambanova-cloud-free-api/).
  *Note the two vendor sources conflict in emphasis: the rate-limits doc page still
  documents a Free Tier, so either the doc is stale or the third-party test was wrong.*
- **Cerebras moved to trial-only** — "**Is there a permanently free tier? No.**" and "$5 in
  credits that expire 30 days after they're granted" with a required verified payment method —
  [inference-docs.cerebras.ai/support/rate-limits](https://inference-docs.cerebras.ai/support/rate-limits).
  The page also notes a rolling rollout: "Uncached rate limits are rolling out gradually. If
  you do not yet see an uncached token limit in your console, it will be visible by
  **August 17, 2026**" — a mid-2026 change to how the limit is metered.
- **Groq's free-plan model list is different from the historically cited one.** The current
  Developer-plan base limits include `openai/gpt-oss-120b`, `qwen/qwen3.6-27b`,
  `qwen/qwen3.8-27b`, `groq/compound`, `whisper-large-v3`, and two Canary Labs Arabic models
  (`canopylabs/orpheus-arabic-saudi`, `canopylabs/orpheus-v1-english`, each 10 RPM / 100 RPD /
  1.2K TPM) — [console.groq.com/docs/rate-limits](https://console.groq.com/docs/rate-limits).
  Older guides citing `llama-3.3-70b-versatile` on Groq's free tier are **stale**; that ID is
  not in the current table.
- **Google's free tier covers the whole Gemini 3.x Flash line.** `gemini-3.8-flash` and
  `gemini-3.7-flash` are both "Free of charge" for input, output (incl. thinking tokens) and
  context caching on the Free Tier, with paid prices shown alongside and a scheduled
  **doubling on 2027-01-01** ($0.75→$1.50 in, $3.75→$7.50 out) — but
  `gemini-3.1-pro-preview` is "**Not available**" on the free tier —
  [ai.google.dev/gemini-api/docs/pricing](https://ai.google.dev/gemini-api/docs/pricing).
  Priority inference is "0.3x the standard rate limit" — [rate-limits](https://ai.google.dev/gemini-api/docs/rate-limits).
- **Google added a spend-based rate limit** on top of RPM/TPM: Tier 1 $10, Tier 2 $50,
  Tier 3 $200 per rolling 10 minutes, Free = N/A — same page. This is a new dimension of
  control not present in older free-tier guides.
- **Z.AI's free SKUs are Flash-only and old**: `GLM-4.7-Flash`, `GLM-4.5-Flash`,
  `GLM-4.6V-Flash` are Free while the GLM-5.x generation is fully priced; the page also
  carries a live discount that "ends at 24:00 on **September 9, 2026**" (i.e. already expired
  as of this check) — [docs.z.ai/guides/overview/pricing](https://docs.z.ai/guides/overview/pricing).
- **Cohere narrowed the free surface** — "Prod keys work like trial keys for newer model
  variants such as **Command A Reasoning**" and "Trial keys (and prod keys on newer Chat
  model variants) are limited to 1,000 API calls a month", with contact-sales required for
  Command A+/A Reasoning/A Translate/A Vision on production — [docs.cohere.com/docs/rate-limits](https://docs.cohere.com/docs/rate-limits).
  Newer models are effectively evaluation-only.
- **Hugging Face free-user credits are tiny and explicitly unstable** — "$0.10, **subject to
  change**" per month, versus $2.00 for PRO —
  [huggingface.co/docs/inference-providers/pricing](https://huggingface.co/docs/inference-providers/pricing).
- **DeepSeek's pricing became time-of-day** — off-peak rates are half of peak, with peak
  01:00–04:00 and 06:00–10:00 UTC Mon–Fri — [api-docs.deepseek.com/quick_start/pricing](https://api-docs.deepseek.com/quick_start/pricing).
- **Tencent's free pack is time-boxed at the programme level** — "本期活动时间截至 **2026 年 12 月 31 日**，后续安排将根据运营情况延续或调整" —
  [cloud.tencent.cn/document/product/1823/130053](https://cloud.tencent.cn/document/product/1823/130053).
  It expires in ~3 months from this check.
- **OpenAI is on Tier 5 now** ($1,000 paid → $200,000/month usage limit), i.e. a tier ladder
  that assumes paid usage is the norm; the Free row's "$100 / month" is now the *lowest* rung
  and is out of step with the pricing page —
  [platform.openai.com/docs/guides/rate-limits](https://platform.openai.com/docs/guides/rate-limits).

### Inferences

- The 12-month trend is **consolidation**: providers that once had generous standing free
  tiers (SambaNova, Cerebras) converted them to signup credits, while the survivors
  (Groq, NVIDIA, Google) are the ones whose business is *not* selling tokens.
- Model-ID churn is the bigger practical hazard than quota churn. Half the "free model" lists
  circulating in 2025 are dead ID lists. Any free-tier inventory older than ~3 months should
  be treated as fiction and re-read from the vendor's rate-limit table.
- HF's "$0.10, subject to change" and Tencent's "until 2026-12-31" are both explicit
  self-declared instabilities — free-tier inventory is a moving target by the vendors' own
  admission.

### Gaps

- **No vendor changelog entries were read.** Google, Groq, Cerebras, Cohere, Z.AI, Alibaba,
  Tencent and Baidu all publish changelogs; a systematic read of the last 12 months of each
  would be the right source for quota-change announcements. I inferred the changes above from
  current-state pages and dated blog/community posts instead.
- The date on which SambaNova's free tier actually stopped working is **unverified** — the
  announcement is 2025-02-08, the community "no plans to maintain" is 2025-01-29, the third-
  party 402 observation is 2026-07-03, and the docs page still shows a Free Tier.
- Whether Cerebras's shift to trial-only has a specific announcement date — unverified.
- Alibaba DashScope's free-quota changes over the period — unverified (page 404).
- Any *new* free tier launched in the last 12 months that I would otherwise have missed —
  I did not find one; the providers added in this pass (Z.AI, Tencent TokenHub, Meta's
  Llama API, IBM, Perplexity Agent API) all use one of the three known patterns rather than
  introducing a new one.

---

## Adjacent findings (not free tiers, but adjacent and worth carrying)

- **Meta Llama API** appeared in search with a genuine free tier: "Free | 60 | 2,000,000"
  RPM/TPM vs "Paid | 3,000 | 4,000,000", limits **per team not per API key** —
  [ai.developer.meta.com/docs/getting-started/pricing-rate-limits](https://ai.developer.meta.com/docs/getting-started/pricing-rate-limits).
  This is a first-party lab free tier and I had not listed Meta in the assignment's roster.
  It surfaced only via search highlights and I did **not** fetch the page directly — flagged
  for the report writer as a probable genuine no-cost free-by-cost tier, **needs a direct read**.
- **Cloudflare Workers AI** — 10,000 Neurons/day free, resetting 00:00 UTC, and selected newer
  DeepSeek/GLM/Kimi models require a paid billing method. Not a model lab, but it is the
  common fallback when labs' free tiers throttle —
  [cloudflare.com/workers-ai/pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/),
  via [aireiter.com/blog/free-llm-api-quota-comparison](https://aireiter.com/blog/free-llm-api-quota-comparison)
  (secondary source, dated 2026-09-08; **discovery only, not verified on the vendor page here**).
