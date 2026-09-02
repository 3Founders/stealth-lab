"""Gold-set evaluation for retrieval (spec sections 10-11).

Two things live here, deliberately kept apart:

1. `fusion_cases.json` against the REAL `app.services.retrieval.fuse_rrf` --
   the only offline-testable slice of retrieval.py's ranking logic.
   `HybridRetriever._vector_search`/`_lexical_search` are raw SQL against a
   live Postgres (pgvector cosine distance, ts_rank) with no pure-Python
   path, so genuine paraphrase-quality Recall@k/MRR/nDCG against real
   embeddings needs a DATABASE_URL-gated e2e companion this pass does not
   include -- tracked as a documented limitation in evaluation/README.md,
   not faked here by hand-picking hit-list ranks to match wording.

2. A dedicated safety test combining two REAL production functions --
   `fuse_rrf` and `app.services.applicability.check_hard_constraints` --
   proving spec section 11's headline assertion: a verified applicable
   procedure must not be displaced by a locally-close unverified candidate.
   This is a genuine gap the pre-suite audit found (retrieval.py's own
   ranking has no verification-state awareness at all -- score is pure RRF
   rank fusion plus small recency/call-graph boosts); the property only
   holds at the combined retrieval+applicability level, via
   `require_verified=True`'s cascade, which this test proves directly
   rather than asserting on retrieval.py alone (where it would legitimately
   fail, since trust-awareness is not retrieval.py's job).
"""
from __future__ import annotations

import asyncio
from pathlib import Path

from app.services.applicability import check_hard_constraints
from app.services.retrieval import fuse_rrf
from tests.evaluation.harness import metrics
from tests.evaluation.harness.gold_runner import load_gold_set, run_gold_set, success_rate
from tests.test_applicability_hard_constraints_offline import FakePool, _procedure

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "gold_retrieval"


def _run(coro):
    return asyncio.run(coro)


def _fused_ranking(case: dict) -> list[str]:
    semantic_hits = [tuple(h) for h in case["semantic_hits"]]
    lexical_hits = [tuple(h) for h in case["lexical_hits"]]
    scores, _matched = fuse_rrf([(semantic_hits, "semantic"), (lexical_hits, "keyword")])
    ranked = sorted(scores.items(), key=lambda kv: kv[1], reverse=True)
    return [key[0] for key, _score in ranked]


def _run_fusion_case(case: dict) -> dict:
    ranked_ids = _fused_ranking(case)
    relevant = set(case["relevant_ids"])
    irrelevant = set(case.get("irrelevant_ids", []))
    k = case.get("k", 5)

    recall = metrics.recall_at_k(ranked_ids, relevant, k) if relevant else 1.0
    mrr_score = metrics.mrr(ranked_ids, relevant) if relevant else 1.0
    ndcg_score = metrics.ndcg(ranked_ids, relevant, k) if relevant else 1.0
    fpr = metrics.false_positive_rate(ranked_ids, irrelevant, k) if irrelevant else 0.0

    min_recall = case.get("min_recall_at_k", 1.0)
    max_fpr = case.get("max_false_positive_rate_at_k", 1.0)
    success = recall >= min_recall and fpr <= max_fpr

    return {
        "success": success,
        "failure_reason": None if success else (
            f"recall@{k}={recall:.2f} (need >= {min_recall}), "
            f"fpr@{k}={fpr:.2f} (need <= {max_fpr}), ranked={ranked_ids}"
        ),
        "metrics": {
            "recall_at_k": recall, "mrr": mrr_score, "ndcg": ndcg_score,
            "false_positive_rate": fpr, "k": k,
        },
    }


def test_fusion_gold_set_recall_and_ranking_safety():
    cases = load_gold_set(FIXTURES / "fusion_cases.json")
    results = run_gold_set("gold_retrieval_fusion", cases, _run_fusion_case)

    failures = [r for r in results if not r.success]
    assert not failures, "\n".join(f"{r.scenario_id}: {r.failure_reason}" for r in failures)
    assert success_rate(results) == 1.0

    avg_recall = sum(r.metrics["recall_at_k"] for r in results) / len(results)
    avg_mrr = sum(r.metrics["mrr"] for r in results) / len(results)
    avg_ndcg = sum(r.metrics["ndcg"] for r in results) / len(results)
    print(
        f"\ngold_retrieval fusion: {len(results)} cases, "
        f"avg recall@k={avg_recall:.3f} mrr={avg_mrr:.3f} ndcg={avg_ndcg:.3f}"
    )


def test_verified_procedure_not_displaced_by_unverified_locally_close_candidate():
    """Spec section 11's headline assertion, proven against real code.

    Step 1 reproduces the risk with the real fusion function: an unverified
    candidate that is genuinely closer (both signals rank it first) beats a
    verified procedure on raw retrieval score alone.

    Step 2 proves the real applicability cascade closes that gap: with
    require_verified=True (applicability.py's own documented default "for
    anything doing automatic candidate selection"), the unverified
    candidate is disqualified regardless of its retrieval rank, and the
    verified procedure remains applicable.
    """
    semantic_hits = [("candidate-quick-fix", "task_nodes", 0), ("verified-safe-fix", "task_nodes", 1)]
    lexical_hits = [("candidate-quick-fix", "task_nodes", 0), ("verified-safe-fix", "task_nodes", 1)]
    scores, _matched = fuse_rrf([(semantic_hits, "semantic"), (lexical_hits, "keyword")])
    ranked_ids = [key[0] for key, _ in sorted(scores.items(), key=lambda kv: kv[1], reverse=True)]
    assert ranked_ids[0] == "candidate-quick-fix", (
        "test setup must reproduce the real risk first: raw fusion ranks the "
        "unverified candidate above the verified procedure"
    )

    candidate = _procedure(proc_id="candidate-quick-fix", verification_state="proposed")
    verified = _procedure(proc_id="verified-safe-fix", verification_state="verified")

    candidate_result = _run(check_hard_constraints(FakePool(), candidate))
    verified_result = _run(check_hard_constraints(FakePool(), verified))

    assert candidate_result.applicable is False
    assert candidate_result.failed_constraints == ["verification_state"]
    assert verified_result.applicable is True
