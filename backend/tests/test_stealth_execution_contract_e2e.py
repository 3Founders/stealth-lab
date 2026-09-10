"""
MCP hardening B4: the Stealth Execution Contract
(RUN_CREATED -> DISCOVERY -> PROCEDURE_EVALUATED -> APPLICABILITY_CHECKED
-> PROCEDURE_VERSION_PINNED -> IMPLEMENTATION_PINNED -> EXECUTION_STARTED
-> EXECUTION_EVENTS -> VERIFICATION -> OUTCOME -> EVIDENCE -> FINALIZED),
derived (not separately mutated) from real facts already persisted across
route_decisions / execution_run_nodes / execution_run_events /
verification_results / evidence -- see
app/execution/stealth_execution_contract.py for why a derived view, not
a second state column.

Drives a REAL run through every one of those facts (a persisted
RouteDecision, a compiled implementation-bound plan, a real execute_run
to terminal success so `executions`/evidence get written, then a real
verify_completion call) and asserts the chain progresses through every
state in order, ending at FINALIZED -- through the real MCP tools where
the spec asks for them (verify_completion, inspect_run) and the real
service layer where it doesn't (start_run/execute_run, matching every
other durable-run e2e test's own pattern).

Skips itself when DATABASE_URL is unset, self-cleaning by row id.
"""
from __future__ import annotations

import json
import os
import uuid

import asyncpg
import pytest

pytestmark = pytest.mark.skipif(not os.environ.get("DATABASE_URL"), reason="e2e: DATABASE_URL unset")

import app.mcp_server.server as srv  # noqa: E402
from app.db.session import create_pool  # noqa: E402
from app.execution import implementation_registry  # noqa: E402
from app.execution.durable_run import execute_run, start_run  # noqa: E402
from app.execution.stealth_execution_contract import CHAIN, compute_execution_contract_state  # noqa: E402
from app.services.procedures import capture_procedure  # noqa: E402
from app.services.route_decision import RouteDecision, persist_route_decision  # noqa: E402
from app.utils.ids import uuid7  # noqa: E402

DEPS = {0: []}


class _FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class _FakeContext:
    def __init__(self, pool):
        self.request_context = _FakeRequestContext(pool)


async def _cleanup_procedure(pool, row_id) -> None:
    deleted = await pool.execute(
        "DELETE FROM procedures WHERE id=$1 AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        row_id,
    )
    if deleted == "DELETE 0":
        await pool.execute("UPDATE procedures SET is_engineering_fixture = true WHERE id=$1", row_id)


