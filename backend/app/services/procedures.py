"""
Procedure representation (ticket 05, memory-substrate map). Dedicated
`procedures` table (backend/db/18_procedures.sql) -- resolves the TASK
NODE vs PROCEDURE collapse spec.md's TARGET SEMANTIC SEPARATION forbids.
`task_nodes` remains the execution/planning node for one run;
`procedures` is the reusable, verified capability, structurally distinct
rather than a tag convention over the same table.

This module owns capture (write) and the lifecycle transitions ticket 13
resolved -- promotion to `verified`, the circuit breaker, and quarantine.
It does NOT own applicability (ticket 12, applicability.py) or the HTN
execution binding (ticket 15, backend/app/execution/) -- those read from
`procedures`, they don't write its lifecycle.

HONEST SCOPE for this pass: ticket 13's promotion criterion cites SPRT
(sequential probability ratio testing, alpha=0.05, beta=0.10) as the
mechanism for deciding "as evidence arrives rather than at a fixed
sample size." What's implemented here is the concrete threshold SPRT is
meant to approximate -- >=10 successes, 0 failures, across >=3 distinct
contexts -- not full SPRT log-likelihood-ratio tracking. The ticket's
own numbers (Beta(11,1) -> [0.74, 0.99]) are what's actually enforced;
SPRT itself is a real, documented gap, not silently claimed as done.
"""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from app.services.embeddings import to_pgvector

CREATED_BY = "procedure_capture"

# Ticket 13's exact numbers -- literature-grounded (Beta-Bernoulli, rule
# of three), not tuned for this repo. Configuration, not literals
# scattered through logic, per the ticket's own "these constants are
# borrowed... unvalidated in this domain, so they are configuration, not
# literals in source."
MIN_SUCCESSES_FOR_VERIFIED = 10
MIN_DISTINCT_CONTEXTS_FOR_VERIFIED = 3

CIRCUIT_BREAKER_OPEN_AFTER_FAILURES = 5
CIRCUIT_BREAKER_HALF_OPEN_PROBE_SECONDS = 60
CIRCUIT_BREAKER_CLOSE_AFTER_SUCCESSES = 5

QUARANTINE_FAILURE_RATE_THRESHOLD = 0.5
QUARANTINE_WINDOW_DAYS = 7
QUARANTINE_DISABLE_AFTER_DAYS = 14


class ProcedureNotFound(Exception):
    """Raised when an operation targets a procedure id that doesn't
    resolve to a live row -- distinct from a silent no-op, since
    callers need to know their procedure_id was wrong, not that nothing
    happened to a real one."""


