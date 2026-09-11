"""
G12 -- closing a `.stealth/` exploration with a real resolution captures a
durable, private Claim in global Postgres (the local-learning write-back
G13's page-fault/coordination work left open). Also exercises the
`open_exploration` / `close_exploration` MCP tools end to end.

Skips (never fails) without DATABASE_URL.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
import uuid

import pytest

from app.db.session import create_pool
from app.stealth.exploration import close_exploration, list_explorations, open_exploration

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database exploration write-back test"
)

_MARK = "test-stealth-exploration-e2e"


async def _cleanup(pool, statement_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM knowledge_nodes WHERE node_type = 'claim' AND name LIKE $1",
        f"{statement_prefix}%",
    )


def test_resolved_exploration_captures_a_private_claim():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid.uuid4().hex[:8]
        question = f"{_MARK}: does the registry invoke charge() {tag}?"
        try:
            with tempfile.TemporaryDirectory() as ws:
                eid = open_exploration(ws, owner="test-owner", question=question, scope="src/**")

                claim_id = await close_exploration(
                    ws, eid, status="RESOLVED", resolution="yes, confirmed via grep",
                    pool=pool, created_by="test-owner", owner_id="test-owner",
                )
                assert claim_id is not None, "a real resolution must capture a claim"

                row = await pool.fetchrow(
                    "SELECT node_type, visibility, owner_id, properties FROM knowledge_nodes "
                    "WHERE id = $1 AND t_invalid IS NULL", claim_id,
                )
                assert row is not None
                assert row["node_type"] == "claim"
                assert row["visibility"] == "private"
                assert row["owner_id"] == "test-owner"
                props = row["properties"]
                if isinstance(props, str):
                    props = json.loads(props)
                assert props.get("exploration_id") == eid
                assert props.get("subject") == question
                assert props.get("predicate") == "resolved_as"
                assert props.get("object") == "yes, confirmed via grep"

                # journal + Postgres agree
                rows = list_explorations(ws)
                assert rows[0]["status"] == "RESOLVED"
        finally:
            await _cleanup(pool, f"{question[:40]}")
            await pool.close()

    asyncio.run(_run())


def test_abandoned_exploration_captures_no_claim():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid.uuid4().hex[:8]
        question = f"{_MARK}: abandoned question {tag}?"
        try:
            with tempfile.TemporaryDirectory() as ws:
                eid = open_exploration(ws, owner="test-owner", question=question)
                claim_id = await close_exploration(
                    ws, eid, status="ABANDONED", pool=pool, created_by="test-owner",
                )
                assert claim_id is None
                count = await pool.fetchval(
                    "SELECT count(*) FROM knowledge_nodes WHERE node_type='claim' AND name LIKE $1",
                    f"{question[:40]}%",
                )
                assert count == 0
        finally:
            await pool.close()

    asyncio.run(_run())


def test_open_and_close_exploration_mcp_tools():
    async def _run():
        import app.mcp_server.server as srv

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid.uuid4().hex[:8]
        question = f"{_MARK}: mcp path question {tag}?"

        class _FakeRequestContext:
            def __init__(self, pool):
                self.lifespan_context = {"pool": pool}

        class _FakeContext:
            def __init__(self, pool):
                self.request_context = _FakeRequestContext(pool)

        ctx = _FakeContext(pool)
        try:
            with tempfile.TemporaryDirectory() as repo_dir:
                open_result = json.loads(await srv.open_exploration(repo_dir, question, ctx))
                eid = open_result["exploration_id"]
                assert open_result["status"] == "ACTIVE"

                close_result = json.loads(await srv.close_exploration(
                    repo_dir, eid, ctx, status="RESOLVED", resolution="confirmed via test",
                ))
                assert close_result["claim_id"] is not None

                row = await pool.fetchrow(
                    "SELECT visibility FROM knowledge_nodes WHERE id = $1 AND t_invalid IS NULL",
                    close_result["claim_id"],
                )
                assert row["visibility"] == "private"
        finally:
            await _cleanup(pool, f"{question[:40]}")
            await pool.close()

    asyncio.run(_run())
