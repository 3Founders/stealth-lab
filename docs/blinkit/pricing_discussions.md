# Pricing a Way: the decisions, the research, and what is actually built

**The record of how kell's pricing was decided, what the 2026 literature says, and where the code stands against it.
2026-11-23.** This is the consolidated document. It supersedes the pricing sections of
`docs/pricing_and_sandbox_per_way.md` §5.0 and `docs/pricing_page.md`, and it extends `docs/pricingestimation.md`
(which remains the model spec for the pre-run estimate).

Read this one first. The companion docs in this folder:

| File | What it answers |
|---|---|
| `pricing_discussions.md` (this) | how we decided to price, what the literature says, what is built |
| `execution.md` | what actually runs a step: model call, sandbox, filesystem, limits |
| `measurement.md` | what a run records, and why attribution is the foundation |
| `caps.md` | why output caps exist, why they were arbitrary, how they become measured |
| `open_questions.md` | the decisions still unmade, each with what it blocks |

---

## 0. The one-paragraph version

A Way must show a price before anyone buys it, and you cannot know a Way's cost before it has run. kell resolved this
with a **range** before the first run (measured cost varies up to 30×, so a single number is a lie) and a **measured
figure** after it. The research then forced two corrections that are not yet built: the first run gives you a
**cost**, not a **cap distribution** — so "price from the first run" works for the floor and not for the cap; and
pricing in tokens anchors kell to a unit whose cost keeps falling, which a16z argues against. The floor comes from
measurement; the price should be a decision above it.

---

## 1. The problem, stated exactly

```
You cannot price a Way before you know what it costs.
You cannot know what it costs before it runs.
A Way must show a price before anyone buys.
```

Every pricing document in this repo is an attempt at that loop. Two facts make it hard, and neither is noise:

- **Variance.** *"When a large language model (LLM) agent executes the same task, token consumption can vary by over
  an order of magnitude across runs"* — TokenCast, arXiv 2609.35760. Up to **30×** for the same task.
- **Compounding.** Every step re-reads everything earlier, so cost grows super-linearly with Way length. A per-step
  sum **underestimates**, and the error grows with the square of the length.

So a point estimate is wrong by construction. Everything below follows from those two sentences.

---

## 2. The decisions we made, and why

| # | Decision | Why | Where it lives |
|---|---|---|---|
| D1 | The pre-run answer is a **range**, never a point | runs vary up to 30×; a page saying ₹42 that charges ₹310 has lied | `cost_estimate.py` |
| D2 | The estimate includes the **re-read term** | step *i* pays for every earlier output, not just its own prompt | `cost_estimate.py` |
| D3 | Price = `cost × (1 + p_retry × retry_fraction) × (1 + margin)`, margin clamped **[0.20, 0.30]** | a 20% margin on a cost that omits retries is a 20% margin on an underestimate | `cost_estimate.py` |
| D4 | Locator cost is **included** (tool/url/file/script) | 21 of 30 stored steps carry a locator; token maths cannot see it | `cost_estimate.py` |
| D5 | `NULL` price = **free**, never `0` | `0` is a deliberate free listing; absent is "not priced" | `db/216`, `procedure_billing.py` |
| D6 | A Way with **no measured run is unpriced** — publishable, not sellable | a price with no measurement is a guess with a currency symbol | **designed, not built** |
| D7 | Per-step **output caps** bound the estimate | the upper bound of the range *is* the cap | `step_budget.py` |
| D8 | Caching is assumed **cold** | a repeat run is far cheaper; pricing it off a warm cache overcharges | documented |
| D9 | Sub-Ways are licensed **transitively**, never charged twice | composition must not double-bill | documented, not implemented |
| D10 | The margin is **stated on the page** | a price whose components are hidden cannot be argued with | `WayChat.tsx` |

### 2.1 The price path, as it exists

```
POST /v1/procedures/{id}/purchase
  -> procedure_billing.create_purchase
     resolves the exact VERSION row (never "whatever is current")
     price NULL or 0 -> free, no purchase
     else Razorpay payment_link (fixed amount, one-time)
  -> payment_link.paid webhook  (signature verified)
     activate_by_link: idempotent on payment id, AMOUNT MUST MATCH
  -> way_runs.start: check_entitlement -> one paid, unconsumed purchase required
  -> run finishes: consume_run (the purchase is spent)
```

