"""Review queue for the Goal hierarchy (remaining_work.md #10).

Two kinds of items need a human:
  * proposed SPECIALIZES edges (low-confidence placements, identity-resolution
    relations) -- stored in goal_relations with status 'proposed';
  * Goals placement flagged as `orphan` or `uncertain` -- goal_review_items.

A reviewer can accept or reject a PROPOSED edge; the decision goes through
`goal_abstraction.persist_goal_relation(expected_status="proposed")`, so the cycle,
redundancy, scope and visibility checks all apply and a concurrent change is a
conflict, not an overwrite. There is no way to create an accepted edge here that
was not first proposed. Visibility: a reviewer only sees edges whose BOTH endpoints
they can see, and flagged Goals they can see (private data never enters the queue
of someone who cannot read it).
"""
from __future__ import annotations

from typing import Any, Optional

from app.services.access import AccessScope, TenantScope, visibility_predicate

REVIEW_DECISIONS = {"accept": "accepted", "reject": "rejected"}


async def list_proposed_relations(
    pool: Any, *, access_scope: AccessScope, limit: int = 50, offset: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    s_vis, s_params = visibility_predicate(access_scope, alias="s", param_index=1)
    n_vis, n_params = visibility_predicate(access_scope, alias="n", param_index=1 + len(s_params))
    base = 1 + len(s_params) + len(n_params)
    page = min(max(int(limit), 1), 200)
    rows = await pool.fetch(
        f"""
        SELECT r.specific_goal_id::text AS specific_goal_id, r.abstract_goal_id::text AS abstract_goal_id,
               r.confidence, r.provenance, r.decision_id::text AS decision_id, r.decision_metadata,
               r.created_at, r.scope_type, r.scope_entity_id,
               s.canonical_name AS specific_name, s.short_description AS specific_description,
               n.canonical_name AS abstract_name, n.short_description AS abstract_description
          FROM goal_relations r
          JOIN goal_search_index s ON s.goal_id = r.specific_goal_id
          JOIN goal_search_index n ON n.goal_id = r.abstract_goal_id
         WHERE r.relation_type = 'SPECIALIZES' AND r.status = 'proposed'
           AND s.status IN ('active', 'candidate') AND n.status IN ('active', 'candidate')
           AND {s_vis} AND {n_vis}
         ORDER BY r.created_at, r.specific_goal_id, r.abstract_goal_id
         LIMIT ${base} OFFSET ${base + 1}
        """,
        *s_params, *n_params, page + 1, max(int(offset), 0),
    )
    items = [
        {
            "specific_goal": {"id": row["specific_goal_id"], "canonical_name": row["specific_name"],
                              "description": row["specific_description"]},
            "abstract_goal": {"id": row["abstract_goal_id"], "canonical_name": row["abstract_name"],
                              "description": row["abstract_description"]},
            "relation": "SPECIALIZES",
            "confidence": row["confidence"],
            "provenance": row["provenance"],
            "judge": {k: (row["decision_metadata"] or {}).get(k)
                      for k in ("judge_provider", "judge_model", "reason", "policy_version")
                      if (row["decision_metadata"] or {}).get(k) is not None},
            "proposed_at": row["created_at"],
        }
        for row in rows[:page]
    ]
    return items, len(rows) > page


async def decide_proposed_relation(
    pool: Any, *, specific_goal_id: str, abstract_goal_id: str, decision: str, reviewer: str,
    reason: str, access_scope: AccessScope,
) -> dict[str, Any]:
    """Accept or reject ONE proposed edge. Raises GoalRelationStatusConflict when the
    edge is not (or no longer) proposed, GoalRelationCycleError /
    GoalRelationRedundancyError / GoalRelationScopeError / GoalRelationVisibilityError
    when acceptance would break the DAG or cross scopes."""
    from app.services.goal_abstraction import persist_goal_relation

    if decision not in REVIEW_DECISIONS:
        raise ValueError("decision must be 'accept' or 'reject'")
    reason = (reason or "").strip()
    if not reason:
        raise ValueError("a reason is required for a review decision")
    tenant = await pool.fetchval(
        "SELECT tenant_id::text FROM goal_relations WHERE specific_goal_id = $1::uuid "
        "AND abstract_goal_id = $2::uuid AND relation_type = 'SPECIALIZES'",
        specific_goal_id, abstract_goal_id)
    tenant_scope = TenantScope.for_tenant(tenant) if tenant else TenantScope.commons()
    return await persist_goal_relation(
        pool, specific_goal_id, abstract_goal_id, status=REVIEW_DECISIONS[decision], provenance="reviewer",
        access_scope=access_scope, tenant_scope=tenant_scope, decided_by=reviewer,
        decision_metadata={"operation": "review", "decision": decision, "reason": reason[:1000],
                           "reviewer": reviewer},
        expected_status="proposed",
    )


async def record_review_item(pool: Any, *, goal_id: str, reason: str, detail: Optional[dict] = None) -> dict[str, Any]:
    """Open (idempotently) a review item for a Goal placement flagged. Scope and
    visibility are copied from the Goal's projection so the queue can filter."""
    goal = await pool.fetchrow(
        "SELECT scope_type, scope_entity_id, owner_id, visibility::text AS visibility "
        "FROM goal_search_index WHERE goal_id = $1::uuid", goal_id)
    if goal is None:
        return {"recorded": False, "reason": "goal_not_projected"}
    row = await pool.fetchrow(
        """
        INSERT INTO goal_review_items (goal_id, reason, scope_type, scope_entity_id, owner_id, visibility, detail)
        VALUES ($1::uuid, $2, $3, $4, $5, $6, $7::jsonb)
        ON CONFLICT (goal_id, reason) WHERE status = 'open' DO NOTHING
        RETURNING id::text AS id
        """,
        goal_id, reason, goal["scope_type"], goal["scope_entity_id"], goal["owner_id"], goal["visibility"],
        detail or {})
    return {"recorded": row is not None, "id": row["id"] if row else None}


async def list_review_items(
    pool: Any, *, access_scope: AccessScope, status: str = "open", limit: int = 50, offset: int = 0,
) -> tuple[list[dict[str, Any]], bool]:
    vis, params = visibility_predicate(access_scope, alias="i", param_index=2)
    page = min(max(int(limit), 1), 200)
    index = 2 + len(params)
    rows = await pool.fetch(
        f"""
        SELECT i.id::text AS id, i.goal_id::text AS goal_id, i.reason, i.status, i.created_at, i.resolved_at,
               i.resolved_by, g.canonical_name, g.short_description
          FROM goal_review_items i JOIN goal_search_index g ON g.goal_id = i.goal_id
         WHERE i.status = $1 AND {vis}
         ORDER BY i.created_at, i.id LIMIT ${index} OFFSET ${index + 1}
        """,
        status, *params, page + 1, max(int(offset), 0))
    return [dict(row) for row in rows[:page]], len(rows) > page


async def close_review_item(pool: Any, *, item_id: str, reviewer: str, status: str) -> bool:
    if status not in ("resolved", "dismissed"):
        raise ValueError("status must be 'resolved' or 'dismissed'")
    tag = await pool.execute(
        "UPDATE goal_review_items SET status = $2, resolved_at = now(), resolved_by = $3 "
        "WHERE id = $1::uuid AND status = 'open'", item_id, status, reviewer)
    return str(tag).endswith(" 1")
