"""
Applicability function (ticket 12, memory-substrate map): is procedure P
applicable to current state S? spec.md is emphatic this is NOT semantic
similarity -- the CBR literature the ticket cites draws exactly this
distinction: similarity is a cheap a-priori approximation of reusability
and is often wrong. This module implements the resolved design directly:
a non-compensatory filter cascade on hard constraints (preconditions,
state, scope, exclusions, temporal validity, verification status,
availability, staleness), with semantic similarity ranking only the
survivors -- never compensating for a violated hard constraint.

Deliberately the mirror image of ticket 14 (retrieval): hard constraints
here disqualify; soft signals there fuse. The two must not be unified --
see this module's docstrings on why a violated precondition is not "a
low score", it's a disqualification.

HONEST SCOPE for this pass:
- Preconditions are checked via project_state() (ticket 10), fail-closed
  under CWA -- "no claim found" and "precondition unsatisfied" are the
  same answer, exactly as ticket 12 resolved (rejecting three-valued
  logic to preserve ticket 10's closed-world assumption).
- Coarse tag-based filtering (ticket 12's first, cheap layer) is NOT
  wired in here -- precondition_gate.py's existing tag machinery is a
  separate, already-real mechanism this pass doesn't touch or duplicate.
- Executable environment checks (ticket 12's fourth layer) are deferred,
  exactly as the ticket itself defers them ("need sandboxing and result
  caching, nothing in milestone 1 forces them").
- "Relevant local graph neighbourhood" ranking is NOT implemented --
  that's ticket 14's territory (local retrieval hierarchy), explicitly
  not built in this pass. Soft ranking here is semantic similarity only.
- Version-space scope/exclusion narrowing (automatic) is NOT
  implemented -- ticket 12 itself defers automation to fog and only
  requires *recording* evidence, which this module does not yet do
  either (recording adaptation-failure conditions is real, separate
  follow-on work, honestly not done here).
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

import asyncpg

from app.services.access import AccessScope
from app.services.embeddings import to_pgvector
from app.services.invariants import check_invariants
from app.services.state import project_state

# Ticket 12's cold-start answer: "disable procedure retrieval entirely
# while evidence is thin... fall back to generative planning." The
# ticket does not give a specific number for "enough evidence" --
# this threshold is a judgement call, not derived from the ticket's own
# text, and is flagged as such rather than presented as resolved.
MIN_VERIFIED_PROCEDURES_TO_ENABLE_RETRIEVAL = 1


@dataclass
class ApplicabilityResult:
    procedure_row_id: str
    applicable: bool
    # Names of every hard constraint that failed, e.g. "temporal_validity",
    # "verification_state", "precondition:subject=foo,predicate=bar" --
    # kept even when applicable=False for exactly one reason (fail-fast
    # short-circuit means only the FIRST failure is usually recorded,
    # not every one), so this is diagnostic, not exhaustive.
    failed_constraints: list[str] = field(default_factory=list)
    similarity_score: Optional[float] = None


async def should_disable_procedure_retrieval(pool: asyncpg.Pool) -> bool:
    """
    Ticket 12's cold-start answer, made real and callable rather than
    left as a design note: while too few procedures have real recorded
    verification evidence, procedure retrieval should be disabled
    entirely and the caller should fall back to generative planning
    (ticket 15's planner-as-default-not-fallback phase). Returns True
    when retrieval should be DISABLED (few verified procedures exist).
    """
    count = await pool.fetchval(
        "SELECT count(*) FROM procedures WHERE verification_state = 'verified' "
        "AND availability = 'active' AND t_invalid IS NULL"
    )
    return count < MIN_VERIFIED_PROCEDURES_TO_ENABLE_RETRIEVAL


def _scope_matches(procedure_scope: dict, current_scope: dict) -> bool:
    """
    A procedure's `scope` narrows where it applies (ticket 12: "scope
    and exclusions... machine-writable, not just human-authored" --
    representation only in this pass, automated narrowing deferred).
    Empty procedure_scope means unrestricted. Each key present on the
    procedure must have at least one overlapping value with the
    caller's current_scope for that same key; a key the procedure
    doesn't mention imposes no constraint.
    """
    if not procedure_scope:
        return True
    for key, allowed_values in procedure_scope.items():
        current_values = current_scope.get(key)
        if not current_values:
            return False  # procedure requires this key scoped; caller didn't supply it
        allowed_set = set(allowed_values) if isinstance(allowed_values, list) else {allowed_values}
        current_set = set(current_values) if isinstance(current_values, list) else {current_values}
        if not (allowed_set & current_set):
            return False
    return True


def _excluded(procedure_exclusions: list, current_scope: dict) -> bool:
    """Each exclusion entry is {"key": ..., "values": [...]}; the
    procedure is excluded if ANY exclusion's values overlap the
    caller's current_scope for that key."""
    for exclusion in procedure_exclusions:
        key = exclusion.get("key")
        excluded_values = exclusion.get("values", [])
        current_values = current_scope.get(key)
        if not current_values:
            continue
        excluded_set = set(excluded_values) if isinstance(excluded_values, list) else {excluded_values}
        current_set = set(current_values) if isinstance(current_values, list) else {current_values}
        if excluded_set & current_set:
            return True
    return False


