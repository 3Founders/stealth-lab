# Architecture inventory

Evidence document for [Architecture inventory](issues/01-architecture-inventory.md). Every
claim below carries a `file:line` citation. No proposals here — see the map's tickets for
those.

## 1. Repository architecture map

Two disjoint code universes, one narrow one-directional bridge:

- **`backend/app/`** — the FastAPI service (`backend/app/main.py:34`). Raw SQL over asyncpg
  (`backend/app/db/session.py`), no ORM, no Alembic. Owns the ontology
  (`knowledge_nodes`/`task_nodes`/`edges`/`episodes`/`episode_links`/`traces`), the Agent
  Store, retrieval/hierarchy/reuse services, the debate/loop subsystem, and 8 API routers.
- **`experiments/swebench_pro/`** — a standalone SWE-bench-Pro benchmark harness. Owns
  `htn_agent.py` (the entire HTN planner/scheduler/executor/replanner — there is no
  equivalent in `backend/app/`), the flat `agent.py::Agent`/`RepoSandbox`, `graph_memory.py`,
  `graph_ingest.py`, `run_graph_experiment.py`.
- **The bridge is one-directional and narrow**: `experiments/swebench_pro/htn_agent.py:46`
  does `from app.services import code_index` — the experiment harness depends on the backend
  app, never the reverse. `backend/tests/test_htn_agent.py:23-26` and
  `test_htn_node_telemetry.py:29-32` reach into `experiments/swebench_pro/` via
  `sys.path.insert`, so HTN-agent tests live in `backend/tests/` despite testing
  `experiments/` code — the only place the two trees touch inside the test suite.
- **`backend/app/mcp_server/`** — a *third*, separate ASGI app (`server.py:170,209`, its own
  port 8765), not mounted into `main.py`'s FastAPI app. It imports from both universes: the
  backend's own services (`KnowledgeUpdater`, `decompose_task`, `retrieve_precedent`) and,
  via `sys.path.insert` (`server.py:80-84`), `experiments/swebench_pro/agent.py`'s **flat**
  `Agent`/`RepoSandbox` — explicitly not `HTNAgent` (confirmed by the import line and the
  docstring at `server.py:435`). Nothing in the live request path (`backend/app/api/` or
  `mcp_server/`) invokes the HTN planner/executor today.

## 2. Current data model map

23 tables across `backend/db/01_ontology.sql` through `10_code_sourced_agents.sql`
(hand-numbered, idempotent raw SQL — `CREATE TABLE IF NOT EXISTS`, `ADD COLUMN IF NOT
EXISTS`, `DO $$ ... EXCEPTION WHEN duplicate_object`), no Alembic anywhere in the repo.

| File | Tables added |
|---|---|
| `01_ontology.sql` | `knowledge_nodes`, `task_nodes`, `edges`, `episodes`, `episode_links`, `traces` |
| `02_loop.sql` | `triggers`, `debates`, `debate_events`, `debate_turns`, `candidates`, `scorecards` |
| `03_access.sql` | (columns only: `visibility`/`owner_id` on `knowledge_nodes`/`task_nodes`/`edges`/`debates`) |
| `04_governance.sql` | `rate_limit_events`, `llm_spend` |
| `05_decomposition.sql` | `decompositions`; adds `provenance_source` enum value `public_generated` |
| `06_generated_files.sql` | `generated_files` |
| `07_agents.sql` | `agents`, `agent_reviews`, `agent_review_events` |
| `08a/08b_graph_workflow_execution_*.sql` | (columns only: `agents.workflow_task_ids`; adds `graph_workflow` to `agent_execution_mode`) |
| `09_seed_internal_agents.sql` | (data seed only) |
| `10_code_sourced_agents.sql` | `agent_code_reviews`; adds `agents.source_detail` |

**Core ontology** (`01_ontology.sql:22-115`, mirrored in `backend/app/models/ontology.py`,
`trace.py`):

- `knowledge_nodes` / `task_nodes`: bitemporal (`t_valid`, `t_invalid`, `t_created`,
  `t_expired`), `provenance` enum (`company_ingested|company_debate|prior_library|
  public_generated`), `embedding VECTOR(1024)`, `node_type`/`skill_ref` as free `TEXT` (not
  enums). `03_access.sql` adds `visibility`/`owner_id` to both.
