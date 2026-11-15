"""A running Way's steps look up only when they need to (app/execution/way_lookups.py), on a real database with JEV,
find_ways and kell's model faked: hints judged once per Way version and cached, a hinted step replaced by the Way
find_ways chose, know-how when that Way cannot be spliced, a failed step rescued once, the session's one lookup,
kell's model saying what it needs, and a goal-only step resolved."""
from __future__ import annotations

import json
import os
from types import SimpleNamespace

import pytest

from app.execution import way_lookups, way_runs
from tests.test_way_runs_e2e import OWNER, fakes, pool, prompt, way  # noqa: F401 -- shared fixtures

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database integration test")

SUBTASK = "Set up a Postgres database for the app"


class Judge:
    """JEV: a step mentioning Postgres is a reusable task narrower than its Way; everything else is trivial."""
    providers = ["jev"]
    chain_id = "fake-jev"

    def __init__(self, relation="specializes", down=False):
        self.relation, self.down, self.calls = relation, down, 0

    async def judge_triage(self, text, ctx=""):
        self.calls += 1
        if self.down:
            return SimpleNamespace(ok=False, value=None)
        kind = "reusable_task" if "Postgres" in text else "trivial_edit"
        return SimpleNamespace(ok=True, value={"kind": kind, "confidence": 0.9})

    async def judge_identity(self, kind, a, b):
        self.calls += 1
        rel = "same" if a == b else self.relation
        return SimpleNamespace(ok=True, value={"relation": rel, "confidence": 0.9}, provider="jev", model="m")


@pytest.fixture
def judge(monkeypatch):
    j = Judge()
    monkeypatch.setattr(way_lookups, "_judge", lambda: j)
    return j


def finder_for(reply: dict | None, seen: list):
    async def finder(query):
        seen.append(query)
        return json.dumps(reply if reply is not None else {"outcome": "no_match", "candidates": []})
    return finder


def resolved(w: dict) -> dict:
    return {"outcome": "resolved", "procedures": [{"procedure_id": w["procedure_id"], "name": "pg setup",
                                                   "what_it_does": "Creates the database",
                                                   "steps": [{"do": "createdb app"}, {"do": "run migrations"}]}]}


async def main_way(pool, *steps):
    return await way(pool, list(steps) or [prompt(1, SUBTASK), prompt(2, "Summarise what was done")])


# ------------------------------------------------------------------------------------------------------- hints

@pytest.mark.asyncio
async def test_hints_are_judged_once_per_way_version_and_only_for_reusable_subtasks(pool, judge):
    w = await main_way(pool)
    plan = await way_runs.expand(pool, await way_runs._way(pool, w["id"], OWNER), OWNER)
    got = await way_lookups.hints(pool, w["id"], plan)
    assert got["1"]["lookup"] is True and got["2"] == {"lookup": False, "kind": "trivial_edit", "confidence": 0.9,
                                                       "why": "not a reusable task"}
    calls = judge.calls
    assert await way_lookups.hints(pool, w["id"], plan) == got and judge.calls == calls      # the cache answers


@pytest.mark.asyncio
async def test_a_step_that_is_the_ways_own_goal_gets_no_hint(pool, judge):
    judge.relation = "same"
    w = await main_way(pool)
    plan = await way_runs.expand(pool, await way_runs._way(pool, w["id"], OWNER), OWNER)
    assert (await way_lookups.hints(pool, w["id"], plan))["1"]["lookup"] is False


@pytest.mark.asyncio
async def test_jev_down_means_no_hints_and_nothing_cached(pool, judge):
    judge.down = True
    w = await main_way(pool)
    plan = await way_runs.expand(pool, await way_runs._way(pool, w["id"], OWNER), OWNER)
    assert await way_lookups.hints(pool, w["id"], plan) == {}
    assert await pool.fetchval("SELECT count(*) FROM way_lookup_hints WHERE procedure_row_id = $1::uuid", w["id"]) == 0


@pytest.mark.asyncio
async def test_script_tool_and_human_steps_are_never_judged(pool, judge, fakes):
    w = await way(pool, [{"order": 1, "title": "Count lines (Postgres)", "actor": "ai",
                          "binding": {"kind": "command", "command": "wc -l"}},
                         {"order": 2, "title": "Approve the Postgres plan", "actor": "human"}])
    run = await way_runs.start(pool, w["id"], owner_id=OWNER, finder=finder_for(None, []))
    assert judge.calls == 0 and run["status"] == "waiting_human"


# ---------------------------------------------------------------------------------------------------- the run

@pytest.mark.asyncio
async def test_a_hinted_step_is_replaced_by_the_way_find_ways_chose(pool, judge):
    pg = await way(pool, [prompt(1, "createdb app"), prompt(2, "run the migrations")])
    w = await main_way(pool)
    seen: list = []
    run = await way_runs.start(pool, w["id"], owner_id=OWNER, input="ship it", finder=finder_for(resolved(pg), seen))
    assert len(seen) == 1 and SUBTASK in seen[0] and "ship it" in seen[0]
    assert [s["title"] for s in run["steps"]] == ["createdb app", "run the migrations", "Summarise what was done"]
    assert run["steps"][0]["looked_up"] == "spliced" and run["your_turn"]["title"] == "createdb app"
    assert "stuck" not in run["your_turn"]                      # a step from a lookup never looks up again
    stored = await way_runs._load(pool, run["run_id"], OWNER)
    assert [s["path"] for s in stored["plan"]] == ["1.1", "1.2", "2"]
    run = await way_runs.report(pool, run["run_id"], owner_id=OWNER, output="db made", finder=finder_for(None, seen))
    assert run["your_turn"]["title"] == "run the migrations" and len(seen) == 1