async def check_hard_constraints(
    pool: asyncpg.Pool,
    procedure: dict,
    *,
    current_scope: Optional[dict] = None,
    access_scope: Optional[AccessScope] = None,
    require_verified: bool = True,
    as_of: Optional[datetime] = None,
    invariant_bindings: Optional[dict[str, float]] = None,
) -> ApplicabilityResult:
    """
    The non-compensatory filter cascade itself. Short-circuits on the
    FIRST failed hard constraint -- this is deliberate, not an
    oversight: a non-compensatory filter doesn't need to enumerate every
    violation, only confirm at least one exists, and short-circuiting is
    also the "fail fast" mitigation ticket 15 names for match-cost
    (don't evaluate every precondition once one has already
    disqualified the procedure).

    `require_verified`: ticket 13's exact wording -- "verified gates
    automatic retrieval; a candidate procedure remains explicitly
    invocable." Pass False for an explicit-invocation caller (a human or
    agent deliberately naming this procedure), True (the default) for
    anything doing automatic candidate selection.

    `current_scope`: caller-supplied dict describing the real current
    task context (e.g. {"repo": [...], "files": [...]})…, checked
    against the procedure's own `scope`/`exclusions` fields.
    """
    current_scope = current_scope or {}
    as_of = as_of or datetime.now(timezone.utc)
    row_id = str(procedure["id"])

    # Temporal validity.
    if procedure["t_invalid"] is not None and procedure["t_invalid"] <= as_of:
        return ApplicabilityResult(row_id, False, ["temporal_validity"])

    # Ticket 13's three orthogonal axes -- all three gate applicability,
    # not just verification_state. A stale-but-verified procedure must
    # not be treated as applicable ("a stale procedure is reused as
    # though verified" is exactly the failure ticket 13 names).
    if procedure["staleness"] == "stale":
        return ApplicabilityResult(row_id, False, ["staleness"])
    if procedure["availability"] != "active":
        return ApplicabilityResult(row_id, False, ["availability"])
    if require_verified and procedure["verification_state"] != "verified":
        return ApplicabilityResult(row_id, False, ["verification_state"])
    # REAL GAP CLOSED (found while wiring the first real caller of this
    # function): migration 20 added approval_status as a column, but
    # nothing ever checked it -- a procedure could reach 'verified' via
    # pure statistics without a human ever having approved it, and
    # automatic retrieval would happily surface it. Tied to the SAME
    # require_verified flag as verification_state, not checked
    # unconditionally: ticket 13's own rule ("verified gates automatic
    # retrieval; a candidate procedure remains explicitly invocable")
    # extends consistently to approval -- explicit invocation
    # (require_verified=False, a human/agent naming this procedure by
    # id) bypasses both gates the same way; only AUTOMATIC selection
    # requires both real evidence AND a human sign-off. Gating this
    # unconditionally would also break test_applicability_e2e.py's
    # existing suite, which predates approval_status and never sets it.
    if require_verified and procedure.get("approval_status") != "approved":
        return ApplicabilityResult(row_id, False, ["approval_status"])

    # Scope / exclusions.
    if not _scope_matches(procedure["scope"] or {}, current_scope):
        return ApplicabilityResult(row_id, False, ["scope"])
    if _excluded(procedure["exclusions"] or [], current_scope):
        return ApplicabilityResult(row_id, False, ["exclusions"])

    # Preconditions -- structured predicates over the claim graph,
    # checked via project_state() (ticket 10), fail-closed under CWA.
    # "No claim found" and "precondition unsatisfied" are the same
    # answer here, deliberately -- ticket 12 rejected three-valued logic
    # specifically to preserve this.
    for precondition in (procedure["preconditions"] or []):
        subject = precondition.get("subject")
        predicate = precondition.get("predicate")
        expected_object = precondition.get("object")
        if not subject:
            continue  # malformed precondition entry -- not this function's job to validate authoring

        claims = await project_state(pool, subjects=[subject], as_of=as_of, scope=access_scope)
        satisfied = any(
            c["properties"].get("predicate") == predicate
            and c["properties"].get("object") == expected_object
            for c in claims
        )
        if not satisfied:
            return ApplicabilityResult(
                row_id, False,
                [f"precondition:subject={subject},predicate={predicate},object={expected_object}"],
            )

    # Numeric invariants -- LAST in the cascade, deliberately. Two
    # reasons, both real: (1) this is the only stage that can invoke a
    # solver, making it the most expensive check here, and
    # find_applicable_procedures() explicitly orders candidates
    # cheapest-to-match-first for exactly that kind of cost reason --
    # running it before the equality checks above would invert that; (2)
    # it is a no-op for every procedure row that exists today (all have
    # empty `invariants`), so it must not sit in front of checks that do
    # real work.
    #
    # UNDECIDABLE IS NOT DISQUALIFYING, and that asymmetry is the whole
    # point. At retrieval time nobody has stated an amount yet, so a
    # procedure whose invariant references unbound quantities is the
    # NORMAL case -- treating that as inapplicable would make every
    # invariant-bearing procedure permanently unretrievable, which is the
    # same "looks correct, never fires" failure V1 exists to prevent.
    # Only a definite violation (all variables bound, relation false)
    # disqualifies.
    invariant_result = check_invariants(
        procedure.get("invariants") or [], invariant_bindings or {},
    )
    if invariant_result.violated or invariant_result.errors:
        return ApplicabilityResult(
            row_id, False,
            [f"invariant:{v}" for v in (invariant_result.violated + invariant_result.errors)],
        )

    return ApplicabilityResult(row_id, True, [])


