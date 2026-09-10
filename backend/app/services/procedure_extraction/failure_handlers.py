"""
Band 2.4 completion -- the four §36 failure-route HANDLERS: the
record-don't-execute half of app/execution/failures.py finally gets its
executors. CORE-A's routing boundary owes the durable, auditable,
idempotent QUEUE (db/27 failure_routes); this module consumes that queue
via fetch_route_queue() and performs each route's mandated update on the
objects this lane owns or their existing generic storage.

The four handlers, one per mandated update (spec v4 §36 verbatim):

    capability_demotion       -> recompute the implementation's
                                 capability from its cumulative evidence
                                 stream via procedure_extraction/
                                 capability.py -- demotion falls out of
                                 recomputation over the stream (the same
                                 bidirectionality capability_trajectory()
                                 proves), then the recomputed verdict is
                                 recorded durably.
    applicability_narrowing   -> edit the procedure's rule detail: the
                                 failed context becomes a machine-
                                 writable EXCLUSION entry (ticket 12's
                                 "scope and exclusions... machine-
                                 writable"), which applicability.py's
                                 existing cascade consumes on the next
                                 match attempt.
    dependency_queue          -> flag the claims DERIVED from
                                 observations extracted in the drifted
                                 context for environment-triggered
                                 revalidation (§37's D3 posture).
    requires_review           -> stamp claim_status='uncertain' on the
                                 targeted claim: nobody classified the
                                 failure, so its belief cannot stay
                                 untouched either.

HONEST SCOPE / NAMED JUDGMENT CALLS (each a named constant, argued here,
retunable by monkeypatch exactly like FALSE_REUSE_ROUTE):

- IDEMPOTENCY LEDGER: every handler gates its write on ONE query --
  "does a change_sets row with reason 'failure_route:<route>:<id>'
  already exist?" -- because db/25's change_sets are [H] append-only:
  a reason present there is permanent proof the mandate fired. The
  failure_routes queue itself stays untouched (it has no processed
  column and its tombstone means RETRACTED, not executed); consuming a
  queue twice therefore performs exactly one update.
- DEPENDENTS RESOLUTION: §20 wants an indexed dependency graph; today
  exactly one derivation index exists -- claim_sources (Band 2.8). So
  "derived claims" = claims reachable claim_sources <- observations
  where the observation was extracted in the FAILED CONTEXT
  (observations.properties->>'context_key', ticket 13's context
  vocabulary end to end). Claims without provenance rows are not
  flagged; richer dependency indexing is Band 4's, not invented here.
- REVIEW STAMP VALUE: 'uncertain' -- an UNCLASSIFIED failure means the
  cause is unknown, which is uncertainty about the claim, not dispute;
  both values come from migration 21's kn_claim_status_chk vocabulary,
  statically pinned by test.
- CAPABILITY PERSISTENCE: capability.py is pure BY DESIGN ([D] storage
  is CORE-A's deferred territory) and no implementations table exists,
  so the demotion verdict is recorded as a ChangeSet operation against
  the TRIGGERING evidence row (detail carries the full recomputed
  record) -- durable, append-only, auditable, zero new tables.
- Every [V] mutation these handlers perform goes through
  changeset_record.record_change_set() (Band 1.9c invariant #7), with
  author=HANDLER_STAMP: handlers run from sweepers, not requests.

Pure functions + single-statement pool calls only -- no transaction
nesting, same discipline as failures.py itself.
"""
from __future__ import annotations

import json
import logging
from typing import Any, Awaitable, Callable, Mapping, Optional

import asyncpg

_LOG = logging.getLogger(__name__)

# READ-IMPORT ONLY per the scoped grant: failures.py is CORE-A's file.
from app.execution.failures import (
    ROUTE_VALUES,
    fetch_route_queue,
    fetch_unrouted_failures,
)
from app.services.changeset_record import ChangeOperation, record_change_set
from app.services.procedure_extraction.capability import (
    CapabilityRecord,
    CapabilityScope,
    OutcomeRecord,
    compute_capability,
)

HANDLER_NAME = "failure_handlers"
HANDLER_VERSION = "1"
HANDLER_STAMP = f"{HANDLER_NAME}@{HANDLER_VERSION}"

