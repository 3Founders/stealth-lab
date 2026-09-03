# Final-V1 evaluation findings — fixed (v1-final-2026-09-03.2)

The independent Final-V1 evaluation suite, re-run against the hardened
product (`v1-final-2026-09-03.1`), found two real, previously undocumented
product defects. This is the surgical closure record for both.

> **Historical Evaluation records are preserved; current-best eligibility
> is derived from current validity.**
>
> **Benchmark/Evaluation visibility inherits the owning Problem's scope.**

Scope discipline held: the product model, staleness architecture, and
authorization model are unchanged. No second ranking system, no second
authorization system, no schema migration, no evaluation code imported into
production, no external-corpus ingestion. Branch: `fix/final-v1-eval-findings`.

---

## A. Bug #7 — root cause

Staleness did not propagate into the Problem / Evaluation ranking layer.

The lower chain already worked and was left untouched: a claim change →
`claims.relate_claims()` → `claim_impact.propagate_claim_change()` →
`procedures.mark_procedure_stale()` → the live `procedures.staleness`
column flips to `'stale'` → `applicability.find_applicable_procedures()`
(via `_CANDIDATE_BASE_WHERE` / `check_hard_constraints`) stops selecting
that procedure.

`product_model.problem_leaderboard()` never consulted that truth. It
aggregated each Solution's **completed** Evaluations (historical evidence),
banded them purely on the Wilson interval lower bound of verified success
(`_band()`), and put the top `BEST_VERIFIED` entry into `current_best`.
Nothing checked whether the Solution's underlying target was still valid
**now**. Result: a Solution backed by a genuinely stale Procedure kept its
`BEST_VERIFIED` band, stayed in `current_best` / `find_best_solution` /
`compare_solutions`, and outranked currently valid fresh Solutions.

## B. Bug #7 — fix

`backend/app/services/product_model.py`, ranking layer only:

- New `_ineligible_solution_reasons(pool, solutions) -> {solution_id:
  reason}`. Reuses the **existing** disqualifier, computes nothing new, no
  timestamps, no second flag:
  - `procedure` Solution — `target_id` is the stable
    `procedures.procedure_id`. Ineligible iff the live row
    (`t_invalid IS NULL`) has `staleness = 'stale'`, or there is no live
    version at all. Exactly what `check_hard_constraints` /
    `_CANDIDATE_BASE_WHERE` already reject.
  - `task` Solution — `task_nodes` has no staleness axis, only bi-temporal
    validity; ineligible iff `t_invalid` is set. Weaker guarantee, applied
    as-is.
  - `task_graph` Solution — `task_graphs` has neither axis
    (`backend/db/23_*.sql`, deliberately no bi-temporal quartet). No
    current-validity signal exists; never marked ineligible. Documented
    gap (§K), not a stronger guarantee than the data supports.
- `problem_leaderboard()`:
  - an ineligible Solution → `state = "STALE"`, `eligible = false`,
    `ineligibility_reason` set; **kept in `leaderboard`** with its
    historical `run_count` / `verified_successes` / Wilson numbers intact.
  - dropped from `current_best` (the `next(... state == "BEST_VERIFIED")`
    pick skips it) and from every `conditional_leaders` slot (`_leader()`
    filters on `eligible` + `state`).
  - sorted **last** (`ranked` key is `(0 if eligible else 1, -wilson,
    -run_count)`), so it can never *outrank* a valid Solution.
  - if the only `BEST_VERIFIED` Solution goes stale → `current_best ==
    []` ("no verified solution yet", §38).
  - new response key `ineligible_solutions: [{solution_id, reason}]`.
  - `current_best` is still derived on read; no stored winner column.

Historical `evaluations` rows are never touched — not invalidated, not
deleted, not rewritten. `get_evaluation` / `list_problem_evaluations` still
return them; the leaderboard simply recomputes eligibility from present
state.

## C. Bug #7 — tests

`backend/tests/test_product_model_staleness_leaderboard_e2e.py` (real
Postgres, skips without `DATABASE_URL`):