- `edges`: single polymorphic table, `edge_type` enum = `REQUIRES, PRODUCES, TRIGGERED_BY,
  SUPERSEDES, VALIDATED_BY, OWNS, RESPONSIBLE_FOR`, plus a `custom_edge_type TEXT` escape
  hatch already in production use for values that never made the enum: `FAILURE_MODE`
  (`failure_capture.py:126`), `CLAIM_OF` and `CONTRADICTS` (`claims.py:36,85,125`). No FK
  constraints — deliberate, enforced in application code
  (`01_ontology.sql:61-63`).
- `episodes`: "non-lossy raw log" (`01_ontology.sql:83-91` comment), `episode_type IN
  (document, trace, debate_transcript)`, `content`/`content_ref` (Supabase Storage path for
  large payloads), `metadata`. **No bitemporal columns, no `provenance` column, no
  `embedding` column** — the only ontology table missing all three.
- `episode_links`: composite PK `(episode_id, target_id, target_table)`, links an episode to
  any of `knowledge_nodes`/`task_nodes`/`edges`.
- `traces`: a **second, unrelated "trace" concept** from the HTN agent's own run telemetry —
  OTel-GenAI-shaped (`trace.py:4-7`, pinned to `OTEL_SPEC_VERSION = "1.30.0-experimental"`),
  `trace_id, task_node_id, actor_id, action_type, outcome, cost, latency_ms,
  parent_trace_id`. Written only via `POST /v1/traces`; the HTN agent's own per-run telemetry
  (`_node_row()`, see §7) never touches this table.

**Agent Store** (`07_agents.sql`, `08a/08b`, `10_code_sourced_agents.sql`): `agents`
(`review_state` enum `ingested→under_review→pending_human_approval→approved/rejected`,
separately-tracked `runnable BOOLEAN` deliberately decoupled from approval —
`07_agents.sql:47-51`; `agent_source`, `agent_execution_mode` incl. `graph_workflow` added by
`08a`), `agent_reviews` (Layer-1 fallacy/groundedness results), `agent_review_events`
(append-only state-transition log), `agent_code_reviews` (bandit-scan results, separate from
`agent_reviews` because the fields differ).

**Claim-level nodes already exist** (`backend/app/services/claims.py`, no migration needed —
its own header explains why): a claim is `knowledge_nodes` with `node_type='claim'`;
`truth_state` (`IN`/`OUT`) rides in the `properties` JSONB, orthogonal to `t_valid`/
`t_invalid` — a claim can still exist (not deleted) while `truth_state='OUT'` (superseded).
`capture_claim()` (`claims.py:39-98`) writes one claim node plus a `PRODUCES`/`CLAIM_OF` edge
to each live `task_node`. `relate_claims()` (`claims.py:101-134`) records `SUPERSEDES` (real
enum value) or `CONTRADICTS` (rides in `custom_edge_type`, same idiom as `FAILURE_MODE`) and
flips the target's `truth_state` to `OUT` **without** setting `t_invalid` — "what we once
believed" and "what we believe now" both stay queryable from the same row. This is a
functioning, if narrow, TMS-adjacent primitive already in production code, not a greenfield
gap.

**No dedicated tables exist for**: `procedures`, `observations`, `node_telemetry`, `evidence`.
Reuse today is `task_nodes` rows tagged `created_by='htn_method_library'`,
`provenance='prior_library'`, with the decomposition (`{id, goal, deps}`) stuffed into
`io_schema` JSONB (`method_library.py:131-176`) — a procedure *is* an HTN plan structurally,
today.

Embeddings: `VECTOR(1024)` on `knowledge_nodes`, `task_nodes`, `agents` — Voyage
`voyage-3-large` (`config.py:132-133`), HNSW indexes (chosen over IVFFlat explicitly to avoid
degrading on an empty table at bootstrap, `01_ontology.sql:117-119`). Local fallback via
`mxbai-embed-large` when `settings.use_local_models` (`embeddings.py:55-75`).

## 3. Current execution flow

Two unrelated execution paths, no shared abstraction:

