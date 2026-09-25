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

import json
import logging
from typing import Any, Optional

import asyncpg

from app.services.embeddings import to_pgvector
from app.services.identity_resolution import (
    SAME_MIN_CONFIDENCE,
    Candidate,
    IdentityReplayError,
    PermanentIdentityConflict,
    _decision_metadata_mismatch_reason,
    _load_prior_decision,
    _row_value,
    _validate_candidate_objects,
    _validate_stored_decision_row,
    canonical_identity_text,
    default_judge,
    fts_or_query,
    record_decision,
    rrf_fuse,
    validate_identity_job_id,
    validate_on_unavailable,
)
from app.services.semantic.chain import SemanticJudge
from app.services.semantic.errors import SemanticJudgmentUnavailable

log = logging.getLogger(__name__)


def procedure_text(name: str, goal: str, steps: Optional[list]) -> str:
    parts = []
    for s in steps or []:
        parts.append(s if isinstance(s, str) else str(s.get("description") or s.get("text") or s.get("name") or ""))
    return f"{name}: {goal}. Steps: " + "; ".join(p for p in parts if p)[:1200]


def _decision_detail(value: Any) -> dict:
    if value is None:
        return {}
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (TypeError, ValueError) as exc:
            raise IdentityReplayError("stored procedure detail JSON is invalid") from exc
    if not isinstance(value, dict):
        raise IdentityReplayError("stored procedure detail must be an object")
    return value


def _procedure_prior_mismatch_reason(
    row: Any,
    *,
    text: str,
    goal_id: str,
    scope_type: str,
    scope_entity_id: Optional[str],
    job_id: Optional[int] = None,
) -> Optional[str]:
    mismatch = _decision_metadata_mismatch_reason(
        row,
        candidate_text=canonical_identity_text(text),
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
        job_id=job_id,
    )
    if mismatch:
        return mismatch
    detail = _decision_detail(_row_value(row, "detail", {}))
    stored_goal_id = detail.get("goal_id")
    if stored_goal_id is None or str(stored_goal_id) != str(goal_id):
        return "goal_id"
    return None


def _procedure_prior_matches(
    row: Any,
    *,
    text: str,
    goal_id: str,
    scope_type: str,
    scope_entity_id: Optional[str],
    job_id: Optional[int] = None,
) -> bool:
    return _procedure_prior_mismatch_reason(
        row,
        text=text,
        goal_id=goal_id,
        scope_type=scope_type,
        scope_entity_id=scope_entity_id,
        job_id=job_id,
    ) is None


async def _live_procedure(
    pool: Any,
    row_id: str,
    *,
    goal_id: str,
    scope_type: str,
    scope_entity_id: Optional[str],
) -> bool:
    from app.services.shards import home_pool

    owner = await home_pool(pool, "procedure", str(row_id), by_row_id=True)
    query = (
        "SELECT 1 FROM procedures WHERE id = $1::uuid AND t_invalid IS NULL "
        "AND availability <> 'disabled' AND achieves_goal_id = $2::uuid "
        "AND (($3 = 'global' AND (scope_type IS NULL OR scope_type = 'global') "
        "AND scope_entity_id IS NULL) OR ($3 <> 'global' AND scope_type = $3 "
        "AND scope_entity_id = $4))"
    )
    args = (str(row_id), str(goal_id), scope_type or "global", scope_entity_id)
    if hasattr(owner, "fetchval"):
        return bool(await owner.fetchval(query, *args))
    return bool(await owner.fetchrow(query, *args))


async def _latest_live_procedure(
    pool: Any,
    row_id: str,
    *,
    goal_id: str,
    scope_type: str,
    scope_entity_id: Optional[str],
) -> Optional[str]:
    from app.services.shards import home_pool

    owner = await home_pool(pool, "procedure", str(row_id), by_row_id=True)
    target = await owner.fetchrow(
        "SELECT procedure_id::text AS procedure_id FROM procedures WHERE id = $1::uuid",
        str(row_id),
    )
    procedure_id = _row_value(target, "procedure_id")
    if not procedure_id:
        return None
    latest = await owner.fetchrow(
        "SELECT id::text AS id FROM procedures "
        "WHERE procedure_id = $1::uuid AND t_invalid IS NULL AND availability <> 'disabled' "
        "AND achieves_goal_id = $2::uuid "
        "AND (($3 = 'global' AND (scope_type IS NULL OR scope_type = 'global') "
        "AND scope_entity_id IS NULL) OR ($3 <> 'global' AND scope_type = $3 "
        "AND scope_entity_id = $4)) "
        "ORDER BY version DESC, t_created DESC, id DESC LIMIT 1",
        str(procedure_id), str(goal_id), scope_type or "global", scope_entity_id,
    )
    latest_id = _row_value(latest, "id")
    return str(latest_id) if latest_id else None


