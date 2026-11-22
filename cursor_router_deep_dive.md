# Cursor Router ("Auto Mode") — Deep Dive

Research date **2026-10-21**. Method: `websearch` was unavailable this session (every call
returned "cancelled"), so discovery ran through DuckDuckGo HTML and all substantive claims were
then verified by fetching primary pages directly: cursor.com docs, the Router announcement blog,
the changelog, and five Cursor forum threads (three with staff replies). No `exa` calls — the
Exa tools were retired mid-session.

**Verification legend used throughout**

- **[D]** documented first-party, cursor.com docs/help (URL given)
- **[B]** documented first-party, Cursor blog post (URL given)
- **[CL]** documented first-party, Cursor changelog (URL given)
- **[F-staff]** Cursor staff statement on the forum (named thread)
- **[F-user]** user report on the forum (unconfirmed by Cursor)
- **[S]** secondary source or inference from documented facts

**Corrections to the brief's assumptions, up front**

| Brief assumed | Reality |
|---|---|
| "auto-smart" might exist | It does — it is the **SDK model id for Router**, documented [D] |
| `optimize_for` cost/balanced/intelligence | Real, exact wire values `cost`, `balanced`, `intelligence` [D]; UI labels are Cost/Balance/Intelligence [D] |
| Router on all plans | **Teams and Enterprise only**; individual plans get Cost-mode-only Auto [D] |
| Router internals reverse-engineered | **No public reverse-engineering of the classifier was found.** The client is closed and Cursor states they "don't share the step by step routing algorithm" [F-staff]. The Request-ID support channel is the only external check [F-staff] |
| Cost vs Balance vs Intelligence SWE-bench numbers | None published; Cursor **explicitly rejects offline evals for router measurement** and reports online A/B + satisfaction/keep-rate instead [B] |

---

## 1. System architecture and execution flow

### 1.1 What Cursor Router is

> "Cursor Router is the model routing system behind **Auto**. Not every request needs
> frontier-level intelligence, so the router sends each request to the model that fits the task."
> — [D] https://cursor.com/docs/cursor-router

- A per-request **classifier**, run server-side before the model call. "Cursor Router runs a
  classifier on each agent request and routes it based on task type and complexity" [D].
- Launched **Jul 22, 2026** for Teams (on by default) and Enterprise (off by default, admin must
  enable) [CL] https://cursor.com/changelog/router · [F-staff] forum 166460.
- Pre-launch, "Auto" already existed (legacy/Cost logic); Router replaced Auto's model selection
  on team plans as of the launch date [D][F-staff].
- Surfaces: desktop, web, iOS, CLI, SDK, Cloud Agents API [CL][D SDK].

### 1.2 Execution flow (verified edges only)

```
 IDE (desktop/web/iOS)   CLI (cursor-agent / ACP)   SDK (@cursor/sdk)   Cloud Agents API
        │                       │                        │                    │
        │  model = "auto"       │  --model auto          │  model id          │  Auto / auto-smart
        │  (or auto-smart)      │  (ACP: acp.json)       │  "auto-smart"      │
        └───────────┬───────────┴───────────┬────────────┴─────────┬──────────┘
                    │  hooks run client-side first                    │
                    │  beforeSubmitPrompt → can block submission [D]  │
                    ▼                                                 ▼
             ┌────────────────────── Cursor backend: agent run ──────────────────────┐
             │                                                                      │
             │   ┌──────────────────── Cursor Router ────────────────────┐          │
             │   │ per-request classifier                                │          │
             │   │ inputs: query · context · task complexity · domain    │          │
             │   │         + per-model behavior knowledge + capacity     │          │
             │   │ modes:  cost │ balanced │ intelligence (admin-restricted)          │
             │   │ pool:   GPT-5.5 · Claude Opus 5 · Grok 4.6 · Claude Fable 5       │
             │   │         (+ plan/capacity-dependent; Composer 2.5 observed)        │
             │   │ constraints: admin allow/block; Grok 4.6 REQUIRED                │
             │   └──────────────────────────┬─────────────────────────────┘          │
             │                              │ route; identity hidden by default      │
             ▼                              ▼                                        │
   harness customization             provider endpoint                            │
   (per-model prompts, tools)        (Anthropic / OpenAI / Google /               │
   (harness auto-swaps on            Cursor-hosted Grok/Composer)                 │
    mid-chat switch) [B]                                                          │
             │                              │                                        │
             ▼                              ▼                                        │
        Run result: requestId · resolved model (SDK exposes it) · TokenUsage            │
        (input/output/cacheRead/cacheWrite tokens) [D SDK]                              │
                                                                                    │
        feedback: keep rate + LLM-classified user satisfaction → training/pool [B] ──┘
```

