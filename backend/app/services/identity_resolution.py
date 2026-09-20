"""
Semantic identity resolution for Goals (and the shared candidate machinery for
Claims/Procedures) -- docs/dedup_and_identity.md.

Rule: deterministic search GENERATES candidates; a MODEL decides identity.

    exact normalized name / alias      -> deterministic identity (string equality,
                                          not a semantic heuristic) -- handled by
                                          the caller before this module
    Postgres FTS  +  pgvector ANN      -> candidate generation
    Reciprocal Rank Fusion             -> ordering of the fused candidate list
    SemanticJudge.judge_identity (JEV -> Gemini -> Gemma, no heuristic provider)
                                       -> same / narrower / broader / related / distinct
    (no `cosine <= X` auto-merge, no SimHash merge -- both were removed)

Failure policy (fail closed where correctness matters):
  * judge chain exhausted while candidates exist -> ``SemanticJudgmentUnavailable``
    (the ingestion worker turns this into a retryable job failure), unless the
    caller passed ``on_unavailable="create"`` (dev / no provider configured), in
    which case a new object is created and the decision is recorded as
    ``judge_unavailable`` so it can be reviewed/merged later.
  * every judged resolution is written to ``identity_decisions`` (durable, vendor
    independent). A replayed job with the same ``idempotency_key`` reuses its
    earlier decision instead of re-judging.
"""
from __future__ import annotations

import asyncio
import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any, Literal, Optional

import asyncpg

from app.services.embeddings import to_pgvector
from app.services.semantic.chain import SemanticJudge
from app.services.semantic.errors import SemanticJudgmentUnavailable
from app.services.semantic.prompts import IDENTITY_PROMPT_VERSION

log = logging.getLogger(__name__)

RRF_K = 60
DEFAULT_FTS_K = 20
DEFAULT_VECTOR_K = 20
DEFAULT_JUDGE_TOP_N = 5
# A model verdict of "same" below this self-reported confidence is treated as
# "related" (fail closed). This is the MODEL's confidence, not a similarity threshold.
SAME_MIN_CONFIDENCE = 0.75

_TOKEN_RE = re.compile(r"[a-z0-9]+")
_STOP = frozenset("a an the of to for in on at by with and or from into is are be as it its this that".split())

_RELATION_TO_DECISION = {
    "same": "same", "specializes": "narrower", "generalizes": "broader", "related": "related",
    "contradicts": "contradicts", "distinct": "distinct", "refinement": "new_version",
}


def fts_or_query(text: str, *, max_terms: int = 24) -> Optional[str]:
    """OR-joined ``to_tsquery`` string from free text (recall-oriented: FTS only
    generates candidates; ``plainto_tsquery``'s AND would drop paraphrases)."""
    seen: list[str] = []
    for tok in _TOKEN_RE.findall(text.lower()):
        if tok in _STOP or tok in seen:
            continue
        seen.append(tok)
    if not seen:
        return None
    return " | ".join(seen[:max_terms])


def rrf_fuse(*ranked: list[str], k: int = RRF_K) -> dict[str, float]:
    """id -> fused score across ranked id lists (rank starts at 1)."""
    scores: dict[str, float] = {}
    for lst in ranked:
        for rank, oid in enumerate(lst, start=1):
            scores[oid] = scores.get(oid, 0.0) + 1.0 / (k + rank)
    return scores


@dataclass
class Candidate:
    id: str
    name: str
    text: str
    fts_rank: Optional[int] = None
    vec_rank: Optional[int] = None
    vec_distance: Optional[float] = None
    rrf: float = 0.0
    home_shard_id: Optional[str] = None
    relation: Optional[str] = None
    confidence: Optional[float] = None

    def as_json(self) -> dict:
        return {k: v for k, v in self.__dict__.items() if k != "text" and v is not None}


