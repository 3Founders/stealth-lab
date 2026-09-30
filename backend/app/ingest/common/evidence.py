"""Evidence a SOURCE gives for a Procedure -- recorded as testimony, never as verification.

A benchmark saying "this run passed the task's tests" is a third party's grade: Kel did not run it. It is stored
as a witness row (`evidence_type` 'benchmark', direction 'supports', no outcome status), validated by the core
evidence contract (`app/execution/evidence.validate_evidence`). Only outcome-bearing rows of Kel's own runs
(`execution_result`, `reproduction`) can ever move a Procedure to `verified` (invariant #3), so this row informs
ranking and never promotes.

`independence_group` is the task: however many times the same task is imported, it counts once.
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

BENCHMARK_STRENGTH = 0.6
BENCHMARK_STRENGTH_METHOD = "benchmark_harness_graded_run"


async def record_benchmark_support(pool: Any, *, procedure_row_id: str, target_version: int, task_key: str,
                                   context_key: str, ingestion_context_id: str, created_by: str,
                                   extractor_version: str) -> str:
    from app.execution.evidence import validate_evidence
    from app.services.access import TenantScope, tenant_transaction
    from app.services.shards import home_pool
    from app.utils.ids import uuid7

    validate_evidence(
        evidence_type="benchmark",
        target={"target_type": "procedure", "target_id": UUID(procedure_row_id), "target_version": target_version},
        direction="supports", strength_score=BENCHMARK_STRENGTH, strength_method=BENCHMARK_STRENGTH_METHOD,
        independence_group=task_key, context_key=context_key, extractor_version=extractor_version,
        created_by=created_by,
    )
    evidence_id = uuid7()
    scope = TenantScope.commons()
    # evidence lives with its Procedure on the Procedure's home shard: retrieval's source-support count reads it there
    owner = await home_pool(pool, "procedure", str(procedure_row_id), by_row_id=True)
    async with tenant_transaction(owner, scope) as conn:
        row = await conn.fetchrow(
            """
            INSERT INTO evidence (
                id, evidence_type, target_type, target_id, target_version,
                direction, strength_score, strength_method,
                independence_group, context_key,
                extractor_version, created_by, visibility, tenant_id,
                ingestion_context_id
            ) VALUES (
                $1::uuid, 'benchmark', 'procedure', $2::uuid, $3,
                'supports', $4, $5,
                $6, $7,
                $8, $9, 'public', $10::uuid,
                $11::uuid
            )
            RETURNING id
            """,
            evidence_id, procedure_row_id, target_version, BENCHMARK_STRENGTH, BENCHMARK_STRENGTH_METHOD,
            task_key, context_key, extractor_version, created_by, scope.tenant_id, ingestion_context_id,
        )
    return str(row["id"])
