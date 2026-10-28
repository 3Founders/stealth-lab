"""Pilot enrolment: everyone who signs in joins one capped pilot organization.

Why this exists. On a shared deployment every provider call is charged to ONE organization and checked against its
policy and budget (providers/service.py, governing_org). A person who has just signed in belongs to no organization, so
every `call_model` would be refused with "billed to an organization and you belong to none". For the pilot, where kell's
own GLM and General Compute credits are offered to every signed-in user, this adds the person to the pilot organization
the first time the server sees their token.

What it does NOT do. It grants no policy of its own: what the member may call, which data classes may leave, and the
per-user daily and monthly dollar caps all live in the organization's `org_policies` row (scripts/setup_pilot.py writes
it). Remove the environment variable and nobody is enrolled any more; existing memberships stay until an admin ends them.

Off by default. With KELL_PILOT_ORG_ID unset this is a no-op, so a deployment that does not run a pilot is unchanged.
It joins people as `member` only (never admin or owner), writes the standard `org_memberships` row the invite flow
writes, and is idempotent.
"""
from __future__ import annotations

import logging
import os
import re
from typing import Any, Mapping, Optional

from app.services.access import tenant_transaction
from app.services.org_onboarding import MEMBER_ROLE_ID

log = logging.getLogger(__name__)

PILOT_ORG_ENV = "KELL_PILOT_ORG_ID"
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")


def pilot_org_id(env: Optional[Mapping[str, str]] = None) -> Optional[str]:
    """The pilot organization's id, or None when no pilot is configured (or the value is not a UUID)."""
    raw = ((os.environ if env is None else env).get(PILOT_ORG_ENV) or "").strip()
    return raw.lower() if raw and _UUID.fullmatch(raw) else None


async def ensure_pilot_member(pool: Any, *, user_id: str, org_ids: tuple[str, ...] = (),
                              env: Optional[Mapping[str, str]] = None) -> bool:
    """Make `user_id` a member of the pilot organization. True when a membership was added just now.

    `org_ids` are the caller's active memberships already resolved by the caller; if the pilot organization is among them
    nothing is written. Never raises: enrolment failing must not turn a valid sign-in into a 401, so a failure is logged
    and the person simply stays outside the pilot (their calls are then refused with the usual explanation)."""
    org = pilot_org_id(env)
    if org is None or not user_id or org in {str(o).lower() for o in org_ids}:
        return False
    try:
        from app.services import org_governance as gov
        async with tenant_transaction(pool, gov._scope(org)) as conn:
            exists = await conn.fetchval("SELECT 1 FROM organizations WHERE id = $1::uuid AND t_expired IS NULL", org)
            if not exists:
                log.warning("pilot enrolment: organization %s does not exist or is closed", org)
                return False
            added = await conn.fetchval(
                "INSERT INTO org_memberships (organization_id, user_id, role_id) VALUES ($1::uuid, $2::uuid, $3::uuid) "
                "ON CONFLICT DO NOTHING RETURNING 1", org, user_id, MEMBER_ROLE_ID)
        return bool(added)
    except Exception:  # noqa: BLE001 - see docstring: enrolment must never break a sign-in
        log.warning("pilot enrolment failed for user %s", user_id, exc_info=True)
        return False
