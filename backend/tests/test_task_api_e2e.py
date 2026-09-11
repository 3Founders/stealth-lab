"""
Live-database proving tests for app/services/task_api.py and
app/services/personal_contributions.py: a real task_node, a real
procedure that names it (both real joins -- migrated_from_task_node_id
AND the edges OWNS/DECOMPOSES_TO path), real evidence from
record_execution_outcome, a real claim, and a real executions row, all
attributed to a real created_by/actor_id value.

Same pattern as every other `*_e2e.py` file (test_canonical_personal_
memory_e2e.py in particular, which this file's plan/execution fixtures
mirror): requires a real DATABASE_URL, skips (not fails) without one.
Self-cleaning by name prefix; append-only tables (evidence, execution_
plans, task_graphs, executions) are left in place, same accepted
convention test_canonical_personal_memory_e2e.py already uses -- they
cannot be deleted (migration 23's engine trigger, migration 24's
tombstone-only contract) and a name-prefixed row causes no collision on
rerun.
"""
from __future__ import annotations

import asyncio
import os
from datetime import datetime, timezone
from uuid import uuid4

import pytest

from app.db.session import create_pool
from app.execution.plan_persistence import persist_compiled_plan, record_plan_execution
from app.execution.plans import compile_plan
from app.execution.procedure_graph import expand_procedure_steps
from app.services.access import AccessScope
from app.services.claims import capture_claim
from app.services.personal_contributions import get_personal_contributions
from app.services.procedures import capture_procedure, record_execution_outcome
from app.services.task_api import get_task_detail

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

PREFIX = "task-api-e2e"
ACTOR = f"{PREFIX}-actor"


class FakeEmbedder:
    async def embed_one(self, text, input_type="document"):
        return [0.0] * 1024


async def _cleanup(pool) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{PREFIX}%",
    )
    # A row referenced by its own execution_plans survives the DELETE above
    # (FK-safe by design) -- it must never be left visible to real retrieval
    # across runs, so fall it back to an explicit fixture flag rather than
    # relying on the procedures.is_engineering_fixture column default.
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{PREFIX}%",
    )
    await pool.execute(
        "DELETE FROM edges WHERE properties->>'_test_prefix' = $1", PREFIX,
    )
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{PREFIX}%")
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{PREFIX}%")


