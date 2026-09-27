"""Proving tests: LLM and other blocking calls on the MCP hot path never block the event loop.

The server is one process with one event loop. A synchronous client call made directly inside an
`async def` froze every other request for the call's whole duration (1-3 s per LLM call). Each test
uses a fake whose call sleeps with `time.sleep` (a real block) and checks that concurrent callers
overlap and that an independent ticker keeps running while they wait.
"""
from __future__ import annotations

import asyncio
import json
import time

from app.db.session import _is_transaction_pooler
from app.execution import intent_resolution as ir
from app.services import embeddings as emb
from app.services.applicability_judge import JudgeCandidateInput, LLMJudge
from app.utils.aio import run_blocking

BLOCK_S = 0.3


class _Msg:
    def __init__(self, content):
        self.message = type("M", (), {"content": content})()


class _SleepyClient:
    """Sync OpenAI-shaped client whose create() blocks the calling thread."""

    def __init__(self, content):
        self._content = content
        self.calls = 0
        self.chat = type("Chat", (), {"completions": self})()

    def create(self, **kwargs):
        self.calls += 1
        time.sleep(BLOCK_S)
        return type("R", (), {"choices": [_Msg(self._content)]})()


_INTENT = json.dumps({
    "outcome": "reduce checkout latency", "object": "checkout", "action": "improve_performance",
    "constraints": [], "verification": "measure latency", "entities": [], "uncertainty": [],
    "alternative_interpretations": [],
})


async def _with_ticker(coro_factory):
    """Run the work while a ticker counts event-loop turns every 20 ms. Returns (elapsed, ticks, result)."""
    ticks = 0
    done = asyncio.Event()

    async def ticker():
        nonlocal ticks
        while not done.is_set():
            ticks += 1
            await asyncio.sleep(0.02)

    t = asyncio.create_task(ticker())
    start = time.perf_counter()
    try:
        result = await coro_factory()
    finally:
        elapsed = time.perf_counter() - start
        done.set()
        await t
    return elapsed, ticks, result


def test_normalize_intent_calls_overlap_and_do_not_block_the_loop():
    ir._INTENT_CACHE.clear()

    async def work():
        clients = [_SleepyClient(_INTENT) for _ in range(3)]   # distinct clients: no cache hits
        return await asyncio.gather(*(ir.normalize_intent("make checkout faster", client=c) for c in clients))

    elapsed, ticks, results = asyncio.run(_with_ticker(work))
    assert all(not r.used_fallback for r in results)
    assert elapsed < BLOCK_S * 3 * 0.8, f"calls ran serially ({elapsed:.2f}s)"
    assert ticks >= 5, f"event loop was blocked (ticker ran {ticks} times)"


def test_llm_judge_batch_runs_candidates_concurrently_off_loop():
    verdict = json.dumps({"verdict": "APPLICABLE", "applicability_probability": 0.9,
                          "supporting_claim_ids": [], "blocking_claim_ids": []})
    client = _SleepyClient(verdict)
    judge = LLMJudge(client=client)
    candidates = [JudgeCandidateInput(candidate_id=f"c{i}", candidate_version=1, candidate_purpose="p",
                                      conditions=[], claims=[]) for i in range(4)]

    elapsed, ticks, out = asyncio.run(_with_ticker(lambda: judge.judge_batch("goal", candidates)))
    assert [j.candidate_id for j in out] == ["c0", "c1", "c2", "c3"]      # order preserved
    assert client.calls == 4
    assert elapsed < BLOCK_S * 4 * 0.6, f"candidates judged one after another ({elapsed:.2f}s)"
    assert ticks >= 5


def test_intent_cache_reuses_successes_per_client_and_retries_fallbacks():
    ir._INTENT_CACHE.clear()
    client = _SleepyClient(_INTENT)
    first = asyncio.run(ir.normalize_intent("make checkout faster", client=client))
    second = asyncio.run(ir.normalize_intent("make checkout faster", client=client))
    assert client.calls == 1 and second.outcome == first.outcome
    second.constraints.append("mutated")                                   # callers get copies
    third = asyncio.run(ir.normalize_intent("make checkout faster", client=client))
    assert third.constraints == []

    other = _SleepyClient(_INTENT)                                          # another client: no reuse
    asyncio.run(ir.normalize_intent("make checkout faster", client=other))
    assert other.calls == 1

    bad = _SleepyClient("not json")                                         # a fallback is not cached
    r1 = asyncio.run(ir.normalize_intent("fix it", client=bad))
    asyncio.run(ir.normalize_intent("fix it", client=bad))
    assert r1.used_fallback and bad.calls == 2


def test_run_blocking_awaits_async_callables_too():
    async def async_fn(x):
        return x * 2

    assert asyncio.run(run_blocking(async_fn, 21)) == 42
    assert asyncio.run(run_blocking(lambda: "sync")) == "sync"


def test_pooler_detection_for_statement_cache():
    assert _is_transaction_pooler("postgresql://u:p@ep-x-123-pooler.c-7.us-east-2.aws.neon.tech/db")
    assert not _is_transaction_pooler("postgresql://u:p@ep-x-123.c-7.us-east-2.aws.neon.tech/db")
    assert not _is_transaction_pooler("postgresql://postgres@127.0.0.1:5432/kel")


def test_vertex_credentials_built_once_and_refreshed_only_when_expired(monkeypatch):
    import google.auth

    class _Creds:
        def __init__(self):
            self.valid = False
            self.refreshes = 0

        def refresh(self, request):
            self.refreshes += 1
            self.valid = True

    creds = _Creds()
    defaults = []
    monkeypatch.setattr(google.auth, "default", lambda scopes=None: (defaults.append(1) or creds, "proj"))
    monkeypatch.setattr(emb, "_vertex_creds", None)

    asyncio.run(emb._vertex_credentials())
    asyncio.run(emb._vertex_credentials())
    assert len(defaults) == 1 and creds.refreshes == 1
    creds.valid = False                                                     # token expired
    asyncio.run(emb._vertex_credentials())
    assert creds.refreshes == 2 and len(defaults) == 1


def test_v1_mcp_transport_is_stateless_so_any_instance_can_serve_any_request():
    """In-memory Mcp-Session-Id state pinned the server to one process and one Cloud Run instance."""
    import app.mcp_server.server as srv

    if srv.MCP_SURFACE != "v1":
        return   # v2 keeps the stateful default (TasksExtension's in-process store)
    assert srv.MCP_STATELESS is True
    assert srv.server.session_manager.stateless is True
