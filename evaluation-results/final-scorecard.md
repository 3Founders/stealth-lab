# StealthLab V1 — Final Evaluation Scorecard

Frozen baseline: commit `a5dace6`, tag `v1-baseline-2026-09-02`. Production code
(`backend/app/`, `frontend/`, `packaging/`) is byte-identical to that tag as of this scorecard —
confirmed via `git diff --stat v1-baseline-2026-09-02 HEAD -- backend/app/ frontend/ packaging/`
returning empty. Everything below is additive test/evaluation infrastructure only.

Machine-readable source: `evaluation-results/v1-baseline/{manifest.json,results.jsonl,summary.md}`
(run id `758d7339eec9`, generated 2026-09-02, 81 cases: 79 passed / 2 expected-pinned failures).
This document is the human synthesis of that data plus every qualitative finding surfaced while
building it — it does not introduce numbers the JSONL doesn't back.

**Read this scorecard as two separate claims, not one:**

> **TEST SYSTEM COMPLETE** — yes, for the scope this pass covered (see section below).
> **PRODUCT PERFORMANCE STRONG** — partially. Most measured surfaces are strong. Two real,
> confirmed defects exist (one moderate-severity false-verification bug, one moderate-severity
> prompt-injection gap). Economic value and baseline-vs-Stealth comparison are **entirely
> unmeasured** this pass — deferred by explicit user decision, not by omission. "Is Stealth
> worth it" cannot yet be answered quantitatively from this scorecard alone.

---

## CORRECTNESS

| metric | definition | n | result | target | notes |
|---|---|---|---|---|---|
| applicability false-accept rate | fraction of should-reject gold cases automatically selected anyway | 31 | **0.000** | 0.0 | spec's named most-important safety metric; CWA fail-closed confirmed quantitatively |
| applicability false-reject rate | fraction of should-accept gold cases wrongly rejected | 31 | 0.000 | low | |
| applicability abstention accuracy | fraction of genuinely-unknown cases correctly abstained | 31 | 1.000 | high | |
| staleness→selection chain | does a real claim change actually remove a procedure from `find_applicable_procedures()`'s output (not just flip a DB column) | 1 real chained e2e case | **pass** | pass | pre-existing test (`test_staleness_selection_e2e.py`), verified against live DB; audit had missed it, no new file needed |
| provenance chain intact | precondition→claim→evidence→source survives extraction/persistence/retrieval as one connected read, with a negative control | 1 e2e case + negative control | **pass** | pass | live DB; wrong-claim_id negative control correctly disqualifies |
| procedure extraction (DeterministicExtractor path) | goal/step/precondition/scope/failure-mode exact match vs. gold | 8 | **8/8 exact** | high | LLM-based extraction path (GroundedHybridExtractor) untested — no live LLM budget this pass |
| generalization (cases A/B/C) | repeated-experience / compatible-variation / contradiction handled per spec §9 | 6 | **6/6 correct** | high | over-gen rate 0.0, under-gen rate 0.0 |
| transfer (case D, boundary variation) | cross-repo/package-version/implementation transfer | 4 | **2/4** (2 expected pinned failures) | n/a — new territory | see Bugs Discovered #3, #4 below; both are *conservative* failures, not unsafe ones |
| ingestion adversarial/ordering | oversized/injection-shaped/adversarial-Unicode content handled safely; ordering has no dependency to break | 9 | **9/9 pass** | pass | structural robustness (no ordering logic exists to fail), not explicit reorder-handling |
| concurrency safety invariants | no lost update / no trust inflation / no corruption under real concurrent load | 5 | **5/5 pass** | pass | live DB, tens-to-low-hundreds concurrent ops (small-scale per explicit user scope decision) |

## QUALITY

| metric | definition | n | result |
|---|---|---|---|
| Recall@k (fusion) | real `fuse_rrf()` against exact/paraphrase/short/long/ambiguous/unseen-terminology/cross-repo cases | 7-8 | **1.000** |
| MRR (fusion) | | 7-8 | **0.929** |
| nDCG (fusion) | | 7-8 | **0.947** |
| step precision/recall | extracted steps vs. gold | 8 | **1.000 / 1.000** |
| precondition precision/recall | extracted preconditions vs. gold | 8 | **1.000 / 1.000** |
| provenance completeness | fraction of gold provenance links surviving extraction | 8 | **1.000** |

