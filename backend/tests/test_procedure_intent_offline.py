"""The procedure-intent gate: only "procedure" opens the picker, and every failure opens it (fail toward the search).

Runs offline with a fake judge; no provider is called.
"""
import asyncio

import pytest

from app.mcp_server.procedure_intent import IntentCache, ProcedureIntent, procedure_intent
from app.services.semantic.chain import ChainResult


class Judge:
    def __init__(self, intent="procedure", confidence=1.0, ok=True, error=None):
        self.intent, self.confidence, self.ok, self.error = intent, confidence, ok, error
        self.calls = 0

    async def judge_procedure(self, query, context=""):
        self.calls += 1
        if self.error:
            raise self.error
        if not self.ok:
            return ChainResult(ok=False, reason="every provider failed")
        return ChainResult(ok=True, value={"intent": self.intent, "confidence": self.confidence}, provider="jev")


def run(coro):
    return asyncio.run(coro)


def test_a_procedure_ask_opens_the_picker():
    verdict = run(procedure_intent("find procedures for research", Judge("procedure", 1.0)))
    assert verdict.search is True
    assert verdict.intent == "procedure"
    assert verdict.provider == "jev"


@pytest.mark.parametrize("query", ["fix the failing test", "add a test for the parser", "what does this function do"])
def test_ordinary_work_skips_the_picker(query):
    verdict = run(procedure_intent(query, Judge("ordinary", 0.95)))
    assert verdict.search is False
    assert verdict.intent == "ordinary"


def test_a_low_confidence_ordinary_verdict_still_opens_the_picker():
    verdict = run(procedure_intent("tidy this up", Judge("ordinary", 0.4)))
    assert verdict.search is True
    assert "below confidence" in verdict.reason


def test_no_judge_opens_the_picker():
    verdict = run(procedure_intent("anything", None))
    assert verdict.search is True
    assert "no procedure judge" in verdict.reason


def test_a_failed_judgment_opens_the_picker():
    verdict = run(procedure_intent("anything", Judge(error=RuntimeError("boom"))))
    assert verdict.search is True
    assert "failed" in verdict.reason


def test_a_judge_without_the_operation_opens_the_picker():
    class Old:
        pass

    verdict = run(procedure_intent("anything", Old()))
    assert verdict.search is True


def test_the_cache_keeps_only_real_verdicts():
    cache = IntentCache(ttl_s=60.0, clock=lambda: 100.0)
    hit = ProcedureIntent(False, "ordinary", 0.9, "jev")
    cache.put("fix it", hit)
    assert cache.get("fix it") == hit
    cache.put("unknown", ProcedureIntent(True, reason="no judge"))
    assert cache.get("unknown") is None          # a failure is never cached
    assert cache.get("fix it", "later turns") is None
