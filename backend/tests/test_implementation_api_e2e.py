"""
Live-database proving tests for the Implementation Registry REST API
(`app/api/implementations.py`), the two new `app/api/tasks.py` routes,
and `GET /v1/solutions/{id}/implementations`.

Same pattern as every other `*_e2e.py` file in this suite (see
`test_claim_graph_api_e2e.py`, `test_procedure_graph_api_e2e.py`):
requires a real DATABASE_URL, skips (not fails) without one. Real
`implementation_registry.register()`/`.activate()` and
`capabilities.record_implementation_outcome()` fixtures, no mocks. Real
cleanup by name prefix, matching every sibling `*_e2e.py` file's own
convention -- `implementations`/`implementation_tasks`/`evidence`/
`procedures`/`task_nodes` rows this test itself created are removed
(evidence via its `t_invalid` tombstone first is not required here since
we DELETE the rows outright, matching this file's own narrow cleanup
scope, not a general evidence-retraction precedent).
"""
from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.api.implementations import router as implementations_router
from app.api.solutions import router as solutions_router
from app.api.tasks import router as tasks_router
from app.db.session import create_pool
from app.execution import implementation_registry
from app.services.access import AccessScope
from app.services.capabilities import record_implementation_outcome
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "impl-api-e2e"

ANON = AccessScope.anonymous()


async def _cleanup(pool) -> None:
    # evidence is append-only (Band 1.9a invariant #19) -- retract via the
    # t_invalid tombstone, never DELETE, matching claim_graph_api_e2e.py's
    # own convention for evidence rows.
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE target_type = 'implementation' "
        "AND t_invalid IS NULL AND target_id IN "
        "(SELECT id FROM implementations WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute(
        "DELETE FROM implementation_tasks WHERE implementation_id IN "
        "(SELECT id FROM implementations WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM implementations WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{PREFIX}%")


async def _task_node(pool, name: str) -> str:
    row = await pool.fetchrow(
        "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2) RETURNING id",
        name, f"skill_{name}",
    )
    return str(row["id"])


def _app(pool) -> FastAPI:
    app = FastAPI()
    app.include_router(implementations_router)
    app.include_router(tasks_router)
    app.include_router(solutions_router)
    app.state.pool = pool
    return app


# ---------------------------------------------------------------------------
# GET /v1/implementations/{id}, list, evidence, capability
# ---------------------------------------------------------------------------


