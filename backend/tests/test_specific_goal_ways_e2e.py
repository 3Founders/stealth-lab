"""Ways observed on more specific Goals, against a real database.

A Goal with no Procedure of its own offers the Procedures of its accepted more specific
Goals -- through the same Procedure tier, judged against the request, labelled with the
Goal each was observed on. Nothing is offered unjudged (no judge -> unresolved, as
before), and a judge that rejects every candidate leaves the Goal unresolved. An
ambiguous find_ways answer stays ambiguous but lists judged ways on its candidates."""
from __future__ import annotations

import pytest

from app.execution.goal_resolution import resolve_goal
from app.services import retrieval_service as rs
from app.services import search_projection as sp
from app.services.access import AccessScope
from app.services.procedures import capture_procedure
from tests.identity_fakes import CallbackProvider, make_judge
from tests.test_goal_abstraction_e2e import DATABASE_URL, _edge, _goal, _run_id, pool  # noqa: F401

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL")
ANON = AccessScope.anonymous()


def _judge(applicable_marker: str):
    def verdict(kind, a, b):
        if kind == "task_procedure":
            return ("applies", 0.9) if applicable_marker in b.lower() else ("not_applicable", 0.9)
        return ("distinct", 0.9)
    return make_judge(CallbackProvider(verdict, name="jev"))


async def _setup(pool, run: str):
    domain = await _goal(pool, f"e2e {run} process and analyze text in python")
    words = await capture_procedure(
        pool, name=f"e2e {run} count words after removing urls", goal=f"e2e {run} count words in a text without urls",
        steps=[{"description": "strip URLs with re.sub"}, {"description": "Counter(words).most_common(n)"}],
        provenance="prior_library", scope_type="global")
    plots = await capture_procedure(
        pool, name=f"e2e {run} plot a histogram", goal=f"e2e {run} plot a histogram of a column",
        steps=[{"description": "df.hist()"}], provenance="prior_library", scope_type="global")
    await sp.drain_outbox(pool)
    words_goal = await pool.fetchval("SELECT achieves_goal_id::text FROM procedures WHERE id = $1::uuid", words["id"])
    plots_goal = await pool.fetchval("SELECT achieves_goal_id::text FROM procedures WHERE id = $1::uuid", plots["id"])
    await _edge(pool, words_goal, domain)
    await _edge(pool, plots_goal, domain)
    await sp.drain_outbox(pool)
    return domain, words_goal, str(words["procedure_id"])


async def _context(query: str, judge):
    return {"_judge": judge, "_query_context": await rs.build_query_context(query, [], embedder=None)}


@pytest.mark.asyncio
async def test_a_goal_without_ways_offers_judged_ways_of_its_more_specific_goals(pool):
    run = _run_id()
    domain, words_goal, words_proc = await _setup(pool, run)
    query = f"count the top words in a paragraph, ignoring links ({run})"

    node = await resolve_goal(pool, domain, context=await _context(query, _judge("count words")), scope=ANON)
    assert node.chosen == "procedure" and node.procedure["procedure_id"] == words_proc
    assert node.procedure["observed_on_more_specific_goal"]["goal_id"] == words_goal
    assert "observed on the more specific Goal" in node.rationale

    rejected = await resolve_goal(pool, domain, context=await _context(query, _judge("nothing matches")), scope=ANON)
    assert rejected.chosen == "unresolved"                        # judged, none applicable

    unjudged = await resolve_goal(pool, domain, context={}, scope=ANON)
    assert unjudged.chosen == "unresolved"                        # never offered without a judgment


@pytest.mark.asyncio
async def test_ambiguous_candidates_carry_judged_ways(pool, monkeypatch):
    import app.mcp_server.server as srv

    run = _run_id()
    domain, words_goal, words_proc = await _setup(pool, run)
    monkeypatch.setattr(rs, "default_judge", lambda: _judge("count words"))
    candidates = [{"goal": {"id": domain, "canonical_name": "text"}, "score": 0.7}]
    await srv._attach_candidate_ways(pool, candidates, f"count words, no urls ({run})", [], scope=ANON)
    ways = candidates[0]["ways"]
    assert [w["procedure_id"] for w in ways] == [words_proc]
    assert ways[0]["observed_on_goal"]["goal_id"] == words_goal
