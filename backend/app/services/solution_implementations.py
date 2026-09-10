"""
Detailed, durable implementation descriptors for a Solution (directive
Sec 53), ADDITIVE to `procedure_graph_api.get_solution_view`'s existing
KIND-level `implementations: {kind: {...ImplementationResolution...}}`
field -- this module does not modify that function or its return shape
(both off-limits per this task's own briefing). It answers a narrower,
different question: "which REAL, DURABLE `implementations` rows (registry
identity, not just a runnable KIND) are actually linked to this
procedure, if any."

TWO LINK SOURCES, UNIONED (v4-hardening Sec B23):

  1. `procedure_implementations` -- the first-class ProcedureImplementation
     relation (migration 67). Keyed by the STABLE `procedures.procedure_id`,
     it is the general case: any procedure can bind an implementation in a
     role, with no task_node involvement. Read via
     `procedure_implementations.list_implementations_for_procedure`.
  2. `procedures.migrated_from_task_node_id` -> `implementation_tasks` ->
     `implementations` -- the ORIGINAL, narrower path (added when a
     procedure was migrated from a task_node's htn_method_library entry).
     A procedure captured directly via `capture_procedure` carries `NULL`
     here, which is honest, not an error.

Before migration 67 this module used ONLY source 2 and therefore returned
`[]` for the common case (`migrated_from_task_node_id IS NULL`). It now
also consults source 1, so a directly-captured procedure with real
`procedure_implementations` bindings returns a non-empty list.

Results are de-duped by `implementation_id`. Each row is annotated with
`source` ('procedure_implementations' | 'implementation_tasks') and
`role`. When an implementation is reachable via BOTH sources, the
relation row wins (it carries the richer edge metadata) and its
`also_via` list records the task path.

Returns `None` for a missing/invisible procedure (mirrors every other
`procedure_graph_api` reader's contract, so the router can 404 the same
way). Returns `[]` for a visible procedure with no linked implementation
from either source.

Exposed as its own endpoint (`GET /v1/solutions/{procedure_row_id}/
implementations`) rather than folded into `get_solution_view`'s payload,
per this task's own PART 4 instruction: safer, zero risk to the already-
tested Wave 1/2 solution view contract.
"""
from __future__ import annotations

from typing import Optional

import asyncpg

from app.execution import implementation_registry
from app.services import procedure_implementations
from app.services.access import AccessScope, visibility_predicate


async def get_solution_implementation_detail(
    pool: asyncpg.Pool, procedure_row_id: str, *, scope: AccessScope,
) -> Optional[list[dict]]:
    """
    Real, durable implementation rows linked to this procedure, unioned
    across the `procedure_implementations` relation (live, binding
    `status='active'`) and the legacy
    `migrated_from_task_node_id -> implementation_tasks` path
    (`status='active'`, via `implementation_registry.get_for_task`, whose
    own visibility filter applies to that source).

    De-duped by `implementation_id`; every row gains `source` and `role`
    keys. The task_node-sourced rows keep their existing
    `implementation_registry` key set unchanged -- only additions.
    """
    vis_sql, vis_params = visibility_predicate(scope, param_index=2)
    procedure = await pool.fetchrow(
        f"SELECT id, procedure_id, migrated_from_task_node_id FROM procedures "
        f"WHERE id = $1::uuid AND {vis_sql}",
        procedure_row_id, *vis_params,
    )
    if procedure is None:
        return None

    merged: dict[str, dict] = {}

    # Source 1: the generalized ProcedureImplementation relation.
    procedure_id = procedure["procedure_id"]
    if procedure_id is not None:
        for row in await procedure_implementations.list_implementations_for_procedure(
            pool, str(procedure_id), status="active",
        ):
            impl_id = str(row.get("implementation_id") or row["id"])
            row.setdefault("implementation_id", impl_id)
            row["source"] = "procedure_implementations"
            row.setdefault("role", "primary")
            merged[impl_id] = row

    # Source 2: the original task_node-attributed path. Unchanged contract.
    task_node_id = procedure["migrated_from_task_node_id"]
    if task_node_id is not None:
        for row in await implementation_registry.get_for_task(
            pool, str(task_node_id), scope=scope, status="active",
        ):
            impl_id = str(row["id"])
            if impl_id in merged:
                merged[impl_id].setdefault("also_via", []).append("implementation_tasks")
                continue
            row["source"] = "implementation_tasks"
            row.setdefault("implementation_id", impl_id)
            row.setdefault("role", "primary")
            merged[impl_id] = row

    return list(merged.values())
