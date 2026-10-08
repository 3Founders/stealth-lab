"""
Offline tests for find_ways triage (app/mcp_server/find_ways_triage.py): one judgment, before any search,
decides whether a request needs a lookup. Proves the decision rule (only a confident non-task skips; every
failure runs the lookup), the JEV and OpenAI-compatible provider contracts, the chain wiring, and that
find_ways skips the whole search for a "not_needed" request -- and runs it otherwise.
"""
from __future__ import annotations

import asyncio
import json

import pytest

import app.mcp_server.server as srv
from app.mcp_server import find_ways_triage as ft
from app.services.semantic import prompts
from app.services.semantic.chain import ChainResult, SemanticJudge
from app.services.semantic.errors import ProviderError
from app.services.semantic.policy import RetryPolicy, SemanticMetrics
from app.services.semantic.providers import CAP_TRIAGE, JEVProvider, OpenAICompatProvider


def _run(coro):
    return asyncio.run(coro)


class FakeJudge:
    def __init__(self, kind=None, confidence=0.9, ok=True, delay=0.0, raises=None):
        self.kind, self.confidence, self.ok, self.delay, self.raises = kind, confidence, ok, delay, raises
        self.calls = []

    async def judge_triage(self, query):
        self.calls.append(query)
        if self.delay:
            await asyncio.sleep(self.delay)
        if self.raises:
            raise self.raises
        if not self.ok:
            return ChainResult(False, reason="all semantic providers failed")
        return ChainResult(True, {"kind": self.kind, "confidence": self.confidence}, provider="jev", model="jev-latest")


# ---- the decision rule -------------------------------------------------------------------------

@pytest.mark.parametrize("kind", ["repo_specific", "trivial_edit", "conversation", "knowledge_question"])
def test_confident_non_task_skips_the_lookup(kind):
    t = _run(ft.triage("explain what parse_args does in cli.py", FakeJudge(kind, 0.9)))
    assert t.needs_retrieval is False and t.kind == kind and t.provider == "jev"


def test_reusable_task_runs_the_lookup_at_any_confidence():
    t = _run(ft.triage("add retry with backoff to the http client", FakeJudge("reusable_task", 0.2)))
    assert t.needs_retrieval is True and t.kind == "reusable_task" and not t.reason


@pytest.mark.parametrize("judge,reason", [
    (None, "no triage judge"),
    (object(), "no triage judge"),
    (FakeJudge(ok=False), "no triage verdict"),
    (FakeJudge(raises=RuntimeError("boom")), "triage failed"),
    (FakeJudge("conversation", 0.4), "below confidence"),
])
def test_every_failure_and_doubt_runs_the_lookup(judge, reason):
    t = _run(ft.triage("set up pytest fixtures for postgres", judge, min_confidence=0.6))
    assert t.needs_retrieval is True
    assert reason in t.reason


def test_a_slow_judge_runs_the_lookup_after_the_time_limit():
    t = _run(ft.triage("set up pytest fixtures for postgres", FakeJudge("conversation", 0.99, delay=0.5), timeout_s=0.05))
    assert t.needs_retrieval is True and "over" in t.reason


def test_not_needed_reply_is_plain_json_with_next():
    body = json.loads(ft.not_needed_reply(ft.Triage(False, "repo_specific", 0.9, "jev")))
    assert body["outcome"] == "not_needed"
    assert body["triage"]["kind"] == "repo_specific"
    assert "repository" in body["next"] and "find_ways again" in body["next"]
    ran = json.loads(ft.with_triage('{"outcome": "resolved"}', ft.Triage(True, "reusable_task", 0.8, "jev")))
    assert ran["triage"]["needs_retrieval"] is True
    assert ft.with_triage("REFUSED: x", ft.Triage(True)) == "REFUSED: x"


# ---- providers and chain -----------------------------------------------------------------------

class FakeRemote:
    model = "jev-latest"

    def __init__(self, answers):
        self.answers, self.calls = answers, []

    async def systemone(self, state, questions):
        self.calls.append((state, questions))
        return self.answers


def test_jev_asks_one_typed_choice_question_over_the_triage_kinds():
    remote = FakeRemote({"kind": {"choice": "trivial_edit", "confidence": 0.85}})
    jev = JEVProvider(remote, {"applicability", "triage"})
    assert jev.supports(CAP_TRIAGE)
    assert _run(jev.triage("fix the typo in README")) == {"kind": "trivial_edit", "confidence": 0.85}
    state, questions = remote.calls[0]
    assert "fix the typo in README" in state
    assert questions["kind"]["type"] == "choice"
    assert set(questions["kind"]["criteria"]) == set(prompts.TRIAGE_KINDS)


