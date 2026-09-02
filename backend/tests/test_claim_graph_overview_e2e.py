"""
Live-database proving tests for the whole-graph claim read that backs the
MCP server's /claim-graph viewer:

  - app/services/claim_graph_api.py::get_claim_graph_overview
  - GET /v1/claims/graph (app/api/claims.py) -- same shape, over HTTP,
    and proof the literal path `graph` is not swallowed by /{claim_id}.

Same pattern as the other *_e2e.py files: requires a real DATABASE_URL,
skips (not fails) without one. Self-cleaning by name prefix.
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def _cleanup(pool, prefix: str) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1) "
        "OR target_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1)",
        f"{prefix}%",
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{prefix}%")
    await pool.execute("DELETE FROM task_nodes WHERE skill_ref LIKE $1", f"{prefix}%")


def test_claim_graph_overview_against_real_postgres():
    async def _run():
        from app.db.session import create_pool
        from app.services.access import AccessScope
        from app.services.claim_graph_api import get_claim_graph_overview
        from app.services.claims import capture_claim, link_claims, relate_claims

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=3)
        prefix = f"claimgraph-e2e-{uuid4().hex[:8]}"
        scope = AccessScope.unrestricted()
        try:
            await _cleanup(pool, prefix)

            await pool.execute(
                "INSERT INTO task_nodes (name, description, skill_ref) VALUES ($1, 'x', $2)",
                f"{prefix}-task", f"{prefix}-task",
            )

            async def _claim(word, **triple):
                cid = await capture_claim(
                    pool, statement=f"{prefix} the {word} holds under load",
                    task_ids=[f"{prefix}-task"], created_by=prefix,
                    embedder=FakeEmbedder(), **triple,
                )
                assert cid, "fixture: claim must be created"
                return str(cid)

            a = await _claim("cache invariant", subject="cache", predicate="requires", object="redis>=7")
            b = await _claim("supporting benchmark")
            c = await _claim("newer routing rule")
            d = await _claim("older routing rule")

            # B SUPPORTS A  -> A's real lifecycle state becomes 'supported'
            await link_claims(pool, from_claim_id=b, to_claim_id=a, relation="SUPPORTS",
                              created_by=prefix)
            # C SUPERSEDES D -> D.truth_state flips OUT (propagate=False: no
            # procedure side effects in this read-path test)
            await relate_claims(pool, from_claim_id=c, to_claim_id=d, relation="SUPERSEDES",
                                created_by=prefix, propagate=False)

            # ---- believed-only view (default) ----
            g = await get_claim_graph_overview(pool, scope=scope, limit=1000, q=prefix)
            ids = {n["id"] for n in g["nodes"]}
            assert {a, b, c} <= ids
            assert d not in ids, "a superseded (OUT) claim is not in the current graph by default"

            node_a = next(n for n in g["nodes"] if n["id"] == a)
            assert node_a["statement"].startswith(prefix)
            assert node_a["subject"] == "cache" and node_a["predicate"] == "requires"
            assert node_a["object"] == "redis>=7"
            assert node_a["truth_state"] == "IN"
            assert node_a["status"] == "supported", node_a

            # Two edge KINDS coexist (get_claim_graph_overview docstring): a
            # 'relation' edge carries `relation` (the custom_edge_type); a
            # 'similarity' edge is a computed embedding-proximity hint and
            # deliberately has NO `relation` key, only `weight`. Select the
            # relation edges before reading `relation`, and check the
            # similarity edges are well formed rather than crashing on them.
            rels = {
                (e["source"], e["target"], e["relation"])
                for e in g["edges"] if e["kind"] == "relation"
            }
            assert (b, a, "SUPPORTS") in rels
            for e in g["edges"]:
                assert e["kind"] in ("relation", "similarity"), e
                if e["kind"] == "similarity":
                    assert "relation" not in e and isinstance(e["weight"], float), e
                else:
                    assert isinstance(e["relation"], str) and e["relation"], e
            assert not any(e["target"] == d for e in g["edges"]), (
                "an edge is only included when BOTH endpoints are in the node set"
            )
            assert g["counts"]["by_status"].get("supported", 0) >= 1
            assert g["counts"]["claims_total"] >= g["counts"]["claims_shown"] >= 3

            # ---- include_retired ----
            g2 = await get_claim_graph_overview(
                pool, scope=scope, limit=1000, q=prefix, include_retired=True,
            )
            ids2 = {n["id"] for n in g2["nodes"]}
            assert d in ids2
            node_d = next(n for n in g2["nodes"] if n["id"] == d)
            assert node_d["truth_state"] == "OUT"
            assert node_d["status"] == "retired", node_d
            assert (c, d, "SUPERSEDES") in {
                (e["source"], e["target"], e["relation"])
                for e in g2["edges"] if e["kind"] == "relation"
            }

            # ---- with_status=False is a raw, statusless dump ----
            g3 = await get_claim_graph_overview(
                pool, scope=scope, limit=1000, q=prefix, with_status=False,
            )
            assert all(n["status"] is None for n in g3["nodes"])
            assert all(n["truth_state"] for n in g3["nodes"])

            # ---- truncation is honest, never a silent cap ----
            g4 = await get_claim_graph_overview(pool, scope=scope, limit=2, q=prefix)
            assert len(g4["nodes"]) == 2
            assert g4["truncated"] is True

            # ---- REST leg: GET /v1/claims/graph, and 'graph' is not parsed
            # as a claim UUID by the /{claim_id} route ----
            import httpx
            from fastapi import FastAPI
            from app.api import claims as claims_api
            from app.api.deps import get_scope

            fapp = FastAPI()
            fapp.state.pool = pool
            fapp.dependency_overrides[get_scope] = lambda: scope
            fapp.include_router(claims_api.router)
            transport = httpx.ASGITransport(app=fapp)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get("/v1/claims/graph", params={"q": prefix, "limit": 600})
                assert resp.status_code == 200, resp.text
                body = resp.json()
                assert {a, b, c} <= {n["id"] for n in body["nodes"]}
                assert body["counts"]["claims_shown"] >= 3

                # 'graph' must route to the list endpoint, never be parsed
                # as a claim UUID by /{claim_id}
                over = await client.get("/v1/claims/graph", params={"limit": 601})
                assert over.status_code == 422 and "less_than_equal" in over.text
        finally:
            await _cleanup(pool, prefix)
            await pool.close()

    asyncio.run(_run())
