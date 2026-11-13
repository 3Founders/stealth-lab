"""Goal submission on a real database (docs/goal_submission.md), with the reviewer, the email and the run model faked:
submit -> a step rejected (kept out of retrieval, emailed) -> fixed -> every step AI approved -> the parent Way -> a run
that pauses at the human step and continues when the person is done."""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.submissions import api, notify, review, runs, store

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL -- live-database integration test")


@pytest_asyncio.fixture
async def pool():
    p = await create_pool(DATABASE_URL)
    try:
        if not await p.fetchval("SELECT to_regclass('goal_submissions') IS NOT NULL"):
            pytest.skip("db/204 is not applied")
        yield p
        # the goals these tests made would be candidates for every other test's goal search
        await p.execute("UPDATE goals SET t_invalid = now() WHERE created_from = 'goal_submission' "
                        "AND owner_id LIKE 'test-%' AND t_invalid IS NULL")
        await p.execute("UPDATE goal_search_index SET status = 'merged' WHERE goal_id IN (SELECT id FROM goals "
                        "WHERE created_from = 'goal_submission' AND owner_id LIKE 'test-%')")
    finally:
        await p.close()


@pytest.fixture
def fakes(monkeypatch):
    """The reviewer rejects any step whose prompt says VAGUE; JEV (faked) answers by the submitted goal's first word:
    "matchme..." is the same as a candidate starting with that word (0.9), "maybeme..." is the same only when asked
    with the description (one vote of three: a possible duplicate), "narrowme..." narrower (0.8), anything else
    distinct; the tool-argument writer picks the first tool; the run model
    echoes; no email leaves; no embedder is called."""
    sent: list[dict] = []
    calls: list[str] = []
    monkeypatch.setenv("SUBMISSION_GOAL_VECTORS", "off")

    class FakeJEV:
        async def judge_identity(self, kind, text, candidate):
            calls.append("match")
            word = text.split()[0].rstrip(":")
            prefix = next((p for p in ("matchme", "maybeme", "narrowme") if word.startswith(p)), None)
            hit = prefix and candidate.split()[0].rstrip(":") == word.replace(prefix, "matchme", 1)
            maybe = ("same", 0.6) if ":" in text else ("related", 0.6)
            rel, conf = {"matchme": ("same", 0.9), "maybeme": maybe,
                         "narrowme": ("specializes", 0.8)}.get(prefix, ("distinct", 0.9)) if hit else ("distinct", 0.9)
            return type("R", (), {"ok": True, "value": {"relation": rel, "confidence": conf}, "provider": "jev",
                                  "model": "fake", "reason": None})()

    from app.submissions import matching

    monkeypatch.setattr(matching, "_JUDGE", FakeJEV())

    async def fake_complete(messages, *, json_mode=False, max_tokens=1500, client=None):
        import json
        import re

        system, body = messages[0]["content"], messages[-1]["content"]
        if system.startswith("You call one tool"):
            calls.append("tool-args")
            tools = json.loads(re.search(r"\[.*\]", body, re.S).group(0))
            return json.dumps({"tool": tools[0]["name"], "arguments": {"query": "papers"}}), "fake-runner"
        if json_mode:
            data = json.loads(re.search(r"\{.*\}", body, re.S).group(0))
            calls.append("review")
            verdicts = [{"order": s["order"], "verdict": "reject" if "VAGUE" in s.get("prompt", "") else "approve",
                         "reason": "say which papers" if "VAGUE" in s.get("prompt", "") else "clear"}
                        for s in data["steps"] if s["to_review"]]
            return json.dumps({"steps": verdicts}), "fake-reviewer"
        calls.append("run")
        return "SUMMARY of: " + body[-200:], "fake-runner"

    async def fake_send(pool, sub, client=None):
        sent.append(sub)
        return True

    async def no_limit(pool, owner_id):
        return None

    monkeypatch.setattr(review, "complete", fake_complete)
    monkeypatch.setattr(notify, "send_rejection", fake_send)
    monkeypatch.setattr(api, "_limit", no_limit)
    return {"sent": sent, "calls": calls}


async def _retrievable(pool, procedure_row_id):
    return await pool.fetchval("SELECT availability = 'active' AND visibility = 'public' AND t_invalid IS NULL "
                               "FROM procedures WHERE id = $1::uuid", procedure_row_id)


