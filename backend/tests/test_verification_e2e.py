"""
MCP hardening B34: live-DB half of the verification ladder --
record_*'s evidence-class-capped states, `evaluate_run_completion`'s
aggregation, and required-field enforcement for human_review.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import os
from uuid import uuid4

import pytest

from app.db.session import create_pool
from app.execution import durable_run as _dr
from app.execution.plan_persistence import persist_compiled_plan
from app.execution.plans import compile_plan
from app.execution.procedure_graph import expand_procedure_steps
from app.services import verification as verif
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{name_prefix}%",
    )


async def _capture(pool, name: str, **kwargs) -> dict:
    kwargs.setdefault("goal", name)
    result = await capture_procedure(
        pool, name=name, provenance="system_pending_review", scope_type="global", **kwargs,
    )
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", result["id"]))


async def _start_run(pool, procedure: dict) -> str:
    steps = procedure.get("steps") or [{"order": 0, "goal": "step"}]
    nodes = await expand_procedure_steps(
        pool, procedure_id=procedure["procedure_id"], procedure_version=procedure["version"], steps=steps,
    )
    compiled = compile_plan(
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        procedure_row_id=procedure["id"], procedure_payload=procedure,
        task_description=procedure["name"], nodes=nodes,
        extractor_version="test_verification_e2e@1", created_by="test",
    )
    compiled, _ = await persist_compiled_plan(pool, compiled)
    graph = compiled.graph
    return await _dr.start_run(
        pool, execution_plan_id=str(compiled.plan.id), task_graph_id=str(compiled.graph.id),
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        node_orders=[n.order for n in graph.nodes], deps={n.order: list(n.deps) for n in graph.nodes},
    )


def test_self_report_can_never_reach_verified():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-verifself-{run_id}"
        try:
            procedure = await _capture(pool, name, postconditions=["it works"])
            exec_run_id = await _start_run(pool, procedure)

            passing = await verif.record_self_report(
                pool, execution_run_id=exec_run_id, criterion_id="postcondition:0",
                statement="it works", claimed_success=True,
            )
            assert passing["state"] == "claimed_done"

            failing = await verif.record_self_report(
                pool, execution_run_id=exec_run_id, criterion_id="postcondition:0",
                statement="it works", claimed_success=False,
            )
            assert failing["state"] == "failed_verification"
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_deterministic_check_reaches_verified():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-verifdet-{run_id}"
        try:
            procedure = await _capture(pool, name, postconditions=["the tests pass"])
            exec_run_id = await _start_run(pool, procedure)

            result = await verif.record_deterministic_check(
                pool, execution_run_id=exec_run_id, criterion_id="postcondition:0",
                statement="the tests pass", passed=True, evidence_refs=["evidence-123"],
            )
            assert result["state"] == "verified"
            assert result["evidence_refs"] == ["evidence-123"]
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_human_review_requires_reviewer_and_targets():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-verifhuman-{run_id}"
        try:
            procedure = await _capture(pool, name, postconditions=["the change is safe"])
            exec_run_id = await _start_run(pool, procedure)

            with pytest.raises(verif.VerificationError):
                await verif.record_human_review(
                    pool, execution_run_id=exec_run_id, criterion_id="postcondition:0",
                    statement="the change is safe", reviewer="", reviewed_targets=[],
                    criterion_answers={}, verdict=True,
                )

            result = await verif.record_human_review(
                pool, execution_run_id=exec_run_id, criterion_id="postcondition:0",
                statement="the change is safe", reviewer="alice",
                reviewed_targets=["diff.patch"], criterion_answers={"q1": "yes"}, verdict=True,
            )
            assert result["state"] == "independently_verified"
            assert result["reviewer"] == "alice"
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_evaluate_run_completion_aggregates_the_weakest_required_rung():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-verifagg-{run_id}"
        try:
            procedure = await _capture(
                pool, name, postconditions=["step a passes", "step b passes"],
            )
            exec_run_id = await _start_run(pool, procedure)

            # No results at all yet -> inconclusive.
            evaluation = await verif.evaluate_run_completion(
                pool, execution_run_id=exec_run_id, procedure=procedure,
            )
            assert evaluation["overall_state"] == "inconclusive"

            await verif.record_deterministic_check(
                pool, execution_run_id=exec_run_id, criterion_id="postcondition:0",
                statement="step a passes", passed=True,
            )
            # One required criterion still has no result -> still inconclusive.
            evaluation = await verif.evaluate_run_completion(
                pool, execution_run_id=exec_run_id, procedure=procedure,
            )
            assert evaluation["overall_state"] == "inconclusive"

            await verif.record_self_report(
                pool, execution_run_id=exec_run_id, criterion_id="postcondition:1",
                statement="step b passes", claimed_success=True,
            )
            # Both required criteria now have a non-failing result --
            # overall must be the WEAKEST rung (self_report's claimed_done),
            # not the strongest (deterministic_check's verified).
            evaluation = await verif.evaluate_run_completion(
                pool, execution_run_id=exec_run_id, procedure=procedure,
            )
            assert evaluation["overall_state"] == "claimed_done"

            # Now make step b FAIL -- overall must flip to failed_verification.
            await verif.record_self_report(
                pool, execution_run_id=exec_run_id, criterion_id="postcondition:1",
                statement="step b passes", claimed_success=False,
            )
            evaluation = await verif.evaluate_run_completion(
                pool, execution_run_id=exec_run_id, procedure=procedure,
            )
            assert evaluation["overall_state"] == "failed_verification"
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_evaluate_run_completion_with_no_postconditions_is_honestly_inconclusive():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-verifempty-{run_id}"
        try:
            procedure = await _capture(pool, name)  # no postconditions at all
            exec_run_id = await _start_run(pool, procedure)
            evaluation = await verif.evaluate_run_completion(
                pool, execution_run_id=exec_run_id, procedure=procedure,
            )
            assert evaluation["overall_state"] == "inconclusive"
            assert evaluation["criteria"] == []
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())
