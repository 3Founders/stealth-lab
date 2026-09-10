"""
MCP hardening B34 STRICT CLOSURE: the bounded human-review packet.

Before this pass, `record_human_review` (the SUBMISSION half) already
enforced "never accept approved=true without reviewer identity,
reviewed targets, criterion answers, timestamp, and Evidence linkage" --
but nothing GENERATED the packet B34's own text names literally
(objective, exact Procedure/version, the criterion, exact files/diff/
ranges, relevant Claim refs, automated evidence already collected,
specific yes/no questions). This file proves the new `generate_review_
packet` MCP tool builds that real, bounded packet, and that the
verification ladder only advances through the real submission path
(`verify_completion`, not this tool) once a real reviewer answers it.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import json
import os
from uuid import uuid4

import pytest

import app.mcp_server.server as srv
from app.db.session import create_pool
from app.execution.durable_run import execute_run, start_run
from app.execution.recorder import record_artifact
from app.services.embeddings import Embedder
from app.services.procedures import (
    MIN_DISTINCT_CONTEXTS_FOR_VERIFIED,
    MIN_SUCCESSES_FOR_VERIFIED,
    approve_procedure,
    capture_procedure,
    record_execution_outcome,
)
from app.utils.ids import uuid7

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
    for i in range(MIN_SUCCESSES_FOR_VERIFIED):
        await record_execution_outcome(
            pool, procedure_row_id=row_id, success=True,
            context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
        )
    await approve_procedure(pool, procedure_row_id=row_id, approved_by="tester")
    await pool.execute("UPDATE procedures SET is_engineering_fixture = false WHERE id = $1", row_id)
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", row_id))


def test_generate_review_packet_contains_every_literal_b34_field_and_advances_only_via_verify_completion():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-b34-packet-{run_id}"
        exec_run_id = None
        try:
            embedder = Embedder()
            goal_text = f"safely migrate the b34 review-packet schema ({run_id})"
            vec = await embedder.embed_one(goal_text, input_type="document")
            procedure = await _make_verified_approved(
                pool, name, goal=goal_text, embedding=vec,
                steps=[{"order": 0, "goal": "migrate the schema"}],
                postconditions=["the schema migration completes without data loss"],
            )
            async with pool.acquire() as c:
                pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", procedure["id"])
                plan_id = await c.fetchval(
                    "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                    " task_description, procedure_content_hash, content_hash, scope_type) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                    str(uuid7()), procedure["procedure_id"], pv, procedure["id"], "b34-packet-e2e",
                    f"pch-{run_id}", f"ch-{run_id}",
                )
                graph_id = await c.fetchval(
                    "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                    "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
                    str(uuid7()), plan_id, f"gh-{run_id}",
                )
            exec_run_id = await start_run(
                pool, execution_plan_id=plan_id, task_graph_id=graph_id,
                procedure_id=procedure["procedure_id"], procedure_version=pv,
                node_orders=[0], deps={0: []}, created_by="b34_packet_e2e",
            )

            # A real artifact recorded on this run -- the packet's own
            # "exact files/diff/ranges to inspect" must surface it.
            await record_artifact(
                pool, exec_run_id, node_order=0, kind="output_file",
                ref="migration_0099.sql", sha256="a" * 64, size_bytes=1234,
            )

            async def run_node(order: int, attempt: int) -> dict:
                return {"order": order}

            result = await execute_run(pool, exec_run_id, deps={0: []}, run_node=run_node, worker_id="b34-packet-w1")
            assert result["status"] == "awaiting_verification"

            ctx = _FakeContext(pool)
            packet_json = await srv.generate_review_packet(exec_run_id, "postcondition:0", ctx)
            assert not packet_json.startswith("REFUSED:"), packet_json
            packet = json.loads(packet_json)

            # Every literal B34 field, real and populated from real facts.
            assert packet["objective"]
            assert packet["procedure_id"] == str(procedure["procedure_id"])
            assert packet["procedure_version"] == pv
            assert packet["criterion"]["criterion_id"] == "postcondition:0"
            assert packet["criterion"]["statement"] == "the schema migration completes without data loss"
            assert any(a["ref"] == "migration_0099.sql" for a in packet["target_artifacts_or_files"])
            assert isinstance(packet["relevant_claim_refs"], list)
            assert packet["automated_evidence_already_collected"] == []  # nothing else reported yet
            assert len(packet["questions"]) == 1
            assert packet["questions"][0]["type"] == "yes_no"
            assert "schema migration completes without data loss" in packet["questions"][0]["text"]

            # Unknown criterion_id -> REFUSED, never a fabricated packet.
            bad = await srv.generate_review_packet(exec_run_id, "postcondition:99", ctx)
            assert bad.startswith("REFUSED:")

            # generate_review_packet itself must NEVER advance the ladder --
            # only a real verify_completion submission may.
            still_awaiting = await pool.fetchval(
                "SELECT status FROM execution_runs WHERE id = $1", exec_run_id,
            )
            assert still_awaiting == "awaiting_verification"

            # Submit the real human review THROUGH the packet's own
            # question -- identity-bound, criterion-bound, evidence-linked.
            reports = json.dumps([{
                "criterion_id": "postcondition:0", "method": "human_review",
                "reviewer": "reviewer-b34-e2e", "reviewed_targets": ["migration_0099.sql"],
                "criterion_answers": {packet["questions"][0]["id"]: True},
                "verdict": True, "evidence_refs": ["migration_0099.sql"],
            }])
            verified = json.loads(await srv.verify_completion(exec_run_id, ctx, reports_json=reports))
            assert verified["overall_state"] == "independently_verified"

            final_row = await pool.fetchrow(
                "SELECT status, final_outcome FROM execution_runs WHERE id = $1", exec_run_id,
            )
            assert final_row["status"] == "succeeded"
            assert final_row["final_outcome"] == "success"

            vr = await pool.fetchrow(
                "SELECT reviewer, reviewed_targets, criterion_answers FROM verification_results "
                "WHERE execution_run_id = $1 AND criterion_id = 'postcondition:0'", exec_run_id,
            )
            assert vr["reviewer"] == "reviewer-b34-e2e"
            assert vr["reviewed_targets"] == ["migration_0099.sql"]
        finally:
            if exec_run_id is not None:
                await pool.execute("DELETE FROM execution_runs WHERE id=$1", exec_run_id)
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_generate_review_packet_refuses_an_unknown_run():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        ctx = _FakeContext(pool)
        try:
            result = await srv.generate_review_packet(str(uuid4()), "postcondition:0", ctx)
            assert result.startswith("REFUSED:")
        finally:
            await pool.close()

    asyncio.run(_run())
