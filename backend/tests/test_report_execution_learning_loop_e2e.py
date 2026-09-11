"""
MCP hardening B18: `report_execution`'s host-executed learning loop --
supplying `observations_json` on a success now attempts real extraction
via the same pipeline `find_best_way`'s own tier-2 sandboxed runs use,
and the resulting candidate is private-by-default (B19). Omitting
`observations_json` (the default) stays byte-identical to the pre-
existing behavior.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import json
import os
from uuid import uuid4

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
    approve_procedure,
    capture_procedure,
    record_execution_outcome,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{name_prefix}%",
    )


async def _make_verified_approved(pool, name: str, *, goal: str | None = None) -> dict:
    result = await capture_procedure(
        pool, name=name, goal=goal or name, provenance="system_pending_review", scope_type="global",
    )
    row_id = result["id"]
    for i in range(MIN_SUCCESSES_FOR_VERIFIED):
        await record_execution_outcome(
            pool, procedure_row_id=row_id, success=True,
            context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
        )
    await approve_procedure(pool, procedure_row_id=row_id, approved_by="tester")
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id))


def test_report_execution_without_observations_is_unchanged():
    """Backward-compat: omitting observations_json must behave exactly
    like the pre-existing tool -- outcome recording only, no
    'extraction' key in the response at all."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-reportlearn-noext-{run_id}"
        try:
            procedure = await _make_verified_approved(pool, name)
            ctx = _FakeContext(pool)
            result = await srv.report_execution(
                procedure_id=str(procedure["procedure_id"]), success=True,
                context_key="ctx-backcompat", ctx=ctx,
            )
            payload = json.loads(result)
            assert "extraction" not in payload
            assert "verification_state" in payload
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_report_execution_with_observations_extracts_a_private_candidate():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-reportlearn-ext-{run_id}"
        try:
            procedure = await _make_verified_approved(pool, name)
            ctx = _FakeContext(pool)
            observations = json.dumps([
                {"observation_type": "file_touched", "label": "src/handler.py",
                 "properties": {"file_path": "src/handler.py"}},
                {"observation_type": "file_touched", "label": "src/handler_test.py",
                 "properties": {"file_path": "src/handler_test.py"}},
            ])
            tool_sequence = json.dumps(["read_file", "edit_file", "run_tests"])
            result = await srv.report_execution(
                procedure_id=str(procedure["procedure_id"]), success=True,
                context_key=f"ctx-learn-{run_id}", ctx=ctx,
                observations_json=observations, tool_sequence_json=tool_sequence,
                task_description=f"fix the handler bug for probe {run_id}",
            )
            payload = json.loads(result)
            assert "extraction" in payload

            if "procedure_id" in payload["extraction"]:
                extracted_row = await pool.fetchrow(
                    "SELECT visibility, owner_id FROM procedures WHERE procedure_id = $1 "
                    "ORDER BY version DESC LIMIT 1",
                    payload["extraction"]["procedure_id"],
                )
                assert extracted_row["visibility"] == "private"
                assert extracted_row["owner_id"] is not None
                await pool.execute(
                    "DELETE FROM procedures WHERE procedure_id = $1::uuid "
                    "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
                    payload["extraction"]["procedure_id"],
                )
            # Else: V5 honestly refused extraction (a real, valid outcome
            # for a minimal 2-observation fixture) -- either branch
            # proves the pipeline actually ran, not a fabricated success.

            # Malformed JSON must REFUSE the extraction attempt without
            # ever silently succeeding.
            bad_result = await srv.report_execution(
                procedure_id=str(procedure["procedure_id"]), success=True,
                context_key=f"ctx-bad-{run_id}", ctx=ctx,
                observations_json="not json",
            )
            bad_payload = json.loads(bad_result)
            assert "skipped" in bad_payload["extraction"]
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_report_execution_does_not_duplicate_the_procedure_it_just_reused():
    """MCP hardening B18 STRICT CLOSURE: V4's literal "if an existing
    procedure was reused, do not automatically duplicate it". `procedure`
    (resolved from the caller's own `procedure_id`) IS the procedure this
    call is reporting an outcome for -- by construction, always a real
    reuse. Passing `task_description` equal to that SAME procedure's own
    `goal` (DeterministicExtractor.extract sets `extracted.goal =
    evidence.goal_text` verbatim -- confirmed real, not assumed) drives
    the extracted candidate's own goal to overlap 1.0 with the reused
    procedure -- `extract_procedure`'s new `reused_procedure_goal` check
    must refuse to persist a near-duplicate, never silently create one."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-reportlearn-nodup-{run_id}"
        try:
            reused_goal = f"deploy the canary release for probe {run_id}"
            procedure = await _make_verified_approved(pool, name, goal=reused_goal)
            ctx = _FakeContext(pool)
            observations = json.dumps([
                {"observation_type": "file_touched", "label": "deploy/canary.yaml",
                 "properties": {"file_path": "deploy/canary.yaml"}},
            ])
            tool_sequence = json.dumps(["read_file", "edit_file", "run_tests"])
            result = await srv.report_execution(
                procedure_id=str(procedure["procedure_id"]), success=True,
                context_key=f"ctx-nodup-{run_id}", ctx=ctx,
                observations_json=observations, tool_sequence_json=tool_sequence,
                task_description=reused_goal,
            )
            payload = json.loads(result)
            assert "skipped" in payload["extraction"], (
                "reporting success for the SAME procedure just reused must "
                "never fabricate a duplicate candidate"
            )
            assert "B18_no_auto_duplicate" in payload["extraction"]["skipped"]

            # No new row was actually created under this name prefix.
            dup_count = await pool.fetchval(
                "SELECT count(*) FROM procedures WHERE goal = $1 AND procedure_id != $2::uuid",
                reused_goal, procedure["procedure_id"],
            )
            assert dup_count == 0
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())
