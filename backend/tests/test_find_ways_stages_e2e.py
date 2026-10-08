"""The per-stage timing, end to end against a real database: a real `find_ways` call over a real Goal and Procedure
writes its stage breakdown into its `retrieval_decisions` row, and the latency report reads that row.

The offline suite (test_stage_timer_offline.py) proves the timer and the record with a stand-in pipeline; this proves
the instrumentation points inside the real pipeline are reached and recorded under the names the report documents.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys

import pytest

import app.mcp_server.server as srv
from app.config import settings
from app.services import retrieval_service as rs
from app.services import search_projection as sp
from app.services.shards import search_pool
from tests.identity_fakes import CallbackProvider, make_judge
from tests.test_goal_abstraction_e2e import DATABASE_URL, _run_id, pool  # noqa: F401
from tests.test_verified_solutions_e2e import _Ctx, _extract

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL")

# the stages a resolved request passes through: named by the instrumented code, documented by the report
PIPELINE = {"governor", "goal_choice", "goal_search", "catch_up", "embed", "goal_search_legs", "judge_goal",
            "hierarchy", "impl", "resolve_tree", "procedure_tier", "procedure_fetch", "procedure_rank", "model_plan"}


def _report():
    path = os.path.join(os.path.dirname(__file__), "..", "scripts", "find_ways_latency_report.py")
    spec = importlib.util.spec_from_file_location("find_ways_latency_report", path)
    module = importlib.util.module_from_spec(spec)
    sys.modules["find_ways_latency_report"] = module
    spec.loader.exec_module(module)
    return module


@pytest.mark.asyncio
async def test_a_resolved_request_records_every_stage_it_passed_through(pool, monkeypatch):
    monkeypatch.setattr("app.services.object_storage.get_store", lambda: None)
    monkeypatch.setattr(settings, "knowledge_verified_examples", True)
    run = _run_id()
    await _extract(pool, run, f"df = df.div(df.sum(axis=0), axis=1)  # {run}")
    await sp.drain_outbox(pool)
    known = f"compute column percentages {run}"

    def judge(kind, a, b):
        if kind == "task_goal":
            return ("matches", 0.95) if known in b.lower() else ("unrelated", 0.95)
        return ("applies", 0.9) if kind == "task_procedure" else ("distinct", 0.9)

    monkeypatch.setattr(rs, "default_judge", lambda: make_judge(CallbackProvider(judge, name="jev")))
    logs = await search_pool(pool)
    before = await logs.fetchval("SELECT count(*) FROM retrieval_decisions WHERE mode = 'find_ways'")

    out = json.loads(await srv.find_ways(known, _Ctx(pool)))
    assert out["outcome"] == "resolved", out

    rows = await logs.fetch(
        "SELECT detail FROM retrieval_decisions WHERE mode = 'find_ways' ORDER BY created_at DESC LIMIT 1")
    assert await logs.fetchval("SELECT count(*) FROM retrieval_decisions WHERE mode = 'find_ways'") == before + 1
    detail = rows[0]["detail"]
    detail = json.loads(detail) if isinstance(detail, str) else detail
    stages = detail["stages"]
    missing = PIPELINE - set(stages)
    assert not missing, f"stages not recorded by the real pipeline: {sorted(missing)} (got {sorted(stages)})"
    assert all(v["n"] >= 1 and v["ms"] >= 0 and v["max_ms"] <= v["ms"] + 0.1 for v in stages.values())
    assert stages["impl"]["ms"] >= stages["goal_choice"]["ms"] and detail["total_ms"] >= stages["impl"]["ms"]
    assert known not in json.dumps(detail)                                  # stage names and numbers only

    summary = _report().summarize([{"detail": detail}], outcome="resolved")
    assert summary["requests"] == 1 and {r["stage"] for r in summary["stages"]} >= PIPELINE
    assert all(r["share"] is not None for r in summary["stages"])
