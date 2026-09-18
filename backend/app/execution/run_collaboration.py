"""
Collaboration records on an execution run: NOTE / BLOCKER / HANDOFF /
QUESTION / ANSWER, plus BLOCKER_RESOLVED / HANDOFF_ACCEPTED (migration 91)
-- the structured record type `.stealth/run.md` has never had (see
migration 90's own docstring for why this is a new table rather than an
extension of the workspace-local `app.stealth.journal`).

CLOSING A BLOCKER OR HANDOFF (migration 91): reuses the SAME `answers_id`
cross-reference mechanism ANSWER already established for QUESTION,
applied twice more rather than reinvented -- `RESOLUTION_TARGET_KIND`
below is the one place that says "a record of THIS kind must have
`answers_id` pointing at a record of THAT kind". Two new, narrow kinds
(not a general lifecycle/state machine): a BLOCKER_RESOLVED/HANDOFF_
ACCEPTED record is itself immutable and permanent, same as every other
record here -- "closing" a blocker means recording a NEW record that
references it, never mutating or deleting the original BLOCKER/HANDOFF
row (see migration 91's own docstring for why two kinds instead of
overloading ANSWER's target check).

This is deliberately NOT a second ownership/lease system: a collaboration
record never grants, revokes, or implies ownership of a node or its
files -- that stays entirely on `execution_run_nodes`' own
`owner_agent_id`/`file_intent_lease_expires_at` columns
(`app.execution.coordination`). An agent cannot "claim" a node by writing
a HANDOFF here; it must still go through `declare_file_intent`. This
module only records what agents want other agents to know.

Same DB-access discipline as `app.execution.coordination`: plain
`pool.fetch`/`fetchrow`/`execute`, no ORM, no ownership side effects.
"""
from __future__ import annotations

from typing import Optional

import asyncpg

KINDS = ("NOTE", "BLOCKER", "HANDOFF", "QUESTION", "ANSWER", "BLOCKER_RESOLVED", "HANDOFF_ACCEPTED")

# A "resolving" kind's own `answers_id` must reference a record of the
# named kind, in the same run -- the single source of truth both
# `record_run_update`'s validation and (indirectly, via what gets
# recorded) `pipe_format._collab_summary`'s open/pending counts rely on.
RESOLUTION_TARGET_KIND = {
    "ANSWER": "QUESTION",
    "BLOCKER_RESOLVED": "BLOCKER",
    "HANDOFF_ACCEPTED": "HANDOFF",
}


class RunCollaborationError(ValueError):
    """Raised for a malformed or referentially-invalid
    `record_run_update` call -- an unknown kind, an `answers_id` that does
    not point at the RIGHT kind of record (per `RESOLUTION_TARGET_KIND`)
    in the SAME run, or a run that does not exist. Never silently coerced
    into a different, "close enough" record."""


async def record_run_update(
    pool: asyncpg.Pool, *, execution_run_id: str, kind: str, body: str, actor_agent_id: str,
    node_order: Optional[int] = None, answers_id: Optional[str] = None,
    target_agent_id: Optional[str] = None,
) -> dict:
    """
    Insert one collaboration record. Validates, then writes -- never
    writes a record it cannot make sense of later:

      - `kind` must be one of `KINDS`.
      - `execution_run_id` must be a real run (a typo'd id is refused,
        not silently orphaned).
      - `answers_id` is only meaningful for a "resolving" kind
        (`RESOLUTION_TARGET_KIND` -- ANSWER, BLOCKER_RESOLVED,
        HANDOFF_ACCEPTED); when given, it must reference an existing
        record of the EXACT expected kind in the SAME run -- answering a
        question from a different run, "resolving" a HANDOFF with
        `kind="BLOCKER_RESOLVED"`, or referencing a non-existent id is
        refused rather than recorded as a dangling/misleading
        cross-reference. Every resolving kind REQUIRES `answers_id`.
      - `target_agent_id` is accepted for any kind (it is informational,
        not enforced) but is only ever populated by callers for HANDOFF;
        no validation is done against a registry of known agents (none
        exists in this codebase).

    Returns the inserted row as a plain dict.
    """
    if kind not in KINDS:
        raise RunCollaborationError(f"kind must be one of {KINDS}, got {kind!r}")
    if not body or not body.strip():
        raise RunCollaborationError("body must be non-empty")

    run_row = await pool.fetchrow("SELECT id FROM execution_runs WHERE id = $1::uuid", execution_run_id)
    if run_row is None:
        raise RunCollaborationError(f"execution_run_id {execution_run_id!r} not found")

    expected_target_kind = RESOLUTION_TARGET_KIND.get(kind)
    if answers_id is not None:
        if expected_target_kind is None:
            raise RunCollaborationError(
                f"answers_id is only valid for kind in {sorted(RESOLUTION_TARGET_KIND)}, got kind={kind!r}"
            )
        target_row = await pool.fetchrow(
            "SELECT id FROM run_collaboration_records "
            "WHERE id = $1::uuid AND execution_run_id = $2::uuid AND kind = $3",
            answers_id, execution_run_id, expected_target_kind,
        )
        if target_row is None:
            raise RunCollaborationError(
                f"answers_id {answers_id!r} does not reference a {expected_target_kind} "
                f"record in run {execution_run_id!r}"
            )
    elif expected_target_kind is not None:
        raise RunCollaborationError(f"kind={kind!r} requires answers_id (pointing at the {expected_target_kind} it resolves)")

    row = await pool.fetchrow(
        """
        INSERT INTO run_collaboration_records
            (execution_run_id, node_order, kind, actor_agent_id, body, answers_id, target_agent_id)
        VALUES ($1::uuid, $2, $3, $4, $5, $6::uuid, $7)
        RETURNING *
        """,
        execution_run_id, node_order, kind, actor_agent_id, body.strip(), answers_id, target_agent_id,
    )
    return dict(row)


async def list_run_collaboration(pool: asyncpg.Pool, execution_run_id: str) -> list[dict]:
    """Every collaboration record for a run, oldest first -- the same
    order `render_run_md` renders them in. Pure read, no folding: a
    BLOCKER/HANDOFF's "resolved"/"accepted" state is derived at render
    time (`app.stealth.pipe_format._collab_summary`) from whether any
    BLOCKER_RESOLVED/HANDOFF_ACCEPTED record's `answers_id` points at it
    -- this function just returns the flat, immutable history."""
    rows = await pool.fetch(
        "SELECT * FROM run_collaboration_records WHERE execution_run_id = $1::uuid ORDER BY created_at ASC",
        execution_run_id,
    )
    return [dict(r) for r in rows]
