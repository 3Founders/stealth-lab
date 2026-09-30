# Free / near-free LLM inference for the substrate — offers, legality, and a 500M-token plan

Research date: 2026-09-27. Research-only; no product code touched.
Framing: **Research** (Band 3 measurement) + **Product** (Band P, cost of the substrate at scale).
Question: what inference can we run for free or near-free at 100–500M tool-calling tokens, is it
legally clean for a substrate that ingests redacted agent traces, and does it work from India?

**Tooling disclosure.** Parallel MCP was not connected for this sweep. All extraction went through
Exa search + direct page fetch instead. Two agents were dispatched via Task and their output was
discarded as unverifiable (they cited dead/aggregator URLs and community trackers as if they were
official); everything below was re-read from the provider's own page. This substitution is a real
reduction in coverage and is disclosed rather than papered over.

**Verification legend.** **[V]** number read directly off the official page, quoted. **[C]** stated
on an official page but not independently re-derived. **[U]** could not confirm — treat as unknown.

**Discovery sources, used for leads only.** `mnfst/awesome-free-llm-apis`,
`open-free-llm-api/awesome-freellm-apis`, `xinrui-z/free-llm`, `cheahjs/free-llm-api-resources`.
Every tracker claim below was checked against the provider's own page, and four of them turned out
to be wrong (see §5).

---

## 0. Headline findings

1. **One offer clears the 500M bar by three orders of magnitude: Hetzner's experimental Inference
   API.** Free while experimental, 4M input + 100K output tokens per 60s and 10 requests per 60s per
   API key. Over 21 days that is ~121B input tokens of headroom for a 500M plan — 0.4% utilisation.
2. **It is the only offer on this list that is EU-hosted, which matters for a substrate holding
   redacted traces.** Hetzner's own write-up notes "Lots of users praised the fact that this was
   entirely EU-based."
3. **Tool calling is undocumented and therefore unproven.** Hetzner's docs cover `/v1/models`,
   `/v1/completions`, `/v1/chat/completions` and message parts of type `text` / `image_url`. There is
   no `tools` parameter and no function-calling section anywhere on the page. The model itself
   (Apache 2.0) does tool calling natively, but the serving layer's support is an assumption until
   probed. This is the single make-or-break fact for our use case — see §3.3.
4. **It was scaled *down*, not up, after launch.** Hetzner launched the experiment 2026-08-07, hit
   capacity limits within 6 hours, and reduced the catalogue to smaller models. The current lineup
   is two Qwen models. This is the central reliability risk: the offer is free partly because it is
   experimental, and it can shrink or vanish with a single email.
5. **Four tracker claims are false or stale** — GitHub Models, OpenCode Zen, Cerebras, and the
   "free" W&B / Pollinations / Chutes entries (§5).
6. **India is fine on paper, unverified in practice.** No official page excludes India; the export
   clause bars embargoed countries only; the endpoint is a single global hostname with no region
   choice. What I could not verify without an account: whether signup demands a payment method, and
   actual latency from India (§6).

---

## 1. Live offers

