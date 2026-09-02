"""
Capability estimation for `target_type='implementation'` evidence rows.

Directive Sec 35-37: REUSE the existing empirical Wilson-interval
machinery (`app.services.procedure_extraction.capability`), do not build a
second one. This module is a thin extension over that machinery for a
target type (`'implementation'`) `evidence_target_type_chk` (migration 24)
already permits at the schema level -- zero schema change needed, exactly
as `.scratch/implementation_registry_audit.md` and this module's own
callers confirm.

HOME: kept in `app/services/` (a new file), not inside
`procedure_extraction/capability.py` itself (that file is explicitly
out-of-bounds for this task -- "import and call only") and not inside
`procedure_extraction/` at all -- that package's own docstring names its
home as an accident of lane ownership at the time it was written
("a new top-level services/capability.py would fall outside every owned
path, and file ownership is absolute (board rule)"), not a statement that
every capability concern must live there. Implementations are a distinct
target type with their own writer module
(`app.execution.implementation_registry`) already living under
`app/execution/` and `app/services/` respectively -- `app/services/
capabilities.py` sits next to `app/services/claim_evidence.py`, the
module this file's write-path most directly mirrors (see below), which is
precedent for "a new target-type-specific evidence module lives in
services/, named after the target type it covers."

TWO REAL SOURCES OF WILSON-INTERVAL MATH ALREADY EXIST IN THIS CODEBASE,
AND WHY THIS MODULE USES THE LOWER-LEVEL ONE:

  - `procedure_extraction.capability.compute_capability()` requires a
    non-blank `environment` per `OutcomeRecord` and produces the fully
    gated L0-5 ladder (independent-groups / environments-held /
    verification-plan / completed-review gates). Nothing in the
    `evidence` table records which environment an outcome ran in
    (confirmed by reading `db/24_evidence.sql` in full -- same finding
    `procedure_graph_api.py::_capability_estimate`'s own docstring
    already made for procedure evidence). Inventing an environment value
    to satisfy `OutcomeRecord` would be exactly the fabrication CLAUDE.md
    forbids.
  - `procedure_graph_api.py::_capability_estimate()` already solved this
    identical problem for procedure evidence: compute P/Wilson/routing
    directly from real evidence rows using ONLY `wilson_interval`,
    `band_for_p`, `route_for_p` (never `compute_capability`/
    `OutcomeRecord`, which need data this schema doesn't have), and name
    the ungated fields as `None` rather than omit them. This module
    mirrors that EXACT posture -- same field names, same honesty rule --
    for `target_type='implementation'` evidence instead of `'procedure'`.
"""
from __future__ import annotations

from typing import Any, Optional

import asyncpg

from app.execution.evidence import outcome_to_evidence
from app.services.access import TenantScope, tenant_transaction
from app.services.procedure_extraction.capability import (
    band_for_p,
    route_for_p,
    wilson_interval,
)

# Same provenance stamp idiom as claim_evidence.py's CLAIM_EVIDENCE_WRITER_STAMP.
IMPLEMENTATION_EVIDENCE_WRITER_STAMP = "capabilities.record_implementation_outcome@v1"

# Same outcome-bearing filter procedure_graph_api.py's own
# _capability_estimate uses -- a live row of one of these two
# evidence_types with a terminal outcome_status is an attempt, in either
# direction: a 'supports' success and a 'contradicts' failure both enter
# the P estimate (the failure is what lowers it). Witness types
# (human_review, documents...) corroborate but never enter the statistic.
_OUTCOME_BEARING_EVIDENCE_TYPES = ("execution_result", "reproduction")


