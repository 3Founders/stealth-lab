# FINAL V1 — Production Readiness Report

**Branch:** `core-a/ingestion-testing` (since integrated — **`main` is now
the authoritative branch**; this report is a point-in-time record of the
hardening pass and is not rewritten)
**Baseline SHA:** `a5dace6ccbe52c7669e13fa0efe8eb17448d05a8` (tag `v1-baseline-2026-09-02`)
**Final candidate SHA:** `dfb793e` (updated by §16 commit) — folded into the
frozen Final V1 `d0b173c` / `v1-final-2026-09-03`. **Post-freeze:** a
security hardening removed the public `apply_change_set` MCP tool; current
launch candidate is `v1-final-2026-09-03.1` → the commit tagged `v1-final-2026-09-03.1`. See
`.scratch/final-v1-postfreeze-hardening.md`.
**DB target for all E2E:** Supabase `wckeklqxmiglivfolujn` (ap-south-1 session pooler), Postgres 17.6, migrations 01–37.
**Date:** 2026-09-03

---

## 1. Scope of this pass

Close **every remaining non-frontend PARTIAL/OPEN** item from
`.scratch/final-v1-hardening-acceptance.md` and run the final release gate. The
standard is *"production ready for final V1 on the actual product path"* — not
isolated services.

Frontend (§34–48) + WebMCP (§49–54) were delivered in parallel by the frontend
lane and are recorded here for completeness; this lane did not author them.

---

## 2. Hardening commits (baseline → candidate)

| SHA | Lane | What |
|---|---|---|
| `92a6eee` | core-a | claim-graph viewer rebuilt on force-graph + similarity edges |
| `5b2fc15` | core-a | better-ways corpus Phase 1 — source resolution + one-procedure gate |
| `3bfe777` → `ed958b2` | core-a | corpus wave Phases 3–10 (Supabase substrate, 50-source registry, admission, canonical ingestion, retrieval + claim-graph proof) |
| `bbf6ff0` | core-a | claim-graph overview e2e — `KeyError 'relation'` fixed at the read layer |
| `53c93f9` | core-a | §0 current-state audit |
| `209564a` | core-a | §28 ChatGPT export branch-tree reconstruction |
| `47f4ffd` | core-a | §29 ingestion treats untrusted documents as data |
| `8aecc04` | core-a | migration 35 — Problem/Benchmark/Solution/Evaluation product model |
| `3229641` | core-a | product-model service + REST + comparability + evidence-derived ranking |
| `9040dfa` | core-a | product-model MCP tools (§37) |
| `002e6da` | core-a | migrations 36/37 + durable execution retry/resume (§24–27, §58 E2E) |
| `0f17055` | core-a | product-model MCP e2e test isolation |
| `320918a` / `892f60c` | core-a | hardening acceptance matrix + offline regression gate CLOSED |
| `b966cd7` | core-a | **execution descriptor (§1) + durable-run wired into the real tier-2 path (§3)** |
| `39e2892` | frontend | **V1 benchmark surface — pages, backend-ranked leaderboard, WebMCP, auth** |
| `44c942c` | core-a | **documentation reflects the shipped Final-V1 system (§6)** |
| `a8bd940` | core-a | **retry/resume REST + MCP surface over the durable-run service (§2)** |
| `885d83a` | core-a | **migration upgrade-path e2e (§4)** |
| `dfb793e` | core-a | **performance sanity baseline (§5)** |

`main` (`origin/main` = `a5dace6`) is **untouched**.

---

## 3. Architecture summary — what actually ships

### 3.1 Product layer (Problem / Benchmark / Solution / Evaluation)
- **Durable, versioned, evidence-derived.** Migrations 35. Tables `problems`,
  `benchmarks` (+ `trg_benchmark_frozen_immutable`), `benchmark_cases`
  (FK `task_nodes` — reuses the smallest existing executable unit, no new
  abstraction), `solutions` (association only — polymorphic `target_id` +
  `target_table`, type↔table CHECK, target never copied), `evaluations`,
  `evaluation_executions`.
- **No stored winner.** `product_model.problem_leaderboard` computes on read
  from completed-evaluation lineage using `wilson_interval` lower bound.
  `current_best` is a **list** (ties via `TIE_EPSILON`); `[]` when nothing is
  verified. The Problem row carries no `winner`/`best_solution_id` column.
- **Untrusted caller cannot fabricate COMPLETED.** DB backstop
  `trg_evaluation_completed_has_lineage` + `complete_evaluation` recomputes
  `run_count` / `verified_successes` from the linked executions & evidence and
  raises on empty `execution_ids`. Caller-supplied success numbers are ignored.
