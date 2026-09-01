"""
Personal contributions composition (directive Sec 32.4/34, the "/me"
surface): what one identified actor has actually contributed to the
substrate, read straight off real provenance columns -- never a
computed/fabricated reputation, forks, or "accepted improvements" figure.

REAL PROVENANCE COLUMNS CONFIRMED BY INSPECTION before writing any query
here (grepped, not assumed):

  - `procedures.created_by`     (db/18_procedures.sql) -- the actor who
    captured a procedure version. `capture_procedure()`'s own
    `created_by` kwarg (app/services/procedures.py:116).
  - `knowledge_nodes.created_by` (db/01_ontology.sql) -- same role for
    claims (`node_type='claim'` rows); `capture_claim()`'s own
    `created_by` kwarg (app/services/claims.py:148).
  - `executions.actor_id`        (db/23_plan_persistence.sql) -- who
    actually RAN a compiled plan, distinct from `executions.created_by`
    (the writer of the row, which may be a system component recording on
    the actor's behalf). Filtered by `actor_id` here: a personal
    contribution history is about who DID the run, not who wrote the
    audit row.

DIRECTIVE-NAMED FIELDS DELIBERATELY OMITTED, WITH REASONS (never a
placeholder/fake value):

  - "reputation" / "evidence score" -- no per-actor aggregate score exists
    anywhere in the schema. Capability scores (procedure_extraction/
    capability.py) are conditioned on (task, implementation, context),
    never on an actor identity -- inventing an actor-level rollup here
    would be a new, unreviewed metric, not a read of existing data.
  - "forks" -- no fork/derivation-of-another-procedure relationship
    exists in the schema. `supersede_procedure()`'s version chain
    (`family_id`) is a single author's own history, not a fork graph.
  - "accepted improvements" -- `approval_status`/`approved_by` exist per
    procedure (migration 20), but there is no concept anywhere of an
    "improvement" as its own object distinct from a new procedure
    version, so this cannot be counted without inventing a definition
    the schema does not carry.

Every list returned here is real rows, scoped by the SAME
`scope_predicates()` builder every other read path in this codebase uses
-- a caller who cannot see a given row does not learn it exists.
"""
from __future__ import annotations

from typing import Any

import asyncpg

from app.services.access import AccessScope, TenantScope, scope_predicates

_PROCEDURE_FIELDS = (
    "id", "procedure_id", "version", "name", "goal",
    "verification_state", "staleness", "availability", "approval_status",
    "t_created",
)

_CLAIM_FIELDS = ("id", "name", "properties", "t_created")

_EXECUTION_FIELDS = (
    "id", "execution_plan_id", "task_graph_id", "procedure_id",
    "procedure_version", "outcome", "started_at", "ended_at", "trace_id",
)


async def _submitted_procedures(
    pool: asyncpg.Pool, actor_subject: str, *, scope: AccessScope, tenant: TenantScope,
) -> list[dict]:
    scope_sql, scope_params, _ = scope_predicates(scope, tenant, param_index=2)
    rows = await pool.fetch(
        f"""
        SELECT {', '.join(_PROCEDURE_FIELDS)} FROM procedures
        WHERE created_by = $1 AND t_invalid IS NULL AND {scope_sql}
        ORDER BY t_created DESC
        """,
        actor_subject, *scope_params,
    )
    return [dict(r) for r in rows]


async def _submitted_claims(
    pool: asyncpg.Pool, actor_subject: str, *, scope: AccessScope, tenant: TenantScope,
) -> list[dict]:
    scope_sql, scope_params, _ = scope_predicates(scope, tenant, param_index=2)
    rows = await pool.fetch(
        f"""
        SELECT {', '.join(_CLAIM_FIELDS)} FROM knowledge_nodes
        WHERE created_by = $1 AND node_type = 'claim' AND t_invalid IS NULL AND {scope_sql}
        ORDER BY t_created DESC
        """,
        actor_subject, *scope_params,
    )
    return [dict(r) for r in rows]


async def _executions(
    pool: asyncpg.Pool, actor_subject: str, *, scope: AccessScope, tenant: TenantScope,
) -> list[dict]:
    scope_sql, scope_params, _ = scope_predicates(scope, tenant, param_index=2)
    rows = await pool.fetch(
        f"""
        SELECT {', '.join(_EXECUTION_FIELDS)} FROM executions
        WHERE actor_id = $1 AND {scope_sql}
        ORDER BY started_at DESC
        """,
        actor_subject, *scope_params,
    )
    return [dict(r) for r in rows]


async def get_personal_contributions(
    pool: asyncpg.Pool, actor_subject: str, *, scope: AccessScope,
) -> dict[str, Any]:
    """The one real composition entry point `app/api/me.py` calls.

    Real, actual data only -- see module docstring for the directive
    fields deliberately omitted rather than fabricated.
    """
    tenant = TenantScope.unrestricted()  # same posture as app/api/graph.py

    submitted_procedures = await _submitted_procedures(
        pool, actor_subject, scope=scope, tenant=tenant,
    )
    submitted_claims = await _submitted_claims(
        pool, actor_subject, scope=scope, tenant=tenant,
    )
    executions = await _executions(
        pool, actor_subject, scope=scope, tenant=tenant,
    )

    return {
        "actor": actor_subject,
        "submitted_procedures": submitted_procedures,
        "submitted_claims": submitted_claims,
        "executions": executions,
    }
