# Output caps: why they exist, why ours were arbitrary, what replaces them

**2026-11-23.** Companion to `measurement.md` (where the data comes from) and `pricing_discussions.md` (what it is
for). You said the caps should be arbitrary no longer — they should be estimated from real runs. This is what that
means exactly, and the one thing it cannot do.

---

## 0. The caps we shipped, and where each number came from

| Constant | Value | Origin |
|---|---|---|
| `MAX_OUTPUT_IN_CONTEXT` | 4,000 chars | **invented.** No measurement behind it. |
| `max_tokens` per step | 2,500 | **invented.** |
| `BASELINE_TOKENS` | 2,500 | preserved the invented one, deliberately, so existing Ways don't change. |
| file-producing step | 8,000 tokens | a guess sized to "a 1500-word document fits". |
| `BASELINE_CONTEXT_CHARS` | 4,000 | as above. |
| file step context | 240,000 chars | a ceiling, chosen to be large rather than right. |
| `MAX_STEP_TOKENS` | 32,000 | a safety ceiling, not a prediction. |
| `sandbox.MAX_OUTPUT` | 12,000 chars | predates this work. |

The honest summary: **every one of these is a judgement, and only one of them (2,500) is a judgement that was
validated by anything running.** That is the problem you identified.

---

## 1. Why a cap is load-bearing, not decoration

A cap is three things at once, and they must agree:

1. **`max_tokens` for the call** — what the model may actually write.
2. **The upper bound of the cost estimate** — `docs/pricingestimation.md` §4.3: *"the upper bound of the estimate IS
   the cap."* An uncapped step makes a Way unquotable.
3. **The truncation threshold** — `artifacts.looks_truncated` refuses a body that used ≥95% of its cap, because a
   `.tex` cut mid-sentence does not compile.

`step_budget.output_cap_tokens(step)` returns **one number used for all three**. If they disagreed, a Way could be
priced for one size of reply and produce another — the buyer pays for 1,500 tokens and receives 8,000, or the reverse.

**So the cap is a pricing parameter.** An arbitrary cap is an arbitrary price.

---

## 2. The literature's rule

QuantAI, *Setting Output Token Limits per Task Without Starving the Model*:

> *"For each task, measure completion length from logs, and set the output cap at that task's 99th percentile."*

| Observed | Cap | Truncation rate |
|---|---|---|
| p50 only | p50 | unacceptable |
| p90 | p90 | ~1 in 10 |
| **p99** | **p99** | **~1 in 100** |
| p99 × 1.5 | headroom | well under 1 in 100 |
| model maximum | never a default | near zero, expensive |

Why p99 and not the mean: *"The distribution is usually long-tailed, not normal... A single 4,000-token generation in
a task whose median is 280 tokens barely moves the mean but is exactly the case that a 90th-percentile cap kills."*

Why not the model maximum: *"Leaving the cap at the model maximum does not make the model more verbose, but it removes
your safety net on runaway generations, accidental loops in agents, and prompt injection that says 'repeat this
forever.'"* The cap is a **cost and safety control**, not only a quality one.

Two cautions that constrain what we may claim:

- **Exclude truncated requests.** They understate true length. Our guard refuses to store such bodies, so we would
  have *no* data rather than *bad* data — but the token count still needs flagging.
- **Require a minimum sample size.** *"A task with forty observations has a p99 that is really just the longest
  request you have seen."*

---

## 3. The correction to "measure it from the first run"

You asked for the cap to be estimated from the first run. **It cannot be, and the reason is arithmetic:**

```
p99 of one sample = that sample
```

A cap set to one run's output length truncates the second run the moment it is one token longer. So:

| The first run gives you | The first run does not give you |
|---|---|
| its **cost** → sets the price **floor** | a **cap** — one sample is not a percentile |
| its **output length** → a data point | a **distribution** for the tail |
| whether the **estimate was right** → calibration | the **p99 × 1.5** headroom |

This is not a limitation of kell's implementation; it is what a percentile is. The honest sequence is:

```
run 1      seed cap (generous) + hard spend ceiling   -> measures cost
runs 2..N  accumulate per-step output lengths
run N≥10   cap := p99(measured) × 1.5                 -> learned
```

---

## 4. What replaces the constants

### 4.1 The seed, for the first run

A Way that has never run gets a **generous** cap plus a **hard spend ceiling** — the cap bounds the reply, the ceiling
bounds the bill. If the first run would exceed the ceiling it **fails loudly** rather than quietly costing more than
the price it was meant to justify.

Why a ceiling and not a predicted number: nobody can predict a first run, so the only honest construction is *"spend
at most this much to learn what it costs."* A number that is bounded and visible beats a number that is guessed and
invisible.

### 4.2 The learned cap

```python
cap_for(procedure_row_id, step_order) =
    p99(measured out_tokens, excluding truncated) × 1.5      if n >= 10
    else seed_cap(step)                                       with the reason shown
```

Per **step**, not per Way. QuantAI: *"Keep `step` separate from `task`... Tracking only a task-level total hides
which step is blowing the budget."* A Way's step 2 (a review) and step 3 (a `.tex` wrapper) have different
distributions and deserve different caps.

### 4.3 The type of content matters, not just length

A `.tex`, a PDF, and a two-sentence summary have wildly different length distributions. The seed therefore depends on
what the step produces:

| Step produces | Seed cap |
|---|---|
| nothing (prose) | 2,500 (the validated baseline) |
| a text document | 8,000 |
| source code / `.tex` | 8,000 |
| a PDF or deck (via script) | n/a — the sandbox's `MAX_OUTPUT` and the artifact size limit apply |

Once measurable, these seeds stop mattering: the measured p99 replaces them.

---

## 5. What this changes on the page

The estimate's upper bound becomes a **measured** bound rather than a declared one, so the range narrows as a Way
runs more. `CostRange` already prints the assumptions; one of them becomes:

> *"output per step bounded by its cap (p99 of 14 measured runs × 1.5)"*

rather than

> *"output per step bounded by its cap; low end assumes 30% of it."*

A Way with no measurements keeps the second sentence, which is honest.

---

## 6. Pitfalls

1. **Setting the cap from one run.** p99 of one sample is that sample. The next run is truncated.
2. **A cap at the model maximum.** No protection against runaway generation, loops, or prompt injection.
3. **One cap for all steps.** Hides which step is blowing the budget, and truncates the long one.
4. **Including truncated requests in the percentile.** They understate, so the cap ratchets *down* over time.
5. **A cap that disagrees with the estimate's bound.** The buyer is priced for one size and gets another.
6. **Forgetting the context window.** The practical ceiling is `context_window − input_tokens`, not the model's
   advertised max output; a wide prompt can fail outright at a legal cap.
7. **Reasoning tokens.** They are billed as output; a cap sized for the visible answer silently cuts the thinking.
8. **No spend ceiling on the first run.** The one run with no history is the one with no bound.

---

## 7. One sentence

**A cap is a pricing parameter, so it cannot be a guess: seed the first run generously with a hard spend ceiling, then
replace the seed with the p99 of measured output per step once there is a distribution — because the first run
measures a cost, and a percentile needs more than one sample.**
