"""
Canonical Goal object (backend/db/83_goals.sql) -- founder directive
"ingestion.md" Sec 2: Goal is the stable-ID linking primitive between
Procedure and Implementation, deliberately lighter-weight than either
(no steps, no invariants, no verification statistics -- those stay on
`procedures`/`implementations`).

This module owns the ONE write path (find_or_create_goal) so every
caller -- app/services/procedures.py::capture_procedure and
app/services/implementation_goals.py's enrichment job today, any future
ingestion source tomorrow -- gets the same dedup discipline: exact
normalized-name match first (ingestion.md Sec 8 tier 1), enforced twice
(here, and again by migration 83's own partial unique indexes -- a
concurrent caller racing this function still cannot create two rows for
the same (normalized_name, scope) pair).

NOT YET implemented (real, intentional gaps -- ingestion.md Sec 8's
tiers 2-5, not silently claimed done):
  - alias-based dedup (tier 2) -- `aliases` is populated at creation
    (the caller's own alternate phrasings, if any) but a *lookup* by
    alias, not just canonical_name, does not exist yet.
  - semantic/embedding similarity dedup (tiers 3-4) -- no embedding
    column on `goals` (ingestion.md Sec 17's "Goals: embed globally" is
    retrieval-layer scope, not this write path's).
  - LLM adjudication for ambiguous near-duplicates (tier 5).
  - explicit merge/review workflow (ingestion.md Sec 8's last
    paragraph) -- `goals.status='merged'` + `merged_into_id` exist as
    schema support, but nothing here flags "these two rows look
    related, review them" or performs a merge. A near-duplicate today
    either collides exactly (same row) or silently becomes a distinct
    row.
"""
from __future__ import annotations

import re
from typing import Any, Optional

import asyncpg

from app.utils.ids import uuid7
from app.services.v0_gate import validate_provenance, validate_scope

_NON_ALNUM_RE = re.compile(r"[^a-z0-9 ]")
_WHITESPACE_RE = re.compile(r"\s+")


def normalize_goal_name(text: str) -> str:
    """Exact-match dedup key (ingestion.md Sec 8 tier 1): lowercase,
    replace non-alphanumerics with spaces, collapse whitespace, trim.

    MUST stay in exact lock-step with its SQL twin,
    `normalize_goal_name()` in backend/db/83_goals.sql -- both are
    relied on to agree bit-for-bit (this function for new writes via
    find_or_create_goal, the SQL function for migration 83's own
    backfill and any future direct-SQL query). See
    tests/test_goals_offline.py for the parity check against a real DB.

    Deliberately NOT stemming or semantic normalization -- "find
    references" and "find callers" stay distinct rows under this alone
    (tiers 3-5 would be needed to unify those; not implemented, see
    module docstring).
    """
    lowered = text.strip().lower()
    stripped = _NON_ALNUM_RE.sub(" ", lowered)
    return _WHITESPACE_RE.sub(" ", stripped).strip()


async def find_or_create_goal(
    pool: asyncpg.Pool,
    *,
    canonical_name: str,
    scope_type: str,
    scope_entity_id: Optional[str] = None,
    provenance: str,
    description: Optional[str] = None,
    expected_outcome: Optional[dict] = None,
    verification_requirement: Optional[dict] = None,
    status: str = "candidate",
    owner_id: Optional[str] = None,
    visibility: str = "public",
    created_from: Optional[str] = None,
    aliases: Optional[list[str]] = None,
    created_by: Optional[str] = None,
) -> dict[str, Any]:
    """Exact-normalized-name dedup, then insert if nothing matched.
    Returns {"id": str, "canonical_name": str, "created": bool}.

    `scope_type`/`provenance` are REQUIRED and V0-gated (Band 1.3
    discipline, same as `capture_procedure` -- "nothing enters without
    scope + provenance"), not defaulted to a silent 'global' -- callers
    must make an explicit choice, same as every other write path in this
    codebase.

    Scope follows the same global/local split access.py's
    scope_predicates() already enforces everywhere else: a 'global'
    scope_type dedups across the WHOLE corpus; any other scope_type
    dedups only within that (scope_type, scope_entity_id) pair
    (ingestion.md Sec 9 -- a local goal at one project must not silently
    collide with a same-named local goal at a different one). Enforced
    twice: the SELECT below, and migration 83's own partial unique
    indexes as a backstop against a concurrent racer.
    """
    resolved_scope_type, resolved_scope_entity_id = validate_scope(
        scope_type, scope_entity_id,
        allow_global_entity_id=bool(scope_type == "global" and scope_entity_id),
    )
    validate_provenance(provenance)

    normalized = normalize_goal_name(canonical_name)
    if not normalized:
        raise ValueError("canonical_name must contain at least one alphanumeric character")

    is_global = resolved_scope_type == "global"
    if is_global:
        existing = await pool.fetchrow(
            "SELECT id, canonical_name FROM goals "
            "WHERE normalized_name = $1 AND t_invalid IS NULL AND status <> 'merged' "
            "AND (scope_type IS NULL OR scope_type = 'global')",
            normalized,
        )
    else:
        existing = await pool.fetchrow(
            "SELECT id, canonical_name FROM goals "
            "WHERE normalized_name = $1 AND t_invalid IS NULL AND status <> 'merged' "
            "AND scope_type = $2 AND scope_entity_id = $3",
            normalized, resolved_scope_type, resolved_scope_entity_id,
        )
    if existing:
        return {
            "id": str(existing["id"]),
            "canonical_name": existing["canonical_name"],
            "created": False,
        }

    goal_id = uuid7()
    try:
        row = await pool.fetchrow(
            """
            INSERT INTO goals (
                id, canonical_name, normalized_name, description, expected_outcome,
                verification_requirement, status, provenance, created_from, owner_id,
                visibility, aliases, created_by, scope_type, scope_entity_id
            ) VALUES (
                $1, $2, $3, $4, $5::jsonb, $6::jsonb, $7, $8, $9, $10,
                $11::visibility_level, $12, $13, $14, $15
            )
            RETURNING id, canonical_name
            """,
            str(goal_id), canonical_name, normalized, description,
            expected_outcome if expected_outcome is not None else {},
            verification_requirement if verification_requirement is not None else {},
            status, provenance, created_from, owner_id, visibility,
            aliases or [], created_by, resolved_scope_type, resolved_scope_entity_id,
        )
    except asyncpg.UniqueViolationError:
        # Lost a race against a concurrent insert of the identical
        # (normalized_name, scope) pair -- migration 83's own partial
        # unique index caught it. Re-select the winner rather than
        # raising a spurious error for what is, semantically, a
        # successful dedup.
        return await find_or_create_goal(
            pool, canonical_name=canonical_name, scope_type=scope_type,
            scope_entity_id=scope_entity_id, provenance=provenance,
        )
    return {"id": str(row["id"]), "canonical_name": row["canonical_name"], "created": True}
