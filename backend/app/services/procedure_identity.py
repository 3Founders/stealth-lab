"""
Canonical Procedure ingestion with goal-constrained, model-decided identity
(docs/dedup_and_identity.md, "Procedure").

    candidate procedure (from ANY adapter)
      -> exact source dedup            (procedures.source_key unique index; retry/race safe)
      -> resolve Goal                  (goals.find_or_create_goal: exact | FTS+vector -> judge)
      -> candidates WITHIN that Goal   (FTS + ANN over the goal's live procedures only)
      -> judge kind=procedure          same | refinement | distinct
             same       -> reuse the existing Procedure; attach this source as provenance
             refinement -> supersede_procedure (new VERSION of the same procedure_id)
             distinct   -> new Procedure linked directly to the SAME goal_id
      -> durable identity_decisions row (idempotent per source_key)

Different methods for one Goal stay separate Procedures; the same method from a
different source is never duplicated.
"""
from __future__ import annotations

import logging
from typing import Any, Optional

import asyncpg

from app.services.embeddings import to_pgvector
from app.services.identity_resolution import (
    Candidate, default_judge, fts_or_query, record_decision, rrf_fuse, SAME_MIN_CONFIDENCE,
)
from app.services.semantic.chain import SemanticJudge
from app.services.semantic.errors import SemanticJudgmentUnavailable

log = logging.getLogger(__name__)


def procedure_text(name: str, goal: str, steps: Optional[list]) -> str:
    parts = []
    for s in steps or []:
        parts.append(s if isinstance(s, str) else str(s.get("description") or s.get("text") or s.get("name") or ""))
    return f"{name}: {goal}. Steps: " + "; ".join(p for p in parts if p)[:1200]


async def _candidates(pool: asyncpg.Pool, goal_id: str, text: str, embedding: Optional[list[float]], model: Optional[str],
                      k: int = 8) -> list[Candidate]:
    by: dict[str, Candidate] = {}
    fts_ids: list[str] = []
    vec_ids: list[str] = []
    q = fts_or_query(text)
    base = "achieves_goal_id = $1::uuid AND t_invalid IS NULL AND availability = 'active' AND is_engineering_fixture = false"
    if q:
        for rank, r in enumerate(await pool.fetch(
                f"SELECT id::text AS id, name, goal, steps FROM procedures WHERE {base} AND "
                f"to_tsvector('english', name || ' ' || goal || ' ' || COALESCE(retrieval_document, '')) @@ to_tsquery('english', $2) "
                f"ORDER BY ts_rank_cd(to_tsvector('english', name || ' ' || goal || ' ' || COALESCE(retrieval_document, '')), "
                f"to_tsquery('english', $2)) DESC, id LIMIT {k}", goal_id, q), 1):
            c = by.setdefault(r["id"], Candidate(r["id"], r["name"], procedure_text(r["name"], r["goal"], r["steps"])))
            c.fts_rank = rank
            fts_ids.append(r["id"])
    if embedding is not None and model:
        for rank, r in enumerate(await pool.fetch(
                f"SELECT id::text AS id, name, goal, steps, embedding <=> $2::vector AS dist FROM procedures WHERE {base} "
                f"AND embedding IS NOT NULL AND embedding_model_id = $3 ORDER BY dist, id LIMIT {k}",
                goal_id, to_pgvector(embedding), model), 1):
            c = by.setdefault(r["id"], Candidate(r["id"], r["name"], procedure_text(r["name"], r["goal"], r["steps"])))
            c.vec_rank, c.vec_distance = rank, float(r["dist"])
            vec_ids.append(r["id"])
    # procedures of this goal that live on OTHER shards (global projection; their canonical rows stay there)
    from app.services.shards import HOME_SHARD, multi_shard

    if await multi_shard(pool):
        for r in await pool.fetch(
                "SELECT procedure_row_id::text AS id, name, search_text FROM procedure_search_index "
                "WHERE goal_id = $1::uuid AND home_shard_id <> $2 AND status = 'active' ORDER BY procedure_id LIMIT $3", goal_id, HOME_SHARD, k):
            by.setdefault(r["id"], Candidate(r["id"], r["name"], (r["search_text"] or r["name"])[:1500]))
    # goals with few procedures: the goal constraint alone is a fine candidate set
    if not by:
        for r in await pool.fetch(f"SELECT id::text AS id, name, goal, steps FROM procedures WHERE {base} ORDER BY id LIMIT {k}", goal_id):
            by[r["id"]] = Candidate(r["id"], r["name"], procedure_text(r["name"], r["goal"], r["steps"]))
    for oid, sc in rrf_fuse(fts_ids, vec_ids).items():
        by[oid].rrf = sc
    return sorted(by.values(), key=lambda c: (-c.rrf, c.id))[:5]


