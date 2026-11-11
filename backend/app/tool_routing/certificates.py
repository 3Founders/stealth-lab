"""Task-scoped intent certificates (db/189, docs/goal_router.md P1-06).

A certificate is a short-lived, signed statement of what one task may do: which servers and
tools, which arguments, which data class, which risk ceiling, how many calls, and until when.
It can only narrow `tool_policies`; it never widens them. The model cannot mint one -- only
`issue()` from a trusted caller can.

`verify()` is deterministic and fail-closed. It checks, in order: the row exists, it is not
revoked, the signature matches the stored claims, it has not expired, and the call is inside the
allowed owner/task/goal/server/tool/data-class/argument set. The first failure is the reason code.
"""
from __future__ import annotations

import json
import re
import secrets
from datetime import datetime, timedelta, timezone
from typing import Any, Iterable, Optional

from app.tool_routing import signing

RISKS = ("R0", "R1", "R2", "R3", "R4")


class CertificateRefused(Exception):
    """Issuing a certificate was refused; the message says why."""


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def _claims(raw: Any) -> dict[str, Any]:
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except ValueError:
            return {}
    return raw if isinstance(raw, dict) else {}


def _iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


async def issue(
    pool: Any,
    *,
    task_id: str,
    owner_id: str,
    goal_id: Optional[str] = None,
    agent: str = "kell",
    allowed_servers: Iterable[str] = (),
    allowed_tools: Iterable[str] = (),
    argument_constraints: Optional[dict[str, Any]] = None,
    data_class: str = "PUBLIC_SOURCE",
    risk_ceiling: str = "R0",
    egress_allow: Iterable[str] = (),
    max_calls: int = 20,
    ttl_s: int = 3600,
    issued_by: str = "policy-engine",
) -> str:
    """Mint one certificate and return its id. Refuses a bad risk, TTL or empty task/owner."""
    if not str(task_id or "").strip():
        raise CertificateRefused("a task id is required")
    if not str(owner_id or "").strip():
        raise CertificateRefused("an owner is required")
    if risk_ceiling not in RISKS:
        raise CertificateRefused(f"risk ceiling must be one of {', '.join(RISKS)}")
    if int(ttl_s) <= 0:
        raise CertificateRefused("ttl must be positive")
    if int(max_calls) <= 0:
        raise CertificateRefused("max_calls must be positive")

    now = datetime.now(timezone.utc)
    expires = now + timedelta(seconds=int(ttl_s))
    cert_id = "ic_" + secrets.token_urlsafe(18)
    claims: dict[str, Any] = {
        "cert_id": cert_id,
        "task_id": str(task_id),
        "owner_id": str(owner_id),
        "agent": str(agent or "kell"),
        "goal_id": str(goal_id) if goal_id else None,
        "allowed_servers": sorted({str(s) for s in allowed_servers if str(s)}),
        "allowed_tools": sorted({str(t) for t in allowed_tools if str(t)}),
        "argument_constraints": argument_constraints or {},
        "data_class": str(data_class or "PUBLIC_SOURCE"),
        "risk_ceiling": risk_ceiling,
        "egress_allow": sorted({str(e) for e in egress_allow if str(e)}),
        "max_calls": int(max_calls),
        "issued_at": _iso(now),
        "expires_at": _iso(expires),
        "issued_by": str(issued_by),
        "nonce": secrets.token_hex(8),
    }
    signature = signing.sign(claims)
    await pool.execute(
        """
        INSERT INTO intent_certificates
            (id, task_id, owner_id, agent, goal_id, data_class, risk_ceiling, max_calls,
             claims, signature, issued_by, issued_at, expires_at)
        VALUES ($1, $2, $3, $4, $5::uuid, $6, $7, $8, $9::jsonb, $10, $11, $12, $13)
        """,
        cert_id, claims["task_id"], claims["owner_id"], claims["agent"], claims["goal_id"],
        claims["data_class"], claims["risk_ceiling"], claims["max_calls"],
        json.dumps(claims), signature, claims["issued_by"], now, expires,
    )
    return cert_id


async def verify(pool: Any, cert_id: str, call: Any) -> tuple[bool, str]:
    """`(True, "")` when the call is inside the certificate; `(False, reason)` otherwise."""
    row = await pool.fetchrow(
        """SELECT id, claims, signature, revoked_at, expires_at
           FROM intent_certificates WHERE id = $1""", str(cert_id or ""))
    if row is None:
        return False, "cert:not_found"
    if row["revoked_at"] is not None:
        return False, "cert:revoked"

    claims = _claims(row["claims"])
    if not claims:
        return False, "cert:malformed"
    if not signing.verify(claims, row["signature"]):
        return False, "cert:signature"

    try:
        expires = datetime.fromisoformat(str(claims.get("expires_at") or ""))
    except ValueError:
        return False, "cert:malformed"
    if expires.tzinfo is None:
        expires = expires.replace(tzinfo=timezone.utc)
    if datetime.now(timezone.utc) >= expires:
        return False, "cert:expired"

    if str(_field(call, "owner_id", "") or "") != str(claims.get("owner_id") or ""):
        return False, "cert:owner"
    if claims.get("task_id") and str(_field(call, "task_id", "") or "") != str(claims["task_id"]):
        return False, "cert:task"
    if claims.get("goal_id") and str(_field(call, "goal_id", "") or "") != str(claims["goal_id"]):
        return False, "cert:goal"

    server = str(_field(call, "server", "") or "")
    allowed_servers = claims.get("allowed_servers") or []
    if allowed_servers and server not in allowed_servers:
        return False, "cert:server"

    tool = str(_field(call, "tool", "") or "")
    allowed_tools = claims.get("allowed_tools") or []
    if allowed_tools and tool not in allowed_tools:
        return False, "cert:tool"

    if str(_field(call, "data_class", "PUBLIC_SOURCE") or "PUBLIC_SOURCE") != str(claims.get("data_class")):
        return False, "cert:data_class"

    ok, reason = _check_arguments(_field(call, "arguments", {}) or {},
                                  claims.get("argument_constraints") or {})
    if not ok:
        return False, reason
    return True, ""


def _check_arguments(arguments: dict[str, Any], constraints: dict[str, Any]) -> tuple[bool, str]:
    for key, rule in (constraints or {}).items():
        if not isinstance(rule, dict):
            continue
        if rule.get("required") and key not in arguments:
            return False, f"cert:argument:{key}:required"
        if key not in arguments:
            continue
        value = arguments[key]
        if rule.get("max_length") is not None and len(str(value)) > int(rule["max_length"]):
            return False, f"cert:argument:{key}:max_length"
        if rule.get("pattern") and not re.search(str(rule["pattern"]), str(value)):
            return False, f"cert:argument:{key}:pattern"
        if rule.get("enum") is not None and value not in rule["enum"]:
            return False, f"cert:argument:{key}:enum"
    return True, ""


async def revoke(pool: Any, cert_id: str, owner_id: str) -> bool:
    status = await pool.execute(
        """UPDATE intent_certificates SET revoked_at = now()
           WHERE id = $1 AND owner_id = $2 AND revoked_at IS NULL""",
        str(cert_id or ""), str(owner_id or ""))
    return status.endswith(" 1")
