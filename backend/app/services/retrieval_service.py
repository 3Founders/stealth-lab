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
                          -> accepted visible hierarchy expansion
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
Accepted, visible goal_relations are used only for bounded candidate expansion.
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
from app.services.access import AccessScope, TenantScope, tenant_predicate, visibility_predicate
from app.services.embeddings import to_pgvector
from app.services.hierarchical_goal_routing import (
    HierarchicalGoalRoutingConfig,
    route_hierarchical_goal_candidates,
)
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
    include_hierarchy_paths: bool = True
    # Hierarchy expansion (bounded; the flat candidates always remain the baseline)
    hierarchy_max_hops: int = 2         # parents of parents / children of children
    hierarchy_max_fanout: int = 8       # neighbours kept per Goal and direction (most relevant first)
    hierarchy_max_candidates: int = 32  # neighbour Goals kept in total
    hierarchy_judge_top_k: int = 8      # neighbours sent to the contextual Goal judge
    hierarchy_seed_max: int = 3         # judged candidates expanded when nothing matched


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
    query_embedding: Optional[list[float]] = None   # set by search_goals; ranks hierarchy neighbours
    embedding_model: Optional[str] = None

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
    goal_routing: Optional[dict[str, Any]] = None

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
    hierarchy: Optional[dict] = None

    def brief(self) -> dict:
        d = {"id": self.id, "name": self.name, "home_shard_id": self.home_shard_id, "rrf": round(self.rrf, 6),
             "fts_rank": self.fts_rank, "vec_rank": self.vec_rank, "judged": self.judged,
             "relation": self.relation, "confidence": self.confidence}
        d = {k: v for k, v in d.items() if v is not None}
        if self.hierarchy is not None:
            d["hierarchy"] = self.hierarchy
        return d


def _default_tenant_scope(scope: AccessScope) -> TenantScope:
    return TenantScope.unrestricted() if scope.is_unrestricted else TenantScope.commons()


def _goal_visibility_predicate(
    scope: AccessScope, alias: str = "", param_index: int = 1
) -> tuple[str, list[Any]]:
    prefix = f"{alias}." if alias else ""
    if scope.is_unrestricted:
        return "TRUE", []
    if scope.viewer_id is None:
        return f"{prefix}visibility = 'public'", []
    if not scope.include_private:
        return f"{prefix}visibility = 'public'", []
    clauses = [f"{prefix}visibility = 'public'", f"{prefix}owner_id = ${param_index}"]
    params: list[Any] = [scope.viewer_id]
    if scope.org_ids:
        index = param_index + 1
        clauses.append(
            f"({prefix}visibility = 'org' AND {prefix}scope_type = 'organization' "
            f"AND {prefix}scope_entity_id = ANY(${index}::text[]))"
        )
        params.append(list(scope.org_ids))
    return "(" + " OR ".join(clauses) + ")", params


def _public_relation_provenance(value: Any) -> dict[str, Any]:
    data = value.as_dict() if hasattr(value, "as_dict") else dict(value)
    public = {
        "id": data.get("edge_id") or data.get("relation_id") or data.get("relation_version_id"),
        "name": data.get("relation_type"),
        "status": data.get("status"),
        "confidence": data.get("confidence"),
        "path": list(data.get("abstraction_path") or []),
        "from_goal_id": data.get("from_goal_id"),
        "to_goal_id": data.get("to_goal_id"),
        "specific_goal_id": data.get("specific_goal_id"),
        "abstract_goal_id": data.get("abstract_goal_id"),
        "direction": data.get("direction"),
        "hop": data.get("hop"),
        "relation_type": data.get("relation_type"),
        "abstraction_path": list(data.get("abstraction_path") or []),
    }
    return {key: item for key, item in public.items() if item is not None}


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
            ctx.query_embedding, ctx.embedding_model = emb, model
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


_HIERARCHY_GOAL_SELECT = """
    SELECT id::text AS id, canonical_name, description, status, scope_type,
           scope_entity_id, visibility::text AS visibility, owner_id, version,
           home_shard_id, t_invalid
    FROM goals
    WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL
"""


def _hierarchy_scope_key(scope_type: Any, scope_entity_id: Any) -> tuple[str, Optional[str]]:
    normalized_type = str(scope_type or "global")
    return normalized_type, None if normalized_type == "global" else scope_entity_id


