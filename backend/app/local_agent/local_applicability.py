"""
DB-free applicability for LocalProcedureStore rows -- the local
counterpart to app/services/applicability.py::check_hard_constraints,
deliberately narrower.

HONEST SCOPE, stated up front so nobody downstream mistakes this for the
full DB-backed cascade:
  - NO precondition-against-claims-graph checking. check_hard_constraints
    resolves `preconditions` against project_state() (the claims graph);
    a local SQLite store has no claims graph to project state from. A
    local procedure's `preconditions` field is carried on the row (same
    JSON shape) but this function does not evaluate it -- a
    precondition-bearing local procedure is neither disqualified nor
    passed on that basis, it is simply not checked. This is a real,
    smaller scope, not a silent equivalent.
  - NO approval_status gate -- there is no separate local "approved by a
    human reviewer" concept; a local procedure's own verification_state
    (candidate/verified, driven by record_local_execution_outcome's real
    counters) is the only trust signal this store has. Documented here
    rather than silently inventing a second gate that means nothing
    locally.
  - NO tenant/visibility scoping -- a local store has exactly one viewer.

WHAT IS REUSED, NOT REINVENTED (Rule 6):
  - app.services.invariants.check_invariants -- the SAME pure numeric-
    invariant solver applicability.py's cascade calls (via its async
    wrapper); this module calls the sync version directly since there is
    no event loop contention concern for a single local agent process.
  - app.services.applicability._scope_matches / _excluded -- confirmed
    DB-free (pure dict logic, no `pool` argument, no query) by reading
    that module before importing from it; imported directly rather than
    copy-pasted, so the two scope-matching definitions can never
    silently drift apart.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.services.applicability import _excluded, _scope_matches
from app.services.invariants import check_invariants


@dataclass
class LocalApplicabilityResult:
    row_id: str
    applicable: bool
    failed_constraints: list[str] = field(default_factory=list)


def check_local_hard_constraints(
    procedure: dict,
    *,
    current_scope: Optional[dict] = None,
    require_verified: bool = True,
    invariant_bindings: Optional[dict[str, float]] = None,
) -> LocalApplicabilityResult:
    """
    The DB-free cascade: temporal validity, staleness, availability,
    verification_state (when require_verified), scope/exclusions, numeric
    invariants -- same order and same short-circuit-on-first-failure
    discipline as check_hard_constraints, minus the two DB-bound stages
    (preconditions, approval_status) documented above.

    `require_verified` mirrors check_hard_constraints' own flag: True
    (the default) is "automatic selection", False is "an explicit,
    caller-named invocation" -- same ticket-13 rule this store's
    verification_state ladder already carries forward.
    """
    current_scope = current_scope or {}
    row_id = str(procedure["id"])

    if procedure.get("t_invalid") is not None:
        return LocalApplicabilityResult(row_id, False, ["temporal_validity"])

    if procedure.get("staleness") == "stale":
        return LocalApplicabilityResult(row_id, False, ["staleness"])
    if procedure.get("availability", "active") != "active":
        return LocalApplicabilityResult(row_id, False, ["availability"])
    if require_verified and procedure.get("verification_state") != "verified":
        return LocalApplicabilityResult(row_id, False, ["verification_state"])

    if not _scope_matches(procedure.get("scope") or {}, current_scope):
        return LocalApplicabilityResult(row_id, False, ["scope"])
    if _excluded(procedure.get("exclusions") or [], current_scope):
        return LocalApplicabilityResult(row_id, False, ["exclusions"])

    invariant_result = check_invariants(
        procedure.get("invariants") or [], invariant_bindings or {},
    )
    if invariant_result.violated or invariant_result.errors:
        return LocalApplicabilityResult(
            row_id, False,
            [f"invariant:{v}" for v in (invariant_result.violated + invariant_result.errors)],
        )

    return LocalApplicabilityResult(row_id, True, [])
