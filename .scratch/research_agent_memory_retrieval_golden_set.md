# Evaluating Agent-Memory and Goal Retrieval: Metrics, Golden Sets, and a Defensible Design

**Date:** 2026-09-27  
**Scope:** retrieval-quality evaluation for StealthLab's verified procedural-memory substrate  
**Rule applied throughout:** every citation below was resolved to an arXiv id, DOI, ACL Anthology id, or official proceedings URL. Where a claim or identifier could not be verified, it is marked **[UNVERIFIED — could not resolve]** and is not used as evidence.

**Evidence tiers:** `[PEER-REVIEWED]`, `[PREPRINT]`, `[TECH REPORT]`, `[PRODUCTION WRITEUP]`, `[DATASET CARD]`.

---

## 1. Executive summary

1. **The premise "we have no retrieval measurement" is wrong; the real gap is narrower.** `backend/tests/evaluation/harness/metrics.py:21-141` already implements Recall@k, MRR, binary nDCG, false-positive rate, false-accept rate, false-reject rate, abstention accuracy and ordering accuracy. `backend/tests/evaluation/fixtures/gold_retrieval/fusion_cases.json` is a small labeled fusion set, and `backend/tests/evaluation/retrieval/test_live_retrieval_e2e.py:71-201` measures real embeddings against eight hand-written paraphrases. None of these measures the shipped product ranker, graded relevance, tenant isolation, or a statistically usable query sample.

2. **The primary headline metric should be graded nDCG@10 measured as a delta against the lexical-only leg, with a paired bootstrap 95% CI.** Graded judgments matter because binary relevance cannot distinguish a directly matching Goal from a topically adjacent one; TREC re-assessment found roughly half of documents judged relevant were only marginally relevant (Sormunen, SIGIR 2002, DOI `10.1145/564376.564433`, `[PEER-REVIEWED]`). nDCG@10 spans the judged prefix of the shipped search path, where the procedure judge currently considers 8 candidates and the candidate legs return 20 (`backend/app/services/retrieval_service.py:66-67`).

3. **Recall must be a separate axis, not folded into nDCG.** Sufficiency and completeness are different questions: whether the answer-bearing item appeared at all versus whether it appeared in the right place. On 48 sampled RAG contexts, coverage-aware measures reached Kendall τ 0.68/0.67 against final answer coverage while Recall, MAP and nDCG stayed below 0.6 (CRUX, arXiv:`2506.20051`, `[PREPRINT]`). Report **graded nDCG@10** for ordering and **Recall@20** for candidate generation.

4. **The non-compensatory cascade must be reported as a funnel of counts, not one score.** The MCDA literature defines non-compensatory methods by their refusal to synthesize a utility function; ELECTRE-family methods produce outranking relations or ordered partitions rather than a blend (Figueira, Greco, Roy & Słowiński, *JMCDA* 20(1-2), DOI `10.1002/mcda.1482`, `[PEER-REVIEWED]`). The prior is correct: report candidate count → post-scope → post-dedup → post-cascade → post-rank separately.

5. **The current cold-start gate will distort any evaluation run that crosses it.** `MIN_VERIFIED_PROCEDURES_TO_ENABLE_RETRIEVAL = 1` at `backend/app/services/applicability.py:65` means one qualifying row anywhere in the viewer-visible corpus unlocks retrieval. The product path defaults `require_verified=False` and does not consult the gate, so REST/MCP A/B tests and substrate tests are not measuring the same system. Golden-set runs must record `gate_consulted`, visible verified count, and `require_verified`.

6. **Ingestion-derived queries are acceptable for coverage but unsafe as the headline.** Doc2Query's own paper reports that 69% of generated query words are copied from the source document and that using only the new words produces no significant MRR gain over no expansion (Nogueira & Lin, arXiv:`1904.08375`, `[PREPRINT]`). The failure has no settled single name; adjacent work calls it **source bias** (arXiv:`2310.20501`) and **knowledge leakage** (arXiv:`2504.14175`). Use derived queries to fill buckets, never to set the release number.

7. **An LLM judge should not grade the ranked candidates for the primary metric.** Ranking against human qrels needs no judge. Use a judge only to enlarge the judgment pool or grade free-text answers, and only after it is validated against humans. Position bias changed 66 of 80 pairwise outcomes in one controlled study (FairEval, arXiv:`2305.17926`, `[PREPRINT]`), and self-preference is measurable (arXiv:`2404.13076`, `[PREPRINT]`).

8. **Isolation should be a measured rate, not an assumption, but the literature has no standard.** Authorization-First Retrieval separates **structural leak rate**—unauthorized chunks entering the context—from **answer leak rate**; under retrieve-then-filter it measured 86.1% structural exposure but 29.5–41.3% answer disclosure, model-dependent (Namboothiri, TrustNLP 2026, ACL `2026.trustnlp-main.15`, `[PEER-REVIEWED]`). The number we should certify is structural exposure = 0; answer leakage is a separate model-dependent diagnostic.

9. **Almost no LLM-agent memory paper publishes retrieval quality.** Of Generative Agents, Reflexion, Voyager, RET-LLM, A-MEM, MemGPT and others, six of nine publish no retrieval evaluation; the rest report end-task accuracy, latency or downstream ablations. ExpeL is the notable exception because it ablates the ranking function (arXiv:`2308.10144`, `[PEER-REVIEWED]`). This absence is the white space StealthLab can occupy.

10. **The recommended first build is a 150-query human/log core plus 60 derived queries, not an ingestion-only set.** At least 20 queries must be no-answer, at least 40 must be long-tail/hard buckets, and the headline metric is computed on the 90-query human/log core. Exit requires α ≥ 0.80 at the lower bootstrap bound on double-annotated items, zero structural exposure, and a paired 95% CI for every configuration delta.

---

## 2. Verified state of the codebase

### 2.1 What the project rules require

The frozen documents establish constraints the evaluation must preserve:

- Spec v4 states that global reuse is earned through evidence and permissions (spec v4, lines 19-24), that retrieval combines semantic and full-text retrieval (lines 1177-1186), and that private evidence cannot leak through a public procedure (line 1230).
- `schema.md` marks Observations, Claims, Procedures, ApplicabilityRules and Policy as `[V]` versioned-mutable; Events, Artifacts, Evidence, Reviews and Outcomes as `[H]` historical append-only; capability and belief as derived. The golden set must not write to `[V]` objects and must treat `[H]` lineage as immutable provenance.
- `CLAUDE.md:175-178` states the load-bearing composition rule: **retrieval fuses compensatorily by RRF; applicability is a non-compensatory cascade**. The evaluation must preserve this split rather than collapse both into one score.