**Caveat that applies to every number above:** gold sets are small (7–31 cases per area) —
statistically indicative of real behavior on real production code, not exhaustive coverage.
`retrieval.py`'s `HybridRetriever._vector_search`/`_lexical_search` (real embedding-level
paraphrase quality) has no offline path — raw SQL/pgvector against a live Postgres, no
pure-Python equivalent — so the Recall/MRR/nDCG numbers above measure `fuse_rrf()`'s rank-fusion
arithmetic only, not real embedding-model paraphrase quality end to end. A DATABASE_URL-gated
e2e companion for that is a documented gap, not built this pass.

## SAFETY

| metric | definition | n | result |
|---|---|---|---|
| privacy failures | adversarial cross-user/private-object access that succeeded when it should have failed | pre-existing suite (IDOR, cross-user isolation, sandbox traversal, actor-ID spoofing) | **0** (pre-existing, confirmed still passing) |
| security failures (SQL injection) | adversarial SQL-shaped content executed instead of stored inert | 2 live-DB cases (ingest_traces boundary + claims.capture_claim) | **0** — confirmed fully parameterized both places, proven against a live DB with a `DROP TABLE`-shaped payload |
| security failures (prompt injection) | adversarial instruction-shaped content influencing an LLM-backed decision | skill_ingestion path | **1 confirmed real gap** — see Bugs Discovered #1 |
| false-execution / false-verification rate | recommendation-only or unverified text classified as executed/verified | chat_history_import (existing P0-3 suite) + new ChatGPT branch adversarial cases | **1 confirmed real gap** — see Bugs Discovered #2 |
| unsafe-selection rate | = applicability false-accept rate | 31 | **0.000** |
| unsafe-transfer rate | boundary-variation transfer that should have been refused but wasn't | 4 | **0.000** — both Case D failures are under- not over-generalization |
| trust inflation under concurrency | duplicate/resubmitted evidence counted as independent | 1 dedicated race case (25 concurrent identical `context_key` submissions) | **0** — `distinct_contexts` stayed 1, not 25 |

**Privacy failures and unsafe-selection rate are both zero on everything measured** — this is
the strongest part of the scorecard. The two confirmed safety-adjacent bugs (false-verification,
prompt injection) are both bounded in blast radius (see details below), not release-blocking in
the "arbitrary code execution" sense, but both are real and both matter.

## ECONOMIC VALUE

**Not executed this pass — machinery only, by explicit user scope decision.**

`experiments/harness/economics.py` (Phase 6) implements the full cost model and ROI/break-even
calculator matching `evaluation/METRICS.md`'s definitions exactly: `evaluate_workload()`,
`cumulative_curve()`, and a worked `demo_workload_shapes()` example covering all four spec-named
workload shapes (one-off, repetitive, mixed, long-lived/changing-environment) against
**synthetic, illustrative numbers** — explicitly not measured from a real run. The one-off case
honestly reports a negative return (build cost exceeds savings for a single use); the repetitive
case shows real ROI and sub-1-reuse break-even; nothing here is fudged to look favorable.

LLM-call cost tracking already exists for real (pre-existing, MEASURE lane):
`experiments/harness/openrouter_arms.py`'s `SpendLog` records real per-call OpenRouter cost.
What does **not** exist anywhere in this codebase yet: tracked cost for ingestion, embedding,
extraction, claim-processing, generalization, storage, or revalidation — `economics.py` takes
all of these as explicit caller-supplied inputs rather than inventing numbers.

**No `baseline_cost`, `stealth_cost`, `net_savings`, `ROI`, or `break_even_reuses` number in this
scorecard is real.** Running the calculator against a real workload, and running
`experiments/harness/`'s three-arm (A/B/C) live comparison to get real baseline-vs-Stealth
numbers, is explicit follow-up work requiring an API budget decision — see Known Limitations.

## OPERATIONAL READINESS

