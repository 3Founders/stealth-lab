"""Import BenchmarkTasks into Kel -- idempotent (re-running changes nothing).

Per task:
  1. its domain Goals exist (exact-name identity, so shared across tasks and runs);
  2. the task's Goal exists (named by its statement; metadata records the source);
  3. an ACCEPTED `SPECIALIZES` edge task -> each domain (provenance
     `benchmark_import:<source>`), so the hierarchy pools evidence per domain;
  4. a FROZEN benchmark on the task Goal:
       evaluation_protocol  the unittest suite + which tests are the visible runtime
                            check (the rest are hidden; the gold grade is all of them)
       environment_specification  harness, libraries, entry point, prompt prefix
       metadata             source, version, external id, split, reference-solution hash
     Imported benchmarks are frozen directly by this operator import -- they are
     published, reviewed datasets -- and are deliberately NOT propagated to
     neighbouring Goals (that path is for community benchmarks, via the freeze API).

Returns a manifest (external_id -> goal_id, benchmark_id, split, tests) for runners
and `routing-import`.
"""
from __future__ import annotations

import logging
from collections import Counter
from typing import Any, Optional, Sequence

from app.benchmarks.tasks import BenchmarkTask, stable_hash
from app.services.access import AccessScope, TenantScope

log = logging.getLogger(__name__)
GOAL_PROVENANCE = "prior_library"
EDGE_DECIDER = "benchmark_import"


def summarize(tasks: Sequence[BenchmarkTask]) -> dict[str, Any]:
    usable = [t for t in tasks if t.excluded_reason is None]
    return {
        "tasks": len(tasks), "usable": len(usable), "excluded": len(tasks) - len(usable),
        "excluded_reasons": dict(Counter(t.excluded_reason.split(":")[0] for t in tasks if t.excluded_reason)),
        "by_domain": dict(Counter(t.domains[0] for t in usable)),
        "by_split": dict(Counter(t.split for t in usable)),
        "visible_tests_mean": round(sum(len(t.visible_tests) for t in usable) / max(len(usable), 1), 2),
        "tests_mean": round(sum(len(t.test_names) for t in usable) / max(len(usable), 1), 2),
    }


async def _goal(pool: Any, name: str, description: Optional[str], metadata: dict, embedder: Any,
                judge_mode: str = "none") -> str:
    from app.services.goals import find_or_create_goal

    created = await find_or_create_goal(
        pool, canonical_name=name, description=description, scope_type="global", provenance=GOAL_PROVENANCE,
        status="active", metadata=metadata, judge_mode=judge_mode, embedder=embedder)
    return str(created["id"])


async def _accepted_edge(pool: Any, child: str, parent: str, source: str) -> bool:
    existing = await pool.fetchval(
        "SELECT status FROM goal_relations WHERE specific_goal_id = $1::uuid AND abstract_goal_id = $2::uuid "
        "AND relation_type = 'SPECIALIZES'", child, parent)
    if existing in ("accepted", "rejected"):
        return False                     # already placed, or a reviewer rejected it: never overrule a human
    from app.services.goal_abstraction import persist_goal_relation

    await persist_goal_relation(
        pool, child, parent, status="accepted", provenance=f"benchmark_import:{source}",
        access_scope=AccessScope.unrestricted(), tenant_scope=TenantScope.commons(), decided_by=EDGE_DECIDER,
        decision_metadata={"reason": "benchmark task placed under its domain by its libraries"},
        **({"expected_status": existing} if existing else {}))
    return True