async def resolve_procedure_identity(
    pool: asyncpg.Pool, *, goal_id: str, name: str, goal_text: str, steps: list, source_key: Optional[str], scope_type: str,
    scope_entity_id: Optional[str], embedder: Any, judge: SemanticJudge, on_unavailable: str, job_id: Optional[int] = None,
) -> tuple[str, Optional[str]]:
    """Procedure identity WITHIN one Goal: returns (decision, resolved_row_id) with decision in
    no_candidates | same | new_version | distinct | judge_unavailable. Shared by ingest_procedure and by
    capture_procedure(procedure_dedup=True), so every adapter that opts in gets the identical behaviour."""
    text = procedure_text(name, goal_text, steps)
    emb, model = None, None
    if embedder is not None:
        emb = await embedder.embed_one(text, input_type="document")
        model = embedder.embedding_model_id()
    cands = await _candidates(pool, goal_id, text, emb, model)
    decision, resolved, provider, mdl = "no_candidates", None, None, None
    if cands:
        for cand in cands:
            res = await judge.judge_identity("procedure", text, cand.text)
            if not res.ok:
                if on_unavailable == "raise":
                    raise SemanticJudgmentUnavailable(f"procedure identity unavailable ({res.reason})", attempts=res.attempts)
                decision = "judge_unavailable"
                break
            cand.relation, cand.confidence = res.value["relation"], res.value["confidence"]
            provider, mdl = res.provider, res.model
            if cand.relation in ("same", "refinement") and cand.confidence >= SAME_MIN_CONFIDENCE:
                decision, resolved = ("same" if cand.relation == "same" else "new_version"), cand.id
                break
        else:
            decision = "distinct"
        await record_decision(
            pool, object_type="procedure", candidate_text=text, scope_type=scope_type, scope_entity_id=scope_entity_id,
            decision=decision, resolved_id=resolved, candidates=cands, judge=judge, provider=provider, model=mdl,
            fts_n=sum(1 for c in cands if c.fts_rank), vec_n=sum(1 for c in cands if c.vec_rank), job_id=job_id,
            idempotency_key=f"proc:{source_key}" if source_key else None, detail={"goal_id": goal_id})
    return decision, resolved


async def attach_procedure_source(pool: asyncpg.Pool, row_id: str, *, source_key: Optional[str], provenance: str) -> dict:
    """Same method from another source: keep ONE procedure, record the source as provenance."""
    from app.services.shards import home_pool
    owner = await home_pool(pool, "procedure", row_id, by_row_id=True)
    row = await owner.fetchrow(
        "UPDATE procedures SET evidence_refs = CASE WHEN evidence_refs @> $2::jsonb THEN evidence_refs "
        "ELSE evidence_refs || $2::jsonb END WHERE id = $1::uuid RETURNING id::text AS id, procedure_id::text AS procedure_id",
        row_id, [{"type": "source", "source_key": source_key, "provenance": provenance}])
    return dict(row)


async def ingest_procedure(
    pool: asyncpg.Pool, *, source_key: str, name: str, goal: str, steps: list, provenance: str, scope_type: str,
    scope_entity_id: Optional[str] = None, owner_id: Optional[str] = None, visibility: str = "public",
    embedder: Any = None, judge: Optional[SemanticJudge] = None, on_unavailable: Optional[str] = None,
    job_id: Optional[int] = None, **capture_kwargs: Any,
) -> dict[str, Any]:
    """Idempotent per ``source_key``. Returns {action, id, procedure_id, goal_id, decision}."""
    from app.services.procedures import capture_procedure, supersede_procedure

    judge = judge if judge is not None else default_judge()
    if on_unavailable is None:
        on_unavailable = "raise" if judge.providers else "create"

    prior = await pool.fetchrow(
        "SELECT id::text AS id, procedure_id::text AS procedure_id, achieves_goal_id::text AS goal_id FROM procedures "
        "WHERE source_key = $1 AND t_invalid IS NULL", source_key)
    if prior:
        return {"action": "duplicate_source", "id": prior["id"], "procedure_id": prior["procedure_id"],
                "goal_id": prior["goal_id"], "decision": "exact_match"}

    # 1) Goal identity first (durable + idempotent through the same key)
    from app.services.goals import find_or_create_goal

    g = await find_or_create_goal(
        pool, canonical_name=goal, scope_type=scope_type, scope_entity_id=scope_entity_id, provenance=provenance,
        created_from="procedure_ingest", owner_id=owner_id, visibility=visibility if visibility in ("public", "private") else "public",
        embedder=embedder, judge=judge, on_unavailable=on_unavailable, job_id=job_id, idempotency_key=f"goal:{source_key}")

    # 2) Procedure identity within that Goal
    decision, resolved = await resolve_procedure_identity(
        pool, goal_id=g["id"], name=name, goal_text=goal, steps=steps, source_key=source_key, scope_type=scope_type,
        scope_entity_id=scope_entity_id, embedder=embedder, judge=judge, on_unavailable=on_unavailable, job_id=job_id)

    if decision == "same":
        row = await attach_procedure_source(pool, resolved, source_key=source_key, provenance=provenance)
        return {"action": "same_procedure", "id": row["id"], "procedure_id": row["procedure_id"], "goal_id": g["id"], "decision": decision}
    if decision == "new_version":
        v = await supersede_procedure(pool, prior_row_id=resolved, changed_fields={"steps": steps, "name": name},
                                      reason=f"refined by source {source_key}")
        if v is not None:
            return {"action": "new_version", "id": v["id"], "procedure_id": v["procedure_id"], "goal_id": g["id"], "decision": decision}
    r = await capture_procedure(
        pool, name=name, goal=goal, steps=steps, provenance=provenance, scope_type=scope_type, scope_entity_id=scope_entity_id,
        owner_id=owner_id, visibility=visibility, goal_embedder=embedder, goal_judge=judge, goal_on_unavailable=on_unavailable,
        source_key=source_key, identity_job_id=job_id, identity_idempotency_key=f"goal:{source_key}", **capture_kwargs)
    return {"action": "duplicate_source" if r.get("duplicate") else "created", "id": r["id"], "procedure_id": r["procedure_id"],
            "goal_id": g["id"], "decision": decision}
