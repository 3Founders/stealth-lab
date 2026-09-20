"""DB-free coverage for app.execution.execution_telemetry -- per-step cost/latency ledger (`step_execution_telemetry`)."""
from __future__ import annotations

import asyncio

import pytest

from app.execution.execution_telemetry import record_step_execution, step_execution_stats


def _run(coro):
    return asyncio.run(coro)


class _Pool:
    def __init__(self, stats_row=None):
        self.inserted = []
        self.stats_row = stats_row

    async def fetchrow(self, sql, *params):
        n = " ".join(sql.split())
        if n.startswith("INSERT INTO step_execution_telemetry"):
            row = dict(zip(["id", "procedure_id", "step_order", "goal_id", "execution_run_id", "execution_run_node_id", "executor",
                            "outcome_status", "prompt_tokens", "completion_tokens", "llm_calls", "wall_seconds", "created_by"], params))
            self.inserted.append(row)
            return row
        if "FROM step_execution_telemetry" in n:
            return self.stats_row
        raise AssertionError(n[:80])


def test_record_reads_whichever_real_keys_exist_and_leaves_the_rest_null():
    pool = _Pool()
    _run(record_step_execution(pool, procedure_id="P", step_order=2, executor="deterministic", outcome_status="success",
                               node_result_data={"wall_time_seconds": 1.5}))
    row = pool.inserted[0]
    assert (row["procedure_id"], row["step_order"], row["wall_seconds"]) == ("P", 2, 1.5)
    assert row["prompt_tokens"] is None and row["llm_calls"] is None          # never a fabricated zero


def test_record_rejects_an_unknown_outcome():
    with pytest.raises(ValueError):
        _run(record_step_execution(_Pool(), procedure_id="P", step_order=0, executor="x", outcome_status="maybe"))


def test_stats_zero_samples_is_honest_none():
    stats = _run(step_execution_stats(_Pool({"sample_count": 0, "success_count": 0, "mean_wall_seconds": None, "mean_prompt_tokens": None,
                                             "mean_completion_tokens": None, "mean_llm_calls": None}), "P", 0))
    assert stats.sample_count == 0 and stats.success_rate is None


def test_stats_rate_and_means():
    stats = _run(step_execution_stats(_Pool({"sample_count": 4, "success_count": 3, "mean_wall_seconds": 2.0, "mean_prompt_tokens": 10.0,
                                             "mean_completion_tokens": 5.0, "mean_llm_calls": 1.0}), "P", 1))
    assert stats.success_rate == 0.75 and stats.mean_wall_seconds == 2.0 and stats.step_order == 1
