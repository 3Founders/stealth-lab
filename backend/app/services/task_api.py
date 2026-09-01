"""
Task API composition (directive Sec 32.4): a read-only view of one
`task_nodes` row -- its own fields, the procedures that reference it, and
capability/failure signal composed from the REAL evidence-based substrate,
never reimplemented or fabricated.

WHY THIS MODULE EXISTS (mirrors the other read-API lanes' `*_api.py`
pattern): the router (`app/api/tasks.py`) stays a thin FastAPI shell;
this module holds the actual composition so it is independently testable
without the ASGI layer.

HONEST SCOPE, confirmed by direct inspection before writing a line here:

  - `task_nodes` (db/01_ontology.sql, extended by migrations 03/11/21) has
    no `verification_stats`/evidence of its own, and
    `app/models/evidence.py::TargetType` is a CLOSED
    `Literal["claim", "procedure", "implementation"]` -- "task" is not a
    member. There is no first-class task-level evidence anywhere in the
    schema. So "capability statistics" and "known failure modes" for a
    task are aggregated over the task's DEPENDENT PROCEDURES' real
    evidence rows -- never synthesized at the task level itself. A task
    with zero dependent procedures returns empty lists for both, honestly.

  - "Dependent procedures" is the real join found by inspection, not a
    guess -- exactly two mechanisms exist in this codebase that connect a
    `procedures` row to a `task_nodes` row, and both are honored here:
      1. `procedures.migrated_from_task_node_id` -- a direct FK
         (db/18_procedures.sql:108), the provenance pointer for a
         procedure migrated FROM a task_node.
      2. `edges` rows with `edge_type='OWNS'`,
         `custom_edge_type='DECOMPOSES_TO'`, `source_table='procedures'`,
         `target_table='task_nodes'` -- written by
         `app/services/skill_ingestion.py::_write_task_nodes`, the real
         SKILL.md ingestion compiler's own step-decomposition write path.
    No other writer links the two tables (grepped, not assumed).

  - Capability computation reuses `procedure_extraction/capability.py`'s
    `compute_capability`/`OutcomeRecord` via
    `procedure_extraction/failure_handlers.py::capability_for_stream` --
    the SAME function `applicability.py`'s own `explain_procedure`-style
    verdict narrative and `handle_capability_demotion` already call, with
    the SAME evidence query shape (target_type='procedure', exact
    (target_id, target_version) pin, `DEMOTION_EVIDENCE_TYPES`/
    `DEMOTION_STREAM_LIMIT`). Nothing here reimplements Wilson intervals,
    banding, or routing.

  - PRE-EXISTING QUIRK OBSERVED, NOT FIXED HERE (out of this task's
    scope): both of that query's real call sites
    (`applicability.py:1002`, `failure_handlers.py:219`) filter
    `direction = 'supports'`, while `outcome_to_evidence()`
    (`app/execution/evidence.py`) defaults a FAILURE outcome's direction
    to `'contradicts'`. Reused verbatim here for consistency with the
    established pattern; flagged in this module's own PR report rather
    than silently changed.

  - "Known failure modes" reads `evidence.failure_class` directly
    (a real, first-class column -- db/24_evidence.sql, spec v4 SS36's six
    causes + `false_reuse`) WITHOUT the `direction='supports'` filter
    above, because failure evidence is written with
    `direction='contradicts'` (`outcome_to_evidence`'s own default) and a
    `direction='supports'` filter would silently hide every real failure
    mode that exists. This is a deliberately DIFFERENT, more honest query
    than the capability-stream one, not an inconsistency.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.services.access import AccessScope, TenantScope, scope_predicates
from app.services.procedure_extraction.failure_handlers import (
    DEMOTION_EVIDENCE_TYPES,
    DEMOTION_STREAM_LIMIT,
    capability_for_stream,
)

_DEMOTION_TYPES_SQL = ", ".join(f"'{t}'" for t in DEMOTION_EVIDENCE_TYPES)

_TASK_NODE_FIELDS = (
    "id", "name", "description", "io_schema", "skill_ref", "success_criteria",
    "cost_estimate", "latency_estimate_ms", "pert_optimistic_ms",
    "pert_likely_ms", "pert_pessimistic_ms", "provenance",
    "scope_type", "scope_entity_id", "t_created",
)

_PROCEDURE_SUMMARY_FIELDS = (
    "id", "procedure_id", "version", "name", "goal",
    "verification_state", "staleness", "availability", "approval_status",
    "t_created",
)


async def _fetch_task_node(
    pool: asyncpg.Pool, task_node_id: str, *, scope: AccessScope, tenant: TenantScope,
) -> Optional[dict]:
    scope_sql, scope_params, _next_idx = scope_predicates(scope, tenant, param_index=2)
    row = await pool.fetchrow(
        f"SELECT * FROM task_nodes WHERE id = $1::uuid AND t_invalid IS NULL AND {scope_sql}",
        task_node_id, *scope_params,
    )
    return dict(row) if row else None


async def _dependent_procedures(
    pool: asyncpg.Pool, task_node_id: str, *, scope: AccessScope, tenant: TenantScope,
) -> list[dict]:
    """Live (t_invalid IS NULL) procedure version rows naming this
    task_node via either of the two real joins documented above."""
    scope_sql, scope_params, _next_idx = scope_predicates(scope, tenant, alias="p", param_index=2)
    rows = await pool.fetch(
        f"""
        SELECT p.* FROM procedures p
        WHERE p.t_invalid IS NULL
          AND {scope_sql}
          AND (
            p.migrated_from_task_node_id = $1::uuid
            OR EXISTS (
              SELECT 1 FROM edges e
              WHERE e.t_invalid IS NULL
                AND e.edge_type = 'OWNS'
                AND e.custom_edge_type = 'DECOMPOSES_TO'
                AND e.source_table = 'procedures'
                AND e.source_id = p.id
                AND e.target_table = 'task_nodes'
                AND e.target_id = $1::uuid
            )
          )
        ORDER BY p.t_created DESC
        """,
        task_node_id, *scope_params,
    )
    return [dict(r) for r in rows]


async def _capability_for_procedure(pool: asyncpg.Pool, procedure: dict) -> dict:
    """Exact reuse of applicability.py/failure_handlers.py's own
    evidence-stream shape and `capability_for_stream()` call -- see this
    module's docstring for the direction='supports' quirk this
    deliberately mirrors rather than silently changes."""
    stream_rows = await pool.fetch(
        f"""
        SELECT outcome_status, context_key, independence_group
        FROM evidence
        WHERE target_type = 'procedure'
          AND target_id = $1::uuid AND target_version = $2
          AND t_invalid IS NULL
          AND direction = 'supports'
          AND evidence_type IN ({_DEMOTION_TYPES_SQL})
          AND outcome_status IN ('success', 'failure')
        ORDER BY t_created ASC, id ASC
        LIMIT {int(DEMOTION_STREAM_LIMIT)}
        """,
        procedure["id"], procedure["version"],
    )
    record = capability_for_stream("procedure", str(procedure["id"]), stream_rows)
    return {
        "procedure_row_id": str(procedure["id"]),
        "procedure_id": str(procedure["procedure_id"]) if procedure.get("procedure_id") else None,
        "version": procedure["version"],
        "p_estimate": record.p_estimate,
        "p_lower": record.p_lower,
        "p_upper": record.p_upper,
        "evidence_count": record.evidence_count,
        "success_count": record.success_count,
        "independent_groups": record.independent_groups,
        "environments_held": record.environments_held,
        "level": record.level,
        "level_label": record.level_label,
        "routing": record.routing.value,
    }


async def _known_failure_modes(pool: asyncpg.Pool, procedures: list[dict]) -> list[str]:
    """Distinct real `evidence.failure_class` values recorded against
    this task's dependent procedure versions. Deliberately NOT filtered
    by direction (see module docstring) -- and deliberately empty, never
    fabricated, when no such evidence exists."""
    if not procedures:
        return []
    ids = [str(p["id"]) for p in procedures]
    versions = [p["version"] for p in procedures]
    rows = await pool.fetch(
        """
        SELECT DISTINCT failure_class
        FROM evidence
        WHERE target_type = 'procedure'
          AND t_invalid IS NULL
          AND failure_class IS NOT NULL
          AND (target_id, target_version) IN (
              SELECT unnest($1::uuid[]), unnest($2::int[])
          )
        """,
        ids, versions,
    )
    return sorted({r["failure_class"] for r in rows if r["failure_class"]})


async def get_task_detail(
    pool: asyncpg.Pool, task_node_id: str, *, scope: AccessScope,
) -> Optional[dict]:
    """The one real composition entry point `app/api/tasks.py` calls.

    Returns None when the task_node does not exist, is not live, or is
    invisible to `scope` -- the router turns that into 404, same
    anti-enumeration posture `app/api/graph.py` already establishes
    (omit, never a placeholder that still reveals existence).
    """
    tenant = TenantScope.unrestricted()  # same posture as app/api/graph.py

    task_node = await _fetch_task_node(pool, task_node_id, scope=scope, tenant=tenant)
    if task_node is None:
        return None

    procedures = await _dependent_procedures(pool, task_node_id, scope=scope, tenant=tenant)
    capability_statistics = [
        await _capability_for_procedure(pool, proc) for proc in procedures
    ]
    known_failure_modes = await _known_failure_modes(pool, procedures)

    return {
        **{k: task_node.get(k) for k in _TASK_NODE_FIELDS},
        "dependent_procedures": [
            {k: proc.get(k) for k in _PROCEDURE_SUMMARY_FIELDS} for proc in procedures
        ],
        "capability_statistics": capability_statistics,
        "known_failure_modes": known_failure_modes,
    }
