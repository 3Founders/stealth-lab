"""Canonical end-to-end demo of the verified procedural memory substrate,
real-code-path, single continuous run against a live Postgres.

HONEST SCOPE (read before trusting a green run to mean more than it
proves): this test drives every real, already-wired joint the substrate
has today, in the order data actually flows through them in production
code -- it does not invent a new pipeline, it calls the same functions
`app/services/observations.py`, `app/services/claims.py`,
`app/services/procedures.py`, `app/services/applicability.py`, and
`app/execution/*` already expose to real callers (the API routers, the
MCP server, `ingestion_jobs.py`).

Two real, independently-confirmed chains are exercised side by side, NOT
as one continuous pipe, because they are not one continuous pipe in this
codebase today (confirmed by reading `extract_procedure`/`capture_
procedure` in full: neither queries the claims graph, and no writer
derives a procedure's `preconditions` from an episode's own claims):

  Chain A -- episodic memory:      episode -> observation -> claim
  Chain B -- procedural memory:    procedure capture -> verification
                                    threshold -> retrieval -> compiled
                                    plan -> executed -> evidence recorded
                                    -> re-retrieval reflects the new tier

Both chains are seeded from the SAME real episode row and inherit the
SAME real scope (`scope_type='project'`, derived from that episode's own
`project_id` by `promote_observation_to_claim`'s existing real derivation
-- proving scope inheritance actually reaches a claim, not just a
procedure) -- demonstrating everything this substrate can honestly claim
end-to-end today, and stopping exactly where the real, already-reported
gap between the two chains begins, rather than papering over it with a
synthetic bridge this test invents itself.

Self-cleaning by name/label prefix. Skips (not fails) without a real
DATABASE_URL, same convention as every other `*_e2e.py` file.
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
from app.services.applicability import find_applicable_procedures
from app.services.claims import capture_claim
from app.services.observations import persist_observation, promote_observation_to_claim
from app.services.procedures import (
    approve_procedure,
    capture_procedure,
    get_procedure,
    record_execution_outcome,
)

DATABASE_URL = os.environ.get("DATABASE_URL")

pytestmark = pytest.mark.skipif(
    not DATABASE_URL, reason="requires a real DATABASE_URL -- this is a live-database integration test"
)

RUN = uuid4().hex[:8]
NAME_PREFIX = f"canon-demo-{RUN}"


async def _cleanup(pool) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{NAME_PREFIX}%",
    )
    await pool.execute("DELETE FROM task_nodes WHERE name LIKE $1", f"{NAME_PREFIX}%")
    await pool.execute("DELETE FROM knowledge_nodes WHERE name LIKE $1", f"{NAME_PREFIX}%")
    await pool.execute("DELETE FROM observations WHERE label LIKE $1", f"{NAME_PREFIX}%")
    await pool.execute(
        "DELETE FROM episode_links WHERE episode_id IN "
        "(SELECT id FROM episodes WHERE content_ref LIKE $1)", f"{NAME_PREFIX}%",
    )
    await pool.execute("DELETE FROM episodes WHERE content_ref LIKE $1", f"{NAME_PREFIX}%")
    await pool.execute("DELETE FROM trace_events WHERE trace_id LIKE $1", f"{NAME_PREFIX}%")
    await pool.execute("DELETE FROM agent_traces WHERE trace_id LIKE $1", f"{NAME_PREFIX}%")


def test_canonical_episodic_and_procedural_memory_lifecycle():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        try:
            await _cleanup(pool)
            project_id = f"{NAME_PREFIX}-project"

            # --- Stage 0: a real, minimal trace + event, the substrate's
            # own raw non-lossy input (12_trace_ingestion_pipeline.sql). ---
            trace_id = f"{NAME_PREFIX}-trace"
            session_id = f"{NAME_PREFIX}-session"
            now = datetime.now(timezone.utc)
            await pool.execute(
                "INSERT INTO agent_traces (trace_id, session_id, provider, "
                "started_at, schema_version) VALUES ($1, $2, 'test', $3, 'v1')",
                trace_id, session_id, now,
            )
            event_id = await pool.fetchval(
                "INSERT INTO trace_events (trace_id, session_id, sequence, "
                "event_type, timestamp, tool_name, success, dedup_key, schema_version) "
                "VALUES ($1, $2, 1, 'tool_call', $3, 'edit_file', true, $4, 'v1') "
                "RETURNING id",
                trace_id, session_id, now, f"{NAME_PREFIX}-dedup",
            )

            # --- Stage 1: episode -- the real non-lossy episodic record,
            # carrying real scope (project_id) an observation/claim will
            # later inherit. ---
            episode_id = await pool.fetchval(
                "INSERT INTO episodes (episode_type, content_ref, timestamp, "
                "metadata, project_id) VALUES ('trace', $1, $2, $3::jsonb, $4) "
                "RETURNING id",
                f"{NAME_PREFIX}#trace:0:1", now, {"session_id": session_id}, project_id,
            )

            # --- Stage 2: observation -- deterministic extraction from the
            # real trace_event above (app/services/observations.py's real
            # writer, not a hand-rolled INSERT). ---
            observation_id = await persist_observation(
                pool,
                observation_type="tool_outcome",
                label=f"{NAME_PREFIX} edit_file succeeded",
                extractor_kind="deterministic",
                event_ids=[str(event_id)],
                properties={"tool_name": "edit_file", "success": True},
            )
            assert observation_id

            # --- Chain A: observation -> claim, with scope DERIVED from
            # the real episode's own project_id (promote_observation_to_
            # claim's real inheritance -- not passed explicitly here, to
            # prove the derivation itself, not just that the parameter
            # exists). Needs a live task_node to attach to (capture_claim's
            # real contract: a claim with no live task_node has nothing to
            # link to). ---
            task_node_id = await pool.fetchval(
                "INSERT INTO task_nodes (name) VALUES ($1) RETURNING id",
                f"{NAME_PREFIX}-task-node",
            )
            claim_id = await promote_observation_to_claim(
                pool,
                observation_id=observation_id,
                task_ids=[str(task_node_id)],
                justification_episode_id=str(episode_id),
            )
            assert claim_id, "promotion must succeed against a live task_node"
            claim_row = await pool.fetchrow(
                "SELECT scope_type, scope_entity_id, "
                "properties->>'epistemic_status' AS epistemic_status "
                "FROM knowledge_nodes WHERE id = $1", claim_id,
            )
            assert claim_row["scope_type"] == "project", (
                "the real gap this proves closed: scope must be DERIVED "
                "from the justifying episode's own project_id, not left "
                "NULL"
            )
            assert claim_row["scope_entity_id"] == project_id
            assert claim_row["epistemic_status"] == "observed", (
                "a deterministic-extractor observation must promote to "
                "epistemic_status='observed', never 'inferred'"
            )

            # --- Chain B: procedure capture -- independent of Chain A
            # today (the real, reported gap: capture_procedure does not
            # query claims for preconditions). Scope explicitly supplied
            # here as the same project, demonstrating a caller CAN align
            # the two chains' scope by convention even though no code
            # wires them together automatically yet. ---
            root = await capture_procedure(
                pool,
                name=f"{NAME_PREFIX}-root",
                goal="demonstrate the canonical procedural memory lifecycle",
                provenance="system_pending_review",
                scope_type="project",
                scope_entity_id=project_id,
                steps=[{"order": 0, "goal": "do the demonstrated work"}],
            )
            procedure_row_id = root["id"]
            procedure_id = root["procedure_id"]

            # A fresh candidate must be invisible to require_verified
            # retrieval -- the substrate's own "nothing is born trusted"
            # rule, proven live before promotion, not just asserted.
            pre_promotion = await find_applicable_procedures(
                pool, current_scope={"names": []}, require_verified=True,
            )
            assert procedure_row_id not in {str(r["id"]) for r in pre_promotion}

            # --- Stage: verification -- real ticket-13 threshold (>=10
            # successes / 0 failures / >=3 distinct contexts), not a
            # shortcut. Each record_execution_outcome call is real,
            # recorded evidence. ---
            contexts = [f"{NAME_PREFIX}-ctx-{i}" for i in range(3)]
            for i in range(10):
                await record_execution_outcome(
                    pool,
                    procedure_row_id=procedure_row_id,
                    success=True,
                    context_key=contexts[i % 3],
                )
            promoted = await get_procedure(pool, procedure_row_id)
            assert promoted["verification_state"] == "verified", (
                "10 real successes across 3 distinct contexts, 0 failures, "
                "must cross the real ticket-13 threshold"
            )

            # verification_state='verified' alone does NOT make a
            # procedure retrievable by automatic selection --
            # check_hard_constraints' own real gate also requires
            # approval_status='approved' (migration 20 + applicability.py's
            # "verified gates automatic retrieval; explicit invocation
            # bypasses both gates" rule): pure statistics are not a human
            # sign-off. Proven here BEFORE approving, not assumed.
            still_pre_approval = await find_applicable_procedures(
                pool, current_scope={"names": []}, require_verified=True, limit=1000,
            )
            assert procedure_row_id not in {str(r["id"]) for r in still_pre_approval}, (
                "verified-but-unapproved must still be unreachable by "
                "automatic retrieval"
            )
            await approve_procedure(pool, procedure_row_id=procedure_row_id, approved_by="test-approver")

            # --- Stage: retrieval -- the now-verified AND approved
            # procedure must be findable by the real applicability
            # pipeline (hard-constraint cascade, no preconditions on this
            # procedure so nothing to violate). ---
            # limit=1000: this is a real-corpus live database that may
            # carry other verified procedures ranked ahead of this one by
            # cost/similarity (no goal_embedding given here) -- this stage
            # proves RETRIEVABILITY (the procedure is a real member of the
            # applicable set), not top-of-ranking, so the limit is widened
            # rather than asserting on rank order.
            post_promotion = await find_applicable_procedures(
                pool, current_scope={"names": []}, require_verified=True,
                limit=1000, candidate_pool_size=5000,
            )
            assert procedure_row_id in {str(r["id"]) for r in post_promotion}, (
                "a verified procedure with no unmet preconditions must be "
                "retrievable"
            )

            # --- Stage: compile + persist -- the real plan/graph compiler,
            # not a stub. expand_procedure_steps is the real entry point a
            # caller uses BEFORE compile_plan (mirrors find_best_way's own
            # tier-2 path in mcp_server/server.py). ---
            fresh = await get_procedure(pool, procedure_row_id)
            nodes = await expand_procedure_steps(
                pool, procedure_id=procedure_id, procedure_version=fresh["version"],
                steps=fresh["steps"],
            )
            compiled = compile_plan(
                procedure_id=procedure_id, procedure_version=fresh["version"],
                procedure_row_id=procedure_row_id, procedure_payload=fresh,
                task_description="demonstrate the canonical procedural memory lifecycle",
                nodes=nodes, extractor_version="test_canonical_e2e@1",
                created_by="test_canonical_e2e",
            )
            persisted, was_new = await persist_compiled_plan(pool, compiled)
            assert was_new
            graph_row = await pool.fetchrow(
                "SELECT id FROM task_graphs WHERE id = $1", persisted.graph.id,
            )
            assert graph_row is not None, "the compiled graph must be real, persisted storage"

            # --- Stage: execution -- one real `executions` audit row,
            # born complete (migration 23's own append-only contract). ---
            execution_id = await record_plan_execution(
                pool, compiled=persisted, outcome="success",
                created_by="test_canonical_e2e",
            )
            execution_row = await pool.fetchrow(
                "SELECT outcome FROM executions WHERE id = $1", execution_id,
            )
            assert execution_row["outcome"] == "success"

            # --- Stage: evidence from that real execution feeds back into
            # the SAME procedure's own verification_stats (the substrate's
            # own closed loop: use produces evidence, evidence sustains
            # trust). ---
            await record_execution_outcome(
                pool, procedure_row_id=procedure_row_id, success=True,
                context_key=contexts[0],
            )
            final = await get_procedure(pool, procedure_row_id)
            assert final["verification_stats"]["successes"] == 11
            assert final["verification_state"] == "verified", (
                "a verified procedure must remain verified after further "
                "real successful use, never silently demoted"
            )
        finally:
            await _cleanup(pool)
            await pool.close()

    asyncio.run(_run())
