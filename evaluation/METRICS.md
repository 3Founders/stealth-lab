# Metric definitions

Every metric below is implemented exactly once, in
`backend/tests/evaluation/harness/metrics.py`. This file is the definition; that file is the
implementation. If they ever disagree, the code is a bug — file a regression case.

## Retrieval

| metric | definition | function |
|---|---|---|
| Recall@k | fraction of the gold relevant-id set that appears anywhere in the top-k ranked results | `recall_at_k(ranked_ids, relevant_ids, k)` |
| MRR | reciprocal rank (1/position) of the first relevant result; 0 if none found in the ranking | `mrr(ranked_ids, relevant_ids)` |
| nDCG@k | binary-relevance normalized discounted cumulative gain over the top-k | `ndcg(ranked_ids, relevant_ids, k)` |
| false-positive rate | fraction of top-k results explicitly labeled irrelevant by the gold case | `false_positive_rate(ranked_ids, irrelevant_ids, k)` |

A gold_retrieval case supplies `relevant_ids` (acceptable answers) and `irrelevant_ids`
(explicitly wrong, not just "everything else") separately — see spec §10's "acceptable
alternatives" requirement.

## Applicability

| metric | definition | function |
|---|---|---|
| precision / recall | standard, computed per accept-label against the gold label sequence | `precision_recall(predicted, gold, positive_label)` |
| false-accept rate | of cases whose gold label is a reject state (stale/superseded/conflicting/non-applicable), fraction the system nonetheless marked applicable | `false_accept_rate(predicted, gold, accept_label, reject_labels)` |
| false-reject rate | of cases whose gold label is applicable, fraction the system rejected | `false_reject_rate(predicted, gold, accept_label)` |
| abstention accuracy | of cases whose gold label is genuinely unknown (no evidence either way), fraction the system correctly abstained rather than guessing accept/reject | `abstention_accuracy(predicted, gold, unknown_label)` |

**False-accept rate is the single most important safety number in this whole suite** — it is
literally "how often does Stealth automatically select a procedure that should have been
rejected," which spec §12 names as the most important safety metric in the entire
specification.

## Learning (procedure extraction / admission)

| metric | definition |
|---|---|
| goal accuracy | exact- or near-match (documented per gold case) of extracted goal vs. gold goal |
| step precision / recall | set-based match of extracted step descriptions vs. gold steps — `step_precision_recall(predicted_steps, gold_steps)` |
| ordering accuracy | pairwise (Kendall-tau-style) agreement on the relative order of steps common to both lists — `ordering_accuracy(predicted_steps, gold_steps)` |
| precondition precision / recall | same shape as step precision/recall, applied to extracted preconditions |
| verification correctness | does the extracted step-level verification match the gold verification method |
| failure-mode correctness | does the extracted failure-mode list match gold |
| provenance completeness | fraction of gold provenance links (precondition→claim→evidence→source) still present after extraction |
| candidate→verified rate | fraction of candidates that reach verified status through real (not seeded) maturation, over a gold set of trial sequences |

## Generalization

| metric | definition |
|---|---|
| generalization precision | of episode-groups the system generalized into one procedure, fraction where gold says they should have been merged |
| generalization recall | of episode-groups gold says should merge, fraction the system actually merged |
| over-generalization rate | fraction of gold-contradictory episode-groups (case C) the system incorrectly merged |
| under-generalization rate | fraction of gold-compatible-variation episode-groups (case B) the system failed to merge |
| transfer success | for boundary-variation cases (case D: different repo/package-version/implementation), fraction where the generalized procedure correctly transfers |
| unsafe transfer rate | fraction of boundary-variation cases where the system transferred a procedure that gold says should NOT have transferred |

## Execution / implementation

| metric | definition |
|---|---|
| success rate | fraction of execution-graph gold cases reaching the gold-expected terminal state |
| implementation correctness | fraction of cases where the implementation that ran matches the one durably bound at plan time |
| replay correctness | fraction of replay cases where re-running stays pinned to the originally bound implementation, even after a newer one is registered |

## Safety

| metric | definition |
|---|---|
| privacy failures | count of adversarial cross-user/private-object access cases that succeeded when they should have been rejected (target: 0; any non-zero count is a release blocker per spec §19) |
| security failures | count of adversarial injection/traversal/spoofing cases that succeeded when they should have been rejected (target: 0) |
| unsafe-selection rate | same as applicability false-accept rate, reported here for the safety rollup |
| stale-selection rate | fraction of staleness-chain gold cases where a procedure remained selectable after its backing claim changed |
| false-execution rate | fraction of evidence-classification gold cases where recommendation-only or hypothetical text was classified as executed |
| false-verification rate | fraction of evidence-classification gold cases where execution without independent verification was classified as verified |

