# DS-1000 round 3: do the knowledge-side improvements help? (confirmation, preregistered)

This file is written, and hashed with the round-3 sample (`runs3/design.sha256`), **before any round-3 model run**. The mechanism under test is on `main`'s working tree behind flags ([docs/knowledge_side_improvements.md](../../docs/knowledge_side_improvements.md), changes 1–5 and 7):
- `KNOWLEDGE_VERIFIED_EXAMPLES` (changes 1–2);
- `KNOWLEDGE_RELATED_EXAMPLES` (changes 3–4, limit 3, drop threshold 0.8);
- `KNOWLEDGE_STRICT_CANDIDATE_WAYS` (change 5);
- judge batch fixes (change 7, always on).

Everything not stated here is as in [PREREGISTRATION.md](PREREGISTRATION.md) (grading, prompts, models, safety, isolation, statistics).

## Hypothesis

With the improvements on, the knowledge `find_ways` returns makes models solve related tasks more often than no knowledge, and at least as well as plain nearest-example retrieval.

## Sample (`design.py --round 3 --transfer 30 --variants 0`)

- Seed `ds1000-kel-v3`.
- **Families:** only families never used in rounds 1 or 2, in any role.
- **Fit:** the originals of the 30 unused families that have variants.
- **Test:** **all** eligible variants of those families (transfer, about 82), plus the 7 remaining unused originals without variants (control).
- **Why all variants:** capping at 2 per family would leave 54 tasks. Variants of one family are correlated, which the family-clustered bootstrap already handles.

## Knowledge base (production-like, fresh database `kel_ds1000_r3`, flags ON while it is built)

- **Problems:** all fit problems of rounds 1, 2 and 3 (150). Earlier rounds' fit attempts are reused; round-3 fit problems are attempted by all 4 models.
- **Import:** embeddings on; `judge_mode="model"`; no hard-coded domain edges. The real ingestion `Worker` then runs `goal_abstraction_placement` and the other jobs it enqueues.
- **Extraction:** `extract_procedure` with up to 5 attempts (the production queue's `max_attempts`). This stores `procedures.verified_example` through the product path.
- **Evidence:** validation runs, then outcomes recorded as evidence. The recommender is refit once over everything.
- **Freeze:** after this, no product code, prompt or parameter changes.

## Queries

- **Arms B and K:** the agent-written request, one sentence written by `gemma-4-31B-it` at temperature 0 from the problem text, the same for both arms.
- **Arm E:** the raw problem text, as in rounds 1–2.
- **Planner policy (unchanged):** resolved → the Procedure; ambiguous → ways on candidates; else re-ask "<candidate name>: <request>" up to 3 times.

## Arms (test)

| Arm | Notes | Models |
|---|---|---|
| A | none | 3 open + Sonnet |
| B | `find_ways` with **flags OFF** (today's product): Procedure only | 3 open |
| **K** | `find_ways` with **flags ON**: Procedure + `verified_example`, plus `related_examples` (up to 3) from the first `find_ways` call | 3 open + Sonnet |
| E | plain RAG: BM25 top-1 over all 150 fit problems with a verified solution, with its code | 3 open |

**Rendering K:**
- Any Procedure the planner selects is shown as in B, followed by its verified solution.
- Then "Similar solved problems (NOT verified to apply; adapt):", listing each related example's task (first 1,200 characters) and code, in `find_ways` order.
- Arms with no notes reuse arm A's graded attempt.

## Analysis

- **Primary:** transfer tasks, 3 open models pooled; Δ = solve rate(K) − solve rate(A).
  - Family-clustered bootstrap 95% CI (10,000 resamples) and exact McNemar.
  - **Confirmed if the CI excludes 0 and p < 0.05.**
- **Per model:** K − A with Holm correction, reported.
- **Secondary** (no correction):
  1. pooled K − E (reported with CI; "at least as good" means the CI's lower bound is above −3 points);
  2. K − B;
  3. K − A on **non-Surface variants only** (Semantic plus Difficult-Rewrite; guards against near-copy answers);
  4. by variant type;
  5. controls: K − A, and tasks lost in K that A solved (all tasks);
  6. Sonnet K − A;
  7. retrieval: coverage and own-family precision of K's notes (Procedures and related examples);
  8. routing with K attempts vs routing with A attempts vs Sonnet-A, at targets 0.5–0.9.
- **Power:** about 82 transfer × 3 models = about 246 pairs. The pooled analysis detects roughly 7–8 points; per model, roughly 13.

## Deviations

(filled in during and after the run)
1. **Routing on tasks without a Kel Procedure (analysis detail, fixed before any test run).** Round 3's knowledge base has no library-level domain Goals (production placement does not create them). For secondary 8, a task with no Kel Procedure is routed on one generic Goal ("Unclassified Python data-science task") created at analysis time with no data, so the recommender answers from its population prior, as production does for an unseen Goal.

## Result against the preregistered rule

**Primary, pooled K − A, transfer (246 pairs): +3.3 points [−3.4, +10.2], McNemar p = 0.27 → NOT CONFIRMED.**

Per model (Holm): gemma +1.2 (p 1.0), gpt-oss +1.2 (p 1.0), deepseek +7.3 (p 0.54).

Secondary results:

| Comparison | Change | 95% CI | p |
|---|---|---|---|
| K − E | 0.0 | [−5.0, +5.2] | 1.0 |
| K − B | +0.4 | [−4.9, +6.1] | 1.0 |
| B − A | +2.8 | [−1.6, +8.1] | 0.14 |
| E − A | +3.3 | [−3.4, +10.0] | 0.31 |
| K − A, non-Surface variants only | +4.8 | [−3.1, +13.0] | 0.15 |
| Controls K − A | −4.8 | [−14.3, 0.0] | 1.0 |
| Sonnet K − A (transfer) | −3.7 | [−13.9, +5.8] | 0.58 |

- **Across all 89 tasks:** 5–7 lost and 7–11 gained per model.
- **Retrieval:** K gave notes on 66 of 82 related tasks (33 with a note from the task's own family), against B 46/82 (30) and E 82/82 (44). K also gave notes on 5 of 7 controls.
- **Routing only, target 0.5:** 83.1% solved at $0.045, against Sonnet alone at 80.9% and $0.106.
- **K plus routing, target 0.5:** 78.7% at $0.067. Longer prompts, no accuracy gain.
