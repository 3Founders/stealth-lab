# StealthLab Final-V1 Candidate — Evaluation Scorecard

**System under test:** `core-a/ingestion-testing` @ `4208b87cbf8233b2ff1b912f67b625217aa4e42c`
(not yet merged to `main`, no frozen tag exists yet — see Freeze Handoff below).
**Evaluation harness:** `evaluation-suite` @ `44c066d73cbc015eee3d454a7895e1198dcdc0c8`
(export generated at commit `cada4d2`, one commit later).
**Historical pre-hardening baseline:** `a5dace6`, tag `v1-baseline-2026-09-02` — **unchanged, not
overwritten**; see `evaluation-results/v1-baseline/` and `evaluation-results/final-scorecard.md`
for that record as it was.

Machine-readable source: `evaluation-results/final-v1-candidate/{manifest.json,results.jsonl,
summary.md}` (run id `2a0f03ff3a31`, generated 2026-09-03, **210 cases: 208 passed / 2
expected-pinned failures**, 0 skipped — DATABASE_URL was live for the whole run against Supabase
project `wckeklqxmiglivfolujn`). This document is the human synthesis of that data plus every
qualitative finding surfaced while building it — it does not introduce a number the JSONL doesn't
back.

**Read this scorecard as two separate claims, not one:**

> **TEST SYSTEM COMPLETE** — yes, for the scope defined in the upgrade task and summarized in
> §29 below.
> **PRODUCT EMPIRICALLY VALIDATED** — no, not as a single verdict. Correctness, security-fix
> verification, and durable-execution reliability are now strongly evidenced on real code against
> a real database. But this pass discovered **two new, real, previously-undocumented defects**
> in the hardened candidate (a benchmark/evaluation privacy gap, and a complete absence of
> staleness-awareness in the product-model ranking layer), and economic value / baseline-vs-Stealth
> comparison remain **entirely unmeasured** — by explicit scope decision, not omission. "Is this
> candidate worth shipping" cannot be answered from this scorecard alone; "does it do what it
> claims, safely, on what was measured" can be answered honestly, and the honest answer has two
> real asterisks on it.

---

## CORRECTNESS