@dataclass
class IdentityOutcome:
    action: Literal["reuse", "create"]
    decision: str
    resolved_id: Optional[str] = None
    candidates: list[Candidate] = field(default_factory=list)
    relations: list[Candidate] = field(default_factory=list)  # narrower/broader/related candidates
    decision_id: Optional[str] = None
    fts_candidates: int = 0
    vector_candidates: int = 0
    judge_provider: Optional[str] = None
    judge_model: Optional[str] = None
    reused_decision: bool = False


_DEFAULT_JUDGE: Optional[SemanticJudge] = None


def default_judge() -> SemanticJudge:
    """Process-wide judge built from settings (JEV -> Gemini -> Gemma)."""
    global _DEFAULT_JUDGE
    if _DEFAULT_JUDGE is None:
        _DEFAULT_JUDGE = SemanticJudge.from_settings()
    return _DEFAULT_JUDGE


def reset_default_judge() -> None:
    global _DEFAULT_JUDGE
    _DEFAULT_JUDGE = None


# --------------------------------------------------------------- candidates

_SCOPE_GLOBAL = "(scope_type IS NULL OR scope_type = 'global')"


def _scope_clause(scope_type: str, n: int) -> tuple[str, list[Any]]:
    """WHERE fragment (params start at $n) restricting to one goal scope."""
    if scope_type == "global":
        return _SCOPE_GLOBAL, []
    return f"scope_type = ${n} AND scope_entity_id = ${n + 1}", [scope_type]


