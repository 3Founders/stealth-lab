# remaining_work.md — what is left (as of 2026-09-25, main @ 0112f8c)

Work list for agents. Each item says **why**, **where**, and **done when**.
Items are independent unless a dependency is named. Read "Ground rules" first.

---

## Ground rules (read before touching anything)

1. **Architecture source of truth:** `final_architecture.md` (v1 split: StealthLab
   *knows*, the local agent *plans and does*) plus the Goal-hierarchy rules below.
   The server never executes or edits a user's repo.
2. **Orthogonal semantics — never couple these:**
   `goals.resolved_at` (the only resolution signal) ≠ Procedure verification
   (≥10 successes, 0 failures, ≥3 distinct contexts, all-time —
   `app/services/procedures.py` `MIN_SUCCESSES_FOR_VERIFIED` /
   `MIN_DISTINCT_CONTEXTS_FOR_VERIFIED`) ≠ Credits ≠ demand ≠ abstraction level
   ≠ applicability. Resolution never propagates through the hierarchy. Benchmark
   transfer never transfers evidence or verification.
3. **Hierarchy:** `goal_relations` (`specific SPECIALIZES abstract`, status
   `proposed|accepted|rejected`) is canonical. Only `accepted` edges route.
   Levels are derived (root 0, `1 + max(parent)`), never client- or LLM-set.
   Reads derive levels live (`app/services/goal_hierarchy_read.py`).
4. **jsonb parameters:** the app pool registers a jsonb codec
   (`app/db/session.py`). Pass Python dicts/lists, **never `json.dumps(...)`**,
   into `$n::jsonb` — a pre-serialized string is stored as a JSON *string*.
   This bug class broke the hierarchy in production once already.
5. **Migrations:** never edit an applied migration (`scripts/migrate.py` records
   checksums and exits 1 on mismatch; the MCP container runs
   `migrate && serve`). Next free number: **118**. Every migration idempotent.
   A migration whose first line is `-- target: search` belongs to the search/log
   database and runs only with `scripts/migrate.py --target search` (117 is one).
6. **Test against a real database**, not only fakes. Offline tests use fake
   pools and missed four production-breaking SQL bugs. Any change to SQL in
   `goal_abstraction.py`, `goal_hierarchy_read.py`, `ingestion_jobs.py`,
   `benchmark_transfer.py`, `identity_resolution.py`, `retrieval_service.py`,
   `economy/*` needs a `*_e2e.py` test (see `tests/test_goal_abstraction_e2e.py`).

7. **Database layout** (docs/sharding.md is authoritative; every hosted project is
   capped at 500 MB):
   - **Control project A** (control DB, K000): knowledge_shards, object_routes,
     procedure_row_routes, goal_names, goal_relations, goal_search_index,
     goal_abstraction_state, projection_outbox, ingestion_jobs, benchmarks /
     benchmark_submissions / solutions / evaluations / benchmark_transfer_decisions,
     credit ledger + economy tables, all private/org canonical rows.
   - **Control project B** (search/log DB, `SEARCH_DATABASE_URL`, optional):
     procedure_search_index, claim_search_index, retrieval_decisions,
     identity_decisions, llm_spend. Accessed ONLY through `shards.search_pool(pool)`
     (returns `pool` itself when unset). Never JOIN these with A's tables.
   - **Knowledge shards K001..K0nn**: public canonical Goals with their exact-Goal
     Procedures, evidence and Claims. Placement is HRW + capacity guard, never hierarchy.
   - Reads by known id/name/owning Goal go through `app/services/routed_reads.py`
     or `home_pool` -- never a query on the control DB's own `goals`/`procedures`
     for an object that may be remote, never a broadcast when the id is known.

