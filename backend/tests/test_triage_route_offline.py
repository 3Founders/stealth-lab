"""
Offline tests for the hook's one-round-trip triage (`POST /triage`, app/mcp_server/server.py) and the memo
that keeps find_ways from judging the same text twice (app/mcp_server/find_ways_triage.py).

The route exists so a Claude Code / Cursor hook can learn "this prompt needs no lookup" without the whole MCP
handshake. What these prove: only a confident non-task says `needs_retrieval: false`; every other outcome
(no judge, a failing judge, a timeout, a rate limit, a disabled setting) says `true`; the route is gated like
the other data routes; a verdict is remembered only when a judge really gave one; and the `find_ways` call
that follows a "yes" reuses the verdict instead of paying for a second judgment.
"""
from __future__ import annotations

import asyncio
import os

import httpx
import pytest

import app.mcp_server.server as srv
from app.mcp_server import find_ways_triage as ft
from app.services.semantic.chain import ChainResult


def _run(coro):
    return asyncio.run(coro)


class FakeJudge:
    def __init__(self, kind="conversation", confidence=0.9, ok=True, raises=None):
        self.kind, self.confidence, self.ok, self.raises = kind, confidence, ok, raises
        self.calls = []

    async def judge_triage(self, query):
        self.calls.append(query)
        if self.raises:
            raise self.raises
        if not self.ok:
            return ChainResult(False, reason="all semantic providers failed")
        return ChainResult(True, {"kind": self.kind, "confidence": self.confidence}, provider="jev", model="jev-latest")


@pytest.fixture(autouse=True)
def _fresh(monkeypatch):
    """Each test starts with an empty memo, an empty window and triage on."""
    monkeypatch.setattr(ft, "CACHE", ft.TriageCache())
    monkeypatch.setattr(ft, "WINDOW", ft.CallWindow(30, 60.0))
    monkeypatch.setattr(srv.settings, "find_ways_triage", True, raising=False)
    monkeypatch.setattr(srv.settings, "deployment_mode", "single_user", raising=False)


def _use_judge(monkeypatch, judge):
    monkeypatch.setattr("app.services.retrieval_service.default_judge", lambda: judge)


def _no_anonymous_reads_app():
    """The same server routes WITHOUT the anonymous-read injector -- how a deployment with Supabase sign-in
    (anonymous reads off) serves them. `srv.app` itself is built with the injector when no Supabase is configured."""
    return srv.server.streamable_http_app(transport_security=srv._transport_security(), stateless_http=srv.MCP_STATELESS)


def _post(body=None, *, headers=None, host="127.0.0.1", raw=None, app=None):
    async def go():
        transport = httpx.ASGITransport(app=app or srv.app, client=(host, 5000))
        async with httpx.AsyncClient(transport=transport, base_url="http://mcp") as client:
            if raw is not None:
                return await client.post("/triage", content=raw, headers=headers or {})
            return await client.post("/triage", json=body, headers=headers or {})
    return _run(go())


# ---- the decision, over HTTP -------------------------------------------------------------------

