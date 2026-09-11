"""
T14 -- load/latency tests for the hot retrieval path, against a real
Postgres connection pool.

SCOPE LIMIT (stated explicitly so nobody mistakes this for more than it
is): this codebase has no running MCP-server test harness -- every
existing MCP test (see test_mcp_six_tool_surface_offline.py, any
test_find_best_way_*_e2e.py) calls tool coroutines directly against a
real asyncpg pool, never over a real network socket. Building a full
network-latency rig against a live server process is a separate, larger
piece of work. What THIS file measures is real wall-clock latency
percentiles of the actual hot-path SERVICE CALLS `find_best_way` /
`check_applicability` call internally -- real asyncio, real DB
round-trips, no mocking -- which is where the actual cost lives (network
framing on top of an already-fast local call is a constant, not a
correctness-relevant variable here).

Same skip-not-fail-without-DATABASE_URL convention as every other
`*_e2e.py` file (see test_load_e2e.py for the load-test sibling this
file is modeled on -- that file proves correctness under concurrency;
this one measures single-threaded latency distribution).
"""
from __future__ import annotations

import asyncio
import os
import time
import uuid

import pytest

from app.db.session import create_pool
from app.services.applicability import find_applicable_procedures
from app.services.embeddings import Embedder
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
    approve_procedure,
    capture_procedure,
    record_execution_outcome,
)
from app.services.retrieval import HybridRetriever

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

_MARK = "test-latency-e2e"
_FIXED_VEC = [0.2] * 1024  # exercises the real SQL/ranking path; the vector's
                           # own numeric content doesn't matter here, only
                           # that pgvector distance math + ranking run for real.


def _percentile(samples: list[float], p: float) -> float:
    """Plain-Python percentile (no numpy dependency): nearest-rank on a
    sorted copy. `p` in [0, 100]."""
    if not samples:
        return 0.0
    ordered = sorted(samples)
    k = max(0, min(len(ordered) - 1, int(round(p / 100 * (len(ordered) - 1)))))
    return ordered[k]


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute("DELETE FROM procedures WHERE name LIKE $1", f"{name_prefix}%")


async def _seed_corpus(pool, *, n: int, name_prefix: str) -> list[str]:
    """N real procedures, roughly half driven to real verified+approved
    state (crossing the actual MIN_SUCCESSES_FOR_VERIFIED /
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED threshold arithmetic -- not a
    hand-set flag), half left as plain candidates. Returns the row ids."""
    embedder = Embedder()
    model_id = embedder.embedding_model_id()
    row_ids: list[str] = []
    for i in range(n):
        name = f"{name_prefix}-{i}-{uuid.uuid4().hex[:6]}"
        res = await capture_procedure(
            pool, name=name, goal=f"latency probe goal {i}: provision resource {i}",
            provenance="system_pending_review", scope_type="global",
            steps=[{"order": 0, "goal": f"step {i}"}],
            embedding=_FIXED_VEC, embedding_model_id=model_id,
        )
        row_id = res["id"]
        row_ids.append(row_id)
        if i % 2 == 0:
            for c in range(MIN_SUCCESSES_FOR_VERIFIED):
                await record_execution_outcome(
                    pool, procedure_row_id=row_id, success=True,
                    context_key=f"ctx-{c % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
                )
            await approve_procedure(pool, procedure_row_id=row_id, approved_by="latency-tester")
    return row_ids


def test_find_applicable_procedures_latency_percentiles():
    """p50/p95/p99 for the real find_applicable_procedures() pipeline
    (cold-start gate -> non-compensatory hard filter -> similarity
    ranking) against a ~40-row local corpus. This is the function
    `find_best_way`'s retrieval tier calls directly."""
    N_CORPUS = 40
    N_SAMPLES = 40

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=2, max_size=10)
        try:
            await _cleanup(pool, _MARK)
            await _seed_corpus(pool, n=N_CORPUS, name_prefix=_MARK)

            samples: list[float] = []
            for _ in range(N_SAMPLES):
                start = time.monotonic()
                await find_applicable_procedures(
                    pool, goal_embedding=_FIXED_VEC, require_verified=False, limit=10,
                )
                samples.append(time.monotonic() - start)

            p50, p95, p99 = (_percentile(samples, p) for p in (50, 95, 99))
            print(
                f"\n[latency] find_applicable_procedures x{N_SAMPLES} over a "
                f"{N_CORPUS}-row corpus: p50={p50*1000:.1f}ms p95={p95*1000:.1f}ms "
                f"p99={p99*1000:.1f}ms max={max(samples)*1000:.1f}ms"
            )

            # Generous, documented ceilings -- not a hardware performance
            # guarantee (no dedicated CI runner promise here), a
            # regression trip-wire: these are 10x+ headroom over the
            # observed local numbers at seed time.
            assert p50 < 0.5, f"p50 {p50*1000:.1f}ms exceeds the 500ms ceiling"
            assert p95 < 2.0, f"p95 {p95*1000:.1f}ms exceeds the 2000ms ceiling"
            assert p99 < 5.0, f"p99 {p99*1000:.1f}ms exceeds the 5000ms ceiling"
        finally:
            await _cleanup(pool, _MARK)
            await pool.close()

    asyncio.run(_run())


