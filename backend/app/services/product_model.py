"""
Benchmark / Solution / Evaluation -- the final-V1 product layer
(directive §9-§19, §36-§37), now hung directly off Goal (migration 110
folded the former separate "Problem" concept into Goal -- they were the
same real-world thing represented twice; Goal is the load-bearing one
with real data and a direct FK from procedures.achieves_goal_id, so
Problem was retired and this layer re-pointed at `goals` instead).

This is an ASSOCIATION + READ-MODEL service over the existing substrate.
It does not execute anything and it does not copy any target object:

    Goal -> Benchmark -> Solution -> (procedure|task_graph|task)
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

Goal creation/search/get itself is NOT duplicated here -- that's
app.services.goals's job (find_or_create_goal's dedup discipline, V0
quality gate, embeddings). This module only lists/finds goals for its
OWN association-layer purposes (list_goals, find_goal) and gates
Benchmark/Solution/Evaluation visibility on a goal being visible
(get_goal_for_product), never re-implements goal creation.
"""
from __future__ import annotations

import json
from typing import Any, Mapping, Optional, Sequence

import asyncpg

from app.services.access import (
    AccessScope,
    TenantScope,
    next_tenant_param_index,
    scope_predicates,
    tenant_predicate,
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
        if k in ("id", "goal_id", "benchmark_id", "solution_id", "target_id",
                 "procedure_id") and v is not None:
            d[k] = str(v)
    d.pop("embedding", None)
    return d


_GOAL_READ_COLUMNS = (
    "g.id, g.canonical_name, g.description, g.objective, g.constraints, "
    "g.input_schema, g.expected_outcome, g.verification_requirement, g.status, "
    "g.metadata, g.resolved_at, g.visibility, g.owner_id, g.scope_type, "
    "g.scope_entity_id, g.provenance, g.created_from, g.aliases, g.tags, "
    "g.version, g.t_valid, g.t_invalid, g.t_created, g.t_expired, g.created_by"
)


def _resolution_clause(resolved: Optional[bool | str], alias: str = "g") -> str:
    if resolved is None or resolved == "all":
        return ""
    if resolved is True or resolved == "resolved":
        return f"{alias}.resolved_at IS NOT NULL"
    if resolved is False or resolved == "unresolved":
        return f"{alias}.resolved_at IS NULL"
    raise ValueError("resolved must be all, resolved, unresolved, or a boolean")


def _public_goal_row(
    row: asyncpg.Record | Mapping[str, Any],
    ranking: Optional[Mapping[str, Any]] = None,
) -> dict[str, Any]:
    result = _row(row)
    if result is None:
        return {}
    result.pop("_rank", None)
    result.pop("ranking_raw", None)
    metadata = result.get("metadata")
    if isinstance(metadata, dict) and "rationale" in metadata:
        result.setdefault("rationale", metadata["rationale"])
    from app.services.goal_ranking import public_goal_ranking, score_goal_candidate

    result["ranking"] = public_goal_ranking(
        ranking if ranking is not None else score_goal_candidate(result)
    )
    return result


def _rank_goal_rows(
    rows: Sequence[asyncpg.Record | Mapping[str, Any]],
    *,
    resolved: Optional[bool | str] = None,
) -> list[dict[str, Any]]:
    from app.services.goal_ranking import rank_goal_candidates

    materialized = [(_row(row), row) for row in rows]
    materialized = [(value, source) for value, source in materialized if value is not None]
    if resolved is True or resolved == "resolved":
        matching = [
            (value, source) for value, source in materialized
            if value.get("resolved_at") is not None
        ]
        if matching:
            materialized = matching
    elif resolved is False or resolved == "unresolved":
        matching = [
            (value, source) for value, source in materialized
            if value.get("resolved_at") is None
        ]
        if matching:
            materialized = matching
    source = {str(value["id"]): original for value, original in materialized}
    ranked = rank_goal_candidates([value for value, _ in materialized])
    return [
        _public_goal_row(source[str(item["goal_id"])], item)
        for item in ranked
        if str(item.get("goal_id")) in source
    ]


# ---------------------------------------------------------------------------
# Goal (list/find only -- create/get live in app.services.goals)
# ---------------------------------------------------------------------------
async def get_goal_for_product(
    pool: asyncpg.Pool, goal_id: str, *, scope: AccessScope,
    tenant_scope: Optional[TenantScope] = None,
) -> Optional[dict[str, Any]]:
    """Existence + visibility gate used throughout this module before
    exposing any Benchmark/Solution/Evaluation -- deliberately lighter
    than app.services.goals.get_goal (no procedure hydration), since every
    caller here only needs "does this goal exist and is it visible to
    `scope`", not the full Goal+Procedures view."""
    sql, params, _ = scope_predicates(scope, tenant_scope or TenantScope.unrestricted(),
                                      alias="g", param_index=2)
    r = await pool.fetchrow(
        f"SELECT {_GOAL_READ_COLUMNS} FROM goals g WHERE g.id = $1 AND g.t_invalid IS NULL AND {sql}",
        goal_id, *params,
    )
    return _public_goal_row(r) if r is not None else None


async def list_goals(
    pool: asyncpg.Pool, *, scope: AccessScope, status: Optional[str] = None,
    resolved: Optional[bool | str] = None, limit: int = 50, offset: int = 0,
    tenant_scope: Optional[TenantScope] = None,
) -> tuple[list[dict[str, Any]], bool]:
    """List visible Goals with resolution filtering and offset pagination."""
    if offset < 0:
        raise ValueError("offset must be non-negative")
    clauses, args = ["g.t_invalid IS NULL"], []
    idx = 1
    if status:
        clauses.append(f"g.status = ${idx}")
        args.append(status)
        idx += 1
    resolution_sql = _resolution_clause(resolved)
    if resolution_sql:
        clauses.append(resolution_sql)
    sql, params, idx = scope_predicates(
        scope, tenant_scope or TenantScope.unrestricted(), alias="g", param_index=idx,
    )
    args.extend(params)
    page_size = min(max(int(limit), 1), 200)
    limit_idx, offset_idx = idx, idx + 1
    args.extend([page_size + 1, max(int(offset), 0)])
    rows = await pool.fetch(
        f"SELECT {_GOAL_READ_COLUMNS} FROM goals g "
        f"WHERE {' AND '.join([*clauses, sql])} "
        f"ORDER BY g.t_created DESC, g.id LIMIT ${limit_idx} OFFSET ${offset_idx}",
        *args,
    )
    ranked = _rank_goal_rows(rows, resolved=resolved)
    return ranked[:page_size], len(rows) > page_size


BROWSE_SPECIFICS_PREVIEW = 6
_LIVE_GOAL_STATUSES_SQL = "('active', 'candidate')"


async def list_goals_browse(
    pool: asyncpg.Pool, *, scope: AccessScope,
    resolved: Optional[bool | str] = None, limit: int = 50, offset: int = 0,
    tenant_scope: Optional[TenantScope] = None,
) -> tuple[list[dict[str, Any]], bool]:
    """The default way to browse Goals: the most abstract ones first.

    An entry is a Goal with NO visible accepted parent. `browse_kind` says which:
    "root" (>=1 visible accepted specific Goal beneath it) or "standalone" (no
    accepted edges at all -- listed too, so a brand-new Goal never vanishes
    just because placement hasn't attached it yet). Only `accepted`
    SPECIALIZES edges count; proposed/rejected are non-routing evidence.

    Visibility is applied to the EDGE ENDPOINTS as well as the Goal: a private
    parent does not stop a Goal being a root for a viewer who can't see that
    parent, and a private descendant contributes nothing to a public root's
    `specific_count` or `specifics`. Nothing here reads or writes
    `resolved_at` beyond the caller's own filter, so resolution never
    propagates through the hierarchy.

    Order is roots by how many Goals sit directly beneath them, then
    standalone Goals, newest first. It is deliberately NOT abstraction_level
    (levels are derived and equal depth is not equal category); direct count is
    the cheap page-local proxy for component size.
    """
    if offset < 0:
        raise ValueError("offset must be non-negative")
    tenant = tenant_scope or TenantScope.unrestricted()
    clauses = ["g.t_invalid IS NULL", f"g.status IN {_LIVE_GOAL_STATUSES_SQL}"]
    resolution_sql = _resolution_clause(resolved)
    if resolution_sql:
        clauses.append(resolution_sql)
    args: list[Any] = []
    goal_sql, goal_params, idx = scope_predicates(scope, tenant, alias="g", param_index=1)
    args.extend(goal_params)
    clauses.append(goal_sql)
    rel_sql, rel_params = tenant_predicate(tenant, alias="r", param_index=idx)
    idx = next_tenant_param_index(tenant, idx)
    args.extend(rel_params)
    parent_sql, parent_params, idx = scope_predicates(scope, tenant, alias="pg", param_index=idx)
    args.extend(parent_params)
    child_sql, child_params, idx = scope_predicates(scope, tenant, alias="cg", param_index=idx)
    args.extend(child_params)
    page_size = min(max(int(limit), 1), 200)
    limit_idx, offset_idx = idx, idx + 1
    args.extend([page_size + 1, max(int(offset), 0)])

    edge = (
        "r.relation_type = 'SPECIALIZES' AND r.status = 'accepted' "
        f"AND {{other}}.t_invalid IS NULL AND {{other}}.status IN {_LIVE_GOAL_STATUSES_SQL} "
        "AND COALESCE(r.scope_type, 'global') = COALESCE(g.scope_type, 'global') "
        "AND r.scope_entity_id IS NOT DISTINCT FROM g.scope_entity_id "
        "AND COALESCE(r.scope_type, 'global') = COALESCE({other}.scope_type, 'global') "
        "AND r.scope_entity_id IS NOT DISTINCT FROM {other}.scope_entity_id "
        f"AND {rel_sql} AND {{other_vis}}"
    )
    parent_edge = edge.format(other="pg", other_vis=parent_sql)
    child_edge = edge.format(other="cg", other_vis=child_sql)
    rows = await pool.fetch(
        f"""
        WITH base AS (
            SELECT {_GOAL_READ_COLUMNS},
                   (SELECT count(*) FROM goal_relations r
                      JOIN goals cg ON cg.id = r.specific_goal_id
                     WHERE r.abstract_goal_id = g.id AND {child_edge}) AS specific_count,
                   EXISTS (SELECT 1 FROM goal_relations r
                      JOIN goals pg ON pg.id = r.abstract_goal_id
                     WHERE r.specific_goal_id = g.id AND {parent_edge}) AS has_parent
              FROM goals g
             WHERE {' AND '.join(clauses)}
        )
        SELECT * FROM base WHERE NOT has_parent
         ORDER BY (specific_count > 0) DESC, specific_count DESC, t_created DESC, id
         LIMIT ${limit_idx} OFFSET ${offset_idx}
        """,
        *args,
    )
    page = rows[:page_size]
    entries: list[dict[str, Any]] = []
    for row in page:
        entry = _public_goal_row(row)
        entry.pop("has_parent", None)
        count = int(row["specific_count"] or 0)
        entry["specific_count"] = count
        entry["browse_kind"] = "root" if count else "standalone"
        entry["specifics"] = []
        entries.append(entry)
    root_ids = [str(e["id"]) for e in entries if e["specific_count"]]
    if root_ids:
        # Fresh predicate/param sequence: every placeholder here is used, and
        # the child endpoint gets the same visibility + tenant + same-scope rules.
        s_args: list[Any] = [root_ids]
        s_rel_sql, s_rel_params = tenant_predicate(tenant, alias="r", param_index=2)
        s_idx = next_tenant_param_index(tenant, 2)
        s_args.extend(s_rel_params)
        s_child_sql, s_child_params, s_idx = scope_predicates(scope, tenant, alias="cg", param_index=s_idx)
        s_args.extend(s_child_params)
        spec_rows = await pool.fetch(
            f"""
            SELECT root_id, id, canonical_name, status, resolved_at FROM (
                SELECT r.abstract_goal_id::text AS root_id, cg.id::text AS id, cg.canonical_name,
                       cg.status, cg.resolved_at,
                       row_number() OVER (PARTITION BY r.abstract_goal_id
                                          ORDER BY cg.canonical_name, cg.id) AS rn
                  FROM goal_relations r
                  JOIN goals g ON g.id = r.abstract_goal_id
                  JOIN goals cg ON cg.id = r.specific_goal_id
                 WHERE r.abstract_goal_id = ANY($1::uuid[])
                   AND r.relation_type = 'SPECIALIZES' AND r.status = 'accepted'
                   AND cg.t_invalid IS NULL AND cg.status IN {_LIVE_GOAL_STATUSES_SQL}
                   AND COALESCE(r.scope_type, 'global') = COALESCE(g.scope_type, 'global')
                   AND r.scope_entity_id IS NOT DISTINCT FROM g.scope_entity_id
                   AND COALESCE(r.scope_type, 'global') = COALESCE(cg.scope_type, 'global')
                   AND r.scope_entity_id IS NOT DISTINCT FROM cg.scope_entity_id
                   AND {s_rel_sql} AND {s_child_sql}
            ) t WHERE rn <= {BROWSE_SPECIFICS_PREVIEW}
            ORDER BY root_id, rn
            """,
            *s_args,
        )
        by_root: dict[str, list[dict[str, Any]]] = {}
        for spec in spec_rows:
            by_root.setdefault(spec["root_id"], []).append({
                "id": spec["id"], "canonical_name": spec["canonical_name"],
                "status": spec["status"], "resolved_at": spec["resolved_at"],
            })
        for entry in entries:
            entry["specifics"] = by_root.get(str(entry["id"]), [])
    return entries, len(rows) > page_size


async def find_goal(
    pool: asyncpg.Pool, query: str, *, scope: AccessScope,
    resolved: Optional[bool | str] = None, limit: int = 10, offset: int = 0,
    tenant_scope: Optional[TenantScope] = None,
) -> tuple[list[dict[str, Any]], bool]:
    """Find visible Goals and return one page plus a continuation flag."""
    if offset < 0:
        raise ValueError("offset must be non-negative")
    import re as _re
    words = [w for w in _re.split(r"[^a-z0-9]+", query.lower()) if len(w) > 1]
    if not words:
        return [], False
    tsq = " | ".join(words)
    doc = "g.canonical_name || ' ' || COALESCE(g.description,'') || ' ' || COALESCE(g.objective,'')"
    clauses = ["g.t_invalid IS NULL"]
    resolution_sql = _resolution_clause(resolved)
    if resolution_sql:
        clauses.append(resolution_sql)
    sql, params, idx = scope_predicates(
        scope, tenant_scope or TenantScope.unrestricted(), alias="g", param_index=2,
    )
    page_size = min(max(int(limit), 1), 50)
    limit_idx, offset_idx = idx, idx + 1
    rows = await pool.fetch(
        f"SELECT {_GOAL_READ_COLUMNS}, "
        f"ts_rank(to_tsvector('english', {doc}), to_tsquery('english', $1)) AS _rank "
        f"FROM goals g WHERE {' AND '.join([*clauses, sql])} "
        f"AND to_tsvector('english', {doc}) @@ to_tsquery('english', $1) "
        f"ORDER BY _rank DESC, g.id LIMIT ${limit_idx} OFFSET ${offset_idx}",
        tsq, *params, page_size + 1, max(int(offset), 0),
    )
    ranked = _rank_goal_rows(rows, resolved=resolved)
    return ranked[:page_size], len(rows) > page_size


# ---------------------------------------------------------------------------
# Benchmark
# ---------------------------------------------------------------------------
async def create_benchmark(
    pool: asyncpg.Pool, *, goal_id: str, name: str, description: Optional[str] = None,
    version: int = 1, evaluation_protocol: Optional[dict] = None,
    environment_specification: Optional[dict] = None, success_criteria: Optional[dict] = None,
    comparison_policy: Optional[dict] = None, status: str = "draft",
    provenance: Optional[str] = None, metadata: Optional[dict] = None,
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    bid = str(uuid7())
    async with tenant_transaction(pool, tenant_scope or _commons()) as conn:
        r = await conn.fetchrow(
            "INSERT INTO benchmarks (id, goal_id, name, description, version, "
            " evaluation_protocol, environment_specification, success_criteria, "
            " comparison_policy, status, provenance, metadata) "
            "VALUES ($1,$2,$3,$4,$5,$6::jsonb,$7::jsonb,$8::jsonb,$9::jsonb,$10,$11,$12::jsonb) "
            "RETURNING *",
            bid, goal_id, name, description, version,
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


async def get_benchmark(
    pool: asyncpg.Pool, benchmark_id: str, *, scope: AccessScope,
    tenant_scope: Optional[TenantScope] = None,
) -> Optional[dict[str, Any]]:
    """A Benchmark has no independent visibility -- it inherits the owning
    Goal's (same rule as ``list_goal_solutions``). Resolve the row, then
    gate on the Goal being visible to ``scope``; otherwise it is
    indistinguishable from "not found" (Bug #8)."""
    r = await pool.fetchrow("SELECT * FROM benchmarks WHERE id=$1", benchmark_id)
    if r is None:
        return None
    if await get_goal_for_product(pool, str(r["goal_id"]), scope=scope,
                                  tenant_scope=tenant_scope) is None:
        return None
    return _row(r)


async def list_goal_benchmarks(
    pool: asyncpg.Pool, goal_id: str, *, scope: AccessScope,
    tenant_scope: Optional[TenantScope] = None,
) -> list[dict[str, Any]]:
    """Benchmarks inherit the Goal's visibility -- gate on the Goal first,
    then return all of its benchmarks (Bug #8)."""
    if await get_goal_for_product(pool, goal_id, scope=scope, tenant_scope=tenant_scope) is None:
        return []
    rows = await pool.fetch(
        "SELECT * FROM benchmarks WHERE goal_id=$1 ORDER BY version DESC, created_at DESC",
        goal_id,
    )
    return [_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Solution (association only -- no target copied)
# ---------------------------------------------------------------------------
async def associate_solution(
    pool: asyncpg.Pool, *, goal_id: str, solution_type: str, target_id: str,
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
            "INSERT INTO solutions (id, goal_id, solution_type, target_id, target_table, "
            " version, status, proposer, provenance, metadata, owner_id, scope_type, scope_entity_id) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb,$11,$12,$13) "
            "ON CONFLICT (goal_id, solution_type, target_id, version) DO UPDATE "
            "  SET status=EXCLUDED.status, metadata=EXCLUDED.metadata, updated_at=now() "
            "RETURNING *",
            sid, goal_id, solution_type, target_id, target_table, version, status,
            proposer, provenance, json.dumps(metadata or {}), owner_id, scope_type, scope_entity_id,
        )
    return _row(r)


async def list_goal_solutions(
    pool: asyncpg.Pool, goal_id: str, *, scope: AccessScope,
    tenant_scope: Optional[TenantScope] = None,
) -> list[dict[str, Any]]:
    """
    A Solution has no independent visibility -- it inherits the Goal's (a
    solution to a private goal is only reachable through that goal). Same
    precedent as task_graphs inheriting an execution plan's scope
    (migration 23). So: gate on the Goal being visible to `scope`, then
    return all its solutions.
    """
    if await get_goal_for_product(pool, goal_id, scope=scope, tenant_scope=tenant_scope) is None:
        return []
    rows = await pool.fetch(
        "SELECT s.* FROM solutions s WHERE s.goal_id=$1 ORDER BY s.created_at",
        goal_id,
    )
    return [_row(r) for r in rows]


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------
async def request_evaluation(
    pool: asyncpg.Pool, *, goal_id: str, benchmark_id: str, solution_id: str,
    procedure_id: Optional[str] = None, procedure_version: Optional[int] = None,
    environment: Optional[dict] = None, methodology: Optional[dict] = None,
    provenance: Optional[str] = None, tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    eid = str(uuid7())
    async with tenant_transaction(pool, tenant_scope or _commons()) as conn:
        # N1 hardening: goal_id/benchmark_id/solution_id/procedure_id/
        # procedure_version are never trusted as a self-consistent bundle
        # just because the client sent them together. solution_id and
        # benchmark_id are resolved server-side and checked against
        # goal_id, and (for procedure solutions) the real target
        # procedure+version is resolved from the solution's target_id --
        # not from whatever procedure_id/procedure_version the client
        # separately supplied -- so a mismatched pairing can never be
        # recorded, and downstream (the benchmark used/validated
        # lifecycle, complete_evaluation's own lineage checks) can trust
        # the Evaluation's own stored columns.
        solution = await conn.fetchrow("SELECT * FROM solutions WHERE id = $1", solution_id)
        if solution is None:
            raise ValueError(f"solution {solution_id} not found")
        if str(solution["goal_id"]) != str(goal_id):
            raise ValueError(
                f"solution {solution_id} belongs to goal {solution['goal_id']}, not {goal_id}"
            )

        benchmark = await conn.fetchrow("SELECT * FROM benchmarks WHERE id = $1", benchmark_id)
        if benchmark is None:
            raise ValueError(f"benchmark {benchmark_id} not found")
        if str(benchmark["goal_id"]) != str(goal_id):
            raise ValueError(
                f"benchmark {benchmark_id} belongs to goal {benchmark['goal_id']}, not {goal_id}"
            )

        if solution["solution_type"] == "procedure":
            # _SOLUTION_TARGET_COL: a procedure Solution's target_id IS the
            # stable procedure_id already, not a version row id -- the live
            # (t_invalid IS NULL) row's version is what an Evaluation pins.
            resolved_procedure_id = str(solution["target_id"])
            proc_row = await conn.fetchrow(
                "SELECT version FROM procedures WHERE procedure_id = $1 AND t_invalid IS NULL",
                solution["target_id"],
            )
            if proc_row is None:
                raise ValueError(
                    f"solution {solution_id} targets procedure {resolved_procedure_id} which has no live version"
                )
            resolved_procedure_version = proc_row["version"]
            if procedure_id is not None and str(procedure_id) != resolved_procedure_id:
                raise ValueError(
                    f"procedure_id {procedure_id} does not match solution {solution_id}'s "
                    f"actual procedure {resolved_procedure_id}"
                )
            if procedure_version is not None and procedure_version != resolved_procedure_version:
                raise ValueError(
                    f"procedure_version {procedure_version} does not match solution {solution_id}'s "
                    f"actual procedure version {resolved_procedure_version}"
                )
            procedure_id, procedure_version = resolved_procedure_id, resolved_procedure_version

        r = await conn.fetchrow(
            "INSERT INTO evaluations (id, goal_id, benchmark_id, solution_id, procedure_id, "
            " procedure_version, environment, "
            " methodology, status, provenance) "
            "VALUES ($1,$2,$3,$4,$5,$6,$7::jsonb,$8::jsonb,'requested',$9) RETURNING *",
            eid, goal_id, benchmark_id, solution_id, procedure_id, procedure_version,
            json.dumps(environment or {}),
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
        # B4 hardening: load the Evaluation FIRST -- its own goal_id/
        # benchmark_id/procedure_id/procedure_version were fixed at
        # request_evaluation() time and are never parameters of this
        # call, so "does this evaluation belong to the right Benchmark/
        # Goal" is already structurally true by construction. What is
        # NOT structurally true, and what this hardening adds, is
        # verifying the referenced executions actually belong to the
        # SAME Procedure+version this evaluation was requested against.
        evaluation = await conn.fetchrow("SELECT * FROM evaluations WHERE id = $1 FOR UPDATE", evaluation_id)
        if evaluation is None:
            raise ValueError(f"evaluation {evaluation_id} not found")
        if evaluation["status"] == "completed":
            # Idempotent (§34/H): a retry with the EXACT same execution
            # set returns the already-completed row unchanged, never
            # rewritten with a possibly-different result. A retry that
            # names a different execution set is refused outright rather
            # than silently overwriting a completed, immutable-by-intent
            # result.
            existing_exec_ids = {
                str(r["execution_id"]) for r in
                await conn.fetch("SELECT execution_id FROM evaluation_executions WHERE evaluation_id = $1", evaluation_id)
            }
            if existing_exec_ids == set(execution_ids):
                return _row(evaluation)
            raise ValueError(
                f"evaluation {evaluation_id} is already completed and cannot be re-completed "
                "with a different set of executions"
            )

        found = await conn.fetch(
            "SELECT id, outcome, procedure_id, procedure_version, started_at, ended_at "
            "FROM executions WHERE id = ANY($1::uuid[])",
            execution_ids,
        )
        found_ids = {str(r["id"]) for r in found}
        missing = [x for x in execution_ids if x not in found_ids]
        if missing:
            raise ValueError(f"execution ids not found: {missing}")

        # Every referenced execution must be of the SAME Procedure +
        # version this Evaluation was requested against -- otherwise a
        # caller could "prove" a benchmark passed using executions of an
        # entirely unrelated Procedure (or even Goal).
        if evaluation["procedure_id"] is not None:
            mismatched = [
                str(r["id"]) for r in found
                if str(r["procedure_id"]) != str(evaluation["procedure_id"])
                or (evaluation["procedure_version"] is not None and r["procedure_version"] != evaluation["procedure_version"])
            ]
            if mismatched:
                raise ValueError(
                    f"execution(s) {mismatched} do not match this evaluation's own procedure/version "
                    f"({evaluation['procedure_id']}, v{evaluation['procedure_version']})"
                )

        # Every referenced execution must have actually finished with a
        # real terminal outcome -- a still-running or never-started
        # execution proves nothing.
        unfinished = [str(r["id"]) for r in found if r["outcome"] is None or r["ended_at"] is None]
        if unfinished:
            raise ValueError(f"execution(s) {unfinished} have not completed -- no terminal outcome recorded")

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

        # B4 hardening: aggregate_result is ALWAYS derived server-side
        # from the recomputed run_count/successes above -- never taken
        # from the caller as authoritative. A caller MAY optionally send
        # a requested result (kept for API compatibility), but it is
        # only ever a claim to check, not a fact to record: a mismatch
        # is rejected outright rather than silently overridden or
        # silently trusted.
        derived_agg = "pass" if successes == run_count else "fail" if successes == 0 else "partial"
        if aggregate_result is not None:
            if aggregate_result not in ("pass", "fail", "partial", "inconclusive"):
                raise ValueError("aggregate_result must be pass|fail|partial|inconclusive")
            if aggregate_result != derived_agg:
                raise ValueError(
                    f"aggregate_result={aggregate_result!r} does not match the server-derived result "
                    f"{derived_agg!r} ({successes}/{run_count} successes, {verified} verified) -- "
                    "the client-requested result is never trusted, only confirmed against real "
                    "execution/evidence data"
                )
        agg = derived_agg

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


async def get_evaluation(
    pool: asyncpg.Pool, evaluation_id: str, *, scope: AccessScope,
    tenant_scope: Optional[TenantScope] = None,
) -> Optional[dict[str, Any]]:
    """An Evaluation inherits the owning Goal's visibility. Resolve the
    row, gate on the Goal being visible to ``scope``, then hydrate the
    linked execution ids (Bug #8)."""
    r = await pool.fetchrow("SELECT * FROM evaluations WHERE id=$1", evaluation_id)
    if r is None:
        return None
    if await get_goal_for_product(pool, str(r["goal_id"]), scope=scope,
                                  tenant_scope=tenant_scope) is None:
        return None
    d = _row(r)
    if d:
        d["executions"] = [
            str(x["execution_id"]) for x in await pool.fetch(
                "SELECT execution_id FROM evaluation_executions WHERE evaluation_id=$1",
                evaluation_id,
            )
        ]
    return d


async def list_goal_evaluations(
    pool: asyncpg.Pool, goal_id: str, *, scope: AccessScope,
    tenant_scope: Optional[TenantScope] = None,
) -> list[dict[str, Any]]:
    """Evaluations inherit the Goal's visibility -- gate on the Goal
    first, then return all of its evaluations (Bug #8)."""
    if await get_goal_for_product(pool, goal_id, scope=scope, tenant_scope=tenant_scope) is None:
        return []
    rows = await pool.fetch(
        "SELECT * FROM evaluations WHERE goal_id=$1 ORDER BY created_at DESC", goal_id,
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
        from app.services.shards import fanout_fetch
        rows = await fanout_fetch(
            pool,
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


async def goal_leaderboard(
    pool: asyncpg.Pool, goal_id: str, *, scope: AccessScope,
    benchmark_id: Optional[str] = None, tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    """
    For each Solution of the Goal, aggregate its COMPLETED evaluations
    (restricted to one benchmark, or the newest benchmark version if not
    given), rank by Wilson lower bound of verified success. Emits the four
    derived states + conditional leaders + ties. Never mutates a stored
    'winner'.
    """
    # Gate the whole read on the Goal being visible to `scope` -- so a
    # private Goal's leaderboard can't even leak its benchmark_id to a
    # stranger (Bug #8). The sub-calls below are each independently gated
    # too; this keeps the empty shape clean and mirrors the REST route.
    if await get_goal_for_product(pool, goal_id, scope=scope, tenant_scope=tenant_scope) is None:
        return {
            "goal_id": goal_id, "benchmark_id": None, "leaderboard": [],
            "current_best": [], "current_best_is_tie": False,
            "conditional_leaders": {"best_reliability": None, "best_cost": None,
                                    "best_latency": None, "best_first_pass": None},
            "ineligible_solutions": [],
            "note": "goal not found or out of scope",
        }
    sols = await list_goal_solutions(pool, goal_id, scope=scope, tenant_scope=tenant_scope)
    benches = await list_goal_benchmarks(pool, goal_id, scope=scope, tenant_scope=tenant_scope)
    if benchmark_id is None and benches:
        benchmark_id = benches[0]["id"]
    all_evals = await list_goal_evaluations(pool, goal_id, scope=scope,
                                            tenant_scope=tenant_scope)
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
        "goal_id": goal_id,
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


async def find_best_verified_solution(
    pool: asyncpg.Pool, goal: str, *, scope: AccessScope, limit: int = 5,
    tenant_scope: Optional[TenantScope] = None,
) -> dict[str, Any]:
    """
    NL goal text -> matching Goal(s) -> that goal's current best VERIFIED
    solution (§38). Never picks a 'best' from text similarity alone: the
    match is a Goal, and the answer is that Goal's evidence-derived
    leaderboard.

    Renamed from the former `find_best_way` (product_model.py's own,
    unrelated to app/mcp_server/server.py's `find_best_way` MCP tool,
    which actually EXECUTES an agent -- two different functions had
    confusingly identical names in two different modules; this one only
    ever looks up an existing verified leaderboard entry, never runs
    anything).
    """
    matches, _has_more = await find_goal(
        pool, goal, scope=scope, limit=limit, tenant_scope=tenant_scope,
    )
    if not matches:
        return {"goal": goal, "matched_goal": None,
                "result": "no matching goal", "current_best": []}
    g = matches[0]
    lb = await goal_leaderboard(pool, g["id"], scope=scope, tenant_scope=tenant_scope)
    if not lb["current_best"]:
        return {"goal": goal, "matched_goal": g, "benchmark_id": lb["benchmark_id"],
                "result": "no verified solution yet", "current_best": [],
                "leaderboard": lb["leaderboard"]}
    return {"goal": goal, "matched_goal": g, "benchmark_id": lb["benchmark_id"],
            "result": "verified", "current_best": lb["current_best"],
            "current_best_is_tie": lb["current_best_is_tie"],
            "leaderboard": lb["leaderboard"],
            "conditional_leaders": lb["conditional_leaders"],
            "other_matched_goals": [m["id"] for m in matches[1:]]}