- **One service, two surfaces.** REST `app/api/problems.py` (17 routes) and MCP
  (`find_problem`, `inspect_problem`, `list_problem_solutions`,
  `compare_solutions`, `inspect_evaluation`, `find_best_solution`) both call
  `app.services.product_model` — no ranking logic in either router or tool.

### 3.2 Durable execution (retry / resume)
- **State layer over the existing substrate, not a new scheduler.**
  Migrations 36/37. `execution_runs` (the mutable resumable unit, beside the
  append-only `executions`) + `execution_run_nodes` (per-node status
  pending/running/succeeded/failed/blocked/cancelled/resumable, `attempt_count`,
  pinned `implementation_id`/`version`, `side_effecting`, `error_class`,
  `worker_id` + lease). No queue, no polling loop — each node transition is its
  own short transaction; retry happens on the next `resume_run` / `retry_node`.
- **The one bridge:** `app/execution/durable_graph.run_graph_durably` — the
  production tier-2 path (`find_best_way`, `reproduce_procedure`) goes through
  it instead of the in-memory `graph_executor`, so a crashed run resumes
  through the same product path. `graph_executor` is unchanged and still used
  by non-stateful callers (`_respond_tier1_hit`, offline tests).
- **Context-free surface:** `app/execution/durable_resume.py` +
  `app/api/runs.py` (`GET /v1/runs/{id}`, `/nodes`, `POST …/resume`,
  `…/nodes/{order}/retry`) + MCP (`inspect_run`, `resume_execution_run`,
  `retry_run_node`). It rebuilds the `CompiledPlan` + `deps` from the persisted
  `execution_plans` / `task_graphs` rows and dispatches each node through the
  real `implementation_executor` → `providers.get_provider(kind).execute`. It
  contains **zero** retry/resume/scheduling logic — every transition, lease,
  terminal fence and attempt bound stays in `durable_run`.
- **Honest refusal:** a run whose plan was compiled by the coding-agent plan
  compilers, or whose pending nodes have no real provider, returns
  `{"status": "needs_product_context", …}` pointing at
  `find_best_way(resume_run_id=…)` — never a fabricated success.

### 3.3 Canonical execution descriptor (§1)
- `implementation_registry.descriptor(row)` — pure function, `DESCRIPTOR_VERSION
  = "impl-descriptor/1"`, stable field order, deterministic serialization,
  missing optionals → `{}`. Validates `kind ∈ REGISTRABLE_KINDS` (the existing
  DB CHECK vocab — no duplicate vocabulary added).
- **Secret-safe:** `_sanitize_auth` replaces any inline secret-ish string with
  `{"redacted": true}` and recurses one level; credential **references** are
  kept. `auth_requirements` carries references only, never material.
- Consumed by MCP `inspect_implementation` / `resolve_implementation` and REST
  `GET /v1/implementations/{id}/descriptor` (404 for missing/invisible).

### 3.4 Ingestion trust boundary (§28, §29)
- **§28** — `_chatgpt_active_node_ids(mapping, current_node)`: `None` → linear
  (behaviour unchanged), `[]` → branching + ambiguous → discussion-only,
  non-empty → active branch only. Abandoned siblings (a fabricated tool call, a
  hedged dead end) contribute no verified evidence and do not suppress a
  genuine active-branch confirmation.
- **§29** — `skill_ingestion.py`: untrusted document body fenced in
  `<untrusted_source>` (fence markers stripped from data), system prompt
  asserts only it is authoritative, `_validate_capability_statement` (exactly
  one CAPABILITY line or ABSTAIN, length/charset), semantic reject of
  trust-claims / meta-directives / ungrounded assertions, **fail-closed** →
  `capability_statement` stays NULL and the procedure lands
  `system_pending_review`. Grep-verified: `capability_statement` is read only
  by `semantic_projections.py` (embedding text) and `replay.py` (string diff)
  — never by applicability / capabilities / verification_state / approval /
  scope / execution. Every ingested procedure is born `candidate`.

---

## 4. Security / authorization re-audit (§9)

