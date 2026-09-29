"""Offline: goal_search_index.has_procedures (migration 128) -- only Goals with a live Procedure are agent candidates,
and a Goal enters the hierarchy when it first gets one."""
from __future__ import annotations

import asyncio

import pytest

from app.services import search_projection as sp


class _Conn:
    """Search + control database in one fake: procedure projections and goal flags."""

    def __init__(self, procedures=None, goals=None):
        self.procedures = dict(procedures or {})       # procedure_id -> (goal_id, status)
        self.goals = dict(goals or {})                  # goal_id -> {"has_procedures": bool, "bumped": int}
        self.sql = []

    async def fetchval(self, sql, *args):
        self.sql.append(sql)
        if "EXISTS (SELECT 1 FROM procedure_search_index" in sql:
            return any(g == args[0] and s == "active" for g, s in self.procedures.values())
        if "UPDATE goal_search_index SET has_procedures" in sql:
            goal_id, has = args
            if goal_id not in self.goals:
                return None
            row = self.goals[goal_id]
            if row["has_procedures"] != has:
                row["bumped"] += 1
            row["has_procedures"] = has
            return has
        if "SELECT goal_id::text FROM procedure_search_index" in sql:
            return (self.procedures.get(args[0]) or (None,))[0]
        raise AssertionError(sql)


@pytest.fixture(autouse=True)
def _one_database(monkeypatch):
    async def same(conn):
        return conn
    monkeypatch.setattr(sp, "search_pool", same)


def _goal(has=False):
    return {"has_procedures": has, "bumped": 0}


def test_first_active_procedure_flips_the_goal_and_bumps_it_for_placement():
    conn = _Conn(procedures={"p1": ("g1", "active")}, goals={"g1": _goal()})
    assert asyncio.run(sp.refresh_goal_has_procedures(conn, "g1")) is True
    assert conn.goals["g1"] == {"has_procedures": True, "bumped": 1}
    asyncio.run(sp.refresh_goal_has_procedures(conn, "g1"))
    assert conn.goals["g1"]["bumped"] == 1, "no flip, no bump: the placement sweep is not re-triggered"


def test_a_goal_whose_only_procedure_is_not_active_is_not_a_candidate():
    for status in ("quarantined", "disabled"):
        conn = _Conn(procedures={"p1": ("g1", status)}, goals={"g1": _goal(True)})
        assert asyncio.run(sp.refresh_goal_has_procedures(conn, "g1")) is False


def test_a_goal_without_a_projection_yet_is_left_to_its_own_projection():
    conn = _Conn(procedures={"p1": ("g1", "active")}, goals={})
    assert asyncio.run(sp.refresh_goal_has_procedures(conn, "g1")) is None


def test_goal_projection_computes_the_flag():
    conn = _Conn(procedures={"p1": ("g1", "active")})
    assert asyncio.run(sp.goal_has_live_procedure(conn, "g1")) is True
    assert asyncio.run(sp.goal_has_live_procedure(conn, "g2")) is False


def test_agent_goal_search_filters_and_identity_search_does_not():
    import inspect

    from app.services import retrieval_service as rs

    agent = inspect.getsource(rs.search_goals)
    assert "AND has_procedures" in agent
    candidates = inspect.getsource(rs.search_goal_candidates)
    assert "if require_procedures:" in candidates
    assert inspect.signature(rs.search_goal_candidates).parameters["require_procedures"].default is False


def test_agent_facing_callers_ask_for_procedures():
    import inspect

    from app.execution import intent_resolution
    from app.mcp_server import server

    assert "require_procedures=True" in inspect.getsource(intent_resolution)
    assert "require_procedures=True" in inspect.getsource(server.search_goals)


def test_only_person_created_goals_are_placed_at_creation():
    import inspect

    from app.services import goals

    src = inspect.getsource(goals.find_or_create_goal)
    assert 'if created_from != "user_created":' in src
    assert src.index('if created_from != "user_created":') < src.index("enqueue_goal_abstraction_placement(")


def test_placement_repair_only_considers_goals_with_procedures():
    import inspect

    from app.services import identity_resolution

    assert "g.has_procedures" in inspect.getsource(identity_resolution.enqueue_missing_goal_placements)
