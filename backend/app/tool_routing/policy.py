"""Deterministic tool policy (db/188, docs/goal_router.md P1-05).

The gate asks one question: may this exact call run? The answer comes from `tool_policies`, not
from the model. Evaluation order:

1. an explicit `deny` row for this server/tool/goal wins over everything;
2. otherwise the most specific enabled row (server+tool > server > goal > global, then priority);
3. the call's data class must be in that row's `data_classes`;
4. the call's risk must be at or below the certificate's `risk_ceiling`;
5. the row's effect is the answer (`allow`, `deny` or `approve`);
6. no row at all is `deny`.

The function is pure with respect to the call: it reads the policy table and returns a decision.
It never executes a tool and never trusts the model's own claim about risk.
"""
from __future__ import annotations

from typing import Any, Optional

RISK_ORDER = {"R0": 0, "R1": 1, "R2": 2, "R3": 3, "R4": 4}


def _get(obj: Any, name: str, default: Any = None) -> Any:
    if obj is None:
        return default
    if isinstance(obj, dict):
        return obj.get(name, default)
    return getattr(obj, name, default)


def risk_at_most(risk: str, ceiling: Optional[str]) -> bool:
    if not ceiling:
        return True
    return RISK_ORDER.get(str(risk), 4) <= RISK_ORDER.get(str(ceiling), -1)


async def evaluate(pool: Any, call: Any, cert: Any = None) -> tuple[str, str, dict[str, Any]]:
    """`(effect, reason, constraints)` for one call; effect is `allow`, `deny` or `approve`."""
    server = str(_get(call, "server", "") or "")
    tool = str(_get(call, "tool", "") or "")
    goal_id = _get(call, "goal_id")
    data_class = str(_get(call, "data_class", "PUBLIC_SOURCE") or "PUBLIC_SOURCE")

    rows = await pool.fetch(
        """
        SELECT id, server, tool, goal_id::text AS goal_id, risk, effect, data_classes,
               max_calls, egress, timeout_s, priority,
               ((server IS NOT NULL)::int * 4 + (tool IS NOT NULL)::int * 2
                + (goal_id IS NOT NULL)::int) AS specificity
        FROM tool_policies
        WHERE enabled
          AND (server IS NULL OR server = $1)
          AND (tool IS NULL OR tool = $2)
          AND (goal_id IS NULL OR goal_id = $3::uuid)
        ORDER BY specificity DESC, priority DESC, id ASC
        """,
        server, tool, goal_id,
    )
    if not rows:
        return "deny", "policy:no_rule", {}

    denied = next((r for r in rows if r["effect"] == "deny"), None)
    if denied is not None:
        return "deny", f"policy:deny:{denied['id']}", {"risk": str(denied["risk"])}

    row = rows[0]
    risk = str(row["risk"])
    constraints: dict[str, Any] = {
        "risk": risk,
        "max_calls": row["max_calls"],
        "egress": list(row["egress"] or []),
        "timeout_s": row["timeout_s"],
    }

    cert_max = _get(cert, "max_calls")
    if cert_max and row["max_calls"]:
        constraints["max_calls"] = min(int(cert_max), int(row["max_calls"]))
    elif cert_max:
        constraints["max_calls"] = int(cert_max)

    if data_class not in (row["data_classes"] or []):
        return "deny", f"policy:data_class:{data_class}", constraints

    ceiling = _get(cert, "risk_ceiling")
    if not risk_at_most(risk, ceiling):
        return "deny", f"policy:risk_ceiling:{risk}>{ceiling}", constraints

    effect = str(row["effect"])
    if effect == "approve":
        return "approve", f"policy:approve:{row['id']}", constraints
    if effect == "allow":
        return "allow", f"policy:allow:{row['id']}", constraints
    return "deny", f"policy:deny:{row['id']}", constraints
