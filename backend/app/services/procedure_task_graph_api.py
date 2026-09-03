"""
Read-only whole-graph overview of PROCEDURES + TASK NODES, for the
`/procedure-graph` viewer served by the MCP HTTP server.

The procedure counterpart of `claim_graph_api.get_claim_graph_overview`:
no existing reader returns "all procedures and how they connect" --
`procedure_graph_api` is single-procedure-centred (one procedure's own
step/subprocedure expansion). This is the one genuinely new query here,
built directly against `procedures` / `task_nodes` / `edges` via
`scope_predicates()` (CLAUDE.md hard rule), not a new schema or traversal
engine.

Node kinds:
  - ``procedure`` -- a live `procedures` row (``t_invalid IS NULL``).
    Coloured downstream by `verification_state`; ``staleness == 'stale'``
    is surfaced so the viewer can ring it (the same truth the Final-V1
    leaderboard eligibility check uses).
  - ``task``      -- a live `task_nodes` row.

Edge kinds (both endpoints must be in the returned node set):
  - ``version``       -- `procedures --SUPERSEDES--> procedures` (incl. the
    ``DUPLICATE_OF`` custom variant); the version / dedup lineage.
  - ``decomposition`` -- `procedures --OWNS/DECOMPOSES_TO--> task_nodes`;
    the procedure's own task graph.
  - ``hierarchy``     -- `task_nodes --OWNS/PARENT_OF--> task_nodes`.
  - ``subprocedure``  -- `procedures --> procedures` parsed from
    ``steps[].subprocedure_ref.procedure_id`` (composition); derived,
    never stored.

`knowledge_nodes --PRODUCES/CLAIM_OF--> task_nodes` edges are deliberately
NOT included -- claims are the claim-graph viewer's territory.

`TenantScope.unrestricted()` throughout, per `graph.py` / `claim_graph_api`
precedent (today's honest permissive-in-effect posture).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Optional

import asyncpg

from app.services.access import AccessScope, TenantScope, scope_predicates

_DEFAULT_LIMIT = 150
_MAX_LIMIT = 600

_LINK_MODES = {"all", "version", "decomposition"}

# edges.(edge_type, custom_edge_type) -> (kind, relation label). Anything
# not in here is dropped (e.g. CLAIM_OF).
_EDGE_MAP: dict[tuple[str, Optional[str]], tuple[str, str]] = {
    ("SUPERSEDES", None): ("version", "SUPERSEDES"),
    ("SUPERSEDES", "DUPLICATE_OF"): ("version", "DUPLICATE_OF"),
    ("OWNS", "DECOMPOSES_TO"): ("decomposition", "DECOMPOSES_TO"),
    ("OWNS", "PARENT_OF"): ("hierarchy", "PARENT_OF"),
}


def _clamp(v: int, lo: int, hi: int) -> int:
    return max(lo, min(int(v), hi))


async def get_procedure_task_overview(
    pool: asyncpg.Pool,
    *,
    scope: AccessScope,
    limit: int = _DEFAULT_LIMIT,
    q: Optional[str] = None,
    include_stale: bool = False,
    include_tasks: bool = True,
    link_mode: str = "all",
) -> dict[str, Any]:
    """Bounded set of live procedure (and optionally task) NODES, most
    recently valid first, plus the EDGES among that set.

    `limit`   -- max procedure nodes (clamped to [1, 600]); tasks that
                 connect to a shown procedure are added on top, themselves
                 capped at `limit`. One extra procedure row is fetched to
                 report `truncated` honestly.
    `q`       -- case-insensitive substring on a procedure's name/goal or a
                 task's name/description.
    `include_stale`  -- include `staleness = 'stale'` procedures (default:
                        exclude, matching applicability's candidate filter).
    `include_tasks`  -- include task nodes + decomposition/hierarchy edges.
    `link_mode`      -- "all" (default) / "version" / "decomposition".
    """
    limit = _clamp(limit, 1, _MAX_LIMIT)
    link_mode = link_mode if link_mode in _LINK_MODES else "all"
    tenant = TenantScope.unrestricted()
    now = datetime.now(timezone.utc)

    # -- procedure nodes -------------------------------------------------
    p_sql, p_params, p_next = scope_predicates(scope, tenant, alias="p", param_index=2)
    clauses = ["p.t_invalid IS NULL", p_sql]
    if not include_stale:
        clauses.append("p.staleness <> 'stale'")
    # $1 is `limit` (appended last); scope_predicates params are $2.. (it
    # was built with param_index=2); the q param, if any, is $p_next.
    if q:
        clauses.append(f"(p.name ILIKE ${p_next} OR COALESCE(p.goal,'') ILIKE ${p_next})")
    where = " AND ".join(c for c in clauses if c)
    q_like = f"%{q}%" if q else None
    proc_sql = (
        "SELECT p.id::text AS id, p.procedure_id::text AS procedure_id, p.name, p.goal, "
        " p.verification_state, p.staleness::text AS staleness, p.availability::text AS availability, "
        " p.provenance, p.version, p.scope_type, p.scope_entity_id, p.created_by, "
        " p.t_valid, p.steps "
        f"FROM procedures p WHERE {where} "
        "ORDER BY p.t_valid DESC NULLS LAST LIMIT $1"
    )
    call_args: list[Any] = [limit + 1]
    call_args.extend(p_params)
    if q:
        call_args.append(q_like)
    proc_rows = await pool.fetch(proc_sql, *call_args)
    truncated = len(proc_rows) > limit
    proc_rows = proc_rows[:limit]

    nodes: list[dict[str, Any]] = []
    proc_ids: set[str] = set()
    steps_by_proc: dict[str, Any] = {}
    proc_logical_to_row: dict[str, str] = {}
    for r in proc_rows:
        proc_ids.add(r["id"])
        proc_logical_to_row[r["procedure_id"]] = r["id"]
        steps = r["steps"]
        if isinstance(steps, str):
            try:
                steps = json.loads(steps)
            except (ValueError, TypeError):
                steps = []
        steps_by_proc[r["id"]] = steps or []
        nodes.append({
            "id": r["id"], "kind": "procedure", "procedure_id": r["procedure_id"],
            "name": r["name"], "goal": r["goal"],
            "verification_state": r["verification_state"], "staleness": r["staleness"],
            "availability": r["availability"], "provenance": r["provenance"],
            "version": r["version"], "scope_type": r["scope_type"],
            "scope_entity_id": r["scope_entity_id"], "created_by": r["created_by"],
            "t_valid": r["t_valid"].isoformat() if r["t_valid"] else None,
            "step_count": len(steps_by_proc[r["id"]]), "degree": 0,
        })

    # -- edges among procedure nodes (version + subprocedure) ----------
    edges: list[dict[str, Any]] = []
    seen_edge: set[str] = set()

    def _add_edge(eid: str, src: str, tgt: str, kind: str, relation: str,
                  t_valid: Any = None) -> None:
        key = f"{kind}:{src}->{tgt}"
        if key in seen_edge or src == tgt:
            return
        seen_edge.add(key)
        edges.append({
            "id": eid, "source": src, "target": tgt, "kind": kind, "relation": relation,
            "t_valid": t_valid.isoformat() if hasattr(t_valid, "isoformat") else t_valid,
        })

    if proc_ids and link_mode in ("all", "version"):
        rows = await pool.fetch(
            "SELECT id::text, source_id::text AS s, target_id::text AS t, edge_type, "
            " custom_edge_type, t_valid FROM edges "
            "WHERE t_invalid IS NULL AND source_table = 'procedures' AND target_table = 'procedures' "
            "AND edge_type = 'SUPERSEDES' "
            "AND source_id::text = ANY($1::text[]) AND target_id::text = ANY($1::text[])",
            list(proc_ids),
        )
        for r in rows:
            mapped = _EDGE_MAP.get((r["edge_type"], r["custom_edge_type"]))
            if mapped:
                _add_edge(r["id"], r["s"], r["t"], mapped[0], mapped[1], r["t_valid"])

    if proc_ids and link_mode == "all":
        for row_id, steps in steps_by_proc.items():
            for step in steps:
                ref = (step or {}).get("subprocedure_ref") if isinstance(step, dict) else None
                if not isinstance(ref, dict):
                    continue
                target_row = proc_logical_to_row.get(str(ref.get("procedure_id")))
                if target_row:
                    _add_edge(f"sub:{row_id}:{target_row}", row_id, target_row,
                              "subprocedure", "SUBPROCEDURE_OF")

    # -- task nodes + decomposition / hierarchy edges -----------------
    task_ids: set[str] = set()
    if include_tasks and proc_ids and link_mode in ("all", "decomposition"):
        deco = await pool.fetch(
            "SELECT id::text, source_id::text AS s, target_id::text AS t, edge_type, "
            " custom_edge_type, t_valid FROM edges "
            "WHERE t_invalid IS NULL AND source_table = 'procedures' AND target_table = 'task_nodes' "
            "AND edge_type = 'OWNS' AND custom_edge_type = 'DECOMPOSES_TO' "
            "AND source_id::text = ANY($1::text[])",
            list(proc_ids),
        )
        cand_task_ids = {r["t"] for r in deco}
        if cand_task_ids:
            t_sql, t_params, t_next = scope_predicates(scope, tenant, alias="t", param_index=2)
            t_clauses = ["t.t_invalid IS NULL", t_sql, f"t.id::text = ANY($1::text[])"]
            t_args: list[Any] = [list(cand_task_ids)[: _MAX_LIMIT]]
            t_args.extend(t_params)
            if q:
                t_clauses.append(f"(t.name ILIKE ${t_next} OR COALESCE(t.description,'') ILIKE ${t_next})")
                t_args.append(q_like)
            t_rows = await pool.fetch(
                "SELECT t.id::text AS id, t.name, t.description, t.skill_ref, t.provenance, "
                " t.scope_type, t.scope_entity_id, t.created_by, t.t_valid "
                f"FROM task_nodes t WHERE {' AND '.join(t_clauses)} "
                "ORDER BY t.t_valid DESC NULLS LAST LIMIT " + str(limit),
                *t_args,
            )
            for r in t_rows:
                task_ids.add(r["id"])
                nodes.append({
                    "id": r["id"], "kind": "task", "name": r["name"],
                    "description": r["description"], "skill_ref": r["skill_ref"],
                    "provenance": r["provenance"], "scope_type": r["scope_type"],
                    "scope_entity_id": r["scope_entity_id"], "created_by": r["created_by"],
                    "t_valid": r["t_valid"].isoformat() if r["t_valid"] else None,
                    "degree": 0,
                })
            for r in deco:
                if r["t"] in task_ids:
                    _add_edge(r["id"], r["s"], r["t"], "decomposition", "DECOMPOSES_TO",
                              r["t_valid"])
            if task_ids:
                hier = await pool.fetch(
                    "SELECT id::text, source_id::text AS s, target_id::text AS t, t_valid "
                    "FROM edges WHERE t_invalid IS NULL AND source_table = 'task_nodes' "
                    "AND target_table = 'task_nodes' AND edge_type = 'OWNS' "
                    "AND custom_edge_type = 'PARENT_OF' "
                    "AND source_id::text = ANY($1::text[]) AND target_id::text = ANY($1::text[])",
                    list(task_ids),
                )
                for r in hier:
                    _add_edge(r["id"], r["s"], r["t"], "hierarchy", "PARENT_OF", r["t_valid"])

    # -- degree ------------------------------------------------------
    deg: dict[str, int] = {}
    for e in edges:
        deg[e["source"]] = deg.get(e["source"], 0) + 1
        deg[e["target"]] = deg.get(e["target"], 0) + 1
    for n in nodes:
        n["degree"] = deg.get(n["id"], 0)

    by_kind = {"procedure": sum(1 for n in nodes if n["kind"] == "procedure"),
               "task": sum(1 for n in nodes if n["kind"] == "task")}
    edges_by_kind: dict[str, int] = {}
    for e in edges:
        edges_by_kind[e["kind"]] = edges_by_kind.get(e["kind"], 0) + 1

    return {
        "nodes": nodes,
        "edges": edges,
        "counts": {
            "nodes_total": by_kind["procedure"] + by_kind["task"],
            "nodes_shown": len(nodes),
            "by_kind": by_kind,
            "edges": len(edges),
            "edges_by_kind": edges_by_kind,
        },
        "truncated": truncated,
        "link_mode": link_mode,
        "include_stale": include_stale,
        "include_tasks": include_tasks,
        "query": q,
        "generated_at": now.isoformat(),
    }