### Local live-DB setup (Linux)
```bash
apt-get install -y postgresql-16 postgresql-16-pgvector
PG=/usr/lib/postgresql/16/bin; D=/var/tmp/slpg; mkdir -p $D && chown postgres $D
su postgres -c "$PG/initdb -D $D/data -U postgres -A trust"
su postgres -c "$PG/pg_ctl -D $D/data -o '-p 55432 -k /tmp' -l $D/log start"
psql -h /tmp -p 55432 -U postgres -c "create database sl"
cd backend && uv venv -p 3.12 .venv && VIRTUAL_ENV=.venv uv pip install -r requirements.txt
export DATABASE_URL=postgresql://postgres@localhost:55432/sl
.venv/bin/python scripts/migrate.py
.venv/bin/python -m pytest tests/test_goal_abstraction_e2e.py -q        # live
env -u DATABASE_URL STEALTHLAB_MCP_TOKEN=x .venv/bin/python -m pytest tests --ignore-glob='*_e2e.py' -q   # offline
```
Run the real MCP server locally:
```bash
DATABASE_URL=... STEALTHLAB_MCP_TOKEN=local STEALTHLAB_MCP_PUBLIC_URL=https://mcp.test \
  .venv/bin/python -m uvicorn app.mcp_server.server:app --port 8765 --workers 1
npx -y --package=./packaging/npm stealthlab-mcp doctor --url http://127.0.0.1:8765/mcp
```

---

## P0 — launch blockers (install via `irm | iex` / `curl | bash` / `npx`)

These need a human with accounts; agents can prepare and verify.

### 1. Deploy the hosted MCP server — *human*
- Runbook: `docs/deploy/hosted-mcp.md`. Railway service with config file
  `railway.mcp.json` (builds `backend/Dockerfile.mcp-server`, 1 replica).
- Required env: `DATABASE_URL`, `STEALTHLAB_MCP_PUBLIC_URL` (without it every
  request via the domain gets 421), `STEALTHLAB_MCP_TOKEN`,
  `DEPLOYMENT_MODE=shared`, `OIDC_ISSUER=https://<proj>.supabase.co/auth/v1`,
  `OIDC_AUDIENCE=authenticated`, `MCP_WORKER_COUNT=1`, judge
  (`JEV_BASE_URL` and/or `GEMINI_API_KEY`), embeddings (`VOYAGE_API_KEY` or
  `GEMINI_API_KEY`).
- **Done when:** `curl https://mcp.<domain>/` returns
  `{"service":"stealthlab-mcp"...}` and
  `npx -y stealthlab-mcp doctor --url https://mcp.<domain>/mcp` prints `ok`.

### 2. Point the website installers at it — *human*
- Set `NEXT_PUBLIC_KEL_MCP_URL=https://mcp.<domain>/mcp` on the prod_frontend
  deployment and rebuild (the build injects it into `public/install.sh|ps1`).
- **Done when:** `curl -fsSL https://<site>/install.sh | grep DEFAULT_URL` shows the URL.

### 3. Publish the npm package `stealthlab-mcp` — *human + agent*
- Agent: set the URL in `packaging/npm/package.json` (`stealthlab.defaultMcpUrl`),
  `packaging/npm/install/install.sh` (`DEFAULT_URL`),
  `packaging/npm/install/install.ps1` (`$DefaultUrl`) — `prepublishOnly`
  (`scripts/check-publish.mjs`) refuses to publish unless all three match.
- Human: add repo secret `NPM_TOKEN`, push tag `stealthlab-mcp-v0.1.0`
  (`.github/workflows/publish-mcp-client.yml`). Name is free on npm.
- **Until published, the one-liners fail at the `npx` step** (except Claude
  Code without Node, which registers the URL directly).
- **Done when:** on a clean machine `irm https://<site>/install.ps1 | iex`
  leaves `claude mcp get stealthlab` showing `√ Connected`.

### 4. Ingestion workers have providers — *human*
- `python -m app.ingestion.worker` refuses to start without a judge and an
  embedder (by design). Confirm the Cloud Run workers (`deploy/ingestion/`)
  have `JEV_BASE_URL`/`GEMINI_API_KEY`, `VOYAGE_API_KEY`/`GEMINI_API_KEY`,
  `GENERAL_COMPUTE_API_KEY`, `GENERAL_COMPUTE_JUDGE_MODEL`, `OBJECT_STORAGE_URL`.
