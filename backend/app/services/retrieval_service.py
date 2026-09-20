"""
THE canonical retrieval service (docs/retrieval_architecture.md).

REST (/search, /search/recommend, /goals/search, /procedures/search,
/solutions/search) and MCP (search_goals, search_procedures, find_best_solution,
find_best_way's lookup step) all delegate here. There is no second ranker.

    query + local Claims
      -> select a SMALL working set of local Claims (budget, never "all")
      -> compact query representation (query + top claim statements)
      TIER 1  Goal:       FTS + ANN on goal_search_index -> RRF fusion -> top-k
                          -> JEV/NLI judge (kind=task_goal) -> resolved Goal(s)
      TIER 2  Procedure:  goal-CONSTRAINED FTS + ANN on procedure_search_index
                          -> RRF -> hydrate ONLY the shards holding the candidates
                          (one batched query per shard) -> hard constraints
                          (scope/staleness/verification/preconditions -- facts)
                          -> JEV/NLI judge (kind=task_procedure, local claims in
                          context) -> evidence (verification stats, linked
                          claims) -> Pareto / policy selection

Deterministic code generates candidates and applies hard FACTUAL constraints.
Semantic judgment is the model's. Failure modes are explicit and tested:

    mode        meaning
    ----------  --------------------------------------------------------------
    jev         every judgment came from JEV
    model       at least one judgment came from a fallback model (NLI tier)
    candidates_only  no semantic provider answered: fused candidates are returned,
                `degraded=True`, nothing is marked judged, no winner is chosen.
There is NO silent switch to a heuristic ranker, and no fabricated certainty.
Hierarchy (goal_relations) is never read.
"""
from __future__ import annotations

import asyncio
import hashlib
import logging
import math
import re
import time
from dataclasses import dataclass, field
from typing import Any, Optional, Sequence

import asyncpg

from app import telemetry as _tel
from app.services.access import AccessScope, visibility_predicate
from app.services.embeddings import to_pgvector
from app.services.identity_resolution import default_judge, fts_or_query, rrf_fuse
from app.services.semantic.chain import SemanticJudge
from app.services.shards import ShardPools, hydrate_rows, pools_for

log = logging.getLogger(__name__)

MODE_JEV, MODE_MODEL, MODE_CANDIDATES = "jev", "model", "candidates_only"


@dataclass(frozen=True)
class RetrievalConfig:
    search_top_k: int = 20          # per-leg candidate depth (FTS and ANN)
    rerank_top_k: int = 8           # fused candidates sent to the semantic judge
    local_claim_budget: int = 12    # working-set size (never all local claims)
    claim_context_chars: int = 900
    goal_resolve_max: int = 3       # resolved goals carried into tier 2
    unjudged_goal_fanin: int = 3    # degraded mode: top fused goals carried into tier 2
    procedure_alternatives: int = 5
    min_confidence: float = 0.6     # model confidence needed to call a verdict "firm"
    judge_concurrency: int = 4


@dataclass
class LocalClaim:
    id: str
    statement: str


@dataclass
class QueryContext:
    query: str
    claims: list[LocalClaim]
    text: str                       # query + local claims: used ONLY by the judge (never for candidate generation)
    dropped_claims: int = 0

    @property
    def claim_ids(self) -> list[str]:
        return [c.id for c in self.claims]


@dataclass
class RetrievalMeta:
    mode: str = MODE_CANDIDATES
    degraded: bool = False
    degraded_reasons: list[str] = field(default_factory=list)
    providers: list[str] = field(default_factory=list)
    embedding_model: Optional[str] = None
    local_claim_ids: list[str] = field(default_factory=list)
    counts: dict[str, int] = field(default_factory=dict)
    latency_ms: dict[str, float] = field(default_factory=dict)
    shards_touched: list[str] = field(default_factory=list)
    unavailable_shards: dict[str, str] = field(default_factory=dict)
    missing_ids: list[str] = field(default_factory=list)
    disqualified: list[dict] = field(default_factory=list)

    def degrade(self, reason: str) -> None:
        self.degraded = True
        if reason not in self.degraded_reasons:
            self.degraded_reasons.append(reason)

    def as_dict(self) -> dict:
        return {k: v for k, v in self.__dict__.items()}


