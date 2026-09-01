"""
Live-database proving tests for app/services/procedure_graph_api.py --
against real procedures, claims, and evidence, not FakePool.

Same pattern as every other `*_e2e.py` file (test_claim_traversal_e2e.py
in particular, which this file's fixtures mirror): requires a real
DATABASE_URL, skips (not fails) without one.
"""
from __future__ import annotations

import asyncio
import os

import pytest

from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.claims import capture_claim
from app.services.procedure_graph_api import (
    get_procedure_claims,
    get_procedure_detail,
    get_procedure_evidence,
    get_procedure_graph,
    get_procedure_versions,
    get_solution_view,
)
from app.services.procedures import capture_procedure, record_execution_outcome, supersede_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "proc-graph-api-e2e"
SCOPE = AccessScope.unrestricted()


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def _cleanup(pool) -> None:
    # evidence is [H] append-only (db/24_evidence.sql, invariant #19):
    # DELETE is refused outright, so a rerun retracts via the same
    # t_invalid tombstone the engine itself enforces, rather than
    # deleting the row.
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE target_type = 'procedure' "
        "AND t_invalid IS NULL AND target_id IN "
        "(SELECT id FROM procedures WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM procedures WHERE name LIKE $1) "
        "OR target_id IN (SELECT id FROM procedures WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{PREFIX}%")


async def _task_node(pool, name: str) -> str:
    await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2)",
        name, f"skill_{name}",
    )
    return name


async def _claim(pool, statement, task_name, *, subject, predicate, obj):
    claim_id = await capture_claim(
        pool, statement=statement, task_ids=[f"skill_{task_name}"],
        embedder=FakeEmbedder(), subject=subject, predicate=predicate, object=obj,
    )
    assert claim_id
    return claim_id


def test_get_procedure_detail_composes_real_claim_and_evidence():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task1")
            claim_id = await _claim(
                pool, f"{PREFIX} pandas installed", task,
                subject=f"{PREFIX}:project:p1", predicate="uses", obj="pandas",
            )
            result = await capture_procedure(
                pool, name=f"{PREFIX}-proc1", goal="do a thing",
                steps=[{"order": 0, "goal": "run it"}],
                preconditions=[{
                    "subject": f"{PREFIX}:project:p1", "predicate": "uses",
                    "object": "pandas", "claim_id": claim_id,
                }],
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=True, context_key="ctx-1",
            )
            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=False, context_key="ctx-2",
                failure_class="environment_changed",
            )

            detail = await get_procedure_detail(pool, row_id, scope=SCOPE)

            assert detail is not None
            assert detail["id"] == row_id
            assert {str(c["id"]) for c in detail["claims"]} == {claim_id}
            assert detail["evidence_summary"]["total"] == 2
            assert detail["evidence_summary"]["success_count"] == 1
            assert detail["evidence_summary"]["failure_count"] == 1
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_procedure_claims_reads_real_precondition_claim():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task2")
            claim_id = await _claim(
                pool, f"{PREFIX} numpy installed", task,
                subject=f"{PREFIX}:project:p2", predicate="uses", obj="numpy",
            )
            result = await capture_procedure(
                pool, name=f"{PREFIX}-proc2", goal="do another thing",
                preconditions=[{
                    "subject": f"{PREFIX}:project:p2", "predicate": "uses",
                    "object": "numpy", "claim_id": claim_id,
                }],
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            claims = await get_procedure_claims(pool, row_id, scope=SCOPE)

            assert {str(c["id"]) for c in claims} == {claim_id}
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_procedure_evidence_reads_real_recorded_outcomes():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            result = await capture_procedure(
                pool, name=f"{PREFIX}-proc3", goal="do a thing",
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]
            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=True, context_key="ctx-1",
            )

            evidence = await get_procedure_evidence(pool, row_id, scope=SCOPE)

            assert len(evidence) == 1
            assert evidence[0]["target_type"] == "procedure"
            assert str(evidence[0]["target_id"]) == row_id
            assert evidence[0]["outcome_status"] == "success"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_procedure_versions_walks_a_real_supersede_chain():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            result = await capture_procedure(
                pool, name=f"{PREFIX}-proc4", goal="v1 goal",
                provenance="system_pending_review", scope_type="global",
            )
            v1_row_id = result["id"]
            procedure_id = result["procedure_id"]

            superseded = await supersede_procedure(
                pool, prior_row_id=v1_row_id,
                changed_fields={"goal": "v2 goal"},
                superseded_by="tester",
            )
            assert superseded is not None

            versions = await get_procedure_versions(pool, procedure_id, scope=SCOPE)

            assert [v["version"] for v in versions] == [1, 2]
            assert versions[0]["t_invalid"] is not None
            assert versions[1]["t_invalid"] is None
            assert versions[1]["goal"] == "v2 goal"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_procedure_graph_expands_real_linear_steps():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            result = await capture_procedure(
                pool, name=f"{PREFIX}-proc5", goal="do a thing",
                steps=[
                    {"order": 0, "goal": "first"},
                    {"order": 1, "goal": "second"},
                ],
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            graph = await get_procedure_graph(pool, row_id, scope=SCOPE)

            assert graph is not None
            assert [n["goal"] for n in graph["nodes"]] == ["first", "second"]
            assert graph["nodes"][1]["deps"] == [0]
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_solution_view_computes_real_capability_from_recorded_outcomes():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            result = await capture_procedure(
                pool, name=f"{PREFIX}-proc6", goal="do a thing",
                steps=[{"order": 0, "goal": "run it", "implementation_hint": "deterministic"}],
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]
            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=True, context_key="ctx-1",
            )
            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=True, context_key="ctx-2",
            )

            solution = await get_solution_view(pool, row_id, scope=SCOPE)

            assert solution is not None
            assert solution["procedure_row_id"] == row_id
            assert solution["capability"]["evidence_count"] == 2
            assert solution["capability"]["success_count"] == 2
            assert 0.0 < solution["capability"]["p_estimate"] <= 1.0
            assert "deterministic" in solution["implementations"]
            assert solution["implementations"]["deterministic"]["supported"] is False
            assert "license" not in solution
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
