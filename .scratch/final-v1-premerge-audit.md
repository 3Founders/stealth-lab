# Final-V1 Pre-Merge Audit

READ-ONLY audit. No source edited, nothing committed or merged.

- Baseline: `a5dace6` (tag `v1-baseline-2026-09-02`, `origin/main`)
- HEAD audited: `2fae92c` (`core-a: Final-V1 scripted browser E2E + wire the Problems list page`)
- Range `a5dace6..2fae92c`: linear, no merge commits (re-verified). ~32 commits, all `core-a:` except `39e2892` (`frontend:`).
- Diff totals: `git diff --stat a5dace6 2fae92c` → 194 files (first range) + the 2fae92c delta (frontendv1/ e2e only). 33k+ insertions, ~74 deletions in backend; the bulk of insertions is `.scratch/` corpus artifacts, `frontendv1/` (new app + package-lock), and new tests.

---

## 1. Pre-merge diff audit (§11)

**Secrets** — `git diff a5dace6 2fae92c | grep -iE 'sk-…|service_role|BEGIN (RSA|OPENSSH) PRIVATE|-----BEGIN|xox[baprs]-|AKIA…|ghp_…|password=…'` → **no hits**. `.env.local.example` (`frontendv1/.env.local.example`) contains only placeholder keys and commented-out OIDC vars, no material. No `.env`, no service-role key, no token.

**Frozen docs** — `verified_procedural_experience_system_ideal_specification_v4.md` and `schema.md` are **absent** from `git diff --name-only a5dace6 2fae92c`. Not edited. (Board note stands: `schema.md` legitimately lacks the mig 35/36/37 tables — recorded, not a doc bug.)

**New files outside `.scratch/` and `frontendv1/`** (all in-scope for the Final-V1 waves):
`backend/app/api/problems.py`, `backend/app/api/runs.py`, `backend/app/execution/durable_graph.py`, `durable_resume.py`, `durable_run.py`, `backend/app/mcp_server/claim_graph_page.py`, `backend/app/mcp_server/vendor/force-graph.min.js`, `backend/app/services/product_model.py`, `backend/db/35_product_model.sql`, `36_durable_execution_runs.sql`, `37_execution_runs_terminal_chk_fix.sql`, `docs/final-v1.md`, plus 12 new `backend/tests/test_*` files.

**New files at `backend/` root** — none. No new one-off `check_*.py` / `test_*_live.py` probes.

**No unrelated refactor / experimental / dead code in production `app/`** — grep of the new modules for `TODO|FIXME|XXX|HACK|breakpoint()|import pdb|print(|NotImplementedError|EXPERIMENTAL` → no hits. Grep for test-leak markers (`pytest|FakePool|FakeConn|monkeypatch|mock|if…testing`) in the new `app/` modules → no hits.

**API surface changes are additive** — `backend/app/api/claims.py` (+77, 0 real deletions), `backend/app/api/implementations.py` (+22, 0 deletions). `backend/app/main.py` adds two `include_router` lines (`problems`, `runs`), no removals.

**server.py deletions reviewed** (`git diff --numstat` = 363 ins / 26 del): the deleted lines are exactly the old in-memory `execute_task_graph(...)` + `record_plan_execution(...)` blocks in the `find_best_way` tier-2 and `reproduce_procedure` paths, replaced by `run_graph_durably`. Intended §3 refactor, not an accidental removal.

**Duplicate abstraction (minor)** — `_mapping_has_branching` / `_chatgpt_active_node_ids` are implemented **verbatim in two files**: `backend/app/local_agent/chat_history_import.py:152,168` and `backend/app/local_agent/historical_bootstrap.py` (same §28 block). Both are local-only ingestion entry points; logic is identical and correct. A shared `_chatgpt_branch.py` helper would be cleaner. Consistent with the repo's "deliberately not shared" pattern (CLAUDE.md), so noted, not blocking.

**Stray artifacts (minor)** — `frontendv1/build.log`, `build2.log`, `build3.log`, `dev.log`, `tsc.log` (0–1160 bytes, plain `next build` / `next dev` console output, no secrets) are committed. `frontendv1/.gitignore` now carries `*.log` (added in `2fae92c`) but the already-tracked copies remain. Recommend `git rm --cached` on the five `.log` files before merge. `frontendv1/tsc.log` is empty.
`.scratch/corpus_wave/sources/_raw/addy_*.md` — six 0-byte files; scratch only, harmless.
`backend/app/mcp_server/vendor/force-graph.min.js` also has a copy at `.scratch/corpus_wave/force-graph.min.js` — vendored lib for the claim-graph viewer page; scratch copy is throwaway.

