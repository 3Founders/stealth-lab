"""A Goal's contract as linked claims and its computed status (migration 153, services/goal_claims.py, the goal resource).

Offline: the migration's shape, the writer's refusals, the status sentence, and the read path's format. The database-backed behaviour
(states, expiry, isolation) is in test_goal_claims_e2e.py (a scratch Postgres only).
"""
from __future__ import annotations

import asyncio
import re
import uuid
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services import goal_claims as gc
from app.services.access import AccessScope

MIGRATION = Path(__file__).resolve().parents[1] / "db" / "153_goal_claims.sql"
GOAL, CLAIM = "11111111-1111-1111-1111-111111111111", "22222222-2222-2222-2222-222222222222"


def run(coro):
    return asyncio.run(coro)


def test_the_migration_has_no_stored_status_and_does_not_touch_goals_status():
    sql = MIGRATION.read_text(encoding="utf-8")
    table = sql[sql.index("CREATE TABLE IF NOT EXISTS goal_claims"):sql.index(");", sql.index("CREATE TABLE IF NOT EXISTS goal_claims"))]
    columns = set(re.findall(r"^\s{4}([a-z_]+)\s+(?:UUID|TEXT|TIMESTAMPTZ|visibility_level)", table, re.M))
    assert {"goal_id", "claim_id", "role", "provenance", "visibility", "owner_id", "scope_type", "scope_entity_id",
            "t_valid", "t_invalid", "t_created", "t_expired"} <= columns
    assert not ({"status", "state", "verified"} & columns), "the status is computed, never a column"
    assert not re.search(r"ALTER\s+TABLE\s+goals\b", sql, re.I), "goals.status keeps its meaning: goals is not altered"
    assert "REFERENCES goals(id)" in sql and "REFERENCES knowledge_nodes(id)" in sql
    assert "CREATE OR REPLACE VIEW goal_status" in sql and sql.count("security_invoker = true") == 2
    assert not re.search(r"^\s*(INSERT|UPDATE|DELETE)\b", sql, re.M), "structure only: no data writes, no backfill"


def test_the_gate_and_the_tombstone_rule_ship_with_the_writer():
    sql = MIGRATION.read_text(encoding="utf-8")
    assert "BEFORE INSERT ON goal_claims" in sql and "is not a claim" in sql, "the claim-only gate"
    assert "BEFORE UPDATE OR DELETE ON goal_claims" in sql and "DELETE rejected" in sql, "invalidate-and-append"
    for col in ("goal_id", "claim_id", "role", "provenance", "created_by", "visibility", "owner_id", "scope_type", "scope_entity_id", "t_valid", "t_created", "t_expired"):
        assert f"NEW.{col}" in sql, f"{col} is missing from the tombstone trigger's comparison"
    assert "sl_routing_allows(visibility::text, owner_id)" in sql and "FORCE ROW LEVEL SECURITY" in sql
    assert hasattr(gc, "link_goal_claim") and hasattr(gc, "unlink_goal_claim"), "the writer"


def test_only_machine_checked_evidence_counts_and_only_guarantees_gate():
    sql = MIGRATION.read_text(encoding="utf-8")
    view = sql[sql.index("CREATE OR REPLACE VIEW goal_claim_state"):sql.index("CREATE OR REPLACE VIEW goal_status")]
    assert view.count("e.evidence_type IN ('execution_result', 'reproduction')") == 2
    for weaker in ("observation", "document", "human_review", "artifact", "external_source"):
        assert weaker not in view, f"{weaker} must not make a claim verified"
    assert "DISTINCT COALESCE(e.independence_group, e.id::text)" in view, "independent groups, as procedure_evidence_stats"
    status = sql[sql.index("CREATE OR REPLACE VIEW goal_status"):]
    assert "s.role = 'guarantee' AND NOT s.claim_closed" in status
    order = [status.index(f"THEN '{w}'") for w in ("expired", "failed", "contested", "verified")]
    assert order == sorted(order), "expired, then failed, then contested, then verified"
    assert "ELSE 'open'" in status


@pytest.mark.parametrize("row,needle", [
    ({"status": "expired", "guarantees": 2}, "superseded after the evidence"),
    ({"status": "failed", "guarantees": 2, "failed_claims": 1}, "1 of 2 guarantee claim(s) failed"),
    ({"status": "contested", "guarantees": 1, "contested_claims": 1}, "both passing and failing"),
    ({"status": "verified", "guarantees": 3}, "all 3 guarantee claim(s) have passing evidence"),
    ({"status": "open", "guarantees": 0}, "contract is empty"),
    ({"status": "open", "guarantees": 2, "verified_claims": 1}, "1 of 2 guarantee claim(s) verified"),
])
def test_the_status_sentence(row, needle):
    assert needle in gc.status_reason(row)


class FakeConn:
    def __init__(self):
        self.calls = []

    @asynccontextmanager
    async def transaction(self):
        yield

    async def execute(self, sql, *args):
        self.calls.append(("execute", sql, args))

    async def fetchrow(self, sql, *args):
        self.calls.append(("fetchrow", sql, args))
        return {"id": uuid.UUID("33333333-3333-3333-3333-333333333333")}

    async def fetch(self, sql, *args):
        return []


class FakePool(FakeConn):
    @asynccontextmanager
    async def acquire(self):
        yield self


