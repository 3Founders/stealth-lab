# HTN relocation and hyperparameters

Type: grilling
Status: resolved
Blocked by: 05

## Question

How does the HTN/DAG engine move into `backend/` as the execution layer downstream of procedural memory — and which of its structural flaws get fixed in the move?

The repo owner's instruction was explicit: move it to the backend, **and** fix structural flaws rather than relocating them. spec.md separately forbids replacing the engine and requires repositioning it as the execution/planning layer downstream of procedural memory.

The relevant existing facts:

- The engine is `experiments/swebench_pro/htn_agent.py`, 1884 lines, three classes in an inheritance chain (`HTNAgent` → `AugmentedHTNAgent` → `ResearchHTNAgent`), with **zero database access**.
- **It is synchronous by design, and that is load-bearing.** `method_library.py` documents why: `run_graph_experiment.py` calls `runner.run(...)` from inside an async function, so awaiting asyncpg inside it raises "cannot be called from a running event loop." The reuse hook is therefore split into an async `_synthesize_method` call *before* the sync `run()`, stashing state on `self._pending_seed_plan`.
- **Per-run state lives on `self`.** This forces the experiment runner to execute arms sequentially even when they are independent.
- `ResearchHTNAgent` items 2–5 raise `NotImplementedError`, and `ResearchHTNAgent` is not used by the experiment runner at all — which imports `AugmentedHTNAgent as HTNAgent`.
- The engine's genuinely good properties, which must survive the move: per-node fresh message lists (context is O(local steps), not O(all steps)); validated DAG parsing that breaks cycles and drops dangling deps; the `deps` vs `requires` distinction, where failure cascades along `requires` only so independent branches still run; localized replanning; the lock-guarded `_Budget` with synchronous reservation before threads start; rich per-node telemetry; and the safety valve that discards the patch when `subgoals_done == 0`.
- Per-node telemetry currently goes to JSONL only. The one thing that reaches Postgres is failures, via `failure_capture.py`.
- **No HTN code is reachable from any HTTP endpoint or MCP tool.** The MCP `solve_task` tool runs the *flat* agent.

The hyperparameters the owner wants addressed — each currently a module constant with a measured justification in a comment: `MAX_SUBGOALS=4`, `MAX_DEPTH=2`, `MAX_METHODS=2`, `STEPS_PER_SUBGOAL=9`, `TOTAL_STEP_BUDGET=72`, `MIN_VIABLE_SUBGOAL_BUDGET=3`, `MAX_PARALLEL_NODES=4`, `PLAN_CONTEXT_MAX_NODES=6`.

Decide:

- Where the engine lives in `backend/app/`, and what its public interface is once a procedure — not a raw issue string — is the input.
- **The sync/async boundary.** Options: keep it sync and run it in a thread (`asyncio.to_thread`, which the MCP server already does for the flat agent); make it async throughout; or split it into a sync core with an async shell. Each has a different cost, and the current split-hook pattern is a workaround that should not survive unexamined.
- **Per-run state off `self`.** A run context object, or a factory per run. This is what unblocks concurrency.
- The hyperparameters: configuration object, per-procedure overrides, or derived from the procedure's own historical statistics? Note that a 3-node ceiling on decomposition is a hard cap on what any procedure can express.

Grill these:

- The three-class inheritance chain has `NotImplementedError` leaves and a subclass nobody runs. Is the right move a relocation, or a rewrite of the class structure keeping the algorithms? The owner asked for structural flaws fixed — this is the largest one.
- Every hyperparameter has a comment citing a measurement from the SWE-bench setting. **Those measurements were taken in a domain this map has just placed out of scope.** Are the values still justified, or are they now unexamined constants carried into a different workload?
- If a procedure carries its own preconditions, decomposition and expected effects, what is left for the planner LLM call to do? Does a fully-specified procedure skip decomposition entirely, making the planner the *fallback* rather than the default — and does the engine's shape support that inversion?
- The engine currently receives memory as an opaque string handed only to the planner, and node executors see none of it. If procedures are the input, does that change — and does it reintroduce the context growth the per-node isolation was built to avoid?

## Research findings (Briefs 3 and 4 — [answers3.md](../research/answers3.md), [answers4.md](../research/answers4.md))

Not an answer; evidence for whoever resolves this. Borrowed from speedup learning, adaptive
computation/metareasoning, algorithm configuration and Python asyncio practice — **nothing
measured for LLM-coding-agent execution engines**.

**15.7 — the utility problem is real, and no LLM-agent work has confronted it.** Minton's result
is confirmed: learned knowledge can *hurt* performance, because match cost grows faster than the
planning effort saved. The formula is directly usable:

