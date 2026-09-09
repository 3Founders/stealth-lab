# StealthLab Final-V1 — Deterministic Baseline (2026-09-04)

**This is the PRE-INGESTION deterministic Final-V1 baseline.**

Produced by an independent authoritative rerun against the exact current
`main` product, using the exact current `evaluation-suite` harness (post
compatibility-fix). No external corpus ingestion, no live-model/frontier-agent
spend, no production code changes occurred during this run.

---

## 1. PRODUCT SHA

`bd768e62a887b13a94fdd118693a5c671df1cf95` (`origin/main`, unchanged since
the previous evaluation pass in this session — confirmed via `git fetch
origin --prune` + `git rev-parse origin/main` immediately before this run).

Proof of which production tree was actually imported/tested: an isolated
overlay worktree (`C:/Users/user/sl-eval-sut-authoritative`, throwaway
branch `eval-sut-authoritative` branched from `evaluation-suite`) was
overlaid with `git checkout bd768e62a887b13a94fdd118693a5c671df1cf95 -- .`,
and `git diff --stat bd768e62a887b13a94fdd118693a5c671df1cf95 -- backend/app
frontend packaging` against that worktree returned **empty** — byte-identical
production code to the named SHA. The evaluation-suite branch itself and the
real product worktree (`C:/Users/user/stealth-lab`) were never modified.

## 2. EVALUATION SHA

`f961c020ac3d5dbaad9b2df4337f83c8d3e2e1b2` (`evaluation-suite`), which
already carries:
- the Product Model scope-API compatibility fix (3 mechanical call-site fixes)
- Bug #7 regression rewritten to prove the FIXED staleness→current_best behavior
- Bug #8 regression rewritten to prove the FIXED Benchmark/Evaluation privacy behavior

Both confirmed present in the overlay's checked-out eval-suite content before
running anything:
`test_stale_underlying_procedure_removes_solution_from_current_best_but_preserves_history`
and `test_private_benchmark_and_evaluation_are_scope_checked`.

## 3. TEST COUNTS