Every edge above is traceable to a source in §8; the only inferred element is the placement of
the classifier inside the backend request path, which follows from "classifies each request
**before a model runs**" [B] and from Request-ID-level routing checks by support [F-staff].

### 1.3 Fallback and failure-path mechanics (documented)

| Trigger | Router behavior | Source |
|---|---|---|
| Enterprise model blocked | "the router routes to an allowed model instead" | [D] cursor-router |
| Grok 4.6 blocked | **Router disabled** — it "needs a powerful yet cost-efficient model to use when it isn't calling other frontier models" | [D] cursor-router |
| Both GPT-5.5 and Claude Opus 5 blocked | "Blocking one reduces routing quality. Blocking both disables the router." | [D] help/available-models |
| Regionally unavailable models | "Use Auto: Auto is available in all regions and selects an available model for each request" — Auto is itself the region fallback | [D] help/available-models |
| Specific model missing from catalog | `{ id: "auto" }` = "Server-selected Auto fallback" | [D] SDK typescript |
| Model id retired | Server-side reroute at auth time (Composer 2 → Composer 2.5) | [D] SDK typescript |
| Security guardrail tripped | "Requests that trip a security guardrail are automatically routed to Claude Opus" (documented on Claude Fable 5) | [D] models-and-pricing |
| Provider outage (sudden) | "Auto chooses a model for a specific request and **retries that same model a few times before failing**. It does shift traffic when a provider is unhealthy, but with a sudden outage like this, you can still get routed to the affected model in the first few minutes. We're improving failover." | [F-staff] forum 170463 |
| Usage limit reached (Router launch bug) | "Router's Auto isn't yet recognized by the continued-access path that keeps free models available at the limit, so it gets incorrectly blocked instead of falling back... the request is being rejected" | [F-staff] forum 166460 |
| Weekly pool exhausted | "Requests are never downgraded in quality or speed"; user can top up, wait, or upgrade | [D] models-and-pricing |

The outage behavior is the load-bearing finding for anyone designing on top of Auto: **routing is
per-request and sticky-per-retry — there is no documented cross-provider failover within a single
failed request**, only slower fleet-level traffic shifting and a stated intent to improve.

---

## 2. Feature vector — what the classifier actually sees

The classifier's inputs are documented at the **signal-class level**, not the feature level. No
token counts, embedding dimensions, thresholds, or weights are public.

### 2.1 Documented input signals

| Signal | Type | Statement | Source |
|---|---|---|---|
| **Query** | prompt text of the request | "analyzes each request on query, context, task complexity, and domain" | [B] blog/router |
| **Context** | surrounding conversation/workspace state | same sentence | [B] blog/router |
| **Task complexity** | derived | same sentence; docs say "routes it based on task type and complexity" | [B] + [D] cursor-router |
| **Domain** | derived | same sentence | [B] blog/router |
| **Per-model behavior knowledge** | fleet-level learned priors | "combined with what we know about each model's behavior. We learn what each model is best at" — e.g. "UI updates go to the model with the best taste, and more complex, long-horizon problems go to frontier reasoning models" | [B] blog/router |
| **Current capacity** | ops state | "It uses internal benchmarks, quality scores, and current capacity, so the pool is not fixed and can change over time" | [F-staff] forum 167010 |
| **Cache-miss cost of the route** | cost model inside training/eval | "Cursor Router is cache-aware in both how it is trained and evaluated... trained on a dataset where routing results in cache misses" | [B] blog/router |

### 2.2 The feedback loop (how rejection/edits enter)

This is a **batch/online-training loop and fleet-level scoring**, not a documented per-user
real-time scorer:

| Mechanism | How it works | Source |
|---|---|---|
| **User satisfaction (AFC)** as RL/training reward | "optimizing for user satisfaction (AFC) as a reward" over "600k+ live requests"; measured in production by "a language model [that] read[s] the user's responses to the agent's initial output... A user moving on to the next feature is a strong signal the agent did its job, while a user pasting a stack trace is a reliable signal that it didn't" (accept ≈ positive; correcting the agent ≈ strong negative). *AFC's expansion is not stated in the post.* | [B] blog/router |
| **Keep rate** | "what fraction of those [agent-proposed changes] remain in the user's codebase after fixed intervals of time" — captures manual rework and iterate-to-fix | [B] blog/router + [B] harness |
| **Operational metrics** | "latency, token efficiency, tool call count, and cache hit rate... directionally useful" | [B] harness |
| **Error taxonomy + anomaly baselines** | `InvalidArguments`, `UnexpectedEnvironment`, `ProviderError`, `UserAborted`, `Timeout`; baselines computed **per-tool and per-model** because "different models may mess up tool calls at different rates" | [B] harness |
| **Pool composition updates** | models added as validated ("A model can be available to select directly before the router routes to it"); required/recommended pool members change (Grok 4.5 at launch [CL] → Grok 4.6 in current docs [D]) | [D][CL] |
| **Real-time per-session scoring** | **Not documented anywhere.** Nothing public says a given user's rejection history or edit pattern feeds the live scoring of their next request. The documented per-request inputs are only query/context/complexity/domain. | — |

### 2.3 Two smaller documented routing surfaces

- **Built-in subagents** (Explore, Bash, Browser): "select their model automatically based on the
  subtask" — a second, task-level router. Custom subagents default to `inherit` (parent's model)
  [D] help/available-models.
- **Auto-review classifier**: `Gemini 3.5 Flash Lite` — "Used only for the Auto-review classifier.
  It is not selectable as a chat or agent model" — documented evidence that Cursor runs a separate
  small-model classifier product-side [D] models-and-pricing.

---

## 3. Prompt-cache impact analysis

This is the section where Cursor's own writing is unusually candid.

### 3.1 Switching models invalidates the KV cache

> "caches are provider- and model-specific, so switching means a cache miss and a slower, more
> expensive first turn. We have experimented with mitigating this by summarizing the conversation
> at switch time, which provides the model with a clean summary that reduces the cache penalty.
> But if the user is deep into a complex task, the summary can lose important details. We generally
> recommend staying with one model for the duration of a conversation unless you have a reason to
> switch." — [B] harness, Apr 30 2026

Mechanics, assembled from documented pieces:

1. **Cache scope.** KV caches are per (provider, model). A route switch — including an Auto
   route that differs from the previous turn — restarts the prefix from cold at the new endpoint
   [B] harness.
2. **Harness adds cost on top of cold cache.** On mid-chat switch the harness swaps in the target
   model's prompts/tools and injects takeover instructions [B] harness — so the new first turn is
   both uncached *and* harness-modified.
3. **Cost accounting makes misses expensive on Teams/Enterprise.** The **Cursor Token Rate
   ($0.25/M) applies to input, output, *and cached* tokens** [D] token-rate — and third-party
   cache writes bill at 1.25× input on Anthropic models [D] models-and-pricing. A miss therefore
   pays full input + the $0.25/M rate; a hit pays ~0.1×–0.05× input.
4. **The router is trained to internalize the miss.** "evaluated in production where our reported
   cost savings include the cost of cache misses in routing decisions" — the 60%-savings headline
   is net of switching costs [B] blog/router.

### 3.2 Documented failure mode: Auto silently losing cache

User-reported, staff-confirmed root cause, fixed:

> "Version 2.6.12 routed requests differently, which made Auto mode prefer **models that do not
> support Anthropic-style prompt caching**. Version 2.6.18 and newer fixed this." — [F-staff]
> forum 154278

- Symptom: **0 cache read / 0 cache write** in Auto while manual selection of the same-class model
  cached normally; a user burned a $20 team limit in hours on prompts that normally cost
  $0.10–0.20 [F-user] forum 154278.
- Detection: SDK `TokenUsage.cacheReadTokens == 0` across turns [D SDK]; dashboard cache columns.
- Debug channel: Request ID (chat top-right → Copy Request ID) lets support "check the model
  routing for your request" — the only documented external way to see a route decision [F-staff]
  forums 154278, 167010. Privacy Mode may make the Request ID unavailable [F-staff].