# ------------------------------------------------------------ query context

_TOK = re.compile(r"[a-z0-9]+")


def _overlap(a: str, b: str) -> float:
    ta, tb = set(_TOK.findall(a.lower())), set(_TOK.findall(b.lower()))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / math.sqrt(len(ta) * len(tb))


async def build_query_context(
    query: str, local_claims: Sequence[LocalClaim | dict] = (), *, embedder: Any = None, cfg: RetrievalConfig = RetrievalConfig(),
) -> QueryContext:
    """Working set of local Claims: at most ``cfg.local_claim_budget``, ranked by
    relevance to the query (embedding cosine when an embedder is available, token
    overlap otherwise -- this only sizes a prompt budget; it decides nothing).
    Thousands of raw claims are never concatenated."""
    claims = [c if isinstance(c, LocalClaim) else LocalClaim(str(c["id"]), str(c.get("statement") or c.get("name") or "")) for c in local_claims]
    claims = [c for c in claims if c.statement.strip()]
    scores = [_overlap(query, c.statement) for c in claims]
    if embedder is not None and claims:
        try:
            qv = await embedder.embed_one(query, input_type="query")
            cvs = await asyncio.gather(*[embedder.embed_one(c.statement, input_type="document") for c in claims[:200]])
            scores = [sum(x * y for x, y in zip(qv, v)) for v in cvs] + scores[len(cvs):]
        except Exception:  # noqa: BLE001 -- budget selection falls back to overlap
            log.info("local-claim embedding unavailable; using token overlap for the working set")
    order = sorted(range(len(claims)), key=lambda i: (-scores[i], claims[i].id))[: cfg.local_claim_budget]
    chosen = [claims[i] for i in order]
    text = query
    if chosen:
        ctx = "; ".join(c.statement.strip() for c in chosen)[: cfg.claim_context_chars]
        text = f"{query}\nKnown in this environment: {ctx}"
    return QueryContext(query=query, claims=chosen, text=text, dropped_claims=len(claims) - len(chosen))


# ----------------------------------------------------------------- SQL legs


@dataclass
class Hit:
    id: str
    name: str
    text: str
    home_shard_id: str
    fts_rank: Optional[int] = None
    vec_rank: Optional[int] = None
    vec_distance: Optional[float] = None
    rrf: float = 0.0
    relation: Optional[str] = None
    confidence: Optional[float] = None
    judged: bool = False
    extra: dict = field(default_factory=dict)

    def brief(self) -> dict:
        d = {"id": self.id, "name": self.name, "home_shard_id": self.home_shard_id, "rrf": round(self.rrf, 6),
             "fts_rank": self.fts_rank, "vec_rank": self.vec_rank, "judged": self.judged,
             "relation": self.relation, "confidence": self.confidence}
        return {k: v for k, v in d.items() if v is not None}


async def _legs(
    pool: asyncpg.Pool, *, table: str, id_col: str, name_col: str, text_expr: str, extra_cols: str,
    ctx_text: str, embedding: Optional[list[float]], embedding_model: Optional[str], scope: AccessScope,
    where_extra: str, extra_params: list, cfg: RetrievalConfig,
) -> tuple[list[Hit], int, int]:
    """FTS + ANN legs over one projection table, RRF-fused. ``where_extra`` may
    reference $1.. for ``extra_params`` (bound first)."""
    base_params = list(extra_params)
    vis_sql, vis_params = visibility_predicate(scope, param_index=len(base_params) + 1)
    params = base_params + vis_params
    where = f"({where_extra}) AND {vis_sql}"
    by_id: dict[str, Hit] = {}
    fts_ids: list[str] = []
    vec_ids: list[str] = []
    cols = f"{id_col}::text AS id, {name_col} AS name, {text_expr} AS text, home_shard_id{extra_cols}"

    q = fts_or_query(ctx_text)
    if q:
        n = len(params) + 1
        rows = await pool.fetch(
            f"SELECT {cols}, ts_rank_cd(search_tsv, to_tsquery('english', ${n})) AS r FROM {table} "
            f"WHERE {where} AND search_tsv @@ to_tsquery('english', ${n}) ORDER BY r DESC, {id_col} LIMIT {cfg.search_top_k}",
            *params, q)
        for rank, r in enumerate(rows, 1):
            h = by_id.setdefault(r["id"], Hit(r["id"], r["name"], r["text"], r["home_shard_id"], extra=_extra(r)))
            h.fts_rank = rank
            fts_ids.append(r["id"])
    if embedding is not None and embedding_model:
        n = len(params) + 1
        rows = await pool.fetch(
            f"SELECT {cols}, embedding <=> ${n}::vector AS dist FROM {table} "
            f"WHERE {where} AND embedding IS NOT NULL AND embedding_model = ${n + 1} "
            f"ORDER BY dist ASC, {id_col} LIMIT {cfg.search_top_k}",
            *params, to_pgvector(embedding), embedding_model)
        for rank, r in enumerate(rows, 1):
            h = by_id.setdefault(r["id"], Hit(r["id"], r["name"], r["text"], r["home_shard_id"], extra=_extra(r)))
            h.vec_rank, h.vec_distance = rank, float(r["dist"])
            vec_ids.append(r["id"])
    for oid, sc in rrf_fuse(fts_ids, vec_ids).items():
        by_id[oid].rrf = sc
    return sorted(by_id.values(), key=lambda h: (-h.rrf, h.id)), len(fts_ids), len(vec_ids)


