"""P1-A isolation tests that were missing (securityp1.md §5.3). The database-backed ones need DATABASE_URL (a migrated
THROWAWAY Postgres; run them with scripts/run_isolation_suites.py, which refuses a non-loopback database) and are
meant to run as the restricted `stealth_app` role as well as the owner. The rest run offline.

  #2  another caller's instance_key, an unknown key and a key issued to nobody answer the SAME error text
  #4  an anonymous caller: every write/execute tool refused by scope, reads allowed; a blank Bearer is anonymous
  #6  a transaction that forgets tenant_transaction: strict (migration 136) tables see and write nothing; the
      db/29 tables see only the shared commons (migration 150 closed the permissive fallback)
  RLS migration 150: sync tables deny without an owner bound; routing logs show 'public' rows to everyone and other
      rows only to their owner's / organisation's readers
  #7  a pooled connection reused after tenant A's transaction carries no tenant setting
  #8  the organisation a provider call is billed to is never taken from the caller unchecked
 #10  a withdrawn (tombstoned) procedure is not returned to anyone, its owner included
"""
from __future__ import annotations

import asyncio
import os
import uuid
from types import SimpleNamespace

import pytest

from app.services.access import AccessScope, TenantScope, tenant_transaction

DATABASE_URL = os.environ.get("DATABASE_URL")
needs_db = pytest.mark.skipif(not DATABASE_URL, reason="requires a migrated throwaway DATABASE_URL")


def run(coro):
    return asyncio.run(coro)


def _unit_vector(seed: str, dim: int = 1024) -> list[float]:
    import hashlib
    import math
    import random

    rng = random.Random(hashlib.sha256(seed.encode("utf-8")).digest())
    v = [rng.uniform(-1.0, 1.0) for _ in range(dim)]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]


@pytest.fixture(autouse=True)
def _offline_embeddings(monkeypatch):
    """No embedding provider is ever called (the suite runs offline; the provider is not under test)."""
    from app.services.embeddings import Embedder

    async def embed(self, texts, input_type="document"):
        return [_unit_vector(t) for t in texts]

    async def embed_one(self, text, input_type="document"):
        return _unit_vector(text)

    monkeypatch.setattr(Embedder, "embed", embed)
    monkeypatch.setattr(Embedder, "embed_one", embed_one)


@pytest.fixture
def srv():
    from pathlib import Path

    from dotenv import dotenv_values

    declared = dotenv_values(Path(__file__).resolve().parents[1] / ".env").get("STEALTHLAB_MCP_TOKEN")
    os.environ.setdefault("STEALTHLAB_MCP_TOKEN", declared or "test-token")
    import app.mcp_server.server as server_module

    return server_module


# ---------------------------------------------------------------- #4 anonymous caller (offline)

def test_an_anonymous_caller_may_read_but_every_write_and_execute_tool_is_refused(srv, monkeypatch):
    from app.mcp_server.anonymous_read import _ANONYMOUS_READ_TOKEN

    verifier = srv.OidcAwareTokenVerifier("operator-secret", None, None)
    token = run(verifier.verify_token(_ANONYMOUS_READ_TOKEN))
    assert token is not None and getattr(token, "subject", None) is None      # never attributed as a user
    monkeypatch.setattr(srv, "get_access_token", lambda: token)
    reads = {"find_ways", "recommend_models"}
    writes = {"report_result", "report_model_run", "submit_way", "report_discovery", "call_model"}
    assert reads | writes == set(srv.V1_TOOLS)                                # all seven v1 tools are classified
    for tool in reads:
        srv._enforce_tool_scope(tool)                                         # allowed
    for tool in writes:
        with pytest.raises(PermissionError, match="forbidden"):
            srv._enforce_tool_scope(tool)
    with pytest.raises(PermissionError):
        srv._enforce_tool_scope("a_tool_nobody_classified_yet")               # unlisted defaults to write


def test_a_blank_bearer_header_is_treated_as_no_header():
    from app.mcp_server.anonymous_read import _is_blank_bearer

    for raw in (b"", b"Bearer", b"Bearer ", b"bearer   ", b"  "):
        assert _is_blank_bearer(raw)
    assert not _is_blank_bearer(b"Bearer abc")


def test_the_anonymous_scope_is_public_only():
    scope = AccessScope.anonymous()
    assert scope.viewer_id is None and not scope.is_unrestricted


# ---------------------------------------------------------------- #8 who pays for a provider call (offline)

def _conn(owner: str):
    return SimpleNamespace(owner=owner, connection_id="c1")


