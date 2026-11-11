"""The deterministic tool-call gate (docs/goal_router.md P1-04).

Every third-party call asks one function, `authorize()`, whether it may run. The answer is built
from the policy store, the task's intent certificate and the audit chain -- never from the model.

Modes (`TOOL_GATE`):

- `off`     -- today's behavior; no policy, no audit.
- `shadow`  -- compute the real decision, write the receipt, but let the call through. The
               default until policies cover the catalog.
- `enforce` -- the computed decision is the answer; a denied call never reaches the network.

Fail-closed: an unreadable policy, a bad certificate, an unavailable signing key or a missing audit
row for an R2+ call is a deny. `authorize()` never raises: a policy/certificate/DB failure becomes a
deny decision (review #34), so a deployment that has not applied `db/188-190` keeps working in
`shadow` and refuses R2+ in `enforce` instead of breaking every `use_tool`. `TOOL_GATE_STRICT=on`
denies R0/R1 too when the gate cannot decide.

R0/R1 calls may run without a certificate; R2+ calls require one. A policy `approve` is not an
execution: the caller must obtain an approval and call again. An approval is bound to the exact call
shape (owner, task, server, tool, argument hash, certificate); `ToolCall.approval_id` is optional and
can only spend an approval that matches the call it is attached to.
"""
from __future__ import annotations

import logging
import os
import uuid
from dataclasses import dataclass, field
from typing import Any, Optional

from app.tool_routing import approvals, audit, certificates, policy, signing

log = logging.getLogger(__name__)

MODES = ("off", "shadow", "enforce")
TRUTHY = ("1", "true", "yes", "on")
# The Goal uuid namespace used by the mapper and the trust view (scripts/map_tool_cards_to_goals.py).
TOOL_GOAL_NS = uuid.UUID("6ba7b811-9dad-11d1-80b4-00c04fd430c8")


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
    approval_id: Optional[str] = None


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


def strict_mode() -> bool:
    """`TOOL_GATE_STRICT=on`: an undecidable call is denied even at R0/R1."""
    return os.environ.get("TOOL_GATE_STRICT", "").strip().lower() in TRUTHY


def goal_uuid(goal: Optional[str]) -> Optional[str]:
    """A Goal as a uuid: a uuid passes through, a taxonomy key is hashed the way the mapper does it.

    `store.recent_goal()` returns the key the router last matched (for example `web`), which is not a
    uuid; the policy and audit columns are uuids, so normalize at the boundary.
    """
    text = str(goal or "").strip()
    if not text:
        return None
    try:
        return str(uuid.UUID(text))
    except ValueError:
        return str(uuid.uuid5(TOOL_GOAL_NS, f"kell:tool-goal:{text}"))


def _field(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


async def authorize(pool: Any, call: ToolCall) -> Decision:
    """Decide one call. The returned `effect` is what the caller must obey. Never raises."""
    mode = gate_mode()
    if mode == "off":
        return Decision("allow", "gate:off", "R0")
    try:
        return await _authorize(pool, call, mode)
    except signing.SigningUnavailable:
        log.exception("gate: signing is unavailable for %s/%s; failing closed",
                      _field(call, "server"), _field(call, "tool"))
        return await _fail_closed(pool, call, mode, "gate:signing_unavailable")
    except Exception:  # noqa: BLE001 -- a decision failure is a decision, not an exception (#34)
        log.exception("gate: could not decide %s/%s; failing closed",
                      _field(call, "server"), _field(call, "tool"))
        return await _fail_closed(pool, call, mode, "gate:error")


async def _authorize(pool: Any, call: ToolCall, mode: str) -> Decision:
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

    approval_id: Optional[str] = None
    if effect == "approve" and mode == "enforce":
        named = str(_field(call, "approval_id", "") or "") or None
        if named and await approvals.consume_named(pool, named, call):
            effect, reason, approval_id = "allow", f"approval:{named}", named
        else:
            found = await approvals.find(pool, call)
            if found is not None and await approvals.consume(pool, str(found["id"])):
                effect, reason, approval_id = "allow", f"approval:{found['id']}", str(found["id"])
            else:
                approval_id = await approvals.request(
                    pool, call, risk=risk,
                    data_class=str(_field(call, "data_class", "PUBLIC_SOURCE") or "PUBLIC_SOURCE"))

    decision = Decision(
        effect=effect,
        reason=reason,
        risk=risk,
        cert_id=cert_id,
        approval_id=approval_id,
        constraints=constraints,
    )
    return await _finish(pool, call, decision, mode)


async def _fail_closed(pool: Any, call: ToolCall, mode: str, reason: str) -> Decision:
    """Turn an undecidable call into a decision. The risk is unknown, so use the provider adapter's
    declared tier when there is one and R4 otherwise; R2+ denies, R0/R1 keeps today's behavior unless
    `TOOL_GATE_STRICT` is on. A receipt is written when the audit sink is reachable."""
    risk = _declared_risk(call)
    deny = strict_mode() or not policy.risk_at_most(risk, "R1")
    decision = Decision("deny" if deny else "allow", reason, risk)
    try:
        await audit.record(pool, call, decision, status=reason)
    except Exception:  # noqa: BLE001 -- the sink itself is what failed
        log.exception("gate: could not write the failure receipt")
    return _apply_mode(decision, mode)


def _declared_risk(call: Any) -> str:
    """The provider adapter's own risk tier, else R4 (unknown is the most dangerous)."""
    try:
        from app import tool_providers

        adapter = tool_providers.get(str(_field(call, "server", "") or ""))
        risk = str(getattr(adapter, "RISK", "") or "") if adapter is not None else ""
        if risk in policy.RISK_ORDER:
            return risk
    except Exception:  # noqa: BLE001 -- the hint is best-effort; R4 is the answer if it fails
        pass
    return "R4"


async def _finish(pool: Any, call: ToolCall, decision: Decision, mode: str) -> Decision:
    """Write the receipt, then apply the mode. Shadow always returns `allow`."""
    try:
        await audit.record(
            pool, call, decision,
            status="shadow" if mode == "shadow" else "decision",
            approval_id=decision.approval_id,
        )
    except signing.SigningUnavailable:
        log.exception("gate: signing is unavailable; R2+ cannot be receipted")
        if mode == "enforce" and not policy.risk_at_most(decision.risk, "R1"):
            return Decision("deny", "gate:signing_unavailable", decision.risk,
                            cert_id=decision.cert_id)
    except Exception:  # noqa: BLE001 -- an audit failure is itself a decision input
        log.exception("gate: audit write failed")
        if mode == "enforce" and not policy.risk_at_most(decision.risk, "R1"):
            return Decision("deny", "gate:audit_unavailable", decision.risk,
                            cert_id=decision.cert_id)
    return _apply_mode(decision, mode)


def _apply_mode(decision: Decision, mode: str) -> Decision:
    if mode != "shadow":
        return decision
    return Decision(
        effect="allow",
        reason=f"shadow:{decision.effect}:{decision.reason}",
        risk=decision.risk,
        cert_id=decision.cert_id,
        approval_id=decision.approval_id,
        constraints=decision.constraints,
        shadow=True,
    )
