"""Offline tests for the latency fixes of 2026-10-02:
  * one TLS context per process (app/utils/tls.py) and one keep-alive client per event loop for the remote judge;
  * a provider that refuses at the account level (401/402/403) is skipped for a while instead of being tried first
    on every judgment.
"""
from __future__ import annotations

import asyncio

import pytest

from app.services.semantic import chain as ch
from app.services.semantic.errors import ErrorKind, ProviderError


def _run(coro):
    return asyncio.run(coro)


def test_one_tls_context_per_process():
    from app.utils.tls import async_http_client, shared_ssl_context

    assert shared_ssl_context() is shared_ssl_context()

    async def build():
        a, b = async_http_client(timeout=5), async_http_client(timeout=5)
        try:
            return a, b
        finally:
            await a.aclose()
            await b.aclose()

    a, b = _run(build())
    assert a._transport._pool._ssl_context is shared_ssl_context() is b._transport._pool._ssl_context


def test_the_remote_judge_reuses_one_client_per_loop():
    from app.services import applicability_judge as aj

    async def twice():
        return await aj._shared_client(5.0), await aj._shared_client(5.0)

    a, b = _run(twice())
    assert a is b


class _P:
    def __init__(self, name, *, fail=None):
        self.name, self.model, self.fail, self.calls = name, "m", fail, 0

    def supports(self, cap):
        return True

    async def identity(self, kind, a, b):
        self.calls += 1
        if self.fail:
            raise self.fail
        return {"relation": "distinct"}


def _judge(*providers, clock):
    from app.services.semantic.policy import RetryPolicy

    async def no_sleep(_):
        return None

    return ch.SemanticJudge(list(providers), RetryPolicy(), sleep=no_sleep, monotonic=lambda: clock[0])


@pytest.fixture(autouse=True)
def _clear():
    ch._SUSPENDED.clear()
    yield
    ch._SUSPENDED.clear()


def test_an_account_refusal_suspends_the_provider_then_it_is_retried():
    clock = [1000.0]
    broke = _P("jev", fail=ProviderError(ErrorKind.PERMANENT, "HTTPStatusError: 402 Payment Required", provider="jev"))
    good = _P("gemma")
    judge = _judge(broke, good, clock=clock)
    for _ in range(5):
        assert _run(judge.judge_identity("goal", "a", "b")).provider == "gemma"
    assert broke.calls == 1 and good.calls == 5          # tried once, then skipped
    clock[0] += ch.SUSPEND_S + 1
    _run(judge.judge_identity("goal", "a", "b"))
    assert broke.calls == 2                              # tried again after the suspension


def test_other_permanent_errors_do_not_suspend_and_a_lone_provider_is_still_tried():
    clock = [0.0]
    bad_request = _P("jev", fail=ProviderError(ErrorKind.PERMANENT, "400 bad request", provider="jev"))
    good = _P("gemma")
    judge = _judge(bad_request, good, clock=clock)
    _run(judge.judge_identity("goal", "a", "b"))
    _run(judge.judge_identity("goal", "a", "b"))
    assert bad_request.calls == 2                        # a request-level error is not an account problem

    ch._SUSPENDED.clear()
    only = _P("jev", fail=ProviderError(ErrorKind.PERMANENT, "402 Payment Required", provider="jev"))
    judge = _judge(only, clock=clock)
    _run(judge.judge_identity("goal", "a", "b"))
    _run(judge.judge_identity("goal", "a", "b"))
    assert only.calls == 2                               # never skipped when nothing else is left