| metric | definition | n | result | target | notes |
|---|---|---|---|---|---|
| Problem/Benchmark/Solution/Evaluation lineage | a completed Evaluation requires real execution+evidence lineage; caller-supplied success_rate/verified_success_rate cannot override recomputed values | 16 e2e cases | **pass** | pass | `test_product_model_e2e.py`-pattern proof via real `product_model.py` calls, live Postgres |
| Comparability/ranking correctness | same-version comparable, different-version/material-env-diff not comparable, incomplete not comparable, Wilson LB, small-n never BEST_VERIFIED, ties, conditional leaders, no stored winner | 10 comparability gold cases + e2e ranking cases | **pass** | pass | `product_model_offline`/`product_model_e2e` |
| find_best_way / product-model convergence | NL goal → Problem → evidence-derived best verified Solution, distinct from the HTN tool of the same name | covered in `product_model_e2e` (16) | **pass** | pass | no-Problem / no-verified-Solution / tie / stale-labeled cases all covered |
| Durable retry/resume semantics | completed node never rerun, bounded retries, crash-mid-node survives+resumes, implementation pinned, stale worker fenced, concurrent resume refused, duplicate resume/evidence prevented, non-retryable/unknown failure classes handled | 24 offline + 9 e2e | **33/33 pass** | pass | real entrypoint chain: `find_best_way` tier-2 equivalent → `run_graph_durably` → node execution → verification/evidence, not an internal helper |
| Retry/resume REST + MCP | inspect run/nodes, resume, retry node; one shared service; cross-user refusal; terminal idempotency; retry policy can't be bypassed; succeeded node can't be retried | 1 comprehensive e2e case | **pass** | pass | proven via real FastAPI router + real MCP server function, both hitting `durable_resume.py` |
| Implementation descriptor | deterministic serialization, stable field order, exact id/version identity, optional-field defaults, protocol derivation, secret redaction, credential_ref preservation, plan-pinning survives a newer registration | 32 offline cases | **32/32 pass** | pass | one real, documented (not fixed) asymmetry: nested `*_ref` keys are over-redacted by `_sanitize_auth`'s one-level recursion — conservative, not a leak |
| Procedure extraction (DeterministicExtractor path) | goal/step/precondition/scope/failure-mode exact match vs. gold | 9 | **9/9 exact** | high | unchanged from historical baseline — hardening touched zero files this depends on |
| Generalization (cases A/B/C) | repeated-experience / compatible-variation / contradiction | 6 | **6/6 correct** | high | unchanged from historical baseline |
| Transfer (case D, boundary variation) | cross-repo/package-version/implementation transfer | 4 | **2/4** (2 expected pinned failures) | n/a | **same two pre-existing conservative gaps as the historical baseline** (no multi-sequence alignment; exact patch-version equality) — not fixed this pass, per explicit task instruction not to "fix" them to improve numbers |
| Staleness → selection (pre-existing chain) | claim change removes a procedure from `find_applicable_procedures()` output | 1 | **pass** | pass | pre-existing test, re-confirmed against the hardened candidate |
| **Staleness → Evaluation/leaderboard (NEW connection this pass)** | when the claim gating a Solution's procedure is superseded and the procedure genuinely goes stale, does the Problem leaderboard / Evaluation still report it as `BEST_VERIFIED`/`current_best`? | 1 e2e case, real chain: claim supersession → real `staleness` column flip (same trigger the pre-existing chain proves) → leaderboard re-read | **CONFIRMED GAP: status is unchanged after staleness** | should not remain current | **new finding, not fixed (house rules)** — see Bug #7 below |
| Ingestion source admission (9 source families) | source → adapter/parser → normalized representation → candidate/evidence/procedure; valid/malformed/duplicate/empty/adversarial/ambiguous/repeated cases | 13+18+4 = 35 | **35/35 pass** | pass | all 9 families (SKILL.md, AGENTS.md, CLAUDE.md, RUNBOOK, CI workflow, git history, Claude history, ChatGPT history, agent trace) have real adapters — none needed inventing |
| Ingestion ordering/adversarial (pre-existing) | oversized/injection-shaped/adversarial-Unicode handled safely | 9 | **9/9 pass** | pass | unchanged |
| Concurrency safety invariants | no lost update / no trust inflation / no corruption under real concurrent load, including the NEW durable-execution layer | 5 (pre-existing) + 1 (new, durable-layer) | **6/6 pass** | pass | new case: concurrent duplicate job submission against one plan, mid-crash, no torn/leaked state |
| Failure-learning cross-checks | retry ≠ independent success, failure can't increase capability, repeated retry doesn't inflate evidence, failure stays inspectable, classification survives resume | 3 (durable layer, cross-referenced) + 2 (new, capabilities.py layer) | **pass** | pass | new coverage proves a recorded failure never raises a Wilson-LB capability estimate, and stays a real queryable row |
| Privacy: Problem isolation (pre-existing + re-confirmed) | private Problem invisible to another user via get/list/find | 2 | **pass** | pass | |
| **Privacy: Benchmark/Evaluation isolation (NEW this pass)** | private Problem's Benchmark/Evaluation reads gated the same way | 1 e2e case (multi-assertion: service layer + real REST) | **CONFIRMED GAP: not gated at all** | should be gated | **new finding, not fixed (house rules)** — see Bug #8 below |
| Publish → independent execution | B discovers only what A published; B's execution/evidence is B's own, never folded into A's | 1 e2e case | **pass** | pass | `run_count` and linked-execution-id sets verified disjoint and correctly attributed |

## QUALITY

