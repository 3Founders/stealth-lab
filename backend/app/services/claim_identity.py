"""
Claim identity (docs/dedup_and_identity.md, "Claim").

Claims are handled MORE conservatively than Goals: a wrong merge silently changes what is believed.

    candidate statement
      -> exact normalized statement in the same scope/visibility class        (deterministic)
      -> candidates: FTS + ANN over claim_search_index (ALL shards), RRF
      -> SemanticJudge.judge_identity("claim")
             same         (confidence >= CLAIM_SAME_MIN_CONFIDENCE)  -> REUSE the existing claim and
                                                                        attach this source as provenance
             contradicts                                             -> KEEP BOTH; record a pending
                                                                        `contradicts` relation candidate
                                                                        (existing claim_equivalence review
                                                                        table -- the one relation system)
             specializes / generalizes / related                     -> new claim + pending `related` candidate
             distinct                                                -> new claim
      -> durable identity_decisions row (idempotent per source)

Rules that make a wrong merge unlikely:
  * disagreement is never merged; a low-confidence "same" is treated as "related";
  * a PRIVATE claim is only ever compared with the same owner's private claims and a PUBLIC claim only with
    public claims -- a private source can never be attached to a public row;
  * same-goal claim resolution is serialised (advisory lock keyed by goal / scope) so two workers ingesting
    paraphrases of one proposition about one goal cannot race past each other;
  * judge outage while candidates exist: fail closed (retryable) for public claims; private claims are created
    and recorded as `judge_unavailable` (never blocks a user's local sync).
"""
from __future__ import annotations

import logging
import re
from typing import Any, Optional

import asyncpg

from app.services.embeddings import to_pgvector
from app.services.identity_resolution import (
    Candidate, default_judge, fts_or_query, record_decision, rrf_fuse,
)
from app.services.semantic.chain import SemanticJudge
from app.services.semantic.errors import SemanticJudgmentUnavailable
from app.services.shards import HOME_SHARD, cached_shards, choose_child_shard, home_pool, pools_for, record_route, writable_shards

log = logging.getLogger(__name__)

CLAIM_SAME_MIN_CONFIDENCE = 0.85
CLAIM_CANDIDATE_TOP_N = 5
_WS = re.compile(r"\s+")


def normalize_statement(s: str) -> str:
    return _WS.sub(" ", s.strip().lower())


async def _candidates(pool, statement: str, *, scope_type, scope_entity_id, visibility: str, owner_id: Optional[str],
                      embedding, embedding_model) -> tuple[list[Candidate], int, int]:
    base = ("status NOT IN ('retracted', 'invalid', 'superseded') AND COALESCE(scope_type, 'global') = $1 "
            "AND COALESCE(scope_entity_id, '') = $2 AND visibility = $3::visibility_level AND COALESCE(owner_id, '') = $4")
    params = [scope_type or "global", scope_entity_id or "", visibility, owner_id or ""]
    by: dict[str, Candidate] = {}
    fts_ids: list[str] = []
    vec_ids: list[str] = []
    q = fts_or_query(statement)
    if q:
        for rank, r in enumerate(await pool.fetch(
                f"SELECT claim_id::text AS id, statement, home_shard_id FROM claim_search_index WHERE {base} "
                f"AND search_tsv @@ to_tsquery('english', $5) ORDER BY ts_rank_cd(search_tsv, to_tsquery('english', $5)) DESC, claim_id LIMIT 20",
                *params, q), 1):
            c = by.setdefault(r["id"], Candidate(r["id"], r["statement"][:200], r["statement"], home_shard_id=r["home_shard_id"]))
            c.fts_rank = rank
            fts_ids.append(r["id"])
    if embedding is not None and embedding_model:
        for rank, r in enumerate(await pool.fetch(
                f"SELECT claim_id::text AS id, statement, home_shard_id, embedding <=> $5::vector AS dist FROM claim_search_index "
                f"WHERE {base} AND embedding IS NOT NULL AND embedding_model = $6 ORDER BY dist, claim_id LIMIT 20",
                *params, to_pgvector(embedding), embedding_model), 1):
            c = by.setdefault(r["id"], Candidate(r["id"], r["statement"][:200], r["statement"], home_shard_id=r["home_shard_id"]))
            c.vec_rank, c.vec_distance = rank, float(r["dist"])
            vec_ids.append(r["id"])
    for oid, sc in rrf_fuse(fts_ids, vec_ids).items():
        by[oid].rrf = sc
    return sorted(by.values(), key=lambda c: (-c.rrf, c.id))[:CLAIM_CANDIDATE_TOP_N], len(fts_ids), len(vec_ids)