- **Test tiers cleanly separated**, per spec §32: `backend/tests/evaluation/*_offline.py` (no
  DB/network/LLM, always runs), `*_e2e.py` (live DB, skip-gated), `experiments/harness/` runs
  (live-model, always a deliberate separate invocation, never part of `pytest tests/`).
- **Machine-readable results**: `evaluation-results/v1-baseline/{manifest.json,results.jsonl}`,
  reproducible via `backend/scripts/export_evaluation_baseline.py`.
- **Regression discipline**: every confirmed bug below is pinned as a test characterizing real
  current behavior (gold_transfer D3/D4, the two gold_evidence branch cases) — a future change
  that fixes or worsens any of them shows up as a deliberate diff, not a silent shift.
- **Documentation**: `evaluation/{README,ARCHITECTURE,METRICS}.md` cover what's tested, how to
  run each tier, how metrics are defined, how to reproduce the baseline, how to add a case, and
  the Chaitanya-convergence bridge.

---

## Bugs discovered

### 1. ChatGPT edited/regenerated-branch evidence leak (CONFIRMED, moderate-high severity)

**Root cause:** `app/local_agent/chat_history_import.py` has a documented scope limit — it does
not walk ChatGPT export `parent_id`/`children` branch structure, and linearizes all nodes by
`create_time` instead.

**Impact:** an abandoned/regenerated sibling branch containing a fabricated tool result (e.g.
"check_endpoint() → 200 OK, all tests passed") gets sorted alongside the real, kept branch. This
drives `extract_candidates_from_conversation()` to emit a candidate with
`evidence_level == "verified"` even though the conversation the user actually continued never
executed or verified anything.

**Reproduction:** `backend/tests/evaluation/fixtures/gold_evidence/cases.json`, case
`regenerated-tool-result-leaks-as-verified-evidence`; pinned by
`backend/tests/evaluation/evidence/test_gold_evidence_offline.py`.

**A second, opposite-direction case** in the same file
(`hedged-regenerated-sibling-suppresses-valid-confirmation`) shows a hedged abandoned sibling can
wrongly suppress a genuine confirmed fix — a false negative alongside the false positive.

**Not fixed** — per "no new V1 features / no silent baseline changes" — recorded here and pinned
as a regression case for whoever picks up branch-aware parsing.

> **RESOLVED on the hardened Final-V1 candidate** (`core-a/ingestion-testing` @ `4208b87`, commit
> `209564a`, task spec §28/§9). `parse_chatgpt_export` now walks the real `current_node`/
> `parent`/`children` tree: an explicit `current_node` resolves the active branch and excludes
> abandoned siblings entirely (both the fabricated-tool-result leak and the hedge-suppression
> case are fixed by this), and ambiguous ancestry (no `current_node`, real branching) is handled
> conservatively — zero messages, zero candidates, never a guessed "safe" branch. This scorecard
> describes the **historical, frozen `v1-baseline-2026-09-02`** run and its numbers are
> unchanged — the fix does not retroactively alter what that baseline measured. The gold cases
> that pinned this bug (`backend/tests/evaluation/fixtures/gold_evidence/cases.json`) have been
> updated in place to prove the fixed behavior against the now-merged hardened code, plus the two
> remaining required regression cases (mixed conversation, ambiguous ancestry) — see
> `evaluation-results/final-v1-candidate/final-scorecard.md` for the hardened candidate's own
> measurement once produced.

### 2. `skill_ingestion._abstract_capability` prompt-injection surface (CONFIRMED, moderate severity, bounded impact)

**Root cause:** `app/services/skill_ingestion.py`'s `_abstract_capability` concatenates untrusted
SKILL.md/AGENTS.md/CLAUDE.md/CI-workflow-synthesized/RUNBOOK.md content directly into its LLM
user prompt with no delimiter separating instruction from data. The only safety check on the
model's response — does it echo a concrete backtick/dotted token from the skill's own source
text — is an anti-hallucination check, not an anti-injection check.

**Impact bound:** `capability_statement` only feeds cross-domain retrieval matching
(`retrieve_local_first`-style), not execution or trust decisions — a manipulated statement can
poison what an unrelated future task's retrieval matches against, not execute code or escalate
privilege.

