"""
DB-free coverage for app/services/procedure_task_graph_api.py
(get_procedure_task_overview) -- the whole-graph feed the /procedure-graph
viewer renders.

Hand-rolled FakePool that inspects the SQL text of the exact queries the
module issues, same convention as test_claim_graph_api_offline.py /
test_procedure_graph_api_offline.py (each offline file rolls its own).
"""
from __future__ import annotations

import asyncio
import json
from datetime import datetime, timezone

import pytest

from app.services.access import AccessScope
from app.services.procedure_task_graph_api import get_procedure_task_overview

NOW = datetime(2026, 9, 3, tzinfo=timezone.utc)


def _norm(sql: str) -> str:
    return " ".join(sql.split())


def _proc(pid, name, *, vs="candidate", stale="fresh", steps=None):
    return {
        "id": f"row-{pid}", "procedure_id": pid, "name": name, "goal": f"{name} goal",
        "verification_state": vs, "staleness": stale, "availability": "active",
        "provenance": "prior_library", "version": 1, "scope_type": "global",
        "scope_entity_id": None, "created_by": "t", "t_valid": NOW,
        "steps": steps or [],
    }


def _task(tid, name):
    return {
        "id": tid, "name": name, "description": f"{name} desc", "skill_ref": f"skill_{name}",
        "provenance": "company_ingested", "scope_type": "global", "scope_entity_id": None,
        "created_by": "t", "t_valid": NOW,
    }


class FakePool:
    def __init__(self, *, procs, tasks, sup_edges, deco_edges, hier_edges):
        self.procs, self.tasks = procs, tasks
        self.sup_edges, self.deco_edges, self.hier_edges = sup_edges, deco_edges, hier_edges
        self.queries: list[str] = []

    async def fetch(self, sql, *params):
        n = _norm(sql)
        self.queries.append(n)
        if "FROM procedures p WHERE" in n:
            rows = list(self.procs)
            if "p.staleness <> 'stale'" in n:
                rows = [r for r in rows if r["staleness"] != "stale"]
            limit = params[0]
            return rows[:limit]
        if "source_table = 'procedures' AND target_table = 'procedures'" in n:
            return list(self.sup_edges)
        if "source_table = 'procedures' AND target_table = 'task_nodes'" in n:
            return list(self.deco_edges)
        if "FROM task_nodes t WHERE" in n:
            return list(self.tasks)
        if "source_table = 'task_nodes' AND target_table = 'task_nodes'" in n:
            return list(self.hier_edges)
        raise AssertionError(f"unexpected query: {n}")


def _run(coro):
    return asyncio.run(coro)


def test_overview_shape_kinds_edges_degree_counts():
    p1 = _proc("p1", "alpha", vs="verified")
    p2 = _proc("p2", "beta", stale="stale",
               steps=[{"order": 0, "goal": "x"},
                      {"order": 1, "goal": "y", "subprocedure_ref": {"procedure_id": "p1", "version": 1}}])
    pool = FakePool(
        procs=[p1, p2], tasks=[_task("t1", "deploy")],
        sup_edges=[{"id": "e-sup", "s": "row-p2", "t": "row-p1",
                    "edge_type": "SUPERSEDES", "custom_edge_type": None, "t_valid": NOW}],
        deco_edges=[{"id": "e-deco", "s": "row-p1", "t": "t1",
                     "edge_type": "OWNS", "custom_edge_type": "DECOMPOSES_TO", "t_valid": NOW}],
        hier_edges=[],
    )
    out = _run(get_procedure_task_overview(pool, scope=AccessScope.unrestricted(),
                                           include_stale=True))
    by_id = {n["id"]: n for n in out["nodes"]}
    assert by_id["row-p1"]["kind"] == "procedure" and by_id["row-p1"]["verification_state"] == "verified"
    assert by_id["row-p2"]["staleness"] == "stale" and by_id["row-p2"]["step_count"] == 2
    assert by_id["t1"]["kind"] == "task" and by_id["t1"]["skill_ref"] == "skill_deploy"

    kinds = sorted(e["kind"] for e in out["edges"])
    assert kinds == ["decomposition", "subprocedure", "version"]
    sub = next(e for e in out["edges"] if e["kind"] == "subprocedure")
    assert sub["source"] == "row-p2" and sub["target"] == "row-p1"

    assert out["counts"]["by_kind"] == {"procedure": 2, "task": 1}
    assert out["counts"]["edges_by_kind"] == {"version": 1, "subprocedure": 1, "decomposition": 1}
    # degree: p1 touched by version(target) + subproc(target) + deco(source) = 3
    assert by_id["row-p1"]["degree"] == 3
    assert by_id["t1"]["degree"] == 1
    assert out["truncated"] is False


def test_include_stale_false_drops_stale_procedure():
    pool = FakePool(procs=[_proc("p1", "a"), _proc("p2", "b", stale="stale")],
                    tasks=[], sup_edges=[], deco_edges=[], hier_edges=[])
    out = _run(get_procedure_task_overview(pool, scope=AccessScope.unrestricted(),
                                           include_stale=False))
    assert [n["procedure_id"] for n in out["nodes"]] == ["p1"]


def test_link_mode_version_skips_task_and_decomposition_queries():
    pool = FakePool(procs=[_proc("p1", "a")], tasks=[_task("t1", "x")],
                    sup_edges=[], deco_edges=[], hier_edges=[])
    out = _run(get_procedure_task_overview(pool, scope=AccessScope.unrestricted(),
                                           link_mode="version"))
    assert out["counts"]["by_kind"]["task"] == 0
    assert not any("task_nodes t WHERE" in q for q in pool.queries)
    assert any("target_table = 'procedures'" in q for q in pool.queries)


def test_truncated_true_when_more_procedures_than_limit():
    pool = FakePool(procs=[_proc(f"p{i}", f"n{i}") for i in range(5)],
                    tasks=[], sup_edges=[], deco_edges=[], hier_edges=[])
    out = _run(get_procedure_task_overview(pool, scope=AccessScope.unrestricted(), limit=3))
    assert out["counts"]["nodes_shown"] == 3
    assert out["truncated"] is True


def test_include_tasks_false_returns_no_task_nodes():
    pool = FakePool(procs=[_proc("p1", "a")], tasks=[_task("t1", "x")],
                    sup_edges=[],
                    deco_edges=[{"id": "e", "s": "row-p1", "t": "t1", "edge_type": "OWNS",
                                 "custom_edge_type": "DECOMPOSES_TO", "t_valid": NOW}],
                    hier_edges=[])
    out = _run(get_procedure_task_overview(pool, scope=AccessScope.unrestricted(),
                                           include_tasks=False))
    assert out["counts"]["by_kind"]["task"] == 0
    assert out["edges"] == []
