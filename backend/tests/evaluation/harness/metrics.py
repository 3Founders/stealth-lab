"""Metric definitions shared across gold-set evaluations.

Exact definitions live in evaluation/METRICS.md; this module is the single
implementation every gold-set test calls, so a metric is defined once and
never silently drifts between e.g. the retrieval and applicability suites.

Two families:
  - ranking metrics (recall_at_k, mrr, ndcg) — take a ranked list of returned
    ids and a set of relevant ids.
  - classification metrics (precision/recall/false_accept_rate/
    false_reject_rate/abstention_accuracy) — take parallel lists of
    predicted and gold labels drawn from a fixed label set, with an
    explicit `unknown_label` for abstention-aware scoring.
"""
from __future__ import annotations

import math
from collections import Counter


def recall_at_k(ranked_ids: list[str], relevant_ids: set[str], k: int) -> float:
    """Fraction of relevant_ids appearing anywhere in ranked_ids[:k]."""
    if not relevant_ids:
        raise ValueError("relevant_ids must be non-empty")
    top_k = set(ranked_ids[:k])
    return len(top_k & relevant_ids) / len(relevant_ids)


def mrr(ranked_ids: list[str], relevant_ids: set[str]) -> float:
    """Reciprocal rank of the first relevant id, 0.0 if none found."""
    for i, rid in enumerate(ranked_ids, start=1):
        if rid in relevant_ids:
            return 1.0 / i
    return 0.0


def ndcg(ranked_ids: list[str], relevant_ids: set[str], k: int | None = None) -> float:
    """Binary-relevance nDCG@k (relevance 1 if in relevant_ids, else 0)."""
    ids = ranked_ids[:k] if k is not None else ranked_ids
    dcg = sum(
        (1.0 if rid in relevant_ids else 0.0) / math.log2(i + 1)
        for i, rid in enumerate(ids, start=1)
    )
    ideal_hits = min(len(relevant_ids), len(ids))
    idcg = sum(1.0 / math.log2(i + 1) for i in range(1, ideal_hits + 1))
    return dcg / idcg if idcg > 0 else 0.0


def false_positive_rate(ranked_ids: list[str], irrelevant_ids: set[str], k: int) -> float:
    """Fraction of top-k results that are explicitly labeled irrelevant."""
    top_k = ranked_ids[:k]
    if not top_k:
        return 0.0
    hits = sum(1 for rid in top_k if rid in irrelevant_ids)
    return hits / len(top_k)


def precision_recall(
    predicted: list[str], gold: list[str], positive_label: str
) -> tuple[float, float]:
    """Binary precision/recall for one label against parallel predicted/gold lists."""
    if len(predicted) != len(gold):
        raise ValueError("predicted and gold must be the same length")
    tp = sum(1 for p, g in zip(predicted, gold) if p == positive_label and g == positive_label)
    fp = sum(1 for p, g in zip(predicted, gold) if p == positive_label and g != positive_label)
    fn = sum(1 for p, g in zip(predicted, gold) if p != positive_label and g == positive_label)
    precision = tp / (tp + fp) if (tp + fp) else 0.0
    recall = tp / (tp + fn) if (tp + fn) else 0.0
    return precision, recall


def false_accept_rate(
    predicted: list[str], gold: list[str], accept_label: str, reject_labels: set[str]
) -> float:
    """Of cases whose gold label says reject, fraction the system accepted.

    This is the spec's headline applicability safety metric: how often a
    procedure that should have been rejected gets automatically selected.
    """
    if len(predicted) != len(gold):
        raise ValueError("predicted and gold must be the same length")
    should_reject = [(p, g) for p, g in zip(predicted, gold) if g in reject_labels]
    if not should_reject:
        return 0.0
    wrongly_accepted = sum(1 for p, g in should_reject if p == accept_label)
    return wrongly_accepted / len(should_reject)


def false_reject_rate(
    predicted: list[str], gold: list[str], accept_label: str
) -> float:
    """Of cases whose gold label says accept, fraction the system rejected."""
    if len(predicted) != len(gold):
        raise ValueError("predicted and gold must be the same length")
    should_accept = [(p, g) for p, g in zip(predicted, gold) if g == accept_label]
    if not should_accept:
        return 0.0
    wrongly_rejected = sum(1 for p, g in should_accept if p != accept_label)
    return wrongly_rejected / len(should_accept)


def abstention_accuracy(
    predicted: list[str], gold: list[str], unknown_label: str
) -> float:
    """Of cases whose gold label is genuinely unknown, fraction correctly abstained."""
    if len(predicted) != len(gold):
        raise ValueError("predicted and gold must be the same length")
    should_abstain = [p for p, g in zip(predicted, gold) if g == unknown_label]
    if not should_abstain:
        return 0.0
    correct = sum(1 for p in should_abstain if p == unknown_label)
    return correct / len(should_abstain)


def confusion_counts(predicted: list[str], gold: list[str]) -> dict[str, int]:
    """Label-pair counts, e.g. {'applicable->applicable': 4, 'unknown->stale': 1, ...}."""
    if len(predicted) != len(gold):
        raise ValueError("predicted and gold must be the same length")
    return dict(Counter(f"{g}->{p}" for p, g in zip(predicted, gold)))


def step_precision_recall(predicted_steps: list[str], gold_steps: list[str]) -> tuple[float, float]:
    """Set-based precision/recall over step descriptions (order handled separately
    by ordering_accuracy — this only measures whether the right steps are present).
    """
    pred_set, gold_set = set(predicted_steps), set(gold_steps)
    tp = len(pred_set & gold_set)
    precision = tp / len(pred_set) if pred_set else 0.0
    recall = tp / len(gold_set) if gold_set else 0.0
    return precision, recall


def ordering_accuracy(predicted_steps: list[str], gold_steps: list[str]) -> float:
    """Fraction of gold-adjacent step pairs whose relative order is preserved
    in predicted_steps (Kendall-tau-style pairwise agreement, restricted to
    steps present in both lists so missing/extra steps are scored separately
    by step_precision_recall)."""
    common = [s for s in gold_steps if s in predicted_steps]
    if len(common) < 2:
        return 1.0 if common == [s for s in predicted_steps if s in gold_steps] else 0.0
    pred_positions = {s: i for i, s in enumerate(predicted_steps)}
    agreements = 0
    total = 0
    for i in range(len(common)):
        for j in range(i + 1, len(common)):
            total += 1
            if pred_positions[common[i]] < pred_positions[common[j]]:
                agreements += 1
    return agreements / total if total else 1.0
