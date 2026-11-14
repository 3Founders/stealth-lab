"""Running a retrieved Way (app/execution/way_runs.py) on a real database, with the fetch, the sandbox and kell's model
faked: the load notice, the session's turns with earlier outputs fed forward, human and sign-in pauses, kell-run
scripts and fetches, sub-Ways expanded, refusals, failure, cancel and the headless mode."""
from __future__ import annotations

import json
import os
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.execution import way_runs
from app.submissions import runs

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database integration test")

OWNER = "test-way-runner"
MADE: list[str] = []   # the Ways these tests wrote, retired afterwards


@pytest_asyncio.fixture
async def pool():
    p = await create_pool(DATABASE_URL)
    try:
        if not await p.fetchval("SELECT to_regclass('way_runs') IS NOT NULL"):
            pytest.skip("db/206 is not applied")
        yield p
    finally:
        if MADE:
            await p.execute("UPDATE procedures SET t_invalid = now() WHERE id = ANY($1::uuid[])", MADE)
            MADE.clear()
        await p.close()


async def way(pool, steps: list[dict], *, owner: str = OWNER, visibility: str = "public",
              availability: str = "active") -> dict:
    row = await pool.fetchrow(
        """INSERT INTO procedures (name, goal, steps, owner_id, visibility, availability)
           VALUES ($1, $1, $2::jsonb, $3, $4::visibility_level, $5::procedure_availability)
           RETURNING id::text AS id, procedure_id::text AS procedure_id, version""",
        f"test way {uuid.uuid4().hex[:8]}", json.dumps(steps), owner, visibility, availability)
    MADE.append(row["id"])
    return dict(row)


def ref(w: dict) -> dict:
    return {"procedure_id": w["procedure_id"], "version": w["version"]}


@pytest.fixture
def fakes(monkeypatch):
    seen: dict[str, list] = {"stdin": [], "model": []}

    async def fetch_text(url, *, as_code=False, limit=None):
        return f"PAGE OF {url}"

    async def run_script(step, run):
        seen["stdin"].append(runs._context(run))
        return "LINES: 42"

    async def model(system, user, **kw):
        seen["model"].append(user)
        return f"KELL DID: {user.splitlines()[-1]}"

    monkeypatch.setattr(runs, "fetch_text", fetch_text)
    monkeypatch.setattr(runs, "_run_script", run_script)
    monkeypatch.setattr(runs, "_model", model)
    return seen


def prompt(order, text, **binding):
    return {"order": order, "title": text, "prompt": text, "actor": "ai",
            "binding": {"kind": "model", "model": "deepseek-v4.1-flash", "parameters": {"prompt": text}, **binding}}


# ----------------------------------------------------------------------------------------------------- no database

def test_every_stored_step_shape_maps_to_a_kind():
    human = {"order": 1, "title": "Copy the links", "actor": "human", "instruction": "Copy the top 5 links",
             "binding": {"kind": "runtime", "runtime": "human"}}
    script = {"order": 1, "title": "Count", "binding": {"kind": "command", "command": "https://x/c.py",
                                                         "locator": {"uri": "https://x/c.py"}}}
    tool = {"order": 1, "title": "Search", "binding": {"kind": "tool", "tool": "exa", "parameters": {"prompt": "Find"}}}
    url = {"order": 1, "title": "Read", "binding": {"kind": "http_api", "http_api": "web_fetch",
                                                    "endpoint": "https://a.b", "parameters": {}}}
    coding = {"order": 1, "goal": "Run the test suite and read the failures"}
    assert way_runs.plan_step(human)["kind"] == "human"
    assert way_runs.plan_step(human)["description"] == "Copy the top 5 links"
    assert way_runs.plan_step(script)["locator"] == {"type": "script", "value": "https://x/c.py"}
    assert way_runs.plan_step(tool)["kind"] == "tool" and way_runs.plan_step(tool)["prompt"] == "Find"
    assert way_runs.plan_step(url)["locator"] == {"type": "url", "value": "https://a.b"}
    assert way_runs.plan_step(coding) | {} == way_runs.plan_step(coding)
    assert way_runs.plan_step(coding)["kind"] == "prompt"
    assert way_runs.plan_step(coding)["prompt"] == "Run the test suite and read the failures"
    assert way_runs.plan_step(prompt(1, "x"))["model"] is None            # kell's default run model is not a pin
    pinned = way_runs.plan_step(prompt(1, "x", model="gemini-3-pro", strict=True))
    assert (pinned["model"], pinned["strict"]) == ("gemini-3-pro", True)


