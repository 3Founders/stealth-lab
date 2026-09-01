"""
Live-database proof of directive Sec 29-32's full hot-path chain:
procedure -> task node -> resolve implementation -> bind durable
implementation/version -> persisted plan -> execution evidence, exercised
through the SAME real functions `app/mcp_server/server.py`'s production
call sites use (`_bind_plan_to_registry`, `implementation_executor.
plan_implementation_id`, `record_plan_execution`'s `implementation_id`
param) -- not a parallel/mocked path.

Central claim under test (directive Sec 31): a newly registered
implementation must NOT silently replace an implementation already frozen
into a persisted plan. Replay of the SAME task recompiles the SAME
content_hash `compile_plan` always would; `_bind_plan_to_registry`'s own
plan-pinning guard (`find_existing_plan` checked BEFORE any re-resolve)
is what keeps that replay bound to the ORIGINAL implementation even after
a newer one is registered and activated for the same task in between.

Same pattern as every other `*_e2e.py` file: requires a real
DATABASE_URL, skips (not fails) without one; self-cleaning by name
prefix.
"""
from __future__ import annotations

import asyncio
import os
from uuid import uuid4

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.execution.implementation_executor import plan_implementation_id
from app.execution.implementation_registry import activate, register
from app.execution.plan_persistence import persist_compiled_plan, record_plan_execution
from app.execution.plans import compile_plan
from app.services.access import AccessScope
from app.services.procedures import capture_procedure, get_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "plan-pin-e2e"


async def _cleanup(pool) -> None:
    await pool.execute(
        "DELETE FROM implementation_tasks WHERE implementation_id IN "
        "(SELECT id FROM implementations WHERE name LIKE $1)", f"{PREFIX}%",
    )
    await pool.execute("DELETE FROM implementations WHERE name LIKE $1", f"{PREFIX}%")
    # procedures/task_nodes/execution_plans/task_graphs/executions are
    # left in place once a real plan is persisted against them (Band 1.7
    # frozen/append-only tables) -- same accepted convention every other
    # e2e file in this suite uses. Each run's names carry a fresh uuid4
    # suffix so a leftover row from a prior run causes no collision.


def test_replay_stays_pinned_to_original_implementation_after_newer_one_is_registered():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            scope = AccessScope.unrestricted()
            run_id = uuid4().hex[:8]

            # --- a real task_node ---
            task_row = await pool.fetchrow(
                "INSERT INTO task_nodes (name, skill_ref) VALUES ($1, $2) RETURNING id",
                f"{PREFIX}-task-{run_id}", f"{PREFIX}-skill-{run_id}",
            )
            task_node_id = str(task_row["id"])

            # --- a real procedure, migrated from that task node (the
            # only real procedure<->task_node link that exists today --
            # see server.py::_bind_plan_to_registry's own docstring) ---
            captured = await capture_procedure(
                pool, name=f"{PREFIX}-proc-{run_id}", goal="do the real thing",
                steps=[{"order": 0, "goal": "do the real thing"}],
                provenance="system_pending_review", scope_type="global",
                migrated_from_task_node_id=task_node_id, created_by=PREFIX,
            )
            matched_procedure = await get_procedure(pool, captured["id"])
            assert matched_procedure is not None
            assert matched_procedure["migrated_from_task_node_id"] is not None

            # --- V1: registered and active BEFORE the first compile ---
            v1 = await register(
                pool, name=f"{PREFIX}-impl", kind="tool", provider="graphify",
                version=1, created_by=PREFIX, task_node_ids=[task_node_id],
            )
            await activate(pool, v1["id"])

            task_description = f"{PREFIX} task {run_id}"

            def _compile():
                return compile_plan(
                    procedure_id=matched_procedure["procedure_id"],
                    procedure_version=matched_procedure["version"],
                    procedure_row_id=matched_procedure["id"],
                    procedure_payload=matched_procedure,
                    task_description=task_description,
                    nodes=[{"order": 0, "goal": "do the real thing", "deps": []}],
                    extractor_version="plan_pinning_e2e@1",
                    created_by=PREFIX,
                )

            # ================= first real run: resolves + freezes V1 =====
            compiled_v1 = await srv._bind_plan_to_registry(pool, _compile(), matched_procedure)
            assert compiled_v1.graph.nodes[0].implementation_id == v1["id"]

            persisted_v1, was_new_1 = await persist_compiled_plan(pool, compiled_v1)
            assert was_new_1 is True
            assert persisted_v1.graph.nodes[0].implementation_id == v1["id"]

            execution_id_1 = await record_plan_execution(
                pool, compiled=persisted_v1, outcome="success", created_by=PREFIX,
                implementation_id=plan_implementation_id(persisted_v1),
            )
            row1 = await pool.fetchrow(
                "SELECT implementation_id FROM executions WHERE id = $1", execution_id_1,
            )
            assert str(row1["implementation_id"]) == v1["id"], (
                "execution evidence must record the real implementation_id that "
                "actually ran -- never blank"
            )

            # --- V2: a NEWER implementation registered+activated for the
            # SAME task, strictly after V1's plan was already persisted ---
            v2 = await register(
                pool, name=f"{PREFIX}-impl", kind="tool", provider="graphify",
                version=2, created_by=PREFIX, task_node_ids=[task_node_id],
            )
            await activate(pool, v2["id"])

            # Sanity: an UNPINNED resolve now prefers V2 (most recently
            # registered active implementation, per resolve()'s own
            # recency-order contract) -- proves V2 really is live and
            # really would win a fresh, unpinned resolution.
            from app.execution.implementation_registry import resolve as registry_resolve
            fresh_resolution = await registry_resolve(pool, task_node_id, scope=scope)
            assert fresh_resolution["id"] == v2["id"]

            # ================= replay: SAME task_description, recompiled
            # from scratch ==================================================
            compiled_replay = await srv._bind_plan_to_registry(pool, _compile(), matched_procedure)
            assert compiled_replay.graph.nodes[0].implementation_id == v1["id"], (
                "a newly registered implementation must not silently replace "
                "an implementation already frozen into a persisted plan"
            )

            persisted_replay, was_new_2 = await persist_compiled_plan(pool, compiled_replay)
            assert was_new_2 is False, "replay must reuse the original persisted plan row, not fork a new one"
            assert persisted_replay.plan.id == persisted_v1.plan.id
            assert persisted_replay.graph.nodes[0].implementation_id == v1["id"]

            execution_id_2 = await record_plan_execution(
                pool, compiled=persisted_replay, outcome="success", created_by=PREFIX,
                implementation_id=plan_implementation_id(persisted_replay),
            )
            row2 = await pool.fetchrow(
                "SELECT implementation_id FROM executions WHERE id = $1", execution_id_2,
            )
            assert str(row2["implementation_id"]) == v1["id"], (
                "replay's own evidence row must still record V1's identity, not V2's"
            )

            # And the persisted task_graphs row itself, read back fresh
            # from storage (not the in-memory object), still names V1.
            graph_row = await pool.fetchrow(
                "SELECT nodes FROM task_graphs WHERE id = $1", persisted_replay.graph.id,
            )
            import json
            stored_nodes = json.loads(graph_row["nodes"]) if isinstance(graph_row["nodes"], str) else graph_row["nodes"]
            assert stored_nodes[0]["implementation_id"] == v1["id"]
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
