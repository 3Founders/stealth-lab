# Unit economics and tool pricing (2026-10-31)

Desk estimate from the repo's own numbers plus third-party price pages. **Re-check every row marked "assumed" or "secondary" before it goes on the site or into a deck.** Rupee figures use Rs 90 to the dollar (a placeholder; at Rs 97 every dollar cost below rises about 8%).

## 1. Inputs (what the model assumes)

| Input | Value | Source | Confidence |
|---|---|---|---|
| Rupees per dollar | 90 | placeholder (`KELL_INR_PER_USD`) | assumed |
| Razorpay fee | 2% + 18% GST on the fee = 2.36% of sales | general knowledge, not read from the account | assumed |
| GST on sales | 18% on top of prices if registered (a pass-through, not a cost) | tax advice pending | open |
| Neon Launch compute | $0.106 per compute-unit-hour (730 h a month) | neon.com/docs/introduction/plans | documented |
| Neon storage | $0.35 per GB-month; about 0.9 GB used (mostly the 48k-card tool catalog) | docs plus the local measurement | documented / measured on a local copy |
| Railway Pro | $20 a seat, includes $20 of usage; CPU about $20 per vCPU-month, memory about $10 per GB-month | third-party summaries | secondary |
| Supabase | Free plan (sign-in only) | decision | decided |
| Misc (domain, email, monitoring) | $5 to $15 | founder estimate | assumed |
| JEV judge (TypeSafe) | $0.042 per million input tokens, output free | several secondary sources, not TypeSafe's page | secondary |
| Cheap-model prices per million tokens (in / out) | gpt-oss-120b 0.037 / 0.17; gemma-4-31b 0.09 / 0.34; glm-5.3-flash 0.15 / 0.50 (cached input 0.03); minimax-m2-7 0.21 / 0.84; deepseek-v3.2 0.27 / 1.10 | docs/cheap_models_notes.md, provider_connections.md | documented (date unknown) |
| Cost of one delegated task, cheap side | average $0.052; range $0.008 (chat) to $0.16 (a vague edit); includes Sonnet review at API-equivalent price | docs/results_2026-10-30.md, 10 prompts, one small repo | measured, tiny sample |
| Image (FLUX) | $0.014 x (width / 1024) x (height / 1024) | founder, for flux-2-klein-4b; the code default is still FLUX-1-schnell | stated |
| Tool search (embed + rerank) | up to $0.0005 per call, about 80 calls a month per active user | assumed from public embedding prices | assumed |

### What goes into each model per step

| Step | Model | Input tokens | Notes |
|---|---|---|---|
| Triage (every prompt) | JEV | about 2,000 | the prompt plus instructions; assumed, measure from logs |
| Goal / procedure judgment (`find_ways`) | JEV | up to about 10,000 | the state is capped near 32K to 64K tokens |
| Tool search | embedding model + reranker | the query (about 100) plus 40 candidate cards (about 12,000) | only when triage says a tool is needed |
| A delegated task | glm / gemma / minimax / gpt-oss / deepseek | 43,000 to 190,000 per solved task, resent every step | output is 3 to 4 times dearer than input; glm always reasons first; racing runs up to four models |
| Review of the answer | Sonnet | the prompt plus the diff | probably on the user's own login; not confirmed |
| Image | FLUX | the prompt, capped at 2,000 characters | cost depends on size, not prompt length |

## 2. Fixed monthly costs

| Line | Low | High |
|---|---|---|
| Neon (0.25 to 1 compute unit always on, plus about 0.9 GB) | $19.7 | $77.7 |
| Railway Pro | $20 | $45 |
| Supabase (Free) | $0 | $0 |
| Misc | $5 | $15 |
| **Total** | **$44.7 (Rs 4,000)** | **$137.7 (Rs 12,400)** |

## 3. Variable costs per user per month

| Line | Per active user | At 450 active users |
|---|---|---|
| JEV triage (about 440 prompts x 2,000 tokens) | $0.04 | $17 |
| JEV, with a 10,000-token judgment on 20% of prompts | $0.09 | $41 |
| Tool search (about 80 calls x up to $0.0005) | up to $0.04 | up to $18 |
| Razorpay | 2.36% of sales | |
| Model usage and images | covered by the included usage, once images are metered | |