async def _resolve_live_procedure(
    pool: Any,
    row_id: str,
    *,
    goal_id: str,
    scope_type: str,
    scope_entity_id: Optional[str],
) -> Optional[str]:
    if await _live_procedure(
        pool, row_id, goal_id=goal_id, scope_type=scope_type, scope_entity_id=scope_entity_id
    ):
        return str(row_id)
    return await _latest_live_procedure(
        pool, row_id, goal_id=goal_id, scope_type=scope_type, scope_entity_id=scope_entity_id
    )


async def _replay_procedure_decision(
    pool: Any,
    prior: Any,
    *,
    goal_id: str,
    scope_type: str,
    scope_entity_id: Optional[str],
    on_unavailable: str,
) -> tuple[str, Optional[str]]:
    decision, _candidates, resolved = _validate_stored_decision_row(prior, "procedure")
    if decision == "judge_unavailable" and on_unavailable != "create":
        raise IdentityReplayError("stored judge_unavailable decision conflicts with on_unavailable='raise'")
    detail = _decision_detail(_row_value(prior, "detail", {}))
    stored_goal_id = detail.get("goal_id")
    if stored_goal_id is None or str(stored_goal_id) != str(goal_id):
        raise PermanentIdentityConflict(
            "procedure", str(_row_value(prior, "idempotency_key", "")),
            "goal identity differs from the stored decision",
        )
    if decision in ("distinct", "judge_unavailable", "no_candidates"):
        return decision, None
    if decision not in ("same", "new_version"):
        raise IdentityReplayError(f"unsupported procedure replay decision: {decision!r}")
    live_id = await _resolve_live_procedure(
        pool, str(resolved), goal_id=goal_id, scope_type=scope_type, scope_entity_id=scope_entity_id
    )
    if live_id is None:
        raise IdentityReplayError("stored procedure decision targets a dead procedure")
    return decision, live_id


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
        from app.services.shards import search_pool

        for r in await (await search_pool(pool)).fetch(
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
    on_unavailable = validate_on_unavailable(on_unavailable)
    job_id = validate_identity_job_id(job_id)
    if on_unavailable is None:
        on_unavailable = "raise" if judge.providers else "create"
    text = procedure_text(name, goal_text, steps)
    idempotency_key = f"proc:{source_key}" if source_key else None
    if idempotency_key:
        prior = await _load_prior_decision(pool, "procedure", idempotency_key)
        if prior is not None:
            mismatch = _procedure_prior_mismatch_reason(
                prior,
                text=text,
                goal_id=goal_id,
                scope_type=scope_type,
                scope_entity_id=scope_entity_id,
                job_id=job_id,
            )
            if mismatch:
                raise PermanentIdentityConflict(
                    "procedure", idempotency_key, f"{mismatch} differs from the stored decision"
                )
            return await _replay_procedure_decision(
                pool, prior, goal_id=goal_id, scope_type=scope_type,
                scope_entity_id=scope_entity_id, on_unavailable=on_unavailable,
            )

    emb, model = None, None
    if embedder is not None:
        emb = await embedder.embed_one(text, input_type="document")
        model = embedder.embedding_model_id()
    cands = _validate_candidate_objects(await _candidates(pool, goal_id, text, emb, model))
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
    if decision == "no_candidates" and idempotency_key is None:
        return decision, resolved
    written = await record_decision(
        pool, object_type="procedure", candidate_text=text, scope_type=scope_type, scope_entity_id=scope_entity_id,
        decision=decision, resolved_id=resolved, candidates=cands, judge=judge, provider=provider, model=mdl,
        fts_n=sum(1 for c in cands if c.fts_rank), vec_n=sum(1 for c in cands if c.vec_rank), job_id=job_id,
        idempotency_key=idempotency_key, detail={"goal_id": goal_id},
    )
    if written is None:
        if idempotency_key is not None:
            raise IdentityReplayError("identity decision was not persisted")
        return decision, resolved
    if _row_value(written, "decision", None) is not None:
        return await _replay_procedure_decision(
            pool, written, goal_id=goal_id, scope_type=scope_type,
            scope_entity_id=scope_entity_id, on_unavailable=on_unavailable,
        )
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

    on_unavailable = validate_on_unavailable(on_unavailable)
    job_id = validate_identity_job_id(job_id)
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
