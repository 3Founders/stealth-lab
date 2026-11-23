"""A run's cost is attributed per call and measured per run (docs/blinkit/measurement.md).

The defect: `llm_spend` recorded tokens and cost with no `run_id`, so a run happened, its spend was recorded, and it
was unattributable. Measured at the same time: a personal (non-org) buyer's calls write no
`provider_call_ledger` row at all, because that table's `organization_id` is NOT NULL — so attribution had to sit on
the path every successful call passes through, not in the org ledger.

What these pin:
  1. a call carries its run and step; a call without either is unchanged
  2. measuring a run equals the ledger for that run, per step
  3. spend not attributed to the run is never counted for it
  4. a failed run does not move a Way's measured cost (one flaky run must not price a Way)
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass

from app.providers.types import CallRequest
from app.execution import way_runs


# --- the request carries attribution, and only when asked to ----------------------------------------------

def test_a_call_carries_its_run_and_step():
    r = CallRequest(prompt="x", run_id="11111111-1111-1111-1111-111111111111", step_order=3)
    assert r.run_id.endswith("1111") and r.step_order == 3


def test_a_call_without_a_run_is_unchanged():
    """Every existing caller must behave exactly as before: no run, no attribution, no error."""
    r = CallRequest(prompt="x")
    assert r.run_id is None and r.step_order is None


def test_attribution_never_fails_a_completed_call():
    """The model answered and was paid for; a bookkeeping error must not discard it."""
    from app.providers.service import _attribute_call

    class BrokenPool:
        async def execute(self, *a, **k):
            raise RuntimeError("ledger is down")

    @dataclass
    class Spec:
        model: str = "m"
        provider: str = "p"

    @dataclass
    class Res:
        cost_usd: float = 0.001
        tokens_in: int = 10
        tokens_cache_read: int = 0
        tokens_cache_write: int = 0
        tokens_out: int = 5

    # Must not raise despite every statement failing.
    asyncio.run(_attribute_call(BrokenPool(), CallRequest(prompt="x", run_id="r", step_order=1), Res(), Spec(), 0.0))


# --- measuring a run ------------------------------------------------------------------------------------

class FakePool:
    """Serves the grouped-ledger SELECT and records the two UPDATEs."""

    def __init__(self, rows):
        self.rows = rows
        self.updates: list[tuple] = []

    async def fetch(self, sql, *args):
        return self.rows

    async def execute(self, sql, *args):
        self.updates.append((sql, args))


def _run():
    return {"id": "run-1", "procedure_row_id": "proc-1", "outputs": []}


def test_measure_equals_the_ledger_sum_per_step():
    pool = FakePool([
        {"step_order": 1, "cost": 0.0009, "tin": 500, "tout": 806},
        {"step_order": 2, "cost": 0.0022, "tin": 300, "tout": 1334},
    ])
    out = asyncio.run(way_runs.measure_run(pool, _run()))
    assert out["cost_usd"] == round(0.0031, 6)
    assert out["cost_paise"] == round(0.0031 * 100, 4)   # sub-paisa precision: a step is often under one paisa
    assert [s["order"] for s in out["steps"]] == [1, 2]
    assert out["tokens_in"] == 800 and out["tokens_out"] == 2140


def test_the_run_total_and_the_way_mean_are_both_written():
    pool = FakePool([{"step_order": 1, "cost": 0.01, "tin": 10, "tout": 20}])
    asyncio.run(way_runs.measure_run(pool, _run()))
    joined = " ".join(sql for sql, _ in pool.updates)
    assert "UPDATE way_runs" in joined and "cost_usd" in joined
    assert "UPDATE procedures" in joined and "measured_runs" in joined


def test_a_sub_paisa_run_is_not_recorded_as_free():
    """Measured 2026-11-23: a real cheap step costs $0.00042 ≈ ₹0.035, which rounds to 0 paise. Storing that would
    record a Way that costs money as free, and let it be priced with a floor of zero."""
    pool = FakePool([{"step_order": 1, "cost": 0.00042, "tin": 812, "tout": 134}])
    out = asyncio.run(way_runs.measure_run(pool, _run()))
    proc_args = next(args for sql, args in pool.updates if "UPDATE procedures" in sql)
    micros = proc_args[1]
    assert micros >= 1, "a paid run must never record a floor of zero"
    assert out["cost_paise"] > 0, "the returned figure must not round a real cost to nothing"


def test_the_way_mean_uses_micros_not_rounded_paise():
    pool = FakePool([{"step_order": 1, "cost": 0.00042, "tin": 1, "tout": 1}])
    asyncio.run(way_runs.measure_run(pool, _run()))
    sql = next(s for s, _ in pool.updates if "UPDATE procedures" in s)
    assert "measured_cost_micros" in sql


def test_a_run_with_no_attributed_spend_measures_nothing():
    """An older run, or one with no model calls, must not be recorded as costing zero."""
    pool = FakePool([])
    assert asyncio.run(way_runs.measure_run(pool, _run())) is None
    assert pool.updates == [], "nothing to write means nothing written"


def test_a_failed_run_does_not_price_a_way():
    """One flaky run must not set the cost of a Way that has never succeeded."""
    pool = FakePool([{"step_order": 1, "cost": 0.01, "tin": 1, "tout": 1}])

    class Recording(FakePool):
        pass

    calls: list[str] = []
    original_fetch = Recording.fetch

    async def spy_fetch(self, sql, *a):
        calls.append(sql)
        return await original_fetch(self, sql, *a)

    Recording.fetch = spy_fetch
    run = {"id": "run-2", "procedure_row_id": "proc-1", "outputs": [], "status": "failed"}

    from app.services import procedure_billing
    released: list[str] = []

    async def fake_release(pool_, run_id):
        released.append(run_id)

    async def fake_consume(pool_, run_id):
        raise AssertionError("a failed run must not consume the purchase")

    saved = (procedure_billing.release_run, procedure_billing.consume_run)
    procedure_billing.release_run, procedure_billing.consume_run = fake_release, fake_consume
    try:
        asyncio.run(way_runs._settle_purchase(pool, run))
    finally:
        procedure_billing.release_run, procedure_billing.consume_run = saved
        Recording.fetch = original_fetch
    assert released == ["run-2"]
    assert pool.updates == [], "a failed run writes no measured cost"
