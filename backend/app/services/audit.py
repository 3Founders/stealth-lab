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