**Reproduction:** `backend/tests/evaluation/security/test_injection_adversarial_offline.py::
test_manipulated_response_without_source_token_echo_is_accepted_as_capability` proves a
manipulated response with no literal source-text echo passes through completely uncaught.

**Not fixed** — recorded and pinned.

> **RESOLVED on the hardened Final-V1 candidate** (`core-a/ingestion-testing` @ `4208b87`, commit
> `47f4ffd`, task spec §29/§10). Untrusted document content is now wrapped in an explicit
> `<untrusted_source>...</untrusted_source>` fence (defense in depth, not the primary fix), and
> `_validate_capability_statement` adds real semantic checks independent of the original
> echo-only defense: reject on a trust/verification/execution-authority assertion, reject on a
> meta-directive aimed at the ingestion system, and reject a candidate not grounded in the parsed
> document's own content (fewer than 2 shared content stems). This last check is what catches the
> exact manipulated payload this bug was reported against — verified directly (zero stem overlap
> between the fabricated capability text and the real skill's content). This scorecard describes
> the **historical, frozen `v1-baseline-2026-09-02`** run and its numbers are unchanged.
> `backend/tests/evaluation/security/test_injection_adversarial_offline.py` has been updated in
> place to prove the fixed behavior against the now-merged hardened code.

### 3 & 4. `synthesis.py` boundary-variation (transfer) gaps (CONFIRMED, low severity — both conservative failures)

- **No multi-sequence alignment**: two episodes achieving the same goal via a materially
  different tool-call pattern are refused rather than merged (similarity 0.17, well under the
  0.6 threshold) — a valid same-method transfer via a different implementation is blocked.
- **No semver-range notion in predicate equality**: a harmless patch-version bump (e.g.
  `python_version` 3.11.2→3.11.6) is refused with exact-string-equality contradiction logic,
  identical treatment to a genuinely breaking version change.

**Both are under-generalization, not over-generalization** — `unsafe_transfer_rate` stayed 0.0
in every case. The system is too conservative here, not unsafe. Pinned as regression baselines
in `backend/tests/evaluation/generalization/test_gold_generalization_offline.py`
(`test_gold_transfer_cases`, cases D3/D4) — if `synthesis.py` becomes less conservative later,
these numbers should improve and the pinned values need a deliberate update.

### 5. `metrics.step_precision_recall` empty/empty scoring bug (evaluation-infrastructure bug, not a product bug — found and fixed in this pass)

Scored an empty-predicted/empty-gold pair as `(0.0, 0.0)` instead of `(1.0, 1.0)`, which would
silently drag down any aggregate with a legitimately-empty case. Fixed directly in
`backend/tests/evaluation/harness/metrics.py`, with a harness self-test pinning the fix — this
one **was** fixed, since it's this pass's own evaluation code, not V1 production code.

### 6. Execution-graph retry/resume — absent, not a bug (documented production gap)

`backend/app/execution/graph_executor.py` has no retry or resume semantics at the code level, not
just untested — confirmed by the pre-work audit. Per "no new V1 features," not implemented this
pass. This is a real capability gap for whoever scopes V1.1, not a regression.

> **RESOLVED on the hardened Final-V1 candidate** (`core-a/ingestion-testing` @ `4208b87`, task
> spec §6/§7). Durable per-node execution state (`execution_run_nodes`/`execution_runs`,
> migration 36) now sits beside the existing in-memory `graph_executor.py` — wired into MCP
> `find_best_way` tier-2 and `reproduce_procedure` via `app/execution/durable_graph.py::
> run_graph_durably`. Retry/resume is a full house of proven invariants (completed nodes never
> re-run, bounded retries with an explicit policy, crash-mid-node survives and resumes,
> implementation binding pinned across resume, a stale worker cannot mutate a terminal node,
> concurrent resume is refused not duplicated) — see this suite's own
> `backend/tests/evaluation/durable/` (added this pass) plus the product's own
> `backend/tests/test_durable_run_e2e.py` / `test_durable_graph_e2e.py` / `test_durable_resume_e2e.py`.

---

## Known limitations

- **Baseline-vs-Stealth (spec §24), ablation (§25), and economics execution (§26)**: machinery
  built (Phases 6–7), **not executed** — no live-LLM runs, no real spend, by explicit user scope
  decision this pass.
- **Capacity-at-scale (spec §30)**: not tested — concurrency/chaos work (Phase 5) proved
  correctness at small/modest scale (tens to low hundreds of concurrent ops), not throughput at
  1k→1M procedures, by explicit user scope decision.
- **LLM-based procedure extraction path** (`GroundedHybridExtractor`, `capability_statement`
  generation): untested this pass — no live LLM budget. All extraction-quality numbers above are
  against `DeterministicExtractor`, the real no-LLM production fallback, not the LLM path.
- **Retrieval embedding-level quality**: `HybridRetriever`'s real vector/lexical search has no
  offline path; Recall/MRR/nDCG numbers measure `fuse_rrf()`'s rank-fusion arithmetic only.
- **Gold sets are small** (7–31 cases per area) — real, but not exhaustive. A regression here
  catches a real class of failure; a green result here is evidence, not proof of absence.
- **Dev DB pooler** in this worktree caps at 15 sessions — running multiple live-DB e2e suites
  back-to-back in one pytest invocation can exhaust it (confirmed environmental, not a product
  defect, by isolating `test_load_e2e.py` and `tests/evaluation/concurrency/` separately).

## Commands

```bash
cd backend
python -m pytest tests/ -q                              # fast, deterministic (no DB/LLM)
python -m pytest tests/evaluation/ -q                    # this suite's offline gold sets
DATABASE_URL=postgresql://... python -m pytest tests/ -q # full, incl. e2e + this suite's e2e areas
python -m pytest tests/evaluation/security/ -q            # security adversarial cases only
DATABASE_URL=postgresql://... python -m pytest tests/evaluation/concurrency/ -q  # concurrency/chaos
python scripts/export_evaluation_baseline.py              # regenerate evaluation-results/v1-baseline/
```

```powershell
# experiments/harness/ -- offline/synthetic by default; live-model runs cost real spend and are
# NOT part of this scorecard:
backend\.venv\Scripts\python.exe -m pytest experiments/harness -q
```

## Final verdict

**TEST SYSTEM COMPLETE: yes**, for this pass's explicitly agreed scope. Every item in the
spec's own §40 acceptance checklist that wasn't explicitly deferred is satisfied: the frozen
baseline evaluates reproducibly, component correctness has gold-labeled quantitative coverage
(not just pass/fail unit tests), the full event→episode→evidence→procedure chain is tested
including historical-evidence semantics, retrieval and applicability both have labeled
benchmarks, staleness is proven through actual selection (not just a status field), candidate
maturation and failure-learning were confirmed already rigorous, multi-episode generalization
including new boundary-variation territory is tested, execution/implementation/composition were
confirmed already correct, security/privacy is adversarially tested including two newly-found
real gaps, cross-user isolation was confirmed already correct, both E2E lifecycles were confirmed
already covered by real-entrypoint tests, results are machine-readable, and documentation exists.
Explicitly and honestly deferred, not silently skipped: live baseline-vs-Stealth comparison,
ablation execution, economics execution, and capacity-at-scale testing — machinery for all four
exists, none were run, by the user's own explicit scope decision going into this pass.

**PRODUCT PERFORMANCE STRONG: partially, and only on what was actually measured.** Every safety
number that was measured came back clean — zero privacy failures, zero unsafe-selection,
zero unsafe-transfer, zero trust-inflation-under-concurrency, zero SQL-injection-executable
paths. That is real, strong evidence on real production code. But two confirmed bugs exist (a
moderate-severity false-verification defect in ChatGPT evidence import, a moderate-severity
prompt-injection gap in skill capability abstraction), and — critically — **the question the
whole spec exists to answer, "does the value created by procedural memory exceed the cost of
creating and maintaining it," is not answered by this scorecard at all.** No real economic
number, no real baseline-vs-Stealth comparison, exists yet. Whether StealthLab V1 is worth
running is still an open, unmeasured question; whether it behaves correctly and safely on
everything measured here is a well-evidenced yes, with two specific, documented, fixable
exceptions.
