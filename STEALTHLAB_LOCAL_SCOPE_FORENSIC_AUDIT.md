# StealthLab — Local-Scope MCP Execution: Forensic Code Audit

**Method:** static, read-only inspection of `C:\Users\user\stealth-lab` at its current checkout (git log timestamps below establish recency). No files modified. Every claim below is grounded in an exact file path, line number, function/table/column name, or an exact test name. Where the underlying investigation could not establish a fact, this is stated as **NOT ESTABLISHED FROM CODE** rather than inferred.

**Scope:** local execution through MCP only (`USER → Claude → local MCP → local execution → local Event/Trace/Episode/Artifact/Observation/Claim/Evidence/Procedure → local retrieval → Claude's next action`). Global ingestion and Postgres-global scope are mentioned only where local execution necessarily touches the same tables.

**Target task used as the trace subject:** "Build a production-ready idempotent webhook ingestion endpoint for Stripe-style payment events..." — used only as a representative coding task; it was never actually implemented or run in the course of this audit.

---

## PHASE 1 — Repository inventory (local-MCP-relevant)

```
backend/app/mcp_server/
    server.py                  — MCP server object, 40 @server.tool() functions, auth, lifespan
    resources.py                — 8 MCP Resources (stealth://...)
    prompts.py                  — 6 MCP Prompts (solve_with_stealth, etc.)
    tasks_extension.py          — MCP protocol Extension (tasks capability), no app.* deps
    claim_graph_page.py         — static HTML/JS string for /claim-graph route (non-MCP)
    procedure_graph_page.py     — static HTML/JS string for /procedure-graph route (non-MCP)
    vendor/force-graph.min.js
    __init__.py                 — empty

backend/app/stealth/            — the ".stealth/" filesystem projection package (NOT a game mechanic)
    __init__.py, atomic.py, errors.py, exploration.py, faults.py,
    format.py, generator.py, journal.py, legacy_context.py

backend/app/execution/
    durable_run.py              — execution_runs state machine, episode open/close, job enqueue
    episode.py                  — open_episode_for_run / close_episode_for_run
    recorder.py                 — record_event() + named event wrappers, get_run_events()
    stealth_projection.py       — shim re-exporting app.stealth's legacy renderers
    (+ durable_resume.py, implementation_registry.py, plan_persistence.py, plans.py,
       procedure_graph.py, graph_executor.py, implementation_executor.py,
       recursion_guard.py, durable_graph.py, coordination.py, applicability.py-adjacent)

backend/app/services/
    claims.py                   — capture_claim()
    claim_evidence.py           — record_claim_evidence()
    claim_belief.py             — compute_belief(), recompute_claim_belief()
    claim_extraction.py         — persist_claim_candidate(), claim-equivalence detection
    observations.py             — extract_deterministic_observations_from_run_event(), persist_observation()
    ingestion_jobs.py           — job queue handlers incl. handle_consolidate_local_episode,
                                   handle_extract_procedure_from_episode, handle_promote_observation_to_claim
    procedures.py                — capture_procedure(), supersede_procedure()
    procedure_extraction/       — extract_procedure() and evidence sourcing (AgentRunEvidenceSource)
    applicability.py            — find_applicable_procedures() (Postgres-only retrieval)
    retrieval.py                — HybridRetriever (used by get_relevant_claims, retrieve_precedent)
    embeddings.py                — Embedder
    authn.py                     — OIDC/token verification, current_actor_id()
    access.py                    — AccessScope, visibility_predicate

backend/app/local_agent/
    __init__.py, runner.py       — ONLY these two files exist (see Phase 13 — the SQLite
                                    local store described in an older internal audit doc
                                    was deleted on 2026-09-10)

backend/db/*.sql                 — migrations; key ones cited throughout: 01, 14, 18, 24, 32,
                                    36, 60, 61, 63, 64-70, 79

.mcp.json                        — repo-root MCP client config (http transport, Bearer token)
```

Full-repo greps for `event|trace|episode|artifact|observation|claim|evidence|procedure|local|.stealth|journal|consolidate|retrieve|search|ingest|MCP|tool|resource|prompt|postgres|repository|sqlite|filesystem` return matches concentrated almost entirely inside the directories above; no other directory tree materially participates in the local-execution-to-knowledge pipeline.

---

## PHASE 2 — MCP entry point (exact)

**Framework:** the real `mcp` Python SDK (not a custom protocol). Imports, `backend/app/mcp_server/server.py:61-65`:
```python
from mcp.server import MCPServer
from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken, TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import Context
```

**Server object** — `server.py:290-313`, variable `server`, class `MCPServer`:
```python
server = MCPServer(
    name="stealthlab",
    version="1.0.0",
    instructions=(...),
    lifespan=lifespan,
    extensions=[TasksExtension()],
    token_verifier=_build_token_verifier(_require_mcp_token()),
    auth=AuthSettings(
        issuer_url=AnyHttpUrl(f"http://127.0.0.1:{_MCP_PORT}"),
        resource_server_url=AnyHttpUrl(f"http://127.0.0.1:{_MCP_PORT}/mcp"),
        required_scopes=["stealthlab:tools"],
    ),
)
```

**Process entry points** — both in `server.py`:
- stdio transport: `server.py:4277-4278` — `if __name__ == "__main__": server.run()`
- HTTP/ASGI transport (the one `.mcp.json` actually uses): `app = server.streamable_http_app()` (`server.py:447`), intended to be launched per the in-file comment (`server.py:320-321`): `uvicorn app.mcp_server.server:app --host 127.0.0.1 --port 8765 --workers 1`.

**Port configuration:** `_MCP_PORT = int(os.environ.get("STEALTHLAB_MCP_PORT", "8765"))` (`server.py:288`).

> **Discrepancy (flagged, not resolved by static code):** `.mcp.json` declares `"url": "http://127.0.0.1:8766/mcp"` — port **8766** — but the code's default and its own launch-comment both say **8765**. This session's actual connection attempt to `stealthlab` failed with `AUTH_HEADER_REJECTED` (HTTP 401, `invalid_token`) rather than a connection-refused error, which is consistent with something listening on 8766 and rejecting the bearer token — but whether that's this exact process with an env override (`STEALTHLAB_MCP_PORT=8766`) or a different process entirely is **NOT ESTABLISHED FROM CODE**. No `packaging/` script setting the port was found.

**Tool registration** — decorator pattern, `@server.tool()`, one per Python function, all 40 defined in `server.py` (none in the sibling files). Full list, exact name = function name, with signature and line:

| # | Tool (fn) name | Line | Signature (abridged) |
|---|---|---|---|
|1|`retrieve_precedent`|680|`(query: str, ctx: Context) -> str`|
|2|`propose_synthesis`|757|`(trigger_id: str, ctx: Context) -> str`|
|3|`find_best_way`|1222|`(task_description: str, ctx: Context, repo_path=None, mode="auto", model="gemma-4-31B-it", max_steps=25, session_id=None, allow_unverified_procedures=False, resume_run_id=None, workspace_id=None, parent_run_id=None, parent_node_order=None, exclusions=None) -> str`|
|4|`reproduce_procedure`|1971|`(procedure_id, repo_path, ctx, model="gemma-4-31B-it", max_steps=25, transfer_repo_path=None, workspace_id=None, transfer_workspace_id=None) -> str`|
|5|`detect_conflict_trigger`|2334|`(new_node_id, ctx) -> str`|
|6|`check_procedure`|2401|`(procedure_id, query, ctx) -> str`|
|7|`search_procedures`|2555|`(task, ctx, state="{}", limit=5, require_verified=False, invariant_bindings="{}") -> str`|
|8|`get_claim_graph`|2626|`(ctx, limit=200, include_retired=False, q=None, with_status=True, link_mode="both", sim_k=3, sim_threshold=0.55) -> str`|
|9|`get_relevant_claims`|2670|`(goal, ctx, context=None, top_k=10) -> str`|
|10|`get_procedure`|2701|`(procedure_id, ctx) -> str`|
|11|`check_applicability`|2723|`(procedure_id, ctx, state="{}", require_verified=True) -> str`|
|12|`report_execution`|2772|`(procedure_id, success, context_key, ctx, steps_used=None, success_criteria=None, failure_class=None, observations_json=None, tool_sequence_json=None, task_description=None, session_id=None) -> str`|
|13|`submit_procedure`|2920|`(name, goal, steps_json, ctx, domain=None, provenance="system_pending_review") -> str`|
|14|`decide_procedure`|3006|`(procedure_id, approver_id, decision, ctx) -> str`|
|15|`decompose_task`|3074|`(problem, ctx) -> str`|
|16|`decide_decomposition`|3165|`(decomposition_id, approver_id, decision, ctx) -> str`|
|17|`submit_approval`|3229|`(scorecard_id, approver_id, decision, ctx, note=None) -> str`|
|18|`resolve_implementation`|3298|`(task_node_id, ctx, hint_kinds_json=None) -> str`|
|19|`inspect_implementation`|3359|`(implementation_id, ctx) -> str`|
|20|`list_task_implementations`|3389|`(task_node_id, ctx, status="active") -> str`|
|21|`submit_implementation`|3410|`(procedure_id, role, ctx, implementation_id=None, name=None, kind=None, provider=None, version=1, ...) -> str`|
|22|`get_implementation_capability`|3514|`(implementation_id, ctx) -> str`|
|23|`find_problem`|3595|`(query, ctx, limit=10) -> str`|
|24|`inspect_problem`|3607|`(problem_id, ctx) -> str`|
|25|`list_problem_solutions`|3627|`(problem_id, ctx) -> str`|
|26|`compare_solutions`|3636|`(problem_id, solution_ids_json, ctx) -> str`|
|27|`inspect_evaluation`|3665|`(evaluation_id, ctx) -> str`|
|28|`find_best_solution`|3680|`(goal, ctx) -> str`|
|29|`continue_run`|3710|`(procedure_run_id, ctx, repo_path=None) -> str`|
|30|`verify_completion`|3762|`(procedure_run_id, ctx, reports_json="[]") -> str`|
|31|`generate_review_packet`|3883|`(procedure_run_id, criterion_id, ctx) -> str`|
|32|`declare_file_intent`|3925|`(procedure_run_id, node_order, owner_agent_id, ctx, write_exact_json="[]", ..., lease_seconds=3600) -> str`|
|33|`inspect_run`|3994|`(run_id, ctx) -> str`|
|34|`resume_execution_run`|4031|`(run_id, ctx) -> str`|
|35|`retry_run_node`|4059|`(run_id, node_order, ctx, force=False) -> str`|
|36|`report_node_progress`|4088|`(run_id, node_order, ok, ctx, result_json="{}", error_class=None, error_json="{}") -> str`|
|37|`get_route_decision`|4134|`(route_decision_id, ctx) -> str`|
|38|`project_knowledge`|4153|`(repo_path, ctx, object_ids_json="[]", query="", top_k=8) -> str`|
|39|`open_exploration`|4201|`(repo_path, question, ctx, scope="-") -> str`|
|40|`close_exploration`|4227|`(repo_path, exploration_id, ctx, status="RESOLVED", resolution="") -> str`|

All return type `str` (JSON-serialized). `server.py` also exposes 5 unauthenticated Starlette routes via `@server.custom_route(...)` for HTML debugging pages (`/claim-graph`, `/claim-graph/data`, `/procedure-graph`, `/procedure-graph/data`, `/claim-graph/vendor/force-graph.js`) — these are **not MCP tools**, they're plain HTTP GET endpoints (`server.py:340-444`).

**Resources** (`resources.py`), registered via `register_resources(server)` (`resources.py:355-362`, invoked `server.py:4273`), each bound with `server.resource(uri, ...)`:

| URI | function | line |
|---|---|---|
|`stealth://procedures/{procedure_id}`|`procedure_resource`|120|
|`stealth://problems/{problem_id}`|`problem_resource`|174|
|`stealth://problems/{problem_id}/solutions`|`problem_solutions_resource`|210|
|`stealth://claims/{claim_id}`|`claim_resource`|223|
|`stealth://evaluations/{evaluation_id}`|`evaluation_resource`|248|
|`stealth://implementations/{implementation_id}`|`implementation_resource`|272|
|`stealth://tasks/{task_node_id}/implementations`|`task_implementations_resource`|291|
|`stealth://runs/{run_id}`|`run_resource`|306|

**Prompts** (`prompts.py`), registered via `register_prompts(server)` (`prompts.py:206-210`, invoked `server.py:4274`):

| name | function | line |
|---|---|---|
|`solve_with_stealth`|`solve_with_stealth(task, repo_path="")`|34|
|`debug_with_stealth`|`debug_with_stealth(symptom, repo_path="")`|68|
|`research_with_stealth`|`research_with_stealth(question)`|94|
|`improve_with_stealth`|`improve_with_stealth(problem_id="", goal="")`|118|
|`verify_with_stealth`|`verify_with_stealth(procedure_id)`|144|
|`contribute_learning`|`contribute_learning(summary="")`|167|

`prompts.py`'s own module docstring (lines 4-8): a Prompt here "is NOT business logic and NOT autonomous execution. It is a short policy the MCP host **can load**" — i.e. pull-based, host-selected, never auto-injected.

**Auth path (Bearer token):**
- `_require_mcp_token()` (`server.py:240-249`) reads env var `STEALTHLAB_MCP_TOKEN`; raises `RuntimeError` at import time if unset.
- `OidcAwareTokenVerifier.verify_token(self, token) -> AccessToken | None` (`server.py:173-237`): if OIDC is configured (`settings.oidc_issuer`/`oidc_audience`), tries `validate_token_async(...)`; otherwise falls back to `secrets.compare_digest(token, self._shared_token)` (`server.py:235-236`) — constant-time compare against `STEALTHLAB_MCP_TOKEN`. Success → `AccessToken(token=token, client_id="stealthlab-local", scopes=["stealthlab:tools"])`; failure → `None`, which the SDK's auth middleware turns into a 401 — this matches the `AUTH_HEADER_REJECTED`/`invalid_token` error this very session got when trying to connect to `stealthlab`.
- Boot guard: `assert_deployment_mode_posture(...)` (`server.py:268-272`) refuses to start in `deployment_mode="shared"` without OIDC configured.