async def capture_procedure(
    pool: asyncpg.Pool,
    *,
    name: str,
    goal: str,
    steps: Optional[list] = None,
    parameter_schema: Optional[dict] = None,
    preconditions: Optional[list] = None,
    required_state: Optional[dict] = None,
    expected_effects: Optional[list] = None,
    postconditions: Optional[list] = None,
    invariants: Optional[list] = None,
    failure_conditions: Optional[list] = None,
    scope: Optional[dict] = None,
    exclusions: Optional[list] = None,
    family_id: Optional[str] = None,
    evidence_refs: Optional[list] = None,
    source_episode_ids: Optional[list[str]] = None,
    provenance: Optional[str] = None,
    domain: Optional[str] = None,
    domain_payload: Optional[dict] = None,
    migrated_from_task_node_id: Optional[str] = None,
    created_by: str = CREATED_BY,
    owner_id: Optional[str] = None,
    visibility: str = "public",
    embedding: Optional[list[float]] = None,
) -> dict:
    """
    Inserts a new procedure, always starting `candidate` / `fresh` /
    `active` (ticket 13's defaults -- nothing is born verified). Returns
    {"id": ..., "procedure_id": ...}: `id` is this specific version row;
    `procedure_id` is the stable handle a caller uses across the version
    chain (see supersede_procedure() for how a new version is created).

    Real, deliberate omission: this does not compute preconditions from
    a source episode's state_before projection automatically (ticket
    12's stated producer) -- that derivation is applicability.py's job
    at retrieval time / a future extraction step's job at capture time,
    not this function's. Passing `preconditions=[]` here is honest about
    what capture alone can produce without that wiring existing yet.
    """
    if visibility not in ("public", "private"):
        raise ValueError(f"visibility must be 'public' or 'private', got {visibility!r}")

    row = await pool.fetchrow(
        """
        INSERT INTO procedures (
            name, goal, steps, parameter_schema, preconditions, required_state,
            expected_effects, postconditions, invariants, failure_conditions,
            scope, exclusions, family_id, evidence_refs, source_episode_ids,
            provenance, domain, domain_payload, migrated_from_task_node_id,
            created_by, owner_id, visibility, embedding
        ) VALUES (
            $1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6::jsonb,
            $7::jsonb, $8::jsonb, $9::jsonb, $10::jsonb,
            $11::jsonb, $12::jsonb, $13, $14::jsonb, $15,
            $16, $17, $18::jsonb, $19,
            $20, $21, $22::visibility_level, $23::vector
        )
        RETURNING id, procedure_id
        """,
        name, goal,
        steps if steps is not None else [],
        parameter_schema if parameter_schema is not None else {},
        preconditions if preconditions is not None else [],
        required_state if required_state is not None else {},
        expected_effects if expected_effects is not None else [],
        postconditions if postconditions is not None else [],
        invariants if invariants is not None else [],
        failure_conditions if failure_conditions is not None else [],
        scope if scope is not None else {},
        exclusions if exclusions is not None else [],
        family_id,
        evidence_refs if evidence_refs is not None else [],
        source_episode_ids or [],
        provenance, domain,
        domain_payload if domain_payload is not None else {},
        migrated_from_task_node_id,
        created_by, owner_id, visibility,
        to_pgvector(embedding) if embedding is not None else None,
    )
    return {"id": str(row["id"]), "procedure_id": str(row["procedure_id"])}


async def get_procedure(pool: asyncpg.Pool, procedure_row_id: str) -> Optional[dict]:
    """Fetch one procedure version row by its own `id` (not
    `procedure_id`, which may have multiple version rows)."""
    row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", procedure_row_id)
    return dict(row) if row else None