- Placement jobs queue on every new Goal (`goal_abstraction_placement`); a
  repair sweep re-enqueues lost ones each worker loop.

### 5. CI green on main — *agent*
- New jobs in `.github/workflows/ci.yml`: `mcp-client` (3 OS × Node 18/22),
  `goal-hierarchy-e2e` (pgvector/pg16, migrate from zero, runs
  `tests/test_goal_abstraction_e2e.py`), `mcp-server-image` (docker build).
- **Done when:** all jobs pass on main; fix any failure at its root (never skip tests).

### 6. Real Windows run of the installer — *agent with a Windows runner or human*
- Verified under PowerShell 7.4 on Linux only. Native Windows paths
  (`claude.cmd`/`code.cmd` via `shell:true` in `packaging/npm/lib/clients.mjs`,
  `cmd /c npx` for Claude Desktop) are unit-tested only.
- **Done when:** Windows PowerShell 5.1 and 7 both complete `irm | iex` and
  Claude Code + Cursor + Claude Desktop configs are correct.

---

## P1 — missing product features

### 7. Community demand via Credit commitments (the demand signal)
**Why:** Credits today are rewards only (`credit_ledger_events.reason` ∈
`new_procedure, improvement, verified_reuse, clawback, admin_adjustment`).
Nothing lets a user commit Credits to a Goal, so demand is always
"unavailable" in `app/services/goal_ranking.py::demand_factor_from_commitments`
(it already accepts `committed_credit_count` / `supporter_count`; nothing supplies them).

**Open product decisions (ask the owner before building):**
(a) escrow (Credits leave the balance) vs pure signal; (b) on resolution, escrow
goes to the contributor of the verifying Procedure, or back to committers.

**Build:**
- Migration 115: allow ledger reasons `goal_commitment` (negative, requires
  `goal_id`) and `goal_commitment_release` (reversal); keep append-only trigger.
  Optionally a `goal_commitments` view (active = commitment without reversal).
- API: `POST /v1/goals/{id}/commitments` (auth, server-derived identity,
  balance check, idempotency key), `DELETE` = reversal row, `GET` totals.
- Quadratic weighting for the ranking signal (Σ√credits per supporter)
  so one whale cannot dominate.
- **Done when:** commitments are append-only and reversible; balances can't go
  negative; ranking receives real demand; commitments never touch
  `resolved_at`, verification, or Procedure ranking (add tests asserting this).

### 8. Upward demand aggregation (depends on 7)
- For a Goal, aggregate demand = distinct commitment events on the Goal **or any
  accepted descendant**, each event counted once per ancestor even with
  multiple paths (dedupe by event id, not by path). Scope/visibility: private
  commitments/Goals must not leak counts or names into public ancestors.
- Use a bounded recursive query or a rebuildable projection; never materialize
  the transitive closure as canonical state.
- **Done when:** e2e test with a diamond (C→A, C→B, A→R, B→R) shows one event on
  C counted once on R; a private descendant contributes nothing to public R.

### 9. Root / top-level Goal discovery — DONE as the default browse view
- Decision (owner): no separate discovery page. `/goals` defaults to the most
  abstract Goals; `GET /v1/goals?view=roots` returns Goals with **no visible
  accepted parent** — `browse_kind: "root"` (>=1 visible accepted specific, with
  up to 6 direct `specifics` and `specific_count`) or `"standalone"` (no
  accepted edges, so a new Goal never vanishes before placement). "All goals"
  toggle keeps the flat list. Ordered by direct specific count, then newest —
  never by abstraction level. Private parents/descendants never leak counts or
  names (endpoints are visibility-filtered). `product_model.list_goals_browse`,
  `tests/test_goals_browse_e2e.py`.
