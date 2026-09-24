"""Best-effort near-duplicate scoring for contribution submissions."""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.services.access import AccessScope, TenantScope, scope_predicates
from app.services.embeddings import Embedder, to_pgvector


async def embed_submission_text(embedder: Optional[Embedder], text: str) -> Optional[list[float]]:
    if not text or not text.strip():
        return None
    try:
        embedder = embedder or Embedder()
        vector, _metadata = await embedder.embed_one_with_metadata(text, input_type="document")
        return vector
    except Exception:
        return None


def submission_dedup_text(*, name: str, rationale: Optional[str], steps: Optional[list]) -> str:
    step_text = " ".join(
        step if isinstance(step, str) else str((step or {}).get("description") or (step or {}).get("action") or step)
        for step in (steps or [])
    )
    return " ".join(part for part in (name, rationale or "", step_text) if part).strip()


def _empty_result(method: str) -> dict[str, Any]:
    return {
        "best_match_id": None,
        "best_match_kind": None,
        "score": None,
        "candidates": [],
        "method": method,
    }


def _predicates(
    scope: AccessScope,
    alias: str,
    param_index: int,
    tenant_scope: Optional[TenantScope] = None,
) -> tuple[str, list[Any], int]:
    return scope_predicates(
        scope,
        tenant_scope or TenantScope.unrestricted(),
        alias=alias,
        param_index=param_index,
    )


async def score_procedure_duplicate(
    pool: asyncpg.Pool,
    *,
    goal_id: str,
    embedding: Optional[list[float]],
    top_k: int = 3,
    scope: AccessScope = AccessScope.anonymous(),
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    if embedding is None:
        return _empty_result("skipped_no_embedding")
    scope = scope or AccessScope.anonymous()
    vector = to_pgvector(embedding)
    submission_sql, submission_params, next_index = _predicates(scope, "s", 3)
    submission_goal_sql, submission_goal_params, next_index = _predicates(
        scope, "sg", next_index
    )
    procedure_sql, procedure_params, next_index = _predicates(
        scope, "p", next_index, tenant_scope
    )
    procedure_goal_sql, procedure_goal_params, next_index = _predicates(
        scope, "pg", next_index
    )
    params = [
        vector,
        goal_id,
        *submission_params,
        *submission_goal_params,
        *procedure_params,
        *procedure_goal_params,
    ]
    limit_index = next_index
    rows = await pool.fetch(
        f"""
        SELECT s.id, 'submission' AS kind, 1 - (s.embedding <=> $1::vector) AS similarity
        FROM procedure_submissions s
        JOIN goals sg ON sg.id = s.goal_id
        WHERE s.goal_id = $2 AND s.embedding IS NOT NULL AND s.status <> 'rejected'
          AND sg.t_invalid IS NULL AND {submission_sql} AND {submission_goal_sql}
        UNION ALL
        SELECT p.id, 'procedure' AS kind, 1 - (p.embedding <=> $1::vector) AS similarity
        FROM procedures p
        JOIN solutions sol ON sol.target_table = 'procedures'
          AND (sol.target_id = p.id OR sol.target_id = p.procedure_id)
          AND sol.status = 'active'
        JOIN goals pg ON pg.id = sol.goal_id
        WHERE sol.goal_id = $2 AND p.embedding IS NOT NULL AND p.t_invalid IS NULL
          AND pg.t_invalid IS NULL AND {procedure_sql} AND {procedure_goal_sql}
        ORDER BY similarity DESC
        LIMIT ${limit_index}
        """,
        *params,
        top_k,
    )
    if not rows:
        return _empty_result("cosine")
    best = rows[0]
    return {
        "best_match_id": str(best["id"]),
        "best_match_kind": best["kind"],
        "score": float(best["similarity"]),
        "candidates": [
            {"id": str(row["id"]), "kind": row["kind"], "similarity": float(row["similarity"])}
            for row in rows
        ],
        "method": "cosine",
    }


async def score_against_parent(
    pool: asyncpg.Pool,
    *,
    parent_procedure_row_id: str,
    embedding: Optional[list[float]],
    scope: AccessScope = AccessScope.anonymous(),
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    if embedding is None:
        return {"score": None, "method": "skipped_no_embedding"}
    scope = scope or AccessScope.anonymous()
    visibility_sql, visibility_params, _ = _predicates(
        scope, "p", 3, tenant_scope
    )
    similarity = await pool.fetchval(
        f"SELECT 1 - (p.embedding <=> $1::vector) FROM procedures p "
        f"WHERE p.id = $2 AND p.embedding IS NOT NULL AND p.t_invalid IS NULL AND {visibility_sql}",
        to_pgvector(embedding),
        parent_procedure_row_id,
        *visibility_params,
    )
    if similarity is None:
        return {"score": None, "method": "parent_has_no_embedding"}
    return {"score": float(similarity), "method": "cosine_vs_parent"}


async def score_benchmark_duplicate(
    pool: asyncpg.Pool,
    *,
    goal_id: str,
    embedding: Optional[list[float]],
    top_k: int = 3,
    scope: AccessScope = AccessScope.anonymous(),
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    if embedding is None:
        return _empty_result("skipped_no_embedding")
    scope = scope or AccessScope.anonymous()
    submission_sql, submission_params, next_index = _predicates(scope, "s", 3)
    goal_sql, goal_params, next_index = _predicates(scope, "g", next_index)
    params = [to_pgvector(embedding), goal_id, *submission_params, *goal_params]
    limit_index = next_index
    rows = await pool.fetch(
        f"""
        SELECT s.id, 'submission' AS kind, 1 - (s.embedding <=> $1::vector) AS similarity
        FROM benchmark_submissions s
        JOIN goals g ON g.id = s.goal_id
        WHERE s.goal_id = $2 AND s.embedding IS NOT NULL AND s.status <> 'rejected'
          AND g.t_invalid IS NULL AND {submission_sql} AND {goal_sql}
        ORDER BY similarity DESC
        LIMIT ${limit_index}
        """,
        *params,
        top_k,
    )
    if not rows:
        return _empty_result("cosine")
    best = rows[0]
    return {
        "best_match_id": str(best["id"]),
        "best_match_kind": best["kind"],
        "score": float(best["similarity"]),
        "candidates": [
            {"id": str(row["id"]), "kind": row["kind"], "similarity": float(row["similarity"])}
            for row in rows
        ],
        "method": "cosine",
    }
