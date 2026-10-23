"""Database-backed proving tests for migration 152 (local_check_evidence) and services/verification.py.

LIVE-DATABASE TEST, LOOPBACK ONLY. It needs DATABASE_URL pointing at a database on this machine that already has the whole
migration chain applied (the scratch cluster on 127.0.0.1:55432, database kel_evidence_test, in the way this was written). It
refuses to run against any other host, and it TRUNCATEs local_check_evidence before each test, which is why that guard exists.
Without a DATABASE_URL it skips.

Row-level security needs a plain role: a superuser bypasses it whatever is declared. The tests therefore connect the service as
`kel_evidence_app` (created here, no superuser, only the privileges the service needs); the owner connection is used only to
prepare and to prove what the engine itself refuses.
"""
from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timezone
from urllib.parse import urlsplit, urlunsplit

import asyncpg
import pytest

from app.db.session import create_pool
from app.services import verification as ver
from app.services.access import TenantScope, bind_scope, scoped_transaction, tenant_transaction

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a loopback DATABASE_URL with migration 152 applied")

COMMONS = TenantScope.commons()
ORG_X = TenantScope.for_tenant(uuid.UUID("aaaaaaaa-aaaa-aaaa-aaaa-aaaaaaaaaaaa"))
REPO = "r:0123456789abcdef"
HEX = "e" * 64
APP_ROLE = "kel_evidence_app"


def _loopback_guard() -> str:
    host = urlsplit(DATABASE_URL).hostname
    if host not in ("127.0.0.1", "localhost", "::1"):
        pytest.exit(f"refusing to run: DATABASE_URL host {host!r} is not a loopback database (this test TRUNCATEs a table)", returncode=2)
    return DATABASE_URL


def _app_dsn(dsn: str) -> str:
    parts = urlsplit(dsn)
    host = parts.hostname + (f":{parts.port}" if parts.port else "")
    return urlunsplit((parts.scheme, f"{APP_ROLE}@{host}", parts.path, parts.query, parts.fragment))


def good(**over):
    base = {
        "repo_ref": REPO, "target_kind": "plan_node", "target_id": "N-3", "method": "deterministic_check", "result": "pass",
        "observed_at": "2026-10-23T10:00:00Z", "check_hash": HEX, "client_event_id": str(uuid.uuid4()),
        "hashes": [{"path_hmac": "b" * 64, "sha": "0123456789ab"}], "commit_sha": "c" * 40, "tree_id": "d" * 40,
        "where_ran": "worktree", "client_version": "1.2.3",
    }
    base.update(over)
    return base


async def _with_pools(scenario):
    dsn = _loopback_guard()
    admin = await create_pool(dsn)
    try:
        async with admin.acquire() as c:
            exists = await c.fetchval("SELECT 1 FROM pg_roles WHERE rolname = $1", APP_ROLE)
            if not exists:
                await c.execute(f"CREATE ROLE {APP_ROLE} LOGIN NOSUPERUSER NOBYPASSRLS")
            await c.execute(f"GRANT USAGE ON SCHEMA public TO {APP_ROLE}")
            await c.execute(f"GRANT SELECT, INSERT, UPDATE ON local_check_evidence TO {APP_ROLE}")
            await c.execute(f"GRANT SELECT ON local_check_current, local_check_effective TO {APP_ROLE}")
            await c.execute(f"GRANT SELECT ON goals TO {APP_ROLE}")
            await c.execute("TRUNCATE local_check_evidence")              # TRUNCATE does not fire the append-only row trigger
        app = await create_pool(_app_dsn(dsn))
        try:
            return await scenario(admin, app)
        finally:
            await app.close()
    finally:
        await admin.close()


def db(scenario_fn):
    def wrapper():
        return asyncio.run(_with_pools(scenario_fn))
    wrapper.__name__ = scenario_fn.__name__
    return wrapper


def test_the_service_connection_is_a_plain_role():
    @db
    async def scenario(admin, app):
        assert await app.fetchval("SELECT rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user") is False

    scenario()


def test_a_pass_is_checked_and_a_fail_is_failed_verification_through_the_service():
    @db
    async def scenario(admin, app):
        a = await ver.record_local_check(app, payload=good(), viewer_id="user-a", tenant_scope=COMMONS)
        b = await ver.record_local_check(app, payload=good(result="fail", check_hash="f" * 64), viewer_id="user-a", tenant_scope=COMMONS)
        assert a["state"] == "checked" and a["recorded"] is True
        assert b["state"] == "failed_verification"
        rows = await admin.fetch("SELECT result, state, provenance, scope_type, scope_entity_id, visibility::text AS v, owner_id, "
                                 "independence_group, tenant_id::text AS t FROM local_check_evidence ORDER BY result")
        assert [(r["result"], r["state"]) for r in rows] == [("fail", "failed_verification"), ("pass", "checked")]
        for r in rows:
            assert (r["provenance"], r["scope_type"], r["scope_entity_id"], r["v"]) == ("company_ingested", "repository", REPO, "private")
            assert (r["owner_id"], r["independence_group"]) == ("user-a", "account:user-a")
            assert r["t"] == str(COMMONS.tenant_id)

    scenario()