def goals_module(goal):
    async def get_goal(pool, goal_id, scope=None):
        return goal
    return SimpleNamespace(get_goal=get_goal)


def test_the_writer_refuses_bad_requests_before_touching_the_database(monkeypatch):
    import app.services.goals as goals

    monkeypatch.setattr(goals, "get_goal", goals_module({"visibility": "public", "scope_type": "repository", "scope_entity_id": "r:0123456789abcdef"}).get_goal)
    pool = FakePool()
    user = AccessScope.for_user("user-a")
    with pytest.raises(gc.GoalClaimError, match="sign in"):
        run(gc.link_goal_claim(pool, goal_id=GOAL, claim_id=CLAIM, role="guarantee", scope=AccessScope.anonymous()))
    with pytest.raises(gc.GoalClaimError, match="goal_id must be a uuid"):
        run(gc.link_goal_claim(pool, goal_id="g1", claim_id=CLAIM, role="guarantee", scope=user))
    with pytest.raises(gc.GoalClaimError, match="role must be one of"):
        run(gc.link_goal_claim(pool, goal_id=GOAL, claim_id=CLAIM, role="verified", scope=user))
    with pytest.raises(gc.GoalClaimError, match="visibility"):
        run(gc.link_goal_claim(pool, goal_id=GOAL, claim_id=CLAIM, role="guarantee", scope=user, visibility="org"))
    monkeypatch.setattr(goals, "get_goal", goals_module(None).get_goal)
    with pytest.raises(gc.GoalClaimError, match="goal .* was not found"):
        run(gc.link_goal_claim(pool, goal_id=GOAL, claim_id=CLAIM, role="guarantee", scope=user))
    monkeypatch.setattr(goals, "get_goal", goals_module({"visibility": "private"}).get_goal)
    with pytest.raises(gc.GoalClaimError, match="public Goal"):
        run(gc.link_goal_claim(pool, goal_id=GOAL, claim_id=CLAIM, role="guarantee", scope=user, visibility="public"))
    assert pool.calls == [], "nothing reached the database"
    with pytest.raises(gc.GoalClaimError, match="sign in"):
        run(gc.unlink_goal_claim(pool, link_id=GOAL, scope=AccessScope.anonymous()))


def test_the_goal_resource_prints_the_status_line_then_the_contract_then_related(monkeypatch):
    import app.mcp_server.resources as res
    from app.services import goals as goals_mod
    import app.services.relevant_claims as rc

    status = {"goal_id": GOAL, "status": "verified", "why": "all 1 guarantee claim(s) have passing evidence and none fails",
              "counts": {"guarantees": 1, "verified_claims": 1, "failed_claims": 0, "contested_claims": 0, "open_claims": 0, "assumptions": 1, "closed_links": 0},
              "claims": [{"claim_id": CLAIM, "role": "guarantee", "state": "verified", "closed": False, "pass_groups": 1, "fail_groups": 0},
                         {"claim_id": "44444444-4444-4444-4444-444444444444", "role": "assumption", "state": "open", "closed": False, "pass_groups": 0, "fail_groups": 0}],
              "client_checks": {"pass": 2}}

    async def get_goal(pool, goal_id, scope=None):
        return {"canonical_name": "Rotate the signing keys"}

    async def goal_status(pool, goal_id, *, scope, tenant_scope=None):
        return status

    async def relevant(pool, goal, access_scope, top_k):
        return [{"claim_id": "55555555-5555-5555-5555-555555555555", "statement": "a related claim", "status": "current"}]

    class Pool:
        async def fetch(self, sql, *args):
            return [{"id": uuid.UUID(CLAIM), "properties": {"statement": "keys rotate every 90 days"}, "name": "k", "visibility": "public",
                     "scope_type": "repository", "claim_status": "supported", "t_created": None},
                    {"id": uuid.UUID("44444444-4444-4444-4444-444444444444"), "properties": {"statement": "the vault is reachable"}, "name": "v",
                     "visibility": "public", "scope_type": "repository", "claim_status": "supported", "t_created": None}]

    async def tenant(pool, scope):
        return None

    monkeypatch.setattr(goals_mod, "get_goal", get_goal)
    monkeypatch.setattr(gc, "goal_status", goal_status)
    monkeypatch.setattr(rc, "get_relevant_claims", relevant)
    monkeypatch.setattr(res, "_scope", lambda: AccessScope.for_user("user-a"))
    monkeypatch.setattr(res, "_tenant_scope_for", tenant)
    ctx = SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": Pool()}))
    text = run(res.goal_claims_resource(GOAL, ctx))
    lines = text.strip().splitlines()
    assert lines[0] == "# claims for goal 11111111-1111-1111-1111-111111111111 (Rotate the signing keys)"
    assert lines[1].startswith(f"GOAL_STATUS|{GOAL}|verified|guarantees=1|verified=1|failed=0|contested=0|open=0|assumptions=1|closed=0|client_checks=pass:2|why=")
    kinds = [(l.split("|")[0], l.split("|")[3]) for l in lines[2:]]
    assert kinds == [("CLAIM", "guarantee"), ("CLAIM", "assumption"), ("CLAIM", "related")]
    assert "keys rotate every 90 days" in lines[2] and "the vault is reachable" in lines[3] and "a related claim" in lines[4]
    assert "|" not in lines[1].split("why=", 1)[1], "the sentence cannot break the pipe grammar"
