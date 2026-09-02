"""
DB-free applicability for LocalProcedureStore rows -- the local
counterpart to app/services/applicability.py::check_hard_constraints,
deliberately narrower.

HONEST SCOPE, stated up front so nobody downstream mistakes this for the
full DB-backed cascade:
  - Precondition evaluation is LOCAL-ENVIRONMENT-ONLY, not
    claims-graph-backed. check_hard_constraints resolves `preconditions`
    against project_state() (the global claims graph); a local SQLite
    store has no claims graph to project state from. What this module
    CAN do instead: resolve a precondition's (predicate, object) against
    real, locally-probed environment facts (app.services.environment_facts
    -- the same PROBE_PREDICATE_VOCABULARY-closed vocabulary the global
    cascade's own derivation already restricts extracted preconditions
    to, per procedure_extraction/derive.py). The caller supplies these
    facts explicitly via `environment_facts` (pre-probed, e.g. by
    app.local_agent.runner via `probe_environment()` /
    `probe_installed_package_versions()`) -- this module never touches
    the filesystem or a package registry itself, keeping it pure and
    trivially fake-able in tests.

    UNKNOWN != TRUE (the real rule this module enforces, replacing the
    prior "skip entirely" behavior): for each declared precondition --
      * predicate not in PROBE_PREDICATE_VOCABULARY, OR no locally-probed
        fact exists for that predicate at all -> UNKNOWN -> reject
        (`precondition:unknown:...`). A local store's absence of
        evidence is never treated as evidence of absence OR presence.
      * a fact exists for that predicate but its object does not match
        the precondition's expected object -> VIOLATED -> reject
        (`precondition:violated:...`).
      * a fact exists and matches -> satisfied, cascade continues.
    A precondition-bearing local procedure can now genuinely fail (or
    pass) this cascade on that basis -- it is no longer unconditionally
    skipped. `require_verified=False` (an explicit, caller-named
    invocation) does not relax this: an explicit invocation still must
    not silently run a procedure whose precondition is known-violated,
    or unknown, against the real local environment.
  - NO approval_status gate -- there is no separate local "approved by a
    human reviewer" concept; a local procedure's own verification_state
    (candidate/verified, driven by record_local_execution_outcome's real
    counters) is the only trust signal this store has. Documented here
    rather than silently inventing a second gate that means nothing
    locally.
  - NO tenant/visibility scoping -- a local store has exactly one viewer.

FULLY LOCAL, NEVER REMOTE. Nothing in this module makes a network call
or touches app.db/asyncpg: `environment_facts` is data the caller already
probed off the local filesystem/interpreter (see
app.services.environment_facts, which is itself DB-free and
network-free by AST-enforced test); this module only compares dicts.

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
  - app.services.environment_facts.PROBE_PREDICATE_VOCABULARY /
    invariant_bindings_from_facts -- the SAME closed vocabulary and
    numeric-binding conversion the global derivation path
    (procedure_extraction/derive.py) and app.local_agent.runner already
    use; this module does not redeclare or re-derive either.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional

from app.services.applicability import _excluded, _scope_matches
from app.services.environment_facts import (
    PROBE_PREDICATE_VOCABULARY,
    EnvironmentFact,
    invariant_bindings_from_facts,
)
from app.services.invariants import check_invariants


@dataclass
class LocalApplicabilityResult:
    row_id: str
    applicable: bool
    failed_constraints: list[str] = field(default_factory=list)


def _facts_by_predicate(
    environment_facts: Optional[list[EnvironmentFact]],
) -> dict[str, set[str]]:
    """Groups probed facts by predicate -> {object, ...}. A predicate
    with NO entry here (as opposed to an entry that just doesn't contain
    the expected object) is the concrete signal `_evaluate_precondition`
    uses to distinguish 'never probed' from 'probed and violated' --
    both reject, but the reason string differs so a caller can tell them
    apart."""
    grouped: dict[str, set[str]] = {}
    for fact in environment_facts or []:
        grouped.setdefault(fact.predicate, set()).add(fact.object)
    return grouped


def _evaluate_precondition(
    precondition: dict, facts_by_predicate: dict[str, set[str]],
) -> Optional[str]:
    """Returns None if satisfied, else a reason string. Equality-only
    matching against (predicate, object) -- the SAME comparison
    app.services.applicability._claim_matches_precondition performs
    against a claim's properties (`properties.get("predicate") ==
    predicate and properties.get("object") == expected_object`); this
    function just resolves against locally-probed facts instead of a
    claims-graph row, so the two never drift on what "matches" means."""
    subject = precondition.get("subject")
    predicate = precondition.get("predicate")
    expected_object = precondition.get("object")

    if not predicate or predicate not in PROBE_PREDICATE_VOCABULARY:
        return (
            f"precondition:unknown:predicate={predicate!r} is not in the local "
            "probe's closed vocabulary -- cannot be resolved locally, and an "
            "unresolvable precondition is never treated as satisfied"
        )

    probed_objects = facts_by_predicate.get(predicate)
    if probed_objects is None:
        return (
            f"precondition:unknown:no local environment fact was probed for "
            f"predicate={predicate!r} (subject={subject!r}) -- absence of "
            "local evidence is not evidence the precondition holds"
        )

    if expected_object not in probed_objects:
        return (
            f"precondition:violated:subject={subject!r} predicate={predicate!r} "
            f"expected object={expected_object!r}, locally probed "
            f"object(s)={sorted(probed_objects)!r}"
        )

    return None


def check_local_hard_constraints(
    procedure: dict,
    *,
    current_scope: Optional[dict] = None,
    require_verified: bool = True,
    invariant_bindings: Optional[dict[str, float]] = None,
    environment_facts: Optional[list[EnvironmentFact]] = None,
) -> LocalApplicabilityResult:
    """
    The DB-free cascade: temporal validity, staleness, availability,
    verification_state (when require_verified), scope/exclusions,
    preconditions (against `environment_facts`, UNKNOWN-rejects per
    module docstring), numeric invariants -- same order and same
    short-circuit-on-first-failure discipline as check_hard_constraints,
    minus the one still-DB-bound stage (approval_status) documented
    above.

    `require_verified` mirrors check_hard_constraints' own flag: True
    (the default) is "automatic selection", False is "an explicit,
    caller-named invocation" -- same ticket-13 rule this store's
    verification_state ladder already carries forward. Precondition
    UNKNOWN/VIOLATED rejection applies in BOTH cases -- see module
    docstring.

    `environment_facts`: real facts the CALLER already probed locally
    (e.g. `app.services.environment_facts.probe_environment(repo_root)`
    plus `probe_installed_package_versions([...])` for names the
    procedure's own preconditions/invariants name) -- this function never
    probes anything itself, it only compares. Omitted/None means "no
    local facts available", which correctly makes every precondition
    UNKNOWN (reject), not a silent pass.

    `invariant_bindings`: explicit numeric bindings, same as before. When
    None (not merely empty), bindings are derived from `environment_facts`
    via `invariant_bindings_from_facts` -- so a caller that already
    probed real package_version facts gets real numeric invariant
    checking (e.g. "pandas_version >= 2.0") without having to convert
    them by hand. Pass `invariant_bindings={}` explicitly to force "no
    bindings" even when `environment_facts` carries some.
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

    facts_by_predicate = _facts_by_predicate(environment_facts)
    for precondition in (procedure.get("preconditions") or []):
        if not isinstance(precondition, dict):
            continue  # malformed precondition entry -- not this function's job to validate authoring
        failure = _evaluate_precondition(precondition, facts_by_predicate)
        if failure is not None:
            return LocalApplicabilityResult(row_id, False, [failure])

    if invariant_bindings is None:
        invariant_bindings = invariant_bindings_from_facts(environment_facts or [])

    invariant_result = check_invariants(
        procedure.get("invariants") or [], invariant_bindings or {},
    )
    if invariant_result.violated or invariant_result.errors:
        return LocalApplicabilityResult(
            row_id, False,
            [f"invariant:{v}" for v in (invariant_result.violated + invariant_result.errors)],
        )

    return LocalApplicabilityResult(row_id, True, [])
