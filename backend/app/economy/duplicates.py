"""
Near-duplicate scoring for a new contribution submission (§10 anti-gaming,
§3 Layer 1). Reuses the exact pattern app/services/dedup.py already
established (pgvector cosine similarity via the `<=>` operator, embeddings
built through app.services.embeddings.Embedder) -- this module does not add
a second similarity engine, it applies the existing one to submissions.

Best-effort throughout: an embedding failure (provider down, no API key
configured) must never block a submission -- it degrades to "no duplicate
score available", never a fabricated one.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.services.embeddings import Embedder, to_pgvector


async def embed_submission_text(embedder: Optional[Embedder], text: str) -> Optional[list[float]]:
    """Best-effort embedding for a submission's dedup text. None on any failure."""
    if not text or not text.strip():
        return None
    try:
        e = embedder or Embedder()
        vec, _meta = await e.embed_one_with_metadata(text, input_type="document")
        return vec
    except Exception:
        return None


def submission_dedup_text(*, name: str, rationale: Optional[str], steps: Optional[list]) -> str:
    step_text = " ".join(
        s if isinstance(s, str) else str((s or {}).get("description") or (s or {}).get("action") or s)
        for s in (steps or [])
    )
    return " ".join(part for part in (name, rationale or "", step_text) if part).strip()


async def score_procedure_duplicate(
    pool: asyncpg.Pool, *, goal_id: str, embedding: Optional[list[float]], top_k: int = 3,
) -> dict[str, Any]:
    """
    Best matching existing Procedure submission or accepted Procedure for
    the SAME goal, by cosine similarity. Returns
    {"best_match_id", "best_match_kind", "score", "candidates"} -- score is
    None (not 0.0) when nothing could be compared, so a caller never reads
    "no evidence of duplication" as "confirmed original."
    """
    if embedding is None:
        return {"best_match_id": None, "best_match_kind": None, "score": None, "candidates": [], "method": "skipped_no_embedding"}
    vec = to_pgvector(embedding)
    rows = await pool.fetch(
        """
        SELECT id, 'submission' AS kind, 1 - (embedding <=> $1::vector) AS similarity
        FROM procedure_submissions
        WHERE goal_id = $2 AND embedding IS NOT NULL AND status <> 'rejected'
        UNION ALL
        SELECT p.id, 'procedure' AS kind, 1 - (p.embedding <=> $1::vector) AS similarity
        FROM procedures p
        JOIN solutions s ON s.target_id = p.id AND s.target_table = 'procedures'
        WHERE s.goal_id = $2 AND p.embedding IS NOT NULL AND p.t_invalid IS NULL
        ORDER BY similarity DESC
        LIMIT $3
        """,
        vec, goal_id, top_k,
    )
    if not rows:
        return {"best_match_id": None, "best_match_kind": None, "score": None, "candidates": [], "method": "cosine"}
    best = rows[0]
    return {
        "best_match_id": str(best["id"]),
        "best_match_kind": best["kind"],
        "score": float(best["similarity"]),
        "candidates": [{"id": str(r["id"]), "kind": r["kind"], "similarity": float(r["similarity"])} for r in rows],
        "method": "cosine",
    }


async def score_against_parent(
    pool: asyncpg.Pool, *, parent_procedure_row_id: str, embedding: Optional[list[float]],
) -> dict[str, Any]:
    """
    §3 of the harden+consolidate directive: "is this actually a meaningful
    improvement over the DECLARED PARENT" -- a different, narrower question
    than score_procedure_duplicate's corpus-wide scan. An improvement
    submission is compared specifically against the one procedure it
    claims to improve, not against whichever sibling happens to be
    nearest in the whole goal's corpus (which could miss a near-identical
    rewrite of the parent if something else in the corpus is even closer,
    or flag a false positive against an unrelated sibling).

    Returns {"score": float|None, "method": str}. `score` is None when
    either side has no embedding to compare -- never fabricated as 0
    ("definitely different") or 1 ("definitely identical").
    """
    if embedding is None:
        return {"score": None, "method": "skipped_no_embedding"}
    vec = to_pgvector(embedding)
    similarity = await pool.fetchval(
        "SELECT 1 - (embedding <=> $1::vector) FROM procedures WHERE id = $2 AND embedding IS NOT NULL",
        vec, parent_procedure_row_id,
    )
    if similarity is None:
        return {"score": None, "method": "parent_has_no_embedding"}
    return {"score": float(similarity), "method": "cosine_vs_parent"}


async def score_benchmark_duplicate(
    pool: asyncpg.Pool, *, goal_id: str, embedding: Optional[list[float]], top_k: int = 3,
) -> dict[str, Any]:
    """Same pattern as score_procedure_duplicate, scoped to Benchmark submissions + accepted Benchmarks."""
    if embedding is None:
        return {"best_match_id": None, "best_match_kind": None, "score": None, "candidates": [], "method": "skipped_no_embedding"}
    vec = to_pgvector(embedding)
    rows = await pool.fetch(
        """
        SELECT id, 'submission' AS kind, 1 - (embedding <=> $1::vector) AS similarity
        FROM benchmark_submissions
        WHERE goal_id = $2 AND embedding IS NOT NULL AND status <> 'rejected'
        ORDER BY similarity DESC
        LIMIT $3
        """,
        vec, goal_id, top_k,
    )
    if not rows:
        return {"best_match_id": None, "best_match_kind": None, "score": None, "candidates": [], "method": "cosine"}
    best = rows[0]
    return {
        "best_match_id": str(best["id"]),
        "best_match_kind": best["kind"],
        "score": float(best["similarity"]),
        "candidates": [{"id": str(r["id"]), "kind": r["kind"], "similarity": float(r["similarity"])} for r in rows],
        "method": "cosine",
    }