@pytest.mark.asyncio
async def test_submit_review_fix_and_run(pool, fakes):
    owner = f"test-{uuid.uuid4().hex[:8]}"
    goal = f"ml research {uuid.uuid4().hex[:6]}"
    out = await api.submit(pool, owner, {"goal": goal, "steps": [
        {"kind": "human", "description": f"Search arxiv for {goal} and paste the top links"},
        {"prompt": "VAGUE do research"}]})
    assert out["mode"] == "human_ai" and out["message"] == "This was submitted as Human+AI."
    s1, s2 = out["steps"]
    assert out["status"] == "needs_changes"
    assert (s1["status"], s2["status"]) == ("ai_approved", "rejected") and s2["reason"] == "say which papers"
    assert await _retrievable(pool, s1["procedure_row_id"])                   # approved: in retrieval
    assert not await _retrievable(pool, s2["procedure_row_id"])               # rejected: never retrieved
    assert fakes["sent"] and fakes["sent"][0]["id"] == out["id"]
    assert await pool.fetchval("SELECT approval_status FROM procedures WHERE id = $1::uuid",
                               s1["procedure_row_id"]) == "ai_approved"
    # a run is refused until the whole Way is approved
    with pytest.raises(runs.RunError, match="needs changes"):
        await api.start_run(pool, owner, out["id"])
    # someone else cannot see or fix it
    with pytest.raises(LookupError):
        await api.get(pool, "someone-else", out["id"])

    fixed = await api.fix_step(pool, owner, out["id"], 2, {"prompt": "Summarise each pasted paper in two lines"})
    assert fixed["status"] == "ai_approved"
    assert [s["status"] for s in fixed["steps"]] == ["ai_approved", "ai_approved"]
    assert fixed["steps"][1]["revision"] == 2
    assert not await _retrievable(pool, s2["procedure_row_id"])               # the old Way of step 2 is retired
    parent = fixed["parent_procedure_id"]
    row = await pool.fetchrow("SELECT approval_status, availability::text AS a, visibility::text AS v, steps, "
                              "domain_payload FROM procedures WHERE id = $1::uuid", parent)
    assert (row["approval_status"], row["a"], row["v"]) == ("ai_approved", "active", "public")
    assert [s["subprocedure_ref"]["version"] for s in row["steps"]] == [1, 1]
    assert row["domain_payload"]["mode_label"] == "Human+AI"
    # the step goals the submission made are now public
    assert {r["visibility"] for r in await pool.fetch(
        "SELECT g.visibility::text AS visibility FROM goals g JOIN goal_submission_steps s ON s.goal_id = g.id "
        "WHERE s.submission_id = $1::uuid", out["id"])} == {"public"}

    run = await api.start_run(pool, owner, out["id"], "diffusion transformers")
    assert run["status"] == "waiting_human" and run["waiting_on"]["order"] == 1
    with pytest.raises(runs.RunError):
        await api.finish_step(pool, "someone-else", run["id"], "x")
    run = await api.finish_step(pool, owner, run["id"], "https://arxiv.org/abs/2212.09748")
    assert run["status"] == "done"
    assert [o["actor"] for o in run["outputs"]] == ["human", "ai"]
    assert "2212.09748" in run["outputs"][1]["output"]                         # the person's note reached step 2
    with pytest.raises(runs.RunError, match="not waiting"):
        await api.finish_step(pool, owner, run["id"], "again")


@pytest.mark.asyncio
async def test_a_person_rejecting_a_step_takes_the_way_out_of_retrieval(pool, fakes):
    owner = f"test-{uuid.uuid4().hex[:8]}"
    out = await api.submit(pool, owner, {"goal": f"tidy notes {uuid.uuid4().hex[:6]}",
                                         "steps": [{"prompt": "Group the notes by topic"}]})
    assert out["status"] == "ai_approved" and out["mode"] == "fully_ai"
    parent = out["parent_procedure_id"]
    assert await _retrievable(pool, parent)
    with pytest.raises(ValueError, match="reason"):
        await review.decide(pool, out["id"], 1, approve=False, by="anuj")
    res = await review.decide(pool, out["id"], 1, approve=False, by="anuj", reason="too broad")
    assert res["status"] == "needs_changes"
    assert not await _retrievable(pool, parent)
    assert not await _retrievable(pool, out["steps"][0]["procedure_row_id"])
    got = await store.get(pool, out["id"])
    assert got["steps"][0]["reason"] == "too broad" and got["steps"][0]["reviewer"] == "person:anuj"