- Still open: order by aggregated demand once item 8 exists.

### 10. Review queue for proposed hierarchy edges
**Why:** low-confidence placements are stored as `proposed`; nothing promotes or
rejects them. `handle_goal_abstraction_audit` (`app/services/ingestion_jobs.py`)
is a **no-op** that returns `audited: True`.
- API (scope `KNOWLEDGE_PUBLISH`): list proposed relations with both Goals +
  judge metadata; decide accept/reject through
  `goal_abstraction.persist_goal_relation(..., expected_status="proposed")`
  (it enforces cycle/redundancy/scope and recomputes state).
- Make the audit job record something actionable (e.g. surface the Goal in the
  queue with its reason `orphan|uncertain`) or remove it.
- Reviewer UI in prod_frontend next to the existing submission review.
- **Done when:** a reviewer can accept/reject; clients still cannot create
  accepted edges directly; tests for forged edge / cycle via review.

### 11. Frontend completion
- Demand UI (commit/withdraw, totals, supporters) — separate from resolution.
- Root discovery page (item 9), proposed-edge review (item 10).
- Optional: "procedures observed on more specific goals" section on abstract
  Goal pages, clearly labelled as **not** this Goal's procedures/verification.
- Loading / empty / error states for all of the above.
- `prod_frontend/README.md` still says "no hosted installer" — update.

### 12. `report_discovery` v2 (sharing, verification, credits)
- Today discoveries are private candidate Claims owned by the reporter. v2:
  opt-in publish flow with review, verification of discoveries, Credits for
  accepted ones. Must never turn request-scoped repo Claims into public facts.

### 13. Benchmark transfer propagation after review
- Transfer is judged (`benchmark_transfer` judgment kind) and produces a draft
  target Benchmark + `needs_review` submission. Recursion today only happens if
  a reviewer freezes the transferred Benchmark and someone calls
  `POST /v1/benchmarks/{id}/transfer`. Consider auto-enqueueing transfer to the
  next direct neighbours on freeze (bounded, idempotent, stops on
  `not_transferable|uncertain`).

---

## P2 — correctness and tech debt

### 14. Two Procedure-selection paths (consolidate or declare authority)
- REST / `find_best_way`: `retrieval_service.retrieve_procedures` (hard
  constraints → `task_procedure` judge → Wilson-LCB Pareto).
- MCP `find_ways`: `app/execution/goal_resolution.resolve_goal` +
  `repo_facts.RepoFactsProcedureSelector`. Goal choice was moved onto
  `retrieval_service` (contextual judge + judged hierarchy neighbours), but the
  Procedure tier still differs and the two can disagree.
- **Done when:** one authoritative implementation (or a documented split with a
  test proving they agree on shared cases).

### 15. Double-encoded jsonb writes outside the hierarchy code
- `json.dumps(...)` into `::jsonb` still in (count per file):
  `trace_worker.py` 13, `economy/submissions.py` 13, `product_model.py` 8,
  `applicability_judge.py` 5, `stealth/generator.py` 4, `ingestion_jobs.py` 4,
  `context_compaction/engine.py` 4, and ~18 more files (`grep -rl "::jsonb" app | xargs grep -c "json.dumps("`).
- Readers currently tolerate strings (`product_model._row`,
  `economy/submissions._project`), so nothing is visibly broken, but SQL
  jsonb operators and CHECKs see strings. Fix writers to pass objects; keep the
  tolerant readers for legacy rows; consider a one-off data repair migration.
- Some call sites may use raw `asyncpg.connect` without the codec — check each.

### 16. `goal_abstraction_state` is written but never read (coverage now refreshed on resolution)
- Maintained on every relation decision (now bounded to the component), but all
  reads derive live. Its `direct_resolved_at` / coverage go stale when a Goal
  resolves (no recompute on resolution). Either use it (root discovery could)
  and add a recompute hook when `resolved_at` changes, or stop writing it.