def _extra(r) -> dict:
    return {k: r[k] for k in r.keys() if k not in ("id", "name", "text", "home_shard_id", "r", "dist")}


# ------------------------------------------------------------ semantic step


async def _judge_all(
    judge: SemanticJudge, kind: str, ctx_text: str, hits: list[Hit], cfg: RetrievalConfig, meta: RetrievalMeta, *, stage: str,
) -> None:
    """Judge ``hits`` (in place). Sets relation/confidence/judged; records mode and
    providers on ``meta``. Never substitutes a heuristic: a failed judgment leaves
    ``judged=False``."""
    if not hits:
        return
    sem = asyncio.Semaphore(cfg.judge_concurrency)
    t0 = time.monotonic()

    async def one(h: Hit):
        async with sem:
            return await judge.judge_identity(kind, ctx_text, h.text)

    results = await asyncio.gather(*[one(h) for h in hits], return_exceptions=True)
    ok = 0
    for h, res in zip(hits, results):
        if isinstance(res, BaseException) or not res.ok:
            continue
        h.relation, h.confidence, h.judged = res.value["relation"], res.value["confidence"], True
        ok += 1
        if res.provider and res.provider not in meta.providers:
            meta.providers.append(res.provider)
    if meta.providers:
        meta.mode = MODE_JEV if meta.providers == ["jev"] else MODE_MODEL
    meta.latency_ms[f"{stage}_rerank"] = (time.monotonic() - t0) * 1000
    meta.counts[f"{stage}_judged"] = ok
    if ok == 0:
        meta.degrade(f"{stage}: no semantic provider answered (JEV and NLI/model rerankers unavailable)")
    elif ok < len(hits):
        meta.degrade(f"{stage}: {len(hits) - ok} of {len(hits)} candidates could not be judged")


async def _catch_up_projection(pool: asyncpg.Pool, meta: RetrievalMeta, *, max_pending: int = 500) -> None:
    """Read-your-writes: knowledge written a moment ago must be retrievable even if no drainer has run yet.
    If the outbox has a small backlog, apply it inline before searching; a large backlog is only reported
    (`projection_lag`), never blocking a request."""
    try:
        n = await pool.fetchval("SELECT count(*) FROM (SELECT 1 FROM projection_outbox WHERE status = 'pending' LIMIT $1) x", max_pending + 1)
        if not n:
            return
        if n > max_pending:
            meta.degrade(f"projection backlog > {max_pending}: very recent writes may not be searchable yet")
            return
        from app.services.search_projection import drain_outbox
        await drain_outbox(pool, batch=max_pending, pools=pools_for(pool), max_batches=1)
    except Exception:  # noqa: BLE001 -- freshness is best-effort; retrieval proceeds on what is projected
        log.info("projection catch-up skipped", exc_info=True)


# ------------------------------------------------------------------- tier 1