@pytest.mark.asyncio
async def test_reviewer_down_decides_nothing(pool, fakes, monkeypatch):
    async def down(*a, **k):
        raise RuntimeError("no reviewer model answered")

    monkeypatch.setattr(review, "complete", down)
    owner = f"test-{uuid.uuid4().hex[:8]}"
    out = await api.submit(pool, owner, {"goal": f"plan a trip {uuid.uuid4().hex[:6]}",
                                         "steps": [{"kind": "human", "description": "Book the train"}]})
    assert out["status"] == "reviewing" and out["steps"][0]["status"] == "reviewing"
    assert not await _retrievable(pool, out["steps"][0]["procedure_row_id"])
    assert fakes["sent"] == []


@pytest.mark.asyncio
async def test_http_routes(pool, fakes):
    """The HTTP surface on a bare Starlette app with a fake sign-in gate: each caller sees only their own."""
    import httpx
    from starlette.applications import Starlette
    from starlette.routing import Route

    from app.mcp_server import submission_routes

    routes: list[Route] = []

    class Server:
        def custom_route(self, path, methods, include_in_schema=False):
            def deco(fn):
                routes.append(Route(path, fn, methods=methods))
                return fn
            return deco

    class Gate:
        async def subject(self, request):
            who = request.headers.get("x-test-user")
            return (who, None) if who else (None, ("sign in", 401))

    submission_routes.register(Server(), Gate(), lambda: pool)
    owner = f"test-{uuid.uuid4().hex[:8]}"
    me = {"x-test-user": owner}
    async with httpx.AsyncClient(transport=httpx.ASGITransport(app=Starlette(routes=routes)),
                                 base_url="http://t") as c:
        assert (await c.post("/v1/goals/submissions", json={})).status_code == 401
        r = await c.post("/v1/goals/submissions/preview", headers=me,
                         json={"goal": "g", "steps": [{"kind": "human", "description": "do it"}]})
        assert r.json()["mode_label"] == "Fully Human"
        r = await c.post("/v1/goals/submissions", headers=me, json={"goal": "g", "steps": [{"kind": "human"}]})
        assert r.status_code == 400 and "describe what they do" in r.text
        r = await c.post("/v1/goals/submissions", headers=me, json={
            "goal": f"read a paper {uuid.uuid4().hex[:6]}",
            "steps": [{"kind": "human", "description": "Open the paper's PDF"}, {"prompt": "Summarise it"}]})
        assert r.status_code == 200, r.text
        sid = r.json()["id"]
        assert r.json()["status"] == "ai_approved"
        assert (await c.get(f"/v1/goals/submissions/{sid}", headers={"x-test-user": "other"})).status_code == 404
        assert [s["id"] for s in (await c.get("/v1/goals/submissions", headers=me)).json()["submissions"]] == [sid]
        run = (await c.post(f"/v1/goals/submissions/{sid}/runs", headers=me, json={"input": "attention"})).json()
        assert run["status"] == "waiting_human"
        run = (await c.post(f"/v1/goals/runs/{run['id']}/done", headers=me, json={"note": "read it"})).json()
        assert run["status"] == "done"
        assert (await c.get(f"/v1/goals/runs/{run['id']}", headers=me)).json()["status"] == "done"


@pytest.mark.asyncio
async def test_a_goal_judged_the_same_is_reused_not_duplicated(pool, fakes):
    import random
    import string

    tag = "".join(random.choice(string.ascii_lowercase) for _ in range(8))   # search drops tokens with digits
    owner = f"test-{uuid.uuid4().hex[:8]}"
    first = await api.submit(pool, owner, {"goal": f"matchme{tag} {tag[::-1]}", "steps": [{"prompt": "Find the papers"}]})
    assert first["status"] == "ai_approved"                      # its goal is now public
    from app.services.search_projection import drain_outbox

    await drain_outbox(pool)                                     # the server's background drain, in a test
    second = await api.submit(pool, owner, {"goal": f"matchme{tag} {tag[::-1]} quickly",
                                            "steps": [{"prompt": "Find the newest papers"}]})
    assert second["goal_id"] == first["goal_id"]
    assert second["goal_match"]["decision"] == "same:jev:fake"
    # approved under another name: that name now finds the goal too
    aliases = await pool.fetchval("SELECT aliases FROM goals WHERE id = $1::uuid", first["goal_id"])
    assert f"matchme{tag} {tag[::-1]} quickly" in aliases
    other = await api.submit(pool, owner, {"goal": f"unrelated {tag} cooking", "steps": [{"prompt": "Plan a meal"}]})
    assert other["goal_id"] != first["goal_id"] and other["goal_match"]["decision"].startswith(("new:",
                                                                                                "no_candidates"))


