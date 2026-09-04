# Step-budget calibration (final pre-score remediation, section C)

## Pre-registered rule (written before this pass's own new calibration trials were run)

**Rule:** for each of the 3 final tasks (T1, T3, T7-v2) x 2 arm-configs (A,
B_default) = 6 cells, run exactly 1 non-scored calibration trial per cell
per candidate `max_steps` value (n=1/cell -- matching this whole pass's
own established real-cost practice, explicitly disclosed as low-powered,
not silently treated as more certain than it is). A cell **passes** at a
given budget if its trial reaches a genuine stop -- `stop_reason` is
anything other than `step_budget`, AND the run does not die via an
unrecovered `api_error` either (a provider-reliability failure is not
evidence the STEP budget itself is wrong, but it is also not a "pass";
see the honest caveat below). Candidate budgets are tried in ascending
order: 25 (already real data, this pass, reused not re-run) -> 40 -> 55.
The chosen `max_steps` is the smallest candidate for which **all 6 cells
pass simultaneously**. If no candidate up to 55 satisfies all 6
simultaneously, the task set itself is reconsidered (task complexity or
statement scope), not silently given a bigger number or different arms
different budgets.

X is deliberately **not** a percentage-of-many-trials bar here (n=1/cell
makes a "≥X%" framing over multiple trials meaningless) -- it is a binary
per-cell "did it reach a real stop, yes/no" check, applied identically to
every cell, with the ascending-budget search itself acting as the honest
escalation rule.

## Step 1: reused, already-real 25-step data (this session, prior pass)

| task | arm | tool_calls used | stop_reason | pass? |
|---|---|---|---|---|
| T1 | A | 19/25 | `api_error` (5 real recoveries, then unrecovered) | NO (provider reliability, not budget -- see caveat) |
| T1 | B_default | 25/25 | `step_budget` | NO |
| T3 | A | 25/25 | `step_budget` | NO |
| T3 | B_default | 25/25 | `step_budget` | NO |
| T7 (original) | A | 25/25 | `step_budget` | NO |
| T7 (original) | B_default | 25/25 | `step_budget` | NO |

**0 of 6 cells pass at 25 steps.** This is a real, previously
under-stated finding this pass's own re-audit surfaced: the earlier
summary ("5/6 hit the step ceiling") is accurate but incomplete framing --
the 6th (T1/A) did not "pass" either, it failed a different way
(provider reliability). **T1 and T3 fail the rule at 25 steps too, not
only the retired T7** -- the step-budget problem was never T7-specific.

**Honest caveat on T1/A's `api_error` outcome:** this is evidence of a
real, separate provider-reliability question (does 5 recoveries then one
unrecovered failure reflect a genuinely bad-luck run, or a systematic
under-provisioning of `MAX_RECOVERIES`/retry budget for a
longer-running task?) -- flagged here, not silently folded into "needs a
bigger step budget," since a bigger step budget does not by itself fix an
unrecovered provider error. See `final-readiness-review.md` for how this
interacts with hard gate F (execution robustness).

## Step 2: candidate budget 40 -- this pass's own new calibration attempt

**Result: could not be completed this pass, for a real, disclosed reason
-- not silently skipped, not guessed around.**

A fresh 6-cell calibration run (T1/T3/T7-v2 x {A, B_default}) was
launched against `max_steps=40`, `time_budget_s=900` per cell
(`run_budget_calibration_40.py`, `run_one_trial(..., time_budget_s=900)`).
The FIRST cell (T1/A) never completed and never produced a
`budget_exceeded` record either, despite the orchestrator's own
`asyncio.wait_for(coro, timeout=900)` wrapper around it -- it ran for
well over 900s with zero output. This was observed twice, independently,
across two separate fresh process launches (once under contention with
the parallel retrieval-calibration run, once running completely alone) --
ruling out cross-job contention as the sole cause.

**Real, disclosed hypothesis (not confirmed with a source-level fix this
pass, reported as an infrastructure finding):** `run_trial_arm_A` calls
into the real agent loop via `asyncio.to_thread` (confirmed by reading
`_run_local_node`'s implementation earlier this pass). `asyncio.wait_for`
can cancel the AWAITING coroutine when its timeout fires, but cannot
forcibly stop the underlying THREAD if that thread is itself blocked on a
real synchronous network call with no timeout of its own (e.g. an HTTP
call to GENERAL_COMPUTE that never returns and was never given its own
socket-level timeout) -- a well-known Python `asyncio.to_thread`
limitation, not a StealthLab-specific product bug. If this is the real
cause, the orchestrator's own 900s ceiling is not actually load-bearing
for arm A trials today.

This is itself a genuine, real finding worth carrying forward -- not
"the calibration failed," but "the calibration attempt surfaced a real
gap in this pass's own harness-level timeout enforcement," clearly
distinct from anything about the product's real retrieval/applicability
correctness (which section A/D of this pass proved solid).

## Decision

**`max_steps` is NOT frozen by this pass.** The pre-registered rule
(Step 0 above) required all 6 cells to pass at some candidate value up to
55; this pass could not even get real data for cell 1 of 6 at 40 due to
the infrastructure issue above, so the rule's own honest outcome is: not
yet decidable. 25 remains the last value with any real completed data,
and at 25, 0/6 cells passed (Step 1 above) -- so the state going into any
future scored pilot is: **no validated `max_steps` value exists today.**

This is reported as a real, current blocker -- not softened, not silently
carried forward as "25 was probably fine."
