"""
Deterministic reuse detection for decomposition (see the roadmap item
"Workbench: fix task/decomposition reuse").

The problem this replaces: decomposition previously asked the model,
in prose, to notice an existing step and avoid duplicating it. That
depends on the model's per-call judgment and whether retrieval's top-5
happened to surface the right node -- neither is guaranteed consistent
between two calls with the identical input. This module moves the
decision out of the model's hands into a real, thresholded number
computed the same way every time.

Two tiers, deliberately different in what they guarantee:

  - Vector similarity (real cosine similarity via pgvector's `<=>`
    operator, NOT the RRF-fused rank position HybridRetriever uses for
    general retrieval -- a rank position tells you "most relevant of
    what was retrieved," not "how similar," and reuse needs the latter,
    an actual number with a real threshold). Requires the problem text
    and the candidate nodes to both have real embeddings.
  - Lexical overlap fallback, when no embedder is available or a node
    has no embedding yet (common in this project's own testing --
    Voyage is frequently unconfigured). A real, computed word-overlap
    ratio, not a semantic measure, and documented as an approximation,
    but still a genuine, repeatable number rather than a model's mood.

Thresholds (0.90 full-match, 0.70 partial) are stated defaults, not
scientifically derived -- expect to tune them against real usage.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Optional

import asyncpg

from app.services.access import AccessScope, visibility_predicate
from app.services.embeddings import Embedder, to_pgvector

FULL_MATCH_THRESHOLD = 0.90
PARTIAL_MATCH_THRESHOLD = 0.70

# Jaccard word-overlap lives on a fundamentally different numeric scale
# than cosine similarity -- realistic paraphrases rarely share more than
# a third of their words, while near-duplicate embeddings routinely
# score above 0.9. Reusing the vector thresholds for lexical overlap
# made the fallback nearly impossible to trigger for realistic input --
# caught by testing with a real paraphrase, not just a mocked score.
LEXICAL_FULL_MATCH_THRESHOLD = 0.55
LEXICAL_PARTIAL_MATCH_THRESHOLD = 0.25

_WORD_RE = re.compile(r"[a-z0-9]+")


@dataclass
class ReusableNode:
    id: str
    table: str  # 'task_nodes' | 'knowledge_nodes'
    name: str
    description: str
    similarity: float
    method: str  # 'vector' | 'lexical'

    @property
    def is_full_match(self) -> bool:
        threshold = FULL_MATCH_THRESHOLD if self.method == "vector" else LEXICAL_FULL_MATCH_THRESHOLD
        return self.similarity >= threshold


def _tokenize(text: str) -> set[str]:
    return set(_WORD_RE.findall(text.lower()))


def _lexical_overlap(a: str, b: str) -> float:
    """
    Jaccard overlap of word sets. A real, computed, repeatable number --
    not a semantic similarity, an approximation of one, stated as such.
    """
    tokens_a, tokens_b = _tokenize(a), _tokenize(b)
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    return len(intersection) / len(union)


async def _vector_candidates(
    pool: asyncpg.Pool, query_vec: list[float], scope: AccessScope, limit: int = 5
) -> list[ReusableNode]:
    vis_sql, vis_params = visibility_predicate(scope, param_index=3)
    vec = to_pgvector(query_vec)
    results: list[ReusableNode] = []
    for table, name_expr in (
        ("task_nodes", "name || ' ' || COALESCE(description, '')"),
        ("knowledge_nodes", "name"),
    ):
        rows = await pool.fetch(
            f"SELECT id, name, {name_expr} AS full_text, "
            f"1 - (embedding <=> $1::vector) AS similarity "
            f"FROM {table} "
            f"WHERE t_invalid IS NULL AND {vis_sql} AND embedding IS NOT NULL "
            f"ORDER BY similarity DESC LIMIT $2",
            vec, limit, *vis_params,
        )
        for r in rows:
            results.append(ReusableNode(
                id=str(r["id"]), table=table, name=r["name"],
                description=r["full_text"], similarity=float(r["similarity"]),
                method="vector",
            ))
    return sorted(results, key=lambda n: n.similarity, reverse=True)


async def _lexical_candidates(
    pool: asyncpg.Pool, problem: str, scope: AccessScope, limit: int = 5
) -> list[ReusableNode]:
    vis_sql, vis_params = visibility_predicate(scope, param_index=1)
    results: list[ReusableNode] = []
    for table, name_expr in (
        ("task_nodes", "name || ' ' || COALESCE(description, '')"),
        ("knowledge_nodes", "name"),
    ):
        rows = await pool.fetch(
            f"SELECT id, name, {name_expr} AS full_text FROM {table} "
            f"WHERE t_invalid IS NULL AND {vis_sql} LIMIT 200",
            *vis_params,
        )
        for r in rows:
            score = _lexical_overlap(problem, r["full_text"])
            if score > 0:
                results.append(ReusableNode(
                    id=str(r["id"]), table=table, name=r["name"],
                    description=r["full_text"], similarity=score, method="lexical",
                ))
    results.sort(key=lambda n: n.similarity, reverse=True)
    return results[:limit]


async def find_reusable_nodes(
    pool: asyncpg.Pool,
    problem: str,
    scope: Optional[AccessScope] = None,
    embedder: Optional[Embedder] = None,
) -> list[ReusableNode]:
    """
    Returns candidates above PARTIAL_MATCH_THRESHOLD, highest similarity
    first. Empty list is a real, valid answer -- nothing existing covers
    this problem closely enough to matter, decompose it as new.

    Vector search is tried first; falls back to lexical only on a real
    failure (no key, provider outage) or when nothing has an embedding
    at all, logged either way, not silently substituted.
    """
    scope = scope or AccessScope.anonymous()

    try:
        embedder = embedder or Embedder()
        query_vec = await embedder.embed_one(problem, input_type="query")
        candidates = await _vector_candidates(pool, query_vec, scope)
        if candidates:
            return [c for c in candidates if c.similarity >= PARTIAL_MATCH_THRESHOLD]
    except Exception:  # noqa: BLE001
        import logging
        logging.getLogger(__name__).error(
            "vector reuse-check unavailable, falling back to lexical overlap", exc_info=True
        )

    lexical = await _lexical_candidates(pool, problem, scope)
    return [c for c in lexical if c.similarity >= LEXICAL_PARTIAL_MATCH_THRESHOLD]
