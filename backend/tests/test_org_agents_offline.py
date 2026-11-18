"""Organization agents, containment edits and the approvals inbox, offline.

These tests exercise the service rules before a database is touched: who may list, create, edit or revoke an agent,
which containment values are accepted, how status is derived, and how an approval is scoped and decided. The SQL is
captured so the arguments (never a secret) can be asserted. RLS itself is migration 214 and is not claimed here.
"""
from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

import pytest

from app.services import org_agents
from app.services import org_governance as gov

ORG = "0b0f6a0e-3a33-4c1c-9f3b-2f3f6f0a0001"
AGENT = "11111111-1111-1111-1111-111111111111"


def run(coro):
    return asyncio.run(coro)


class FakeConn:
    def __init__(self, answers):
        self.answers, self.log = answers, []

    def _find(self, sql):
        for needle, value in self.answers.items():
            if needle in sql:
                return value() if callable(value) else value
        return None

    async def execute(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql)

    async def fetch(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql) or []

    async def fetchrow(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql)

    async def fetchval(self, sql, *a):
        self.log.append((sql, a))
        return self._find(sql)

    def transaction(self):
        @asynccontextmanager
        async def tx():
            yield

        return tx()


class FakePool:
    def __init__(self, answers=None):
        self.conn = FakeConn(answers or {})

    @asynccontextmanager
    async def acquire(self):
        yield self.conn

    def statements(self, needle):
        return [(s, a) for s, a in self.conn.log if needle in s]


def _now():
    return datetime.now(timezone.utc)


def _agent_row(**over):
    row = {"id": AGENT, "name": "reviewer", "kind": "agent", "owner_sub": "sub-ada", "owner_name": "Ada",
           "owner_email": "ada@acme.co", "created_at": _now(), "last_seen": _now(), "revoked_at": None,
           "scopes": ["read"], "data_ceiling": "PUBLIC_SOURCE", "budget_usd_month": None, "expires_at": None}
    row.update(over)
    return row


# ------------------------------------------------------------------ role checks

@pytest.mark.parametrize("call", [
    lambda p: org_agents.list_agents(p, actor_roles={"member"}, org_id=ORG),
    lambda p: org_agents.create_agent(p, actor_subject="sub-ada", actor_user_id=None, actor_roles={"member"},
                                      org_id=ORG, name="reviewer"),
    lambda p: org_agents.update_agent(p, actor_subject="sub-ada", actor_user_id=None, actor_roles={"member"},
                                      org_id=ORG, agent_id=AGENT, scopes=["read"]),
    lambda p: org_agents.revoke_agent(p, actor_subject="sub-ada", actor_user_id=None, actor_roles={"member"},
                                      org_id=ORG, agent_id=AGENT),
    lambda p: org_agents.list_approvals(p, actor_roles={"member"}, org_id=ORG),
])
def test_members_cannot_manage_agents_or_approvals(call):
    with pytest.raises(gov.NotAuthorized):
        run(call(FakePool()))


# ------------------------------------------------------------------ listing and status

def test_status_is_one_word_and_offline_is_not_revoked():
    now = _now()
    assert org_agents._status(None, now - timedelta(seconds=5), None, now) == "online"
    assert org_agents._status(None, now - timedelta(minutes=5), None, now) == "offline"
    assert org_agents._status(now, now, None, now) == "revoked"
    assert org_agents._status(None, now, now - timedelta(seconds=1), now) == "expired"


def test_list_agents_maps_owner_status_and_containment():
    now = _now()
    pool = FakePool({"FROM delegate_agents a": [
        _agent_row(last_seen=now - timedelta(seconds=3)),
        _agent_row(id="22222222-2222-2222-2222-222222222222", name="expired", expires_at=now - timedelta(days=1)),
        _agent_row(id="33333333-3333-3333-3333-333333333333", name="gone", revoked_at=now),
    ]})
    rows = run(org_agents.list_agents(pool, actor_roles={"admin"}, org_id=ORG))
    assert [r["status"] for r in rows] == ["online", "expired", "revoked"]
    assert rows[0]["owner_name"] == "Ada" and rows[0]["scopes"] == ["read"]


# ------------------------------------------------------------------ creating

