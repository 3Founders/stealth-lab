# The next build: enforce the unmeasured gate, then attribute

**The plan for this pass, written before the code. 2026-11-23.** Companion to `what_was_done.md` (what the previous
pass built) and `measurement.md` (the schema this pass creates).

> **Status: both steps are built.** See §"What actually happened" at the end — it records three discoveries that
> changed the plan, one of them a bug that would have recorded paid runs as free.

Two steps, in this order, for a reason:

1. **Stop pricing a Way that has never run.** Cheap, immediate, and it stops the exact failure sitting live: a ₹1 Way
   whose run cost nobody recorded.
2. **Attribution.** Two nullable columns and a thread through `call_unit`, without which nothing downstream — floor,
   realised margin, learned caps — can exist.

Doing 2 first would leave the hole open while the foundation was laid. Doing 1 first means the only Ways that can be
priced are ones whose cost will soon be known.

---

## Step 1 — `set_price` refuses a Way with no measured run

### What is wrong now

```python
async def set_price(pool, *, procedure_row_id, price_paise, currency="INR", priced_by="operator"):
    """Price one exact version. None un-prices it; 0 is a deliberate free listing."""
    # no check that the Way has ever run
```

`set_price` is the only writer of `procedures.price_paise`. It takes any number from any operator. The ₹1 on the
NeurIPS Way was set that way, and `pricing_discussions.md` D6 says that must not happen: *"a Way with no measured run
is unpriced — publishable, not sellable."* `what_was_done.md` §6 confirms the rule is still unenforced and is now more
urgent, because one purchase equals one run of unbounded cost.

### The rule

`set_price` refuses to set a **positive** price unless the version row shows a measured run:

- `price_paise = None` (un-price) — always allowed. Withdrawing a price cannot mislead anyone.
- `price_paise = 0` (deliberate free listing) — always allowed. A free Way has no margin to be wrong about.
- `price_paise > 0` — **refused** unless `procedures.measured_runs > 0` and `procedures.measured_cost_paise` is set.

The refusal is a named error with the sentence a person needs:

> *"this Way has not been run, so its cost is unknown; run it once, then price it"*

### The columns it reads

Step 2 adds them; this step reads them. So the two migrations land together and Step 1's guard is inert for exactly
as long as it takes Step 2 to fill them — which is correct, because an unpriced Way cannot be bought anyway.

```sql
ALTER TABLE procedures ADD COLUMN IF NOT EXISTS measured_runs        integer NOT NULL DEFAULT 0;
ALTER TABLE procedures ADD COLUMN IF NOT EXISTS measured_cost_paise  bigint;   -- mean over the measured runs
```

**Existing prices are not touched.** The ₹1 Way stays ₹1 until it is re-measured or withdrawn. A migration that
silently un-prices live listings would break a real buyer's page to satisfy a rule; the rule applies to the *next*
price set. This is recorded as a known exception below.

### Tests

| Claim | Test |
|---|---|
| a positive price is refused with no measured run | `test_pricing_an_unmeasured_way_is_refused` |
| the refusal names the Way and says what to do | `test_the_refusal_says_run_it_once` |
| `None` (un-price) always works | `test_un_pricing_never_needs_a_measurement` |
| `0` (free) always works | `test_a_free_listing_needs_no_measurement` |
| a measured Way can be priced | `test_a_measured_way_can_be_priced` |

---

## Step 2 — Attribution

### Schema (`db/224_run_cost_attribution.sql`)

```sql
ALTER TABLE llm_spend ADD COLUMN IF NOT EXISTS run_id     uuid;
ALTER TABLE llm_spend ADD COLUMN IF NOT EXISTS step_order integer;
CREATE INDEX ... ON llm_spend (run_id, step_order) WHERE run_id IS NOT NULL;

ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS cost_usd   numeric(12, 6);
ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS tokens_in  integer;
ALTER TABLE way_runs ADD COLUMN IF NOT EXISTS tokens_out integer;
```

Nullable, no default, no backfill. Every existing caller of `record()` writes `NULL` and behaves exactly as before.

### Threading

`CallRequest` gains two optional fields; `governance.record()` writes them. The chain:

```
way_runs.start(run_id)
  -> advance() per step
     -> _run_by_kell(step, run)
        -> _pinned / _run_automated
           -> call_unit(..., run_id=run["id"], step_order=step["order"])
              -> governance.record(... run_id, step_order)
```

`tool_gateway_calls` already has `task_id` and `cert_id`; a `run_id` there is the same shape and is added in the same
migration, so a tool step's locator cost is attributable too.

### Measuring a run