@dataclass
class GoalSearchResult:
    resolved: list[Hit]
    candidates: list[Hit]
    resolution: str            # 'matches' | 'partial' | 'unjudged' | 'none'


async def search_goals(
    pool: asyncpg.Pool, ctx: QueryContext, *, scope: AccessScope, embedder: Any = None, judge: Optional[SemanticJudge] = None,
    cfg: RetrievalConfig = RetrievalConfig(), meta: Optional[RetrievalMeta] = None,
) -> GoalSearchResult:
    meta = meta if meta is not None else RetrievalMeta()
    judge = judge if judge is not None else default_judge()
    await _catch_up_projection(pool, meta)
    emb, model = None, None
    if embedder is not None:
        t0 = time.monotonic()
        try:
            emb = await embedder.embed_one(ctx.query, input_type="query")
            model = embedder.embedding_model_id()
            meta.embedding_model = model
        except Exception:  # noqa: BLE001 -- FTS-only, flagged
            meta.degrade("embedding provider unavailable: lexical candidates only")
        meta.latency_ms["embed"] = (time.monotonic() - t0) * 1000
    with _tel.span("retrieval.goal_search", kind="RETRIEVER", on_error=_tel.FailureCode.RETRIEVAL_ERROR) as sp:
        t0 = time.monotonic()
        cands, n_fts, n_vec = await _legs(
            pool, table="goal_search_index", id_col="goal_id", name_col="canonical_name",
            text_expr="canonical_name || COALESCE(': ' || short_description, '')", extra_cols="",
            ctx_text=ctx.query, embedding=emb, embedding_model=model, scope=scope,
            where_extra="status IN ('active', 'candidate')", extra_params=[], cfg=cfg)
        meta.latency_ms["goal_search"] = (time.monotonic() - t0) * 1000
        meta.counts.update(goal_fts_candidates=n_fts, goal_vector_candidates=n_vec, goal_fused=len(cands))
        _tel.set_attrs(sp, fts=n_fts, vector=n_vec, fused=len(cands))
    top = cands[: cfg.rerank_top_k]
    await _judge_all(judge, "task_goal", ctx.text, top, cfg, meta, stage="goal")
    judged = [h for h in top if h.judged]
    matches = sorted((h for h in judged if h.relation == "matches" and (h.confidence or 0) >= cfg.min_confidence),
                     key=lambda h: (-(h.confidence or 0), -h.rrf, h.id))
    partial = sorted((h for h in judged if h.relation in ("partial", "matches")),
                     key=lambda h: (-(h.confidence or 0), -h.rrf, h.id))
    if matches:
        return GoalSearchResult(matches[: cfg.goal_resolve_max], cands, "matches")
    if partial:
        return GoalSearchResult(partial[: cfg.goal_resolve_max], cands, "partial")
    if not judged and top:
        # Degraded: no semantic verdict. Carry the top fused goals forward, but
        # nothing is marked resolved -- callers see resolution == 'unjudged'.
        return GoalSearchResult(top[: cfg.unjudged_goal_fanin], cands, "unjudged")
    return GoalSearchResult([], cands, "none")


# ------------------------------------------------------------------- tier 2


def wilson_lcb(successes: int, attempts: int, z: float = 1.96) -> Optional[float]:
    """Lower confidence bound of the success rate; None when there is no evidence."""
    if attempts <= 0:
        return None
    p = successes / attempts
    denom = 1 + z * z / attempts
    centre = p + z * z / (2 * attempts)
    margin = z * math.sqrt((p * (1 - p) + z * z / (4 * attempts)) / attempts)
    return max(0.0, (centre - margin) / denom)


def pareto_front(items: list[dict], keys: Sequence[str]) -> list[dict]:
    """Non-dominated set maximising every key; a None value is worse than any number
    (an unproven procedure cannot dominate a proven one on evidence, and vice versa
    an unproven one is only dominated, never promoted)."""
    def v(it, k):
        return -math.inf if it[k] is None else it[k]

    front = []
    for a in items:
        dominated = any(all(v(b, k) >= v(a, k) for k in keys) and any(v(b, k) > v(a, k) for k in keys) for b in items if b is not a)
        if not dominated:
            front.append(a)
    return front


