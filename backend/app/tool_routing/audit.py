"""Append-only, tamper-evident tool-call audit (db/190, docs/goal_router.md P1-07).

Every decision -- allow, deny or approve -- becomes one receipt. The receipt stores hashes, never
arguments or results. Rows form a hash chain: each row's `prev_hash` is the previous row's `hash`,
and `signature` is HMAC-SHA256 over the same canonical payload. `verify_chain()` walks it.

Writes take a transaction-scoped advisory lock, so concurrent callers cannot fork the chain. A
failed write is not swallowed here: the gate decides whether a missing receipt should deny the
call (it should, for R2+).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timezone
from typing import Any, Optional

from app.tool_routing import signing


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


def _payload(row: Any) -> dict[str, Any]:
    """The signed bytes of a stored row, rebuilt exactly as `record()` built them."""
    return {
        "receipt_id": str(row["receipt_id"]),
        "at": _iso(row["at"]),
        "owner_id": row["owner_id"],
        "agent": str(row["agent"]),
        "task_id": row["task_id"],
        "goal_id": str(row["goal_id"]) if row["goal_id"] else None,
        "server": str(row["server"]),
        "tool": str(row["tool"]),
        "args_hash": str(row["args_hash"]),
        "decision": str(row["decision"]),
        "reason": str(row["reason"]),
        "risk": str(row["risk"]),
        "data_class": str(row["data_class"]),
        "cert_id": row["cert_id"],
        "approval_id": row["approval_id"],
        "latency_ms": row["latency_ms"],
        "cost_usd": float(row["cost_usd"]) if row["cost_usd"] is not None else None,
        "result_status": str(row["result_status"]),
        "result_hash": row["result_hash"],
        "prev_hash": str(row["prev_hash"]),
    }


async def record(
    pool: Any,
    call: Any,
    decision: Any,
    *,
    status: str = "",
    latency_ms: Optional[int] = None,
    cost_usd: Optional[float] = None,
    result_hash: Optional[str] = None,
    approval_id: Optional[str] = None,
) -> str:
    """Write one receipt and return its id. The chain is serialized by an advisory lock."""
    receipt_id = "rc_" + secrets.token_urlsafe(18)
    at = datetime.now(timezone.utc)
    payload: dict[str, Any] = {
        "receipt_id": receipt_id,
        "at": _iso(at),
        "owner_id": _field(call, "owner_id"),
        "agent": str(_field(call, "agent", "kell") or "kell"),
        "task_id": _field(call, "task_id"),
        "goal_id": str(_field(call, "goal_id")) if _field(call, "goal_id") else None,
        "server": str(_field(call, "server", "") or ""),
        "tool": str(_field(call, "tool", "") or ""),
        "args_hash": signing.sha256_json(_field(call, "arguments", {}) or {}),
        "decision": str(_field(decision, "effect", "deny") or "deny"),
        "reason": str(_field(decision, "reason", "") or ""),
        "risk": str(_field(decision, "risk", "R1") or "R1"),
        "data_class": str(_field(call, "data_class", "PUBLIC_SOURCE") or "PUBLIC_SOURCE"),
        "cert_id": _field(call, "cert_id") or _field(decision, "cert_id"),
        "approval_id": approval_id or _field(decision, "approval_id"),
        "latency_ms": int(latency_ms) if latency_ms is not None else None,
        "cost_usd": float(cost_usd) if cost_usd is not None else None,
        "result_status": str(status or ""),
        "result_hash": result_hash,
        "prev_hash": signing.GENESIS_HASH,
    }

    async with pool.acquire() as conn:
        async with conn.transaction():
            await conn.execute("SELECT pg_advisory_xact_lock(hashtext('tool_call_audit'))")
            prev = await conn.fetchval(
                "SELECT hash FROM tool_call_audit ORDER BY id DESC LIMIT 1")
            payload["prev_hash"] = str(prev or signing.GENESIS_HASH)
            digest = hashlib.sha256(signing.canonical(payload)).hexdigest()
            signature = signing.sign(payload)
            payload["hash"] = digest
            payload["signature"] = signature
            await conn.execute(
                """
                INSERT INTO tool_call_audit
                    (at, receipt_id, owner_id, agent, task_id, goal_id, server, tool, args_hash,
                     decision, reason, risk, data_class, cert_id, approval_id, latency_ms,
                     cost_usd, result_status, result_hash, prev_hash, hash, signature)
                VALUES ($1, $2, $3, $4, $5, $6::uuid, $7, $8, $9, $10, $11, $12, $13, $14,
                        $15, $16, $17, $18, $19, $20, $21, $22)
                """,
                at, receipt_id, payload["owner_id"], payload["agent"], payload["task_id"],
                payload["goal_id"], payload["server"], payload["tool"], payload["args_hash"],
                payload["decision"], payload["reason"], payload["risk"], payload["data_class"],
                payload["cert_id"], payload["approval_id"], payload["latency_ms"],
                payload["cost_usd"], payload["result_status"], payload["result_hash"],
                payload["prev_hash"], payload["hash"], payload["signature"],
            )
    return receipt_id


async def verify_chain(pool: Any, since: Optional[datetime] = None) -> tuple[bool, str, int]:
    """Walk the chain from `since` (or the beginning). -> (ok, message, receipts checked).

    A window starts mid-chain, so its first row's `prev_hash` points at the last row before the
    window, not at genesis. Anchor to that row instead; only a window that starts at the first row
    (or the whole chain) starts at `GENESIS_HASH`. This is still a real link check: a break at the
    boundary fails here.
    """
    rows = await pool.fetch(
        """SELECT * FROM tool_call_audit
           WHERE ($1::timestamptz IS NULL OR at >= $1)
           ORDER BY id ASC""", since)
    prev = signing.GENESIS_HASH
    if since is not None and rows:
        anchor = await pool.fetchval(
            "SELECT hash FROM tool_call_audit WHERE id < $1 ORDER BY id DESC LIMIT 1", rows[0]["id"])
        prev = str(anchor or signing.GENESIS_HASH)
    for row in rows:
        if str(row["prev_hash"]) != prev:
            return False, f"broken link at receipt {row['receipt_id']}", 0
        payload = _payload(row)
        digest = hashlib.sha256(signing.canonical(payload)).hexdigest()
        if digest != str(row["hash"]):
            return False, f"hash mismatch at receipt {row['receipt_id']}", 0
        if not signing.verify(payload, row["signature"]):
            return False, f"bad signature at receipt {row['receipt_id']}", 0
        prev = str(row["hash"])
    return True, "ok", len(rows)