def test_get_implementation_returns_real_registered_row():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            impl = await implementation_registry.register(
                pool, name=f"{PREFIX}-graphify", kind="tool", provider="graphify",
                created_by=f"{PREFIX}-tester",
            )
            impl_id = impl["id"]

            result = await implementation_registry.get(pool, impl_id, scope=ANON)
            assert result is not None
            assert result["name"] == f"{PREFIX}-graphify"
            assert result["status"] == "candidate"
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_router_end_to_end_against_a_real_pool():
    """Everything -- pool creation, HTTP calls, and cleanup -- runs inside
    ONE asyncio.run() / one event loop: asyncpg pool connections are
    bound to the loop that created them, and the ASGI app run through
    httpx's ASGITransport must execute on that same loop too (same fix
    `test_claim_graph_api_e2e.py`'s own router smoke test already
    applies)."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)

            task_id = await _task_node(pool, f"{PREFIX}-task1")

            impl = await implementation_registry.register(
                pool, name=f"{PREFIX}-graphify", kind="tool", provider="graphify",
                created_by=f"{PREFIX}-tester",
                requirements={"needs": "network"},
                invocation={"protocol": "mcp", "tool": "graphify.extract"},
                auth_requirements={"credential_ref": "graphify_api_key"},
                task_node_ids=[task_id],
            )
            impl_id = impl["id"]
            activated = await implementation_registry.activate(pool, impl_id)
            assert activated["status"] == "active"

            evidence_id = await record_implementation_outcome(
                pool, implementation_id=impl_id, outcome_status="success",
                success_criteria={"predicate": "ran without error"},
                context_key=f"{PREFIX}-ctx",
            )
            assert evidence_id

            procedure = await capture_procedure(
                pool, name=f"{PREFIX}-linked-proc", goal="g",
                preconditions=[], provenance="system_pending_review", scope_type="global",
            )
            # Attribute the procedure to the same task_node via the real
            # migrated_from_task_node_id column (direct UPDATE -- there is
            # no capture_procedure kwarg for it; this column's only real
            # writer today is the not-yet-built legacy migration path per
            # implementation_registry_audit.md, so this test sets it
            # directly to exercise get_solution_implementation_detail's
            # real read side honestly).
            await pool.execute(
                "UPDATE procedures SET migrated_from_task_node_id = $1::uuid WHERE id = $2::uuid",
                task_id, procedure["id"],
            )

            app = _app(pool)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                # GET /v1/implementations/{id}
                resp = await client.get(f"/v1/implementations/{impl_id}")
                assert resp.status_code == 200, resp.text
                body = resp.json()
                assert body["id"] == impl_id
                assert body["status"] == "active"
                # auth_requirements only ever stores a credential_ref shape,
                # never a secret value -- sanity check nothing else leaked.
                assert body["auth_requirements"] == {"credential_ref": "graphify_api_key"}

                resp = await client.get(f"/v1/implementations/{uuid.uuid4()}")
                assert resp.status_code == 404

                # GET /v1/implementations (list)
                resp = await client.get(f"/v1/implementations?provider=graphify&status=active")
                assert resp.status_code == 200, resp.text
                assert any(r["id"] == impl_id for r in resp.json())

                # GET /v1/implementations/{id}/evidence
                resp = await client.get(f"/v1/implementations/{impl_id}/evidence")
                assert resp.status_code == 200, resp.text
                assert [e["id"] for e in resp.json()] == [evidence_id]

                # GET /v1/implementations/{id}/capability
                resp = await client.get(f"/v1/implementations/{impl_id}/capability")
                assert resp.status_code == 200, resp.text
                cap = resp.json()
                assert cap["evidence_count"] == 1
                assert cap["success_count"] == 1
                assert cap["level_gated"] is None

                # GET /v1/tasks/{id}/implementations
                resp = await client.get(f"/v1/tasks/{task_id}/implementations")
                assert resp.status_code == 200, resp.text
                assert [r["id"] for r in resp.json()] == [impl_id]

                resp = await client.get(f"/v1/tasks/{task_id}/implementations?status=all")
                assert resp.status_code == 200, resp.text
                assert [r["id"] for r in resp.json()] == [impl_id]

                # POST /v1/tasks/{id}/resolve-implementation -- real resolve
                resp = await client.post(
                    f"/v1/tasks/{task_id}/resolve-implementation", json={},
                )
                assert resp.status_code == 200, resp.text
                resolved = resp.json()
                assert resolved["implementation_id"] == impl_id
                assert resolved["provider"] == "graphify"
                assert "no hint given" in resolved["reason"]

                # POST resolve-implementation -- honest null for a task
                # with no linked implementation at all.
                empty_task_id = await _task_node(pool, f"{PREFIX}-task-empty")
                resp = await client.post(
                    f"/v1/tasks/{empty_task_id}/resolve-implementation", json={},
                )
                assert resp.status_code == 200, resp.text
                empty_resolved = resp.json()
                assert empty_resolved["implementation_id"] is None
                assert "no active implementation is linked" in empty_resolved["reason"]

                # GET /v1/solutions/{id}/implementations
                resp = await client.get(f"/v1/solutions/{procedure['id']}/implementations")
                assert resp.status_code == 200, resp.text
                assert [r["id"] for r in resp.json()] == [impl_id]
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_get_implementation_hides_a_private_row_from_an_anonymous_scope():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            impl = await implementation_registry.register(
                pool, name=f"{PREFIX}-private-impl", kind="tool", provider="graphify",
                created_by=f"{PREFIX}-tester",
                visibility="private", owner_id=f"{PREFIX}-owner",
            )
            impl_id = impl["id"]

            assert await implementation_registry.get(pool, impl_id, scope=ANON) is None

            owner_scope = AccessScope.for_user(f"{PREFIX}-owner")
            visible = await implementation_registry.get(pool, impl_id, scope=owner_scope)
            assert visible is not None

            app = _app(pool)
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(
                    f"/v1/implementations/{impl_id}", headers={"X-Viewer-Id": ""},
                )
                assert resp.status_code == 404
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
