"""The deterministic tool-call gate (docs/goal_router.md P1-04).

Every third-party call asks one function, `authorize()`, whether it may run. The answer is built
from the policy store, the task's intent certificate and the audit chain -- never from the model.

Modes (`TOOL_GATE`):

- `off`     -- today's behavior; no policy, no audit.
- `shadow`  -- compute the real decision, write the receipt, but let the call through. The
               default until policies cover the catalog.
- `enforce` -- the computed decision is the answer; a denied call never reaches the network.

Fail-closed: an unreadable policy, a bad certificate, or a missing audit row for an R2+ call is a
deny. R0/R1 calls may run without a certificate; R2+ calls require one. A policy `approve` is not
an execution: the caller must obtain an approval and call again.
"""
from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, Optional

from app.tool_routing import audit, certificates, policy

log = logging.getLogger(__name__)

MODES = ("off", "shadow", "enforce")


@dataclass
class ToolCall:
    owner_id: Optional[str]
    task_id: Optional[str]
    goal_id: Optional[str]
    server: str
    tool: str
    arguments: dict[str, Any]
    data_class: str = "PUBLIC_SOURCE"
    agent: str = "kell"
    cert_id: Optional[str] = None


@dataclass
class Decision:
    effect: str                       # "allow" | "deny" | "approve"
    reason: str
    risk: str                         # "R0".."R4"
    cert_id: Optional[str] = None
    approval_id: Optional[str] = None
    constraints: dict[str, Any] = field(default_factory=dict)
    shadow: bool = False


def gate_mode() -> str:
    """`off`, `shadow` or `enforce`; missing/empty is `shadow`, an unknown value is `enforce`."""
    raw = os.environ.get("TOOL_GATE")
    if raw is None or not raw.strip():
        return "shadow"
    mode = raw.strip().lower()
    return mode if mode in MODES else "enforce"


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


async def authorize(pool: Any, call: ToolCall) -> Decision:
    """Decide one call. The returned `effect` is what the caller must obey."""
    mode = gate_mode()
    if mode == "off":
        return Decision("allow", "gate:off", "R0")

    cert_id = str(_field(call, "cert_id", "") or "") or None
    cert = None
    if cert_id:
        ok, why = await certificates.verify(pool, cert_id, call)
        if not ok:
            return await _finish(pool, call, Decision("deny", why, "R4", cert_id=cert_id), mode)
        cert = await certificates.load(pool, cert_id)
        if cert is None:
            return await _finish(pool, call,
                                 Decision("deny", "cert:not_found", "R4", cert_id=cert_id), mode)

    effect, reason, constraints = await policy.evaluate(pool, call, cert)
    risk = str(constraints.get("risk") or "R4")

    # R0/R1 may run without a certificate; R2+ needs one. A policy that already denied keeps its
    # own reason (`policy:no_rule` is more useful than `cert:required`).
    if not cert_id and effect in ("allow", "approve") and not policy.risk_at_most(risk, "R1"):
        effect, reason = "deny", "cert:required"

    decision = Decision(
        effect=effect,
        reason=reason,
        risk=risk,
        cert_id=cert_id,
        constraints=constraints,
    )
    return await _finish(pool, call, decision, mode)


async def _finish(pool: Any, call: ToolCall, decision: Decision, mode: str) -> Decision:
    """Write the receipt, then apply the mode. Shadow always returns `allow`."""
    try:
        await audit.record(
            pool, call, decision,
            status="shadow" if mode == "shadow" else "decision",
            approval_id=decision.approval_id,
        )
    except Exception:  # noqa: BLE001 -- an audit failure is itself a decision input
        log.exception("gate: audit write failed")
        if mode == "enforce" and not policy.risk_at_most(decision.risk, "R1"):
            return Decision("deny", "gate:audit_unavailable", decision.risk,
                            cert_id=decision.cert_id)

    if mode == "shadow":
        return Decision(
            effect="allow",
            reason=f"shadow:{decision.effect}:{decision.reason}",
            risk=decision.risk,
            cert_id=decision.cert_id,
            approval_id=decision.approval_id,
            constraints=decision.constraints,
            shadow=True,
        )
    return decision