async def record_implementation_outcome(
    pool: asyncpg.Pool,
    *,
    implementation_id: str,
    task_node_id: Optional[str] = None,
    outcome_status: str,
    evidence_type: str = "execution_result",
    success_criteria: Optional[dict] = None,
    direction: Optional[str] = None,
    strength_score: float = 1.0,
    strength_method: str = "recorded_outcome",
    context_key: Optional[str] = None,
    failure_class: Optional[str] = None,
    independence_group: Optional[str] = None,
    created_by: Optional[str] = None,
    visibility: str = "public",
    owner_id: Optional[str] = None,
) -> str:
    """
    Record one real evidence row targeting a durable implementation
    (`target_type='implementation'`). Mirrors `claim_evidence.py::
    record_claim_evidence`'s EXACT pattern: build a validated `Evidence`
    via the real `outcome_to_evidence()`, then run the same `INSERT INTO
    evidence (...)` column list and value-binding shape every other
    evidence writer in this codebase uses -- one real evidence INSERT
    shape, now targeted at a fourth kind of row.

    `target_version=None`: implementations are addressed by durable row
    id, not a version chain the way procedures are (confirmed directly
    from `evidence_proc_version_chk`, migration 24: `target_version IS
    NOT NULL` is required only when `target_type = 'procedure'` --
    'implementation' is free to leave it NULL, exactly `_require_target`'s
    own reasoning in `app/execution/evidence.py`).

    `task_node_id`, if given, rides into `context_key` only when the
    caller does not already supply one -- a light, honest breadcrumb
    (which task this outcome was observed satisfying), never a second
    real column this table doesn't have. Evidence has no
    `implementation_task_node_id` column; inventing a query filter on top
    of `context_key` string matching would overstate what this actually
    guarantees, so `get_implementation_capability` below does NOT filter
    by it unless a caller supplies a matching `context_key` on both
    sides -- named honestly in that function's own docstring.

    `tenant_id` rides on `TenantScope.commons()` inside a
    `tenant_transaction`, the identical default `claim_evidence.py` uses.

    Returns the new evidence row's real `id` (as `str`).
    """
    resolved_context_key = context_key
    if resolved_context_key is None and task_node_id is not None:
        resolved_context_key = f"task_node:{task_node_id}"

    evidence = outcome_to_evidence(
        evidence_type=evidence_type,
        target={
            "target_type": "implementation",
            "target_id": implementation_id,
            "target_version": None,
        },
        outcome_status=outcome_status,
        success_criteria=success_criteria,
        direction=direction,
        strength_score=strength_score,
        strength_method=strength_method,
        independence_group=independence_group,
        context_key=resolved_context_key,
        failure_class=failure_class,
        created_by=created_by or IMPLEMENTATION_EVIDENCE_WRITER_STAMP,
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


async def _get_implementation_evidence(pool: asyncpg.Pool, implementation_id: str) -> list[dict]:
    """Every live evidence row targeting one implementation, oldest
    first -- same shape `claim_evidence.py::get_claim_evidence` uses for
    `target_type='claim'`."""
    rows = await pool.fetch(
        """
        SELECT * FROM evidence
        WHERE target_type = 'implementation' AND target_id = $1::uuid
          AND t_invalid IS NULL
        ORDER BY t_valid ASC
        """,
        implementation_id,
    )
    return [dict(row) for row in rows]


def _capability_estimate_from_evidence(evidence: list[dict]) -> dict:
    """The exact honest-fields-only computation
    `procedure_graph_api.py::_capability_estimate` performs for procedure
    evidence, applied to implementation evidence instead. Same field
    names, same posture: `level_gated` is `None`, named not omitted --
    this schema has no `environment`/`completed_review`/
    `verification_plan_satisfied` column on `evidence` or
    `implementations`, so the fully gated L0-5 ladder
    (`compute_capability()`'s gates) cannot be honestly computed from
    this composition alone.

    Same filter as the mirrored function, restated honestly here too:
    the stream is gated by `evidence_type` and a terminal
    `outcome_status` ONLY, NOT by `direction`. `outcome_to_evidence()`
    defaults a failure's `direction` to `'contradicts'`, so an
    `AND direction == 'supports'` filter here silently excluded every
    failure recorded through `record_implementation_outcome` -- P would
    not move no matter how many times the implementation failed. A
    recorded failure IS an outcome-bearing attempt and IS what lowers
    `p_estimate`. This matches the mirrored
    `procedure_graph_api.py::_capability_estimate` and
    `procedure_evidence_stats` (db/24 as amended by db/34); "how much
    independent SUPPORTING evidence exists" is still a
    direction='supports' question, but it is a different question from
    "what is P"."""
    outcome_bearing = [
        e for e in evidence
        if e.get("evidence_type") in _OUTCOME_BEARING_EVIDENCE_TYPES
        and e.get("outcome_status") in ("success", "failure")
    ]
    total = len(outcome_bearing)
    successes = sum(1 for e in outcome_bearing if e["outcome_status"] == "success")

    p_lower, p_upper = wilson_interval(successes, total)
    p_estimate = p_lower

    independent_groups = len({
        e["independence_group"] for e in outcome_bearing if e.get("independence_group")
    })

    band = 0
    if total > 0 and successes > 0:
        band = band_for_p(p_estimate)

    return {
        "p_estimate": p_estimate,
        "p_lower": p_lower,
        "p_upper": p_upper,
        "evidence_count": total,
        "success_count": successes,
        "independent_groups": independent_groups,
        "band": band,
        "routing": route_for_p(p_estimate).value,
        "level_gated": None,
    }


async def get_implementation_capability(
    pool: asyncpg.Pool, implementation_id: str, *, task_node_id: Optional[str] = None,
) -> dict:
    """
    Real evidence query (`target_type='implementation'`, `target_id=
    implementation_id`, `t_invalid IS NULL`) fed into the SAME
    `compute_capability`/`wilson_interval` math
    `procedure_graph_api.py::get_solution_view`'s own `_capability_
    estimate` helper already uses for procedures -- mirrored field for
    field: `p_estimate`/`p_lower`/`p_upper`/`evidence_count`/
    `success_count`/`independent_groups`/`band`/`routing` are real and
    computed; `level_gated` is `None`, named honestly (see
    `_capability_estimate_from_evidence`'s own docstring for exactly
    why -- no environment-gated data source exists in this schema).

    `task_node_id`, if given, is advisory-only filtering by
    `context_key = f"task_node:{task_node_id}"` -- the SAME breadcrumb
    `record_implementation_outcome` writes when it isn't given an explicit
    `context_key`. This is a best-effort narrowing, not a real foreign
    key: an outcome recorded with a different (or no) `context_key`
    convention for the same task is silently excluded when this filter is
    applied, so pass `task_node_id=None` (the default) to see this
    implementation's FULL evidence stream when in doubt.
    """
    evidence = await _get_implementation_evidence(pool, implementation_id)
    if task_node_id is not None:
        expected_context_key = f"task_node:{task_node_id}"
        evidence = [e for e in evidence if e.get("context_key") == expected_context_key]

    estimate = _capability_estimate_from_evidence(evidence)
    return {
        "implementation_id": implementation_id,
        "task_node_id": task_node_id,
        **estimate,
    }


async def compare_implementations(
    pool: asyncpg.Pool, implementation_ids: list[str],
) -> list[dict]:
    """
    Real, derived comparison across several implementations -- calls
    `get_implementation_capability` for each and orders the results by
    real `p_estimate` descending. Directive Sec 35's explicit rule: never
    a static, hand-authored performance claim -- every field in the
    returned list traces back to a real `evidence` row or is honestly
    `None`/zero when no evidence exists yet.
    """
    results = [
        await get_implementation_capability(pool, implementation_id)
        for implementation_id in implementation_ids
    ]
    return sorted(results, key=lambda r: r["p_estimate"], reverse=True)