| Surface | Finding | Verdict |
|---|---|---|
| Cross-user execution-run mutation (resume / retry) | `authorize_run_mutation(run_created_by, actor_id)` raises `NotYourRun` (403 REST / `REFUSED` MCP) iff **both** identities resolve and differ. REST resolves from `scope.viewer_id`; MCP from `resolved_caller_identity_or_none()` (MCP access-token subject → OIDC actor contextvar → `None`, never a tool-name fallback). | **PASS** — matches the repo's header-identity posture; `execution_runs` has no owner column so "unknown on either side → allow" is consistent with every other write path. |
| Execution-run **reads** | Open (`GET /v1/runs/{id}` returns `created_by`, `scope_type`, `scope_entity_id`). | **ACCEPTED** — same posture as every other read surface in V1; no secret material. Noted as a post-V1 tightening if private Problems ship. |
| Problem / Benchmark / Evaluation privacy | `problems` carry `owner_id` / `visibility` / `scope_type`; reads thread `scope_predicates()`. Solutions inherit Problem visibility (`list_problem_solutions` gates on `get_problem(scope)`). `test_migration_upgrade_e2e.py` re-verifies a private procedure is absent from the anonymous-visible set post-upgrade. | **PASS** |
| Descriptor secret leakage | `_sanitize_auth` unit-tested for inline `token` / `client_secret` / nested `oauth` → `{"redacted": true}`; `test_implementation_descriptor_offline.py` asserts no drift. | **PASS** |
| Evaluation lifecycle forgery | DB trigger + service both block `status='completed'` without execution lineage; caller success numbers ignored. | **PASS** |
| Ingestion capability escalation | Fail-closed; capability statement never becomes trust/execution authority (grep-verified). | **PASS** |
| MCP `apply_change_set` | **CLOSED post-freeze (`v1-final-2026-09-03.1`): removed as a public MCP tool** (public tool count 30 → 29). Graph mutation from MCP is gated via `submit_approval` / `decide_decomposition` only; `KnowledgeUpdater` reachable from `app/api/approval.py::decide` / `app/api/decompose.py::decide` alone. | **PASS** |
| Credentials in Postgres | No new secret-storing column this wave; `auth_requirements` holds `credential_ref` only. | **PASS** |

No secret, `.env`, or service-role key is committed. `frontendv1/.env.local.example`
contains placeholders only (verified).

---

## 5. Retry / resume final-semantics re-check through the wired path (§10)

Verified by `test_durable_graph_e2e.py` (through `run_graph_durably`, the helper
MCP `find_best_way` / `reproduce_procedure` now call) and
`test_durable_resume_e2e.py` (through the REST/MCP surface):

| Invariant | Evidence |
|---|---|
| A succeeds → B fails/crashes → state persists; worker disappears; a new invocation resumes | `test_run_graph_durably_creates_durable_run_and_resumes_through_the_same_helper`: run 1 raises `WorkerLost` mid-node B → A `succeeded`, B `running` (expired lease), C `pending`, **0** `executions` rows. |
| A is **not** re-run on resume | callback log asserts `[(0, 1)]` for node 0 across both passes. |
| B obeys the retry policy; attempt count persists | `by2[1]["attempt_count"] == 2`, `first_pass_success is False`. `RETRYABLE_ERROR_CLASSES` vs `NON_RETRYABLE`; unknown class → not retried; `max_attempts` default 3 (`test_durable_run_offline.py`, 12 cases). |
| C stays blocked until B succeeds | `_blocked()` / `_ready()` over `deps`; `by2[2]["status"] == "succeeded"` only after B. |
| Implementation pinned, never re-resolved on resume | `execution_run_nodes.implementation_id` / `implementation_version` set at first resolve; `trg_ern_terminal_fence` blocks rewriting a succeeded node's binding. |
| Exactly one immutable `executions` row; no duplicate evidence, no trust inflation | terminal `_finalize` appends **one** row via `record_plan_execution` (impl id pinned); the manual `record_plan_execution` calls in `server.py` tier-2 / `reproduce_procedure` were **deleted** (comment records why). `len(rows) == 1`, pinned to `procedure_id` / v1. |
| Concurrent resume safe | `_claim_run` (worker_id + lease); second worker under a live lease → `ResumeInProgress` (409). `test_concurrent_resume_is_refused_not_duplicated`. |
| Stale worker cannot mutate terminal state | `trg_ern_terminal_fence` — a succeeded node cannot leave succeeded or have `attempt_count` / `result_ref` / impl binding rewritten. |
| Side-effecting node not blindly replayed | `_drive`: a `running` + expired-lease node with `side_effecting=true` → `resumable` + run `status='paused'` + a note that `retry_node()` needs an explicit decision. Pure nodes re-arm and retry. |
| Terminal stays terminal | 2nd resume of a succeeded run is an idempotent no-op (`len(calls) == calls_before`). |