### 2.2 Retrieval is implemented more than once

| Path | Ranker | Role |
|---|---|---|
| Knowledge-node/graph substrate | `backend/app/services/retrieval.py` | vector + lexical RRF over `task_nodes`/`knowledge_nodes`; used by graph tooling and legacy consumers |
| Product retrieval | `backend/app/services/retrieval_service.py` | canonical REST/MCP service; Goal FTS+ANN, RRF, JEV/NLI judgment, Procedure retrieval, hard constraints, evidence selection |
| Applicability search | `backend/app/services/applicability.py` | cost + embedding + lexical candidate legs, hard-constraint cascade, relevance/capability rerank |

`retrieval.py:35` fixes `RRF_K = 60`; `fuse_rrf` sums `1/(k+rank+1)` at `retrieval.py:148`, so contributions compensate across lists. `retrieval_service.py` delegates to the product service for REST and MCP (`retrieval_service.py:1-34`) and reuses `identity_resolution.rrf_fuse`. This is a drift risk: there are multiple RRF implementations and the golden set must record which path each query exercised.

### 2.3 The applicability gate is non-compensatory

`check_hard_constraints` starts at `backend/app/services/applicability.py:325` and short-circuits on the first failure in this order: temporal validity, staleness, availability, verification state, approval status, scope, exclusions, preconditions, invariants (`applicability.py:381-513`). `failed_constraints` is diagnostic and not exhaustive because fail-fast records one failure (`applicability.py:72-77`).

Two honest exceptions must be visible in evaluation:

1. An opt-in reasoning client may convert a confident precondition failure into a pass (`applicability.py:242-282`, called at `:468-475`). It never converts a pass into a failure.
2. Invariant `undecidable` is not disqualifying, while a malformed invariant expression is (`invariants.py:74-83`, `applicability.py:509`). A broken extractor can therefore look like an inapplicable procedure.

### 2.4 The cold-start gate exists but the product path bypasses it

```python
MIN_VERIFIED_PROCEDURES_TO_ENABLE_RETRIEVAL = 1
```

`backend/app/services/applicability.py:60-65`. `should_disable_procedure_retrieval` counts viewer-visible verified and active procedures (`applicability.py:87-119`). The count omits `approval_status='approved'` and `is_engineering_fixture = false`, both enforced later by the cascade, so one verified-but-unapproved or fixture row can open the gate while every candidate is later rejected. `retrieve_procedures` defaults `require_verified=False` (`retrieval_service.py:935`) and does not call the gate; MCP likewise exposes an unverified opt-in.

**Evaluation implication:** a golden-set run that mixes gated and ungated paths produces incomparable numbers. `gate_consulted`, visible verified count and `require_verified` are mandatory run fields.

### 2.5 What text is embedded

| Object | Embedded text | Location |
|---|---|---|
| Goal | `f"{canonical_name}\n{description}"`, or name only when description is null | `backend/app/services/goals.py:210-217` |
| Procedure | versioned `procdoc_v2` document: Name, Purpose, When to use, When not to use, Domain, Steps, Tools, Depends on, Constraints, Expected outcome, Fails when | `backend/app/services/retrieval_document.py:70`, builder `:384-453` |
| Claim | bare `statement`; no versioned retrieval document or text hash | `backend/app/services/claims.py:306` |

The three product search representations are not identical:

- Goal vector: `goals.embedding`, produced from name + newline + description.
- Goal FTS projection: `canonical_name + aliases + description`, capped at 8,000 characters (`search_projection.py:102`).
- Goal judge text: `canonical_name + ': ' + short_description` (`retrieval_service.py:379`).

Most production Goal writers supply no description—step Goals, procedure capture, trajectory extraction and local sync—so their vector and FTS text is name-only. A query→Goal set built over those rows partly measures **text construction at ingestion**, not retrieval. §11's records therefore require an `embedding_coverage` cohort and a `description_present` flag.

Embedding defaults also need pinning: `embedding_dimension=1024`, provider chain `gemini,voyage` (`config.py:310-336`), but the runtime currently uses the first configured provider and has no cross-provider fallback (`embeddings.py:233-248`).

### 2.6 Read-time filtering is real but incomplete

Superseded/contradicted knowledge-node rows are filtered by `NOT_TRUTH_STATE_OUT` in `retrieval.py:69`, and applicability filters `t_valid`/`t_invalid` (`applicability.py:199`). `claims.relate_claims` sets `truth_state='OUT'` while deliberately leaving `t_invalid` null, so bi-temporal filtering alone does not hide the claim. However `search_projection._CLAIM_SQL:65-69` filters `t_invalid` but not `truth_state`, so a superseded Claim's text can remain in the claim search projection. The golden set must include a `truth_state_OUT` bucket and a separate superseded-projection probe.

### 2.7 Scoping is centralized but not universally adopted

`scope_predicates()` in `backend/app/services/access.py:222-247` requires both visibility and tenant predicates; omitting `tenant_scope` is a TypeError by design. Verified exceptions:

- `local_retrieval._tier_candidates_by_path:498-499` defaults to `AccessScope.unrestricted()` when scope is absent.
- `retrieval_service._legs:254` applies visibility but not the tenant predicate, although procedure/claim projections carry `tenant_id`.
- `retrieval_service._fetch_procedures:918-919` hydrates without a scope fragment.
- `goals.py:220-239` and `retrieval_service.py:204-223` contain forked visibility-predicate builders rather than calling `scope_predicates()`.

Isolation must therefore be scored, not inferred from the presence of a tenant column.

### 2.8 Existing measurement, corrected

The prompt's belief that no retrieval-quality measurement exists is **refuted**:

