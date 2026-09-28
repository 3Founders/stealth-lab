"""Offline: JSON objects are recovered from wrapped LLM replies (BLOCKERS.md I4), in the shared helper and in
both skill extractors; truncated or non-object replies are still failures."""
from __future__ import annotations

import pytest

from app.services.llm_json import parse_json_object
from app.services.skill_extraction import grounded, ungrounded

OBJ = '{"abstain": false, "x": 1}'


@pytest.mark.parametrize("reply", [
    OBJ,
    f"```json\n{OBJ}\n```",
    f"```JSON\n{OBJ}\n```",
    f"```\n{OBJ}\n```",
    f"Here is the JSON:\n```json\n{OBJ}\n```",
    f"```json\n{OBJ}\n```\nLet me know if you need more.",
    f"Sure! {OBJ} Hope that helps.",
    f"```json\n{OBJ}",                       # opening fence, no closing fence
    f"```json\n[1, 2]\n```\n```json\n{OBJ}\n```",   # first fenced block is not an object
])
def test_wrapped_objects_are_recovered(reply):
    assert parse_json_object(reply) == {"abstain": False, "x": 1}


@pytest.mark.parametrize("reply", [
    "", None, "no json here", "[1, 2, 3]", '"a string"',
    '{"x": 1, "y": [1, 2',                    # truncated: never repaired
    '```json\n{"x": 1, "y": [1, 2\n```',
])
def test_failures_stay_failures(reply):
    assert parse_json_object(reply) is None


@pytest.mark.parametrize("module", [grounded, ungrounded])
def test_extractors_accept_prose_wrapped_abstain(module):
    assert module._parse_response('Here you go:\n```json\n{"abstain": true}\n```') is module._ABSTAIN
    assert module._parse_response('I cannot extract this. {"abstain": true}') is module._ABSTAIN


@pytest.mark.parametrize("module", [grounded, ungrounded])
def test_extractors_still_reject_garbage_and_wrong_shapes(module):
    assert module._parse_response("the model rambled") is None
    assert module._parse_response("[1, 2]") is None
    # parses as JSON, but fails the response schema (procedures must be a list): still a parse failure
    assert module._parse_response('Result:\n```json\n{"procedures": "not a list"}\n```') is None