def test_the_same_event_twice_leaves_one_row():
    @db
    async def scenario(admin, app):
        payload = good()
        first = await ver.record_local_check(app, payload=payload, viewer_id="user-a", tenant_scope=COMMONS)
        again = await ver.record_local_check(app, payload=dict(payload), viewer_id="user-a", tenant_scope=COMMONS)
        assert first["recorded"] is True and again["recorded"] is False and again["duplicate"] is True
        assert again["id"] == first["id"]
        assert await admin.fetchval("SELECT count(*) FROM local_check_evidence") == 1
        # the same client_event_id from ANOTHER account is a different event
        other = await ver.record_local_check(app, payload=dict(payload), viewer_id="user-b", tenant_scope=COMMONS)
        assert other["recorded"] is True
        assert await admin.fetchval("SELECT count(*) FROM local_check_evidence") == 2

    scenario()


def test_the_table_is_append_only_with_one_tombstone_exception():
    @db
    async def scenario(admin, app):
        r = await ver.record_local_check(app, payload=good(), viewer_id="user-a", tenant_scope=COMMONS)
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await admin.execute("UPDATE local_check_evidence SET result = 'fail', state = 'failed_verification' WHERE id = $1::uuid", r["id"])
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await admin.execute("DELETE FROM local_check_evidence WHERE id = $1::uuid", r["id"])
        with pytest.raises(asyncpg.RaiseError, match="append-only"):
            await admin.execute("UPDATE local_check_evidence SET t_invalid = now(), client_version = 'x' WHERE id = $1::uuid", r["id"])
        await admin.execute("UPDATE local_check_evidence SET t_invalid = now() WHERE id = $1::uuid", r["id"])       # the tombstone
        with pytest.raises(asyncpg.RaiseError, match="already retracted"):
            await admin.execute("UPDATE local_check_evidence SET t_invalid = now() WHERE id = $1::uuid", r["id"])
        assert await admin.fetchval("SELECT count(*) FROM local_check_evidence") == 1

    scenario()


def _insert_sql(**over):
    cols = {
        "client_event_id": str(uuid.uuid4()), "repo_ref": REPO, "target_kind": "plan_node", "target_id": "N-3",
        "method": "deterministic_check", "result": "pass", "state": "checked", "independence_group": "account:x",
        "observed_at": datetime.now(timezone.utc), "hashes": [], "check_hash": HEX, "owner_id": "x",
    }
    cols.update(over)
    names = ", ".join(cols)
    marks = ", ".join(f"${i + 1}" for i in range(len(cols)))
    return f"INSERT INTO local_check_evidence ({names}) VALUES ({marks})", list(cols.values())


def test_the_engine_refuses_what_the_service_could_never_write():
    @db
    async def scenario(admin, app):
        sql, args = _insert_sql()
        await admin.execute(sql, *args)                                            # the baseline row is valid
        bad = [
            dict(state="verified"),                                                # a reported pass can be at most checked
            dict(state="independently_verified"),
            dict(result="fail"),                                                   # fail with state checked
            dict(state="failed_verification"),                                     # pass with state failed
            dict(method="self_report"), dict(method="human_review"),
            dict(target_kind="claim"), dict(target_id="fix the login bug"), dict(repo_ref="github.com/a/b"),
            dict(check_hash="npm test"), dict(commit_sha="main"), dict(where_ran="cloud"),
            dict(independence_group="   "),
            dict(hashes=[{"path": "src/app.js", "sha": "abcd"}]),                  # a path in the clear
            dict(hashes=[{"path_hmac": "b" * 64, "sha": "abcd", "extra": 1}]),
            dict(method="independent_agent"),                                      # no models
            dict(method="independent_agent", reviewer_model="glm-5.3", executor_model="GLM-5.3"),     # the same model
            dict(reviewer_model="claude-sonnet-5-5", executor_model="glm-5.3"),    # a deterministic row names no models
        ]
        for over in bad:
            sql, args = _insert_sql(**over)
            with pytest.raises(asyncpg.CheckViolationError):
                await admin.execute(sql, *args)
        # and one independent_agent row that is allowed
        sql, args = _insert_sql(method="independent_agent", reviewer_model="claude-sonnet-5-5", executor_model="glm-5.3")
        await admin.execute(sql, *args)

    scenario()


