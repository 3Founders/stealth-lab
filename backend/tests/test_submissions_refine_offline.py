"""Improve / specialize / abstract a Way (app/submissions/refine.py): one model call, and the submission rules decide
whether the rewrite is usable. The model itself is faked here; what is under test is the prompt we send, the shape we
return, and the refusals (an unknown mode, an invalid rewrite, no model answering)."""
from __future__ import annotations

import asyncio
import json

import pytest

from app.submissions import intake, refine, review

DRAFT = {
    "goal": "Research ML",
    "description": "Look at papers.",
    "steps": [
        {"kind": "human", "title": "Find", "description": "Search arxiv for diffusion transformers and copy 5 links"},
        {"kind": "automated", "title": "Summarise", "prompt": "Summarise each link in two lines"},
    ],
}


def run(coro):
    return asyncio.run(coro)


def fake_complete(answer, seen=None):
    async def complete(messages, **kw):
        if seen is not None:
            seen.append((messages, kw))
        text = answer if isinstance(answer, str) else json.dumps(answer)
        return text, "deepseek-v4.1-flash"
    return complete


def test_specialize_rewrites_the_goal_and_every_step(monkeypatch):
    seen = []
    answer = {
        "goal": "Research diffusion transformers in PyTorch repositories",
        "description": "Find recent papers and summarise them for one repository.",
        "steps": [
            {"kind": "human", "title": "Find", "description": "Search arxiv for diffusion transformers and copy 5 links"},
            {"kind": "automated", "title": "Summarise", "prompt": "Summarise each link in two lines",
             "locator": "https://arxiv.org/list/cs.LG/recent"},
        ],
    }
    monkeypatch.setattr(review, "complete", fake_complete(answer, seen))
    out = run(refine.refine(DRAFT, mode="specialize", instruction="focus on PyTorch"))

    assert out["goal"] == "Research diffusion transformers in PyTorch repositories"
    assert out["mode"] == "specialize" and out["mode_label"] == "Specialized"
    assert out["model"] == "deepseek-v4.1-flash"
    assert [s["kind"] for s in out["steps"]] == ["human", "automated"]
    # A locator comes back classified, the way the form and the submit route expect it.
    assert out["steps"][1]["locator"] == {"type": "url", "value": "https://arxiv.org/list/cs.LG/recent"}
    assert "prompt" not in out["steps"][0] and "description" in out["steps"][0]

    messages, kw = seen[0]
    assert "specialize" in messages[0]["content"]
    assert "Research ML" in messages[1]["content"] and "focus on PyTorch" in messages[1]["content"]
    assert kw["json_mode"] is True


def test_a_rewrite_that_breaks_a_submission_rule_is_refused(monkeypatch):
    bad = {"goal": "X", "description": "", "steps": [
        {"kind": "human", "title": "Do", "description": "read the file", "prompt": "and think about it"},
    ]}
    monkeypatch.setattr(review, "complete", fake_complete(bad))
    with pytest.raises(intake.SubmissionError) as err:
        run(refine.refine(DRAFT, mode="improve"))
    assert "nothing changed" in str(err.value)


def test_an_unknown_mode_is_refused_before_any_model_call(monkeypatch):
    async def never(*a, **k):
        raise AssertionError("the model must not be called for an unknown mode")
    monkeypatch.setattr(review, "complete", never)
    with pytest.raises(intake.SubmissionError) as err:
        run(refine.refine(DRAFT, mode="shrink"))
    assert "improve" in str(err.value)


def test_the_draft_is_validated_before_the_call(monkeypatch):
    async def never(*a, **k):
        raise AssertionError("the model must not be called for an invalid draft")
    monkeypatch.setattr(review, "complete", never)
    with pytest.raises(intake.SubmissionError):
        run(refine.refine({"goal": "", "steps": DRAFT["steps"]}, mode="improve"))


def test_no_model_answering_is_one_sentence(monkeypatch):
    async def down(*a, **k):
        raise RuntimeError("DEEPINFRA_KEY is not set")
    monkeypatch.setattr(review, "complete", down)
    with pytest.raises(intake.SubmissionError) as err:
        run(refine.refine(DRAFT, mode="abstract"))
    assert "try again" in str(err.value)


def test_the_refine_route_is_registered_before_the_submission_id_route():
    """`/v1/goals/submissions/{submission_id}` would swallow `/refine` as an id; order is the fix, so it is asserted."""
    from app.mcp_server import submission_routes

    class FakeServer:
        def __init__(self):
            self.paths: list[str] = []

        def custom_route(self, path, **_kw):
            def decorate(fn):
                self.paths.append(path)
                return fn
            return decorate

    server = FakeServer()
    submission_routes.register(server, gate=object(), pool_getter=lambda: None)
    assert "/v1/goals/submissions/refine" in server.paths
    assert server.paths.index("/v1/goals/submissions/refine") < server.paths.index(
        "/v1/goals/submissions/{submission_id}")