def test_a_confident_non_task_says_no_lookup_needed(monkeypatch):
    judge = FakeJudge("trivial_edit", 0.95)
    _use_judge(monkeypatch, judge)
    resp = _post({"query": "rename foo to bar in utils.py"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["needs_retrieval"] is False and body["kind"] == "trivial_edit" and body["provider"] == "jev"
    assert judge.calls == ["rename foo to bar in utils.py"]


def test_a_reusable_task_says_look_it_up(monkeypatch):
    _use_judge(monkeypatch, FakeJudge("reusable_task", 0.4))
    body = _post({"query": "add retry with backoff to the http client"}).json()
    assert body["needs_retrieval"] is True and body["kind"] == "reusable_task"


@pytest.mark.parametrize("judge,why", [
    (None, "no triage judge"),
    (FakeJudge(ok=False), "no triage verdict"),
    (FakeJudge(raises=RuntimeError("boom")), "triage failed"),
    (FakeJudge("conversation", 0.3), "below confidence"),
])
def test_every_failure_or_doubt_says_look_it_up(monkeypatch, judge, why):
    _use_judge(monkeypatch, judge)
    body = _post({"query": "thanks, that looks right to me"}).json()
    assert body["needs_retrieval"] is True and why in body["reason"]


def test_a_disabled_setting_says_look_it_up(monkeypatch):
    monkeypatch.setattr(srv.settings, "find_ways_triage", False, raising=False)
    judge = FakeJudge("conversation", 0.99)
    _use_judge(monkeypatch, judge)
    body = _post({"query": "hello there, how are you"}).json()
    assert body["needs_retrieval"] is True and judge.calls == []


# ---- input and gating ---------------------------------------------------------------------------

@pytest.mark.parametrize("body", [{}, {"query": ""}, {"query": "   "}, {"query": 5}, {"q": "x"}, ["x"]])
def test_a_malformed_body_is_a_400(body):
    assert _post(body).status_code == 400


def test_a_body_that_is_not_json_is_a_400():
    assert _post(raw=b"not json", headers={"content-type": "application/json"}).status_code == 400


def test_the_query_is_cut_to_the_hook_limit(monkeypatch):
    judge = FakeJudge("conversation", 0.9)
    _use_judge(monkeypatch, judge)
    _post({"query": "x" * 5000})
    assert len(judge.calls[0]) == ft.QUERY_MAX


def test_a_remote_caller_without_credentials_is_refused(monkeypatch):
    judge = FakeJudge("conversation", 0.9)
    _use_judge(monkeypatch, judge)
    resp = _post({"query": "hello there, how are you"}, host="203.0.113.7", app=_no_anonymous_reads_app())
    assert resp.status_code == 401 and "www-authenticate" in resp.headers and judge.calls == []


def test_shared_deployments_never_get_the_loopback_pass(monkeypatch):
    monkeypatch.setattr(srv.settings, "deployment_mode", "shared", raising=False)
    judge = FakeJudge("conversation", 0.9)
    _use_judge(monkeypatch, judge)
    assert _post({"query": "hello there, how are you"}, app=_no_anonymous_reads_app()).status_code == 401
    assert judge.calls == []


def test_a_valid_bearer_is_served_from_anywhere(monkeypatch):
    _use_judge(monkeypatch, FakeJudge("conversation", 0.9))
    token = os.environ["STEALTHLAB_MCP_TOKEN"]
    resp = _post({"query": "hello there, how are you"}, host="203.0.113.7", app=_no_anonymous_reads_app(),
                 headers={"authorization": f"Bearer {token}"})
    assert resp.status_code == 200 and resp.json()["needs_retrieval"] is False


def test_the_anonymous_read_posture_is_served_like_find_ways(monkeypatch):
    """Free reads: with anonymous reads on (no Supabase sign-in configured) a token-less caller is served, as for
    find_ways itself -- the per-caller rate limit is what bounds the spend."""
    _use_judge(monkeypatch, FakeJudge("conversation", 0.9))
    resp = _post({"query": "hello there, how are you"}, host="203.0.113.7")
    assert resp.status_code == 200 and resp.json()["needs_retrieval"] is False


def test_the_rate_limit_fails_toward_the_lookup(monkeypatch):
    monkeypatch.setattr(ft, "WINDOW", ft.CallWindow(2, 60.0))
    judge = FakeJudge("conversation", 0.9)
    _use_judge(monkeypatch, judge)
    results = [_post({"query": f"hello there number {i}"}).json() for i in range(3)]
    assert [r["needs_retrieval"] for r in results] == [False, False, True]
    assert "rate limit" in results[2]["reason"] and len(judge.calls) == 2


# ---- the memo -----------------------------------------------------------------------------------

def test_find_ways_reuses_the_verdict_the_hook_just_paid_for(monkeypatch):
    judge = FakeJudge("reusable_task", 0.9)
    _use_judge(monkeypatch, judge)
    _post({"query": "add retry with backoff to the http client"})
    again = _run(srv._find_ways_triage("add retry with backoff to the http client "))   # trailing space: same text
    assert again is not None and again.kind == "reusable_task" and len(judge.calls) == 1


def test_a_failed_judgment_is_never_remembered(monkeypatch):
    monkeypatch.setattr(ft, "CACHE", ft.TriageCache())
    _use_judge(monkeypatch, FakeJudge(ok=False))
    _post({"query": "fix the flaky test in the billing module"})
    good = FakeJudge("reusable_task", 0.9)
    _use_judge(monkeypatch, good)
    again = _run(srv._find_ways_triage("fix the flaky test in the billing module"))
    assert again.kind == "reusable_task" and len(good.calls) == 1      # the recovered judge was asked


def test_memo_entries_expire_and_the_memo_is_bounded():
    now = [0.0]
    cache = ft.TriageCache(ttl_s=10, max_entries=2, clock=lambda: now[0])
    t = ft.Triage(True, "reusable_task", 0.9, "jev")
    cache.put("a", t)
    assert cache.get("a") is not None
    now[0] = 11
    assert cache.get("a") is None                                       # expired
    now[0] = 12
    for q in ("b", "c", "d"):
        cache.put(q, t)
    assert cache.get("b") is None and cache.get("c") is not None        # oldest evicted past max_entries
    cache.put("e", ft.Triage(True, reason="no triage judge configured"))  # no judgment (kind None): not kept
    assert cache.get("e") is None


def test_the_window_forgets_old_calls():
    now = [0.0]
    window = ft.CallWindow(2, 60.0, clock=lambda: now[0])
    assert window.allow("u") and window.allow("u") and not window.allow("u")
    assert window.allow("other")                                        # per caller
    now[0] = 61
    assert window.allow("u")
