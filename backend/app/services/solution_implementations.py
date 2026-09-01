"""
Detailed, durable implementation descriptors for a Solution (directive
Sec 53), ADDITIVE to `procedure_graph_api.get_solution_view`'s existing
KIND-level `implementations: {kind: {...ImplementationResolution...}}`
field -- this module does not modify that function or its return shape
(both off-limits per this task's own briefing). It answers a narrower,
different question: "which REAL, DURABLE `implementations` rows (registry
identity, not just a runnable KIND) are actually linked to this
procedure's task, if any."

WHERE THE LINK COMES FROM: `procedures.migrated_from_task_node_id`
(`db/18_procedures.sql`) is the only real, first-class column connecting
a procedure row to a task_node -- populated when a procedure was migrated
from (or otherwise attributed to) a task_node's own htn_method_library
entry. A procedure captured directly via `capture_procedure` with no such
migration carries `NULL` here, which is an honest "this procedure has no
linked task_node," not an error -- this module returns `[]` in that case,
never a fabricated link.

Exposed as its own endpoint (`GET /v1/solutions/{procedure_row_id}/
implementations`) rather than folded into `get_solution_view`'s payload,
per this task's own PART 4 instruction: safer, zero risk to the already-
tested, already-committed Wave 1/2 solution view contract.
"""
from __future__ import annotations

from typing import Optional

import asyncpg

from app.execution import implementation_registry
from app.services.access import AccessScope, visibility_predicate


async def get_solution_implementation_detail(
    pool: asyncpg.Pool, procedure_row_id: str, *, scope: AccessScope,
) -> Optional[list[dict]]:
    """
    Real, durable implementation rows linked (via `implementation_tasks`)
    to this procedure's `migrated_from_task_node_id`, visibility-filtered
    by `implementation_registry.get_for_task()` itself (default
    `status='active'`, matching that function's own ordinary-retrieval
    default).

    Returns `None` for a missing/invisible procedure (mirrors every
    other `procedure_graph_api` reader's contract, so the router can 404
    the same way). Returns `[]` (not `None`) for a visible procedure with
    no linked task_node, or a linked task_node with no active durable
    implementations -- both honest empties, not errors.
    """
    vis_sql, vis_params = visibility_predicate(scope, param_index=2)
    procedure = await pool.fetchrow(
        f"SELECT id, migrated_from_task_node_id FROM procedures "
        f"WHERE id = $1::uuid AND {vis_sql}",
        procedure_row_id, *vis_params,
    )
    if procedure is None:
        return None

    task_node_id = procedure["migrated_from_task_node_id"]
    if task_node_id is None:
        return []

    return await implementation_registry.get_for_task(
        pool, str(task_node_id), scope=scope, status="active",
    )
