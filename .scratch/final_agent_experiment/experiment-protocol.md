# Final Baseline vs Stealth Agent Experiment -- FROZEN PROTOCOL (v2)

**STATUS UPDATE (final pre-score remediation pass, this document's
governing pass): T7 has been retired and replaced with T7-v2 (see
`t7-review.md`, `task-set.md`) -- the original T7's 0/11-recall problem is
now understood to be a real grader/ground-truth defect (confirmed false
positive: `_now_iso`) plus a separate scale problem, not primarily a
step-budget question. The retrieval negative-control gap (an irrelevant
query still returning all 3 admitted procedures at low similarity) has
been investigated and addressed with a calibrated similarity floor -- see
`retrieval-calibration.md` for the calibration set, measured score
distribution, and the frozen decision rule. `max_steps` status: see
`step-budget-calibration.md` for this pass's own fresh 40-step calibration
against the FINAL task set (T1/T3/T7-v2) -- this status line is updated
below once that data is in. Do not treat this file as authorizing a scored
run until `final-readiness-review.md`'s own hard-gate table says READY.**

This document supersedes `protocol.md` for the items it updates; it does
NOT retroactively alter `protocol.md`'s own text (per the coordinator's
explicit instruction against silently rewriting frozen history) --
`protocol.md` remains the original design record, this file is the
current-state amendment.

## What changed from `protocol.md` (v1)

| item | v1 (`protocol.md`) | v2 (this file) | status |
|---|---|---|---|
| `max_steps` | 8 (pilot), 25 (recommended, unverified) | **UNRESOLVED** -- 25 AND 40 both tested to completion (hang bug fixed this pass); T7-v2 passes both, T1/T3 consume 100% of budget at both with no convergence trend (`step-budget-calibration.md`) | **NOT FROZEN** |
| Execution-robustness / hang-safety | orchestrator could hang indefinitely on a non-responding provider call (`asyncio.wait_for` can't kill a blocked thread) | **FIXED** -- real OS socket timeout, scoped + save/restore, proven via offline tests + live stress test (solo + concurrent) | frozen (this fix itself is not going to be reverted) |
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

---

## FINAL PRE-SCORE REMEDIATION PASS -- outcome (supersedes the "What must
## happen" list above)

1. **`max_steps`** -- **still NOT resolved, but the reason has changed.**
   The previous blocker (the orchestrator's own timeout enforcement gap --
   a calibration trial could hang indefinitely, `asyncio.wait_for`
   cancelling the awaiting coroutine but not the underlying blocked
   thread) is **FIXED and proven**: a real OS socket timeout
   (`socket.setdefaulttimeout()`, scoped around the one real HTTP call
   site with save/restore) now reliably bounds every model call, verified
   via 3 new offline regression tests plus a non-scored end-to-end stress
   test through the real orchestrator path (solo + concurrent-contention
   runs, both bounded, zero lingering threads). With that fixed, BOTH 25
   and 40 were run to completion for real this pass. Result: T7-v2 passes
   cleanly at both (well under either ceiling); **T1 and T3 fail at BOTH
   25 and 40, consuming exactly 100% of the budget every time with zero
   convergence trend between the two data points** -- strong evidence this
   is a task-design/scope problem for T1 and T3 (the same class of issue
   the original T7 had), not a budget-size problem. Candidate 55 was
   deliberately not run this pass (disclosed judgment call, see
   `step-budget-calibration.md`'s "Decision on candidate 55" section) --
   the flat non-convergent pattern at 25->40 makes it unlikely to resolve
   by itself, and redesigning T1/T3 is outside this pass's mandate (fixing
   the timeout/execution-robustness architecture). **This is now the sole
   remaining blocker, and it is a task-design question, not an
   infrastructure one.**
2. **T7** -- **resolved.** Retired and replaced with T7-v2 (bounded,
   75-file, pure-AST grader, zero cross-file-call ambiguity). See
   `t7-review.md`, `task-set.md`.
3. **Retrieval negative-control abstention** -- **resolved.**
   `_MIN_RELEVANCE_SIMILARITY=0.45`, calibrated from real data (16 of a
   planned 26 queries -- see `retrieval-calibration.md` for the honest
   accounting of why not all 26), implemented in
   `applicability.py::find_applicable_procedures`, both directions
   live-tested and passing.
4. **Execution-robustness / hang-safety** -- **resolved, newly this
   pass.** See `remediation-results.md` for the full investigation,
   fix, and proof.

**This document is still NOT authorizing a scored run** -- `max_steps`
remains unresolved, but the blocker has moved from "the harness can't even
run a calibration trial without risking a hang" to "T1 and T3 don't
converge at any tested budget, likely needing task redesign like the
original T7 did." T7, retrieval abstention, and execution robustness are
all now genuinely closed. See `final-readiness-review.md`'s
ADDENDUM FINAL READINESS block for the complete gate table.

## STATUS UPDATE 2: task set and step budget frozen (this pass)

This supersedes the item above for `max_steps` and the T1/T3 status; it
does not rewrite the frozen history above it.

1. **T1 and T3 -- resolved.** Investigated once (bounded, single pass,
   no further iteration authorized or taken), both **REPAIRED** (not
   retired, not redesigned-for-difficulty): T1's grader/scope bundled an
   unnecessary exhaustive-enumeration requirement onto an otherwise valid
   lookup question; T3's grader used a naive substring scanner that
   miscounted comment/docstring mentions as real call sites, penalizing
   genuine correct renames. Originals preserved unchanged as `T1`/`T3`
   (historical record, not deleted). Live final set: `T1-v2`, `T3-v2`,
   `T7-v2`. Full evidence and fix details in `remediation-results.md`
   ("Part T1/T3") and `task-set.md`.
2. **`max_steps` -- FROZEN at 40.** A 6-cell, 1-trial-per-cell, non-scored
   safety check of the final set (`T1-v2`/`T3-v2`/`T7-v2` x {A,
   B_default}, `run_final_set_safety_check.py`) confirmed zero hangs (max
   observed 729.6s against the 900s ceiling) and no runaway consumption
   beyond the configured budget. See `step-budget-calibration.md`'s
   final "Decision" section for the full per-cell table and reasoning.

**This document now authorizes the final scored pilot to proceed** --
task set frozen (3 tasks: `T1-v2`, `T3-v2`, `T7-v2`), `max_steps=40`
frozen, retrieval abstention, execution robustness, and corpus
contamination all previously resolved and re-confirmed unchanged this
pass. See `final-readiness-review.md`'s FOURTH/FINAL addendum for the
complete gate table.
