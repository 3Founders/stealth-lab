"""Client-reported check evidence (migration 152, services/verification.py, the MCP tool report_check_evidence).

Offline proving tests for docs/evidence_upload_design.md section 5: a caller never chooses a state, only the whitelist is
accepted, the same event twice is one row, scope and provenance reach the write, the reviewer must not be the executor, the
migration keeps the table append-only with strict row-level security, and the client's whitelist equals the server's. The
database-backed counterparts are in test_local_check_evidence_e2e.py (a scratch Postgres only).
"""
from __future__ import annotations

import asyncio
import inspect
import json
import re
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import verification as ver
from app.services.access import AccessScope, TenantScope

ROOT = Path(__file__).resolve().parents[2]
MIGRATION = ROOT / "backend" / "db" / "152_local_check_evidence.sql"
CLIENT_WHITELIST = ROOT / "packaging" / "npm" / "lib" / "evidence_upload.mjs"
TENANT = TenantScope.for_tenant(uuid.UUID("11111111-1111-1111-1111-111111111111"))
HEX64 = "a" * 64


def run(coro):
    return asyncio.run(coro)


def good(**over):
    base = {
        "repo_ref": "r:0123456789abcdef", "target_kind": "plan_node", "target_id": "N-3", "method": "deterministic_check",
        "result": "pass", "observed_at": "2026-10-23T10:00:00Z", "check_hash": HEX64,
        "client_event_id": "6f1f5f6e-7d7e-4f0e-9d3a-0a7b6c5d4e3f",
        "hashes": [{"path_hmac": "b" * 64, "sha": "0123456789ab"}], "commit_sha": "c" * 40, "tree_id": "d" * 40,
        "where_ran": "worktree", "client_version": "1.2.3",
    }
    base.update(over)
    return base


class FakeConn:
    def __init__(self, insert_result):
        self.calls = []
        self._insert_result = insert_result

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        if "INSERT INTO local_check_evidence" in sql:
            return self._insert_result
        return {"id": uuid.UUID("22222222-2222-2222-2222-222222222222"), "state": "checked"}

    async def fetch(self, sql, *args):
        self.calls.append(("fetch", sql, args))
        return []

    async def fetchval(self, sql, *args):
        self.calls.append(("fetchval", sql, args))
        return 1 if self.goal_exists else None

    goal_exists = True


class FakePool:
    def __init__(self, insert_result=None):
        self.conn = FakeConn(insert_result)
        self.insert_result = insert_result

    @asynccontextmanager
    async def acquire(self):
        yield self.conn


# -------------------------------------------------------------------------------------------------- the state is never asked for

def test_a_caller_cannot_set_a_state():
    assert "state" not in inspect.signature(ver.record_local_check).parameters
    with pytest.raises(ver.VerificationError, match="fields not allowed.*state"):
        ver.validate_local_check(good(state="verified"))
    assert ver.local_check_state("deterministic_check", "pass") == "checked"
    assert ver.local_check_state("independent_agent", "pass") == "checked", "a reviewer's pass is reported, not observed: checked too"
    assert ver.local_check_state("deterministic_check", "fail") == "failed_verification"
    for method in ("self_report", "human_review", "real_world_outcome", "artifact_inspection", ""):
        with pytest.raises(ver.VerificationError):
            ver.local_check_state(method, "pass")
    with pytest.raises(ver.VerificationError):
        ver.local_check_state("deterministic_check", "maybe")
    assert set(ver.LOCAL_CHECK_STATES) == {"checked", "failed_verification"}, "no stored state reaches verified"