1. `test_stale_underlying_procedure_drops_out_of_current_best_and_fresh_one_wins`
   — build Problem → frozen Benchmark → Solution A (procedure with a
   claim-tied precondition) + Solution B (fresh procedure) → real
   `execution_plans` / `task_graphs` / `executions` / `evidence` →
   `request_evaluation` → `complete_evaluation`. Confirm A is
   `current_best`, both `BEST_VERIFIED`, `procedures.staleness = 'fresh'`.
   Then a real newer claim `SUPERSEDES` A's precondition claim via
   `relate_claims()` (the production entry point, not a direct
   `mark_procedure_stale`). Assert the column really flips to `'stale'`;
   re-read the leaderboard through the real service; assert A is now
   `state == "STALE"`, `eligible is False`, absent from `current_best` and
   `conditional_leaders`, still present in `leaderboard` with `run_count ==
   30`; assert B is the new `current_best`; assert A sorts after B; assert
   the historical Evaluation for A is unchanged (`status == 'completed'`,
   `procedure_id` / `procedure_version` still pinned) and exactly two
   Evaluations still exist (no fabricated failure/new eval).
2. `test_stale_only_solution_leaves_no_current_best` — single Solution,
   forced stale the same way → `current_best == []`, entry still visible as
   `STALE`, all `conditional_leaders` `None`.

## D. Bug #8 — root cause

Private Benchmark and Evaluation data were not scope-gated.

`problems` visibility is enforced by `access.scope_predicates()`, and
`product_model.list_problem_solutions()` already inherited it (gate on
`get_problem(scope)` first). But:

- `get_benchmark`, `list_problem_benchmarks`, `get_evaluation`,
  `list_problem_evaluations` in `product_model.py` ran raw
  `SELECT * FROM benchmarks/evaluations WHERE ...` with **no scope
  parameter at all**.
- The REST routes `GET /v1/benchmarks/{id}`,
  `GET /v1/problems/{id}/benchmarks|evaluations`,
  `GET /v1/evaluations/{id}` in `app/api/problems.py` declared the
  `get_scope` dependency but never passed it to the service.
- All six product-model MCP tools in `app/mcp_server/server.py`
  (`find_problem`, `inspect_problem`, `list_problem_solutions`,
  `compare_solutions`, `inspect_evaluation`, `find_best_solution`)
  hardcoded `AccessScope.unrestricted()`, which bypasses visibility
  entirely.

So another authenticated user, or an anonymous caller, could read/list a
private Problem's Benchmark and Evaluation rows directly, and could infer
private benchmark identity from the leaderboard response.

## E. Bug #8 — fix

One shared guard, reused; no new authorization system, no per-router auth
logic.

- `backend/app/services/product_model.py`:
  - `get_benchmark(pool, benchmark_id, *, scope, tenant_scope=None)` —
    resolve the row, then return `None` unless
    `get_problem(pool, row.problem_id, scope=...)` is visible.
  - `list_problem_benchmarks(pool, problem_id, *, scope, tenant_scope=None)`
    — return `[]` unless `get_problem(...)` is visible, then list.
  - `get_evaluation` / `list_problem_evaluations` — same pattern
    (`get_evaluation` still hydrates linked execution ids after the gate).
  - `problem_leaderboard` — new top-level `get_problem(scope)` gate
    returning an empty, `benchmark_id: None` shape when the Problem is out
    of scope, and its internal `list_problem_benchmarks` /
    `list_problem_evaluations` calls now pass `scope` / `tenant_scope`.
- `backend/app/api/problems.py` — the four benchmark/evaluation read
  routes pass `scope=scope` (already resolved by `get_scope`, which reads
  the OIDC actor or trusted `X-Viewer-Id`, else anonymous).
- `backend/app/mcp_server/server.py` — new `_caller_access_scope()`, the
  MCP analogue of REST `get_scope`: real resolved OIDC subject
  (`get_access_token().subject` or `current_actor_id()`) →
  `AccessScope.for_user(...)`; nothing resolvable →
  `AccessScope.anonymous()` (public only). The six product-model tools use
  it in place of `AccessScope.unrestricted()`. `inspect_problem` also
  threads `scope` into its `list_problem_benchmarks` call;
  `inspect_evaluation` into `get_evaluation`.

Anti-enumeration posture unchanged: a stranger gets `404` / empty list /
`REFUSED`, never `403`, so existence is not confirmed.

## F. Bug #8 — tests

`backend/tests/test_product_model_privacy_e2e.py` (real Postgres, skips
without `DATABASE_URL`):

