"""
`init_workspace` MCP tool end to end: the "connect -> identify workspace
-> bootstrap if needed -> return continuation context" onboarding
entrypoint this server never had (`probe_environment()` existed but was
only ever called from `reproduce_procedure`'s own internal flow).

Covers the product spec's Case 1 (empty/new repo: no fabricated Claims,
clean init, `.stealth/` still created) and the AGENTS.md/README-present
case (facts captured with real provenance), plus idempotency + surfacing
an active run's open collaboration state on a later connection.

Same pattern as `test_run_collaboration_mcp_e2e.py`: skips (never fails)
without a real DATABASE_URL, self-cleaning by name prefix.
"""
from __future__ import annotations

import asyncio
import json
import os
import tempfile
from uuid import uuid4

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.execution import durable_run as _dr
from app.execution.plan_persistence import persist_compiled_plan
from app.execution.plans import compile_plan
from app.execution.procedure_graph import expand_procedure_steps
from app.services.procedures import capture_procedure

DATABASE_URL = os.environ.get("DATABASE_URL")
pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


async def _cleanup_claims(pool, project_id: str) -> None:
    await pool.execute(
        "DELETE FROM knowledge_nodes WHERE node_type = 'claim' AND name LIKE $1", f"project:{project_id}%",
    )


async def _cleanup_procedure(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )
    await pool.execute("UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{name_prefix}%")


async def _capture(pool, name: str, **kwargs) -> dict:
    kwargs.setdefault("goal", name)
    result = await capture_procedure(pool, name=name, provenance="system_pending_review", scope_type="global", **kwargs)
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", result["id"]))


async def _start_run(pool, procedure: dict) -> str:
    steps = procedure.get("steps") or [{"order": 0, "goal": "step"}]
    nodes = await expand_procedure_steps(
        pool, procedure_id=procedure["procedure_id"], procedure_version=procedure["version"], steps=steps,
    )
    compiled = compile_plan(
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        procedure_row_id=procedure["id"], procedure_payload=procedure,
        task_description=procedure["name"], nodes=nodes,
        extractor_version="test_workspace_init_mcp_e2e@1", created_by="test",
    )
    compiled, _ = await persist_compiled_plan(pool, compiled)
    graph = compiled.graph
    return await _dr.start_run(
        pool, execution_plan_id=str(compiled.plan.id), task_graph_id=str(compiled.graph.id),
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        node_orders=[n.order for n in graph.nodes], deps={n.order: list(n.deps) for n in graph.nodes},
    )


def test_empty_repo_bootstraps_cleanly_with_no_fabricated_claims():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        with tempfile.TemporaryDirectory() as repo_dir:
            ctx = _FakeContext(pool)
            result = json.loads(await srv.init_workspace(repo_path=repo_dir, ctx=ctx))
            try:
                assert result["first_connection"] is True
                assert result["claims_written"] == []
                assert result["environment_facts"] == []
                assert all(present is False for present in result["workspace_facts"]["doc_files"].values())
                assert result["workspace_facts"]["has_ci_config"] is False
                assert result["workspace_facts"]["db_dir"] is None

                meta_path = os.path.join(repo_dir, ".stealth", "meta.json")
                assert os.path.isfile(meta_path)
                with open(meta_path, encoding="utf-8") as f:
                    meta = json.load(f)
                assert meta["schema"].startswith("stealth-")
            finally:
                await _cleanup_claims(pool, result["project_id"])
        await pool.close()

    asyncio.run(_run())


def test_agents_md_and_readme_are_captured_as_provenanced_claims():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        with tempfile.TemporaryDirectory() as repo_dir:
            with open(os.path.join(repo_dir, "AGENTS.md"), "w", encoding="utf-8") as f:
                f.write("# agents\n")
            with open(os.path.join(repo_dir, "README.md"), "w", encoding="utf-8") as f:
                f.write("# readme\n")
            os.makedirs(os.path.join(repo_dir, ".github", "workflows"), exist_ok=True)
            with open(os.path.join(repo_dir, ".github", "workflows", "ci.yml"), "w", encoding="utf-8") as f:
                f.write("name: ci\n")

            ctx = _FakeContext(pool)
            result = json.loads(await srv.init_workspace(repo_path=repo_dir, ctx=ctx))
            try:
                assert result["first_connection"] is True
                assert result["workspace_facts"]["doc_files"]["AGENTS.md"] is True
                assert result["workspace_facts"]["doc_files"]["README.md"] is True
                assert result["workspace_facts"]["has_ci_config"] is True
                assert result["claims_written"], "AGENTS.md/README.md/CI presence must be persisted as real claims"

                rows = await pool.fetch(
                    "SELECT properties FROM knowledge_nodes WHERE id = ANY($1::uuid[])",
                    result["claims_written"],
                )
                predicates = {r["properties"]["predicate"] for r in rows}
                assert "has_doc_file:AGENTS.md" in predicates
                assert "has_doc_file:README.md" in predicates
                assert "has_ci_config" in predicates
                for r in rows:
                    assert r["properties"]["epistemic_status"] == "observed"
                    assert r["properties"]["claim_type"] == "workspace_structure_fact"

                # calling again must not duplicate the same facts (idempotent
                # per subject+predicate, same discipline assert_environment_
                # claims already uses)
                result2 = json.loads(await srv.init_workspace(repo_path=repo_dir, ctx=ctx))
                assert result2["claims_written"] == []
            finally:
                await _cleanup_claims(pool, result["project_id"])
        await pool.close()

    asyncio.run(_run())


def test_second_connection_is_idempotent_and_surfaces_an_active_runs_blocker():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        tag = uuid4().hex[:8]
        name = f"proc-test-workspaceinit-{tag}"
        try:
            proc = await _capture(pool, name)
            run_id = await _start_run(pool, proc)
            ctx = _FakeContext(pool)

            with tempfile.TemporaryDirectory() as repo_dir:
                blocker = json.loads(await srv.record_run_update(
                    run_id=run_id, kind="BLOCKER", body="init_workspace should see this blocker",
                    ctx=ctx, repo_path=repo_dir,
                ))
                assert blocker["stealth_projection"] == "written"

                first = json.loads(await srv.init_workspace(repo_path=repo_dir, ctx=ctx))
                assert first["first_connection"] is False, "a real run.md projection already exists here"
                assert first["continuation"]["active_run"] == run_id
                open_blocker_bodies = [b["body"] for b in first["continuation"]["open_blockers"]]
                assert "init_workspace should see this blocker" in open_blocker_bodies
                assert first["projection"] == "written"

                second = json.loads(await srv.init_workspace(repo_path=repo_dir, ctx=ctx))
                assert second["first_connection"] is False
                assert second["continuation"]["active_run"] == run_id
                open_blocker_bodies_2 = [b["body"] for b in second["continuation"]["open_blockers"]]
                assert "init_workspace should see this blocker" in open_blocker_bodies_2
        finally:
            await _cleanup_procedure(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_refuses_a_repo_path_that_is_not_a_directory():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            ctx = _FakeContext(pool)
            with tempfile.NamedTemporaryFile(delete=False) as f:
                not_a_dir = f.name
            try:
                result = await srv.init_workspace(repo_path=not_a_dir, ctx=ctx)
                assert result.startswith("REFUSED:")
            finally:
                os.unlink(not_a_dir)
        finally:
            await pool.close()

    asyncio.run(_run())