def test_a_reported_pass_is_checked_in_the_insert_and_verified_only_comes_from_corroboration():
    pool = FakePool(insert_result={"id": uuid.uuid4(), "state": "checked"})
    out = run(ver.record_local_check(pool, payload=good(), viewer_id="user-1", tenant_scope=TENANT))
    insert = next(c for c in pool.conn.calls if c[0] == "fetchrow" and "INSERT INTO local_check_evidence" in c[1])
    assert "checked" in insert[2], "state computed by the service"
    assert out == {"recorded": True, "duplicate": False, "id": out["id"], "state": "checked"}
    sql = MIGRATION.read_text(encoding="utf-8")
    view = sql[sql.index("CREATE OR REPLACE VIEW local_check_effective"):]
    assert "passing_groups >= 2 THEN 'verified'" in view, "verified needs two independent groups"
    assert "'verified'" not in sql[:sql.index("CREATE OR REPLACE VIEW local_check_effective")].split("local_check_evidence_state_chk")[1].split(";")[0]


# -------------------------------------------------------------------------------------------------- the whitelist

@pytest.mark.parametrize("field,value", [
    ("command", "npm test"), ("path", "src/app.js"), ("output", "ok"), ("review_text", "looks right"), ("diff", "+x"),
    ("state", "verified"), ("tenant_id", str(uuid.uuid4())), ("owner_id", "someone-else"), ("repo_url", "https://github.com/a/b"),
    ("prompt", "fix the bug"), ("provenance", "company_ingested"), ("visibility", "public"),
])
def test_a_field_outside_the_whitelist_is_refused_not_ignored(field, value):
    with pytest.raises(ver.VerificationError, match=f"fields not allowed.*{field}"):
        ver.validate_local_check(good(**{field: value}))
    pool = FakePool()
    with pytest.raises(ver.VerificationError):
        run(ver.record_local_check(pool, payload=good(**{field: value}), viewer_id="user-1", tenant_scope=TENANT))
    assert pool.conn.calls == [], "nothing touched the database"


@pytest.mark.parametrize("over,needle", [
    ({"repo_ref": "my-private-repo"}, "repo_ref"), ({"target_id": "fix the login bug"}, "target_id"),
    ({"target_kind": "claim"}, "target_kind"), ({"check_hash": "abc"}, "check_hash"), ({"method": "self_report"}, "method"),
    ({"result": "maybe"}, "result"), ({"client_event_id": "not-a-uuid"}, "client_event_id"),
    ({"observed_at": "yesterday"}, "observed_at"), ({"commit_sha": "main"}, "commit_sha"), ({"where_ran": "cloud"}, "where_ran"),
    ({"hashes": [{"path": "src/app.js", "sha": "abcd"}]}, "hashes"),
    ({"hashes": [{"path_hmac": "b" * 64, "sha": "abcd", "extra": 1}]}, "hashes"),
    ({"hashes": [{"path_hmac": "short", "sha": "abcd"}]}, "hashes"), ({"hashes": "none"}, "hashes"),
    ({"goal_id": "goal-1"}, "goal_id"), ({"client_version": "1.0 (private build)"}, "client_version"),
])
def test_malformed_values_are_refused(over, needle):
    with pytest.raises(ver.VerificationError, match=needle):
        ver.validate_local_check(good(**over))


def test_required_fields_and_the_future_are_refused():
    for key in ver.LOCAL_CHECK_REQUIRED:
        bad = good()
        del bad[key]
        with pytest.raises(ver.VerificationError, match="missing"):
            ver.validate_local_check(bad)
    future = (datetime.now(timezone.utc) + timedelta(days=3)).isoformat()
    with pytest.raises(ver.VerificationError, match="future"):
        ver.validate_local_check(good(observed_at=future))
    with pytest.raises(ver.VerificationError):
        ver.validate_local_check("not an object")


def test_the_clients_whitelist_equals_the_servers():
    text = CLIENT_WHITELIST.read_text(encoding="utf-8")
    block = re.search(r"CHECK_EVIDENCE_FIELDS\s*=\s*\[(.*?)\];", text, re.S).group(1)
    client = tuple(re.findall(r'"([a-z_]+)"', block))
    assert client == ver.LOCAL_CHECK_FIELDS, "packaging/npm/lib/evidence_upload.mjs and verification.py must change together"


# -------------------------------------------------------------------------------------------------- the reviewer is not the executor

