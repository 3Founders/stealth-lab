"""Live-database tests for the Goal abstraction DAG, placement and Benchmark transfer.

The offline suites drive these services through fake pools, which is how four
production-breaking defects went unnoticed (a pre-serialized jsonb parameter that
tripped a CHECK on every relation decision, an ambiguous column in the hierarchy
read, an undefined name on the placement path, and an INSERT with one placeholder
too few in transfer lineage). Everything here runs real SQL, triggers and the job
queue; only the model's verdicts are scripted.
"""
from __future__ import annotations

import os
import uuid

import pytest
import pytest_asyncio

from app.db.session import create_pool
from app.ingestion import queue as q
from app.services import benchmark_transfer as bt
from app.services import product_model as pm
from app.services import search_projection as sp
from app.services.access import AccessScope, TenantScope
from app.services.goal_abstraction import GoalAbstractionError, persist_goal_relation
from app.services.goal_hierarchy_read import enrich_goals
from app.services.goals import find_or_create_goal
from app.services.identity_resolution import goal_abstraction_placement_key, merge_goal
from app.services.ingestion_jobs import handle_goal_abstraction_placement

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL,
    reason="requires a real DATABASE_URL -- this is a live-database integration test",
)

U = AccessScope.unrestricted()


@pytest_asyncio.fixture
async def pool():
    p = await create_pool(DATABASE_URL)
    try:
        yield p
    finally:
        await p.close()


def _run_id() -> str:
    return uuid.uuid4().hex[:8]


async def _goal(pool, name: str, **kwargs) -> str:
    created = await find_or_create_goal(
        pool, canonical_name=name, scope_type="global", provenance="system_pending_review",
        judge_mode="none", status="active", **kwargs,
    )
    await sp.drain_outbox(pool)
    return created["id"]


async def _edge(pool, specific: str, abstract: str, status: str = "accepted", **kwargs):
    return await persist_goal_relation(
        pool, specific, abstract, status=status, provenance="e2e", access_scope=U,
        tenant_scope=TenantScope.unrestricted(), decided_by="e2e-reviewer",
        decision_metadata={"reason": "e2e"}, **kwargs,
    )


async def _levels(pool, *ids: str) -> dict[str, dict]:
    rows = await enrich_goals(pool, [{"id": i} for i in ids], access_scope=U, tenant_scope=TenantScope.unrestricted())
    return {row["id"]: row for row in rows}


async def _independent_level(pool, goal_id: str) -> int:
    """Longest accepted path to a root, computed without the service code."""
    return await pool.fetchval(
        """
        WITH RECURSIVE up(goal_id, depth) AS (
            SELECT $1::uuid, 0
            UNION ALL
            SELECT r.abstract_goal_id, up.depth + 1 FROM up
            JOIN goal_relations r ON r.specific_goal_id = up.goal_id
             AND r.relation_type = 'SPECIALIZES' AND r.status = 'accepted')
        SELECT max(depth) FROM up
        """,
        goal_id,
    )


@pytest.mark.asyncio
async def test_levels_track_graph_mutation_and_rejection(pool):
    run = _run_id()
    a, b, c = [await _goal(pool, f"e2e {run} {n} goal") for n in ("alpha", "bravo", "charlie")]
    await _edge(pool, b, a)
    await _edge(pool, c, b)
    rows = await _levels(pool, a, b, c)
    assert [rows[x]["abstraction_level"] for x in (a, b, c)] == [0, 1, 2]
    assert [g["id"] for g in rows[b]["abstracts"]] == [a] and [g["id"] for g in rows[a]["specializes"]] == [b]

    # a deeper new parent (from a separate chain) moves the descendant down; rejecting it moves it back
    p1, p2, p3 = [await _goal(pool, f"e2e {run} {n} goal") for n in ("papa", "quebec", "romeo")]
    await _edge(pool, p2, p1)
    await _edge(pool, p3, p2)
    await _edge(pool, b, p3)
    assert (await _levels(pool, c))[c]["abstraction_level"] == 4 == await _independent_level(pool, c)
    rejected = await _edge(pool, b, p3, status="rejected", expected_status="accepted")
    assert (await _levels(pool, c))[c]["abstraction_level"] == 2 == await _independent_level(pool, c)

    # The stored projection matches an independent recomputation, and refreshing
    # it read only this edge's component -- never the rest of the corpus.
    total_goals = await pool.fetchval("SELECT count(*) FROM goal_search_index")
    assert rejected["projection"]["component_goals"] <= 6 < total_goals or total_goals <= 6
    for goal_id in (a, b, c, p1, p2, p3):
        stored = await pool.fetchval(
            "SELECT abstraction_level FROM goal_abstraction_state WHERE goal_id = $1", goal_id)
        assert stored in (None, await _independent_level(pool, goal_id)), goal_id


@pytest.mark.asyncio
async def test_graph_rejects_cycles_self_edges_duplicates_and_implied_edges(pool):
    run = _run_id()
    a, b, c = [await _goal(pool, f"e2e {run} {n} goal") for n in ("one", "two", "three")]
    await _edge(pool, b, a)
    await _edge(pool, c, b)
    with pytest.raises(GoalAbstractionError):
        await _edge(pool, a, c)                     # cycle
    with pytest.raises(GoalAbstractionError):
        await _edge(pool, c, a)                     # already implied through b
    with pytest.raises(Exception):
        await pool.execute(                         # cycle through raw SQL hits the trigger
            "INSERT INTO goal_relations (specific_goal_id, abstract_goal_id, status, decided_by, "
            "decision_metadata, scope_type) VALUES ($1, $2, 'accepted', 'x', '{\"a\": 1}', 'global')", a, c)
    with pytest.raises(Exception):
        await pool.execute(
            "INSERT INTO goal_relations (specific_goal_id, abstract_goal_id) VALUES ($1, $1)", a)
    relation_status = await pool.fetchval(
        "SELECT status FROM goal_relations WHERE specific_goal_id = $1 AND abstract_goal_id = $2", b, a)
    assert relation_status == "accepted"
    assert await pool.fetchval(
        "SELECT jsonb_typeof(decision_metadata) FROM goal_relations WHERE specific_goal_id = $1", b) == "object"


