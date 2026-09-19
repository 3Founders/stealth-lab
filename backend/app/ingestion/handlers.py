"""Canonical bundle handler: the ONE write path for already-extracted knowledge.

Adapters that do their own extraction (skill packages, trajectories, repo
analysers, third-party pipelines) emit *candidate bundles*; this handler turns a
bundle into canonical objects, always through the same steps:

    source registration + exact source dedup   (procedures.source_key, ingestion_contexts)
    Goal identity        (exact | FTS+vector -> JEV/NLI judge, durable decision)
    Procedure identity   (goal-constrained candidates -> judge: same / refinement / distinct)
    Claims               (exact-statement identity in scope; provenance = ingestion context + source_key)
    shard routing        (goal home shard; Procedure co-located)
    canonical persistence
    projection           (DB trigger -> outbox; the worker drains it)
    optional GoalRelation (proposed edges from judged goal relations)

Payload (job_type ``ingest_candidate_bundle``)::

    {"source_key": "<stable id of the source content>", "source_uri": "...",
     "goal": "...", "procedure": {"name": "...", "steps": [...]},
     "claims": [{"statement": "..."}], "provenance": "prior_library"}

Scope / owner / visibility come from the JOB (``payload["_job"]``), never from
the payload: a worker cannot be tricked into publishing private knowledge.
The whole bundle runs under one advisory lock keyed by ``source_key``, so
duplicate delivery and racing workers serialise instead of duplicating.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import asyncpg

from app.services.ingestion_jobs import JOB_HANDLERS

log = logging.getLogger(__name__)
JOB_TYPE = "ingest_candidate_bundle"


class Dependencies:
    """Injectable model seams (tests inject frozen fakes; production builds from settings)."""

    embedder: Any = None
    judge: Any = None

    @classmethod
    def configure(cls, *, embedder: Any = None, judge: Any = None) -> None:
        cls.embedder, cls.judge = embedder, judge

    @classmethod
    def get_embedder(cls, pool: asyncpg.Pool) -> Any:
        if cls.embedder is None:
            from app.services.embeddings import Embedder

            cls.embedder = Embedder(rate_limit_pool=pool)
        return cls.embedder

    @classmethod
    def get_judge(cls) -> Any:
        if cls.judge is None:
            from app.services.identity_resolution import default_judge

            cls.judge = default_judge()
        return cls.judge


def _norm(s: str) -> str:
    return " ".join(s.lower().split())


async def handle_ingest_candidate_bundle(pool: asyncpg.Pool, payload: dict) -> dict:
    from app.services.procedure_identity import ingest_procedure

    job = payload.get("_job") or {}
    scope_type = job.get("scope_type")
    if not scope_type:
        raise ValueError("bundle job has no scope_type: explicit scope is required")   # permanent
    visibility = job.get("visibility") or "public"
    owner_id = job.get("owner_id")
    scope_entity_id = job.get("scope_entity_id")
    source_key = payload["source_key"]
    provenance = payload.get("provenance") or "prior_library"
    proc = payload["procedure"]
    embedder, judge = Dependencies.get_embedder(pool), Dependencies.get_judge()

    result: dict[str, Any] = {}
    async with pool.acquire() as lock:
        async with lock.transaction():
            await lock.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"ingest:{source_key}")

            # source registration (idempotent per source_key): the ingestion context is the provenance anchor
            from app.services.ingestion_context import open_ingestion_context

            ctx_uri = payload.get("source_uri") or f"bundle:{source_key}"
            ctx_id = await pool.fetchval(
                "SELECT id FROM ingestion_contexts WHERE source_uri = $1 AND extractor_id = 'candidate_bundle' "
                "ORDER BY started_at DESC LIMIT 1", ctx_uri)
            if ctx_id is None:
                ctx_id = await open_ingestion_context(
                    pool, source_type="bundle", source_uri=ctx_uri, source_hash=source_key,
                    extractor_id="candidate_bundle", extractor_version="v1", actor_id="ingestion_worker",
                    scope_type=scope_type, scope_entity_id=scope_entity_id or (source_key if scope_type != "global" else None),
                    classification="PUBLIC_DOCUMENT" if visibility == "public" else "PRIVATE_DOCUMENT",
                    visibility=visibility, owner_id=owner_id)

            r = await ingest_procedure(
                pool, source_key=source_key, name=proc["name"], goal=payload["goal"], steps=proc.get("steps") or [],
                provenance=provenance, scope_type=scope_type, scope_entity_id=scope_entity_id, owner_id=owner_id,
                visibility=visibility, embedder=embedder, judge=judge, job_id=job.get("id"))
            result["procedure"] = r

            from app.services.claims import capture_claim

            claim_ids = []
            for c in payload.get("claims") or []:
                stmt = c["statement"].strip()
                existing = await pool.fetchval(
                    "SELECT id FROM knowledge_nodes WHERE node_type = 'claim' AND t_invalid IS NULL "
                    "AND lower(regexp_replace(name, '\\s+', ' ', 'g')) = $1 AND scope_type IS NOT DISTINCT FROM $2 "
                    "AND scope_entity_id IS NOT DISTINCT FROM $3", _norm(stmt), scope_type, scope_entity_id)
                if existing:
                    claim_ids.append(str(existing))
                    continue
                cid = await capture_claim(
                    pool, statement=stmt, task_ids=[], created_by="ingestion_worker", owner_id=owner_id,
                    visibility=visibility if visibility in ("public", "private") else "public",
                    scope_type=scope_type, scope_entity_id=scope_entity_id, source_ref=source_key,
                    ingestion_context_id=str(ctx_id), embedder=embedder,
                    properties={"goal_id": r["goal_id"]})
                if cid:
                    claim_ids.append(str(cid))
            result["claims"] = claim_ids
    return result


JOB_HANDLERS[JOB_TYPE] = handle_ingest_candidate_bundle
