"""Calling another person's agent (@anuj/kell) without a shared room, and an agent calling an agent: who may, how deep, who approves."""
import asyncio

import pytest

from app.services import delegate as dg
from app.services import rooms


def run(c):
    return asyncio.run(c)


class Pool:
    """Just enough of a pool for post_message / call_agent: one agent per (owner, name), a set of grants, a task log."""

    def __init__(self, agents, links, auto=()):
        self.agents, self.links, self.auto, self.tasks, self.parent_depth = agents, set(links), set(auto), [], 0

    async def fetchval(self, sql, *a):
        if "FROM agent_links" in sql:
            owner, caller, auto_only = a
            return 1 if (owner, caller) in self.links and (not auto_only or (owner, caller) in self.auto) else None
        if "FROM room_members" in sql:
            return None
        if "count(*)" in sql:
            return 0
        return None

    async def fetchrow(self, sql, *a):
        if "FROM delegate_agents" in sql:
            return self.agents.get((a[0], a[1]))
        if "SELECT depth FROM delegate_tasks" in sql:
            return {"depth": self.parent_depth}
        if "INSERT INTO delegate_tasks" in sql:
            self.tasks.append(a)
            from datetime import datetime, timezone
            now = datetime.now(timezone.utc)
            return {"id": a[0], "agent_id": a[2], "room": a[3], "from_handle": a[4], "body": a[5], "locators": "[]", "depth": a[9],
                    "status": a[6], "created_at": now, "claimed_at": None, "finished_at": None, "result_text": None,
                    "result_check": None, "room_id": None, "owner_sub": a[1]}
        return None

    async def execute(self, *a):
        return "DELETE 0"


@pytest.fixture
def pool(monkeypatch):
    async def sub_of(pool_, name):
        return {"anuj": "sub-anuj", "me": "sub-me"}.get(name)

    async def label(pool_, sub):
        return {"sub-anuj": "anuj", "sub-me": "me"}[sub]

    monkeypatch.setattr(rooms, "sub_of_username", sub_of)
    monkeypatch.setattr(rooms, "owner_label", label)
    agents = {("sub-anuj", "kell"): {"id": "a1", "owner_sub": "sub-anuj", "name": "kell", "last_seen": None, "revoked_at": None}}
    return Pool(agents, links=[("sub-anuj", "sub-me")])


def test_a_granted_person_can_call_without_a_shared_room_and_the_owner_must_approve(pool):
    task = run(dg.call_agent(pool, "sub-me", "@anuj/kell", "status?", "claude"))
    assert task["status"] == "needs_approval" and task["depth"] == 1 and task["from"] == "@me/claude"


def test_auto_approve_skips_the_button(pool):
    pool.auto.add(("sub-anuj", "sub-me"))
    assert run(dg.call_agent(pool, "sub-me", "@anuj/kell", "status?"))["status"] == "queued"


def test_without_a_grant_or_room_the_agent_is_unreachable(pool):
    pool.links.clear()
    with pytest.raises(dg.DelegateError) as e:
        run(dg.call_agent(pool, "sub-me", "@anuj/kell", "status?"))
    assert e.value.status == 404


def test_a_chain_of_calls_stops_at_the_depth_limit(pool):
    pool.parent_depth = dg.MAX_DEPTH
    with pytest.raises(dg.DelegateError) as e:
        run(dg.call_agent(pool, "sub-me", "@anuj/kell", "again", parent_task_id="00000000-0000-0000-0000-000000000001"))
    assert e.value.status == 409
    pool.parent_depth = 2
    assert run(dg.call_agent(pool, "sub-me", "@anuj/kell", "again", parent_task_id="00000000-0000-0000-0000-000000000001"))["depth"] == 3
