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

Limits: email delivery is best effort (Resend, when configured; the admin always gets the one-time code), `ready` means
"made at least one settled gateway call", which a self-test satisfies but does not prove their editor works. Removing a
member expires their memberships and revokes the agents and grants they held here; their account and personal work are
untouched.
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
from app.services import org_keys, org_mail
from app.services.access import tenant_transaction
from app.services.auth_context import AuthContext

MEMBER_ROLE_ID = "10000000-0000-0000-0000-000000000003"      # db/28_identity.sql seeds the four builtin roles
ROLE_IDS = {
    "owner": "10000000-0000-0000-0000-000000000001",
    "admin": "10000000-0000-0000-0000-000000000002",
    "member": MEMBER_ROLE_ID,
    "viewer": "10000000-0000-0000-0000-000000000004",
}
INVITE_ROLES = ("owner", "admin", "member", "viewer")
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
                        org_id: str, label: Optional[str], days: int, max_uses: int,
                        role: str = "member", email: Optional[str] = None) -> dict[str, Any]:
    gov._require(actor_roles, gov.ADMIN_ROLES, "creating an invite")
    role = (role or "member").strip().lower()
    if role not in INVITE_ROLES:
        raise gov.Invalid(f"role must be one of {', '.join(INVITE_ROLES)}")
    if role == "owner" and "owner" not in actor_roles:
        raise gov.NotAuthorized("only an owner can invite another owner")
    if not isinstance(days, int) or not 1 <= days <= MAX_INVITE_DAYS:
        raise gov.Invalid(f"days must be a whole number from 1 to {MAX_INVITE_DAYS}")
    if not isinstance(max_uses, int) or not 1 <= max_uses <= MAX_INVITE_USES:
        raise gov.Invalid(f"max_uses must be a whole number from 1 to {MAX_INVITE_USES}")
    if label is not None and (not isinstance(label, str) or len(label) > 80):
        raise gov.Invalid("label must be text of at most 80 characters")
    if email is not None and (not isinstance(email, str) or len(email) > 320 or "@" not in email):
        raise gov.Invalid("email must be an address")
    secret = secrets.token_hex(12)
    code = f"sl1_{uuid.UUID(str(org_id)).hex}_{secret}"
    expires = datetime.now(timezone.utc) + timedelta(days=days)
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        row = await conn.fetchrow(
            "INSERT INTO org_invites (organization_id, secret_hash, label, created_by, expires_at, max_uses, role, email) "
            "VALUES ($1::uuid, $2, $3, $4, $5, $6, $7, $8) RETURNING id::text AS id, created_at",
            str(org_id), _hash(secret), (label or "").strip() or None, actor_subject, expires, max_uses, role,
            (email or "").strip() or None)
        org_name = await conn.fetchval(
            "SELECT name FROM organizations WHERE id = $1::uuid AND t_expired IS NULL", str(org_id)) or "your organization"
        await gov._audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id, action="org_invite.create",
                         object_type="org_invite", object_id=row["id"], org_id=org_id,
                         details={"label": label, "expires_at": expires.isoformat(), "max_uses": max_uses,
                                  "role": role, "email": (email or None)})
    # The code exists in the clear only in this response and (when an address was given) the message sent now.
    address = (email or "").strip() or None
    sent = False
    if address:
        origin = (settings.frontend_origin or "").split(",")[0].strip().rstrip("/")
        link = f"{origin}/join?code={code}" if origin else ""
        try:
            sent = await org_mail.send_invite(to=address, org_name=org_name, code=code, role=role,
                                              expires_at=expires.isoformat(), link=link)
        except Exception:  # noqa: BLE001 -- a mail bug must not fail an invite that is already valid
            sent = False
        if sent:
            try:
                async with tenant_transaction(pool, gov._scope(org_id)) as conn:
                    await conn.execute("UPDATE org_invites SET email_sent_at = now() WHERE id = $1::uuid",
                                       row["id"])
            except Exception:  # noqa: BLE001 -- the invite is already valid; a timestamp is not worth failing it
                pass
    return {"id": row["id"], "code": code, "expires_at": expires.isoformat(), "max_uses": max_uses, "label": label,
            "role": role, "email": address, "email_sent": sent,
            "email_sent_at": datetime.now(timezone.utc).isoformat() if sent else None}