```python
async def measure_run(pool, run_id) -> dict:
    # sum the attributed rows, per step
    # write way_runs.cost_usd / tokens_in / tokens_out
    # update procedures.measured_runs, measured_cost_paise (mean)
```

Called from `_settle_purchase`'s neighbourhood (once per finished run, `done` only — a failed run's spend is real but
should not move the price of a Way that has never *succeeded*, or one flaky run would set a price).

### Tests

| Claim | Test |
|---|---|
| a two-step run writes two rows, same run, different step | `test_two_steps_two_rows_one_run` |
| `measure_run` equals the summed ledger | `test_measure_equals_the_ledger_sum` |
| an unattributed row is never counted for a run | `test_unattributed_spend_is_not_a_run` |
| a failed run does not set a price | `test_a_failed_run_does_not_price_a_way` |
| no `run_id` means `NULL`, never a crash | `test_a_call_without_a_run_still_records` |

---

## What this pass does **not** do

| Left for the pass after | Why |
|---|---|
| learned caps (p99 × 1.5) | needs a distribution; one run is not one |
| realised margin on the page | needs a price that a measurement set |
| the first-run spend ceiling | policy question (fail vs warn) — `open_questions.md` Q3 |
| reprice on a cheaper run | **still open** — Q4, unmade |
| sample run + thumbnails | `thumbnails.md`; its own open questions |
| PII scanning | `thumbnails.md` §7; blocks publishing a sample |

---

## Known exception, stated rather than hidden

**The ₹1 Way is priced without a measurement.** Step 1's guard applies to the next `set_price` call, not to prices
already set. Consequence: if its run costs more than ₹1, that sale loses money and the page still says ₹1. Step 2's
measurement is what turns that from an unknown into a number, and re-pricing it is the first thing the next pass does.

The alternative — un-pricing it in the migration — would take a live listing down on the strength of a rule its own
buyer never agreed to. A price that is wrong is better handled by measuring it than by disappearing it.

---

## Order of work

```
1. db/224 (both column sets, one migration)
2. procedure_billing.set_price guard + tests
3. CallRequest fields + governance.record + tests
4. thread run_id through way_runs
5. measure_run + the measured_* update + tests
6. measure the ₹1 Way for real; record the number here
```

Each step is a separate commit with its pytest counts, per the repo's rule.

---

## One sentence

**Refuse to price a Way nobody has run, then make every model call say which run and step it belonged to — because a
price with no measurement behind it is a guess, and a cost that was measured but unattributed cannot price anything.**

---

## What actually happened

Built, verified against the live database, **6226 passed / 16 pre-existing failures**, migrations `224` and `225`
applied.

| Piece | Where | Tests |
|---|---|---|
| the unmeasured-price gate | `procedure_billing.set_price` | 8 + 2 in the billing file |
| attribution on every successful call | `providers/service._attribute_call` | 6 |
| measurement of a run | `way_runs.measure_run` | 5 |
| schema | `db/224`, `db/225` | migrated |

### Discovery 1 — the org ledger would have missed every Way run

The plan said to attribute into `llm_spend` via `governance.record`. Wrong on two counts:

- `call_unit` never writes `llm_spend` at all; it writes `provider_call_ledger`.
- `provider_call_ledger.organization_id` is **NOT NULL**, and `hold_budget` returns `(None, None)` when no
  organisation governs the call (`service.py:275`). **A personal buyer's Way run writes no ledger row whatsoever.**

So attribution was attached to `call_unit`'s success path instead — the one place every successful call passes
through, org-governed or not.

### Discovery 2 — a real cost rounded to zero

The first live probe attributed a real step at $0.00042 and stored `measured_cost_paise = 0`. That is ₹0.042 recorded
as **free** — and a free floor lets `set_price` accept any price, which is the exact failure the floor exists to
prevent. `db/225` adds `measured_cost_micros` (1e-6 of a rupee); paise is now display only. Verified live: ₹0.042
stored exactly.

This is also why `224` was not edited to fix it: migrations are immutable once applied, and the checksum guard
refused. That guard was right.

### Discovery 3 — the cheapest step is not the whole cost

A one-call probe is not a Way. `measure_run` was proven correct end to end (attributed row → run total → procedure
mean), but **the ₹1 Way itself has not been run through this path yet**, because its provider connections are
registered from org keys at server startup and a probe cannot borrow them without guessing at auth. Measuring the
real Way needs one real run through the server.

### What the next pass inherits

- The ₹1 Way still has `measured_runs = 0`. Its price predates the gate and is the one known exception (§ above).
  Running it once through the real server is the first action.
- Every condition for C (floor + chosen price) and D (learned caps) is now met by the schema; only the code remains.

