"""kell's rewrite of a submitted Way (app/submissions/rewrite.py): the proposal keeps the steps, checks each with intake's
rules, and only marks a step changed when it changed."""
from __future__ import annotations

import json

import pytest

from app.submissions import intake
from app.submissions.rewrite import _current, parse_proposal

SUB = {"goal_name": "ML research", "steps": [
    {"order": 1, "kind": "human", "title": "Find", "description": "Search arxiv", "status": "ai_approved"},
    {"order": 2, "kind": "automated", "title": "Sum", "prompt": "VAGUE", "locator": {"type": "url", "value": "https://a.b"},
     "status": "rejected", "reason": "say which papers"},
]}


def test_the_current_steps_carry_the_reviewers_reason_and_the_locator_as_text():
    cur = _current(SUB)
    assert cur[1]["reviewer_reason"] == "say which papers" and cur[1]["locator"] == "https://a.b"
    assert "reviewer_reason" not in cur[0]


def test_only_a_changed_step_is_marked_changed():
    text = json.dumps({"steps": [
        {"order": 1, "kind": "human", "title": "Find", "description": "Search arxiv"},
        {"order": 2, "kind": "automated", "title": "Sum", "prompt": "Summarise each paper", "locator": "https://a.b"},
    ], "note": "clearer"})
    out = parse_proposal(text, _current(SUB))
    assert out["changed"] == [2] and out["note"] == "clearer"
    assert out["steps"][1] == {"order": 2, "kind": "automated", "title": "Sum", "prompt": "Summarise each paper",
                               "locator": "https://a.b"}


def test_a_step_that_breaks_intakes_rules_is_kept_as_it_was():
    text = json.dumps({"steps": [
        {"order": 1, "kind": "human", "title": "Find", "description": ""},          # a person's step with no words
        {"order": 2, "kind": "automated", "title": "Sum", "prompt": "Summarise"},
    ]})
    out = parse_proposal(text, _current(SUB))
    assert out["changed"] == [2] and out["steps"][0]["description"] == "Search arxiv"
    assert "Step 1 kept as it was" in out["note"]


def test_a_rewrite_that_adds_or_drops_steps_is_refused():
    with pytest.raises(intake.SubmissionError):
        parse_proposal(json.dumps({"steps": [{"order": 1, "kind": "human", "description": "x"}]}), _current(SUB))
    with pytest.raises(intake.SubmissionError):
        parse_proposal("not json at all", _current(SUB))