def _hierarchy_projection(row: Any, prefix: str) -> dict[str, Any]:
    goal_id = str(row[f"{prefix}_projection_id"])
    return {
        "id": goal_id,
        "goal_id": goal_id,
        "canonical_name": row.get(f"{prefix}_canonical_name"),
        "description": row.get(f"{prefix}_description"),
        "status": row.get(f"{prefix}_projected_status"),
        "scope_type": row.get(f"{prefix}_projected_scope_type"),
        "scope_entity_id": row.get(f"{prefix}_projected_scope_entity_id"),
        "visibility": row.get(f"{prefix}_projected_visibility"),
        "owner_id": row.get(f"{prefix}_projected_owner_id"),
        "home_shard_id": row.get(f"{prefix}_home_shard_id"),
        "version": row.get(f"{prefix}_projected_version"),
    }


def _hierarchy_projection_is_current(projection: dict[str, Any], source: dict[str, Any]) -> bool:
    if source.get("t_invalid") is not None:
        return False
    if str(source.get("status")) not in ("active", "candidate"):
        return False
    if str(projection.get("status")) != str(source.get("status")):
        return False
    if int(projection.get("version") or 0) != int(source.get("version") or 0):
        return False
    if _hierarchy_scope_key(projection.get("scope_type"), projection.get("scope_entity_id")) != _hierarchy_scope_key(
        source.get("scope_type"), source.get("scope_entity_id")
    ):
        return False
    if str(projection.get("visibility")) != str(source.get("visibility")):
        return False
    if projection.get("owner_id") != source.get("owner_id"):
        return False
    return str(projection.get("home_shard_id")) == str(source.get("home_shard_id"))


# Routing reasons that only mean "the expansion was bounded" (see
# hierarchical_goal_routing._TRUNCATION_REASONS) -- never a reason to drop it.
_HIERARCHY_BOUNDED_REASONS = frozenset({"hop_limit_reached", "fanout_limit_reached", "graph_candidate_limit_reached"})
# Ranking-only columns of the hierarchy edge query; not part of an edge.
_HIERARCHY_RANKING_COLUMNS = (
    "node_id", "side", "neighbour_id", "neighbour_name", "neighbour_semantic", "neighbour_lexical",
    "specific_semantic", "abstract_semantic", "specific_lexical", "abstract_lexical", "side_rank", "side_total",
)


def _hierarchy_flat_diagnostics(
    anchors: Sequence[Hit], reasons: Sequence[str], config: HierarchicalGoalRoutingConfig,
    *, degraded_reasons: Sequence[str] = (), details: Optional[dict[str, Any]] = None,
) -> dict[str, Any]:
    diagnostics = {
        "mode": "flat",
        "used_flat_fallback": True,
        "fallback_reasons": list(dict.fromkeys(reasons)),
        "degraded": bool(degraded_reasons),
        "degraded_reasons": list(dict.fromkeys(degraded_reasons)),
        "truncated": any(reason in _HIERARCHY_BOUNDED_REASONS for reason in degraded_reasons),
        "semantic_anchor_count": len(anchors),
        "graph_candidate_count": 0,
        "input_relation_count": 0,
        "accepted_relation_count": 0,
        "relevant_accepted_relation_count": 0,
        "ignored_relation_statuses": {},
        "ignored_relation_types": {},
        "invalid_relation_count": 0,
        "anchors_missing_goal_id": 0,
        "limits": {
            "max_hops": config.max_hops,
            "max_fanout": config.max_fanout,
            "max_graph_candidates": config.max_graph_candidates,
            "expand_parents": config.expand_parents,
            "expand_children": config.expand_children,
        },
    }
    if details:
        diagnostics.update(details)
    return diagnostics


def _hierarchy_seeds(goal_result: GoalSearchResult, cfg: RetrievalConfig) -> tuple[list[Hit], str]:
    """The Goals the hierarchy is expanded from. Judged matches or partial
    matches when there are any; otherwise the top judged candidates, so a query
    worded more specifically (or more broadly) than every stored Goal can still
    reach the right Goal through the graph. Unjudged results never expand: a
    neighbour could not be judged either."""
    if goal_result.resolution in ("matches", "partial"):
        return list(goal_result.resolved), goal_result.resolution
    if goal_result.resolution == "none":
        judged = [hit for hit in goal_result.candidates if hit.judged and hit.hierarchy is None]
        return judged[: cfg.hierarchy_seed_max], "judged_candidates"
    return [], goal_result.resolution