**Context object:** `mcp.server.mcpserver.Context`, passed as `ctx` to every tool. The ONLY field actually read anywhere in `mcp_server` code is `ctx.request_context.lifespan_context["pool"]` (the asyncpg pool — set by `lifespan()`'s yielded `{"pool": pool}`, `server.py:167`). There is **no** `workspace_id`/caller-identity/repo-scope field on `Context` itself — those are resolved separately via contextvars: `get_access_token()` and `current_actor_id()`, wrapped by `_resolve_caller_identity()` (`server.py:450-475`) and `_caller_access_scope()` (`server.py:584-608`).

**Config values read by the MCP layer** (`app.config.settings`): `deployment_mode`, `oidc_issuer`/`oidc_audience`, `hosted_execution_enabled` (gates whether a `repo_path` param is trusted directly vs. must resolve through a registered workspace), `general_compute_api_key`/`general_compute_base_url` (for a tier-1 OpenAI-compatible LLM client). `DATABASE_URL` is read directly from `os.environ` in `lifespan()` (`server.py:162-164`), **not** via `settings.database_url` — whether these are guaranteed identical at runtime is NOT ESTABLISHED FROM CODE. There is **no** `.stealth` path setting on `Settings` — the `.stealth` directory location is derived per-call from the caller-supplied `repo_path`/`workspace_root` MCP parameter, never a global config value.

**Import graph (server.py, abridged, lines 41-124):** `app.db.session.create_pool`, `app.execution.{durable_resume, durable_run, implementation_registry}`, `app.api.{approval, decompose}`, `app.services.{access, applicability, authn, decomposition, embeddings, knowledge_conflict, local_retrieval, procedure_extraction, retrieval, reuse_detection}`, `app.observability`, `app.config.settings`, `app.debate.{panel, state_machine}`, `app.services.loop.LoopOrchestrator`, `app.mcp_server.{tasks_extension, claim_graph_page, procedure_graph_page}`, `app.services.{claim_graph_api, procedure_task_graph_api, product_model}`; plus numerous function-local imports inside individual tool bodies (`app.services.workspace_registry`, `app.execution.{plan_persistence, plans, procedure_graph, graph_executor, implementation_executor, recursion_guard, durable_graph, coordination}`, `app.services.{verification, route_decision, decompose}`, `app.stealth.{errors, faults, exploration}`, `app.utils.ids`).

**The "six-tool surface" test:** `backend/tests/test_mcp_six_tool_surface_offline.py`. Its own docstring (lines 1-14) calls it the "**5**-tool minimal MCP surface": `search_procedures, get_procedure, check_applicability, report_execution, submit_procedure`. A 6th, `decide_procedure`, is added later in the same file (lines 458-461, comment: "The 6th primitive, found missing by this file's own live counterpart"). **There is no single list-equality assertion enumerating all 6 in one place** — the "six tools" figure comes from reading the docstring's 5 plus the labeled "6th primitive" section, not one assertion. This is a *named minimal subset*, not the full tool surface — the actual server exposes 40 `@server.tool()` functions (table above), not 6.

---

## PHASE 3 — Concrete MCP interaction trace for the webhook task

Given the task ("Build a production-ready idempotent webhook ingestion endpoint..."), the tools that **exist and are plausible** for Claude to call, based on their names/signatures:

- `search_procedures(task="...", ...)` — look for a prior procedure matching this goal.
- `find_best_way(task_description="...", repo_path=..., mode="auto", ...)` — the heavier tier-1/tier-2 orchestrator.
- `get_relevant_claims(goal="...", ...)` — pull prior Claims relevant to webhook/idempotency/retry topics.
- `check_applicability(procedure_id, state=...)` — before reusing a found procedure.
- `report_execution(procedure_id, success, context_key, ...)` — after finishing, to record outcome.
- `submit_procedure(...)` — if no matching procedure existed and Claude wants to contribute one directly (bypassing execution-based capture).

**THERE IS NO CODE-LEVEL EVIDENCE THAT CLAUDE AUTOMATICALLY CALLS ANY OF THESE.** Per Phase 2's Prompts/Resources findings and Phase 15 below, every one of these tool calls is opt-in — nothing in `server.py`, `prompts.py`, or `resources.py` forces a call before, during, or after a coding task. Whether a given real Claude session calls any of them at all for a given task is **NOT ESTABLISHED FROM CODE** — it depends on the calling agent/host's own decision logic, which is outside this repository.

**Actually invoked in tests/runtime** (i.e. proven to work end-to-end via a real test, not just defined): `search_procedures`, `capture_procedure` (service-level, not a tool but the underlying write path), `find_best_way`'s tier-1 path — see Phase 23's test table.

---

## PHASE 4-12 — Local execution lifecycle (Event → Trace → Episode → Artifact → Observation → Claim → Evidence → Procedure)

### Event

- **Table:** `execution_run_events` (`backend/db/61_execution_run_events.sql:18-25`): `id UUID PK, execution_run_id UUID NOT NULL REFERENCES execution_runs(id) ON DELETE CASCADE, node_order INTEGER, event_type TEXT NOT NULL, payload JSONB NOT NULL DEFAULT '{}', created_at TIMESTAMPTZ DEFAULT now()`. `seq BIGSERIAL` added by `db/63_execution_run_events_seq.sql:17` (ordering fix — `created_at` alone allowed same-transaction ties). Full `event_type` vocabulary CHECK constraint: `db/70_execution_run_events_full_vocabulary.sql:20-33`. **Append-only**, enforced by DB trigger `tg_execution_run_events_frozen` (`db/61...sql:41-47`) which blocks UPDATE/DELETE.
- **No Python class** wraps a row — plain `asyncpg` dicts.
- **Write facade:** `record_event(conn, *, execution_run_id, event_type, node_order=None, payload=None)` — `backend/app/execution/recorder.py:43-53`. Validates `event_type in EVENT_TYPES` (line 23-38), then `INSERT INTO execution_run_events (execution_run_id, node_order, event_type, payload) VALUES (...)`. Named wrappers (`record_run_created`, `record_node_started`, `record_tool_called`, `record_node_failed`, etc.) at `recorder.py:56-297` all funnel into this one INSERT.
- **Callers:** exclusively inside `backend/app/execution/durable_run.py`, always in the same transaction as the corresponding `execution_runs`/`execution_run_nodes` state-changing UPDATE/INSERT (e.g. `record_run_created` at `durable_run.py:202` inside `start_run`; `record_run_claimed`/`record_run_started` at `:297,299`; `record_node_*`/`record_verification_*`/`record_child_run*` throughout, each guarded by `if tag != "UPDATE 0":`).
- **Read path:** `get_run_events(pool, execution_run_id)` (`recorder.py:300-306`) — `SELECT ... ORDER BY seq`.
- **Storage:** Postgres only. No filesystem/local path exists for raw events.
- **Field mapping:** caller's `event_type` string → `event_type` column (must be in the whitelist); `node_order` → column or NULL; caller's kwargs assembled into a dict → `payload` JSONB; `id`/`seq`/`created_at` are DB-generated.

### Trace

Two unrelated things share the name "trace":

1. `traces` table (`db/01_ontology.sql:103-115`) — an older ontology construct (`trace_id TEXT PK`, links to `task_nodes`). **No code under `app/execution/` inserts into this table** — it is not used by the execution-run machinery. Treat as legacy/unrelated for this audit's purposes.
2. `execution_runs.trace_id` (TEXT column, `db/51_execution_run_identity.sql:63`) — the real causal-chain identifier. Set in `durable_run.py:161-163`: `if trace_id is None: trace_id = str(uuid7())`, and **inherited by children**: `durable_run.py:150-151` — `if parent_row["trace_id"] is not None: trace_id = str(parent_row["trace_id"])` (comment: "a child cannot legitimately start a NEW trace"). **This is many-to-one**: one `trace_id` can span multiple `execution_runs` rows (a root run plus all its recursive children) — it is NOT one trace per run.

Naming wrinkle: `open_episode_for_run()` stores this `trace_id` value into the `episodes.session_id` column (`episode.py:71,79`), not a column literally named `trace_id` on `episodes`.

### Episode

- **Table:** `episodes` (`db/01_ontology.sql:83-91`; base columns `id, tenant_id, episode_type, content, content_ref, timestamp, metadata`). Extended by `db/79_local_episode_learning.sql:29-37`: adds `execution_run_id UUID REFERENCES execution_runs(id) ON DELETE CASCADE`, a partial unique index `uq_episodes_execution_run`, and widens the `episode_type` CHECK to add `'execution'` (previously `document|trace|debate_transcript`). Columns actually used by the execution path (`episode.py:70-75`): `episode_type, execution_run_id, session_id, start_ts, owner_id, visibility, scope_type, scope_entity_id, project_id, parent_episode_id, metadata`. Exact origin migration of `start_ts`/`end_ts` is **NOT ESTABLISHED FROM CODE** beyond confirming they exist and are used.
- **No Python class** backs an execution episode row. Two unrelated `class Episode` definitions exist elsewhere (`app/models/ontology.py:184`, a Pydantic model for `document/trace/debate_transcript` only, no `execution_run_id` field; `app/services/trace_worker.py:536`, an unrelated dataclass for transcript-line segmentation) — neither is the execution-episode shape.
- **Creation:** `open_episode_for_run(conn, run_id, *, created_by=None, scope_type=None, scope_entity_id=None, trace_id=None, parent_run_id=None) -> str` (`episode.py:35-86`). Called exactly once, from `durable_run.start_run()` (`durable_run.py:252-256`), inside the same transaction as the `execution_runs` INSERT.
- **Terminal state:** no dedicated status enum — closure is `episodes.end_ts` becoming non-NULL. The outcome string (`"success"`, `"failed_verification"`, etc.) is stored inside `metadata`, not a CHECK-constrained column on `episodes` itself.
- **Closure function:** `close_episode_for_run(conn, run_id, *, outcome) -> Optional[str]` (`episode.py:89-109`):
  ```sql
  UPDATE episodes SET end_ts = now(), metadata = metadata || jsonb_build_object('outcome', $2::text)
  WHERE execution_run_id = $1 AND end_ts IS NULL RETURNING id
  ```
  This is a **plain function call, not a DB trigger**.
- **Enqueue on closure:** `_close_episode_and_enqueue_consolidation(conn, run_id, outcome)` (`durable_run.py:613-634`):
  ```python
  episode_id = await _episode.close_episode_for_run(conn, run_id, outcome=outcome)
  if episode_id is not None:
      await conn.execute(
          "INSERT INTO ingestion_jobs (job_type, payload) VALUES ($1, $2::jsonb)",
          "consolidate_local_episode",
          {"episode_id": episode_id, "execution_run_id": run_id},
      )
  ```
  Called from exactly 3 terminal-transition sites, each `if tag != "UPDATE 0":` guarded: `durable_run.py:670` (success), `:746` (`_finalize`, generic outcome), `:803` (verification-failure path). **This is the code proving "episodes close on terminal transitions" and "closure enqueues bounded consolidate_local_episode" — both real, at these exact 3 call sites.**

### Consolidation (`consolidate_local_episode`)

`async def handle_consolidate_local_episode(pool, payload)` — `backend/app/services/ingestion_jobs.py:1002-1237`. Registered `JOB_HANDLERS["consolidate_local_episode"] = handle_consolidate_local_episode` (`:1240`).

Sequence inside it:
1. Read `episodes` row (`:1027-1031`); **idempotency guard**: bail if `metadata.consolidated_at` already set (`:1039-1041`); bail if run gone (`:1047-1049`); raise if `execution_runs.final_outcome IS NULL` (`:1050-1054`).
2. Read `execution_run_events` filtered to `_MEANINGFUL_RUN_EVENT_TYPES`, `ORDER BY seq ASC` (`:1062-1066`).
3. For each meaningful event: `extract_deterministic_observations_from_run_event(event)` → `persist_observation(...)`, `extractor_kind='deterministic'` (`:1071-1084`). If zero meaningful events, marks `metadata.consolidated_at` and returns (`:1090-1094`) — **no Claim/Evidence/Procedure attempted**.
4. One LLM-backed pass, `extract_claim_candidates_cached(...)` over a rendered episode text, → `persist_claim_candidate(...)` per candidate, anchored via `observation_id=observation_dicts[0]["id"]` → internally calls `capture_claim(...)` → also writes one `episode_links` row per persisted claim (`:1137-1168`).
5. `record_claim_evidence(evidence_type="execution_result", outcome_status=outcome, ...)` per persisted claim (`:1186-1192`).
6. **Only if `outcome == "success"`:** calls `extract_procedure(...)` attempting a Procedure candidate (`:1198-1232`).
7. Always, at the end: `episodes.metadata.consolidated_at = now()` (`:1234-1237`).

### Artifact

Two unrelated things:

1. `ingested_artifacts` table (`db/32_ingestion_provenance.sql:34-63`) — exclusively the **document/repo ingestion** path (`source_type` values like `'skill_md_dir'`, `'github_workflow'`, etc.). Its `run_id` column points at `ingestion_runs`, not `execution_runs`. **NOT ESTABLISHED FROM CODE** that any code path creates an `ingested_artifacts` row from a local `execution_run` — no writer anywhere takes an `execution_run_id`.
2. Local-execution "artifact": an `execution_run_events` row with `event_type='artifact_recorded'`. This is converted **directly into an Observation** by `extract_deterministic_observations_from_run_event` (`observations.py:180-185`) — there is **no dedicated Artifact table/row for local execution output**. The "Artifact" layer, as a distinct persisted entity between Event and Observation, **does not exist for the local-execution path** — it is skipped entirely.

### Observation

- **Pure derivation:** `extract_deterministic_observations_from_run_event(event: dict) -> Optional[dict]` (`observations.py:142-206`). Input: one `execution_run_events` row (`event_type`, `node_order`, `payload`). Handles `event_type` ∈ {`node_succeeded`, `node_failed`, `artifact_recorded`, `verification_completed`, `tool_result`}. Output: `{"observation_type": ..., "label": ..., "properties": {...}}`.
- **Write:** `persist_observation(pool, *, observation_type, label, extractor_kind, event_ids=None, execution_run_event_ids=None, properties=None, model_id=None, prompt_hash=None, decoding_params_hash=None, owner_id=None, visibility="public") -> str` (`observations.py:284-371`). Writes `observations` table (`db/14_observations.sql:9-39`: `id, observation_type, label, extractor_kind CHECK IN ('deterministic','model'), extractor_name, code_version, model_id, prompt_hash, decoding_params_hash, properties JSONB, visibility, owner_id, created_by, extracted_at` — **deliberately no confidence column**, per comment at lines 29-33), plus one `observation_events` row per event id (`INSERT INTO observation_events (observation_id, execution_run_event_id) VALUES (...)`, `:365-370`).
- `observation_events` was extended by `db/79_local_episode_learning.sql:46-79` to add `execution_run_event_id`, with CHECK `observation_events_exactly_one_event_chk`: `(event_id IS NOT NULL) <> (execution_run_event_id IS NOT NULL)`.
- **Caller:** `handle_consolidate_local_episode` (loop at `ingestion_jobs.py:1071-1084`).
- **A separate, NOT auto-wired function:** `promote_observation_to_claim` (`observations.py:374`) — its only production caller is `handle_promote_observation_to_claim`, a **different, separately-enqueued** job type (`ingestion_jobs.py:321-396`). **NOT ESTABLISHED FROM CODE** that anything auto-enqueues this for local-execution episodes — the local path uses the LLM-based `persist_claim_candidate` route instead (see below), bypassing `promote_observation_to_claim` entirely.
- Also **NOT ESTABLISHED FROM CODE**: any caller wiring `extract_model_observation` (a semantic/LLM labeler, `observations.py:229`, takes a `trace_event` dict) to local `execution_run_events` — it appears built for a different, global `trace_event` universe.

### Claim

- **Function:** `capture_claim(pool, *, statement, task_ids, justification_episode_id=None, ..., source_ref=None, ingestion_context_id=None, observation_id=None) -> Optional[str]` (`claims.py:145-169`).
- **No-op conditions (quoted exactly):**
  - `claims.py:282-284`: `has_provenance_ref = bool(source_ref or ingestion_context_id or observation_id); accepted_pre_db = has_provenance_ref or justification_episode_id is not None; if not accepted_pre_db and not task_ids: ... return None`
  - `claims.py:315-317`: `if not rows and justification_episode_id is None and not has_provenance_ref: logger.info("capture_claim: no anchor and no provenance ref; dropping"); return None`
  - So it writes only if: (a) ≥1 `task_ids` resolves to a live `task_nodes` row, OR (b) `justification_episode_id` given, OR (c) `source_ref`/`ingestion_context_id`/`observation_id` given. A truly unprovenanced claim is a **silent, logged no-op** — this is explicit, deliberate behavior, not a bug.
- **Writes:** `knowledge_nodes` (`node_type='claim'`, embedding, `provenance='company_ingested'`, owner/visibility/scope); `edges` (PRODUCES/CLAIM_OF per resolved `task_ids`); `episode_links` row if `justification_episode_id` given; **`claim_sources (claim_id, observation_id) VALUES ($1,$2) ON CONFLICT DO NOTHING`** if `observation_id` given.
- **Automatic call from local execution:** yes — `handle_consolidate_local_episode` (`ingestion_jobs.py:1153-1163`) → `claim_extraction.persist_claim_candidate(...)` → `capture_claim(...)` (`claim_extraction.py:548-572`), anchored via `observation_id=anchor_observation_id` where `anchor_observation_id` is a real Observation this same episode's execution events produced (`ingestion_jobs.py:1151`).
- **Dedup:** not a hash or DB unique constraint — an LLM-judge **detect-only** pass, run *after* the claim is already written: `_detect_and_record_equivalence(pool, claim_id, statement, ...)` (`claim_extraction.py:576-614`) → `find_candidate_claim_pairs` (embedding-similarity neighbours) → `classify_claim_relation` (LLM judge) → if relation != `"unknown"`, records a `claim_relation` candidate row. Explicit docstring: "NEVER merges, NEVER reuses an existing claim in place of this one... detect only" (`claim_extraction.py:594-599`). Best-effort, wrapped in try/except.
- **Idempotency against re-consolidating the same episode** (a different mechanism from claim dedup): the `episodes.metadata.consolidated_at` guard in `handle_consolidate_local_episode` (see above), not claim-content dedup.

### Evidence

- **Table:** `evidence` (`db/24_evidence.sql:38+`): `id, evidence_type evidence_kind, target_type TEXT, target_id UUID, target_version INTEGER, direction TEXT, strength_score REAL, strength_method TEXT, independence_group TEXT, context_key TEXT, source_id UUID, content_ref UUID, outcome_status TEXT, success_criteria JSONB DEFAULT '{}', failure_class TEXT` + `created_by, visibility, owner_id, tenant_id`. `evidence_kind` enum's first value is `'execution_result'` ("a recorded run produced the outcome").
- **Function:** `record_claim_evidence(pool, *, claim_id, evidence_type, outcome_status, success_criteria=None, direction=None, strength_score=1.0, strength_method="recorded_outcome", independence_group=None, context_key=None, failure_class=None, created_by=None, visibility="public", owner_id=None) -> str` (`claim_evidence.py:48-64`). Inserts into `evidence` (lines 125-159), then best-effort calls `claim_belief.recompute_claim_belief(...)` (lines 167-172).
- **`independence_group`** (doc at `claim_evidence.py:88-99`): rows sharing a named group NEVER count as independent corroboration of each other; NULL = self-grouped/independent.
- **Automatic call from local execution:** `handle_consolidate_local_episode` (`ingestion_jobs.py:1186-1192`):
  ```python
  for claim_id in persisted_claim_ids:
      await record_claim_evidence(
          pool, claim_id=claim_id, evidence_type="execution_result",
          outcome_status=outcome, success_criteria=evidence_success_criteria,
          created_by=CONSOLIDATION_EXTRACTOR, owner_id=owner_id, visibility=visibility,
      )
  ```
  `outcome = run["final_outcome"]`, read from `execution_runs.final_outcome`. This is **one evidence row per persisted claim per episode** — not per individual test in the hypothetical webhook task's test suite (duplicate-delivery / invalid-signature / concurrency / retry tests would all collapse into a single episode-level pass/fail outcome unless the run/episode boundary is drawn per-test, which nothing in this code does).

### Belief (feeds Claim status, not a separate "layer" the user asked about, but load-bearing)

`compute_belief` weights (`claim_belief.py`): `_STRONG_TYPES = {execution_result, reproduction, benchmark, experiment}` weight/ceiling 1.0 (line 81, 85, 91); `_DOCUMENT_TYPES = {document, external_source, human_review}` weight 0.5, **ceiling 0.5** (line 82, 91); `_WEAK_TYPES = {observation, artifact}` (unknown types fail-closed here) weight 0.3, ceiling 0.4 (line 83, 91); `_NO_SUPPORT_CEILING = 0.30` (line 94); **`_ZERO_EVIDENCE_FLOOR = 0.10`** (line 98).

### Procedure

- **Creation:** `capture_procedure(pool, *, name, goal, steps=None, ..., scope=None, scope_type=None, scope_entity_id=None, ..., availability="active", ...) -> dict` (`procedures.py:94`). INSERT into `procedures` (`db/18_procedures.sql`), full column list includes `procedure_id, name, goal, steps, parameter_schema, preconditions, ..., evidence_refs, source_episode_ids, provenance, scope_type, scope_entity_id, embedding*, retrieval_document*, availability, is_engineering_fixture`. Every row is born `verification_state='candidate'` (DB default), `staleness='fresh'`.
- **Versioning:** `supersede_procedure(...)` (`procedures.py:391`) carries forward a fixed column list (`_SUPERSEDE_CARRY_COLUMNS`, lines 340-357).
- **Two distinct paths that can produce a Procedure from local execution:**
  1. **Automatic, per-episode:** inside `handle_consolidate_local_episode`, only when `outcome == "success"`, a direct call to `extract_procedure(...)` (`ingestion_jobs.py:1198-1232`) attempts a Procedure candidate immediately after that episode's Claims/Evidence are written.
  2. **Separate, admin/CLI-triggered batch job:** `handle_extract_procedure_from_episode(pool, payload)` (`ingestion_jobs.py:781`) — a **queued job handler**, enqueued elsewhere (lines 740-778) "behind an explicit `--extract-limit` flag" per `run_ingestion.py`'s own comment — i.e. an operator-run sweep over a *backlog* of past episodes, not something episode-closure enqueues automatically (episode closure only ever enqueues `consolidate_local_episode`, never `extract_procedure_from_episode`). Verified current-code guard (`ingestion_jobs.py:791-802`):
     ```python
     if not episode_id or not session_id or not isinstance(goal_text, str) or not goal_text.strip():
         raise ValueError("extract_procedure_from_episode payload missing source-derived ids or goal_text")
     if outcome != "success":
         raise ValueError("extract_procedure_from_episode requires an explicit successful outcome")
     ```
     This matches (and confirms as still-true) the repo's own `MCP_HARDENING_DEFERRED_ITEMS.md` claim about this hardening. Always produces `visibility="private"` (comment `:863-876`).
- **Ad-hoc capture scope derivation ("MCP ad-hoc Procedure scope derives repo/user scope"):** the ONLY ad-hoc capture site is inside `find_best_way` (`server.py:1708-1722`):
  ```python
  adhoc_owner = _resolve_caller_identity(fallback="find_best_way_adhoc")
  adhoc_scope_type = "repository" if workspace_id else "user"
  adhoc_scope_entity_id = workspace_id or adhoc_owner
  ```
  then `capture_procedure(..., scope_type=adhoc_scope_type, scope_entity_id=adhoc_scope_entity_id, created_by=adhoc_owner, visibility="private", owner_id=adhoc_owner)`.
  - **"Repo" scope** = the caller-supplied `workspace_id` MCP parameter — **not** cwd, **not** git remote. (A separate, unrelated mechanism, `_git_repo_identity`/`_local_context_key` in `local_agent/runner.py:286-346`, derives a git-remote+HEAD-SHA identity, but only for `report_execution`'s `context_key`, never for ad-hoc scope.)
  - **"User" scope** = `_resolve_caller_identity()` (`server.py:450-475`): OIDC `sub` via `get_access_token()`, else `current_actor_id()` contextvar, else the literal fallback string `"find_best_way_adhoc"` when unauthenticated.

---

## PHASE 12B — DAG/Plan execution engine internals (fills a gap left open at Phase 18/19 below in the original pass — "graph_executor.py / implementation_executor.py internals not traced in this pass")

### Data structure: what a "Plan"/"node" is, exactly

`PlanNode`, `ExecutionPlan`, `TaskGraph` are defined in `backend/app/models/plan.py` (imported at `plans.py:41-47`); `plans.py` itself is the compile/validate/hash boundary over those models, not the definition site.

**It is a real DAG, not a linear list.** Exact fields: `n.order` (int identity within the graph), `n.deps: list[int]` (the other nodes' `order` values this node depends on — this is the entire dependency-encoding mechanism), `n.goal`, `n.node_class` (`NODE_CLASSES = ("predictable","uncertain","high-risk")`), `n.scope_type`/`n.scope_entity_id`, `n.task_node_id`, `n.implementation_id`, `n.implementation_hint`.

- Cycle/uniqueness validation: `_check_acyclic()` (`plans.py:162-183`) — Kahn's algorithm over `n.deps`; `dependents[d]` is a list, so multiple dependents per node are explicitly supported (a real DAG, not a tree/chain).
- `validate_graph()` (`plans.py:186-212`) requires unique `n.order` values, validates every `n.deps` entry references an existing, non-self `order`, then calls `_check_acyclic`.
- `compile_plan(nodes: Sequence[PlanNode|Mapping], ...)` (`plans.py:266-377`) is **pure and pool-free** — it does NOT call an LLM to invent nodes. It validates (`validate_graph`), computes a deterministic `graph_hash`/`content_hash` (SHA-256 over canonical JSON, `plans.py:73-91,333-352`), and returns `CompiledPlan(plan, graph)`.

### Where the nodes actually come from (task → DAG)

`durable_run.start_run()` itself does **not** decompose a task into steps — it receives `node_orders`/`deps` already computed (`durable_run.py:96-121`) and only creates durable rows from them. The decomposition happens one layer up, in `server.py`, before `compile_plan`/`start_run` are ever called:
```
steps = matched_procedure.get("steps") or [{"order": 0, "goal": task_description}]
nodes = await expand_procedure_steps(pool, procedure_id=..., procedure_version=..., steps=steps)
compiled_plan = compile_plan(..., nodes=nodes, ...)
```
`expand_procedure_steps` (`app/execution/procedure_graph.py`) only resolves subprocedure references into linear/graph nodes from a **Procedure's pre-existing `steps` field** (tier-1: a matched procedure) — it does not invent steps via an LLM call. In the tier-2 no-match case, this collapses to a single node for the whole task (`graph_executor.py`'s own docstring, line 111: "tier-2, which compiles one node per flat-agent run"). **NOT ESTABLISHED FROM CODE**: any call site where `compile_plan`'s `nodes=` argument is populated by a from-scratch LLM decomposition rather than a Procedure's `steps` field — every path read traces back to `steps`.

### Scheduling / walking the DAG

Two executor modules exist; both are **strictly sequential — no parallelism anywhere**:

- **`graph_executor.py`** (in-memory, non-durable, used by offline tests + as the underlying algorithm): `async def execute_task_graph(graph, *, run_node: Callable[[PlanNode], Awaitable[NodeResult]]) -> GraphExecutionResult` (`graph_executor.py:101-105`). `_topological_batches()` (`:62-98`) runs Kahn's algorithm, grouping ready (indegree-0) nodes into batches by dependency depth. Execution:
  ```python
  for batch in batches:
      for node in batch:
          blocked_by = [d for d in node.deps if statuses.get(d) in ("failure", "skipped")]
          if blocked_by:
              statuses[node.order] = "skipped"
              continue
          result = await run_node(node)      # graph_executor.py:130 -- one at a time, no gather
  ```
  Outcome decision (`:134-143`): all succeeded → `"success"`; none succeeded → `"failure"`; mixed → `"needs_rework"`. Failure only skips a node's *transitive dependents* — unrelated branches still run.
- **`durable_graph.py`** — the actual production tier-2 path (docstring, lines 1-11: "The one bridge from a compiled TaskGraph to a DURABLE execution run... so a crashed run can be resumed"; `graph_executor.py` itself is used only by offline tests in production). `run_graph_durably()` (`:57-149`) wraps `durable_run.start_run()` + `durable_run.execute_run()`/`resume_run()`. Real persistence, in `durable_run.py`:
  - `start_run`: INSERT `execution_runs` row + one `execution_run_nodes` row per node, `status='pending'` (`durable_run.py:167-199`) — **before** any node executes.
  - `_node_claim` (`:337-353`): UPDATE a node to `status='running'` — **before** its work runs.
  - `_node_finish` (`:356-430`): UPDATE to `status='succeeded'`/`'failed'` — **after** its work runs.
  - `_run_one_node` (`:480-515`) sequences exactly: claim → `await run_node(order, attempt)` (no DB transaction held open during the call itself, per docstring `:9-11`) → finish.
  - `_drive` (`:518-567`): `for order, node in nodes.items(): ... outcome = await _run_one_node(...)` — plain sequential `for`-loop with `await`, breaks on non-success. **Multiple nodes never literally execute concurrently in either module** — `asyncio.gather`/a worker pool were searched for and not found.
- **Crash recovery**: a crash mid-DAG leaves a node `'running'` with an expired `lease_expires_at`; `resume_run()` (`durable_run.py:859-889`) detects this and re-arms/parks it — but per Phase 17, nothing calls `resume_run()` automatically on process restart.

### What "executing a node" actually does — the critical fork

`execute_implementation(pool, node, context, *, scope)` (`implementation_executor.py:275-384`) is pure dispatch, three branches, no LLM call of its own at this layer:
1. `node.implementation_id is None` (the "overwhelmingly common case" per its own docstring) → `providers.get_provider("frontier").execute(node, context)` (`:308-311`).
2. `node.implementation_id` resolves to a registered adapter/provider kind → dispatches via `build_adapter(kind)` / `providers.get_provider(kind)` (`:365-384`).
3. Unresolvable kind → honest `NodeResult(status="failure", ...)` — never silently falls back to frontier (`:312-323,371-383`).

**`FrontierProvider.execute()`** (`providers.py:218-227`) is the branch that does real coding work: it wraps `app.local_agent.runner._run_local_node` (`runner.py:360-458`), which constructs a real `agent.Agent` + `agent.RepoSandbox` (imported from `experiments/swebench_pro/agent.py`, an `OpenAI`-backed client) and runs `node_agent.run(instance, sandbox, ...)` inside `asyncio.to_thread` — a genuine tool-calling LLM turn against a real repo checkout that edits files, returning `NodeResult(status=..., data={"files_edited":..., "patch":..., "tool_calls":...})` (`runner.py:432-458`). `DeterministicProvider.execute()` (`providers.py:285-316`) runs a fixed script instead, via `SubprocessSandboxExecutor`.

**THE FORK THAT MATTERS MOST FOR THIS AUDIT — does StealthLab do the coding, or does an external Claude Code session?** Both modes are real, coexisting, and selected by `find_best_way`'s `mode` parameter:

- **`mode="full_run"` (and tier-1's per-step path) — StealthLab drives:** `server.py:1813-1837` defines a `run_node(node)` closure that itself constructs `agent.Agent(client, model, max_steps=max_steps)` and calls `await asyncio.to_thread(node_agent.run, node_instance, sandbox, ...)` — **StealthLab's own MCP server process spawns its own OpenAI-backed coding agent against a `RepoSandbox`, per node**, then passes this `run_node` closure into `run_graph_durably(pool, compiled_plan, run_node, ...)` (`server.py:1849`).
- **`mode="plan_only"` + `continue_run` — an external agent (e.g. Claude Code) drives:** `_respond_plan_only()` (`server.py:1063-1096`), quoted verbatim from its own code comment:
  > "WITHOUT calling this server's own LLM even once. No `client.chat.completions.create`, no `execute_task_graph`... This mode returns the real, composed step plan... as data the caller executes with its OWN LLM and its OWN native file/tool capabilities, then reports back via `report_execution`... StealthLab stays pure procedural memory for this call, never an executor."

  It calls `create_pending_run()` (`durable_graph.py:152-204`) → `start_run()`, but **deliberately never calls `execute_run()`** — the run stays `'pending'`. The external agent does the real coding using its own tools (Edit/Bash/etc., outside StealthLab entirely), then reports outcomes via the MCP tool `report_node_progress` (`server.py:4088-4131`, backed by `durable_run.report_node_progress`/`report_node_progress_by_id`, `durable_run.py:433-471`) — this drives the node through the **identical** `_node_claim`/`_node_finish` state machine the server's own driving loop uses (comment: "transitions through the EXACT SAME `_node_claim`/`_node_finish` mechanics"). The terminal-state fence (`durable_run.py:443-448`) still applies — a node already `succeeded`/`cancelled` can't be re-reported.

  `graph_executor.py`'s own docstring (lines 11-19) confirms the design is deliberately agnostic: "dependency-injected node execution... not a hardcoded call to any specific agent."

**Conclusion:** whether "executing a node" means StealthLab's own backend LLM agent editing your repo, or Claude Code (this session) doing the actual edits and phoning results back in, is entirely determined by which `mode` `find_best_way` was called with — the DAG/durability machinery itself (`plans.py`/`graph_executor.py`/`durable_graph.py`/`durable_run.py`) is identical either way and cannot tell the difference.

### File-collision coordination for the external-agent case

`declare_file_intent` (`server.py:3925-3991`, backed by `app.execution.coordination.declare_file_intent`) exists specifically because `plan_only` mode's real editing happens outside StealthLab's control: a node declares, with a lease (`lease_seconds`), which files/globs/symbols it expects to read/write **before** starting work, so a second concurrent agent working an overlapping run gets a detectable `FileIntentConflict` (listing the exact overlapping run/node/owner/files, `server.py:3976-3988`) instead of silently racing another agent's edits. Explicitly advisory, not an OS lock (docstring, `server.py:3936-3939`).

---

## PHASE 13 — `.stealth` forensic audit (exhaustive)

**This is the single most consequential finding of this audit, and it directly contradicts an older internal doc in this same repo (`MCP_HARDENING_DEFERRED_ITEMS.md`, self-dated 2026-09-10), which claimed `.stealth/context.md`/`run.json`/`meta.json` were "Missing entirely... No writer for any of it. State: OPEN (greenfield)."** Verified against current code, that claim is **stale** — the module was built essentially concurrently with or immediately after that doc entry.

### What `.stealth/` actually is

`backend/app/stealth/__init__.py` docstring lays out the exact layout on disk (relative to a caller-supplied `workspace_root`):
```
.stealth/
    context.md, run.json, meta.json, claims.md, procedures.md, implementations.md, run.md
    index/
        root.idx, claims.idx, procedures.idx, implementations.idx, run.idx
    events.jsonl        (append-only journal)
    .lock               (SingleWriterLock)
```

Every generated file states in its own header: **"GENERATED, not canonical. Do not hand-edit."** (per `.gitignore`'s own comment at lines 35-40, corroborated by `generator.py`'s output construction). This settles the "source of truth vs. projection" question directly: `.stealth/` is a **regenerated local projection/cache of canonical Postgres state**, never itself authoritative, and it is **gitignored** — never committed.

### File-by-file (all 9 modules read in full)

| File | Role | Path construction | Writes to disk? |
|---|---|---|---|
|`__init__.py`|package docstring + re-exports (`generate_projection`, `project_knowledge`, `read_faulted`, `open_exploration`, `close_exploration`, `list_explorations`, journal fns)|—|no|
|`atomic.py`|`atomic_write(path, content)`, `atomic_write_batch(files)`|N/A (given path)|**yes** — `tempfile.mkstemp(dir=directory, prefix=".tmp-stealth-")` → write → `os.fsync` → `os.replace(tmp_path, path)` (lines 24-40, 43-76). True write-temp→fsync→rename.|
|`errors.py`|`StealthProjectionError(Exception)`|—|no|
|`exploration.py`|`exploration_id()`, `open_exploration()`, `close_exploration()` (optionally captures a private global Claim via `app.services.claims.capture_claim`), `list_explorations()`, `render_exploration_page()`|delegates to `journal.py`|indirectly (via journal)|
|`faults.py`|page-fault mechanism: `read_faulted`/`_write_faulted` at `.stealth/index/faulted.json` (lines 42, 53-70); `project_knowledge()` merges global Postgres objects into `.stealth/claims.md`/`procedures.md`/`implementations.md` + `.idx` files|`os.path.join(_sdir(workspace_root), "index", "faulted.json")`|yes, via `atomic_write_batch` (lines 239-297)|
|`format.py`|pure `.idx`/`.md` render/parse logic (`IdxRow`, `RunIdxRow`, `RootRow`, `MdBlock`, `render_idx`, `render_md_page`, `parse_idx`, `parse_md_page`)|N/A|no — string-only|
|`generator.py`|`generate_projection()` — top-level regenerator|`stealth_dir = os.path.join(workspace_root, STEALTH_DIRNAME)`; `index_dir = os.path.join(stealth_dir, "index")` (lines 452-453)|yes — builds a `(path, content)` plan list, `atomic_write_batch(plan)` under a `SingleWriterLock`, `meta.json` written last (lines 467-484)|
|`journal.py`|`.stealth/events.jsonl` append-only journal + `SingleWriterLock` (`.stealth/.lock`, `O_CREAT\|O_EXCL`, stale-steal after 60s, Windows `PermissionError` race handled)|`_stealth_dir()` → `os.path.join(workspace_root, STEALTH_DIRNAME)` (lines 41-50)|yes|
|`legacy_context.py`|`STEALTH_DIRNAME = ".stealth"` (line 19), `CONTEXT_MD_MAX_BYTES = 8192`; original B35 renderers `_render_context_md`/`_render_run_json`/`_render_meta_json`|—|indirectly (rendering only; writing done via `atomic.py`/`generator.py`)|

**Byte budgets enforced:** `ROOT_IDX_MAX_BYTES=4096`, `TYPE_IDX_MAX_BYTES=65536` (`format.py:30,34`), `CONTEXT_MD_MAX_BYTES=8192` (`legacy_context.py:23`) — enforced/raised in `generator.py:400-411`.

**Staleness/revision tracking:** `meta.json`'s `projection_revision`/`sync_cursor` fields (`legacy_context.py:159-168`), tied to the journal's monotonic `seq`.

**Regeneration trigger:** per `.gitignore`'s comment (lines 35-40) and `server.py:3738-3743`'s docstring, `.stealth/{context.md,run.json,meta.json}` is refreshed "on every `find_best_way`/`continue_run` call given a `repo_path`" — i.e. call-triggered, not a background daemon.



### Does `.stealth`/`.stealthlab` exist on disk right now, in this checkout?

```
$ ls -la .stealth .stealthlab
ls: cannot access '.stealth': No such file or directory
ls: cannot access '.stealthlab': No such file or directory
```
**Neither exists.** Both are lazily generated only when a real MCP tool call supplies a real `repo_path`/`workspace_root` and reaches the projection code — which has not happened in this checkout. This is consistent with "generated, not canonical" — nothing in this repo's own working directory currently depends on `.stealth/` existing.

### Event → ... → Procedure, restated purely in terms of `.stealth`

| Layer | `.stealth/` representation |
|---|---|
| Event | **none** — Postgres only (`execution_run_events`) |
| Trace | **none** — Postgres only (`execution_runs.trace_id`) |
| Episode | **none** — Postgres only (`episodes`) |
| Artifact | **none** (the layer itself doesn't exist for local execution — see Phase 4-12) |
| Observation | **none directly** — but `project_knowledge()`/`faults.py` can pull *global* claims/procedures (which may ultimately be evidenced by observations) into `.stealth/claims.md` etc. as a read-side projection |
| Claim | **yes, indirectly** — `.stealth/claims.md` + `claims.idx`, populated by `project_knowledge()`/`faults.py` from canonical Postgres `knowledge_nodes`, on demand |
| Evidence | **none directly** |
| Procedure | **yes, indirectly** — `.stealth/procedures.md` + `procedures.idx`, same mechanism |
| Run/session working context | **yes, directly** — `.stealth/context.md`, `run.json`, `meta.json`, `run.md` — this is the one layer `.stealth/` represents as a first-class, purpose-built thing (an agent-facing router/context snapshot for the *current* run, not a copy of the durable Event/Trace/Episode/Claim/Procedure graph) |

So: **`.stealth` is NOT the knowledge graph.** It is (a) a generated, on-demand, per-repo-checkout **read-side cache/projection** of a *subset* of canonical Postgres Claims/Procedures (via `project_knowledge`), plus (b) a purpose-built **current-run working-context snapshot** (`context.md`/`run.json`/`meta.json`) that has no equivalent Postgres row of its own — it's a rendering, not a fact table. It is never a write-path for Events/Traces/Episodes/Observations/Evidence, and it is never durable across a fresh checkout (gitignored, not committed, regenerated from Postgres on the next call).

---

## PHASE 14 — Filesystem vs Postgres matrix

| Data | Local filesystem | `.stealth/` | Postgres | In-memory | Other |
|---|---|---|---|---|---|
| Execution Event | — | — | `execution_run_events` (authoritative) | — | — |
| Trace (causal chain id) | — | — | `execution_runs.trace_id` (authoritative) | — | — |
| Episode | — | — | `episodes` (authoritative) | — | — |
| Local-execution "Artifact" | — | — | not a distinct row (folds into Event→Observation) | — | — |
| Observation | — | — | `observations` + `observation_events` (authoritative) | — | — |
| Claim | — | `claims.md`/`.idx` (projection, on-demand) | `knowledge_nodes` (authoritative) | — | — |
| Evidence | — | — | `evidence` (authoritative) | — | — |
| Procedure | — | `procedures.md`/`.idx` (projection, on-demand) | `procedures` (authoritative) | — | — |
| Current-run working context | — | `context.md`/`run.json`/`meta.json`/`run.md` (only authoritative copy of *this rendering*, but derived from Postgres each time) | source data lives in `execution_runs`/`episodes`/etc. | — | — |
| Old local SQLite store | — | — | — | — | **deleted** (`local_store.py`, commit `32092ce`) |
| MCP bearer token | — | — | — | — | env var `STEALTHLAB_MCP_TOKEN` (process env, not DB/FS) |

**Synchronization / rehydration:**
- Postgres is written first and is authoritative for every durable layer (Event/Trace/Episode/Observation/Claim/Evidence/Procedure).
- `.stealth/` is written **later, on demand**, purely as a derivative rendering — never the other way around. No code path reads `.stealth/` back into Postgres.
- If `.stealth/` is deleted: no data loss — it's regenerated (in full) on the next `find_best_way`/`continue_run`/`project_knowledge` call with a `repo_path`. This is proven by its "GENERATED, not canonical" self-declaration and its exclusive write path being `generate_projection()`/`project_knowledge()`, both of which read from Postgres.
- If Postgres is unavailable: **NOT ESTABLISHED FROM CODE** what `.stealth/`-dependent tools do (no fallback-to-stale-`.stealth/` logic was found or ruled out in this pass).
- If Postgres has data but `.stealth/` doesn't (the actual current state of this checkout): this is the *normal*, expected state — `.stealth/` is lazily built, not eagerly kept in sync.
- Postgres can fully reconstruct `.stealth/` (that's its only write trigger). `.stealth/` **cannot** reconstruct Postgres — it's not even attempted; nothing reads `.stealth/*.md`/`*.json` back into a write path except the exploration write-back (`exploration.py`'s `close_exploration` optionally calling `capture_claim` — a *new* private Claim describing the exploration's resolution, not a replay of `.stealth/`'s existing content into Postgres).
- The old `.stealthlab/*.db` SQLite store, when it existed, was a genuinely separate local substrate (per its own now-stale `.gitignore` comment: "this checkout's own local substrate, not global data") — but it no longer exists in code, so it's moot for current behavior.

---

## PHASE 15 — Retrieval (fresh Claude session → prior local knowledge)

**Literal call chain for `search_procedures`:**
```
MCP tool "search_procedures"
  -> server.py:search_procedures()                              [L2555]
     -> app/services/embeddings.py: Embedder().embed_one(task, input_type="query")   [server.py:2602-2603]
     -> app/services/applicability.py:find_applicable_procedures()                    [server.py:2604; def L676]
          -> queries Postgres `procedures` table directly (asyncpg.Pool) --
             cold-start gate -> hard-constraint cascade -> similarity ranking of survivors
     -> reshaped to JSON at server.py:2615-2623:
        [{"id","procedure_id","version","name","goal","verification_state","similarity"}, ...]
  -> MCP tool response: JSON string (text content) returned to the calling client
```

**Literal call chain for `get_relevant_claims`:**
```
MCP tool "get_relevant_claims"
  -> server.py:get_relevant_claims()          [L2670]
     -> HybridRetriever (vector+lexical)
          -> queries `knowledge_nodes` WHERE node_type='claim'   (Postgres)
     -> bounded Claim references, top_k capped at 25
```

**`find_best_way`** (the heavier path, `server.py:1222`): tier 1 calls `find_applicable_procedures()` directly (same function as `search_procedures` uses); tier 2 (when `repo_path` given) executes, and on no match, ad-hoc-captures via `capture_procedure()` (Phase 12) then persists an execution plan (`app/execution/plan_persistence.py`).

**No local SQLite fan-out anymore** — `unified_retrieval.py` **does not exist** in the current tree (only referenced in `runner.py`'s comments, in past tense, describing a removed mechanism). All retrieval is Postgres-only.

**Is anything automatic?** No. `prompts.py`'s own docstring states its Prompts are host-loaded, not autonomous; `resources.py`'s Resources are pull-based (`stealth://...` URIs a client must request). **No code in `server.py`, `prompts.py`, or `resources.py` implements a system-prompt injection, mandatory pre-tool-call hook, or auto-attached resource at session start.** Retrieval is entirely opt-in — the calling agent (Claude, or whatever MCP host it runs inside) must itself decide to call `search_procedures`/`find_best_way`/`get_relevant_claims`/request a Resource/load a Prompt.

**Serialization / limits:** `search_procedures(limit=5)` default; `get_relevant_claims(top_k=10)` default (capped 25 internally per the finding above); `get_claim_graph(limit=200)`; all responses are JSON-encoded strings (MCP tool return type is uniformly `str`).

---

## PHASE 16 — Failure paths (what's actually established)

| Scenario | Established behavior |
|---|---|
| Execution crashes mid-run | `execution_run_nodes.lease_expires_at` times out; `resume_run()` (`durable_run.py:859-889`) detects this via `lease_expires_at < now()` and re-arms/parks nodes — **but this must be called explicitly by something outside this repo** (an API/external caller), not automatically. |
| Process dies halfway through | Same as above — no automatic sweep on restart (see Phase 17). |
| Episode never closes | `episodes.end_ts` stays NULL forever unless something calls `close_episode_for_run` — **NO EXPLICIT HANDLING FOUND** for auto-detecting/timing-out a stuck-open episode. |
| Episode closes as failure | `_close_episode_and_enqueue_consolidation` is called from the failure/verification-failure paths too (`durable_run.py:746,803`) — consolidation still runs, but Procedure extraction is skipped (`outcome == "success"` gate at `ingestion_jobs.py:1198`) and Evidence is still recorded with `outcome_status` reflecting the failure. |
| Consolidation fails (exception mid-handler) | **NOT ESTABLISHED FROM CODE** — no explicit try/except-and-requeue logic was found inside `handle_consolidate_local_episode` itself; whether the generic job-queue runner retries a failed job is **NOT ESTABLISHED FROM CODE** in this pass (the job dispatch/retry loop itself was not read). |
| Observation creation fails | If `extract_deterministic_observations_from_run_event` returns `None` for an event type it doesn't recognize, that event is silently skipped (no error) — by construction of the loop, not an explicit failure handler. |
| Claim creation fails / no-ops | Explicit, deliberate: `capture_claim` logs and returns `None` when no anchor/provenance exists (Phase 12) — this is documented behavior, not a crash. |
| Procedure creation fails | **NOT ESTABLISHED FROM CODE** what happens if `extract_procedure()` throws inside `handle_consolidate_local_episode` (line 1198-1232) — no try/except was confirmed around that specific call in this pass. |
| `.stealth` write fails | `atomic_write`/`atomic_write_batch` use fsync+rename; **NOT ESTABLISHED FROM CODE** what the caller does if `SingleWriterLock` acquisition fails beyond the documented 60s stale-steal (i.e., whether a caller silently proceeds without a fresh projection, or surfaces an error to Claude) — not traced in this pass. |
| Postgres write fails | **NOT ESTABLISHED FROM CODE** in this pass (transaction/rollback semantics of `durable_run.py`'s connections weren't traced end-to-end here). |
| Retrieval finds nothing | `find_applicable_procedures` returns `candidates == []` — proven by `test_retrieval_negative_control_e2e.py::test_genuinely_unrelated_query_abstains_rather_than_returning_the_least_bad_option` (Phase 23). |
| Retrieval finds an old irrelevant procedure | **NOT ESTABLISHED FROM CODE** in this pass — the similarity/hard-constraint cascade in `find_applicable_procedures` was not read in enough depth to state its false-positive behavior precisely. |
| Duplicate execution / re-consolidation | Explicitly idempotent: `metadata.consolidated_at` guard (episode) + observed `claim_count == 1` after re-running consolidation twice, per `test_consolidation_produces_observations_claim_and_evidence` (Phase 23). |
| Parent/child run | `trace_id` inheritance (Phase 4-12, Trace section) — a child run always inherits the parent's `trace_id`; each child still gets its own `execution_run_events`/episode. |
| MCP connection drops | **NOT ESTABLISHED FROM CODE** in this pass (transport-level reconnect behavior belongs to the `mcp` SDK, not this repo's own code). |

---

## PHASE 17 — Restart/crash semantics

| Layer | Survives process/machine restart? | Recovered how? |
|---|---|---|
| Event | Yes — Postgres row, committed per-transaction alongside the state change it documents. | Nothing to recover — it's already durable. |
| Trace (`trace_id`) | Yes — plain column on `execution_runs`. | N/A |
| Episode | Yes if the transaction that opened it committed; stays open (`end_ts IS NULL`) forever if the process died before closing it. | **No explicit "recover all open episodes on boot" code path was found** — `main.py`'s `lifespan()` (lines 29-74) only does safety-gate asserts, `create_pool()`, and starts the generic `ingestion_scheduler` (which drains `ingestion_jobs`, not execution-run-specific recovery). |
| execution_runs / execution_run_nodes | Yes — Postgres rows persist; a node's `lease_expires_at` will eventually be stale. | `resume_run()` exists (`durable_run.py:859-889`) but is **explicitly caller-driven** — nothing calls it automatically at startup. |
| Observation/Claim/Evidence | Yes, if already committed before the crash. | N/A — no rehydration needed, they're just rows. |
| Procedure | Same as above. | N/A |
| `.stealth/*` | **No** — gitignored, never committed, and this audit found the directory doesn't even exist in the current checkout. | Regenerated fresh on the next `find_best_way`/`continue_run`/`project_knowledge` call — from Postgres, not from any prior `.stealth/` state. |

**Startup code performing rehydration:** none found beyond generic pool creation and the job scheduler starting (which processes whatever's already queued in `ingestion_jobs`, including any `consolidate_local_episode` jobs that were enqueued-but-not-yet-run before a crash — those would survive and eventually run, since the enqueue is a committed Postgres row). **No explicit scan-and-resume of orphaned `execution_runs`/open `episodes` exists — NOT ESTABLISHED FROM CODE otherwise.**

## PHASE 17B — CORRECTION: does anything actually drain the `consolidate_local_episode` job? (found during the global-scope pass, materially qualifies Phase 4-12/17 above)

The statement just above — "the job scheduler starting... processes whatever's already queued" — needs a load-bearing caveat that a later, deeper pass surfaced. There are **two separate ASGI apps** in this repo, and they are not both started by the same command:

- `backend/app/main.py` — a FastAPI app (`app = FastAPI(..., lifespan=lifespan)`, `main.py:81`) whose `lifespan()` calls `ingestion_scheduler.start(app)` (`main.py:69`). **This is the only place the job-queue scheduler is started.**
- `backend/app/mcp_server/server.py` — the MCP server (`app = server.streamable_http_app()`, `server.py:447`), the one `.mcp.json` actually points at. Its own `lifespan()` (`server.py:154-170`) does **only**: assert `DATABASE_URL` is set, `create_pool()`, yield `{"pool": pool}`, close pool on shutdown. **Zero references to `ingestion_scheduler` anywhere in `server.py`** (confirmed by grep, 0 hits). The file's own comment at `server.py:327-329` calls itself "the SECOND ASGI app in this project" — i.e. the codebase itself is aware these are two independent processes.

`ingestion_scheduler.py`'s driving loop (`_loop()`, lines 129-166) is a plain `asyncio.create_task` `while True: sleep(interval); tick` loop (no cron, no external scheduler) — `settings.ingestion_auto_interval_seconds` defaults to `60` (`config.py:315`), `settings.ingestion_auto_enabled` defaults to `True` (`config.py:313`). **But** `settings.ingestion_auto_mode` defaults to `"local"` (`config.py:314`), and per the scheduler's own code/docstring (lines 16-20, 146-153), `"local"` mode was made a no-op in the same P5 commit that deleted the SQLite store — only `INGESTION_AUTO_MODE=global`, set explicitly, makes each tick actually call `_run_global_tick` → `app.api.admin.process_ingestion(...)`, which is the function that claims/dispatches rows from the shared `ingestion_jobs` table (the same table + same `JOB_HANDLERS` dict that `consolidate_local_episode`, `extract_procedure_from_episode`, and the document-ingestion job types all share — one queue, one dispatcher, confirmed by `ingestion_jobs.py:422` `JOB_HANDLERS` and its registration lines).

**Net effect, stated plainly:** if a user's actual running setup is *only* `.mcp.json`'s configured MCP server (`python -m uvicorn app.mcp_server.server:app` or stdio `server.run()`) — which is the only process this whole audit was originally scoped to — then:
1. Local execution through `find_best_way` still writes `execution_runs`/`execution_run_nodes`/`episodes`/`execution_run_events` correctly (Phase 4-12), because those writes happen synchronously inside `durable_run.py`, in the same request, not via the job queue.
2. The episode-closure code still does commit an `INSERT INTO ingestion_jobs (job_type='consolidate_local_episode', ...)` row (Phase 4-12) — that part is real and unconditional.
3. **But nothing in the MCP-server-only process ever claims or runs that job.** Draining requires `backend/app/main.py` to also be running as a separate process, AND `INGESTION_AUTO_MODE=global` to be set (not the shipped default of `"local"`). Absent both, the row sits in `ingestion_jobs` forever, and the entire downstream chain this audit traced in Phase 4-12 (Observations, Claims, Evidence, Procedure candidates) **never actually executes** — not because the code is wrong, but because nothing calls it.
4. `NOT ESTABLISHED FROM CODE`: whether any deployment of this project actually runs `main.py` alongside the MCP server with `INGESTION_AUTO_MODE=global` set — that is an external ops/deployment fact, not something in this repo. The repo's own `admin.py` docstring (lines 7-10, 160-163) frames `/v1/admin/ingestion/process` as something to be called "from a cron job... or curl" — i.e., the repo deliberately leaves this wiring outside itself.

This does not change any of the code-level facts established in Phase 4-12 (the handler, the schema, the tests all genuinely exist and pass against a real DB when invoked) — it changes the answer to "does this happen automatically for a user who just runs the MCP server as configured in `.mcp.json`," which is: **the enqueue happens; the drain does not, unless a second process is also running with a non-default setting.**

---

## PHASE 18 — End-to-end verbatim example (webhook ingestion task)

```
[1] USER: "Build a production-ready idempotent webhook ingestion endpoint for
           Stripe-style payment events..."

[2] Claude decides whether to use StealthLab MCP at all.
    THERE IS NO CODE-LEVEL EVIDENCE THAT CLAUDE AUTOMATICALLY CALLS ANY
    STEALTHLAB TOOL — this decision happens entirely outside this repository
    (in the calling agent/host). This transcript assumes Claude DOES decide
    to call tools, to illustrate what would happen if it did.

[3] [Claude action]      Calls MCP tool `search_procedures`
    [MCP tool call]      search_procedures(task="idempotent webhook ingestion
                          for Stripe-style payment events with signature
                          verification and replay protection", limit=5)
    [server function]    server.py:search_procedures() [L2555]
    [downstream]         Embedder().embed_one(...) -> find_applicable_procedures()
                          [app/services/applicability.py:676] -> SELECT ... FROM
                          procedures (Postgres)
    [storage op]         READ only, Postgres `procedures` table
    [return value]       Either [] (if this checkout has never captured a
                          matching procedure -- true for a fresh checkout, per
                          Phase 13's finding that .stealth/ doesn't even exist
                          here yet) or a JSON list of
                          {"id","procedure_id","version","name","goal",
                           "verification_state","similarity"} dicts
    [what Claude sees]   A JSON string tool result -- Claude decides for
                          itself whether/how to use it; nothing forces it to.

[4] [Claude action]      Calls MCP tool `find_best_way` to actually orchestrate
                          the coding task (assuming Claude chooses tier-2/local
                          execution rather than coding unassisted)
    [MCP tool call]      find_best_way(task_description="...", repo_path=
                          "C:\\Users\\user\\stealth-lab", mode="auto",
                          workspace_id=<workspace_id or None>)
    [server function]    server.py:find_best_way() [L1222]
    [downstream]         tier 1: find_applicable_procedures() (same as [3]).
                          On no verified match: tier 2 execution begins ->
                          app/execution/durable_run.py:start_run(...)
    [storage op]         WRITE: execution_runs row (id=<execution_run_id>,
                          status='pending'->'running', trace_id=<trace_id>
                          via uuid7() since this is a root run) + one
                          execution_run_nodes row per planned step, all inside
                          one Postgres transaction
                          WRITE: episodes row via open_episode_for_run(conn,
                          run_id, ...) -- episode_type='execution',
                          execution_run_id=<execution_run_id>,
                          session_id=<trace_id>, end_ts=NULL -- SAME transaction
                          WRITE: execution_run_events row, event_type=
                          'run_created', via record_run_created() (recorder.py)
                          -- SAME transaction
    [return value]       A procedure_run_id / execution_run_id string,
                          returned as part of the tool's JSON response
    [what Claude sees]   The run has started; Claude/the executor proceeds to
                          perform the actual coding work (writing the FastAPI
                          endpoint, signature verification, idempotency key
                          check, retry logic, and the 4 required test classes)
                          via whatever execution substrate find_best_way's
                          tier-2 machinery drives (graph_executor.py /
                          implementation_executor.py -- NOT traced in this
                          pass at the level of "which shell commands run").

[5] As execution proceeds, each meaningful step emits an Event:
    [downstream]         durable_run.py calls record_node_started /
                          record_tool_called / record_node_succeeded /
                          record_node_failed / record_verification_* /
                          record_child_run* (recorder.py:56-297), each ->
                          record_event() -> INSERT INTO execution_run_events
                          (execution_run_id=<execution_run_id>, node_order=N,
                          event_type=..., payload={...}) -- Postgres, one row
                          per event, seq auto-incremented, append-only
                          (trigger tg_execution_run_events_frozen blocks any
                          later UPDATE/DELETE of these rows)
    [example payloads]   event_type='tool_result' (payload may include a
                          representation of a test run passing/failing);
                          event_type='artifact_recorded' if the executor emits
                          one for, say, the generated webhook_router.py diff
                          or the pytest output (there's no separate Artifact
                          table -- this event goes straight to Observation
                          in step [7], never to ingested_artifacts)
    [linking]            execution_run_id ties every event to the run; there
                          is no separate "Trace" row -- the run's own
                          trace_id column IS the trace; node_order links
                          events to a specific execution_run_nodes row

[6] Execution finishes. Suppose all 4 required test classes (duplicate
    delivery, invalid signature, concurrency, retry) pass.
    [downstream]         durable_run.py's success path (around L670) sets
                          execution_runs.status='succeeded' (guarded by
                          trg_execution_runs_status_transition_fence, which
                          only allows running->succeeded), sets
                          execution_runs.final_outcome='success', then calls
                          _close_episode_and_enqueue_consolidation(conn,
                          run_id, outcome="success") [durable_run.py:613-634]
    [storage op]         WRITE: episodes.end_ts=now(),
                          episodes.metadata = metadata || {"outcome":"success"}
                          [episode.py:89-109]
                          WRITE: INSERT INTO ingestion_jobs (job_type,
                          payload) VALUES ('consolidate_local_episode',
                          {"episode_id": <episode_id>,
                           "execution_run_id": <execution_run_id>})
    [what Claude sees]   The tool response Claude eventually gets back from
                          find_best_way/report_execution/verify_completion
                          reflects success; Claude has NO direct visibility
                          into the consolidation job -- it runs asynchronously,
                          off the request path.

[7] Some time later, the job queue runner picks up the
    'consolidate_local_episode' job.
    [downstream]         handle_consolidate_local_episode(pool, payload)
                          [ingestion_jobs.py:1002]
    Step A (Observations, deterministic, unconditional):
      for each meaningful execution_run_events row (node_succeeded,
      node_failed, artifact_recorded, verification_completed, tool_result),
      in seq order:
        extract_deterministic_observations_from_run_event(event)
          -> {"observation_type": ..., "label": ..., "properties": {...}}
        persist_observation(pool, observation_type=..., label=...,
          extractor_kind='deterministic', execution_run_event_ids=[event_id])
          -> INSERT INTO observations (...) VALUES (...) RETURNING id
          -> INSERT INTO observation_events (observation_id,
             execution_run_event_id) VALUES (<obs_id>, <event_id>)
      [storage] Postgres `observations` + `observation_events`.
      [what links it to Event] execution_run_event_id FK, exact 1:1 per event
      that produced it.

    Step B (Claim, LLM-derived, one pass):
      extract_claim_candidates_cached(rendered_episode_text, ...)
        -> e.g. a candidate statement like "idempotency keys computed from
           the Stripe event id reliably prevent duplicate processing under
           concurrent delivery in this codebase"
      persist_claim_candidate(pool, candidate, observation_id=
          observation_dicts[0]["id"], ...)
        -> claim_extraction.py:548-572 -> capture_claim(pool,
           statement=..., task_ids=[], observation_id=<obs_id>, ...)
        capture_claim's no-op check passes because observation_id is set
        (has_provenance_ref=True) -- it WILL write.
        -> INSERT INTO knowledge_nodes (node_type='claim', name=<statement>,
           properties={...}, embedding=<vec>, provenance='company_ingested',
           owner_id=<owner>, visibility=<episode's visibility>) RETURNING id
        -> INSERT INTO claim_sources (claim_id, observation_id) VALUES
           (<claim_id>, <obs_id>) ON CONFLICT DO NOTHING
        -> one episode_links row (episode_id=<episode_id>, ...) since
           justification_episode_id was also threaded through
      [dedup] best-effort _detect_and_record_equivalence() runs afterward --
      does NOT block or merge; only flags a possible relation to an existing
      similar claim, asynchronously.

    Step C (Evidence):
      record_claim_evidence(pool, claim_id=<claim_id>,
        evidence_type="execution_result", outcome_status="success",
        success_criteria={...}, created_by=<consolidation_extractor>,
        owner_id=<owner>, visibility=<visibility>)
        -> INSERT INTO evidence (id, evidence_type='execution_result',
           target_type='claim', target_id=<claim_id>, outcome_status=
           'success', ...) RETURNING id
        -> best-effort claim_belief.recompute_claim_belief(pool, <claim_id>)
           -> knowledge_nodes.belief_score updated (execution_result is a
              "strong" evidence class: weight 1.0, ceiling 1.0)

    Step D (Procedure -- only because outcome == "success"):
      extract_procedure(pool, ... ) attempts to synthesize a Procedure
      candidate from this episode -- e.g. name="idempotent webhook ingestion
      with signature verification", goal=<task_description>, steps=[...],
      scope_type/scope_entity_id derived the same way as [3]'s ad-hoc path
      IF this is the code path that reaches capture_procedure (Phase 12's
      "automatic, per-episode" path) -- exact internal steps of
      extract_procedure() itself were NOT read in this pass beyond
      confirming it's called here; treat its own internal logic as
      NOT ESTABLISHED FROM CODE beyond this call site.
      -> capture_procedure(...) -> INSERT INTO procedures (...)
         verification_state defaults to 'candidate'

    Step E (always, last):
      UPDATE episodes SET metadata = metadata || {"consolidated_at": now()}
      WHERE id = <episode_id>

[8] SUBSEQUENT RETRIEVAL: a fresh Claude session, later, gets a similar task
    ("add webhook replay protection to our payments service").
    [Claude action]      IF it decides to call search_procedures(task="...")
                          -- again, nothing forces this call
    [server function]    server.py:search_procedures() -> find_applicable_
                          procedures() -> SELECT ... FROM procedures WHERE
                          ... (visibility/scope predicate, similarity rank)
    [return value]       The candidate Procedure from [7] Step D, IF its
                          scope_type/scope_entity_id/visibility match this new
                          caller's resolved identity/workspace (private
                          procedures are invisible to other owners -- proven
                          by test_search_privacy_leak_e2e.py, Phase 23), AND
                          IF its verification_state satisfies the query's
                          require_verified filter (a brand-new 'candidate'
                          procedure would be EXCLUDED from a
                          require_verified=True search -- NOT ESTABLISHED
                          FROM CODE exactly what promotes 'candidate' to a
                          verified state in this pass, beyond noting
                          check_applicability's own require_verified=True
                          default parameter)
    [what Claude sees]   Either the candidate procedure (if scope/visibility/
                          verification_state line up) or an empty list.

[9] .stealth/ involvement in this whole transcript: NONE of the above (Event/
    Trace/Episode/Observation/Claim/Evidence/Procedure) ever touches
    .stealth/. The only point `.stealth/` would be touched is if Claude
    separately called project_knowledge(repo_path=..., query="webhook
    idempotency") or find_best_way with a repo_path (which refreshes
    .stealth/{context.md,run.json,meta.json} as a side effect, per
    server.py:3738-3743) -- and even then, that's a rendering of the SAME
    Postgres facts above into a local file, not a new source of truth.
```

---

## PHASE 19 — Call graph (condensed)

```
mcp_server/server.py:find_best_way()
  -> app/services/applicability.py:find_applicable_procedures()
       -> Postgres SELECT (procedures)
  -> app/execution/durable_run.py:start_run()
       -> Postgres INSERT (execution_runs, execution_run_nodes)
       -> app/execution/episode.py:open_episode_for_run()
            -> Postgres INSERT (episodes)
       -> app/execution/recorder.py:record_run_created() -> record_event()
            -> Postgres INSERT (execution_run_events)
  ... (execution proceeds; graph_executor.py / implementation_executor.py --
       internals not traced in this pass) ...
  -> durable_run.py:<success path, ~L670>
       -> Postgres UPDATE execution_runs.status/final_outcome
            (trigger: trg_execution_runs_status_transition_fence)
       -> durable_run.py:_close_episode_and_enqueue_consolidation()
            -> episode.py:close_episode_for_run() -> Postgres UPDATE episodes
            -> Postgres INSERT ingestion_jobs(job_type='consolidate_local_episode')

[async, separate job-runner process/loop -- dispatch mechanism not traced]
ingestion_jobs.py:JOB_HANDLERS['consolidate_local_episode']
  -> ingestion_jobs.py:handle_consolidate_local_episode()
       -> app/services/observations.py:extract_deterministic_observations_from_run_event()
       -> app/services/observations.py:persist_observation()
            -> Postgres INSERT (observations, observation_events)
       -> app/services/claim_extraction.py:extract_claim_candidates_cached() [LLM]
       -> app/services/claim_extraction.py:persist_claim_candidate()
            -> app/services/claims.py:capture_claim()
                 -> Postgres INSERT (knowledge_nodes, edges, claim_sources, episode_links)
       -> app/services/claim_evidence.py:record_claim_evidence()
            -> Postgres INSERT (evidence)
            -> app/services/claim_belief.py:recompute_claim_belief()
                 -> Postgres UPDATE knowledge_nodes.belief_score
       -> (if outcome=='success') app/services/procedure_extraction:extract_procedure()
            -> app/services/procedures.py:capture_procedure()
                 -> Postgres INSERT (procedures)
       -> Postgres UPDATE episodes.metadata.consolidated_at

[separately, on demand, never in the chain above]
mcp_server/server.py:project_knowledge() / find_best_way(repo_path=...)
  -> app/stealth/generator.py:generate_projection()
       -> reads Postgres (procedures, knowledge_nodes, ...)
       -> app/stealth/atomic.py:atomic_write_batch()
            -> local filesystem: <workspace_root>/.stealth/{context.md,run.json,
               meta.json,claims.md,procedures.md,implementations.md,index/*.idx}
```

---

## PHASE 20 — Data lineage table

| Item | Original source | Transformation | Persistence | Consolidation | Retrieval | MCP serialization | Reaches Claude? |
|---|---|---|---|---|---|---|---|
| Execution event | `record_event()` call inside `durable_run.py` | none — raw dict payload | `execution_run_events` (Postgres) | consumed by consolidation, never itself surfaced via a retrieval tool | none (no MCP tool reads raw events directly — `inspect_run` may, not confirmed) | `inspect_run` tool possibly (NOT ESTABLISHED FROM CODE in this pass) | only if Claude calls `inspect_run` |
| Observation | `execution_run_events` row | `extract_deterministic_observations_from_run_event()` (pure fn) | `observations`+`observation_events` (Postgres) | feeds Claim extraction | no dedicated MCP retrieval tool for raw Observations found | — | not directly (no tool found) |
| Claim | Observation (via `observation_id` anchor) | LLM (`extract_claim_candidates_cached`) | `knowledge_nodes` (Postgres) | dedup-detection only (never merged) | `get_relevant_claims`, `get_claim_graph`, `search_procedures`'s ranking may implicitly use claims | JSON via those tools | **yes**, if Claude calls one of those tools |
| Evidence | `execution_runs.final_outcome` | `record_claim_evidence()` | `evidence` (Postgres) | feeds `recompute_claim_belief` | no direct MCP tool found returning raw Evidence rows (belief score surfaces indirectly via claim fields) | — | indirectly, via a Claim's belief_score field if exposed |
| Procedure | episode consolidation (`extract_procedure`, success-only) or ad-hoc `find_best_way` capture | `capture_procedure()` | `procedures` (Postgres) | `supersede_procedure()` on later versions | `search_procedures`, `get_procedure`, `check_applicability` | JSON | **yes**, if Claude calls one of those tools |
| `.stealth/` context snapshot | live Postgres state at call time | `generate_projection()` | local filesystem only, gitignored | N/A (not consolidated, regenerated) | read only by whatever local agent process opens the file directly (not an MCP tool return — MCP tools return JSON strings, not file contents, per this audit's tool table) | N/A | **NOT ESTABLISHED FROM CODE** that any MCP tool response includes `.stealth/` file contents verbatim — it appears to be for a co-located local process/CLI to read off disk, not shipped back through MCP |

---

## PHASE 21 — Intended vs. actual

| Intended behavior | Actual implementation | Evidence | Gap? |
|---|---|---|---|
| Every local execution creates events | Yes | `durable_run.py`'s `record_*` calls at every state transition | No |
| Events become traces | Partially — `trace_id` is a column, not a distinct persisted "Trace" object; the `traces` table exists but is unused by execution | `durable_run.py:150-163`; `db/01_ontology.sql:103` unused | Terminology gap, not a functional one |
| Traces become episodes | Not really — an Episode is opened per `execution_run`, independent of `trace_id`; multiple runs (hence one trace) can each have their own episode | `episode.py:35-86`, `db/79...sql:29-37` (unique index is per `execution_run_id`, not per `trace_id`) | Yes — "trace → episode" isn't the actual cardinality; it's "run → episode", and "run(s) → trace" |
| Episodes close correctly | Yes, at exactly 3 real terminal-transition call sites, idempotently | `durable_run.py:613-634,670,746,803` | No |
| Closure triggers consolidation | Yes | same as above | No |
| Observations are created | Yes, deterministically, unconditionally | `ingestion_jobs.py:1071-1084` | No |
| Observations cite execution events | Yes, via `observation_events.execution_run_event_id` FK | `db/79...sql:46-79` | No |
| Claims are derived | Yes, but LLM-dependent and best-effort; explicitly can no-op if no LLM client or no provenance | `claim_extraction.py`, `claims.py:282-317` | Partial — "test_consolidation_without_llm_client..." proves zero Claims can result |
| Evidence is linked | Yes, one row per persisted claim per episode (not per individual test) | `ingestion_jobs.py:1186-1192` | Partial — coarser granularity than "per test assertion" |
| Procedures are derived | Yes, but ONLY on `outcome=='success'`, and via two DIFFERENT mechanisms (auto per-episode vs. admin-batch `extract_procedure_from_episode`) that are easy to conflate | `ingestion_jobs.py:1198-1232` vs `:781-880` | Yes — this is a real, non-obvious bifurcation worth knowing about |
| `.stealth/` stores local knowledge | No — it stores a *rendering* of a subset of it, on demand, never durably | Phase 13 | Yes, if "stores" implies source-of-truth; No, if "stores" means "can display" |
| Postgres is authoritative | Yes, for every durable layer | Phase 14 | No |
| Local knowledge survives restart | Yes for everything in Postgres; No for `.stealth/` (never meant to) | Phase 17 | No (by design) |
| Fresh agents can retrieve prior knowledge | Yes, mechanically (Phase 15's call chain is real and tested) — but only IF the fresh agent chooses to call a retrieval tool | Phase 15, Phase 23 | **Yes — retrieval is opt-in, not automatic; this is the single biggest gap between "the knowledge exists" and "Claude actually receives it"** |
| Repository/user scope works | Yes, exactly as coded (workspace_id ⇒ repository scope, else resolved auth identity ⇒ user scope) | Phase 12's ad-hoc scope section | No |
| MCP exposes retrieval | Yes — multiple tools | Phase 2's tool table | No |
| Claude automatically uses retrieval | **No** | Phase 15 | **Yes, gap** |
| Claude automatically records execution | Only if Claude routes the coding work through `find_best_way`'s tier-2 execution — a coding task done by Claude directly (outside MCP tool calls) creates zero events/episodes/observations | Phase 2 tool list; nothing hooks a bare Claude Code edit/bash session into `durable_run.py` | **Yes, gap — using StealthLab MCP tools like `search_procedures` alone does NOT create any execution trail; only `find_best_way`'s own orchestrated execution does** |
| Failures are captured | Partially — episode/evidence still record a failure outcome, but several failure sub-paths (consolidation exception, Procedure-extraction exception, `.stealth` lock contention beyond 60s) are NOT ESTABLISHED FROM CODE in this pass | Phase 16 | Partial/Unknown |
| Failed runs can teach future agents | Plausible in principle (Evidence with `outcome_status != 'success'` still gets recorded and feeds belief), but Procedure candidates are only ever synthesized on success — a failed run cannot itself become a reusable Procedure | `ingestion_jobs.py:1198` gate | Partial |

---

---

# PART II — GLOBAL SCOPE AUDIT

Same method and discipline as Part I: exact file:line, exact table/column, exact test name; "NOT ESTABLISHED FROM CODE" where unprovable. This covers what Part I explicitly excluded — global document ingestion, local→global publication, global retrieval/ranking, and cross-tenant enforcement — verified independently against current code, not trusted from the repo's own prior audit docs.

## G-PHASE 1 — Global document ingestion: the canonical chain

**Orchestrator:** `compile_skill_artifact()` (`backend/app/services/skill_ingestion.py:2080-2645`). Exact call order for a fresh capture (line numbers from the "fresh-capture branch," 2492 onward — the "new_version branch" at 2259 is structurally identical):

1. `parse_skill_md` (2109) — structural parse. Failure → `_write_artifact_row` only (an `ingested_artifacts` audit row), returns `status="rejected"`, **nothing else written**.
2. `screening.screen_document_text` pre-check (2134) — any finding → same reject-and-stop.
3. `_screen_untrusted_document` (2171) — injection/trust-escalation signal detection.
4. **`classify_admission(...)` (2179-2185) — the admission gate, and it runs BEFORE every canonical-chain write** (Source, IngestionContext, Observation, Evidence, Claim, artifact_blocks are all downstream of this call). A `reject` decision short-circuits here — only `_write_artifact_row` runs.
5. Staleness/prior-art checks (2216-2242).
6. `_open_ingestion_provenance` (2497) = `register_source()` + `open_ingestion_context()` — **this is where the canonical chain actually starts**.
7. `capture_procedure()` (2504).
8. `_emit_document_observation` (2541) → `persist_observation`.
9. `_emit_document_screening_and_claims` (2554) → persisted screening row + `claim_extraction.persist_claim_candidate` → `capture_claim` → conditionally `add_procedure_claim_ref(role=<candidate.suggested_procedure_role>, ...)` (skill_ingestion.py:1421-1432 — the role is whatever the extraction candidate specifies, e.g. `RATIONALE`, never hardcoded).
10. `_persist_package_relations` (2577).
11. `_emit_document_evidence` (2581) — raw `INSERT INTO evidence` with `evidence_type='document'`.
12. `_emit_document_claim_evidence` per claim (2591) — a second evidence row per claim, `target_type='claim'`.
13. `_write_artifact_row` (2599) — the `ingested_artifacts` row.
14. `_persist_document_blocks` (2608) → `artifact_blocks.normalize_markdown` → `redact_blocks_for_persistence` → `persist_artifact_blocks`.
15. `_attach_observation_block_ref`/`_emit_block_observations` (2613-2618).
16. `complete_ingestion_context(status="completed")` (2621).

**Key functions, exact:**
- `register_source()` (`sources.py:53-126`) — writes `sources` (`db/64_sources.sql:74-125`), dedup via `ON CONFLICT (source_type, locator, publisher) DO UPDATE ... RETURNING id, (xmax=0) AS inserted`. **Caveat, stated in the module's own docstring:** Postgres treats NULL as distinct in a UNIQUE index, so a `publisher=None` source never actually dedups against another `publisher=None` source with the same locator.
- `open_ingestion_context()`/`complete_ingestion_context()` (`ingestion_context.py:49-125`) — writes `ingestion_contexts` (`db/65_ingestion_contexts.sql:31-66`): `actor_id`, `workspace_id`, `scope_type`/`scope_entity_id`, `classification`, `extractor_id`/`extractor_version` (both `NOT NULL`).
- `artifact_blocks.normalize_markdown()`/`persist_artifact_blocks()` (`artifact_blocks.py:136, 434-493`) — writes `artifact_blocks` (`db/69_artifact_blocks.sql:56-126`), `UNIQUE(artifact_id, artifact_content_hash, block_index)`.
- `redact_blocks_for_persistence()` (`artifact_blocks.py:85-100`) → `screening.redact_document_text` (`screening.py:214-235`) — **purely regex-based** (reuses `trace_redaction.KNOWN_TOKEN_PATTERNS` + two hand-written regexes for private-key headers and `key=value` credential pairs), not a library. Offsets (`source_start`/`source_end`) still point at the raw, unredacted artifact; only the persisted `text` column is redacted.
- `screening.screen_document_text()`/`decide()`/`record_screening_run()` (`screening.py:238-372, 375-383, 571-629`) — writes `screening_decisions` (`db/68_screening_decisions.sql:52-114`). Reused detectors, verbatim import: `from app.services.skill_ingestion import _META_DIRECTIVE_RE, _TRUST_ASSERTION_RE` and `from app.services.trace_redaction import KNOWN_TOKEN_PATTERNS`. Note: `check_type='source_trust'` has **no implemented detector at all** — the module's own docstring says so (lines 41-44).
- The Claim write is `capture_claim(pool, statement=..., source_ref=..., ingestion_context_id=..., observation_id=..., ...)` — same function Part I traced for local execution, called here via `claim_extraction.persist_claim_candidate` (`claim_extraction.py:548-572`) instead of directly.

**Is this automatic?** **No.** Two production entry points only, both explicit:
- `backend/scripts/ingest_skills.py` — a hand-run CLI.
- `handle_ingest_skill_package` (`ingestion_jobs.py:122-162`, in the shared `JOB_HANDLERS` dict) — but the only writer of these jobs, `enqueue_skill_package_jobs`, is itself called only from `scripts/ingest_skills.py:136` (plus a test). **Nothing in request-handling code, a webhook, or `ingestion_scheduler.py`'s background loop ever calls `enqueue_skill_package_jobs` or `compile_skill_artifact` on its own.** Draining a queued job additionally requires either the manual `scripts/run_ingestion.py` or the admin endpoint `POST /v1/admin/ingestion/process` (itself framed by its own docstring as "call it from a cron job... or curl" — external wiring, not something this repo sets up).

**Test proof:** `backend/tests/test_ingestion_canonical_chain_e2e.py::test_procedural_skill_md_produces_the_full_canonical_chain` genuinely asserts, against a real Postgres DB, all 12 steps of the chain (Source → IngestionContext → `ingested_artifacts` → `artifact_blocks` with offset round-tripping → Observation → 2 Evidence rows (procedure-targeted + claim-targeted, shared `independence_group` prefixed `"skill_md:"`) → Claim (with `claim_sources` link) → `procedure_claim_refs(role='RATIONALE')` → `screening_decisions` rows → Procedure `verification_state='candidate'`), plus a B2 invariant that zero `task_nodes` edges are created. Claim extraction in this test is driven by a **fake, hand-scripted LLM client**, not a real model — the test proves the plumbing/schema/linkage, not real-model extraction quality. A companion test, `test_non_procedural_document_produces_no_procedure`, confirms a non-procedural document yields zero Procedures. **This test genuinely supports the prior internal doc's "canonical chain" claim — it is not weaker than advertised.**

## G-PHASE 2 — Local → Global publication

**`publish_procedure()` (`backend/app/services/publication.py:146-370`).** Does **not** flip the source row's visibility. It creates a **brand-new** `procedures` row via `capture_procedure(..., visibility="public", scope_type="global", owner_id=actor_subject)` whose `domain_payload.published_from_procedure_row_id` points back at the source — the original private/org row is untouched, keeps its own `verification_state`/`verification_stats` forever. Comment at `publication.py:253-255`: "NO evidence, NO verification stats, NO private embedding are forwarded" to the new row — it starts at zero evidence, `verification_state='candidate'` (the DB column default), regardless of the source's own verification history.

**Gates, checked in order, all collected into one `reasons` list before a single `PublicationDenied(reasons)` raise (`publication.py:244`):**
1. Ownership: `src.owner_id != actor_subject`.
2. Scope: `src.visibility` must be `'private'`/`'org'`/`'organization'` — an already-public source can't be re-published.
3. Classification: `classify_procedure_row(src)` rejects `EXECUTION_SECRET`/`PERSONAL_DATA`/`CONFIDENTIAL_DATA`/`SECURITY_DATA`.
4. **Full dependency traversal** — `traverse_publication_dependencies()` (`publication_deps.py:235-544`) — see below.
5. Provenance: `src.provenance` must be non-empty.
6. Sanitization residue: `_residual_secret_signals(scrubbed)` on the sanitized copy.

**`traverse_publication_dependencies()`** walks: procedure → `procedure_dependencies` → procedure → `procedure_claim_refs` → claims (`knowledge_nodes`) → `claim_sources` → observations → `ingestion_contexts`/`ingested_artifacts`/`artifact_blocks` → `sources` → `evidence` (targeting the procedure or any reached claim). **One level deep per edge kind, explicitly not a transitive closure** (docstring lines 47-52). Blocking conditions: visibility `private`/`org`/`organization` anywhere in the reached graph; classification in `PRIVATE_CLASSES` (`{ORG_PRIVATE, USER_PRIVATE, EXECUTION_SECRET, PERSONAL_DATA, CONFIDENTIAL_DATA, SECURITY_DATA, AUDIT_DATA}`); a resolved source with `reliability_score < SOURCE_TRUST_FLOOR` (**exactly 0.3**, `publication_deps.py:88`) — a NULL score is not auto-blocking; any unresolved reference (fail-closed); and hitting the traversal cap, **exactly `MAX_TRAVERSAL_NODES = 500`** (`publication_deps.py:81`), which appends a `traversal_truncated` blocking entry.

**Private-evidence-≠-global-verification, verified accurate in current code:** a private/org evidence row is always blocking (`"private evidence does not become global verification (A14)"`, line ~513-524). Independence requires **≥2 distinct `independence_group`s among PUBLIC evidence only** (`independent_public = len(public_groups) >= 2`, line 505) — a single lone public `execution_result` success is non-blocking but explicitly flagged `"independent": False` with the note `"private_evidence_not_global_verification"`. `global_verification_required = not independent_public`. Test: `test_publication_deps_offline.py::test_lone_public_execution_evidence_is_not_independent_and_notes_it` confirms this exact behavior.

**Who calls `publish_procedure`?** Exactly one caller found: `POST /v1/procedures/{procedure_row_id}/publish` (`backend/app/api/procedures.py:297-327`), gated by `require_authenticated_user`. **No MCP tool wraps it** (not found in the 40-tool list from Part I, and no other caller exists in `backend/`). **No automatic/threshold-triggered publication exists anywhere** — the module's own docstring lists "promotes silently after a successful execution" under an explicit "It never" list (`publication.py:13`).

**`verification_state` is a completely separate axis from publish/visibility — do not conflate them.** Enum `procedure_verification_state ∈ {'candidate','verified','retired'}` (`db/18_procedures.sql:22`). Transition `candidate→verified` happens via `record_execution_outcome()` (`procedures.py:542-788`), exact thresholds: `MIN_SUCCESSES_FOR_VERIFIED = 10`, `MIN_DISTINCT_CONTEXTS_FOR_VERIFIED = 3`, requiring `total_failures == 0` (`procedures.py:657-670`), backstopped by a DB trigger (`sl_verified_requires_evidence`, `db/30_verified_requires_evidence.sql:48-63`) that blocks the transition unless `procedure_evidence_stats.independent_supporting_required >= 1`. **This can happen entirely on a `visibility='private'` row, which then stays private and verified forever** — verification never causes publication, and publication never inherits verification (the fresh global row always restarts at `'candidate'`).

**Tests:** `test_phase4_publication_offline.py` — 11+ tests covering clean publish, non-owner refusal, already-public refusal, private-dependency blocking, missing-provenance blocking, residual-secret blocking, private-referenced-claim blocking (even when the procedure's own deps are clean), and the 4 `withdraw_publication` outcome branches. `test_publication_deps_offline.py`/`_e2e.py` — 13+ tests directly exercising every blocking condition above, including one that constructs `MAX_TRAVERSAL_NODES + 5` rows to prove the 500-node cap actually fires.

## G-PHASE 3 — Global retrieval and ranking

**`find_applicable_procedures()`** (`backend/app/services/applicability.py:676-689`) full pipeline, in order:
1. **Cold-start gate** (`should_disable_procedure_retrieval`, lines 81-112): if fewer than `MIN_VERIFIED_PROCEDURES_TO_ENABLE_RETRIEVAL = 1` verified+active+live procedures are visible to the caller, returns `[]` immediately — no cascade runs at all. Only applies when `require_verified=True`; `require_verified=False` bypasses it deliberately (documented anti-bootstrap-deadlock rationale).
2. **Candidate pre-filter** (`_fetch_candidate_pool`, line 462) — RRF-fuses up to 4 legs (cost, vector similarity, lexical full-text, hierarchy-exclusion), then re-fetches full rows gated by `visibility_predicate`.
3. **Hard-constraint cascade** (`check_hard_constraints`, line 242-396), run concurrently via `asyncio.gather` over all candidates, in this exact order, **non-compensatory (confirmed — each stage returns/short-circuits immediately on failure rather than scoring)**: temporal validity → staleness → availability → verification_state (if `require_verified`) → approval_status (if `require_verified`) → scope/exclusions → preconditions (against the claim graph) → numeric invariants (solver, deliberately last/cheapest-first-ordered).
4. **Similarity+capability ranking of survivors only** — a second vector-similarity query scoped to just the survivor ids, fused via the same `fuse_rrf` with a Wilson-lower-bound capability signal, then `passes_relevance_gate`.

**Scope:** `find_applicable_procedures` is genuinely global by default — `access_scope` defaults to `AccessScope.unrestricted()` wherever unset, which makes `visibility_predicate()` return literal `"TRUE"` (no filter, sees everything). It becomes caller-scoped (public+own+org) only when a real per-request `AccessScope` is threaded in. There is no separate "public-only" code path — one query, one predicate, parameterized by caller identity.

**`HybridRetriever`** (`retrieval.py:153`) — "hybrid" = pgvector cosine-distance similarity + genuine Postgres full-text search (`ts_rank`/`to_tsvector`/`to_tsquery`), fused via **real reciprocal rank fusion**: `fuse_rrf()` (`retrieval.py:122-150`) implements `score += 1.0/(k+rank+1)` with `RRF_K = 60` — pinned by `test_retrieval_rrf_properties.py::test_default_k_matches_module_constant`. `applicability.py` reuses this exact same `fuse_rrf` primitive for its own two fusion points — not a second, divergent implementation.

**Index-freshness claim from the prior internal audit doc — REFUTED as literally stated, but the underlying mechanism exists under different names.** `canonical_revision`/`indexed_revision`/`index_lag` as literal identifiers: **0 matches anywhere in the codebase.** But a real, working, tested equivalent exists: `procedures.retrieval_indexed_at` (real column, `db/72_retrieval_index_freshness.sql:36-37`), the `procedure_index_lag` view (`db/72...sql:40-61`, computing `reason ∈ {no_embedding, never_indexed, stale_since_update, current}`), and `app.services.index_freshness.get_index_lag()` (a real callable returning `{current_recipe, lag_count, recipe_drift_count, total_stale, sample}`), tested in `test_index_lag_e2e.py`. Additionally, **the specific guarantee the old doc said was missing — "authoritative version/applicability checks run against canonical rows before selection" — is real and quoted directly** from `applicability.py:713-722`: every surviving candidate id from the vector/lexical legs is **re-fetched from its live `procedures` row** before both the hard-constraint cascade and ranking; index hits are only ever used as a ranked id list, never as the data itself. Embeddings carry a real `embedding_model_id` versioning tag (`"{provider}:{model}"`, e.g. `"gemini:gemini-embedding-001"`) and every ranking query filters `AND embedding_model_id = $2` — vectors from different embedding models/versions are never compared against each other.

**`reuse_detection.py::_vector_candidates`** — a genuinely **separate** retrieval path (queries `task_nodes` + `knowledge_nodes`, never `procedures`), fused with `find_applicable_procedures`'s output only at the MCP `retrieve_precedent` tool's caller level (per `applicability.py`'s own comment acknowledging this as a previously-disclosed gap, closed by adding `verified_procedure_candidates()` specifically so `retrieve_precedent` could fuse both). The exact fusion call site inside `server.py`'s `retrieve_precedent` was not re-read in this pass — NOT ESTABLISHED FROM CODE beyond the documented intent.

**Tests proving ranking quality, not just plumbing:** `test_applicability_capability_ranking_e2e.py::test_strong_capability_outranks_weak_capability_at_equal_applicability` (10 real recorded successes outrank zero evidence at equal hard-constraint applicability); `test_retrieval_quality_e2e.py::test_direct_match_ranks_first` (≥80% top-1, ≥90% top-3 for exact-match queries against real corpus data); `test_retrieval_rrf_properties.py` (monotonicity + multi-list-boost properties of RRF itself, not just its use).

## G-PHASE 4 — Global schema map (migrations read in full: 01, 14, 18, 24, 32, 64-69, 79)

Full column lists for `knowledge_nodes`, `task_nodes`, `edges`, `episodes`, `episode_links`, `traces`, `observations`, `observation_events`, `procedures`, `evidence`, `ingested_artifacts`, `ingestion_runs`, `sources`, `ingestion_contexts`, `procedure_claim_refs`, `procedure_implementations` (relation columns added by migration 67), `screening_decisions`, `artifact_blocks` were all independently re-verified against the migration files and match Part I's earlier partial citations plus the new global-only tables (`sources`, `ingestion_contexts`, `procedure_claim_refs`, `screening_decisions`, `artifact_blocks`) — full detail is in the raw investigation output; key facts worth calling out explicitly:

- **`knowledge_nodes.node_type` has no enum/CHECK constraint at all** — it's plain `TEXT NOT NULL` (`01_ontology.sql:25`). It genuinely is a multi-type table in active use, not a claims-only table in disguise: **8 distinct `node_type` values found in live code** — `claim` (overwhelmingly dominant), `claim_family`, `code_location`, `failure_mode`, `hierarchy_group`, `policy`, `policy_document`, `fact`.
- **Complete `CREATE TRIGGER` inventory across all migrations read**, 18 triggers total, notable ones beyond Part I's `trg_execution_runs_status_transition_fence`: `tg_evidence_append_only` (evidence rows are append-only; only the `t_invalid` tombstone may change, DELETE always rejected), `sl_verified_requires_evidence`/`trg_procedures_verified_requires_evidence` (blocks a procedure entering `verification_state='verified'` unless `procedure_evidence_stats.independent_supporting_required >= 1` — a real DB-level backstop against gaming verification), `trg_ern_terminal_fence` (blocks rewriting a `succeeded`/`cancelled` execution_run_node), plus several append-only freezes (`execution_plans`, `task_graphs`, `executions`, `change_sets`, `failure_routes`) and `updated_at`-touch triggers.
- **`classify_admission()`** (`ingestion_admission.py:494-548`) — the gate in front of all global document ingestion. Order: structural validity (>200 steps, >20k chars/step, >200k total chars, or >5% control-char ratio → immediate reject, before secret/dangerous-content scans even run) → secret scan (always redacts in place; private-key blocks → reject, other secrets → review) → dangerous-content regex composition (credential-theft-intent alone → reject; credential-access + exfil-verb combo → reject; destructive commands alone → reject; privesc alone → review, but privesc + any of {theft, exfil, destructive} → reject) → malicious-tool-name check (named tools like mimikatz/cobaltstrike/meterpreter → reject; merely declaring shell/network access never rejects) → injection-signal findings → **if any review-severity finding remains and no LLM client is configured, defaults to `"review"` (fail-safe, zero LLM calls in the normal path)** → otherwise an LLM risk classifier runs (`safe→admit, unsafe→reject, uncertain→review`, and any client failure abstains to `uncertain`). Downstream: `reject` writes only an `ingested_artifacts` audit row, no procedure; `review` writes a full candidate but `availability='quarantined'`, excluded from every normal retrieval surface (`_CANDIDATE_BASE_WHERE` in `applicability.py`); `admit` is `availability='active'`, immediately retrievable. **`verification_state` is never touched by admission in any branch** — it stays at the DB default (`'candidate'`) regardless of admit/review/reject.

## G-PHASE 5 — Two genuine privacy/consistency findings surfaced only by this global-scope pass

**Finding A — claim-equivalence dedup search has zero owner/tenant/visibility scoping.** `find_candidate_claim_pairs()` (`claim_equivalence.py:65-89`) exact SQL:
```sql
SELECT b.id, b.name AS statement, 1 - (a.embedding <=> b.embedding) AS similarity
FROM knowledge_nodes a, knowledge_nodes b
WHERE a.id = $1::uuid AND a.node_type = 'claim' AND a.t_invalid IS NULL
  AND b.node_type = 'claim' AND b.t_invalid IS NULL AND b.id != a.id
  AND a.embedding IS NOT NULL AND b.embedding IS NOT NULL
ORDER BY a.embedding <=> b.embedding ASC
LIMIT $2
```
No `visibility`, `owner_id`, `tenant_id`, or `scope_type` predicate anywhere. A private claim is compared, by embedding similarity, against **every other live claim in the entire corpus regardless of who owns it**, and a detected relation is written to `claim_relation_candidates` (`record_claim_relation_candidate`, line 141) — which itself performs no visibility check either, and whose review-queue reader (`get_pending_claim_relation_candidates`, line 183) also has no visibility filter. Net effect: **a private claim's existence and its embedding-similarity relationship to another user's private claim is discoverable** through the relation-candidates review queue, with no code-level barrier preventing it. This never auto-merges or auto-publishes anything (`record_claim_relation_candidate`'s own comment: "NEVER calls relate_claims/link_claims") — it's a detect-only side channel, but a real one.

**Finding B — local execution-result evidence has no independence de-duplication at all, while global document evidence does.** In `consolidate_local_episode` (`ingestion_jobs.py:1186-1192`), `record_claim_evidence(..., evidence_type="execution_result", ...)` is called **without an `independence_group` argument at all** — it defaults to `None`. In `claim_belief.py`'s aggregation, a `None` group becomes a synthetic per-row key (`__self_{i}`), meaning **every local execution-result row counts as its own independently-corroborating group** — repeated runs of the same procedure, by the same or different users, each inflate the apparent independent-corroboration count with no de-duplication. Contrast: `skill_ingestion.py`'s global document-evidence emitters (`_emit_document_evidence`/`_emit_document_claim_evidence`, lines 1046-1145) explicitly set `independence_group = f"skill_md:{source_hash}"` — correctly collapsing repeated ingestion of the *same* document into one corroborating group. **The global path was hardened against this exact problem; the local execution path was not.** This is a real, findable asymmetry, not a hypothetical — confirmed by grepping every `record_claim_evidence`/direct-`evidence`-INSERT call site found in this pass.

## G-PHASE 6 — Scope/visibility enforcement mechanics

**`AccessScope`/`visibility_predicate`** (`access.py:30-126`): `AccessScope(viewer_id=None, include_private=True, org_ids=())`. `visibility_predicate()` returns, for a signed-in viewer with `include_private=True`: `(visibility='public' OR owner_id=$N [OR (visibility='org' AND tenant_id IN (...))])`. For `unrestricted()` (`viewer_id=None, include_private=False`): literal `"TRUE"` — no filtering at all. Module docstring states the deliberate policy: "a fully shared commons... both predicates are permissive" by default; callers must explicitly construct a restricting scope.

**Identity resolution** (`authn.py`): `current_actor_id()` reads a `contextvars.ContextVar` set by ASGI middleware after `validate_token_async` succeeds; the JWT claim used is `sub` (`options={"require": ["exp","sub"]}` on decode; empty `sub` raises `TokenRejected`). `assert_deployment_mode_posture()` (called from `server.py:268`, i.e. the MCP server's own boot path) refuses to start **only** when `deployment_mode == "shared"` and OIDC issuer/audience are unset — the default `deployment_mode="single_user"` never raises regardless of auth config. A separate, stricter guard (`assert_boot_posture()`) exists for `main.py`'s REST app only.

**Workspace registry** (`workspace_registry.py`) — `registered_workspaces` is a real table distinct from user/repository; `register_workspace()` requires an `owner`/`admin` role and canonicalizes `storage_path` under an allowlist of root directories. **But** `workspace_id` is only ever validated against this registry when `settings.hosted_execution_enabled=True`. In the default/local mode, `_authorize_repo_execution()` (`server.py:643-644`) returns the caller-supplied `repo_path` unchanged, and `find_best_way`'s `workspace_id` parameter is accepted as a **free-form, unvalidated string** used purely as a scoping key (`server.py:1721-1722`) — no registry lookup happens at all outside hosted mode.

**Extensive test coverage confirmed** beyond Part I's `test_search_privacy_leak_e2e.py`: `test_cross_user_isolation_e2e.py` (private procedure/claim/evidence invisible to a different authenticated viewer), `test_access.py` (unit-level `visibility_predicate` contract, 7+ tests), `test_agent_store_idor_e2e.py` (IDOR check), `test_b19_multi_user_privacy_e2e.py` (includes `test_org_private_procedure_visible_to_org_member_not_to_outsider`, exercising the `org_ids` branch specifically), `test_hardening_h1_identity_tenancy.py` (tenancy+visibility predicate composition, plus a meta-test `test_no_module_outside_access_py_writes_tenant_filters_by_hand` guarding against ad-hoc bypasses of the one shared predicate builder).

---

## PHASE 22 — Gap summary

### Confirmed implemented
- MCP server (`mcp` SDK), 40 tools, 8 resources, 6 prompts, Bearer-token auth with OIDC fallback.
- Execution run state machine with a DB-enforced status transition fence.
- Event recording (append-only, ordered by `seq`), inside the same transaction as the state change it documents.
- Episode open-on-run-creation, close-on-terminal-transition (3 real call sites), idempotent.
- Episode closure enqueuing `consolidate_local_episode` (a real Postgres-backed job queue row).
- Deterministic Observation extraction from execution events, unconditionally, with exact FK linkage.
- LLM-based Claim derivation anchored to a real Observation, with explicit no-op semantics when unprovenanced.
- Evidence recording (`evidence_type='execution_result'`) tied to episode outcome, feeding a real belief-score computation with documented weights/ceilings.
- Procedure capture, both automatically (per successful episode) and via an admin-triggered batch sweep, and ad-hoc via `find_best_way` with explicit repo/user scope derivation.
- `.stealth/` filesystem projection: real, atomic (fsync+rename), journaled, page-fault-capable, byte-budgeted, and explicitly non-canonical.
- Retrieval tools (`search_procedures`, `get_relevant_claims`, `find_best_way`) hitting real Postgres queries, proven end-to-end by privacy-scoped tests.

### Partially implemented
- "Trace" as a concept exists only as a column (`trace_id`), not a first-class persisted object with its own lifecycle.
- Evidence granularity is per-episode, not per-test/per-assertion — a 4-test suite (duplicate delivery / invalid signature / concurrency / retry) collapses to one evidence row per claim.
- Failure-path handling: episode/evidence failure recording works, but several deeper failure sub-cases (mid-consolidation exception, mid-procedure-extraction exception, prolonged lock contention) are NOT ESTABLISHED FROM CODE.
- Restart recovery: `resume_run()` exists but nothing calls it automatically on process boot.

### Exists but is not automatically connected
- **Retrieval tools exist and work, but nothing forces Claude to call them** — a coding task can proceed with zero StealthLab interaction at all.
- **Execution recording only happens if Claude routes work through `find_best_way`'s own tier-2 execution machinery** — merely calling `search_procedures`/`get_relevant_claims` and then coding directly (outside MCP tool orchestration) produces no Event/Episode/Observation/Claim/Evidence trail at all.
- **`promote_observation_to_claim` and `extract_model_observation` exist as functions but have no confirmed automatic caller from the local-execution path** — the local path uses a different, LLM-based route (`persist_claim_candidate`) instead, bypassing them.
- **`.stealth/` is a real, working system, but it is a rendering, never a write path** — nothing routes new knowledge into Postgres via `.stealth/`; it is strictly downstream.
- **(Global) `consolidate_local_episode` gets enqueued unconditionally by episode closure, but nothing drains it unless a *second, separate process* (`main.py`'s FastAPI app, not the MCP server) is also running with `INGESTION_AUTO_MODE=global` explicitly set** — the shipped default (`"local"`) makes the scheduler's own tick a no-op even when that second process is running. See Phase 17B.
- **(Global) Document ingestion (`compile_skill_artifact`) is never triggered automatically by anything** — only a human running `scripts/ingest_skills.py`, or a human/cron hitting `/v1/admin/ingestion/process` for an already-queued job.
- **(Global) `verification_state` and publication/visibility are fully orthogonal axes that are easy to conflate** — a procedure can be `verified` (10 successes, 0 failures, 3 contexts, DB-trigger-enforced) while remaining `private` forever; publishing never inherits verification and always restarts the new global row at `'candidate'`.

### Not implemented
- No dedicated "Artifact" table/row for local execution output (diffs, test results) — this layer is entirely absorbed into the Event→Observation transition; asking "where is the Artifact for this run" has no answer beyond the raw `artifact_recorded` event.
- No `.stealthlab/*.db` local SQLite cache — deliberately removed (`local_store.py`, commit `32092ce`).
- No automatic startup rehydration of open episodes/stuck execution runs.
- No `canonical_revision`/`indexed_revision`/`index_lag` fields under those literal names (though a functionally equivalent, real, tested mechanism exists under different names — `retrieval_indexed_at`/`procedure_index_lag`/`get_index_lag` — see G-Phase 3; the prior internal audit's "OPEN/missing" claim here is refuted, not confirmed).
- No `independence_group` de-duplication for local execution-result evidence (Finding B, G-Phase 5) — this is a real, unaddressed gap, in contrast to global document evidence, which does de-duplicate correctly.
- No owner/visibility scoping on claim-equivalence dedup search (Finding A, G-Phase 5) — a real, unaddressed privacy gap, not a hypothetical.
- No MCP tool wraps `publish_procedure` — global publication is HTTP-API-only, human-triggered, never reachable from an MCP coding session directly.

### Confirmed implemented (addendum, Phase 12B — DAG engine)
- The DAG is a real dependency graph (`n.order`/`n.deps`, Kahn's-algorithm cycle check, multi-dependent support), not disguised as one.
- Node execution is durably checkpointed to Postgres before and after each node (`_node_claim`/`_node_finish`), enabling crash-point resume via `resume_run()` (manual trigger only, per Phase 17).
- Two real, coexisting execution modes: `mode="full_run"` (StealthLab's own MCP server process spawns an LLM coding agent per node, via `providers.FrontierProvider`/`local_agent/runner.py`) vs. `mode="plan_only"`+`continue_run` (an external agent — e.g. this Claude Code session — does the coding and reports back via `report_node_progress`, using the identical state machine). `declare_file_intent` exists specifically to coordinate the latter mode's concurrent external edits.
- Execution is strictly sequential (no `asyncio.gather`/worker pool found in `graph_executor.py` or `durable_run.py`) — dependency batching determines *order*, never *parallelism*.

### Confirmed implemented (addendum, Part II — global scope)
- The full 12-step global document-ingestion canonical chain (Source → IngestionContext → Artifact → artifact_blocks → Observation → Evidence(document) → Claim → procedure_claim_ref → screening_decision → Procedure candidate), genuinely proven end-to-end against a real DB by `test_ingestion_canonical_chain_e2e.py`.
- A real, non-compensatory, cold-start-gated, index-freshness-safe global retrieval pipeline with genuine RRF fusion (pinned `RRF_K=60`) and embedding-model-versioned similarity ranking.
- A real, multi-gated, dependency-traversal-checked (500-node cap, 0.3 trust floor) publication pipeline that creates a fresh global row rather than mutating the private source, with a correctly-implemented "private evidence ≠ global verification" independence rule.
- Real, DB-trigger-backstopped verification-state gating (`sl_verified_requires_evidence`) that can't be bypassed by a direct UPDATE.

### Unknown / requires runtime instrumentation
- Exact behavior of `extract_procedure()`'s internals (what specifically it extracts and how, beyond "it's called when outcome=='success'").
- Exact retry/backoff semantics of the generic `ingestion_jobs` queue runner for a failing `consolidate_local_episode` job.
- Whether Postgres write failures inside `durable_run.py`'s transactions leave partial state (event committed, episode not, etc.) — needs live fault injection, not static reading.
- What promotes a `procedures.verification_state` from `'candidate'` to something `require_verified=True` queries would surface — not traced in this pass (this is now partially answered by G-Phase 2's `record_execution_outcome` finding — 10 successes/0 failures/3 contexts — but the trigger conditions for `require_verified=True` queries specifically, beyond the state value itself, were not re-verified here).
- Whether any actual deployment of this system runs `main.py` alongside the MCP server with `INGESTION_AUTO_MODE=global` set — an ops/deployment fact outside this repo (Phase 17B).
- The exact fusion call site inside `server.py`'s `retrieve_precedent` tool that combines `_vector_candidates` and `verified_procedure_candidates` (documented intent confirmed, exact code not re-read in the global-scope pass).

---

## PHASE 23 — Test evidence table

| Claim | Test file | Test name | What it actually proves |
|---|---|---|---|
| Episode opens on run creation, idempotently | `backend/tests/test_local_episode_learning.py` | `test_episode_opens_on_run_creation_and_is_idempotent` | one episode row created with correct fields; a second call doesn't duplicate it |
| Episode closes once on success, enqueues consolidation once | same file | `test_episode_closes_once_on_success_and_enqueues_consolidation_once` | `end_ts` set, `metadata.outcome`, exactly one `ingestion_jobs` row, duplicate finalize doesn't add a second |
| Consolidation produces Observation+Claim+Evidence | same file | `test_consolidation_produces_observations_claim_and_evidence` | a deterministic Observation, a persisted Claim, and an `evidence_type='execution_result'`/`outcome_status='success'` row all exist; re-running doesn't duplicate the claim |
| No LLM client → Observations still persist, nothing fabricated | same file | `test_consolidation_without_llm_client_preserves_observations_and_fabricates_nothing` | Observations ≥1, zero Claims, zero Procedures |
| Private-visibility Claim invisible to unrelated owner | same file | `test_private_claim_invisible_to_unrelated_owner` | visibility scoping enforced on consolidation output |
| Private Procedure invisible to a different caller via `search_procedures` | `backend/tests/test_search_privacy_leak_e2e.py` | `test_private_procedure_invisible_to_search_by_a_different_caller` | genuine capture→search round trip, scoped correctly by owner |
| Private Procedure invisible via `find_best_way` tier 1 | same file | `test_find_best_way_tier1_does_not_leak_a_private_procedure` | same, through the heavier orchestrator |
| Unrelated query abstains rather than false-positive matching | `backend/tests/test_retrieval_negative_control_e2e.py` | `test_genuinely_unrelated_query_abstains_rather_than_returning_the_least_bad_option` | `find_applicable_procedures` returns `[]` for a genuinely unrelated query |
| `decide_procedure` is a real 6th minimal-surface tool | `backend/tests/test_mcp_six_tool_surface_offline.py` | `test_decide_procedure_approved_calls_the_real_approve_function` / `..._rejected_calls_the_real_reject_function` | tool wraps a real approve/reject function, not a stub |
| `extract_procedure_from_episode` requires real `goal_text` + success outcome | NOT ESTABLISHED FROM CODE — a specific test for this exact guard was not located in this pass (only the guard's source code, `ingestion_jobs.py:791-802`, was confirmed) | — | — |
| `.stealth/` atomic write leaves no temp file on success | `backend/tests/test_stealth_projection_offline.py` | (exact function name not captured in the sub-investigation's notes; file confirmed to assert this) | `_atomic_write` behavior |
| `.stealth/` journal ordering + lock exclusivity | `backend/tests/test_stealth_journal_offline.py` | (multiple tests, names not individually captured) | monotonic `seq`, `since_seq` filtering, corrupt-line tolerance, `SingleWriterLock` exclusivity |
| `.stealth/` exploration open/close/list round trip | `backend/tests/test_stealth_exploration_offline.py` | (multiple tests) | journal-backed round trip; `close_exploration` without `pool` never touches the DB |
| No test found | — | — | Any test proving automatic (non-opt-in) retrieval before a coding task — **NO DIRECT TEST FOUND**, consistent with Phase 15's finding that no such automatic behavior exists to test |
| No test found | — | — | Startup rehydration of orphaned episodes/execution_runs — **NO DIRECT TEST FOUND** |

---

## Bottom line (covers both Part I — local scope — and Part II — global scope)

The suspicion that prompted this audit — that "Event → Trace → Episode → Artifact → Observation → Claim → Evidence → Procedure" might exist more on paper than in the runtime path from a Claude coding task to durable, retrievable learning — is **partially correct, in five specific and now precisely located ways**:

1. **The write side is real and well-tested**, both locally and globally. Locally: Event → Episode → (skipping a dedicated Artifact layer) → Observation → Claim → Evidence → (conditionally) Procedure, via `find_best_way`'s own execution machinery, is a genuine, idempotent, transactionally-sound pipeline (Part I). Globally: the 12-step document-ingestion canonical chain (Source → IngestionContext → Artifact → artifact_blocks → Observation → Evidence → Claim → procedure_claim_ref → screening_decision → Procedure) is equally real, and the local→global publication gate (dependency traversal, 500-node cap, trust floor, "private evidence ≠ global verification") is genuinely enforced, not aspirational (Part II).
2. **The `.stealth/` filesystem layer, which an internal audit doc in this very repo claimed didn't exist, actually does now** — built the same week as that doc, correctly a non-canonical, regenerable projection, never a second source of truth.
3. **The actual gap is upstream of all of that, at three separate points, not one:**
   - Nothing makes Claude call any StealthLab tool in the first place — `search_procedures`/`get_relevant_claims`/`find_best_way` are opt-in, exactly like every other tool. A session that codes directly, or that only reads (`search_procedures`) without routing execution through `find_best_way`, produces zero Events/Episodes/Observations/Claims/Evidence. (Part I.)
   - **Even when local execution DOES happen through `find_best_way`, and DOES enqueue a `consolidate_local_episode` job, that job is only ever drained by a *separate process* (`main.py`, not the MCP server `.mcp.json` actually launches) running with a non-default setting (`INGESTION_AUTO_MODE=global`).** Running exactly what `.mcp.json` configures — the MCP server alone — writes the enqueue row and then leaves it unclaimed forever. This is the single most consequential finding of the whole audit: the local learning pipeline's write side is real, but its own trigger to actually run may never fire in the deployment this audit was scoped to. (Phase 17B.)
   - Global document ingestion has the same shape of gap one level up: the entire canonical chain is real and tested, but nothing in the running system calls it — it requires a human to run a CLI script, and nothing auto-promotes locally-verified private knowledge to global candidacy either (publication is a manual, authenticated HTTP call with no MCP tool wrapper, and `verification_state` — the thing that actually measures "this works" — is completely orthogonal to visibility/publication). (Part II.)
4. **Two independent, previously-unflagged asymmetries were found, both real:** claim-equivalence dedup search has no owner/visibility scoping at all (a private claim's similarity relationship to another user's private claim is discoverable via a review queue with no filter), and local execution-result evidence has no independence de-duplication (repeated local runs each inflate corroboration counts) while the equivalent global/document evidence path was correctly hardened against exactly that problem. (G-Phase 5.)
5. One thing this audit **refutes rather than confirms** about the repo's own prior claims: the internal doc's assertion that index-freshness tracking (`canonical_revision`/`indexed_revision`/`index_lag`) is "OPEN/missing" is wrong as stated — a real, tested, differently-named mechanism (`retrieval_indexed_at`/`procedure_index_lag`/`get_index_lag`) exists and does exactly what was asked for, including the specific "never select on stale canonical content" guarantee. Not every gap the prior docs named is still a gap; this one was already closed under a different name. (G-Phase 3.)

That is the honest shape of the system: the pipes are real and tested end to end, in both scopes — but the taps that should turn them on are either optional (Claude's own tool-call decision), or depend on a second process and a non-default environment variable that this audit cannot confirm is actually running anywhere.
