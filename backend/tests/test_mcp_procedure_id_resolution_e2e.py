"""Final-V1 eval finding C: the MCP procedure tools must accept EITHER the
stable ``procedures.procedure_id`` family handle OR the per-version
``procedures.id`` row key -- the row key is what a caller sees in the DB /
the /procedure-graph viewer / another tool's output, and before this it
was bounced with an unhelpful "no live procedure".

Real Postgres; skips without DATABASE_URL.
"""
from __future__ import annotations

import json
import os
import uuid

import pytest

pytestmark = pytest.mark.skipif(
    not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset"
)

from app.db.session import create_pool  # noqa: E402
from app.mcp_server import server as srv  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402


class _Ctx:
    def __init__(self, pool):
        class _RC:
            pass
        self.request_context = _RC()
        self.request_context.lifespan_context = {"pool": pool}


@pytest.mark.asyncio
async def test_procedure_tools_accept_both_the_handle_and_the_row_key():
    pool = await create_pool(statement_cache_size=0)
    tag = uuid.uuid4().hex[:8]
    try:
        res = await capture_procedure(
            pool, name=f"mcp-idres-e2e-{tag}", goal="finding C fixture",
            steps=[{"order": 0, "goal": "do it"}],
            provenance="prior_library", scope_type="global", created_by="mcp_idres_e2e",
        )
        row_id, handle = res["id"], res["procedure_id"]
        assert row_id != handle

        # the shared canonicaliser
        assert await srv._canonical_procedure_id(pool, handle) == handle
        assert await srv._canonical_procedure_id(pool, row_id) == handle
        assert await srv._canonical_procedure_id(pool, str(uuid.uuid4())) is None
        assert await srv._canonical_procedure_id(pool, "not-a-uuid") is None

        ctx = _Ctx(pool)

        # get_procedure: handle AND row key both resolve to the same live row
        by_handle = json.loads(await srv.get_procedure(handle, ctx))
        by_rowkey = json.loads(await srv.get_procedure(row_id, ctx))
        assert by_handle["procedure_id"] == by_rowkey["procedure_id"] == handle
        assert by_handle["id"] == by_rowkey["id"] == row_id

        # check_procedure: row key now yields a verdict, not a REFUSED
        verdict = await srv.check_procedure(row_id, "reuse now", ctx)
        assert verdict.strip().startswith("{"), verdict
        assert json.loads(verdict)["verdict"] in ("ALLOW", "WOULD_REFUSE")

        # check_applicability: row key accepted
        appl = await srv.check_applicability(row_id, ctx, require_verified=False)
        assert appl.strip().startswith("{"), appl

        # genuinely unknown / malformed ids still refuse, with a message that
        # names both things that were tried
        missing = await srv.get_procedure(str(uuid.uuid4()), ctx)
        assert "REFUSED" in missing and "row key" in missing
        assert "not a valid procedure id" in await srv.get_procedure("nope", ctx)
    finally:
        await pool.close()