**Verdict: PASS** — no secret, no frozen-doc edit, no unrelated refactor, no dead/experimental code in `app/`, no test workaround in production. Minor cleanups only (5 stray `.log` files; 2 duplicated local-agent helpers; a scratch copy of a vendored JS lib). None blocking.

---

## 2. Product-path trace (§5)

**Durable execution wiring — verified in code:**

- `find_best_way` tier-2 (`backend/app/mcp_server/server.py:1345-1362`) calls `run_graph_durably(...)` and NOT `execute_task_graph`. Comment `server.py:1387-1389`: "the immutable `executions` row is appended by durable_run's `_finalize` … do NOT call record_plan_execution here or the run gets two." No `record_plan_execution(` call in that function — the only actual `record_plan_execution(` call in server.py is `server.py:886` (tier-1). Confirmed by `git grep 'record_plan_execution(' -- server.py`.
- `reproduce_procedure` (`server.py:1689-1702`, inside `_run_tier`) calls `run_graph_durably(...)`; outcome recorded via `record_execution_outcome(..., evidence_type="reproduction")` (`server.py:1713`), which is the procedure-outcome recorder, not `record_plan_execution`. No double-record.
- `_respond_tier1_hit` (`server.py:803-889`) deliberately still uses the in-memory pass: `execute_task_graph(compiled_plan.graph, ...)` at `server.py:883`, then `record_plan_execution(...)` at `server.py:886`. Correct — tier-1 is non-stateful (docstring `server.py:911-919`).
- `graph_executor.execute_task_graph` still exists (`backend/app/execution/graph_executor.py:101`). Non-test callers: `server.py:883` (tier-1, above) and `backend/app/local_agent/runner.py:282` (local non-durable agent). Not a second live engine on the durable path.
- `run_graph_durably` (`backend/app/execution/durable_graph.py:56-133`) delegates to `durable_run.start_run` / `execute_run` / `resume_run`, passing `compiled` so `durable_run._finalize` appends the one immutable `executions` row.
- `durable_run._finalize` (`backend/app/execution/durable_run.py:294-329`): appends **one** `executions` row via `record_plan_execution` (`durable_run.py:315`, only when `compiled` supplied), inside a single transaction, then `UPDATE execution_runs SET … final_execution_id=$4 … WHERE id=$1 AND status NOT IN ('succeeded','failed','cancelled')`. `execute_run` (`durable_run.py:347-351`) and `resume_run` (`durable_run.py:374-378`) both short-circuit on an already-terminal run before `_drive`, so `_finalize` runs once per run → one immutable `executions` row.
- Retry/resume surface: REST `backend/app/api/runs.py` (`inspect_run`, `resume_execution_run`, `retry_run_node`) delegates to `durable_resume`; MCP `server.py:2836-2899` (`inspect_run`, `resume_execution_run`, `retry_run_node`).

**Leftover dead imports (minor, non-blocking):** `server.py:1283` still imports `execute_task_graph` and `server.py:1284`/`server.py:1631` still import `record_plan_execution` even though the calls were deleted. `NodeResult` and `persist_compiled_plan` from the same import lines are still used. Unused-import lint nit; no second engine is invoked, no double-record.

**Product model (§14/§16/§37/§38) — verified in `backend/app/services/product_model.py`:**

- `problem_leaderboard` (`product_model.py:479-560`): `current_best` is a `list[str]` (`best: list[str]` at `product_model.py:538`), computed **on read** (lines 538-543) as the top `BEST_VERIFIED` entry by Wilson lower bound (`wilson_interval`, imported `product_model.py:36`) plus ties within `TIE_EPSILON`. `[]` when nobody clears the bar (`product_model.py:538,557` comment "`[]` means: no verified solution yet (§38)"). No `UPDATE` of any stored winner anywhere in the function.
- `complete_evaluation` (`product_model.py:311-397`): raises `ValueError("cannot complete an evaluation with no linked executions (§16)")` on empty `execution_ids` (`product_model.py:325-326`); **recomputes** `run_count = len(found_ids)` (`product_model.py:345`) and `verified` via a `fetchval` over linked executions + supporting evidence (`product_model.py:350`), ignoring caller-supplied counts; caller count keys are stripped from `metrics` (`product_model.py:378-379`). DB backstop trigger `trg_evaluation_completed_has_lineage` created in `backend/db/35_product_model.sql:308-316`.
- Read paths thread scope: `get_problem` / `list_problems` / `find_problem` call `scope_predicates(scope, tenant_scope or TenantScope.unrestricted(), …)` (`product_model.py:115,133,162`). REST routes all take `scope: AccessScope = Depends(get_scope)` and pass `scope=scope`; `problem_leaderboard` route gates on `get_problem(scope)` first (`backend/app/api/problems.py:144-146`).