### 3.3 Cursor's own mitigations (documented)

| Mitigation | Status | Source |
|---|---|---|
| Summarize conversation at switch time to shrink the re-sent prefix | Experimented; rejected as default — loses detail on complex tasks; official advice is "stay with one model per conversation" | [B] harness |
| Delegate instead of switch: subagent with a particular model (fresh context window) | Shipped | [B] harness, [D] subagents |
| Cache-aware router training/eval | Shipped (Router launch) | [B] blog/router |
| Per-model harness provisioning (patch-format vs string-replacement tools, per-provider prompts) | Shipped — reduces the *quality* penalty of switching, not the cache penalty | [B] harness |

### 3.4 Workaround that follows from all of the above

For reproducible runs (evals, A/B baselines): **pin a fixed model id** — Cursor's SDK docs say it
directly: "The underlying model can change between requests. **Prefer a fixed model id when you
need reproducible comparisons**" [D] SDK typescript.

---

## 4. Governance and API schema

### 4.1 The documented wire contract

Everything below is [D] (cursor-router doc + SDK typescript doc). Labels vs wire values differ:

| Product label | SDK wire value |
|---|---|
| Cost | `cost` |
| Balance | `balanced` |
| Intelligence | `intelligence` |

```jsonc
// Documented selection shape (TS: ModelSelection; Python: ModelSelection)
{
  "model": {
    "id": "auto-smart",                       // Cursor Router
    "params": [
      { "id": "optimize_for", "value": "balanced" }  // cost | balanced | intelligence
    ]
  }
}
```

```jsonc
// Catalog discovery — Cursor.models.list() (shape as documented in usage)
{
  "id": "auto-smart",
  "parameters": [
    { "id": "optimize_for",
      "values": [ { "value": "cost" }, { "value": "balanced" }, { "value": "intelligence" } ] }
  ],
  "presets": [ /* "preset variants" per docs; not itemized */ ]
}
```

**Contract rules (all [D], SDK typescript):**

- `optimize_for` must be passed explicitly. Omitting it or sending legacy `default` is "Not a
  supported Router contract."
- `Cursor.models.list()` is the source of truth: `auto-smart` appears only if Router is enabled
  for the API key's team; enterprise admins must enable Router before it appears in the catalog.
- Mode values are account-dependent — a team can have modes disabled, so validate against
  `optimize_for.values` before sending.
- Per-run override: `agent.send(msg, { model })` changes the mode for that run and is then
  **sticky** for subsequent sends.
- `run.model` / `result.model` = "resolved ModelSelection used for this run," immutable once the
  run starts — i.e. the SDK *does* expose what served, unlike the IDE default UI.
- `{ id: "auto" }` remains valid as the server-selected fallback when a specific model is missing
  from the catalog; `default` is legacy.
- The SDK/Cloud Agents API is **not** a raw inference API: "Cursor does not currently document a
  raw Router endpoint for arbitrary model calls."
- Composer 2 / composer-2-fast ids are silently rerouted to Composer 2.5 at auth time.