def _hydrate_cols() -> str:
    from app.services.applicability import PROCEDURE_COLS_NO_HEAVY
    return PROCEDURE_COLS_NO_HEAVY + ", achieves_goal_id, source_locator, source_artifacts"


async def _fetch_procedures(pool: Any, ids: list[str]):
    return await pool.fetch(f"SELECT {_hydrate_cols()} FROM procedures WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL", ids)


@dataclass
class ProcedureSearchResult:
    ranked: list[dict]
    selected: Optional[dict]
    alternatives: list[dict]
    frontier: list[dict]
    selection_reason: str
    diagnostics: list = field(default_factory=list)      # ApplicabilityResult for EVERY hydrated candidate (hard-constraint verdicts)


async def retrieve_procedures(
    pool: asyncpg.Pool, ctx: QueryContext, goals: Sequence[Hit], *, scope: AccessScope, pools: Optional[ShardPools] = None,
    embedder: Any = None, judge: Optional[SemanticJudge] = None, cfg: RetrievalConfig = RetrievalConfig(),
    meta: Optional[RetrievalMeta] = None, current_scope: Optional[dict] = None, require_verified: bool = False,
    invariant_bindings: Optional[dict] = None, excluded_procedure_ids: Optional[Sequence[str]] = None,
) -> ProcedureSearchResult:
    meta = meta if meta is not None else RetrievalMeta()
    judge = judge if judge is not None else default_judge()
    pools = pools or pools_for(pool)
    goal_ids = [g.id for g in goals]
    if not goal_ids:
        return ProcedureSearchResult([], None, [], [], "no goal resolved: procedures are never searched globally")
    emb = None
    model = meta.embedding_model
    if embedder is not None:
        try:
            emb = await embedder.embed_one(ctx.query, input_type="query")
            model = embedder.embedding_model_id()
        except Exception:  # noqa: BLE001
            meta.degrade("embedding provider unavailable: lexical procedure candidates only")
    t0 = time.monotonic()
    with _tel.span("retrieval.procedure_search", kind="RETRIEVER", on_error=_tel.FailureCode.RETRIEVAL_ERROR) as sp:
        cands, n_fts, n_vec = await _legs(
            pool, table="procedure_search_index", id_col="procedure_id", name_col="name",
            text_expr="name || COALESCE(': ' || summary, '') || COALESCE(' preconditions: ' || preconditions_summary, '')",
            extra_cols=", procedure_row_id::text AS procedure_row_id, goal_id::text AS goal_id",
            ctx_text=ctx.query, embedding=emb, embedding_model=model, scope=scope,
            where_extra="goal_id = ANY($1::uuid[]) AND status = 'active'", extra_params=[goal_ids], cfg=cfg)
        _tel.set_attrs(sp, fts=n_fts, vector=n_vec, fused=len(cands))
    meta.latency_ms["procedure_search"] = (time.monotonic() - t0) * 1000
    meta.counts.update(procedure_fts_candidates=n_fts, procedure_vector_candidates=n_vec, procedure_fused=len(cands))
    cands = cands[: cfg.rerank_top_k]
    if not cands:
        return ProcedureSearchResult([], None, [], [], "no procedure is linked to the resolved goal(s)")

    # -- hydrate canonical rows: one batched query per involved shard only
    t0 = time.monotonic()
    hyd = await hydrate_rows(pools, {c.extra["procedure_row_id"]: c.home_shard_id for c in cands}, _fetch_procedures)
    meta.latency_ms["hydrate"] = (time.monotonic() - t0) * 1000
    meta.shards_touched = sorted(hyd.shard_batches)
    meta.unavailable_shards.update(hyd.unavailable_shards)
    meta.missing_ids.extend(hyd.missing_ids)
    if hyd.partial:
        meta.degrade("shard unavailable: " + ", ".join(sorted(hyd.unavailable_shards)) + " (candidates on it were NOT evaluated)")

    # -- hard factual constraints (existing cascade: scope/exclusions/staleness/verification/preconditions)
    from app.services.applicability import check_hard_constraints

    state_cache: dict = {}
    live: list[tuple[Hit, dict]] = []
    diagnostics: list = []
    excluded = {str(x) for x in (excluded_procedure_ids or [])}
    for c in cands:
        row = hyd.rows.get(c.extra["procedure_row_id"])
        if row is None or str(row["procedure_id"]) in excluded:
            continue
        row["_similarity_score"] = c.rrf
        res = await check_hard_constraints(pool, row, current_scope=current_scope, access_scope=scope,
                                           require_verified=require_verified, state_cache=state_cache,
                                           invariant_bindings=invariant_bindings)
        res.procedure = row
        res.similarity_score = c.rrf
        diagnostics.append(res)
        if res.applicable:
            live.append((c, row))
        else:
            meta.disqualified.append({"procedure_id": c.id, "failed": res.failed_constraints[:3]})
    meta.counts["procedure_after_hard_constraints"] = len(live)

    # -- semantic: does this method apply here (local claims are in ctx.text)
    hits = [c for c, _ in live]
    for c, row in live:
        c.text = f"{row['name']}: {row['display_description'] or row['goal']}. {row['capability_statement'] or ''}"
    await _judge_all(judge, "task_procedure", ctx.text, hits, cfg, meta, stage="procedure")

    items: list[dict] = []
    for c, row in live:
        if c.judged and c.relation == "not_applicable" and (c.confidence or 0) >= cfg.min_confidence:
            meta.disqualified.append({"procedure_id": c.id, "failed": ["semantic:not_applicable"]})
            continue
        stats = row["verification_stats"] or {}
        att, suc = int(stats.get("attempts", 0)), int(stats.get("successes", 0))
        app_score = None
        if c.judged:
            app_score = (c.confidence or 0.0) * (1.0 if c.relation == "applies" else 0.5)
        items.append({
            "procedure_id": c.id, "id": c.extra["procedure_row_id"], "goal_id": c.extra["goal_id"],
            "name": row["name"], "display_name": row["display_name"] or row["name"],
            "goal": row["goal"], "verification_state": row["verification_state"], "version": row["version"],
            "home_shard_id": c.home_shard_id, "rrf": c.rrf, "judged": c.judged, "relation": c.relation,
            "confidence": c.confidence, "applicability_score": app_score,
            "evidence": {"attempts": att, "successes": suc, "lcb": wilson_lcb(suc, att)},
            "evidence_lcb": wilson_lcb(suc, att),
            "_row": row,
        })

    # -- supporting / contradicting claims linked to the candidates (one query)
    if items:
        try:
            rows = await pool.fetch(
                "SELECT procedure_id::text AS pid, claim_id::text AS cid, role FROM procedure_claim_refs "
                "WHERE procedure_id = ANY($1::uuid[]) AND t_invalid IS NULL", [i["procedure_id"] for i in items])
            by = {}
            for r in rows:
                by.setdefault(r["pid"], []).append({"claim_id": r["cid"], "role": r["role"]})
            for i in items:
                i["claims"] = by.get(i["procedure_id"], [])
        except Exception:  # noqa: BLE001 -- evidence enrichment is best-effort
            meta.degrade("linked-claim lookup failed")

    result = _select(items, meta, cfg)
    # ApplicabilityResult order: applicable candidates in selection order first, then the rest by fused rank
    order = {i["procedure_id"]: k for k, i in enumerate(result.ranked)}
    diagnostics.sort(key=lambda d: (0 if d.applicable and str(d.procedure["procedure_id"]) in order else 1,
                                    order.get(str(d.procedure["procedure_id"]), 10_000), -(d.similarity_score or 0.0)))
    result.diagnostics = diagnostics
    return result


