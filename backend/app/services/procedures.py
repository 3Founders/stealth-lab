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
from typing import Any, Mapping, Optional
from uuid import UUID as UUIDType

import asyncpg

from app.config import settings
from app.utils.ids import uuid7
from app.services.embeddings import to_pgvector

from app.execution.evidence import outcome_to_evidence
from app.execution.failures import classify_and_route
from app.services.access import AccessScope, TenantScope, tenant_transaction

CREATED_BY = "procedure_capture"

# WAVE-3 adoption sweep: this module became the FIRST tenant_transaction()
# caller -- every statement below runs with app.tenant_id bound
# transaction-locally, so db/29's RLS backstop is armed-and-keyed here
# rather than permissive-by-absence (cross-lane request #1).
OUTCOME_WRITER_STAMP = "record_execution_outcome@1"

# Invariant #13 judgment call, named: the lifecycle writer records REAL
# runs (the executor calls this after an actual execution), but its
# historical API carries only `success: bool` -- no criteria argument.
# A bare model-asserted success must never reach the evidence table, so
# the writer synthesizes explicit criteria from what the call itself
# measured (the run's step count / costs) unless the caller passes
# richer criteria. The predicate names the recording provenance; the
# metrics are the run's own numbers.
LIFECYCLE_OUTCOME_CRITERIA_PREDICATE = (
    "procedure execution completed with a recorded terminal outcome"
)

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