async def record_execution_outcome(
    pool: asyncpg.Pool,
    *,
    procedure_row_id: str,
    success: bool,
    context_key: str,
    steps_used: Optional[int] = None,
    match_cost: float = 0.0,
    realised_savings: float = 0.0,
) -> dict:
    """
    Real, single source of truth for every ticket 13 lifecycle
    transition -- promotion, circuit breaker, quarantine all derive from
    the same `verification_stats` this function updates, under one
    row-locked transaction (same `SELECT ... FOR UPDATE` discipline
    knowledge_update.py already established for non-destructive
    updates elsewhere in this codebase).

    `context_key` is the caller's own notion of "distinct context"
    (ticket 13: "different files, environments, dependency sets") --
    this function doesn't define what makes two executions the same
    context, it only deduplicates by whatever string the caller passes.
    Tracked as a real set (`context_keys_seen` in verification_stats),
    not just a counter, so "is this a new context" is answered
    correctly rather than assumed monotonic.

    Returns the procedure row AFTER all transitions have been applied,
    so a caller can observe a promotion/quarantine/circuit-open that
    just happened as a direct result of this call.
    """
    async with pool.acquire() as conn:
        async with conn.transaction():
            row = await conn.fetchrow(
                "SELECT * FROM procedures WHERE id = $1 FOR UPDATE", procedure_row_id
            )
            if row is None:
                raise ProcedureNotFound(procedure_row_id)

            stats = dict(row["verification_stats"])
            stats.setdefault("context_keys_seen", [])
            stats.setdefault("consecutive_failures", 0)
            stats.setdefault("quarantine_entered_at", None)
            stats.setdefault("consecutive_successes_since_quarantine", 0)

            stats["attempts"] = stats.get("attempts", 0) + 1
            stats["match_cost_total"] = stats.get("match_cost_total", 0) + match_cost
            stats["realised_savings_total"] = stats.get("realised_savings_total", 0) + realised_savings

            if context_key not in stats["context_keys_seen"]:
                stats["context_keys_seen"].append(context_key)
            stats["distinct_contexts"] = len(stats["context_keys_seen"])

            if success:
                stats["successes"] = stats.get("successes", 0) + 1
                stats["consecutive_failures"] = 0
                if steps_used is not None:
                    prior_mean = stats.get("mean_steps")
                    prior_successes = stats["successes"] - 1
                    stats["mean_steps"] = (
                        steps_used if prior_mean is None or prior_successes == 0
                        else (prior_mean * prior_successes + steps_used) / stats["successes"]
                    )
            else:
                stats["consecutive_failures"] = stats.get("consecutive_failures", 0) + 1

            verification_state = row["verification_state"]
            availability = row["availability"]

            # Ticket 13: ">=10 successes, 0 failures, across >=3 distinct
            # contexts" for verified. "0 failures" means the procedure
            # has never recorded a failure at all -- not just none
            # recently -- since a single real failure genuinely
            # disqualifies the Beta(11,1) argument this threshold rests
            # on (successes minus failures, not successes alone).
            total_failures = stats["attempts"] - stats["successes"]
            if (
                verification_state == "candidate"
                and total_failures == 0
                and stats["successes"] >= MIN_SUCCESSES_FOR_VERIFIED
                and stats["distinct_contexts"] >= MIN_DISTINCT_CONTEXTS_FOR_VERIFIED
            ):
                verification_state = "verified"

            # Ticket 13's circuit breaker: open (quarantine) after 5
            # failures; close (un-quarantine) after 5 consecutive
            # successes recorded WHILE quarantined. The half-open probe
            # itself isn't a separate stored state -- availability=
            # 'quarantined' already means "don't auto-select this, but
            # an explicit call here can still record an outcome for
            # it", which IS the probe; each such call while quarantined
            # is one probe result.
            if availability == "quarantined":
                if success:
                    stats["consecutive_successes_since_quarantine"] = (
                        stats.get("consecutive_successes_since_quarantine", 0) + 1
                    )
                else:
                    stats["consecutive_successes_since_quarantine"] = 0

            if not success and stats["consecutive_failures"] >= CIRCUIT_BREAKER_OPEN_AFTER_FAILURES:
                if availability == "active":
                    availability = "quarantined"
                    stats["quarantine_entered_at"] = datetime.now(timezone.utc).isoformat()
                    stats["consecutive_successes_since_quarantine"] = 0
            elif (
                availability == "quarantined"
                and stats["consecutive_successes_since_quarantine"] >= CIRCUIT_BREAKER_CLOSE_AFTER_SUCCESSES
            ):
                availability = "active"
                stats["quarantine_entered_at"] = None
                stats["consecutive_successes_since_quarantine"] = 0

            updated = await conn.fetchrow(
                """
                UPDATE procedures
                SET verification_stats = $2::jsonb,
                    verification_state = $3::procedure_verification_state,
                    availability = $4::procedure_availability,
                    updated_at = now()
                WHERE id = $1
                RETURNING *
                """,
                procedure_row_id, stats, verification_state, availability,
            )
            return dict(updated)


async def check_quarantine_and_disable(pool: asyncpg.Pool, procedure_row_id: str) -> dict:
    """
    Ticket 13's quarantine escalation: disable after 14 days in
    quarantine, regardless of activity. This is time-driven, not
    outcome-driven (unlike the circuit breaker above), so it's a
    separate function meant to be called periodically (e.g. by a
    scheduled job), not inline with every execution outcome.

    Real, explicit scope limit: the ">=50% failure rate over 7 days"
    quarantine *entry* condition from ticket 13 is NOT implemented here
    -- only the simpler failure-streak-based circuit breaker (above) and
    the 14-day forced disable are. Computing a true 7-day rolling
    failure rate needs per-execution timestamped records, which
    verification_stats' aggregate-counter shape does not carry; adding
    that is real, separate schema work (a procedure_executions table),
    not something to silently approximate here.
    """
    row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", procedure_row_id)
    if row is None:
        raise ProcedureNotFound(procedure_row_id)

    stats = dict(row["verification_stats"])
    entered_at = stats.get("quarantine_entered_at")
    if row["availability"] != "quarantined" or not entered_at:
        return dict(row)

    entered_dt = datetime.fromisoformat(entered_at)
    days_in_quarantine = (datetime.now(timezone.utc) - entered_dt).days
    if days_in_quarantine < QUARANTINE_DISABLE_AFTER_DAYS:
        return dict(row)

    updated = await pool.fetchrow(
        "UPDATE procedures SET availability = 'disabled', updated_at = now() "
        "WHERE id = $1 RETURNING *",
        procedure_row_id,
    )
    return dict(updated)


