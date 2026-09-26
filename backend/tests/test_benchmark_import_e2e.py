"""Benchmark import against a real database: Goals under domain Goals (accepted edges),
frozen benchmarks with the visible/gold test split, no reference solution stored,
idempotent re-runs, a reviewer's rejection never overruled, no transfer propagation."""
from __future__ import annotations

import json

import pytest

from app.benchmarks import bigcodebench as bcb
from app.benchmarks.importer import import_tasks
from app.benchmarks.tasks import assign_splits
from tests.benchmark_fixtures import row
from tests.test_goal_abstraction_e2e import DATABASE_URL, _run_id, pool  # noqa: F401

pytestmark = pytest.mark.skipif(not DATABASE_URL, reason="requires a real DATABASE_URL")


def _rows(run: str) -> list[dict]:
    return [
        row(0, f"Calculate permutation difference averages for run {run}.", "['random', 'itertools']"),
        row(1, f"Load a CSV for run {run} and plot a histogram of each numeric column.",
            "['pandas', 'matplotlib', 'random']"),
        row(2, f"Fetch a web page for run {run} and extract every link.", "['requests', 'bs4', 're']"),
        row(3, f"Hash a file for run {run}.", "['hashlib']", n_tests=2),
        row(4, f"Group sales records for run {run} by month.", "['pandas', 'datetime']"),
    ]


@pytest.mark.asyncio
async def test_import_is_complete_frozen_and_idempotent(pool):
    run = _run_id()
    tasks = bcb.tasks_from_rows(_rows(run))
    assign_splits(tasks)
    report = await import_tasks(pool, tasks)
    assert report["task_goals"] == 4 and report["summary"]["excluded"] == 1
    assert report["benchmarks_created"] == 4
    assert report["edges_created"] == 1 + 2 + 2 + 2
    manifest = {m["external_id"]: m for m in report["manifest"]}
    m1 = manifest["BigCodeBench/1"]

    bench = await pool.fetchrow("SELECT * FROM benchmarks WHERE id = $1::uuid", m1["benchmark_id"])
    assert bench["status"] == "frozen" and bench["frozen_at"] is not None
    protocol = bench["evaluation_protocol"]
    protocol = json.loads(protocol) if isinstance(protocol, str) else protocol
    assert protocol["visible_tests"] == m1["visible_tests"] and set(protocol["tests"]) == set(m1["tests"])
    blob = json.dumps({k: (v if isinstance(v, (str, int, float, type(None))) else str(v)) for k, v in dict(bench).items()})
    assert "return 1" not in blob                                      # reference solution never stored

    edges = await pool.fetch(
        "SELECT abstract_goal_id::text AS parent, status, provenance FROM goal_relations "
        "WHERE specific_goal_id = $1::uuid", m1["goal_id"])
    assert len(edges) == 2 and all(e["status"] == "accepted" and e["provenance"] == "benchmark_import:bigcodebench"
                                   for e in edges)
    assert await pool.fetchval("SELECT count(*) FROM goal_search_index WHERE goal_id = $1::uuid", m1["goal_id"]) == 1
    assert await pool.fetchval(
        "SELECT count(*) FROM ingestion_jobs WHERE job_type = 'benchmark_transfer' AND payload::text LIKE $1",
        f"%{m1['benchmark_id']}%") == 0                                # imported benchmarks do not propagate

    # a reviewer rejects one placement; re-importing must not overrule them
    await pool.execute(
        "UPDATE goal_relations SET status = 'rejected' WHERE specific_goal_id = $1::uuid AND abstract_goal_id = $2::uuid",
        m1["goal_id"], edges[0]["parent"])
    again = await import_tasks(pool, bcb.tasks_from_rows(_rows(run)))
    assert again.get("benchmarks_created", 0) == 0 and again.get("edges_created", 0) == 0
    assert {m["external_id"]: m["goal_id"] for m in again["manifest"]} == {k: v["goal_id"] for k, v in manifest.items()}
    assert await pool.fetchval(
        "SELECT status FROM goal_relations WHERE specific_goal_id = $1::uuid AND abstract_goal_id = $2::uuid",
        m1["goal_id"], edges[0]["parent"]) == "rejected"


@pytest.mark.asyncio
async def test_dry_run_writes_nothing(pool):
    run = _run_id()
    tasks = bcb.tasks_from_rows(_rows(run))
    report = await import_tasks(pool, tasks, dry_run=True)
    assert report["dry_run"] and report["summary"]["usable"] == 4 and "manifest" not in report
    assert await pool.fetchval("SELECT count(*) FROM goals WHERE canonical_name LIKE $1", f"%run {run}%") == 0