**Verdict: PASS** — the durable path is wired end to end, tier-1 in-memory pass is intentionally retained, no second live engine, no double-record, `current_best` is derived-on-read and never stored, `complete_evaluation` recomputes and rejects empty lineage with a DB backstop. One minor unused-import nit in `server.py` (calls removed, imports left).

---

## 3. Security final check (§6)

- **Descriptor secret handling** — `backend/app/execution/implementation_registry.py:439-458` `_sanitize_auth`: an inline secret-ish string value → `{"redacted": True}` (`implementation_registry.py:449`), nested dict secret values likewise (`implementation_registry.py:452-453`); `_ref`-suffixed keys and `_REF_OK_KEYS` (`credential_ref`, `secret_ref`, `vault_path`, `env`, …) pass through as references. `descriptor()` (`implementation_registry.py:461-497`) sets `"auth_requirements": _sanitize_auth(row.get("auth_requirements"))` and the writer-side contract forbids literal secrets in `auth_requirements` (`implementation_registry.py:146`). No raw secret material is emitted. Accepted residual: a secret placed under a *non*-secret-named top-level key would pass verbatim (`implementation_registry.py:457`) — mitigated by the writer contract, not the projection.
- **Cross-user run mutation** — `backend/app/execution/durable_resume.py:117-123` `authorize_run_mutation` raises `NotYourRun` **iff both** `actor_id` and `run_created_by` are known and differ (`durable_resume.py:121`); called before resume (`durable_resume.py:303`) and before retry_node (`durable_resume.py:330`). REST maps `NotYourRun` → `HTTPException(status_code=403, …)` (`backend/app/api/runs.py:68-69,89-90`); MCP maps → `"REFUSED: not your run -- …"` (`backend/app/mcp_server/server.py:2865-2866,2894-2895`). Reads are open — explicitly documented as accepted posture (`durable_resume.py:39-43`, `runs.py:17`).
- **Private Problems/Benchmarks/Evaluations** — read paths thread `scope_predicates()` (see §2). `problems` table has `owner_id`, `visibility` (`CHECK IN ('public','private','unlisted')`), `scope_type`, `scope_entity_id` (`backend/db/35_product_model.sql:40-74`). Solutions inherit the Problem's visibility (`product_model.py:272`).
- **§28 ChatGPT branch reconstruction** — `backend/app/local_agent/chat_history_import.py:168-206` `_chatgpt_active_node_ids`: `None` = linear (caller keeps legacy order, `chat_history_import.py:175-176`); `[]` = branching but ambiguous ancestry → discussion-only (`chat_history_import.py:178,206`); non-empty = active root→`current_node` branch, root first (`chat_history_import.py:188-189,199`). Only the active branch is read; abandoned siblings contribute no evidence. Same logic mirrored in `historical_bootstrap.py`.
- **§29 skill ingestion** — untrusted doc wrapped in `<untrusted_source>` fence (`backend/app/services/skill_ingestion.py:290,464-465`). `_validate_capability_statement` (`skill_ingestion.py:504-537`): must be `str`, single line (no `\n`/`\r`, `skill_ingestion.py:516`), length in `[12,400]`, no control chars; rejects `_TRUST_ASSERTION_RE` (`skill_ingestion.py:522`) and `_META_DIRECTIVE_RE` (`skill_ingestion.py:525`); requires ≥2 grounded stem overlaps with the parsed doc (`skill_ingestion.py:535`); returns `None` on any failure, never a repaired string. `_abstract_capability` returns `None` on no-client / API error / `ABSTAIN` / not-exactly-one-`CAPABILITY:`-line (`skill_ingestion.py:589,595`). Fail-closed: injection signals → provenance `system_pending_review`, model never invoked, `capability_statement` stays `NULL` (`skill_ingestion.py:411-412,714-717`).
- **`capability_statement` blast radius** — `git grep -n capability_statement -- backend/app`: the only *decision* consumers are `backend/app/execution/replay.py:299,324-325` (re-extraction drift check) and `backend/app/services/semantic_projections.py:127-128` (embedding text). Other hits are: the *separate* local-agent abstraction `_abstract_capability_statement_local` in `local_agent/local_learning.py`, procedure-extraction *writers* (`procedure_extraction/*`, `skill_ingestion.py` UPDATE), a SELECT column list in `services/procedures.py:229`, and the redaction field list in `observability.py:59`. **Not** referenced by `applicability.py`, `capabilities.py`, `verification_state`, `approval.py`, scope, or `execution/` decision logic. Invariant holds.
- **MCP `apply_change_set`** — registered with a bare `@server.tool()` (`server.py:555-556`), NOT behind a runtime flag, and it IS in the shipped packaging tool snapshot (`packaging/tests/test_server_offline.py:28`). `packaging/README.md:253` explicitly documents it as "an ungated write". This is **unchanged from the `a5dace6` baseline** — not a regression introduced by this branch — but it does contradict the CLAUDE.md/`commLLM.md` "behind an opt-in flag, does not ship public" statement. Flagged as a pre-existing posture discrepancy for the founder, not a merge blocker for this branch.
- **Do-not-weaken invariants** — spot-checked intact: candidate≠verified (`complete_evaluation` recomputes `verified` from evidence; mig 30 `verified_requires_evidence` trigger untouched), failure≠success (`durable_run` classifies node failure and records it; `run_succeeded` requires `graph_result.outcome == "success" AND non-empty diff`, `server.py:1379`), retry≠independent-evidence (`resume_run` never re-runs a `succeeded` node, `durable_run.py:367`; `retry_node` is bounded + explicit, `durable_run.py:395-433`; reproduction evidence is a distinct `evidence_type="reproduction"`), private≠global (scope_predicates on all product-model reads), recommendation≠execution (tier-1 hit returns advice, records `record_plan_execution` only for the actual in-memory run).