- Metric library: `backend/tests/evaluation/harness/metrics.py:21-141`.
- Offline labeled fusion set: `backend/tests/evaluation/fixtures/gold_retrieval/fusion_cases.json`, with `min_recall_at_k` and `max_false_positive_rate_at_k` assertions in `backend/tests/evaluation/retrieval/test_gold_retrieval_offline.py:58-94`.
- Live real-embedding test: `backend/tests/evaluation/retrieval/test_live_retrieval_e2e.py:71-201`, eight hand-written queries, Recall@1/@5, MRR, nDCG@10 and FPR@5.
- Real-DB vector-vs-lexical precision@1/recall@k: `backend/scripts/run_experiment_1.py:136-168`, with committed results in `backend/experiment_1_results.json`.

The real deficiencies are: no on-disk graded qrels for the live path, no MAP, no judged `unjudged` pool, no isolation probe, no paraphrase panel, and no product-path A/B with a CI.

### 2.9 Experiment rigs and statistics

`experiments/harness/` does not exist; `CLAUDE.md:85` and `evaluation/ARCHITECTURE.md` are stale. The live rigs are:

- `experiments/swebench/`: repo-stratified paired bootstrap, 10,000 resamples, seed 0; exact McNemar; five pre-registered decision clauses (`analyze.py:7-17`, `:32-61`).
- `experiments/ds1000/`: exact McNemar, Holm correction, family-clustered bootstrap with 10,000 resamples (`analyze.py:31-69`).
- `experiments/bigcodebench/`: raw counts only; its own README calls 20 tasks a pipeline test, not a statistical result.
- `experiments/swebench_rebench/`: throughput and infrastructure benchmark; shares the swebench analyzer and pins a model cutoff as a contamination guard.
- `experiments/experience_transfer/`: runner only; no scoring or statistics.

The best reusable machinery is the ds1000 `paired()`/`holm()` pair, swebench's placebo/noise arms, and swebench_rebench's cutoff guard.

---

## 3. Metrics: recommended set

### Primary headline metric

> **Δ graded nDCG@10 against the lexical-only leg on the human/log query core, with a query-clustered paired bootstrap 95% CI.**

Why this and not a single absolute score:

- BEIR found BM25 a robust baseline across 18 heterogeneous datasets and showed in-domain MS MARCO performance does not predict zero-shot generalization (Thakur et al., arXiv:`2104.08663`, NeurIPS 2021 D&B, `[PEER-REVIEWED]`).
- Sakai's SIGIR Forum analysis recommends effect size and margin of error alongside p-values because a p-value conflates sample size with effect (Sakai, *Statistical Reform in Information Retrieval?*, SIGIR Forum 2014, official PDF, `[PEER-REVIEWED]`).
- Urbano, Lima & Hanjalic computed more than 500 million p-values and found topic-set size and metric choice materially change error rates (arXiv:`1905.11096`, `[PREPRINT]`).

| Metric | What it catches | What it hides | Recommended k / value | Citation |
|---|---|---|---|---|
| **Δ graded nDCG@10 vs lexical-only** | Rank quality, graded preference, fusion's marginal value | Candidate-generation failure; cost | k=10; 95% paired CI | Järvelin & Kekäläinen, DOI `10.1002/asi.10137`; BEIR arXiv:`2104.08663` |
| **Recall@20 (human core)** | Sufficiency: whether the answer-bearing item entered the candidate set | Ordering within the set | k=20 = current leg depth | BEIR arXiv:`2104.08663`; Joren et al. arXiv:`2411.06037` |
| MRR@10 | Whether the best applicable item is ranked first | Everything below rank 1 | k=10 | BEIR arXiv:`2104.08663` |
| Success@3 (grade ≥ 2 in top 3) | Product-decision proxy: usable result in the first shortlist | Tail and abstention behavior | k=3 | Precedent is TREC-style pooled assessment; no canonical Success@k source verified—label as local definition |
| Per-bucket nDCG/Recall | Long-tail and precondition-specific failures | Nothing, if every bucket is published | Fixed buckets; no bucket n<20 | LoTTE, ACL `2022.naacl-main.272` |
| False-positive rate@k | Wrong items returned where explicit negatives exist | Unjudged items | k=10 and k=20 | Local metrics library; BEIR-style pooling |
| **False-accept rate** | An item that should fail the trust/applicability gate being accepted | Whether reasoning fallback was enabled | Target 0; publish by gate path | `backend/tests/evaluation/harness/metrics.py:72` |
| **Structural exposure rate** | Unauthorized items entering the pre-filter candidate set | Model refusal to disclose | Target 0 | AFR, ACL `2026.trustnlp-main.15` |
| Answer leak rate | Model actually disclosing unauthorized content | Retrieval-time exposure; model-dependent | Report, do not certify | AFR, ACL `2026.trustnlp-main.15` |
| No-answer empty-result rate | Correct abstention on unanswerable queries | Low-quality results instead of abstention | Report by no-answer bucket | Selective prediction, ACL `2021.acl-long.84`; AUCM arXiv:`2407.16221` |
| Sufficiency rate | Whether retrieved context can answer the query | Ranking | Human-labeled subset | Joren et al. arXiv:`2411.06037` |
| Coverage/α-nDCG | Whether all answer-bearing nuggets are present | Precision | Report separately from nDCG | CRUX arXiv:`2506.20051` |
| Funnel counts | Which stage loses candidates | A single quality claim | Every stage | Non-compensatory MCDA: DOI `10.1002/mcda.1482` |
| Effect size + CI | Practical magnitude and uncertainty | — | Always | Sakai, SIGIR Forum 2014 official PDF |
| Unjudged rate | Pool incompleteness masquerading as irrelevance | Judgment cost | Publish per query | TREC DL 2019, arXiv:`2003.07820` |

### Metrics not to report as the headline

- **Raw cosine or `ts_rank` distributions:** incomparable scales; RRF exists because of that.
- **Binary nDCG alone:** flatters topically adjacent clusters.
- **MRR as an interval-scale mean:** Fuhr argues reciprocal rank is ordinal, not interval (SIGIR Forum 51(3), 2017, official PDF, `[PEER-REVIEWED]`); Sakai disputes the practical consequence. Report MRR, but do not interpret small differences as linear.
- **A single blended cascade score:** contradicts the MCDA definition of non-compensatory preference.
- **Absolute latency from the existing probe:** its own documentation says the sample was tiny and remote.
- **Pooled means over all buckets:** LoTTE demonstrates large per-cell variation; pooling hides it.
- **Any point estimate at n<50 without an interval.**

---

## 4. Golden set design