def test_the_billed_organisation_is_never_taken_from_the_caller_unchecked():
    from app.providers.service import ProviderCallDenied, governing_org

    member_of_x = SimpleNamespace(org_ids=("org-x",))
    # an org-owned connection is billed to its owner; naming another org is refused
    assert governing_org(_conn("org:org-x"), member_of_x, None, governed=True) == "org-x"
    with pytest.raises(ProviderCallDenied):
        governing_org(_conn("org:org-x"), member_of_x, "org-y", governed=True)
    # naming an organisation you are not a member of is refused
    with pytest.raises(ProviderCallDenied, match="not a member"):
        governing_org(_conn("platform"), member_of_x, "org-y", governed=True)
    # no organisation context at all fails CLOSED for an org-billed connection
    with pytest.raises(ProviderCallDenied):
        governing_org(_conn("platform"), SimpleNamespace(org_ids=()), None, governed=True)
    # several memberships and no choice: refused, never guessed
    with pytest.raises(ProviderCallDenied, match="several"):
        governing_org(_conn("platform"), SimpleNamespace(org_ids=("org-x", "org-z")), None, governed=True)


# ---------------------------------------------------------------- #2 error oracle (database)

@needs_db
def test_unknown_foreign_and_ownerless_instance_keys_answer_identically():
    from app.db.session import create_pool
    from app.routing import plan, store

    async def go():
        from app.services.embeddings import Embedder
        from app.services.goals import find_or_create_goal
        from app.services.search_projection import drain_outbox
        from app.services.shards import pools_for

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        made = await find_or_create_goal(
            pool, canonical_name=f"Rotate the p1a{uuid.uuid4().hex[:8]} oracle keys", scope_type="global",
            provenance="system_pending_review", rationale="p1a oracle", owner_id=None, visibility="public",
            created_by="p1a", embedder=Embedder())
        gid = str(made["id"])
        await drain_outbox(pool, batch=200, pools=pools_for(pool), max_batches=5)
        try:
            owned, ownerless = f"{gid}.{uuid.uuid4().hex[:16]}", f"{gid}.{uuid.uuid4().hex[:16]}"
            for key, caller in ((owned, "user-a"), (ownerless, None)):
                await store.record_decision(pool, {
                    "id": str(uuid.uuid4()), "goal_id": gid, "instance_key": key, "candidates": ["m|s"],
                    "ladder": ["m|s"], "propensity": 1.0, "meets_target": True, "predicted": {},
                    "constraints": {"_caller": caller}, "visibility": "public", "owner_id": None})
            b = AccessScope.for_user("user-b")
            texts = []
            for key in (owned, ownerless, f"{gid}.{uuid.uuid4().hex[:16]}"):
                with pytest.raises(plan.RoutingError) as err:
                    await plan.load_instance(pool, b, key)
                texts.append(str(err.value))
            assert len(set(texts)) == 1, texts                    # no way to tell foreign / ownerless / unknown apart
            # the stored constraints are a JSON object now (migration 149 / store._jsonable), visible to SQL ->>
            got = await pool.fetchval(
                "SELECT constraints->>'_caller' FROM routing_decisions WHERE instance_key = $1", owned)
            assert got == "user-a"
        finally:
            log = await store._goal_log_pool(pool, gid)
            await log.execute("DELETE FROM routing_decisions WHERE goal_id = $1::uuid", gid)
            await pool.execute("UPDATE goals SET t_invalid = now() WHERE id = $1::uuid", gid)
            await pool.close()
    run(go())


# ---------------------------------------------------------------- #7 pooled connection reuse (database)

@needs_db
def test_a_pooled_connection_carries_no_tenant_after_a_tenant_transaction():
    from app.db.session import create_pool

    async def go():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=1)    # one connection: reuse is certain
        try:
            org = str(uuid.uuid4())
            async with tenant_transaction(pool, TenantScope.for_tenant(org)) as conn:
                assert await conn.fetchval("SELECT current_setting('app.tenant_id', true)") == org
            leftover = await pool.fetchval("SELECT current_setting('app.tenant_id', true)")
            assert leftover in (None, ""), "tenant A's setting leaked to the next borrower of the connection"
        finally:
            await pool.close()
    run(go())


# ---------------------------------------------------------------- #6 forgetting tenant_transaction (database)

