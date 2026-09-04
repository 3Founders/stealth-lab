# Final Baseline vs Stealth Agent Experiment -- FROZEN PROTOCOL (v2)

**STATUS: NOT frozen for execution. This document records the current
best-understood configuration after the final-readiness-gate pass, but
section 3's `max_steps` is explicitly UNRESOLVED (see
`budget-calibration.md`'s addendum) -- do not treat this file as
authorizing a scored run. It will be re-issued as genuinely frozen once
`max_steps` is validated and any task-quality changes to T7 are made and
also frozen, per this same process.**

This document supersedes `protocol.md` for the items it updates; it does
NOT retroactively alter `protocol.md`'s own text (per the coordinator's
explicit instruction against silently rewriting frozen history) --
`protocol.md` remains the original design record, this file is the
current-state amendment.

## What changed from `protocol.md` (v1)

| item | v1 (`protocol.md`) | v2 (this file) | status |
|---|---|---|---|
| `max_steps` | 8 (pilot), 25 (recommended, unverified) | **UNRESOLVED** -- 25 tested and found insufficient for 5/6 cells (`budget-calibration.md` addendum) | **NOT FROZEN** |
| Task set (T1/T3/T7) | as designed | T7 flagged as likely needing redesign (0/11 ground-truth recall at both 16 and 25 steps) -- not yet redesigned this pass | **NOT FROZEN** |
| Corpus eligibility | contaminated (all 12 verified procedures were fixtures) | **FIXED** -- db/39+db/40+`_CANDIDATE_BASE_WHERE`, live-verified | frozen (this fix itself is not going to be reverted) |
| Execution robustness (T7 crash) | `_MCP_SESSION_HTTP_TIMEOUT_SECONDS=650` fix (prior pass) | re-verified this pass, confirmed solid, no other TaskGroup/timeout risk found | frozen |
| Failure classification | single generic error field | **NEW**: `orchestrator.py::classify_failure()`, 6 real categories, 10/10 offline tests passing | frozen (additive, non-breaking) |
| Real external knowledge availability | unverified (no live-DB check yet) | **CONFIRMED RETRIEVABLE** -- see `final-readiness-review.md` gate F | frozen (as a fact; the gate itself is proven, not re-litigated) |
| Model / provider | `gpt-oss-120b` via GENERAL_COMPUTE | unchanged | frozen |
| Cost | unavailable (no real pricing config) | unchanged, still genuinely unavailable | frozen |
| Isolation mechanism | disposable git worktree per trial | unchanged, confirmed still working (calibration + retrieval-verification trials this pass all used it / a documented direct equivalent) | frozen |

## Sections carried forward unchanged from `protocol.md` (see that file for
## full text -- not reproduced here to avoid drift between two full copies)

- Trial count (3 per task per arm-config for the initial scored run)
- Success criterion (each task's own deterministic `verify_fn`, never
  subjective)
- Environmental-failure handling (tagged, excluded from primary
  success-rate comparison, never silently dropped)
- Analysis method (paired per-task comparison, raw + success-normalized,
  no significance claims at this sample size)
- No mid-experiment parameter changes once genuinely frozen

## What must happen before this document can be reissued as genuinely
## FROZEN and a scored run authorized

1. **A validated `max_steps`.** Either: (a) a further calibration round at
   a value higher than 25, with real evidence it lets T1 and T3 reach a
   natural stop most of the time (not "seems enough" -- an actual test,
   per `budget-calibration.md`'s addendum), or (b) redesign T1/T3 as well
   if they also turn out to need an unreasonably large budget.
2. **A decision on T7.** Either redesign it to a bounded, achievable scope
   (recommended, given 0/11 recall held constant across an 8->16->25 step
   escalation -- a real signal more budget alone will not fix it) or
   accept a much larger budget specifically for it with evidence that
   choice actually converges. This is a task-quality decision, not this
   document's to make -- flagged here as a blocking dependency.
3. **Re-run this same non-scored calibration process** against whatever
   new `max_steps`/task-set is chosen, and only then reissue this file
   with `max_steps` and the task set both marked genuinely FROZEN.

## Gates independently confirmed ready by this pass (do not need
## re-verification in a future pass, only the two items above do)

See `final-readiness-review.md` for the full gate-by-gate accounting
(sections A-P). Confirmed with real evidence this pass: clean isolated
worktree path, real MCP server start, real MCP client connect, real
Stealth retrieval works, retrieval correctly excludes engineering/test
fixtures for normal queries, a legitimate admitted procedure is
retrievable (and correctly ranked, and correctly invisible under the
strict `require_verified=True` default -- both directions proven), a
real negative-control query correctly abstains, provider/timeout handling
is robust and tested, metrics capture (tokens/model_calls/tool_calls/
wall_clock/retries/files_touched/task_success/correctness) all confirmed
working end to end, cost correctly reported unavailable rather than
fabricated, no secrets recorded, no frozen baseline (`main`,
`evaluation-suite`, `better-ways-candidate-results`,
`better-ways-admission`) modified.