def _select(items: list[dict], meta: RetrievalMeta, cfg: RetrievalConfig) -> ProcedureSearchResult:
    # deterministic presentation order only (fused rank); this is NOT a semantic ranking
    ranked = sorted(items, key=lambda i: (-(i["applicability_score"] or 0.0), -i["rrf"], i["procedure_id"]))
    judged = [i for i in ranked if i["judged"] and i["relation"] in ("applies", "partial")]
    if meta.mode == MODE_CANDIDATES or not judged:
        return ProcedureSearchResult(ranked, None, ranked[: cfg.procedure_alternatives], [],
                                     "no semantic judgment available: candidates returned unranked, no winner chosen")
    with_ev = [i for i in judged if i["evidence_lcb"] is not None]
    if not with_ev:
        return ProcedureSearchResult(ranked, None, judged[: cfg.procedure_alternatives], judged,
                                     "no execution evidence for any applicable procedure: no winner invented")
    front = pareto_front(judged, ("applicability_score", "evidence_lcb"))
    front.sort(key=lambda i: (-(i["evidence_lcb"] or 0.0), -(i["applicability_score"] or 0.0), i["procedure_id"]))
    selected = front[0]
    alts = [i for i in judged if i is not selected][: cfg.procedure_alternatives]
    return ProcedureSearchResult(ranked, selected, alts, front,
                                 "selected by evidence (Wilson lower bound) among non-dominated applicable procedures")


