# blinkit

**The pricing and execution record for Ways. Start with `pricing_discussions.md`.** 2026-11-23.

This folder consolidates what was previously spread across `docs/pricing_page.md`,
`docs/pricing_and_sandbox_per_way.md` §5, `docs/pricingestimation.md` and `docs/steps_sandboxes_filesystems.md`, plus
two decisions made in conversation that those documents did not record.

| File | Read it for | Status of what it describes |
|---|---|---|
| **`pricing_discussions.md`** | how we decided to price, what the 2026 literature says, what is built vs missing | pricing gate ✅ built; measurement ❌ not built |
| **`execution.md`** | what actually runs a step: model call, sandbox, filesystem, limits | ✅ built and verified end to end |
| **`measurement.md`** | what a run records; why `llm_spend` needs a `run_id` before anything can be priced from a run | ❌ designed, not built |
| **`caps.md`** | why output caps are pricing parameters, why ours were invented, what replaces them | ⚠️ seeds built; learned caps not built |
| **`open_questions.md`** | the ten decisions still open, each with a proposed default | — |
| **`next_pass_plan.md`** | the plan for the unmeasured-price gate and attribution, written before the code, plus what actually happened | ✅ built |
| **`presentation_rooms.md`** | a Way running in a Room as a presentation: inputs first, per-step price, restart from the middle | ✅ built (run rail in `/chat`) |
| **`storage_choice.md`** | R2 vs Backblaze B2: cost, measured latency from six locations, and why the CDN decides it | ⚠️ decided open; code provider-agnostic, provider not chosen |
| **`launch_b2_cloudflare_india_us.md`** | the launch runbook: B2 US East + Cloudflare, for India and the US, every step in order | ⚠️ written; not yet executed |
| **`what_was_done.md`** | what this session built, where it improves on the record, and what is still missing | ✅ built (purchase/run path, Way page) |

---

## The three findings that changed things

1. **The first run measures a cost, not a cap.** One sample is not a percentile — the p99 of one run *is* that run, so
   a cap set from it truncates the next slightly-longer run. The first run sets a **floor**; caps need a
   distribution (`caps.md` §3).
2. **Cost is measured but unattributed.** `llm_spend` records tokens and cost with no `run_id` or `step_order`, so no
   Way run's cost can be recovered. Two nullable columns unblock the entire measurement layer (`measurement.md` §1).
3. **Pricing in tokens is contested.** a16z argues it anchors kell to a unit whose cost keeps falling; their buyer
   survey preferred work-based credits 27 to 14. A Way is already a work unit, so cost should set the floor, not the
   price (`pricing_discussions.md` §3.4).

---

## The build order

```
A. attribution   llm_spend.run_id + step_order; way_runs.cost_usd     ✅ built (db/224, 225)
B. measurement   per-step n / p50 / p90 / p99, refusing below n=10    ✅ per-run cost built; percentiles not yet
C. pricing       measured floor + author's chosen price + realized margin   ⬜ next
D. learned caps  p99 × 1.5 once there is a distribution                     ⬜
E. first-run ceiling  a hard spend limit that fails loudly                  ⬜
```

A is done. The gate that refuses to price a Way nobody has run is done too — `set_price` returns 409 with
*"run it once, then price it"*. What is **not** done: measuring the ₹1 Way for real, and everything from C down.
See `next_pass_plan.md` § "What actually happened" for the three discoveries that changed that plan, including a
real cost that rounded to zero paise.

---

## Related, outside this folder

- `docs/pricingestimation.md` — the pre-run estimator's model (still correct for layer 1).
- `docs/steps_sandboxes_filesystems.md` — the sandbox research this folder's `execution.md` implements.
- `docs/procedure_outputs.md` — where artifacts live; object storage, not Postgres.
- `docs/thumbnails.md` — the listing picture: sample run, covers, filmstrip, PII.
- `docs/procedure_billing.md` — the purchase/licence API contract.
- `docs/sandboxing.md` — provider rate cards and isolation levels.
