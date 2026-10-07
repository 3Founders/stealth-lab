"""Organisation admin API: what an organisation's admins control (policy, kill switch), see (usage, audit log) and
decide (legal holds, erasure). Everything here is a thin wrapper over services/org_governance.py, which enforces the
roles again itself.

Who may call: a signed-in HUMAN who is a member of the organisation. A caller who is not a member gets 404 (the
organisation's existence is not revealed); a member without the needed role gets 403. A service credential or an
anonymous caller never reaches these routes.

No defaults hide here: the policy read returns 409 until an admin has set one, the export needs an explicit date range,
and erasure answers with a manifest that names everything it could not remove.
"""
from __future__ import annotations

import csv
import io
import json
from datetime import datetime, timezone
from typing import Any, Optional

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.api.deps import get_auth_context
from app.services import org_governance as gov
from app.services.auth_context import AuthContext

router = APIRouter(prefix="/v1/orgs/{org_id}", tags=["org-admin"])
mine_router = APIRouter(prefix="/v1/orgs", tags=["org-admin"])      # the one route that has no org id yet

_ROLE_ORDER = ("owner", "admin", "member", "viewer")


async def get_pool(request: Request):
    return request.app.state.pool


def _caller(ctx: Any, org_id: str) -> tuple[AuthContext, frozenset[str]]:
    if not isinstance(ctx, AuthContext):
        raise HTTPException(401, "sign in as a person to use organization admin routes")
    roles = ctx.role_in(org_id)
    if not roles:
        raise HTTPException(404, "no such organization")
    return ctx, roles


@mine_router.get("/mine")
async def my_organizations(pool=Depends(get_pool), ctx=Depends(get_auth_context)) -> list[dict]:
    """The organisations the caller belongs to, with their highest role in each. Members only; a person with no
    organisation gets []. Names come from the database; a membership whose organisation row is gone is left out."""
    if not isinstance(ctx, AuthContext):
        raise HTTPException(401, "sign in as a person to list your organizations")
    ids = [str(o) for o in ctx.org_ids]
    if not ids:
        return []
    rows = await pool.fetch("SELECT id::text AS id, name FROM organizations WHERE id = ANY($1::uuid[]) "
                            "AND t_expired IS NULL", ids)
    names = {r["id"]: r["name"] for r in rows}
    out = []
    for org in ids:
        if org not in names:
            continue
        roles = ctx.role_in(org)
        ordered = [r for r in _ROLE_ORDER if r in roles]
        if not ordered:
            continue
        out.append({"organization_id": org, "name": names[org], "role": ordered[0], "roles": ordered})
    return sorted(out, key=lambda o: o["name"].lower())


def _http(exc: gov.GovernanceError) -> HTTPException:
    return HTTPException(exc.status_code, str(exc))


def _when(value: str, name: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError as exc:
        raise HTTPException(422, f"{name} must be an ISO date or datetime") from exc
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=timezone.utc)


class PolicyBody(BaseModel):
    expected_version: Optional[int] = Field(default=None, description="the version you read; omit to create")
    policy: dict[str, Any]


class KillSwitchBody(BaseModel):
    on: bool
    reason: str


class ReasonBody(BaseModel):
    reason: str


@router.get("/policy")
async def read_policy(org_id: str, pool=Depends(get_pool), ctx=Depends(get_auth_context)) -> dict:
    _, roles = _caller(ctx, org_id)
    if not roles & gov.ADMIN_ROLES:
        raise HTTPException(403, "reading the policy needs the role owner or admin")
    try:
        return (await gov.load_policy(pool, org_id)).as_dict()
    except gov.GovernanceError as exc:
        raise _http(exc) from exc


@router.put("/policy")
async def write_policy(org_id: str, body: PolicyBody, pool=Depends(get_pool), ctx=Depends(get_auth_context)) -> dict:
    caller, roles = _caller(ctx, org_id)
    try:
        policy = await gov.put_policy(pool, actor_subject=caller.subject, actor_user_id=caller.user_id,
                                      actor_roles=roles, org_id=org_id, changes=body.policy,
                                      expected_version=body.expected_version)
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return policy.as_dict()


@router.post("/kill-switch")
async def kill_switch(org_id: str, body: KillSwitchBody, pool=Depends(get_pool), ctx=Depends(get_auth_context)) -> dict:
    caller, roles = _caller(ctx, org_id)
    try:
        policy = await gov.set_kill_switch(pool, actor_subject=caller.subject, actor_user_id=caller.user_id,
                                           actor_roles=roles, org_id=org_id, on=body.on, reason=body.reason)
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return policy.as_dict()