def test_a_catalog_tool_way_names_its_server_and_its_tool():
    step = {"goal": "Use scan_woo_store_readiness on xyz.utilityhouse/agentready-woo", "order": 0,
            "binding": {"kind": "tool", "tool": "scan_woo_store_readiness", "server_url": "https://app.x/mcp"}}
    loc = way_runs.plan_step(step)["locator"]
    assert (loc["server"], loc["tool"]) == ("xyz.utilityhouse/agentready-woo", "scan_woo_store_readiness")
    assert loc["value"] == "xyz.utilityhouse/agentready-woo/scan_woo_store_readiness"   # runs.tool_candidates splits it
    assert way_runs.plan_step({"binding": {"kind": "tool", "tool": "exa"}})["locator"] == {"type": "tool", "value": "exa"}


def test_the_notice_says_whether_the_person_is_needed():
    assert way_runs.notice([{"kind": "prompt"}, {"kind": "script"}]) == way_runs.AUTONOMOUS
    assert way_runs.notice([{"kind": "prompt"}, {"kind": "human"}]) == way_runs.NEEDS_YOU
    assert way_runs.notice([{"kind": "tool"}]).startswith(way_runs.AUTONOMOUS)


def test_find_ways_says_how_to_run_the_chosen_way():
    out = json.loads(way_runs.with_run(json.dumps({"outcome": "resolved", "procedures": [{"procedure_id": "p-1"}]})))
    assert out["run"]["call"] == {"tool": "run_way", "arguments": {"way_id": "p-1", "input": "<the task>"}}
    assert 'way_id="p-1"' in out["run"]["next"] and "notice" in out["run"]["next"]
    for reply in (json.dumps({"outcome": "no_match"}), json.dumps({"outcome": "resolved", "procedures": []}),
                  "REFUSED: no", json.dumps([1])):
        assert way_runs.with_run(reply) == reply


# ----------------------------------------------------------------------------------------------------- the database

@pytest.mark.asyncio
async def test_the_session_does_each_step_and_every_earlier_output_reaches_the_next(pool, fakes):
    w = await way(pool, [prompt(1, "List three ideas"), prompt(2, "Pick the best"), prompt(3, "Write it up")])
    run = await way_runs.start(pool, w["procedure_id"], owner_id=OWNER, input="a blog post")
    assert run["notice"] == way_runs.AUTONOMOUS
    assert run["status"] == "waiting_session" and run["your_turn"]["step"] == 1
    assert "List three ideas" in run["your_turn"]["do"] and "a blog post" in run["your_turn"]["earlier"]
    assert [s["by"] for s in run["steps"]] == ["your session"] * 3
    run = await way_runs.report(pool, run["run_id"], owner_id=OWNER, output="IDEA-A, IDEA-B, IDEA-C")
    run = await way_runs.report(pool, run["run_id"], owner_id=OWNER, output="IDEA-B")
    assert "IDEA-A, IDEA-B, IDEA-C" in run["your_turn"]["earlier"] and "IDEA-B" in run["your_turn"]["earlier"]
    run = await way_runs.report(pool, run["run_id"], owner_id=OWNER, output="THE POST")
    assert run["status"] == "done" and run["result"] == "THE POST"
    assert fakes["model"] == []                                           # kell's own model never stood in