CLI/ACP surface [F-staff] forum 166460: `cursor-agent --list-models` shows `auto`/`default`
(desync with the ACP session's `auto-smart` "which we're improving"); JetBrains ACP pins a model
via `~/.jetbrains/acp.json` → `"args": ["--model", "composer-2.5", "acp"]`.

### 4.2 Enterprise admin policy surface (all [D] cursor-router unless noted)

| Control | Values / behavior |
|---|---|
| **Enable Cursor Router** | on/off per team; Enterprise must enable manually (off by default); Teams on by default [CL]; configurable per organization group |
| **Routing preferences** | which optimization modes members may select — "You can disable up to 2 modes" |
| **Default mode** | admins can set the default mode [CL] |
| **Underlying model visibility** | Display vs Hidden; "Hidden is the default and recommended, so results are judged on their own merit" — applies to Balance/Intelligence |
| **Impose Auto** | **Soft** = default each new chat to Auto (members can switch); **Hard** = lock the model picker to Auto; both off by default |
| **Model allow/block** | allow/block list shapes the routing pool; blocked models are skipped, router falls back to an allowlisted alternative; blocking required models disables the router (see §1.3) |
| **Token Rate / spend** | $0.25/M Cursor Token Rate on third-party routes (input+output+cached; incl. BYOK); monthly additional-usage limit; prepaid credit + Auto Top-up; team-shared prepaid balance [D] token-rate, models-and-pricing |
| **Enterprise Cost-mode billing** | Since **Sep 7, 2026**, Enterprise Auto *Cost* also bills at the routed model's rate (was flat-rate) [F-staff] forum 171127 |
| **Privacy Mode** | team-wide enforcement available; constrains debugging (Request ID availability) [F-user]/[F-staff] forum 154278 |

### 4.3 What a true "auto-smart JSON schema" would need — and what isn't public

No public OpenAPI/JSON-Schema exists for `auto-smart`. Not documented anywhere: per-mode model
distributions, classifier weights/scores, per-request routing rationale, SLA/latency budgets for
the classifier hop, retry counts, or failover target ordering. The `presets` field's contents are
also not itemized publicly.

---

## 5. Benchmarks and token economics

### 5.1 Cursor's published router numbers (all first-party [B], Jul 22 2026)

| Claim | Number | Method |
|---|---|---|
| Frontier quality at savings | **~60% lower cost** | online A/B, millions of requests |
| Auto Intelligence vs Fable 5 | satisfaction ≈ Fable, **~60% lower cost for teams** | online A/B |
| Auto Intelligence vs Opus 4.8 | **+15% satisfaction** at nearly the same cost | online A/B |
| Auto Balance vs Opus 4.8 | **above Opus 4.8 satisfaction at ~36% lower cost** | online A/B |
| Auto Balance vs GPT-5.6 Sol | "comparable satisfaction at a lower spend rate" | online A/B |
| Early-access enterprises | **30–50% savings** vs pricing all traffic at Opus 4.8, "no decrease in quality", three high-volume accounts, thousands of users | early-access comparison |
| **Cost per commit** | **$6.76** Intelligence · **$4.63** Balance · Opus 4.8 **$7.34** · Fable 5 **$12.69** · GPT-5.6 Sol ≈ Intelligence cost with lower satisfaction | production traffic |
| Adoption baseline | ~60% of developers pick a single model as daily driver (the motivation) | [B] |

**Method note:** Cursor deliberately declines offline-eval/SWE-bench-style comparisons for the
router — "offline evals... are limited by their small size, their distance from real-world usage,
and the difficulty of reducing success to a rubric. Offline evals also omit the extra cache-miss
cost that comes from switching models." They keep [CursorBench](https://cursor.com/blog/cursorbench)
as their offline suite for harness/model work [B]. So per-mode SWE-bench numbers **do not exist
publicly**, by design.

**Quality metrics defined [B]:** user satisfaction (LLM-classified from user responses; move-on =
positive, correction = negative) and keep rate (fraction of agent code surviving in the codebase).
These are the same two metrics used to evaluate "every model launch and harness improvement in the
past nine months."

### 5.2 Token economics (per 1M tokens, [D] models-and-pricing; verified table)

**Router pool models:**

| Model | Input | Cache write | Cache read | Output | Notes |
|---|---|---|---|---|---|
| Grok 4.6 | $2 | — | $0.5 | $6 | **required pool member**; jointly trained w/ SpaceXAI |
| GPT-5.5 | $5 | — | $0.5 | $30 | long context to 1M at 2× input |
| Claude Opus 5 | $5 | $6.25 | $0.5 | $25 | 1M extended context, no surcharge |
| Claude Fable 5 | $10 | $12.5 | $1 | $50 | guardrail trips → auto-route to Opus |
| Composer 2.5 | $0.5 | — | $0.2 | $2.5 | (Fast: $3 / $0.5 / $15) |

**The routing bill has three parts:**

1. **Routed model's list price** for every Auto mode (all modes bill this way since Sep 7 for
   Enterprise Cost too) [D][F-staff].
2. **Cursor Token Rate**: +$0.25/M on third-party routes — input, output, **and cached tokens**;
   also on BYOK; exempts first-party (Grok/Composer) and Cost mode and individual plans [D] token-rate.
3. **Plan structure**: weekly pool for Cursor models (shared with Grok apps); third-party models
   only from Premium+ ($30) up, paid from prepaid credit; legacy plans (Pro/Pro+/Ultra/Teams Std/Prem)
   keep two monthly pools + on-demand [D] models-and-pricing.