def test_jev_without_the_capability_is_skipped_and_a_bad_reply_is_transient():
    assert not JEVProvider(FakeRemote({}), {"applicability"}).supports(CAP_TRIAGE)
    bad = JEVProvider(FakeRemote({"kind": {"choice": "maybe", "confidence": 0.5}}), {"triage"})
    with pytest.raises(ProviderError):
        _run(bad.triage("x"))


class _Msg:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()
        self.finish_reason = "stop"


class FakeOpenAI:
    def __init__(self, content):
        self.content, self.seen = content, []
        outer = self

        class _Completions:
            async def create(self, **kw):
                outer.seen.append(kw)
                return type("R", (), {"choices": [_Msg(outer.content)]})()

        self.chat = type("C", (), {"completions": _Completions()})()


def test_openai_compatible_provider_parses_the_json_verdict():
    client = FakeOpenAI('{"kind": "knowledge_question", "confidence": 0.7}')
    p = OpenAICompatProvider("gemini", [client], "gemini-x")
    assert _run(p.triage("what is a mutex")) == {"kind": "knowledge_question", "confidence": 0.7}
    assert client.seen[0]["messages"][0]["content"] == prompts.TRIAGE_SYSTEM_PROMPT


def test_chain_falls_back_from_jev_to_the_next_provider():
    failing_jev = JEVProvider(FakeRemote({"kind": {"choice": "nonsense"}}), {"triage"})
    gemini = OpenAICompatProvider("gemini", [FakeOpenAI('{"kind": "conversation", "confidence": 0.95}')], "g")

    async def no_sleep(_):
        return None

    judge = SemanticJudge([failing_jev, gemini], RetryPolicy(per_provider_attempts=1), SemanticMetrics(), sleep=no_sleep)
    r = _run(judge.judge_triage("thanks, that worked"))
    assert r.ok and r.provider == "gemini" and r.value["kind"] == "conversation" and r.fallback_used


# ---- find_ways -----------------------------------------------------------------------------------

class FakeRequestContext:
    lifespan_context = {"pool": None}


class FakeContext:
    request_context = FakeRequestContext()


@pytest.fixture
def triage_on(monkeypatch):
    monkeypatch.setattr(srv.settings, "find_ways_triage", True)
    monkeypatch.setattr(srv.settings, "find_ways_governor", False)
    recorded = []

    async def record(ctx, query, reply, shards, ms, governor=None):
        recorded.append({"reply": reply, "governor": governor})

    monkeypatch.setattr(srv, "_record_find_ways", record)
    return recorded


def _use_judge(monkeypatch, judge):
    from app.services import retrieval_service as rs

    monkeypatch.setattr(rs, "default_judge", lambda: judge)


def test_find_ways_skips_the_whole_search_for_a_not_needed_request(monkeypatch, triage_on):
    _use_judge(monkeypatch, FakeJudge("repo_specific", 0.9))

    async def forbidden(*a, **k):
        raise AssertionError("the search must not run for a not_needed request")

    monkeypatch.setattr(srv, "_find_ways_impl", forbidden)
    body = json.loads(_run(srv.find_ways("explain what the parse_args function in cli.py does", FakeContext())))
    assert body["outcome"] == "not_needed" and body["triage"]["kind"] == "repo_specific"
    assert triage_on[0]["governor"] == "triage"


def test_find_ways_runs_the_search_for_a_task_and_records_the_verdict(monkeypatch, triage_on):
    _use_judge(monkeypatch, FakeJudge("reusable_task", 0.8))
    ran = []

    async def impl(query, ctx, **kw):
        ran.append(query)
        return json.dumps({"outcome": "no_match"})

    monkeypatch.setattr(srv, "_find_ways_impl", impl)
    body = json.loads(_run(srv.find_ways("add retry with exponential backoff to the http client", FakeContext())))
    assert ran and body["outcome"] == "no_match"
    assert body["triage"] == {"needs_retrieval": True, "kind": "reusable_task", "confidence": 0.8,
                              "provider": "jev", "latency_ms": body["triage"]["latency_ms"]}


def test_find_ways_without_triage_is_unchanged(monkeypatch, triage_on):
    monkeypatch.setattr(srv.settings, "find_ways_triage", False)
    judge = FakeJudge("conversation", 0.99)
    _use_judge(monkeypatch, judge)

    async def impl(query, ctx, **kw):
        return json.dumps({"outcome": "no_match"})

    monkeypatch.setattr(srv, "_find_ways_impl", impl)
    body = json.loads(_run(srv.find_ways("add retry with exponential backoff to the http client", FakeContext())))
    assert "triage" not in body and judge.calls == []
