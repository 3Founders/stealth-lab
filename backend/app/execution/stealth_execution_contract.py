"""
MCP hardening B4: the Stealth Execution Contract -- the spec's own named
chain:

    RUN_CREATED -> DISCOVERY -> PROCEDURE_EVALUATED -> APPLICABILITY_CHECKED
    -> PROCEDURE_VERSION_PINNED -> IMPLEMENTATION_PINNED -> EXECUTION_STARTED
    -> EXECUTION_EVENTS -> VERIFICATION -> OUTCOME -> EVIDENCE -> FINALIZED

"A run cannot claim Stealth procedural provenance without this chain.
Every transition MUST be persisted transactionally with an idempotency
key/version guard. Invalid transitions MUST fail closed with a typed
error; they must not be silently coerced to the nearest valid state."

WHY A DERIVED VIEW, NOT A NEW MUTABLE STATE COLUMN (CLAUDE.md rule 2: no
parallel architectures): every one of these 12 states already corresponds
to a REAL fact this codebase persists transactionally, in its own
existing table, guarded by its own existing constraint:

  RUN_CREATED              execution_runs row exists
  DISCOVERY                route_decisions row exists for this run
                            (persist_route_decision, its own insert)
  PROCEDURE_EVALUATED      that route_decision names a procedure_id
  APPLICABILITY_CHECKED    that route_decision's `applicable` is not NULL
                            (diagnose_candidates always sets it either way)
  PROCEDURE_VERSION_PINNED execution_runs.procedure_version (NOT NULL,
                            set once at INSERT, migration 36's own schema)
  IMPLEMENTATION_PINNED    >=1 execution_run_nodes row has a real
                            implementation_id (host-executed nodes may
                            legitimately never pin one -- optional, not
                            skipped-in-error)
  EXECUTION_STARTED        execution_runs.status has left 'pending'
                            (enforced today by B4's OWN status-transition
                            fence, migration 60, added earlier this pass)
  EXECUTION_EVENTS         >=1 execution_run_events row exists (B7/B8's
                            durable, append-only log)
  VERIFICATION             >=1 verification_results row exists for this
                            run (B34's ladder)
  OUTCOME                  execution_runs.final_outcome is NOT NULL
  EVIDENCE                 >=1 evidence row with target_type='execution'
                            AND target_id = execution_runs.final_execution_id
                            (the real, pre-existing linkage pattern --
                            confirmed live via grep of every INSERT INTO
                            evidence call site, not guessed)
  FINALIZED                execution_runs.status IN ('succeeded','failed')
                            AND final_execution_id IS NOT NULL (migration
                            36's own terminal_chk CHECK constraint --
                            already enforces this pairing transactionally)

A second, independently-mutated `stealth_execution_state` column would
only be able to drift from these real facts (exactly the "field nobody
keeps in sync" antipattern). This module computes the chain honestly
from what's already there -- "invalid transitions fail closed" is
already true of every step (you cannot get a FINALIZED executions row
without procedure_version pinned first: it's a NOT NULL column set at
INSERT; you cannot get a verification_results row without a real
execution_run_id FK). What was missing, and what this module adds, is
making that chain OBSERVABLE as one ordered, testable value -- "routing
becomes observable and testable" is B2's own phrase, extended here to
the whole run lifecycle.

Some states are legitimately OPTIONAL for a given run (a `plan_only`
run never reaches IMPLEMENTATION_PINNED; a procedure with no
postconditions never reaches VERIFICATION) -- `reached` lists exactly
which states this run's real facts satisfy, in chain order, with
`current_state` naming the FURTHEST one reached. `skipped` names states
between the furthest-reached one and the next one that are NOT
satisfied, so a caller can tell "optional, not yet reached" apart from
"blocked".
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

CHAIN: tuple[str, ...] = (
    "RUN_CREATED",
    "DISCOVERY",
    "PROCEDURE_EVALUATED",
    "APPLICABILITY_CHECKED",
    "PROCEDURE_VERSION_PINNED",
    "IMPLEMENTATION_PINNED",
    "EXECUTION_STARTED",
    "EXECUTION_EVENTS",
    "VERIFICATION",
    "OUTCOME",
    "EVIDENCE",
    "FINALIZED",
)


async def compute_execution_contract_state(
    pool: asyncpg.Pool, execution_run_id: str,
) -> Optional[dict[str, Any]]:
    """Returns None if the run does not exist. Otherwise a dict:
    {"reached": [...ordered state names...], "current_state": str,
     "skipped_optional": [...state names between the furthest transactional
     fact and the next one, not yet or never satisfied...]}."""
    run = await pool.fetchrow(
        "SELECT status, procedure_version, final_outcome, final_execution_id, route_decision_id "
        "FROM execution_runs WHERE id = $1", execution_run_id,
    )
    if run is None:
        return None

    reached: list[str] = ["RUN_CREATED"]

    route_decision = None
    if run["route_decision_id"] is not None:
        route_decision = await pool.fetchrow(
            "SELECT procedure_id, applicable FROM route_decisions WHERE id = $1",
            run["route_decision_id"],
        )
    if route_decision is not None:
        reached.append("DISCOVERY")
        if route_decision["procedure_id"] is not None:
            reached.append("PROCEDURE_EVALUATED")
        if route_decision["applicable"] is not None:
            reached.append("APPLICABILITY_CHECKED")

    if run["procedure_version"] is not None:
        reached.append("PROCEDURE_VERSION_PINNED")

    has_implementation = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM execution_run_nodes "
        "WHERE execution_run_id = $1 AND implementation_id IS NOT NULL)",
        execution_run_id,
    )
    if has_implementation:
        reached.append("IMPLEMENTATION_PINNED")

    if run["status"] != "pending":
        reached.append("EXECUTION_STARTED")

    has_events = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM execution_run_events WHERE execution_run_id = $1)",
        execution_run_id,
    )
    if has_events:
        reached.append("EXECUTION_EVENTS")

    has_verification = await pool.fetchval(
        "SELECT EXISTS(SELECT 1 FROM verification_results WHERE execution_run_id = $1::uuid)",
        execution_run_id,
    )
    if has_verification:
        reached.append("VERIFICATION")

    if run["final_outcome"] is not None:
        reached.append("OUTCOME")

    has_evidence = False
    if run["final_execution_id"] is not None:
        has_evidence = await pool.fetchval(
            "SELECT EXISTS(SELECT 1 FROM evidence "
            "WHERE target_type = 'execution' AND target_id = $1)",
            run["final_execution_id"],
        )
    if has_evidence:
        reached.append("EVIDENCE")

    if run["status"] in ("succeeded", "failed") and run["final_execution_id"] is not None:
        reached.append("FINALIZED")

    furthest_index = max(CHAIN.index(s) for s in reached)
    skipped_optional = [s for s in CHAIN[:furthest_index + 1] if s not in reached]

    return {
        "reached": reached,
        "current_state": reached[-1],
        "skipped_optional": skipped_optional,
    }