@needs_db
def test_without_a_tenant_setting_strict_tables_see_nothing_and_the_db29_tables_see_only_the_commons():
    """Run as the restricted role (stealth_app) to mean anything: an owner / BYPASSRLS role skips RLS entirely."""
    from app.db.session import create_pool

    async def go():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=1)
        try:
            bypass = await pool.fetchval(
                "SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user")
            if bypass:
                pytest.skip("connected as a role that bypasses RLS; run as stealth_app (scripts/sql/create_app_role.sql)")
            org = str(uuid.uuid4())
            await pool.execute("INSERT INTO organizations (id, name, slug) VALUES ($1::uuid, $2, $2)", org,
                               f"p1a-{org[:8]}")
            async with tenant_transaction(pool, TenantScope.for_tenant(org)) as conn:
                await conn.execute("INSERT INTO org_policies (organization_id, updated_by) VALUES ($1::uuid, 'p1a')", org)
            # strict (migration 136): with no tenant setting the row is invisible
            assert await pool.fetchval("SELECT count(*) FROM org_policies WHERE organization_id = $1::uuid", org) == 0
            async with tenant_transaction(pool, TenantScope.for_tenant(org)) as conn:
                assert await conn.fetchval(
                    "SELECT count(*) FROM org_policies WHERE organization_id = $1::uuid", org) == 1
            # migration 150 closed securityp1.md §5.1 #3: with app.tenant_id unset, the db/29 tables allow ONLY the shared
            # commons tenant's rows -- any other tenant's row is invisible -- and the system scope is explicit
            commons, other = "00000000-0000-0000-0000-000000000001", str(uuid.uuid4())
            assert await pool.fetchval("SELECT sl_tenant_scope_allows($1::uuid)", commons) is True
            assert await pool.fetchval("SELECT sl_tenant_scope_allows($1::uuid)", other) is False
            async with tenant_transaction(pool, TenantScope.for_tenant(other)) as conn:
                assert await conn.fetchval("SELECT sl_tenant_scope_allows($1::uuid)", other) is True
                assert await conn.fetchval("SELECT sl_tenant_scope_allows($1::uuid)", commons) is False
            async with tenant_transaction(pool, TenantScope.unrestricted()) as conn:      # the explicit system scope
                assert await conn.fetchval("SELECT sl_tenant_scope_allows($1::uuid)", other) is True
        finally:
            try:
                async with tenant_transaction(pool, TenantScope.for_tenant(org)) as conn:
                    await conn.execute("DELETE FROM org_policies WHERE organization_id = $1::uuid", org)
            except Exception:  # noqa: BLE001 -- cleanup only
                pass
            await pool.close()
    run(go())


# ---------------------------------------------------------------- #10 withdrawn content (database)

@needs_db
def test_a_withdrawn_procedure_is_returned_to_nobody():
    from app.db.session import create_pool
    from app.services.applicability import find_applicable_procedures
    from app.services.procedures import capture_procedure

    async def go():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        row_id = None
        try:
            run_id = uuid.uuid4().hex[:8]
            v = _unit_vector(f"p1a-withdrawn-{run_id}")
            result = await capture_procedure(
                pool, name=f"p1a-withdrawn-{run_id}", goal=f"p1a withdrawn marker {run_id}",
                steps=[{"order": 0, "goal": "do it"}], provenance="system_pending_review", scope_type="global",
                embedding=v, is_engineering_fixture=False)
            row_id = result["id"]
            before = await find_applicable_procedures(pool, goal_embedding=v, require_verified=False, limit=10)
            assert str(row_id) in {str(c["id"]) for c in before}          # control: reachable while live
            await pool.execute("UPDATE procedures SET t_invalid = now() WHERE id = $1", row_id)
            after = await find_applicable_procedures(pool, goal_embedding=v, require_verified=False, limit=10)
            assert str(row_id) not in {str(c["id"]) for c in after}
        finally:
            if row_id is not None:
                await pool.execute("DELETE FROM procedures WHERE id = $1", row_id)
            await pool.close()
    run(go())



# ---------------------------------------------------------------- migration 150: sync and routing tables (database)

async def _restricted_pool():
    from app.db.session import create_pool

    pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
    if await pool.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user"):
        await pool.close()
        pytest.skip("connected as a role that bypasses RLS; run as stealth_app (scripts/sql/create_app_role.sql)")
    return pool


