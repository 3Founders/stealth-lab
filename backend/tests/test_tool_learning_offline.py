"""The ranking learns from every tool call and from people's votes (2026-11-05), and the room agent's tool search is decided by the triage
judge (JEV first) with the word rules as the fallback. No model, network or database."""

import pytest

from app.services import room_agent
from app.tool_routing import retrieve, store


def test_the_judge_decides_when_the_room_agent_must_search_first():
    # it needs a web search: a search under that goal, even for a message the word rules call small talk
    assert room_agent.decide("btc price?", {"kind": "knowledge_question", "tool": "web", "confidence": 0.9}) == (True, False, "web")
    # sure it is conversation: no forced search, nothing has to be made
    assert room_agent.decide("yes, do that then please", {"kind": "conversation", "tool": None, "confidence": 0.9}) == (False, False, None)
    # unsure, or no verdict at all: the word rules, as before
    assert room_agent.decide("yes, do that then please", {"kind": "conversation", "tool": None, "confidence": 0.4})[0] is True
    assert room_agent.decide("make me a slide deck on htn", None) == (True, True, None)
    assert room_agent.decide("make me a slide deck on htn", {"kind": "reusable_task", "tool": "bogus", "confidence": 1}) == (True, True, None)
    # a canvas open: never forced
    assert room_agent.decide("make it shorter please", {"kind": "reusable_task", "tool": "web", "confidence": 1}, skip_search=True) == (False, False, None)


def test_every_gateway_call_is_judged_automatically():
    assert store.judge_call("ok", "a page of search results " * 3) == "pass"
    assert store.judge_call("ok", "  ") == "fail"                       # answered nothing
    assert store.judge_call("failed:timeout", "") == "fail"
    assert store.judge_call("failed:ConnectError", "") == "fail"
    assert store.judge_call("failed:tool_error", "bad arguments") is None    # often the agent's own arguments
    assert store.judge_call("failed:signin", "") is None
    assert store.judge_call("refused:secret", "") is None


def test_a_search_remembers_its_goal_for_the_calls_that_follow():
    store.note_goal("me", "web")
    assert store.recent_goal("me") == "web" and store.recent_goal("someone-else") is None


@pytest.mark.asyncio
async def test_a_search_with_no_goal_given_still_uses_what_worked_for_the_goal_it_found():
    from app.tool_routing.catalog import ToolCard

    asked: list[str] = []

    async def outcomes_for(goal):
        asked.append(goal)
        return {}

    cards = [ToolCard(id="r:a/web", title="Web", name="a/web", server="a", description="search the web for current news", source="registry", extra={})]
    await retrieve.rank("search the web for news", cards, outcomes_for=outcomes_for)
    assert asked and asked[0]
