"""Automatic Benchmark transfer propagation, against a real database.

A reviewed, frozen Benchmark is offered to its DIRECT graph neighbours; every hop
is a judged transfer (never a copy), a not_transferable verdict creates nothing
(so the walk stops there), the walk never goes back through the Goal it came
from, it is bounded in hops, and nothing about evidence, verification or
resolution moves with it. Only the judge's verdicts are scripted."""
from __future__ import annotations

import pytest

from app.ingestion import queue as q
from app.services import benchmark_transfer as bt
from app.services import product_model as pm
from app.services.access import AccessScope, TenantScope
from app.services.goal_abstraction import persist_goal_relation
from tests.test_goal_abstraction_e2e import DATABASE_URL, _goal, _job_row, _run_id, _Verdicts, pool  # noqa: F401

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL")


async def _edge(pool, specific: str, abstract: str) -> None:
    """An accepted public edge exactly as placement writes it (commons tenant)."""
    await persist_goal_relation(
        pool, specific, abstract, status="accepted", provenance="e2e", access_scope=AccessScope.unrestricted(),
        tenant_scope=TenantScope.commons(), decided_by="e2e-reviewer", decision_metadata={"reason": "e2e"})


async def _frozen_benchmark(pool, goal_id: str, name: str) -> str:
    bench = await pm.create_benchmark(pool, goal_id=goal_id, name=name, success_criteria={"exit_code": 0})
    await pool.execute(
        "INSERT INTO benchmark_submissions (id, goal_id, benchmark_id, name, status, submitted_by, provenance, "
        "scope_type, owner_id, visibility, reviewed_by, reviewed_at) VALUES (gen_random_uuid(), $1, $2, $3, "
        "'accepted', 'reviewer', 'system_pending_review', 'global', 'reviewer', 'public', 'reviewer', now())",
        goal_id, bench["id"], name)
    await pm.freeze_benchmark(pool, bench["id"])
    return bench["id"]


async def _run_transfer(pool, target_goal: str, source_benchmark: str, verdict: str) -> dict:
    job = await _job_row(
        pool, "job_type = 'benchmark_transfer' AND payload->>'target_goal_id' = $1 "
              f"AND payload->>'source_benchmark_id' = '{source_benchmark}'", target_goal)
    return await bt.handle_benchmark_transfer(
        pool, {**job["payload"], "_job": q.trusted_job_metadata(job)}, judge=_Verdicts(lambda _t: verdict))


async def _jobs_from(pool, source_benchmark: str) -> set[str]:
    rows = await pool.fetch(
        "SELECT payload->>'target_goal_id' AS target FROM ingestion_jobs "
        "WHERE job_type = 'benchmark_transfer' AND payload->>'source_benchmark_id' = $1", source_benchmark)
    return {row["target"] for row in rows}


@pytest.mark.asyncio
async def test_frozen_benchmark_propagates_hop_by_hop_and_stops_where_it_should(pool, monkeypatch):
    run = _run_id()
    top = await _goal(pool, f"e2e {run} build software")
    mid = await _goal(pool, f"e2e {run} build a python package")
    low = await _goal(pool, f"e2e {run} build a python wheel")
    leaf = await _goal(pool, f"e2e {run} build a manylinux python wheel")
    await _edge(pool, mid, top)
    await _edge(pool, low, mid)
    await _edge(pool, leaf, low)

    # 1. freezing a reviewed Benchmark on `mid` offers it to BOTH direct neighbours
    source = await _frozen_benchmark(pool, mid, f"e2e {run} package builds")
    first = await bt.propagate_after_freeze(pool, source)
    assert first["propagated"] and first["hops"] == 0
    assert await _jobs_from(pool, source) == {top, low}
    assert await bt.propagate_after_freeze(pool, source) == {**first, "transfers": [
        {**t, "created": False} for t in first["transfers"]]}          # idempotent: no duplicate jobs

    # 2. `top` judged not transferable: nothing is created there, so nothing can propagate from it
    denied = await _run_transfer(pool, top, source, "not_transferable")
    assert denied["decision"] == "not_transferable"
    assert await pool.fetchval("SELECT count(*) FROM benchmarks WHERE goal_id = $1", top) == 0

    # 3. `low` judged transferable: a DRAFT target Benchmark; once reviewed and frozen it
    #    propagates onward to `leaf` only -- never back to `mid`, where it came from
    await _run_transfer(pool, low, source, "transferable")
    low_bench = await pool.fetchval("SELECT id::text FROM benchmarks WHERE goal_id = $1", low)
    assert await pool.fetchval("SELECT status FROM benchmarks WHERE id = $1::uuid", low_bench) == "draft"
    await pm.freeze_benchmark(pool, low_bench)
    second = await bt.propagate_after_freeze(pool, low_bench)
    assert second["propagated"] and second["hops"] == 1
    assert await _jobs_from(pool, low_bench) == {leaf}

    # 4. the hop bound stops the walk
    monkeypatch.setenv("STEALTH_BENCHMARK_TRANSFER_MAX_HOPS", "1")
    bounded = await bt.propagate_after_freeze(pool, low_bench)
    assert bounded == {"propagated": False, "reason": "max_hops_reached", "hops": 1, "max_hops": 1}

    # 5. transfer carries meaning only: no evidence, no verification, no resolution
    for goal_id in (top, mid, low, leaf):
        assert await pool.fetchval("SELECT resolved_at FROM goals WHERE id = $1", goal_id) is None
