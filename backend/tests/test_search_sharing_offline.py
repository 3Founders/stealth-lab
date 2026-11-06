"""One tool search per request: the prefetch, the model's own forced search and a retry share one router run instead of three."""
import asyncio

import pytest

from app.mcp_server import server as sv


@pytest.fixture(autouse=True)
def clean(monkeypatch):
    sv._SEARCH_CACHE.clear()
    sv._SEARCH_FLIGHT.clear()
    monkeypatch.delenv("KELL_SEARCH_CACHE_S", raising=False)
    yield
    sv._SEARCH_CACHE.clear()
    sv._SEARCH_FLIGHT.clear()


def fake(calls, delay=0.05):
    async def run(need, goal, installed, k, owner=None):
        calls.append((need, goal, k, owner))
        await asyncio.sleep(delay)
        return {"goal": goal or "web", "tools": [{"name": f"t{i}"} for i in range(k)]}
    return run


@pytest.mark.asyncio
async def test_two_callers_for_the_same_request_share_one_search_and_each_gets_its_own_top(monkeypatch):
    calls: list = []
    monkeypatch.setattr(sv, "_external_tools_uncached", fake(calls))
    a, b = await asyncio.gather(sv._external_tools("make slides", "", None, 3, "u1"), sv._external_tools("Make slides ", "", None, 14, "u1"))
    assert len(calls) == 1 and calls[0][2] == sv.SEARCH_K          # one run, made wide
    assert [t["name"] for t in a["tools"]] == ["t0", "t1", "t2"] and len(b["tools"]) == 14
    a["tools"].append({"name": "mine"})                              # a caller's edits do not leak into the shared answer
    c = await sv._external_tools("make slides", "", None, 5, "u1")
    assert len(c["tools"]) == 5 and len(calls) == 1                  # served from the short cache


@pytest.mark.asyncio
async def test_a_caller_that_gives_up_does_not_cancel_the_search_the_next_one_uses(monkeypatch):
    calls: list = []
    monkeypatch.setattr(sv, "_external_tools_uncached", fake(calls, delay=0.2))
    with pytest.raises(asyncio.TimeoutError):
        await asyncio.wait_for(sv._external_tools("make slides", "", None, 3, "u1"), timeout=0.05)     # the prefetch's two-second limit
    out = await sv._external_tools("make slides", "", None, 14, "u1")                                   # the model's own search, a moment later
    assert len(calls) == 1 and len(out["tools"]) == 14


@pytest.mark.asyncio
async def test_different_people_goals_and_installed_lists_are_not_shared(monkeypatch):
    calls: list = []
    monkeypatch.setattr(sv, "_external_tools_uncached", fake(calls, delay=0))
    await sv._external_tools("x y z", "", None, 3, "u1")
    await sv._external_tools("x y z", "", None, 3, "u2")
    await sv._external_tools("x y z", "web", None, 3, "u1")
    await sv._external_tools("x y z", "", [{"server": "s", "tools": []}], 3, "u1")
    assert len(calls) == 4


@pytest.mark.asyncio
async def test_the_cache_can_be_turned_off_and_an_empty_answer_is_not_kept(monkeypatch):
    calls: list = []
    monkeypatch.setenv("KELL_SEARCH_CACHE_S", "0")
    monkeypatch.setattr(sv, "_external_tools_uncached", fake(calls, delay=0))
    await sv._external_tools("a b c", "", None, 3, "u1")
    await sv._external_tools("a b c", "", None, 3, "u1")
    assert len(calls) == 2
    monkeypatch.delenv("KELL_SEARCH_CACHE_S")

    async def none(need, goal, installed, k, owner=None):
        calls.append(1)
        return None
    monkeypatch.setattr(sv, "_external_tools_uncached", none)
    await sv._external_tools("d e f", "", None, 3, "u1")
    await sv._external_tools("d e f", "", None, 3, "u1")
    assert calls.count(1) == 2