# Phase 5 (memory-substrate map, gap #8) -- merge_duplicate_procedures'
# survivor rule. Ticket 13's own ladder ("candidate", "verified") plus
# "retired" as strictly weaker than either live state -- a retired
# procedure only survives a merge against another retired one. Not a
# DB enum ORDER; a plain dict keeps this local to the one function that
# needs it rather than teaching the schema a total order it doesn't
# otherwise care about.
_VERIFICATION_STATE_RANK = {"retired": -1, "candidate": 0, "verified": 1}


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
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
) -> dict:
    """
    Inserts a new procedure, always starting `candidate` / `fresh` /
    `active` (ticket 13's defaults -- nothing is born verified). Returns
    {"id": ..., "procedure_id": ...}: `id` is this specific version row;
    `procedure_id` is the stable handle a caller uses across the version
    chain (see supersede_procedure() for how a new version is created).

    Band 1.2/1.3: provenance and scope are V0-gated at this boundary --
    a procedure without explicit provenance or without a derivable scope
    is rejected before touching the database. The shard-key columns are
    derived from `domain` (the ingestion adapter's locality signal)
    unless the caller overrides them.

    Real, deliberate omission: this does not compute preconditions from
    a source episode's state_before projection automatically (ticket
    12's stated producer) -- that derivation is applicability.py's job
    at retrieval time / a future extraction step's job at capture time,
    not this function's. Passing `preconditions=[]` here is honest about
    what capture alone can produce without that wiring existing yet.
    """
    if visibility not in ("public", "private"):
        raise ValueError(f"visibility must be 'public' or 'private', got {visibility!r}")

    # --- V0 gate (Band 1.3): nothing enters without provenance + scope ---
    from app.services.v0_gate import validate_provenance, validate_scope

    validate_provenance(provenance)
    procedure_scope_type, procedure_scope_entity_id = validate_scope(
        scope_type, scope_entity_id or domain,
        allow_global_entity_id=bool(scope_type == "global" and scope_entity_id),
    )

    row = await pool.fetchrow(
        """
        INSERT INTO procedures (
            id, name, goal, steps, parameter_schema, preconditions, required_state,
            expected_effects, postconditions, invariants, failure_conditions,
            scope, exclusions, family_id, evidence_refs, source_episode_ids,
            provenance, domain, domain_payload, migrated_from_task_node_id,
            created_by, owner_id, visibility, embedding,
            scope_type, scope_entity_id, embedding_model_id, embedding_dim
        ) VALUES (
            $24::uuid, $1, $2, $3::jsonb, $4::jsonb, $5::jsonb, $6::jsonb,
            $7::jsonb, $8::jsonb, $9::jsonb, $10::jsonb,
            $11::jsonb, $12::jsonb, $13, $14::jsonb, $15,
            $16, $17, $18::jsonb, $19,
            $20, $21, $22::visibility_level, $23::vector,
            $25, $26, $27, $28
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
        # Band 1.5: time-ordered id generated app-side (UUIDv7); the DB
        # default remains as a last-resort fallback for non-repository writes.
        str(uuid7()),
        procedure_scope_type,
        procedure_scope_entity_id,
        # Band 1.6: embedding provenance stamps — which model produced this
        # vector, at what dimension. Null iff embedding is null.
        (settings.embedding_model if embedding is not None else None),
        (settings.embedding_dimension if embedding is not None else None),
    )
    return {"id": str(row["id"]), "procedure_id": str(row["procedure_id"])}


async def get_procedure(pool: asyncpg.Pool, procedure_row_id: str) -> Optional[dict]:
    """Fetch one procedure version row by its own `id` (not
    `procedure_id`, which may have multiple version rows)."""
    row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", procedure_row_id)
    return dict(row) if row else None


# Columns copied from the prior version row onto the new one unless the
# caller overrides them in `changed_fields`. Deliberately omits the
# identity/lifetime columns supersede_procedure() forces itself (id,
# procedure_id, version, the four bitemporal stamps, created_at,
# updated_at) -- everything else about a procedure version is carried
# forward so a source-content change that only touches `steps` doesn't
# silently drop hard-won verification_stats or evidence_refs.
_SUPERSEDE_CARRY_COLUMNS: tuple[str, ...] = (
    "family_id", "name", "goal", "steps", "parameter_schema",
    "preconditions", "required_state", "expected_effects", "postconditions",
    "invariants", "failure_conditions", "scope", "exclusions",
    "verification_state", "staleness", "availability", "verification_stats",
    "evidence_refs", "source_episode_ids", "migrated_from_task_node_id",
    "provenance", "domain", "domain_payload", "created_by", "visibility",
    "owner_id", "embedding", "embedding_model_id", "embedding_dim",
    "scope_type", "scope_entity_id", "approval_status", "approved_by",
    "approved_at", "capability_statement", "extracted_by",
)

# Per-column SQL cast for the carry-forward INSERT. asyncpg infers scalar
# text/int/timestamptz from the INSERT target, so only the structured
# types need an explicit cast -- same set capture_procedure()'s own INSERT
# casts.
_SUPERSEDE_COLUMN_CASTS: dict[str, str] = {
    "steps": "jsonb", "parameter_schema": "jsonb", "preconditions": "jsonb",
    "required_state": "jsonb", "expected_effects": "jsonb",
    "postconditions": "jsonb", "invariants": "jsonb",
    "failure_conditions": "jsonb", "scope": "jsonb", "exclusions": "jsonb",
    "verification_stats": "jsonb", "evidence_refs": "jsonb",
    "domain_payload": "jsonb",
    "source_episode_ids": "uuid[]",
    "verification_state": "procedure_verification_state",
    "staleness": "procedure_staleness",
    "availability": "procedure_availability",
    "visibility": "visibility_level",
    "embedding": "vector",
}


def _supersede_embedding_value(v: Any) -> Optional[str]:
    """A prior row's embedding comes back from `SELECT *` as pgvector's
    text form (asyncpg has no vector codec -- see embeddings.to_pgvector);
    a caller override in `changed_fields` may be a raw float sequence.
    Normalize both to the text form the `::vector` cast accepts."""
    if v is None:
        return None
    if isinstance(v, str):
        return v
    return to_pgvector(v)


async def supersede_procedure(
    pool: asyncpg.Pool,
    *,
    prior_row_id: str,
    changed_fields: Optional[Mapping[str, Any]] = None,
    superseded_by: str = "skill_md_ingestion",
    reason: Optional[str] = None,
) -> Optional[dict]:
    """
    Create the next version of an existing logical procedure.

    Referenced by capture_procedure()'s own docstring since Band 1 but
    never implemented until Phase 2's ingestion compiler needed it: a
    changed public source (a new SKILL.md content hash for the same URI)
    must produce a NEW version of the same `procedure_id`, never an
    in-place overwrite (brief §10 -- "Never silently replace a prior
    version").

    Mechanics, all inside one transaction this function owns:
      1. Lock the prior version row (`FOR UPDATE`, live rows only). Fewer
         than one live row -> return None (a concurrent supersede/merge
         already moved it -- a valid no-op, same contract
         merge_duplicate_procedures() uses).
      2. INSERT a new row reusing the prior `procedure_id`, `version =
         prior.version + 1`, every _SUPERSEDE_CARRY_COLUMNS value copied
         forward unless `changed_fields` overrides it, fresh bitemporal
         stamps, a fresh UUIDv7 `id`.
      3. Close the prior row's validity window (`t_invalid = t_expired =
         now()`) -- tombstone, never delete, this table's own idiom.
      4. Write a `SUPERSEDES` edge new -> prior ('company_debate'
         provenance, the value merge_duplicate_procedures() already reuses
         for internal-maintenance edge writes -- the enum has no
         'maintenance' member).
      5. Record a ChangeSet (invariant #7): a create_version op for the
         new row + an invalidate op for the prior one.

    Returns {"id", "procedure_id", "version"} for the new version, or
    None if the prior row was already gone.
    """
    changed = dict(changed_fields or {})
    unknown = set(changed) - set(_SUPERSEDE_CARRY_COLUMNS)
    if unknown:
        raise ValueError(
            f"supersede_procedure: changed_fields has non-carry columns {sorted(unknown)} "
            f"(allowed: {list(_SUPERSEDE_CARRY_COLUMNS)})"
        )

    now = datetime.now(timezone.utc)
    new_id = str(uuid7())

    async with pool.acquire() as conn:
        async with conn.transaction():
            prior = await conn.fetchrow(
                "SELECT * FROM procedures WHERE id = $1::uuid AND t_invalid IS NULL FOR UPDATE",
                prior_row_id,
            )
            if prior is None:
                return None

            new_version = prior["version"] + 1
            procedure_id = prior["procedure_id"]

            # Build the carry-forward column list + params in a fixed order.
            insert_cols = ["id", "procedure_id", "version"]
            placeholders = ["$1::uuid", "$2::uuid", "$3"]
            params: list[Any] = [new_id, procedure_id, new_version]
            idx = 4
            for col in _SUPERSEDE_CARRY_COLUMNS:
                value = changed[col] if col in changed else prior[col]
                if col == "embedding":
                    value = _supersede_embedding_value(value)
                cast = _SUPERSEDE_COLUMN_CASTS.get(col)
                insert_cols.append(col)
                placeholders.append(f"${idx}::{cast}" if cast else f"${idx}")
                params.append(value)
                idx += 1

            sql = (
                f"INSERT INTO procedures ({', '.join(insert_cols)}, "
                f"t_valid, t_invalid, t_created, t_expired, created_at, updated_at) "
                f"VALUES ({', '.join(placeholders)}, "
                f"now(), NULL, now(), NULL, now(), now()) "
                f"RETURNING id, procedure_id, version"
            )
            inserted = await conn.fetchrow(sql, *params)

            await conn.execute(
                "UPDATE procedures SET t_invalid = $2, t_expired = $2, updated_at = $2 "
                "WHERE id = $1::uuid",
                prior_row_id, now,
            )

            await conn.execute(
                "INSERT INTO edges (edge_type, source_id, source_table, "
                "target_id, target_table, properties, provenance, "
                "t_valid, t_created, created_by) "
                "VALUES ('SUPERSEDES', $1, 'procedures', $2, 'procedures', "
                "$3::jsonb, 'company_debate', $4, $4, $5)",
                inserted["id"], prior_row_id,
                {"reason": reason or f"source content changed ({superseded_by})",
                 "prior_version": prior["version"], "new_version": new_version},
                now, superseded_by,
            )

    from app.services.changeset_record import record_change_set, ChangeOperation
    await record_change_set(
        pool, author=superseded_by,
        reason=reason or (
            f"procedure {procedure_id} superseded: version {prior['version']} -> {new_version}"
        ),
        operations=[
            ChangeOperation(
                operation="create_version", target_table="procedures",
                target_id=str(inserted["id"]),
                detail={
                    "procedure_id": str(procedure_id),
                    "version": new_version,
                    "supersedes_row_id": str(prior_row_id),
                    "changed_fields": sorted(changed),
                },
            ),
            ChangeOperation(
                operation="invalidate", target_table="procedures",
                target_id=str(prior_row_id),
                detail={"superseded_by_row_id": str(inserted["id"]),
                        "new_version": new_version},
            ),
        ],
    )

    return {
        "id": str(inserted["id"]),
        "procedure_id": str(inserted["procedure_id"]),
        "version": inserted["version"],
    }


async def record_execution_outcome(
    pool: asyncpg.Pool,
    *,
    procedure_row_id: str,
    success: bool,
    context_key: str,
    steps_used: Optional[int] = None,
    match_cost: float = 0.0,
    realised_savings: float = 0.0,
    success_criteria: Optional[Mapping[str, Any]] = None,
    failure_class: Optional[str] = None,
    owner_id: Optional[str] = None,
    visibility: str = "public",
    tenant_scope: Optional[TenantScope] = None,
    evidence_type: str = "execution_result",
) -> dict:
    """
    Real, single source of truth for every ticket 13 lifecycle
    transition -- promotion, circuit breaker, quarantine all derive from
    the same `verification_stats` this function updates, under one
    row-locked transaction (same `SELECT ... FOR UPDATE` discipline
    knowledge_update.py already established for non-destructive
    updates elsewhere in this codebase).

    `evidence_type` defaults to `"execution_result"` (organic reuse --
    the procedure was selected to accomplish a real task and its outcome
    is recorded as a side effect). Pass `"reproduction"` when the caller
    is not organically reusing the procedure but deliberately re-running
    it specifically to test whether it still reproduces its claimed
    result (app/execution/reproduction.py::reproduce_procedure()). Both
    are OUTCOME_BEARING_TYPES (execution/evidence.py) and both count
    toward `procedure_evidence_stats.independent_supporting_required`
    (db/24_evidence.sql) -- today `execution_result` is the only type
    any caller actually produces, which is honest (real, recorded
    outcomes) but weaker than the founder's spec envisions: a procedure
    can reach `verified` on repeated successful *use* alone, never on an
    independent reproduction attempt. This parameter is what lets a
    caller record the stronger kind honestly, without duplicating this
    function's counter/circuit-breaker/quarantine logic.

    `context_key` is the caller's own notion of "distinct context"
    (ticket 13: "different files, environments, dependency sets") --
    this function doesn't define what makes two executions the same
    context, it only deduplicates by whatever string the caller passes.
    Tracked as a real set (`context_keys_seen` in verification_stats),
    not just a counter, so "is this a new context" is answered
    correctly rather than assumed monotonic.

    WAVE-3 (cross-lane request #1 landed): this is now ALSO the
    evidence writer. One execution_result evidence row per outcome --
    built by app/execution/evidence.py::outcome_to_evidence(), inserted
    BEFORE the counters UPDATE so db/30's verified-requires-evidence
    engine trigger sees it when the promotion transition fires in the
    same transaction (gate and writer together, never a half-gate).
    Failed outcomes are classified and routed in that same transaction
    via classify_and_route() -- pass `failure_class` when the caller
    knows the §36 cause; leaving it NULL is honest and lands the
    failure in the requires_review queue ("nobody classified this" is
    itself a finding), never silently dropped. Successes carry no cause
    and are never routed.

    `success_criteria` feeds invariant #13's gate for successes; when
    omitted, explicit criteria are synthesized from the measurements
    this call actually recorded (see
    LIFECYCLE_OUTCOME_CRITERIA_PREDICATE) -- bare model-asserted
    success stays unwritable.

    `tenant_scope` binds app.tenant_id for the whole transaction
    (db/29's RLS backstop reads it); default Commons matches today's
    shared-commons posture and the tenant_id DEFAULT every evidence row
    carries. TenantScope.unrestricted() opens the plain-transaction
    maintenance hatch.

    Returns the procedure row AFTER all transitions have been applied,
    so a caller can observe a promotion/quarantine/circuit-open that
    just happened as a direct result of this call.
    """
    scope = tenant_scope if tenant_scope is not None else TenantScope.commons()
    async with tenant_transaction(pool, scope) as conn:
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

        # ---- evidence write (WAVE-3): one row per outcome, before the
        # counters UPDATE so migration 30's engine trigger counts THIS
        # run's evidence when it gates the candidate->verified jump.
        criteria = None
        if success:
            criteria = success_criteria if success_criteria is not None else {
                "predicate": LIFECYCLE_OUTCOME_CRITERIA_PREDICATE,
                "metrics": {
                    key: value
                    for key, value in (
                        ("steps_used", steps_used),
                        ("match_cost", match_cost),
                        ("realised_savings", realised_savings),
                    )
                    if value is not None
                },
            }
        evidence = outcome_to_evidence(
            evidence_type=evidence_type,
            target={
                "target_type": "procedure",
                "target_id": str(row["id"]),
                "target_version": row["version"],
            },
            outcome_status="success" if success else "failure",
            success_criteria=criteria,
            failure_class=failure_class,
            context_key=context_key,
            created_by=OUTCOME_WRITER_STAMP,
            visibility=visibility,
            owner_id=owner_id,
        )
        inserted = await conn.fetchrow(
            """
            INSERT INTO evidence (
                id, evidence_type, target_type, target_id, target_version,
                direction, strength_score, strength_method,
                independence_group, context_key,
                outcome_status, success_criteria, failure_class,
                created_by, visibility, owner_id, tenant_id
            ) VALUES (
                $1::uuid, $2, $3, $4::uuid, $5,
                $6, $7, $8,
                $9, $10,
                $11, $12::jsonb, $13,
                $14, $15, $16, $17::uuid
            )
            RETURNING id, target_type, target_id, target_version,
                      outcome_status, failure_class, context_key,
                      independence_group
            """,
            evidence.id,
            evidence.evidence_type,
            evidence.target.target_type,
            evidence.target.target_id,
            evidence.target.target_version,
            evidence.direction,
            evidence.strength.score,
            evidence.strength.method,
            evidence.independence_group,
            evidence.context_key,
            evidence.outcome_status,
            evidence.success_criteria,
            evidence.failure_class,
            evidence.created_by,
            evidence.visibility,
            evidence.owner_id,
            scope.tenant_id,
        )
        if not success:
            # Failures route in the SAME transaction (§36); successes
            # raise NotClassifiable inside classify_failure and must be
            # skipped by the caller -- which this branch is.
            await classify_and_route(conn, dict(inserted))

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
    from app.services.changeset_record import record_change_set, status_change
    await record_change_set(
        pool, author="system:quarantine_timer",
        reason=f"14-day quarantine expiry -> availability 'disabled' "
               f"(entered {entered_at})",
        operations=[status_change(procedure_row_id, {
            "availability": "disabled",
            "quarantine_entered_at": entered_at,
        })],
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


async def mark_procedure_stale(
    pool: asyncpg.Pool, *, procedure_row_id: str, reason: str, detected_by: str,
) -> dict:
    """
    Phase 4 (memory-substrate map): the narrowest real staleness signal --
    a procedure's own bound numeric invariant (e.g. `pandas_version >=
    2.0`) genuinely contradicted by a fresh environment probe of a real
    repo. `staleness` (db/18_procedures.sql) has existed since Band 1 and
    is already a real, enforced hard constraint in
    applicability.py::check_hard_constraints() -- staleness='stale' rows
    are excluded from retrieval -- but until this pass nothing in
    production code ever SET it away from 'fresh'; only test fixtures did
    (confirmed by repo-wide search this session).

    Deliberately one-directional and narrow, per the founder's own "start
    deterministic... do not build the complete TMS yet": this function
    only ever moves 'fresh' -> 'stale'. It does not attempt to un-stale a
    procedure automatically -- a procedure whose invariant later holds
    again deserves a real re-verification pass (the 'revalidating' state
    already modeled in the DB enum), not a silent auto-reversal, which is
    real, separate, larger work this pass does not attempt.

    A no-op, returning the row unchanged, if the procedure is already
    'stale' or 'revalidating' -- repeated real-world drift detection
    against the same already-flagged procedure must not spam the
    ChangeSet audit trail with duplicate transitions.

    Records a ChangeSet (same convention as approve_procedure/
    reject_procedure/check_quarantine_and_disable): a staleness
    transition is a [V] status mutation and must be auditable after the
    fact, including WHY (`reason` -- e.g. which invariant was violated
    under which real probed bindings) and WHO/WHAT detected it
    (`detected_by` -- e.g. "reproduce_procedure:<repo_path basename>").
    """
    row = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", procedure_row_id)
    if row is None:
        raise ProcedureNotFound(procedure_row_id)
    if row["staleness"] != "fresh":
        return dict(row)

    updated = await pool.fetchrow(
        "UPDATE procedures SET staleness = 'stale', updated_at = now() "
        "WHERE id = $1 RETURNING *",
        procedure_row_id,
    )
    from app.services.changeset_record import record_change_set, status_change
    await record_change_set(
        pool, author=detected_by,
        reason=f"staleness fresh -> stale: {reason}",
        operations=[status_change(procedure_row_id, {"staleness": "stale"})],
    )
    return dict(updated)


async def approve_procedure(pool: asyncpg.Pool, *, procedure_row_id: str, approved_by: str) -> None:
    """
    The missing counterpart to migration 20's approval_status column --
    real gap, found while wiring applicability.py's new approval_status
    gate (see that module's own comment): the column existed, nothing
    could ever set it to 'approved' except raw SQL. Deliberately
    ORTHOGONAL to verification_state -- an approved procedure with 2
    recorded successes is still, correctly, not verified; approving
    something does not fast-track statistical verification.

    Records a ChangeSet (Band 1.9c, invariant #7): approval is a [V]
    status mutation and must be auditable after the fact.
    """
    await pool.execute(
        "UPDATE procedures SET approval_status = 'approved', approved_by = $2, "
        "approved_at = now() WHERE id = $1::uuid",
        procedure_row_id, approved_by,
    )
    from app.services.changeset_record import record_change_set, status_change
    await record_change_set(
        pool, author=approved_by,
        reason="procedure approval_status -> approved",
        operations=[status_change(procedure_row_id, {"approval_status": "approved"})],
    )


async def reject_procedure(pool: asyncpg.Pool, *, procedure_row_id: str, approved_by: str) -> None:
    await pool.execute(
        "UPDATE procedures SET approval_status = 'rejected', approved_by = $2, "
        "approved_at = now() WHERE id = $1::uuid",
        procedure_row_id, approved_by,
    )
    from app.services.changeset_record import record_change_set, status_change
    await record_change_set(
        pool, author=approved_by,
        reason="procedure approval_status -> rejected",
        operations=[status_change(procedure_row_id, {"approval_status": "rejected"})],
    )


# ---------------------------------------------------------------------------
# Phase 5 (memory-substrate map, gap #8): procedure-level canonicalization.
#
# check_novelty() (skill_ingestion.py) is refuse-only and only ever sees ONE
# candidate at admission time, before it is written -- it cannot merge two
# rows that are already both persisted (e.g. the same real capability
# ingested once via ingest_skill_md with provenance='prior_library' and once
# via extraction/organic capture with provenance='system_pending_review').
# This is the separate, later-stage real-merge path check_novelty structurally
# cannot provide. It reuses dedup.py's clustering primitive
# (find_duplicate_clusters(table="procedures")) but deliberately NOT
# dedup.py's merge_cluster: that function's "earliest"/"latest" survivor
# rule, hard-coded edge provenance, and unconditional tombstone-with-no-
# provenance-preservation are the right shape for task_nodes/knowledge_nodes
# (Part A's actual use case -- accidental re-creation / policy supersession)
# but wrong for procedures, which carry ticket-13 verification state,
# evidence_refs, and source_episode_ids that a merge must not silently drop.
#
# Survivor rule (principled, reuses verification_stats already on the row --
# no new scoring pass): highest verification_state on the ticket-13 ladder
# (verified > candidate > retired), tie-broken by most recorded successes,
# then most distinct_contexts, then earliest t_created (same "first real one
# wins" tie-break dedup.py's own merge_cluster uses for "earliest").
#
# Losers are NEVER hard-deleted -- t_invalid = now() (this table's own
# bi-temporal invalidate-and-append convention, db/18_procedures.sql), same
# tombstone-not-delete discipline retire_negative_utility_procedures() and
# every other lifecycle transition in this module already uses. The
# survivor's evidence_refs/source_episode_ids absorb the losers' (union, not
# overwrite); a loser's own `provenance` string cannot be losslessly merged
# into the survivor's single TEXT provenance column, so it is preserved
# honestly as a structured entry inside evidence_refs instead of silently
# discarded or silently overwriting the survivor's own provenance. A
# DUPLICATE_OF edge (same SUPERSEDES/custom_edge_type convention
# dedup.py::merge_cluster uses for task_nodes/knowledge_nodes -- procedures
# became a valid edge source_table/target_table in db/18) records the merge
# for graph-side discoverability, and a ChangeSet (same convention as
# approve_procedure/mark_procedure_stale/check_quarantine_and_disable above)
# records it for audit.
# ---------------------------------------------------------------------------

def _procedure_rank_key(row: Mapping[str, Any]) -> tuple:
    stats = row["verification_stats"] or {}
    state_rank = _VERIFICATION_STATE_RANK.get(row["verification_state"], 0)
    successes = stats.get("successes", 0)
    distinct_contexts = stats.get("distinct_contexts", 0)
    t_created = row["t_created"]
    # Higher is better on the first three; for t_created, EARLIER wins a
    # full tie, so its contribution is negated (a smaller/earlier
    # timestamp must sort as "more preferred" under reverse=True below).
    return (state_rank, successes, distinct_contexts, -t_created.timestamp())


async def merge_duplicate_procedures(
    pool: asyncpg.Pool,
    *,
    cluster_ids: list[str],
    merged_by: str,
) -> Optional[dict]:
    """
    Merge one cluster of near-duplicate procedure ids (from
    dedup.find_duplicate_clusters(pool, "procedures", ...)) into a single
    survivor, inside a transaction this function owns.

    Returns None if fewer than 2 members of `cluster_ids` are still live
    (t_invalid IS NULL) -- a concurrent merge or edit already reconciled
    this cluster, a valid no-op, not an error (same contract
    dedup.py::merge_cluster uses).

    Otherwise returns {"table": "procedures", "canonical_id",
    "canonical_name", "merged_ids", "merged_names", "change_set_id"}.

    Does NOT rewire edges pointing at a loser's row (unlike
    dedup.py::merge_cluster) -- no producer creates edges with
    source_table/target_table='procedures' yet (gap #9, task
    composability, is explicitly deferred per the memory-substrate map),
    so there is nothing real to rewire; adding that machinery ahead of a
    real producer would be exactly the "second dedup system" Rule 6
    forbids building preemptively.
    """
    now = datetime.now(timezone.utc)

    async with pool.acquire() as conn:
        async with conn.transaction():
            rows = await conn.fetch(
                "SELECT * FROM procedures WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL FOR UPDATE",
                [UUIDType(i) for i in cluster_ids],
            )
            if len(rows) < 2:
                return None

            ordered = sorted(rows, key=_procedure_rank_key, reverse=True)
            canonical = ordered[0]
            duplicates = ordered[1:]
            canonical_id = canonical["id"]

            merged_evidence_refs = list(canonical["evidence_refs"] or [])
            merged_source_episode_ids = list(canonical["source_episode_ids"] or [])
            seen_source_ids = {str(x) for x in merged_source_episode_ids}

            for dup in duplicates:
                dup_id = dup["id"]

                for ref in (dup["evidence_refs"] or []):
                    if ref not in merged_evidence_refs:
                        merged_evidence_refs.append(ref)
                merged_evidence_refs.append({
                    "merged_from_procedure_row_id": str(dup_id),
                    "merged_from_provenance": dup["provenance"],
                    "merged_from_name": dup["name"],
                    "merged_at": now.isoformat(),
                    "merge_reason": "duplicate cluster merge (embedding-similarity complete-linkage)",
                })

                for sid in (dup["source_episode_ids"] or []):
                    if str(sid) not in seen_source_ids:
                        merged_source_episode_ids.append(sid)
                        seen_source_ids.add(str(sid))

                await conn.execute(
                    "UPDATE procedures SET t_invalid = $2, t_expired = $2, updated_at = $2 WHERE id = $1",
                    dup_id, now,
                )
                # provenance is edges' real ENUM (provenance_source,
                # db/01_ontology.sql) -- 'company_debate' is the exact
                # value dedup.py::merge_cluster already reuses for this
                # same internal-maintenance-write shape (there is no
                # 'dedup'/'maintenance' enum member), so this follows
                # that established convention rather than adding a new
                # ad hoc string that the ENUM would reject anyway.
                await conn.execute(
                    "INSERT INTO edges (edge_type, custom_edge_type, source_id, source_table, "
                    "target_id, target_table, properties, provenance, t_valid, t_created, created_by) "
                    "VALUES ('SUPERSEDES', 'DUPLICATE_OF', $1, 'procedures', $2, 'procedures', "
                    "$3::jsonb, 'company_debate', $4, $4, $5)",
                    canonical_id, dup_id,
                    {"reason": "procedure dedup: complete-linkage merge"}, now, merged_by,
                )

            await conn.execute(
                "UPDATE procedures SET evidence_refs = $2::jsonb, source_episode_ids = $3, "
                "updated_at = $4 WHERE id = $1",
                canonical_id, merged_evidence_refs, merged_source_episode_ids, now,
            )

    from app.services.changeset_record import record_change_set, ChangeOperation
    operations = [
        ChangeOperation(
            operation="invalidate", target_table="procedures", target_id=str(dup["id"]),
            detail={
                "merged_into": str(canonical_id),
                "reason": "duplicate cluster merge (embedding-similarity complete-linkage)",
            },
        )
        for dup in duplicates
    ]
    operations.append(ChangeOperation(
        operation="revise", target_table="procedures", target_id=str(canonical_id),
        detail={
            "absorbed_procedure_row_ids": [str(dup["id"]) for dup in duplicates],
            "evidence_refs_count": len(merged_evidence_refs),
            "source_episode_ids_count": len(merged_source_episode_ids),
        },
    ))
    change_set_id = await record_change_set(
        pool, author=merged_by,
        reason=f"procedure dedup merge: {len(duplicates)} duplicate(s) merged into "
               f"{canonical['name']!r} ({canonical_id})",
        operations=operations,
    )

    return {
        "table": "procedures",
        "canonical_id": str(canonical_id),
        "canonical_name": canonical["name"],
        "merged_ids": [str(dup["id"]) for dup in duplicates],
        "merged_names": [dup["name"] for dup in duplicates],
        "change_set_id": str(change_set_id),
    }


async def run_procedure_dedup_sweep(
    pool: asyncpg.Pool,
    *,
    scope: Optional[AccessScope] = None,
    embedder: Optional[Any] = None,
    merged_by: str = "procedure_dedup_sweep",
    apply: bool = False,
) -> list[dict]:
    """
    Batch counterpart to merge_duplicate_procedures(), same cautious-by-
    default posture as dedup.py::run_dedup_sweep (dry run unless
    `apply=True`) -- an internal/admin operation only, not a request-path
    call. Finds clusters via dedup.find_duplicate_clusters(table=
    "procedures"), which needs no tenant_scope argument here: procedures
    carries no tenant_id column (see that function's own docstring), so
    the only correct scope for this table is the unrestricted default it
    already falls back to.
    """
    from app.services.dedup import find_duplicate_clusters

    scope = scope or AccessScope.unrestricted()
    clusters = await find_duplicate_clusters(pool, "procedures", scope, embedder)

    reports: list[dict] = []
    for cluster in clusters:
        ids = [c["id"] for c in cluster]
        if not apply:
            reports.append({
                "table": "procedures",
                "canonical_id": ids[0],
                "canonical_name": cluster[0]["name"],
                "merged_ids": ids[1:],
                "merged_names": [c["name"] for c in cluster[1:]],
                "change_set_id": None,
            })
            continue
        report = await merge_duplicate_procedures(pool, cluster_ids=ids, merged_by=merged_by)
        if report is not None:
            reports.append(report)

    return reports
