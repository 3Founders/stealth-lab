"""
Problem / Benchmark / Solution / Evaluation -- the final-V1 product layer
(directive §9-§19, §36-§37).

This is an ASSOCIATION + READ-MODEL service over the existing substrate.
It does not execute anything and it does not copy any target object:

    Problem -> Benchmark -> Solution -> (procedure|task_graph|task)
            -> Evaluation (aggregate over real executions + evidence)
            -> current-best-verified (DERIVED on read, never stored)

Rules honoured here:
  - every read threads scope through access.scope_predicates() (CLAUDE.md).
  - every write runs inside access.tenant_transaction().
  - ranking reuses the empirical Wilson lower bound
    (procedure_extraction.capability.wilson_interval) -- capabilities.py's
    own "reuse, do not reinvent" rule (its module docstring).
  - an Evaluation cannot become 'completed' without real execution lineage
    -- enforced HERE and, as a backstop, by migration 35's trigger.
  - no universal winner field: a benchmark leader is computed per request.
  - unknown stays unknown (aggregate_result NULL, INSUFFICIENT_EVIDENCE).
"""
from __future__ import annotations

import json
from typing import Any, Optional

import asyncpg

from app.services.access import (
    AccessScope,
    TenantScope,
    scope_predicates,
    tenant_transaction,
)
from app.services.procedure_extraction.capability import wilson_interval
from app.utils.ids import uuid7

CREATED_BY = "product_model"

# Minimum completed runs before a solution can be called anything other than
# INSUFFICIENT_EVIDENCE (directive §17: n=2 must never dominate n=500).
MIN_RUNS_FOR_RANKING = 5
# Wilson-lower thresholds for the derived bands (directive §19).
BEST_VERIFIED_FLOOR = 0.70
HIGH_PERFORMING_FLOOR = 0.50
# Two solutions whose Wilson lower bounds are within this are a tie.
TIE_EPSILON = 0.02

_SOLUTION_TABLE = {"procedure": "procedures", "task_graph": "task_graphs", "task": "task_nodes"}
# For a 'procedure' Solution the target is the STABLE logical handle
# (procedures.procedure_id), not a version row -- the exact version is
# pinned later on the Evaluation (§13/§26). task_graph/task target their
# own row id.
_SOLUTION_TARGET_COL = {"procedure": "procedure_id", "task_graph": "id", "task": "id"}


def _commons() -> TenantScope:
    return TenantScope.commons()


def _row(r: asyncpg.Record | None) -> Optional[dict[str, Any]]:
    if r is None:
        return None
    d = dict(r)
    for k, v in list(d.items()):
        if isinstance(v, str) and k in ("constraints", "metadata", "evaluation_protocol",
                                        "environment_specification", "success_criteria",
                                        "comparison_policy", "environment", "metrics",
                                        "verification_summary", "methodology", "input_context",
                                        "expected_outcome", "verification_criteria"):
            try:
                d[k] = json.loads(v)
            except (ValueError, TypeError):
                pass
        if hasattr(v, "isoformat"):
            d[k] = v.isoformat()
        if k in ("id", "problem_id", "benchmark_id", "solution_id", "target_id",
                 "procedure_id", "implementation_id") and v is not None:
            d[k] = str(v)
    return d