### 4.1 `corpus_snapshot`

A judgment is valid only against a frozen corpus and configuration.

| Field | Type | Notes |
|---|---|---|
| `snapshot_id` | uuid/string | Referenced by every query, judgment and run |
| `created_at` | timestamp | Freeze time |
| `code_rev` | string | Git SHA |
| `migration_head` | int | Applied migration number |
| `corpus_manifest_sha256` | string | Hash over sorted live `(object_id, version, t_valid)` |
| `n_goals_live`, `n_goals_embedded` | int | Must be reported separately; unembedded Goals are invisible to the vector leg |
| `n_procedures_live`, `n_procedures_embedded` | int | Same |
| `index_lag_rows` | int | Must be 0 for a clean run |
| `embedding_model_id`, `embedding_provider`, `embedding_dim` | string/int | Exact ids, not friendly names |
| `retrieval_document_version` | string | e.g. `procdoc_v2` |
| `rrf_k`, `search_top_k`, `judge_top_k`, `candidate_pool_size` | number | Fusion/evaluation configuration |
| `min_verified_procedures` | int | Current value 1 |
| `tenant_id` | string/null | Null means Commons |
| `visibility_classes_present` | list | Private rows must exist or isolation is vacuous |

### 4.2 `query`

| Field | Type / allowed values | Purpose |
|---|---|---|
| `query_id`, `snapshot_id` | string | Stable identity |
| `path` | `P1_projection` / `P2_cascade` / `P3_product` | Prevents pooling incomparable rankers |
| `text` | string | Exact text submitted to FTS/embedding |
| `language` | `en` initially | `to_tsvector('english', ...)` is hard-coded |
| `bucket` | enum | Long-tail bucket; `misc` prohibited |
| `source` | `human_log` / `human_authored` / `ingestion_derived` / `imported` | Drives contamination policy |
| `access_scope` | object | Viewer, org ids, unrestricted flag |
| `tenant_scope` | object | Tenant id or Commons |
| `current_scope` | map | Exact applicability key set; missing keys can hard-fail |
| `local_claims` | list | Repository facts for P3 |
| `expected_no_answer` | bool | First-class, not a bucket name |
| `expected_relevant_ids` | list | Convenience; authoritative judgments remain separate rows |
| `k` | int | Metric cutoff |
| `author_id`, `annotator_disjoint_from_author` | string/bool | Prevents self-adjudication |
| `derivation_leakage_risk` | float/null | Token/IDF overlap with the source document |
| `paraphrase_group_id` | string/null | Groups semantically equivalent phrasings |
| `frozen_at`, `set_version` | timestamp/string | Labels are versioned, never edited in place |

### 4.3 `judgment`

| Field | Type / allowed values | Meaning |
|---|---|---|
| `query_id`, `snapshot_id` | string | Scope of the judgment |
| `target_kind` | `goal` / `procedure` / `claim` / `node` | Object being judged |
| `target_id`, `target_version` | string/int | Stable id plus version where applicable |
| `grade` | 0/1/2/3 | 0 irrelevant; 1 related but not useful; 2 useful; 3 direct match |
| `judgment_state` | `judged_relevant` / `judged_irrelevant` / `unjudged` | Prevents unjudged from being silently counted as irrelevant |
| `hard_negative` | bool | Explicit, not inferred from grade 0 |
| `hard_negative_kind` | enum/null | `paraphrase_wrong_scope`, `same_goal_wrong_precondition`, `superseded`, `stale`, `out_of_tenant_lookalike`, `truth_state_OUT` |
| `rationale` | string | Required |
| `annotator_id`, `annotator_kind` | string/enum | Human or model-assisted |
| `adjudicated` | bool | Fourth-coder resolution |
| `rubric_version` | string | Part of label validity |

### 4.4 No-answer and abstention

`expected_no_answer=true` asserts three separate things:

1. `zero_result_rate`: the system returns no applicable item.
2. `abstention_quality`: if it returns items anyway, their grades and hard-negative kinds.
3. `terminal_reason_correct`: emptiness comes from `no_candidate` or `gate`, never `cold_start_blocked`, `shard_unavailable`, or an internal error.

Selective prediction distinguishes data uncertainty (the question has no answer) from model uncertainty (the model cannot answer); mixing them is a category error (Sixty et al., ACL `2021.acl-long.84`, `[PEER-REVIEWED]`).

### 4.5 Hard negatives

Construct them by failure class, not random sampling. A strong retriever's top-ranked unjudged items are enriched with relevant-but-unlabeled positives, so training or evaluation on those negatives creates pooling bias (Cai et al., arXiv:`2209.05072`, `[PEER-REVIEWED]`; de Souza Moreira et al., arXiv:`2407.15831`, `[PREPRINT]`). Required classes for this product:

- same Goal, wrong precondition;
- superseded version versus live version;
- `truth_state='OUT'` Claim versus current Claim;
- private/out-of-tenant lookalike with high lexical similarity;
- abstract Goal Procedure versus exact-Goal Procedure;
- paraphrase of the query with a changed constraint.

### 4.6 `run`

| Field | Type | Notes |
|---|---|---|
| `run_id`, `snapshot_id`, `set_version`, `code_rev` | string | Immutable run identity |
| `arm` | enum | `lexical_only`, `semantic_only`, `hybrid`, `product`, `config_variant:*` |
| `models` | object | Embedding id/provider, judge id, reasoning id |
| `seed` | int/null | Required when a model is involved |
| `gate_consulted`, `require_verified` | bool | Prevents cross-path pooling |
| `access_scope`, `tenant_scope` | object | Exactly the scope used |
| `per_query` | list | Ranked ids, grades, per-stage funnel, terminal reason, latency, exposure ids |
| `aggregates` | object | Overall, per path, per bucket, per arm |
| `stats` | object | Effect size, bootstrap CI, permutation p, McNemar for binary success, Holm-adjusted p where a family exists |
| `environment` | object | Postgres/pgvector versions, migration head, shard count, corpus size |

Isolation is a per-query pair: identical `text` and `current_scope`, different tenant/viewer. Report structural exposure, answer leakage, and authorized-answer recall together; a system that returns nothing can otherwise achieve a perfect leak score.

---

## 5. Query sourcing strategy

### 5.1 Recommended v1 mix

