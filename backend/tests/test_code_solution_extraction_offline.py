"""CodeSolutionExtractor (procedure_extraction/strategies.py): Procedures from a VERIFIED
code solution -- grounded steps (every cited call occurs in the code), pitfalls kept,
code never stored, abstain honoured, invented calls refused."""
from __future__ import annotations

import asyncio
import json
from types import SimpleNamespace

import pytest

from app.services.procedure_extraction.evidence import ProcedureEvidence
from app.services.procedure_extraction.schema import ExtractionTransientFailure
from app.services.procedure_extraction.strategies import (
    _ABSTAIN,
    CodeSolutionExtractor,
    parse_code_solution_response,
    verified_code_solution,
)

CODE = """import re
from collections import Counter

def task_func(text, n):
    text = re.sub(r"https?://\\S+", "", text)
    words = re.findall(r"\\b\\w+\\b", text)
    return Counter(words).most_common(n)
"""


def _evidence(code: str = CODE, verified: bool = True) -> ProcedureEvidence:
    return ProcedureEvidence(
        goal_text="Count the most frequent words in a text after removing URLs", outcome="success",
        observations=[
            {"observation_type": "task_statement", "label": "task", "properties": {"text": "Count words..."}},
            {"observation_type": "code_solution", "label": "solution",
             "properties": {"code": code, "verified": verified}},
        ],
        tool_sequence=["write_code", "run_tests"])


class _Client:
    def __init__(self, reply: str):
        self.reply = reply
        self.chat = SimpleNamespace(completions=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.kwargs = kwargs
        return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=self.reply))])


GOOD = json.dumps({
    "capability_statement": "Count the most frequent words in text after stripping URLs.",
    "steps": [{"action": "Remove URLs with a regular expression", "apis": ["re.sub"]},
              {"action": "Tokenize into words", "apis": ["re.findall"]},
              {"action": "Count and take the top n", "apis": ["collections.Counter", "Counter.most_common",
                                                              "numpy.unique"]}],
    "pitfalls": ["Return a list of (word, count) tuples"],
})


def test_steps_are_grounded_in_the_verified_code_and_pitfalls_kept():
    parsed = parse_code_solution_response(GOOD, CODE)
    statement, steps, pitfalls, dropped = parsed
    assert statement.startswith("Count the most frequent")
    assert steps[2][1] == ["collections.Counter", "Counter.most_common"] and dropped == ["numpy.unique"]
    assert pitfalls == ["Return a list of (word, count) tuples"]


def test_abstain_and_mostly_invented_calls_are_refused():
    assert parse_code_solution_response('{"abstain": true}', CODE) is _ABSTAIN
    invented = json.dumps({"capability_statement": "x", "steps": [
        {"action": "a", "apis": ["pandas.read_csv", "numpy.argsort"]}, {"action": "b", "apis": ["re.sub"]}]})
    assert parse_code_solution_response(invented, CODE) is None          # 2 of 3 cited calls never occur
    assert parse_code_solution_response("not json", CODE) is None


def test_extractor_builds_a_procedure_without_storing_code():
    client = _Client(GOOD)
    proc = asyncio.run(CodeSolutionExtractor(client).extract(None, _evidence()))
    assert [s.action for s in proc.steps][0] == "Remove URLs with a regular expression"
    assert proc.steps[0].allowed_implementations == [{"type": "library_call", "name": "re.sub"}]
    assert proc.failure_conditions == ["Return a list of (word, count) tuples"]
    assert "Counter(words)" not in proc.model_dump_json()                 # the solution's code is not in it
    assert "Verified solution" in client.kwargs["messages"][1]["content"]


def test_only_verified_solutions_count_and_bad_replies_surface():
    assert verified_code_solution(_evidence(verified=False)) is None
    assert verified_code_solution(_evidence()) is not None
    assert asyncio.run(CodeSolutionExtractor(_Client(GOOD)).extract(None, _evidence(verified=False))) is None
    with pytest.raises(ExtractionTransientFailure):
        asyncio.run(CodeSolutionExtractor(_Client("garbage")).extract(None, _evidence()))