@pytest.mark.asyncio
async def test_a_maybe_duplicate_is_kept_new_and_a_narrower_goal_is_linked(pool, fakes):
    import random
    import string

    tag = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    owner = f"test-{uuid.uuid4().hex[:8]}"
    base = await api.submit(pool, owner, {"goal": f"matchme{tag} {tag[::-1]}", "steps": [{"prompt": "Find the papers"}]})
    maybe = await api.submit(pool, owner, {"goal": f"maybeme{tag} {tag[::-1]}", "description": "the same, maybe", "steps": [{"prompt": "Find them"}]})
    assert maybe["goal_id"] != base["goal_id"]
    assert maybe["goal_match"]["decision"] == "possible_duplicate:jev:fake"
    assert maybe["goal_match"]["possible_duplicate"]["id"] == base["goal_id"]
    narrow = await api.submit(pool, owner, {"goal": f"narrowme{tag} {tag[::-1]}", "steps": [{"prompt": "Find one"}]})
    assert narrow["goal_id"] != base["goal_id"] and narrow["goal_match"]["relations"][0]["relation"] == "specializes"
    assert await pool.fetchval(          # approved: placement links it as a special case, the edge search walks
        "SELECT count(*) FROM goal_relations WHERE specific_goal_id = $1::uuid AND abstract_goal_id = $2::uuid "
        "AND relation_type = 'SPECIALIZES' AND status = 'accepted'", narrow["goal_id"], base["goal_id"]) == 1
    assert await pool.fetchval(          # the possible duplicate is never linked
        "SELECT count(*) FROM goal_relations WHERE $1::uuid IN (specific_goal_id, abstract_goal_id) "
        "AND status = 'accepted'", maybe["goal_id"]) == 0


@pytest.mark.asyncio
async def test_a_private_unapproved_goal_is_never_a_match(pool, fakes):
    import random
    import string

    tag = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    hidden = await api.submit(pool, f"test-{uuid.uuid4().hex[:8]}",
                              {"goal": f"matchme{tag} {tag[::-1]}", "steps": [{"prompt": "VAGUE do it"}]})
    assert hidden["status"] == "needs_changes"                   # its goal stays private
    mine = await api.submit(pool, f"test-{uuid.uuid4().hex[:8]}",
                            {"goal": f"matchme{tag} {tag[::-1]} again", "steps": [{"prompt": "Find the papers"}]})
    assert mine["goal_id"] != hidden["goal_id"]
    assert hidden["goal_name"] not in str(mine["goal_match"])


@pytest.mark.asyncio
async def test_a_tool_step_runs_through_the_gateway_and_pauses_for_a_sign_in(pool, fakes, monkeypatch):
    import json

    calls: list[tuple] = []
    answers = [json.dumps({"sign_in_required": True, "server": "fake/papers", "link": "https://sign.in/x"}),
               "3 papers found"]

    async def tool_list(owner, server):
        if server != "fake/papers":
            return f"REFUSED: no server named {server!r} in kell's catalog"
        return json.dumps({"server": server, "tools": [{"name": "search", "input_schema": {"type": "object"}}]})

    async def tool_call(owner, server, tool, arguments):
        calls.append((owner, server, tool, arguments))
        return answers.pop(0)

    async def runnable(pool, value):
        return None

    monkeypatch.setattr(runs, "TOOL_LIST", tool_list)
    monkeypatch.setattr(runs, "TOOL_CALL", tool_call)
    monkeypatch.setattr(review, "_tool_runnable", runnable)
    owner = f"test-{uuid.uuid4().hex[:8]}"
    out = await api.submit(pool, owner, {"goal": f"find papers {uuid.uuid4().hex[:6]}", "steps": [
        {"prompt": "Search for diffusion papers", "locator": "fake/papers/search"},
        {"prompt": "Summarise them"}]})
    assert out["status"] == "ai_approved" and out["mode"] == "fully_ai"
    run = await api.start_run(pool, owner, out["id"])
    assert run["status"] == "waiting_human" and run["waiting_on"]["kind"] == "signin"
    assert "https://sign.in/x" in run["waiting_on"]["description"]
    run = await api.finish_step(pool, owner, run["id"])                  # signed in: the same step again
    assert run["status"] == "done", run["error"]
    assert [c[1:3] for c in calls] == [("fake/papers", "search")] * 2 and calls[0][0] == owner
    assert run["outputs"][0]["output"] == "3 papers found"


