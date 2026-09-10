"""
G14 -- retrieval index-freshness contract (spec §B37).

There is no detached retrieval index in StealthLab: embeddings and every
authoritative column live on the `procedures` row, retrieval returns bare
ids, and every candidate is re-hydrated from its live row before ranking
and selection. So "a stale index makes an agent act on outdated text"
cannot happen here by construction.

The one real lag is the *embedding* trailing its source text (row edited,
re-embed not yet run). This module surfaces that lag from the
`procedure_index_lag` view (migration 72) plus a service-layer check of
recipe-version drift against the live `RETRIEVAL_DOCUMENT_VERSION`
constant (kept out of the view so bumping the constant needs no
migration). `scripts/backfill_procedure_embeddings.py` is the resumable
rebuild-from-canonical job; `mark_procedure_indexed` is what it calls to
clear a row's lag.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.services.retrieval_document import RETRIEVAL_DOCUMENT_VERSION

INDEX_LAG_VIEW = "procedure_index_lag"


async def mark_procedure_indexed(
    pool_or_conn: Any, procedure_row_id: str, *, conn: Optional[asyncpg.Connection] = None
) -> None:
    """Stamp `retrieval_indexed_at = now()` for one procedure row -- call
    it in the same place the embedding + retrieval_document are written."""
    target = conn or pool_or_conn
    await target.execute(
        "UPDATE procedures SET retrieval_indexed_at = now() WHERE id = $1::uuid",
        procedure_row_id,
    )


async def get_index_lag(pool: asyncpg.Pool, *, limit: int = 100) -> dict[str, Any]:
    """
    Report the retrieval-index lag. Combines the timestamp/embedding lag
    from `procedure_index_lag` with a recipe-version-drift check against
    the current `RETRIEVAL_DOCUMENT_VERSION`.

    Returns:
      {
        "current_recipe": "<RETRIEVAL_DOCUMENT_VERSION>",
        "lag_count": int,          # rows behind on timestamp/embedding
        "recipe_drift_count": int, # live rows on an older retrieval_document_version
        "total_stale": int,        # union
        "sample": [ {id, procedure_id, version, name, reason}, ... ]  # up to `limit`
      }
    """
    async with pool.acquire() as conn:
        lag_rows = await conn.fetch(
            f"SELECT id::text, procedure_id::text, version, name, reason, "
            f"retrieval_document_version "
            f"FROM {INDEX_LAG_VIEW} ORDER BY updated_at DESC LIMIT $1",
            limit,
        )
        lag_count = await conn.fetchval(f"SELECT count(*) FROM {INDEX_LAG_VIEW}")
        recipe_drift_count = await conn.fetchval(
            "SELECT count(*) FROM procedures "
            "WHERE t_invalid IS NULL "
            "  AND (retrieval_document_version IS NULL "
            "       OR retrieval_document_version <> $1)",
            RETRIEVAL_DOCUMENT_VERSION,
        )
        total_stale = await conn.fetchval(
            "SELECT count(*) FROM procedures p "
            "WHERE p.t_invalid IS NULL AND ("
            "     p.embedding IS NULL"
            "  OR p.retrieval_indexed_at IS NULL"
            "  OR p.retrieval_indexed_at < p.updated_at"
            "  OR p.retrieval_document_version IS NULL"
            "  OR p.retrieval_document_version <> $1)",
            RETRIEVAL_DOCUMENT_VERSION,
        )

    sample = []
    for r in lag_rows:
        reason = r["reason"]
        if reason == "current" and r["retrieval_document_version"] != RETRIEVAL_DOCUMENT_VERSION:
            reason = "recipe_drift"
        sample.append({
            "id": r["id"], "procedure_id": r["procedure_id"], "version": r["version"],
            "name": r["name"], "reason": reason,
        })

    return {
        "current_recipe": RETRIEVAL_DOCUMENT_VERSION,
        "lag_count": int(lag_count or 0),
        "recipe_drift_count": int(recipe_drift_count or 0),
        "total_stale": int(total_stale or 0),
        "sample": sample,
    }