### 17. Placement result counters
- `handle_goal_abstraction_placement` counts pre-existing accepted neighbours
  twice on replay (`accepted_edges` 2 for one edge). Cosmetic; fix the tally.

### 18. Pre-existing failing offline tests (fail on main before this work too)
```
tests/test_auth_hardening_offline.py::test_supabase_service_role_key_is_never_referenced_by_the_backend_or_frontends
tests/test_bypass_closure_offline.py::test_B1_associate_solution_forces_proposed_status_and_server_identity
tests/test_bypass_closure_offline.py::test_B3_freeze_benchmark_proceeds_and_audits_when_accepted
tests/test_bypass_closure_offline.py::test_B3_freeze_benchmark_refuses_without_an_accepted_submission
tests/test_debate_openrouter_offline.py  (18 tests)
tests/test_phase1_security_boundaries_offline.py::test_one_builder_rule_no_hand_written_tenant_filters
tests/test_procdoc_v2_pipeline_offline.py::test_no_non_canonical_procedure_embedding_recipe_in_app
```
Root-cause each (code or stale test); never skip or delete a test to get green.

### 19. Migration 110 is not re-runnable
- `ALTER TABLE ... RENAME COLUMN problem_id TO goal_id` is unguarded; a second
  run fails. The migrate ledger prevents re-runs, and editing it would change its
  checksum (breaks deploys) — leave the file; only fix if a deliberate ledger
  correction is planned.

### 20a. Operations for the split control plane -- *human*
- Provision B: `python scripts/migrate.py --target search --dsn <B>`, set
  `SEARCH_DATABASE_URL` on every process, `admin search-db-backfill`.
- `admin shard-weight K000 0` once remote shards exist (public Goals never land on A).
- Schedule `admin prune-operational --older-than-days 30 --apply` (daily).
- Register shards with `--capacity-bytes 524288000`; the worker marks them `full` at 85%.

### 20. Performance measurements (none recorded yet)
- Per-request telemetry now exists: `retrieval_decisions.detail.shard_requests`
  (distinct shards, hydrations vs broadcasts, route lookups, per-shard latency,
  timeouts, cold connects) for find_best_way and find_ways.
- Measure on a realistic corpus (≥50k Goals, ≥10k accepted edges): placement
  latency, judge calls per placement, hierarchical vs flat retrieval latency,
  transfer latency, component-bounded recompute on a large root.
- Watch: coverage recompute for very abstract roots scales with subtree size.

### 21. Docs drift
- `backend/README_MCP_SERVER.md` tool table is stale (v1 surface is
  `find_ways`, `report_discovery`, prompts `survey_repo`, `plan_and_run`).
- `final_architecture.md` / `final_thing.md` don't describe the Goal hierarchy,
  Benchmark transfer, hosted deployment, or the connector.
- `proj_status.md` / `demo.md` are out of date (noted in final_architecture.md).

---

## Already done (don't redo) — for orientation
- Hierarchy fixed against real Postgres: jsonb decision metadata, ambiguous-column
  read query, `NameError` in `load_goal_identity_decision`, transfer lineage
  INSERT placeholder count, merge moving accepted edges (cycle → `proposed`).
- Graph neighbours judged (`task_goal`) before their Procedures are used;
  `find_ways` Goal choice via contextual judge + repo facts, lexical fallback
  labelled `goal_judgment.mode = "lexical_fallback"`.
- Dedicated `benchmark_transfer` judgment kind; placement prunes transitively
  redundant edges, enqueues neighbour frozen-Benchmark transfers, repair sweep.
- MCP server hostable (`STEALTHLAB_MCP_PUBLIC_URL`, `$PORT`, healthcheck).
- Connector `packaging/npm` (`stealthlab-mcp`), installers served by the site,
  verified `curl | bash` and `irm | iex` journeys.
- `report_discovery` source rows private per reporter; `create_goal_from_user`
  requires rationale + outcome and strips server-owned metadata keys.