# The idempotency ledger key prefix inside change_sets.reason. Full
# reason = f"{LEDGER_PREFIX}{route}:{failure_route_row_id}".
LEDGER_PREFIX = "failure_route:"

# Routes this lane executes, mapped to their handler coroutines. Keys are
# pinned to ROUTE_VALUES by test; plan_revision/procedure_version_candidate/
# no_op land with their owners (plans/extraction/no-one), never here.
HANDLED_ROUTES: dict[str, Callable[..., Awaitable[bool]]] = {}

# ---- named config -----------------------------------------------------

# Demotion reads the SAME stream discipline db/24's
# procedure_evidence_stats view counts attempts under, so the Python
# computation and the SQL statistics can never disagree about what an
# attempt is.
DEMOTION_EVIDENCE_TYPES: tuple[str, ...] = ("execution_result", "reproduction")
DEMOTION_STREAM_LIMIT = 5000

# context_key may be unrecorded on old rows; capability still needs a
# non-blank environment per OutcomeRecord. A named sentinel beats a
# fabricated context.
UNRECORDED_ENVIRONMENT = "(unrecorded)"

# The exclusion dimension the narrowing handler writes -- ticket 13's own
# context vocabulary, consumed by applicability._excluded() unchanged.
NARROWING_SCOPE_KEY = "context_key"

# claim_status vocabulary (migration 21's kn_claim_status_chk).
#
# B9 ownership boundary: `candidate | supported | disputed | uncertain` are
# a DERIVED PROJECTION of belief + conflict (claim_belief.status_from_belief)
# -- no handler hand-stamps them. `stale | superseded | invalid | retracted`
# are LIFECYCLE transitions that this module and relate_claims own directly.
#
# So handle_dependency_queue sets 'stale' (a lifecycle transition: the
# claim's extraction environment drifted -> it must be revalidated), while
# handle_requires_review no longer sets 'uncertain' -- it records a
# non-status `properties.review` marker and calls recompute_claim_belief.
REVALIDATION_CLAIM_STATUS = "stale"  # lifecycle: environment-drift revalidation

# Batch ceiling for the dependency fan-out; mirrors fetch_route_queue's
# default page size so one consume flags at most one page of dependents.
DEPENDENT_CLAIMS_LIMIT = 500

DEFAULT_QUEUE_LIMIT = 500


def ledger_reason(route: str, failure_route_id: str) -> str:
    """The deterministic change_sets.reason proving this mandate fired."""
    return f"{LEDGER_PREFIX}{route}:{failure_route_id}"


async def _already_applied(
    pool: asyncpg.Pool, route: str, failure_route_id: str,
) -> bool:
    """True iff this exact queue row has been executed before. One SELECT
    against the append-only ChangeSet log -- [H] means the answer can
    never be retroactively erased, which is what makes re-consuming a
    queue row a guaranteed no-op rather than a usually-no-op."""
    return bool(await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM change_sets WHERE reason = $1)",
        ledger_reason(route, failure_route_id),
    ))


def _payload_dict(route_row: Mapping[str, Any]) -> dict[str, Any]:
    payload = route_row.get("payload")
    if isinstance(payload, str):          # direct-SQL writers may pass text
        try:
            payload = json.loads(payload)
        except (TypeError, ValueError):
            payload = {}
    return dict(payload) if isinstance(payload, Mapping) else {}


def _route_id(route_row: Mapping[str, Any]) -> str:
    rid = route_row.get("id")
    if rid is None:
        raise ValueError("queue row without failure_routes.id -- not consumable")
    return str(rid)


# ---------------------------------------------------------------------
# 1. capability_demotion -- "capability demotion on THAT implementation"
# ---------------------------------------------------------------------