| Provider | Free allowance (quoted) | Rate limit | Tool calling | Card / ID | Data statement | Source + date |
|---|---|---|---|---|---|---|
| **Hetzner Inference API** [V] | "As long as the Inference API remains in experimental status, it is free of charge." | 4M input + 100k output tok/60s; 10 req/60s, per API key; HTTP 429 | **Undocumented** — no `tools` param in docs | No card stated; token via `experiments.hetzner.com` → Inference tab | "We do not store the content of request and response and we do not plan on doing so in the future" | [docs](https://docs.hetzner.com/general/company-and-policy/experiments/inference/), 2026-07-23 |
| **Cloudflare Workers AI** [C] | 10,000 Neurons/day | Neuron-based | Yes (OpenAI-compatible) | No | Provider retention terms apply | [pricing](https://developers.cloudflare.com/workers-ai/platform/pricing/), undated |
| **LLM7** [C] | 500K anonymous / 1M free-token tokens per 24h | Per-24h token pool | Yes | No for anonymous tier | [U] | [limits](https://docs.llm7.io/limits), undated |
| **OpenTyphoon** [C] | Free research tier | 5 req/s, 200 req/min | Yes | Account | Usage data collected | [docs](https://docs.opentyphoon.ai/en/), undated |
| **Api.Airforce** [C] | Free plan | 1 RPM, 1,000 req/day | Yes | Account | [U] | [pricing](https://api.airforce/pricing/), undated |
| **Requesty** [C] | 200 free requests/day | Per-day request cap | Yes | Account | [U] — official page figures are internally inconsistent | [pricing](https://www.requesty.ai/pricing), undated |
| **Scaleway** [C] | 1M free tokens (one-time, not monthly) | Standard tier limits | Yes | Card | Standard DPA | [pricing](https://www.scaleway.com/en/pricing/model-as-a-service/), undated |
| **Hugging Face Inference Providers** [C] | $0.10/month credits | Tier limits | Yes | Account | [U] | [pricing](https://huggingface.co/docs/inference-providers/main/en/pricing), undated |
| **Modal** [C] | $30/month free compute | — (self-hosted, your own model) | Your choice | Card | Your own container | [pricing](https://modal.com/pricing), undated |
| **Lightning AI** [C] | 5 credits / 80 GPU hours | — (self-hosted) | Your choice | Card | Your own container | [pricing](https://lightning.ai/pricing/), undated |
| **Beam** [C] | $30/month free credit | — (self-hosted) | Your choice | Card | Your own container | [pricing](https://www.beam.cloud/pricing), undated |

**Reading the table.** Everything in rows 2–11 either needs a card, is metered in requests rather
than tokens, or gives you a GPU to serve your own weights. Only Hetzner is a hosted,
zero-cost, token-scale offer. The Modal / Lightning / Beam rows are the honest fallback if the
Hetzner experiment disappears: self-hosting Qwen3.6-35B-A3B (Apache 2.0) on ~$30/month of GPU
time is cheaper per token than any metered free tier, and it is the only row where you control
retention outright.

---

## 2. Expired, cut, or never-free

| Offer | Status | Evidence |
|---|---|---|
| **GitHub Models** | Retired **2026-07-30** | Official GitHub page announces service retirement. Trackers still list it. |
| **Roo Code** | Extension shut down **2026-05-15** | Official docs state the shutdown. |
| **OpenCode Zen** | No longer free — requires topping up **$20** balance | Official page. Tracker claims of free Zen models are obsolete. |
| **Cerebras** | No permanent free tier. New **verified-payment** accounts get **$5**, expiring after **30 days** | Official page. Both conditions — verified payment and the 30-day expiry — are usually dropped in trackers. |
| **W&B / Pollinations / Chutes** | Tracker "free" entries do not match current official offers | Official pages contradict the trackers. |

**Lesson worth keeping.** Every one of these is a row that a tracker still shows as green. A tracker
is a lead list, never a source. Any offer in this repo's config comments must carry the date it was
last read off the provider's page, or it will rot exactly this way.

---

## 3. Hetzner deep-dive

### 3.1 What it is

An OpenAI-spec REST API at `https://inference.hetzner.com/api/v1`, exposing
`/v1/models`, `/v1/completions`, `/v1/chat/completions`. Any OpenAI-compatible SDK or plain HTTP
client works. Current catalogue, quoted from the docs page dated 2026-07-23:

| Model | Type | Context | Modalities | License |
|---|---|---|---|---|
| `Qwen/Qwen3.6-35B-A3B-FP8` | Causal LM + Vision (MoE, 35B total / 3B active) | 262,144 tokens | Text, Image | Apache 2.0 |
| `Qwen3.8-27B` | Dense | 262,144 tokens | Text, Image | Apache 2.0 |

Both Apache 2.0 — no licence obligations ride along with the free access. Contrast with the Kimi K3
model Hetzner explicitly declined to offer for exactly that reason ("K3's very specific license
requirements did not allow us to offer it as a free experiment"). Hetzner's own reasoning is a
licence-cleanliness filter, which is a mild positive signal about the rest of the catalogue.

Note the MoE shape: 35B total, 3B active. That is a fast, cheap model, not a frontier one. It is in
the same class as `gpt-oss` / `deepseek` in our price table, not in the class of `gpt-4.1`.

### 3.2 Reliability, stated plainly

The docs page opens with: *"This service is provided for experimental purposes only and is offered
'as is'. We have no control over or liability for their behavior. Performance and availability are
not guaranteed, especially during periods of high demand. No backups are created … you should not
use the platform for production environments."*

The blog post (*What we learned from our Inference Experiment*, 2026-08-17) gives the trajectory:
launch 2026-08-07 → capacity limits within 6 hours → 8-GPU servers added → **scaled down to smaller
models** at T+7 days, with "further improvements" needed. The reason it is free is that it is an
experiment, and the direction of travel has so far been *down*.

There is **no SLA**. The `Cloud and vServer service agreement`'s 99.9% availability clause covers
Cloud Servers explicitly and excludes other cloud services; the Inference API is not a Cloud Server,
so it is not covered. Treat it as a dev/CI/harness resource, never as a substrate dependency.

### 3.3 Tool calling — the open question

The docs never mention `tools`, `tool_choice`, or function calling. They document exactly two
content part types (`text`, `image_url`). The vLLM-based serving stack Hetzner describes *does*
support tool calling for Qwen3, so the likely answer is yes — but "likely" is not a fact we can put
in a config comment.

This is a one-request probe, not a research project. Reproduce it in §4.

### 3.4 Data handling

Direct quotes, both from the docs page:

- *"We do only keep data that is necessary in order to track usage and (in the future) bill usage:
  request timestamps, token counts etc."*
- *"We do not store the content of request and response and we do not plan on doing so in the future
  (unless some law requires us to do so)."*

That is the best data posture on this entire list. It aligns with our own redaction chokepoint
(`backend/app/services/trace_redaction.py::redact_event`): we do not need to trust the provider not
to see plaintext, because we never send it. Worth being precise about the residual risk, though —
"we do not store" is a commitment in an experimental, as-is, no-liability service. That is a weaker
guarantee than a DPA-backed deletion clause, and it can change when the experiment changes.

---

## 4. How to use it, and the probe that decides everything

### 4.1 Getting access

1. Log in at `https://experiments.hetzner.com`.
2. In the left-hand **APPS** menu, select **Inference**.
3. Click **Create API Token** in the top-right. Copy it immediately — the docs pattern for Hetzner
   tokens is that they cannot be viewed again after the dialog closes.
4. Export it. Do not commit it; `backend/.env` is the established home and is gitignored.

No Hetzner Cloud project, no server, and no `hcloud` CLI is needed — the Inference token is
separate from the Cloud Console's per-project API tokens, and the endpoint is not `api.hetzner.cloud`.

### 4.2 Smoke test

```bash
curl -s https://inference.hetzner.com/api/v1/models \
  -H "Authorization: Bearer $HETZNER_TOKEN"
```

```bash
curl -s https://inference.hetzner.com/api/v1/chat/completions \
  -H "Authorization: Bearer $HETZNER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3.6-35B-A3B-FP8",
       "messages":[{"role":"user","content":"List three laws of thermodynamics."}]}'
```

### 4.3 The tool-calling probe (run this first)

```bash
curl -s https://inference.hetzner.com/api/v1/chat/completions \
  -H "Authorization: Bearer $HETZNER_TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"model":"Qwen/Qwen3.6-35B-A3B-FP8",
       "messages":[{"role":"user","content":"What is the weather in Berlin?"}],
       "tools":[{"type":"function","function":{
         "name":"get_weather",
         "description":"Get current weather for a city",
         "parameters":{"type":"object","properties":{
           "city":{"type":"string"}},"required":["city"]}}}],
       "tool_choice":"auto"}'
```

**Pass:** response contains `choices[0].message.tool_calls` with `function.name == "get_weather"`.
**Pass-with-caveat:** HTTP 200, the parameter is silently ignored, content comes back as prose — the
model works, the OpenAI tool protocol does not, and the coding-agent path is unusable.
**Fail:** HTTP 400 mentioning `tools` — the parameter is rejected outright.

Record the result in this file. Until it is recorded, treat tool calling as unsupported.

---

## 5. Is it okay for our use case?

I read our own code rather than assuming. Three concrete findings, two of them blockers.

### 5.1 Good: the text-only paths fit with no code change

`backend/app/debate/panel.py:136-147` builds an `AsyncOpenAI(api_key=..., base_url=...)` client. The
`general_compute_*` block in `backend/app/config.py` is exactly the OpenAI-compatible escape hatch,
and `Settings` has no `env_prefix`, so these are plain uppercase env vars:

```
USE_GENERAL_COMPUTE=true
GENERAL_COMPUTE_BASE_URL=https://inference.hetzner.com/api/v1
GENERAL_COMPUTE_API_KEY=<token>
GENERAL_COMPUTE_FALLBACK_MODEL=Qwen3.8-27B
```

Panel and judge agents are text-and-JSON only — `panel.py` never passes `tools`. So the debate and
extraction paths would work against Hetzner as soon as the base URL and key are set.

### 5.2 Blocker 1 — the panel needs 3 models, Hetzner has 2

`general_compute_panel()` (`panel.py:452-457`) raises unless `general_compute_panel_models` lists at
least three models, because `assert_heterogeneous` is what makes a debate worth having. Hetzner
offers exactly two, and both are Qwen — same lineage, which is the failure mode the whole
heterogeneity requirement exists to prevent. I checked what family string each derives under
`_derive_family` (`panel.py:409-438`): `Qwen/Qwen3.6-35B-A3B-FP8` → `"qwen3.6"` and `Qwen3.8-27B` →
`"qwen3.8"`, so the check would technically pass on string distinctness while both being Qwen
checkpoints. That is a false pass, and the right response is to not rely on it.

**Verdict: Hetzner cannot serve the debate panel.** It is a single-family, two-model endpoint. It
can serve the judge, the extraction/fallback path, and the coding agent — but a "panel" of two Qwen
variants arguing with each other is the correlated-error failure the panel exists to catch, and no
amount of prompt engineering fixes it.

### 5.3 Blocker 2 — the budget guardrail will mis-price it

`estimate_cost` (`backend/app/services/governance.py:133-173`) keys off a family→price table. There
is no `qwen` entry, so both Hetzner models fall into the unknown-provider branch and get priced at
the *most expensive* known rate (Anthropic, 3.0/15.0 per Mtok) with a warning log. Direction is
conservative and therefore safe, but it prices a free service as one of the most expensive ones, so
the spend cap will trigger at roughly a fourteenth of the real budget and quietly throttle ingest.

The fix is a two-line table addition — `"qwen3.6": (0.0, 0.0), "qwen3.8": (0.0, 0.0)`, alongside the
existing `local`/`mock` zero entries, since the service is genuinely free. Do not skip this: a
guardrail that fires on a free provider is an outage you will misdiagnose as a quota problem.

### 5.4 Blocker 3 — the coding agent depends on the unproven probe

`backend/app/execution/coding_agent.py:1025` passes `tools=self._tools` into the chat-completions
call. If the §4.3 probe fails, the coding-agent arm — the Band 3 measurement rig — cannot run
against Hetzner at all. This is the one that matters most, and it is a two-minute test.

### 5.5 Not covered: embeddings

`backend/app/services/embeddings.py` uses `local_base_url` or Gemini, and the substrate's vector
dimension is pinned to 1024 to match `VECTOR(1024)` in `backend/db/01_ontology.sql`. Hetzner serves
no embedding model. Retrieval keeps using local/Gemini/voyage regardless; Hetzner is a generation
substitute only. Worth stating because "swap the model provider" quietly implies otherwise.

### 5.6 Summary

| Path | Works with Hetzner? | Blocker |
|---|---|---|
| Debate panel | **No** | Needs ≥3 genuinely distinct families; Hetzner has 2 Qwen models |
| Judge | Yes | Pick a family distinct from the panel's — so not Hetzner, if the panel is not |
| Extraction / ingestion fallback | Yes | Add `qwen*` to `_PRICE_PER_MTOK` as 0.0 |
| Coding agent (`coding_agent.py`) | **Unknown** | Depends on the §4.3 tool-calling probe |
| Local agent runner | Yes | Same OpenAI-compatible path |
| Embeddings / retrieval | **No** | No embedding model served; keep local/Gemini |
| Production substrate traffic | **No** | "Do not use the platform for production environments", no SLA, no backups |

**Overall: it is a good fit for the measurement rig and the ingestion/extraction path, and a bad fit
for the panel, for embeddings, and for anything production.** That is a narrower but still useful
verdict, and it is the honest one.

---

## 6. India

### 6.1 What I could verify [V]

- **No official page excludes India.** The only country restriction I found is in the US-specific
  special terms, and it bars *embargoed* countries and named denied parties (OFAC SDN, Commerce
  Denied Persons). India is neither. The clause also says non-US users "access the Site or our
  Services from other countries or jurisdictions … on [their] own initiative and [are] responsible
  for compliance with the local laws of that jurisdiction."
- **No region choice is required.** `inference.hetzner.com` is a single global hostname. Unlike
  Cloud servers — which have `ap-southeast` (Singapore) available, at a higher price, and which
  Hetzner documents as colocation rather than its own facility — the inference endpoint gives us no
  location to pick and therefore no wrong choice to make.
- **EU-based processing.** Hetzner's blog records that users "praised the fact that this was
  entirely EU-based." Hetzner is Hetzner Online GmbH, Gunzenhausen, HRB 6089, VAT DE812871812.
- **Governing law is German**, place of jurisdiction Gunzenhausen, with Hetzner retaining the right
  to sue at the customer's place of business.

### 6.2 What I could not verify [U]

- **Whether signup requires a payment method.** I have not created an account. If a card is
  required, an Indian card is likely fine but I have not confirmed it, and it would be a foreign
  currency transaction. Since the service is free, no payment should actually be needed — but the
  sign-up gate is exactly the kind of thing that is undocumented until you hit it.
- **Latency from India.** The docs never state where the inference fleet sits. If it is in Germany,
  expect roughly 150–250 ms RTT from India versus ~50–70 ms to Singapore, and streaming TTFB will
  feel it. This matters for an agent harness doing many sequential tool-calling round-trips: at
  20 sequential turns per task, 200 ms of extra RTT is 4 seconds per task.
- **Whether the `experiments.hetzner.com` login flow handles Indian addresses/timezones cleanly.**

### 6.3 India-specific law

- **DPDP Act 2023** applies to us as a Data Fiduciary for any personal data in traces. Transferring
  personal data outside India requires a lawful basis and contractual safeguards. If the content
  never leaves our redaction chokepoint as personal data, the transfer question does not arise for
  content — only for account-level metadata (timestamps, token counts, our name and address).
  Practical mitigation: use a business entity's name, not founder personal details, in the
  Hetzner account.
- **No FEMA/LRS issue** while the service is free — LRS and TCS govern outward remittance. This
  becomes a question only if Hetzner converts the experiment to paid, at which point the
  classification of a EUR-denominated service invoice from Germany should be checked before the
  first payment.
- **No encryption-at-rest question**, because nothing is stored. This is a genuine advantage of
  Hetzner over the US-based free tiers, where US jurisdiction and their own retention terms apply
  to content you cannot see.

### 6.4 India verdict

**Nothing in the official terms blocks an Indian user, and the EU-only processing is the most
privacy-friendly option on the list.** Two unverified items gate a real decision — the sign-up
payment requirement and latency — and both are answered by spending ten minutes creating an account
and running the §4.3 probe. That single afternoon converts §6.2 from `[U]` to fact, and it is the
cheapest next step available.

---

## 7. The 500M-token plan (one fixed model, 21 days)

1. **Model:** `Qwen/Qwen3.6-35B-A3B-FP8` only — one fixed model, Apache 2.0, 262,144 ctx, from `https://inference.hetzner.com/api/v1`; never switch models, because a switch collapses kv-cache hit rate and agentic coding is input-token-heavy.
2. **Budget:** 500M input tokens over 21 days at an assumed 40K average input per request ≈ **12,500 requests ≈ 0.43 requests/min** sustained.
3. **Against the cap:** the published 10 req/60s and 4M input tok/60s limits allow ~302,400 requests and ~121B input tokens over the same 21 days — so the plan sits at **4.1% of the request cap and 0.41% of the input-token cap**; output at 2K/request is 25M tokens, 0.21% of its cap.
4. **Hard precondition:** do not start until the §4.3 tool-calling probe returns a real `tool_calls` payload; if it does not, this plan is void and the Modal self-hosting row in §1 is the fallback at ~$30/month.
5. **Guardrails:** 429-aware retry with the repo's existing key-rotation path, a circuit breaker that trips on the second consecutive availability failure, and a hard "no production traffic" rule — this endpoint has no SLA and no backups.

Arithmetic note: at these caps the plan is nowhere near rate-limited. If the real 500M figure turns
out to be 10× larger, it still fits without changing provider. The binding constraint is uptime and
tool support, not quota.

---

## 8. Jina privacy-policy review

Reviewed: `https://jina.ai/legal/#privacy-policy` (undated; accessed 2026-09-27), against
`https://www.elastic.co/legal/privacy-statement` and
`https://www.elastic.co/legal/data-processing-agreement`.

**The finding that matters most: the Jina page is not the operative policy for the API.** It states
that Jina AI has been acquired by Elastic and that the older policies "may no longer reflect" current
processing. For actual API processing, the governing documents are Elastic's privacy statement and
DPA. Reading the Jina page alone would give a false picture of current obligations.

| Issue | Assessment |
|---|---|
| Scope of the privacy text | Website-specific rather than API-specific; API processing is governed by Elastic's DPA and privacy statement |
| Training on your data | Terms §10.4 carries a no-training promise for API content. This is the single most important term for us. |
| Operational data | Aggregated operational / diagnostic / usage data is collected and allowed. Redacted prompts that are themselves metadata stay in scope here — this is the exposure to weigh. |
| Cookies | Advertising and analytics cookies may constitute sale or sharing under CCPA/CPRA. Relevant to a web-facing product; not to a server-side token call. |
| Automated use | Terms restrict automated scraping and use as a competing service. Our ingestion path is neither, but the restriction is real and worth not tripping. |
| Terms stability | Terms may change on six weeks' notice. Long-lived automation should re-read this on a schedule. |
| Liability | Capped at 50% of fees paid in the prior 12 months — **effectively zero for a free user.** You have no monetary recourse. |

**Verdict: acceptable for public code and non-sensitive prompts; not for anything under NDA or
containing customer data.** For our substrate, Jina is a search/MCP-shaped tool for public-web
retrieval, not a generation provider — and the zero liability cap for free users is the reason not to
route anything through it that we would be upset to lose.

---

## 9. Open items

- [ ] Run the §4.3 tool-calling probe and record the result here. Everything downstream is gated on it.
- [ ] Confirm whether `experiments.hetzner.com` sign-up requires a payment method, from India.
- [ ] Measure RTT from India to `inference.hetzner.com` and record it in §6.2.
- [ ] If the probe passes: add `"qwen3.6": (0.0, 0.0), "qwen3.8": (0.0, 0.0)` to `_PRICE_PER_MTOK`
      in `backend/app/services/governance.py` — in the same change that wires the provider, per the
      half-gate rule. A proving test ships with it.
- [ ] Re-read the Hetzner docs page monthly. The catalogue already changed once (scaled down at
      T+7 days) and the "we will notify you in advance via email" promise is the only warning we get.
- [ ] Re-verify §2 before anyone quotes those offers again.
