"""
Real, live-database proof that `report_execution`'s `success_criteria`
contract works end to end THROUGH the actual MCP dispatch path -- not by
calling the Python function directly (that would miss the exact bug this
fix closes: pydantic argument validation against the tool's advertised
schema). Same convention as test_claim_graph_mcp_e2e.py: a real
DATABASE_URL, skips without one; a `_FakeContext` stands in for the MCP
request context so `ctx.request_context.lifespan_context["pool"]` resolves,
but the call itself goes through `ToolManager.call_tool()`, the same
dispatcher a real JSON-RPC `tools/call` request drives.

Covers the mission's cases A (structured predicate), B (structured
metrics), D (malformed shape), E (wrong primitive type), F (omitted
criteria on success), and G (failure, no criteria) against real Postgres
and the real evidence/invariant-13 machinery -- nothing here is mocked.
"""
from __future__ import annotations

import json
import os
from uuid import uuid4

import pytest

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
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")


async def _capture(pool, name: str):
    from app.services.procedures import capture_procedure

    return await capture_procedure(
        pool, name=name, goal="g", provenance="system_pending_review", scope_type="global",
    )


def test_report_execution_accepts_a_real_structured_predicate():
    """Case A, driven through the real MCP dispatch path."""
    import app.mcp_server.server as srv
    from app.db.session import create_pool

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = f"re-mcp-contract-{uuid4().hex[:8]}"
        try:
            await _cleanup(pool, prefix)
            proc = await _capture(pool, f"{prefix}-a")
            ctx = _FakeContext(pool)

            raw = await srv.server._tool_manager.call_tool(
                "report_execution",
                {
                    "procedure_id": proc["procedure_id"], "success": True,
                    "context_key": f"{prefix}-ctx",
                    "success_criteria": {"predicate": "tests pass"},
                },
                context=ctx,
            )
            payload = json.loads(raw)
            assert payload["verification_stats"]["successes"] == 1
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_report_execution_accepts_real_structured_metrics():
    """Case B."""
    import app.mcp_server.server as srv
    from app.db.session import create_pool

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = f"re-mcp-contract-{uuid4().hex[:8]}"
        try:
            await _cleanup(pool, prefix)
            proc = await _capture(pool, f"{prefix}-b")
            ctx = _FakeContext(pool)

            raw = await srv.server._tool_manager.call_tool(
                "report_execution",
                {
                    "procedure_id": proc["procedure_id"], "success": True,
                    "context_key": f"{prefix}-ctx",
                    "success_criteria": {"metrics": {"tests_passed": 42, "tests_failed": 0}},
                },
                context=ctx,
            )
            payload = json.loads(raw)
            assert payload["verification_stats"]["successes"] == 1
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_report_execution_refuses_real_malformed_criteria():
    """Case D: {"predicate": ""} clears the MCP schema (it's a real
    object) but must be refused by the evidence layer's own invariant-13
    check -- a schema-valid dict is not automatically acceptable
    content."""
    import app.mcp_server.server as srv
    from app.db.session import create_pool

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = f"re-mcp-contract-{uuid4().hex[:8]}"
        try:
            await _cleanup(pool, prefix)
            proc = await _capture(pool, f"{prefix}-d")
            ctx = _FakeContext(pool)

            raw = await srv.server._tool_manager.call_tool(
                "report_execution",
                {
                    "procedure_id": proc["procedure_id"], "success": True,
                    "context_key": f"{prefix}-ctx",
                    "success_criteria": {"predicate": ""},
                },
                context=ctx,
            )
            assert raw.startswith("REFUSED:")

            row = await pool.fetchrow(
                "SELECT verification_stats FROM procedures WHERE procedure_id = $1",
                proc["procedure_id"],
            )
            assert dict(row["verification_stats"]).get("successes", 0) == 0
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_report_execution_schema_rejects_a_real_string_argument():
    """Case E, against the real dispatcher: a caller following the OLD
    str-typed contract (or a confused client) sends a plain string.
    Pydantic argument validation refuses it before report_execution's
    body -- and therefore before any DB row is touched."""
    import app.mcp_server.server as srv
    from app.db.session import create_pool
    from mcp.server.mcpserver.exceptions import ToolError

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = f"re-mcp-contract-{uuid4().hex[:8]}"
        try:
            await _cleanup(pool, prefix)
            proc = await _capture(pool, f"{prefix}-e")
            ctx = _FakeContext(pool)

            with pytest.raises(ToolError):
                await srv.server._tool_manager.call_tool(
                    "report_execution",
                    {
                        "procedure_id": proc["procedure_id"], "success": True,
                        "context_key": f"{prefix}-ctx",
                        "success_criteria": "not-json",
                    },
                    context=ctx,
                )

            row = await pool.fetchrow(
                "SELECT verification_stats FROM procedures WHERE procedure_id = $1",
                proc["procedure_id"],
            )
            assert dict(row["verification_stats"]).get("successes", 0) == 0
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_report_execution_omitted_criteria_synthesizes_not_refuses():
    """Case F, against real Postgres: existing, deliberate behavior
    (unchanged by this fix) -- omitting success_criteria on a real
    success is NOT the same as an empty/malformed one. It is honest
    (record_execution_outcome() synthesizes criteria from the call's own
    real measurements) rather than a bare, unexplained "it worked"."""
    import app.mcp_server.server as srv
    from app.db.session import create_pool

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = f"re-mcp-contract-{uuid4().hex[:8]}"
        try:
            await _cleanup(pool, prefix)
            proc = await _capture(pool, f"{prefix}-f")
            ctx = _FakeContext(pool)

            raw = await srv.server._tool_manager.call_tool(
                "report_execution",
                {
                    "procedure_id": proc["procedure_id"], "success": True,
                    "context_key": f"{prefix}-ctx", "steps_used": 3,
                },
                context=ctx,
            )
            payload = json.loads(raw)
            assert payload["verification_stats"]["successes"] == 1

            evidence_row = await pool.fetchrow(
                "SELECT success_criteria FROM evidence WHERE target_id = $1 "
                "ORDER BY t_created DESC LIMIT 1",
                proc["id"],
            )
            criteria = dict(evidence_row["success_criteria"])
            assert criteria.get("predicate")
            assert criteria.get("metrics", {}).get("steps_used") == 3
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    import asyncio
    asyncio.run(_run())


def test_report_execution_failure_without_criteria_still_works():
    """Case G: unaffected by this fix -- a failure report needs no
    success_criteria at all."""
    import app.mcp_server.server as srv
    from app.db.session import create_pool

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = f"re-mcp-contract-{uuid4().hex[:8]}"
        try:
            await _cleanup(pool, prefix)
            proc = await _capture(pool, f"{prefix}-g")
            ctx = _FakeContext(pool)

            raw = await srv.server._tool_manager.call_tool(
                "report_execution",
                {
                    "procedure_id": proc["procedure_id"], "success": False,
                    "context_key": f"{prefix}-ctx",
                },
                context=ctx,
            )
            payload = json.loads(raw)
            assert payload["verification_stats"]["attempts"] == 1
            assert payload["verification_stats"]["successes"] == 0
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    import asyncio
    asyncio.run(_run())
