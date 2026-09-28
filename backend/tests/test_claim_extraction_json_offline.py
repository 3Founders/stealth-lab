"""Proving test: the claim extractor accepts the JSON shapes providers actually return.

Bare json.loads rejected a ```json-fenced reply, so every such chunk silently produced no claims.
"""
import json

import pytest

from app.services.claim_extraction import _parse_json_object

PAYLOAD = {"claims": [{"statement": "Tests run with pytest"}]}


@pytest.mark.parametrize("text", [
    json.dumps(PAYLOAD),
    "```json\n" + json.dumps(PAYLOAD) + "\n```",
    "```\n" + json.dumps(PAYLOAD) + "\n```",
    "Here are the claims:\n" + json.dumps(PAYLOAD) + "\nDone.",
])
def test_accepts_plain_fenced_and_wrapped_json(text):
    assert _parse_json_object(text) == PAYLOAD


@pytest.mark.parametrize("text", ["", "no json here", '{"claims": [', "```json\n[1, 2]\n```"])
def test_still_rejects_what_is_not_a_json_object(text):
    with pytest.raises((json.JSONDecodeError, ValueError)):
        _parse_json_object(text)