**Verdict: PASS**, with one carried note: `apply_change_set` ships in the public tool registry ungated — pre-existing, not a regression, but inconsistent with the documented "opt-in flag" posture. Recommend the founder confirm the intended V1 posture.

---

## 4. Migration / DB check (§7)

- `backend/db/` files present: `01`–`14`, `16`–`37` plus split `08a`/`08b`. **`15` is absent** (14 → 16). Verified via `git ls-tree a5dace6 backend/db/`: the gap **already exists at the baseline** — not introduced by this branch. `16_state_projection_index.sql` is the successor. Note for the record; not a diff concern.
- New migrations `35`, `36`, `37` each carry a header stating the next free number: `35_product_model.sql:4` ("Next free number: 35 (34 is highest)"), `36_durable_execution_runs.sql:4` ("Next free number: 36 (35 is highest)"), `37_execution_runs_terminal_chk_fix.sql:3` ("Next free number: 37 (36 is highest)"). No pre-existing migration file appears in the diff → none rewritten (checksum immutability preserved).
- `git grep -nE 'DROP TABLE|DROP COLUMN|TRUNCATE|DELETE FROM' -- backend/db/35 36 37` → **empty**. The only `DROP` statements are `DROP TRIGGER IF EXISTS …` (re-create idempotency, `35:149,316,326,328,330`; `36:168,178`) and, in `37`, `ALTER TABLE execution_runs DROP CONSTRAINT execution_runs_terminal_chk` guarded by `IF EXISTS` — a one-directional CHECK swap (old bidirectional `(status IN terminal) = (final_execution_id IS NOT NULL)` → `final_execution_id IS NULL OR status IN ('succeeded','failed')`). Full file read; header states "No data migration (execution_runs is new in 36 and has no rows…)". Correct and additive.
- `35` and `36` are additive + idempotent: every table is `CREATE TABLE IF NOT EXISTS`, every index `CREATE INDEX IF NOT EXISTS`, every constraint wrapped in `DO $$ … IF NOT EXISTS (SELECT 1 FROM pg_constraint WHERE conname=…) … $$`, every trigger `DROP TRIGGER IF EXISTS` + `CREATE TRIGGER`. Same discipline as mig 33. `test_migration_upgrade_e2e.py` (new, `+651`) exercises "populate pre-hardening V1 → apply 35-37 → data intact + new tables usable".