# ------------------------------------------------------------------ facade


async def find_best_way(
    pool: asyncpg.Pool, goal_text: str, *, local_claims: Sequence[LocalClaim | dict] = (), scope: AccessScope,
    embedder: Any = None, judge: Optional[SemanticJudge] = None, pools: Optional[ShardPools] = None,
    cfg: RetrievalConfig = RetrievalConfig(), current_scope: Optional[dict] = None, require_verified: bool = False,
    record: bool = True,
) -> dict[str, Any]:
    """The one recommendation entry point: Goal resolution -> Procedure retrieval
    -> applicability/evidence -> selection. Never raises for semantic outages."""
    meta = RetrievalMeta()
    t0 = time.monotonic()
    ctx = await build_query_context(goal_text, local_claims, embedder=embedder, cfg=cfg)
    meta.local_claim_ids = ctx.claim_ids
    g = await search_goals(pool, ctx, scope=scope, embedder=embedder, judge=judge, cfg=cfg, meta=meta)
    p = await retrieve_procedures(pool, ctx, g.resolved, scope=scope, pools=pools, embedder=embedder, judge=judge, cfg=cfg,
                                  meta=meta, current_scope=current_scope, require_verified=require_verified)
    meta.latency_ms["total"] = (time.monotonic() - t0) * 1000
    def _public(item):
        return None if item is None else {k: v for k, v in item.items() if k != "_row"}

    out = {
        "goal": goal_text,
        "goal_resolution": {"status": g.resolution, "goals": [h.brief() for h in g.resolved],
                            "candidates": [h.brief() for h in g.candidates[: cfg.rerank_top_k]]},
        "recommendation": _public(p.selected),
        "alternatives": [_public(i) for i in p.alternatives],
        "frontier": [i["procedure_id"] for i in p.frontier],
        "procedures": [_public(i) for i in p.ranked],
        "confidence": ("none" if p.selected is None else "high" if (p.selected["confidence"] or 0) >= 0.85 else "medium"),
        "reason": p.selection_reason,
        "retrieval": meta.as_dict(),
        "query_context": {"local_claim_ids": ctx.claim_ids, "dropped_local_claims": ctx.dropped_claims},
    }
    if record:
        await _record(pool, goal_text, scope, g, p, meta)
    return out


async def _record(pool, query, scope, g: GoalSearchResult, p: ProcedureSearchResult, meta: RetrievalMeta) -> None:
    try:
        await pool.execute(
            "INSERT INTO retrieval_decisions (query_sha256, viewer_id, goal_ids, procedure_ids, selected_procedure_id, "
            "local_claim_ids, mode, degraded, detail) VALUES ($1, $2, $3::uuid[], $4::uuid[], $5::uuid, $6, $7, $8, $9::jsonb)",
            hashlib.sha256(query.encode()).hexdigest(), getattr(scope, "viewer_id", None), [h.id for h in g.resolved],
            [i["procedure_id"] for i in p.ranked], p.selected["procedure_id"] if p.selected else None,
            meta.local_claim_ids, meta.mode, meta.degraded,
            {"goal_resolution": g.resolution, "reasons": meta.degraded_reasons, "providers": meta.providers,
             "shards": meta.shards_touched, "unavailable_shards": meta.unavailable_shards,
             "counts": meta.counts, "selection": p.selection_reason})
    except Exception:  # noqa: BLE001 -- the decision log never fails a retrieval
        log.warning("retrieval decision not recorded", exc_info=True)


# ------------------------------------------------------------------- adapters for other entry points
# Every REST/MCP surface that needs procedures goes through one of these three functions; there is no other ranker.


