"""
FINAL-V1 §5 -- performance SANITY probe (hand-run, NOT a pytest file).

Production-readiness sanity check, not scale benchmarking. For each real
code path below it seeds the minimum data (or reuses an existing
`[pm-e2e ...]` / `[pm-mcp ...]` problem), runs it N>=20 times (embedding:
>=5), and records per-call latency (ms) plus the DB query COUNT for one
instrumented call.

Query counting: `asyncpg.connection.Connection.{fetch,fetchrow,fetchval,
execute,executemany}` are monkeypatched to bump a thread-safe counter.
`Pool.fetch(...)` and friends forward to those, and `tenant_transaction`
issues its BEGIN / `SET LOCAL` / COMMIT through `execute`, so the count is
"every statement the path put on the wire", transaction-control included
(noted in the write-up).

Run:
    export DATABASE_URL='postgresql://.../postgres'
    python .scratch/perf_probe.py

Writes raw results to .scratch/perf_results.json.
"""
from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time
import uuid
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1] / "backend"
sys.path.insert(0, str(BACKEND))

import asyncpg  # noqa: E402

from app.db.session import create_pool  # noqa: E402
from app.services import product_model as pm  # noqa: E402
from app.services import claim_graph_api  # noqa: E402
from app.services.access import AccessScope  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402
from app.utils.ids import uuid7  # noqa: E402
from app.execution.durable_run import (  # noqa: E402
    start_run, execute_run, resume_run, run_status, WorkerLost,
)
import app.mcp_server.server as srv  # noqa: E402

RESULTS_PATH = Path(__file__).resolve().parent / "perf_results.json"
N = 20
N_EMBED = 6
SCOPE = AccessScope.unrestricted()
DEPS = {0: [], 1: [0], 2: [1]}

# --------------------------------------------------------------------------
# DB query counter -- monkeypatch asyncpg.Connection methods.
# --------------------------------------------------------------------------
_QC = {"n": 0, "on": False}
_orig = {}


def _wrap(name):
    fn = getattr(asyncpg.connection.Connection, name)

    async def inner(self, *a, **kw):
        if _QC["on"]:
            _QC["n"] += 1
        return await fn(self, *a, **kw)

    inner.__name__ = name
    return inner


for _m in ("fetch", "fetchrow", "fetchval", "execute", "executemany"):
    _orig[_m] = getattr(asyncpg.connection.Connection, _m)
    setattr(asyncpg.connection.Connection, _m, _wrap(_m))


class count_queries:
    def __enter__(self):
        _QC["n"] = 0
        _QC["on"] = True
        return self

    def __exit__(self, *exc):
        _QC["on"] = False
        self.count = _QC["n"]
        return False


# --------------------------------------------------------------------------
# timing
# --------------------------------------------------------------------------
async def measure(name, coro_factory, *, n=N, count_first=True):
    """coro_factory() -> awaitable. Runs once as warm-up (optionally with a
    query count), then n timed iterations."""
    print(f"  [{name}] warm-up + {n} samples ...", flush=True)
    qcount = None
    failures = []
    try:
        if count_first:
            with count_queries() as cq:
                await coro_factory()
            qcount = cq.count
        else:
            await coro_factory()
    except Exception as e:  # noqa: BLE001
        failures.append(f"warmup: {type(e).__name__}: {e}")
        return {"samples": 0, "failures": failures, "query_count": qcount}

    lat = []
    for _ in range(n):
        t0 = time.perf_counter()
        try:
            await coro_factory()
        except Exception as e:  # noqa: BLE001
            failures.append(f"{type(e).__name__}: {e}")
            continue
        lat.append((time.perf_counter() - t0) * 1000.0)

    if not lat:
        return {"samples": 0, "failures": failures, "query_count": qcount}
    lat.sort()
    return {
        "samples": len(lat),
        "p50_ms": round(statistics.median(lat), 2),
        "p95_ms": round(lat[min(len(lat) - 1, int(len(lat) * 0.95))], 2),
        "mean_ms": round(statistics.fmean(lat), 2),
        "min_ms": round(lat[0], 2),
        "max_ms": round(lat[-1], 2),
        "query_count": qcount,
        "failures": failures,
    }


# --------------------------------------------------------------------------
# seeding helpers (adapted from backend/tests/test_product_model_e2e.py)
# --------------------------------------------------------------------------
class _FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        h = abs(hash(text))
        return [((h >> (i % 40)) & 1) * 0.1 + 0.01 for i in range(1024)]


