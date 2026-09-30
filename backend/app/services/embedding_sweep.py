"""Embed Goals and Procedures that were written without a vector.

Found 2026-09-30 on the production ingestion run: claims were embedded on write, but no Goal and no Procedure was --
Goals get a vector only when their creator passes an embedder, and Procedures were designed to be embedded later by
scripts/backfill_procedure_embeddings.py, which nothing ran. Without vectors, Goal and Procedure search is keyword-
only and the identity judge sees no semantic candidates.

Used two ways: the ingestion pipelines call it for what they just wrote, and the worker sweeps it on every
maintenance tick, so any writer that forgets is caught within minutes. The text is the canonical one the backfill
scripts use (a Goal: goal_embedding_text(name, description); a Procedure: its retrieval document), so a vector
written here is indistinguishable from one written by those scripts. Each row is updated only while it still has no
vector, and its search projection is refreshed.
"""
from __future__ import annotations

import hashlib
import json
import logging
from typing import Any, Optional, Sequence

log = logging.getLogger(__name__)

BATCH = 64


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


async def embed_goals(pool: Any, embedder: Any, *, ids: Optional[Sequence[str]] = None, limit: int = 500,
                      control: Any = None) -> int:
    """Embed live Goals that have no vector (only `ids` when given) in the database `pool`. Returns how many were
    embedded. `control` is the control database whose projection outbox is told (default: `pool` itself)."""
    control = control if control is not None else pool
    from app.services.embeddings import to_pgvector
    from app.services.goals import goal_embedding_text
    from app.services.search_projection import enqueue

    where = "t_invalid IS NULL AND embedding IS NULL"
    args: list[Any] = []
    if ids is not None:
        if not ids:
            return 0
        where += " AND id = ANY($1::uuid[])"
        args.append(list(ids))
    rows = await pool.fetch(
        f"SELECT id::text AS id, canonical_name, description FROM goals WHERE {where} ORDER BY id LIMIT {int(limit)}",
        *args)
    done = 0
    model_id, provider = embedder.embedding_model_id(), embedder._configured_provider()  # noqa: SLF001
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        texts = [goal_embedding_text(r["canonical_name"], r["description"]) for r in chunk]
        vectors = await embedder.embed(texts, input_type="document")
        for row, text, vec in zip(chunk, texts, vectors):
            if not vec or len(vec) != embedder.dimension:
                log.warning("goal %s: embedding has the wrong shape; left for the next sweep", row["id"])
                continue
            status = await pool.execute(
                "UPDATE goals SET embedding = $2::vector, embedding_model_id = $3, embedding_provider = $4, "
                "embedding_text_hash = $5 WHERE id = $1::uuid AND embedding IS NULL",
                row["id"], to_pgvector(vec), model_id, provider, _sha(text))
            if status.endswith(" 1"):
                await enqueue(control, "goal", row["id"])
                done += 1
    return done


async def embed_procedures(pool: Any, embedder: Any, *, ids: Optional[Sequence[str]] = None,
                           limit: int = 500, control: Any = None) -> int:
    """Embed live Procedure rows that have no vector (only rows whose `id` is in `ids` when given), from the
    canonical retrieval document, stamping the document and its version exactly as the backfill script does."""
    from app.services.embeddings import to_pgvector
    from app.services.retrieval_document import (
        RETRIEVAL_DOCUMENT_VERSION,
        build_procedure_retrieval_document,
        retrieval_document_sha256,
    )
    from app.services.search_projection import enqueue

    control = control if control is not None else pool
    where = "t_invalid IS NULL AND embedding IS NULL"
    args: list[Any] = []
    if ids is not None:
        if not ids:
            return 0
        where += " AND id = ANY($1::uuid[])"
        args.append(list(ids))
    rows = [dict(r) for r in await pool.fetch(
        f"SELECT * FROM procedures WHERE {where} ORDER BY t_created LIMIT {int(limit)}", *args)]
    if not rows:
        return 0
    dep_rows = await pool.fetch(
        "SELECT procedure_id::text AS procedure_id, dependency_ref FROM procedure_dependencies "
        "WHERE procedure_id = ANY($1::uuid[])", [r["procedure_id"] for r in rows])
    deps: dict[str, list[str]] = {}
    for d in dep_rows:
        deps.setdefault(d["procedure_id"], []).append(d["dependency_ref"])
    done = 0
    model_id, provider = embedder.embedding_model_id(), embedder._configured_provider()  # noqa: SLF001
    for start in range(0, len(rows), BATCH):
        chunk = rows[start:start + BATCH]
        docs = [build_procedure_retrieval_document(p, dependencies=deps.get(str(p["procedure_id"]))) for p in chunk]
        vectors = await embedder.embed(docs, input_type="document")
        for proc, doc, vec in zip(chunk, docs, vectors):
            if not vec or len(vec) != embedder.dimension:
                log.warning("procedure %s: embedding has the wrong shape; left for the next sweep", proc["id"])
                continue
            status = await pool.execute(
                "UPDATE procedures SET embedding = $2::vector, embedding_model_id = $3, embedding_dim = $4, "
                "embedding_provider = $5, embedding_input_type = 'document', embedding_text_hash = $6, "
                "retrieval_document = $7, retrieval_document_version = $8, retrieval_document_sha256 = $9, "
                "domain_payload = jsonb_set(COALESCE(domain_payload, '{}'::jsonb), '{embedding}', $10::jsonb, true), "
                "updated_at = now(), retrieval_indexed_at = now() "
                "WHERE id = $1 AND embedding IS NULL",
                proc["id"], to_pgvector(vec), model_id, embedder.dimension, provider, _sha(doc), doc,
                RETRIEVAL_DOCUMENT_VERSION, retrieval_document_sha256(doc),
                json.dumps({"provider": provider, "model_id": model_id, "dimension": embedder.dimension,
                            "input_type": "document", "text_sha256": _sha(doc)}))
            if status.endswith(" 1"):
                await enqueue(control, "procedure", str(proc["procedure_id"]))
                done += 1
    return done


async def embed_everywhere(control: Any, embedder: Any, *, goal_ids: Optional[Sequence[str]] = None,
                           procedure_row_ids: Optional[Sequence[str]] = None, limit: int = 500,
                           goals: bool = True, procedures: bool = True) -> dict:
    """`embed_goals` / `embed_procedures` on the control database AND every knowledge shard (a Goal or Procedure
    homed on a shard has its canonical row -- and so its vector -- there; docs/sharding.md). With no shard this is
    exactly the two calls on the control database."""
    from app.services.shards import all_pools

    out = {"goals": 0, "procedures": 0}
    for _shard, p in await all_pools(control):
        if goals and (goal_ids is None or goal_ids):
            out["goals"] += await embed_goals(p, embedder, ids=goal_ids, limit=limit, control=control)
        if procedures and (procedure_row_ids is None or procedure_row_ids):
            out["procedures"] += await embed_procedures(p, embedder, ids=procedure_row_ids, limit=limit,
                                                        control=control)
    return out


async def sweep(pool: Any, embedder: Any, *, limit: int = 500) -> dict:
    """Embed whatever is still missing a vector, on every knowledge database (worker maintenance)."""
    return await embed_everywhere(pool, embedder, limit=limit)
