"""
Live-database proving tests: User A's private rows are not reachable by
User B's authenticated session through the real API/service call path
(CLAUDE.md's `scope_predicates()`/`visibility_predicate()` contract).

Same pattern as every other `*_e2e.py` file in this suite (see
`test_procedure_graph_api_e2e.py`, `test_claim_graph_api_e2e.py`,
`test_implementation_api_e2e.py`): requires a real DATABASE_URL, skips
(not fails) without one. Identity for each simulated caller comes through
the SAME `X-Viewer-Id` header `app/api/deps.py::get_scope` documents as
the (deliberately temporary, pre-real-auth) trust boundary -- this test
does not construct SQL directly and does not import `visibility_predicate`
itself; it only ever asks the real routers, exactly as a real client
would.

Three tables, three writers, one shared assertion: `owner_id`/
`visibility='private'` on a row means only that owner's viewer_id (or an
unrestricted/internal caller) can read it back through the real API --
never another authenticated viewer, and never an anonymous one.
"""
from __future__ import annotations

import asyncio
import os
import uuid

import httpx
import pytest
from fastapi import FastAPI

from app.api.claims import router as claims_router
from app.api.procedures import router as procedures_router
from app.db.session import create_pool
from app.services.claims import capture_claim
from app.services.procedures import capture_procedure, record_execution_outcome

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "cross-user-iso-e2e"
ALICE = "alice"
BOB = "bob"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def _cleanup(pool) -> None:
    await pool.execute(
        "UPDATE evidence SET t_invalid = now() WHERE t_invalid IS NULL AND ("
        "  target_id IN (SELECT id FROM procedures WHERE name LIKE $1)"
        "  OR target_id IN (SELECT id FROM knowledge_nodes WHERE name LIKE $1)"
        ")",
        f"{PREFIX}%",
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


def _app() -> FastAPI:
    app = FastAPI()
    app.include_router(procedures_router)
    app.include_router(claims_router)
    return app


def test_private_procedure_is_invisible_to_a_different_authenticated_viewer():
    """Table 1: `procedures`. A private procedure owned by alice: alice's
    own session sees it, bob's real, authenticated session does not (404,
    same anti-enumeration posture the router already documents -- never a
    403 that would confirm the row exists), and neither does an anonymous
    caller."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            result = await capture_procedure(
                pool, name=f"{PREFIX}-alice-private-proc", goal="alice's private procedure",
                steps=[{"order": 0, "goal": "do the private thing"}],
                provenance="system_pending_review", scope_type="global",
                owner_id=ALICE, visibility="private",
            )
            row_id = result["id"]
            app = _app()
            app.state.pool = pool
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/v1/procedures/{row_id}", headers={"X-Viewer-Id": ALICE})
                assert resp.status_code == 200, resp.text
                assert resp.json()["id"] == row_id

                resp = await client.get(f"/v1/procedures/{row_id}", headers={"X-Viewer-Id": BOB})
                assert resp.status_code == 404, (
                    "bob's authenticated session retrieved alice's private "
                    f"procedure -- cross-user isolation broken: {resp.text}"
                )

                resp = await client.get(f"/v1/procedures/{row_id}")  # anonymous, no header
                assert resp.status_code == 404, resp.text
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_private_claim_is_invisible_to_a_different_authenticated_viewer():
    """Table 2: `knowledge_nodes` (claims). Same contract as above, through
    `/v1/claims/{id}` -- a real, independent router and a real,
    independent service module (`claim_graph_api.get_claim`)."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            task_row = await pool.fetchrow(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2) RETURNING id",
                f"{PREFIX}-task", f"skill_{PREFIX}",
            )
            claim_id = await capture_claim(
                pool, statement=f"{PREFIX} alice's private claim",
                task_ids=[f"skill_{PREFIX}"], embedder=FakeEmbedder(),
                subject=f"{PREFIX}:project:p1", predicate="uses", object="pandas",
                owner_id=ALICE, visibility="private",
            )
            assert claim_id
            app = _app()
            app.state.pool = pool
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/v1/claims/{claim_id}", headers={"X-Viewer-Id": ALICE})
                assert resp.status_code == 200, resp.text
                assert resp.json()["id"] == claim_id

                resp = await client.get(f"/v1/claims/{claim_id}", headers={"X-Viewer-Id": BOB})
                assert resp.status_code == 404, (
                    "bob's authenticated session retrieved alice's private "
                    f"claim -- cross-user isolation broken: {resp.text}"
                )

                resp = await client.get(f"/v1/claims/{claim_id}")  # anonymous
                assert resp.status_code == 404, resp.text
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())


def test_private_evidence_row_is_omitted_from_a_different_viewers_evidence_list():
    """Table 3: `evidence`. The parent procedure itself is public (both
    viewers can read the procedure row), but one evidence row targeting it
    is private to alice. Anti-enumeration posture for a COLLECTION
    endpoint is omission, not a 404 for the whole list (claims.py's own
    documented rule) -- bob's evidence list must be missing alice's
    private row entirely, never a redacted placeholder that would still
    reveal it exists."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            result = await capture_procedure(
                pool, name=f"{PREFIX}-shared-proc", goal="a public procedure",
                steps=[{"order": 0, "goal": "do the public thing"}],
                provenance="system_pending_review", scope_type="global",
            )
            row_id = result["id"]

            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=True, context_key=f"{PREFIX}-public-ctx",
            )
            await record_execution_outcome(
                pool, procedure_row_id=row_id, success=False, context_key=f"{PREFIX}-private-ctx",
                failure_class="environment_changed", owner_id=ALICE, visibility="private",
            )
            # Fixture setup only (not part of the isolation assertion below,
            # which goes through the real API exclusively): read back the
            # two real evidence ids `record_execution_outcome` just wrote,
            # by the distinct `context_key` each call was given.
            public_evidence_id = str(await pool.fetchval(
                "SELECT id FROM evidence WHERE target_id = $1::uuid AND context_key = $2",
                row_id, f"{PREFIX}-public-ctx",
            ))
            private_evidence_id = str(await pool.fetchval(
                "SELECT id FROM evidence WHERE target_id = $1::uuid AND context_key = $2",
                row_id, f"{PREFIX}-private-ctx",
            ))
            assert public_evidence_id != private_evidence_id

            app = _app()
            app.state.pool = pool
            transport = httpx.ASGITransport(app=app)
            async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
                resp = await client.get(f"/v1/procedures/{row_id}/evidence", headers={"X-Viewer-Id": ALICE})
                assert resp.status_code == 200, resp.text
                alice_ids = {e["id"] for e in resp.json()}
                assert {public_evidence_id, private_evidence_id} <= alice_ids

                resp = await client.get(f"/v1/procedures/{row_id}/evidence", headers={"X-Viewer-Id": BOB})
                assert resp.status_code == 200, resp.text
                bob_ids = {e["id"] for e in resp.json()}
                assert public_evidence_id in bob_ids
                assert private_evidence_id not in bob_ids, (
                    "bob's authenticated session saw alice's private evidence row "
                    "-- cross-user isolation broken"
                )

                resp = await client.get(f"/v1/procedures/{row_id}/evidence")  # anonymous
                assert resp.status_code == 200, resp.text
                anon_ids = {e["id"] for e in resp.json()}
                assert public_evidence_id in anon_ids
                assert private_evidence_id not in anon_ids
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