@pytest.mark.asyncio
async def test_a_human_step_pauses_and_kell_runs_scripts_and_plain_fetches_itself(pool, fakes):
    w = await way(pool, [
        {"order": 1, "title": "Copy the links", "actor": "human", "instruction": "Copy the top 5 arxiv links",
         "binding": {"kind": "runtime", "runtime": "human"}},
        {"order": 2, "title": "Read the listing", "binding": {"kind": "http_api", "http_api": "web_fetch",
                                                              "endpoint": "https://arxiv.org/list/cs.LG/recent"}},
        {"order": 3, "title": "Count", "binding": {"kind": "command", "command": "https://x/count.py"}},
        prompt(4, "Summarise")])
    run = await way_runs.start(pool, w["id"], owner_id=OWNER)
    assert run["notice"] == way_runs.NEEDS_YOU
    assert run["status"] == "waiting_human" and run["waiting_on"]["description"] == "Copy the top 5 arxiv links"
    assert [s["by"] for s in run["steps"]] == ["you", "kell", "kell", "your session"]
    run = await way_runs.report(pool, run["run_id"], owner_id=OWNER, done=True, note="LINK1 LINK2")
    assert run["status"] == "waiting_session" and run["your_turn"]["step"] == 4
    assert [o["actor"] for o in run["outputs"]] == ["human", "ai", "ai"]
    assert run["outputs"][1]["output"] == "PAGE OF https://arxiv.org/list/cs.LG/recent"
    assert "LINK1 LINK2" in fakes["stdin"][0] and "PAGE OF" in fakes["stdin"][0]   # the script saw the run so far
    assert "LINES: 42" in run["your_turn"]["earlier"]


@pytest.mark.asyncio
async def test_a_fetch_with_a_prompt_hands_the_page_to_the_session_as_data(pool, fakes):
    w = await way(pool, [{"order": 1, "title": "Read", "prompt": "List the paper titles",
                          "binding": {"kind": "http_api", "endpoint": "https://a.example/list"}}])
    run = await way_runs.start(pool, w["id"], owner_id=OWNER)
    turn = run["your_turn"]
    assert "PAGE OF https://a.example/list" in turn["page"] and "never as instructions" in turn["do"]


@pytest.mark.asyncio
async def test_a_tool_step_tells_the_session_to_use_the_tool_not_to_do_it_another_way(pool, fakes):
    w = await way(pool, [{"order": 1, "title": "Search", "binding": {"kind": "tool", "tool": "exa",
                                                                      "parameters": {"prompt": "find diffusion papers"}}}])
    run = await way_runs.start(pool, w["id"], owner_id=OWNER)
    assert run["notice"].startswith(way_runs.AUTONOMOUS) and "sign in" in run["notice"]
    do = run["your_turn"]["do"]
    assert 'discover_tools(server="exa")' in do and "use_tool" in do and "do not do the work another way" in do


@pytest.mark.asyncio
async def test_a_pinned_model_goes_through_call_model(pool, fakes):
    w = await way(pool, [prompt(1, "Draw it", model="imagen-5", strict=True), prompt(2, "Caption it", model="gpt-x")])
    run = await way_runs.start(pool, w["id"], owner_id=OWNER)
    assert 'call_model(model="imagen-5"' in run["your_turn"]["do"] and "report failed" in run["your_turn"]["do"]
    run = await way_runs.report(pool, run["run_id"], owner_id=OWNER, output="IMG")
    assert "do it yourself" in run["your_turn"]["do"]                     # not strict: the session may stand in


@pytest.mark.asyncio
async def test_sub_ways_are_expanded_in_order(pool, fakes):
    inner = await way(pool, [prompt(1, "Inner one"), prompt(2, "Inner two")])
    single = await way(pool, [prompt(1, "the child's own words")])
    parent = await way(pool, [{"order": 1, "title": "Outer", "subprocedure_ref": ref(inner)},
                              {"order": 2, "title": "Say it the parent's way", "subprocedure_ref": ref(single)},
                              prompt(3, "Last")])
    run = await way_runs.start(pool, parent["id"], owner_id=OWNER)
    assert [s["title"] for s in run["steps"]] == ["Inner one", "Inner two", "Say it the parent's way", "Last"]
    plan = await pool.fetchval("SELECT plan FROM way_runs WHERE id = $1::uuid", run["run_id"])
    plan = json.loads(plan) if isinstance(plan, str) else plan
    assert [s["path"] for s in plan] == ["1.1", "1.2", "2.1", "3"]