def capability_for_stream(
    target_type: str,
    target_id: str,
    outcome_rows: list[Mapping[str, Any]],
) -> CapabilityRecord:
    """Recompute the capability record over an implementation's cumulative
    outcome stream -- the demotion half of capability_trajectory()'s
    replay: appended failures lower the Wilson lower bound, later
    successes raise it again, and the LEVEL follows P through the D1
    bands. Pure; the handler calls this so tests can prove the drop
    without any database at all."""
    outcomes = [
        OutcomeRecord(
            success=(row["outcome_status"] == "success"),
            environment=row["context_key"] or UNRECORDED_ENVIRONMENT,
            independence_group=row.get("independence_group"),
        )
        for row in outcome_rows
    ]
    scope = CapabilityScope(
        task=f"{target_type}:{target_id}",
        state_signature="(cumulative evidence stream)",
        environment="(aggregated over recorded contexts)",
        input_signature="(all recorded inputs)",
        evaluation_criterion="outcome_status == 'success'",
    )
    return compute_capability(outcomes, scope)


async def handle_capability_demotion(
    pool: asyncpg.Pool, route_row: Mapping[str, Any],
) -> bool:
    """implementation_wrong's mandate. Reads the target implementation's
    outcome-bearing evidence stream, recomputes capability from scratch
    (demotion is recomputation, never a decrement), and records the new
    verdict durably. Returns True iff THIS call performed the update."""
    route = route_row["route"]
    fr_id = _route_id(route_row)
    if await _already_applied(pool, route, fr_id):
        return False

    target_type = str(route_row["target_type"])
    target_id = str(route_row["target_id"])
    types_sql = ", ".join(f"'{t}'" for t in DEMOTION_EVIDENCE_TYPES)
    stream = await pool.fetch(
        f"""
        SELECT outcome_status, context_key, independence_group
        FROM evidence
        WHERE target_type = $1
          AND target_id = $2::uuid
          AND t_invalid IS NULL
          AND evidence_type IN ({types_sql})
          AND outcome_status IN ('success', 'failure')
        ORDER BY t_created ASC, id ASC
        LIMIT {int(DEMOTION_STREAM_LIMIT)}
        """,
        target_type, target_id,
    )
    record = capability_for_stream(target_type, target_id, stream)

    await record_change_set(
        pool,
        author=HANDLER_STAMP,
        reason=ledger_reason(route, fr_id),
        operations=[ChangeOperation(
            operation="status_change",
            target_table="evidence",
            target_id=str(route_row["evidence_id"]),
            detail={
                "capability_verdict": {
                    "subject": {"target_type": target_type, "target_id": target_id},
                    "p_lower": record.p_lower,
                    "p_estimate": record.p_estimate,
                    "level": record.level,
                    "level_label": record.level_label,
                    "routing": record.routing.value
                    if hasattr(record.routing, "value") else str(record.routing),
                    "evidence_count": record.evidence_count,
                    "success_count": record.success_count,
                },
                "trigger": {
                    "failure_class": route_row.get("failure_class"),
                    "independence_group": (_payload_dict(route_row)
                                            .get("independence_group")),
                },
            },
        )],
    )
    return True


# ---------------------------------------------------------------------
# 2. applicability_narrowing -- "scope/exclusion update" for
#    input_abnormal (+ false_reuse, failures.py's named judgment call)
# ---------------------------------------------------------------------


async def handle_applicability_narrowing(
    pool: asyncpg.Pool, route_row: Mapping[str, Any],
) -> bool:
    """input_abnormal's mandate: edit the RULE DETAIL -- the procedure's
    exclusions gain one structured entry carrying the context that just
    failed abnormally, so the next match attempt in that context is
    disqualified by applicability.py's hard-constraint cascade instead of
    being re-admitted. Non-procedure targets have nothing narrowable and
    are skipped honestly (the queue row stays the audit record)."""
    route = route_row["route"]
    fr_id = _route_id(route_row)
    if await _already_applied(pool, route, fr_id):
        return False

    if route_row["target_type"] != "procedure":
        return False
    failed_context = _payload_dict(route_row).get("failed_context_key")
    if not failed_context:
        # Without a context there is nothing to narrow -- inventing a
        # blank exclusion would ban the procedure EVERYWHERE.
        return False

    proc_id = str(route_row["target_id"])
    row = await pool.fetchrow(
        "SELECT exclusions FROM procedures "
        "WHERE id = $1::uuid AND t_invalid IS NULL",
        proc_id,
    )
    if row is None:
        return False

    entries = list(row["exclusions"] or [])
    entries.append({
        "key": NARROWING_SCOPE_KEY,
        "values": [failed_context],
        "_narrowed_by": HANDLER_STAMP,
        "_source_route_id": fr_id,
        "_failure_class": route_row.get("failure_class"),
    })
    await pool.execute(
        "UPDATE procedures SET exclusions = $2::jsonb, updated_at = now() "
        "WHERE id = $1::uuid AND t_invalid IS NULL",
        proc_id, json.dumps(entries),
    )
    await record_change_set(
        pool,
        author=HANDLER_STAMP,
        reason=ledger_reason(route, fr_id),
        operations=[ChangeOperation(
            operation="revise",
            target_table="procedures",
            target_id=proc_id,
            detail={"added_exclusion": entries[-1]},
        )],
    )
    return True