---

## 6. Migration upgrade path (§4)

`test_migration_upgrade_e2e.py` (1 passed, ~14s):
- Spins up a **throwaway** local Postgres 17 cluster (pgvector 0.8.0) on a
  random port; docker `pgvector/pgvector:pg15` fallback; `pytest.skip` naming
  both if neither is available.
- Applies **01..34 only** (replicating `migrate.py`'s ledger + checksum +
  per-file-tx loop), writes a pre-hardening dataset through the **real** write
  paths (`capture_procedure` ×2 incl. one `visibility='private'`,
  `capture_claim`, `implementation_registry.register`, plus real-shape INSERTs
  for `execution_plans` / `task_graphs` / 6 `executions` / 5 `evidence`).
- Applies **35/36/37** via the real `scripts/migrate.py` subprocess.
- Asserts: no checksum drift (`--status`: no MISMATCH, no pending, 37 applied);
  **every pre-existing row byte-for-byte unchanged** (full snapshot dict diff);
  scope/visibility still enforced (real `scope_predicates()` — private proc
  absent from the anonymous set); new product-model tables work and reference
  the pre-existing rows (`create_problem` → `create_benchmark` →
  `associate_solution` → `request_evaluation` → `complete_evaluation` with the
  pre-existing execution ids → `problem_leaderboard`); new durable-run tables
  work (`start_run` + `execute_run` a 2-node graph against the pre-existing
  plan); no destructive DDL in 35/36/37 (`git grep -E "DROP TABLE|DROP
  COLUMN|TRUNCATE"` → empty).

Migrations 35/36/37 are additive + idempotent; 01–37 are immutable + checksummed.

---

## 7. Performance sanity (§5)

`.scratch/final-v1-perf-sanity.md` — probe vs live Supabase, N=20 per DB path,
zero failures. **GO.**

| Path | p50 ms | p95 ms | DB queries |
|---|---:|---:|---:|
| `problem_leaderboard` | 150 | 374 | **8 (fixed — no N+1)** |
| `durable_run.start_run` (3-node) | 133 | 361 | 7 |
| `mcp.inspect_problem` | 283 | 744 | 16 |
| `claim_graph_api.get_claim_graph_overview` (with_status) | 394 | 952 | 98 |
| `embeddings.embed_one` (distinct text) | 3431 | 5599 | n/a (external API) |

- **Leaderboard N+1: none.** 4 fixed SQL reads + a pure-Python loop over
  pre-fetched lists — query count is data-independent (8 whether 2 or 200
  evaluations).
- Durable retry/drive loops bounded (`while attempt < max_attempts`). Embedding
  cache dedupes repeats. No unbounded retry, no repeated embed.
- **Bounded O(N) fan-out** in `get_claim_graph_overview(with_status=True)`:
  ~4.5 queries/node, capped by `limit ≤ 600` and a semaphore of 8, documented,
  flat fast-path (`with_status=False`) exists. Logged as a post-V1 follow-up
  (set-based lifecycle query), not a blocker.
- Absolute latencies include public-internet RTT to a remote session pooler —
  a ceiling, not a floor.

---

## 8. API / MCP / WebMCP

- **REST:** `/v1/problems` (17 routes), `/v1/implementations/{id}/descriptor`,
  `/v1/runs/{id}` + `/nodes` + `/resume` + `/nodes/{order}/retry`. All thin,
  all delegate to a shared service.
- **MCP:** product-model tools (6) + `inspect_run` / `resume_execution_run` /
  `retry_run_node` + descriptor on `inspect_implementation` /
  `resolve_implementation`. `find_best_way` gained `resume_run_id` and now
  drives tier-2 through `run_graph_durably`.
- **REST ranking == MCP ranking** — one service, verified.
- **WebMCP** (`frontendv1/src/webmcp/`, frontend lane, `39e2892`):
  `document.modelContext.registerTool` wrapper + feature-detect, semantic tools
  over the domain API, schema validation. Owned + accepted by the frontend
  lane.

---

## 9. Frontend status

Delivered by the frontend lane in `39e2892` (`frontendv1/`, Next 16 + React 19,
52 tracked files): Home / Search / Problem (benchmark hero with
current-best/tie/open + backend-ranked leaderboard) / Solution / Procedure /
Task / Implementation / Claim / Evaluation / Repository / Project / Personal /
Auth (OIDC + dev viewer fallback) / Submit pages, single typed API client
(`src/lib/api/{client,types}.ts`), WebMCP provider.