async def list_invites(pool: Any, *, actor_roles: Collection[str], org_id: str) -> list[dict[str, Any]]:
    gov._require(actor_roles, gov.ADMIN_ROLES, "listing invites")
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        rows = await conn.fetch(
            "SELECT id::text AS id, label, role, email, created_by, created_at, expires_at, max_uses, uses, revoked_at, "
            "email_sent_at, "
            "(revoked_at IS NULL AND expires_at > now() AND uses < max_uses) AS active "
            "FROM org_invites WHERE organization_id = $1::uuid ORDER BY created_at DESC LIMIT 200", str(org_id))
    return [{**dict(r), "created_at": r["created_at"].isoformat(), "expires_at": r["expires_at"].isoformat(),
             "revoked_at": r["revoked_at"].isoformat() if r["revoked_at"] else None,
             "email_sent_at": r["email_sent_at"].isoformat() if r["email_sent_at"] else None} for r in rows]


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
            "SELECT id, role FROM org_invites WHERE organization_id = $1::uuid AND secret_hash = $2 AND revoked_at IS NULL "
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
            role_name = str(invite.get("role") or "member")
            role_id = ROLE_IDS.get(role_name, MEMBER_ROLE_ID)
            await conn.execute(
                "INSERT INTO org_memberships (organization_id, user_id, role_id) VALUES ($1::uuid, $2::uuid, $3::uuid) "
                "ON CONFLICT (organization_id, user_id, role_id) DO UPDATE SET t_expired = NULL",
                org_id, ctx.user_id, role_id)
            await gov._audit(conn, actor_subject=ctx.subject, actor_user_id=ctx.user_id, action="org_invite.redeem",
                             object_type="org_invite", object_id=str(invite["id"]), org_id=org_id,
                             details={"role": role_name})
    return {"organization_id": org_id, "name": org["name"], "already_member": bool(already),
            "role": str(invite.get("role") or "member")}


# ------------------------------------------------------------------ member lifecycle (admin side)

async def _member_subject(conn: Any, org_id: str, user_id: str) -> str:
    sub = await conn.fetchval(
        "SELECT u.external_subject FROM users u JOIN org_memberships m ON m.user_id = u.id "
        "WHERE m.organization_id = $1::uuid AND m.user_id = $2::uuid AND m.t_expired IS NULL LIMIT 1",
        str(org_id), str(user_id))
    if not sub:
        raise gov.NotFound("no such member")
    return str(sub)


async def _owner_count(conn: Any, org_id: str) -> int:
    return int(await conn.fetchval(
        "SELECT count(DISTINCT m.user_id) FROM org_memberships m JOIN roles r ON r.id = m.role_id "
        "WHERE m.organization_id = $1::uuid AND m.t_expired IS NULL AND r.name = 'owner'", str(org_id)) or 0)


async def _is_owner(conn: Any, org_id: str, user_id: str) -> bool:
    return bool(await conn.fetchval(
        "SELECT 1 FROM org_memberships m JOIN roles r ON r.id = m.role_id "
        "WHERE m.organization_id = $1::uuid AND m.user_id = $2::uuid AND m.t_expired IS NULL AND r.name = 'owner' LIMIT 1",
        str(org_id), str(user_id)))


async def _is_admin(conn: Any, org_id: str, user_id: str) -> bool:
    """Owner or admin. Used only to decide who may change or remove another administrator."""
    return bool(await conn.fetchval(
        "SELECT 1 FROM org_memberships m JOIN roles r ON r.id = m.role_id "
        "WHERE m.organization_id = $1::uuid AND m.user_id = $2::uuid AND m.t_expired IS NULL "
        "AND r.name IN ('owner', 'admin') LIMIT 1", str(org_id), str(user_id)))


async def set_member_role(pool: Any, *, actor_subject: str, actor_user_id: Optional[str], actor_roles: Collection[str],
                          org_id: str, user_id: str, role: str) -> dict[str, Any]:
    """Give one member a different role. Expires the old membership and writes the new one.

    An owner may change anyone. An admin may change members and viewers, and may step down from admin themselves;
    an owner's or another admin's role is an owner's decision. The last owner cannot be demoted, and only an owner
    can create another owner.
    """
    gov._require(actor_roles, gov.ADMIN_ROLES, "changing a member's role")
    role = (role or "").strip().lower()
    if role not in ROLE_IDS:
        raise gov.Invalid(f"role must be one of {', '.join(ROLE_IDS)}")
    if role == "owner" and "owner" not in actor_roles:
        raise gov.NotAuthorized("only an owner can make another owner")
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        sub = await _member_subject(conn, org_id, user_id)
        was_owner = await _is_owner(conn, org_id, user_id)
        was_admin = await _is_admin(conn, org_id, user_id)
        same_person = actor_user_id is not None and str(user_id) == str(actor_user_id)
        if (was_owner or was_admin) and "owner" not in actor_roles and not same_person:
            raise gov.NotAuthorized("only an owner can change an owner's or another admin's role")
        if was_owner and role != "owner" and await _owner_count(conn, org_id) <= 1:
            raise gov.Conflict("an organization needs at least one owner")
        await conn.execute(
            "UPDATE org_memberships SET t_expired = now() WHERE organization_id = $1::uuid AND user_id = $2::uuid "
            "AND t_expired IS NULL", str(org_id), str(user_id))
        await conn.execute(
            "INSERT INTO org_memberships (organization_id, user_id, role_id, granted_by) VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid) "
            "ON CONFLICT (organization_id, user_id, role_id) DO UPDATE SET t_expired = NULL, granted_by = EXCLUDED.granted_by",
            str(org_id), str(user_id), ROLE_IDS[role], actor_user_id)
        await gov._audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id, action="org_member.role",
                         object_type="org_member", object_id=str(user_id), org_id=org_id,
                         details={"role": role, "was_owner": was_owner, "was_admin": was_admin})
    return {"user_id": str(user_id), "subject": sub, "role": role}