| metric | definition | n | result | limitations |
|---|---|---|---|---|
| Recall@k / MRR / nDCG (rank-fusion arithmetic only) | `fuse_rrf()` against hand-picked hit lists | 7–8 | **1.000 / 0.929 / 0.947** | unchanged from historical baseline — fusion math untouched by hardening |
| **Recall@1 / Recall@5 / MRR / nDCG@10 / FPR@5 (real embedding-level, NEW this pass)** | real query → real Voyage embedding → real pgvector + lexical search → fusion → ranked results, over 4 genuinely distinct real topics + 3 distractors, own-created content | 8 real paraphrase queries | **Recall@1=1.000, Recall@5=1.000, MRR=1.000, nDCG@10=1.000, FPR@5=0.025** | **n=8 is a real but small sample** — indicative that the real embedding model + real retrieval path work end to end, not a statistically robust benchmark. `HybridRetriever` retrieves over `task_nodes`/`knowledge_nodes`; `procedures`' own separate vector-search path (`applicability.py`) was not the target of this measurement |
| Step/precondition precision/recall | extracted vs. gold | 8/9 | **1.000 / 1.000** | unchanged |
| Provenance completeness | fraction of gold provenance links surviving extraction | 8/9 | **1.000** | unchanged |

## SAFETY

| metric | definition | n | result |
|---|---|---|---|
| Privacy failures — Problem | adversarial cross-user Problem access that succeeded when it should fail | re-confirmed | **0** |
| **Privacy failures — Benchmark/Evaluation (NEW)** | same, for Benchmark/Evaluation reads | 1 confirmed case | **1 CONFIRMED real gap — see Bug #8** |
| Security failures (SQL injection) | adversarial SQL-shaped content executed instead of stored inert | 2 live-DB cases | **0**, unchanged |
| Security failures (ChatGPT-branch false verification) | Bug #1 from the historical baseline | 5 required regression cases (2 updated, 3 new: mixed conversation, ambiguous ancestry, linear-unchanged) | **0 — RESOLVED, proven fixed against real code** (was 1 confirmed gap in the historical baseline) |
| Security failures (ingestion prompt injection) | Bug #2 from the historical baseline | regression suite updated in place | **0 — RESOLVED, proven fixed against real code** (was 1 confirmed gap) |
| Unsafe-selection rate | applicability false-accept rate | 31 | **0.000**, unchanged |
| Unsafe-transfer rate | boundary-variation transfer that should refuse but didn't | 4 | **0.000** — both remaining failures are under-, not over-, generalization |
| Trust inflation under concurrency / durable retry | duplicate/resubmitted evidence counted as independent | re-confirmed (concurrency) + new (durable retry exhaustion, duplicate resume) | **0** |
| Cross-user execution-run mutation | resume/retry another user's run | re-confirmed via REST + MCP | **0** — `NotYourRun`/403/"REFUSED" on both surfaces |
| **Staleness blindness in ranking (NEW)** | does a stale/invalidated Solution ever silently keep BEST_VERIFIED/current_best status | 1 confirmed case | **1 CONFIRMED real gap — see Bug #7** |
| Descriptor secret leakage | no secret material anywhere in a serialized Implementation descriptor | 32 | **0**, confirmed via full-JSON-blob absence check |

**Two release-relevant bugs from the historical baseline are now RESOLVED and proven fixed on
real code. Two NEW, real, previously-undocumented gaps were found by this pass's own new
coverage.** Net: safety posture on everything re-measured improved; safety posture on the two
newly-tested surfaces (Benchmark/Evaluation privacy, staleness-aware ranking) is worse than the
scorecard would have implied had those surfaces not been tested at all. This is the evaluation
system doing its job, not a product regression introduced by this pass — see Known Limitations.

## RELIABILITY

