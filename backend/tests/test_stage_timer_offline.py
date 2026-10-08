"""
Offline tests for per-request stage timing (app/utils/stage_timer.py) and the report built on it.

What has to hold, because the latency work that follows decides what to change from these numbers:
  * a stage is timed even when its block raises, and costs nothing when no request is being recorded;
  * concurrent calls and tasks started by gather write into the request's recorder, while two requests running
    at the same time never see each other's stages;
  * `find_ways` writes the breakdown into its `retrieval_decisions` row (the one place it is read from), on the
    answered path and on the "not needed" path, and never stores request text;
  * the report's percentiles and shares are computed the way they claim to be.
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import sys
import time
from types import SimpleNamespace

import pytest

import app.mcp_server.server as srv
from app.mcp_server import find_ways_triage as ft
from app.services.semantic.chain import ChainResult
from app.utils import stage_timer as st


def _run(coro):
    return asyncio.run(coro)


# ---- the timer ------------------------------------------------------------------------------------------

def test_stage_records_calls_sum_and_longest():
    async def go():
        rec = st.begin()
        for ms in (0.02, 0.05):
            with st.stage("work"):
                await asyncio.sleep(ms)
        st.add("measured_elsewhere", 12.34)
        return rec.as_dict()

    out = _run(go())
    assert out["work"]["n"] == 2 and out["work"]["ms"] >= 60 and out["work"]["max_ms"] >= 45
    assert out["measured_elsewhere"] == {"ms": 12.3, "n": 1, "max_ms": 12.3}


def test_a_block_that_raises_is_still_timed():
    async def go():
        rec = st.begin()
        with pytest.raises(RuntimeError):
            with st.stage("boom"):
                await asyncio.sleep(0.01)
                raise RuntimeError("x")
        return rec.as_dict()

    assert _run(go())["boom"]["n"] == 1


def test_it_is_a_no_op_without_a_recorder():
    async def go():
        # a fresh context: nothing begun
        with st.stage("ignored"):
            await asyncio.sleep(0)
        st.add("ignored", 5)
        return st.snapshot(), st.current()

    assert _run(go()) == ({}, None)


def test_gather_and_threads_write_into_the_same_recorder():
    async def one(i):
        with st.stage("parallel"):
            await asyncio.sleep(0.05)

    async def in_thread():
        def work():
            with st.stage("threaded"):
                time.sleep(0.01)
        await asyncio.to_thread(work)

    async def go():
        rec = st.begin()
        t0 = time.perf_counter()
        await asyncio.gather(*[one(i) for i in range(4)], in_thread())
        return rec.as_dict(), (time.perf_counter() - t0) * 1000

    out, wall = _run(go())
    assert out["parallel"]["n"] == 4 and out["threaded"]["n"] == 1
    assert out["parallel"]["ms"] > wall                  # four overlapping 50 ms calls: the sum exceeds the wall time


def test_two_requests_at_once_never_mix():
    async def request(name, delay):
        rec = st.begin()
        with st.stage(name):
            await asyncio.sleep(delay)
        await asyncio.sleep(0.01)
        return set(rec.as_dict())

    async def go():
        return await asyncio.gather(request("a", 0.03), request("b", 0.01))

    assert _run(go()) == [{"a"}, {"b"}]


# ---- find_ways writes the breakdown into its durable record ---------------------------------------------

class Ctx:
    request_context = SimpleNamespace(lifespan_context={"pool": None})


class Judge:
    def __init__(self, kind, delay=0.0):
        self.kind, self.delay = kind, delay

    async def judge_triage(self, query):
        await asyncio.sleep(self.delay)
        return ChainResult(True, {"kind": self.kind, "confidence": 0.95}, provider="jev", model="jev-latest")


@pytest.fixture
def written(monkeypatch):
    """Triage on, governor off, and the real `_record_find_ways` writing to a fake log pool."""
    monkeypatch.setattr(srv.settings, "find_ways_triage", True)
    monkeypatch.setattr(srv.settings, "find_ways_governor", False)
    monkeypatch.setattr(ft, "CACHE", ft.TriageCache())
    rows: list[tuple] = []

    class Pool:
        async def execute(self, sql, *args):
            rows.append(args)

    async def pool_for_log(pool, query):
        return Pool()

    monkeypatch.setattr("app.services.search_group.pool_for_log", pool_for_log)

    async def impl(query, ctx, **kw):
        with st.stage("embed"):
            await asyncio.sleep(0.01)
        return json.dumps({"outcome": "no_match"})

    monkeypatch.setattr(srv, "_find_ways_impl", impl)
    return rows


def _judge(monkeypatch, judge):
    monkeypatch.setattr("app.services.retrieval_service.default_judge", lambda: judge)


def test_an_answered_request_records_its_stages(monkeypatch, written):
    _judge(monkeypatch, Judge("reusable_task", delay=0.02))
    _run(srv.find_ways("add retry with backoff to the http client", Ctx()))
    detail = written[-1][-1]
    stages = detail["stages"]
    assert {"governor", "triage_judge", "impl", "embed", "model_plan"} <= set(stages)
    assert stages["triage_judge"]["ms"] >= 15 and stages["impl"]["ms"] >= stages["embed"]["ms"] >= 8
    assert detail["outcome"] == "no_match" and detail["total_ms"] >= stages["impl"]["ms"]


def test_a_skipped_request_records_the_triage_and_nothing_after_it(monkeypatch, written):
    _judge(monkeypatch, Judge("conversation", delay=0.01))
    _run(srv.find_ways("thanks, that looks right to me", Ctx()))
    detail = written[-1][-1]
    assert detail["outcome"] == "not_needed" and detail["governor"] == "triage"
    assert "triage_judge" in detail["stages"] and "impl" not in detail["stages"] and "embed" not in detail["stages"]


def test_a_memo_hit_costs_no_judge_stage(monkeypatch, written):
    _judge(monkeypatch, Judge("reusable_task"))
    query = "add retry with backoff to the http client"
    _run(srv.find_ways(query, Ctx()))
    _run(srv.find_ways(query, Ctx()))
    assert "triage_judge" in written[0][-1]["stages"] and "triage_judge" not in written[1][-1]["stages"]


def test_the_record_holds_stage_names_and_numbers_only(monkeypatch, written):
    _judge(monkeypatch, Judge("reusable_task"))
    secret = "add retry for the SECRET-TOKEN-xyz client"
    _run(srv.find_ways(secret, Ctx()))
    blob = json.dumps(written[-1][-1])
    assert "SECRET-TOKEN" not in blob
    assert all(set(v) == {"ms", "n", "max_ms"} for v in written[-1][-1]["stages"].values())


def test_nothing_is_added_when_no_stage_was_recorded(monkeypatch, written):
    _judge(monkeypatch, Judge("reusable_task"))

    async def go():
        st._RECORDER.set(None)
        await srv._record_find_ways(Ctx(), "q", json.dumps({"outcome": "x"}), {}, 1.0)

    _run(go())
    assert "stages" not in written[-1][-1]


# ---- the report -----------------------------------------------------------------------------------------

def _report():
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "find_ways_latency_report.py")
    spec = importlib.util.spec_from_file_location("find_ways_latency_report", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["find_ways_latency_report"] = module
    spec.loader.exec_module(module)
    return module


def _row(total, outcome="resolved", **stages):
    return {"detail": {"outcome": outcome, "total_ms": total,
                       "stages": {k: {"ms": v, "n": 1, "max_ms": v} for k, v in stages.items()}}}


def test_the_report_computes_percentiles_and_shares():
    rep = _report()
    rows = [_row(1000 + 100 * i, embed=100.0 + i, judge_goal=500.0 + 10 * i) for i in range(10)]
    out = rep.summarize(rows)
    assert out["requests"] == 10 and out["total"]["p50"] == 1400 and out["total"]["p90"] == 1800 and out["total"]["p99"] == 1900
    by = {r["stage"]: r for r in out["stages"]}
    assert by["judge_goal"]["p50"] == 540.0 and by["judge_goal"]["p99"] == 590.0 and by["embed"]["n"] == 10
    assert [r["stage"] for r in out["stages"]] == ["judge_goal", "embed"]          # biggest first
    assert by["judge_goal"]["share"] == pytest.approx(545 / 1450, abs=0.001)


def test_the_report_filters_by_outcome_reads_json_text_and_survives_old_rows():
    rep = _report()
    rows = [_row(300, outcome="not_needed", triage_judge=250.0), _row(5000, embed=900.0),
            {"detail": json.dumps({"outcome": "resolved", "total_ms": 4000})},         # recorded before stages existed
            {"detail": "not json"}, {"detail": None}]
    only = rep.summarize(rows, outcome="not_needed")
    assert only["requests"] == 1 and only["total"]["p50"] == 300 and [r["stage"] for r in only["stages"]] == ["triage_judge"]
    everything = rep.summarize(rows)
    assert everything["requests"] == 3 and everything["outcomes"] == {"resolved": 2, "not_needed": 1}
    text = rep.render(everything)
    assert "embed" in text and "triage_judge" in text


def test_an_empty_report_says_why():
    rep = _report()
    assert "no row has a stage breakdown" in rep.render(rep.summarize([]))
