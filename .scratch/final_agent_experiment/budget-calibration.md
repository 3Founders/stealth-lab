# Step-Budget Calibration

## Current 8-step failure pattern (from the scored pilot)

All 12 scored pilot trials (T1/T3/T7 x 2 trials x {A, B_default}) at
`max_steps=8` that reached a real stopping point (i.e. excluding the 2 T7
crashes fixed separately in `remediation-results.md`) hit
`stop_reason=step_budget, tool_calls=8` -- every single one exhausted the
budget mid-exploration, none reached `finish`/wrote an answer file.
Wall-clock ranged 7.3s-136.2s depending on task/arm.

## Calibration method

Ran 6 NON-SCORED calibration trials (T1/T3/T7 x {A, B_default}, 1 trial
each) at `max_steps=16` -- reusing `orchestrator.py`'s real `run_one_trial`
machinery (real isolated disposable worktrees, real MCP server for B
trials, real model calls), written to `.scratch/final_agent_experiment/
calibration/` (never `trials.jsonl`). Full raw records in
`calibration/raw/` and `calibration/calibration_max_steps_16.jsonl`.

## Observed steps needed per task/arm at max_steps=16

| task | arm | wall-clock | `tool_calls` (steps used) | `stop_reason` | outcome |
|---|---|---|---|---|---|
| T1 | A | 160.5s | 16 (ceiling) | `step_budget` | no answer file |
| T1 | B_default | 57.6s | 16 (ceiling) | `step_budget` | no answer file |
| T3 | A | 17.6s | 16 (ceiling) | `step_budget` | no answer file |
| T3 | B_default | 115.9s | 16 (ceiling) | `step_budget` | no answer file |
| T7 | A | 102.3s | 16 (ceiling) | `step_budget` | no answer file |
| T7 | B_default | 727.8s | 16 (ceiling) | `step_budget` | 0/11 ground-truth items matched |

**6 of 6 calibration trials STILL hit the step ceiling at 16 -- doubling
the pilot's budget did not get a single (task, arm) cell to a natural
stop.** This is a real, honest, somewhat surprising finding: the problem
is not simply "8 was a little low." No arm was favored or disfavored in
this outcome (0/6 across both arms identically) -- this calibration
decision is not being made based on which arm performs better, since
neither arm converged even once.

T7/B_default's own verification detail is the most informative single data
point: the task asks the agent to find 11 distinct
function-definition-plus-external-caller relationships across the
`backend/app/` tree. At 16 steps it had matched 0 of 11. This is a task
that inherently requires a large number of distinct read/grep operations
to answer thoroughly -- not evidence of the agent looping unproductively
(wall-clock-per-step was reasonable, ~45s/step, consistent with real
tool-call+model-latency, not a hang).

## Proposed new fixed budget

**`max_steps=25`**, applying identically to both arms A and B.

### Justification

1. **Not arbitrary -- grounded in existing precedent already in this exact
   codebase.** `Agent.__init__`'s own signature
   (`experiments/swebench_pro/agent.py`) already defaults to
   `max_steps: int = 25` -- the pilot's `max_steps=8` was a substantial,
   apparently uncalibrated deviation BELOW the agent implementation's own
   native default, not a value derived from this task set's actual needs.
2. **Bounded, not "arbitrarily huge."** 25 is close to double the 16 this
   calibration pass tested (itself double the pilot's 8), not an order of
   magnitude jump, and matches a value the agent class was already
   designed around elsewhere.
3. **Honest limitation of this calibration pass, stated plainly:** no
   calibration trial at `max_steps=25` (or higher) was run this pass --
   doing so would have meant a THIRD real-money calibration round, and the
   6-trial round at 16 already gives a clear, unambiguous signal (0/6
   converged) that doesn't require further probing to justify moving to a
   meaningfully larger, precedented value rather than guessing again at an
   arbitrary intermediate number. **This means 25 is a well-justified
   recommendation, not an independently pilot-verified sufficient value.**
   The first real trials run at `max_steps=25` (whenever the next scored
   pilot/pass is authorized) should be watched closely: if T1/T3/T7 STILL
   uniformly hit the ceiling at 25, that would be strong evidence the
   problem is not (or not only) budget size at all, but task design/scope
   (see below) -- a real possibility this pass could not rule out.

### A real, related caveat: this may be partly a task-design issue, not purely a budget one

Given that even DOUBLING the budget (8->16) produced literally zero
newly-converged cells, it is possible that some or all of T1/T3/T7 (as
currently worded in the frozen `tasks.jsonl`) are simply scoped too
broadly for efficient completion in any modest step budget, independent of
the exact ceiling chosen -- T7 in particular (11 distinct symbol/caller
pairs to find and report) is inherently a large-surface exploration task.
This is flagged explicitly in `remediation-results.md`'s classification
table as a genuine "experiment design problem" component alongside the
"calibration/infrastructure" framing, rather than assuming a single-value
budget fix definitely resolves it.

## Frozen protocol amendment

Per the coordinator's explicit instruction not to silently rewrite the
original protocol text: `protocol.md` is UNCHANGED by this pass. This
document is the calibration decision record. Before any future scored
pilot/matrix run, `protocol.md` section 3 ("Model, configuration, budgets")
should be explicitly amended to read `max_steps=25` (currently states 8,
the value calibrated as insufficient here), with a dated note pointing back
to this document -- not done as part of this remediation pass, since no
new scored run is being authorized here.
