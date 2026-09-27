"""Related examples (DS-1000 round-4 fix) against a real database: a VARIANT of a known task is a different Goal,
so find_ways answers no_match -- and still returns the neighbouring verified solution as a related example,
labelled not verified to apply. A Goal the judge firmly calls unrelated contributes nothing; flag off, nothing."""
from __future__ import annotations

import json

import pytest

import app.mcp_server.server as srv
from app.config import settings
from app.services import retrieval_service as rs
from app.services import search_projection as sp
from tests.identity_fakes import CallbackProvider, make_judge
from tests.test_goal_abstraction_e2e import DATABASE_URL, _run_id, pool  # noqa: F401
from tests.test_verified_solutions_e2e import _Ctx, _extract

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL")


@pytest.fixture
def flags(monkeypatch):
    monkeypatch.setattr(settings, "knowledge_verified_examples", True)
    monkeypatch.setattr(settings, "knowledge_related_examples", True)
    monkeypatch.setattr("app.services.object_storage.get_store", lambda: None)


@pytest.mark.asyncio
async def test_variant_request_gets_the_neighbours_verified_solution(pool, flags, monkeypatch):
    run = _run_id()
    code = f"df = df.div(df.sum(axis=0), axis=1)  # {run}"
    await _extract(pool, run, code)
    await sp.drain_outbox(pool)
    known = f"compute column percentages {run}"

    def verdicts(firm: bool):
        def judge(kind, a, b):
            if kind == "task_goal":
                # the known Goal is a close variant of the request: 'unrelated' at low confidence, or firmly unrelated
                return ("unrelated", 0.99 if firm else 0.5) if known in b.lower() else ("unrelated", 0.99)
            return ("applies", 0.9) if kind == "task_procedure" else ("distinct", 0.9)
        return lambda: make_judge(CallbackProvider(judge, name="jev"))

    request = f"compute row percentages instead of column percentages {run}"
    monkeypatch.setattr(rs, "default_judge", verdicts(firm=False))
    out = json.loads(await srv.find_ways(request, _Ctx(pool)))
    assert out["outcome"] == "no_match"
    ex = out["related_examples"]
    assert ex and ex[0]["verified_solution"]["code"] == code
    assert "NOT verified to apply" in ex[0]["label"] and ex[0]["relevance"]["relation"] == "unrelated"

    monkeypatch.setattr(rs, "default_judge", verdicts(firm=True))
    out = json.loads(await srv.find_ways(request, _Ctx(pool)))
    assert all(e["verified_solution"]["code"] != code for e in out.get("related_examples") or [])

    monkeypatch.setattr(settings, "knowledge_related_examples", False)
    monkeypatch.setattr(rs, "default_judge", verdicts(firm=False))
    out = json.loads(await srv.find_ways(request, _Ctx(pool)))
    assert "related_examples" not in out
