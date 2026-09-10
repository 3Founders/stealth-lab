"""
MCP hardening B30: the relevant Claim working-set derivation mechanism
-- `get_relevant_claims`. Claims are an independent global/local
knowledge substrate; this module requests a BOUNDED relevant working
set for a goal/subproblem, never the whole Claim graph.

Reuses, does not duplicate: `app.services.retrieval.HybridRetriever`
already does hybrid vector+lexical entrypoint selection, scope/tenant
filtering, and belief-aware (`truth_state != 'OUT'`) bounded graph
expansion over `knowledge_nodes`/`task_nodes` -- exactly ticket 14's real
staged pipeline. This module restricts that retriever to
`knowledge_nodes`, filters its hits down to real Claims (`node_type=
'claim'` -- `knowledge_nodes` also holds non-Claim rows like claim_family
hubs, which this tool must never present as Claims), and reuses
`app.services.claims.get_claim_lifecycle_state` for each hit's
belief/status rather than inventing a second belief computation.

Returned refs are COMPACT (B30's own rule: "Default MCP responses
return compact references/summaries, not full Claim bodies or
evidence"): claim_id, version, scope, status/belief,
reason_for_relevance, applicability, evidence pointer. Full Claim/
evidence retrieval stays lazy -- a caller wanting the full claim reads
it via the existing `stealth://claims/{claim_id}` resource or
`get_claim_graph`, not through this tool.
"""
from __future__ import annotations

from typing import Optional

import asyncpg

from app.services.access import AccessScope
from app.services.retrieval import HybridRetriever

DEFAULT_TOP_K = 10
MAX_TOP_K = 25  # a hard ceiling -- "never the whole Claim graph", even if a caller asks for more


async def get_relevant_claims(
    pool: asyncpg.Pool,
    *,
    goal: str,
    context: Optional[str] = None,
    top_k: int = DEFAULT_TOP_K,
    access_scope: Optional[AccessScope] = None,
) -> list[dict]:
    """
    Bounded, compact Claim references relevant to `goal` (optionally
    narrowed by free-text `context` -- environment/constraint text,
    concatenated into the same retrieval query rather than driving a
    second query path). Never returns more than `MAX_TOP_K` -- B30's own
    "the working set MUST be bounded" rule, enforced here, not just
    documented.
    """
    from app.services.claims import get_claim_lifecycle_state

    top_k = min(top_k, MAX_TOP_K)
    query_text = f"{goal}\n{context}" if context else goal

    retriever = HybridRetriever(pool, scope=access_scope or AccessScope.unrestricted(), tables=("knowledge_nodes",))
    # B37: coarse domain/topic routing, opt-in and additive -- a no-op
    # until a real hierarchy exists over knowledge_nodes (hierarchy.py's
    # build_hierarchy_for_table), at which point this narrows candidate
    # retrieval to the query's own top-level branch before RRF fusion.
    result = await retriever.retrieve(
        query_text, top_k=top_k, expand_depth=1, max_context_nodes=top_k,
        coarse_route_table="knowledge_nodes",
    )

    if not result.nodes:
        return []

    ids = [n.id for n in result.nodes]
    rows = await pool.fetch(
        "SELECT id, properties, scope_type, scope_entity_id, t_valid, t_invalid "
        "FROM knowledge_nodes WHERE id = ANY($1::uuid[]) AND node_type = 'claim'",
        ids,
    )
    by_id = {r["id"]: r for r in rows}

    refs: list[dict] = []
    for node in result.nodes:
        row = by_id.get(node.id)
        if row is None:
            continue  # a real, non-Claim knowledge_node hit (e.g. a claim_family hub) -- never presented as a Claim
        properties = row["properties"]
        lifecycle_state = await get_claim_lifecycle_state(pool, str(row["id"]))
        refs.append({
            "claim_id": str(row["id"]),
            "version": properties.get("claim_version", 1),
            "scope": {"scope_type": row["scope_type"], "scope_entity_id": row["scope_entity_id"]},
            "status": lifecycle_state,
            "belief": properties.get("truth_state"),
            "statement": properties.get("statement"),
            "reason_for_relevance": {
                "matched_by": node.matched_by, "hops": node.hops, "score": node.score,
            },
            "applicability": None,  # this tool is general-purpose, not tied to one Procedure's precondition evaluation -- see continue_run/route_decision for that
            "evidence_summary": "lazy -- fetch via stealth://claims/{claim_id} or get_claim_graph for full evidence",
        })
        if len(refs) >= top_k:
            break
    return refs