async def remove_member(pool: Any, *, actor_subject: str, actor_user_id: Optional[str], actor_roles: Collection[str],
                        org_id: str, user_id: str) -> dict[str, Any]:
    """Take one person out of the organization: their memberships expire, and the relay agents they owned are revoked.

    The last owner cannot be removed; only an owner can remove an owner or another admin. A person may remove
    themselves (unless they are the last owner). The person-owned tables are closed by RLS, so their rows are revoked
    and their grants deleted BEFORE the membership expires: after expiry the tenant policy can no longer see them.
    Their account and personal work are untouched.
    """
    gov._require(actor_roles, gov.ADMIN_ROLES, "removing a member")
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        sub = await _member_subject(conn, org_id, user_id)
        was_owner = await _is_owner(conn, org_id, user_id)
        was_admin = await _is_admin(conn, org_id, user_id)
        same_person = actor_user_id is not None and str(user_id) == str(actor_user_id)
        if was_owner:
            if "owner" not in actor_roles:
                raise gov.NotAuthorized("only an owner can remove an owner")
            if await _owner_count(conn, org_id) <= 1:
                raise gov.Conflict("an organization needs at least one owner")
        elif was_admin and "owner" not in actor_roles and not same_person:
            raise gov.NotAuthorized("only an owner can remove another admin")
        revoked = 0
        try:
            if await conn.fetchval("SELECT to_regclass('public.delegate_agents') IS NOT NULL"):
                done = await conn.execute(
                    "UPDATE delegate_agents SET revoked_at = now() WHERE owner_sub = $1 AND revoked_at IS NULL", sub)
                revoked = int(str(done).rsplit(" ", 1)[-1]) if done else 0
        except Exception:  # noqa: BLE001 -- removal must not fail because an optional table is missing
            revoked = 0
        # A person who is no longer in the organization must not keep a grant to call someone's desktop or agent,
        # nor let someone keep calling theirs. The tables are optional (older deployments); skip when absent.
        for table, sql in (
            ("desktop_grants", "DELETE FROM desktop_grants WHERE owner_id = $1 OR grantee_id = $1"),
            ("agent_links", "DELETE FROM agent_links WHERE owner_sub = $1 OR peer_sub = $1"),
        ):
            try:
                if await conn.fetchval(f"SELECT to_regclass('public.{table}') IS NOT NULL"):
                    await conn.execute(sql, sub)
            except Exception:  # noqa: BLE001 -- same reason as above
                pass
        status = await conn.execute(
            "UPDATE org_memberships SET t_expired = now() WHERE organization_id = $1::uuid AND user_id = $2::uuid "
            "AND t_expired IS NULL", str(org_id), str(user_id))
        await gov._audit(conn, actor_subject=actor_subject, actor_user_id=actor_user_id, action="org_member.remove",
                         object_type="org_member", object_id=str(user_id), org_id=org_id,
                         details={"was_owner": was_owner, "was_admin": was_admin, "agents_revoked": revoked})
    return {"user_id": str(user_id), "subject": sub, "removed": status.endswith("1"), "agents_revoked": revoked}


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
        pending = await conn.fetchval(
            "SELECT count(*) FROM tool_approvals a JOIN users u ON u.external_subject = a.owner_id "
            "JOIN org_memberships m ON m.user_id = u.id AND m.organization_id = $1::uuid AND m.t_expired IS NULL "
            "WHERE a.status = 'pending' AND a.expires_at > now()", str(org_id))
    try:
        policy = await gov.load_policy(pool, org_id)
        policy_ok, kill = True, policy.kill_switch
    except gov.PolicyMissing:
        policy_ok, kill = False, False
    return {"organization_id": str(org_id), "provider_keys": int(keys or 0), "policy_set": policy_ok,
            "kill_switch": kill, "active_invites": int(invites or 0), "members": int(joined or 0),
            "members_ready": int(ready or 0), "pending_approvals": int(pending or 0),
            "next_step": ("connect_key" if not keys else "set_policy" if not policy_ok else "running" if ready
                          else "waiting_for_developers" if (invites or joined > 1) else "invite_developers")}