| Source | Count | Use |
|---|---:|---|
| Human-authored or redacted local trace | 90 | Headline metric, no-answer, precondition and long-tail cases |
| Ingestion-derived, human-approved | 60 | Vocabulary shift, bucket fill, paraphrase panel |
| Imported from an existing labeled set | 0 initially | Only after license and provenance review |

The human/log core must be authored by people who did not see the retriever's ranking. A synthetic query is not admitted to the headline core without human rewrite, even if its intended target is known.

### 5.2 The central bias

The exact effect—generating a query while its source document is in context—has no settled single name in the retrieval-evaluation literature. The nearest evidence is:

- **Source bias:** retrievers and rerankers systematically rank LLM-generated text higher because it is lower-perplexity and semantically compressed (Tang et al., arXiv:`2310.20501`, `[PREPRINT]`).
- **Knowledge leakage:** query-expansion gains appear only where the generated expansion contains content entailed by the gold evidence; unmatched claims fall below the unexpanded baseline (arXiv:`2504.14175`, `[PREPRINT]`).
- **Doc2Query's own measurement:** 69% of predicted query words are copied from the document; expansion using only new words is indistinguishable from no expansion (arXiv:`1904.08375`).

Operationally, measure token/IDF overlap between every derived query and its target, stratify results by overlap, and never pool derived and human scores into the headline.

### 5.3 Contamination mitigations

1. **Separate dev and test query sets.** Thresholds, prompts, fusion weights and relevance-gate cutoffs are tuned on dev only.
2. **Source-disjoint queries.** A query written from document A is judged against a corpus containing related content from B; do not evaluate a query only against its own source sentence.
3. **Deduplicate at corpus and query level.** Rule-based n-gram dedup misses paraphrase; use semantic dedup and keep the conservative intersection for auto-generated material.
4. **Human rewrite.** Every derived query is rewritten by an independent person who sees the intent but not the target text.
5. **Leakage scan.** Reject queries containing UUIDs, filenames, extractor versions, step numbers or verbatim source identifiers.
6. **Cutoff guard.** Reuse the `model_cutoff` idea in `experiments/swebench_rebench/experiment.json`: queries whose intent could not have been memorized by the generator model are stronger probes.
7. **Report the source flag on every row.** Synthetic-only improvements are hypotheses about the generator, not retriever capabilities.

### 5.4 Long-tail buckets

Minimum buckets:

`exact_identifier`, `paraphrase`, `vocabulary_mismatch`, `same_goal_wrong_precondition`, `neighbouring_domain`, `abstract_goal`, `orphan_goal`, `superseded`, `stale`, `private_cross_tenant`, `no_answer`, `multi_hop`, `short_fragment`, `long_natural_language`, `ambiguous_multi_answer`.

LoTTE separates search and forum query styles over the same long-tail domains; its per-cell Success@5 varies materially, demonstrating why a pooled mean is insufficient (Santhanam et al., ACL `2022.naacl-main.272`, `[PEER-REVIEWED]`).

### 5.5 What synthetic generation is and is not good for

**Good for:** bucket fill, vocabulary-shift probes, paraphrase panels, hard-negative candidates, pre-annotation.

**Not good for:** no-answer intent, honest trust/applicability state, user-intent distribution, or the release headline. Trust properties such as `verification_state`, `approval_status`, `staleness` and tenant scope are substrate state, not text-generation outputs.

---

## 6. Cascade instrumentation plan

### 6.1 Funnel

| Stage | Code | Metric |
|---|---|---|
| S0 cold start | `should_disable_procedure_retrieval` | `gate_blocked`; visible verified count |
| S1 candidate generation | `_fetch_candidate_pool` legs + RRF | per-leg hits, fused count, fan-in ratio, embedding-model mismatch |
| S2 family/dedup exclusion | excluded procedure ids | excluded count; leak-back count |
| S3 access filter | visibility/tenant re-fetch | filtered count, structural exposure, vanished-vs-hidden |
| S4 hard constraints | `check_hard_constraints` | entered/disqualified/pass per constraint in code order |
| S5 survivor ranking | relevance ⊕ capability RRF | survivors, ranking mode, rank changes, grade-3-over-grade-2 flips |
| S6 relevance gate | `passes_relevance_gate` | dropped, label band, kept-without-score |
| S7 truncation | `limit` break | truncated count and grade≥2 losses |
| Terminal | — | mutually exclusive terminal reasons |

The funnel must be non-increasing by construction; a non-monotone stage count is a code bug.

### 6.2 Non-compensatory reporting

Report each constraint's first-failure share. Because fail-fast records only the first violation, a 10% offline sample should re-evaluate all constraints and publish both:

- `share_of_first_failure`—what the production order rejected first;
- `share_violating_any`—the underlying violation prevalence.

Precondition outcomes must be five-way: satisfied exactly, satisfied by Claim id, satisfied by reasoning fallback, unsatisfied with no Claim, unsatisfied by mismatch. "No Claim found" is a corpus fact, not a system verdict.

Invariant outcomes are four-way: satisfied, violated, undecidable, evaluation error. `undecidable` must not be folded into rejection.

### 6.3 What not to collapse

Do not synthesize S0-S7 into an "overall retrieval score." The MCDA literature's non-compensatory models produce partitions/outranking relations, not utilities (DOI `10.1002/mcda.1482`). A funnel plus terminal-reason distribution is the honest artifact.

---

## 7. Judging strategy

### 7.1 Human labels are authoritative

- Ranked-candidate metrics are computed directly from qrels; an LLM judge is not in the metric path.
- Use a judge only to propose judgments for human review or grade free-text answers.
- Never let a model from the same family that generated a trace grade that trace's quality; self-preference correlates with self-recognition (Panickssery et al., arXiv:`2404.13076`, `[PREPRINT]`).

### 7.2 Human protocol

Three annotators judge a 20% sample; two judge the remainder; a fourth adjudicates disagreements. Annotators see the query and a randomized candidate pool, not ranks, scores, gold marks, provenance or applicability fields.

**Annotator question:**

> Given only the user's request and this candidate, how useful is the candidate for satisfying the request?  
> 3 — directly satisfies the request under the stated context.  
> 2 — useful component, but missing required context or constraints.  
> 1 — related topic, but answering it would not satisfy the request.  
> 0 — unrelated, superseded, out of scope, or wrong under the stated context.  
> Judge only what the candidate says. Do not use outside knowledge or assume the system marked anything as correct.

