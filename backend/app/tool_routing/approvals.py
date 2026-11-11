"""Human approvals for tool calls the policy marks `approve` (db/196, docs/goal_router.md P1-11).

An approval is scoped to one exact call shape: owner, task, server, tool, argument hash and certificate.
It is short-lived, single-use, and signed by the decision the gate already made. `request()` returns a
pending id; the UI approves or denies it; the gate's next identical call finds it, consumes it once, and
runs. A changed argument or a different certificate is a different approval.

Nothing here stores arguments, only their hash, so the card can show what will run without keeping the
payload.
"""
from __future__ import annotations

import secrets
from typing import Any, Optional

from app.tool_routing import signing

STATUSES = ("pending", "approved", "denied", "expired", "used")


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _row(row: Any) -> dict[str, Any]:
    return dict(row)


async def request(pool: Any, call: Any, *, risk: str, data_class: str, ttl_s: int = 900) -> str:
    """Create or reuse a pending approval for this exact call shape; -> approval id."""
    owner = str(_field(call, "owner_id", "") or "")
    server = str(_field(call, "server", "") or "")
    tool = str(_field(call, "tool", "") or "")
    if not owner or not server or not tool:
        raise ValueError("an approval needs an owner, a server and a tool")
    task_id = _field(call, "task_id")
    cert_id = _field(call, "cert_id")
    args_hash = signing.sha256_json(_field(call, "arguments", {}) or {})

    row = await pool.fetchrow(
        """SELECT id FROM tool_approvals
           WHERE owner_id = $1 AND task_id IS NOT DISTINCT FROM $2
             AND server = $3 AND tool = $4 AND args_hash = $5
             AND cert_id IS NOT DISTINCT FROM $6
             AND status = 'pending' AND expires_at > now()
           ORDER BY requested_at DESC LIMIT 1""",
        owner, task_id, server, tool, args_hash, cert_id)
    if row is not None:
        return str(row["id"])

    approval_id = "ap_" + secrets.token_urlsafe(18)
    await pool.execute(
        """INSERT INTO tool_approvals
               (id, owner_id, task_id, goal_id, server, tool, args_hash, risk, data_class,
                cert_id, status, expires_at)
           VALUES ($1, $2, $3, $4::uuid, $5, $6, $7, $8, $9, $10, 'pending',
                   now() + make_interval(secs => $11))""",
        approval_id, owner, task_id, _field(call, "goal_id"), server, tool, args_hash,
        str(risk or "R2"), str(data_class or "PUBLIC_SOURCE"), cert_id, max(1, int(ttl_s)))
    return approval_id


async def find(pool: Any, call: Any) -> Optional[dict[str, Any]]:
    """An approved, unexpired, unused approval for this exact call shape, or None."""
    owner = str(_field(call, "owner_id", "") or "")
    server = str(_field(call, "server", "") or "")
    tool = str(_field(call, "tool", "") or "")
    if not owner or not server or not tool:
        return None
    row = await pool.fetchrow(
        """SELECT * FROM tool_approvals
           WHERE owner_id = $1 AND task_id IS NOT DISTINCT FROM $2
             AND server = $3 AND tool = $4 AND args_hash = $5
             AND cert_id IS NOT DISTINCT FROM $6
             AND status = 'approved' AND used_at IS NULL AND expires_at > now()
           ORDER BY decided_at DESC LIMIT 1""",
        owner, _field(call, "task_id"), server, tool,
        signing.sha256_json(_field(call, "arguments", {}) or {}), _field(call, "cert_id"))
    return _row(row) if row is not None else None


async def consume(pool: Any, approval_id: str) -> bool:
    """Spend an approval exactly once; False if it was used, denied, expired or never approved."""
    status = await pool.execute(
        """UPDATE tool_approvals
           SET status = 'used', used_at = now()
           WHERE id = $1 AND status = 'approved' AND used_at IS NULL AND expires_at > now()""",
        str(approval_id or ""))
    return status.endswith(" 1")


async def approve(pool: Any, approval_id: str, owner_id: str, *, decided_by: str = "user",
                  ttl_s: int = 900) -> bool:
    status = await pool.execute(
        """UPDATE tool_approvals
           SET status = 'approved', decided_at = now(), decided_by = $3,
               expires_at = now() + make_interval(secs => $4)
           WHERE id = $1 AND owner_id = $2 AND status = 'pending' AND expires_at > now()""",
        str(approval_id or ""), str(owner_id or ""), str(decided_by or "user"), max(1, int(ttl_s)))
    return status.endswith(" 1")


async def deny(pool: Any, approval_id: str, owner_id: str, *, decided_by: str = "user",
               note: str = "") -> bool:
    status = await pool.execute(
        """UPDATE tool_approvals
           SET status = 'denied', decided_at = now(), decided_by = $3, note = $4
           WHERE id = $1 AND owner_id = $2 AND status = 'pending' AND expires_at > now()""",
        str(approval_id or ""), str(owner_id or ""), str(decided_by or "user"), str(note or "")[:500])
    return status.endswith(" 1")


async def get(pool: Any, approval_id: str, owner_id: Optional[str] = None) -> Optional[dict[str, Any]]:
    row = await pool.fetchrow(
        """SELECT * FROM tool_approvals
           WHERE id = $1 AND ($2::text IS NULL OR owner_id = $2)""",
        str(approval_id or ""), owner_id)
    return _row(row) if row is not None else None


async def list_pending(pool: Any, owner_id: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        """SELECT * FROM tool_approvals
           WHERE owner_id = $1 AND status = 'pending' AND expires_at > now()
           ORDER BY requested_at DESC LIMIT $2""",
        str(owner_id or ""), max(1, min(int(limit), 100)))
    return [_row(r) for r in rows]
