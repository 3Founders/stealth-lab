# HTN relocation and hyperparameters

Type: grilling
Status:
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