@pytest.mark.asyncio
async def test_finding_the_same_way_again_gives_know_how_not_a_loop(pool, judge):
    w = await main_way(pool)
    run = await way_runs.start(pool, w["id"], owner_id=OWNER, finder=finder_for(resolved(w), []))
    assert [s["title"] for s in run["steps"]] == [SUBTASK, "Summarise what was done"]
    assert "createdb app" in run["your_turn"]["know_how"] and run["steps"][0]["looked_up"] == "know_how"


@pytest.mark.asyncio
async def test_a_failed_step_is_rescued_once_then_the_run_fails(pool, judge):
    pg = await way(pool, [prompt(1, "createdb app")])
    w = await main_way(pool, prompt(1, "Write the release notes"), prompt(2, "Summarise"))
    seen: list = []
    run = await way_runs.start(pool, w["id"], owner_id=OWNER, finder=finder_for(resolved(pg), seen))
    assert seen == []                                            # no hint: nothing looked up up front
    run = await way_runs.report(pool, run["run_id"], owner_id=OWNER, failed="no changelog",
                                finder=finder_for(resolved(pg), seen))
    assert run["status"] == "waiting_session" and run["your_turn"]["title"] == "createdb app" and len(seen) == 1
    run = await way_runs.report(pool, run["run_id"], owner_id=OWNER, failed="createdb missing",
                                finder=finder_for(resolved(pg), seen))
    assert run["status"] == "failed" and len(seen) == 1


@pytest.mark.asyncio
async def test_the_session_asks_once_per_step(pool, judge):
    w = await main_way(pool, prompt(1, "Write the release notes"), prompt(2, "Summarise"))
    seen: list = []
    know = {"outcome": "ambiguous", "candidates": [], "suggested": {"goal": "Release notes from git log",
                                                                    "verified_solution": {"summary": "git log --oneline"}}}
    run = await way_runs.start(pool, w["id"], owner_id=OWNER, finder=finder_for(know, seen))
    assert "lookup=" in run["your_turn"]["stuck"]
    run = await way_runs.report(pool, run["run_id"], owner_id=OWNER, lookup="how to build notes from commits",
                                finder=finder_for(know, seen))
    assert "git log --oneline" in run["your_turn"]["know_how"] and "stuck" not in run["your_turn"]
    assert seen[0].startswith("how to build notes from commits")
    with pytest.raises(way_runs.RunError, match="already had its lookup"):
        await way_runs.report(pool, run["run_id"], owner_id=OWNER, lookup="again", finder=finder_for(know, seen))


@pytest.mark.asyncio
async def test_asking_for_the_ways_own_goal_is_refused_without_a_search(pool, judge):
    w = await main_way(pool, prompt(1, "Write the release notes"))
    goal = (await way_runs._way(pool, w["id"], OWNER))["goal"]
    seen: list = []
    run = await way_runs.start(pool, w["id"], owner_id=OWNER, finder=finder_for(None, seen))
    with pytest.raises(way_runs.RunError, match="already does"):
        await way_runs.report(pool, run["run_id"], owner_id=OWNER, lookup=goal, finder=finder_for(None, seen))
    assert seen == []


@pytest.mark.asyncio
async def test_kells_model_says_what_it_needs_and_gets_it(pool, judge, fakes, monkeypatch):
    from app.submissions import runs

    asked: list = []

    async def model(system, user, **kw):
        asked.append(user)
        return "NEEDS: the release-notes format" if len(asked) == 1 else "NOTES DONE"
    monkeypatch.setattr(runs, "_model", model)
    know = {"outcome": "ambiguous", "candidates": [], "suggested": {"goal": "Keep a Changelog format"}}
    w = await main_way(pool, prompt(1, "Write the release notes"))
    seen: list = []
    run = await way_runs.start(pool, w["id"], owner_id=OWNER, mode="headless", finder=finder_for(know, seen))
    assert run["status"] == "done" and run["result"] == "NOTES DONE"
    assert way_lookups.NEEDS_PREFIX in asked[0] and "Keep a Changelog" in asked[1]
    assert seen[0].startswith("the release-notes format")


@pytest.mark.asyncio
async def test_a_goal_only_step_is_resolved_when_reached(pool, judge):
    pg = await way(pool, [prompt(1, "createdb app")])
    w = await way(pool, [{"order": 1, "title": "Have a database", "actor": "ai", "kind": "subgoal", "subgoal_id": "g"}])
    run = await way_runs.start(pool, w["id"], owner_id=OWNER, finder=finder_for(resolved(pg), []))
    assert [s["title"] for s in run["steps"]] == ["createdb app"]


@pytest.mark.asyncio
async def test_without_a_finder_nothing_is_judged_or_looked_up(pool, judge):
    w = await main_way(pool)
    run = await way_runs.start(pool, w["id"], owner_id=OWNER)
    assert judge.calls == 0 and run["your_turn"]["title"] == SUBTASK and "hint" not in run["steps"][0]
    with pytest.raises(way_runs.RunError, match="cannot look anything up"):
        await way_runs.report(pool, run["run_id"], owner_id=OWNER, lookup="how")