async def _fetch_hierarchy_edges(
    pool: Any, *, up_ids: Sequence[str], down_ids: Sequence[str], scope: AccessScope,
    tenant_scope: TenantScope, fanout: int, ctx: Optional[QueryContext],
) -> list[Any]:
    """Accepted edges leaving `up_ids` upward (their parents) and `down_ids`
    downward (their children), at most `fanout` per node and direction. The kept
    ones are the most relevant to the query: embedding similarity first (same
    embedding model only), lexical rank second. One control-database query."""
    relation_tenant_sql, relation_tenant_params = tenant_predicate(tenant_scope, alias="r", param_index=3)
    index = 3 + len(relation_tenant_params)
    specific_vis_sql, specific_vis_params = _goal_visibility_predicate(scope, alias="s", param_index=index)
    index += len(specific_vis_params)
    abstract_vis_sql, abstract_vis_params = _goal_visibility_predicate(scope, alias="n", param_index=index)
    index += len(abstract_vis_params)
    q_i, e_i, m_i, f_i = index, index + 1, index + 2, index + 3
    embedding = ctx.query_embedding if ctx is not None else None
    model = ctx.embedding_model if ctx is not None else None

    def semantic(alias: str) -> str:
        return (f"CASE WHEN ${e_i}::vector IS NOT NULL AND {alias}.embedding IS NOT NULL "
                f"AND {alias}.embedding_model = ${m_i}::text THEN 1 - ({alias}.embedding <=> ${e_i}::vector) ELSE 0 END")

    return await pool.fetch(
        f"""
        WITH edge_rows AS (
        SELECT r.specific_goal_id::text AS specific_goal_id,
               r.abstract_goal_id::text AS abstract_goal_id,
               r.relation_type, r.status, r.confidence, r.provenance,
               r.decision_id::text AS decision_id, r.decided_by, r.decided_at,
               s.goal_id::text AS specific_projection_id,
               s.canonical_name AS specific_canonical_name,
               s.short_description AS specific_description,
               s.status AS specific_projected_status,
               s.version AS specific_projected_version,
               s.scope_type AS specific_projected_scope_type,
               s.scope_entity_id AS specific_projected_scope_entity_id,
               s.visibility::text AS specific_projected_visibility,
               s.owner_id AS specific_projected_owner_id,
               s.home_shard_id AS specific_home_shard_id,
               n.goal_id::text AS abstract_projection_id,
               n.canonical_name AS abstract_canonical_name,
               n.short_description AS abstract_description,
               n.status AS abstract_projected_status,
               n.version AS abstract_projected_version,
               n.scope_type AS abstract_projected_scope_type,
               n.scope_entity_id AS abstract_projected_scope_entity_id,
               n.visibility::text AS abstract_projected_visibility,
               n.owner_id AS abstract_projected_owner_id,
               n.home_shard_id AS abstract_home_shard_id,
               {semantic("s")} AS specific_semantic,
               {semantic("n")} AS abstract_semantic,
               COALESCE(ts_rank_cd(s.search_tsv, to_tsquery('english', ${q_i}::text)), 0) AS specific_lexical,
               COALESCE(ts_rank_cd(n.search_tsv, to_tsquery('english', ${q_i}::text)), 0) AS abstract_lexical
        FROM goal_relations r
        JOIN goal_search_index s ON s.goal_id = r.specific_goal_id
        JOIN goal_search_index n ON n.goal_id = r.abstract_goal_id
        WHERE r.relation_type = 'SPECIALIZES'
          AND r.status = 'accepted'
          AND (r.specific_goal_id = ANY($1::uuid[]) OR r.abstract_goal_id = ANY($2::uuid[]))
          AND s.status IN ('active', 'candidate')
          AND n.status IN ('active', 'candidate')
          AND {relation_tenant_sql}
          AND {specific_vis_sql}
          AND {abstract_vis_sql}
          AND COALESCE(s.scope_type, 'global') = COALESCE(n.scope_type, 'global')
          AND s.scope_entity_id IS NOT DISTINCT FROM n.scope_entity_id
          AND COALESCE(r.scope_type, 'global') = COALESCE(s.scope_type, 'global')
          AND r.scope_entity_id IS NOT DISTINCT FROM s.scope_entity_id
        ), sides AS (
            SELECT e.*, e.specific_goal_id AS node_id, 'parents' AS side,
                   e.abstract_goal_id AS neighbour_id, e.abstract_canonical_name AS neighbour_name,
                   e.abstract_semantic AS neighbour_semantic, e.abstract_lexical AS neighbour_lexical
              FROM edge_rows e WHERE e.specific_goal_id::uuid = ANY($1::uuid[])
            UNION ALL
            SELECT e.*, e.abstract_goal_id, 'children',
                   e.specific_goal_id, e.specific_canonical_name,
                   e.specific_semantic, e.specific_lexical
              FROM edge_rows e WHERE e.abstract_goal_id::uuid = ANY($2::uuid[])
        ), ranked AS (
            SELECT sides.*,
                   row_number() OVER (PARTITION BY node_id, side
                                      ORDER BY neighbour_semantic DESC, neighbour_lexical DESC,
                                               neighbour_name NULLS LAST, neighbour_id) AS side_rank,
                   count(*) OVER (PARTITION BY node_id, side) AS side_total
              FROM sides
        )
        SELECT * FROM ranked
         WHERE side_rank <= ${f_i}
         ORDER BY node_id, side, side_rank
        """,
        list(up_ids),
        list(down_ids),
        *relation_tenant_params,
        *specific_vis_params,
        *abstract_vis_params,
        fts_or_query(ctx.query) if ctx is not None else None,
        to_pgvector(embedding) if embedding is not None and model else None,
        model,
        fanout,
    )


