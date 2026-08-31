"""
Read-only, bounded traversal layer over the claim graph (CONSOLIDATED
directive's EXPLAIN / RESEARCH / DECIDE modes).

This module invents no new graph mechanism. It composes over primitives
that already exist and are already tested elsewhere:

  - `app.services.procedures.get_procedure` -- the procedure row, whose
    `preconditions` JSONB (list of `{subject, predicate, object}`) is
    the thing EXPLAIN/DECIDE walk.
  - `app.services.state.project_state` -- the EXACT subject -> claims
    lookup `app.services.applicability.check_hard_constraints` already
    uses per precondition (see its `_project_state_cached` helper). This
    module calls the same underlying function directly rather than
    reinvent a parallel claim-lookup path.
  - `app.services.applicability.check_hard_constraints` -- the real,
    non-compensatory hard-constraint cascade. DECIDE returns its result
    verbatim; this module never reimplements the cascade itself.
  - `app.services.claims.get_claim_relations` -- the new bounded,
    single-hop relation reader. RESEARCH composes exactly two calls to
    it (hop 1, then hop 2 from each hop-1 neighbour) -- never more.

Everything here is READ-ONLY. Every loop in this module is bounded by a
real, already-finite quantity that exists before the loop starts (a
procedure's own precondition list, or a claim's own real relation edges)
-- never by open-ended graph recursion. `research()` hard-codes a 2-hop
cap (`MAX_RESEARCH_HOPS`) and refuses a caller that asks for more; this
is a deliberate, directive-mandated limit, not a default that happens to
be small today.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from app.services.access import AccessScope
from app.services.applicability import ApplicabilityResult, check_hard_constraints
from app.services.claim_evidence import get_claim_evidence
from app.services.claims import get_claim_relations
from app.services.procedures import get_procedure
from app.services.state import project_state

# ---------------------------------------------------------------------------
# RESEARCH bucketing -- CONSOLIDATED's own relation vocabulary
# (app.services.claims.RELATIONS | GENERAL_RELATIONS), split three ways
# per this module's brief: SUPPORTS/GENERALIZES/DERIVED_FROM point at
# corroborating evidence; CONTRADICTS is the one relation that asserts
# conflict; everything else (REFINES/DEPENDS_ON/CONDITIONAL_ON/
# SPECIALIZES/INSTANTIATES/APPLIES_TO/SUPERSEDES) is real and relevant
# but neither straightforwardly "for" nor "against".
# ---------------------------------------------------------------------------
CONTRADICTING_RELATIONS = {"CONTRADICTS"}
SUPPORTING_RELATIONS = {"SUPPORTS", "GENERALIZES", "DERIVED_FROM"}

MAX_RESEARCH_HOPS = 2


def _bucket_for(relation: str) -> str:
    if relation in CONTRADICTING_RELATIONS:
        return "contradicting"
    if relation in SUPPORTING_RELATIONS:
        return "supporting"
    return "other"


# ---------------------------------------------------------------------------
# EXPLAIN
# ---------------------------------------------------------------------------


@dataclass
class PreconditionExplanation:
    subject: Optional[str]
    predicate: Optional[str]
    expected_object: Any
    satisfied: bool
    # Claims found for `subject` whose predicate/object actually match
    # this precondition -- the same equality check
    # applicability.check_hard_constraints performs, reused verbatim.
    supporting_claims: list[dict] = field(default_factory=list)
    # Live evidence rows (target_type='claim') for every claim in
    # `supporting_claims`, oldest first, via the real
    # `claim_evidence.get_claim_evidence()` reader. Empty for an
    # unsatisfied precondition (no supporting claims to fetch evidence
    # for) or for a claim nobody has recorded evidence against yet --
    # both are honest empties, not a missing feature.
    evidence: list[dict] = field(default_factory=list)


@dataclass
class ExplainResult:
    procedure_row_id: str
    preconditions: list[PreconditionExplanation] = field(default_factory=list)


async def explain(
    pool: asyncpg.Pool,
    procedure_row_id: str,
    *,
    as_of: Optional[datetime] = None,
    access_scope: Optional[AccessScope] = None,
) -> ExplainResult:
    """
    "Why does this procedure believe what it believes?"

    Walks the procedure's own `preconditions` (bounded -- a procedure's
    precondition list is finite and authored, not a graph to traverse),
    and for each precondition's `subject`, looks up the live claims via
    `project_state` -- the identical subject -> claims mechanism
    `check_hard_constraints` uses. Does NOT re-derive applicability;
    that is DECIDE's job.

    Also attaches real evidence: for each precondition's
    `supporting_claims`, fetches that claim's live evidence rows via
    `app.services.claim_evidence.get_claim_evidence()` (the bounded,
    real reader over `target_type = 'claim'` evidence, oldest first)
    and attaches the combined list on `PreconditionExplanation.evidence`.
    An unsatisfied precondition (no supporting claims) or a claim with
    no recorded evidence yet both surface as an honest empty list, not
    an error.
    """
    procedure = await get_procedure(pool, procedure_row_id)
    if procedure is None:
        raise ValueError(f"no procedure row with id={procedure_row_id!r}")

    as_of = as_of or datetime.now(timezone.utc)
    explanations: list[PreconditionExplanation] = []
    for precondition in (procedure.get("preconditions") or []):
        subject = precondition.get("subject")
        predicate = precondition.get("predicate")
        expected_object = precondition.get("object")
        if not subject:
            # Same posture check_hard_constraints takes for a malformed
            # entry: not this function's job to validate authoring, but
            # it plainly cannot be satisfied without a subject.
            explanations.append(PreconditionExplanation(
                subject=subject, predicate=predicate, expected_object=expected_object,
                satisfied=False, supporting_claims=[],
            ))
            continue

        claims = await project_state(pool, subjects=[subject], as_of=as_of, scope=access_scope)
        matching = [
            c for c in claims
            if c["predicate"] == predicate and c["object"] == expected_object
        ]
        evidence: list[dict] = []
        for claim in matching:
            evidence.extend(await get_claim_evidence(pool, claim["id"]))
        explanations.append(PreconditionExplanation(
            subject=subject, predicate=predicate, expected_object=expected_object,
            satisfied=bool(matching), supporting_claims=matching, evidence=evidence,
        ))

    return ExplainResult(procedure_row_id=str(procedure_row_id), preconditions=explanations)


# ---------------------------------------------------------------------------
# RESEARCH
# ---------------------------------------------------------------------------


@dataclass
class RelationHop:
    edge_id: str
    relation: str
    hop: int
    from_claim_id: str
    to_claim_id: str
    properties: dict


@dataclass
class ResearchResult:
    claim_id: str
    supporting: list[RelationHop] = field(default_factory=list)
    contradicting: list[RelationHop] = field(default_factory=list)
    other: list[RelationHop] = field(default_factory=list)


def _record_hop(
    row: dict, *, hop: int, claim_id: str,
    supporting: list[RelationHop], contradicting: list[RelationHop], other: list[RelationHop],
    seen_edge_ids: set[str],
) -> Optional[str]:
    """Append `row` (a get_claim_relations result) to the right bucket,
    tagged with which hop produced it. Returns the id of the OTHER claim
    on this edge (not `claim_id` itself) so the caller can extend the
    hop-2 frontier, or None if this edge id was already recorded (a
    claim reachable via two different hop-1 edges must not be queried
    twice)."""
    edge_id = str(row["id"])
    if edge_id in seen_edge_ids:
        return None
    seen_edge_ids.add(edge_id)

    relation = row["relation"]
    source_id = str(row["source_id"])
    target_id = str(row["target_id"])
    entry = RelationHop(
        edge_id=edge_id, relation=relation, hop=hop,
        from_claim_id=source_id, to_claim_id=target_id,
        properties=dict(row.get("properties") or {}),
    )
    bucket = _bucket_for(relation)
    if bucket == "supporting":
        supporting.append(entry)
    elif bucket == "contradicting":
        contradicting.append(entry)
    else:
        other.append(entry)

    other_claim_id = target_id if source_id == str(claim_id) else source_id
    return other_claim_id if other_claim_id != str(claim_id) else None


async def research(
    pool: asyncpg.Pool,
    claim_id: str,
    *,
    max_hops: int = MAX_RESEARCH_HOPS,
) -> ResearchResult:
    """
    "What do we know that's relevant to this claim?"

    A bounded, 2-hop-max read built entirely out of
    `claims.get_claim_relations` (the documented single-hop primitive):
    hop 1 is every relation touching `claim_id` directly; hop 2 is one
    more `get_claim_relations` call per DISTINCT claim hop 1 reached
    (never per-edge, so a claim reachable by several hop-1 edges is only
    queried once). Both bounds -- the fixed 2-hop cap and the dedup by
    claim id -- are real bounds, not a style choice: this is explicitly
    NOT a general BFS/graph-traversal implementation, per the directive
    this work comes from.
    """
    if max_hops < 1:
        raise ValueError("max_hops must be >= 1")
    if max_hops > MAX_RESEARCH_HOPS:
        raise ValueError(
            f"max_hops={max_hops} exceeds the hard-coded cap of "
            f"{MAX_RESEARCH_HOPS} -- unbounded/deeper traversal is not "
            "supported by this module"
        )

    supporting: list[RelationHop] = []
    contradicting: list[RelationHop] = []
    other: list[RelationHop] = []
    seen_edge_ids: set[str] = set()

    hop1 = await get_claim_relations(pool, claim_id, direction="both")
    frontier: set[str] = set()
    for row in hop1:
        neighbour = _record_hop(
            row, hop=1, claim_id=claim_id,
            supporting=supporting, contradicting=contradicting, other=other,
            seen_edge_ids=seen_edge_ids,
        )
        if neighbour is not None:
            frontier.add(neighbour)

    if max_hops >= 2:
        for neighbour_claim_id in frontier:
            hop2 = await get_claim_relations(pool, neighbour_claim_id, direction="both")
            for row in hop2:
                _record_hop(
                    row, hop=2, claim_id=neighbour_claim_id,
                    supporting=supporting, contradicting=contradicting, other=other,
                    seen_edge_ids=seen_edge_ids,
                )

    return ResearchResult(
        claim_id=str(claim_id), supporting=supporting,
        contradicting=contradicting, other=other,
    )


# ---------------------------------------------------------------------------
# DECIDE
# ---------------------------------------------------------------------------


@dataclass
class DecideResult:
    procedure_row_id: str
    applicability: ApplicabilityResult
    # Populated only when applicability failed specifically on a
    # precondition: {"subject", "predicate", "expected_object", "claims"}
    # -- the claims that DO exist for that subject (whatever they say),
    # so the caller sees the closest real belief instead of just "failed".
    closest_precondition_claims: Optional[dict[str, Any]] = None


async def _closest_precondition_claims(
    pool: asyncpg.Pool, procedure: dict, *,
    access_scope: Optional[AccessScope], as_of: datetime,
) -> Optional[dict[str, Any]]:
    """
    Re-walks the SAME bounded, authored precondition list
    check_hard_constraints iterates, in the same order, using the SAME
    subject -> claims lookup EXPLAIN uses (`project_state`). Stops at
    the first unsatisfied precondition -- identical short-circuit
    semantics to check_hard_constraints, so when it failed on a
    precondition this reproduces exactly which one, without re-running
    (or reimplementing) the temporal/staleness/scope/invariant stages
    that already ran once in check_hard_constraints itself.
    """
    for precondition in (procedure.get("preconditions") or []):
        subject = precondition.get("subject")
        predicate = precondition.get("predicate")
        expected_object = precondition.get("object")
        if not subject:
            continue
        claims = await project_state(pool, subjects=[subject], as_of=as_of, scope=access_scope)
        satisfied = any(
            c["predicate"] == predicate and c["object"] == expected_object for c in claims
        )
        if not satisfied:
            return {
                "subject": subject,
                "predicate": predicate,
                "expected_object": expected_object,
                "claims": claims,
            }
    return None


async def decide(
    pool: asyncpg.Pool,
    procedure_row_id: str,
    current_scope: dict,
    access_scope: Optional[AccessScope] = None,
    *,
    require_verified: bool = True,
    as_of: Optional[datetime] = None,
    invariant_bindings: Optional[dict[str, float]] = None,
) -> DecideResult:
    """
    "Does this procedure apply here, and if not, why not?"

    A thin, honest wrapper: fetches the procedure row and calls the
    REAL `applicability.check_hard_constraints` cascade, returning its
    result verbatim. The only value-add here is diagnostic: when the
    cascade's first (short-circuiting) failure was a precondition, this
    looks up which claims -- if any -- exist for that precondition's
    subject, via the exact mechanism EXPLAIN uses, so the caller sees
    the closest real belief instead of a bare "failed" verdict.
    """
    procedure = await get_procedure(pool, procedure_row_id)
    if procedure is None:
        raise ValueError(f"no procedure row with id={procedure_row_id!r}")

    as_of = as_of or datetime.now(timezone.utc)
    result = await check_hard_constraints(
        pool, procedure,
        current_scope=current_scope,
        access_scope=access_scope,
        require_verified=require_verified,
        as_of=as_of,
        invariant_bindings=invariant_bindings,
    )

    closest = None
    if (
        not result.applicable
        and result.failed_constraints
        and result.failed_constraints[0].startswith("precondition:")
    ):
        closest = await _closest_precondition_claims(
            pool, procedure, access_scope=access_scope, as_of=as_of,
        )

    return DecideResult(
        procedure_row_id=str(procedure_row_id),
        applicability=result,
        closest_precondition_claims=closest,
    )