@router.get("/usage")
async def usage(org_id: str, since: str = Query(...), until: str = Query(...), pool=Depends(get_pool),
                ctx=Depends(get_auth_context)) -> dict:
    _, roles = _caller(ctx, org_id)
    try:
        rows = await gov.usage_daily(pool, actor_roles=roles, org_id=org_id, since=_when(since, "since"),
                                     until=_when(until, "until"))
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return {"organization_id": org_id, "rows": json.loads(json.dumps(rows, default=str))}


@router.get("/members")
async def members(org_id: str, pool=Depends(get_pool), ctx=Depends(get_auth_context)) -> dict:
    """Current members and roles (read only). `subject` matches `user_id` in the usage, calls and denials data."""
    _, roles = _caller(ctx, org_id)
    try:
        rows = await gov.list_members(pool, actor_roles=roles, org_id=org_id)
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return {"organization_id": org_id, "members": json.loads(json.dumps(rows, default=str))}


@router.get("/usage/users")
async def usage_users(org_id: str, since: str = Query(...), until: str = Query(...), pool=Depends(get_pool),
                      ctx=Depends(get_auth_context)) -> dict:
    """Spend and tokens per person per day. `user_id` is the identity provider's user id, not an email."""
    _, roles = _caller(ctx, org_id)
    try:
        rows = await gov.usage_by_user(pool, actor_roles=roles, org_id=org_id, since=_when(since, "since"),
                                       until=_when(until, "until"))
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return {"organization_id": org_id, "rows": json.loads(json.dumps(rows, default=str))}


@router.get("/budget")
async def budget(org_id: str, pool=Depends(get_pool), ctx=Depends(get_auth_context)) -> dict:
    """The organisation's and each person's position against the policy's budgets right now (settled spend plus live
    holds). 409 until a policy exists."""
    _, roles = _caller(ctx, org_id)
    try:
        out = await gov.budget_status(pool, actor_roles=roles, org_id=org_id)
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return json.loads(json.dumps(out, default=str))


@router.get("/calls")
async def calls(org_id: str, since: str = Query(...), until: str = Query(...), after: Optional[str] = None,
                limit: int = Query(200, ge=1, le=1000), model: Optional[str] = None, tool: Optional[str] = None,
                status: Optional[str] = None, user: Optional[str] = None, pool=Depends(get_pool),
                ctx=Depends(get_auth_context)) -> dict:
    """Metadata for every provider call (no prompt, no reply), oldest first, paged by `next_after`."""
    _, roles = _caller(ctx, org_id)
    try:
        page = await gov.list_calls(pool, actor_roles=roles, org_id=org_id, since=_when(since, "since"),
                                    until=_when(until, "until"), after=after, limit=limit, model=model, tool=tool,
                                    status=status, user=user)
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return json.loads(json.dumps(page, default=str))


@router.get("/denials")
async def denials(org_id: str, since: str = Query(...), until: str = Query(...), after: Optional[str] = None,
                  limit: int = Query(200, ge=1, le=1000), pool=Depends(get_pool),
                  ctx=Depends(get_auth_context)) -> dict:
    """What the organisation's policy and budgets refused: counts by reason and the events."""
    _, roles = _caller(ctx, org_id)
    try:
        page = await gov.list_denials(pool, actor_roles=roles, org_id=org_id, since=_when(since, "since"),
                                      until=_when(until, "until"), after=after, limit=limit)
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return json.loads(json.dumps(page, default=str))


@router.get("/performance/summary")
async def performance_summary(org_id: str, since: str = Query(...), until: str = Query(...),
                              group_by: str = Query("total"), pool=Depends(get_pool),
                              ctx=Depends(get_auth_context)) -> dict:
    """True merged p50/p95/p99 over the whole range (not an average of daily values), grouped by model, provider,
    tool or tier, or `total`. Range limited to 366 days."""
    _, roles = _caller(ctx, org_id)
    try:
        rows = await gov.performance_summary(pool, actor_roles=roles, org_id=org_id, since=_when(since, "since"),
                                             until=_when(until, "until"), group_by=group_by)
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return {"organization_id": org_id, "group_by": group_by, "rows": json.loads(json.dumps(rows, default=str))}