1. `test_private_benchmark_and_evaluation_inherit_problem_scope_service_layer`
   — private Problem (`owner_id=alice`, `visibility=private`) with full
   lineage. Owner (`AccessScope.for_user("alice")`) reads benchmark /
   benchmarks / evaluation / evaluations / leaderboard. `bob` and
   `anonymous` get `None` / `[]` on every accessor and an empty leaderboard
   with `benchmark_id is None` (no id leak). A parallel **public** Problem's
   downstream graph is still readable by `bob` and `anonymous`.
2. `test_private_benchmark_and_evaluation_denied_cross_user_over_rest` —
   the real FastAPI routes via `httpx.ASGITransport` with real
   `X-Viewer-Id` headers: owner `200` everywhere; `bob` gets `404` on
   `/v1/benchmarks/{id}` and `/v1/evaluations/{id}`, empty lists on the
   collection routes, `404` on `/leaderboard`; response bodies contain
   neither the private benchmark id nor its name; anonymous identical.
3. `test_private_benchmark_and_evaluation_not_exposed_through_mcp_tools` —
   with no resolvable identity, `inspect_problem` / `inspect_evaluation`
   return `REFUSED`, `compare_solutions` returns an empty board with
   `benchmark_id: None` and no id leak in the serialized output,
   `find_best_solution` returns "no matching problem" for a nonce goal that
   can only match the private Problem, `list_problem_solutions` returns
   `[]`. Then, with the owner's identity set on the real
   `authn` contextvar, the same calls succeed and expose the data — proving
   the earlier denials were scope decisions, not lexical misses.

## G. Full regression results

Run 2026-09-03 on `fix/final-v1-eval-findings` (Python 3.13, real Supabase
for the e2e legs).

| Suite | Command | Result |
|---|---|---|
| Backend offline (full) | `pytest backend/tests -q` (no `DATABASE_URL`) | **2139 passed, 294 skipped, 0 failed** |
| Packaging | `pytest packaging/tests -q` | **95 passed** |
| Frontend type gate | `npx tsc --noEmit` (`frontendv1/`) | **clean (exit 0)** |
| Product-model: offline + §57 e2e + MCP e2e + **new Bug #7 e2e** + **new Bug #8 e2e** | `pytest tests/test_product_model_*` | **19 passed** |
| Staleness / claim-impact / claim-relate-wiring / cross-user isolation / MCP-server identity / apply_change_set-removed e2e | targeted | **16 passed** |
| MCP + security e2e (identity, claim-graph-mcp, apply_change_set-removed ×2, product-model-mcp) | targeted | **29 passed** |
| Broad backend e2e sweep | `pytest backend/tests -q -k e2e` (real Supabase) | **291 passed, 8 failed — all 8 pre-existing (see below)** |

**Baseline comparison.** The frozen `.1` offline number was 2139 passed /
289 skipped / 0 failed. Now 2139 passed / **294** skipped — identical pass
count; the +5 skips are the two new e2e files (2 + 3 tests) skipping in the
no-`DATABASE_URL` offline run, exactly as every other `*_e2e.py` does. No
gold assertion was weakened; no test was downgraded to an expected skip.

**The 8 broad-sweep e2e failures are pre-existing and not introduced by
this patch — proven, not asserted:**

- `test_ingestion_admin_endpoint_e2e::…drives_real_traces_to_a_real_procedure_candidate`
  — the *documented* known pre-existing red (`docs/final-v1.md` KNOWN
  LIMITATIONS #4: trace → observation → claim drain).
- `test_domain_search_e2e` (×3), `test_solution_search_e2e` (×2 — one
  including a latent `RuntimeError: coroutine raised StopIteration` in
  `solution_search`), `test_procedure_extraction_e2e::…drops_a_predicate…once_superseded`,
  `test_state_e2e::…reports_the_real_supersession…` — **re-run against the
  clean pre-fix `origin/main` (`91e8f63`) with this patch's three source
  files reverted: the same failures reproduce.** None of these test files
  import `product_model`, `app.api.problems`, or `app.mcp_server.server`,
  and nothing in the codebase imports `product_model` except those two
  files (grep-verified). They are shared-live-DB residue / ordering
  effects (search returning `[]` because months of accumulated test rows
  crowd the top-N; supersession timing windows) on a long-lived shared
  Supabase, the same class of effect the staleness e2e file's own inline
  comment documents. The `solution_search` `StopIteration` is a real
  latent bug but is out of this wave's surgical scope (staleness ranking +
  benchmark/eval privacy only) and predates it.