# ---------------------------------------------------------------------
# 3. dependency_queue -- "dependent claims into the dependency queue"
# ---------------------------------------------------------------------

_DERIVED_CLAIMS_SQL = """
SELECT DISTINCT cs.claim_id::text AS claim_id
FROM claim_sources cs
JOIN observations o ON o.id = cs.observation_id
JOIN knowledge_nodes kn ON kn.id = cs.claim_id
WHERE o.properties->>'context_key' = $1
  AND kn.t_invalid IS NULL
  AND kn.properties->>'truth_state' IS DISTINCT FROM 'OUT'
ORDER BY claim_id
LIMIT $2
"""


async def handle_dependency_queue(
    pool: asyncpg.Pool, route_row: Mapping[str, Any],
) -> bool:
    """environment_changed's mandate: the derived claims of the drifted
    context get FLAGGED for revalidation -- claim_status='stale' (§37's
    environment-triggered posture) plus a properties marker naming the
    route that queued them. Truth Maintenance note: truth_state='OUT'
    claims are skipped -- they are already out of belief, there is
    nothing left to stale."""
    route = route_row["route"]
    fr_id = _route_id(route_row)
    if await _already_applied(pool, route, fr_id):
        return False

    failed_context = _payload_dict(route_row).get("failed_context_key")
    if not failed_context:
        return False

    claim_ids = [
        r["claim_id"] for r in await pool.fetch(
            _DERIVED_CLAIMS_SQL, failed_context, DEPENDENT_CLAIMS_LIMIT,
        )
    ]
    if not claim_ids:
        # Nothing derivable yet (promotion may lag the failure) -- leave
        # the mandate queued rather than pretend it fired.
        return False

    marker = {
        "revalidation": {
            "queued_by": HANDLER_STAMP,
            "route": route,
            "route_id": fr_id,
            "failure_class": route_row.get("failure_class"),
            "context_key": failed_context,
            "evidence_id": str(route_row["evidence_id"]),
        },
    }
    await pool.execute(
        "UPDATE knowledge_nodes SET claim_status = $2, "
        "properties = properties || $3::jsonb "
        "WHERE id = ANY($1::uuid[])",
        claim_ids, REVALIDATION_CLAIM_STATUS, json.dumps(marker),
    )
    await record_change_set(
        pool,
        author=HANDLER_STAMP,
        reason=ledger_reason(route, fr_id),
        operations=[
            ChangeOperation(
                operation="status_change",
                target_table="knowledge_nodes",
                target_id=cid,
                detail={
                    "claim_status": REVALIDATION_CLAIM_STATUS,
                    "reason": "environment_changed",
                    "context_key": failed_context,
                },
            )
            for cid in claim_ids
        ],
    )
    return True


# ---------------------------------------------------------------------
# 4. requires_review -- "(class NULL) -> requires_review"
# ---------------------------------------------------------------------