This half is **built and verified**: 1 licence sold, the gate refuses a paid Way without one, and a ₹1 payment
cannot activate a ₹500 Way.

---

## 3. What the 2026 literature says

Searched via Exa on 2026-11-23. Four sources changed or confirmed our thinking.

### 3.1 TokenCast — variance and composition (confirmed)

arXiv 2609.35760. Per-run variance up to 30×; a **composable per-segment cost representation** composed into a
cumulative estimate (14.5% MAE reduction). Its composition rule is exactly a Way's shape: a segment is
`(n calls, net input growth g, residual b)`, and later segments pay to re-read earlier ones. This is D2, and it is
the reason a naive per-step sum is wrong.

### 3.2 QuantAI — **the cap is a p99 of measured output** (new, and it corrects us)

> *"For each task, measure completion length from logs, and set the output cap at that task's 99th percentile."*

| Observed | Cap | Truncation rate |
|---|---|---|
| p50 | p50 | unacceptable |
| p90 | p90 | ~1 in 10 |
| **p99** | **p99** | **~1 in 100** |
| p99 × 1.5 | headroom | well under 1 in 100 |
| model maximum | never a default | near zero truncation, expensive |

Two cautions that decide what we can honestly claim:

- *"Exclude requests that were already truncated at the old cap, because they understate true length."* Our
  truncation guard already refuses such bodies (`artifacts.looks_truncated`), so we would have **no data**, not bad
  data.
- *"Require a minimum sample size before trusting a percentile. A task with forty observations has a p99 that is
  really just the longest request you have seen."*

**The correction to our premise:** the **first run gives you a cost, not a cap distribution**. One run is one
sample, and the p99 of one sample *is* that sample. So "price from the first run" is valid for **the floor** and
invalid for **the cap**. See §5 and `caps.md`.

### 3.3 UsageBox — attribution is the load-bearing piece (confirms what we lack)

> *"capturing, per customer: input and output tokens per call at the correct per-model rate, cache reads versus cache
> writes, retries and failed calls (you paid for them — decide explicitly whether the customer does), and every
> sub-agent or tool call an agent run fans out into."*

Three rebilling models, and what each demands:

| Model | Customer pays | Who eats variance | What the meter must prove |
|---|---|---|---|
| Pass-through 1.0× | exactly cost | nobody | perfect attribution |
| **Markup (cost-plus)** | cost × 1.x | customer pays variance + margin | attribution **plus realized-margin telemetry** |
| Flat | fixed | the vendor | cohort economics + a kill-switch |

> *"A stated 20% covers retries, failed calls, orchestration overhead, and the metering infrastructure itself."*
> *"A meter that reports realized margin per customer per model is how a 20% markup stays 20%."*

**Two consequences for us:**

1. Our margin is **stated, never realized**. Nothing checks whether 20% actually held. D10 puts it on the page; that
   is display, not telemetry.
2. Our retry term and our margin may **double-count retries**, since the industry figure that people call "20%"
   usually includes them. Our formula is defensible only if the margin is read as covering orchestration and
   metering — which must be said explicitly, not assumed.

### 3.4 a16z — **don't price in tokens** (the frame challenge)

> *"When an application prices that work in tokens, it imports the model provider's cost structure into the
> relationship with the customer and anchors the product's value to a unit whose cost keeps falling."*
> *"Companies should price at the highest layer of value that they can reliably measure, attribute, and defend."*

Their ladder: sell model access → **price tokens**; turn models into useful work → **price the recognizable work
unit**; deliver an attributable result → **price the outcome**.

Their evidence, a survey of 50 technical AI buyers: **27 preferred credits tied to recognizable work, 14 preferred
tokens.**

**Why this matters to kell specifically, and is not abstract:** a Way already *is* a recognizable work unit — "a
NeurIPS-format report", not "3,412 tokens". Our page should charge for the report. Cost measurement then does two
jobs only: it sets a **floor** (never sell below cost) and it produces **realized margin** (is the price we chose
actually holding?). It should not be the number printed.

Honest scope limit: a16z is a venture firm's strategy piece, not a measurement. It is an argument about framing, and
it is the strongest argument against pure cost-plus in what we found.

