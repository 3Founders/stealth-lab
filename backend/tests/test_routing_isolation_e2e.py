"""
Live-database proving tests for tenant isolation on the model-routing surface (recommend_models,
report_model_run, report_result, call_model) and on 'org' visibility.

Requires DATABASE_URL (a migrated Postgres + pgvector); skips without one, like every *_e2e.py here. The
identity is set the way the server resolves it (`_caller_access_scope`), so each assertion goes through the
real tool handlers and their real SQL.

  1. A private Goal is invisible to the routing tools for another user and an anonymous caller: recommend_models
     and report_model_run answer "not found" and write nothing.
  2. An instance_key is bound to the caller it was issued to, even on a PUBLIC Goal everyone can see (the MCP
     spec's state-handle-hijacking rule): another user cannot report under it or run its next model, and a later
     decision made under the same key does not hand it over.
  3. 'org' visibility reaches members of that org only.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest

from app.db.session import create_pool
from app.routing import store
from app.services.access import AccessScope

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test",
)


def _ctx(pool):
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": pool}))


def _hash_vector(text: str, dim: int = 1024) -> list[float]:
    import hashlib
    import math
    import random

    rng = random.Random(hashlib.sha256(text.encode("utf-8")).digest())
    v = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@pytest.fixture(autouse=True)
def _offline_embeddings(monkeypatch):
    from app.services.embeddings import Embedder

    async def embed(self, texts, input_type="document"):
        return [_hash_vector(t) for t in texts]

    async def embed_one(self, text, input_type="document"):
        return _hash_vector(text)

    monkeypatch.setattr(Embedder, "embed", embed)
    monkeypatch.setattr(Embedder, "embed_one", embed_one)


@pytest.fixture
def srv():
    # The server refuses to import when the token in effect differs from the one backend/.env declares, so a
    # hard-coded "test-token" made this suite error out (never run) on every checkout that has a .env.
    from pathlib import Path

    from dotenv import dotenv_values

    declared = dotenv_values(Path(__file__).resolve().parents[1] / ".env").get("STEALTHLAB_MCP_TOKEN")
    os.environ.setdefault("STEALTHLAB_MCP_TOKEN", declared or "test-token")
    import app.mcp_server.server as server_module

    return server_module


def _as(monkeypatch, srv, scope: AccessScope) -> None:
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: scope)
    monkeypatch.setattr(srv, "_resolve_caller_identity", lambda fallback: scope.viewer_id or fallback)


async def _goal(pool, *, owner: str, visibility: str) -> str:
    from app.services.embeddings import Embedder
    from app.services.goals import find_or_create_goal
    from app.services.search_projection import drain_outbox
    from app.services.shards import pools_for

    name = f"Rotate the zqx{uuid.uuid4().hex[:8]} signing keys for the billing service"
    made = await find_or_create_goal(
        pool, canonical_name=name, scope_type="global", provenance="system_pending_review",
        rationale="routing isolation", owner_id=owner, visibility=visibility, created_by=owner, embedder=Embedder())
    gid = str(made["id"])
    await drain_outbox(pool, batch=200, pools=pools_for(pool), max_batches=5)
    return gid


async def _decision(pool, gid: str, key: str, *, caller, visibility: str, owner: str) -> None:
    await store.record_decision(pool, {
        "id": str(uuid.uuid4()), "goal_id": gid, "procedure_id": None, "instance_key": key, "params_version": None,
        "candidates": ["m|direct"], "ladder": ["m|direct"], "propensity": 1.0, "meets_target": True, "predicted": {},
        "constraints": {"check_kind": "tests", "previous_attempts": 0, **({"_caller": caller} if caller else {})},
        "visibility": visibility, "owner_id": owner})


async def _observations(pool, gid: str, key: str) -> int:
    log = await store._goal_log_pool(pool, gid)
    return await log.fetchval("SELECT count(*) FROM routing_observations WHERE goal_id = $1::uuid AND instance_key = $2",
                              gid, key)


async def _cleanup(pool, gids: list[str]) -> None:
    for gid in gids:
        log = await store._goal_log_pool(pool, gid)
        await log.execute("DELETE FROM routing_observations WHERE goal_id = $1::uuid", gid)
        await log.execute("DELETE FROM routing_decisions WHERE goal_id = $1::uuid", gid)
        await pool.execute("UPDATE goals SET t_invalid = now() WHERE id = $1::uuid", gid)


def test_a_private_goal_is_invisible_to_the_routing_tools(srv, monkeypatch):
    alice, bob = f"alice-{uuid.uuid4().hex[:6]}", f"bob-{uuid.uuid4().hex[:6]}"

    async def run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        gid = None
        try:
            gid = await _goal(pool, owner=alice, visibility="private")
            key = f"{gid}.{uuid.uuid4().hex[:16]}"
            for who in (AccessScope.for_user(bob), AccessScope.anonymous()):
                _as(monkeypatch, srv, who)
                label = who.viewer_id or "anonymous"
                out = await srv.recommend_models(_ctx(pool), ["m|direct"], goal_id=gid)
                assert out.startswith("REFUSED") and "not found" in out, f"{label} reached alice's private Goal: {out}"
                out = await srv.report_model_run(_ctx(pool), "m", "direct", True, key, goal_id=gid)
                assert out.startswith("REFUSED") and "not found" in out, f"{label} reported on alice's private Goal: {out}"
            assert await _observations(pool, gid, key) == 0
            _as(monkeypatch, srv, AccessScope.for_user(alice))
            out = await srv.recommend_models(_ctx(pool), ["m|direct"], goal_id=gid)
            assert "not found" not in out, f"the owner cannot reach her own Goal: {out}"
        finally:
            if gid:
                await _cleanup(pool, [gid])
            await pool.close()

    asyncio.run(run())


def test_an_instance_key_belongs_to_its_caller_even_on_a_public_goal(srv, monkeypatch):
    alice, bob = f"alice-{uuid.uuid4().hex[:6]}", f"bob-{uuid.uuid4().hex[:6]}"

    async def run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        gid = None
        try:
            gid = await _goal(pool, owner=alice, visibility="public")
            key = f"{gid}.{uuid.uuid4().hex[:16]}"
            await _decision(pool, gid, key, caller=alice, visibility="public", owner=alice)

            _as(monkeypatch, srv, AccessScope.for_user(bob))
            assert (await srv.report_result(_ctx(pool), key, False)).startswith("REFUSED: unknown instance_key")
            assert (await srv.call_model(_ctx(pool), "hello", model="auto", instance_key=key)).startswith(
                "REFUSED: unknown instance_key")
            assert (await srv.call_model(_ctx(pool), "hello", model="m", instance_key=key)).startswith(
                "REFUSED: unknown instance_key")
            assert await _observations(pool, gid, key) == 0

            # a later decision under the same key (recommend_models lets a caller reuse a key), with no owner,
            # must not hand the instance over
            await _decision(pool, gid, key, caller=None, visibility="public", owner=bob)
            assert (await srv.report_result(_ctx(pool), key, False)).startswith("REFUSED: unknown instance_key")
            assert await _observations(pool, gid, key) == 0

            _as(monkeypatch, srv, AccessScope.for_user(alice))
            out = await srv.report_result(_ctx(pool), key, False)
            assert not out.startswith("REFUSED"), out
            assert await _observations(pool, gid, key) == 1
        finally:
            if gid:
                await _cleanup(pool, [gid])
            await pool.close()

    asyncio.run(run())


def test_org_visibility_reaches_only_members_of_that_org(srv):
    alice = f"alice-{uuid.uuid4().hex[:6]}"
    org_a, org_b = str(uuid.uuid4()), str(uuid.uuid4())

    async def run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        gid = None
        try:
            gid = await _goal(pool, owner=alice, visibility="private")
            await pool.execute("UPDATE goal_search_index SET visibility = 'org', tenant_id = $2::uuid "
                               "WHERE goal_id = $1::uuid", gid, org_a)

            async def sees(scope) -> bool:
                return await store.visible_goal(pool, gid, scope) is not None

            assert await sees(AccessScope.for_org_member("member-1", [org_a]))
            assert await sees(AccessScope.for_org_member("member-2", [org_b, org_a]))
            assert await sees(AccessScope.for_user(alice))                                # the owner keeps access
            assert not await sees(AccessScope.for_org_member("outsider", [org_b]))
            assert not await sees(AccessScope.for_user("nobody"))
            assert not await sees(AccessScope.anonymous())
        finally:
            if gid:
                await _cleanup(pool, [gid])
            await pool.close()

    asyncio.run(run())