async def generate_goal_candidates(
    pool: asyncpg.Pool, text: str, *, scope_type: str, scope_entity_id: Optional[str],
    embedding: Optional[list[float]] = None, embedding_model: Optional[str] = None,
    fts_k: int = DEFAULT_FTS_K, vector_k: int = DEFAULT_VECTOR_K, top_n: int = DEFAULT_JUDGE_TOP_N,
    exclude_id: Optional[str] = None, created_within: Optional[tuple[Any, float]] = None,
) -> tuple[list[Candidate], int, int]:
    """FTS + ANN candidates over CANONICAL ``goals`` (never the projection: a
    goal committed a millisecond ago by a concurrent worker must be visible),
    fused with RRF. Returns (top_n fused, n_fts, n_vector).

    Uses migration 83's ``idx_goals_fts`` expression and migration 84's HNSW
    index on ``goals.embedding``. The vector leg only compares vectors from the
    SAME embedding model (never mixes vector spaces)."""
    scope_sql, extra = _scope_clause(scope_type, 1)
    params: list[Any] = list(extra)
    if scope_type != "global":
        params.append(scope_entity_id)
    base = f"t_invalid IS NULL AND status <> 'merged' AND {scope_sql}"
    if exclude_id:
        params.append(exclude_id)
        base += f" AND id <> ${len(params)}::uuid"
    if created_within:  # (anchor timestamp, +/- minutes): only goals created near the anchor
        params.extend([created_within[0], float(created_within[1])])
        base += (f" AND t_created BETWEEN ${len(params) - 1}::timestamptz - make_interval(mins => ${len(params)}) "
                 f"AND ${len(params) - 1}::timestamptz + make_interval(mins => ${len(params)})")

    by_id: dict[str, Candidate] = {}
    fts_ids: list[str] = []
    q = fts_or_query(text)
    if q:
        rows = await pool.fetch(
            f"SELECT id::text AS id, canonical_name, description, home_shard_id, "
            f"ts_rank_cd(to_tsvector('english', canonical_name || ' ' || COALESCE(description, '')), "
            f"to_tsquery('english', ${len(params) + 1})) AS r FROM goals "
            f"WHERE {base} AND to_tsvector('english', canonical_name || ' ' || COALESCE(description, '')) "
            f"@@ to_tsquery('english', ${len(params) + 1}) ORDER BY r DESC, id LIMIT {int(fts_k)}",
            *params, q)
        for rank, r in enumerate(rows, start=1):
            c = by_id.setdefault(r["id"], Candidate(r["id"], r["canonical_name"], _goal_text(r), home_shard_id=r["home_shard_id"]))
            c.fts_rank = rank
            fts_ids.append(r["id"])

    vec_ids: list[str] = []
    if embedding is not None and embedding_model:
        vparams = params + [to_pgvector(embedding), embedding_model]
        n_vec, n_model = len(params) + 1, len(params) + 2
        rows = await pool.fetch(
            f"SELECT id::text AS id, canonical_name, description, home_shard_id, "
            f"embedding <=> ${n_vec}::vector AS dist FROM goals "
            f"WHERE {base} AND embedding IS NOT NULL AND embedding_model_id = ${n_model} "
            f"ORDER BY dist ASC, id LIMIT {int(vector_k)}", *vparams)
        for rank, r in enumerate(rows, start=1):
            c = by_id.setdefault(r["id"], Candidate(r["id"], r["canonical_name"], _goal_text(r), home_shard_id=r["home_shard_id"]))
            c.vec_rank, c.vec_distance = rank, float(r["dist"])
            vec_ids.append(r["id"])

    # Goals homed on OTHER shards are not in this database's `goals`: candidates for them come
    # from the global goal projection (their canonical rows stay on their shard).
    from app.services.shards import HOME_SHARD, multi_shard

    if await multi_shard(pool):
        rparams: list[Any] = [HOME_SHARD, "global" if scope_type == "global" else scope_type, scope_entity_id or ""]
        rbase = ("home_shard_id <> $1 AND status <> 'merged' AND COALESCE(scope_type, 'global') = $2 "
                 "AND COALESCE(scope_entity_id, '') = $3")
        if exclude_id:
            rparams.append(exclude_id)
            rbase += f" AND goal_id <> ${len(rparams)}::uuid"
        if q:
            n = len(rparams) + 1
            for rank, r in enumerate(await pool.fetch(
                    f"SELECT goal_id::text AS id, canonical_name, short_description AS description, home_shard_id FROM goal_search_index "
                    f"WHERE {rbase} AND search_tsv @@ to_tsquery('english', ${n}) "
                    f"ORDER BY ts_rank_cd(search_tsv, to_tsquery('english', ${n})) DESC, goal_id LIMIT {int(fts_k)}", *rparams, q), 1):
                c = by_id.setdefault(r["id"], Candidate(r["id"], r["canonical_name"], _goal_text(r), home_shard_id=r["home_shard_id"]))
                c.fts_rank = c.fts_rank or rank
                fts_ids.append(r["id"])
        if embedding is not None and embedding_model:
            n = len(rparams) + 1
            for rank, r in enumerate(await pool.fetch(
                    f"SELECT goal_id::text AS id, canonical_name, short_description AS description, home_shard_id, "
                    f"embedding <=> ${n}::vector AS dist FROM goal_search_index WHERE {rbase} AND embedding IS NOT NULL "
                    f"AND embedding_model = ${n + 1} ORDER BY dist, goal_id LIMIT {int(vector_k)}",
                    *rparams, to_pgvector(embedding), embedding_model), 1):
                c = by_id.setdefault(r["id"], Candidate(r["id"], r["canonical_name"], _goal_text(r), home_shard_id=r["home_shard_id"]))
                c.vec_rank, c.vec_distance = rank, float(r["dist"])
                vec_ids.append(r["id"])

    scores = rrf_fuse(fts_ids, vec_ids)
    for oid, sc in scores.items():
        by_id[oid].rrf = sc
    fused = sorted(by_id.values(), key=lambda c: (-c.rrf, c.id))[:top_n]
    return fused, len(fts_ids), len(vec_ids)


def _goal_text(r) -> str:
    d = r["description"]
    return f"{r['canonical_name']}: {d}" if d else r["canonical_name"]


# ------------------------------------------------------------------ resolve


