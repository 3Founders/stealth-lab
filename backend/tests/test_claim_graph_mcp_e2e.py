"""
Live-database proof that the claim-graph viewer is actually reachable
THROUGH the MCP server: the `get_claim_graph` MCP tool and the two plain
HTTP routes (`GET /claim-graph`, `GET /claim-graph/data`) that
`app/mcp_server/claim_graph_page.py` renders against.

Same convention as test_mcp_server_identity_e2e.py: a real DATABASE_URL,
skips without one; a `_FakeContext` standing in for the MCP request
context so the tool body can be driven directly.
"""
from __future__ import annotations

import asyncio
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


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def _cleanup(pool, prefix: str) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1) "
        "OR target_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1)", f"{prefix}%",
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM task_nodes WHERE skill_ref LIKE $1", f"{prefix}%")


def test_claim_graph_reachable_through_mcp_tool_and_http_routes():
    import app.mcp_server.server as srv
    from app.db.session import create_pool
    from app.services.claims import capture_claim

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        prefix = f"cg-mcp-e2e-{uuid4().hex[:8]}"
        try:
            await _cleanup(pool, prefix)
            await pool.execute(
                "INSERT INTO task_nodes (name, description, skill_ref) VALUES ($1, 'x', $2)",
                f"{prefix}-task", f"{prefix}-task",
            )
            cid = await capture_claim(
                pool, statement=f"{prefix} the deploy gate must be green",
                task_ids=[f"{prefix}-task"], created_by=prefix, embedder=FakeEmbedder(),
                subject="deploy_gate", predicate="must_be", object="green",
            )
            assert cid

            # ---- the MCP tool ----
            raw = await srv.get_claim_graph(
                _FakeContext(pool), limit=600, q=prefix, with_status=False,
            )
            payload = json.loads(raw)
            assert str(cid) in {n["id"] for n in payload["nodes"]}
            node = next(n for n in payload["nodes"] if n["id"] == str(cid))
            assert node["subject"] == "deploy_gate" and node["object"] == "green"
            assert set(payload["counts"]) >= {"claims_total", "claims_shown", "edges", "by_status"}

            # ---- the plain HTTP routes on the MCP ASGI app ----
            import httpx

            prev = srv._LIFESPAN_STATE.get("pool")
            srv._LIFESPAN_STATE["pool"] = pool
            try:
                transport = httpx.ASGITransport(app=srv.app)
                async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
                    page = await client.get("/claim-graph")
                    assert page.status_code == 200
                    assert "text/html" in page.headers["content-type"]
                    assert "Claim Graph" in page.text and "claim-graph/data" in page.text

                    data = await client.get(
                        "/claim-graph/data", params={"q": prefix, "with_status": "false", "limit": 50},
                    )
                    assert data.status_code == 200
                    body = data.json()
                    assert str(cid) in {n["id"] for n in body["nodes"]}
                    assert body["counts"]["claims_shown"] >= 1
            finally:
                if prev is None:
                    srv._LIFESPAN_STATE.pop("pool", None)
                else:
                    srv._LIFESPAN_STATE["pool"] = prev
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    asyncio.run(_run())
