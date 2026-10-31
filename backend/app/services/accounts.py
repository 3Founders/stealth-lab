"""A person's own wallet account: no organization row, no membership, nothing hidden.

A wallet, a spending policy and the provider-call ledger are keyed by an id that was an organization id. Migration 164
lets that id be a USER id as well, so a person who belongs to no company is governed, metered and billed by the same
machinery as a team: a policy row whose limiter is the prepaid wallet (wallet_required), the ledger, the kill switch.
The account id of a person IS their user id. Teams (a Pilot bought for several people) are real organizations created at
purchase (services/plans.py), visible and owned by the buyer.

Created the first time the server sees a signed-in person (idempotent, never raises: a failure must not turn a valid
sign-in into a 401), and off unless KELL_WALLETS=on (KELL_PERSONAL_ORGS=on is accepted for the same effect, for a
deployment that set the old name), so a deployment that does not sell credits is unchanged.

What it does NOT do: it grants no credit. A new person's balance is zero, so every call is refused with "buy credits"
until a payment is credited (or usage is included by a plan, services/plans.py).
"""
from __future__ import annotations

import logging
import os
from typing import Any, Mapping, Optional

from app.services.access import TenantScope, tenant_transaction

log = logging.getLogger(__name__)

ENV = "KELL_WALLETS"
LEGACY_ENV = "KELL_PERSONAL_ORGS"
# what an individual account may call: the public-data pooled models, through the two tools the apps use. Private data
# classes join this list only on a zero-retention provider, deliberately and per provider, not here.
PROVIDERS = ("zai", "general_compute")
MODELS = ("glm-5.3-flash", "gemma-4-31b-it")
TOOLS = ("call_model", "chat_completions")
DATA_CLASSES = ("PUBLIC_SOURCE", "PUBLIC_DERIVED", "GLOBAL_PROCEDURE")
# the wallet is the limiter; these only stop a runaway bug, not a customer
MONTHLY_USD = "5000.00"
PER_USER_DAILY_USD = "1000.00"


def enabled(env: Optional[Mapping[str, str]] = None) -> bool:
    e = os.environ if env is None else env
    return any((e.get(name) or "").strip().lower() in ("on", "1", "true", "yes") for name in (ENV, LEGACY_ENV))


async def ensure_account(pool: Any, *, user_id: str, env: Optional[Mapping[str, str]] = None) -> bool:
    """Make sure the person's own account has its spending policy. True when it exists afterwards, False when wallets are
    off, the id is missing, or the write failed (logged; the caller carries on without it)."""
    if not enabled(env) or not user_id:
        return False
    try:
        async with tenant_transaction(pool, TenantScope.for_tenant(str(user_id))) as conn:
            await conn.execute(
                "INSERT INTO org_policies (organization_id, allowed_providers, allowed_models, allowed_tools, "
                "allowed_data_classes, monthly_budget_usd, per_user_daily_budget_usd, wallet_required, updated_by) "
                "VALUES ($1::uuid, $2, $3, $4, $5, $6::numeric, $7::numeric, TRUE, 'wallet_account') ON CONFLICT DO NOTHING",
                str(user_id), list(PROVIDERS), list(MODELS), list(TOOLS), list(DATA_CLASSES), MONTHLY_USD,
                PER_USER_DAILY_USD)
        return True
    except Exception:  # noqa: BLE001 - see docstring
        log.warning("wallet account setup failed for user %s", user_id, exc_info=True)
        return False


async def resolve_orgs(pool: Any, *, user_id: str, org_ids: tuple[str, ...],
                       env: Optional[Mapping[str, str]] = None) -> tuple[str, ...]:
    """The accounts a signed-in person acts under. With wallets OFF this is `org_ids` unchanged (the pilot enrolment, if
    configured, is the caller's business). With them ON: the person's own account is ensured and added, and the shared
    pilot organization is dropped, so a person without credit is refused with 'buy credits' instead of drawing on the
    free pilot. Teams the person belongs to stay in the list."""
    if not enabled(env) or not user_id:
        return tuple(org_ids)
    from app.services import pilot_enrol as pilot
    pilot_org = pilot.pilot_org_id(env)
    kept = [o for o in org_ids if not (pilot_org and str(o).lower() == pilot_org)]
    if await ensure_account(pool, user_id=user_id, env=env) and str(user_id).lower() not in {str(o).lower() for o in kept}:
        kept.append(str(user_id).lower())
    return tuple(kept)