## Economics (machinery defined, not executed live this pass)

| metric | definition |
|---|---|
| baseline_cost | full cost of a workload run by a baseline (no-Stealth) agent |
| stealth_cost | full cost of the same workload run with Stealth, including ingestion/embedding/extraction/claim-processing/generalization/retrieval/storage/revalidation/execution overhead |
| gross_savings | baseline_cost − stealth_cost, ignoring one-time memory-building cost |
| net_savings | gross_savings − amortized one-time memory-building cost for the workload |
| ROI | net_savings / stealth_cost |
| break_even_reuses | number of times a procedure must be reused before net_savings crosses zero |

## Performance sanity (measured, not re-run here)

Task spec §21. The hardened Final-V1 candidate (`core-a/ingestion-testing`) already carries a
real performance **sanity** probe — not a capacity/scale benchmark — covering: `product_model`
reads (`get_problem`, `list_problem_solutions`, `get_benchmark`, `problem_leaderboard`,
`get_evaluation`, `complete_evaluation`), durable execution (`start_run`, `execute_run`,
`resume_run`), MCP (`find_problem`, `inspect_problem`, `find_best_solution`), and the claim-graph
overview. Raw data: `.scratch/final-v1-perf-sanity.md` + `.scratch/perf_results.json` on that
branch (`git show origin/core-a/ingestion-testing:.scratch/final-v1-perf-sanity.md`); this section
summarizes it, it does not re-measure it — this evaluation suite has not re-run that probe.

**Verdict from that document: GO.** Every DB path measured has a bounded, data-size-independent
(or explicitly `LIMIT`-capped) query count: `problem_leaderboard` is a fixed 4 reads with no N+1;
durable execution has bounded retries (`max_attempts`, `RETRYABLE_ERROR_CLASSES`) and a
non-spinning drive loop; embeddings cache repeated identical text. The one O(N) fan-out found
(`claim_graph_api.get_claim_graph_overview(with_status=True)`, ~4.5 queries/node) is bounded
(≤600 nodes), concurrency-limited, documented in its own docstring, and has a flat
`with_status=False` fast path — recorded there as a follow-up watch-point, not a blocker.

**Explicit limitations preserved, not paraphrased away:**
- N=20 timed calls per path (embedding: 6 distinct + 5 repeat) — a **sanity** check on real code
  paths for small representative workloads, not production-scale capacity evidence (task spec
  §21's own instruction: "Do not present a 20-run sanity probe as production-scale capacity
  evidence").
- Measured against a **remote Supabase session pooler reached over the public internet** from a
  Windows dev box — every absolute latency number includes real network round-trips (~25-40ms
  each) and is therefore a **ceiling, not a floor**; a co-located app+DB would be far lower. What
  the probe's numbers are evidence for is **shape** (query count, and whether it grows with data
  size), not absolute production latency.
- `find_best_way` tier-2 (the durable-execution-backed HTN coding-agent path) was **not
  measured** — it requires a live sandboxed repo + model, out of scope for a sanity probe.
- `query_count` slightly over-counts (it includes `tenant_transaction` BEGIN/`SET LOCAL`/COMMIT
  and asyncpg's per-acquire connection-reset statement) — a real but small, documented
  measurement artifact, not a correction to any of the numbers above.

Reproduce: `DATABASE_URL=... python .scratch/perf_probe.py` on `core-a/ingestion-testing` (not
this branch — the probe script lives on the product branch, alongside the code it measures).

## Result schema (reconciled superset)

`backend/tests/evaluation/harness/results.py`'s `EvalResult` and `experiments/harness/`'s
per-arm result dict overlap on: `task_id`/`scenario_id`, tokens (in/out/total), `llm_calls`
(harness: implicit; this suite: explicit), `tool_calls`, latency, cost, and a success/valid
flag. This suite's `EvalResult` additionally carries `run_id`, `retries`, `files_touched`,
`verification_result`, and an open `metrics` dict for area-specific numbers that don't fit the
generic schema (e.g. `recall_at_5`, `precondition_precision`) — see spec §2's full field list,
which `EvalResult` implements directly.