async def find_applicable_procedures(
    pool: asyncpg.Pool,
    *,
    goal_embedding: Optional[list[float]] = None,
    current_scope: Optional[dict] = None,
    access_scope: Optional[AccessScope] = None,
    require_verified: bool = True,
    limit: int = 10,
    candidate_pool_size: int = 200,
    invariant_bindings: Optional[dict[str, float]] = None,
) -> list[dict]:
    """
    Real ticket-12 pipeline, end to end: cold-start gate, then the
    non-compensatory hard filter, then semantic-similarity ranking of
    survivors ONLY (never the reverse order -- ranking before filtering
    would let a high similarity score influence which candidates get
    fetched, which is a milder form of the criterion-compensation
    antipattern this whole design exists to avoid).

    Returns [] immediately if should_disable_procedure_retrieval()
    holds -- an empty list is the caller's real signal to fall back to
    generative planning (ticket 15), not an error.

    `candidate_pool_size`: ticket 15's match-cost-aware ordering
    ("order candidate procedures cheapest-to-match first") -- candidates
    are fetched ordered by their own precondition count ascending (fewer
    predicates to evaluate = cheaper to match), so if candidate_pool_size
    is smaller than the true candidate set, the ones skipped are the
    more expensive ones to check, not an arbitrary subset.
    """
    if await should_disable_procedure_retrieval(pool):
        return []

    rows = await pool.fetch(
        "SELECT * FROM procedures "
        "WHERE t_invalid IS NULL AND staleness != 'stale' AND availability = 'active' "
        "ORDER BY jsonb_array_length(preconditions) ASC "
        "LIMIT $1",
        candidate_pool_size,
    )

    survivors = []
    for row in rows:
        procedure = dict(row)
        result = await check_hard_constraints(
            pool, procedure, current_scope=current_scope, access_scope=access_scope,
            require_verified=require_verified, invariant_bindings=invariant_bindings,
        )
        if result.applicable:
            survivors.append(procedure)

    if not survivors:
        return []

    if goal_embedding is None:
        # No query embedding supplied -- return hard-filter survivors
        # unranked rather than fabricating a similarity order. Real,
        # honest degradation, not silently swapped for e.g. recency.
        return survivors[:limit]

    survivor_ids = [s["id"] for s in survivors]
    ranked = await pool.fetch(
        "SELECT id, 1 - (embedding <=> $1::vector) AS similarity FROM procedures "
        "WHERE id = ANY($2::uuid[]) AND embedding IS NOT NULL "
        "ORDER BY embedding <=> $1::vector ASC "
        "LIMIT $3",
        to_pgvector(goal_embedding), survivor_ids, limit,
    )
    ranked_ids = {str(r["id"]): r["similarity"] for r in ranked}
    survivors_by_id = {str(s["id"]): s for s in survivors}

    result_list = []
    for rid, similarity in ranked_ids.items():
        proc = dict(survivors_by_id[rid])
        proc["_similarity_score"] = similarity
        result_list.append(proc)

    # Survivors with no embedding at all can't be ranked -- append them
    # after the ranked ones rather than silently dropping them, since
    # they're still real, hard-filter-passing candidates.
    unranked_survivors = [s for s in survivors if str(s["id"]) not in ranked_ids]
    return result_list + unranked_survivors[: max(0, limit - len(result_list))]
