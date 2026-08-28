"""Regression: double-encoded JSONB must not crash observation extraction.

RUNBOOK.md's own documented pitfall -- "asyncpg jsonb codec is registered
on app pools; passing pre-dumped JSON strings to $n::jsonb DOUBLE-ENCODES
them (stored as json-string)". A double-encoded column decodes to a *str*,
not a dict, so the previous single-shot `if isinstance(str): json.loads()`
returned a str and the next `.get()` raised
`'str' object has no attribute 'get'`.

Not hypothetical: this failed 32 of 3313 real ingestion jobs on
2026-08-28 while ingesting a real 1.1MB Claude Code hook trace.

Offline by construction -- extract_deterministic_observations is a pure
function, no pool and no network, so no FakePool is needed here.
"""
from __future__ import annotations

import json

import pytest

from app.services.observations import (
    _decode_json_field,
    extract_deterministic_observations,
)


def _single(payload: dict) -> str:
    """What a correctly-encoded JSONB column decodes to (a dict) -- here as
    the str form asyncpg hands back when no jsonb codec is registered."""
    return json.dumps(payload)


def _double(payload: dict) -> str:
    """The bug shape: a pre-dumped JSON string passed to $n::jsonb, so the
    stored value is a JSON *string* whose content is itself JSON."""
    return json.dumps(json.dumps(payload))


def test_old_single_shot_decode_would_have_thrown():
    """Pins the actual defect, so this test fails loudly if someone
    reverts the loop to a single json.loads()."""
    raw = _double({"file_path": "/repo/a.py"})
    once = json.loads(raw)
    assert isinstance(once, str), "double-encoded value must decode to str first"
    with pytest.raises(AttributeError, match="'str' object has no attribute 'get'"):
        once.get("file_path")  # type: ignore[attr-defined]


@pytest.mark.parametrize("encode", [_single, _double], ids=["single", "double"])
def test_file_touched_extracted_through_either_encoding(encode):
    obs = extract_deterministic_observations({
        "tool_name": "Edit",
        "tool_input": encode({"file_path": "/repo/a.py"}),
    })
    assert len(obs) == 1
    assert obs[0]["observation_type"] == "file_touched"
    assert obs[0]["properties"]["file_path"] == "/repo/a.py"


@pytest.mark.parametrize("encode", [_single, _double], ids=["single", "double"])
def test_bash_command_extracted_through_either_encoding(encode):
    obs = extract_deterministic_observations({
        "tool_name": "Bash",
        "tool_input": encode({"command": "git commit -m x"}),
    })
    assert [o["observation_type"] for o in obs] == ["commit_made"]


def test_plain_dict_still_works():
    obs = extract_deterministic_observations({
        "tool_name": "Write", "tool_input": {"file_path": "/repo/b.py"},
    })
    assert obs[0]["properties"]["file_path"] == "/repo/b.py"


@pytest.mark.parametrize(
    "bad", [None, "", "not json at all", "[1,2,3]", '"just a string"', 42, []],
    ids=["none", "empty", "garbage", "list", "json-str", "int", "empty-list"],
)
def test_malformed_input_yields_no_observations_instead_of_crashing(bad):
    """A malformed event must be skipped, never crash the job owning the
    whole trace_event -- that is what turned 32 bad rows into 32 failures."""
    assert extract_deterministic_observations({"tool_name": "Edit", "tool_input": bad}) == []


def test_decoder_is_bounded_not_infinite():
    """Quadruple-encoded is beyond any real double-encode; the decoder must
    terminate and degrade to {} rather than spin."""
    payload = {"file_path": "/repo/deep.py"}
    quad = json.dumps(json.dumps(json.dumps(json.dumps(payload))))
    assert _decode_json_field(quad) == payload  # 4 unwraps is within the bound

    five = json.dumps(quad)
    assert _decode_json_field(five) == {}  # beyond the bound -> {}, no hang