@pytest.mark.asyncio
async def test_a_script_step_is_reviewed_with_its_source_and_runs_in_the_sandbox(pool, fakes, monkeypatch):
    from app.submissions import sandbox

    seen: dict = {}

    async def fetch(url, *, as_code=False, limit=12000):
        return "import sys\nprint(len(sys.stdin.read()))\n"

    async def run_script(code, name, stdin=""):
        seen.update(code=code, name=name, stdin=stdin)
        return sandbox.ScriptResult(0, "42\n", "")

    monkeypatch.setattr(runs, "fetch_text", fetch)
    monkeypatch.setattr(sandbox, "run_script", run_script)
    owner = f"test-{uuid.uuid4().hex[:8]}"
    out = await api.submit(pool, owner, {"goal": f"count words {uuid.uuid4().hex[:6]}", "steps": [
        {"kind": "human", "description": "Paste the text to count"},
        {"title": "Count the words in the text", "locator": "https://github.com/x/y/blob/main/count.py"}]})
    assert out["status"] == "ai_approved"
    run = await api.start_run(pool, owner, out["id"])
    run = await api.finish_step(pool, owner, run["id"], "one two three")
    assert run["status"] == "done", run["error"]
    assert run["outputs"][1]["output"] == "42\n"
    assert "one two three" in seen["stdin"] and seen["name"].endswith("count.py")


@pytest.mark.asyncio
async def test_a_script_on_the_submitters_computer_is_rejected(pool, fakes):
    owner = f"test-{uuid.uuid4().hex[:8]}"
    out = await api.submit(pool, owner, {"goal": f"local script {uuid.uuid4().hex[:6]}",
                                         "steps": [{"title": "Train the model", "locator": "C:/Users/me/train.py"}]})
    assert out["status"] == "needs_changes"
    assert "on your computer" in out["steps"][0]["reason"]


@pytest.mark.asyncio
async def test_a_fixed_step_retires_the_goal_it_left(pool, fakes):
    import random
    import string

    tag = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    owner = f"test-{uuid.uuid4().hex[:8]}"
    out = await api.submit(pool, owner, {"goal": f"collect {tag} papers", "steps": [
        {"title": f"Gather {tag} sources", "prompt": "VAGUE find stuff"}]})
    left = out["steps"][0]["goal_id"]
    assert out["steps"][0]["status"] == "rejected"
    fixed = await api.fix_step(pool, owner, out["id"], 1, {"title": f"List {tag} preprints", "prompt": "List them"})
    assert fixed["steps"][0]["goal_id"] != left and fixed["steps"][0]["status"] == "ai_approved"
    assert await pool.fetchval("SELECT t_invalid IS NOT NULL FROM goals WHERE id = $1::uuid", left)


@pytest.mark.asyncio
async def test_the_same_name_pending_for_someone_else_is_published_without_their_description(pool, fakes):
    import random
    import string

    tag = "".join(random.choice(string.ascii_lowercase) for _ in range(8))
    first = await api.submit(pool, f"test-{uuid.uuid4().hex[:8]}", {
        "goal": f"plan {tag} trip", "steps": [{"title": f"Pick {tag} dates", "prompt": "VAGUE PRIVATE NOTE"}]})
    pending = first["steps"][0]["goal_id"]
    assert first["steps"][0]["status"] == "rejected"           # its goal stays private
    second = await api.submit(pool, f"test-{uuid.uuid4().hex[:8]}", {
        "goal": f"organise {tag} trip", "steps": [{"title": f"Pick {tag} dates", "prompt": "Choose dates that fit"}]})
    assert second["steps"][0]["goal_id"] == pending            # the same name is the same goal
    row = await pool.fetchrow("SELECT visibility::text AS v, description FROM goals WHERE id = $1::uuid", pending)
    assert row["v"] == "public" and "PRIVATE NOTE" not in (row["description"] or "")
    assert await _retrievable(pool, second["steps"][0]["procedure_row_id"])
