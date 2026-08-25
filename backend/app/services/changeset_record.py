"""Universal ChangeSet recording (Band 1.9c, invariant #7).

Every mutation of a [V] object must produce a ChangeSet record. Until this
module existed, `models/change.py` represented debate *proposals* only --
nothing recorded what actually mutated after approval. This module is the
post-hoc record: append-only rows in `change_sets` /
`change_set_operations` (db/25), one call at each service mutation boundary.

Coverage honesty (v1): wired today into procedure lifecycle mutations
(approve / reject / quarantine-disable) because those are the [V]-mutations
that exist. Supersession/revision paths for knowledge_nodes and
observations wire in as the §20 revision machinery builds them -- the
function below is their single entry point, so coverage grows by adding
one call per new boundary, never by new machinery.

Offline-testable: touches the pool only through execute()/executemany(),
so a FakePool capturing statements proves emission without a database.
"""
from __future__ import annotations

from typing import Any, Optional
from uuid import UUID

import asyncpg
import json
from pydantic import BaseModel, Field, ValidationError

from app.services.authn import current_actor_id

# [V] tables that exist today (db/25 CHECK enforces the same list at the
# engine level; this duplicate exists so producers fail fast with a good
# message instead of an engine error).
TARGET_TABLES = frozenset({
    "knowledge_nodes", "task_nodes", "procedures",
    "observations", "states", "evidence",
})

OPERATIONS = frozenset({
    "invalidate", "revise", "create_version", "create", "relate",
    "status_change",
})


class ChangeOperation(BaseModel):
    operation: str
    target_table: str
    target_id: Optional[str] = None
    detail: dict[str, Any] = Field(default_factory=dict)

    def validate_against_whitelists(self) -> None:
        if self.operation not in OPERATIONS:
            raise ValueError(
                f"unknown ChangeSet operation {self.operation!r} (valid: {sorted(OPERATIONS)})"
            )
        if self.target_table not in TARGET_TABLES:
            raise ValueError(
                f"ChangeSet target_table {self.target_table!r} is not a [V] object "
                f"(valid: {sorted(TARGET_TABLES)})"
            )


class ChangesetRecordError(ValueError):
    """Raised when a ChangeSet record would violate the contract."""


async def record_change_set(
    pool: asyncpg.Pool,
    *,
    author: Optional[str] = None,
    reason: str,
    operations: list[ChangeOperation],
    scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None,
) -> UUID:
    """Append one ChangeSet + its operations. Returns the ChangeSet id.

    Fails closed: no operations -> refuse (an empty record would let a
    boundary claim coverage it does not have); any invalid operation ->
    refuse before touching the pool.

    Band 2.9 attribution: an explicit `author` always wins (existing
    callers — procedures.py's lifecycle boundaries, the quarantine timer —
    are untouched). Omitted, the author resolves from the authenticated
    request actor (authn contextvar); resolving to nothing raises rather
    than recording an unattributed mutation — "nothing is born
    unattributed" now has a propagation path, not just a demand.
    """
    if author is None:
        author = current_actor_id()
    if not author or not author.strip():
        raise ChangesetRecordError(
            "ChangeSet requires an author — nothing is born unattributed "
            "(pass one explicitly or call from an authenticated request)"
        )
    if not operations:
        raise ChangesetRecordError(
            "refusing to record an empty ChangeSet — boundaries must declare "
            "what mutated or not call this function"
        )
    try:
        for op in operations:
            op.validate_against_whitelists()
    except ValidationError as exc:  # defensive; callers build typed ops
        raise ChangesetRecordError(str(exc)) from exc

    cs_id: UUID = await pool.fetchval(
        "INSERT INTO change_sets (author, reason, scope_type, scope_entity_id) "
        "VALUES ($1, $2, NULLIF($3, ''), NULLIF($4, '')) RETURNING id",
        author, reason, scope_type, scope_entity_id,
    )
    await pool.executemany(
        "INSERT INTO change_set_operations "
        "(change_set_id, operation, target_table, target_id, detail) "
        "VALUES ($1, $2, $3, $4, $5::jsonb)",
        [
            (cs_id, op.operation, op.target_table, op.target_id,
             json.dumps(op.detail))
            for op in operations
        ],
    )
    return cs_id


def status_change(procedure_row_id: str, detail: dict[str, Any]) -> ChangeOperation:
    """Convenience builder for procedure lifecycle status mutations."""
    return ChangeOperation(
        operation="status_change", target_table="procedures",
        target_id=procedure_row_id, detail=detail,
    )