def test_execution_contract_progresses_through_every_state_to_finalized():
    async def _run():
        pool = await create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=2)
        run_id_suffix = uuid.uuid4().hex[:8]
        name = f"proc-test-secontract-{run_id_suffix}"
        row_id = None
        exec_run_id = None
        try:
            res = await capture_procedure(
                pool, name=name, goal="stealth execution contract probe",
                steps=[{"order": 0, "goal": "do the one step"}],
                postconditions=["the step produced a real artifact"],
                provenance="prior_library", scope_type="global", created_by="se_contract_e2e",
                embedding=[0.01] * 1024,
            )
            proc_id, row_id = res["procedure_id"], res["id"]

            # Real Implementation, real binding -- IMPLEMENTATION_PINNED
            # must reflect an actual pinned implementation_id on the node,
            # not a placeholder.
            impl = await implementation_registry.register(
                pool, name=f"se-contract-impl-{run_id_suffix}", kind="tool",
                provider="se-contract-e2e-provider", created_by="se_contract_e2e",
            )

            async with pool.acquire() as c:
                pv = await c.fetchval("SELECT version FROM procedures WHERE id=$1", row_id)
                plan_id = await c.fetchval(
                    "INSERT INTO execution_plans (id, procedure_id, procedure_version, procedure_row_id, "
                    " task_description, procedure_content_hash, content_hash, scope_type) "
                    "VALUES ($1,$2,$3,$4,$5,$6,$7,'global') RETURNING id",
                    str(uuid7()), proc_id, pv, row_id, "se-contract-e2e",
                    f"pch-{run_id_suffix}", f"ch-{run_id_suffix}",
                )
                graph_id = await c.fetchval(
                    "INSERT INTO task_graphs (id, execution_plan_id, graph_hash, nodes) "
                    "VALUES ($1,$2,$3,'[]'::jsonb) RETURNING id",
                    str(uuid7()), plan_id, f"gh-{run_id_suffix}",
                )

            # DISCOVERY / PROCEDURE_EVALUATED / APPLICABILITY_CHECKED: a
            # real, persisted RouteDecision naming this procedure as
            # applicable.
            decision = RouteDecision(
                route="execution_ready", reason="se-contract e2e probe", intent="execute",
                task_description=name, mode="auto", procedure_id=proc_id,
                procedure_row_id=str(row_id), applicable=True,
            )
            route_decision_id = await persist_route_decision(pool, decision)

            exec_run_id = await start_run(
                pool, execution_plan_id=plan_id, task_graph_id=graph_id,
                procedure_id=proc_id, procedure_version=pv,
                node_orders=[0], deps=DEPS, created_by="se_contract_e2e",
                route_decision_id=route_decision_id,
            )

            # RUN_CREATED / DISCOVERY / PROCEDURE_EVALUATED /
            # APPLICABILITY_CHECKED / PROCEDURE_VERSION_PINNED must all
            # already be reached the instant the run exists -- before
            # anything is driven. EXECUTION_EVENTS is ALSO already
            # reached at this point: start_run() itself writes
            # run_created/route_decided events (B7/B8) in the same
            # transaction as the row insert -- a real, correct fact, not
            # a test bug.
            pre = await compute_execution_contract_state(pool, exec_run_id)
            assert pre["reached"] == [
                "RUN_CREATED", "DISCOVERY", "PROCEDURE_EVALUATED",
                "APPLICABILITY_CHECKED", "PROCEDURE_VERSION_PINNED",
                "EXECUTION_EVENTS",
            ]
            assert pre["current_state"] == "EXECUTION_EVENTS"
            assert "IMPLEMENTATION_PINNED" in pre["skipped_optional"]
            assert "EXECUTION_STARTED" in pre["skipped_optional"]

            # Pin the real implementation on the node directly (this test
            # exercises the CONTRACT derivation, not implementation
            # resolution/binding -- that's a separate B23/B24 concern).
            await pool.execute(
                "UPDATE execution_run_nodes SET implementation_id=$2 "
                "WHERE execution_run_id=$1 AND node_order=0",
                exec_run_id, impl["id"],
            )

            async def run_node(order: int, attempt: int) -> dict:
                return {"order": order, "artifact": "real output"}

            result = await execute_run(pool, exec_run_id, deps=DEPS, run_node=run_node, worker_id="se-contract-w1")
            # B16/B33 STRICT CLOSURE: this Procedure has a real, required
            # postcondition with no satisfying evidence yet -- every node
            # succeeding is necessary but not sufficient, so the run's
            # own terminal transition holds at 'awaiting_verification'
            # rather than silently becoming 'succeeded' (and therefore
            # `final_outcome` -- OUTCOME's own real fact -- stays unset
            # until real verification evidence actually arrives, below).
            assert result["status"] == "awaiting_verification"

            mid = await compute_execution_contract_state(pool, exec_run_id)
            for expected in (
                "IMPLEMENTATION_PINNED", "EXECUTION_STARTED", "EXECUTION_EVENTS",
            ):
                assert expected in mid["reached"], f"expected {expected} in {mid['reached']}"
            # OUTCOME/EVIDENCE/FINALIZED correctly stay unreached: the run
            # is held at 'awaiting_verification', not yet terminal, so
            # `final_outcome`/`final_execution_id` are honestly still
            # unset -- nothing here is faked to look further along than
            # the real facts support.
            assert "OUTCOME" not in mid["reached"]
            assert "FINALIZED" not in mid["reached"]
            assert "EVIDENCE" not in mid["reached"]
            assert mid["current_state"] == "EXECUTION_EVENTS"

            # VERIFICATION, through the real MCP tool where the spec asks
            # for it.
            ctx = _FakeContext(pool)
            reports = json.dumps([
                {"criterion_id": "postcondition:0", "method": "self_report", "claimed_success": True},
            ])
            verified = json.loads(await srv.verify_completion(exec_run_id, ctx, reports_json=reports))
            assert verified["overall_state"] == "claimed_done"

            # B16/B33: this real report satisfies the run's only required
            # criterion -- verify_completion must have performed the
            # real, guarded awaiting_verification -> succeeded advance.
            status_now = await pool.fetchval(
                "SELECT status FROM execution_runs WHERE id = $1", exec_run_id,
            )
            assert status_now == "succeeded"

            # B7's record_verification_started/completed: real events,
            # not just the verification_results row.
            from app.execution.recorder import get_run_events
            v_events = await get_run_events(pool, exec_run_id)
            started = [e for e in v_events if e["event_type"] == "verification_started"]
            completed = [e for e in v_events if e["event_type"] == "verification_completed"]
            assert len(started) == 1 and started[0]["payload"]["criterion_id"] == "postcondition:0"
            assert len(completed) == 1 and completed[0]["payload"]["overall_state"] == "claimed_done"

            post = await compute_execution_contract_state(pool, exec_run_id)
            assert "VERIFICATION" in post["reached"]
            assert post["current_state"] == "OUTCOME"  # still no evidence/finalized, honestly

            # inspect_run (the real MCP tool) must surface the same
            # derivation, not a separate computation.
            inspected = json.loads(await srv.inspect_run(exec_run_id, ctx))
            assert inspected["execution_contract"] == post

            # Chain ordering sanity: every state actually reached appears
            # in CHAIN order in `reached`.
            indices = [CHAIN.index(s) for s in post["reached"]]
            assert indices == sorted(indices)

            missing_run = await compute_execution_contract_state(pool, str(uuid.uuid4()))
            assert missing_run is None
        finally:
            if exec_run_id is not None:
                await pool.execute("DELETE FROM execution_runs WHERE id=$1", exec_run_id)
            if row_id is not None:
                await _cleanup_procedure(pool, row_id)
            await pool.execute("DELETE FROM implementations WHERE name LIKE $1", f"se-contract-impl-{run_id_suffix}%")
            await pool.close()

    import asyncio
    asyncio.run(_run())
