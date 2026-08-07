"""
Hierarchical decomposition tree (Part B of HIERARCHICAL_DECOMPOSITION_PLAN.md).

Design decisions made explicit here, each one because it was tested or
reasoned through earlier, not picked casually:

  - Internal nodes are ordinary rows in task_nodes/knowledge_nodes, not
    a new table. "Internal" is a STRUCTURAL property (has outgoing
    OWNS/PARENT_OF edges), never a stored flag -- task_nodes has no
    generic properties column to put one in, and this way both node
    types are handled identically instead of special-cased.

  - Routing signal is the plain mean of children's embeddings.
    Representative-point-set routing and blending in an independent
    "self" embedding were both benchmarked against it and did not win
    -- see conversation history / plan doc. Kept deliberately simple
    because the more complicated alternatives were tested and lost.

  - Query-time descent is beam search with CONFIDENCE-ADAPTIVE width
    (narrow by default, widen only when the top candidates at a level
    are close), never beam=1/greedy -- greedy was confirmed materially
    worse under query noise. Leaf-level comparison is always exact
    (coarse-to-fine), never the tree's approximate signal.

  - Tree CONSTRUCTION is bottom-up clustering (you already have many
    leaves to organize), reusing dedup.py's complete_linkage_clusters
    as the actual grouping primitive -- not the top-down "decompose a
    single task" direction, which is what /v1/decompose already does
    for a genuinely new incoming problem.

  - This module writes to the persisted graph (build_hierarchy_for_table,
    attach_new_leaf), so like dedup.py's batch sweep it is an
    internal/admin operation, never reachable from untrusted input.
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional
from uuid import UUID

import asyncpg

from app.services.access import AccessScope, visibility_predicate
from app.services.dedup import complete_linkage_clusters
from app.services.embeddings import Embedder
from app.services.precondition_gate import extract_postconditions, postconditions_compatible
from app.services.reuse_detection import (
    FULL_MATCH_THRESHOLD,
    LEXICAL_FULL_MATCH_THRESHOLD,
    _lexical_overlap,
)

log = logging.getLogger(__name__)

DEFAULT_GROUP_THRESHOLD = 0.75  # looser than FULL_MATCH_THRESHOLD (0.90) --
# grouping into a subtree means "related enough to belong together",
# not "the same thing" (that's Part A's job, at a higher threshold).
DEFAULT_MIN_CHILDREN = 2   # matches the tree invariant: no single-child nodes
DEFAULT_MAX_CHILDREN = 12  # soft branching-factor cap -- large fan-out is no
                            # better than flat search under a fake hierarchy label


# ---------------------------------------------------------------------
# Pure logic: no DB, no embeddings -- just a similarity callable
# ---------------------------------------------------------------------

def _chunk(items: list[str], size: int) -> list[list[str]]:
    """Dumb fallback: fixed-size chunks. Only used if tightening the
    threshold repeatedly still can't split an oversized cluster --
    rare, but must terminate rather than leave max_children violated
    or loop forever."""
    return [items[i:i + size] for i in range(0, len(items), size)]


def group_with_branching_limit(
    keys: list[str],
    similarity: Callable[[str, str], float],
    threshold: float = DEFAULT_GROUP_THRESHOLD,
    min_children: int = DEFAULT_MIN_CHILDREN,
    max_children: int = DEFAULT_MAX_CHILDREN,
    max_split_attempts: int = 4,
) -> list[list[str]]:
    """
    Complete-linkage cluster `keys`, then recursively re-cluster any
    resulting group that's too big at a progressively tighter threshold,
    so no single internal node ends up with an unbounded fan-out that's
    really just flat search wearing a tree costume.

    Groups smaller than min_children are returned as-is (singletons,
    typically) -- they simply don't become internal nodes this round;
    a group only becomes an internal node when it has >= min_children
    members. Whether to promote them is the CALLER's decision
    (plan_next_level), not this function's -- this function only
    groups, it doesn't decide node creation.
    """
    clusters = complete_linkage_clusters(keys, similarity, threshold)
    result: list[list[str]] = []
    for cluster in clusters:
        if len(cluster) <= max_children:
            result.append(cluster)
            continue

        split = None
        step = max((1.0 - threshold) / (max_split_attempts + 1), 0.01)
        for attempt in range(1, max_split_attempts + 1):
            tighter = min(threshold + step * attempt, 0.999)
            sub = complete_linkage_clusters(cluster, similarity, tighter)
            if len(sub) > 1 and all(len(s) <= max_children for s in sub):
                split = sub
                break
        result.extend(split if split is not None else _chunk(cluster, max_children))
    return result


@dataclass
class ProposedGroup:
    member_keys: list[str]
    is_internal: bool  # True iff len(member_keys) >= min_children


def plan_next_level(
    keys: list[str],
    similarity: Callable[[str, str], float],
    threshold: float = DEFAULT_GROUP_THRESHOLD,
    min_children: int = DEFAULT_MIN_CHILDREN,
    max_children: int = DEFAULT_MAX_CHILDREN,
) -> list[ProposedGroup]:
    """
    One level of bottom-up tree construction: group `keys`, decide which
    groups qualify to become internal nodes. Every input key appears in
    exactly one output group (nothing is dropped or duplicated).
    """
    groups = group_with_branching_limit(keys, similarity, threshold, min_children, max_children)
    return [ProposedGroup(member_keys=g, is_internal=len(g) >= min_children) for g in groups]


# ---------------------------------------------------------------------
# DB-backed: construction and traversal over the persisted graph
# ---------------------------------------------------------------------

_OWNS_FILTER = "e.edge_type = 'OWNS' AND e.custom_edge_type = 'PARENT_OF'"


def _name_expr(table: str, alias: str = "") -> str:
    prefix = f"{alias}." if alias else ""
    if table == "task_nodes":
        return f"{prefix}name || ' ' || COALESCE({prefix}description, '')"
    return f"{prefix}name"


async def _fetch_roots(pool: asyncpg.Pool, table: str, scope: AccessScope) -> list[dict]:
    """Nodes with no incoming PARENT_OF edge -- i.e. not yet owned by
    any internal node. These are the current top of whatever tree
    structure exists so far (possibly still a flat, unclustered set)."""
    vis_sql, vis_params = visibility_predicate(scope, alias="n", param_index=1)
    rows = await pool.fetch(
        f"SELECT n.id, n.name, (n.embedding IS NOT NULL) AS has_embedding, "
        f"{_name_expr(table, alias='n')} AS full_text "
        f"FROM {table} n "
        f"WHERE n.t_invalid IS NULL AND {vis_sql} "
        f"AND NOT EXISTS (SELECT 1 FROM edges e WHERE e.t_invalid IS NULL AND {_OWNS_FILTER} "
        f"  AND e.target_id = n.id AND e.target_table = '{table}')",
        *vis_params,
    )
    return [dict(r) for r in rows]


async def _pairwise_similarity(
    pool: asyncpg.Pool, table: str, ids: list[str], scope: AccessScope, rows_by_id: dict
) -> tuple[Callable[[str, str], float], float]:
    """Returns (similarity_fn, threshold_used). Vector via SQL self-join
    when every id has an embedding (mirrors dedup.py's approach --
    asyncpg has no vector codec, so this is computed server-side, never
    parsed from raw bytes in Python); lexical fallback otherwise."""
    use_vector = all(rows_by_id[i]["has_embedding"] for i in ids)
    if use_vector:
        pair_rows = await pool.fetch(
            f"SELECT a.id AS id_a, b.id AS id_b, 1 - (a.embedding <=> b.embedding) AS similarity "
            f"FROM {table} a JOIN {table} b ON a.id < b.id "
            f"WHERE a.id = ANY($1::uuid[]) AND b.id = ANY($1::uuid[]) "
            f"AND a.t_invalid IS NULL AND b.t_invalid IS NULL",
            [UUID(i) for i in ids],
        )
        sim_lookup: dict[tuple[str, str], float] = {}
        for r in pair_rows:
            a, b, s = str(r["id_a"]), str(r["id_b"]), float(r["similarity"])
            sim_lookup[(a, b)] = s
            sim_lookup[(b, a)] = s

        def sim(a: str, b: str) -> float:
            return sim_lookup.get((a, b), 0.0)
        return sim, DEFAULT_GROUP_THRESHOLD

    def sim(a: str, b: str) -> float:
        return _lexical_overlap(rows_by_id[a]["full_text"], rows_by_id[b]["full_text"])
    # Lexical overlap lives on a different numeric scale than cosine --
    # scale the grouping threshold down the same proportion
    # reuse_detection.py's lexical thresholds sit below its vector ones.
    return sim, DEFAULT_GROUP_THRESHOLD * (LEXICAL_FULL_MATCH_THRESHOLD / FULL_MATCH_THRESHOLD)


async def _create_internal_node(
    conn: asyncpg.Connection, table: str, child_ids: list[str], name: str, now: datetime, created_by: str,
) -> str:
    """
    Insert one internal (aggregator) node whose embedding is the mean
    of its children's embeddings, computed server-side via pgvector's
    avg() aggregate (requires pgvector >= 0.5.0 -- if unavailable on
    the target instance, this is the one place that needs a Python-side
    fallback, e.g. via the `pgvector` package's asyncpg codec registration).
    """
    ids = [UUID(i) for i in child_ids]
    if table == "task_nodes":
        row = await conn.fetchrow(
            "INSERT INTO task_nodes (name, description, provenance, embedding, t_valid, t_created, created_by) "
            "SELECT $1, $2, 'company_debate', avg(embedding), $3, $3, $4 "
            "FROM task_nodes WHERE id = ANY($5::uuid[]) AND t_invalid IS NULL "
            "RETURNING id",
            name, f"Aggregates {len(child_ids)} related task(s).", now, created_by, ids,
        )
    else:
        row = await conn.fetchrow(
            "INSERT INTO knowledge_nodes (node_type, name, properties, provenance, embedding, "
            "t_valid, t_created, created_by) "
            "SELECT 'hierarchy_group', $1, $2, 'company_debate', avg(embedding), $3, $3, $4 "
            "FROM knowledge_nodes WHERE id = ANY($5::uuid[]) AND t_invalid IS NULL "
            "RETURNING id",
            name, {"member_count": len(child_ids)}, now, created_by, ids,
        )
    internal_id = str(row["id"])

    for child_id in child_ids:
        await conn.execute(
            "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
            "target_id, target_table, provenance, t_valid, t_created, created_by) "
            "VALUES ('OWNS', 'PARENT_OF', $1, $2, $3, $2, 'company_debate', $4, $4, $5)",
            UUID(internal_id), table, UUID(child_id), now, created_by,
        )
    return internal_id


@dataclass
class HierarchyBuildReport:
    table: str
    levels_built: int
    internal_nodes_created: int
    final_root_count: int
    level_details: list[dict] = field(default_factory=list)


async def build_hierarchy_for_table(
    pool: asyncpg.Pool,
    table: str,
    scope: Optional[AccessScope] = None,
    embedder: Optional[Embedder] = None,
    summarizer: Optional[Callable[[list[dict]], str]] = None,
    threshold: float = DEFAULT_GROUP_THRESHOLD,
    min_children: int = DEFAULT_MIN_CHILDREN,
    max_children: int = DEFAULT_MAX_CHILDREN,
    max_levels: int = 6,
    approver_id: str = "hierarchy_builder",
    apply: bool = False,
) -> HierarchyBuildReport:
    """
    Bottom-up: repeatedly group the current roots of `table` into
    internal nodes, until a level produces no new internal nodes (a
    single root remains, or nothing groups tightly enough) or
    max_levels is hit.

    Idempotent-ish: re-running after new leaves were added only groups
    whatever is currently rootless -- it does not touch or rebuild
    already-owned subtrees. That also means it never rebalances an
    existing subtree; see attach_new_leaf for how new leaves join
    without triggering a rebuild.

    Dry run by default (apply=False), matching dedup.py's posture --
    the report shows what WOULD be built without writing anything.
    """
    scope = scope or AccessScope.unrestricted()
    embedder = embedder or Embedder()
    summarizer = summarizer or _default_summary
    now = datetime.now(timezone.utc)

    total_internal = 0
    level_details = []
    level = 0

    while level < max_levels:
        roots = await _fetch_roots(pool, table, scope)
        if len(roots) < min_children:
            break  # nothing left that could form a new internal node

        rows_by_id = {str(r["id"]): r for r in roots}
        ids = list(rows_by_id.keys())
        sim, eff_threshold = await _pairwise_similarity(pool, table, ids, scope, rows_by_id)
        groups = plan_next_level(ids, sim, eff_threshold, min_children, max_children)

        internal_groups = [g for g in groups if g.is_internal]
        level_details.append({
            "level": level, "roots_seen": len(roots),
            "groups_formed": len(groups), "internal_nodes_proposed": len(internal_groups),
        })

        if not internal_groups:
            break  # nothing groups tightly enough -- stop, don't force it

        if apply:
            async with pool.acquire() as conn:
                async with conn.transaction():
                    for g in internal_groups:
                        members = [rows_by_id[k] for k in g.member_keys]
                        name = summarizer(members)
                        await _create_internal_node(conn, table, g.member_keys, name, now, approver_id)
        total_internal += len(internal_groups)
        level += 1

        if len(internal_groups) == 1 and len(groups) == 1:
            break  # collapsed to a single root -- tree is complete

    final_roots = await _fetch_roots(pool, table, scope)
    return HierarchyBuildReport(
        table=table, levels_built=level, internal_nodes_created=total_internal if apply else 0,
        final_root_count=len(final_roots), level_details=level_details,
    )


def _default_summary(members: list[dict]) -> str:
    """Cheap, deterministic fallback label when no LLM summarizer is
    given -- keeps ingestion runnable without an LLM call per internal
    node (see plan doc's cost-control discussion). A real summarizer
    callable (LLM-authored rollup) can be passed in instead."""
    names = [m["name"] for m in members[:3]]
    more = f" (+{len(members) - 3} more)" if len(members) > 3 else ""
    return f"Group: {', '.join(names)}{more}"


# ---------------------------------------------------------------------
# Query-time traversal
# ---------------------------------------------------------------------

@dataclass
class SearchResult:
    leaf_id: Optional[str]
    leaf_name: Optional[str]
    similarity: Optional[float]
    used_flat_fallback: bool
    comparisons: int


def _props_col(table: str) -> str:
    return "success_criteria" if table == "task_nodes" else "properties"


async def _passes_postcondition_gate(
    pool: asyncpg.Pool, table: str, node_id: str, query_postconditions: Optional[list[str]]
) -> bool:
    """Rule 1 gate, checked only against the WINNING leaf a search is
    about to return -- not during descent, so the beam-search logic
    itself stays untouched. Optional: when query_postconditions is
    None (nothing supplied one), this always passes -- zero behavior
    change from before this existed."""
    if query_postconditions is None:
        return True
    row = await pool.fetchrow(f"SELECT {_props_col(table)} AS props FROM {table} WHERE id = $1", UUID(node_id))
    candidate_postconditions = extract_postconditions(row["props"]) if row else None
    return postconditions_compatible(candidate_postconditions, query_postconditions)


async def hierarchical_search(
    pool: asyncpg.Pool,
    table: str,
    query_text: str,
    scope: Optional[AccessScope] = None,
    embedder: Optional[Embedder] = None,
    beam: int = 1,
    adaptive: bool = True,
    gap_threshold: float = 0.03,
    expanded_beam: int = 3,
    confidence_floor: float = 0.3,
    query_vec: Optional[list[float]] = None,
    query_postconditions: Optional[list[str]] = None,
) -> SearchResult:
    """
    Beam-descend the tree: mean-vector routing, confidence-adaptive
    width, exact comparison always at the leaf level. If the best
    available branch ever scores below confidence_floor, abort the
    descent and report used_flat_fallback=True -- the CALLER is
    expected to fall back to reuse_detection.find_reusable_nodes /
    HybridRetriever in that case, this function does not do that
    itself (keeps this module's contract to "tree search only").

    `query_vec`: pass an already-computed embedding to skip embedding
    `query_text` again (see decomposition.py's threading of one
    up-front embed call through every reuse-check site).

    `query_postconditions`: Rule 1 gate (precondition_gate.py).
    Optional, checked only against the final winning leaf -- if it
    fails, this reports used_flat_fallback=True rather than returning
    a match the gate rejected, same signal the caller already handles
    for a low-confidence result.
    """
    scope = scope or AccessScope.unrestricted()
    embedder = embedder or Embedder()
    comparisons = 0

    if query_vec is None:
        query_vec = await embedder.embed_one(query_text, input_type="query")
    from app.services.embeddings import to_pgvector
    vec_str = to_pgvector(query_vec)

    frontier_ids = [str(r["id"]) for r in await _fetch_roots(pool, table, scope)]
    if not frontier_ids:
        return SearchResult(None, None, None, used_flat_fallback=True, comparisons=0)

    for _ in range(20):  # hard cap on depth -- a real tree won't be this deep;
                          # guards against an edge-graph cycle turning into an infinite loop
        scored = await pool.fetch(
            f"SELECT id, name, 1 - (embedding <=> $1::vector) AS similarity "
            f"FROM {table} WHERE id = ANY($2::uuid[]) AND t_invalid IS NULL",
            vec_str, [UUID(i) for i in frontier_ids],
        )
        comparisons += len(scored)
        if not scored:
            return SearchResult(None, None, None, used_flat_fallback=True, comparisons=comparisons)

        ranked = sorted(scored, key=lambda r: r["similarity"], reverse=True)
        if ranked[0]["similarity"] < confidence_floor:
            return SearchResult(None, None, None, used_flat_fallback=True, comparisons=comparisons)

        eff_beam = beam
        if adaptive and len(ranked) > 1 and (ranked[0]["similarity"] - ranked[1]["similarity"]) < gap_threshold:
            eff_beam = max(beam, expanded_beam)
        next_frontier = [str(r["id"]) for r in ranked[:eff_beam]]

        children = await pool.fetch(
            f"SELECT e.source_id, n.id, n.name FROM edges e "
            f"JOIN {table} n ON n.id = e.target_id AND n.t_invalid IS NULL "
            f"WHERE e.t_invalid IS NULL AND {_OWNS_FILTER} AND e.source_table = '{table}' "
            f"AND e.target_table = '{table}' AND e.source_id = ANY($1::uuid[])",
            [UUID(i) for i in next_frontier],
        )

        if not children:
            # Frontier nodes are leaves -- ranked already IS the exact
            # (coarse-to-fine) leaf-level comparison. Done.
            best = next(r for r in ranked if str(r["id"]) in next_frontier)
            if not await _passes_postcondition_gate(pool, table, str(best["id"]), query_postconditions):
                return SearchResult(None, None, None, used_flat_fallback=True, comparisons=comparisons)
            return SearchResult(
                leaf_id=str(best["id"]), leaf_name=best["name"], similarity=float(best["similarity"]),
                used_flat_fallback=False, comparisons=comparisons,
            )

        frontier_ids = list({str(c["id"]) for c in children})

    log.warning("hierarchical_search hit the depth cap without reaching a leaf -- possible cycle in PARENT_OF edges")
    return SearchResult(None, None, None, used_flat_fallback=True, comparisons=comparisons)


async def batch_hierarchical_search(
    pool: asyncpg.Pool,
    table: str,
    queries: dict[str, list[float]],
    scope: Optional[AccessScope] = None,
    beam: int = 1,
    adaptive: bool = True,
    gap_threshold: float = 0.03,
    expanded_beam: int = 3,
    confidence_floor: float = 0.3,
    query_postconditions: Optional[dict[str, list[str]]] = None,
) -> dict[str, SearchResult]:
    """
    Same algorithm as hierarchical_search (mean-vector routing,
    confidence-adaptive beam, exact leaf-level comparison, flat-fallback
    signal on low confidence) but for MANY queries at once, keyed by
    caller-supplied ref (e.g. a proposed ChangeSet op's ref).

    `query_postconditions`: Rule 1 gate, keyed by the same ref as
    `queries`. Optional and per-ref -- a ref with no entry (or when the
    whole dict is None) passes through ungated, same as
    hierarchical_search's single-query version.

    Built for Part C (per-subtask reuse resolution): naively calling
    hierarchical_search once per proposed subtask means N round trips
    per level, N times the cost. This batches by GROUPING queries that
    currently share the same frontier and scoring each group's queries
    against that frontier in ONE SQL round trip (a query x candidate
    cross join), so the round-trip count is bounded by the number of
    DISTINCT frontiers active at a level, not by the number of queries.

    All queries share the same frontier at level 1 (the tree's roots),
    so level 1 is always exactly one round trip regardless of N. Deeper
    levels cost more only to the extent queries actually diverge onto
    different branches -- and subtasks proposed from the SAME parent
    decomposition are plausibly semantically related, so in practice
    they're expected to cluster onto shared branches rather than
    scatter into N singleton groups. Worth confirming against real
    traffic rather than assumed, same caveat as everywhere else in this
    module that depends on real usage patterns.
    """
    scope = scope or AccessScope.unrestricted()
    from app.services.embeddings import to_pgvector
    refs = list(queries.keys())
    vec_texts = {ref: to_pgvector(vec) for ref, vec in queries.items()}
    comparisons = {ref: 0 for ref in refs}
    results: dict[str, SearchResult] = {}
    last_best: dict[str, tuple[str, str, float]] = {}  # ref -> (id, name, similarity)

    root_rows = await _fetch_roots(pool, table, scope)
    if not root_rows:
        return {ref: SearchResult(None, None, None, True, 0) for ref in refs}
    root_ids = [str(r["id"]) for r in root_rows]

    frontier: dict[str, list[str]] = {ref: root_ids for ref in refs}
    active = set(refs)

    for _ in range(20):  # same hard depth cap as hierarchical_search
        if not active:
            break

        groups: dict[frozenset, list[str]] = {}
        for ref in active:
            groups.setdefault(frozenset(frontier[ref]), []).append(ref)

        winners: dict[str, list[str]] = {}  # ref -> next frontier ids (empty if resolved this round)
        for frontier_ids, group_refs in groups.items():
            pairs = [(ref, vec_texts[ref]) for ref in group_refs]
            rows = await pool.fetch(
                f"SELECT q.ref, n.id, n.name, 1 - (n.embedding <=> q.vec_text::vector) AS similarity "
                f"FROM unnest($1::text[], $2::text[]) AS q(ref, vec_text) "
                f"CROSS JOIN {table} n "
                f"WHERE n.id = ANY($3::uuid[]) AND n.t_invalid IS NULL",
                [p[0] for p in pairs], [p[1] for p in pairs], [UUID(i) for i in frontier_ids],
            )
            by_ref: dict[str, list] = {}
            for row in rows:
                by_ref.setdefault(row["ref"], []).append(row)

            for ref in group_refs:
                comparisons[ref] += len(frontier_ids)
                scored = by_ref.get(ref, [])
                if not scored:
                    results[ref] = SearchResult(None, None, None, True, comparisons[ref])
                    active.discard(ref)
                    continue
                ranked = sorted(scored, key=lambda r: r["similarity"], reverse=True)
                if ranked[0]["similarity"] < confidence_floor:
                    results[ref] = SearchResult(None, None, None, True, comparisons[ref])
                    active.discard(ref)
                    continue
                last_best[ref] = (str(ranked[0]["id"]), ranked[0]["name"], float(ranked[0]["similarity"]))
                eff_beam = beam
                if adaptive and len(ranked) > 1 and (ranked[0]["similarity"] - ranked[1]["similarity"]) < gap_threshold:
                    eff_beam = max(beam, expanded_beam)
                winners[ref] = [str(r["id"]) for r in ranked[:eff_beam]]

        if not winners:
            break

        all_next_ids = {i for ids in winners.values() for i in ids}
        children_rows = await pool.fetch(
            f"SELECT e.source_id, n.id, n.name FROM edges e "
            f"JOIN {table} n ON n.id = e.target_id AND n.t_invalid IS NULL "
            f"WHERE e.t_invalid IS NULL AND {_OWNS_FILTER} AND e.source_table = '{table}' "
            f"AND e.target_table = '{table}' AND e.source_id = ANY($1::uuid[])",
            [UUID(i) for i in all_next_ids],
        )
        children_by_parent: dict[str, set[str]] = {}
        for c in children_rows:
            children_by_parent.setdefault(str(c["source_id"]), set()).add(str(c["id"]))

        next_active = set()
        leaf_resolutions: dict[str, tuple[str, str, float]] = {}
        for ref, ids in winners.items():
            children = {cid for i in ids for cid in children_by_parent.get(i, set())}
            if not children:
                # Frontier nodes are leaves -- last_best[ref] IS the exact
                # (coarse-to-fine) leaf-level comparison already computed.
                leaf_resolutions[ref] = last_best[ref]
            else:
                frontier[ref] = list(children)
                next_active.add(ref)

        # Rule 1 gate, batched: one extra round trip covering every ref
        # that actually has a postcondition constraint this round, not
        # one query per ref -- same batching principle as the rest of
        # this function.
        gated_refs = [r for r in leaf_resolutions if query_postconditions and r in query_postconditions]
        props_by_id: dict[str, object] = {}
        if gated_refs:
            ids_to_check = list({leaf_resolutions[r][0] for r in gated_refs})
            prop_rows = await pool.fetch(
                f"SELECT id, {_props_col(table)} AS props FROM {table} WHERE id = ANY($1::uuid[])",
                [UUID(i) for i in ids_to_check],
            )
            props_by_id = {str(r["id"]): r["props"] for r in prop_rows}

        for ref, (bid, bname, bsim) in leaf_resolutions.items():
            if query_postconditions and ref in query_postconditions:
                candidate_postconditions = extract_postconditions(props_by_id.get(bid))
                if not postconditions_compatible(candidate_postconditions, query_postconditions[ref]):
                    results[ref] = SearchResult(None, None, None, True, comparisons[ref])
                    continue
            results[ref] = SearchResult(bid, bname, bsim, False, comparisons[ref])
        active = next_active

    for ref in active:  # depth cap hit without resolving -- same cycle-guard as hierarchical_search
        log.warning("batch_hierarchical_search hit the depth cap for ref=%s without reaching a leaf", ref)
        results[ref] = SearchResult(None, None, None, True, comparisons[ref])

    return results


async def attach_new_leaf(
    pool: asyncpg.Pool,
    table: str,
    leaf_id: str,
    scope: Optional[AccessScope] = None,
    embedder: Optional[Embedder] = None,
    approver_id: str = "hierarchy_insert",
) -> Optional[str]:
    """
    Find where a newly created leaf belongs in the existing tree and
    attach it, updating ancestor embeddings incrementally (O(1) running
    mean per ancestor, not a full re-summarization -- see plan doc's
    maintenance-cost discussion; a periodic full LLM re-summarization
    pass to correct drift is a separate, not-yet-built job).

    Returns the parent internal node's id, or None if nothing suitable
    was found (leaf stays a root -- a valid outcome, it may become the
    seed of a future group on the next build_hierarchy_for_table pass).
    """
    scope = scope or AccessScope.unrestricted()
    embedder = embedder or Embedder()

    row = await pool.fetchrow(
        f"SELECT {_name_expr(table)} AS full_text FROM {table} WHERE id = $1", UUID(leaf_id)
    )
    if row is None:
        return None

    result = await hierarchical_search(
        pool, table, row["full_text"], scope=scope, embedder=embedder,
        beam=3, adaptive=True,
    )
    if result.used_flat_fallback or result.leaf_id is None or result.leaf_id == leaf_id:
        return None

    now = datetime.now(timezone.utc)
    # The traversal returns a LEAF (its whole point is exact leaf-level
    # comparison) -- the new node becomes that leaf's SIBLING, i.e. it
    # attaches to that leaf's parent, not to the leaf itself.
    parent_row = await pool.fetchrow(
        f"SELECT e.source_id FROM edges e WHERE e.t_invalid IS NULL AND {_OWNS_FILTER} "
        f"AND e.source_table = '{table}' AND e.target_table = '{table}' AND e.target_id = $1",
        UUID(result.leaf_id),
    )
    if parent_row is None:
        return None  # the matched leaf is itself a root with no parent yet
    parent_id = parent_row["source_id"]

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute(
                "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
                "target_id, target_table, provenance, t_valid, t_created, created_by) "
                "VALUES ('OWNS', 'PARENT_OF', $1, $2, $3, $2, 'company_debate', $4, $4, $5)",
                parent_id, table, UUID(leaf_id), now, approver_id,
            )
            # O(1) running-mean update, walked up the ancestor chain.
            # The averaging subquery lives in SET, not FROM: Postgres
            # allows a SET-clause subquery to correlate to the UPDATE
            # target's own columns (here, {table}.id) -- a FROM-clause
            # subquery cannot. This also keeps the embedding entirely
            # server-side; it's never fetched into Python, which matters
            # since asyncpg has no codec for the vector type (see
            # embeddings.py's to_pgvector docstring).
            current = parent_id
            while current is not None:
                await conn.execute(
                    f"UPDATE {table} SET embedding = ("
                    f"  SELECT avg(m.embedding) FROM edges e "
                    f"  JOIN {table} m ON m.id = e.target_id AND m.t_invalid IS NULL "
                    f"  WHERE e.source_id = {table}.id AND e.t_invalid IS NULL AND {_OWNS_FILTER} "
                    f"  AND e.source_table = '{table}' AND e.target_table = '{table}'"
                    f") WHERE id = $1",
                    current,
                )
                grandparent = await conn.fetchrow(
                    f"SELECT e.source_id FROM edges e WHERE e.t_invalid IS NULL AND {_OWNS_FILTER} "
                    f"AND e.source_table = '{table}' AND e.target_table = '{table}' AND e.target_id = $1",
                    current,
                )
                current = grandparent["source_id"] if grandparent else None

    return str(parent_id)