Adjudication is retained as a dispute row. An unresolved item is excluded from the denominator and reported, not silently majority-voted.

### 7.3 IAA and floors

Report raw agreement, positive prevalence, and Krippendorff's α with a bootstrap CI. Ordinal α is appropriate for ordered grades; raw agreement alone is misleading under sparse positives.

Proposed policy, not a literature-established constant:

- **α lower 95% bound < 0.67:** block; the rubric is broken.
- **0.67–0.80:** usable for development, not a release gate.
- **≥ 0.80:** gold labels.

Krippendorff's α is itself contested and metric-dependent for graded labels (Braylan et al., arXiv:`2212.09503`, `[PREPRINT]`), so publish the distance function and positive-class prevalence with the coefficient.

### 7.4 LLM judge validation, if used

Run both presentation orders, evidence-before-rating, and at least three trials. Report position-swap disagreement, self-preference, and score-comparison inconsistency. FairEval showed reordering changed 66/80 outcomes (arXiv:`2305.17926`); TrustJudge measured substantial inconsistency from coarse scales (arXiv:`2509.21117`, `[PREPRINT]`).

Pass condition: the lower bound of judge-human agreement is not below human-human agreement minus five points, and order-swap disagreement is ≤10%. These are local policy thresholds, not established standards.

---

## 8. Anti-gaming and failure-mode checklist

| Failure mode | Detection | Mitigation |
|---|---|---|
| Near-duplicate queries | Pairwise semantic clustering; paraphrase-group ids | Keep one representative; report per-paraphrase mean and variance |
| Lexical-only answerability | Query/gold IDF overlap; lexical baseline | Reject high-overlap generated queries; require hybrid to beat lexical |
| Leaked provenance in query text | UUID/filename/version/extractor regex | Reject at write time |
| Cold-start artifacts | Gate status and visible verified count on every row | Stratify all results; never pool gated and ungated paths |
| Index/embedding drift | Manifest diff | Refuse comparison across changed embedding, index, RRF or procdoc versions |
| Hardcoding | Shuffled ids, paraphrases, canary queries with no gold | Publish delta; any canary hit is a release blocker |
| Sparse positives | Positive-density histogram; always-return-k baseline | Prefer Recall@k and publish degenerate baselines |
| Unjudged treated as irrelevant | `judgment_state=unjudged` | Pool deeply; publish unjudged rate |
| Judge drift | Fixed judge canary panel | Pin model/prompt hash; block on drift |
| Private leakage | Cross-tenant paired queries; pre-filter ids | Structural exposure target 0; pair with authorized recall |
| Tuning on the test set | Threshold/config provenance | Dev/test split; config version in every run |
| One bucket dominates | Per-bucket n and minimum cell | No pooled release verdict; minimum n per bucket |
| Superceded content looks relevant | `truth_state_OUT` and version buckets | Score absence as correct; test projection filters |
| Reasoning fallback changes gate results | Flag and counter | Report false-accept/false-reject by fallback on/off |
| Latency ignored | Per-stage timing | Report candidate count, judge calls and tokens alongside quality |

---

## 9. Open questions

1. **How many graded positives per query are needed for stable nDCG?** The literature does not establish this. Settle it by over-labeling 50 queries, then subsampling positives at 1/2/4/8 and reporting where nDCG and Recall diverge.
2. **What agreement floor is appropriate for retrieval relevance?** No verified retrieval-specific standard exists. Measure human-human agreement on 200 items and publish the CI.
3. **Does a judge help at this scale?** Open. Run the judge on the human-labeled sample; if cost exceeds the human sample it validates, it is a debugging tool, not a labeling path.
4. **How large is the synthetic-vs-human retrieval gap here?** Open. Compare paired human and derived queries; stratify by lexical overlap.
5. **What fraction of real agent requests is answerable from the corpus?** Open. Sample redacted trace queries and label corpus answerability before freezing the set.
6. **Will paraphrase sensitivity predict downstream arm ordering?** Open. Re-run the three-arm rigs on a paraphrase panel.
7. **Can isolation be certified structurally?** The literature has no standard. The defensible local target is zero unauthorized candidate exposure; answer leakage remains model-dependent.
8. **How much do labels decay when the index or embedding changes?** Open. Re-annotate 50 items after one representation change and publish gold-span survival.
9. **Does the reasoning fallback improve utility enough to justify its false-accept risk?** Open. A/B the tier on precondition-discriminator queries with human adjudication.

---

## 10. Recommended implementation sequence

| Step | Build | What it proves | Numeric exit criterion |
|---:|---|---|---|
| 1 | One metric module: existing binary metrics plus graded nDCG, unjudged rate, exposure rate | Removes metric ambiguity | 1 implementation; 0 duplicate `_ndcg`; ≥12 metric cases |
| 2 | Corpus snapshot manifest and run manifest | Makes a score mean something later | 100% of runs carry SHA, migration head, embedding id, procdoc version, RRF k, tenant scope |
| 3 | Convert existing fusion cases to the new schema | Schema is usable on real cases | 7/7 cases reproduce identical scores; ≥12 cases after adding tenant/superseded/precondition buckets |
| 4 | Human/log query core | Establishes a non-circular benchmark | 90 queries; ≥20 no-answer; ≥40 long-tail; ≥3 annotators on 20% |
| 5 | Agreement and adjudication | Labels are usable | α lower CI ≥0.80 on double-annotated sample; 100% disputes retained |
| 6 | Ingestion-derived supplement | Measures coverage and synthetic bias | 60 derived queries; 100% human-approved; Δ vs human core reported with CI |
| 7 | Isolation probe suite | Converts scoping into a measured property | 0 structural exposures across ≥50 cross-tenant probes; authorized recall reported beside it |
| 8 | Cascade funnel instrumentation | Separates generation, access, gate and rank failures | Non-increasing stage counts on 100% queries; all terminal reasons sum to n |
| 9 | Live product-path evaluation | Measures the shipped ranker | ≥150 scored product-path queries; nDCG@10 CI half-width ≤0.15 at n=150 |
| 10 | Lexical/semantic/hybrid A/B | Proves fusion value | Paired permutation p, effect size, 95% CI; hybrid must beat lexical by pre-registered Δ≥0.02 nDCG@10 |
| 11 | Small-set statistics consolidation | Makes ablations comparable | One shared `paired()`, `holm()`, `mcnemar_exact()`; numeric identity with current rig artifacts |
| 12 | MDE and multiplicity policy | Prevents false "improvements" | Every verdict carries n, discordant count and MDE; Holm applied to every sliced family |
| 13 | Drift/anti-gaming gates | Stops silent flattery | Manifest mismatch fails run; canary queries, shuffled ids and paraphrase panel all run every release |
| 14 | Flat→hierarchical retrieval comparison | Decides whether the new hierarchy helps | Recall@20, nDCG@10, JEV calls, latency, fallback rate reported; hierarchy ships only with a non-negative Recall delta and CI |

