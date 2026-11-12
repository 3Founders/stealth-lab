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
    finally:
        await p.close()


@pytest.fixture
def fakes(monkeypatch):
    """The reviewer rejects any step whose prompt says VAGUE; the run model echoes; no email leaves."""
    sent: list[dict] = []
    calls: list[str] = []

    async def fake_complete(messages, *, json_mode=False, max_tokens=1500, client=None):
        import json
        import re

        body = messages[-1]["content"]
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
