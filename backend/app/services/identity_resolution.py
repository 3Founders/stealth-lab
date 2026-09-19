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