async def _load_prior_decision(pool, object_type: str, key: str) -> Optional[asyncpg.Record]:
    return await pool.fetchrow(
        "SELECT id::text AS id, decision, resolved_id::text AS resolved_id, judge_provider, judge_model "
        "FROM identity_decisions WHERE object_type = $1 AND idempotency_key = $2", object_type, key)


async def record_decision(
    pool: asyncpg.Pool, *, object_type: str, candidate_text: str, scope_type: Optional[str],
    scope_entity_id: Optional[str], decision: str, resolved_id: Optional[str], candidates: list[Candidate],
    judge: Optional[SemanticJudge], provider: Optional[str], model: Optional[str], fts_n: int, vec_n: int,
    job_id: Optional[int], idempotency_key: Optional[str], detail: Optional[dict] = None,
) -> Optional[str]:
    """Insert the durable decision row. With an idempotency key a concurrent
    duplicate loses the race harmlessly and the winner's id is returned."""
    row = await pool.fetchrow(
        """
        INSERT INTO identity_decisions (object_type, candidate_text, scope_type, scope_entity_id, decision,
            resolved_id, candidates, judge_chain, judge_provider, judge_model, prompt_version,
            fts_candidates, vector_candidates, job_id, idempotency_key, detail)
        VALUES ($1, $2, $3, $4, $5, $6::uuid, $7::jsonb, $8, $9, $10, $11, $12, $13, $14, $15, $16::jsonb)
        ON CONFLICT (object_type, idempotency_key) WHERE idempotency_key IS NOT NULL DO NOTHING
        RETURNING id::text
        """,
        object_type, candidate_text[:2000], scope_type, scope_entity_id, decision, resolved_id,
        [c.as_json() for c in candidates], (judge.chain_id if judge else None), provider, model,
        IDENTITY_PROMPT_VERSION, fts_n, vec_n, job_id, idempotency_key, detail or {})
    if row:
        return row["id"]
    prior = await _load_prior_decision(pool, object_type, idempotency_key or "")
    return prior["id"] if prior else None  # pragma: no cover -- conflict implies a prior row