def test_a_reviewer_that_is_the_executor_is_refused():
    review = good(method="independent_agent", reviewer_model="glm-5.3-flash", executor_model="glm-5.3-flash")
    with pytest.raises(ver.VerificationError, match="same model"):
        ver.validate_local_check(review)
    with pytest.raises(ver.VerificationError, match="same model"):
        ver.validate_local_check(good(method="independent_agent", reviewer_model=" GLM-5.3-Flash ", executor_model="glm-5.3-flash"))
    pool = FakePool()
    with pytest.raises(ver.VerificationError, match="same model"):
        run(ver.record_local_check(pool, payload=review, viewer_id="user-1", tenant_scope=TENANT))
    assert pool.conn.calls == []
    ok = ver.validate_local_check(good(method="independent_agent", reviewer_model="claude-sonnet-5-5", executor_model="glm-5.3-flash"))
    assert ok["reviewer_model"] == "claude-sonnet-5-5" and ok["executor_model"] == "glm-5.3-flash"
    with pytest.raises(ver.VerificationError, match="needs reviewer_model and executor_model"):
        ver.validate_local_check(good(method="independent_agent"))
    with pytest.raises(ver.VerificationError, match="belong to independent_agent"):
        ver.validate_local_check(good(reviewer_model="claude-sonnet-5-5"))


# -------------------------------------------------------------------------------------------------- recording

def test_recording_binds_the_tenant_and_the_owner_first_and_carries_scope_and_provenance():
    pool = FakePool(insert_result={"id": uuid.uuid4(), "state": "checked"})
    run(ver.record_local_check(pool, payload=good(), viewer_id="user-1", tenant_scope=TENANT))
    kinds = [(c[0], c[1][:40]) for c in pool.conn.calls]
    settings = [c for c in pool.conn.calls if c[0] == "execute"]
    assert [a[2][0] for a in settings] == ["app.tenant_id", "app.owner_subject"], "tenant, then owner, before any insert"
    assert settings[0][2][1] == str(TENANT.tenant_id) and settings[1][2][1] == "user-1"
    assert kinds.index(("fetchrow", pool.conn.calls[2][1][:40])) > 1
    insert = next(c for c in pool.conn.calls if c[0] == "fetchrow" and "INSERT INTO local_check_evidence" in c[1])
    args = insert[2]
    assert "ON CONFLICT (owner_id, client_event_id) DO NOTHING" in insert[1]
    assert "repository" in args and "r:0123456789abcdef" in args, "V0 scope: the repository"
    assert "company_ingested" in args, "V0 provenance"
    assert "user-1" in args and "account:user-1" in args
    assert args[-1] == "r:0123456789abcdef" and args[-2] == "repository"
    assert [h["path_hmac"] for h in args[args.index("account:user-1") + 4]] == ["b" * 64], "hashes go in as a list for the jsonb codec"


def test_an_unknown_goal_id_is_refused_before_the_insert():
    pool = FakePool(insert_result={"id": uuid.uuid4(), "state": "checked"})
    pool.conn.goal_exists = False
    gid = "33333333-3333-3333-3333-333333333333"
    with pytest.raises(ver.VerificationError, match="goal .* was not found"):
        run(ver.record_local_check(pool, payload=good(goal_id=gid), viewer_id="user-1", tenant_scope=TENANT))
    assert not any("INSERT INTO local_check_evidence" in c[1] for c in pool.conn.calls)
    pool.conn.goal_exists = True
    out = run(ver.record_local_check(pool, payload=good(goal_id=gid), viewer_id="user-1", tenant_scope=TENANT))
    assert out["recorded"] is True


def test_the_same_event_twice_is_one_row():
    pool = FakePool(insert_result=None)                 # ON CONFLICT DO NOTHING: nothing returned
    out = run(ver.record_local_check(pool, payload=good(), viewer_id="user-1", tenant_scope=TENANT))
    assert out["recorded"] is False and out["duplicate"] is True and out["id"] == "22222222-2222-2222-2222-222222222222"
    assert any("WHERE owner_id = $1 AND client_event_id" in c[1] for c in pool.conn.calls), "the existing row is looked up for the reply"
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "CREATE UNIQUE INDEX IF NOT EXISTS idx_local_check_evidence_event" in sql and "(owner_id, client_event_id)" in sql


