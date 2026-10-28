"""Every person gets a hidden personal organization (a team of one), so a person outside any company can still be governed,
metered and billed by the same machinery as a team: policy, ledger, kill switch, prepaid wallet.

Created the first time the server sees a signed-in person who has no organization yet (or none that is personal), owned by
them, with a policy whose limiter is the prepaid wallet (wallet_required) rather than a small flat cap: the money-in minus
spend check in org_governance.reserve_call stops spending at zero. Idempotent (the slug is derived from the user id), never
raises (a failure must not turn a valid sign-in into a 401), and off unless KELL_PERSONAL_ORGS=on, so a deployment that
does not sell credits is unchanged.

What it does NOT do: it grants no credit. A new person's balance is zero, so every call is refused with "buy credits" until
a payment is credited (or a signup credit is added by hand through wallet.credit_payment).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional

from app.services.access import TenantScope, tenant_transaction

log = logging.getLogger(__name__)

OWNER_ROLE_ID = "10000000-0000-0000-0000-000000000001"      # db/28_identity.sql
ENV = "KELL_PERSONAL_ORGS"
# what a personal organization may call: the public-data pooled models, through the two tools the apps use. Private data
# classes join this list only on a zero-retention provider, deliberately and per provider, not here.
PROVIDERS = ("zai", "general_compute")
MODELS = ("glm-5.3-flash", "gemma-4-31b-it")
TOOLS = ("call_model", "chat_completions")
DATA_CLASSES = ("PUBLIC_SOURCE", "PUBLIC_DERIVED", "GLOBAL_PROCEDURE")
# the wallet is the limiter; these only stop a runaway bug, not a customer
MONTHLY_USD = "5000.00"
PER_USER_DAILY_USD = "1000.00"


def enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    return ((os.environ if env is None else env).get(ENV) or "").strip().lower() in ("on", "1", "true", "yes")


def slug_for(user_id: str) -> str:
    return "personal-" + str(user_id).replace("-", "")[:20].lower()


async def ensure_personal_org(pool: Any, *, user_id: str, org_ids: tuple[str, ...] = (),
                              env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The id of the person's personal organization if one was just created, else None (already has it, disabled, or failed)."""
    if not enabled(env) or not user_id:
        return None
    slug = slug_for(user_id)
    try:
        from app.services import org_governance as gov
        # a plain connection for the lookup/creation of the organization row (no tenant exists yet); the policy and the
        # membership are written inside the new organization's own scope
        async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
            existing = await conn.fetchval("SELECT id::text FROM organizations WHERE slug = $1 AND t_expired IS NULL", slug)
            if existing:
                return None if existing in {str(o).lower() for o in org_ids} else await _join(pool, gov, existing, user_id)
            org_id = await conn.fetchval(
                "INSERT INTO organizations (name, slug) VALUES ('Personal', $1) ON CONFLICT (slug) DO NOTHING RETURNING id::text",
                slug)
            if not org_id:
                return None
        return await _setup(pool, gov, org_id, user_id)
    except Exception:  # noqa: BLE001 - see docstring
        log.warning("personal organization setup failed for user %s", user_id, exc_info=True)
        return None


async def _join(pool: Any, gov: Any, org_id: str, user_id: str) -> Optional[str]:
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        added = await conn.fetchval(
            "INSERT INTO org_memberships (organization_id, user_id, role_id) VALUES ($1::uuid, $2::uuid, $3::uuid) "
            "ON CONFLICT DO NOTHING RETURNING 1", org_id, user_id, OWNER_ROLE_ID)
    return org_id if added else None


async def _setup(pool: Any, gov: Any, org_id: str, user_id: str) -> str:
    async with tenant_transaction(pool, gov._scope(org_id)) as conn:
        await conn.execute(
            "INSERT INTO org_policies (organization_id, allowed_providers, allowed_models, allowed_tools, "
            "allowed_data_classes, monthly_budget_usd, per_user_daily_budget_usd, wallet_required, updated_by) "
            "VALUES ($1::uuid, $2, $3, $4, $5, $6::numeric, $7::numeric, TRUE, 'personal_org') ON CONFLICT DO NOTHING",
            org_id, list(PROVIDERS), list(MODELS), list(TOOLS), list(DATA_CLASSES), MONTHLY_USD, PER_USER_DAILY_USD)
        await conn.execute(
            "INSERT INTO org_memberships (organization_id, user_id, role_id) VALUES ($1::uuid, $2::uuid, $3::uuid) "
            "ON CONFLICT DO NOTHING", org_id, user_id, OWNER_ROLE_ID)
    return org_id


async def resolve_orgs(pool: Any, *, user_id: str, org_ids: tuple[str, ...],
                       env: Optional[Mapping[str, str]] = None) -> tuple[str, ...]:
    """The organizations a signed-in person acts under. With personal organizations OFF this is `org_ids` unchanged (the
    pilot enrolment, if configured, is the caller's business). With them ON: the person's personal organization is ensured
    and added, and the shared pilot organization is dropped, so exactly one organization pays by default and a person
    without credit is refused with 'buy credits' instead of drawing on the free pilot."""
    if not enabled(env) or not user_id:
        return tuple(org_ids)
    created = await ensure_personal_org(pool, user_id=user_id, org_ids=org_ids, env=env)
    personal = created or await _existing_personal(pool, user_id)
    from app.services import pilot_enrol as pilot
    pilot_org = pilot.pilot_org_id(env)
    kept = [o for o in org_ids if not (pilot_org and str(o).lower() == pilot_org)]
    if personal and personal.lower() not in {str(o).lower() for o in kept}:
        kept.append(personal)
    return tuple(kept)


async def _existing_personal(pool: Any, user_id: str) -> Optional[str]:
    try:
        async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:
            return await conn.fetchval("SELECT id::text FROM organizations WHERE slug = $1 AND t_expired IS NULL",
                                       slug_for(user_id))
    except Exception:  # noqa: BLE001
        log.warning("personal organization lookup failed for user %s", user_id, exc_info=True)
        return None
