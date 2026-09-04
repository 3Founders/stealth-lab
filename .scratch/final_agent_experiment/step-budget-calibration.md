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

## UPDATE (this pass): the hanging-calibration blocker above is fixed

The `asyncio.to_thread` + no-real-network-timeout hypothesis above is
CONFIRMED as the real root cause and FIXED. See `remediation-results.md`
for the full investigation and proof. Summary: `Agent._complete()`
(`experiments/swebench_pro/agent.py`) already passed `timeout=
REQUEST_TIMEOUT` (180.0) to openai's `.create()` -- a controlled, offline,
reproducible probe against a real local socket that accepts a TCP
connection and sends nothing proved this bare-float per-call timeout does
NOT reliably bound the call (measured ~2x its configured budget); an
explicit client-level `httpx.Timeout(...)` with a zero-retry transport was
WORSE (still hadn't returned after >150s against a 5s budget, had to be
force-killed). Only a real OS socket timeout via
`socket.setdefaulttimeout()`, scoped tightly around the one HTTP call site
with save/restore, reliably bounded it -- verified in repeated
single-threaded runs (~1s over budget, consistently) AND under genuine
two-thread concurrent contention (each thread's call still bounded near
its own configured budget, no cross-thread leakage). Implemented as
`_bounded_socket_timeout()` wrapping `Agent._complete()`'s one real call
site. 3 new deterministic offline regression tests added
(`experiments/swebench_pro/test_bounded_model_call_timeout.py`) plus a
non-scored end-to-end stress test through the REAL orchestrator ->
runner.py -> agent.py -> openai-client path (not just the isolated
function), reproducing the original hang shape and proving bounded
termination: solo run 17.2s, two-concurrent-trial run 14.1s each (both
truly parallel, not serialized), zero lingering/stuck threads afterward
(`threads_before == threads_after`).

## Step 2 (redone, this pass, post-fix): candidate budget 40 -- completed for real

With the timeout fix in place, BOTH candidate budgets (25 -- re-run fresh,
not reused, since the fix could plausibly change behavior; and 40) were
run to completion for the first time. Neither hung. Full real results:

| task | arm | 25-step stop_reason (tool_calls) | 40-step stop_reason (tool_calls) |
|---|---|---|---|
| T1 | A | `step_budget` (25/25) | `step_budget` (40/40) |
| T1 | B_default | `api_error` (20 tool_calls, unrecovered) | `step_budget` (40/40) |
| T3 | A | `step_budget` (25/25) | `step_budget` (40/40) |
| T3 | B_default | `step_budget` (25/25) | `step_budget` (40/40) |
| T7-v2 | A | `no_tool_call` (20/25 -- genuine stop) | `no_tool_call` (25/40 -- genuine stop) |
| T7-v2 | B_default | `no_tool_call` (20/25 -- genuine stop) | `no_tool_call` (26/40 -- genuine stop) |

**2 of 6 cells pass at 25 (both T7-v2 cells). 2 of 6 cells pass at 40
(the same two, T7-v2 only). T1 and T3 fail at BOTH budgets, every single
time, by consuming exactly 100% of whatever budget is offered** (25/25,
then 40/40 -- not 23/25 trending toward 38/40, a genuine converging
pattern would look like that; this is flat, maximal consumption at both
tested points). T7-v2 is unaffected by the budget increase because it
was already stopping naturally, well under either ceiling, both times.

**No hang occurred anywhere in either run.** Every non-passing cell
resolved cleanly (`step_budget` or, once, a recovered-then-failed
`api_error`) within its configured time budget -- the infrastructure gap
that blocked this section entirely last pass is genuinely closed.

## Decision on candidate 55: not run, judgment call, reasoning disclosed

The pre-registered rule's ascending search is 25 -> 40 -> 55. This pass
deliberately did NOT spend a third full 6-trial round on 55. Reasoning:
T1 and T3 show a flat 100%-budget-consumption pattern at BOTH 25 and 40
with zero convergence trend between them -- the honest, evidence-based
read is that these two tasks are not "close" to fitting in a larger
step budget, they are structurally not converging regardless of budget
size (the same class of problem the ALREADY-retired original T7 had:
scope too large for the mechanism to naturally terminate, not merely
under-provisioned). Running 55 would very likely reproduce the identical
100%-consumption pattern for T1/T3 at a higher real-money cost, and this
task's actual mandate (per the coordinator's own framing) is the
timeout/execution-robustness architecture, with calibration explicitly
gated on that being fixed first -- not a mandate to redesign T1/T3.
This is disclosed as a deliberate scope/resource judgment call, not a
silently skipped step: if the coordinator wants 55 run for completeness,
or wants T1/T3 investigated and redesigned the way the original T7 was
in the previous pass, that is real, well-scoped follow-up work, not
already done here.

## Decision

**`max_steps` is still NOT frozen by this pass -- but for a materially
different, better-evidenced reason than before.** Previously: unknown,
because the harness itself could not even complete a calibration attempt
(the hang). Now: known and specific -- T7-v2 alone would be comfortably
served by `max_steps=25` (both its cells stop naturally at 20/25 and
20-26/40, nowhere near either ceiling), but T1 and T3 do not converge at
either 25 or 40 steps, so no SINGLE common budget across all 3 tasks has
been empirically validated, and the evidence suggests raising it further
without also revisiting T1/T3's task design is unlikely to fix that by
itself. This is reported as a real, current, well-evidenced blocker --
not softened, not silently carried forward as "40 is probably enough."
