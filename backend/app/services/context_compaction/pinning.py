"""
Deterministic context pinning -- lifecycle/state management, NOT semantic
judgment. A pin only restricts how aggressively a unit may be reduced:

  hard  -> always KEEP_VERBATIM (system prompt, the user's goal/latest
           instruction, user constraints, explicitly pinned items)
  soft  -> never DROP / KEEP_REFERENCE_ONLY; the semantic judge may still
           choose KEEP_VERBATIM vs KEEP_COMPACT (unresolved failures, items a
           blocker refers to, the recent working window)
"""
from __future__ import annotations

from app.services.context_compaction.models import StealthState, Unit

HARD, SOFT = "hard", "soft"


def _signature(unit: Unit) -> tuple:
    call = next((i for i in unit.items if i.kind == "tool_call"), None)
    r = unit.result
    return (r.tool_name, call.text if call else (r.path or ""))


def unresolved_failure_units(units: list[Unit]) -> set[str]:
    """A failed unit is unresolved unless a LATER unit with the same tool +
    input signature succeeded (e.g. the test command was re-run green)."""
    out: set[str] = set()
    for idx, u in enumerate(units):
        if not u.result.failed:
            continue
        sig = _signature(u)
        recovered = any(
            _signature(v) == sig and not v.result.failed and v.result.kind not in ("assistant_message", "user_message")
            for v in units[idx + 1:]
        )
        if not recovered:
            out.add(u.unit_id)
    return out


def compute_pins(units: list[Unit], state: StealthState, *, recent_window: int = 6) -> dict[str, tuple[str, str]]:
    pins: dict[str, tuple[str, str]] = {}
    user_units = [u for u in units if u.result.kind == "user_message"]
    for u in units:
        if any(i.kind == "system" for i in u.items) or any(i.meta.get("pinned") for i in u.items):
            pins[u.unit_id] = (HARD, "system_or_explicit_pin")
    if user_units:
        pins.setdefault(user_units[0].unit_id, (HARD, "user_goal"))
        pins.setdefault(user_units[-1].unit_id, (HARD, "latest_user_instruction"))
    constraints = [c.lower() for c in state.constraints if c]
    for u in user_units:
        low = u.result.text.lower()
        if any(c in low for c in constraints):
            pins.setdefault(u.unit_id, (HARD, "user_constraint"))
    unresolved = unresolved_failure_units(units)
    blockers = [b.lower() for b in state.blockers if b]
    for u in units:
        if u.unit_id in pins:
            continue
        if u.unit_id in unresolved:
            pins[u.unit_id] = (SOFT, "unresolved_failure")
        elif blockers and any(b in " ".join(i.text for i in u.items).lower() for b in blockers):
            pins[u.unit_id] = (SOFT, "blocker_ref")
    for u in (units[-recent_window:] if recent_window > 0 else []):
        pins.setdefault(u.unit_id, (SOFT, "recent_window"))
    return pins