**Plan gate on the router itself:** Free/Plus — no Router (no Auto beyond Cost on individual
plans). Router = Teams/Enterprise only [D].

### 5.3 Model-mix distribution statistics

Cursor publishes none per mode. What exists: the pool list (four models, dynamic) [D]; a
staff statement that mix "depends on your plan and current capacity" [F-staff] 167010; and user
observations of strong Composer 2.5 skew on legacy plans ("Auto now appears to route virtually
100% of my requests to Composer 2.5") [F-user] 167010 — unconfirmed. "Cursor routes hundreds of
millions of coding requests each week across every model and provider" [B] is the only scale figure.

---

## 6. Known failure modes and workarounds

| # | Failure mode | Evidence | Workaround |
|---|---|---|---|
| 1 | **Cache silently dropped in Auto** → 0 cache read/write, context rebilled as fresh input; usage spikes (e.g. $20 team limit in hours) | [F-user→confirmed root cause] forum 154278: v2.6.12 routed to models lacking Anthropic-style caching; fixed 2.6.18 | Update ≥2.6.18; or pin a manual model; detect via SDK `cacheReadTokens`; give support a Request ID to inspect the route |
| 2 | **Sudden provider outage: no per-request failover** — every retry hits the same routed model, then the request fails | [F-staff] forum 170463 (Grok provider outage, Sep 3 2026): "retries that same model a few times before failing... We're improving failover" | Manually pick a non-affected model until status.cursor.com shows resolved |
| 3 | **"Rate limited by model provider" inside Auto** — misleading UX: Auto users are told to "switch to the 'auto' model" | [F-user] forums 135645, 137332 | Distinguish provider-limit vs plan-limit; switch model or retry; check status page |
| 4 | **Router blocks requests at team usage limit** (launch-window bug): continued-access path didn't recognize Router's Auto, so it rejected instead of falling back to free models | [F-staff] forum 166460 | Pin `composer-2.5` (stays available at the limit); admin raise limit; fixed in rollout |
| 5 | **Composer-dominance drift** — Auto stops escalating complex tasks to frontier models (user saw ~100% Composer 2.5) | [F-user] forum 167010; staff declined to confirm/deny; requested Request IDs | Pin the model you need for hard tasks; submit Request IDs for investigation |
| 6 | **Corporate compliance**: user-level allowlist unsupported; a company-approved model list that excludes the router's pool makes Auto unusable | [F-user + staff response] forum 167010 ("a user-selectable allowlist for Auto isn't supported yet") | Admin allow/block config; or manual model pick; related FRs: 167190, 166260 |
| 7 | **Blocking required models disables the router** entirely (Grok 4.6 required; blocking both GPT-5.5 and Opus 5 disables) | [D] | Keep required/recommended pool models enabled; accept the quality reduction otherwise |
| 8 | **`auto-smart` vs `auto`/`default` desync** between `cursor-agent --list-models` and ACP sessions | [F-staff] forum 166460 | Discover via `Cursor.models.list()` in SDK contexts; pin concrete ids in ACP |
| 9 | **Non-reproducible routes** — underlying model can change between requests in one conversation | [D] SDK typescript (explicit reproducibility warning) | Fixed model id for any measurement or baseline work |
| 10 | **Debuggability** — routed identity hidden by default; Privacy Mode can remove Request IDs | [D] cursor-router, [F-staff] 154278 | Admin can set visibility to Displayed; rely on SDK `result.model` where available |

**Structural risk worth naming:** the router's cheapest-path model is *Cursor's own* model
(Grok 4.6, "required"). Cost-optimal routing therefore structurally favors the operator's
first-party models — a design that is rational for Cursor and a lock-in consideration for buyers.

---

## 7. What is NOT publicly documented (honest gaps)

- Exact classifier architecture, weights, thresholds, per-signal contributions, embedding
  extraction (the brief's "AST diff size", "error log attachments", "prompt embeddings",
  "session friction history" — none appear in any first-party or secondary source found).
- Per-user/real-time personalization of routing.
- Retry counts, timeout budgets, failover topology, cache TTL handling on routed switches.
- Per-mode model mix and its evolution (only anecdotal [F-user] evidence).
- Raw Router endpoint for arbitrary (non-agent) model calls — explicitly not offered [D].
- No independent teardown of the Router classifier was found; the deepest public artifacts are
  the SDK catalog contract and support's Request-ID routing checks. (The Substack piece "Inside
  Cursor's model switching infrastructure" was located but not fetched this session — unexamined.)

---

## 8. Sources

**First-party (fetched 2026-10-21):**

- Docs — Cursor Router: https://cursor.com/docs/cursor-router
- Docs — Models & pricing: https://cursor.com/docs/models-and-pricing
- Help — Available models (router pool, FAQ): https://cursor.com/help/models-and-usage/available-models
- Help — Cursor Token Rate: https://cursor.com/help/models-and-usage/token-rate
- Blog — Introducing Cursor Router (Jul 22, 2026): https://cursor.com/blog/router
- Changelog — Cursor Router (Jul 22, 2026): https://cursor.com/changelog/router
- Blog — Continually improving our agent harness (Apr 30, 2026): https://cursor.com/blog/continually-improving-agent-harness
- Docs — TypeScript SDK (Router contract, TokenUsage, reproducibility note): https://cursor.com/docs/sdk/typescript

**Forum (staff statements and user reports, fetched 2026-10-21):**

- Cache-loss bug + 2.6.18 fix: https://forum.cursor.com/t/auto-mode-not-using-prompt-caching-0-cache-read-write-sudden-usage-spike/154278 (dup: .../154654)
- Pool/transparency: https://forum.cursor.com/t/which-models-are-behind-cursor-auto-mode/167010
- Enterprise Cost billing change (Sep 7): https://forum.cursor.com/t/cursor-auto-model-transparency/171127
- `auto-smart` id + usage-limit fallback bug + ACP pin: https://forum.cursor.com/t/php-storm-cursor-auto-model-set-as-auto-smart/166460
- Provider-outage retry semantics: https://forum.cursor.com/t/model-router-down-rate-limited-by-model-provider/170463
- Rate-limit-inside-Auto UX: https://forum.cursor.com/t/cursor-auto-mode-error/135645, .../rate-limit-in-auto-mode/137332

**Secondary (located, consistent with first-party; not load-bearing):**

- vibecodingacademy.ai/blog/cursor-auto-mode-router-explained (four-signals reading, matches [B])
- explainx.ai "Cursor Router ... 60% lower cost" (July 2026 recap)
- dev.to KV-state reuse essay (susheem-k); cldnavi prompt-caching guide (Teknium quote)

---

## 9. Implications for StealthLab (short)

1. **The production proof of routing exists — with our exact gap.** Cursor trains on 600k+
   satisfaction-labeled requests and A/B-tests across millions, yet publishes **no verifier
   false-accept rate, no per-route evidence artifact, and hides the routed model by default.**
   Their quality metrics (satisfaction, keep rate) are outcome proxies, not verification. The
   evidence layer (WHAT_STEALTHLAB_IS.md) sits exactly in the hole their default hides.
2. **Reproducibility is on our side.** Cursor's own SDK docs tell teams to pin a fixed model for
   "reproducible comparisons." Any A/B routed-vs-fixed claim they'd accept must be run fixed —
   which is arm A of the 205-task experiment. Our measurement discipline transfers directly.
3. **Their cache-aware routing is our §7/§2 already validated.** "Offline evals omit the
   cache-miss cost of switching" [B] is Cursor admitting the switching-cost confound our pilot
   plan gates on. Quote it in positioning when the routing question comes up (see
   FundingGrants/POSITIONING_ROUTING.md — Cursor Auto is a per-request router, a different
   animal from the item-level routers we ruled out).
4. **The `auto` id detail matters for our dispatch hook.** docs/cursor_dispatch.md already
   treats `model: "auto"` as "not a model" — correct per [D SDK]: `auto` is the server-selected
   fallback, and `auto-smart` is the Router. If StealthLab ever plans against Router requests,
   the plan target is unknowable (hidden identity) unless the team admin sets visibility to
   Displayed or the SDK run result is read — reinforcing that dispatch against Auto is
   structurally unverifiable from the client.
5. **Delegation shape convergence.** Cursor's answer to mid-chat switching is "delegate to a
   subagent on a fresh context" [B harness] — the same fresh-window delegation our dispatch
   hook performs. The difference remains the gate: theirs is a router guess, ours is a verified
   artifact.