@needs_db
def test_sync_rows_are_invisible_without_their_owner_bound_and_a_conflict_is_still_refused():
    from app.services.access import scoped_transaction
    from app.stealth import project_sync as ps

    async def go():
        pool = await _restricted_pool()
        project = str(uuid.uuid4())
        alice, bob = f"alice-{uuid.uuid4().hex[:6]}", f"bob-{uuid.uuid4().hex[:6]}"
        try:
            await ps.sync_project(pool, project_id=project, owner_subject=alice)
            # unscoped and wrong-owner reads see nothing; the owner sees her row
            assert await pool.fetchval("SELECT count(*) FROM synced_projects WHERE project_id = $1::uuid", project) == 0
            async with scoped_transaction(pool, owner=bob) as conn:
                assert await conn.fetchval(
                    "SELECT count(*) FROM synced_projects WHERE project_id = $1::uuid", project) == 0
            assert (await ps.get_synced_project(pool, project_id=project, owner_subject=alice)) is not None
            assert await ps.get_synced_project(pool, project_id=project, owner_subject=bob) is None
            assert [r["project_id"] for r in await ps.list_synced_projects(pool, owner_subject=bob)] == []
            # the cross-owner checks still work, without revealing the other owner
            prev = await ps.preview_sync(pool, project_id=project, owner_subject=bob)
            assert prev["synced_by_someone_else"] is True and "owner_subject" not in prev
            with pytest.raises(ps.AlreadySyncedToAnotherAccount):
                await ps.sync_project(pool, project_id=project, owner_subject=bob)
            # a write that names another owner is refused by the policy itself
            with pytest.raises(Exception):
                async with scoped_transaction(pool, owner=bob) as conn:
                    await conn.execute("INSERT INTO synced_projects (project_id, owner_subject) VALUES ($1::uuid, $2)",
                                       str(uuid.uuid4()), alice)
            assert await ps.unsync_project(pool, project_id=project, owner_subject=bob) is False
            assert await ps.unsync_project(pool, project_id=project, owner_subject=alice) is True
        finally:
            async with scoped_transaction(pool, system=True) as conn:
                await conn.execute("DELETE FROM synced_projects WHERE project_id = $1::uuid", project)
            await pool.close()
    run(go())


@needs_db
def test_routing_logs_show_public_rows_to_all_and_private_rows_only_to_their_readers():
    from app.routing import store
    from app.services.access import AccessScope as Scope

    async def go():
        pool = await _restricted_pool()
        gid, org = str(uuid.uuid4()), str(uuid.uuid4())
        alice, bob = f"alice-{uuid.uuid4().hex[:6]}", f"bob-{uuid.uuid4().hex[:6]}"
        keys = {}
        try:
            for name, vis, owner in (("pub", "public", None), ("priv", "private", alice), ("org", "org", org)):
                keys[name] = f"{gid}.{uuid.uuid4().hex[:16]}"
                with store.routing_scope(system=True):
                    await store.record_decision(pool, {
                        "id": str(uuid.uuid4()), "goal_id": gid, "instance_key": keys[name], "candidates": ["m|s"],
                        "ladder": ["m|s"], "propensity": 1.0, "meets_target": True, "predicted": {},
                        "constraints": {}, "visibility": vis, "owner_id": owner})

            async def seen(scope=None, system=False):
                with store.routing_scope(scope, system=system):
                    out = set()
                    for name, key in keys.items():
                        if await store.instance_decision(pool, gid, key) is not None:
                            out.add(name)
                    return out

            assert await seen() == {"pub"}                                          # no scope: public only
            assert await seen(Scope.anonymous()) == {"pub"}
            assert await seen(Scope.for_user(bob)) == {"pub"}                       # another user
            assert await seen(Scope.for_user(alice)) == {"pub", "priv"}             # the owner
            assert await seen(Scope.for_org_member(bob, [org])) == {"pub", "org"}  # an org member
            assert await seen(system=True) == {"pub", "priv", "org"}                # workers
            # a private write outside its owner's scope is refused by the policy (fails closed)
            with pytest.raises(Exception):
                with store.routing_scope(Scope.for_user(bob)):
                    await store.record_decision(pool, {
                        "id": str(uuid.uuid4()), "goal_id": gid, "instance_key": f"{gid}.x", "candidates": [],
                        "ladder": [], "propensity": 1.0, "meets_target": True, "predicted": {}, "constraints": {},
                        "visibility": "private", "owner_id": alice})
        finally:
            with store.routing_scope(system=True):
                log = await store._goal_log_pool(pool, gid)
                await store._log_call(log, "execute", "DELETE FROM routing_decisions WHERE goal_id = $1::uuid", gid)
            await pool.close()
    run(go())


def test_routing_rows_of_an_org_goal_are_owned_by_the_organisation():
    from app.routing.store import routing_owner

    assert routing_owner("org", "creator-user", "org-1") == "org-1"
    assert routing_owner("private", "u1", "org-1") == "u1"
    assert routing_owner("public", None, None) is None
    assert routing_owner("org", "creator-user", None) == "creator-user"      # no tenant recorded: the owner