---

## 4. What is built, exactly

### 4.1 Estimation — `backend/app/submissions/cost_estimate.py`

```python
for each step i:
    in_i    = tokens(prompt_i) + Σ_{j<i} tokens(output_j)     # the re-read term
    out_i   ∈ [0.30 × cap_i, cap_i]                            # the range
    cost_i  = in_i × in_rate + out_i × out_rate + locator_cost
expected    = cost × (1 + p_retry × retry_fraction)            # retries, BEFORE margin
price       = expected × (1 + margin)                          # margin ∈ [0.20, 0.30]
```

- `locator_rates`: `tool` $0.005, `url` $0.0005, `file` $0, `script` $0.0002 per call — **vendor estimates**, because
  `tool_gateway_calls.cost_usd` is 0/130 populated.
- 24 tests (`tests/test_cost_estimate_offline.py`), including "a long Way costs more than the sum of its parts".
- Surfaced as `cost_estimate` in `api.get_public`, rendered by `CostRange` in `WayChat.tsx`.

**Live on the real Way:** cost ₹0.22–₹0.62 → price ₹0.30–₹0.85, margin 20%, retry 15%, URL locator charged on step 1.

### 4.2 Caps and budget — `backend/app/execution/step_budget.py`

- `output_cap_tokens(step)` — declared cap, else 8,000 for a step that writes a file, else 2,500.
- `context_budget_chars(step)` — 4,000 for prose (unchanged, so existing Ways are unaffected), 240,000 for a file step.
- Used **three times for one number**: `max_tokens` for the call, the upper bound of the estimate, and the threshold
  at which a truncated body is refused. If they disagreed, a Way could be priced for one size and produce another.
- 15 tests.

### 4.3 The gate — `services/procedure_billing.py`

Built and verified. `check_entitlement` (purchase required), `bind_purchase` (one purchase, one run), `consume_run`,
`release_run`, `may_download` (licence-bound, for artifacts), `set_price(..., priced_by="operator")`.

**`set_price` is the only writer of `price_paise`, and it takes a number from a human.** That is how the ₹1
happened: `--paise 100 --by operator`.

### 4.4 The gap, stated plainly

| Thing needed to "price from the measured run" | Status |
|---|---|
| per-call tokens and cost recorded | ✅ `llm_spend` has `input_tokens`, `output_tokens`, `estimated_cost` |
| **`llm_spend.run_id`** | ❌ **missing** — spend is unattributable to a run |
| **`llm_spend.step_order`** | ❌ **missing** — nor to a step |
| **`way_runs.cost_usd` / `tokens_out`** | ❌ **missing** — nowhere to put the total |
| measure a run | ❌ impossible without the above |
| price from a measurement | ❌ `set_price` only takes a human's number |
| learned caps | ❌ caps are declared constants |
| realized margin | ❌ never computed |

`governance.record()` writes `(scope_key, provider, model, operation, estimated_cost, input_tokens, output_tokens)`.
Every field is there except the two that say *which Way run this was*.

---

## 5. The three-layer answer, corrected

`docs/pricingestimation.md` proposed three layers. The research corrects layer 2.

