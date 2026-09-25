"""Both doors use ONE Procedure tier (remaining_work.md #14).

REST `find_best_way` and MCP `find_ways` must agree on a shared case: the same
Procedure judged not applicable is excluded by both, and the same Procedure comes
first. Real database; only the judge's verdicts are scripted."""
import json
import os
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.services import retrieval_service as rs
from app.services import search_projection as sp
from app.services import shards as sh
from app.services.access import AccessScope
from app.services.procedures import capture_procedure
from tests.identity_fakes import CallbackProvider, ConceptEmbedder, make_judge

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="requires a real DATABASE_URL")

T = "tier" + uuid.uuid4().hex[:6]
EMB = ConceptEmbedder()
U = AccessScope.unrestricted()


def _verdict(kind, a, b):
    ours = T in b.lower()
    if kind == "task_goal":
        return ("matches", 0.9) if ours else ("unrelated", 0.9)
    if kind == "task_procedure":
        if not ours:
            return ("not_applicable", 0.9)
        return ("not_applicable", 0.95) if "windows only" in b.lower() else ("applies", 0.9)
    return ("distinct", 0.9)


JUDGE = make_judge(CallbackProvider(_verdict, name="jev"))


class _Ctx:
    def __init__(self, pool):
        self.request_context = type("R", (), {"lifespan_context": {"pool": pool}})()


@pytest_asyncio.fixture
async def pool(monkeypatch):
    monkeypatch.setattr(rs, "default_judge", lambda: JUDGE)
    p = await create_pool()
    yield p
    await p.close()


@pytest.mark.asyncio
async def test_find_best_way_and_find_ways_agree(pool):
    goal = f"{T} convert markdown to pdf"
    for name, description in (("pandoc route", "use pandoc"), ("windows only route", "windows only: use word")):
        await capture_procedure(
            pool, name=f"{T} {name}", goal=goal, steps=[{"description": description}],
            provenance="prior_library", scope_type="global", goal_embedder=EMB, goal_judge=JUDGE)
    await sp.drain_outbox(pool, pools=sh.pools_for(pool))

    rest = await rs.find_best_way(pool, goal, scope=U, embedder=EMB, judge=JUDGE, require_verified=False, record=False)
    rest_names = [p["name"] for p in rest["procedures"]]
    assert rest_names[0] == f"{T} pandoc route"
    assert f"{T} windows only route" not in rest_names

    import app.mcp_server.server as srv

    reply = json.loads(await srv.find_ways(goal, _Ctx(pool), semantic=False, use_llm=False))
    assert reply["outcome"] == "resolved"
    text = json.dumps(reply)
    assert f"{T} pandoc route" in text
    assert f"{T} windows only route" not in text      # the same judged exclusion as REST
