"""Public benchmark evidence in the joint fit (docs/plan_2026-10_priors_library_survey.md §2.2).

Two kinds of public result enter the ONE Bayesian model as likelihood, never as heuristics:

  * per-item results (a model + scaffold resolved item X or not) are ordinary 'benchmark'
    observations. When the item is one of our Goals (SWE-rebench rows are) they land on that Goal;
    otherwise the item is an EVIDENCE Goal: public, no embedding (we do not embed outside the
    product), the structural features of its reference patch, and parents
        benchmark node  <-  benchmark/repo node  <-  item
    so its difficulty pools with its repository's and its benchmark's. Evidence Goals shape the
    global parameters (abilities, skills, the card regression, W) and are never stored as posteriors.
  * aggregate-only results (a leaderboard %) are binomial counts on the benchmark node.

numpy only.
"""
from __future__ import annotations

import uuid
from typing import Any, Iterable, Mapping, Optional, Sequence

NAMESPACE = uuid.UUID("6f1d1c3e-8f0b-4b8f-9a77-2f0c9a4e7b21")


def node_id(key: str) -> str:
    """Stable id of a synthetic node or evidence item ("bench:swe-bench-verified", item keys)."""
    return str(uuid.uuid5(NAMESPACE, key))


def benchmark_node(benchmark: str) -> str:
    return node_id(f"bench:{benchmark}")


def repo_node(benchmark: str, repo: str) -> str:
    return node_id(f"bench:{benchmark}/{repo}")


def item_key(benchmark: str, instance_id: str) -> str:
    return f"{benchmark}:{instance_id}"


def assemble(items: Iterable[Mapping[str, Any]], aggregates: Sequence[Mapping[str, Any]] = (),
             known_goals: Optional[set[str]] = None) -> tuple[dict[str, dict], dict[str, list[str]], list[dict]]:
    """(goal_meta additions, parent additions, aggregates with goal_id) for the joint fit.

    items: routing_evidence_items rows. An item that IS one of our Goals (is_goal, or its goal_id is
    in known_goals) only contributes its features; the Goal keeps its own parents."""
    known = known_goals or set()
    meta: dict[str, dict] = {}
    parents: dict[str, list[str]] = {}

    def node(gid: str) -> None:
        meta.setdefault(gid, {"visibility": "public", "embedding": None, "owner_id": None, "synthetic": True})

    for it in items:
        gid = str(it["goal_id"])
        if it.get("is_goal") or gid in known:
            if it.get("features"):
                meta.setdefault(gid, {})["features"] = dict(it["features"])
            continue
        bench, repo = str(it["benchmark"]), it.get("repo")
        top = benchmark_node(bench)
        node(top)
        if repo:
            mid = repo_node(bench, str(repo))
            node(mid)
            parents.setdefault(mid, [top])
            parents[gid] = [mid]
        else:
            parents[gid] = [top]
        meta[gid] = {"visibility": "public", "embedding": None, "owner_id": None, "synthetic": True,
                     "features": dict(it.get("features") or {})}
    out_aggs = []
    for a in aggregates:
        top = benchmark_node(str(a["benchmark"]))
        node(top)
        out_aggs.append({**a, "goal_id": top})
    return meta, parents, out_aggs


def merge_goal_meta(base: Mapping[str, Mapping[str, Any]], extra: Mapping[str, Mapping[str, Any]]) -> dict[str, dict]:
    """base (our Goals) wins on everything except features, which only evidence supplies."""
    out = {g: dict(m) for g, m in base.items()}
    for g, m in extra.items():
        if g in out:
            if m.get("features") is not None:
                out[g]["features"] = m["features"]
        elif not m.keys() <= {"features"}:            # a features-only entry for a Goal we cannot see
            out[g] = dict(m)
    return out