def test_create_agent_records_containment_and_returns_offline():
    now = _now()
    pool = FakePool({"SELECT count(*) FROM delegate_agents": 0,
                     "INSERT INTO delegate_agents": {"id": AGENT, "name": "reviewer", "kind": "agent",
                                                     "created_at": now},
                     "INSERT INTO audit_events": {"id": 1}})
    out = run(org_agents.create_agent(pool, actor_subject="sub-ada", actor_user_id="u1", actor_roles={"admin"},
                                      org_id=ORG, name="Reviewer", scopes=["read", "read", "call_model"],
                                      data_ceiling="USER_PRIVATE", budget_usd_month="20", expires_days=30))
    assert out["name"] == "reviewer" and out["status"] == "offline"
    assert out["scopes"] == ["read", "call_model"] and out["data_ceiling"] == "USER_PRIVATE"
    insert = pool.statements("INSERT INTO delegate_agents")[0][1]
    assert insert[1:6] == ("sub-ada", "reviewer", "agent", ["read", "call_model"], "USER_PRIVATE")
    assert str(insert[6]) == "20" and insert[7] == 30


@pytest.mark.parametrize("kw,needle", [
    ({"name": "-bad"}, "name must be"),
    ({"name": "ok", "data_ceiling": "EVERYTHING"}, "data ceiling"),
    ({"name": "ok", "budget_usd_month": "-1"}, "zero or more"),
    ({"name": "ok", "expires_days": 0}, "expires_days"),
    ({"name": "ok", "expires_days": 366}, "expires_days"),
])
def test_create_agent_refuses_bad_containment(kw, needle):
    with pytest.raises(gov.Invalid, match=needle):
        run(org_agents.create_agent(FakePool(), actor_subject="sub-ada", actor_user_id=None,
                                    actor_roles={"admin"}, org_id=ORG, **kw))


def test_create_agent_caps_live_agents_per_person():
    pool = FakePool({"SELECT count(*) FROM delegate_agents": 20})
    with pytest.raises(gov.Conflict, match="at most 20"):
        run(org_agents.create_agent(pool, actor_subject="sub-ada", actor_user_id=None, actor_roles={"admin"},
                                    org_id=ORG, name="reviewer"))


# ------------------------------------------------------------------ editing

def _update_pool(before=None, after=None):
    return FakePool({
        "a.owner_sub, a.name, a.scopes": before or {"id": AGENT, "owner_sub": "sub-ada", "name": "reviewer",
                                                    "scopes": ["read"], "data_ceiling": "PUBLIC_SOURCE",
                                                    "budget_usd_month": None, "expires_at": None},
        "a.kind, a.created_at": after or _agent_row(),
        "UPDATE delegate_agents": "UPDATE 1",
        "INSERT INTO audit_events": {"id": 1},
    })


def test_update_agent_changes_only_the_fields_sent_and_audits_before_and_after():
    pool = _update_pool(after=_agent_row(scopes=["read", "call_model"], data_ceiling="USER_PRIVATE",
                                         budget_usd_month=20, expires_at=_now() + timedelta(days=30)))
    out = run(org_agents.update_agent(pool, actor_subject="sub-ada", actor_user_id="u1", actor_roles={"admin"},
                                      org_id=ORG, agent_id=AGENT, scopes=["read", "call_model"],
                                      data_ceiling="USER_PRIVATE", budget_usd_month="20", expires_days=30))
    assert out["scopes"] == ["read", "call_model"] and out["data_ceiling"] == "USER_PRIVATE"
    sql, args = pool.statements("UPDATE delegate_agents")[0]
    assert "scopes = $3::text[]" in sql and "data_ceiling = $4" in sql
    assert "budget_usd_month = $5" in sql and "make_interval(days => $6::int)" in sql
    assert args[1] == AGENT and args[-1] == "sub-ada"
    audit = pool.statements("INSERT INTO audit_events")[0][1]
    assert "org_agent.update" in audit and "call_model" in str(audit)


def test_update_agent_can_clear_the_budget_and_expiry():
    pool = _update_pool(after=_agent_row(budget_usd_month=None, expires_at=None))
    run(org_agents.update_agent(pool, actor_subject="sub-ada", actor_user_id=None, actor_roles={"owner"},
                                org_id=ORG, agent_id=AGENT, budget_usd_month="", clear_expiry=True))
    sql = pool.statements("UPDATE delegate_agents")[0][0]
    assert "budget_usd_month = NULL" in sql and "expires_at = NULL" in sql


def test_update_agent_needs_something_to_change():
    with pytest.raises(gov.Invalid, match="nothing to change"):
        run(org_agents.update_agent(FakePool(), actor_subject="sub-ada", actor_user_id=None,
                                    actor_roles={"admin"}, org_id=ORG, agent_id=AGENT))


