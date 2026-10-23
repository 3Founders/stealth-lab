"""A Goal's contract as linked claims, and the Goal's status computed from the evidence on them (migration 153).

The primitive stays the Goal. Its contract is the set of claims linked to it, each with a role: `assumption` (must hold before) or
`guarantee` (must hold after). Nobody types a status: `goal_status` (a view, never a column) works out verified | open | failed |
contested | expired from the evidence on the guarantee claims, and `goals.status` keeps its own meaning (the record's lifecycle).

This module is the WRITER that goes with migration 153's gate (hard rule 6): it links and ends links; the trigger that refuses a
non-claim and the rule that a link is ended, never edited, live in the migration. It also reads the status for the resource and the
client. Docstrings say what is NOT done: composition across the Goal tree and human review as typed evidence are next.
"""
from __future__ import annotations

import uuid
from typing import Any, Optional

ROLES: tuple[str, ...] = ("assumption", "guarantee")
STATUSES: tuple[str, ...] = ("verified", "open", "failed", "contested", "expired")


class GoalClaimError(ValueError):
    """A malformed or refused link request. The message is shown to the caller as is."""


def _uuid_str(value: Any, label: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise GoalClaimError(f"{label} must be a uuid") from exc


def status_reason(row: dict) -> str:
    """One plain sentence for the status, from the counts the view returns."""
    status = row.get("status")
    n = int(row.get("guarantees") or 0)
    if status == "expired":
        return "a claim in this Goal's contract was superseded after the evidence was recorded"
    if status == "failed":
        return f"{row.get('failed_claims', 0)} of {n} guarantee claim(s) failed their check"
    if status == "contested":
        return f"{row.get('contested_claims', 0)} of {n} guarantee claim(s) have both passing and failing evidence"
    if status == "verified":
        return f"all {n} guarantee claim(s) have passing evidence and none fails"
    if n == 0:
        return "no live guarantee claim is linked: the contract is empty"
    return f"{row.get('verified_claims', 0)} of {n} guarantee claim(s) verified; the rest have no machine-checked evidence yet"


async def link_goal_claim(pool, *, goal_id: str, claim_id: str, role: str, scope, visibility: str = "private") -> dict:
    """Link `claim_id` to `goal_id` with `role`, as `scope.viewer_id`. Idempotent per (goal, claim, role, owner).
    -> {linked, duplicate, id, goal_id, claim_id, role}"""
    from app.services import goals as _goals
    from app.services.access import bind_scope, routing_readers_of, scoped_transaction, visibility_predicate
    from app.services.v0_gate import validate_provenance, validate_scope
    from app.utils.ids import uuid7_str

    viewer = getattr(scope, "viewer_id", None)
    if not viewer:
        raise GoalClaimError("sign in to link a claim to a Goal")
    goal_id, claim_id = _uuid_str(goal_id, "goal_id"), _uuid_str(claim_id, "claim_id")
    if role not in ROLES:
        raise GoalClaimError(f"role must be one of {ROLES}")
    if visibility not in ("private", "public"):
        raise GoalClaimError("visibility must be private or public")
    goal = await _goals.get_goal(pool, goal_id, scope=scope)
    if goal is None:
        raise GoalClaimError(f"goal {goal_id} was not found")
    if visibility == "public" and str(goal.get("visibility") or "public") != "public":
        raise GoalClaimError("a public link needs a public Goal")
    vis_sql, vis_params = visibility_predicate(scope, param_index=2)
    claim = await pool.fetchrow(
        f"SELECT id FROM knowledge_nodes WHERE id = $1::uuid AND node_type = 'claim' AND t_invalid IS NULL AND {vis_sql}",
        claim_id, *vis_params)
    if claim is None:
        raise GoalClaimError(f"claim {claim_id} was not found (it must be a live claim you may see)")
    # V0 gate: the scope of the link is the Goal's own; a Goal with none is the linker's own
    g_type, g_entity = goal.get("scope_type"), goal.get("scope_entity_id")
    if not g_type:
        g_type, g_entity = "user", str(viewer)
    elif g_type == "global":
        g_entity = None
    scope_type, scope_entity = validate_scope(g_type, g_entity)
    provenance = validate_provenance("company_ingested", derived=False)
    async with scoped_transaction(pool, owner=viewer, readers=routing_readers_of(scope)) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO goal_claims (id, goal_id, claim_id, role, provenance, created_by, visibility, owner_id, scope_type, scope_entity_id)
            VALUES ($1::uuid, $2::uuid, $3::uuid, $4, $5, $6, $7::visibility_level, $6, $8, $9)
            ON CONFLICT (goal_id, claim_id, role, COALESCE(owner_id, '')) WHERE t_invalid IS NULL DO NOTHING
            RETURNING id
            """,
            uuid7_str(), goal_id, claim_id, role, provenance, viewer, visibility, scope_type, scope_entity)
        if row is None:
            existing = await conn.fetchrow(
                "SELECT id FROM goal_claims WHERE goal_id = $1::uuid AND claim_id = $2::uuid AND role = $3 AND owner_id = $4 AND t_invalid IS NULL",
                goal_id, claim_id, role, viewer)
            return {"linked": False, "duplicate": True, "id": str(existing["id"]) if existing else None,
                    "goal_id": goal_id, "claim_id": claim_id, "role": role}
    return {"linked": True, "duplicate": False, "id": str(row["id"]), "goal_id": goal_id, "claim_id": claim_id, "role": role}


async def unlink_goal_claim(pool, *, link_id: str, scope) -> dict:
    """End a link (set t_invalid; nothing is deleted). Only a link the caller may see can be ended; the engine refuses a second end."""
    from app.services.access import routing_readers_of, scoped_transaction

    viewer = getattr(scope, "viewer_id", None)
    if not viewer:
        raise GoalClaimError("sign in to end a link")
    link_id = _uuid_str(link_id, "link_id")
    async with scoped_transaction(pool, owner=viewer, readers=routing_readers_of(scope)) as conn:
        row = await conn.fetchrow(
            "UPDATE goal_claims SET t_invalid = now() WHERE id = $1::uuid AND t_invalid IS NULL AND owner_id = $2 RETURNING id",
            link_id, viewer)
    if row is None:
        raise GoalClaimError(f"link {link_id} was not found, is not yours, or is already ended")
    return {"ended": True, "id": str(row["id"])}


async def goal_status(pool, goal_id: str, *, scope, tenant_scope=None) -> Optional[dict]:
    """The computed status of one Goal, with the claims behind it. None when the Goal is not visible to `scope`.

    Read under the caller's own scopes (their tenant for the evidence, their owner and organisations for the links): another
    account's private links and evidence do not count for you. `client_checks` is the caller's own client-reported checks for this
    Goal (migration 152), shown beside the status and NOT part of it: a local check is not evidence on a claim."""
    from app.services import goals as _goals
    from app.services.access import TenantScope, bind_scope, routing_readers_of, tenant_transaction

    goal_id = _uuid_str(goal_id, "goal_id")
    if await _goals.get_goal(pool, goal_id, scope=scope) is None:
        return None
    async with tenant_transaction(pool, tenant_scope or TenantScope.commons()) as conn:
        await bind_scope(conn, owner=getattr(scope, "viewer_id", None), readers=routing_readers_of(scope))
        row = await conn.fetchrow("SELECT * FROM goal_status WHERE goal_id = $1::uuid", goal_id)
        links = await conn.fetch(
            "SELECT claim_id, role, state, claim_closed, pass_groups, fail_groups FROM goal_claim_state "
            "WHERE goal_id = $1::uuid ORDER BY role, link_id", goal_id)
        checks = await conn.fetch(
            "SELECT result, count(*) AS n FROM local_check_current WHERE goal_id = $1::uuid GROUP BY result", goal_id)
    data = dict(row) if row else {"goal_id": goal_id, "status": "open", "guarantees": 0}
    out = {
        "goal_id": goal_id, "status": data["status"], "why": status_reason(data),
        "counts": {k: int(data.get(k) or 0) for k in
                   ("guarantees", "verified_claims", "failed_claims", "contested_claims", "open_claims", "assumptions", "closed_links")},
        "claims": [{"claim_id": str(r["claim_id"]), "role": r["role"], "state": r["state"], "closed": bool(r["claim_closed"]),
                    "pass_groups": int(r["pass_groups"]), "fail_groups": int(r["fail_groups"])} for r in links],
        "client_checks": {r["result"]: int(r["n"]) for r in checks},
    }
    return out
