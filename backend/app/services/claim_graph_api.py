"""
Read-only Claim Graph API domain layer (directive §40).

Pure composition over already-real, already-tested services -- no new
schema, no migration, nothing here reimplements a query another module
already owns:

  - `app.services.claims.get_claim_relations` -- single-hop relation read.
  - `app.services.claim_traversal.research` -- bounded 2-hop traversal.
  - `app.services.claim_evidence.get_claim_evidence` -- per-claim evidence.
  - `app.services.claim_impact.find_procedures_referencing_claim` -- claim
    -> dependent-procedure lookup.
  - `app.services.claims.get_claim_version_chain` +
    `app.services.claim_temporal.get_claim_commit_history` -- version/
    commit history.

The one genuinely new primitive is `get_claim`, below: no existing reader
fetches a single claim row by id (`claims.py`'s own callers all resolve a
claim indirectly -- by family, by subject, by task). It lives here rather
than in `claims.py` itself because it is API-layer composition (the read
this router needs), not core substrate `claims.py` owns.

SCOPE DISCIPLINE (CLAUDE.md hard rule: `scope_predicates()` is the only
legal source of tenant/visibility SQL): every function below either
builds its own predicate directly against `knowledge_nodes`/`evidence`/
`procedures` (mirroring `get_claim`'s own shape), or -- when composing a
service function that returns rows NOT already scope-filtered (relation
edges, traversal hops, evidence rows, dependent procedures) -- resolves
the row ids the composed call returned and re-checks them against
`scope_predicates()` before returning, via `_visible_ids` below. This is
the same anti-enumeration posture `app/api/graph.py` already established:
a row the caller's scope cannot see is OMITTED from the response, never
labelled or 404'd per-row (that would leak existence).

`TenantScope.unrestricted()` is used throughout, per `graph.py`'s own
precedent -- today's honest permissive-in-effect posture; the seam this
module offers a future resolved tenant scope is the same as `graph.py`'s.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timezone
from typing import Any, Iterable, Optional

import asyncpg

from app.services import claim_evidence as claim_evidence_service
from app.services import claim_impact
from app.services import claim_temporal
from app.services import claim_traversal
from app.services import claims as claims_service
from app.services.access import AccessScope, TenantScope, scope_predicates

# A whole-graph read (`get_claim_graph_overview`) is a DASHBOARD endpoint,
# not a hot path: it is bounded by `limit` (default + hard cap below) and
# computes one real `get_claim_lifecycle_state` per shown node. That per
# node call issues a few small reads; bounded concurrency keeps the whole
# overview well under a second on a normal graph without opening the door
# to an unbounded fan-out.
_GRAPH_OVERVIEW_DEFAULT_LIMIT = 200
_GRAPH_OVERVIEW_MAX_LIMIT = 600
_GRAPH_OVERVIEW_STATUS_CONCURRENCY = 8


async def get_claim(
    pool: asyncpg.Pool, claim_id: str, *, scope: AccessScope,
) -> Optional[dict]:
    """
    The one missing primitive: a single live claim row by id, scope-
    filtered. `None` when `claim_id` does not resolve to a live
    `node_type='claim'` row, OR when it does but the caller's scope
    cannot see it -- deliberately the same `None` for both, per this
    module's anti-enumeration posture (a caller must not be able to
    distinguish "does not exist" from "exists, not yours" from the return
    value alone).
    """
    tenant = TenantScope.unrestricted()
    scope_sql, scope_params, _ = scope_predicates(scope, tenant, param_index=2)
    row = await pool.fetchrow(
        f"SELECT * FROM knowledge_nodes WHERE id = $1::uuid "
        f"AND node_type = 'claim' AND t_invalid IS NULL AND {scope_sql}",
        claim_id, *scope_params,
    )
    return dict(row) if row else None


async def _visible_ids(
    pool: asyncpg.Pool, table: str, ids: Iterable[str], *, scope: AccessScope,
) -> set[str]:
    """
    Given a set of row ids from some OTHER table (`knowledge_nodes`,
    `evidence`, `procedures`), return the subset visible under `scope` --
    the same per-row visibility re-check `app/api/graph.py::get_subgraph`
    already does for its own hydrate step, generalized to any of the
    three tables this module touches. `table` is never caller-supplied
    (always one of the three literal names below), so this is not a SQL
    injection surface.
    """
    id_list = list(ids)
    if not id_list:
        return set()
    tenant = TenantScope.unrestricted()
    scope_sql, scope_params, _ = scope_predicates(scope, tenant, param_index=2)
    rows = await pool.fetch(
        f"SELECT id FROM {table} WHERE id = ANY($1::uuid[]) AND {scope_sql}",
        id_list, *scope_params,
    )
    return {str(r["id"]) for r in rows}


def _other_claim_id(row: dict, claim_id: str) -> str:
    source = str(row["source_id"])
    target = str(row["target_id"])
    return target if source == str(claim_id) else source


async def get_claim_neighbors(
    pool: asyncpg.Pool,
    claim_id: str,
    *,
    relations: Optional[set[str]] = None,
    direction: str = "both",
    scope: AccessScope,
) -> list[dict]:
    """
    Single-hop relation neighbors of one claim -- composes
    `claims.get_claim_relations` verbatim (direction/relations validation
    is entirely its job, not re-checked here), then drops any edge whose
    OTHER endpoint is a claim the caller's scope cannot see. `[]` when
    `claim_id` itself isn't visible (or doesn't exist) -- no edges
    against an invisible claim are ever surfaced.
    """
    if await get_claim(pool, claim_id, scope=scope) is None:
        return []
    rows = await claims_service.get_claim_relations(
        pool, claim_id, direction=direction, relations=relations,
    )
    neighbor_ids = {_other_claim_id(r, claim_id) for r in rows}
    visible = await _visible_ids(pool, "knowledge_nodes", neighbor_ids, scope=scope)
    return [dict(r) for r in rows if _other_claim_id(r, claim_id) in visible]


async def traverse_claim_graph(
    pool: asyncpg.Pool, claim_id: str, *, max_hops: int = 2, scope: AccessScope,
) -> dict:
    """
    Bounded multi-hop traversal -- composes `claim_traversal.research`
    verbatim (the 2-hop cap, the dedup-by-claim-id frontier, the
    supporting/contradicting/other bucketing are entirely its job, not
    re-derived here). Every hop whose `from_claim_id`/`to_claim_id` is a
    claim the caller's scope cannot see is dropped from the response.

    Returns an honest empty-bucket dict (never raises) when `claim_id`
    itself is not visible -- same "no existence leak" posture as
    `get_claim_neighbors`.
    """
    if await get_claim(pool, claim_id, scope=scope) is None:
        return {
            "claim_id": str(claim_id),
            "supporting": [], "contradicting": [], "other": [],
        }

    result = await claim_traversal.research(pool, claim_id, max_hops=max_hops)
    all_hops = result.supporting + result.contradicting + result.other
    touched_ids: set[str] = set()
    for hop in all_hops:
        touched_ids.add(hop.from_claim_id)
        touched_ids.add(hop.to_claim_id)
    visible = await _visible_ids(pool, "knowledge_nodes", touched_ids, scope=scope)

    def _dump(hops: list) -> list[dict]:
        return [
            {
                "edge_id": h.edge_id,
                "relation": h.relation,
                "hop": h.hop,
                "from_claim_id": h.from_claim_id,
                "to_claim_id": h.to_claim_id,
                "properties": h.properties,
            }
            for h in hops
            if h.from_claim_id in visible and h.to_claim_id in visible
        ]

    return {
        "claim_id": result.claim_id,
        "supporting": _dump(result.supporting),
        "contradicting": _dump(result.contradicting),
        "other": _dump(result.other),
    }


async def get_claim_evidence_api(
    pool: asyncpg.Pool, claim_id: str, *, scope: AccessScope,
) -> list[dict]:
    """
    Live evidence rows for one claim -- composes
    `claim_evidence.get_claim_evidence` verbatim (the `target_type =
    'claim'`/`t_invalid IS NULL`/oldest-first query is entirely its job),
    then drops any evidence row the caller's scope cannot see (`evidence`
    itself carries `visibility`/`owner_id`/`tenant_id`, per
    `db/24_evidence.sql`). `[]` when `claim_id` isn't visible.
    """
    if await get_claim(pool, claim_id, scope=scope) is None:
        return []
    rows = await claim_evidence_service.get_claim_evidence(pool, claim_id)
    ids = {str(r["id"]) for r in rows}
    visible = await _visible_ids(pool, "evidence", ids, scope=scope)
    return [dict(r) for r in rows if str(r["id"]) in visible]


async def get_claim_dependents(
    pool: asyncpg.Pool, claim_id: str, *, scope: AccessScope,
) -> list[dict]:
    """
    Procedures whose `preconditions` reference this claim -- composes
    `claim_impact.find_procedures_referencing_claim` verbatim (the
    `preconditions @> [{"claim_id": ...}]` containment query is entirely
    its job), already hydrated with `id`/`name` by that function itself,
    then drops any procedure the caller's scope cannot see. `[]` when
    `claim_id` isn't visible.
    """
    if await get_claim(pool, claim_id, scope=scope) is None:
        return []
    procedures = await claim_impact.find_procedures_referencing_claim(pool, claim_id)
    ids = {p["id"] for p in procedures}
    visible = await _visible_ids(pool, "procedures", ids, scope=scope)
    return [p for p in procedures if p["id"] in visible]


async def get_claim_history(
    pool: asyncpg.Pool, claim_id: str, *, scope: AccessScope,
) -> list[dict]:
    """
    One claim's version chain, oldest to newest, each version carrying
    its own real commit history -- composes `claims.get_claim_version_
    chain` (the family-walk) and `claim_temporal.get_claim_commit_
    history` (the commit join) verbatim, neither re-walked or re-joined
    here; this function only merges their outputs (both already walk the
    identical chain in the identical order, so a merge by claim id is
    exact, not a fuzzy join) and drops any version the caller's scope
    cannot see. `[]` when `claim_id` isn't visible or has no chain.
    """
    if await get_claim(pool, claim_id, scope=scope) is None:
        return []
    chain = await claims_service.get_claim_version_chain(pool, claim_id)
    if not chain:
        return []
    commit_history = await claim_temporal.get_claim_commit_history(pool, claim_id)
    by_id: dict[str, dict[str, Any]] = {h["claim_id"]: h for h in commit_history}

    ids = [str(row["id"]) for row in chain]
    visible = await _visible_ids(pool, "knowledge_nodes", ids, scope=scope)

    history: list[dict] = []
    for row in chain:
        claim_version_id = str(row["id"])
        if claim_version_id not in visible:
            continue
        props = dict(row["properties"])
        commit_entry = by_id.get(claim_version_id)
        history.append({
            "claim_id": claim_version_id,
            "version": (commit_entry or {}).get("version", props.get("claim_version", 1)),
            "statement": (commit_entry or {}).get("statement", props.get("statement")),
            "t_valid": (commit_entry or {}).get("t_valid"),
            "commits": (commit_entry or {}).get("commits", []),
            "properties": props,
        })
    return history


_GRAPH_LINK_MODES = ("both", "relations", "similarity")
_GRAPH_SIM_K_MAX = 8
_GRAPH_SIM_THRESHOLD_FLOOR = 0.30


async def get_claim_graph_overview(
    pool: asyncpg.Pool,
    *,
    scope: AccessScope,
    limit: int = _GRAPH_OVERVIEW_DEFAULT_LIMIT,
    include_retired: bool = False,
    q: Optional[str] = None,
    with_status: bool = True,
    link_mode: str = "both",
    sim_k: int = 3,
    sim_threshold: float = 0.55,
) -> dict[str, Any]:
    """
    The whole-graph read a visualizer needs: a bounded set of live claim
    NODES (scope-filtered, most-recently-valid first) plus the EDGES among
    that node set. No existing reader returns "the graph" -- every other
    function in this module is single-claim-centered -- so this is the one
    genuinely new query here, built the same way `get_claim` builds its own
    predicate (directly against `knowledge_nodes`/`edges` via
    `scope_predicates()`), not a new schema or traversal engine.

    Two edge KINDS, because the claim<->claim *relation* graph
    (SUPPORTS/SUPERSEDES/...) is sparse-to-empty in real corpora and a
    node cloud with no links is not a usable view:

      - `kind="relation"`  -- a real live claim<->claim relation edge
        (`edges.custom_edge_type` in `ALL_CLAIM_RELATIONS`). Directed.
      - `kind="similarity"` -- an UNDIRECTED nearest-neighbour edge in
        claim-embedding space (pgvector cosine), so semantically-close
        claims are visibly connected the way an Obsidian-style graph
        connects related notes. Derived, never stored; `weight` is the
        cosine similarity (0..1).

    `link_mode`: "both" (default) / "relations" / "similarity".
    `sim_k`: nearest neighbours per node for similarity edges (1..8).
    `sim_threshold`: minimum cosine similarity for a similarity edge
    (floored at 0.30). Similarity edges are computed ONLY among the
    returned node set and only for nodes that carry an embedding.

    `limit`: max nodes returned (clamped to
    [1, _GRAPH_OVERVIEW_MAX_LIMIT]). One extra row is fetched internally
    to report `truncated` honestly rather than silently capping.

    `include_retired`: by default only claims still believed
    (`truth_state = 'IN'`) are nodes -- a superseded/contradicted claim is
    history, not "the current claim graph". Pass True to also include
    `OUT` claims (they come back with `status` `retired`/`contradicted`).

    `q`: optional case-insensitive substring filter on the claim's stored
    statement (`knowledge_nodes.name`, which holds `statement[:200]`).

    `with_status`: when True (default) each node carries the REAL
    lifecycle state from `claims.get_claim_lifecycle_state` (one bounded
    read per node, computed against a single shared `as_of` so the whole
    snapshot is internally consistent). Pass False for a faster raw dump
    that reports only `truth_state`.

    Returns `{"nodes": [...], "edges": [...], "counts": {...},
    "truncated": bool, "generated_at": iso}`. Every node carries `degree`
    (its edge count within this view). Node/edge rows the caller's scope
    cannot see are simply absent -- same anti-enumeration posture as every
    other function in this module; an edge is included only when BOTH its
    endpoints are in the returned node set.
    """
    limit = max(1, min(int(limit), _GRAPH_OVERVIEW_MAX_LIMIT))
    link_mode = link_mode if link_mode in _GRAPH_LINK_MODES else "both"
    sim_k = max(1, min(int(sim_k), _GRAPH_SIM_K_MAX))
    sim_threshold = max(_GRAPH_SIM_THRESHOLD_FLOOR, min(float(sim_threshold), 0.999))
    tenant = TenantScope.unrestricted()
    now = datetime.now(timezone.utc)

    scope_sql, scope_params, next_index = scope_predicates(scope, tenant, param_index=2)
    clauses = [
        "node_type = 'claim'",
        "t_invalid IS NULL",
        scope_sql,
    ]
    params: list[Any] = [limit + 1, *scope_params]
    if not include_retired:
        clauses.append("COALESCE(properties->>'truth_state', 'IN') = 'IN'")
    if q:
        clauses.append(f"name ILIKE ${next_index}")
        params.append(f"%{q}%")

    where = " AND ".join(clauses)
    rows = await pool.fetch(
        f"SELECT id, name, properties, t_valid, created_by, scope_type, scope_entity_id "
        f"FROM knowledge_nodes WHERE {where} ORDER BY t_valid DESC LIMIT $1",
        *params,
    )
    truncated = len(rows) > limit
    rows = rows[:limit]

    total_where = ["node_type = 'claim'", "t_invalid IS NULL", scope_sql]
    total_params: list[Any] = list(scope_params)
    if not include_retired:
        total_where.append("COALESCE(properties->>'truth_state', 'IN') = 'IN'")
    claims_total = await pool.fetchval(
        f"SELECT count(*) FROM knowledge_nodes WHERE {' AND '.join(total_where)}",
        *total_params,
    )

    node_ids = [str(r["id"]) for r in rows]

    statuses: dict[str, str] = {}
    if with_status and node_ids:
        sem = asyncio.Semaphore(_GRAPH_OVERVIEW_STATUS_CONCURRENCY)

        async def _one(cid: str) -> tuple[str, str]:
            async with sem:
                try:
                    state = await claims_service.get_claim_lifecycle_state(
                        pool, cid, as_of=now,
                    )
                except ValueError:
                    state = "unknown"  # raced away between the node fetch and this read
            return cid, state

        for cid, state in await asyncio.gather(*(_one(c) for c in node_ids)):
            statuses[cid] = state

    nodes: list[dict[str, Any]] = []
    by_status: dict[str, int] = {}
    for r in rows:
        props = dict(r["properties"] or {})
        cid = str(r["id"])
        truth_state = props.get("truth_state", "IN")
        status = statuses.get(cid) if with_status else None
        if status:
            by_status[status] = by_status.get(status, 0) + 1
        nodes.append({
            "id": cid,
            "statement": props.get("statement") or r["name"],
            "truth_state": truth_state,
            "status": status,
            "subject": props.get("subject"),
            "predicate": props.get("predicate"),
            "object": props.get("object"),
            "claim_type": props.get("claim_type"),
            "epistemic_status": props.get("epistemic_status"),
            "scope_type": r["scope_type"],
            "scope_entity_id": r["scope_entity_id"],
            "created_by": r["created_by"],
            "t_valid": r["t_valid"],
        })

    edges: list[dict[str, Any]] = []
    seen_pairs: set[tuple[str, str]] = set()

    if node_ids and link_mode in ("both", "relations"):
        edge_rows = await pool.fetch(
            "SELECT id, source_id, target_id, custom_edge_type AS relation, "
            "created_by, t_valid "
            "FROM edges "
            "WHERE source_table = 'knowledge_nodes' AND target_table = 'knowledge_nodes' "
            "AND t_invalid IS NULL "
            "AND custom_edge_type = ANY($1::text[]) "
            "AND source_id = ANY($2::uuid[]) AND target_id = ANY($2::uuid[])",
            list(claims_service.ALL_CLAIM_RELATIONS), node_ids,
        )
        for e in edge_rows:
            s, t = str(e["source_id"]), str(e["target_id"])
            seen_pairs.add((s, t))
            edges.append({
                "id": str(e["id"]),
                "source": s,
                "target": t,
                "kind": "relation",
                "relation": e["relation"],
                "created_by": e["created_by"],
                "t_valid": e["t_valid"],
            })

    if node_ids and link_mode in ("both", "similarity"):
        # Undirected k-NN in claim-embedding space, restricted to the
        # returned node set. One LATERAL query: for each embedded node, its
        # `sim_k` closest other embedded nodes in the set above threshold.
        sim_rows = await pool.fetch(
            """
            SELECT a.id AS src, nn.id AS tgt, nn.sim
            FROM knowledge_nodes a
            CROSS JOIN LATERAL (
                SELECT b.id, 1 - (a.embedding <=> b.embedding) AS sim
                FROM knowledge_nodes b
                WHERE b.id = ANY($1::uuid[])
                  AND b.id <> a.id
                  AND b.embedding IS NOT NULL
                ORDER BY a.embedding <=> b.embedding
                LIMIT $2
            ) nn
            WHERE a.id = ANY($1::uuid[])
              AND a.embedding IS NOT NULL
              AND nn.sim >= $3
            """,
            node_ids, sim_k, sim_threshold,
        )
        for r in sim_rows:
            s, t = str(r["src"]), str(r["tgt"])
            key = (s, t) if s < t else (t, s)
            if key in seen_pairs:
                continue  # a real relation edge already connects these two
            seen_pairs.add(key)
            edges.append({
                "id": f"sim:{key[0]}:{key[1]}",
                "source": key[0],
                "target": key[1],
                "kind": "similarity",
                "weight": round(float(r["sim"]), 4),
            })

    degree: dict[str, int] = {}
    for e in edges:
        degree[e["source"]] = degree.get(e["source"], 0) + 1
        degree[e["target"]] = degree.get(e["target"], 0) + 1
    for n in nodes:
        n["degree"] = degree.get(n["id"], 0)

    edges_by_kind: dict[str, int] = {}
    for e in edges:
        edges_by_kind[e["kind"]] = edges_by_kind.get(e["kind"], 0) + 1

    return {
        "nodes": nodes,
        "edges": edges,
        "counts": {
            "claims_total": int(claims_total or 0),
            "claims_shown": len(nodes),
            "edges": len(edges),
            "edges_by_kind": edges_by_kind,
            "by_status": by_status,
        },
        "truncated": truncated,
        "include_retired": include_retired,
        "link_mode": link_mode,
        "query": q,
        "generated_at": now.isoformat(),
    }
