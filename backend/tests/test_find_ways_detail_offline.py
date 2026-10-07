"""find_ways(detail="summary"): the same answer, shorter -- and the full one one call away."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

import app.mcp_server.find_ways_governor as gov_mod
import app.mcp_server.server as srv
from app.mcp_server import find_ways_detail as detail

LONG = "do the thing carefully and verify it afterwards " * 20


def _step(i, **kw):
    return {"order": i, "do": LONG, "description": LONG, "kind": "instruction", "check": {"cmd": "pytest -q " + LONG},
            "binding": None, "needs": ["python"], "source_locator": {"doc": "x"}, **kw}


def _reply(**extra):
    return json.dumps({
        "outcome": "resolved", "goal": {"id": "g1", "name": "Add a DOCX export"},
        "procedures": [{"goal_id": "g1", "procedure_id": "p1", "name": "docx export", "what_it_does": "Exports a DOCX",
                        "why_chosen": "best match", "repo_fit": {"supported": ["R-001"]}, "preconditions": ["python>=3.10"],
                        "alternatives": [{"procedure_id": "p2", "name": "pdf export"}],
                        "verified_solution": {"summary": LONG, "patch": "diff --git " + LONG * 5, "tests": ["a", "b", "c", "d", "e"]},
                        "steps": [_step(1), _step(2, binding={"tool": "make"}, kind="action"), _step(3, kind="subgoal", subgoal_id="g2")]}],
        "related_examples": [{"goal_name": f"ex {i}", "solution": LONG * 3} for i in range(9)],
        "next": "compile it", **extra})


def test_summary_keeps_what_chooses_and_shortens_what_executes():
    full = _reply()
    out = json.loads(detail.summarize(full))
    proc = out["procedures"][0]
    assert (proc["goal_id"], proc["procedure_id"], proc["name"], proc["why_chosen"]) == ("g1", "p1", "docx export", "best match")
    assert proc["repo_fit"] == {"supported": ["R-001"]} and proc["preconditions"] == ["python>=3.10"]
    assert proc["alternatives"] == [{"procedure_id": "p2", "name": "pdf export"}]
    assert [s["order"] for s in proc["steps"]] == [1, 2, 3] and all(len(s["do"]) <= detail.STEP_CHARS + 1 for s in proc["steps"])
    assert proc["steps"][1]["has_binding"] is True and proc["steps"][0]["has_binding"] is False
    assert proc["steps"][0]["has_check"] is True and proc["steps"][2]["subgoal_id"] == "g2"
    assert "source_locator" not in proc["steps"][0] and "needs" not in proc["steps"][0]
    assert proc["verified_solution"]["clipped"] is True and len(proc["verified_solution"]["patch"]) <= detail.CLIP_CHARS + 1
    assert len(proc["verified_solution"]["tests"]) == detail.CLIP_ITEMS
    assert len(out["related_examples"]) == detail.EXAMPLES_KEPT
    assert out["detail"] == "summary" and "detail=\"full\"" in out["expand"] and out["next"] == "compile it"
    assert len(detail.summarize(full)) < len(full) / 2 and out["shortened_from_chars"] == len(full)


def test_nothing_is_invented_or_reworded():
    out = json.loads(detail.summarize(_reply()))
    original = json.loads(_reply())
    for step_out, step_in in zip(out["procedures"][0]["steps"], original["procedures"][0]["steps"]):
        assert step_in["do"].startswith(step_out["do"].rstrip("…"))                      # a prefix, never a rewrite


@pytest.mark.parametrize("reply", [
    "REFUSED: bad json", "not json at all", "[1, 2]", json.dumps({"outcome": "no_match", "candidates": []}),
    json.dumps({"outcome": "ambiguous", "candidates": [{"goal": "a"}]}), json.dumps({"outcome": "refused", "governor": {}})])
def test_replies_with_nothing_to_shorten_come_back_unchanged(reply):
    assert detail.summarize(reply) == reply


def test_a_summary_that_would_not_be_smaller_is_not_used():
    tiny = json.dumps({"outcome": "resolved", "procedures": [{"goal_id": "g", "steps": [{"order": 1, "do": "x", "kind": "instruction"}]}]})
    assert detail.summarize(tiny) == tiny


def test_an_ambiguous_suggestion_is_clipped():
    reply = json.dumps({"outcome": "ambiguous", "candidates": [], "suggested": {"goal": "g", "verified_solution": {"patch": LONG * 5}}})
    out = json.loads(detail.summarize(reply))
    assert out["suggested"]["clipped"] is True and len(out["suggested"]["verified_solution"]["patch"]) <= detail.CLIP_CHARS + 1


# ------------------------------------------------------------------ the tool

def _ctx():
    return SimpleNamespace(request_context=SimpleNamespace(lifespan_context={"pool": object()}))


@pytest.fixture
def tool(monkeypatch):
    calls = {"impl": 0}

    async def impl(*a, **k):
        calls["impl"] += 1
        return _reply()

    async def record(*a, **k):
        return None
    governor = gov_mod.FindWaysGovernor()
    monkeypatch.setattr(srv, "_find_ways_impl", impl)
    monkeypatch.setattr(srv, "_record_find_ways", record)
    monkeypatch.setattr(srv, "_find_ways_caller", lambda ctx: "caller-1")
    monkeypatch.setattr(gov_mod, "governor", lambda: governor)
    return calls


def find(**kw):
    return asyncio.run(srv.find_ways("add a docx export to the report page", _ctx(), **kw))


def test_default_is_the_full_reply_plus_the_untrusted_content_notice(tool):
    body = json.loads(find())
    notice = body.pop("content_trust")
    assert body == json.loads(_reply()) and "untrusted data" in notice


def test_the_notice_is_only_on_replies_that_carry_contributed_text():
    assert srv._mark_untrusted(json.dumps({"outcome": "no_match", "candidates": []})) == json.dumps(
        {"outcome": "no_match", "candidates": []})
    assert srv._mark_untrusted("REFUSED: x") == "REFUSED: x"
    assert srv._mark_untrusted(json.dumps({"outcome": "refused", "governor": {}})).count("content_trust") == 0
    ambiguous = json.loads(srv._mark_untrusted(json.dumps({"outcome": "ambiguous", "candidates": [{"goal": "a"}]})))
    assert "content_trust" in ambiguous


def test_summary_then_full_costs_one_lookup(tool):
    short = json.loads(find(detail="summary"))
    assert short["detail"] == "summary" and tool["impl"] == 1
    full = find(detail="full")                                    # served from the governor's cache of the FULL reply
    body = json.loads(full)
    assert tool["impl"] == 1 and body["procedures"][0]["steps"][0]["do"] == LONG
    assert body["governor"]["cached"] is True and "detail" not in body


def test_a_cached_reply_can_be_summarised_too(tool):
    find(detail="full")
    again = json.loads(find(detail="summary"))
    assert tool["impl"] == 1 and again["detail"] == "summary"


def test_an_unknown_detail_is_refused_before_the_governor_counts_it(tool):
    assert find(detail="tiny").startswith("REFUSED: detail must be one of")
    assert tool["impl"] == 0


def test_a_model_plan_is_attached_to_the_summary_too(tool, monkeypatch):
    from app.routing import plan
    monkeypatch.setattr(srv, "_caller_access_scope", lambda: None)

    async def fake_plan(pool, **kw):
        return {"status": "ok", "ladder": ["a|h"]}
    monkeypatch.setattr(plan, "model_plan", fake_plan)
    body = json.loads(find(detail="summary", candidates=["a|h"]))
    assert body["detail"] == "summary" and body["model_plan"]["ladder"] == ["a|h"]