async def resolve_goal_identity(
    pool: asyncpg.Pool, *, name: str, description: Optional[str], scope_type: str,
    scope_entity_id: Optional[str], embedding: Optional[list[float]], embedding_model: Optional[str],
    judge: Optional[SemanticJudge] = None, on_unavailable: Optional[str] = None,
    job_id: Optional[int] = None, idempotency_key: Optional[str] = None,
    fts_k: int = DEFAULT_FTS_K, vector_k: int = DEFAULT_VECTOR_K, top_n: int = DEFAULT_JUDGE_TOP_N,
    same_min_confidence: float = SAME_MIN_CONFIDENCE,
) -> IdentityOutcome:
    """Decide whether the candidate Goal already exists. Never mutates ``goals``;
    the caller creates the row on ``action == 'create'``."""
    judge = judge if judge is not None else default_judge()
    if on_unavailable is None:
        on_unavailable = "raise" if judge.providers else "create"
    cand_text = f"{name}: {description}" if description else name

    if idempotency_key:
        prior = await _load_prior_decision(pool, "goal", idempotency_key)
        if prior is not None and prior["decision"] in ("same", "exact_match") and prior["resolved_id"]:
            alive = await pool.fetchval(
                "SELECT 1 FROM goals WHERE id = $1::uuid AND status <> 'merged' AND t_invalid IS NULL", prior["resolved_id"])
            if alive:
                return IdentityOutcome("reuse", prior["decision"], prior["resolved_id"], decision_id=prior["id"],
                                       judge_provider=prior["judge_provider"], judge_model=prior["judge_model"],
                                       reused_decision=True)

    t0 = time.monotonic()
    candidates, n_fts, n_vec = await generate_goal_candidates(
        pool, cand_text, scope_type=scope_type, scope_entity_id=scope_entity_id, embedding=embedding,
        embedding_model=embedding_model, fts_k=fts_k, vector_k=vector_k, top_n=top_n)
    cand_ms = (time.monotonic() - t0) * 1000

    async def _finish(decision: str, resolved: Optional[str], *, provider=None, model=None, relations=None,
                      action: str = "create", detail: Optional[dict] = None) -> IdentityOutcome:
        detail = {**(detail or {}), "candidate_ms": round(cand_ms, 1)}
        if decision == "no_candidates":
            # Nothing was judged; the new goal row itself (created_from/provenance)
            # is the durable record. Persisting these would double every write.
            return IdentityOutcome(action, decision, resolved, candidates, [], None, n_fts, n_vec)  # type: ignore[arg-type]
        did = await record_decision(
            pool, object_type="goal", candidate_text=cand_text, scope_type=scope_type,
            scope_entity_id=scope_entity_id, decision=decision, resolved_id=resolved, candidates=candidates,
            judge=judge, provider=provider, model=model, fts_n=n_fts, vec_n=n_vec, job_id=job_id,
            idempotency_key=idempotency_key, detail=detail)
        return IdentityOutcome(action, decision, resolved, candidates, relations or [], did, n_fts, n_vec, provider, model)  # type: ignore[arg-type]

    if not candidates:
        return await _finish("no_candidates", None)

    relations: list[Candidate] = []
    for cand in candidates:
        res = await judge.judge_identity("goal", cand_text, cand.text)
        if not res.ok:
            if on_unavailable == "raise":
                raise SemanticJudgmentUnavailable(
                    f"goal identity judgment unavailable ({res.reason}); {len(candidates)} candidate(s) unresolved",
                    attempts=res.attempts)
            return await _finish("judge_unavailable", None, relations=relations,
                                 detail={"reason": res.reason, "unjudged": [c.id for c in candidates]})
        verdict = res.value
        cand.relation, cand.confidence = verdict["relation"], verdict["confidence"]
        if verdict["relation"] == "same":
            if verdict["confidence"] >= same_min_confidence:
                return await _finish("same", cand.id, provider=res.provider, model=res.model, action="reuse",
                                     relations=relations)
            cand.relation = "related"  # low-confidence "same" is never a merge
        if cand.relation in ("specializes", "generalizes", "related"):
            relations.append(cand)
    if not relations:
        final = "distinct"
    else:
        first = relations[0].relation
        final = {"specializes": "narrower", "generalizes": "broader"}.get(first, "related")
    return await _finish(final, None, provider=res.provider, model=res.model,
                         relations=relations, detail={"judged": len(candidates)})


async def propose_goal_relations(
    pool: asyncpg.Pool, new_goal_id: str, relations: list[Candidate], *, decision_id: Optional[str],
    provenance: str = "identity_resolution",
) -> int:
    """Store hierarchy edges implied by judged relations. Best-effort and
    OPTIONAL: hierarchy is never required for ingestion or retrieval, so a
    failure here is logged, not raised. Status stays 'proposed'."""
    n = 0
    for c in relations:
        if c.relation not in ("specializes", "generalizes"):
            continue
        specific, abstract = (new_goal_id, c.id) if c.relation == "specializes" else (c.id, new_goal_id)
        try:
            await pool.execute(
                "INSERT INTO goal_relations (specific_goal_id, abstract_goal_id, relation_type, status, confidence, "
                "provenance, decision_id) VALUES ($1::uuid, $2::uuid, 'SPECIALIZES', 'proposed', $3, $4, $5::uuid) "
                "ON CONFLICT DO NOTHING", specific, abstract, c.confidence, provenance, decision_id)
            n += 1
        except Exception:  # noqa: BLE001 -- hierarchy is optional; never blocks ingestion
            log.warning("goal_relation %s -> %s not stored", specific, abstract, exc_info=True)
    return n


# ---------------------------------------------------------- reconciliation

RECONCILE_LOCK = "reconcile_goals"


