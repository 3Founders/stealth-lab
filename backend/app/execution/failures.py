"""
Band 2.4 -- the §36 failure-routing boundary: classify a failed
execution's evidence row into its cause, resolve the cause's mandated
update, and record that decision durably so the owning subsystem can
execute it.

The storage half is db/27_failure_routing.sql (failure_routes [H]
append-only with a tombstone exception, UNIQUE (evidence_id, route),
NULL-class-iff-requires_review engine CHECK); this module is the
boundary half, mirroring app/execution/evidence.py's role for db/24.

THE ROUTING TABLE (spec v4 §36, verbatim; false_reuse's placement is
this module's one judgment call, see FALSE_REUSE_ROUTE):

    procedure_wrong      -> procedure_version_candidate   new version of
                                                          the procedure
    implementation_wrong -> capability_demotion           on THAT
                                                          implementation
    environment_changed  -> dependency_queue              dependent claims
                                                          re-checked
    input_abnormal       -> applicability_narrowing       scope/exclusion
                                                          update
    verification_wrong   -> plan_revision                 verification-plan
                                                          revision
    external_failure     -> no_op                         no knowledge
                                                          update, by
                                                          mandate
    false_reuse          -> applicability_narrowing       the reuse gate
                                                          admitted a
                                                          failure -- the
                                                          match was wrong,
                                                          narrow it
    (class NULL)         -> requires_review               nobody classified
                                                          this; that is
                                                          itself a finding

WHY RECORD-INSTEAD-OF-EXECUTE: the mandated updates live in different
owners' code (procedure revision -> extraction; demotion -> capability
stream computation over evidence; narrowing -> applicability; plan
revision -> execution plans). What this lane owes the system is the
durable, auditable, idempotent QUEUE those owners consume --
side-effect-at-classify would be none of those things. Handlers land
with their owners; every decision here is already queryable and
retractable meanwhile.

Pure functions + thin pool calls only: classification and route
resolution are provable offline; the pool calls are single-statement
INSERT/SELECT with no transaction nesting, same discipline as
claim_sources' writer.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Optional

import asyncpg

ROUTER_NAME = "failure_router"
ROUTER_VERSION = "1"
ROUTER_STAMP = f"{ROUTER_NAME}@{ROUTER_VERSION}"

# The seven §36 causes exactly as db/24's evidence_failure_class_values_chk
# spells them (imported vocabulary, not re-spelled: drift breaks the import).
FAILURE_CLASSES: tuple[str, ...] = (
    "procedure_wrong",
    "implementation_wrong",
    "environment_changed",
    "input_abnormal",
    "verification_wrong",
    "external_failure",
    "false_reuse",
)

# The mandated update per cause. Values are the exact vocabulary
# db/27_failure_routing.sql's failure_route_values_chk enforces -- the
# static-equality test pins Python and DDL to the same tuple.
ROUTE_VALUES: tuple[str, ...] = (
    "procedure_version_candidate",
    "capability_demotion",
    "dependency_queue",
    "applicability_narrowing",
    "plan_revision",
    "no_op",
    "requires_review",
)

FAILURE_ROUTES: dict[str, str] = {
    "procedure_wrong": "procedure_version_candidate",
    "implementation_wrong": "capability_demotion",
    "environment_changed": "dependency_queue",
    "input_abnormal": "applicability_narrowing",
    "verification_wrong": "plan_revision",
    "external_failure": "no_op",
}

UNCLASSIFIED_ROUTE = "requires_review"

# The one judgment call in the table, named so it can be argued with:
# spec §36 lists false_reuse as a cause but assigns it no route, and the
# assignment's routing table doesn't either. A false reuse means the
# reuse GATE admitted something that then failed -- the match was the
# bug, so the mandate-shaped answer is an applicability/scope narrowing
# of what contexts may claim this procedure. Named constant, not an
# inline literal, precisely so a future ruling changes ONE line.
FALSE_REUSE_ROUTE = "applicability_narrowing"


class NotClassifiable(ValueError):
    """The row is not a failed outcome -- successes carry no cause,
    needs_rework carries no TERMINAL cause, witness rows carry no
    outcome at all. Callers surface this verbatim: feeding it a
    non-failure is a caller bug, not a routing decision."""


@dataclass(frozen=True)
class FailureClassification:
    """One evidence row read as a §36 classification: what failed, on
    which target, under which context, and the route its cause mandates."""

    evidence_id: str
    failure_class: Optional[str]          # None == unclassified, by design
    route: str
    target_type: str
    target_id: str
    target_version: Optional[int]
    context_key: Optional[str]
    independence_group: Optional[str]

    @property
    def unclassified(self) -> bool:
        return self.failure_class is None


def classify_failure(evidence_row: Mapping[str, Any]) -> FailureClassification:
    """Resolve one stored evidence row to its §36 route. Pure.

    Accepts anything Mapping-like (asyncpg Record, dict,
    models.evidence.Evidence.model_dump()). Raises NotClassifiable for
    any row that is not a terminal FAILURE -- classification exists only
    where a cause can exist (§36's pipeline starts at Failure, not at
    Outcome)."""
    row = dict(evidence_row)
    if row.get("outcome_status") != "failure":
        raise NotClassifiable(
            f"only failed outcomes classify (spec §36); got "
            f"outcome_status={row.get('outcome_status')!r}"
        )

    failure_class = row.get("failure_class")
    if failure_class is None:
        # Unclassified is itself a routing decision (db/24's header
        # note): nobody named a cause, so no mandated update may fire
        # -- a human looks at it instead.
        route = UNCLASSIFIED_ROUTE
    elif failure_class in FAILURE_ROUTES:
        route = FAILURE_ROUTES[failure_class]
    elif failure_class == "false_reuse":
        route = FALSE_REUSE_ROUTE
    else:
        raise NotClassifiable(
            f"unknown failure_class {failure_class!r} (valid: {FAILURE_CLASSES})"
        )

    return FailureClassification(
        evidence_id=str(row["id"]),
        failure_class=failure_class,
        route=route,
        target_type=str(row.get("target_type")),
        target_id=str(row.get("target_id")),
        target_version=row.get("target_version"),
        context_key=row.get("context_key"),
        independence_group=row.get("independence_group"),
    )


def build_payload(classification: FailureClassification) -> dict[str, Any]:
    """The typed per-route contents the consuming owner needs beyond
    this row's own columns. Kept minimal ON PURPOSE: everything already
    on the routing row (evidence link, class, target triple) is NOT
    duplicated here -- payload carries only what the columns cannot say."""
    payload: dict[str, Any] = {}
    if classification.route == "capability_demotion":
        # Demotion is computed over the implementation's evidence stream
        # (compute_capability); the capped-independence group of THIS
        # failure is an aggregation input the consumer must respect.
        if classification.independence_group is not None:
            payload["independence_group"] = classification.independence_group
    if classification.context_key is not None:
        # Narrowing learns WHERE it failed; dependency re-checks learn
        # under which context the environment drifted.
        payload["failed_context_key"] = classification.context_key
    return payload


async def record_routing(
    pool: asyncpg.Pool, classification: FailureClassification,
) -> Optional[str]:
    """Append the routing decision. Idempotent by the table's
    UNIQUE (evidence_id, route): a second call for an already-routed
    failure returns None instead of double-firing the mandate. Returns
    the new failure_routes.id otherwise."""
    return await pool.fetchval(
        """
        INSERT INTO failure_routes
            (evidence_id, failure_class, route, payload, routed_by)
        VALUES ($1::uuid, $2, $3, $4::jsonb, $5)
        ON CONFLICT (evidence_id, route) DO NOTHING
        RETURNING id
        """,
        classification.evidence_id,
        classification.failure_class,
        classification.route,
        _payload_json(classification),
        ROUTER_STAMP,
    )


def _payload_json(classification: FailureClassification) -> str:
    import json

    return json.dumps(build_payload(classification))


@dataclass(frozen=True)
class RoutingOutcome:
    """What classify_and_route did: the classification, and whether THIS
    call appended the row (route_row_id set) or found it already routed
    (already_routed True). Both set never happens; both unset means the
    caller re-routed to a DIFFERENT route than a previous generation
    did -- legal by design (append supersedes), surfaced so callers can
    tell the cases apart."""

    classification: FailureClassification
    route_row_id: Optional[str]
    already_routed: bool


async def classify_and_route(
    pool: asyncpg.Pool, evidence_row: Mapping[str, Any],
) -> RoutingOutcome:
    """The one-call API the evidence writer uses inside its own
    transaction shape: classify a failed-outcome row, append its route.
    See cross-lane request #1 -- when evidence writes wire into the
    lifecycle path, THIS is the call that goes next to them."""
    classification = classify_failure(evidence_row)
    route_row_id = await record_routing(pool, classification)
    return RoutingOutcome(
        classification=classification,
        route_row_id=route_row_id,
        already_routed=route_row_id is None,
    )


# --------------------------------------------------------------- readers


async def fetch_unrouted_failures(
    pool: asyncpg.Pool, *, limit: int = 500,
) -> list[dict]:
    """Failed evidence rows with NO routing decision yet -- the sweep
    half of the contract. Classification lagging behind storage must be
    VISIBLE, not silent: this query is how the lag is seen (and how a
    periodic sweeper finds its work)."""
    return [
        dict(r) for r in await pool.fetch(
            """
            SELECT e.id, e.evidence_type, e.target_type, e.target_id,
                   e.target_version, e.outcome_status, e.failure_class,
                   e.context_key, e.independence_group
            FROM evidence e
            LEFT JOIN failure_routes fr ON fr.evidence_id = e.id
            WHERE e.outcome_status = 'failure'
              AND e.t_invalid IS NULL
              AND fr.id IS NULL
            ORDER BY e.t_created ASC
            LIMIT $1
            """,
            limit,
        )
    ]


async def fetch_route_queue(
    pool: asyncpg.Pool, route: str, *, limit: int = 500,
) -> list[dict]:
    """Live decisions awaiting their owner's execution, oldest first.
    `no_op` rows are readable here too -- 'we looked and deliberately
    did nothing' should be as auditable as any other decision."""
    if route not in ROUTE_VALUES:
        raise ValueError(f"unknown route {route!r} (valid: {ROUTE_VALUES})")
    return [
        dict(r) for r in await pool.fetch(
            """
            SELECT fr.id, fr.evidence_id, fr.failure_class, fr.route,
                   fr.payload, fr.routed_by, fr.t_created,
                   e.target_type, e.target_id, e.target_version
            FROM failure_routes fr
            JOIN evidence e ON e.id = fr.evidence_id
            WHERE fr.route = $1
              AND fr.t_invalid IS NULL
            ORDER BY fr.t_created ASC
            LIMIT $2
            """,
            route, limit,
        )
    ]