async def handle_requires_review(
    pool: asyncpg.Pool, route_row: Mapping[str, Any],
) -> bool:
    """The unclassified-failure mandate: flag the targeted CLAIM for human
    review, and re-derive its standing from the evidence.

    B9 ownership boundary (`claim_belief.status_from_belief`): the values
    `candidate | supported | disputed | uncertain` are a DERIVED PROJECTION
    of belief + conflict state -- no path hand-stamps them. So instead of
    `SET claim_status = 'uncertain'`, this records a non-status
    `properties.review` marker ("a human is looking at this") and calls
    `recompute_claim_belief`, which re-derives `claim_status` from the
    claim's evidence and any open conflict. A claim with only weak/no
    evidence lands `uncertain` anyway; a claim with strong evidence stays
    `supported` -- flagged for review, but not falsely downgraded.
    (`stale | superseded | invalid | retracted` remain lifecycle
    transitions owned by `handle_dependency_queue` / `relate_claims`.)

    Non-claim targets have no `claim_status`; the routing row itself
    remains the human-review worklist."""
    route = route_row["route"]
    fr_id = _route_id(route_row)
    if await _already_applied(pool, route, fr_id):
        return False

    if route_row["target_type"] != "claim":
        return False

    claim_id = str(route_row["target_id"])
    row = await pool.fetchrow(
        "SELECT claim_status FROM knowledge_nodes "
        "WHERE id = $1::uuid AND t_invalid IS NULL",
        claim_id,
    )
    if row is None:
        return False
    prior = row["claim_status"]

    marker = {
        "review": {
            "requested_by": HANDLER_STAMP,
            "route": route,
            "route_id": fr_id,
            "reason": "unclassified_failure",
            "evidence_id": str(route_row["evidence_id"]),
        },
    }
    # The review marker only -- claim_status is NOT set here.
    await pool.execute(
        "UPDATE knowledge_nodes SET properties = properties || $2::jsonb "
        "WHERE id = $1::uuid AND t_invalid IS NULL",
        claim_id, json.dumps(marker),
    )

    # Re-derive claim_status from evidence + conflict state. recompute
    # records its own evidence-citing ChangeSet; the route's ledger row
    # below is the idempotency proof for _already_applied.
    recomputed_status = None
    try:
        from app.services.claim_belief import recompute_claim_belief

        belief = await recompute_claim_belief(
            pool, claim_id,
            changeset_reason=f"claim flagged for review by failure routing ({route}:{fr_id})",
        )
        recomputed_status = belief.get("claim_status") if isinstance(belief, dict) else None
    except Exception as exc:  # noqa: BLE001 -- best-effort; the review marker + ledger still land
        _LOG.warning("requires_review: belief recompute failed for %s: %s", claim_id, exc)

    await record_change_set(
        pool,
        author=HANDLER_STAMP,
        reason=ledger_reason(route, fr_id),
        operations=[ChangeOperation(
            operation="status_change",
            target_table="knowledge_nodes",
            target_id=claim_id,
            detail={
                "flagged_for_review": True,
                "prior_claim_status": prior,
                "recomputed_claim_status": recomputed_status,
                "reason": "unclassified_failure",
            },
        )],
    )
    return True


HANDLED_ROUTES.update({
    "capability_demotion": handle_capability_demotion,
    "applicability_narrowing": handle_applicability_narrowing,
    "dependency_queue": handle_dependency_queue,
    "requires_review": handle_requires_review,
})


# ---------------------------------------------------------------------
# dispatcher + visibility sweep
# ---------------------------------------------------------------------


async def run_failure_handlers(
    pool: asyncpg.Pool, *, limit: int = DEFAULT_QUEUE_LIMIT,
) -> dict[str, int]:
    """Consume every handled route's queue and execute its mandates.

    Returns {route: updates_performed}. Rows whose update was already
    applied (idempotent re-consume) do not count -- the number is real
    work done, not rows seen. Deliberately NEVER touches
    fetch_unrouted_failures: unrouted failures have NO decision yet, so
    by definition no handler may fire for them; they are visible via
    unclassified_backlog() below and nothing else."""
    applied: dict[str, int] = {}
    for route, handler in HANDLED_ROUTES.items():
        count = 0
        for row in await fetch_route_queue(pool, route, limit=limit):
            if row.get("route") != route:
                raise ValueError(
                    f"queue returned {row.get('route')!r} while consuming {route!r}"
                )
            if await handler(pool, row):
                count += 1
        applied[route] = count
    return applied


async def unclassified_backlog(
    pool: asyncpg.Pool, *, limit: int = DEFAULT_QUEUE_LIMIT,
) -> list[dict]:
    """Classification-lag VISIBILITY, nothing more: the failed evidence
    rows no router has decided yet. Read-only by contract -- a failure
    nobody classified must surface loudly, never silently execute."""
    return await fetch_unrouted_failures(pool, limit=limit)