@router.get("/performance")
async def performance(org_id: str, since: str = Query(...), until: str = Query(...), pool=Depends(get_pool),
                      ctx=Depends(get_auth_context)) -> dict:
    """p50/p95/p99 provider latency and our own gate time per model and tier, our share of total time, and cache hit
    rates. Admin/owner only."""
    _, roles = _caller(ctx, org_id)
    try:
        rows = await gov.performance_daily(pool, actor_roles=roles, org_id=org_id, since=_when(since, "since"),
                                           until=_when(until, "until"))
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return {"organization_id": org_id, "rows": json.loads(json.dumps(rows, default=str))}


_AUDIT_COLUMNS = ("id", "t_created", "actor_subject", "actor_user_id", "action", "object_type", "object_id",
                  "tenant_id", "details", "prev_hash", "row_hash")


@router.get("/audit")
async def audit(org_id: str, since: str = Query(...), until: str = Query(...), after_id: int = Query(0, ge=0),
                limit: int = Query(1000, ge=1, le=gov.MAX_EXPORT_ROWS), format: str = Query("json", pattern="^(json|csv)$"),
                pool=Depends(get_pool), ctx=Depends(get_auth_context)) -> Response:
    """The organisation's audit events, oldest first, paged by `after_id`. The reply says whether the hash chain
    verified; the CSV form puts that, and the next cursor, in response headers."""
    caller, roles = _caller(ctx, org_id)
    try:
        page = await gov.audit_export(pool, actor_roles=roles, org_id=org_id, since=_when(since, "since"),
                                      until=_when(until, "until"), after_id=after_id, limit=limit)
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    headers = {"X-Chain-Intact": str(page["chain_intact"]).lower(),
               "X-Next-After-Id": "" if page["next_after_id"] is None else str(page["next_after_id"])}
    if format == "csv":
        buf = io.StringIO()
        writer = csv.writer(buf)
        writer.writerow(_AUDIT_COLUMNS)
        for e in page["events"]:
            writer.writerow([json.dumps(e[c], default=str) if c == "details" else e[c] for c in _AUDIT_COLUMNS])
        return Response(buf.getvalue(), media_type="text/csv", headers=headers)
    return Response(json.dumps(page, default=str), media_type="application/json", headers=headers)


@router.post("/legal-holds", status_code=201)
async def place_hold(org_id: str, body: ReasonBody, pool=Depends(get_pool), ctx=Depends(get_auth_context)) -> dict:
    caller, roles = _caller(ctx, org_id)
    try:
        hold_id = await gov.place_legal_hold(pool, actor_subject=caller.subject, actor_user_id=caller.user_id,
                                             actor_roles=roles, org_id=org_id, reason=body.reason)
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return {"hold_id": hold_id}


@router.delete("/legal-holds/{hold_id}", status_code=204)
async def release_hold(org_id: str, hold_id: str, pool=Depends(get_pool), ctx=Depends(get_auth_context)) -> Response:
    caller, roles = _caller(ctx, org_id)
    try:
        await gov.release_legal_hold(pool, actor_subject=caller.subject, actor_user_id=caller.user_id,
                                     actor_roles=roles, org_id=org_id, hold_id=hold_id)
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return Response(status_code=204)


@router.post("/erasure-requests", status_code=201)
async def request_erasure(org_id: str, body: ReasonBody, pool=Depends(get_pool), ctx=Depends(get_auth_context)) -> dict:
    caller, roles = _caller(ctx, org_id)
    try:
        request_id = await gov.request_erasure(pool, actor_subject=caller.subject, actor_user_id=caller.user_id,
                                               actor_roles=roles, org_id=org_id, reason=body.reason)
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return {"request_id": request_id, "status": "requested", "next": "a different owner must approve it"}


@router.post("/erasure-requests/{request_id}/approve")
async def approve_erasure(org_id: str, request_id: str, pool=Depends(get_pool), ctx=Depends(get_auth_context)) -> dict:
    caller, roles = _caller(ctx, org_id)
    try:
        await gov.approve_erasure(pool, actor_subject=caller.subject, actor_user_id=caller.user_id,
                                  actor_roles=roles, org_id=org_id, request_id=request_id)
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
    return {"request_id": request_id, "status": "approved"}


@router.post("/erasure-requests/{request_id}/execute")
async def execute_erasure(org_id: str, request_id: str, pool=Depends(get_pool), ctx=Depends(get_auth_context)) -> dict:
    """Runs what can run today and says plainly what cannot: `completed` is true only when nothing is blocked."""
    caller, roles = _caller(ctx, org_id)
    try:
        return await gov.execute_erasure(pool, actor_subject=caller.subject, actor_user_id=caller.user_id,
                                         actor_roles=roles, org_id=org_id, request_id=request_id)
    except gov.GovernanceError as exc:
        raise _http(exc) from exc