This lane did **not** author or modify `frontendv1/` (§8 coordination rule). One
git-hygiene note: `frontendv1/` was once staged for wholesale deletion by a
stray broad `git add` in the shared tree and was restored from HEAD; a small
number of build-log files (`build*.log`, `dev.log`, `tsc.log`) are tracked in
`39e2892` and could be `.gitignore`d by the frontend lane — cosmetic, not a
blocker.

---

## 10. Full regression (§11)

All runs 2026-09-03, exit 0.

| Suite | Command | Result |
|---|---|---|
| Backend offline | `cd backend && python -m pytest tests -q` (DATABASE_URL unset) | **2117 passed, 288 skipped, 0 failed**, 14 warnings, 5m22s. The 288 skips are `*_e2e.py` / `test_schema_drift.py` self-skipping without a DB, as designed. |
| Backend E2E vs Supabase | `DATABASE_URL=<pooler> python -m pytest tests/test_product_model_e2e.py tests/test_product_model_mcp_e2e.py tests/test_durable_run_e2e.py tests/test_durable_graph_e2e.py tests/test_durable_resume_e2e.py tests/test_claim_graph_overview_e2e.py tests/test_claim_graph_mcp_e2e.py tests/test_migration_upgrade_e2e.py -q` | **12 passed, 0 failed**, ~1m06s (each file is one end-to-end scenario: product lineage 1, product MCP 1, durable_run 3, durable_graph 1, durable_resume 3, claim-graph overview 1, claim-graph MCP 1, migration upgrade 1). |
| Harness | `cd experiments/harness && python -m pytest tests -q` | **254 passed**, 6.7s. |
| Packaging | `cd packaging && python -m pytest tests -q` | **95 passed**, 9.2s (after `9721c84` — the stale tool-list snapshot). |

Total: **2478 passed, 288 skipped, 0 failed** across offline + live + harness +
packaging.

The `providers.py` `SubprocessSandboxExecutor: network_access=False is NOT
enforced` warning during `test_durable_resume_e2e` is a pre-existing, documented
executor limitation (module docstring) surfaced because the deterministic
recovery test genuinely runs generated code — not a regression.

Not re-run this pass (out of scope / unchanged): `experiments/swebench_pro/`
(older HTN/graph-memory arms), the ~25 hand-run `backend/check_*.py` probes, and
the pre-existing live-DB-only `test_ingestion_admin_endpoint_e2e` red (audit-noted;
trace→claim path, not the corpus/product path).

---

## 11. Known limitations

### KNOWN-ACCEPTED (documented, not blockers)
- **Execution-run reads are unauthenticated.** Consistent with every other V1
  read surface. Tighten if private Problems ship.
- **`get_claim_graph_overview(with_status=True)`** issues ~4.5 queries/node
  (bounded by `limit ≤ 600`, semaphore 8). Flat fast-path exists. Post-V1:
  set-based lifecycle query.
- **`mcp.inspect_problem`** re-runs `get_problem` 3× / solutions 2× per call —
  constant factor, not data-scaled.
- **`list_problem_{solutions,benchmarks,evaluations}`** have no `LIMIT` —
  bounded in practice by small per-problem object counts.
- **Coding-agent (sandbox) durable runs cannot be resumed context-free** — the
  REST/MCP surface returns `needs_product_context` and points at
  `find_best_way(resume_run_id=…)`. By design.
- **`find_best_way` tier-2 not perf-measured** (requires the sandbox).
- **Frozen-doc drift:** `schema.md` does not list the migration 35/36 tables;
  the "no solutions table" decision in `BAND0_DECISIONS.md` was reversed with a
  board note. Frozen docs were **not** edited (hard rule 3). Recorded here and
  in the `44c942c` commit message.
- **`frontendv1/` build logs tracked** in `39e2892` — cosmetic, frontend lane's
  call.
- Pre-existing `test_ingestion_admin_endpoint_e2e` red (audit-noted, trace→claim
  path, live-DB only) — out of this wave's scope, not on the corpus/product
  path.

### UNRESOLVED-BLOCKERS

**None** on the backend / product-substrate path.

One PARTIALLY CLOSED item remains, owned by the frontend lane, not a backend
blocker: a scripted browser E2E of the `frontendv1/` surface (§56) was not part
of the frontend lane's landed commit `39e2892`. The pages are built and typed
against the live API contract; the interactive-browser proof is the frontend
lane's to close.

---

