# Plan: wire local-first `.stealth/run.md` state into `find_best_way`'s real `full_run` path

## Goal
Postgres stays the source of truth (durable Procedures/Claims/Evidence, the
historical record of past Executions) but stops being the *live* source
during an active run. Live per-node progress moves to a plain local file;
Postgres gets one flush write at the end.

## Already built and proven (automationbench_experiment/, not yet wired into the real server)
- `local_dag_state.py` -- `.stealth/run.md` format: `N. [ ] <goal>  deps=[...]`.
  `create_run`, `read_run`, `mark_node`, `next_actionable_node` (first pending
  node whose deps all succeeded), `is_complete`. Pure file I/O, no Postgres.
- `local_implementations.py` -- zero-LLM-cost "is there a cheaper way"
  lookup, keyed on EXACT normalized goal text (no fuzzy matching -- a
  near-miss gets a real LLM call, never a wrong replay). Conservative:
  only ever resolves when a goal's local record is 100% clean
  (`successes == uses`); one failure under that exact goal text stops it
  being trusted again.
- `dag_driver.py` -- the loop: per actionable node, check the local cache
  first (replay if clean); otherwise run a real bounded SLM turn
  (facts-carrying between steps, the fix from the earlier failed
  ablation) and buffer its tool-call sequence. **Buffered, not cached
  yet** -- only flushed into `local_implementations` once the WHOLE
  run's real grader result (`task_completed_correctly`) is known, so a
  failing run poisons nothing. Verified live: a failing run correctly
  cached zero nodes; a cache hit correctly replays with 0 tokens / 0 LLM
  calls.
- Real, live-tested finding this design already survived: static
  system-prompt injection of a full procedure/checklist (tried 3 separate
  times, different models/domains) never helped and often cost MORE
  tokens for the same or worse outcome. The per-node, one-line-at-a-time
  + facts-carrying + local cache design is the one that actually showed
  real savings (verified: 0-token cache replay).

## What's NOT done yet -- this step
Wire the same idea into the REAL production path: `find_best_way`
(`app/mcp_server/server.py`), `mode="full_run"`, the branch that currently
does, per node:
```
compiled_plan = compile_plan(...)          # unchanged -- still the real, frozen DAG
compiled_plan = await _bind_plan_to_registry(...)
compiled_plan, _ = await persist_compiled_plan(pool, compiled_plan)
# then, per node, today: a live execution_run_nodes write happens via the
# durable substrate (create_pending_run / report_node_progress-equivalent)
```

### Planned change
1. **Compile-time stays identical.** `compile_plan()` / `persist_compiled_plan()`
   still produce the real, frozen `TaskGraph`/`PlanNode[]` in Postgres --
   nothing about *planning* moves local. Only *live execution progress* does.
2. **Render the compiled plan to `.stealth/run.md`** once, at the start of
   `full_run`, using `local_dag_state.create_run()` (already built) fed from
   `compiled_plan.task_graph.nodes` (goal + deps already present on
   `PlanNode`).
3. **Per node**, inside the existing `run_node()` closure (server.py
   ~line 1800): check `local_implementations.resolve_local_implementation()`
   first. On a clean hit, skip the `Agent.run()` call entirely for that
   node. Otherwise run `Agent.run()` as today, then call
   `local_dag_state.mark_node()` (not a live Postgres write) with the
   result.
4. **At run completion** (success or failure), a single new flush function:
   - Writes the final `execution_runs` row status/outcome (one write,
     not a live trail).
   - Writes final (not per-step) `execution_run_nodes` rows, one per node,
     from the finished `run.md`.
   - If the run passed: flushes each node's buffered tool-call sequence
     into `local_implementations` (already the behavior in `dag_driver.py`)
     AND, if genuinely durable/reusable, promotes it toward a real
     Postgres-registered Implementation via the existing
     `implementation_registry` (not invented here -- reuses what
     `resolve_implementation`/`get_implementation_capability` already read).
   - Model on `app/stealth/exploration.py`'s `close_exploration`: capture
     something durable ONLY at a real completion point, and ONLY if there's
     something worth keeping -- never mid-flight, never unconditionally.

### Explicit tradeoff (already discussed and accepted)
Crash-safety via `execution_run_nodes.lease_expires_at`/`resumable` no
longer applies to progress *within* a run -- if the process dies mid-run,
nothing durable exists to resume from except what `.stealth/run.md` itself
recorded on disk (which survives a crash, since it's a real file, just
isn't visible to any OTHER Postgres-reading caller until the flush). This
was agreed to be an acceptable trade for this experimentation path, not
applied to the production debate/approval flow.

### Touch points in server.py (for review before editing)
- ~line 1780-1830: `full_run`'s node-execution closure (`run_node`,
  `Agent(client, model, ...)`, `node_runs`/`node_notes`).
- Wherever `create_pending_run` / the "durable substrate... one
  execution_run per graph" comment lives (same region) -- this still runs
  ONCE at the start (to get a real `execution_run_id` to flush into later),
  it just stops being written to per-node.
- New: a `flush_run_to_postgres(pool, run_md_path, execution_run_id, ...)`
  function, called once at the end of `full_run`, not per node.

## Order of work from here
1. Confirm this plan (this file).
2. Add a real offline test for the new flush function against a fake/local
   `run.md`, BEFORE touching `server.py` -- same discipline as everything
   else built this session (tests ship with the code, not after).
3. Make the `server.py` edit as a single, small, reviewable diff --
   replacing the per-node live-write call sites, not rewriting the
   surrounding compile/bind/persist logic.
4. Re-run the exact gemma `full_run` coding task from earlier (the
   `tasknode_driver.py` fix) against the new code path and diff the
   before/after token cost and diagnostics, the same way every other
   change this session was verified rather than assumed.