| Layer | Original claim | Corrected |
|---|---|---|
| **1. Range before the run** | includes re-read term and a cap bound | ✅ holds — it is honest as a *bound* |
| **2. First run is the measurement** | "the price stops being an estimate" | ⚠️ **half true.** The first run gives a **cost**, so it sets the **floor**. It cannot set a **cap** (one sample), and it should not set the printed **price** (a16z: don't anchor to tokens). |
| **3. TokenCast composition** | learned per-segment after N runs | ✅ holds, and it is what makes caps *measured* rather than arbitrary |

### 5.1 The corrected rule

```
measured_floor = measured_cost × (1 + p_retry)        # never sell below this
chosen_price   ≥ measured_floor                        # the author decides, above the floor
realized_margin = (chosen_price − measured_cost) / chosen_price   # checked, not asserted
cap_step        = p99(measured output tokens, n ≥ N)   # learned, once there is a distribution
first_run_cap   = a generous seed + a hard spend ceiling           # because p99(1 sample) = that sample
```

---

## 6. What we recommend building, in order

**A. Attribution** — the foundation; nothing else is possible without it.

```sql
ALTER TABLE llm_spend   ADD COLUMN IF NOT EXISTS run_id uuid;
ALTER TABLE llm_spend   ADD COLUMN IF NOT EXISTS step_order integer;
ALTER TABLE way_runs    ADD COLUMN IF NOT EXISTS cost_usd numeric(12,6);
ALTER TABLE way_runs    ADD COLUMN IF NOT EXISTS tokens_out integer;
```

Threading: `call_unit` already carries `actor` and `tool`; a `run_id`/`step_order` pair travels the same way, and
`governance.record` writes them. Two nullable columns, so no backfill and no behaviour change for existing paths.

**B. Measurement** — `cost_estimate.measure_run(run_id)` sums the attributed rows; per-step `n`, `p50`, `p90`, `p99`,
mean cost. **Refuses to compute a cap below a minimum sample size** and says so, rather than pretending.

**C. Pricing** — `set_price_from_run(procedure_row_id)` computes the **floor** and writes it next to the author's
chosen price. The page shows both: "costs ₹0.31 to run; you pay ₹1.00." Realized margin computed per Way version.

**D. Learned caps** — replace the constants in `step_budget.py` with `p99 × 1.5`, falling back to the constant until
`n ≥ N` (N ≈ 10: 1 sample is not a percentile, and neither is 3).

**E. The first-run ceiling** — a run that has never happened gets a hard spend limit (say ₹50). If the first run
exceeds it, it **fails loudly** rather than quietly costing more than the price it was meant to justify. This is the
honest way to bound a number nobody can predict.

---

## 7. Pitfalls, all of them earned

1. **A point estimate.** Runs vary 30×. Report a range or lie.
2. **Forgetting the re-read term.** Summing per-step costs underestimates long Ways, quadratically.
3. **Pricing off a warm cache.** A repeat run is much cheaper; price the cold case.
4. **Treating 1 sample as a p99.** It is that sample. It sets a floor, not a cap.
5. **Selling an unrun Way.** A price with no measurement behind it is a guess with a currency symbol.
6. **Learning before measuring.** Layer 3 without Layer 2's data is curve-fitting to nothing.
7. **Charging the estimate.** The buyer pays the fixed licence; the estimate is information, not a bill.
8. **A margin that is stated but never checked.** "20%" printed on a page proves nothing; realized margin is the
   only evidence it held.
9. **Double-counting retries** in both the retry term and the margin.
10. **Anchoring the price to tokens** when the product is work. Use cost to find the floor; let the author decide the
    price.
11. **Locator rates that are guesses presented as measurements.** `cost_usd` is 0/130; say so on the page.
12. **An arbitrary cap.** It is both a cost control and the estimate's upper bound, so an arbitrary one makes the
    estimate arbitrary.

---

## 8. One sentence

**Estimate a range before the run, use the first run's measured cost as a floor rather than a price, learn the cap
from a distribution once there is one, and charge for the work — because agent cost varies up to 30×, one sample is
not a percentile, and a token price anchors kell to a unit whose cost keeps falling.**

---

## Sources

- TokenCast: Forecasting Token Consumption During LLM Agent Execution — https://arxiv.org/abs/2609.35760
- EGTP: Predicting LLM Output Length via Entropy-Guided Representations (ICLR 2026) — summarised in
  https://aipractitioner.substack.com/p/llm-cost-prediction-from-single-prompts
- Scheduling LLM Inference with Uncertainty-Aware Output Length Predictions — https://arxiv.org/html/2604.00499
- QuantAI: Setting Output Token Limits per Task Without Starving the Model —
  https://quantaintelligence.ai/blog/setting-output-token-limits-per-task-without-starving-the-model
- UsageBox: AI API Pricing — Pass-Through vs Markup vs Flat (2026) —
  https://usagebox.com/articles/ai-rebilling-pass-through-vs-markup-vs-flat-2026
- a16z: You are not a model. Don't price per token. —
  https://a16z.com/you-are-not-a-model-dont-price-per-token/
- Vikas Malpani: The Cost-First AI Product Launch Playbook — https://vikasmalpani.com/cost-first-ai-product-launch/
- OpenAI prompt caching — https://developers.openai.com/api/docs/guides/prompt-caching