def test_update_agent_that_is_not_in_the_organization_is_not_found():
    pool = FakePool({"a.owner_sub, a.name, a.scopes": None})
    with pytest.raises(gov.NotFound):
        run(org_agents.update_agent(pool, actor_subject="sub-ada", actor_user_id=None, actor_roles={"admin"},
                                    org_id=ORG, agent_id=AGENT, scopes=["read"]))


# ------------------------------------------------------------------ revoking

def test_revoke_agent_cancels_open_tasks_and_audits():
    pool = FakePool({"a.owner_sub, a.name FROM delegate_agents": {"id": AGENT, "owner_sub": "sub-ada",
                                                                 "name": "reviewer"},
                     "UPDATE delegate_agents SET revoked_at": "UPDATE 1",
                     "UPDATE delegate_tasks": "UPDATE 2",
                     "INSERT INTO audit_events": {"id": 1}})
    out = run(org_agents.revoke_agent(pool, actor_subject="sub-ada", actor_user_id="u1", actor_roles={"admin"},
                                      org_id=ORG, agent_id=AGENT))
    assert out == {"id": AGENT, "revoked": True, "cancelled": 2}
    assert "status = 'cancelled'" in pool.statements("UPDATE delegate_tasks")[0][0]


def test_revoke_agent_outside_the_organization_is_not_found():
    with pytest.raises(gov.NotFound):
        run(org_agents.revoke_agent(FakePool({"a.owner_sub, a.name FROM delegate_agents": None}),
                                    actor_subject="sub-ada", actor_user_id=None, actor_roles={"admin"},
                                    org_id=ORG, agent_id=AGENT))


# ------------------------------------------------------------------ approvals

def test_approvals_reject_an_unknown_status():
    with pytest.raises(gov.Invalid, match="status must be one of"):
        run(org_agents.list_approvals(FakePool(), actor_roles={"admin"}, org_id=ORG, status="waiting"))


def test_approvals_list_the_members_rows_with_a_safe_hash():
    row = {"id": "ap_1", "owner_id": "sub-ada", "task_id": "t1", "goal_id": "g1", "server": "github",
           "tool": "create_issue", "args_hash": "sha256:abc", "risk": "R2", "data_class": "PUBLIC_SOURCE",
           "cert_id": "c1", "status": "pending", "requested_at": _now(), "decided_at": None,
           "expires_at": _now() + timedelta(minutes=10), "used_at": None, "decided_by": None, "note": None,
           "owner_name": "Ada", "owner_email": "ada@acme.co"}
    pool = FakePool({"FROM tool_approvals a": [row]})
    out = run(org_agents.list_approvals(pool, actor_roles={"admin"}, org_id=ORG, status="pending"))
    assert out[0]["tool"] == "create_issue" and out[0]["owner_name"] == "Ada"
    assert "status = $2" in pool.statements("FROM tool_approvals a")[0][0]


def _decision_pool(owner="sub-ada", result="UPDATE 1"):
    return FakePool({"SELECT a.id, a.owner_id, a.status": {"id": "ap_1", "owner_id": owner, "status": "pending"},
                     "UPDATE tool_approvals": result,
                     "INSERT INTO audit_events": {"id": 1}})


def test_an_admin_can_decide_a_members_approval():
    pool = _decision_pool()
    out = run(org_agents.decide_approval(pool, actor_subject="sub-owner", actor_user_id="u1",
                                         actor_roles={"owner"}, org_id=ORG, approval_id="ap_1", yes=True))
    assert out == {"id": "ap_1", "status": "approved"}
    assert "status = 'approved'" in pool.statements("UPDATE tool_approvals")[0][0]


def test_an_approval_that_is_no_longer_pending_is_a_conflict():
    with pytest.raises(gov.Conflict, match="no longer pending"):
        run(org_agents.decide_approval(_decision_pool(result="UPDATE 0"), actor_subject="sub-owner",
                                       actor_user_id=None, actor_roles={"admin"}, org_id=ORG,
                                       approval_id="ap_1", yes=False))


def test_an_approval_outside_the_organization_is_not_found():
    with pytest.raises(gov.NotFound):
        run(org_agents.decide_approval(FakePool({"SELECT a.id, a.owner_id, a.status": None}),
                                       actor_subject="sub-owner", actor_user_id=None, actor_roles={"admin"},
                                       org_id=ORG, approval_id="ap_1", yes=True))