async def _make_procedure(pool, name):
    res = await capture_procedure(
        pool, name=name, goal=f"{name} goal", steps=[{"order": 0, "goal": "do it"}],
        provenance="prior_library", scope_type="global", created_by="perf_probe",
        embedding=await _FakeEmbedder().embed_one(name),
    )
    return res["procedure_id"], res["id"]


async def _run_executions(pool, procedure_id, procedure_row_id, *, n, successes):
    async with pool.acquire() as conn:
        pv = await conn.fetchval("SELECT version FROM procedures WHERE id=$1", procedure_row_id)
        plan_id = await conn.fetchval(
            "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
            " task_description, procedure_content_hash, content_hash, scope_type) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
            str(uuid7()), procedure_id, pv, procedure_row_id,
            "perf-probe run", f"pch-{uuid.uuid4().hex[:12]}", f"ch-{uuid.uuid4().hex[:12]}",
        )
        graph_id = await conn.fetchval(
            "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
            "VALUES ($1,$2,$3,$4::jsonb) RETURNING id",
            str(uuid7()), plan_id, f"h-{uuid.uuid4().hex[:12]}", json.dumps({"nodes": []}),
        )
        ids = []
        for i in range(n):
            outcome = "success" if i < successes else "failure"
            eid = await conn.fetchval(
                "INSERT INTO executions (id, execution_plan_id, task_graph_id, procedure_id, "
                " procedure_version, started_at, ended_at, outcome, created_by, scope_type) "
                "VALUES ($1,$2,$3,$4,$5, now() - interval '10 seconds', now(), $6, 'perf_probe','global') "
                "RETURNING id",
                str(uuid7()), plan_id, graph_id, procedure_id, pv, outcome,
            )
            ids.append(str(eid))
        for _ in range(successes):
            await conn.execute(
                "INSERT INTO evidence (id, evidence_type, target_type, target_id, "
                " target_version, direction, strength_score, strength_method, "
                " outcome_status, success_criteria, created_by) "
                "VALUES ($1,'execution_result','procedure',$2,$3,'supports',1.0,'perf_probe',"
                " 'success', $4::jsonb, 'perf_probe')",
                str(uuid7()), procedure_id, pv, json.dumps({"predicate": "benchmark_case_passed"}),
            )
        return ids, plan_id, graph_id


async def _plan_chain(pool):
    proc_id, row_id = await _make_procedure(pool, f"perf-dr-{uuid.uuid4().hex[:8]}")
    async with pool.acquire() as c:
        pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
        plan_id = await c.fetchval(
            "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
            " task_description, procedure_content_hash, content_hash, scope_type) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
            str(uuid7()), proc_id, pv, row_id, "perf-dr",
            f"pch-{uuid.uuid4().hex[:10]}", f"ch-{uuid.uuid4().hex[:10]}",
        )
        graph_id = await c.fetchval(
            "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
            "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
            str(uuid7()), plan_id, f"gh-{uuid.uuid4().hex[:10]}",
        )
    return proc_id, pv, plan_id, graph_id


class _Ctx:
    def __init__(self, pool):
        class _RC:
            pass
        self.request_context = _RC()
        self.request_context.lifespan_context = {"pool": pool}


