"""Canonical bundle handler: the ONE write path for already-extracted knowledge.

Adapters that do their own extraction (skill packages, trajectories, repo
analysers, third-party pipelines) emit *candidate bundles*; this handler turns a
bundle into canonical objects, always through the same steps:

    source registration + exact source dedup   (procedures.source_key, ingestion_contexts)
    Goal identity        (exact | FTS+vector -> JEV/NLI judge, durable decision)
    Procedure identity   (goal-constrained candidates -> judge: same / refinement / distinct)
    Claims               (claim_identity.ingest_claim: exact | FTS+ANN -> judge same/contradicts/…; provenance kept)
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

            from app.services.source_locators import procedure_locator_from_source

            # provenance pointer for the procedure (adapter-supplied, else derived from the source itself);
            # steps must each carry a locator (their own, or the procedure's marked as inherited)
            proc_locator = proc.get("source_locator") or procedure_locator_from_source(
                source_key=source_key, uri=payload.get("source_uri"), content_hash=payload.get("content_hash"),
                object_locator=(payload.get("raw_object") or {}).get("locator") if isinstance(payload.get("raw_object"), dict) else None)
            r = await ingest_procedure(
                pool, source_key=source_key, name=proc["name"], goal=payload["goal"], steps=proc.get("steps") or [],
                provenance=provenance, scope_type=scope_type, scope_entity_id=scope_entity_id, owner_id=owner_id,
                visibility=visibility, embedder=embedder, judge=judge, job_id=job.get("id"),
                source_locator=proc_locator, require_source_locators=True)
            result["procedure"] = r

            from app.services.claim_identity import ingest_claim

            goal_shard = await pool.fetchval(
                "SELECT home_shard_id FROM object_routes WHERE object_type = 'goal' AND object_id = $1::uuid", r["goal_id"])
            claim_ids = []
            claim_results = []
            for c in payload.get("claims") or []:
                cr = await ingest_claim(
                    pool, statement=c["statement"], scope_type=scope_type, scope_entity_id=scope_entity_id, visibility=visibility,
                    owner_id=owner_id, source_key=source_key, source_ref=source_key, ingestion_context_id=str(ctx_id),
                    goal_id=r["goal_id"], goal_home_shard=goal_shard, embedder=embedder, judge=judge, job_id=job.get("id"))
                claim_ids.append(cr["claim_id"])
                claim_results.append(cr)
            result["claim_results"] = claim_results
            result["claims"] = claim_ids
    return result


JOB_HANDLERS[JOB_TYPE] = handle_ingest_candidate_bundle