**Verdict: PASS** — 35/36/37 are contiguous, correctly headed, additive, idempotent, and non-destructive. The `15` gap is pre-existing at baseline and out of scope for this merge.

---

## 5. Docs accuracy (§8)

- `docs/final-v1.md` (new, 457 lines): has `## SHIPPED IN FINAL V1` (line 32), `## KNOWN V1 QUALITY LIMITATIONS` (line 380), `## POST-V1 EXPERIMENT / MEASUREMENT` (line 431). Structure matches the required SHIPPED / KNOWN LIMITATIONS / POST-V1 shape.
- `ARCHITECTURE.md`: prepended a `> **Historical doc.**` banner + a "Final-V1 update (2026-09-03)" section that explicitly overrides the stale claims — "Execution is no longer 'always a single in-memory pass'", "Evaluation is a first-class product concept", "Problem/Benchmark/Solution/Evaluation are shipped, not 'future concepts'". Points readers to `docs/final-v1.md`.
- `README.md`: added "Final-V1 update (2026-09-03)" section correcting the same items and stating "The V1 product surface is `frontendv1/` … The older `frontend/` … is **not** the V1 surface."
- `commLLM.md`: added a "Final-V1 update" paragraph — product model shipped, durable execution + resume, execution descriptor, §28/§29 ingestion hardening, "V1 product UI is `frontendv1/`".
- `backend/README_MCP_SERVER.md`: updated (`+67/-…`) — not separately quoted here but included in the doc wave.
- `git grep` for stale phrases (`only in-memory`, `evaluation is only a testing`, `…are future/planned`, `frontend/ is the V1`, old tool counts) across `README.md ARCHITECTURE.md docs/final-v1.md backend/README_MCP_SERVER.md commLLM.md` → **no surviving stale claim**.

**Minor doc drift (non-blocking):**
- **Tool count**: `README.md` and `commLLM.md` update sections say "**28 registered tools**". The live registry has **30** (`grep '@server.tool()'` + `packaging/tests/test_server_offline.py::test_all_registered_tools` asserts an explicit 30-name list, which is authoritative and was updated in this branch). Prose undercounts by 2 (looks like `inspect_run` + one product-model tool were missed in the tally). The enforced snapshot test is correct; only the narrative is stale.
- `README.md` / `commLLM.md` retain their older frozen sections ("20 tools registered today", "exposing 20 tools") beneath the new dated update sections. This is the repo's documented frozen-section + dated-update convention, not a contradiction to fix, but a first-time reader sees both "20" and "28" in one file.
- Board note acknowledged (not a bug): `schema.md` is frozen and legitimately lacks the mig 35/36/37 tables.

**Verdict: PASS** — every stale claim called out in the brief has been corrected or explicitly superseded; `docs/final-v1.md` has the required three sections. Only nit: the "28 tools" figure in `README.md`/`commLLM.md` should read 30 to match the enforced registry snapshot.

---

## Blockers

_None._

Recommended (non-blocking) cleanups before or shortly after merge:
1. `git rm --cached frontendv1/build.log frontendv1/build2.log frontendv1/build3.log frontendv1/dev.log frontendv1/tsc.log` — stray build output, now `.gitignore`d but still tracked. (No secrets in them.)
2. Drop the now-unused imports of `execute_task_graph` (`backend/app/mcp_server/server.py:1283`) and `record_plan_execution` (`server.py:1284`, `server.py:1631`) — the calls were removed in the durable-path refactor; `NodeResult` / `persist_compiled_plan` on the same lines are still used.
3. Fix the "28 registered tools" figure in `README.md` and `commLLM.md` → 30 (matches `packaging/tests/test_server_offline.py`).
4. Founder call: `apply_change_set` ships ungated in the public MCP registry (pre-existing at baseline; `packaging/README.md:253` documents it) — confirm this is the intended V1 posture vs. the CLAUDE.md "opt-in flag, not public" statement.
5. Optional: de-duplicate `_chatgpt_active_node_ids` / `_mapping_has_branching` (identical in `chat_history_import.py` and `historical_bootstrap.py`).

---

PRE-MERGE AUDIT: CLEAR