def test_task_detail_and_personal_contributions_against_real_postgres():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)

            # --- a real task_node ---
            task_name = f"{PREFIX}-fix-flaky-test-{uuid4().hex[:8]}"
            task_row = await pool.fetchrow(
                "INSERT INTO task_nodes (name, description, skill_ref, "
                "provenance, created_by, scope_type) "
                "VALUES ($1, $2, $3, 'company_ingested', $4, 'global') RETURNING id",
                task_name, "a real task for the task API e2e proof", task_name, ACTOR,
            )
            task_node_id = str(task_row["id"])

            # --- a real procedure, linked via BOTH real joins:
            # migrated_from_task_node_id (direct FK) AND an edges
            # OWNS/DECOMPOSES_TO row (skill_ingestion.py's own shape) ---
            proc_name = f"{PREFIX}-proc-{uuid4().hex[:8]}"
            captured = await capture_procedure(
                pool, name=proc_name, goal="stabilize the flaky test",
                provenance="system_pending_review", scope_type="global",
                migrated_from_task_node_id=task_node_id, created_by=ACTOR,
                steps=[{"order": 0, "goal": "stabilize the flaky test"}],
            )
            procedure_row_id = captured["id"]
            procedure_id = captured["procedure_id"]
            procedure = await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", procedure_row_id)
            await pool.execute(
                "INSERT INTO edges (edge_type, custom_edge_type, source_id, "
                "source_table, target_id, target_table, properties, created_by) "
                "VALUES ('OWNS', 'DECOMPOSES_TO', $1::uuid, 'procedures', $2::uuid, "
                "'task_nodes', $3::jsonb, $4)",
                procedure_row_id, task_node_id, {"_test_prefix": PREFIX}, ACTOR,
            )

            # --- real evidence: two successes, one failure with a real
            # failure_class -- record_execution_outcome is the one real
            # writer for both verification_stats AND the evidence table. ---
            await record_execution_outcome(
                pool, procedure_row_id=procedure_row_id, success=True, context_key="ctx-a",
            )
            await record_execution_outcome(
                pool, procedure_row_id=procedure_row_id, success=True, context_key="ctx-b",
            )
            await record_execution_outcome(
                pool, procedure_row_id=procedure_row_id, success=False, context_key="ctx-c",
                failure_class="environment_changed",
            )

            # --- a real claim, same created_by ---
            claim_id = await capture_claim(
                pool, statement=f"{PREFIX} claim: the fix holds under load",
                task_ids=[task_name], created_by=ACTOR, embedder=FakeEmbedder(),
            )
            assert claim_id

            # --- a real executions row, attributed to ACTOR via actor_id ---
            # expand_procedure_steps is the real entry point a caller uses
            # BEFORE compile_plan (mirrors find_best_way's own tier-2 path
            # in mcp_server/server.py, and test_canonical_personal_memory_
            # e2e.py's identical pattern) -- compile_plan's own
            # validate_graph refuses an empty node list.
            nodes = await expand_procedure_steps(
                pool, procedure_id=procedure["procedure_id"],
                procedure_version=procedure["version"], steps=procedure["steps"],
            )
            compiled = compile_plan(
                procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
                procedure_row_id=procedure_row_id, procedure_payload=dict(procedure),
                task_description=f"{PREFIX} real execution", nodes=nodes,
                extractor_version="test_task_api_e2e@1", created_by=ACTOR,
            )
            persisted, _was_new = await persist_compiled_plan(pool, compiled)
            execution_id = await record_plan_execution(
                pool, compiled=persisted, outcome="success", actor_id=ACTOR, created_by=ACTOR,
            )
            assert execution_id

            scope = AccessScope.unrestricted()

            # ================= get_task_detail =================
            detail = await get_task_detail(pool, task_node_id, scope=scope)
            assert detail is not None
            assert detail["skill_ref"] == task_name

            dep_ids = {str(p["id"]) for p in detail["dependent_procedures"]}
            assert procedure_row_id in dep_ids, (
                "both the migrated_from_task_node_id FK and the edges "
                "OWNS/DECOMPOSES_TO row point at the SAME procedure here, "
                "so DISTINCT must still yield exactly one dependent row"
            )
            assert len(detail["dependent_procedures"]) == 1

            assert len(detail["capability_statistics"]) == 1
            cap = detail["capability_statistics"][0]
            assert cap["procedure_row_id"] == procedure_row_id
            # A recorded failure (direction='contradicts' by
            # outcome_to_evidence's default) IS an outcome-bearing attempt
            # and counts here: the capability stream gates on
            # evidence_type + terminal outcome_status, not direction
            # (db/34_evidence_stats_count_failures.sql). 2 successes + 1
            # failure = 3.
            assert cap["evidence_count"] == 3
            assert cap["success_count"] == 2

            # known_failure_modes reads failure_class directly -- the real
            # failure is visible here too.
            assert detail["known_failure_modes"] == ["environment_changed"]

            # ================= get_personal_contributions =================
            contributions = await get_personal_contributions(pool, ACTOR, scope=scope)
            assert contributions["actor"] == ACTOR
            proc_ids = {str(p["id"]) for p in contributions["submitted_procedures"]}
            assert procedure_row_id in proc_ids
            claim_ids = {str(c["id"]) for c in contributions["submitted_claims"]}
            assert claim_id in claim_ids
            exec_ids = {str(e["id"]) for e in contributions["executions"]}
            assert str(execution_id) in exec_ids

            # a real, unrelated actor sees none of this
            other = await get_personal_contributions(pool, f"{PREFIX}-nobody", scope=scope)
            assert other["submitted_procedures"] == []
            assert other["submitted_claims"] == []
            assert other["executions"] == []
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