def test_an_anonymous_viewer_cannot_record():
    with pytest.raises(ver.VerificationError, match="signed-in"):
        run(ver.record_local_check(FakePool(), payload=good(), viewer_id="", tenant_scope=TENANT))


def test_the_effective_state_reads_under_the_system_scope_with_the_tenant_as_a_filter():
    pool = FakePool()
    run(ver.local_check_effective(pool, tenant_scope=TENANT, repo_ref="r:0123456789abcdef", target_id="N-3"))
    first = pool.conn.calls[0]
    assert first[0] == "execute" and first[2][0] == "app.rls_system"
    query = next(c for c in pool.conn.calls if c[0] == "fetch")
    assert "FROM local_check_effective WHERE tenant_id = $1::uuid AND repo_ref = $2 AND target_id = $3" in query[1]
    assert str(query[2][0]) == str(TENANT.tenant_id) and query[2][1:] == ("r:0123456789abcdef", "N-3")
    assert "SELECT check_hash, passing_groups, failing_groups, effective_state, last_observed_at" in query[1], "counts only"


# -------------------------------------------------------------------------------------------------- the MCP tool

@pytest.fixture()
def mcp(monkeypatch):
    import app.mcp_server.server as srv

    return srv


def ctx_for(pool):
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": pool}))


class UntouchablePool:
    def __getattr__(self, name):
        raise AssertionError(f"the pool must not be touched ({name})")


def test_the_tool_needs_a_signed_in_user(mcp, monkeypatch):
    monkeypatch.setattr(mcp, "_caller_access_scope", lambda: AccessScope.anonymous())
    out = run(mcp.report_check_evidence(ctx_for(UntouchablePool()), good()))
    assert out.startswith("REFUSED: sign in")


def test_the_tool_refuses_extra_fields_before_any_rate_limit_or_query(mcp, monkeypatch):
    monkeypatch.setattr(mcp, "_caller_access_scope", lambda: AccessScope.for_user("user-1"))
    out = run(mcp.report_check_evidence(ctx_for(UntouchablePool()), good(command="npm test")))
    assert out.startswith("REFUSED: fields not allowed") and "command" in out


def test_the_tool_goes_through_the_three_abuse_buckets_then_records(mcp, monkeypatch):
    from app.services import governance

    seen = []

    async def check_and_record(self, key, path):
        seen.append((key, path))

    async def tenant_scope_for_actor(pool, actor):
        return "internal-id", TENANT

    recorded = []

    async def record_local_check(pool, *, payload, viewer_id, tenant_scope):
        recorded.append((viewer_id, tenant_scope))
        return {"recorded": True, "duplicate": False, "id": "x", "state": "checked"}

    monkeypatch.setattr(mcp, "_caller_access_scope", lambda: AccessScope.for_user("user-1"))
    monkeypatch.setattr(governance.RateLimiter, "check_and_record", check_and_record)
    import app.services.authn as authn

    monkeypatch.setattr(authn, "tenant_scope_for_actor", tenant_scope_for_actor)
    monkeypatch.setattr(ver, "record_local_check", record_local_check)
    out = json.loads(run(mcp.report_check_evidence(ctx_for(object()), good())))
    assert out["state"] == "checked"
    assert [s[0] for s in seen] == ["user:user-1", "user:user-1", "global:report_check_evidence"]
    assert [s[1] for s in seen] == ["mcp/report_check_evidence", "mcp/report_check_evidence/day", "mcp/report_check_evidence/all"]
    assert recorded == [("user-1", TENANT)]