async def relink_procedures_of_merged_goals(pool: asyncpg.Pool) -> int:
    """Repair Procedures that a concurrent writer linked to a goal just before it was
    merged (the write-time trigger cannot see an uncommitted merge). Idempotent."""
    res = await pool.execute(
        "UPDATE procedures p SET achieves_goal_id = g.merged_into_id FROM goals g "
        "WHERE p.achieves_goal_id = g.id AND g.status = 'merged' AND g.merged_into_id IS NOT NULL")
    return int(res.split()[-1])


async def merge_goal(pool: asyncpg.Pool, loser_id: str, survivor_id: str, *, decision_id: Optional[str] = None) -> dict[str, int]:
    """Merge ``loser`` into ``survivor`` atomically: every Procedure
    that pointed at the loser now points at the survivor, hierarchy edges move,
    the loser's names become aliases, and the loser row becomes status='merged'
    (kept for audit; never deleted). Idempotent."""
    from app.services.shards import HOME_SHARD, list_shards, multi_shard, pools_for

    if await multi_shard(pool):
        return await _merge_goal_sharded(pool, loser_id, survivor_id)
    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext($1))", f"goal-merge:{loser_id}")
            state = await conn.fetchval("SELECT status FROM goals WHERE id = $1::uuid FOR UPDATE", loser_id)
            if state is None or state == "merged":
                return {"procedures": 0, "already_merged": 1}
            procs = int((await conn.execute(
                "UPDATE procedures SET achieves_goal_id = $2::uuid WHERE achieves_goal_id = $1::uuid", loser_id, survivor_id)).split()[-1])
            await conn.execute(
                "INSERT INTO goal_relations (specific_goal_id, abstract_goal_id, relation_type, status, confidence, provenance) "
                "SELECT CASE WHEN specific_goal_id = $1::uuid THEN $2::uuid ELSE specific_goal_id END, "
                "       CASE WHEN abstract_goal_id = $1::uuid THEN $2::uuid ELSE abstract_goal_id END, relation_type, status, confidence, provenance "
                "FROM goal_relations WHERE (specific_goal_id = $1::uuid OR abstract_goal_id = $1::uuid) "
                "AND NOT (specific_goal_id = $1::uuid AND abstract_goal_id = $2::uuid) "
                "AND NOT (abstract_goal_id = $1::uuid AND specific_goal_id = $2::uuid) "
                "ON CONFLICT DO NOTHING", loser_id, survivor_id)
            await conn.execute("DELETE FROM goal_relations WHERE specific_goal_id = $1::uuid OR abstract_goal_id = $1::uuid", loser_id)
            await conn.execute(
                "UPDATE goals g SET aliases = (SELECT ARRAY(SELECT DISTINCT a FROM unnest(g.aliases || l.aliases || ARRAY[l.canonical_name]) a "
                "WHERE a <> g.canonical_name)) FROM goals l WHERE g.id = $2::uuid AND l.id = $1::uuid", loser_id, survivor_id)
            await conn.execute(
                "UPDATE goals SET status = 'merged', merged_into_id = $2::uuid, reconciled_at = now() WHERE id = $1::uuid",
                loser_id, survivor_id)
            return {"procedures": procs, "already_merged": 0}