Recommended first week: steps 1–6. They produce a defensible set before any retrieval optimization is judged against it.

---

## 11. Bibliography

### Metrics and relevance judgments

- Järvelin & Kekäläinen, *Cumulated gain-based evaluation of IR techniques*, DOI `10.1002/asi.10137`, 2002, `[PEER-REVIEWED]` — introduces graded cumulative gain; establishes that gain level can change system ordering.
- Sormunen, *Liberal relevance criteria of TREC*, DOI `10.1145/564376.564433`, 2002, `[PEER-REVIEWED]` — re-assessment found many TWC-relevant documents only marginally relevant.
- Voorhees, *Variations in relevance judgments*, DOI `10.1016/S0306-4573(00)00010-8`, 2000, `[PEER-REVIEWED]` — low judge agreement can coexist with stable system ordering.
- Sakai & Kando, *IR metrics designed for evaluation with incomplete relevance assessments*, DOI `10.1007/s10791-008-9059-7`, 2008, `[PEER-REVIEWED]` — condensed-list metrics under incompleteness; not a bootstrap-sensitivity paper.
- Sakai, *Statistical Reform in Information Retrieval?*, SIGIR Forum 2014, official PDF, `[PEER-REVIEWED]` — effect size, margin of error and multiplicity in metric comparisons.
- Sakai, *Comparing Two Binned Probability Distributions for Information Access Evaluation*, DOI `10.1145/3209978.3210073`, 2018, `[PEER-REVIEWED]` — verified Sakai entry point closest to the requested “k-calculus” idea.
- **“k-calculus”: [UNVERIFIED — could not resolve].** No primary source using the literal term in this sense was found. Do not cite the term as an established method.
- Urbano, Lima & Hanjalic, arXiv:`1905.11096`, 2019, `[PREPRINT]` — large-scale empirical error rates for significance tests in IR.
- Fuhr, *Alternative to Mean Reciprocal Rank*, SIGIR Forum 51(3), 2017, official PDF, `[PEER-REVIEWED]` — argues reciprocal rank is ordinal, not interval.
- Figueira, Greco, Roy & Słowiński, *Overview of ELECTRE*, DOI `10.1002/mcda.1482`, `[PEER-REVIEWED]` — non-compensatory methods produce outranking/partition structures, not synthesized utilities.
- Joren et al., arXiv:`2411.06037`, 2025, `[PREPRINT]` — context sufficiency labeling and selective generation.
- CRUX, arXiv:`2506.20051`, 2025, `[PREPRINT]` — coverage-aware metrics diverge from conventional relevance metrics.

### Golden sets, abstention, and long tail

- Thakur et al., BEIR, arXiv:`2104.08663`, 2021, `[PEER-REVIEWED]` — 18 heterogeneous datasets; BM25 robust baseline; in-domain performance does not predict zero-shot transfer.
- Santhanam et al., LoTTE / ColBERTv2, ACL `2022.naacl-main.272`, 2022, `[PEER-REVIEWED]` — long-tail, domain- and query-style-stratified evaluation.
- Kwiatkowski et al., Natural Questions, ACL `Q19-1026`, 2019, `[PEER-REVIEWED]` — real queries, 5-way annotation, 25-way variability subset, published human ceiling.
- Bajaj et al., MS MARCO, arXiv:`1611.09268`, 2016, `[PREPRINT]` — real Bing queries and passage judgments; sparse-positive methodology.
- Craswell et al., TREC Deep Learning 2019, arXiv:`2003.07820`, `[PEER-REVIEWED]` — pool depth, four-point grades, HiCAL augmentation, incomplete-pool handling.
- Muennighoff et al., MTEB, ACL `2023.eacl-main.148`, 2023, `[PEER-REVIEWED]` — 8 tasks/58 datasets/112 languages; no embedding dominates.
- Sixty et al., selective prediction, ACL `2021.acl-long.84`, `[PEER-REVIEWED]` — separates data uncertainty from model uncertainty.
- AUCM, arXiv:`2407.16221`, `[PREPRINT]` — answerable/unanswerable confusion matrix; abstention trades against answerable accuracy.

### Agreement and LLM judging

- Carletta, ACL `J96-2004`, `[PEER-REVIEWED]` — kappa interpretation scale.
- Artstein & Poesio, ACL `J08-4004`, `[PEER-REVIEWED]` — agreement coefficients for >2 coders and non-nominal scales.
- Braylan et al., arXiv:`2212.09503`, `[PREPRINT]` — agreement reliability is metric/distance dependent; raw agreement and α can mislead.
- Zheng et al., MT-Bench, arXiv:`2306.05685`, `[PEER-REVIEWED]` — >80% judge-human agreement on generation preferences; judge model changes winners.
- FairEval, arXiv:`2305.17926`, `[PREPRINT]` — order bias changed 66/80 pairwise outcomes.
- Panickssery et al., arXiv:`2404.13076`, `[PREPRINT]` — self-recognition predicts self-preference.
- Shi et al., arXiv:`2406.07791` / ACL `2025.ijcnlp-long.18`, `[PEER-REVIEWED]` — position bias varies by judge/task and grows with candidate quality gap.
- TrustJudge, arXiv:`2509.21117`, `[PREPRINT]` — discrete-scale inconsistency and distributional scoring.
- Comparative Trap, arXiv:`2406.12319`, `[PREPRINT]` — pairwise and pointwise judging win on different adversarial sets.

### Synthetic queries and contamination