# --------------------------------------------------------------------------
async def main():
    dsn = os.environ.get("DATABASE_URL")
    if not dsn:
        print("DATABASE_URL unset", file=sys.stderr)
        sys.exit(2)
    pool = await create_pool(dsn, statement_cache_size=0)
    ctx = _Ctx(pool)
    out: dict = {}
    tag = uuid.uuid4().hex[:8]
    print(f"seed tag: {tag}", flush=True)

    # ---- seed one full product-model problem with 2 solutions + evals ----
    print("seeding product-model problem ...", flush=True)
    problem = await pm.create_problem(
        pool, title=f"[pm-e2e {tag}] perf probe -- deterministic suite",
        objective="baseline latency of the product read model", proposer="perf_probe",
    )
    bench = await pm.create_benchmark(
        pool, problem_id=problem["id"], name="perf-bench", version=1,
        evaluation_protocol={"verification": "deterministic"},
        environment_specification={"runtime": "linux"},
    )
    pa_id, pa_row = await _make_procedure(pool, f"perf-{tag}-A")
    pb_id, pb_row = await _make_procedure(pool, f"perf-{tag}-B")
    sa = await pm.associate_solution(pool, problem_id=problem["id"], solution_type="procedure",
                                     target_id=pa_id, status="active")
    sb = await pm.associate_solution(pool, problem_id=problem["id"], solution_type="procedure",
                                     target_id=pb_id, status="active")
    exec_a, _, _ = await _run_executions(pool, pa_id, pa_row, n=8, successes=7)
    exec_b, _, _ = await _run_executions(pool, pb_id, pb_row, n=8, successes=3)
    ea = await pm.request_evaluation(pool, problem_id=problem["id"], benchmark_id=bench["id"],
                                     solution_id=sa["id"], procedure_id=pa_id, procedure_version=1,
                                     environment={"runtime": "linux"},
                                     methodology={"verification": "deterministic"})
    eb = await pm.request_evaluation(pool, problem_id=problem["id"], benchmark_id=bench["id"],
                                     solution_id=sb["id"], procedure_id=pb_id, procedure_version=1,
                                     environment={"runtime": "linux"},
                                     methodology={"verification": "deterministic"})
    done_a = await pm.complete_evaluation(pool, ea["id"], execution_ids=exec_a)
    await pm.complete_evaluation(pool, eb["id"], execution_ids=exec_b)
    pid = problem["id"]

    # ---------------- product_model read paths ----------------
    out["product_model.get_problem"] = await measure(
        "get_problem", lambda: pm.get_problem(pool, pid, scope=SCOPE))
    out["product_model.list_problem_solutions"] = await measure(
        "list_problem_solutions", lambda: pm.list_problem_solutions(pool, pid, scope=SCOPE))
    out["product_model.get_benchmark"] = await measure(
        "get_benchmark", lambda: pm.get_benchmark(pool, bench["id"]))
    out["product_model.problem_leaderboard"] = await measure(
        "problem_leaderboard", lambda: pm.problem_leaderboard(pool, pid, scope=SCOPE))
    out["product_model.get_evaluation"] = await measure(
        "get_evaluation", lambda: pm.get_evaluation(pool, ea["id"]))

    # complete_evaluation on a fresh small run (6 linked executions).
    # complete_evaluation is idempotent (INSERT ... ON CONFLICT DO NOTHING +
    # recompute + UPDATE), so we seed ONE eval with 6 executions and call
    # complete_evaluation on it N times -- isolates the aggregation cost.
    ce_pcid, ce_pcrow = await _make_procedure(pool, f"perf-ce-{tag}")
    ce_s = await pm.associate_solution(pool, problem_id=pid, solution_type="procedure",
                                       target_id=ce_pcid, status="active")
    ce_ids, _, _ = await _run_executions(pool, ce_pcid, ce_pcrow, n=6, successes=5)
    ce_ev = await pm.request_evaluation(pool, problem_id=pid, benchmark_id=bench["id"],
                                        solution_id=ce_s["id"], procedure_id=ce_pcid, procedure_version=1,
                                        environment={"runtime": "linux"},
                                        methodology={"verification": "deterministic"})

    out["product_model.complete_evaluation_6exec"] = await measure(
        "complete_evaluation (6 linked execs, idempotent recompute)",
        lambda: pm.complete_evaluation(pool, ce_ev["id"], execution_ids=ce_ids), n=20)

    # ---------------- durable execution ----------------
    # Hoist the plan/graph/procedure seed OUT of the per-iteration factories
    # so the durable numbers measure the durable_run code, not capture_procedure.
    dr_proc_id, dr_pv, dr_plan_id, dr_graph_id = await _plan_chain(pool)

    async def _start_run_once():
        return await start_run(
            pool, execution_plan_id=dr_plan_id, task_graph_id=dr_graph_id,
            procedure_id=dr_proc_id, procedure_version=dr_pv,
            node_orders=[0, 1, 2], deps=DEPS, max_attempts=3, created_by="perf_probe")

    out["durable_run.start_run_3node"] = await measure(
        "start_run (3-node)", _start_run_once, n=20)

    async def _execute_run_once():
        proc_id, pv, plan_id, graph_id = dr_proc_id, dr_pv, dr_plan_id, dr_graph_id
        rid = await start_run(
            pool, execution_plan_id=plan_id, task_graph_id=graph_id,
            procedure_id=proc_id, procedure_version=pv,
            node_orders=[0, 1, 2], deps=DEPS, max_attempts=3, created_by="perf_probe")

        async def cb(order, attempt):
            return {"order": order, "attempt": attempt, "ok": True}

        return await execute_run(pool, rid, deps=DEPS, run_node=cb, worker_id="perf-w1")

    out["durable_run.execute_run_3node_full"] = await measure(
        "execute_run (3-node, trivial cb)", _execute_run_once, n=20)

    async def _resume_run_once():
        rid = await start_run(
            pool, execution_plan_id=dr_plan_id, task_graph_id=dr_graph_id,
            procedure_id=dr_proc_id, procedure_version=dr_pv,
            node_orders=[0, 1, 2], deps=DEPS, max_attempts=3, created_by="perf_probe")
        crashed = {"done": False}

        async def cb(order, attempt):
            if order == 1 and not crashed["done"]:
                crashed["done"] = True
                raise WorkerLost("simulated mid-node worker loss")
            return {"order": order, "attempt": attempt, "ok": True}

        try:
            await execute_run(pool, rid, deps=DEPS, run_node=cb, worker_id="perf-w1")
        except WorkerLost:
            pass
        return await resume_run(pool, rid, deps=DEPS, run_node=cb, worker_id="perf-w2")

    out["durable_run.resume_run_after_worker_loss"] = await measure(
        "resume_run (1 crashed node)", _resume_run_once, n=20)

    # ---------------- MCP layer ----------------
    out["mcp.find_problem"] = await measure(
        "mcp find_problem", lambda: srv.find_problem(f"deterministic suite perf {tag}", ctx))
    out["mcp.inspect_problem"] = await measure(
        "mcp inspect_problem", lambda: srv.inspect_problem(pid, ctx))
    out["mcp.find_best_solution"] = await measure(
        "mcp find_best_solution", lambda: srv.find_best_solution(f"deterministic suite perf {tag}", ctx))

    # ---------------- claim graph overview ----------------
    out["claim_graph_api.get_claim_graph_overview_both"] = await measure(
        "claim_graph overview (link_mode=both)",
        lambda: claim_graph_api.get_claim_graph_overview(pool, scope=SCOPE, link_mode="both"))
    out["claim_graph_api.get_claim_graph_overview_both_no_status"] = await measure(
        "claim_graph overview (with_status=False)",
        lambda: claim_graph_api.get_claim_graph_overview(pool, scope=SCOPE, link_mode="both",
                                                         with_status=False))

    # ---------------- embedding (external API) ----------------
    from app.services.embeddings import Embedder
    emb = Embedder()
    emb_lat = []
    emb_fail = []
    # distinct text per call -> real provider latency, not the in-process cache.
    for i in range(N_EMBED):
        t0 = time.perf_counter()
        try:
            await emb.embed_one(f"perf probe embedding sample {tag} number {i}", "query")
            emb_lat.append((time.perf_counter() - t0) * 1000.0)
        except Exception as e:  # noqa: BLE001
            emb_fail.append(f"{type(e).__name__}: {e}")
    # same-text repeat x5 to show the cache path
    cache_lat = []
    for _ in range(5):
        t0 = time.perf_counter()
        try:
            await emb.embed_one(f"perf probe cached text {tag}", "query")
            cache_lat.append((time.perf_counter() - t0) * 1000.0)
        except Exception as e:  # noqa: BLE001
            emb_fail.append(f"cache: {type(e).__name__}: {e}")
    if emb_lat:
        emb_lat.sort()
        out["embeddings.embed_one_distinct"] = {
            "samples": len(emb_lat),
            "p50_ms": round(statistics.median(emb_lat), 2),
            "p95_ms": round(emb_lat[min(len(emb_lat) - 1, int(len(emb_lat) * 0.95))], 2),
            "mean_ms": round(statistics.fmean(emb_lat), 2),
            "min_ms": round(emb_lat[0], 2),
            "max_ms": round(emb_lat[-1], 2),
            "query_count": None,
            "note": "external provider API (Voyage/Gemini chain); not a DB path",
            "failures": emb_fail,
        }
    else:
        out["embeddings.embed_one_distinct"] = {"samples": 0, "failures": emb_fail or ["no samples"]}
    if cache_lat:
        cache_lat.sort()
        out["embeddings.embed_one_cached_repeat"] = {
            "samples": len(cache_lat),
            "p50_ms": round(statistics.median(cache_lat), 2),
            "mean_ms": round(statistics.fmean(cache_lat), 2),
            "min_ms": round(cache_lat[0], 2),
            "max_ms": round(cache_lat[-1], 2),
            "note": "same text 5x -- calls 2..5 served from in-process _EMBED_CACHE",
            "failures": [],
        }

    out["find_best_way_tier2"] = {
        "samples": 0,
        "note": "not measured, requires sandbox (real repo + model)",
        "failures": [],
    }

    RESULTS_PATH.write_text(json.dumps(out, indent=2, default=str), encoding="utf-8")
    print(f"\nwrote {RESULTS_PATH}", flush=True)
    for k, v in out.items():
        print(f"  {k:52s} p50={v.get('p50_ms')!s:>9} p95={v.get('p95_ms')!s:>9} "
              f"q={v.get('query_count')!s:>4} n={v.get('samples')} "
              f"{'FAIL:' + str(v['failures']) if v.get('failures') else ''}")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