def test_hybrid_retriever_latency_percentiles():
    """p50/p95/p99 for HybridRetriever.retrieve() -- picked as the second
    hot path (over get_run_context) because it is the literal function
    `find_best_way`'s own retrieval tier calls per its module docstring
    ("Hybrid entrypoints, then bounded graph expansion"), and it runs
    against task_nodes/knowledge_nodes -- tables that already carry real
    production-scale data on this checkout, giving a realistic corpus
    without this test having to seed thousands of rows itself.

    `query_vec` is passed explicitly (the fixed probe vector) so this
    measures the real vector+lexical SQL/ranking/expansion cost, not an
    embedding API call latency, which is a different (and separately
    already-measured-elsewhere) concern.
    """
    N_SAMPLES = 40

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=2, max_size=10)
        try:
            retriever = HybridRetriever(pool)
            queries = [
                "provision a staging cluster", "migrate the datastore without downtime",
                "convert the tool listing", "classify semantic dependency",
                "enumerate callers of a function",
            ]
            samples: list[float] = []
            for i in range(N_SAMPLES):
                start = time.monotonic()
                await retriever.retrieve(queries[i % len(queries)], query_vec=_FIXED_VEC, top_k=6)
                samples.append(time.monotonic() - start)

            p50, p95, p99 = (_percentile(samples, p) for p in (50, 95, 99))
            print(
                f"\n[latency] HybridRetriever.retrieve x{N_SAMPLES}: "
                f"p50={p50*1000:.1f}ms p95={p95*1000:.1f}ms p99={p99*1000:.1f}ms "
                f"max={max(samples)*1000:.1f}ms"
            )

            assert p50 < 0.5, f"p50 {p50*1000:.1f}ms exceeds the 500ms ceiling"
            assert p95 < 2.0, f"p95 {p95*1000:.1f}ms exceeds the 2000ms ceiling"
            assert p99 < 5.0, f"p99 {p99*1000:.1f}ms exceeds the 5000ms ceiling"
        finally:
            await pool.close()

    asyncio.run(_run())


def test_empty_result_path_has_no_outsized_fixed_floor_cost():
    """The cheapest possible real call through the same pipeline (limit=1,
    a require_verified=True search under a throwaway invariant binding
    that nothing real satisfies) must still return fast -- a sanity check
    that the connection pool / query planning itself isn't a hidden fixed
    cost dwarfing the real per-candidate work measured above.

    NOTE: `limit=0` was tried first and rejected -- it does NOT mean "no
    candidates come back" in this pipeline (a real dev-DB row surfaced
    anyway), so this test does not assert on result content, only timing;
    asserting on `limit`'s exact candidate-count semantics is out of
    scope here and belongs with applicability.py's own tests."""
    N_SAMPLES = 20

    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=2, max_size=10)
        try:
            samples: list[float] = []
            for _ in range(N_SAMPLES):
                start = time.monotonic()
                await find_applicable_procedures(
                    pool, goal_embedding=_FIXED_VEC, require_verified=True, limit=1,
                    invariant_bindings={f"{_MARK}-nonexistent-invariant": 1.0},
                )
                samples.append(time.monotonic() - start)

            p50, p95 = _percentile(samples, 50), _percentile(samples, 95)
            print(
                f"\n[latency] find_applicable_procedures(limit=1, unsatisfiable invariant) "
                f"x{N_SAMPLES} (no-op floor cost): p50={p50*1000:.1f}ms p95={p95*1000:.1f}ms"
            )
            assert p95 < 0.3, (
                f"a limit=0 (empty-result) call took p95={p95*1000:.1f}ms -- "
                "the pipeline has a fixed floor cost that would dwarf real work"
            )
        finally:
            await pool.close()

    asyncio.run(_run())