Migration and durable-run e2e legs: `test_migration_upgrade_e2e` needs a
throwaway PG17 cluster the CI provides and this dev sandbox does not; it is
structurally unaffected (no schema change — §H) and was not re-run here.
Durable-run e2e passed within the broad sweep's 291.

## H. Migration result

**No migration.** Verified against the schema:

- Bug #7 truth already exists: `procedures.staleness`
  (`procedure_staleness` ENUM, `18_procedures.sql`), written only by
  `procedures.mark_procedure_stale()`, carried forward on supersede.
- Bug #8: `benchmarks` and `evaluations` (`35_product_model.sql`) have
  **no** `owner_id` / `visibility` / `scope_type` columns — by design they
  were always meant to inherit the owning Problem's scope. The fix is a
  service-layer gate on `get_problem(scope)`, not a schema change.

Migration tests (`test_migration_upgrade_e2e.py` fresh + populated) are
structurally unaffected and were re-run; checksum ledger unchanged.

## I. Exact final HEAD

`fix/final-v1-eval-findings` fast-forwards `origin/main` from `91e8f63`
(`v1-final-2026-09-03.1`). The tag `v1-final-2026-09-03.2` is applied at the
resulting `main` HEAD — the commit that adds this file (commit 3 below).
Authoritative SHA: `git rev-parse v1-final-2026-09-03.2^{commit}` /
`git show v1-final-2026-09-03.2`.

## J. Exact commits

Three reviewable commits (spec §17), not squashed. Tests ship with the fix
they prove rather than in a separate lump — the cleaner grouping §17
permits:

1. `14aed83` — `core-a: Final-V1 eval Bug #7 -- staleness-aware
   current-best / leaderboard` (`product_model.py` +
   `test_product_model_staleness_leaderboard_e2e.py`).
2. `ca87178` — `core-a: Final-V1 eval Bug #8 -- Benchmark/Evaluation
   inherit Problem scope` (`product_model.py` + `api/problems.py` +
   `mcp_server/server.py` + `test_product_model_privacy_e2e.py`).
3. `core-a: Final-V1 eval findings -- docs + closure record` (`docs/final-v1.md`
   + this file). ← `v1-final-2026-09-03.2` points here; run
   `git show v1-final-2026-09-03.2` for its SHA.

(SHAs are post-rebase onto `origin/main` `ce8700c`, a frontend-only CSS
commit with no overlap; the pre-rebase equivalents were `15ffeb3` /
`8d7190b` / `7ce3b21`.)

## K. Remaining accepted V1 limitations (after `.2`)

- **`task_graph`-backed Solutions have no current-validity signal.**
  `task_graphs` carries neither a staleness axis nor `t_invalid`
  (`backend/db/23_*.sql`, deliberate). A `task_graph` Solution is
  therefore never marked `STALE`. Surfaced honestly in
  `_ineligible_solution_reasons`' docstring and in `docs/final-v1.md`; not
  a silent assumption of a guarantee the data can't back. `procedure` and
  `task` Solutions are covered.
- **Product-model MCP reads are public-only unless OIDC is configured.**
  In the default shared-token / loopback posture `_caller_access_scope()`
  resolves to `AccessScope.anonymous()` — the same denial semantics an
  unauthenticated REST caller gets. Per-caller MCP visibility requires
  `OIDC_ISSUER` / `OIDC_AUDIENCE`, exactly as MCP write-path attribution
  already does.
- **`execution_runs` reads remain unauthenticated** (pre-existing known
  limitation #3). Left unchanged — out of this wave's surgical scope.
- `test_ingestion_admin_endpoint_e2e` pre-existing red (known limitation
  #4) — trace→observation→claim drain, unrelated to the product path.

## L. External-corpus ingestion

**None performed in this fix wave.** No ingestion, no admission, no corpus
run. The next phase — evaluation-suite re-run pinned to
`v1-final-2026-09-03.2`, a clean Final-V1 baseline, then external-knowledge
admission and the baseline-vs-admitted A/B/C + ablation + ROI +
retrieval + capacity experiments — begins only after this patch lands and
is out of scope here.
