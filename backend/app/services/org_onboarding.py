"""Getting an organisation's developers on: invitations, joining, the client configuration, and the admin's checklist.

The flow this serves:
  1. An operator creates the organisation and its first owner (backend/scripts/provision_org.py). There is no self-serve
     sign-up; that is deliberate for the first customers.
  2. The owner signs in to the admin console, connects the company's provider key (services/org_keys.py), applies the
     recommended policy with a budget they type, and creates an invite.
  3. A developer runs `kell join <code>`: they sign in, `join` below adds them to the organisation as a
     `member`, and `client_config` tells their machine which models to use and where the gateway is.
  4. The CLI's self-test makes one tiny call through the gateway; the ledger row it writes is what counts them as ready.

An invite is a random secret shown once; only its SHA-256 is stored. The code also names the organisation
(`sl1_<org id as 32 hex>_<secret>`), so redeeming it can bind the right tenant before reading anything, and a wrong or
used code gets the same answer as an unknown one. Redeeming twice as the same person consumes one use.

Limits: no email delivery (the admin shares the command), no per-invite role (everyone joins as `member`), no removal or
role-change route (list_members says the same), and `ready` means "made at least one settled gateway call", which a
self-test satisfies but does not prove their editor works.
"""
from __future__ import annotations

import hashlib
import json
import re
import secrets
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Collection, Mapping, Optional

from app.config import settings
from app.services import org_governance as gov
from app.services import org_keys
from app.services.access import tenant_transaction
from app.services.auth_context import AuthContext

MEMBER_ROLE_ID = "10000000-0000-0000-0000-000000000003"      # db/28_identity.sql seeds the four builtin roles
CODE = re.compile(r"sl1_([0-9a-f]{32})_([0-9a-f]{24})")
MAX_INVITE_DAYS = 30
MAX_INVITE_USES = 5000
RECOMMENDED_TOOLS = ("chat_completions", "call_model")
GATEWAY_TOOL = "chat_completions"


def _hash(secret: str) -> str:
    return hashlib.sha256(secret.encode("utf-8")).hexdigest()


def _org_from_hex(h: str) -> str:
    return str(uuid.UUID(h))


def parse_code(code: str) -> tuple[str, str]:
    m = CODE.fullmatch((code or "").strip())
    if not m:
        raise gov.NotFound("this invite is not valid")
    return _org_from_hex(m.group(1)), m.group(2)


# ------------------------------------------------------------------ invites (admin side)

async def create_invite(pool: Any, *, actor_subject: str, actor_user_id: Optional[str], actor_roles: Collection[str],
                        org_id: str, label: Optional[str], days: int, max_uses: int) -> dict[str, Any]:
    gov._require(actor_roles, gov.ADMIN_ROLES, "creating an invite")
    if not isinstance(days, int) or not 1 <= days <= MAX_INVITE_DAYS:
        raise gov.Invalid(f"days must be a whole number from 1 to {MAX_INVITE_DAYS}")
    if not isinstance(max_uses, int) or not 1 <= max_uses <= MAX_INVITE_USES:
        raise gov.Invalid(f"max_uses must be a whole number from 1 to {MAX_INVITE_USES}")
    if label is not None and (not isinstance(label, str) or len(label) > 80):
        raise gov.Invalid("label must be text of at most 80 characters")
    secret = secrets.token_hex(12)
    code = f"sl1_{uuid.UUID(str(org_id)).hex}_{secret}"
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        row = await conn.fetchrow(
            "INSERT INTO org_invites (organization_id, secret_hash, label, created_by, expires_at, max_uses) "
            "VALUES ($1::uuid, $2, $3, $4, $5, $6) RETURNING id::text AS id, created_at",
            str(org_id), _hash(secret), (label or "").strip() or None, actor_subject, expires, max_uses)
        await gov._audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id, action="org_invite.create",
                         object_type="org_invite", object_id=row["id"], org_id=org_id,
                         details={"label": label, "expires_at": expires.isoformat(), "max_uses": max_uses})
    # the only time the code exists in the clear
    return {"id": row["id"], "code": code, "expires_at": expires.isoformat(), "max_uses": max_uses, "label": label}