async def _route_goal_candidates(
    pool: Any, goal_result: GoalSearchResult, *, scope: AccessScope, cfg: RetrievalConfig,
    meta: RetrievalMeta, pools: Optional[ShardPools] = None, tenant_scope: Optional[TenantScope] = None,
    ctx: Optional[QueryContext] = None, judge: Optional[SemanticJudge] = None,
) -> list[Hit]:
    """Hybrid anchors -> bounded accepted-DAG neighbourhood -> contextual Goal judge.

    The hierarchy only ADDS candidates: every neighbour still goes through the
    same query + local-Claims judgment as a flat candidate. Size limits bound the
    neighbourhood (most query-relevant kept); they never switch it off. A
    neighbour that is stale, missing or on an unreachable shard is dropped on its
    own. Only a structural problem (invalid/cyclic relations) or a failed query
    falls back to flat retrieval, which always remains the baseline."""
    anchors = list(goal_result.resolved)
    tenant_scope = tenant_scope or _default_tenant_scope(scope)
    routing_config = HierarchicalGoalRoutingConfig(
        max_hops=cfg.hierarchy_max_hops, max_fanout=cfg.hierarchy_max_fanout,
        max_graph_candidates=cfg.hierarchy_max_candidates,
    )
    seeds, seed_mode = _hierarchy_seeds(goal_result, cfg)
    if not seeds:
        reason = "semantic_resolution_unjudged" if goal_result.resolution == "unjudged" else "no_semantic_anchors"
        meta.goal_routing = _hierarchy_flat_diagnostics(anchors, (reason,), routing_config)
        return anchors
    seed_ids = list(dict.fromkeys(str(goal.id) for goal in seeds))

    # -- bounded breadth-first walk: parents upward, children downward
    t0 = time.monotonic()
    edges: dict[tuple[str, str], dict[str, Any]] = {}
    relevance: dict[str, tuple[float, float]] = {}
    truncated_sides: list[tuple[str, str, int]] = []
    bounded_reasons: list[str] = []
    kept_nodes: set[str] = set(seed_ids)
    up_ids, down_ids = list(seed_ids), list(seed_ids)
    try:
        for _hop in range(cfg.hierarchy_max_hops):
            if not up_ids and not down_ids:
                break
            rows = await _fetch_hierarchy_edges(
                pool, up_ids=up_ids, down_ids=down_ids, scope=scope, tenant_scope=tenant_scope,
                fanout=cfg.hierarchy_max_fanout, ctx=ctx,
            )
            wanted = {("parents", node) for node in up_ids} | {("children", node) for node in down_ids}
            new_up: dict[str, None] = {}
            new_down: dict[str, None] = {}
            for raw in rows:
                row = dict(raw)
                side, node = str(row["side"]), str(row["node_id"])
                if (side, node) not in wanted:
                    continue
                if int(row["side_total"]) > cfg.hierarchy_max_fanout:
                    truncated_sides.append((node, side, int(row["side_total"])))
                neighbour = str(row["neighbour_id"])
                score = (float(row["neighbour_semantic"] or 0.0), float(row["neighbour_lexical"] or 0.0))
                relevance[neighbour] = max(relevance.get(neighbour, (0.0, 0.0)), score)
                edges.setdefault((str(row["specific_goal_id"]), str(row["abstract_goal_id"])), row)
                if neighbour not in kept_nodes:
                    (new_up if side == "parents" else new_down)[neighbour] = None
            # global cap on the neighbourhood: keep the most relevant new Goals
            capacity = max(cfg.hierarchy_max_candidates - (len(kept_nodes) - len(seed_ids)), 0)
            fresh = sorted(set(new_up) | set(new_down), key=lambda gid: (relevance[gid], gid), reverse=True)
            if len(fresh) > capacity:
                bounded_reasons.append("graph_candidate_limit_reached")
                fresh = fresh[:capacity]
            kept_nodes.update(fresh)
            up_ids = [gid for gid in new_up if gid in kept_nodes]
            down_ids = [gid for gid in new_down if gid in kept_nodes]
    except Exception as exc:
        meta.latency_ms["goal_hierarchy"] = (time.monotonic() - t0) * 1000
        degraded_reason = f"goal hierarchy query failed: {type(exc).__name__}"
        meta.degrade(degraded_reason)
        meta.goal_routing = _hierarchy_flat_diagnostics(
            anchors, ("hierarchy_query_error",), routing_config, degraded_reasons=(degraded_reason,),
        )
        return anchors
    meta.latency_ms["goal_hierarchy"] = (time.monotonic() - t0) * 1000
    meta.counts["goal_hierarchy_input_relations"] = len(edges)
    if not edges:
        meta.goal_routing = _hierarchy_flat_diagnostics(anchors, ("no_edges",), routing_config)
        return anchors
    if truncated_sides:
        bounded_reasons.append("fanout_limit_reached")

    relation_edges: list[dict[str, Any]] = []
    projections: dict[str, dict[str, Any]] = {}
    for key, raw in edges.items():
        if key[0] not in kept_nodes or key[1] not in kept_nodes:
            continue
        row = dict(raw)
        for ranking_column in _HIERARCHY_RANKING_COLUMNS:
            row.pop(ranking_column, None)
        specific = _hierarchy_projection(row, "specific")
        abstract = _hierarchy_projection(row, "abstract")
        projections[specific["goal_id"]] = specific
        projections[abstract["goal_id"]] = abstract
        row["specific_goal"] = specific
        row["abstract_goal"] = abstract
        relation_edges.append(row)

    async def fetch(goal_pool: Any, goal_ids: list[str]):
        return await goal_pool.fetch(_HIERARCHY_GOAL_SELECT, goal_ids)

    routes = {goal_id: str(projection.get("home_shard_id") or "K000") for goal_id, projection in projections.items()}
    t1 = time.monotonic()
    try:
        hydration = await hydrate_rows(pools or pools_for(pool), routes, fetch)
    except Exception as exc:
        degraded_reason = f"goal hierarchy hydration failed: {type(exc).__name__}"
        meta.degrade(degraded_reason)
        meta.goal_routing = _hierarchy_flat_diagnostics(
            anchors, ("hierarchy_hydration_error",), routing_config, degraded_reasons=(degraded_reason,),
        )
        return anchors
    meta.latency_ms["goal_hierarchy_hydrate"] = (time.monotonic() - t1) * 1000
    meta.shards_touched = sorted(set(meta.shards_touched) | set(hydration.shard_batches))
    meta.unavailable_shards.update(hydration.unavailable_shards)
    meta.missing_ids.extend(hydration.missing_ids)

    # A Goal that could not be read, or whose projection disagrees with its
    # canonical row, is dropped ON ITS OWN (with its edges); the rest stays.
    missing_ids = sorted(goal_id for goal_id in projections if goal_id not in hydration.rows)
    stale_ids = sorted(
        goal_id for goal_id, projection in projections.items()
        if goal_id in hydration.rows and not _hierarchy_projection_is_current(projection, hydration.rows[goal_id])
    )
    dropped = set(missing_ids) | set(stale_ids)
    if hydration.partial:
        meta.degrade("goal hierarchy unavailable_shard")
    if missing_ids:
        meta.degrade("goal hierarchy missing_goal")
    if stale_ids:
        meta.degrade("goal hierarchy stale_goal_projection")
    if dropped:
        relation_edges = [
            edge for edge in relation_edges
            if str(edge["specific_goal_id"]) not in dropped and str(edge["abstract_goal_id"]) not in dropped
        ]

    anchor_payloads = [
        {"id": goal.id, "goal_id": goal.id, "canonical_name": goal.name, "text": goal.text,
         "home_shard_id": goal.home_shard_id, "score": goal.rrf}
        for goal in seeds if str(goal.id) not in dropped
    ]
    try:
        routed = route_hierarchical_goal_candidates(
            anchor_payloads, relation_edges, goal_records=hydration.rows, config=routing_config,
        )
    except Exception as exc:
        degraded_reason = f"goal hierarchy routing failed: {type(exc).__name__}"
        meta.degrade(degraded_reason)
        meta.goal_routing = _hierarchy_flat_diagnostics(
            anchors, ("hierarchy_routing_error",), routing_config, degraded_reasons=(degraded_reason,),
        )
        return anchors

    routing_diagnostics = routed.diagnostics.as_dict()
    # Size limits only mean "bounded"; only a structural problem falls back to flat.
    bounded_reasons += [r for r in routed.diagnostics.degraded_reasons if r in _HIERARCHY_BOUNDED_REASONS]
    blocking_reasons = [r for r in routed.diagnostics.degraded_reasons if r not in _HIERARCHY_BOUNDED_REASONS]
    if blocking_reasons:
        degraded_reasons = [*blocking_reasons, "hierarchy_degraded"]
        for degraded_reason in degraded_reasons:
            meta.degrade(f"goal hierarchy {degraded_reason}")
        routing_diagnostics.update({
            "mode": "flat", "used_flat_fallback": True,
            "fallback_reasons": list(dict.fromkeys(["hierarchy_degraded", *routing_diagnostics.get("fallback_reasons", [])])),
            "degraded": True, "degraded_reasons": list(dict.fromkeys(degraded_reasons)),
            "truncated": bool(bounded_reasons), "graph_candidate_count": 0,
        })
        meta.goal_routing = routing_diagnostics
        return anchors

    graph_hits: list[Hit] = []
    for candidate in routed.graph_candidates:
        goal_id = str(candidate.goal_id)
        source = hydration.rows.get(goal_id)
        if source is None or goal_id in dropped:
            continue
        hierarchy: dict[str, Any] = {"origin": "graph"}
        if cfg.include_hierarchy_paths:
            hierarchy.update({
                "abstraction_path": list(candidate.abstraction_path),
                "relation_provenance": [_public_relation_provenance(item) for item in candidate.relation_provenance],
            })
        name = str(source.get("canonical_name") or goal_id)
        description = source.get("description") or ""
        graph_hits.append(Hit(
            id=goal_id, name=name, text=f"{name}: {description}".strip(": "),
            home_shard_id=str(source.get("home_shard_id") or "K000"), rrf=0.0, hierarchy=hierarchy,
        ))

    # Judge the most query-relevant neighbours first, whatever their direction
    # (stable sort keeps traversal order for ties).
    graph_hits.sort(key=lambda hit: relevance.get(hit.id, (0.0, 0.0)), reverse=True)
    meta.counts["goal_hierarchy_graph_candidates"] = len(graph_hits)
    routing_diagnostics.update({
        "seed_mode": seed_mode,
        "seed_count": len(anchor_payloads),
        "degraded": False,
        "degraded_reasons": [],
        "truncated": bool(bounded_reasons),
        "truncation_reasons": list(dict.fromkeys(bounded_reasons)),
        "dropped_goal_ids": sorted(dropped),
    })
    if truncated_sides:
        routing_diagnostics["truncated_sides"] = [
            {"goal_id": goal_id, "direction": side, "kept": cfg.hierarchy_max_fanout, "available": total}
            for goal_id, side, total in sorted(set(truncated_sides))
        ]
    meta.goal_routing = routing_diagnostics
    if cfg.include_hierarchy_paths:
        meta.goal_routing["paths"] = [hit.hierarchy for hit in graph_hits]
    flat_ids = {str(candidate.id) for candidate in goal_result.candidates}
    goal_result.candidates = list(goal_result.candidates) + [hit for hit in graph_hits if str(hit.id) not in flat_ids]
    # A neighbouring Goal is a CANDIDATE, not an answer: it goes through the same
    # contextual Goal judgment (query + local Claims) as the flat candidates
    # before any of its Procedures are considered. Adjacency alone never makes a
    # parent's or child's Procedures eligible.
    admitted = await _judge_graph_candidates(graph_hits, ctx=ctx, judge=judge, cfg=cfg, meta=meta)
    routed_goals = list(anchors)
    seen = {str(goal.id) for goal in anchors}
    for hit in admitted:
        if hit.id not in seen:
            routed_goals.append(hit)
            seen.add(hit.id)
    return routed_goals


