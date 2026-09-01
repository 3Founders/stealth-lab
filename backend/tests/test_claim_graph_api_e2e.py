"""
Live-database proving tests for app/services/claim_graph_api.py (directive
§40's read-only Claim Graph API) and app/api/claims.py's `/v1/claims/*`
router.

Same pattern as every other `*_e2e.py` file in this suite (see
`test_claim_evidence_e2e.py`, `test_claim_impact_e2e.py`,
`test_claim_relations_e2e.py`): requires a real DATABASE_URL, skips (not
fails) without one. Real `capture_claim`/`relate_claims`/`link_claims`/
`record_claim_evidence`/`capture_procedure` fixtures, no mocks. Real
cleanup: evidence and knowledge_nodes' own append-only rules are honored
(`evidence` retracted via its `t_invalid` tombstone, never DELETEd;
`edges`/`knowledge_nodes`/`procedures`/`task_nodes` rows this test itself
created are DELETEd by name prefix, matching every sibling `*_e2e.py`
file's own cleanup convention).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.api.claims import router as claims_router
from app.db.session import create_pool
from app.services import claim_graph_api
from app.services.access import AccessScope
from app.services.claim_evidence import record_claim_evidence
from app.services.claims import capture_claim, link_claims, relate_claims
from app.services.procedure_extraction.derive import precondition_with_claim
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "claim-graph-api-e2e"

ANON = AccessScope.anonymous()


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def _cleanup(pool) -> None:
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE target_type = 'claim' "
        "AND t_invalid IS NULL AND target_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1) "
        "OR target_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1)",
        f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{PREFIX}%")


async def _task_node(pool, name: str) -> str:
    await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2)",
        name, f"skill_{name}",
    )
    return name


async def _claim(pool, statement: str, task_name: str, **kwargs) -> str:
    claim_id = await capture_claim(
        pool, statement=statement, task_ids=[f"skill_{task_name}"],
        embedder=FakeEmbedder(), **kwargs,
    )
    assert claim_id
    return claim_id


def _app(pool) -> FastAPI:
    app = FastAPI()
    app.include_router(claims_router)
    app.state.pool = pool
    return app


# ---------------------------------------------------------------------------
# get_claim
# ---------------------------------------------------------------------------


def test_get_claim_returns_a_real_captured_claim():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task1")
            claim_id = await _claim(pool, f"{PREFIX} claim one", task)

            result = await claim_graph_api.get_claim(pool, claim_id, scope=ANON)

            assert result is not None
            assert str(result["id"]) == claim_id
            assert result["properties"]["statement"] == f"{PREFIX} claim one"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_claim_none_for_unknown_id():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            result = await claim_graph_api.get_claim(pool, str(uuid.uuid4()), scope=ANON)
            assert result is None
        finally:
            await pool.close()

    asyncio.run(_run())


def test_get_claim_hides_a_private_claim_from_an_anonymous_scope():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task-priv")
            claim_id = await _claim(
                pool, f"{PREFIX} private claim", task,
                visibility="private", owner_id=f"{PREFIX}-owner",
            )

            assert await claim_graph_api.get_claim(pool, claim_id, scope=ANON) is None

            owner_scope = AccessScope.for_user(f"{PREFIX}-owner")
            visible = await claim_graph_api.get_claim(pool, claim_id, scope=owner_scope)
            assert visible is not None
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# get_claim_neighbors / traverse_claim_graph
# ---------------------------------------------------------------------------


def test_get_claim_neighbors_and_traverse_compose_real_relations():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task2")
            a = await _claim(pool, f"{PREFIX} claim A", task)
            b = await _claim(pool, f"{PREFIX} claim B", task)
            c = await _claim(pool, f"{PREFIX} claim C", task)

            await link_claims(pool, from_claim_id=a, to_claim_id=b, relation="SUPPORTS")
            await link_claims(pool, from_claim_id=b, to_claim_id=c, relation="SUPPORTS")

            neighbors = await claim_graph_api.get_claim_neighbors(pool, a, scope=ANON)
            assert {(n["relation"], str(n["target_id"])) for n in neighbors} == {("SUPPORTS", b)}

            traversal = await claim_graph_api.traverse_claim_graph(pool, a, scope=ANON)
            supporting_pairs = {
                (h["from_claim_id"], h["to_claim_id"]) for h in traversal["supporting"]
            }
            assert (a, b) in supporting_pairs
            assert (b, c) in supporting_pairs  # real hop 2
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_traverse_claim_graph_omits_a_neighbor_the_scope_cannot_see():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task-vis")
            a = await _claim(pool, f"{PREFIX} claim vis-A", task)
            private_b = await _claim(
                pool, f"{PREFIX} claim vis-B private", task,
                visibility="private", owner_id=f"{PREFIX}-owner2",
            )
            await link_claims(pool, from_claim_id=a, to_claim_id=private_b, relation="SUPPORTS")

            traversal = await claim_graph_api.traverse_claim_graph(pool, a, scope=ANON)
            touched = {h["to_claim_id"] for h in traversal["supporting"]}
            assert private_b not in touched
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# get_claim_evidence_api
# ---------------------------------------------------------------------------


def test_get_claim_evidence_api_returns_real_evidence_row():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task3")
            claim_id = await _claim(pool, f"{PREFIX} claim with evidence", task)

            evidence_id = await record_claim_evidence(
                pool, claim_id=claim_id, evidence_type="execution_result",
                outcome_status="success",
                success_criteria={"predicate": "ran without error"},
                context_key=f"{PREFIX}-ctx",
            )

            rows = await claim_graph_api.get_claim_evidence_api(pool, claim_id, scope=ANON)

            assert len(rows) == 1
            assert str(rows[0]["id"]) == evidence_id
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# get_claim_dependents
# ---------------------------------------------------------------------------


def test_get_claim_dependents_finds_the_real_referencing_procedure():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task4")
            claim_id = await _claim(pool, f"{PREFIX} claim for precondition", task)

            procedure = await capture_procedure(
                pool, name=f"{PREFIX}-referencing-proc", goal="g",
                preconditions=[
                    precondition_with_claim("repo", "has_version", "2.0", claim_id=claim_id),
                ],
                provenance="system_pending_review", scope_type="global",
            )

            dependents = await claim_graph_api.get_claim_dependents(pool, claim_id, scope=ANON)

            assert {d["id"] for d in dependents} == {procedure["id"]}
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# get_claim_history
# ---------------------------------------------------------------------------


def test_get_claim_history_walks_a_real_supersede_chain():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task5")
            from app.services.claims import supersede_claim

            root = await _claim(pool, f"{PREFIX} claim root v1", task)
            v2 = await supersede_claim(
                pool, prior_claim_id=root, statement=f"{PREFIX} claim root v2",
                task_ids=[f"skill_{task}"], embedder=FakeEmbedder(),
            )
            assert v2

            history = await claim_graph_api.get_claim_history(pool, root, scope=ANON)

            ids = [h["claim_id"] for h in history]
            assert ids == [root, v2]
            versions = {h["claim_id"]: h["version"] for h in history}
            assert versions[root] == 1
            assert versions[v2] == 2
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# Router smoke, against a real pool
# ---------------------------------------------------------------------------


def test_router_end_to_end_against_a_real_pool():
    """Everything -- pool creation, HTTP calls, and cleanup -- runs inside
    ONE asyncio.run() / one event loop: asyncpg pool connections are
    bound to the loop that created them, and the ASGI app run through
    httpx's ASGITransport must execute on that same loop too."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task = await _task_node(pool, f"{PREFIX}-task6")
            claim_id = await _claim(pool, f"{PREFIX} router claim", task)

            app = _app(pool)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/v1/claims/{claim_id}")
                assert resp.status_code == 200
                assert resp.json()["id"] == claim_id

                resp = await client.get(f"/v1/claims/{uuid.uuid4()}")
                assert resp.status_code == 404

                for suffix in ("neighbors", "traverse", "evidence", "dependents", "history"):
                    resp = await client.get(f"/v1/claims/{claim_id}/{suffix}")
                    assert resp.status_code == 200, f"{suffix} -> {resp.status_code}: {resp.text}"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
