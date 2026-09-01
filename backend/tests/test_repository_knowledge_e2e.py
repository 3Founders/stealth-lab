"""
Live-database proving tests for repository_knowledge.py
(get_repository_knowledge/get_project_knowledge) + the /v1/repositories
and /v1/projects routers.

Same pattern as every other `*_e2e.py` file: requires a real DATABASE_URL,
skips (not fails) without one. Real claims/procedures are inserted with
real scope_type='repository'/'project' and a real scope_entity_id;
assertions check that the right rows come back and wrong-scope rows do
not, then clean up everything this test wrote.
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import httpx
import pytest
from fastapi import FastAPI

from app.api.projects import router as projects_router
from app.api.repositories import router as repositories_router
from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.claims import capture_claim
from app.services.repository_knowledge import (
    get_project_knowledge,
    get_repository_knowledge,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "repo-knowledge-e2e"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def _cleanup(pool) -> None:
    await pool.execute(
        "DELETE FROM edges WHERE source_id IN "
        "(SELECT id FROM knowledge_nodes WHERE name LIKE $1) "
        "OR target_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{PREFIX}%")


async def _task_node(pool, name: str) -> str:
    await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2)",
        name, f"skill_{name}",
    )
    return name


async def _claim(pool, statement: str, task_name: str, *, scope_type: str, scope_entity_id: str) -> str:
    claim_id = await capture_claim(
        pool, statement=statement, task_ids=[f"skill_{task_name}"],
        embedder=FakeEmbedder(), scope_type=scope_type, scope_entity_id=scope_entity_id,
    )
    assert claim_id
    return claim_id


async def _procedure(pool, name: str, *, scope_type: str, scope_entity_id: str) -> str:
    row = await pool.fetchrow(
        "INSERT INTO procedures (name, goal, verification_state, scope_type, scope_entity_id) "
        "VALUES ($1, 'a real goal', 'verified', $2, $3) RETURNING id",
        name, scope_type, scope_entity_id,
    )
    return str(row["id"])


def test_repository_knowledge_returns_only_this_repositorys_claims_and_procedures():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            repo_a = f"{PREFIX}-org/repo-a-{uuid4().hex[:8]}"
            repo_b = f"{PREFIX}-org/repo-b-{uuid4().hex[:8]}"

            task_a = await _task_node(pool, f"{PREFIX}-task-a")
            await _claim(
                pool, f"{PREFIX} claim for repo A", task_a,
                scope_type="repository", scope_entity_id=repo_a,
            )
            task_b = await _task_node(pool, f"{PREFIX}-task-b")
            await _claim(
                pool, f"{PREFIX} claim for repo B", task_b,
                scope_type="repository", scope_entity_id=repo_b,
            )
            await _procedure(pool, f"{PREFIX} procedure for repo A", scope_type="repository", scope_entity_id=repo_a)
            await _procedure(pool, f"{PREFIX} procedure for repo B", scope_type="repository", scope_entity_id=repo_b)

            result = await get_repository_knowledge(pool, repo_a, scope=AccessScope.unrestricted())

            assert result["repository_id"] == repo_a
            statements = {c["statement"] for c in result["claims"]}
            assert statements == {f"{PREFIX} claim for repo A"}
            proc_names = {p["name"] for p in result["relevant_procedures"]}
            assert proc_names == {f"{PREFIX} procedure for repo A"}
            # repo B's data must never leak into repo A's read.
            assert f"{PREFIX} claim for repo B" not in statements
            assert f"{PREFIX} procedure for repo B" not in proc_names
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_repository_knowledge_claims_carry_real_lifecycle_state():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            repo = f"{PREFIX}-org/repo-lifecycle-{uuid4().hex[:8]}"
            task = await _task_node(pool, f"{PREFIX}-task-lifecycle")
            await _claim(
                pool, f"{PREFIX} lifecycle claim", task,
                scope_type="repository", scope_entity_id=repo,
            )

            result = await get_repository_knowledge(pool, repo, scope=AccessScope.unrestricted())
            assert len(result["claims"]) == 1
            assert result["claims"][0]["lifecycle_state"] == "current"
            assert result["confidence_summary"]["claims_by_lifecycle_state"] == {"current": 1}
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_unknown_repository_id_returns_empty_result():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            result = await get_repository_knowledge(
                pool, f"{PREFIX}-nonexistent-{uuid4().hex}", scope=AccessScope.unrestricted(),
            )
            assert result["claims"] == []
            assert result["relevant_procedures"] == []
            assert result["conflicts"] == []
        finally:
            await pool.close()

    asyncio.run(_run())


def test_project_knowledge_reads_project_scope_only_not_repository_scope():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            project = f"{PREFIX}-proj-{uuid4().hex[:8]}"
            repo = f"{PREFIX}-org/repo-under-project-{uuid4().hex[:8]}"

            task_p = await _task_node(pool, f"{PREFIX}-task-proj")
            await _claim(
                pool, f"{PREFIX} project-level claim", task_p,
                scope_type="project", scope_entity_id=project,
            )
            task_r = await _task_node(pool, f"{PREFIX}-task-repo-under-proj")
            await _claim(
                pool, f"{PREFIX} repository-level claim under the project", task_r,
                scope_type="repository", scope_entity_id=repo,
            )

            result = await get_project_knowledge(pool, project, scope=AccessScope.unrestricted())
            statements = {c["statement"] for c in result["claims"]}
            # Only the project-scoped claim, per the documented no-rollup
            # design choice -- the repository-scoped claim must not appear.
            assert statements == {f"{PREFIX} project-level claim"}
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_repository_router_returns_live_composed_knowledge():
    """Real ASGI app + real pool, both driven from ONE event loop
    (asyncio.run() below) -- an asyncpg pool's connections are bound to
    the loop that created them, so a sync TestClient (which runs
    requests on its own thread/loop) would cross-loop and fail. Using
    httpx.AsyncClient(transport=ASGITransport(...)) inside the same
    async function that owns the pool avoids that entirely."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            repo = f"{PREFIX}-router-repo-{uuid4().hex[:8]}"
            task = await _task_node(pool, f"{PREFIX}-router-task")
            await _claim(
                pool, f"{PREFIX} router-visible claim", task,
                scope_type="repository", scope_entity_id=repo,
            )

            app = FastAPI()
            app.include_router(repositories_router)
            app.include_router(projects_router)
            app.state.pool = pool

            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/v1/repositories/{repo}")
                assert resp.status_code == 200
                body = resp.json()
                assert body["repository_id"] == repo
                assert len(body["claims"]) == 1
                assert body["claims"][0]["statement"] == f"{PREFIX} router-visible claim"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