def test_the_tool_errors_on_the_rate_limit_and_refuses_when_the_tenant_is_ambiguous(mcp, monkeypatch):
    from app.services import governance
    import app.services.authn as authn

    monkeypatch.setattr(mcp, "_caller_access_scope", lambda: AccessScope.for_user("user-1"))

    async def over(self, key, path):
        raise governance.RateLimitExceeded("slow down", retry_after_seconds=7) if "retry_after_seconds" in inspect.signature(
            governance.RateLimitExceeded.__init__).parameters else governance.RateLimitExceeded("slow down")

    monkeypatch.setattr(governance.RateLimiter, "check_and_record", over)
    # an error, not a REFUSED reply: the client queues an error for later and treats REFUSED as final
    with pytest.raises(RuntimeError, match="rate limit"):
        run(mcp.report_check_evidence(ctx_for(object()), good()))

    async def ok(self, key, path):
        return None

    async def ambiguous(pool, actor):
        raise authn.AmbiguousTenant("active memberships in multiple organizations: a(1), b(2)")

    monkeypatch.setattr(governance.RateLimiter, "check_and_record", ok)
    monkeypatch.setattr(authn, "tenant_scope_for_actor", ambiguous)
    out = run(mcp.report_check_evidence(ctx_for(object()), good()))
    assert out.startswith("REFUSED:") and "multiple organizations" in out


def test_the_tool_is_on_the_v1_surface_classified_and_annotated(mcp):
    assert "report_check_evidence" in mcp.V1_TOOLS and "report_check_evidence" not in mcp.CORE_TOOLS
    assert mcp._TOOL_SCOPES["report_check_evidence"] == mcp._WRITE
    assert mcp._V1_ANNOTATIONS["report_check_evidence"]["idempotent_hint"] is True
    params = inspect.signature(mcp.report_check_evidence).parameters
    assert list(params) == ["ctx", "evidence"], "one object argument: the service decides what it may hold"


# -------------------------------------------------------------------------------------------------- the migration, statically

def test_the_migration_keeps_the_table_append_only_with_strict_rls_and_no_free_text_column():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert [p.name for p in (ROOT / "backend" / "db").glob("152_*.sql")] == ["152_local_check_evidence.sql"]
    table = sql[sql.index("CREATE TABLE IF NOT EXISTS local_check_evidence"):sql.index("CREATE OR REPLACE FUNCTION sl_local_check_hashes_ok")]
    columns = set(re.findall(r"^\s{4}([a-z_]+)\s+(?:UUID|TEXT|JSONB|TIMESTAMPTZ|visibility_level)", table, re.M))
    assert {"owner_id", "tenant_id", "check_hash", "hashes", "provenance", "scope_type", "scope_entity_id", "t_invalid"} <= columns
    forbidden = {"command", "cmd", "check", "path", "output", "review_text", "diff", "prompt", "repo_url", "repo_name", "statement", "detail"}
    assert not (columns & forbidden), f"a free-text column crept in: {sorted(columns & forbidden)}"
    assert "owner_id            TEXT NOT NULL" in table
    # append-only, as evidence (migration 24)
    assert "BEFORE UPDATE OR DELETE ON local_check_evidence" in sql and "DELETE rejected" in sql
    assert "only the t_invalid retraction tombstone may change" in sql
    for col in columns:
        assert f"NEW.{col}" in sql or col == "t_invalid", f"{col} is missing from the tombstone trigger's comparison"
    # strict RLS with no fallback
    assert "ENABLE ROW LEVEL SECURITY" in sql and "FORCE ROW LEVEL SECURITY" in sql
    assert "sl_owner_scope_allows(owner_id) AND sl_tenant_scope_allows(tenant_id)" in sql
    assert sql.count("security_invoker = true") == 2
    # the states the service can produce, and the independence of the reviewer, are in the engine too
    assert "(result = 'pass' AND state = 'checked') OR (result = 'fail' AND state = 'failed_verification')" in sql
    assert "lower(btrim(reviewer_model)) <> lower(btrim(executor_model))" in sql
    # fresh start: no data writes
    assert not re.search(r"^\s*(INSERT|UPDATE|DELETE)\b", sql, re.M), "a migration here adds structure only"
