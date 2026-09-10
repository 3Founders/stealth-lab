"""
Claim-keyed evidence: write and read evidence rows targeting a claim
(`target_type = 'claim'`), closing the exact gap
`app.services.claim_traversal.explain()`'s own docstring used to name
("no existing reader function fetches evidence keyed by claim id
anywhere in this codebase today").

This module invents nothing new at the storage or validation layer. It
composes over what already exists and is already tested elsewhere:

  - `app.execution.evidence.outcome_to_evidence()` -- the real, pure
    builder that validates an outcome into an `Evidence` model. Reused
    verbatim; this module never re-implements its checks.
  - `app.services.procedures.record_execution_outcome`'s real
    `INSERT INTO evidence (...)` call -- the only real writer of this
    table anywhere in the codebase before this file. `record_
    claim_evidence()` mirrors its exact column list and value-binding
    pattern (including the `tenant_id` column, sourced the same way:
    `TenantScope.commons().tenant_id` inside a `tenant_transaction`,
    per the repo's write-path convention -- CLAUDE.md "Write paths use
    `tenant_transaction`"). It does not target a procedure version, so
    `target_version` stays NULL, which db/24_evidence.sql's
    `evidence_proc_version_chk` only requires for `target_type =
    'procedure'` -- a claim target is free to leave it NULL.
  - db/24_evidence.sql -- `target_type IN ('claim', 'procedure',
    'implementation')` already allows `'claim'`; no migration needed.
    Evidence is [H] (append-only, tombstone-only UPDATE), so the reader
    below filters `t_invalid IS NULL` exactly like every other live-row
    read over this table.
"""
from __future__ import annotations

from typing import Any, Mapping, Optional

import asyncpg

from app.execution.evidence import outcome_to_evidence
from app.services.access import TenantScope, tenant_transaction

# Same provenance stamp idiom as procedures.py's OUTCOME_WRITER_STAMP --
# named, greppable, not a bare literal scattered through call sites.
CLAIM_EVIDENCE_WRITER_STAMP = "claim_evidence.record_claim_evidence@v1"


async def record_claim_evidence(
    pool: asyncpg.Pool,
    *,
    claim_id: str,
    evidence_type: str,
    outcome_status: str,
    success_criteria: Optional[Mapping[str, Any]] = None,
    direction: Optional[str] = None,
    strength_score: float = 1.0,
    strength_method: str = "recorded_outcome",
    independence_group: Optional[str] = None,
    context_key: Optional[str] = None,
    failure_class: Optional[str] = None,
    created_by: Optional[str] = None,
    visibility: str = "public",
    owner_id: Optional[str] = None,
) -> str:
    """
    Record one real evidence row targeting a claim.

    Builds a validated `Evidence` object via the real
    `outcome_to_evidence()` with
    `target={"target_type": "claim", "target_id": claim_id,
    "target_version": None}` (claims are row-id-addressed --
    `knowledge_nodes` has no version column, so a claim target never
    carries one, matching `_require_target`'s own rule in
    `app/execution/evidence.py`), then does the real
    `INSERT INTO evidence (...)` -- the exact column list and
    value-binding pattern `procedures.py::record_execution_outcome`'s
    real writer uses, so there is exactly one real evidence INSERT
    shape in this codebase, targeted at three different tables' worth
    of rows.

    `tenant_id` rides on `TenantScope.commons().tenant_id` inside a
    `tenant_transaction`, the same default `record_execution_outcome`
    falls back to when no caller-supplied scope is given -- claim
    evidence has no tenant-scoped caller yet, so Commons (today's
    shared-commons posture) is the honest default rather than inventing
    a parameter this module has no real caller for.

    `independence_group` (V4-hardening §14 / B10): rows sharing a named
    group NEVER count as independent corroboration of each other
    (`db/24_evidence.sql` CHECK + the `DISTINCT COALESCE(independence_group,
    id::text)` aggregators). Before this parameter existed every
    claim-evidence row was self-grouped (NULL), so five writes derived
    from the SAME source each inflated a claim's independent-evidence
    count. A caller that knows two rows come from one underlying source
    (same document, same deterministic fixture, same execution replayed)
    MUST pass the same non-blank string for both. NULL stays the default:
    self-grouped, i.e. genuinely independent. The real
    `outcome_to_evidence()`/`validate_evidence()` gate rejects a blank
    string verbatim -- this module does not re-implement that check.

    Returns the new evidence row's real `id` (as `str`).
    """
    evidence = outcome_to_evidence(
        evidence_type=evidence_type,
        target={
            "target_type": "claim",
            "target_id": claim_id,
            "target_version": None,
        },
        outcome_status=outcome_status,
        success_criteria=success_criteria,
        direction=direction,
        strength_score=strength_score,
        strength_method=strength_method,
        independence_group=independence_group,
        context_key=context_key,
        failure_class=failure_class,
        created_by=created_by or CLAIM_EVIDENCE_WRITER_STAMP,
        visibility=visibility,
        owner_id=owner_id,
    )

    scope = TenantScope.commons()
    async with tenant_transaction(pool, scope) as conn:
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
            RETURNING id
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
    return str(inserted["id"])


async def get_claim_evidence(pool: asyncpg.Pool, claim_id: str) -> list[dict]:
    """
    Every live evidence row for one claim, oldest first.

    Bounded, real reader: `evidence` is [H] (append-only per
    db/24_evidence.sql's own contract -- retraction is the one legal
    UPDATE, via the `t_invalid` tombstone), so this is a plain filtered
    scan, not a traversal -- exactly the reader
    `claim_traversal.explain()`'s own docstring said did not exist yet.
    """
    rows = await pool.fetch(
        """
        SELECT * FROM evidence
        WHERE target_type = 'claim' AND target_id = $1::uuid
          AND t_invalid IS NULL
        ORDER BY t_valid ASC
        """,
        claim_id,
    )
    return [dict(row) for row in rows]