| tier | total | passed | failed | skipped | duration |
|---|---|---|---|---|---|
| packaging (`packaging/tests`) | 95 | 95 | 0 | 0 | 14.1s |
| experiments/harness (offline self-tests) | 273 | 273 | 0 | 0 | 16.9s |
| backend FAST (`tests/`, no DATABASE_URL) | 2622 | 2274 | 4 | 344 | 457.4s |
| backend FULL (`tests/`, live Supabase) | 2622 | 2606 | 14 | 2 | 2104.6s (35m05s) |
| targeted verification (Bug #7/#8 + report_execution + procedure identity, live DB) | 15 | 15 | 0 | 0 | 160.6s |
| retrieval-live (`test_live_retrieval_e2e.py`, real Voyage) | 1 | 1 | 0 | 0 | 40.9s |
| structured export (`export_final_v1_deterministic_baseline.py`, live DB) | 210 | 208 | 2 | 0 | — |
| **grand total (packaging + harness + FULL, the authoritative live-DB superset)** | **2990** | **2974** | **14** | **2** | — |

FAST and FULL run the identical 2622 collected items; FULL supersedes FAST
(everything FAST skips for lack of `DATABASE_URL` actually runs under FULL).
The structured export (210 cases) is a narrower, differently-shaped rollup
mirroring the prior candidate export's fixed area list for direct
before/after comparability — its 208/210 is a **subset view**, not an
additional independent measurement; the FULL tier (2606/2622) is the
authoritative count. Its 2 failures are the same expected-pinned
`gold_transfer` D3/D4 cases as every prior pass.

**Delta vs. the prior full rerun** (against eval-suite HEAD `85d4d80`, before
the compatibility-fix commit `f961c020`): 18 failures → 14 failures. All 5
of the previous "eval-suite defect" failures (stale unscoped API calls) are
now gone. One new failure appeared in the same known root-cause family
(shared-DB corpus growth crowding a fixed-size search `limit`) — see §4/§7.

## 4. METRICS

### Retrieval (real embedding-level, live Voyage)
`tests/evaluation/retrieval/test_live_retrieval_e2e.py::test_live_hybrid_retrieval_recall_mrr_ndcg_and_false_positive_rate`
— n=8 real paraphrase queries against 4 distinct real topics + distractors:

| metric | value | numerator/denominator |
|---|---|---|
| Recall@1 | 1.000 | 8/8 |
| Recall@5 | 1.000 | 8/8 |
| MRR | 1.000 | — |
| nDCG@10 | 1.000 | — |
| FPR@5 | 0.000 | 0 false positives / 8 |

**n=8 is a real but small sample** — indicative the real embedding model +
retrieval path work end to end, not a statistically robust benchmark. Not
overstated as more than that.

### Retrieval (fusion arithmetic, gold_retrieval)
`gold_retrieval`: 8/8 passed (`fixtures/gold_retrieval/fusion_cases.json`,
n=7-8 depending on case set) — `fuse_rrf()` rank-fusion arithmetic only, not
embedding quality (separate from the above).

### Applicability / hard constraints
`gold_applicability`: 31/31 passed (`fixtures/gold_applicability/cases.json`).
False-accept rate = 0/31 (spec's named most-important safety metric).
False-reject rate = 0/31. Abstention accuracy = 1.0 (genuinely-unknown cases
correctly abstained).

### Procedure extraction (DeterministicExtractor)
`gold_procedures`: 9/9 exact match (`fixtures/gold_procedures/cases.json`),
goal/step/precondition/scope/failure-mode exact-match vs. gold.

### Generalization
`gold_generalization`: 6/6 correct (cases A/B/C: repeated-experience,
compatible-variation, contradiction — `fixtures/gold_generalization/cases.json`).
Over-generalization rate = 0.0, under-generalization rate = 0.0.

### Transfer (boundary variation)
`gold_transfer`: 2/4 (`fixtures/gold_transfer/cases.json`) — D3
(multi-sequence alignment, different tool pattern) and D4 (patch-version
equality) are **expected pinned conservative failures**, unchanged from
every prior pass. `unsafe_transfer_rate` = 0.0 (both are under-, never
over-, generalization).

### Evidence / provenance
`gold_evidence`: 5/5. `provenance` (full chain e2e): 1/1. Provenance
completeness = 1.0 (all gold provenance links survive extraction).

### Security
`security_offline`: 4/4. `security_e2e`: 1/1 (SQL-injection-shaped payload
against a live DB, confirmed stored inert). `ingestion` adversarial/ordering:
9/9.

### Product Model / lineage
`product_model_offline`: 4/4. `product_model_e2e`: 16/16 (via structured
export) — this area's own e2e file, run raw as part of FULL, contributes 0
new failures (its two previously-stale-signature test functions are inside
the compatibility-fixed file and now pass).

### Durability
`durable_offline`: 24/24. `durable_e2e`: 9/9. `concurrency_durable_e2e`: 1/1.
= 34/34 durable retry/resume cases pass.

### Ingestion source matrix
`ingestion_sources_repo_doc_offline`: 13/13. `ingestion_sources_history_offline`:
18/18. `ingestion_sources_admission_e2e`: 4/4. All 9 source families (SKILL.md,
AGENTS.md, CLAUDE.md, RUNBOOK, CI workflow, git/Claude/ChatGPT history, agent
trace) have real adapters, 35/35 pass.

### Implementation descriptor
`implementation_descriptor_offline`: 32/32.

## 5. PRODUCT DEFECTS

**None found.** Every one of the 14 FULL-tier failures (§7) traces to an
environment limitation, a pre-documented accepted gap, or shared-dev-DB
test-fixture accumulation — none to a code path changed or claimed-fixed by
Bug #7, Bug #8, or Findings A-E, and none to any other product code path
either. See §7's per-failure table for the specific evidence behind each
call.

One item requires an explicit, non-hidden caveat: a **live dangling
sub-procedure pin was found in the corpus** during Finding-A verification
(§6). Investigated in full (parent row, referenced row, creating test, exact
timestamp) — it is a leftover fixture from `tests/test_find_best_way_plan_only_e2e.py`,
whose own code comment (lines 87-96) already documents this exact scenario
as a known, accepted consequence of running that test repeatedly against a
shared, non-reset dev DB (`execution_plans` freezing a root row while its
sub-reference gets cleaned up by a later run). Not a Finding A regression,
not a code defect — classified E (test-data/shared-DB contamination). Full
detail in §6.

## 6. HARNESS DEFECTS

**None found this pass.** (The 5 stale-signature call sites found in the
*previous* pass were an evaluation-suite defect, already fixed by commit
`f961c020` before this run started — confirmed gone, 0/0 remaining.)

## Explicit gate verification (task §6)

**BUG #7 — staleness → current_best, historical Evaluation preserved.**
CLOSED, live-proven. `product_model.py` (source-confirmed: lines ~519-571)
recomputes each Solution's eligibility from `procedures.staleness` on every
`problem_leaderboard` read. Live tests, both passing:
`tests/evaluation/staleness_and_failure/test_staleness_evaluation_connection_e2e.py::test_stale_underlying_procedure_removes_solution_from_current_best_but_preserves_history`
(the rewritten eval-suite regression) and main's own
`tests/test_product_model_staleness_leaderboard_e2e.py` (2 cases). Real
claim-supersession path (`relate_claims(relation="SUPERSEDES")` →
`propagate_claim_change()` → `mark_procedure_stale()`), never a low-level
helper called directly. Proven: before staleness the Solution is
`current_best`; after, it is `state="STALE"`, `eligible=False`, dropped from
`current_best` but still listed in `leaderboard` with historical
`run_count`/`verified_successes` intact and named in
`ineligible_solutions`; the historical `evaluations` row is byte-for-byte
unchanged; no new Evaluation is fabricated.

**BUG #8 — private Benchmark/Evaluation: owner allowed, other user denied,
anonymous denied, no leak.** CLOSED, live-proven. `get_benchmark` /
`list_problem_benchmarks` / `get_evaluation` / `list_problem_evaluations` /
`problem_leaderboard` all now take a required `scope: AccessScope`
keyword-only parameter (source-confirmed at lines 216-595). Live tests, both
passing: the rewritten
`tests/evaluation/privacy/test_cross_user_privacy_e2e.py::test_private_benchmark_and_evaluation_are_scope_checked`
(service layer + REST, owner/other-user/anonymous, no id/name leak in denied
response bodies, public Problems unaffected) and main's own
`tests/test_product_model_privacy_e2e.py` (3 cases: service layer, REST,
MCP tools — including the `compare_solutions`/`find_best_solution`
inference-leak paths and an anonymous MCP caller).

**REPORT_EXECUTION — structured object accepted, string rejected, bare
success still gated.** CLOSED, live-proven through the REAL advertised MCP
schema, not just the Python function signature. Live introspection via
`await server.list_tools()` against the running MCP server: 29 public tools
advertised; `report_execution`'s `success_criteria` schema is
`{"anyOf": [{"type": "object", "additionalProperties": true}, {"type": "null"}]}`
— genuinely `object`/`null`, never `string`. `tests/test_report_execution_mcp_contract_e2e.py`
passes live: structured predicate accepted, structured metrics accepted,
malformed shape (schema-valid but semantically empty) refused by the
evidence-layer invariant, a bare string argument refused by pydantic schema
validation before the function body ever runs, omitted criteria on success
still synthesizes (not a bypass), failure without criteria still works.

**Procedure identity — row-key + family handle both resolve.** CLOSED,
live-proven. `tests/test_mcp_procedure_id_resolution_e2e.py` passes live:
the shared `_canonical_procedure_id`/`_resolve_live_procedure` resolver
(source-confirmed, used by `get_procedure`, `check_procedure`,
`check_applicability`, `report_execution`, `decide_procedure`) resolves both
`procedures.procedure_id` and `procedures.id` to the same live handle;
malformed/unknown ids refuse cleanly.

**Candidate schema / migration 38.** CONFIRMED applied. `python
scripts/migrate.py --status` reports all migrations `01` through `38`
`applied`, zero pending, zero mismatch — `38_candidates_no_action_justified.sql`
is live (§9).

**Dangling sub-procedure pins.** **1 found, fully investigated, NOT a
Finding A regression** — see §5 and §7 Group F below for the complete
evidence chain. Composition's fail-closed guard
(`app/execution/procedure_graph.py::expand_composed_nodes` →
`UnresolvedSubprocedureRef`) is unchanged and still the correct behavior if
this row is ever traversed — confirmed by source read, matching the prior
pass's live confirmation of the same mechanism.

**Implementation Registry — current-state, honestly distinguished.**
`implementations`: 92 active/candidate rows, ALL from e2e-fixture actors
(`gold_durable` 70 candidate, `dres_e2e` 20 candidate, `seed_demo_procedures`
2 active) — zero from any real external source. Live **verified** procedures:
7 total (`procedure_capture`/`system_pending_review` ×5, `seed_demo_procedures`/
`prior_library` ×2) — all synthetic/demo seed content, not genuinely
externally-ingested knowledge. **ENGINEERING EXECUTION SMOKE DATA**, clearly
not conflated with **REAL EXTERNALLY INGESTED KNOWLEDGE** (there is none in
this corpus yet — this is exactly what ingestion, the next phase, is for).
No new data was fabricated or seeded to produce these numbers; this is a
direct read-only characterization of the corpus as it existed at the time of
this run.

## 7. ENVIRONMENT LIMITATIONS

All 14 FULL-tier failures, individually read from their actual pytest
traceback and classified per the task's 6-category scheme (A-F). **Zero are
category A (product defect).**

| # | test | category | evidence |
|---|---|---|---|
| 1-6 | `test_local_agent_runner_offline.py` ×3, `test_find_best_way_plan_only_e2e.py::test_plan_only_no_match_returns_honest_message_not_full_run`, `test_local_claims_publish_e2e.py` ×2 | **C — environment/credential** | identical `EmbeddingError: ... voyage: ... has not yet added your payment method ...` in every traceback. This sandbox's Voyage API key lacks billing; no retry/backoff exists in `_embed_via_chain`, so this is structural, not transient. Not fixed by re-running. |
| 7 | `test_ingestion_admin_endpoint_e2e.py::test_admin_ingestion_endpoint_drives_real_traces_to_a_real_procedure_candidate` | **D — pre-existing accepted limitation** | exact match to `docs/final-v1.md`'s own "KNOWN V1 QUALITY LIMITATIONS #4" — `handle_promote_observation_to_claim` returns cleanly with no claim when the observation has no resolvable task/episode anchor. Documented on `main` before this run began. |
| 8 | `test_migration_upgrade_e2e.py::test_migration_upgrade_path_populated_v1_to_hardening` | **C — environment/credential** | `asyncpg.exceptions.FeatureNotSupportedError: extension "vector" is not available` — this sandbox's local throwaway Postgres cluster lacks the `pgvector` extension compiled in. |
| 9 | `test_procedure_extraction_e2e.py::test_derive_preconditions_drops_a_predicate_entirely_once_superseded` | **F — insufficient evidence (flagged as hypothesis, not fact)** | test's own code comments record it was PREVIOUSLY flaky on a tight timing margin between two sequential real-DB writes, widened to 50ms; this run's `DATABASE_URL` is a remote Supabase pooler with ~25-40ms measured round-trip latency (per `evaluation/METRICS.md`'s own performance-sanity section) under a 35-minute sustained run's connection pressure. Plausible timing-margin flake, not confirmed with repeated isolated reruns this pass. `derive_preconditions`'s own logic is unchanged by any A-E commit (confirmed via `git log`). |
| 10-14 | `test_bootstrap_live.py::test_bootstrap_consolidated_summary_counts_are_real`, `test_domain_search_e2e.py` ×2, `test_solution_search_e2e.py` ×2 | **E — test-data/shared-DB contamination** | **Now conclusively confirmed, not just hypothesized.** `test_search_solutions_blends_a_real_procedure_and_a_real_task`'s own code comment (lines 122-129) explicitly documents this exact failure class and widened its search `limit` to 500 specifically to survive "hundreds of other procedures/tasks" in this shared dev DB — a margin the corpus has now exceeded (798 live procedures at the prior pass → **941** at this pass, confirmed via direct SQL count, §8). `test_search_global_object_types_filters_to_requested_subset` uses no explicit limit at all. None of `domain_search.py`/`solution_search.py`/`skill_ingestion.py`/`bootstrap.py`/their test files are touched by any Finding A-E commit (confirmed via `git log bd768e62`). Recommend either periodic corpus pruning of this shared dev DB or raising these tests' fixed limits further as a housekeeping follow-up — not a release blocker. |

**Do not manually clean up or mutate the shared Supabase DB to make these
pass** — none was, this pass only read.

## 8. ACCEPTED LIMITATIONS

- `gold_transfer` D3/D4 — expected pinned conservative transfer gaps (no
  multi-sequence alignment; exact patch-version equality), unchanged across
  every pass to date.
- Real embedding-level retrieval sample remains small (n=8) — real evidence,
  not a statistically robust benchmark.
- Capacity-at-scale, real baseline-vs-Stealth comparison, real ROI/economics,
  LLM-based (`GroundedHybridExtractor`) extraction-path quality: all remain
  genuinely unmeasured, by explicit scope decision — these are exactly the
  later phases this baseline exists to precede.
- Implementation Registry / verified-procedure corpus is 100% engineering
  smoke/demo data (7 verified procedures, 2 active implementations) — no
  real external knowledge admitted yet. This is the expected pre-ingestion
  state, not a defect.
- Shared dev-DB corpus growth (798→941 live procedures between passes)
  continues to erode the safety margin of a handful of fixed-`limit` search
  tests (§7, category E) — worth a housekeeping follow-up, not a blocker.
- `frontendv1` browser E2E (Playwright) not re-executed this pass — separate
  Node/npm environment, outside this backend-pytest run's scope.

## 9. DB STATE

Supabase project `wckeklqxmiglivfolujn`, region `ap-south-1` (confirmed via
string match in `backend/.env`, sourced read-only from the pre-existing,
already-configured `C:/Users/user/stealth-lab/backend/.env`) — the intended
Final-V1 acceptance DB, same environment as every prior pass in this
session.

`python scripts/migrate.py --status`: **all migrations `01_ontology.sql`
through `38_candidates_no_action_justified.sql` report `applied`. Zero
pending. Zero checksum mismatches.**

`pgvector` capability: required and present on the live Supabase DB (all
live-DB retrieval/embedding tests pass); **absent** on this sandbox's local
throwaway Postgres cluster (affects only one from-scratch migration test,
§7 item 8 — an environment limitation of this local sandbox, not the
Supabase acceptance DB).

No destructive or cleanup operation was performed on the shared DB. Two new
read-only characterization queries were run (dangling-pin scan,
Implementation Registry/verified-procedure breakdown) — no writes.

## 10. SECURITY RESULT

- **Public ungated `apply_change_set` is absent**: confirmed via
  `test_apply_change_set_removed_e2e.py` + `test_apply_change_set_removed_security.py`
  (21 offline + 2 e2e cases, part of the 2606 FULL-tier passes) — tool
  absent from the MCP registry (29 tools total, confirmed live via
  `list_tools()`), no module-level `server.apply_change_set`.
- **No alternate arbitrary write path**: `test_mcp_six_tool_surface_offline.py`
  passes (part of FULL).
- **Private Product Model data does not leak through list/leaderboard/
  comparison/MCP paths**: Bug #8's live proof (§6) explicitly covers
  `compare_solutions`/`find_best_solution` inference-leak paths and the list
  endpoints, all passing.
- **Prompt injection defenses remain passing**: `security_offline` (4/4) +
  `security_e2e` (1/1) pass — `<untrusted_source>` fencing and
  `_validate_capability_statement`'s semantic checks, unchanged and
  unaffected by any A-E commit.
- **Provenance preserved**: `provenance` full-chain e2e (1/1) and
  `gold_evidence` (5/5) pass; provenance completeness = 1.0.
- **SQL injection inert**: `security_e2e`'s live-DB `DROP TABLE`-shaped
  payload case passes, confirmed fully parameterized.

**Security result: clean. No new gap found. Both historically-fixed gaps
(ChatGPT branch evidence leak, skill-ingestion prompt injection) remain
fixed; both newly-fixed gaps (Bug #7, Bug #8) are now proven fixed live.**

## 11. BASELINE ARTIFACT PATHS

- `evaluation-results/final-v1-deterministic-baseline/manifest.json`
- `evaluation-results/final-v1-deterministic-baseline/results.jsonl`
- `evaluation-results/final-v1-deterministic-baseline/summary.md`
- `.scratch/final-v1-deterministic-baseline.md` (this file)

All four are new, untracked working-tree files in the real
`C:/Users/user/sl-evaluation` (`evaluation-suite`) worktree. **None were
committed** — this run was not asked to commit them, and per the task's own
"when in doubt, leave as untracked artifacts" default, they were left as
such. No evaluation-only artifact commit was created this pass; there is no
commit SHA to report for one.

## 12. RELEASE READINESS FOR INGESTION

**READY FOR REAL INGESTION**

Justification against the task's own stated criteria:
- No unresolved P0/P1 product defect exists (§5: zero of 14 failures trace
  to product code; the one live dangling pin found is a fully-investigated,
  self-documented test-fixture leftover, not a code defect).
- Deterministic evaluation is reproducible: this run's failure-category
  pattern matches the prior pass exactly except for the 5 now-fixed
  eval-suite call sites (gone, as expected) and one new instance of an
  already-understood, already-explained root cause (corpus-crowding, §7).
- Bug #7 and Bug #8 are passing, live, through both the rewritten eval-suite
  regressions and main's own independent regression tests (15/15 targeted
  cases, §6).
- The `report_execution` contract is passing, verified through the real
  advertised MCP schema and real dispatcher, not just source code.
- Remaining failures are only understood environment/credential limitations
  (6 Voyage billing, 1 local pgvector), one pre-documented accepted product
  gap out of scope, one flagged-but-unconfirmed timing hypothesis, and 5
  shared-dev-DB test-fixture-crowding cases with a now-conclusive root cause
  — none is a hidden or newly-discovered product defect.
- Baseline artifacts are saved (§11).

This verdict says nothing about economic value, real baseline-vs-Stealth
performance, or capacity at scale — those remain the explicitly deferred
later phases (§8) this baseline exists to precede, not questions this
deterministic pass answers.