async def compute_utility(pool: asyncpg.Pool, procedure_row_id: str) -> Optional[float]:
    """
    Ticket 13's retirement criterion, orthogonal to failure:
    utility(P) = (application_frequency * average_savings) - match_cost

    application_frequency is approximated here as times_reused (how many
    times this procedure has actually been selected for reuse, as
    distinct from `attempts`, which method_library.py's existing field
    already tracks separately). Returns None if there's no attempt
    history yet -- utility is undefined for a never-executed procedure,
    not zero.
    """
    row = await pool.fetchrow("SELECT verification_stats FROM procedures WHERE id = $1", procedure_row_id)
    if row is None:
        raise ProcedureNotFound(procedure_row_id)

    stats = dict(row["verification_stats"])
    attempts = stats.get("attempts", 0)
    if attempts == 0:
        return None

    times_reused = stats.get("times_reused", 0)
    realised_savings_total = stats.get("realised_savings_total", 0)
    match_cost_total = stats.get("match_cost_total", 0)

    average_savings = realised_savings_total / attempts
    application_frequency = times_reused
    return (application_frequency * average_savings) - match_cost_total


async def retire_negative_utility_procedures(pool: asyncpg.Pool, *, min_attempts: int = 1) -> list[str]:
    """
    Ticket 13: "a procedure with negative utility is deleted regardless
    of how well-verified it is." Retirement here means
    verification_state='retired' -- consistent with ticket 05's
    versioning ("must never silently overwrite"), a retired procedure's
    row and history stay real and queryable, just no longer selectable.
    Real deletion of a `verified` row would contradict spec.md's own
    "never silently overwrite" requirement this whole design rests on.

    Returns the list of procedure `id`s retired by this call, so a
    caller (e.g. a scheduled job) can log/audit what happened rather
    than only trusting a row count.
    """
    candidates = await pool.fetch(
        "SELECT id, verification_stats FROM procedures "
        "WHERE verification_state != 'retired' "
        "AND (verification_stats->>'attempts')::int >= $1",
        min_attempts,
    )
    retired_ids: list[str] = []
    for row in candidates:
        stats = dict(row["verification_stats"])
        attempts = stats.get("attempts", 0)
        if attempts == 0:
            continue
        average_savings = stats.get("realised_savings_total", 0) / attempts
        utility = (stats.get("times_reused", 0) * average_savings) - stats.get("match_cost_total", 0)
        if utility < 0:
            await pool.execute(
                "UPDATE procedures SET verification_state = 'retired', updated_at = now() WHERE id = $1",
                row["id"],
            )
            retired_ids.append(str(row["id"]))
    return retired_ids


async def approve_procedure(pool: asyncpg.Pool, *, procedure_row_id: str, approved_by: str) -> None:
    """
    The missing counterpart to migration 20's approval_status column --
    real gap, found while wiring applicability.py's new approval_status
    gate (see that module's own comment): the column existed, nothing
    could ever set it to 'approved' except raw SQL. Deliberately
    ORTHOGONAL to verification_state -- an approved procedure with 2
    recorded successes is still, correctly, not verified; approving
    something does not fast-track statistical verification.
    """
    await pool.execute(
        "UPDATE procedures SET approval_status = 'approved', approved_by = $2, "
        "approved_at = now() WHERE id = $1::uuid",
        procedure_row_id, approved_by,
    )


async def reject_procedure(pool: asyncpg.Pool, *, procedure_row_id: str, approved_by: str) -> None:
    await pool.execute(
        "UPDATE procedures SET approval_status = 'rejected', approved_by = $2, "
        "approved_at = now() WHERE id = $1::uuid",
        procedure_row_id, approved_by,
    )