async def _attach_provenance(pool, claim_id: str, source_key: str, source_ref: Optional[str], ingestion_context_id: Optional[str]) -> None:
    owner = await home_pool(pool, "claim", claim_id)
    ref = {"source_key": source_key, "source_ref": source_ref, "ingestion_context_id": ingestion_context_id}
    await owner.execute(
        "UPDATE knowledge_nodes SET properties = jsonb_set(properties, '{source_refs}', "
        "CASE WHEN COALESCE(properties->'source_refs', '[]'::jsonb) @> $2::jsonb THEN COALESCE(properties->'source_refs', '[]'::jsonb) "
        "ELSE COALESCE(properties->'source_refs', '[]'::jsonb) || $2::jsonb END, true) WHERE id = $1::uuid", claim_id, [ref])


async def ingest_claim(
    pool: asyncpg.Pool, *, statement: str, scope_type: str, scope_entity_id: Optional[str] = None,
    visibility: str = "public", owner_id: Optional[str] = None, source_key: str, source_ref: Optional[str] = None,
    ingestion_context_id: Optional[str] = None, goal_id: Optional[str] = None, goal_home_shard: Optional[str] = None,
    embedder: Any = None, judge: Optional[SemanticJudge] = None, on_unavailable: Optional[str] = None,
    job_id: Optional[int] = None, created_by: str = "ingestion_worker", properties: Optional[dict] = None,
) -> dict[str, Any]:
    """Idempotent per (source_key, statement). Returns {action, claim_id, decision, home_shard_id, related:[…]}."""
    from app.services.claims import capture_claim
    from app.services.claim_equivalence import record_claim_relation_candidate

    statement = statement.strip()
    if not statement:
        raise ValueError("empty claim statement")
    judge = judge if judge is not None else default_judge()
    if on_unavailable is None:
        on_unavailable = "create" if (visibility != "public" or not judge.providers) else "raise"
    norm = normalize_statement(statement)
    idem = f"claim:{source_key}:{norm[:120]}"
    scope_key = f"{goal_id or 'noscope:' + (scope_type or 'global') + ':' + (scope_entity_id or '')}"

    async with pool.acquire() as lock:
        async with lock.transaction():
            await lock.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"claim-identity:{scope_key}")

            # 0. replay: this exact source+statement already resolved
            prior = await pool.fetchrow("SELECT decision, resolved_id::text AS rid FROM identity_decisions WHERE object_type='claim' AND idempotency_key=$1", idem)
            if prior and prior["rid"]:
                await _attach_provenance(pool, prior["rid"], source_key, source_ref, ingestion_context_id)
                return {"action": "reused", "claim_id": prior["rid"], "decision": prior["decision"], "related": []}

            # 1. exact (canonical, local) -- covers claims not yet projected
            exact = await pool.fetchval(
                "SELECT id::text FROM knowledge_nodes WHERE node_type = 'claim' AND t_invalid IS NULL "
                "AND lower(regexp_replace(name, '\\s+', ' ', 'g')) = $1 AND scope_type IS NOT DISTINCT FROM $2 "
                "AND scope_entity_id IS NOT DISTINCT FROM $3 AND visibility = $4::visibility_level AND owner_id IS NOT DISTINCT FROM $5",
                norm, scope_type, scope_entity_id, visibility, owner_id)
            if exact is None:   # ... and on other shards, via the global projection
                exact = await pool.fetchval(
                    "SELECT claim_id::text FROM claim_search_index WHERE lower(regexp_replace(statement, '\\s+', ' ', 'g')) = $1 "
                    "AND COALESCE(scope_type, 'global') = $2 AND COALESCE(scope_entity_id, '') = $3 AND visibility = $4::visibility_level "
                    "AND COALESCE(owner_id, '') = $5 AND status NOT IN ('retracted', 'invalid', 'superseded') LIMIT 1",
                    norm, scope_type or "global", scope_entity_id or "", visibility, owner_id or "")
            if exact:
                await _attach_provenance(pool, exact, source_key, source_ref, ingestion_context_id)
                return {"action": "reused", "claim_id": exact, "decision": "exact_match", "related": []}

            # 2. semantic candidates -> judge
            emb, model = None, None
            if embedder is not None:
                emb = await embedder.embed_one(statement, input_type="document")
                model = embedder.embedding_model_id()
            cands, n_fts, n_vec = await _candidates(pool, statement, scope_type=scope_type, scope_entity_id=scope_entity_id,
                                                    visibility=visibility, owner_id=owner_id, embedding=emb, embedding_model=model)
            decision, resolved, provider, mdl = "no_candidates", None, None, None
            related: list[tuple[str, str, float]] = []
            if cands:
                decision = "distinct"
                for cand in cands:
                    res = await judge.judge_identity("claim", statement, cand.text)
                    if not res.ok:
                        if on_unavailable == "raise":
                            raise SemanticJudgmentUnavailable(f"claim identity unavailable ({res.reason})", attempts=res.attempts)
                        decision = "judge_unavailable"
                        break
                    cand.relation, cand.confidence = res.value["relation"], res.value["confidence"]
                    provider, mdl = res.provider, res.model
                    if cand.relation == "same" and cand.confidence >= CLAIM_SAME_MIN_CONFIDENCE:
                        decision, resolved = "same", cand.id
                        break
                    if cand.relation == "same":          # low-confidence same: never a merge
                        cand.relation = "related"
                    if cand.relation == "contradicts":
                        related.append((cand.id, "contradicts", cand.confidence))
                        decision = "contradicts"
                    elif cand.relation in ("specializes", "generalizes", "related"):
                        related.append((cand.id, "related", cand.confidence))
                        if decision == "distinct":
                            decision = {"specializes": "narrower", "generalizes": "broader"}.get(cand.relation, "related")
                await record_decision(
                    pool, object_type="claim", candidate_text=statement, scope_type=scope_type, scope_entity_id=scope_entity_id,
                    decision=decision, resolved_id=resolved, candidates=cands, judge=judge, provider=provider, model=mdl,
                    fts_n=n_fts, vec_n=n_vec, job_id=job_id, idempotency_key=idem,
                    detail={"goal_id": goal_id, "source_key": source_key})
            if decision == "same":
                await _attach_provenance(pool, resolved, source_key, source_ref, ingestion_context_id)
                return {"action": "reused", "claim_id": resolved, "decision": "same", "related": []}

            # 3. create (on the goal's shard when public; home shard when private)
            shards = await cached_shards(pool)
            claim_home = choose_child_shard(goal_home_shard, statement, writable_shards(shards, visibility=visibility))
            wpool = pool if claim_home == HOME_SHARD else await pools_for(pool).get(claim_home)
            props = dict(properties or {})
            if goal_id:
                props.setdefault("goal_id", goal_id)
            props["source_refs"] = [{"source_key": source_key, "source_ref": source_ref, "ingestion_context_id": ingestion_context_id}]
            cid = await capture_claim(
                wpool, statement=statement, task_ids=[], created_by=created_by, owner_id=owner_id,
                visibility=visibility if visibility in ("public", "private") else "public", scope_type=scope_type,
                scope_entity_id=scope_entity_id, source_ref=source_ref or source_key, ingestion_context_id=ingestion_context_id,
                embedder=embedder, properties=props)
            if not cid:
                raise ValueError("capture_claim declined the write (no anchor)")
            cid = str(cid)
            if claim_home != HOME_SHARD:
                await record_route(pool, "claim", cid, claim_home)
                from app.services.search_projection import enqueue, project_object
                await enqueue(pool, "claim", cid)
                try:
                    async with pool.acquire() as conn:
                        async with conn.transaction():
                            await project_object(conn, "claim", cid, pools=pools_for(pool))
                except Exception:  # noqa: BLE001 -- outbox repairs
                    pass
            for other_id, rel, conf in related:
                try:
                    await record_claim_relation_candidate(pool, claim_a_id=cid, claim_b_id=other_id, relation=rel,
                                                          confidence=conf, created_by=created_by)
                except Exception:  # noqa: BLE001 -- review candidates never block ingestion
                    log.warning("claim relation candidate not stored", exc_info=True)
            return {"action": "created", "claim_id": cid, "decision": decision, "home_shard_id": claim_home,
                    "related": [{"claim_id": o, "relation": r, "confidence": c} for o, r, c in related]}