| metric | definition | n | result | notes |
|---|---|---|---|---|
| Durable execution correctness | see CORRECTNESS table's retry/resume rows | 33 | **pass** | this is the direct fix for the historical baseline's Bug #6 ("execution-graph retry/resume was found genuinely absent") |
| Migration upgrade safety | fresh-DB vs. existing-DB upgrade, no checksum drift, no destructive DDL, pre-existing rows byte-identical | 1 (product's own `test_migration_upgrade_e2e.py`, re-confirmed reachable from this branch) | **pass** | proven on a throwaway PG17 cluster per the hardening branch's own acceptance record |
| Concurrency/chaos, modest scale | duplicate jobs, concurrent publication/resume, worker loss, LLM/DB timeout, partial persistence | 6 (5 pre-existing + 1 new durable-layer) | **6/6 pass** | tens-to-low-hundreds of concurrent ops, by explicit scope decision — not capacity-at-scale |
| Session-pooler stability | environmental, not a product defect | observed | Supabase session-mode pooler caps at 15 connections, shared across concurrent worktrees/lanes on this machine — occasional transient exhaustion during heavy parallel test runs this pass, always cleared on retry | documented here, not hidden |

## ECONOMICS

**Machinery verified/extended, not executed — no real number in this section, by explicit task
scope decision.**

- `experiments/harness/economics.py`'s cost model already separates memory-building costs
  (retrieval, storage, revalidation, execution_overhead) from baseline/execution cost. This pass
  added two labeled slots the hardened candidate's new surfaces needed and previously had nowhere
  to go: `durable_retry_overhead` (per-node retry/resume bookkeeping) and
  `product_model_read_overhead` (the extra Problem/Benchmark/Solution/Evaluation reads
  `find_best_way`'s product-model path now does) — both default `0.0`, both additive to `.total`,
  no calculation logic changed, no number computed.
- `experiments/harness/run_harness.py`'s scripted-arm episode schema was missing 4 of the 10
  fields a baseline-vs-Stealth comparison needs (`model_calls`, `cost_usd`,
  `verification_result`, `first_pass_success`) — added at honest zero/None defaults; a new
  structural test asserts the full 10-field set exists.
- `ablation_config.py`'s existing arm ladder (A → C[+retrieval+applicability+procedure_reuse] →
  E[+decomposition] → F[+implementation_selection] → G[full stack]) already matches the task's
  own named contribution-layer order — verified, no gap, no change needed.
- **No live-LLM run, no real spend, no ablation sweep, no real workload was executed this pass.**
  Any `baseline_cost`/`stealth_cost`/`net_savings`/`ROI`/`break_even_reuses` number would be
  synthetic; none is claimed here.

## PERFORMANCE

Integrated from the hardened candidate's own already-measured sanity probe
(`.scratch/final-v1-perf-sanity.md` on `core-a/ingestion-testing`, not re-measured this pass —
see `evaluation/METRICS.md`'s new Performance Sanity section for the full table).

- **Verdict: GO.** Every DB path has a bounded, data-size-independent (or explicitly `LIMIT`-capped)
  query count: `problem_leaderboard` is a fixed 4 reads with no N+1; durable execution has bounded
  retries and a non-spinning drive loop; embeddings cache repeats (same-text re-embed is
  effectively free).
- One documented, non-blocking watch-point: `get_claim_graph_overview(with_status=True)` does an
  O(N)-per-node lifecycle fan-out (~4.5 queries/node), bounded to ≤600 nodes, concurrency-capped,
  with a flat `with_status=False` fast path already available — a follow-up for the lead if claim
  counts grow into the hundreds, not a blocker.
- **This is a sanity check, explicitly not a capacity/scale benchmark.** N=20 timed calls per
  path over a remote session-pooler connection (network round-trips inflate every absolute
  number; the *shape* — query count independent of data size — is what was actually being
  checked). `find_best_way` tier-2 itself was not measured (requires a live coding-agent sandbox).
  Do not present these numbers as production-scale capacity evidence.

## OPERATIONAL READINESS

- **Test tiers cleanly separated and documented** (`evaluation/README.md`'s new §26 section):
  FAST (`pytest tests/`), FULL (`DATABASE_URL=... pytest tests/`), SECURITY
  (`pytest tests/evaluation/security`), E2E (full `tests/evaluation` with DATABASE_URL), DURABLE
  (`pytest tests/evaluation/durable`), RETRIEVAL (`pytest tests/evaluation/retrieval/test_live_retrieval_e2e.py`),
  LOAD (`pytest tests/evaluation/concurrency`), EXPERIMENT and ECONOMICS explicitly documented as
  never-automatic, real-cost, explicit-invocation-only.
- **Machine-readable results with a disambiguated manifest**:
  `evaluation-results/final-v1-candidate/{manifest.json,results.jsonl,summary.md}` now separately
  name `system_under_test_commit` (`4208b87`), `evaluation_harness_commit` (`44c066d`),
  `historical_baseline_commit` (`a5dace6`), and a nullable `final_v1_tag` — the historical
  baseline's single ambiguous `commit` field is fixed for all future exports, without altering
  the historical `v1-baseline/manifest.json` itself.
- **Regression discipline maintained across a real fix**: the two historical-baseline bugs that
  got fixed (Bugs #1, #2) had their pinned gold cases updated IN PLACE to prove the fixed
  behavior — not deleted, not weakened — so a future regression on either shows up as a deliberate
  diff, exactly like the still-open pinned gaps (gold_transfer D3/D4).
- **Reproducibility**: `python backend/scripts/export_final_v1_candidate_baseline.py` regenerates
  this export against whatever the working tree currently contains; re-running it against the
  exact `4208b87` state (this branch's current HEAD lineage) should reproduce 208/210 exactly.
- **Documentation**: `evaluation/{README,ARCHITECTURE,METRICS}.md` all updated this pass —
  doc/code path drift fixed (§1), the historical Known Limitations section annotated with
  forward-pointing "RESOLVED on the hardened candidate" notes (not rewritten), and the new
  Performance Sanity section added.

---

## Bugs discovered

### 1. ChatGPT edited/regenerated-branch evidence leak — **RESOLVED**

Was CONFIRMED, moderate-high severity, in the historical baseline. **Fixed on this candidate**
(commit `209564a`): `parse_chatgpt_export` now walks the real `current_node`/`parent`/`children`
tree; an abandoned sibling (fabricated tool result, or a hedge) is excluded from the active
branch entirely instead of being linearized in by `create_time`. Proven via 5 required
regression cases in `backend/tests/evaluation/fixtures/gold_evidence/cases.json` /
`test_gold_evidence_offline.py`: the two original findings flipped to fixed-behavior, plus 3
new required cases (mixed conversation preserves per-step state, ambiguous ancestry resolves
conservatively to zero candidates, ordinary linear conversation output is byte-identical
with/without tree metadata).

### 2. `skill_ingestion._abstract_capability` prompt-injection surface — **RESOLVED**

Was CONFIRMED, moderate severity, bounded impact. **Fixed on this candidate** (commit
`47f4ffd`): untrusted content is now wrapped in an `<untrusted_source>` fence (defense in depth),
and `_validate_capability_statement` adds real semantic checks — reject on a
trust/verification/execution-authority assertion, reject on a meta-directive, and reject a
candidate not grounded in the parsed document's own content (fewer than 2 shared content stems).
Proven directly: the exact "grants full administrative access" payload this bug was originally
reported against now computes zero stem overlap with the real skill's content and is rejected.

### 3 & 4. `synthesis.py` boundary-variation (transfer) gaps — **UNCHANGED, still open, by design**

Same two conservative (under-, not over-, generalization) gaps as the historical baseline: no
multi-sequence alignment for a materially different tool-call pattern achieving the same goal;
no semver-range notion, so a harmless patch bump is treated identically to a breaking change.
Not fixed this pass, per explicit instruction not to alter the synthesis algorithm to improve
benchmark numbers. `unsafe_transfer_rate` stays 0.0.

### 5. `metrics.step_precision_recall` empty/empty scoring bug — **fixed in a prior pass (evaluation infrastructure, not product)**

Unchanged from the historical baseline record — this was this suite's own bug, already fixed
before this pass began.

### 6. Execution-graph retry/resume — **RESOLVED**

Was a documented production gap (absent, not just untested) in the historical baseline.
**Implemented on this candidate**: durable per-node execution state
(`execution_run_nodes`/`execution_runs`, migration 36) sits beside the pre-existing in-memory
`graph_executor.py`, wired into the `find_best_way` tier-2 execution path and
`reproduce_procedure` via `app/execution/durable_graph.py::run_graph_durably`. See the
CORRECTNESS table's durable retry/resume row (33/33 real cases) for the full proof — completed
nodes never rerun, bounded/policy-driven retries, crash-mid-node survives and resumes, a stale
worker cannot mutate a terminal node, concurrent resume is refused not duplicated, implementation
binding is pinned across resume.

### 7. Product-model layer has zero staleness awareness — **NEW, CONFIRMED, not fixed (house rules)**

**Root cause:** `product_model.py` contains no reference to procedure/claim staleness anywhere
(`grep -in stale` on that file returns nothing). `problem_leaderboard`, `complete_evaluation`,
and `current_best` compute purely from `evaluations`/`evaluation_executions` rows; nothing in
that computation joins against or checks `procedures.staleness`.

**Impact:** a Solution's underlying procedure can be flipped to `stale` by a real claim
supersession — the exact same trigger the pre-existing
`test_staleness_selection_e2e.py` proves correctly excludes it from
`find_applicable_procedures()`'s output — and the Problem/Benchmark/Evaluation layer will
continue to report that Solution as `BEST_VERIFIED`/`current_best` with no change at all. The
staleness mechanism works; the product-model ranking layer simply never asks it the question.

**Reproduction:** `backend/tests/evaluation/staleness_and_failure/test_staleness_evaluation_connection_e2e.py::test_stale_underlying_procedure_does_not_change_evaluation_or_leaderboard_status`
— real claim creation, real gating, real 10/10-success evidence earning `BEST_VERIFIED`, real
claim supersession via `relate_claims()`, real staleness-column flip confirmed, leaderboard
re-read showing no change.

**Not fixed** — per house rules against touching product code this pass. This is exactly the
scenario task spec §14 asked this suite to be able to detect ("An Evaluation based on an
invalidated/stale solution MUST NOT silently remain treated as a current verified leader") — it
detected it, and the answer is that today's product does not yet meet that bar.

### 8. Private Problem's Benchmarks and Evaluations are not scope-checked at all — **NEW, CONFIRMED, not fixed (house rules)**

**Root cause:** `product_model.get_benchmark`, `list_problem_benchmarks`, `get_evaluation`, and
`list_problem_evaluations` take no `scope` parameter whatsoever — unlike the sibling
`list_problem_solutions`, which correctly gates on the parent Problem's visibility first. The
corresponding REST routes (`GET /v1/benchmarks/{id}`, `/v1/evaluations/{id}`,
`/v1/problems/{id}/benchmarks`, `/v1/problems/{id}/evaluations`) all resolve a caller's
`X-Viewer-Id` via the same `Depends(get_scope)` every other route on that router uses — the
resolved scope is simply never passed into the service call.

**Impact:** a private Problem's Benchmark and Evaluation content — including everything the
Evaluation joins in (linked execution ids, recomputed success metrics) — is fully readable by
any other resolved identity, or anonymously if the UUID is known, and **discoverable without even
knowing the UUID** via the two list endpoints, which apply no Problem-visibility gate at all.
Unlike `app/api/runs.py` (which explicitly documents "reads are open" as a deliberate policy for
execution runs), no such stated rationale exists for this asymmetry — it reads as an
inconsistency, not a decision.

**Reproduction:**
`backend/tests/evaluation/privacy/test_cross_user_privacy_e2e.py::test_private_benchmark_and_evaluation_are_not_scope_checked_confirmed_gap`
— proven at both the service layer and against a real FastAPI router: the parent Problem
correctly 404s for another viewer over REST, while its Benchmark/Evaluation content and list
endpoints return 200 with the private content included.

**Not fixed** — per house rules against touching product code this pass. Recommend this be
treated as release-blocking or near-blocking given `PRIVATE_VISIBILITY_ENABLED`/`REAL_AUTH_ENABLED`
are explicitly named V2 gates in this codebase's own `.env` conventions — this gap sits squarely
inside the feature those gates exist to protect.

---

## Known limitations

- **Baseline-vs-Stealth (§18), ablation (§19), and economics execution (§20)**: machinery
  verified/extended this pass, **not executed** — no live-LLM runs, no real spend, by explicit
  scope decision.
- **Capacity-at-scale (§22's boundary)**: concurrency/chaos work proved correctness at
  small/modest scale (tens of concurrent ops, including the new durable-execution layer), not
  throughput at 1k→1M procedures/runs.
- **Real embedding-level retrieval (§17)**: measured with n=8 real paraphrase queries against 4
  genuinely distinct topics — real and honest, but a small sample; not a claim of statistically
  robust embedding-model benchmarking.
- **LLM-based procedure extraction path** (`GroundedHybridExtractor`) remains untested — no live
  LLM budget, unchanged from the historical baseline.
- **Gold sets remain small** (4–35 cases per area) — real evidence on real production code, not
  exhaustive coverage.
- **Two NEW confirmed gaps** (Bugs #7, #8 above) are real, current, and unresolved as of this
  export. They are not regressions introduced by this evaluation pass — they are pre-existing
  behavior this pass's new coverage was specifically built to be able to detect, and did.
- **Session-pooler contention**: this dev environment's Supabase session-mode pooler (15
  connections) is shared across concurrent worktrees/lanes; heavy parallel `pytest` invocations
  against it occasionally see transient exhaustion in `tests/evaluation/concurrency/`, clearing
  on retry. Confirmed environmental, not a product defect.
- **`_sanitize_auth`'s one-level-recursion asymmetry** (Implementation descriptor redaction): a
  nested `*_ref` key is over-redacted even though an identical top-level one is correctly kept —
  conservative (hides a reference), not a secret leak. Not fixed, documented.

## Commands

```bash
cd backend
python -m pytest tests/ -q                                     # FAST
DATABASE_URL=postgresql://... python -m pytest tests/ -q       # FULL
python -m pytest tests/evaluation/security -q                  # SECURITY
DATABASE_URL=postgresql://... python -m pytest tests/evaluation -q   # E2E (complete lifecycle)
DATABASE_URL=postgresql://... python -m pytest tests/evaluation/durable -q            # DURABLE
DATABASE_URL=postgresql://... python -m pytest tests/evaluation/retrieval/test_live_retrieval_e2e.py -q  # RETRIEVAL
DATABASE_URL=postgresql://... python -m pytest tests/evaluation/concurrency -q        # LOAD
python scripts/export_final_v1_candidate_baseline.py            # regenerate this export
```

```
# EXPERIMENT (live baseline-vs-Stealth) and ECONOMICS (real workload ROI) are explicit,
# never-automatic, real-cost invocations -- see evaluation/README.md §26. Not run this pass.
```

## Final verdict

**TEST SYSTEM COMPLETE: yes**, for this pass's scope. The suite now reproducibly measures
correctness, safety, privacy, evidence integrity, retrieval (both fusion-arithmetic and real
embedding-level), applicability, staleness (including its new product-model-layer connection),
failure learning, generalization, the full Problem/Benchmark/Solution/Evaluation lifecycle,
durable retry/resume through real entrypoints, Implementation binding, baseline-vs-Stealth/
ablation/economics machinery (structurally), and modest concurrency/chaos — against a real,
reproducible, disambiguated baseline for the hardened candidate.

**PRODUCT EMPIRICALLY VALIDATED: partially, with two new real caveats.** Two previously-confirmed
release-relevant bugs are now genuinely fixed and proven so. Durable execution — the headline
capability this hardening wave added — is thoroughly and successfully proven correct across 33
real test cases. But this same rigor found two more real gaps that did not exist as tested
findings before this pass: private Benchmark/Evaluation content leaks to any other user, and the
product-model ranking layer has no staleness awareness at all. Neither is a regression this pass
caused; both are real, current defects in the candidate this pass evaluated, surfaced by exactly
the kind of testing this task exists to build. Economic value and baseline-vs-Stealth comparison
remain completely unmeasured. Whether this candidate is ready to become the frozen Final-V1 tag
is a decision for whoever owns that call — this scorecard's job is to make sure that decision is
made with the two new findings in view, not surprised by them later.
