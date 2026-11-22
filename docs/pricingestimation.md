# Pricing a Way before it has ever run

> **Superseded in part by `docs/blinkit/pricing_discussions.md`** (2026-11-23). §4.2 below ("the first run is the
> measurement") is corrected there: the first run measures a **cost**, so it sets the price floor — it cannot set a
> cap, because one sample is not a percentile. Layer 1 (the pre-run range) remains correct and is what ships.

**What the literature says, what kell records, and what to build. 2026-11-22.** Researched against TokenCast
(arXiv 2609.35760), EGTP (ICLR 2026), provider caching documentation, and the LLM cost-prediction survey
literature; then checked against what this repo actually stores (`db/04_governance.sql`,
`app/execution/way_runs.py`).

---

## 0. The problem, stated exactly

You cannot price a Way before you know what it costs. You cannot know what it costs before it runs. And a Way
must show a price **before** anyone buys.

That is a genuine chicken-and-egg, and it is the one thing the previous pricing documents glossed over. This
document resolves it in three layers (§4), each strictly better than the last, all available on day one.

---

## 1. Why naive prediction fails

The survey literature is blunt about the scale of the error:

> **"When a large language model (LLM) agent executes the same task, token consumption can vary by over an order
> of magnitude across runs."** — TokenCast, arXiv 2609.35760

> "In practice, token consumption across different executions of the same task can differ by **up to 30×**."

The reason is structural, not noise: *"every action an agent takes can trigger new model calls, and consumption
across steps is interdependent: the longer the context left by earlier steps, the larger the input to every
subsequent call."*

**So a single number is the wrong output.** Any honest price estimate is a **range**, and the range is wide. A
page that prints "₹42" and charges ₹310 has lied, even if the model was doing its best.

This directly contradicts what I wrote in `docs/pricing_and_sandbox_per_way.md` §5.0, where I proposed
`price = run_cost × (1 + margin)` as though `run_cost` were a constant. It is not. Corrected here.

---

## 2. What the literature actually offers

Four methods, in increasing order of effort and accuracy.

| Method | What it is | Evidence | Effort |
|---|---|---|---|
| **Self-prediction** | Ask a model, before generating, how long it will be | weak; the survey lists it as a baseline that other methods beat | free |
| **Regression / classification** | A small model (BERT/DistilBERT) maps prompt → output tokens | solid for a single call | training |
| **EGTP** (ICLR 2026) | Predicts **during prefill**, before the first output token, from entropy-weighted hidden states | r = 0.451 between token entropy and token importance | needs model internals |
| **TokenCast** (arXiv 2609.35760) | Learns a **composable cost representation per segment**, composed into a cumulative estimate | **14.5% MAE reduction** over the strongest comparator across 96 combinations; 32.8 ms per forecast | measurement over runs |

**EGTP is not available to us.** It reads hidden states during prefill — we call providers over HTTP and never see
them. Anything requiring model internals is out.

**TokenCast is the right model for kell**, because of its *composition rule*, which maps almost exactly onto how a
Way is shaped:

> "TokenCast represents segment cost relative to its starting input length. The next segment inherits the context
> produced by the preceding one, yielding an **exact composition rule** for adjacent segments."

A segment is `(n, g, b)`:

- `n` — number of calls in the segment
- `g` — the **net change in input length** the segment introduces
- `b` — residual consumption after subtracting the starting-input baseline

And for a chain of segments the total is **not** the sum of their costs — it is the sum **plus** the re-read cost
of every later call seeing everything earlier:

> "Composing adjacent segments yields a cumulative estimate that captures the **extra input cost incurred when
> context from earlier segments is re-read by every later call**."

**This is the sentence that matters for kell.** In `way_runs.py`, *"every step sees the run's input and every
earlier step's output, not just the last one."* So the cost of step 5 depends on the **output of steps 1–4**, not
only on step 4 — and a naive estimator that sums per-step costs will systematically **underestimate**, badly, on
long Ways. The longer the Way, the worse the error, because the error scales with the square of length.

---

## 3. The second lever: caching

Prompt caching discounts **reused prefixes** by up to **95%** of the input rate (provider docs). A Way's earlier
steps are a stable prefix for its later ones, which means:

- a step's real cost is **not** `input × rate`, it is `uncached_input × rate + cached_input × 0.1 × rate`;
- a Way run **twice** costs far less the second time, and pricing it off the first run overcharges;
- a price must say whether it assumes a warm cache. We should assume **cold**, and treat cache hits as margin —
  never promise a price we only hit when warm.

**This is also the argument for capping output** (§4.3): output tokens are the uncacheable, expensive half.

---

## 4. The three layers, in order

### Layer 1 — a ruler, not a guess (available today, zero data)

Before anything is learned, estimate from what is **structurally known**:

```
For each step i:
  in_i   = tokens(input) + Σ_{j<i} tokens(output_j)      # the re-read rule, TokenCast's g
  out_i  ≤ cap_i                                          # the per-step cap (§4.3)
  cost_i = in_i × in_rate + out_i × out_rate
price_before_run = Σ cost_i × margin
```

Two things this gets right that naive estimators do not:

1. **It includes the re-read term**, so step 5 is charged for reading steps 1–4.
2. **It reports a range**, not a point: lower bound at 30% of `out_i`, upper at 100%.

**It is honest about being a bound.** The page says *"estimated ₹X–₹Y; the first run sets the real figure"*, not
*"₹X"*. A bound that is never wrong is worth more than a point estimate that usually is.

### Layer 2 — the first run is the measurement (the owner's rule, and it is correct)

**After the first successful run, the price stops being an estimate.** The run records:

- actual `in_i`/`out_i` per step (the executor already accumulates outputs; the providers already return usage),
- actual sandbox seconds,
- wall time.

That becomes `run_cost_paise` — the real number — and the Way is priced from it.

**This is why the first run is special and why it should be *by the author*, before listing.** A Way that has
never run is `unpriced` and may be published but not sold. That is a hard gate, and it is the cheapest possible
one: it costs the author one run.

### Layer 3 — TokenCast composition, once there is history (learned)

With N runs of a Way, each step's segment `(n, g, b)` gets a **mean and a spread** instead of a bound. New Ways
without history borrow from:
- **the same author's** other Ways (their prompts and habits),
- **Ways with the same output kind** (a 5-step research report looks like another 5-step research report),
- **the global prior** for step count.

Refinement is free at run time: TokenCast refreshes its forecast *"as execution unfolds, requiring no additional
LLM calls"*, and its **Task Update** setting (`T̂ = S_k + R̂_k`) is exactly what a live price readout wants.

**Do not build Layer 3 first.** It needs data that Layer 2 produces.

### 4.3 Capping output, per step

You asked for output tokens capped per step, and the literature supports it beyond cost reasons:

- **Output is the uncacheable half.** Caching discounts input up to 95%; nothing discounts output.
- **The upper bound of the estimate is the cap.** `max_tokens` turns "up to 30×" into "at most `cap` × steps",
  which is what makes Layer 1's range trustworthy rather than a guess.
- It is already how `sandbox.py` behaves (`MAX_OUTPUT = 12_000`), so the discipline exists in the codebase.

Add `output_cap_tokens` per step, defaulting to something small (e.g. 1,500) and bounded by the step's
declared need. A step that legitimately needs more declares it, which is a reviewable act.

### 4.4 Margin must pay for retries, not sit on top of an underestimate

**A 20–30% margin on the measured cost is not a 20–30% margin, because the measured cost is one lucky run.**

Retries are not an edge case: an agent that fails a verification runs the step again, and **a retry costs more than
the first attempt**, because it re-reads a context that already contains the failure. So the margin is applied to an
expected cost that already contains the retry term:

```
expected = first_attempt_cost × (1 + retry_probability × retry_fraction)
price    = expected × (1 + margin)          margin ∈ [0.20, 0.30]
```

At `retry_probability = 0.15` and `retry_fraction = 1.0`, **one step in seven costs double** — a 17% invisible
uplift that a naked 20% margin would silently absorb. The margin is clamped to the band, so a caller cannot pass
0.8 and charge 80%.

**Both numbers are printed on the page.** "You pay ₹X–₹Y, which covers 25% margin and 15% of steps running twice."
A price whose components are hidden is the kind of price a buyer cannot argue with, which is not a compliment.

### 4.5 Locators cost money the token maths cannot see

**21 of the 30 stored steps carry a locator** — 14 `tool`, 7 `url`. A tool call is a real charge (Exa returns an
actual `costDollars.total`), and a URL fetch is real work. An estimate that only counts tokens is wrong about the
majority of steps.

And the honest caveat: **`tool_gateway_calls.cost_usd` exists and 0 of 130 recorded calls has it filled in.** So
locator cost is currently *estimated from vendor price lists*, not measured. The assumption is printed on the page,
and `locator_rates` is overridable so a real price replaces the guess the moment `cost_usd` starts being written.

Rates used, per call: `tool` $0.005, `url` $0.0005, `file` $0, `script` $0.0002 (a 2-minute sandbox term).

---

## 5. What kell already records

From `db/04_governance.sql`, the `llm_spend` table already has:

```
model, estimated_cost NUMERIC, input_tokens INTEGER, output_tokens INTEGER
```

So **per-call token and cost telemetry already exists.** The gap is that it is not keyed to a **Way step**, so no
per-step segment can be learned. That is the one schema change Layer 3 needs:

```sql
ALTER TABLE llm_spend ADD COLUMN IF NOT EXISTS run_id UUID;      -- the Way run
ALTER TABLE llm_spend ADD COLUMN IF NOT EXISTS step_order INTEGER; -- which step
```

With those two columns, every run produces the `(n, g, b)` tuples TokenCast composes, for free.

---

## 6. Build order

1. **`output_cap_tokens` per step**, enforced at the call. Without it nothing downstream has a ceiling.
2. **`run_id` + `step_order` on `llm_spend`.** Cheap, and it is what makes every later run usable as training
   data. Do it before there is history worth having.
3. **Layer 1 estimator** (`estimate_cost(way) -> {low, high, assumptions}`) — the re-read rule and the range.
4. **Gate: unpriced Ways cannot be sold.** Publishing is free; selling needs one completed run.
5. **Layer 2: price from the first run's measured cost**, replacing the estimate on the page with the real figure
   and keeping the estimate visible as history.
6. **Layer 3: TokenCast's segment composition**, once per-step history exists.

---

## 7. Pitfalls

1. **A point estimate.** Runs vary up to 30×. Report a range or lie.
2. **Forgetting the re-read term.** Summing per-step costs underestimates long Ways, quadratically.
3. **Pricing off a warm cache.** A repeat run is much cheaper than a cold one; price the cold case.
4. **No output cap.** Unbounded output is unbounded cost, and it is the *only* unbounded half.
5. **Selling an unrun Way.** A price with no measurement behind it is a guess with a currency symbol.
6. **Learned model before measurement.** Layer 3 without Layer 2's data is curve-fitting to nothing.
7. **Charging the estimate.** The buyer pays the fixed licence; the estimate is information, not a bill.

---

## 8. One sentence

**Estimate a range before the first run (with the re-read term and a per-step output cap), measure the real cost
during it, and only then learn per-segment composition — because agent token consumption varies up to 30×,
context re-reading compounds with Way length, and the honest price before measurement is a bound, not a number.**

---

## Sources

- TokenCast: Forecasting Token Consumption During LLM Agent Execution — https://arxiv.org/abs/2609.35760
- EGTP: Predicting LLM Output Length via Entropy-Guided Representations (ICLR 2026) — summarised in
  https://aipractitioner.substack.com/p/llm-cost-prediction-from-single-prompts
- Scheduling LLM Inference with Uncertainty-Aware Output Length Predictions — https://arxiv.org/html/2604.00499
- OpenAI prompt caching (up to 95% cheaper cached input) — https://developers.openai.com/api/docs/guides/prompt-caching
