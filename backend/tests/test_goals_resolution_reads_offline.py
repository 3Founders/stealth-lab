from __future__ import annotations

import asyncio
from datetime import datetime, timezone

from app.services import product_model as pm
from app.services.access import AccessScope


class _PagePool:
    """Pages are chosen on the goal projection (`sql`/`args`: covers every shard),
    then the page's canonical rows are read (`hydrate_sql`)."""

    def __init__(self, rows):
        self.rows = rows
        self.sql = ""
        self.args = ()
        self.hydrate_sql = ""

    async def fetch(self, sql, *args):
        compact = " ".join(sql.split())
        if "FROM goal_search_index" in compact:
            self.sql, self.args = compact, args
            return [{"goal_id": row["id"], "home_shard_id": "K000"} for row in self.rows]
        self.hydrate_sql = compact
        wanted = set(args[0])
        return [row for row in self.rows if row["id"] in wanted]


def _run(coro):
    return asyncio.run(coro)


def _goal(goal_id, *, resolved_at=None, embedding="[1,2,3]"):
    return {
        "id": goal_id,
        "canonical_name": "find references safely",
        "resolved_at": resolved_at,
        "embedding": embedding,
    }


def test_list_goals_filters_status_and_resolution_with_offset_page():
    resolved_at = datetime(2026, 9, 24, tzinfo=timezone.utc)
    pool = _PagePool([_goal("g1", resolved_at=resolved_at), _goal("g2")])

    goals, has_more = _run(pm.list_goals(
        pool,
        scope=AccessScope.unrestricted(),
        status="active",
        resolved=True,
        limit=1,
        offset=3,
    ))

    assert len(goals) == 1
    assert has_more is True
    assert goals[0]["resolved_at"] == "2026-09-24T00:00:00+00:00"
    assert "embedding" not in goals[0]
    assert "g.resolved_at IS NOT NULL" in pool.sql
    assert "g.status = $1" in pool.sql
    assert "SELECT g.id," in pool.hydrate_sql and "embedding" not in pool.hydrate_sql
    assert "SELECT g.*" not in pool.sql
    assert "embedding" not in pool.sql
    assert "LIMIT $2 OFFSET $3" in pool.sql
    assert pool.args == ("active", 2, 3)


def test_list_goals_resolved_false_selects_only_unresolved_goals():
    pool = _PagePool([_goal("g1")])

    goals, has_more = _run(pm.list_goals(
        pool, scope=AccessScope.unrestricted(), resolved=False, limit=5,
    ))

    assert goals[0]["id"] == "g1"
    assert has_more is False
    assert "g.resolved_at IS NULL" in pool.sql
    assert "g.status = " not in pool.sql
    assert pool.args == (6, 0)


def test_find_goal_defaults_to_all_resolution_states_and_safe_projection():
    pool = _PagePool([_goal("g1")])

    goals, has_more = _run(pm.find_goal(
        pool, "find references", scope=AccessScope.unrestricted(), limit=3, offset=2,
    ))

    assert goals[0]["id"] == "g1"
    assert has_more is False
    assert "embedding" not in goals[0]
    assert "g.resolved_at IS" not in pool.sql
    assert "SELECT g.id," in pool.hydrate_sql and "embedding" not in pool.hydrate_sql
    assert "SELECT g.*" not in pool.sql
    assert "embedding" not in pool.sql
    assert "LIMIT $2 OFFSET $3" in pool.sql
    assert pool.args == ("find | references", 4, 2)


def test_find_goal_supports_resolved_and_unresolved_filters():
    resolved_pool = _PagePool([_goal("g1"), _goal("g2")])
    goals, has_more = _run(pm.find_goal(
        resolved_pool, "find references", scope=AccessScope.unrestricted(),
        resolved="resolved", limit=1,
    ))
    assert goals[0]["id"] == "g1"
    assert has_more is True
    assert "g.resolved_at IS NOT NULL" in resolved_pool.sql
    assert "LIMIT $2 OFFSET $3" in resolved_pool.sql

    unresolved_pool = _PagePool([_goal("g2")])
    goals, has_more = _run(pm.find_goal(
        unresolved_pool, "find references", scope=AccessScope.unrestricted(),
        resolved="unresolved", limit=1,
    ))
    assert goals[0]["id"] == "g2"
    assert has_more is False
    assert "g.resolved_at IS NULL" in unresolved_pool.sql


def test_find_goal_rejects_unknown_resolution_filter():
    try:
        _run(pm.find_goal(
            _PagePool([]), "find references", scope=AccessScope.unrestricted(), resolved="maybe",
        ))
    except ValueError as exc:
        assert "resolved must be" in str(exc)
    else:
        raise AssertionError("unknown resolution filter must fail")