## 4. Unit economics per sale (Rs 90 / $)

Contribution = revenue - Razorpay - included usage consumed - Rs 7 of judging and search per paying user.

| Tier | Price | Included | Razorpay | Usage cost if all spent | Contribution, all spent | Contribution, half spent |
|---|---|---|---|---|---|---|
| Starter | Rs 200 | $1 | Rs 5 | Rs 90 | **Rs 98 (49%)** | Rs 143 (72%) |
| Pro | Rs 1,200 | $6 | Rs 28 | Rs 540 | **Rs 625 (52%)** | Rs 895 (75%) |
| Plus | Rs 2,000 | $12 | Rs 47 | Rs 1,080 | **Rs 866 (43%)** | Rs 1,406 (70%) |
| Pilot, per seat | Rs 649 | $4 | Rs 15 | Rs 360 | **Rs 267 (41%)** | Rs 447 (69%) |

Plus is the thinnest tier because it gives $0.006 of usage per rupee against $0.005 for the others. At Rs 97 / $ Plus falls to about 39%.

## 5. Scenario: 450 registered users, 10% pay (45), mix 40% Starter / 40% Pro / 20% Plus

| Line | Rs per month |
|---|---|
| Revenue (45 x Rs 960 average) | 43,200 |
| Razorpay (2.36%) | -1,020 |
| Included usage consumed: all spent / half spent | -21,060 / -10,530 |
| Judging for all 450 users: low / high | -1,530 / -3,690 |
| **Left before fixed costs** | **17,430 to 30,120** |
| Fixed costs: low / high | -4,023 / -12,393 |
| **Net per month** | **about +5,000 to +26,000** |

Break-even is about **8 to 34 paying customers (2% to 8% of 450)**. Images are assumed to be metered; unmetered they are an open-ended loss (section 6).

## 6. Tool usage: what each kind costs us and how to price it

| Kind of tool | Our marginal cost | Proposed price |
|---|---|---|
| **Discovery and routing** (tool search, ranking, outcome learning) | under Rs 0.05 a call | **Free, in every tier.** This is the product. |
| **Read-only third-party servers through the gateway** (no sign-in) | server time only | **Free within fair use** (60 calls an hour per user already enforced). |
| **kell-run generation tools** (images today; audio or video later) | real: $0.014 per 1024x1024 image | **Cost plus 25%**, charged to the same wallet, priced per unit by size (below). |
| **Paid third-party tools** (Monid-style endpoints billed per call) | the vendor's price | **Vendor price plus 10% to 15%**, shown before the call, reserved against the balance first, refunded on failure. Not built; needs gateway phase 2. |
| **Tools that act for the user** (send, post, pay) | none | Not sold through the gateway yet. |

### Image price list (proposed: cost plus 25%)

| Size | Our cost | Price | In rupees | Starter ($1) | Pro ($6) | Plus ($12) |
|---|---|---|---|---|---|---|
| 512x512 | $0.0035 | $0.0044 | Rs 0.39 | 228 | 1,371 | 2,742 |
| 1024x768 or 768x1024 | $0.0105 | $0.0131 | Rs 1.18 | 76 | 457 | 914 |
| 1280x720 or 720x1280 | $0.0123 | $0.0154 | Rs 1.39 | 65 | 390 | 780 |
| **1024x1024 (default)** | **$0.0140** | **$0.0175** | **Rs 1.58** | **57** | **343** | **686** |

### Principles

1. **One balance.** Tools draw on the same dollar pool as models; the page shows "about N images" so the pool is easy to read. A separate allowance per tool is simpler to market but needs its own counters and is harder to keep honest.
2. **Charge where there is marginal cost, keep discovery free.** Free search brings people to the paid tools.
3. **Price per unit, not per call,** when cost varies (image size). Show the price before running.
4. **Reserve first, settle after, refund on failure,** using the existing ledger (`reserve_call` / `settle_call`). Add `max_cost_usd` to tool calls as `call_model` already has.
5. **Cap the day,** per account and in the database, not in memory: today's 30-a-day counter lives in server memory and the images are not charged to the wallet at all. At 30 images a day a user costs us $12.60 a month with no revenue behind it; 450 users at the cap would be about $5,700.
6. **No resale of a vendor whose terms forbid it;** read each third-party tool's terms before it goes on the allowlist.