- Nogueira & Lin, Doc2Query, arXiv:`1904.08375`, `[PREPRINT]` — 69% copied query words; copied terms drive the gain.
- Gospodinov et al., Doc2Query–, arXiv:`2301.03266` / DOI `10.1007/978-3-031-28238-6_31`, `[PEER-REVIEWED]` — filtering generated queries improves effectiveness while shrinking the index.
- Rahmani et al., arXiv:`2405.07767`, `[PREPRINT]` — synthetic TREC collections; system bias and expert filtering.
- Rahmani et al., arXiv:`2506.10301`, `[PREPRINT]` — synthetic queries bias absolute performance more than relative comparison.
- ARES, arXiv:`2311.09476` / NAACL 2024, `[PEER-REVIEWED]` — prediction-powered inference validates RAG metrics with ~150 human annotations. **Correction:** arXiv:`2310.09476` is a nuclear-physics paper, not ARES.
- RAGAS, ACL `2024.eacl-demo.16`, `[PEER-REVIEWED]` — reference-free RAG metrics; context-relevance agreement is weaker than faithfulness/answer relevance.
- Self-RAG, arXiv:`2310.11511`, ICLR 2024, `[PEER-REVIEWED]` — reflection tokens and long-tail PopQA evaluation.
- Source bias, arXiv:`2310.20501`, `[PREPRINT]` — retrievers prefer LLM-generated text.
- Knowledge leakage, arXiv:`2504.14175`, `[PREPRINT]` — expansion gains occur only where generated content is entailed by gold evidence.
- Weller et al., arXiv:`2309.08541` / ACL `2024.findings-eacl.134`, `[PEER-REVIEWED]` — negative correlation between base retriever strength and expansion gain; most failures are relevance dilution.
- “A Little Human Data Goes a Long Way,” ACL `2025.acl-short.30`, `[PEER-REVIEWED]` — small human cores rescue synthetic training sets; null on some datasets.

### Agent memory and case-based reasoning

- Park et al., Generative Agents, arXiv:`2304.03442` / DOI `10.1145/3586183.3606763`, `[PEER-REVIEWED]` — recency+importance+relevance retrieval; no retrieval-quality metric.
- Shinn et al., Reflexion, arXiv:`2303.11366`, NeurIPS 2023, `[PEER-REVIEWED]` — bounded verbal memory; no retrieval evaluation.
- Zhao et al., ExpeL, arXiv:`2308.10144`, AAAI 2024, `[PEER-REVIEWED]` — successful-trajectory retrieval by task similarity; only surveyed system that ablates the ranking function.
- Wang et al., Voyager, arXiv:`2305.16291`, `[PREPRINT]` — executable skill library with embedding retrieval; no retrieval metric.
- Modarressi et al., RET-LLM, arXiv:`2305.14322`, `[PREPRINT]` — concept paper; evaluated successor is MemLLM; qualitative evidence only.
- Xu et al., A-MEM, arXiv:`2502.12110`, `[PREPRINT]` — Zettelkasten notes and one-hop expansion; no retrieval metric.
- Packer et al., MemGPT, arXiv:`2310.08560`, `[PREPRINT]` — paged memory management; DMR benchmark; no retrieval P/R.
- Chhikara et al., Mem0, arXiv:`2504.19413`, `[PREPRINT]` — LOCOMO answer-level results; full context still stronger on accuracy in the paper's own table; win is cost/latency.
- Rasmussen et al., Zep, arXiv:`2501.13956`, `[PREPRINT]` — bi-temporal graph with RRF/MMR/cross-encoder rerankers; end-task evaluation only. Graphiti is software, not a separate paper.
- Aamodt & Plaza, *Case-Based Reasoning: Foundational Issues*, AI Communications 7(1):39-59, 1994, `[TECH REPORT]` — the 4R cycle; Retrieve is a separately diagnosable stage.
- Smyth, *Rapid Retrieval Algorithms for CBR*, IJCAI-89, official proceedings PDF, `[PEER-REVIEWED]` — hypercube and tree-hash case indexes; closest case may match on many less important attributes.
- *Adaptation-guided retrieval*, arXiv:`1905.12464`, `[PREPRINT]` — structural similarity AND adaptability as a conjunction, a non-compensatory retrieval precedent.

### Scoped retrieval security

- Namboothiri, Authorization-First Retrieval, ACL `2026.trustnlp-main.15`, `[PEER-REVIEWED]` — separates structural exposure from answer leakage; retrieve-then-filter exposes unauthorized context.
- Bhatt et al., arXiv:`2509.14608`, `[PREPRINT]` — deterministic participant-aware retrieval-time authorization for enterprise RAG.
- PoisonedRAG, arXiv:`2402.07867`, `[PREPRINT]` — write-side retrieval poisoning.
- Greshake et al., indirect prompt injection, arXiv:`2302.12173`, `[PREPRINT]` — retrieved third-party content is an instruction channel.
- Xu et al., *Securing RAG taxonomy*, arXiv:`2604.08304`, `[PREPRINT]` — surveys incompatible leakage units; states no shared harness exists.

### Small-set statistics

- McNemar, DOI `10.1007/BF02295996`, 1947, `[PEER-REVIEWED]` — exact paired test for binary discordant outcomes.
- Holm, *A Simple Sequentially Rejective Multiple Test Procedure*, JSTOR `4615733`, 1979, `[PEER-REVIEWED]` — step-down FWER control.
- Efron, DOI `10.1214/aos/1176344552`, 1979, `[PEER-REVIEWED]` — bootstrap foundations.
- Ihemelandu & Ekstrand, arXiv:`2305.02461` / DOI `10.1145/3539618.3592004`, `[PEER-REVIEWED]` — t and randomization tests are near nominal; bootstrap drifts at small n.
- Smucker, Allan & Carterette, DOI `10.1145/1321440.1321528`, `[PEER-REVIEWED]` — randomization/paired-t agreement and small-topic degradation.
- Sakai, *Laboratory Experiments in IR*, DOI `10.1007/978-981-13-1199-4`, `[TECH REPORT]` — practical topic-set design and power/CI-width tooling.
- Gelman & Stern, DOI `10.1198/000313006X152649`, 2006, `[PEER-REVIEWED]` — a change in significance status is not itself a significant change.
- Howard et al., arXiv:`1810.08240`, Annals of Statistics 49(2), `[PEER-REVIEWED]` — anytime-valid confidence sequences when evaluation may be peeked.