@dataclass
class GoalFirstResult:
    goals: GoalSearchResult
    procedures: ProcedureSearchResult
    meta: RetrievalMeta
    ctx: QueryContext


async def search_procedures(
    pool: asyncpg.Pool, query_text: str, *, scope: AccessScope, local_claims: Sequence[LocalClaim | dict] = (),
    embedder: Any = None, judge: Optional[SemanticJudge] = None, pools: Optional[ShardPools] = None,
    cfg: RetrievalConfig = RetrievalConfig(), current_scope: Optional[dict] = None, require_verified: bool = False,
    invariant_bindings: Optional[dict] = None, excluded_procedure_ids: Optional[Sequence[str]] = None,
) -> GoalFirstResult:
    """Tier 1 -> Tier 2 without the recommendation envelope: the full ranked/diagnosed procedure set."""
    meta = RetrievalMeta()
    t0 = time.monotonic()
    ctx = await build_query_context(query_text, local_claims, embedder=embedder, cfg=cfg)
    meta.local_claim_ids = ctx.claim_ids
    g = await search_goals(pool, ctx, scope=scope, embedder=embedder, judge=judge, cfg=cfg, meta=meta)
    p = await retrieve_procedures(pool, ctx, g.resolved, scope=scope, pools=pools, embedder=embedder, judge=judge, cfg=cfg,
                                  meta=meta, current_scope=current_scope, require_verified=require_verified,
                                  invariant_bindings=invariant_bindings, excluded_procedure_ids=excluded_procedure_ids)
    meta.latency_ms["total"] = (time.monotonic() - t0) * 1000
    return GoalFirstResult(g, p, meta, ctx)


async def diagnose_procedures(pool: asyncpg.Pool, query_text: str, **kw) -> tuple[list, GoalFirstResult]:
    """Hard-constraint verdicts (ApplicabilityResult, with failed_constraints / UNKNOWN preconditions preserved) for
    goal-first candidates, applicable ones first. Used by MCP routing, which must tell "inapplicable" from "blocked
    on an unknown precondition". Returns (diagnostics, full result)."""
    res = await search_procedures(pool, query_text, **kw)
    return res.procedures.diagnostics, res


async def search_goal_candidates(
    pool: asyncpg.Pool, *, query_text: Optional[str], query_embedding: Optional[list[float]] = None,
    embedding_model: Optional[str] = None, scope: AccessScope, status: Optional[str] = None, limit: int = 10,
    cfg: RetrievalConfig = RetrievalConfig(),
) -> list[dict]:
    """Judge-free Goal search (search-as-you-type / browse): FTS + ANN over the global goal projection, RRF-fused,
    canonical rows hydrated by shard. Same candidate machinery as Tier 1, without the semantic rerank."""
    if not query_text and not query_embedding:
        raise ValueError("search_goal_candidates requires query_text and/or query_embedding")
    where = f"status IN ('active', 'candidate')" if status is None else "status = $1"
    params = [] if status is None else [status]
    cfg2 = replace_cfg(cfg, search_top_k=max(limit * 3, 10))
    cands, _n, _m = await _legs(
        pool, table="goal_search_index", id_col="goal_id", name_col="canonical_name",
        text_expr="canonical_name || COALESCE(': ' || short_description, '')", extra_cols="",
        ctx_text=query_text or "", embedding=query_embedding, embedding_model=embedding_model, scope=scope,
        where_extra=where, extra_params=params, cfg=cfg2)
    top = cands[:limit]
    if not top:
        return []
    pools = pools_for(pool)

    async def fetch(p, ids):
        return await p.fetch(
            "SELECT id, canonical_name, description, status, scope_type, scope_entity_id, expected_outcome, "
            "verification_requirement, home_shard_id FROM goals WHERE id = ANY($1::uuid[])", ids)

    hyd = await hydrate_rows(pools, {h.id: h.home_shard_id for h in top}, fetch)
    return [{**hyd.rows[h.id], "id": h.id} for h in top if h.id in hyd.rows]


def replace_cfg(cfg: RetrievalConfig, **kw) -> RetrievalConfig:
    from dataclasses import replace
    return replace(cfg, **kw)
