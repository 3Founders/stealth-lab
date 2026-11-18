"""Organization view of the people's relay agents and the tool approvals waiting on them.

An agent and an approval are both owned by a person (`owner_sub` / `owner_id`), not by the organization. This
module answers the org-level questions without changing that model:

* which agents do members of this organization run (and who owns each one);
* which pending tool approvals belong to members of this organization.

Reading is for owners/admins. Deciding an approval or revoking an agent is allowed for an owner/admin, or for
the person who owns it. Every write is audited through the organization's governance chain.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any, Collection, Optional

from app.services import org_governance as gov
from app.services.access import tenant_transaction
from app.tool_routing import approvals
from app.utils.ids import uuid7

NAME_RE = re.compile(r"^[a-z0-9][a-z0-9_.-]{0,39}$")
DATA_CEILINGS = ("PUBLIC_SOURCE", "PUBLIC_DERIVED", "GLOBAL_PROCEDURE", "USER_PRIVATE")
MAX_SCOPES = 50


def _iso(v: Any) -> Optional[str]:
    return v.isoformat() if v is not None else None


def _clean_scopes(value: Any) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, (list, tuple)) or not all(isinstance(v, str) for v in value):
        raise gov.Invalid("scopes must be a list of strings")
    out = [v.strip() for v in value if v.strip()]
    if len(out) > MAX_SCOPES:
        raise gov.Invalid(f"at most {MAX_SCOPES} scopes")
    return list(dict.fromkeys(out))


async def list_agents(pool: Any, *, actor_roles: Collection[str], org_id: str) -> list[dict[str, Any]]:
    gov._require(actor_roles, gov.ADMIN_ROLES, "listing agents")
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        rows = await conn.fetch(
            """SELECT a.id::text AS id, a.name, a.kind, a.created_at, a.last_seen, a.revoked_at,
                      a.scopes, a.data_ceiling, a.budget_usd_month, a.expires_at,
                      u.external_subject AS owner_sub, u.display_name AS owner_name, u.email AS owner_email
                 FROM delegate_agents a
                 JOIN users u ON u.external_subject = a.owner_sub
                 JOIN org_memberships m ON m.user_id = u.id AND m.organization_id = $1::uuid AND m.t_expired IS NULL
                ORDER BY (a.revoked_at IS NULL) DESC, a.created_at DESC
                LIMIT 500""", str(org_id))
    now = datetime.now(timezone.utc)
    out = []
    for r in rows:
        last = r["last_seen"]
        out.append({
            "id": r["id"], "name": r["name"], "kind": r["kind"], "owner_sub": r["owner_sub"],
            "owner_name": r["owner_name"], "owner_email": r["owner_email"],
            "created_at": _iso(r["created_at"]), "last_seen": _iso(last), "revoked_at": _iso(r["revoked_at"]),
            "online": bool(last and (now - last).total_seconds() <= 60),
            "scopes": list(r["scopes"] or []), "data_ceiling": r["data_ceiling"],
            "budget_usd_month": str(r["budget_usd_month"]) if r["budget_usd_month"] is not None else None,
            "expires_at": _iso(r["expires_at"]),
        })
    return out


async def create_agent(pool: Any, *, actor_subject: str, actor_user_id: Optional[str], actor_roles: Collection[str],
                       org_id: str, name: str, kind: str = "agent", scopes: Any = None,
                       data_ceiling: str = "PUBLIC_SOURCE", budget_usd_month: Any = None,
                       expires_days: Optional[int] = None) -> dict[str, Any]:
    gov._require(actor_roles, gov.ADMIN_ROLES, "creating an agent")
    name = (name or "").strip().lower()
    kind = (kind or "agent").strip().lower()[:40] or "agent"
    if not NAME_RE.match(name):
        raise gov.Invalid("name must be 1-40 letters, digits, dots, dashes or underscores, starting with a letter or digit")
    ceiling = (data_ceiling or "PUBLIC_SOURCE").strip().upper()
    if ceiling not in DATA_CEILINGS:
        raise gov.Invalid(f"data ceiling must be one of {', '.join(DATA_CEILINGS)}")
    clean_scopes = _clean_scopes(scopes)
    budget = None
    if budget_usd_month is not None and str(budget_usd_month).strip() != "":
        budget = gov._clean_money("budget_usd_month", budget_usd_month)
    if expires_days is not None and (not isinstance(expires_days, int) or not 1 <= expires_days <= 365):
        raise gov.Invalid("expires_days must be a whole number from 1 to 365")
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        live = await conn.fetchval(
            "SELECT count(*) FROM delegate_agents WHERE owner_sub = $1 AND revoked_at IS NULL", actor_subject)
        if int(live or 0) >= 20:
            raise gov.Conflict("at most 20 live agents per person; revoke one first")
        try:
            row = await conn.fetchrow(
                """INSERT INTO delegate_agents
                       (id, owner_sub, name, kind, scopes, data_ceiling, budget_usd_month, expires_at, sponsor_sub)
                   VALUES ($1, $2, $3, $4, $5::text[], $6, $7,
                           CASE WHEN $8::int IS NULL THEN NULL ELSE now() + make_interval(days => $8::int) END, $9)
                   RETURNING id::text AS id, name, kind, created_at""",
                uuid7(), actor_subject, name, kind, clean_scopes, ceiling, budget, expires_days, actor_subject)
        except Exception as exc:  # noqa: BLE001 -- unique violation without importing asyncpg
            if "unique" in str(exc).lower() or "delegate_agents_owner_name_live" in str(exc):
                raise gov.Conflict(f"you already have an agent named {name!r}") from exc
            raise
        await gov._audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id, action="org_agent.create",
                         object_type="agent", object_id=row["id"], org_id=org_id,
                         details={"name": name, "kind": kind, "scopes": clean_scopes, "data_ceiling": ceiling})
    return {"id": row["id"], "name": row["name"], "kind": row["kind"], "owner_sub": actor_subject,
            "scopes": clean_scopes, "data_ceiling": ceiling,
            "budget_usd_month": str(budget) if budget is not None else None,
            "created_at": _iso(row["created_at"])}


async def revoke_agent(pool: Any, *, actor_subject: str, actor_user_id: Optional[str], actor_roles: Collection[str],
                       org_id: str, agent_id: str) -> dict[str, Any]:
    """Revoke one agent. Its owner may always do this; an org owner/admin may do it for a member."""
    gov._require(actor_roles, gov.ADMIN_ROLES, "revoking an agent")
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        row = await conn.fetchrow(
            """SELECT a.id::text AS id, a.owner_sub, a.name FROM delegate_agents a
                 JOIN users u ON u.external_subject = a.owner_sub
                 JOIN org_memberships m ON m.user_id = u.id AND m.organization_id = $1::uuid AND m.t_expired IS NULL
                WHERE a.id = $2::uuid""", str(org_id), str(agent_id))
        if row is None:
            raise gov.NotFound("no such agent in this organization")
        if row["owner_sub"] != actor_subject and "owner" not in actor_roles and "admin" not in actor_roles:
            raise gov.NotAuthorized("only an owner or admin can revoke another person's agent")
        await conn.execute("UPDATE delegate_agents SET revoked_at = COALESCE(revoked_at, now()) WHERE id = $1::uuid",
                           str(agent_id))
        cancelled = await conn.execute(
            "UPDATE delegate_tasks SET status = 'cancelled', finished_at = now() "
            "WHERE agent_id = $1::uuid AND status = ANY($2::text[])",
            str(agent_id), ["needs_approval", "queued", "claimed"])
        await gov._audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id, action="org_agent.revoke",
                         object_type="agent", object_id=str(agent_id), org_id=org_id,
                         details={"owner_sub": row["owner_sub"], "name": row["name"]})
    return {"id": str(agent_id), "revoked": True,
            "cancelled": int(str(cancelled).rsplit(" ", 1)[-1]) if cancelled else 0}


async def list_approvals(pool: Any, *, actor_roles: Collection[str], org_id: str,
                         status: str = "pending") -> list[dict[str, Any]]:
    gov._require(actor_roles, gov.ADMIN_ROLES, "listing approvals")
    where = "a.status = $2" if status and status != "all" else "TRUE"
    args: list[Any] = [str(org_id)] + ([status] if where != "TRUE" else [])
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        rows = await conn.fetch(
            f"""SELECT a.id, a.owner_id, a.task_id, a.goal_id::text AS goal_id, a.server, a.tool, a.args_hash,
                       a.risk, a.data_class, a.cert_id, a.status, a.requested_at, a.decided_at, a.expires_at,
                       a.used_at, a.decided_by, a.note,
                       u.display_name AS owner_name, u.email AS owner_email
                  FROM tool_approvals a
                  JOIN users u ON u.external_subject = a.owner_id
                  JOIN org_memberships m ON m.user_id = u.id AND m.organization_id = $1::uuid AND m.t_expired IS NULL
                 WHERE {where}
                 ORDER BY a.requested_at DESC LIMIT 200""", *args)
    return [{**dict(r), "goal_id": r["goal_id"],
             "requested_at": _iso(r["requested_at"]), "decided_at": _iso(r["decided_at"]),
             "expires_at": _iso(r["expires_at"]), "used_at": _iso(r["used_at"])} for r in rows]


async def decide_approval(pool: Any, *, actor_subject: str, actor_user_id: Optional[str],
                          actor_roles: Collection[str], org_id: str, approval_id: str, yes: bool) -> dict[str, Any]:
    """Approve or deny one pending approval. The owner may decide their own; an owner/admin may decide for a member."""
    gov._require(actor_roles, gov.ADMIN_ROLES, "deciding an approval")
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        row = await conn.fetchrow(
            """SELECT a.id, a.owner_id, a.status FROM tool_approvals a
                 JOIN users u ON u.external_subject = a.owner_id
                 JOIN org_memberships m ON m.user_id = u.id AND m.organization_id = $1::uuid AND m.t_expired IS NULL
                WHERE a.id = $2""", str(org_id), str(approval_id))
        if row is None:
            raise gov.NotFound("no such approval in this organization")
        if row["owner_id"] != actor_subject and "owner" not in actor_roles and "admin" not in actor_roles:
            raise gov.NotAuthorized("only an owner or admin can decide another person's approval")
        if yes:
            ok = await approvals.approve(conn, approval_id, row["owner_id"], decided_by=actor_subject)
        else:
            ok = await approvals.deny(conn, approval_id, row["owner_id"], decided_by=actor_subject)
        if not ok:
            raise gov.Conflict("that approval is no longer pending")
        await gov._audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id,
                         action="tool_approval.approve" if yes else "tool_approval.deny",
                         object_type="tool_approval", object_id=str(approval_id), org_id=org_id,
                         details={"owner_sub": row["owner_id"]})
    return {"id": str(approval_id), "status": "approved" if yes else "denied"}
