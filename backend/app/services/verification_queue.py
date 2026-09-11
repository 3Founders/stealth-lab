"""
G24 residual (audit doc): "independent global re-verification is
recorded-as-required but not executed."

`app.services.publication_deps.traverse_publication_dependencies` already
computes `global_verification_required` correctly on every publish (True
unless the traversal found >=2 independent public verification groups).
`publish_procedure` (publication.py) already stores that flag in
`publication_records.classification_report`. But nothing ever consumed
it -- there was no durable, queryable record of "this procedure needs
independent re-verification," only a boolean buried in one publish call's
own JSON blob.

This module is that durable queue -- migration 74's
`pending_global_verifications` table -- populated by `publish_procedure`
at the exact point it already computes the requirement. It is
DELIBERATELY, per founder-established precedent (migration 73's
`claim_relation_candidates`), a REVIEW QUEUE, not an auto-executing
re-verification pipeline:

    - This module NEVER flips `procedures.verification_state`.
    - This module NEVER decides what counts as "independent enough" --
      that determination already happened once, correctly, in
      publication_deps.py; this only records that the determination
      came back "required."
    - Resolving a row here is bookkeeping (a human, or a later-decided
      mechanism, looked at it) -- promoting the procedure to verified
      remains the existing, separate evidence.py / claim_belief-style
      write path, untouched by this module.
"""
from __future__ import annotations

from typing import Optional

import asyncpg

from app.services.access import TenantScope, tenant_transaction
from app.utils.ids import uuid7

STATUSES = ("pending", "resolved", "dismissed")


async def record_pending_global_verification(
    pool: asyncpg.Pool, *, procedure_id: str, procedure_row_id: str,
    publication_id: Optional[str], reason: str, created_by: str,
) -> str:
    """One row per publish that came back `global_verification_required=
    True`. Not idempotent on (procedure_id) -- a procedure can be
    re-published/re-versioned and each occasion where the requirement
    still holds is its own real, dated queue entry, not a thing to
    collapse into one."""
    row_id = uuid7()
    scope = TenantScope.commons()
    async with tenant_transaction(pool, scope) as conn:
        await conn.execute(
            "INSERT INTO pending_global_verifications ("
            "id, procedure_id, procedure_row_id, publication_id, reason, created_by"
            ") VALUES ($1::uuid, $2::uuid, $3::uuid, $4::uuid, $5, $6)",
            row_id, procedure_id, procedure_row_id, publication_id, reason, created_by,
        )
    return str(row_id)


async def get_pending_global_verifications(pool: asyncpg.Pool, *, limit: int = 100) -> list[dict]:
    """The review queue: every `status='pending'` row, oldest first, joined
    to the procedure's live name/goal/verification_state so a reviewer
    doesn't have to look the procedure up separately."""
    rows = await pool.fetch(
        """
        SELECT v.id, v.procedure_id, v.procedure_row_id, v.publication_id,
               v.reason, v.created_by, v.t_created,
               p.name AS procedure_name, p.goal AS procedure_goal,
               p.verification_state
        FROM pending_global_verifications v
        JOIN procedures p ON p.id = v.procedure_row_id
        WHERE v.status = 'pending'
        ORDER BY v.t_created ASC
        LIMIT $1
        """,
        limit,
    )
    return [dict(r) for r in rows]


async def resolve_pending_global_verification(
    pool: asyncpg.Pool, *, entry_id: str, resolution: str, resolved_by: str,
    status: str = "resolved",
) -> None:
    """Mark one queue entry reviewed. Does NOT touch procedures.
    verification_state -- see this module's own docstring."""
    if status not in STATUSES:
        raise ValueError(f"status must be one of {STATUSES}, got {status!r}")
    scope = TenantScope.commons()
    async with tenant_transaction(pool, scope) as conn:
        await conn.execute(
            "UPDATE pending_global_verifications SET status = $2, resolution = $3, "
            "resolved_by = $4, resolved_at = now() WHERE id = $1::uuid",
            entry_id, status, resolution, resolved_by,
        )