- **`ExecutionHarness`** (`backend/app/services/execution.py`) — single-skill, in-request,
  synchronous execution invoked directly from `agents.py:148,168,201`
  (`/v1/agents/medical-report-extraction/run`). Writes one `traces` row per invocation via
  the OTel-shaped `TraceRecord`. No DAG, no scheduling, no replanning.
- **HTN scheduler** (`experiments/swebench_pro/htn_agent.py`) — entirely separate: `_decompose()`
  (`:589`) → `parse_dag()` (`:464`, static, validates/dedupes/breaks cycles, caps at
  `MAX_SUBGOALS=4`) → `_schedule()` (`:1188`, topological, one node at a time) → `_run_turn()`
  (`:1055`) → `_run_node()` (`:806`, the leaf executor, tool-calling loop against
  `SUBGOAL_TOOLS`) → on failure, `_replan()` (`:632`). `AugmentedHTNAgent._schedule` (`:1350`)
  is a threaded, speculative-parallel variant of the same scheduler. In-memory DAG only
  (`list[Node]`), serialized to `.jsonl` result files by `run_graph_experiment.py`, never to
  Postgres. `_rehydrate()` (`:1006`) can reconstruct a `Node` graph from a prior run's
  snapshot for mid-DAG resume.
- These two never call each other and share no code. The MCP server's `solve_task`
  (`mcp_server/server.py:424-525`) is a third path again — it runs the **flat** `Agent`, not
  `ExecutionHarness` and not `HTNAgent`.

## 4. Current memory/reuse flow

Four independent mechanisms at different granularities, mostly **not wired into any default
call path**:

- **`method_library.py`** — `find_reusable_plan()` (`:72`, cosine similarity over
  `htn_method_library`-tagged `task_nodes`, lexical fallback, gated by `precondition_gate.py`)
  and `persist_plan()` (`:131`, writes a succeeded run's decomposition back). Both are
  unit-tested (`test_method_library.py`) but **not called from `run_graph_experiment.py`'s
  main loop** — `ResearchHTNAgent`'s own class docstring (`htn_agent.py:1800-1823`) states
  this explicitly.
- **`reuse_detection.py`** — coarser-granularity "has this exact problem been solved before"
  check (`find_reusable_nodes()`, vector-first with lexical Jaccard fallback, two threshold
  tiers `0.90`/`0.70`). Different question from method_library's "has this decomposition
  shape been seen before."
- **`subtask_reuse.py`** — `resolve_subtask_reuse()` (`:74`) runs *during* generative
  decomposition: batches embeddings for every proposed `create_task_node`/
  `create_knowledge_node` op in a `ChangeSet`, drops ops that match an existing node at
  ≥0.90 similarity (shrink-only, never rewrites an edge onto a real node — a stated security
  constraint). Called from `decomposition.py:419-427` ("Part C"), so **this one is live** in
  the `/v1/decompose` path.
- **`dedup.py`** — sibling-dedup within one `ChangeSet` (`dedupe_changeset_ops`), "Part A" of
  the same `decomposition.py` call chain, plus a DB-writing `merge_cluster` (tested
  separately, `test_merge_cluster.py`).
- **Evidence writes**: `failure_capture.py::capture_failure()` (`:68`, failure → a
  `knowledge_nodes` row `node_type='failure_mode'` + `OWNS`/`FAILURE_MODE` edge) is wired,
  but only into the `htn` arm's post-run block in `run_graph_experiment.py:466`, best-effort/
  non-fatal. `claims.py::capture_claim()` exists and is tested (`test_claims.py`) but has no
  confirmed production call site found in this pass.

## 5. Current retrieval flow

- **`HybridRetriever`** (`backend/app/services/retrieval.py:80`) — pgvector cosine +
  Postgres FTS (`to_tsvector`/`ts_rank`, rewritten from AND to OR-matching,
  `retrieval.py:159-172`), fused via RRF (`RRF_K=60`, `retrieval.py:35,222`), then bounded
  1-hop graph expansion (`GraphStore.traverse_from`, hierarchy-group edges/nodes excluded
  from expansion/search). Access-scoped via `AccessScope`/`visibility_predicate` throughout.
- **`hierarchy.py`** — an independent, bottom-up clustered tree over `task_nodes`/
  `knowledge_nodes` (`PARENT_OF` edges, no dedicated tree table), built by
  `build_hierarchy_for_table()` (`:263`), queried by `hierarchical_search()` (`:391`,
  confidence-adaptive beam-search descent, `confidence_floor=0.3` fallback to flat search),
  batched via `batch_hierarchical_search()` (`:508`), incrementally maintained via
  `attach_new_leaf()` (`:686`, O(1) running-mean update up the ancestor chain). This is
  already local-first, scoped retrieval — not something to build from scratch.
- **`call_graph.py`** — name-based, tree-sitter-only static call-graph reachability
  (`reachable_symbols()`, BFS up to `MAX_HOPS=2`), explicitly advisory (name collisions
  possible across files), aimed at the failure pattern where a fix needs a file only
  reachable via a call, not visible in the issue text.
- **`agent_search.py`** — the same lexical+vector+RRF pattern as `HybridRetriever`, pointed
  at `agents` instead of `task_nodes`/`knowledge_nodes` (`search_agents()`, `:87`), restricted
  to `review_state='approved'` only. Kept separate rather than folded into `HybridRetriever`
  because the result shape differs.
- **Caller**: `decomposition.py::_try_hierarchical_match()` (`:200`) tries the hierarchy tree
  first, falls through to the flat scan otherwise — the one place hierarchy and flat search
  are actually chained.
- `HybridRetriever` and `hierarchical_search` are **not blended** — `graph_memory.py` reports
  both separately, by explicit design (module docstring `:26-39`).

## 6. Current evidence/provenance flow

- `provenance` enum (`company_ingested|company_debate|prior_library|public_generated`) on
  `knowledge_nodes`/`task_nodes`/`edges` — **not** present on `episodes` or `traces`.
- `episode_links` is the only cross-reference from raw material to graph objects — it links
  an episode to a knowledge/task node or an edge, but nothing currently populates it from the
  HTN agent's run output.
- `claims.py` (§2 above) is the closest thing to a justification graph: `CLAIM_OF` edges from
  a claim to the task_nodes it supports, `SUPERSEDES`/`CONTRADICTS` edges between claims,
  `truth_state` flips without destroying history.
- `failure_capture.py` is the only evidence-capture path with a live (if narrow) call site.
- **There is no `evidence` table.** Citations/support are resolved at query time from the
  edges above and never persisted as a standalone record.
- No `confidence` column exists anywhere in the schema — the closest analogues are
  `agent_reviews.groundedness_score` (a review-quality metric, not per-claim confidence) and
  vector-search `similarity` (computed at query time, never stored).

## 7. Current HTN/DAG flow

All in `experiments/swebench_pro/htn_agent.py`:

- `Node` dataclass (`:227-287`) — `id, goal, deps, requires, status, attempts, note,
  last_evidence, path_hint, depth, parent`, plus instrumentation fields added later
  (`steps_used, budget_granted, rounds, llm_calls, prompt_tokens, completion_tokens,
  wall_seconds, started_at, ended_at, tool_calls, files_edited`).
- `parse_dag()` (`:464`, static) — validates the planner's JSON output: drops dangling deps,
  drops self-loops, breaks cycles (keeps only backward-pointing edges), caps at
  `MAX_SUBGOALS=4` (`:70`).
- **`deps` vs `requires`** (`:232-242`): `deps` is soft ordering only; `requires` is hard —
  only `requires`-dependents get cascade-blocked on failure (`_block_dependents()`, `:979`).
- Two schedulers: `HTNAgent._schedule()` (`:1188`, sequential topological) and
  `AugmentedHTNAgent._schedule()` (`:1350`, threaded/speculative-parallel via
  `concurrent.futures`, `_sandbox_lock` around file-mutating tool calls).
- Replanning triggers (`_replan()`, `:632`): on a node exhausting a scheduling round with no
  terminal call, or explicit `subgoal_failed`; LLM proposes one alternative approach; retried
  up to `MAX_METHODS=2` (`:69`).
- `_Budget` (`:290-324`) — thread-safe per-round step reservation, gated by
  `MIN_VIABLE_SUBGOAL_BUDGET=3` (`:109`) so a round isn't charged an attempt if it can't
  possibly finish.
- Per-node telemetry serialization: `_node_row()` (`:385-408`), explicitly documented as an
  **additive-only contract** so old `.jsonl` result files stay loadable; `_rehydrate()`
  defaults missing (legacy) fields to zero.
- Recursive decomposition at execution time: a leaf can call `decompose_subgoal` to split
  into 2–4 children, up to `MAX_DEPTH=2` (`:83`) — decomposition is not purely top-down.

## 8. Current API flow

8 routers, 20 endpoints (`main.py:47-54`) + `/health` (`main.py:57`) = 21 total:

| Router | Prefix | Endpoints |
|---|---|---|
| `ingest.py` | `/v1/traces` | `POST /v1/traces` |
| `approval.py` | `/v1/approvals` | `POST /{debate_id}/human-turn`, `POST /{scorecard_id}`, `GET /pending`, `GET /{scorecard_id}` |
| `admin.py` | `/v1/admin` | `POST /scan` |
| `graph.py` | `/v1/graph` | `GET /{node_id}` |
| `chat.py` | `/v1/chat` | `POST ""` |
| `decompose.py` | `/v1/decompose` | `POST ""`, `GET /pending`, `GET /{decomposition_id}`, `POST /{decomposition_id}/decide` |
| `agents.py` | `/v1/agents` | `POST /medical-report-extraction/run`, `GET /files/{file_id}` |
| `agent_store.py` | `/v1/agent-store` | `GET ""`, `GET /pending`, `POST /submit`, `POST /promote`, `POST /{agent_id}/decide`, `GET /{agent_id}` |

No episode/trace-read, claim, or procedure endpoints exist anywhere. `/v1/traces` is
write-only ingestion.

`deps.py` — `get_scope()` (trusted, unverified `X-Viewer-Id` header),
`require_trustworthy_identity()` (startup guard, raises if `private_visibility_enabled` is on
without `real_auth_enabled`), `enforce_limits()` (rate limit + cost governance).
`access.py::visibility_predicate()` is the single centralized SQL-predicate builder — no
caller hand-writes a `visibility`/`owner_id` filter.

**MCP server** — 8 tools (not 7; verified by grep, `mcp_server/server.py:212,254,322,424,
528,595,684,739`): `retrieve_precedent`, `apply_change_set` (ungated write, bypasses
approval — documented as intentional, only for `decompose_task` output), `propose_synthesis`,
`solve_task` (arbitrary caller-controlled `repo_path`, no directory allowlisting — documented
gap), `detect_conflict_trigger`, `decompose_task`, `decide_decomposition`,
`submit_approval`. Auth is a single static bearer token (`StaticTokenVerifier`,
`server.py:128-155`), HTTP-transport only. `tasks_extension.py` wraps only
`propose_synthesis`/`solve_task` in async task semantics (`TASK_AUGMENTABLE_TOOLS`, `:61`),
backed by an in-memory, non-durable, single-process task store.

## 9. Current test coverage

44 test files under `backend/tests/` (40 tracked + 4 new/uncommitted per git status:
`test_htn_agent.py`, `test_htn_node_telemetry.py`, `test_htn_real_llm_solve.py`,
`test_htn_real_swebench_instances.py`). README claims "168 tests" (`README.md:34`) — a count,
not a file count. Coverage highlights relevant to this effort: `test_claims.py` (claim
hyper-nodes), `test_method_library.py`, `test_subtask_reuse.py`, `test_hierarchy.py` +
`test_hierarchical_search_mixed_beam.py` (a real production-bug regression), `test_access.py`,
`test_ingest.py` (mocked pool only), `test_call_graph.py`, `test_precondition_gate.py`.

**Untested/weakly-tested subsystems worth flagging for later tickets**: `retrieval.py`'s RRF
arithmetic has no dedicated unit test found in this pass (only integration-style coverage via
`test_chat.py`); no test exercises `episode_links` population from a real HTN run (nothing
populates it in the first place — see §6); no test exists for the pool double-management bug
(§ Concrete defects) since it only manifests at process shutdown.

## 10. Exact gaps relative to the target architecture (spec.md)

| spec.md concept | Status |
|---|---|
| RAW EXPERIENCE / TRACE | Two disconnected "trace" concepts exist (OTel `traces` table vs. HTN `.jsonl` telemetry); neither is a durable, replayable raw-event log. **Gap.** |
| EPISODE | `episodes` table exists, matches "non-lossy raw log" intent, but lacks bitemporal/provenance/embedding columns every other ontology table has, and nothing populates `episode_links` from HTN runs today. **Partially present, disconnected from execution.** |
| OBSERVATION | No table, no service. **Gap.** |
| CLAIM / STATE | `claims.py` already implements claim-as-`knowledge_node` with `SUPERSEDES`/`CONTRADICTS`/truth_state — closer to spec.md's ask than a greenfield gap. STATE (time-sensitive claims for "current situation") has no dedicated mechanism beyond bitemporal `t_valid`/`t_invalid`. **Directly reusable foundation exists; state layer is a gap.** |
| PROCEDURE | Only `method_library`'s task_node-tagging convention — a procedure *is* an HTN plan today, violating the user's Procedure≠HTN invariant structurally. **Gap requiring a real decision (ticket 05).** |
| PROCEDURE EXECUTION / EVIDENCE | No evidence table; `failure_capture.py`/`claims.py` are the only evidence-adjacent writers, both narrow and mostly unwired. **Gap.** |
| APPLICABILITY | `precondition_gate.py`'s Rule-1 (Jaccard tag overlap) is a real but explicitly informal v1, covering only 2 of spec.md's 9 applicability factors. **Partial.** |
| LOCALITY | `hierarchy.py` + `retrieval.py` already implement most of spec.md's retrieval hierarchy (structural → graph → semantic + rerank via RRF). **Largely present — reuse, don't rebuild.** |
| HTN/DAG | Fully implemented, but lives entirely in `experiments/swebench_pro/`, decoupled from the backend/Postgres substrate — no persistence, no replay. **Present but isolated; relocation is the owner's explicit decision (ticket 15).** |
| AGENT | Agent Store (`agents`/`agent_reviews`/lifecycle) is a complete, reusable match for spec.md's AGENT concept. **Present, directly reusable.** |
| TMS PREP | `claims.py`'s `SUPERSEDES`/`CONTRADICTS` truth_state flip is real dependency-propagation-adjacent infrastructure, not simulated. **Stronger foundation than the spec anticipated.** |
| Claude Code adapter | Nothing exists — no hook receiver, no collector, no idempotency/ordering handling. **Gap** (ticket 07 research in progress). |

## Concrete defects found during inspection

- **`embedding_joint` ghost column**: `retrieval.py:89-102` accepts and validates
  `embedding_column in ("embedding", "embedding_joint")`, but no DDL file in `backend/db/`
  creates an `embedding_joint` column anywhere — constructing a retriever with it passes
  validation and fails only at query time.
- **`public_generated` enum/model drift**: `ProvenanceSource` in
  `backend/app/models/ontology.py` does not list `public_generated`, but
  `05_decomposition.sql:13` adds it to the database enum and
  `knowledge_update.py:98,124,137` and `mcp_server/server.py:699` all write it — so
  hydrating those rows through `from_row()` raises a validation error.
- **Pool double-management bug**: `main.py:27` calls `create_pool()` directly (storing the
  result in `app.state.pool`), never `get_pool()` — so the module-global `_pool` in
  `db/session.py:19` stays `None`. `main.py:31` then calls `close_pool()`
  (`session.py:48-52`), which checks `if _pool is not None` — false — so the live pool
  created at startup is never actually closed at shutdown.
- **Setup docs silently stop short**: `README.md:22-26` lists `psql -f` commands for
  `01_ontology.sql` through `05_decomposition.sql` only — `06_generated_files.sql` through
  `10_code_sourced_agents.sql` (Agent Store, code-sourced-agent review, generated files) are
  never mentioned in the documented setup path.
- **`tenant_id` is decorative**: present on `traces` and (per `03_access.sql`'s own comment)
  called out as a V0 pattern that "looked implemented and was decorative" — confirmed no
  query in `backend/app/` filters by it; `access.py`'s docstring names this as a cautionary
  tale for why `visibility`/`owner_id` must go through the centralized predicate instead.
