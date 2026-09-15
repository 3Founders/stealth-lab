"""
DB-free coverage for app.execution.execution_telemetry -- the real
per-execution cost/latency ledger (migration 85, meta-harness/execu.md
Sec 13/14/32). Every real dependency is a FakePool answering exactly the
two real queries this module issues.
"""
from __future__ import annotations

import asyncio

import pytest

from app.execution.execution_telemetry import (
    ImplementationExecutionStats,
    implementation_execution_stats,
    record_implementation_execution,
)


def _run(coro):
    return asyncio.run(coro)


class _FakeTelemetryPool:
    def __init__(self):
        self.inserted: list[dict] = []
        self._stats_row = None

    def set_stats_row(self, row: dict):
        self._stats_row = row

    async def fetchrow(self, sql, *params):
        n = " ".join(sql.split())
        if n.startswith("INSERT INTO implementation_execution_telemetry"):
            row = {
                "id": params[0], "implementation_id": params[1], "goal_id": params[2],
                "execution_run_id": params[3], "execution_run_node_id": params[4],
                "executor": params[5], "outcome_status": params[6],
                "prompt_tokens": params[7], "completion_tokens": params[8], "llm_calls": params[9],
                "wall_seconds": params[10], "created_by": params[11],
            }
            self.inserted.append(row)
            return row
        if "FROM implementation_execution_telemetry" in n and "SELECT" in n:
            return self._stats_row
        raise AssertionError(f"unexpected fetchrow: {n[:80]}")


# ---------------------------------------------------------------------
# record_implementation_execution
# ---------------------------------------------------------------------


def test_record_rejects_invalid_outcome_status():
    pool = _FakeTelemetryPool()
    with pytest.raises(ValueError):
        _run(record_implementation_execution(
            pool, implementation_id="I-1", executor="deterministic", outcome_status="maybe",
        ))
    assert pool.inserted == []


def test_record_extracts_real_fields_from_node_result_data():
    pool = _FakeTelemetryPool()
    _run(record_implementation_execution(
        pool, implementation_id="I-1", executor="frontier", outcome_status="success",
        node_result_data={"prompt_tokens": 120, "completion_tokens": 40, "llm_calls": 2},
    ))
    row = pool.inserted[0]
    assert row["prompt_tokens"] == 120
    assert row["completion_tokens"] == 40
    assert row["llm_calls"] == 2
    assert row["wall_seconds"] is None  # frontier data has no wall_seconds key in this fixture


def test_record_prefers_wall_seconds_key_falls_back_to_wall_time_seconds():
    pool = _FakeTelemetryPool()
    _run(record_implementation_execution(
        pool, implementation_id="I-1", executor="deterministic", outcome_status="success",
        node_result_data={"wall_time_seconds": 1.23},
    ))
    assert pool.inserted[0]["wall_seconds"] == 1.23


def test_record_with_no_node_result_data_leaves_every_measured_field_none_not_zero():
    pool = _FakeTelemetryPool()
    _run(record_implementation_execution(
        pool, implementation_id="I-1", executor="api", outcome_status="failure",
    ))
    row = pool.inserted[0]
    assert row["prompt_tokens"] is None
    assert row["completion_tokens"] is None
    assert row["llm_calls"] is None
    assert row["wall_seconds"] is None


def test_record_threads_optional_goal_and_run_linkage():
    pool = _FakeTelemetryPool()
    _run(record_implementation_execution(
        pool, implementation_id="I-1", executor="tool", outcome_status="success",
        goal_id="G-1", execution_run_id="R-1", execution_run_node_id="N-1", created_by="tester",
    ))
    row = pool.inserted[0]
    assert row["goal_id"] == "G-1"
    assert row["execution_run_id"] == "R-1"
    assert row["execution_run_node_id"] == "N-1"
    assert row["created_by"] == "tester"


# ---------------------------------------------------------------------
# implementation_execution_stats
# ---------------------------------------------------------------------


def test_stats_with_zero_samples_is_honest_none_not_fabricated_zero():
    pool = _FakeTelemetryPool()
    pool.set_stats_row({
        "sample_count": 0, "success_count": 0, "mean_wall_seconds": None,
        "mean_prompt_tokens": None, "mean_completion_tokens": None, "mean_llm_calls": None,
    })
    stats = _run(implementation_execution_stats(pool, "I-1"))
    assert stats.sample_count == 0
    assert stats.success_rate is None  # never a fabricated 0.0 or 1.0


def test_stats_computes_real_success_rate_and_means():
    pool = _FakeTelemetryPool()
    pool.set_stats_row({
        "sample_count": 4, "success_count": 3, "mean_wall_seconds": 2.5,
        "mean_prompt_tokens": 100.0, "mean_completion_tokens": 50.0, "mean_llm_calls": 1.5,
    })
    stats = _run(implementation_execution_stats(pool, "I-1"))
    assert stats.sample_count == 4
    assert stats.success_rate == 0.75
    assert stats.mean_wall_seconds == 2.5
    assert stats.mean_prompt_tokens == 100.0