async def _frozen_benchmark(pool: Any, goal_id: str, task: BenchmarkTask) -> tuple[str, bool]:
    name = f"{task.source} {task.external_id}"
    existing = await pool.fetchval(
        "SELECT id::text FROM benchmarks WHERE goal_id = $1::uuid AND name = $2 AND version = 1", goal_id, name)
    if existing:
        return existing, False
    from app.services import product_model as pm

    row = await pm.create_benchmark(
        pool, goal_id=goal_id, name=name, description=f"{task.source} {task.source_version} task {task.external_id}",
        evaluation_protocol={
            "framework": "unittest", "test_code": task.test_code, "tests": task.test_names,
            "visible_tests": task.visible_tests, "hidden_tests": task.hidden_tests,
            "gold": "every test in test_code passes",
            "runtime_check": "the visible_tests pass (what an agent may run between attempts)",
        },
        environment_specification={
            "harness": task.source, "version": task.source_version, "libs": task.libs,
            "entry_point": task.entry_point, "prompt_prefix": task.prompt_prefix,
        },
        success_criteria={"predicate": "all tests in the benchmark's test_code pass"},
        comparison_policy={"metric": "pass@1 on the full test suite"},
        status="frozen", provenance=f"benchmark_import:{task.source}@{task.source_version}",
        metadata={"source": task.source, "source_version": task.source_version, "external_id": task.external_id,
                  "split": task.split, "domains": task.domains, "reference_solution_sha256": task.reference_sha256},
    )
    await pool.execute("UPDATE benchmarks SET frozen_at = COALESCE(frozen_at, now()) WHERE id = $1::uuid", row["id"])
    return str(row["id"]), True


async def import_tasks(pool: Any, tasks: Sequence[BenchmarkTask], *, embedder: Any = None,
                       limit: Optional[int] = None, dry_run: bool = False, judge_mode: str = "none",
                       domain_edges: bool = True) -> dict[str, Any]:
    """`judge_mode="model"` runs the production identity path for each task Goal (semantic
    same/narrower/broader/related judgment, and the goal-abstraction placement job it
    enqueues); "none" (default) is exact-name identity only. `domain_edges=False` skips the
    library-derived domain Goals and their accepted edges, leaving the hierarchy entirely to
    production placement."""
    from app.services import search_projection as sp

    usable = [t for t in tasks if t.excluded_reason is None]
    if limit is not None:
        usable = usable[:limit]
    report: dict[str, Any] = {"summary": summarize(tasks), "selected": len(usable), "dry_run": dry_run}
    if dry_run or not usable:
        return report

    domains = list(dict.fromkeys(d for t in usable for d in t.domains)) if domain_edges else []
    from app.benchmarks.bigcodebench import domain_description

    domain_ids = {d: await _goal(pool, d, domain_description(d), {"benchmark_domain": True}, embedder)
                  for d in domains}
    goal_ids: dict[str, str] = {}
    for task in usable:
        goal_ids[task.external_id] = await _goal(
            pool, task.goal_name, task.goal_description,
            {"benchmark_source": task.source, "benchmark_version": task.source_version,
             "external_id": task.external_id, "libs": task.libs}, embedder, judge_mode)
    await sp.drain_outbox(pool)          # edges and routing read the Goal projection

    counts = Counter()
    manifest = []
    for task in usable:
        gid = goal_ids[task.external_id]
        for domain in (task.domains if domain_edges else []):
            if await _accepted_edge(pool, gid, domain_ids[domain], task.source):
                counts["edges_created"] += 1
        bench_id, created = await _frozen_benchmark(pool, gid, task)
        counts["benchmarks_created" if created else "benchmarks_existing"] += 1
        manifest.append({
            "source": task.source, "version": task.source_version, "external_id": task.external_id,
            "goal_id": gid, "benchmark_id": bench_id, "split": task.split, "domains": task.domains,
            "entry_point": task.entry_point, "tests": task.test_names, "visible_tests": task.visible_tests,
            "libs": task.libs,
        })
    await sp.drain_outbox(pool)
    report.update({"domain_goals": len(domain_ids), "task_goals": len(goal_ids), **counts, "manifest": manifest,
                   "manifest_sha256": stable_hash(*(m["external_id"] + m["goal_id"] for m in manifest))})
    return report