def test_row_level_security_isolates_accounts_and_tenants():
    @db
    async def scenario(admin, app):
        await ver.record_local_check(app, payload=good(), viewer_id="user-a", tenant_scope=COMMONS)
        await ver.record_local_check(app, payload=good(), viewer_id="user-b", tenant_scope=COMMONS)
        await ver.record_local_check(app, payload=good(), viewer_id="user-c", tenant_scope=ORG_X)

        async def owners(scope, owner=None, system=False):
            if scope is not None:
                async with tenant_transaction(app, scope) as conn:
                    await bind_scope(conn, owner=owner, system=system)
                    return sorted(r["owner_id"] for r in await conn.fetch("SELECT owner_id FROM local_check_evidence"))
            async with scoped_transaction(app, owner=owner, system=system) as conn:
                return sorted(r["owner_id"] for r in await conn.fetch("SELECT owner_id FROM local_check_evidence"))

        assert await app.fetch("SELECT 1 FROM local_check_evidence") == [], "no scope bound: nothing"
        assert await owners(COMMONS) == [], "a tenant without an account: nothing"
        assert await owners(None, owner="user-a") == ["user-a"], "no tenant bound is the commons posture (migration 150), and only its own"
        assert await owners(COMMONS, owner="user-a") == ["user-a"]
        assert await owners(COMMONS, owner="user-b") == ["user-b"]
        assert await owners(ORG_X, owner="user-a") == [], "user-a has no rows in org X"
        assert await owners(ORG_X, owner="user-c") == ["user-c"]
        assert await owners(COMMONS, owner="user-c") == [], "user-c's rows are in org X, not the commons"
        assert await owners(None, system=True) == ["user-a", "user-b", "user-c"]
        # WITH CHECK: one account cannot write a row for another
        async with tenant_transaction(app, COMMONS) as conn:
            await bind_scope(conn, owner="user-b")
            sql, args = _insert_sql(owner_id="user-a", independence_group="account:user-a")
            with pytest.raises(asyncpg.InsufficientPrivilegeError):
                await conn.execute(sql, *args)

    scenario()


def test_verified_needs_two_independent_accounts_on_the_same_check_and_files():
    @db
    async def scenario(admin, app):
        H1 = [{"path_hmac": "1" * 64, "sha": "aaaa1111"}]
        H2 = [{"path_hmac": "1" * 64, "sha": "bbbb2222"}]          # the same file, other content: newer code

        async def state(scope=COMMONS, target="N-3"):
            rows = await ver.local_check_effective(app, tenant_scope=scope, repo_ref=REPO, target_id=target)
            return [(r["effective_state"], r["passing_groups"], r["failing_groups"]) for r in rows]

        def at(n):
            return f"2026-10-23T10:{n:02d}:00Z"

        await ver.record_local_check(app, payload=good(hashes=H1, observed_at=at(1)), viewer_id="user-a", tenant_scope=COMMONS)
        assert await state() == [("checked", 1, 0)], "one account: checked, not verified"
        await ver.record_local_check(app, payload=good(hashes=H1, observed_at=at(2)), viewer_id="user-a", tenant_scope=COMMONS)
        assert await state() == [("checked", 1, 0)], "the same account again is the same group"
        await ver.record_local_check(app, payload=good(hashes=H1, observed_at=at(3)), viewer_id="user-b", tenant_scope=COMMONS)
        assert await state() == [("verified", 2, 0)], "two accounts, same check, same files"
        await ver.record_local_check(app, payload=good(hashes=H1, result="fail", observed_at=at(4)), viewer_id="user-b", tenant_scope=COMMONS)
        assert await state() == [("contested", 1, 1)], "user-b's latest says fail on the same files"
        await ver.record_local_check(app, payload=good(hashes=H2, observed_at=at(5)), viewer_id="user-a", tenant_scope=COMMONS)
        assert await state() == [("checked", 1, 0)], "newer files: the older signature no longer counts"
        assert await state(ORG_X) == [], "another tenant sees none of it"
        assert await state(target="N-9") == [], "another target"
        # a retraction removes a row from the count
        await admin.execute("UPDATE local_check_evidence SET t_invalid = now() WHERE owner_id = 'user-a' AND observed_at = $1",
                            datetime(2026, 10, 23, 10, 5, tzinfo=timezone.utc))
        assert await state() == [("contested", 1, 1)], "user-a's current row is its earlier pass again; user-b's fail is the newest"

    scenario()


def test_an_unknown_goal_is_refused_and_a_known_one_is_recorded():
    @db
    async def scenario(admin, app):
        with pytest.raises(ver.VerificationError, match="was not found"):
            await ver.record_local_check(app, payload=good(goal_id=str(uuid.uuid4())), viewer_id="user-a", tenant_scope=COMMONS)
        gid = await admin.fetchval(
            "INSERT INTO goals (canonical_name, provenance, scope_type, scope_entity_id) "
            "VALUES ('evidence test goal ' || gen_random_uuid()::text, 'company_ingested', 'repository', $1) RETURNING id", REPO)
        out = await ver.record_local_check(app, payload=good(goal_id=str(gid)), viewer_id="user-a", tenant_scope=COMMONS)
        assert out["recorded"] is True
        assert await admin.fetchval("SELECT goal_id::text FROM local_check_evidence") == str(gid)

    scenario()
