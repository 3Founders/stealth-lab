# Architecture inventory

Type: task
Status: resolved
Blocked by:

## Question

Produce the written architecture inventory — spec.md's FIRST DELIVERABLE items 1–9 — as `.scratch/memory-substrate/inventory.md`, so every later ticket cites it rather than re-deriving the codebase from scratch.

This is a `task` ticket: nothing here is decided, but the representation tickets are blocked until the evidence is written down in one place.

Cover, each claim carrying a `file:line` citation:

1. **Repository architecture map** — the two disjoint code universes (`backend/` and `experiments/swebench_pro/`), what lives in each, and the one-directional import bridge between them.
2. **Current data model map** — all 23 tables across `backend/db/01`–`10`, the enum types, the bi-temporal columns, and the pydantic mirrors in `backend/app/models/`.
3. **Current execution flow** — `ExecutionHarness` (single skill, one `traces` row) vs the HTN scheduler in `experiments/swebench_pro/htn_agent.py`.
4. **Current memory/reuse flow** — `method_library.py`, `reuse_detection.py`, `subtask_reuse.py`, `dedup.py`, and which of them any default configuration actually exercises.
5. **Current retrieval flow** — `HybridRetriever` (vector + FTS + RRF + bounded graph expansion), `hierarchy.py` beam descent, `call_graph.py` reachability, `agent_search.py`.
6. **Current evidence/provenance flow** — the `provenance` enum, `episode_links`, `claims.py`, `failure_capture.py`, and the fact that there is no evidence table.
7. **Current HTN/DAG flow** — `Node`, `parse_dag`, both schedulers, `deps` vs `requires` semantics, replanning triggers, the `_Budget` lock, per-node telemetry.
8. **Current API flow** — all 8 routers and 21 endpoints, `deps.py` scoping, and the MCP server's 7 tools.
9. **Current test coverage** — the 42 test files and, more importantly, the untested subsystems.

Then the part that makes it useful rather than merely descriptive:

10. **Exact gaps relative to the target architecture** in spec.md — what is missing, what is present but shaped differently, what is present and directly reusable.

Also record the concrete defects found during inspection, since several tickets depend on them:

- `embedding_joint` is accepted and validated by `retrieval.py` but is created by no DDL file in `backend/db/`.
- `ProvenanceSource` in `backend/app/models/ontology.py` omits `public_generated`, which the database has and `knowledge_update.apply_generated()` writes — so hydrating those rows through `from_row()` raises.
- `main.py` builds a pool via `create_pool()` (which does not set the module global) and shuts down via `close_pool()` (which closes the module global), so the live pool is never closed.
- The documented setup loop `for f in db/0*.sql` silently skips `10_code_sourced_agents.sql`.
- `tenant_id` exists on every table and is filtered by zero queries.

Do not propose changes in this document. It is evidence, not a plan.

## Answer

Written to [inventory.md](../inventory.md), covering all 10 items plus the concrete defects.
Highlights that reshape later tickets:

- **`claims.py` already implements a claim-as-`knowledge_node` primitive** (`node_type='claim'`,
  `truth_state` IN/OUT in `properties`, `CLAIM_OF`/`SUPERSEDES`/`CONTRADICTS` edges, history
  preserved on supersession) — this is a real, tested, TMS-adjacent foundation, not a
  greenfield gap. Ticket 03 (claim-representation) should evaluate extending this, not
  designing from scratch.
- **Procedure ≠ HTN is currently violated structurally**: `method_library.py` stores a
  procedure as an HTN decomposition (`{id, goal, deps}`) tagged onto a `task_nodes` row —
  today a procedure *is* an HTN plan. Confirms ticket 05 is the sharpest open gap.
- **Locality is largely already built**: `hierarchy.py` (bottom-up tree, beam-search descent)
  + `retrieval.py` (RRF + bounded graph expansion) already implement most of spec.md's
  retrieval hierarchy. Ticket 14 should scope to integration/gaps, not a rebuild.
- **Agent Store is a complete, reusable match** for spec.md's AGENT concept (full lifecycle,
  review states, `runnable` flag decoupled from approval).
- Four concrete defects confirmed by direct inspection (not just cited from other tickets):
  the `embedding_joint` ghost column, the `public_generated` enum/model drift, a pool
  double-management bug in `main.py`/`db/session.py` (`close_pool()` never actually closes
  the pool `main.py` created), and setup docs that stop at `05_decomposition.sql` leaving
  `06`–`10` undocumented.
- MCP server exposes 8 tools, not 7 (verified by grep); API surface is 8 routers / 21
  endpoints including `/health` — both counts confirmed exactly as this ticket's draft
  expected otherwise.