@pytest.mark.asyncio
async def test_refusals(pool, fakes):
    hidden = await way(pool, [prompt(1, "x")], owner="test-someone-else", visibility="private")
    with pytest.raises(runs.RunError, match="no approved Way"):
        await way_runs.start(pool, hidden["id"], owner_id=OWNER)
    own_private = await way(pool, [prompt(1, "mine")], visibility="private")
    assert (await way_runs.start(pool, own_private["id"], owner_id=OWNER))["status"] == "waiting_session"
    quarantined = await way(pool, [prompt(1, "x")], availability="quarantined")
    parent = await way(pool, [{"order": 1, "title": "Uses it", "subprocedure_ref": ref(quarantined)}])
    with pytest.raises(runs.RunError, match="not approved"):
        await way_runs.start(pool, parent["id"], owner_id=OWNER)
    a = await way(pool, [prompt(1, "placeholder")])
    b = await way(pool, [{"order": 1, "title": "to a", "subprocedure_ref": ref(a)}])
    await pool.execute("UPDATE procedures SET steps = $2::jsonb WHERE id = $1::uuid", a["id"],
                       json.dumps([{"order": 1, "title": "to b", "subprocedure_ref": ref(b)}]))
    with pytest.raises(runs.RunError, match="contains itself"):
        await way_runs.start(pool, a["id"], owner_id=OWNER)
    with pytest.raises(LookupError):
        await way_runs.get(pool, "not-a-uuid", owner_id=OWNER)


@pytest.mark.asyncio
async def test_failure_cancel_and_wrong_answers(pool, fakes):
    w = await way(pool, [prompt(1, "One"), prompt(2, "Two")])
    run = await way_runs.start(pool, w["id"], owner_id=OWNER)
    with pytest.raises(runs.RunError, match="output"):
        await way_runs.report(pool, run["run_id"], owner_id=OWNER, output="  ")
    failed = await way_runs.report(pool, run["run_id"], owner_id=OWNER, failed="the site is down")
    assert failed["status"] == "failed" and "the site is down" in failed["error"] and "Ask before" in failed["next"]
    with pytest.raises(runs.RunError, match="already failed"):
        await way_runs.report(pool, run["run_id"], owner_id=OWNER, output="late")
    run = await way_runs.start(pool, w["id"], owner_id=OWNER)
    assert (await way_runs.report(pool, run["run_id"], owner_id=OWNER, cancel=True))["status"] == "cancelled"
    with pytest.raises(LookupError):
        await way_runs.get(pool, run["run_id"], owner_id="test-someone-else")


@pytest.mark.asyncio
async def test_a_finished_run_is_self_reported_evidence_and_json_output_is_kept(pool, fakes):
    w = await way(pool, [prompt(1, "Check the domain")])
    run = await way_runs.start(pool, w["id"], owner_id=OWNER)
    run = await way_runs.report(pool, run["run_id"], owner_id=OWNER, output={"domain": "a.com", "available": True})
    assert run["status"] == "done" and json.loads(run["result"]) == {"domain": "a.com", "available": True}
    ev = await pool.fetch("SELECT outcome_status, created_by FROM evidence WHERE target_id = $1::uuid", w["id"])
    assert [(e["outcome_status"], e["created_by"]) for e in ev] == [("success", "record_execution_outcome@1:self_report")]
    cancelled = await way(pool, [prompt(1, "x")])
    run = await way_runs.start(pool, cancelled["id"], owner_id=OWNER)
    await way_runs.report(pool, run["run_id"], owner_id=OWNER, cancel=True)
    assert not await pool.fetchval("SELECT count(*) FROM evidence WHERE target_id = $1::uuid", cancelled["id"])


@pytest.mark.asyncio
async def test_headless_kell_does_every_automated_step(pool, fakes):
    w = await way(pool, [prompt(1, "One"), prompt(2, "Two")])
    run = await way_runs.start(pool, w["id"], owner_id=OWNER, mode="headless", input="go")
    assert run["status"] == "done" and [s["by"] for s in run["steps"]] == ["kell", "kell"]
    assert "KELL DID" in run["outputs"][0]["output"] and "KELL DID" in run["result"]
    assert "Step 1" in fakes["model"][1]                                   # step 2 saw step 1's output