async def reconcile_goals(
    pool: asyncpg.Pool, *, embedder: Any = None, judge: Optional[SemanticJudge] = None, window_minutes: Optional[float] = 30.0,
    batch: int = 100, same_min_confidence: float = SAME_MIN_CONFIDENCE, lock_timeout_s: float = 120.0,
) -> dict[str, Any]:
    """Second identity pass for goals created concurrently (see migration 95).

    For every unreconciled goal G: judge G against goals created within
    ``window_minutes`` of G (``None`` = the whole corpus, for a legacy sweep).
    If the model says *same*, the goal with the LARGER id (newer uuid7) is merged
    into the smaller one -- a deterministic tie-break, so concurrent sweeps and
    both directions of a race converge on the same survivor. A judge outage
    leaves G unreconciled (retried next sweep); nothing is guessed. Single-flight
    across workers via a session advisory lock."""
    judge = judge if judge is not None else default_judge()
    out = {"checked": 0, "merged": 0, "deferred": 0, "skipped": False, "relinked": 0}
    async with pool.acquire() as lock:
        # BLOCK (bounded) instead of skipping: a sweep that started earlier works from an
        # older snapshot, so goals created after it must be picked up by the next sweep,
        # not silently left unreconciled because two workers finished at the same moment.
        try:
            await asyncio.wait_for(lock.execute("SELECT pg_advisory_lock(hashtext($1))", RECONCILE_LOCK), timeout=lock_timeout_s)
        except asyncio.TimeoutError:
            out["skipped"] = True
            return out
        try:
            from app.services.shards import HOME_SHARD, list_shards, multi_shard, pools_for

            sources: list[tuple[Any, Any]] = []      # (row, pool that owns it)
            shard_list = [s.shard_id for s in await list_shards(pool)] if await multi_shard(pool) else [HOME_SHARD]
            for sid in shard_list:
                try:
                    spool = pool if sid == HOME_SHARD else await pools_for(pool).get(sid)
                    for r in await spool.fetch(
                            "SELECT id::text AS id, canonical_name, description, scope_type, scope_entity_id, t_created, "
                            "embedding::text AS emb, embedding_model_id FROM goals "
                            "WHERE reconciled_at IS NULL AND status <> 'merged' AND t_invalid IS NULL ORDER BY id LIMIT $1", batch):
                        sources.append((r, spool))
                except Exception:  # noqa: BLE001 -- unreachable shard: its goals stay unreconciled until it is back
                    out["deferred"] += 1
            sources.sort(key=lambda t: t[0]["id"])
            for g, gpool in sources[:batch]:
                out["checked"] += 1
                if await gpool.fetchval("SELECT status FROM goals WHERE id = $1::uuid", g["id"]) == "merged":
                    continue
                emb = [float(x) for x in g["emb"].strip("[]").split(",")] if g["emb"] else None
                text = _goal_text({"canonical_name": g["canonical_name"], "description": g["description"]})
                cands, n_fts, n_vec = await generate_goal_candidates(
                    pool, text, scope_type=g["scope_type"] or "global", scope_entity_id=g["scope_entity_id"], embedding=emb,
                    embedding_model=g["embedding_model_id"], exclude_id=g["id"],
                    created_within=(g["t_created"], window_minutes) if window_minutes else None)
                unavailable = False
                for cand in cands:
                    res = await judge.judge_identity("goal", text, cand.text)
                    if not res.ok:
                        unavailable = True
                        break
                    cand.relation, cand.confidence = res.value["relation"], res.value["confidence"]
                    if cand.relation == "same" and cand.confidence >= same_min_confidence:
                        survivor, loser = (cand.id, g["id"]) if cand.id < g["id"] else (g["id"], cand.id)
                        did = await record_decision(
                            pool, object_type="goal", candidate_text=text, scope_type=g["scope_type"],
                            scope_entity_id=g["scope_entity_id"], decision="same", resolved_id=survivor, candidates=cands,
                            judge=judge, provider=res.provider, model=res.model, fts_n=n_fts, vec_n=n_vec, job_id=None,
                            idempotency_key=f"reconcile:{loser}:{survivor}", detail={"reconcile": True, "merged_loser": loser})
                        await merge_goal(pool, loser, survivor, decision_id=did)
                        out["merged"] += 1
                        break
                    if cand.relation in ("specializes", "generalizes"):
                        # hierarchy is added asynchronously: an edge whose other end did not exist at creation time
                        out["relations"] = out.get("relations", 0) + await propose_goal_relations(
                            pool, g["id"], [cand], decision_id=None, provenance="goal_reconciliation")
                if unavailable:
                    out["deferred"] += 1
                    continue
                await gpool.execute("UPDATE goals SET reconciled_at = now() WHERE id = $1::uuid AND reconciled_at IS NULL", g["id"])
        finally:
            await lock.execute("SELECT pg_advisory_unlock(hashtext($1))", RECONCILE_LOCK)
    out["relinked"] = await relink_procedures_of_merged_goals(pool)
    return out


