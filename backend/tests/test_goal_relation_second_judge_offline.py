"""Offline: the second, independent judgment for low-confidence Goal hierarchy placements."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services import goal_relation_second_judge as sj


@pytest.mark.parametrize("first,relation,conf,expected", [
    ("specializes", "specializes", 0.9, "accept"),
    ("specializes", "specializes", 0.6, "review"),
    ("specializes", "generalizes", 0.95, "review"),
    ("specializes", "unrelated", 0.85, "reject"),
    ("generalizes", "overlapping", 0.9, "reject"),
    ("specializes", "same", 0.99, "review"),
    ("specializes", None, None, "review"),
])
def test_decision_rule(first, relation, conf, expected):
    assert sj.decide(first, relation, conf) == expected


def _client(reply=None, raises=None):
    class Completions:
        def __init__(self):
            self.seen = None

        def create(self, **kw):
            self.seen = kw
            if raises:
                raise raises
            return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=reply))], usage=None)
    return SimpleNamespace(chat=SimpleNamespace(completions=Completions()))


A = {"canonical_name": "Fix nested forbidden keys detection in translation files"}
B = {"canonical_name": "Validate translation dictionaries"}


def _ask(client):
    return asyncio.run(sj.second_opinion(A, B, first_relation="specializes", client=client, model="m"))


def test_agreement_accepts_and_the_first_verdict_is_not_shown():
    client = _client(json.dumps({"relation": "specializes", "confidence": 0.9, "reason": "narrower case"}))
    got = _ask(client)
    assert got.decision == "accept" and got.relation == "specializes"
    prompt = json.dumps(client.chat.completions.seen["messages"])
    assert "0.9" not in prompt.split("A:")[-1] and "specializes" not in prompt.split("A:")[-1]


def test_an_unusable_or_unavailable_judge_is_human_review(monkeypatch):
    assert _ask(_client("not json")).decision == "review"
    assert _ask(_client(json.dumps({"relation": "kinda", "confidence": 0.9}))).decision == "review"
    assert _ask(_client(raises=RuntimeError("503"))).decision == "review"
    monkeypatch.setattr(sj, "_client", lambda: None)          # no provider configured: never a network call
    got = asyncio.run(sj.second_opinion(A, B, first_relation="specializes", client=None, model="m"))
    assert got.decision == "review" and got.reason == "second judge not configured"