```
utility(P) = (application_frequency × average_savings) − match_cost
```

Documented mitigations, all of which bear on this ticket: **utility-based retention** (delete
negative-utility procedures — see ticket 13), **selective forgetting**, and **match-cost-aware
indexing** — order procedures by match cost, cheapest first, and reorder precondition checks
within a procedure to fail fast. That last one is an architectural requirement on how the
procedure store is queried, and it belongs to whatever this ticket builds around retrieval →
instantiation.

The finding the search flagged as most significant: **the 2026 skill-library survey does not
mention the utility problem or negative transfer at all.** LLM-agent skill libraries are being
built without confronting a known, named failure mode from the previous generation of this exact
idea. Being early to confront it is a genuine position for this project — and the corollary is
that nobody has measured whether it bites for LLM agents, so it must be instrumented rather than
assumed either way.

Its quality-side counterpart is **negative transfer** (see ticket 12): retrieved procedures
constraining the system into a worse solution than fresh planning would have found. Measuring
either requires a **matched no-transfer control arm**.

**15.7 (cont.) — planner-as-fallback is phase-dependent, not a single switch.** Ticket 12's
cold-start finding lands here: while evidence is thin, **procedure retrieval should be disabled
entirely and generative planning is the default**; retrieval is enabled once enough preconditions
are actually recorded. So the inversion this ticket proposes is correct as an *end state*, but the
system passes through a phase where the planner is the default rather than the fallback. Whatever
interface this ticket defines should make that switchable rather than baking in one regime.

**15.6 — which inherited constants are actually suspect.** The transferability split is clean and
directly answers this ticket's audit question:

- **Structural limits transfer well** across task distributions — `MAX_DEPTH`, parallelism cap.
- **Budgets, thresholds and stopping criteria are highly distribution-sensitive** — `MAX_SUBGOALS`,
  retry attempts, per-subtask and total step budgets, `MIN_VIABLE_SUBGOAL_BUDGET`.

So the eight constants are not uniformly suspect: the second group is where the SWE-bench-derived
measurements have plausibly gone stale, and the first group can likely be carried over. Cheap
re-validation protocol offered: empirical-Bayes shrinkage plus A/B (adaptive vs. static) on a
held-out set, rather than a full re-tuning sweep.

**15.4 — adaptive budgets, with a real sample-size requirement.** Adaptive per-instance budgets
beat well-tuned static ones by **~10–20%** on heterogeneous task distributions (metareasoning
work reports ~15–25% on long-horizon tasks). But per-item derivation needs **~30–50 executions per
procedure type** before it outperforms a global default — which is far more than early operation
will supply. The named cold-start pattern is **empirical-Bayes shrinkage**: shrink per-procedure
estimates toward the global mean, with the weight moving toward the per-procedure estimate as
evidence accumulates. That is a single mechanism covering both regimes, so the config/per-item
choice this ticket poses is arguably a false dichotomy.

**15.2 — sync core, async shell, confirmed.** The production pattern is exactly the third option:
**functional core / imperative shell** with all I/O at the boundary (sans-I/O protocol design is
the related idea). Fully converting to async is rejected as disruptive; a worker thread is fine
for I/O-bound work like LLM calls but contends on the GIL for CPU-bound work.

For the specific failure this ticket describes — a sync function needing to *initiate* async work
mid-execution — two patterns survive cancellation and timeouts cleanly: **pre-fetch everything at
the boundary** (which is what the current `_pending_seed_plan` workaround is a crude instance of),
or a **queue-based request/response channel** between core and shell.
`run_coroutine_threadsafe` works but carries cancellation-semantics pitfalls: `CancelledError`
must be propagated rather than swallowed, and `future.result(timeout)` must handle both
`TimeoutError` and `CancelledError` explicitly. Any shared state touched from the worker thread
needs real thread-safe primitives.

## Answer

**Location and interface.** The engine moves to **`backend/app/execution/`** — not
`backend/app/services/`, because it is an engine with its own control flow, not a stateless
service function, and putting it in `services/` would invite the flat-module pattern that
directory already suffers from. Public interface is one entry point: **procedure + bound
parameters + state → execution record**. That signature is what makes it a consumer of the
substrate rather than a peer of it, which is the repositioning spec.md asks for.

**Sync core, async shell.** Fully converting ~1900 lines to async is disruptive and buys nothing
the boundary pattern doesn't. A worker thread alone is acceptable for I/O-bound LLM calls but
leaves the same structural problem. So: **the engine stays synchronous and does no I/O; all I/O
happens at an async shell at the boundary, pre-fetched before the run begins.**