# ---------------------------------------------------------------------------
# Problem
# ---------------------------------------------------------------------------
async def create_problem(
    pool: asyncpg.Pool, *, title: str, description: Optional[str] = None,
    objective: Optional[str] = None, constraints: Optional[list] = None,
    status: str = "open", proposer: Optional[str] = None,
    provenance: Optional[str] = None, metadata: Optional[dict] = None,
    owner_id: Optional[str] = None, visibility: str = "public",
    scope_type: Optional[str] = None, scope_entity_id: Optional[str] = None,
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    if not title or not title.strip():
        raise ValueError("problem title is required")
    pid = str(uuid7())
    async with tenant_transaction(pool, tenant_scope or _commons()) as conn:
        r = await conn.fetchrow(
            "INSERT INTO problems (id, title, description, objective, constraints, status, "
            " proposer, provenance, metadata, owner_id, visibility, scope_type, scope_entity_id) "
            "VALUES ($1,$2,$3,$4,$5::jsonb,$6,$7,$8,$9::jsonb,$10,$11,$12,$13) RETURNING *",
            pid, title.strip(), description, objective,
            json.dumps(constraints or []), status, proposer, provenance,
            json.dumps(metadata or {}), owner_id, visibility, scope_type, scope_entity_id,
        )
    return _row(r)


async def get_problem(
    pool: asyncpg.Pool, problem_id: str, *, scope: AccessScope,
    tenant_scope: Optional[TenantScope] = None,
) -> Optional[dict[str, Any]]:
    sql, params, _ = scope_predicates(scope, tenant_scope or TenantScope.unrestricted(),
                                      alias="p", param_index=2)
    r = await pool.fetchrow(
        f"SELECT p.* FROM problems p WHERE p.id = $1 AND {sql}", problem_id, *params,
    )
    return _row(r)


async def list_problems(
    pool: asyncpg.Pool, *, scope: AccessScope, status: Optional[str] = None,
    limit: int = 50, tenant_scope: Optional[TenantScope] = None,
) -> list[dict[str, Any]]:
    clauses, args = [], []
    idx = 1
    if status:
        clauses.append(f"p.status = ${idx}")
        args.append(status)
        idx += 1
    sql, params, _ = scope_predicates(scope, tenant_scope or TenantScope.unrestricted(),
                                      alias="p", param_index=idx)
    args.extend(params)
    idx += len(params)
    where = " AND ".join([*clauses, sql]) if clauses else sql
    args.append(min(int(limit), 200))
    rows = await pool.fetch(
        f"SELECT p.* FROM problems p WHERE {where} ORDER BY p.updated_at DESC LIMIT ${idx}",
        *args,
    )
    return [_row(r) for r in rows]


async def find_problem(
    pool: asyncpg.Pool, query: str, *, scope: AccessScope, limit: int = 10,
    tenant_scope: Optional[TenantScope] = None,
) -> list[dict[str, Any]]:
    """
    NL goal -> Problem(s), ranked. A natural-language goal is not a strict
    boolean query, so the words are OR'd into the tsquery and `ts_rank`
    does the discrimination; a problem must still share at least one
    lexeme with the goal (rank > 0). Searches title + description +
    objective.
    """
    import re as _re
    words = [w for w in _re.split(r"[^a-z0-9]+", query.lower()) if len(w) > 1]
    if not words:
        return []
    tsq = " | ".join(words)  # OR: an NL goal is not a boolean AND query
    sql, params, _ = scope_predicates(scope, tenant_scope or TenantScope.unrestricted(),
                                      alias="p", param_index=2)
    doc = "p.title || ' ' || COALESCE(p.description,'') || ' ' || COALESCE(p.objective,'')"
    rows = await pool.fetch(
        f"SELECT p.*, ts_rank(to_tsvector('english', {doc}), to_tsquery('english', $1)) AS _rank "
        f"FROM problems p WHERE {sql} "
        f"AND to_tsvector('english', {doc}) @@ to_tsquery('english', $1) "
        f"ORDER BY _rank DESC LIMIT ${2 + len(params)}",
        tsq, *params, min(int(limit), 50),
    )
    return [_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------
async def create_benchmark(
    pool: asyncpg.Pool, *, problem_id: str, name: str, description: Optional[str] = None,
    version: int = 1, evaluation_protocol: Optional[dict] = None,
    environment_specification: Optional[dict] = None, success_criteria: Optional[dict] = None,
    comparison_policy: Optional[dict] = None, status: str = "draft",
    provenance: Optional[str] = None, metadata: Optional[dict] = None,
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    bid = str(uuid7())
    async with tenant_transaction(pool, tenant_scope or _commons()) as conn:
        r = await conn.fetchrow(
            "INSERT INTO benchmarks (id, problem_id, name, description, version, "
            " evaluation_protocol, environment_specification, success_criteria, "
            " comparison_policy, status, provenance, metadata) "
            "VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9::jsonb,$10,$11,$12::jsonb) "
            "RETURNING *",
            bid, problem_id, name, description, version,
            json.dumps(evaluation_protocol or {}), json.dumps(environment_specification or {}),
            json.dumps(success_criteria or {}), json.dumps(comparison_policy or {}),
            status, provenance, json.dumps(metadata or {}),
        )
    return _row(r)


async def freeze_benchmark(
    pool: asyncpg.Pool, benchmark_id: str, *, tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    """Make a benchmark's measured meaning immutable (§12). Idempotent."""
    async with tenant_transaction(pool, tenant_scope or _commons()) as conn:
        r = await conn.fetchrow(
            "UPDATE benchmarks SET status='frozen', frozen_at=COALESCE(frozen_at, now()) "
            "WHERE id=$1 RETURNING *", benchmark_id,
        )
    if r is None:
        raise ValueError(f"benchmark {benchmark_id} not found")
    return _row(r)


async def get_benchmark(pool: asyncpg.Pool, benchmark_id: str) -> Optional[dict[str, Any]]:
    r = await pool.fetchrow("SELECT * FROM benchmarks WHERE id=$1", benchmark_id)
    return _row(r)


async def list_problem_benchmarks(pool: asyncpg.Pool, problem_id: str) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT * FROM benchmarks WHERE problem_id=$1 ORDER BY version DESC, created_at DESC",
        problem_id,
    )
    return [_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Solution (association only -- no target copied)
# ---------------------------------------------------------------------------
async def associate_solution(
    pool: asyncpg.Pool, *, problem_id: str, solution_type: str, target_id: str,
    version: int = 1, status: str = "proposed", proposer: Optional[str] = None,
    provenance: Optional[str] = None, metadata: Optional[dict] = None,
    owner_id: Optional[str] = None, scope_type: Optional[str] = None,
    scope_entity_id: Optional[str] = None, tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    target_table = _SOLUTION_TABLE.get(solution_type)
    if target_table is None:
        raise ValueError(f"solution_type must be one of {sorted(_SOLUTION_TABLE)}")
    sid = str(uuid7())
    async with tenant_transaction(pool, tenant_scope or _commons()) as conn:
        id_col = _SOLUTION_TARGET_COL[solution_type]
        exists = await conn.fetchval(
            f"SELECT 1 FROM {target_table} WHERE {id_col}=$1 LIMIT 1",  # noqa: S608 -- table+col from fixed whitelists
            target_id,
        )
        if not exists:
            raise ValueError(
                f"{solution_type} target {target_id} does not exist "
                f"({target_table}.{id_col})"
            )
        r = await conn.fetchrow(
            "INSERT INTO solutions (id, problem_id, solution_type, target_id, target_table, "
            " version, status, proposer, provenance, metadata, owner_id, scope_type, scope_entity_id) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,$13) "
            "ON CONFLICT (problem_id, solution_type, target_id, version) DO UPDATE "
            "  SET status=EXCLUDED.status, metadata=EXCLUDED.metadata, updated_at=now() "
            "RETURNING *",
            sid, problem_id, solution_type, target_id, target_table, version, status,
            proposer, provenance, json.dumps(metadata or {}), owner_id, scope_type, scope_entity_id,
        )
    return _row(r)


async def list_problem_solutions(
    pool: asyncpg.Pool, problem_id: str, *, scope: AccessScope,
    tenant_scope: Optional[TenantScope] = None,
) -> list[dict[str, Any]]:
    """
    A Solution has no independent visibility -- it inherits the Problem's
    (a solution to a private problem is only reachable through that
    problem). Same precedent as task_graphs inheriting an execution
    plan's scope (migration 23). So: gate on the Problem being visible to
    `scope`, then return all its solutions.
    """
    if await get_problem(pool, problem_id, scope=scope, tenant_scope=tenant_scope) is None:
        return []
    rows = await pool.fetch(
        "SELECT s.* FROM solutions s WHERE s.problem_id=$1 ORDER BY s.created_at",
        problem_id,
    )
    return [_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
async def request_evaluation(
    pool: asyncpg.Pool, *, problem_id: str, benchmark_id: str, solution_id: str,
    procedure_id: Optional[str] = None, procedure_version: Optional[int] = None,
    implementation_id: Optional[str] = None, implementation_version: Optional[int] = None,
    environment: Optional[dict] = None, methodology: Optional[dict] = None,
    provenance: Optional[str] = None, tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    eid = str(uuid7())
    async with tenant_transaction(pool, tenant_scope or _commons()) as conn:
        r = await conn.fetchrow(
            "INSERT INTO evaluations (id, problem_id, benchmark_id, solution_id, procedure_id, "
            " procedure_version, implementation_id, implementation_version, environment, "
            " methodology, status, provenance) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9::jsonb,$10::jsonb,'requested',$11) RETURNING *",
            eid, problem_id, benchmark_id, solution_id, procedure_id, procedure_version,
            implementation_id, implementation_version, json.dumps(environment or {}),
            json.dumps(methodology or {}), provenance,
        )
    return _row(r)


async def complete_evaluation(
    pool: asyncpg.Pool, evaluation_id: str, *, execution_ids: list[str],
    aggregate_result: Optional[str] = None, extra_metrics: Optional[dict] = None,
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    """
    Finish an evaluation. §16: an untrusted caller CANNOT fabricate a
    completed result -- `execution_ids` must name real `executions` rows,
    they are linked via `evaluation_executions`, and run_count + the
    outcome metrics are RECOMPUTED here from those executions + their
    evidence, not taken from the caller. `extra_metrics` is merged in only
    for values the substrate cannot derive (e.g. token counts the caller
    measured), never for success/verified counts.
    """
    if not execution_ids:
        raise ValueError("cannot complete an evaluation with no linked executions (§16)")
    async with tenant_transaction(pool, tenant_scope or _commons()) as conn:
        found = await conn.fetch(
            "SELECT id, outcome, started_at, ended_at FROM executions WHERE id = ANY($1::uuid[])",
            execution_ids,
        )
        found_ids = {str(r["id"]) for r in found}
        missing = [x for x in execution_ids if x not in found_ids]
        if missing:
            raise ValueError(f"execution ids not found: {missing}")

        for x in execution_ids:
            await conn.execute(
                "INSERT INTO evaluation_executions (id, evaluation_id, execution_id, role) "
                "VALUES ($1,$2,$3,'benchmark_case_run') ON CONFLICT DO NOTHING",
                str(uuid7()), evaluation_id, x,
            )

        # Recompute from lineage.
        run_count = len(found_ids)
        successes = sum(1 for r in found if r["outcome"] == "success")
        # verified success = a linked execution that ALSO has a supporting
        # evidence row (evidence.target_type='execution' where a writer set
        # it, else fall back to procedure-targeted supporting evidence).
        verified = await conn.fetchval(
            "SELECT count(DISTINCT ee.execution_id) FROM evaluation_executions ee "
            "JOIN executions e ON e.id = ee.execution_id AND e.outcome = 'success' "
            "WHERE ee.evaluation_id = $1 AND EXISTS ("
            "  SELECT 1 FROM evidence ev WHERE ev.t_invalid IS NULL AND ev.direction = 'supports' "
            "  AND ev.evidence_type IN ('execution_result','reproduction') "
            "  AND ((ev.target_type = 'execution' AND ev.target_id = ee.execution_id) "
            "    OR (ev.target_type = 'procedure' AND ev.target_id = e.procedure_id "
            "        AND ev.target_version = e.procedure_version)))",
            evaluation_id,
        ) or 0
        latencies = sorted(
            (r["ended_at"] - r["started_at"]).total_seconds()
            for r in found if r["ended_at"] and r["started_at"]
        )
        p_lo, p_hi = wilson_interval(int(verified), run_count)
        metrics: dict[str, Any] = {
            "run_count": run_count,
            "success_rate": round(successes / run_count, 4) if run_count else None,
            "verified_success_rate": round(verified / run_count, 4) if run_count else None,
            "verified_success_wilson_lower": round(p_lo, 4),
            "verified_success_wilson_upper": round(p_hi, 4),
            "failure_rate": round((run_count - successes) / run_count, 4) if run_count else None,
        }
        if latencies:
            metrics["p50_latency_s"] = round(latencies[len(latencies) // 2], 3)
            metrics["p95_latency_s"] = round(latencies[max(0, int(len(latencies) * 0.95) - 1)], 3)
        for k, v in (extra_metrics or {}).items():
            if k not in ("run_count", "successes", "verified", "verified_success_rate",
                         "success_rate", "verified_success_wilson_lower"):
                metrics[k] = v

        agg = aggregate_result
        if agg not in ("pass", "fail", "partial", "inconclusive", None):
            raise ValueError("aggregate_result must be pass|fail|partial|inconclusive|null")
        if agg is None and run_count:
            agg = "pass" if successes == run_count else "fail" if successes == 0 else "partial"

        r = await conn.fetchrow(
            "UPDATE evaluations SET status='completed', completed_at=now(), "
            " run_count=$2, metrics=$3::jsonb, aggregate_result=$4, "
            " verification_summary=$5::jsonb WHERE id=$1 RETURNING *",
            evaluation_id, run_count, json.dumps(metrics), agg,
            json.dumps({"verified_successes": int(verified), "successes": successes,
                        "run_count": run_count, "source": "recomputed_from_evaluation_executions"}),
        )
    if r is None:
        raise ValueError(f"evaluation {evaluation_id} not found")
    return _row(r)


async def invalidate_evaluation(
    pool: asyncpg.Pool, evaluation_id: str, *, reason: str,
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    async with tenant_transaction(pool, tenant_scope or _commons()) as conn:
        r = await conn.fetchrow(
            "UPDATE evaluations SET status='invalidated', completed_at=COALESCE(completed_at, now()), "
            " metrics = metrics || jsonb_build_object('invalidated_reason', $2::text) "
            "WHERE id=$1 RETURNING *", evaluation_id, reason,
        )
    if r is None:
        raise ValueError(f"evaluation {evaluation_id} not found")
    return _row(r)


async def get_evaluation(pool: asyncpg.Pool, evaluation_id: str) -> Optional[dict[str, Any]]:
    r = await pool.fetchrow("SELECT * FROM evaluations WHERE id=$1", evaluation_id)
    d = _row(r)
    if d:
        d["executions"] = [
            str(x["execution_id"]) for x in await pool.fetch(
                "SELECT execution_id FROM evaluation_executions WHERE evaluation_id=$1",
                evaluation_id,
            )
        ]
    return d


async def list_problem_evaluations(pool: asyncpg.Pool, problem_id: str) -> list[dict[str, Any]]:
    rows = await pool.fetch(
        "SELECT * FROM evaluations WHERE problem_id=$1 ORDER BY created_at DESC", problem_id,
    )
    return [_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Comparability (§18)
# ---------------------------------------------------------------------------
_NON_MATERIAL_ENV_KEYS = {"note", "notes", "operator", "timestamp", "run_id", "seed_label"}


def _material_env(env: dict | None) -> dict:
    return {k: v for k, v in (env or {}).items() if k not in _NON_MATERIAL_ENV_KEYS}


def evaluations_comparable(a: dict[str, Any], b: dict[str, Any]) -> tuple[bool, Optional[str]]:
    """
    Two evaluations may be ranked together only when their benchmark
    version/case set, verification semantics and material environment
    requirements are compatible (§18). Returns (comparable, reason_if_not).
    """
    if a.get("benchmark_id") != b.get("benchmark_id"):
        return False, "different benchmark id (a benchmark version pins the case set + protocol)"
    va = (a.get("methodology") or {}).get("verification")
    vb = (b.get("methodology") or {}).get("verification")
    if va != vb:
        return False, f"different verification semantics ({va!r} vs {vb!r})"
    ea, eb = _material_env(a.get("environment")), _material_env(b.get("environment"))
    if ea != eb:
        return False, f"different material environment requirements ({ea} vs {eb})"
    if a.get("status") != "completed" or b.get("status") != "completed":
        return False, "both evaluations must be completed to be comparable"
    return True, None


# ---------------------------------------------------------------------------
# Leaderboard / current best (§19) -- computed per request, never stored
# ---------------------------------------------------------------------------
def _band(wilson_lower: float, run_count: int, verified: int) -> str:
    if run_count < MIN_RUNS_FOR_RANKING or verified == 0:
        return "INSUFFICIENT_EVIDENCE"
    if wilson_lower >= BEST_VERIFIED_FLOOR:
        return "BEST_VERIFIED"
    if wilson_lower >= HIGH_PERFORMING_FLOOR:
        return "HIGH_PERFORMING"
    return "PROMISING"


# A Solution's completed Evaluations are HISTORICAL evidence; whether that
# Solution is still an eligible *current* leader is a separate question that
# depends on whether the thing it points at is valid RIGHT NOW. Final-V1
# eval Bug #7: a Solution whose underlying Procedure had gone stale kept its
# BEST_VERIFIED band and stayed in current_best. Eligibility is derived here
# from the SAME truth find_applicable_procedures() uses -- the live
# procedures row's `staleness` (== 'stale' disqualifies, matching
# applicability._CANDIDATE_BASE_WHERE / check_hard_constraints) -- never a
# second flag, never a timestamp heuristic.
_STALE_PROCEDURE_REASON = "underlying procedure is stale (procedures.staleness = 'stale')"
_NO_LIVE_PROCEDURE_REASON = "underlying procedure has no live version (fully tombstoned)"
_NO_LIVE_TASK_REASON = "underlying task is no longer live (task_nodes.t_invalid set)"


async def _ineligible_solution_reasons(
    pool: asyncpg.Pool, solutions: list[dict[str, Any]],
) -> dict[str, str]:
    """`solution_id -> reason` for Solutions that must NOT rank as a current
    leader. Absent from the map == eligible.

    - ``procedure`` Solutions: the target_id IS the stable
      ``procedures.procedure_id`` (see ``_SOLUTION_TARGET_COL``). Ineligible
      iff the live row (``t_invalid IS NULL``) is ``staleness = 'stale'`` or
      there is no live row at all -- exactly the disqualifiers
      ``check_hard_constraints`` and ``_CANDIDATE_BASE_WHERE`` apply. This is
      the existing staleness truth; nothing new is computed.
    - ``task`` Solutions: ``task_nodes`` carries no staleness axis, only
      bi-temporal validity, so the only "no longer valid" signal is
      ``t_invalid`` being set. Weaker guarantee than a Procedure, applied
      as-is.
    - ``task_graph`` Solutions: ``task_graphs`` has neither a staleness axis
      nor ``t_invalid`` (``backend/db/23_*.sql`` -- deliberately no
      bi-temporal quartet). There is therefore NO current-validity signal
      for a task_graph-backed Solution; it is never marked ineligible here.
      Documented gap, not a silent assumption of a stronger guarantee.
    """
    by_type: dict[str, dict[str, list[str]]] = {}
    for s in solutions:
        by_type.setdefault(s["solution_type"], {}) \
               .setdefault(str(s["target_id"]), []).append(s["id"])

    out: dict[str, str] = {}

    proc_targets = by_type.get("procedure", {})
    if proc_targets:
        rows = await pool.fetch(
            "SELECT procedure_id::text AS pid, staleness::text AS st FROM procedures "
            "WHERE procedure_id = ANY($1::uuid[]) AND t_invalid IS NULL",
            list(proc_targets),
        )
        live = {r["pid"]: r["st"] for r in rows}
        for tid, sids in proc_targets.items():
            reason = None
            if tid not in live:
                reason = _NO_LIVE_PROCEDURE_REASON
            elif live[tid] == "stale":
                reason = _STALE_PROCEDURE_REASON
            if reason:
                for sid in sids:
                    out[sid] = reason

    task_targets = by_type.get("task", {})
    if task_targets:
        rows = await pool.fetch(
            "SELECT id::text AS tid FROM task_nodes "
            "WHERE id = ANY($1::uuid[]) AND t_invalid IS NULL",
            list(task_targets),
        )
        live_ids = {r["tid"] for r in rows}
        for tid, sids in task_targets.items():
            if tid not in live_ids:
                for sid in sids:
                    out[sid] = _NO_LIVE_TASK_REASON

    return out


async def problem_leaderboard(
    pool: asyncpg.Pool, problem_id: str, *, scope: AccessScope,
    benchmark_id: Optional[str] = None, tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    """
    For each Solution of the Problem, aggregate its COMPLETED evaluations
    (restricted to one benchmark, or the newest benchmark version if not
    given), rank by Wilson lower bound of verified success. Emits the four
    derived states + conditional leaders + ties. Never mutates a stored
    'winner'.
    """
    sols = await list_problem_solutions(pool, problem_id, scope=scope, tenant_scope=tenant_scope)
    benches = await list_problem_benchmarks(pool, problem_id)
    if benchmark_id is None and benches:
        benchmark_id = benches[0]["id"]
    all_evals = await list_problem_evaluations(pool, problem_id)
    completed = [e for e in all_evals if e["status"] == "completed"
                 and (benchmark_id is None or e["benchmark_id"] == benchmark_id)]

    # Bug #7: a completed Evaluation is historical evidence; current-best
    # eligibility is recomputed here from the target's validity RIGHT NOW.
    ineligible = await _ineligible_solution_reasons(pool, sols)

    entries: list[dict[str, Any]] = []
    for s in sols:
        elig = s["id"] not in ineligible
        se = [e for e in completed if e["solution_id"] == s["id"]]
        if not se:
            entry = {"solution_id": s["id"], "solution_type": s["solution_type"],
                     "target_id": s["target_id"], "run_count": 0,
                     "state": "STALE" if not elig else "INSUFFICIENT_EVIDENCE",
                     "eligible": elig, "evaluations": 0}
            if not elig:
                entry["ineligibility_reason"] = ineligible[s["id"]]
            entries.append(entry)
            continue
        # comparability: keep only evals mutually comparable with the first.
        pivot = se[0]
        comparable = [pivot] + [e for e in se[1:] if evaluations_comparable(pivot, e)[0]]
        incomparable = [e for e in se[1:] if not evaluations_comparable(pivot, e)[0]]
        run_count = sum(int(e.get("run_count") or 0) for e in comparable)
        verified = sum(int((e.get("verification_summary") or {}).get("verified_successes", 0))
                       for e in comparable)
        successes = sum(int((e.get("verification_summary") or {}).get("successes", 0))
                        for e in comparable)
        w_lo, w_hi = wilson_interval(verified, run_count)
        def _mean(key: str) -> Optional[float]:
            vals = [float(e["metrics"][key]) for e in comparable
                    if isinstance(e.get("metrics"), dict) and e["metrics"].get(key) is not None]
            return round(sum(vals) / len(vals), 4) if vals else None
        entry = {
            "solution_id": s["id"], "solution_type": s["solution_type"],
            "target_id": s["target_id"],
            "run_count": run_count, "successes": successes, "verified_successes": verified,
            "success_rate": round(successes / run_count, 4) if run_count else None,
            "verified_success_rate": round(verified / run_count, 4) if run_count else None,
            "verified_success_wilson_lower": round(w_lo, 4),
            "verified_success_wilson_upper": round(w_hi, 4),
            "p50_latency_s": _mean("p50_latency_s"), "p95_latency_s": _mean("p95_latency_s"),
            "cost": _mean("cost"), "first_pass_success_rate": _mean("first_pass_success_rate"),
            "evaluations": len(comparable),
            "incomparable_evaluations": len(incomparable),
            "state": _band(w_lo, run_count, verified),
            "eligible": elig,
        }
        if not elig:
            # Historical numbers stay visible on the row; the Solution is
            # simply no longer a current leader (Bug #7). It is never
            # rewritten or deleted.
            entry["state"] = "STALE"
            entry["ineligibility_reason"] = ineligible[s["id"]]
        entries.append(entry)

    # Ineligible entries always sort last, so they can never *outrank* a
    # currently valid Solution; `.get` on the wilson key keeps a Solution
    # with zero completed evaluations from raising here.
    ranked = sorted(entries, key=lambda e: (
        0 if e.get("eligible", True) else 1,
        -e.get("verified_success_wilson_lower", -1.0),
        -e.get("run_count", 0),
    ))
    # current best: the highest Wilson-lower entry that is BEST_VERIFIED,
    # plus any tied within TIE_EPSILON. None if nobody clears the bar.
    best: list[str] = []
    top = next((e for e in ranked if e["state"] == "BEST_VERIFIED"), None)
    if top is not None:
        best = [e["solution_id"] for e in ranked if e["state"] == "BEST_VERIFIED"
                and abs(e["verified_success_wilson_lower"]
                        - top["verified_success_wilson_lower"]) <= TIE_EPSILON]

    def _leader(key: str, *, minimize: bool) -> Optional[str]:
        cand = [e for e in entries if e.get(key) is not None
                and e["state"] not in ("INSUFFICIENT_EVIDENCE", "STALE")
                and e.get("eligible", True)]
        if not cand:
            return None
        pick = (min if minimize else max)(cand, key=lambda e: e[key])
        return pick["solution_id"]

    return {
        "problem_id": problem_id,
        "benchmark_id": benchmark_id,
        "leaderboard": ranked,
        "current_best": best,                       # [] means: no verified solution yet (§38)
        "current_best_is_tie": len(best) > 1,
        "conditional_leaders": {
            "best_reliability": _leader("verified_success_wilson_lower", minimize=False),
            "best_cost": _leader("cost", minimize=True),
            "best_latency": _leader("p95_latency_s", minimize=True),
            "best_first_pass": _leader("first_pass_success_rate", minimize=False),
        },
        "ineligible_solutions": [
            {"solution_id": sid, "reason": r} for sid, r in sorted(ineligible.items())
        ],
        "note": "computed on read from completed-evaluation lineage; no winner is stored. "
                "A Solution whose underlying target is no longer valid (e.g. its "
                "procedure went stale) stays visible in `leaderboard` with its historical "
                "numbers but is state=STALE, excluded from current_best and conditional "
                "leaders (Bug #7).",
    }


async def find_best_way(
    pool: asyncpg.Pool, goal: str, *, scope: AccessScope, limit: int = 5,
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    """
    NL goal -> matching Problem(s) -> that problem's current best VERIFIED
    solution (§38). Never picks a 'best' from text similarity alone: the
    match is a Problem, and the answer is that Problem's evidence-derived
    leaderboard.
    """
    matches = await find_problem(pool, goal, scope=scope, limit=limit, tenant_scope=tenant_scope)
    if not matches:
        return {"goal": goal, "matched_problem": None,
                "result": "no matching problem", "current_best": []}
    p = matches[0]
    lb = await problem_leaderboard(pool, p["id"], scope=scope, tenant_scope=tenant_scope)
    if not lb["current_best"]:
        return {"goal": goal, "matched_problem": p, "benchmark_id": lb["benchmark_id"],
                "result": "no verified solution yet", "current_best": [],
                "leaderboard": lb["leaderboard"]}
    return {"goal": goal, "matched_problem": p, "benchmark_id": lb["benchmark_id"],
            "result": "verified", "current_best": lb["current_best"],
            "current_best_is_tie": lb["current_best_is_tie"],
            "leaderboard": lb["leaderboard"],
            "conditional_leaders": lb["conditional_leaders"],
            "other_matched_problems": [m["id"] for m in matches[1:]]}