## 12. Post-V1 work (explicitly deferred)

- Set-based claim-lifecycle query for large graphs.
- Owner column + authenticated reads on `execution_runs` if private Problems ship.
- Full convergence of `find_best_way` (HTN coding agent) and `find_best_solution`
  (evidence leaderboard) into one entry point.
- `schema.md` refresh (frozen — needs a formal unfreeze decision).
- Perf measurement of the tier-2 coding-agent path under a real sandbox.
- Three-arm measurement sweeps (Band 3 — solo / ordinary memory / verified
  substrate).

---

## 13. Code review (§12) — `git diff --stat a5dace6..HEAD`

Reviewed the full backend diff (net-new modules + tests + migrations 35/36/37;
modified `server.py`, `main.py`, `claims.py`, `implementations.py`,
`chat_history_import.py`, `historical_bootstrap.py`, `skill_ingestion.py`):

| Risk | Finding |
|---|---|
| Duplicated abstractions | None. Descriptor reuses `REGISTRABLE_KINDS`; `durable_resume` has zero retry logic (delegates to `durable_run`); `durable_graph` is the single CompiledPlan→durable-run bridge; product ranking reuses `wilson_interval`. |
| Dead code | None found in the diff. `graph_executor` retained deliberately for non-stateful callers. |
| Fake / hand-written state | None. `current_best=[]` when unverified; `needs_product_context` instead of a fabricated success; evaluation completion recomputes from lineage. |
| Client-supplied trust | Blocked: capability statement can't become trust/execution authority (§29, grep-verified); untrusted caller can't fabricate COMPLETED (DB trigger + service); descriptor strips inline secrets. |
| Missing authorization | Run mutations gated (`authorize_run_mutation`). Reads open — consistent with the V1 posture, noted in §11 KNOWN-ACCEPTED. |
| Unbounded retries | None — `while attempt < max_attempts`, default 3; unknown error class → not retried. Perf probe confirmed all loops bounded. |
| Unsafe side-effect replay | Blocked — `_drive` parks a crashed side-effecting node as `resumable` + run `paused`, requires explicit `retry_node()`. |
| Migration hazards | 35/36/37 additive + idempotent; no `DROP TABLE`/`DROP COLUMN`/`TRUNCATE` (asserted in `test_migration_upgrade_e2e.py`); 01–37 immutable + checksummed. 37 only relaxes a CHECK one-directionally. |
| REST vs MCP drift | None — both surfaces delegate to the same service for product model and for retry/resume. |
| Frozen execution semantics | `executions` / `execution_plans` / `task_graphs` still append-only + trigger-frozen; the only behavioural change is tier-2 routing through `run_graph_durably`, which still appends exactly one immutable `executions` row (double `record_plan_execution` removed). |
| Stale docs | Fixed in `44c942c`. Frozen-doc drift recorded, not edited (hard rule 3). |
| Accidental cross-lane changes | `frontendv1/` was restored after a stray broad stage; every agent commit verified to contain only its own files. |

---

## 14. Verdict

**FINAL V1 PRODUCTION READINESS: COMPLETE.**

Every required non-frontend acceptance item is CLOSED with a commit SHA and a
test that runs:

- §1 canonical execution descriptor — `b966cd7`, `test_implementation_descriptor_offline.py` (7)
- §2 retry/resume REST + MCP surface — `a8bd940`, `test_durable_resume_offline.py` (11) + `test_durable_resume_e2e.py` (3)
- §3 durable-run wired into the real tier-2 path — `b966cd7`, `test_durable_graph_e2e.py` (1)
- §4 migration upgrade-path e2e — `885d83a`, `test_migration_upgrade_e2e.py` (1)
- §5 performance sanity — `dfb793e`, `.scratch/final-v1-perf-sanity.md` (GO)
- §6 documentation — `44c942c`, `docs/final-v1.md`
- §7 WebMCP — `39e2892` (frontend lane), `frontendv1/src/webmcp/`
- §9 security re-audit — §4 above, PASS on every surface
- §10 retry/resume final semantics through the wired path — §5 above
- §11 full regression — §10 above: 2478 passed / 288 skipped / **0 failed**
- §12 code review — §13 above, no blockers
- §13 acceptance matrix rewritten — `.scratch/final-v1-hardening-acceptance.md`
- §14 this report

The one PARTIALLY CLOSED row (frontend scripted browser E2E, §56) is owned by
the frontend lane and is not a backend or product-substrate blocker.

**Per the directive: product development stops here.**
