"""
Live-database proving tests for tenant isolation on the MCP surface a signed-in
user actually uses (v1): one user's private rows never reach another signed-in
user or an anonymous caller.

The identity is set the way the server resolves it -- `_caller_access_scope()`,
which reads the verified token's subject -- so every assertion runs through the
real tool / resource handlers and their real SQL (scope_predicates /
visibility_predicate). Requires DATABASE_URL (a migrated Postgres + pgvector);
skips without one. CI runs it in the live-Postgres job.

  1. report_discovery writes a PRIVATE claim: the author reads it back through
     stealth://procedures/{id}/claims; another user and an anonymous caller get
     the same Procedure's claims without it.
  2. A PRIVATE Procedure owned by one user is not found -- not merely empty -- for
     another user or an anonymous caller, through the same resource.
  3. A user's private Goal is not offered to another user by the Goal search that
     both find_ways and submit_way use.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest

from app.db.session import create_pool
from app.services.access import AccessScope
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test",
)

PREFIX = "mcp-tenant-iso-e2e"


def _ctx(pool):
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": pool}))


async def _cleanup(pool) -> None:
    await pool.execute("DELETE FROM knowledge_nodes WHERE node_type = 'claim' "
                       "AND properties->>'source' = 'report_discovery' "
                       "AND properties->>'problem' LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{PREFIX}%")


def _as(monkeypatch, srv, scope: AccessScope) -> None:
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: scope)
    monkeypatch.setattr(srv, "_resolve_caller_identity", lambda fallback: scope.viewer_id or fallback)


def _hash_vector(text: str, dim: int = 1024) -> list[float]:
    """Deterministic stand-in vector: the same text always maps to the same unit
    vector, different texts to (near-)orthogonal ones. Isolation is decided by
    SQL visibility predicates, not by vector quality, and CI has no embedding key."""
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
    os.environ.setdefault("STEALTHLAB_MCP_TOKEN", "test-token")
    import app.mcp_server.server as server_module

    return server_module


def test_a_private_discovery_reaches_only_its_author(srv, monkeypatch):
    from app.mcp_server import resources

    alice, bob = f"alice-{uuid.uuid4().hex[:6]}", f"bob-{uuid.uuid4().hex[:6]}"
    secret = f"{PREFIX}-{uuid.uuid4().hex[:8]} the flaky test needs a fixed seed"

    async def run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            proc = await capture_procedure(
                pool, name=f"{PREFIX}-public-proc", goal="run the test suite reliably",
                steps=[{"order": 0, "goal": "run the tests"}], provenance="system_pending_review",
                scope_type="global", owner_id=alice, visibility="public",
            )
            pid = str(proc["procedure_id"] if proc.get("procedure_id") else proc["id"])

            _as(monkeypatch, srv, AccessScope.for_user(alice))
            out = await srv.report_discovery("fix", pid, secret, "seed the RNG in conftest", _ctx(pool))
            assert '"visibility": "private"' in out, out
            assert secret in await resources.procedure_claims_resource(pid, _ctx(pool))

            _as(monkeypatch, srv, AccessScope.for_user(bob))
            text = await resources.procedure_claims_resource(pid, _ctx(pool))
            assert "Not found" not in text                     # bob sees the public Procedure ...
            assert secret not in text, "bob read alice's private discovery"   # ... but not alice's claim

            _as(monkeypatch, srv, AccessScope.anonymous())
            assert secret not in await resources.procedure_claims_resource(pid, _ctx(pool))
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(run())


def test_a_private_procedure_is_not_found_by_anyone_else(srv, monkeypatch):
    from app.mcp_server import resources

    alice, bob = f"alice-{uuid.uuid4().hex[:6]}", f"bob-{uuid.uuid4().hex[:6]}"

    async def run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            proc = await capture_procedure(
                pool, name=f"{PREFIX}-private-proc", goal="alice's private deploy routine",
                steps=[{"order": 0, "goal": "deploy"}], provenance="system_pending_review",
                scope_type="global", owner_id=alice, visibility="private",
            )
            row_id = str(proc["id"])
            _as(monkeypatch, srv, AccessScope.for_user(alice))
            assert "Not found" not in await resources.procedure_claims_resource(row_id, _ctx(pool))
            for other in (AccessScope.for_user(bob), AccessScope.anonymous()):
                _as(monkeypatch, srv, other)
                assert "Not found" in await resources.procedure_claims_resource(row_id, _ctx(pool)), (
                    f"{other.viewer_id or 'anonymous'} resolved alice's private procedure")
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(run())


def test_a_private_goal_is_not_offered_to_another_user(srv):
    """The Goal search find_ways and submit_way both sit on (search_goals over
    goal_search_index, scoped by the caller) never returns another user's private
    Goal. Checked on the search itself rather than on resolve_intent's ranking
    thresholds, so the assertion is about visibility, not about a score."""
    from app.services.embeddings import Embedder
    from app.services.goals import find_or_create_goal, search_goals

    alice, bob = f"alice-{uuid.uuid4().hex[:6]}", f"bob-{uuid.uuid4().hex[:6]}"
    marker = f"zqx{uuid.uuid4().hex[:8]}"
    name = f"Rotate the {marker} signing keys for the billing service"

    async def run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        gid = None
        try:
            made = await find_or_create_goal(
                pool, canonical_name=name, scope_type="global", provenance="system_pending_review",
                rationale="private ops", owner_id=alice, visibility="private", created_by=alice,
                embedder=Embedder(),
            )
            gid = str(made["id"])
            vec = _hash_vector(name)

            async def ids(scope):
                rows = await search_goals(pool, query_text=name, query_embedding=vec, scope=scope, limit=20)
                return {str(r["id"]) for r in rows}

            assert gid in await ids(AccessScope.for_user(alice)), "the owner cannot find her own Goal"
            for other in (AccessScope.for_user(bob), AccessScope.anonymous()):
                assert gid not in await ids(other), f"{other.viewer_id or 'anonymous'} was offered alice's private Goal"
        finally:
            if gid:
                await pool.execute("UPDATE goals SET t_invalid = now() WHERE id = $1::uuid", gid)
            await pool.close()

    asyncio.run(run())