async def list_invites(pool: Any, *, actor_roles: Collection[str], org_id: str) -> list[dict[str, Any]]:
    gov._require(actor_roles, gov.ADMIN_ROLES, "listing invites")
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        rows = await conn.fetch(
            "SELECT id::text AS id, label, created_by, created_at, expires_at, max_uses, uses, revoked_at, "
            "(revoked_at IS NULL AND expires_at > now() AND uses < max_uses) AS active "
            "FROM org_invites WHERE organization_id = $1::uuid ORDER BY created_at DESC LIMIT 200", str(org_id))
    return [{**dict(r), "created_at": r["created_at"].isoformat(), "expires_at": r["expires_at"].isoformat(),
             "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None} for r in rows]


async def revoke_invite(pool: Any, *, actor_subject: str, actor_user_id: Optional[str], actor_roles: Collection[str],
                        org_id: str, invite_id: str) -> None:
    gov._require(actor_roles, gov.ADMIN_ROLES, "revoking an invite")
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        row = await conn.fetchrow(
            "UPDATE org_invites SET revoked_at = now(), revoked_by = $3 WHERE organization_id = $1::uuid AND "
            "id = $2::uuid AND revoked_at IS NULL RETURNING id", str(org_id), invite_id, actor_subject)
        if row is None:
            raise gov.NotFound("no such active invite")
        await gov._audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id, action="org_invite.revoke",
                         object_type="org_invite", object_id=invite_id, org_id=org_id, details={})


# ------------------------------------------------------------------ joining (developer side)

async def redeem_invite(pool: Any, ctx: AuthContext, code: str) -> dict[str, Any]:
    """Add the signed-in person to the invite's organisation as a member. One generic error for every way an invite can
    be unusable (unknown, expired, revoked, used up), so a code cannot be probed."""
    org_id, secret = parse_code(code)
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        invite = await conn.fetchrow(
            "SELECT id FROM org_invites WHERE organization_id = $1::uuid AND secret_hash = $2 AND revoked_at IS NULL "
            "AND expires_at > now() AND uses < max_uses FOR UPDATE", org_id, _hash(secret))
        if invite is None:
            raise gov.NotFound("this invite is not valid")
        org = await conn.fetchrow("SELECT name FROM organizations WHERE id = $1::uuid AND t_expired IS NULL", org_id)
        if org is None:
            raise gov.NotFound("this invite is not valid")
        already = await conn.fetchval(
            "SELECT 1 FROM org_memberships WHERE organization_id = $1::uuid AND user_id = $2::uuid AND t_expired IS NULL "
            "LIMIT 1", org_id, ctx.user_id)
        if not already:
            await conn.execute("UPDATE org_invites SET uses = uses + 1 WHERE id = $1", invite["id"])
            await conn.execute(
                "INSERT INTO org_memberships (organization_id, user_id, role_id) VALUES ($1::uuid, $2::uuid, $3::uuid) "
                "ON CONFLICT DO NOTHING", org_id, ctx.user_id, MEMBER_ROLE_ID)
            await gov._audit(conn, actor_subject=ctx.subject, actor_user_id=ctx.user_id, action="org_invite.redeem",
                             object_type="org_invite", object_id=str(invite["id"]), org_id=org_id, details={})
    return {"organization_id": org_id, "name": org["name"], "already_member": bool(already)}


# ------------------------------------------------------------------ what a joined machine is told

def gateway_url() -> str:
    url = (settings.public_api_url or "").strip().rstrip("/")
    if not url.startswith("https://") and not url.startswith("http://127.0.0.1") and not url.startswith("http://localhost"):
        raise gov.Invalid("this server has no public API address configured (PUBLIC_API_URL), so it cannot tell a "
                          "developer's machine where the gateway is")
    return url


async def client_config(pool: Any, org_id: str) -> dict[str, Any]:
    """The `exec.json` pieces a developer's machine needs, from the organisation's connected keys. No credential: the
    machine authenticates to the gateway with its own kell sign-in, and the gateway holds the provider key."""
    base = gateway_url()
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        rows = await conn.fetch("SELECT provider, units FROM org_provider_keys WHERE organization_id = $1::uuid AND enabled "
                                "ORDER BY connection_id", str(org_id))
        name = await conn.fetchval("SELECT name FROM organizations WHERE id = $1::uuid", str(org_id))
    profiles: dict[str, Any] = {}
    for r in rows:
        for u in (r["units"] if isinstance(r["units"], list) else json.loads(r["units"])):
            profiles.setdefault(u["model"], {
                "model_id": u["model"], "openai_base_url": f"{base}/v1", "key_from": "stealthlab",
                "input_per_mtok": u["input_per_mtok"], "output_per_mtok": u["output_per_mtok"],
                **{k: u[k] for k in org_keys.CLIENT_FLAGS if k in u}})
    if not profiles:
        raise gov.Conflict("your admin has not connected a provider key yet, so there are no models to use")
    return {"organization_id": str(org_id), "name": name, "gateway": f"{base}/v1", "models": list(profiles),
            "profiles": profiles}


# ------------------------------------------------------------------ the admin's checklist

def recommended_policy(keys: list[Mapping[str, Any]], monthly_budget_usd: Any, per_user_daily_budget_usd: Any) -> dict[str, Any]:
    """A complete policy built from what the admin connected: exactly those providers, models and data classes, the two
    tools the gateway and MCP use, and the budgets the admin typed (there is no default budget)."""
    if not keys:
        raise gov.Conflict("connect a provider key first: a policy lists the providers and models it allows")
    providers = sorted({k["provider"] for k in keys})
    models = sorted({u["model"] for k in keys for u in k["units"]})
    classes = sorted({c for k in keys for c in k["allowed_data_classes"]})
    return {"allowed_providers": providers, "allowed_models": models, "allowed_tools": list(RECOMMENDED_TOOLS),
            "allowed_data_classes": classes, "monthly_budget_usd": monthly_budget_usd,
            "per_user_daily_budget_usd": per_user_daily_budget_usd}


async def apply_recommended_policy(pool: Any, *, actor_subject: str, actor_user_id: Optional[str],
                                   actor_roles: Collection[str], org_id: str, monthly_budget_usd: Any,
                                   per_user_daily_budget_usd: Any) -> gov.OrgPolicy:
    """Create the policy from the connected keys, or bring an existing one in line with them (keeping its kill switch)."""
    gov._require(actor_roles, gov.ADMIN_ROLES, "applying the recommended policy")
    keys = await org_keys.list_provider_keys(pool, actor_roles=actor_roles, org_id=org_id)
    changes = recommended_policy([k for k in keys if k["enabled"]], monthly_budget_usd, per_user_daily_budget_usd)
    try:
        current = await gov.load_policy(pool, org_id)
        expected: Optional[int] = current.version
    except gov.PolicyMissing:
        expected = None
    return await gov.put_policy(pool, actor_subject=actor_subject, actor_user_id=actor_user_id, actor_roles=actor_roles,
                                org_id=org_id, changes=changes, expected_version=expected)


async def onboarding_status(pool: Any, *, actor_roles: Collection[str], org_id: str) -> dict[str, Any]:
    gov._require(actor_roles, gov.ADMIN_ROLES, "viewing onboarding")
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        keys = await conn.fetchval("SELECT count(*) FROM org_provider_keys WHERE organization_id = $1::uuid AND enabled",
                                   str(org_id))
        invites = await conn.fetchval(
            "SELECT count(*) FROM org_invites WHERE organization_id = $1::uuid AND revoked_at IS NULL AND "
            "expires_at > now() AND uses < max_uses", str(org_id))
        ready = await conn.fetchval(
            "SELECT count(DISTINCT actor_subject) FROM provider_call_ledger WHERE organization_id = $1::uuid AND "
            "tool = $2 AND status = 'settled'", str(org_id), GATEWAY_TOOL)
        joined = await conn.fetchval(
            "SELECT count(DISTINCT user_id) FROM org_memberships WHERE organization_id = $1::uuid AND t_expired IS NULL",
            str(org_id))
    try:
        policy = await gov.load_policy(pool, org_id)
        policy_ok, kill = True, policy.kill_switch
    except gov.PolicyMissing:
        policy_ok, kill = False, False
    return {"organization_id": str(org_id), "provider_keys": int(keys or 0), "policy_set": policy_ok,
            "kill_switch": kill, "active_invites": int(invites or 0), "members": int(joined or 0),
            "members_ready": int(ready or 0),
            "next_step": ("connect_key" if not keys else "set_policy" if not policy_ok else "running" if ready
                          else "waiting_for_developers" if (invites or joined > 1) else "invite_developers")}
