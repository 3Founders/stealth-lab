# Measurement: what a run records, and why attribution comes first

**The layer between "a run happened" and "we know what it cost". 2026-11-23.** Companion to `execution.md` (what
runs) and `pricing_discussions.md` (why we measure).

---

## 0. The gap in one table

| Needed to price from a measured run | Exists? |
|---|---|
| per-call input/output tokens | ✅ `llm_spend.input_tokens`, `output_tokens` |
| per-call cost | ✅ `llm_spend.estimated_cost` |
| which provider/model | ✅ `llm_spend.provider`, `model` |
| **which Way run** | ❌ **no `run_id`** |
| **which step** | ❌ **no `step_order`** |
| the run's total | ❌ no column on `way_runs` |
| output length per step (for caps) | ⚠️ in `outputs` jsonb as text, not in tokens |

`governance.record()` writes:

```python
INSERT INTO llm_spend (scope_key, provider, model, operation,
                       estimated_cost, input_tokens, output_tokens)
```

Every field is there except the two that say *which run this was*. So today: a run happens, its cost is recorded, and
**it is unattributable**. You cannot ask "what did run X cost" — the information was never written down.

This is why the build order in `pricing_discussions.md` §6 starts with two columns and nothing else.

---

## 1. The attribution schema

```sql
-- db/223_llm_spend_attribution.sql
ALTER TABLE llm_spend ADD COLUMN IF NOT EXISTS run_id uuid;
ALTER TABLE llm_spend ADD COLUMN IF NOT EXISTS step_order integer;

CREATE INDEX IF NOT EXISTS idx_llm_spend_run ON llm_spend (run_id, step_order)
    WHERE run_id IS NOT NULL;

ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS cost_usd numeric(12, 6);
ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS tokens_in integer;
ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS tokens_out integer;
```

Why this shape:

- **Nullable, no default.** Every existing caller of `record()` keeps working and writes `NULL`; a path that has no
  run to attribute to is honestly "no run". No backfill — there is nothing to guess.
- **`step_order`, not `step_id`.** A step is identified by its position in a run's plan (`way_runs.plan`), and the
  same step in two runs must aggregate together to produce a distribution. A per-run step id would fragment it.
- **A partial index**, because most `llm_spend` rows (ingestion, search) will never have a run, and a full index would
  pay for them.
- **The total on `way_runs`** is a denormalisation. It is worth it: a page showing "this run cost ₹0.31" must not
  aggregate a ledger at read time, and an `UPDATE` at run end is one row.

---

## 2. Threading it through

`call_unit` already carries `actor` and `tool`. The run id and step order travel the same way:

```python
CallRequest(prompt=..., max_tokens=budget(step),
            run_id=run["id"], step_order=step["order"])   # new, both optional
```

and `governance.record()` writes them into the two columns. Nothing else changes: the cost is already computed there
(`estimate_cost(provider, in, out)`), so the measurement layer adds no new arithmetic — it adds **keys**.

**What gets attributed, and must not be missed** (UsageBox, §3.3 of `pricing_discussions.md`):

- input and output tokens per call
- cache reads vs cache writes (billed differently, and large)
- **retries and failed calls** — we paid for them; whether the buyer does is a decision, not an accident
- **every tool call a step fans out into** (`tool_gateway_calls`, whose `cost_usd` is 0/130 populated)
- sandbox seconds, which today are not recorded anywhere

Failing to attribute one of these does not make the price wrong by a little; it makes the floor wrong, and the floor
is what protects the margin.

---

## 3. What a run reports, once attributed

```python
measure_run(run_id) -> {
  "cost_usd": 0.0031,
  "tokens_in": 12480, "tokens_out": 2140,
  "steps": [
    {"order": 1, "calls": 1, "cost_usd": 0.0009, "out_tokens": 806, "locator_usd": 0.0005,
     "truncated": False},
    {"order": 2, "calls": 1, "cost_usd": 0.0022, "out_tokens": 1334, "locator_usd": 0.0,
     "truncated": False},
  ],
  "measured": True,
}
```

Note `truncated`. QuantAI's caution is that **a truncated request understates true length**, so it must be excluded
from any percentile. Our truncation guard already refuses to *store* such a body, but the *token count* still needs
flagging, or the p99 will be computed from requests that were cut.

---

## 4. Distributions, and the honest sample-size limit

A cap needs a distribution, not a number:

```python
caps_for(procedure_row_id, step_order) -> {
  "n": 14, "p50": 812, "p90": 1500, "p99": 2310, "cap": 3465,     # p99 × 1.5
}
```

**With `n < MIN_SAMPLES` (10) it returns the range and refuses to name a cap**, with the reason. The literature is
explicit: *"a task with forty observations has a p99 that is really just the longest request you have seen."* At
`n = 1` the p99 **is** the single sample, so a cap set from it truncates the next run that is one token longer.

This is the correction to "price from the first run" (`pricing_discussions.md` §5): the first run gives a **cost**
(sets the floor) but not a **cap** (needs a distribution).

---

## 5. Realized margin

The margin is currently **stated**: printed on the page as 20%. It is never checked.

```
realized_margin = (charged − measured_cost) / charged
```

per Way version, over its runs. `UsageBox`: *"A meter that reports realized margin per customer per model is how a
20% markup stays 20%."* Without it, a 20% stated margin can drift to 4% through a model price rise, a retry storm, or
a cache behaviour change, and the page would still say 20%.

A Way is a fixed-price licence (one output for ₹1), so once sold the price cannot change for that buyer — but the
**next** buyer's price can, and the author must be shown the measured margin before setting it.

---

## 6. Privacy and scope

Every attributed row obeys the existing rules: it goes through `scope_predicates()` and `tenant_transaction` like any
other read. `llm_spend` is per-owner: a Way's measured cost is visible to the **author** and to **kell**, never to a
buyer as another person's data. What a buyer sees is the price and the estimate's assumptions, which is already the
page's design.

The run's `input` is the buyer's; its cost is the author's cost of goods. Those are different objects and must not be
joined in anything the buyer can read.

---

## 7. Acceptance

1. A two-step run produces **two** `llm_spend` rows with the same `run_id` and different `step_order`.
2. `measure_run(run_id)` equals `SUM(estimated_cost)` for that run, to the paise.
3. A run whose step was truncated is **excluded** from `caps_for`, and the reason is visible.
4. `caps_for` returns **no cap** below 10 samples, and says why.
5. Two runs of the same Way produce two rows in the distribution, not one overwritten.
6. A model price change moves `realized_margin` and the change is visible without a code change.

---

## 8. One sentence

**Record which run and which step every model call belonged to, because cost that is measured but unattributed cannot
price anything — and a percentile from one sample is not a percentile, it is that sample.**