This turns today's workaround into the design. `ResearchHTNAgent`'s `_pending_seed_plan` — an
async pre-step stashing results on the instance because `HTNAgent.run()` is sync and cannot await
— is currently a hack apologised for in its own docstring; under this decision it becomes *the
pattern*, applied deliberately and uniformly. If mid-run async ever becomes unavoidable, the
documented options that survive cancellation cleanly are a queue-based request/response channel,
or `run_coroutine_threadsafe` with explicit `CancelledError` and `TimeoutError` handling — the
latter is a known pitfall source and is not the default choice.

**Restructure, not relocation.** The owner's standing instruction is that structural flaws get
fixed in the move. The flaw is the three-class inheritance chain — `HTNAgent` →
`AugmentedHTNAgent` → `ResearchHTNAgent` — where each subclass overrides `_schedule`, making
scheduling behaviour a function of which class you instantiated. Replaced by **one engine with a
pluggable scheduler strategy** (sequential, speculative-parallel), so the two schedulers become
values rather than subclasses. Per-run mutable state moves off `self` into an explicit
**`RunContext`**, which is also what makes the engine safe to reuse across runs.

**Hyperparameters split by transferability — the eight constants are not uniformly suspect.** Each
carries an in-source comment citing a measurement from a benchmark domain now scoped out of this
project, so the audit question is real. The literature's split is clean and decides it:

- **Structural limits transfer** across task distributions — `MAX_DEPTH`, parallelism cap. Carried
  over as sound, static config.
- **Budgets, thresholds and stopping criteria are highly distribution-sensitive** —
  `MAX_SUBGOALS`, retry attempts, per-subtask and total step budgets,
  `MIN_VIABLE_SUBGOAL_BUDGET`. Config with per-procedure override, and **marked provisional /
  unvalidated for this domain** at the point of definition, not in a comment elsewhere.

Adaptive per-instance budgets beat well-tuned static ones by ~10–20% on heterogeneous
distributions, so this is worth doing eventually — but per-item derivation needs **~30–50
executions per procedure type** before it outperforms a global default, which milestone 1 will not
supply. The named cold-start pattern is **empirical-Bayes shrinkage** (shrink per-procedure
estimates toward the global mean, weighting toward the per-procedure estimate as evidence
accrues), which incidentally makes the config-versus-derived choice this ticket posed a false
dichotomy — one mechanism spans both regimes. Deferred to fog on sample-size grounds.

**The planner is phase-dependent, not permanently a fallback.** The proposed inversion — stored
procedures retrieved and instantiated, planner only for novel situations — is correct as an *end
state* and supported in principle (a fully-specified procedure with satisfied preconditions and
deterministic effects leaves the planner nothing to do). But ticket 12's cold-start decision lands
directly here: while evidence is thin, **procedure retrieval is disabled and generative planning
is the default**. The system therefore passes through a phase where the planner is primary, and
the interface must make that **switchable rather than baking in either regime**. Execution
monitoring supplies the third trigger: replan when preconditions fail *at execution time*, not
only at selection time.

**Match-cost-aware ordering, because of the utility problem.** Minton's result — learned knowledge
can make a system net *slower*, because match cost grows faster than planning effort saved — bears
directly on how this engine queries the procedure store. Two documented mitigations are
architectural and belong here: **order candidate procedures cheapest-to-match first**, and
**order precondition checks within a procedure to fail fast**. The retention side of the mitigation
(delete negative-utility procedures) lives in ticket 13.

Worth stating plainly because it shapes expectations: **no LLM-agent work has confronted the
utility problem** — the 2026 skill-library survey does not mention it or negative transfer at all.
So this is not a solved risk being managed, it is an open one being instrumented.

**Memory reaching node executors: unchanged in milestone 1.** Memory remains an opaque string
supplied to the planner. Making it structured and available to node executors is a real change
with no forcing requirement yet, and adding it speculatively would widen the interface before
anything needs the width.

**Provenance of this answer.** Literature-grounded: sync-core/async-shell as the production
pattern and its cancellation pitfalls, the structural-versus-budget transferability split, the
~10–20% adaptive-budget gain and ~30–50-instance sample requirement, empirical-Bayes shrinkage as
the cold-start pattern, and the utility problem with its match-cost mitigations. Judgement calls:
the `backend/app/execution/` location, the strategy-object restructure, and keeping memory opaque.
Flagged absent by the research: **no study measures asyncio integration for agent engines, budget
adaptation for agent procedures, or retrieval-then-instantiate versus generative planning for
coding agents** — the inversion is supported in principle and unmeasured in practice.
