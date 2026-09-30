"""Vectors for what an ingestion item wrote (2026-09-30: production Goals and Procedures had none).

`ingest_embedder(pool)` is the one Embedder a pipeline process uses (its batcher paces and coalesces requests).
`embed_written` embeds the Goals and Procedures an item just wrote, right away, so they are searchable by meaning
as soon as the item is recorded; a failure here never fails the item -- the worker's sweep
(app/services/embedding_sweep.py) embeds anything still missing within minutes.
"""
from __future__ import annotations

import logging
from typing import Any, Iterable, Optional

log = logging.getLogger(__name__)

_EMBEDDER: Optional[Any] = None


def ingest_embedder(pool: Any) -> Any:
    global _EMBEDDER
    if _EMBEDDER is None:
        from app.services.embeddings import Embedder

        _EMBEDDER = Embedder(rate_limit_pool=pool)
    return _EMBEDDER


async def embed_written(pool: Any, *, goal_ids: Iterable[str] = (), procedure_row_ids: Iterable[str] = (),
                        procedure_ids: Iterable[str] = ()) -> dict:
    from app.services.embedding_sweep import embed_everywhere
    from app.services.shards import all_pools

    emb = ingest_embedder(pool)
    out = {"goals": 0, "procedures": 0}
    goals = sorted({str(g) for g in goal_ids if g})
    rows = sorted({str(r) for r in procedure_row_ids if r})
    lineages = sorted({str(p) for p in procedure_ids if p})
    try:
        if lineages:
            for _shard, p in await all_pools(pool):       # a lineage's rows are on its home shard
                rows = sorted(set(rows) | {str(r["id"]) for r in await p.fetch(
                    "SELECT id FROM procedures WHERE procedure_id = ANY($1::uuid[]) AND t_invalid IS NULL", lineages)})
        if goals or rows:
            done = await embed_everywhere(pool, emb, goal_ids=goals, procedure_row_ids=rows)
            out["goals"], out["procedures"] = done["goals"], done["procedures"]
    except Exception:  # noqa: BLE001 -- the worker's sweep embeds whatever is left
        log.warning("embedding the item's Goals/Procedures failed; the worker sweep will retry", exc_info=True)
        out["error"] = True
    return out