def combine_goal_resolution(goal_result: GoalSearchResult, routed: Sequence[Hit], cfg: RetrievalConfig) -> GoalSearchResult:
    """The Goal resolution after hierarchy expansion: flat and graph candidates
    compete on the SAME judged verdicts. A neighbour the judge calls a firm match
    can therefore be the chosen Goal; adjacency alone never can."""
    if goal_result.resolution == "unjudged":
        return goal_result
    judged = [hit for hit in routed if hit.judged]
    matches = sorted(
        (hit for hit in judged if hit.relation == "matches" and (hit.confidence or 0) >= cfg.min_confidence),
        key=lambda hit: (-(hit.confidence or 0), -hit.rrf, hit.id))
    partial = sorted(
        (hit for hit in judged if hit.relation in ("partial", "matches")),
        key=lambda hit: (-(hit.confidence or 0), -hit.rrf, hit.id))
    if matches:
        return GoalSearchResult(matches[: cfg.goal_resolve_max], goal_result.candidates, "matches")
    if partial:
        return GoalSearchResult(partial[: cfg.goal_resolve_max], goal_result.candidates, "partial")
    return goal_result


async def _judge_graph_candidates(
    graph_hits: list[Hit], *, ctx: Optional[QueryContext], judge: Optional[SemanticJudge],
    cfg: RetrievalConfig, meta: RetrievalMeta,
) -> list[Hit]:
    if not graph_hits:
        return []
    routing = meta.goal_routing if meta.goal_routing is not None else {}
    if ctx is None or judge is None:
        routing["graph_admitted"] = 0
        routing["graph_not_admitted_reason"] = "no contextual judge supplied"
        return []
    to_judge = graph_hits[: cfg.hierarchy_judge_top_k]
    await _judge_all(judge, "task_goal", ctx.text, to_judge, cfg, meta, stage="goal_hierarchy")
    admitted = [h for h in to_judge if h.judged and h.relation in ("matches", "partial")]
    routing["graph_judged"] = sum(1 for h in to_judge if h.judged)
    routing["graph_admitted"] = len(admitted)
    routing["graph_not_judged_beyond_budget"] = max(0, len(graph_hits) - len(to_judge))
    for hit in graph_hits:
        if hit.hierarchy is not None:
            hit.hierarchy["admitted"] = hit in admitted
    return admitted


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
    record: bool = True, tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    """The one recommendation entry point: Goal resolution -> Procedure retrieval
    -> applicability/evidence -> selection. Never raises for semantic outages."""
    meta = RetrievalMeta()
    tenant_scope = tenant_scope or _default_tenant_scope(scope)
    t0 = time.monotonic()
    ctx = await build_query_context(goal_text, local_claims, embedder=embedder, cfg=cfg)
    meta.local_claim_ids = ctx.claim_ids
    g = await search_goals(pool, ctx, scope=scope, embedder=embedder, judge=judge, cfg=cfg, meta=meta)
    routed_goals = await _route_goal_candidates(
        pool, g, scope=scope, cfg=cfg, meta=meta, pools=pools, tenant_scope=tenant_scope,
        ctx=ctx, judge=judge if judge is not None else default_judge(),
    )
    g = combine_goal_resolution(g, routed_goals, cfg)
    p = await retrieve_procedures(pool, ctx, routed_goals, scope=scope, pools=pools, embedder=embedder, judge=judge, cfg=cfg,
                                  meta=meta, current_scope=current_scope, require_verified=require_verified)
    meta.latency_ms["total"] = (time.monotonic() - t0) * 1000
    def _public(item):
        return None if item is None else {k: v for k, v in item.items() if k != "_row"}

    flat_candidates = [
        candidate for candidate in g.candidates[: cfg.rerank_top_k]
        if candidate.hierarchy is None
    ]
    graph_candidates = [
        candidate for candidate in g.candidates
        if candidate.hierarchy is not None and candidate.hierarchy.get("origin") == "graph"
    ]
    out = {
        "goal": goal_text,
        "goal_resolution": {"status": g.resolution, "goals": [h.brief() for h in g.resolved],
                            "candidates": [h.brief() for h in flat_candidates + graph_candidates]},
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
    tenant_scope: Optional[TenantScope] = None,
) -> GoalFirstResult:
    """Tier 1 -> Tier 2 without the recommendation envelope: the full ranked/diagnosed procedure set."""
    meta = RetrievalMeta()
    tenant_scope = tenant_scope or _default_tenant_scope(scope)
    t0 = time.monotonic()
    ctx = await build_query_context(query_text, local_claims, embedder=embedder, cfg=cfg)
    meta.local_claim_ids = ctx.claim_ids
    g = await search_goals(pool, ctx, scope=scope, embedder=embedder, judge=judge, cfg=cfg, meta=meta)
    routed_goals = await _route_goal_candidates(
        pool, g, scope=scope, cfg=cfg, meta=meta, pools=pools, tenant_scope=tenant_scope,
        ctx=ctx, judge=judge if judge is not None else default_judge(),
    )
    g = combine_goal_resolution(g, routed_goals, cfg)
    p = await retrieve_procedures(pool, ctx, routed_goals, scope=scope, pools=pools, embedder=embedder, judge=judge, cfg=cfg,
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
    embedding_model: Optional[str] = None, scope: AccessScope, status: Optional[str] = None,
    resolved: str = "all", limit: int = 10, cfg: RetrievalConfig = RetrievalConfig(),
) -> list[dict]:
    """Judge-free Goal search with resolution filtering and safe public rows."""
    if not query_text and not query_embedding:
        raise ValueError("search_goal_candidates requires query_text and/or query_embedding")
    if resolved not in ("all", "resolved", "unresolved"):
        raise ValueError("resolved must be all, resolved, or unresolved")
    where = "status IN ('active', 'candidate')" if status is None else "status = $1"
    params = [] if status is None else [status]
    cfg2 = replace_cfg(cfg, search_top_k=max(limit * 3, 10))
    cands, _n, _m = await _legs(
        pool, table="goal_search_index", id_col="goal_id", name_col="canonical_name",
        text_expr="canonical_name || COALESCE(': ' || short_description, '')", extra_cols="",
        ctx_text=query_text or "", embedding=query_embedding, embedding_model=embedding_model, scope=scope,
        where_extra=where, extra_params=params, cfg=cfg2)
    candidate_hits = cands
    if not candidate_hits:
        return []
    pools = pools_for(pool)

    async def fetch(p, ids):
        return await p.fetch(
            "SELECT id, canonical_name, description, objective, constraints, expected_outcome, "
            "verification_requirement, status, metadata, resolved_at, visibility, owner_id, "
            "scope_type, scope_entity_id, home_shard_id FROM goals "
            "WHERE id = ANY($1::uuid[])", ids)

    hyd = await hydrate_rows(pools, {h.id: h.home_shard_id for h in candidate_hits}, fetch)
    rows: list[dict] = []
    for hit in candidate_hits:
        source = hyd.rows.get(hit.id)
        if source is None:
            continue
        row = dict(source)
        row["id"] = str(row.get("id") or hit.id)
        row.pop("embedding", None)
        row.pop("ranking_raw", None)
        if resolved == "resolved" and row.get("resolved_at") is None:
            continue
        if resolved == "unresolved" and row.get("resolved_at") is not None:
            continue
        rows.append(row)
    from app.services.goal_ranking import public_goal_ranking, rank_goal_candidates

    ranked = rank_goal_candidates(rows)
    by_id = {str(row["id"]): row for row in rows}
    return [
        {
            **by_id[str(item["goal_id"])],
            "ranking": public_goal_ranking(item),
        }
        for item in ranked
        if str(item.get("goal_id")) in by_id
    ][:limit]


async def search_goal_candidates_page(
    pool: asyncpg.Pool, *, query_text: Optional[str], query_embedding: Optional[list[float]] = None,
    embedding_model: Optional[str] = None, scope: AccessScope, status: Optional[str] = None,
    resolved: str = "all", limit: int = 10, offset: int = 0,
    cfg: RetrievalConfig = RetrievalConfig(),
) -> tuple[list[dict], bool]:
    """Return one candidate page and whether another page exists."""
    if offset < 0:
        raise ValueError("offset must be non-negative")
    page_size = max(1, int(limit))
    rows = await search_goal_candidates(
        pool, query_text=query_text, query_embedding=query_embedding,
        embedding_model=embedding_model, scope=scope, status=status,
        resolved=resolved, limit=offset + page_size + 1, cfg=cfg,
    )
    page = rows[offset:offset + page_size]
    return page, len(rows) > offset + page_size


def replace_cfg(cfg: RetrievalConfig, **kw) -> RetrievalConfig:
    from dataclasses import replace
    return replace(cfg, **kw)
