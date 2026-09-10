"""
MCP hardening B35: `find_best_way(mode='plan_only', repo_path=...)` and
`continue_run(repo_path=...)` both refresh the real `.stealth/`
projection under `repo_path`, end to end through the real MCP tools.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import json
import os
import tempfile
from uuid import uuid4

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.services.embeddings import Embedder
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
    approve_procedure,
    capture_procedure,
    record_execution_outcome,
)

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


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{name_prefix}%",
    )


async def _make_verified_approved(pool, name: str, **kwargs) -> dict:
    kwargs.setdefault("goal", name)
    if "embedding" in kwargs and "embedding_model_id" not in kwargs:
        kwargs["embedding_model_id"] = Embedder().embedding_model_id()
    result = await capture_procedure(
        pool, name=name, provenance="system_pending_review", scope_type="global", **kwargs,
    )
    row_id = result["id"]
    await pool.execute("UPDATE procedures SET is_engineering_fixture = false WHERE id = $1", row_id)
    for i in range(MIN_SUCCESSES_FOR_VERIFIED):
        await record_execution_outcome(
            pool, procedure_row_id=row_id, success=True,
            context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
        )
    await approve_procedure(pool, procedure_row_id=row_id, approved_by="tester")
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id))


def test_find_best_way_and_continue_run_both_refresh_the_real_stealth_projection():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-stealthfbw-{run_id}"
        try:
            embedder = Embedder()
            goal_text = f"provision the staging cluster ({run_id})"
            vec = await embedder.embed_one(goal_text, input_type="document")
            await _make_verified_approved(
                pool, name, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "provision nodes"}],
            )

            ctx = _FakeContext(pool)
            with tempfile.TemporaryDirectory() as repo_dir:
                plan_result = await srv.find_best_way(
                    task_description=goal_text, ctx=ctx, mode="plan_only", repo_path=repo_dir,
                )
                payload = json.loads(plan_result)
                assert payload["stealth_projection"] == "written"
                procedure_run_id = payload["procedure_run_id"]

                stealth_dir = os.path.join(repo_dir, ".stealth")
                entries = set(os.listdir(stealth_dir))
                assert {"context.md", "run.json", "meta.json", "index"} <= entries
                assert "root.idx" in os.listdir(os.path.join(stealth_dir, "index"))
                with open(os.path.join(stealth_dir, "run.json"), encoding="utf-8") as f:
                    run_json = json.load(f)
                assert run_json["procedure_run_id"] == procedure_run_id

                # continue_run's own repo_path also refreshes it (proving
                # the projection is regenerable independent of find_best_way).
                continue_result = await srv.continue_run(procedure_run_id, ctx, repo_path=repo_dir)
                continue_payload = json.loads(continue_result)
                assert continue_payload["stealth_projection"] == "written"

            # continue_run WITHOUT repo_path must not attempt any write
            # (and must not even mention the field) -- omission, not a
            # forced no-op status.
            no_repo_result = await srv.continue_run(procedure_run_id, ctx)
            no_repo_payload = json.loads(no_repo_result)
            assert "stealth_projection" not in no_repo_payload
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())
