"""
IngestionContext lifecycle -- the one durable provenance row every
ingestion opens, that every object it derives can be traced back to.

WHY THIS EXISTS
    The "who / under what scope / by which extractor / from what input"
    facts were scattered: `ingestion_runs` (migration 32) is a per-run
    metrics manifest with no actor / workspace / scope; `ingested_artifacts`
    is per-artifact; `extractor_version` was a lone TEXT column on four
    tables and never bundled. No derived object could answer V4-hardening
    §32's provenance invariant in one traversal. Migration 51 added
    `ingestion_contexts` plus a nullable `ingestion_context_id` back-link on
    `ingested_artifacts`, `observations`, `procedures`, `evidence`,
    `knowledge_nodes`. This module is its writer.

CONTEXT vs RUN -- the split, stated plainly
    An `ingestion_runs` row is the OPERATIONAL unit: one adapter sweep and
    its counters. An `ingestion_contexts` row is the PROVENANCE unit: one
    logical ingestion request with an actor, a workspace, a scope, a
    classification, an extractor identity. They are ~1:1 in the common case
    (`run_ref` links them), but the split lets one long-lived context span
    several runs and lets a run with no request context still work. Every
    object an ingestion derives stamps `ingestion_context_id` so its full
    provenance is one join away.

HONEST SCOPE LIMITS
    - `status` is advisory bookkeeping. Nothing here enforces that a
      context reaches a terminal state; a crashed ingestion leaves it
      `open`, which is the honest record of what happened.
    - No FK from the derived side (migration 51's choice): a context row
      stays queryable forever even after its derived rows are tombstoned.
    - `t_created` is the only temporal column; contexts are not
      bi-temporal (they are operational metadata, not world-state).
"""
from __future__ import annotations

from typing import Optional

import asyncpg

from app.services.access import TenantScope, tenant_transaction
from app.services.v0_gate import validate_scope
from app.utils.ids import uuid7

# The CHECK on ingestion_contexts.status (migration 51).
INGESTION_CONTEXT_STATUSES = ("open", "completed", "failed", "rejected")


async def open_ingestion_context(
    pool: asyncpg.Pool,
    *,
    source_type: str,
    extractor_id: str,
    extractor_version: str,
    actor_id: str,
    scope_type: str,
    scope_entity_id: Optional[str] = None,
    source_ref: Optional[str] = None,
    source_uri: Optional[str] = None,
    source_hash: Optional[str] = None,
    workspace_id: Optional[str] = None,
    environment_id: Optional[str] = None,
    classification: Optional[str] = None,
    visibility: str = "public",
    owner_id: Optional[str] = None,
    tenant_id: Optional[str] = None,
    run_ref: Optional[str] = None,
) -> str:
    """
    Open one IngestionContext and return its id (a uuid7 string).

    ``scope_type`` is V0-gated (the 10 canonical values; a non-global scope
    must name what it points at) -- the same rule migration 51's own CHECK
    encodes as engine teeth for direct-SQL writers.

    Write goes through ``tenant_transaction(pool, TenantScope.commons())``:
    the tenant is bound as the first statement after BEGIN.
    """
    validate_scope(scope_type, scope_entity_id)

    context_id = uuid7()
    scope = TenantScope.commons()
    bound_tenant = tenant_id if tenant_id is not None else scope.tenant_id
    async with tenant_transaction(pool, scope) as conn:
        await conn.fetchrow(
            """
            INSERT INTO ingestion_contexts (
                id, source_ref, source_type, source_uri, source_hash,
                actor_id, workspace_id, environment_id,
                scope_type, scope_entity_id, visibility, owner_id, tenant_id,
                classification, extractor_id, extractor_version, run_ref, status
            ) VALUES (
                $1::uuid, $2::uuid, $3, $4, $5,
                $6, $7::uuid, $8,
                $9, $10, $11::visibility_level, $12, $13::uuid,
                $14, $15, $16, $17::uuid, 'open'
            )
            RETURNING id
            """,
            context_id, source_ref, source_type, source_uri, source_hash,
            actor_id, workspace_id, environment_id,
            scope_type, scope_entity_id, visibility, owner_id, bound_tenant,
            classification, extractor_id, extractor_version, run_ref,
        )
    return str(context_id)


async def complete_ingestion_context(
    pool: asyncpg.Pool, context_id: str, *, status: str = "completed",
) -> None:
    """Close a context: set ``completed_at`` and a terminal ``status``
    (``completed`` | ``failed`` | ``rejected`` -- ``open`` is not terminal
    and is rejected here rather than silently written)."""
    if status not in INGESTION_CONTEXT_STATUSES or status == "open":
        raise ValueError(
            f"complete_ingestion_context: status must be one of "
            f"{INGESTION_CONTEXT_STATUSES[1:]}, got {status!r}"
        )
    scope = TenantScope.commons()
    async with tenant_transaction(pool, scope) as conn:
        await conn.execute(
            "UPDATE ingestion_contexts SET status = $2, completed_at = now() "
            "WHERE id = $1::uuid",
            context_id, status,
        )


async def get_ingestion_context(pool: asyncpg.Pool, context_id: str) -> Optional[dict]:
    """One IngestionContext row by id as a plain dict, or None."""
    row = await pool.fetchrow(
        "SELECT * FROM ingestion_contexts WHERE id = $1::uuid",
        context_id,
    )
    return dict(row) if row else None