async def _merge_goal_sharded(pool: asyncpg.Pool, loser_id: str, survivor_id: str) -> dict[str, int]:
    """merge_goal when more than one shard exists: procedures that achieve the loser may live on
    ANY shard, the loser row lives on its home shard, control tables (relations, names) are here.
    Each step is idempotent, so a crash mid-way is repaired by simply running the merge again."""
    from app.services.shards import HOME_SHARD, list_shards, pools_for

    sp = pools_for(pool)
    loser_shard = await pool.fetchval("SELECT home_shard_id FROM object_routes WHERE object_type='goal' AND object_id=$1::uuid", loser_id) or HOME_SHARD
    lpool = pool if loser_shard == HOME_SHARD else await sp.get(loser_shard)
    state = await lpool.fetchval("SELECT status FROM goals WHERE id = $1::uuid", loser_id)
    if state is None or state == "merged":
        return {"procedures": 0, "already_merged": 1}
    moved = 0
    for shard in await list_shards(pool):
        try:
            spool = pool if shard.shard_id == HOME_SHARD else await sp.get(shard.shard_id)
        except Exception:  # noqa: BLE001 -- an unreachable shard: procedures there are repaired by the relink sweep
            continue
        moved += int((await spool.execute(
            "UPDATE procedures SET achieves_goal_id = $2::uuid WHERE achieves_goal_id = $1::uuid", loser_id, survivor_id)).split()[-1])
    await pool.execute(
        "INSERT INTO goal_relations (specific_goal_id, abstract_goal_id, relation_type, status, confidence, provenance) "
        "SELECT CASE WHEN specific_goal_id = $1::uuid THEN $2::uuid ELSE specific_goal_id END, "
        "       CASE WHEN abstract_goal_id = $1::uuid THEN $2::uuid ELSE abstract_goal_id END, relation_type, status, confidence, provenance "
        "FROM goal_relations WHERE (specific_goal_id = $1::uuid OR abstract_goal_id = $1::uuid) "
        "AND NOT (specific_goal_id = $1::uuid AND abstract_goal_id = $2::uuid) "
        "AND NOT (abstract_goal_id = $1::uuid AND specific_goal_id = $2::uuid) ON CONFLICT DO NOTHING", loser_id, survivor_id)
    await pool.execute("DELETE FROM goal_relations WHERE specific_goal_id = $1::uuid OR abstract_goal_id = $1::uuid", loser_id)
    lrow = await lpool.fetchrow("SELECT canonical_name, aliases FROM goals WHERE id = $1::uuid", loser_id)
    survivor_shard = await pool.fetchval("SELECT home_shard_id FROM object_routes WHERE object_type='goal' AND object_id=$1::uuid", survivor_id) or HOME_SHARD
    surv_pool = pool if survivor_shard == HOME_SHARD else await sp.get(survivor_shard)
    await surv_pool.execute(
        "UPDATE goals g SET aliases = (SELECT ARRAY(SELECT DISTINCT a FROM unnest(g.aliases || $2::text[]) a WHERE a <> g.canonical_name)) "
        "WHERE g.id = $1::uuid", survivor_id, list(lrow["aliases"] or []) + [lrow["canonical_name"]])
    await lpool.execute("UPDATE goals SET status = 'merged', merged_into_id = $2::uuid, reconciled_at = now() WHERE id = $1::uuid", loser_id, survivor_id)
    await pool.execute("DELETE FROM goal_names WHERE goal_id = $1::uuid", loser_id)
    from app.services.search_projection import enqueue
    for oid in (loser_id, survivor_id):
        await enqueue(pool, "goal", oid)
    return {"procedures": moved, "already_merged": 0}