## 7. Open items

- Confirm JEV's price on TypeSafe's own page, and whether it can rise.
- Confirm who pays for the Sonnet reviews in managed mode (must be the user's own login).
- Confirm Razorpay's fee on the account, and the GST position.
- Decide the model for images (code default is FLUX-1-schnell; the quoted price is for flux-2-klein-4b) and its DeepInfra id.
- Measure real tokens per prompt for triage and `find_ways`, and cost per task, on a bigger sample than 10.
- Replace the Neon, Railway and misc ranges with the real bills.

## 8. Routing the user's main model through us (`/v1/messages`)

Token volumes come from a research pass (2026-10-31). Anthropic publishes dollars, not tokens; converting its figures with its own sample token mix gives about 95% of input as re-read cached prefix, 0.5% output. Claude Code resends the whole conversation on every request (primary source: code.claude.com/docs/en/costs and /prompt-caching). Cursor and Codex figures are forum or blog claims. Chat-assistant volumes are my own estimates (no public data).

### Tokens per user per month (millions)

| Scenario | Fresh or first-seen input | Cached input | Output (billed, incl. thinking) |
|---|---|---|---|
| Claude Code light (about $30 a month at Anthropic prices) | 0.5 | 60 | 0.4 to 1.2 |
| Claude Code typical (about $200 a month, 20 active days) | 26 | 400 | 2.5 to 7.5 |
| Claude Code heavy ($1,000+; one report at 5,000) | 100 | 3,500 | 15 to 45 |
| Cursor typical Pro (forum) | 8 | 80 | 1.5 |
| Chat assistant typical (about 120 messages) | 1 | 0.5 | 0.1 |
| One delegated attempt: small edit / typical task / large refactor | 0.05 / 0.1 / 0.5 | 0.25 / 1.5 / 8 | 0.008 / 0.025 / 0.1 |

### What it costs us on glm-5.3-flash ($0.15 fresh, $0.03 cached, $0.50 out per million)

The swing factor is the provider's own prefix-cache hit rate (the cache markers are dropped on this path). Switching models mid-session resets the cache.

| Monthly cost | 95% hit | 90% hit | 50% hit | no cache |
|---|---|---|---|---|
| Light | $2 to 3 | $3 | $6 | $9 to 10 |
| **Typical** | **$17 to 19** | **$19 to 22** | **$40 to 42** | **$65 to 68** |
| Heavy | $137 to 152 | $159 to 174 | $332 to 346 | $548 to 562 |

With no caching: gemma-4-31b typical about $39 to 41; gpt-oss-120b about $16 to 17; deepseek-v3.2 about $118 to 123.
Cross-check, delegated attempts on glm: small edit $0.02 to 0.03, typical task $0.07 to 0.10, large refactor $0.37 to 0.47 (the measured average of $0.052 per task sits inside this range).

### Consequences

- A typical full-time Claude Code user costs **$17 to 22 a month at a 90 to 95% cache hit and $40 or more at 50%**. That exceeds Plus's $12 of included usage and approaches its Rs 2,000 (about $22) price. Main-model routing cannot sit inside the included usage; it must be metered per token (cost plus 25%: about $24 to 28 at a good hit rate, $50 or more at a poor one).
- A weaker model usually takes more steps for the same task, so these token counts are probably low by 1.5 to 2 times.
- **Capacity:** Anthropic's per-user rate guidance is 200 to 300 thousand tokens a minute; the three General Compute models share about 200 thousand a minute for the whole key. One heavy routed user can use the whole quota.
- Chat assistants (claude.ai) stay on Claude; only delegated sub-tasks (a few thousand tokens each) reach us, so their cost is pennies.

