"""
Phase 1 P0 (launch compliance spec V1): the ONE centralized audit-event
writer. Every security-sensitive transition — publication, withdrawal,
export, authorization changes, repository access grants — records here.
No feature builds its own ad-hoc audit log.

FAIL-CLOSED POLICY: record_audit_event RAISES on write failure. These
rows are the durable evidence that a security transition was authorized
and performed; silently swallowing a failed write would produce exactly
the "looked implemented and was decorative" posture access.py's
docstring warns about. Callers that genuinely cannot abort the operation
post-effect must not use this module as a best-effort logger — if an
operation's ordering makes the effect precede the audit write, the
caller records the attempt BEFORE the effect where possible.

Append-only: this module never UPDATEs or DELETEs. Historical audit
rows are immutable by design (spec principle 17 — evidence is not
rewritten).
"""
from __future__ import annotations

from typing import Any, Optional

_AUDIT_INSERT = (
    "INSERT INTO audit_events "
    "(actor_subject, actor_user_id, action, object_type, object_id, "
    "tenant_id, details) "
    "VALUES ($1, $2::uuid, $3, $4, $5, $6::uuid, $7::jsonb) RETURNING id"
)


class AuditWriteFailed(RuntimeError):
    """The audit row could not be persisted. Fail closed."""


async def record_audit_event(
    pool: Any,
    *,
    actor_subject: str,
    action: str,
    object_type: str,
    object_id: str,
    actor_user_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> str:
    """Append one audit event; returns the new event id.

    `actor_subject` is the validated token subject (never a
    client-asserted field). `details` is free-form structured context
    (what changed, why, from what state) — it must not carry secrets.
    """
    import json as _json

    try:
        row = await pool.fetchrow(
            _AUDIT_INSERT,
            actor_subject,
            actor_user_id,
            action,
            object_type,
            object_id,
            tenant_id,
            _json.dumps(details or {}),
        )
    except Exception as exc:  # noqa: BLE001 — fail closed on any write failure
        raise AuditWriteFailed(
            f"audit write failed for {action} on {object_type}/{object_id}: {exc}"
        ) from exc
    if row is None:
        raise AuditWriteFailed(
            f"audit write returned no row for {action} on {object_type}/{object_id}"
        )
    return str(row["id"])


async def export_tenant_events(pool: Any, *, tenant_id: str, since: Any, until: Any, after_id: int,
                               limit: int) -> dict:
    """One organisation's audit events, oldest first, a page at a time (`after_id` is the cursor), plus the result of
    verifying that organisation's hash chain (db/136). Read-only. audit_events has no row-level security, so the tenant
    predicate is explicit, and it lives here with the rest of the audit code."""
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            "SELECT id, t_created, actor_subject, actor_user_id::text AS actor_user_id, action, object_type, "
            "object_id, tenant_id::text AS tenant_id, details, prev_hash, row_hash FROM audit_events "
            "WHERE tenant_id = $1::uuid AND id > $2 AND t_created >= $3 AND t_created < $4 ORDER BY id LIMIT $5",
            str(tenant_id), after_id, since, until, limit)
        broken = await conn.fetchval("SELECT sl_audit_chain_check($1::uuid)", str(tenant_id))
    events = [dict(r) for r in rows]
    return {"events": events, "next_after_id": events[-1]["id"] if len(events) == limit else None,
            "chain_intact": broken is None, "first_broken_id": broken}


async def record_security_event(
    pool: Any,
    *,
    actor_subject: Optional[str],
    action: str,
    object_type: str,
    object_id: str,
    tenant_id: Optional[str] = None,
    details: Optional[dict] = None,
) -> None:
    """BEST-EFFORT twin of record_audit_event for events whose failure must not
    change the outcome of the request that produced them: access-denied,
    legacy-admin-key use, credential rejection. It never raises (a broken audit
    table must not turn a 403 into a 500 or gate a security decision on audit
    availability) and logs a warning instead. State-changing security
    transitions (publication, deletion, role/credential changes) keep using the
    fail-closed record_audit_event.

    Callers must never put tokens, keys or raw JWTs in `details`."""
    import logging

    if pool is None:
        return
    try:
        await record_audit_event(
            pool, actor_subject=actor_subject or "anonymous", action=action,
            object_type=object_type, object_id=object_id, tenant_id=tenant_id, details=details,
        )
    except Exception as exc:  # noqa: BLE001
        logging.getLogger(__name__).warning("security audit write failed for %s: %s", action, type(exc).__name__)