@pytest.mark.asyncio
async def test_multi_parent_orphan_and_proposed_edges(pool):
    run = _run_id()
    x, y, z, orphan = [await _goal(pool, f"e2e {run} {n} goal") for n in ("xray", "yankee", "zulu", "oscar")]
    await _edge(pool, z, x)
    await _edge(pool, z, y)
    await _edge(pool, orphan, x, status="proposed")
    rows = await _levels(pool, z, orphan)
    assert len(rows[z]["abstracts"]) == 2 and rows[z]["abstraction_level"] == 1
    assert rows[orphan]["abstraction_level"] == 0 and rows[orphan]["abstracts"] == []


@pytest.mark.asyncio
async def test_merge_moves_accepted_edges_and_demotes_a_cycle(pool):
    run = _run_id()
    root, loser, survivor, middle = [await _goal(pool, f"e2e {run} {n} goal") for n in ("root", "loser", "survivor", "middle")]
    await _edge(pool, loser, root)
    await merge_goal(pool, loser, survivor)
    assert await pool.fetchval(
        "SELECT status FROM goal_relations WHERE specific_goal_id = $1 AND abstract_goal_id = $2", survivor, root) == "accepted"

    loser2, survivor2 = [await _goal(pool, f"e2e {run} {n} two goal") for n in ("loser", "survivor")]
    await _edge(pool, loser2, middle)
    await _edge(pool, middle, survivor2)
    await merge_goal(pool, loser2, survivor2)          # re-pointing would close survivor2 -> middle -> survivor2
    assert await pool.fetchval(
        "SELECT status FROM goal_relations WHERE specific_goal_id = $1 AND abstract_goal_id = $2", survivor2, middle) == "proposed"


async def _job_row(pool, where: str, value: str) -> dict:
    """The queued job exactly as the worker would hand it to the handler."""
    row = await pool.fetchrow(f"SELECT *, attempts + 1 AS attempt FROM ingestion_jobs WHERE {where}", value)
    assert row is not None, f"no job where {where} = {value}"
    return dict(row)


class _Verdicts:
    chain_id = "scripted"

    def __init__(self, relation_for):
        self.relation_for = relation_for
        self.kinds: list[str] = []

    async def judge_identity_batch(self, kind, a, candidates):
        self.kinds.append(kind)

        class R:
            ok, provider, model, attempts = True, "scripted", "scripted", 1

        R.value = [{"relation": self.relation_for(str(getattr(c, "text", c))), "confidence": 0.95} for c in candidates]
        return R()


@pytest.mark.asyncio
async def test_placement_to_transfer_pipeline(pool):
    run = _run_id()
    parent = await _goal(pool, f"deploy {run} a service", description="ship a service to an environment")
    bench = await pm.create_benchmark(
        pool, goal_id=parent, name=f"smoke {run}", success_criteria={"http": {"path": "/health", "status": 200}})
    await pool.execute(
        "INSERT INTO benchmark_submissions (id, goal_id, benchmark_id, name, status, submitted_by, provenance, "
        "scope_type, owner_id, visibility, reviewed_by, reviewed_at) VALUES (gen_random_uuid(), $1, $2, $3, "
        "'accepted', 'reviewer', 'system_pending_review', 'global', 'reviewer', 'public', 'reviewer', now())",
        parent, bench["id"], bench["name"])
    await pm.freeze_benchmark(pool, bench["id"])
    child = await _goal(pool, f"deploy {run} service to staging on kubernetes")

    job = await _job_row(pool, "idempotency_key = $1", goal_abstraction_placement_key(child))
    payload = {**job["payload"], "judge_mode": "model", "_job": q.trusted_job_metadata(job)}
    judge = _Verdicts(lambda text: "specializes" if f"deploy {run} a service" in text.lower() else "related")
    placed = await handle_goal_abstraction_placement(pool, payload, judge=judge)

    assert placed["accepted_edges"] == 1 and placed["benchmark_transfers_enqueued"] == 1
    assert (await _levels(pool, child))[child]["abstraction_level"] == 1

    transfer = await _job_row(pool, "job_type = 'benchmark_transfer' AND payload->>'target_goal_id' = $1", child)
    transfer_judge = _Verdicts(lambda _text: "partial")
    decision = await bt.handle_benchmark_transfer(
        pool, {**transfer["payload"], "_job": q.trusted_job_metadata(transfer)}, judge=transfer_judge)

    assert transfer_judge.kinds == ["benchmark_transfer"] and decision["decision"] == "partial"
    target = await pool.fetchrow(
        "SELECT status, jsonb_typeof(success_criteria) AS kind FROM benchmarks WHERE goal_id = $1", child)
    assert target["status"] == "draft" and target["kind"] == "object"
    assert await pool.fetchval("SELECT status FROM benchmarks WHERE id = $1", bench["id"]) == "frozen"
    assert await pool.fetchval("SELECT resolved_at FROM goals WHERE id = $1", child) is None
