"""
MCP hardening B9-B13: live-DB half of the recursion guard --
root_run_id propagation (migration 52), the ancestor-chain-derived
depth/budget/wall-clock checks, and `describe_child_status`'s
derived "waiting on a live child" read.

Same pattern as every other `*_e2e.py` file: skips without a real
DATABASE_URL, self-cleaning by name prefix.
"""
import asyncio
import os
from datetime import datetime, timedelta, timezone
from uuid import uuid4

import pytest

from app.config import settings
from app.db.session import create_pool
from app.execution import durable_run as _dr
from app.execution.recursion_guard import (
    ChildExecutionBudgetExceeded,
    CostBudgetExceeded,
    RecursionCycleDetected,
    RecursionDepthExceeded,
    ToolCallBudgetExceeded,
    TokenBudgetExceeded,
    WallClockBudgetExceeded,
    check_recursion_limits,
    decide_child_failure_strategy,
    describe_child_status,
    describe_terminal_child_failure,
)
from app.execution.plan_persistence import persist_compiled_plan
from app.execution.plans import compile_plan
from app.execution.procedure_graph import expand_procedure_steps
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


async def _cleanup(pool, name_prefix: str) -> None:
    await pool.execute(
        "DELETE FROM procedures WHERE name LIKE $1 "
        "AND id NOT IN (SELECT procedure_row_id FROM execution_plans)",
        f"{name_prefix}%",
    )
    await pool.execute(
        "UPDATE procedures SET is_engineering_fixture = true WHERE name LIKE $1", f"{name_prefix}%",
    )


async def _capture(pool, name: str, **kwargs) -> dict:
    kwargs.setdefault("goal", name)
    result = await capture_procedure(
        pool, name=name, provenance="system_pending_review", scope_type="global", **kwargs,
    )
    return dict(await pool.fetchrow("SELECT * FROM procedures WHERE id = $1", result["id"]))


async def _compiled_plan(pool, procedure: dict, task_description: str):
    steps = procedure.get("steps") or [{"order": 0, "goal": task_description}]
    nodes = await expand_procedure_steps(
        pool, procedure_id=procedure["procedure_id"], procedure_version=procedure["version"], steps=steps,
    )
    compiled = compile_plan(
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        procedure_row_id=procedure["id"], procedure_payload=procedure,
        task_description=task_description, nodes=nodes,
        extractor_version="test_recursion_guard_e2e@1", created_by="test",
    )
    compiled, _ = await persist_compiled_plan(pool, compiled)
    return compiled


async def _start_run(pool, procedure: dict, compiled, **kwargs) -> str:
    graph = compiled.graph
    return await _dr.start_run(
        pool, execution_plan_id=str(compiled.plan.id), task_graph_id=str(compiled.graph.id),
        procedure_id=procedure["procedure_id"], procedure_version=procedure["version"],
        node_orders=[n.order for n in graph.nodes], deps={n.order: list(n.deps) for n in graph.nodes},
        **kwargs,
    )


def test_root_run_points_at_itself_and_child_inherits_root():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recroot-{run_id}"
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)

            root_id = await _start_run(pool, procedure, compiled)
            root_row = await pool.fetchrow("SELECT root_run_id, parent_run_id FROM execution_runs WHERE id = $1", root_id)
            assert str(root_row["root_run_id"]) == root_id
            assert root_row["parent_run_id"] is None

            child_id = await _start_run(pool, procedure, compiled, parent_run_id=root_id)
            child_row = await pool.fetchrow("SELECT root_run_id, parent_run_id FROM execution_runs WHERE id = $1", child_id)
            assert str(child_row["root_run_id"]) == root_id
            assert str(child_row["parent_run_id"]) == root_id

            grandchild_id = await _start_run(pool, procedure, compiled, parent_run_id=child_id)
            grandchild_row = await pool.fetchrow("SELECT root_run_id FROM execution_runs WHERE id = $1", grandchild_id)
            assert str(grandchild_row["root_run_id"]) == root_id, "root_run_id must propagate transitively, not just one level"
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_detects_cycle_via_procedure_id():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-reccycle-{run_id}"
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)
            root_id = await _start_run(pool, procedure, compiled)

            with pytest.raises(RecursionCycleDetected):
                await check_recursion_limits(
                    pool, parent_run_id=root_id, candidate_procedure_id=procedure["procedure_id"],
                )

            # A genuinely different candidate must pass.
            chain = await check_recursion_limits(
                pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()),
            )
            assert chain.root_run_id == root_id
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_enforces_configured_depth(monkeypatch):
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recdepth-{run_id}"
        monkeypatch.setattr(settings, "procedure_run_max_recursion_depth", 2)
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)

            root_id = await _start_run(pool, procedure, compiled)  # depth 1
            # A child OF the root would be depth 2 -- exactly at the limit, allowed.
            await check_recursion_limits(pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()))
            child_id = await _start_run(pool, procedure, compiled, parent_run_id=root_id)  # depth 2

            # A child of THAT child would be depth 3 -- exceeds max_recursion_depth=2.
            with pytest.raises(RecursionDepthExceeded):
                await check_recursion_limits(pool, parent_run_id=child_id, candidate_procedure_id=str(uuid4()))
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_enforces_configured_child_budget(monkeypatch):
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recbudget-{run_id}"
        monkeypatch.setattr(settings, "procedure_run_max_child_executions", 2)
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)

            root_id = await _start_run(pool, procedure, compiled)  # chain size 1
            await _start_run(pool, procedure, compiled, parent_run_id=root_id)  # chain size 2 -- at budget

            with pytest.raises(ChildExecutionBudgetExceeded):
                await check_recursion_limits(pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()))
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_enforces_configured_wall_clock_budget(monkeypatch):
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recwallclock-{run_id}"
        monkeypatch.setattr(settings, "procedure_run_max_wall_clock_seconds", 1)
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)
            root_id = await _start_run(pool, procedure, compiled)
            # Backdate started_at so the 1-second budget is already exceeded.
            await pool.execute(
                "UPDATE execution_runs SET started_at = $1 WHERE id = $2",
                datetime.now(timezone.utc) - timedelta(seconds=10), root_id,
            )
            with pytest.raises(WallClockBudgetExceeded):
                await check_recursion_limits(pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()))
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_enforces_configured_token_budget(monkeypatch):
    """B12's token budget: real, ATOMIC usage (durable_run.record_run_usage)
    summed across the whole chain (same root_run_id), never estimated."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-rectokens-{run_id}"
        monkeypatch.setattr(settings, "procedure_run_max_tokens", 100)
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)

            root_id = await _start_run(pool, procedure, compiled)
            await check_recursion_limits(pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()))

            await _dr.record_run_usage(pool, root_id, tokens=150)
            with pytest.raises(TokenBudgetExceeded):
                await check_recursion_limits(pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()))
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_enforces_configured_tool_call_budget(monkeypatch):
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-rectoolcalls-{run_id}"
        monkeypatch.setattr(settings, "procedure_run_max_tool_calls", 5)
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)

            root_id = await _start_run(pool, procedure, compiled)
            child_id = await _start_run(pool, procedure, compiled, parent_run_id=root_id)
            # Split across TWO runs in the same chain -- proves this is a
            # real SUM over the chain, not just the parent's own row.
            await _dr.record_run_usage(pool, root_id, tool_calls=3)
            await _dr.record_run_usage(pool, child_id, tool_calls=3)

            with pytest.raises(ToolCallBudgetExceeded):
                await check_recursion_limits(pool, parent_run_id=child_id, candidate_procedure_id=str(uuid4()))
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_check_recursion_limits_enforces_configured_cost_budget(monkeypatch):
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-reccost-{run_id}"
        monkeypatch.setattr(settings, "procedure_run_max_cost_usd", 1.0)
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)

            root_id = await _start_run(pool, procedure, compiled)
            await _dr.record_run_usage(pool, root_id, cost_usd=1.5)

            with pytest.raises(CostBudgetExceeded):
                await check_recursion_limits(pool, parent_run_id=root_id, candidate_procedure_id=str(uuid4()))
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_record_run_usage_is_a_real_atomic_increment_not_an_overwrite():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recusageinc-{run_id}"
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)
            root_id = await _start_run(pool, procedure, compiled)

            await _dr.record_run_usage(pool, root_id, tokens=10, tool_calls=1, cost_usd=0.1)
            await _dr.record_run_usage(pool, root_id, tokens=10, tool_calls=1, cost_usd=0.1)

            row = await pool.fetchrow(
                "SELECT tokens_used, tool_calls_used, cost_usd_used FROM execution_runs WHERE id = $1", root_id,
            )
            assert row["tokens_used"] == 20
            assert row["tool_calls_used"] == 2
            assert float(row["cost_usd_used"]) == pytest.approx(0.2)

            # A zero-usage call is a real, honest no-op -- never a
            # spurious UPDATE for nothing actually spent.
            await _dr.record_run_usage(pool, root_id)
            row2 = await pool.fetchrow("SELECT tokens_used FROM execution_runs WHERE id = $1", root_id)
            assert row2["tokens_used"] == 20
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_describe_child_status_reports_a_live_child_and_ignores_terminal_ones():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recwaiting-{run_id}"
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step one"}, {"order": 1, "goal": "step two"}])
            compiled = await _compiled_plan(pool, procedure, name)
            root_id = await _start_run(pool, procedure, compiled)
            node_row_id = await pool.fetchval(
                "SELECT id FROM execution_run_nodes WHERE execution_run_id = $1 AND node_order = 0", root_id,
            )

            assert await describe_child_status(pool, run_id=root_id, node_row_id=str(node_row_id)) is None

            child_id = await _start_run(
                pool, procedure, compiled, parent_run_id=root_id, parent_node_id=str(node_row_id),
            )
            status = await describe_child_status(pool, run_id=root_id, node_row_id=str(node_row_id))
            assert status is not None
            assert status["child_run_id"] == child_id
            assert status["child_status"] == "pending"

            await pool.execute("UPDATE execution_runs SET status = 'cancelled' WHERE id = $1", child_id)
            assert await describe_child_status(pool, run_id=root_id, node_row_id=str(node_row_id)) is None, (
                "a terminal child must no longer be reported as 'waiting on'"
            )
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


# ---------------------------------------------------------------------------
# B11: recursive failure semantics -- real, decided (not fabricated)
# strategies from a real failed child, applied for real when the
# decision is fail_parent.
# ---------------------------------------------------------------------------
class _Transient(Exception):
    """Classifies to error_class='transient' via durable_run.classify_error
    (name contains 'transient') -- a REAL retryable failure signal."""


async def _fail_a_child_node(pool, child_id: str, *, exc: Exception, max_attempts: int = 3) -> None:
    """Drives `child_id`'s only node through a REAL failing execute_run
    so its error_class/attempt_count/max_attempts are genuine, not
    hand-set."""
    async def run_node(order: int, attempt: int) -> dict:
        raise exc

    await pool.execute(
        "UPDATE execution_run_nodes SET max_attempts = $2 WHERE execution_run_id = $1",
        child_id, max_attempts,
    )
    await _dr.execute_run(pool, child_id, deps={0: []}, run_node=run_node, worker_id="b11-e2e")


async def _fail_a_child_node_once(pool, child_id: str, *, error_class: str, max_attempts: int = 3) -> None:
    """A single, HOST-REPORTED failure (B6's report_node_progress --
    claims+finishes the node exactly once, no internal retry loop) that
    leaves real attempt budget remaining, then finalizes the RUN itself
    to a real terminal 'failed' state from that one node's real status --
    exactly the shape a genuinely-retryable-but-not-yet-retried child
    run has. `execute_run`'s OWN internal retry loop would otherwise
    exhaust every attempt internally before a run can ever go terminal,
    which would make 'retry' remaining a contradiction -- this reproduces
    the real B6 host-executed path where that internal loop never runs."""
    await pool.execute(
        "UPDATE execution_run_nodes SET max_attempts = $2 WHERE execution_run_id = $1",
        child_id, max_attempts,
    )
    # A run stays 'pending' until claimed (execute_run/resume_run's own
    # job, neither of which this helper calls) -- claim for real first,
    # matching the B4 status-transition guard's real requirement
    # (pending -> failed directly is correctly rejected; pending ->
    # running -> failed is the real path).
    await _dr._claim_run(pool, child_id, "b11-e2e")
    await _dr.report_node_progress(
        pool, child_id, 0, ok=False, error_class=error_class, error={"message": "real reported failure"},
    )
    await _dr._finalize(pool, child_id)


def test_decide_child_failure_strategy_retries_a_real_retryable_failure():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recfailretry-{run_id}"
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)
            root_id = await _start_run(pool, procedure, compiled)
            node_row_id = await pool.fetchval(
                "SELECT id FROM execution_run_nodes WHERE execution_run_id = $1 AND node_order = 0", root_id,
            )
            child_id = await _start_run(
                pool, procedure, compiled, parent_run_id=root_id, parent_node_id=str(node_row_id),
            )
            await _fail_a_child_node_once(pool, child_id, error_class="transient", max_attempts=3)

            failure = await describe_terminal_child_failure(pool, run_id=root_id, node_row_id=str(node_row_id))
            assert failure is not None
            assert failure["child_run_id"] == child_id

            decision = await decide_child_failure_strategy(
                pool, parent_run_id=root_id, parent_node_id=str(node_row_id), child_run_id=child_id,
            )
            assert decision["strategy"] == "retry"

            # The parent's own node must NOT have been touched for a
            # 'retry' decision -- still real, honest 'pending' (never
            # driven in this test).
            parent_status = await pool.fetchval("SELECT status FROM execution_run_nodes WHERE id = $1", node_row_id)
            assert parent_status == "pending"
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_decide_child_failure_strategy_finds_a_real_alternative_implementation():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recfailalt-{run_id}"
        impl_ids: list[str] = []
        try:
            from app.execution import implementation_registry
            from app.services.procedure_implementation_bindings import activate_binding, link_implementation

            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)
            root_id = await _start_run(pool, procedure, compiled)
            node_row_id = await pool.fetchval(
                "SELECT id FROM execution_run_nodes WHERE execution_run_id = $1 AND node_order = 0", root_id,
            )
            child_id = await _start_run(
                pool, procedure, compiled, parent_run_id=root_id, parent_node_id=str(node_row_id),
            )
            await _fail_a_child_node(pool, child_id, exc=ValueError("non-retryable"), max_attempts=1)

            # TWO real active implementation bindings for the child's procedure.
            for i in range(2):
                impl = await implementation_registry.register(
                    pool, name=f"recfailalt-impl-{run_id}-{i}", kind="tool",
                    provider="recfailalt-e2e", created_by="test",
                )
                impl_ids.append(impl["id"])
                binding = await link_implementation(
                    pool, procedure_id=procedure["procedure_id"], implementation_id=impl["id"],
                    role="primary", created_by="test",
                )
                await activate_binding(pool, binding["id"])

            decision = await decide_child_failure_strategy(
                pool, parent_run_id=root_id, parent_node_id=str(node_row_id), child_run_id=child_id,
            )
            assert decision["strategy"] == "search_alternative"
            assert "implementation bindings" in decision["reason"]
        finally:
            for iid in impl_ids:
                await pool.execute("DELETE FROM procedure_implementations WHERE implementation_id=$1", iid)
                await pool.execute("DELETE FROM implementations WHERE id=$1", iid)
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_decide_child_failure_strategy_branches_to_a_real_different_procedure():
    """B11 STRICT CLOSURE: `branch` (distinct from `search_alternative`)
    -- no alternative Implementation for the failed procedure, but a
    genuinely DIFFERENT, real, verified+approved Procedure exists for
    the same goal. Uses B32's own real `excluded_procedure_ids`
    exclusion primitive, not an ad-hoc filter."""
    async def _run():
        from app.services.embeddings import Embedder

        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recfailbranch-{run_id}"
        name_alt = f"proc-test-recfailbranchalt-{run_id}"
        try:
            embedder = Embedder()
            goal_text = f"safely rotate the deployment credentials ({run_id})"
            vec = await embedder.embed_one(goal_text, input_type="document")

            procedure = await _capture(
                pool, name, goal=goal_text, embedding=vec,
                embedding_model_id=embedder.embedding_model_id(),
                steps=[{"order": 0, "goal": "step"}],
            )
            compiled = await _compiled_plan(pool, procedure, goal_text)
            root_id = await _start_run(pool, procedure, compiled)
            node_row_id = await pool.fetchval(
                "SELECT id FROM execution_run_nodes WHERE execution_run_id = $1 AND node_order = 0", root_id,
            )
            child_id = await _start_run(
                pool, procedure, compiled, parent_run_id=root_id, parent_node_id=str(node_row_id),
            )
            await _fail_a_child_node(pool, child_id, exc=ValueError("non-retryable"), max_attempts=1)

            # A genuinely different, real, VERIFIED+APPROVED procedure for
            # the exact same goal -- diagnose_candidates' own
            # require_verified=True default demands real evidence, never
            # a bare candidate.
            alt = await capture_procedure(
                pool, name=name_alt, goal=goal_text, embedding=vec,
                embedding_model_id=embedder.embedding_model_id(),
                provenance="system_pending_review", scope_type="global",
                steps=[{"order": 0, "goal": "a completely different approach"}],
            )
            for i in range(MIN_SUCCESSES_FOR_VERIFIED):
                await record_execution_outcome(
                    pool, procedure_row_id=alt["id"], success=True,
                    context_key=f"ctx-{i % (MIN_DISTINCT_CONTEXTS_FOR_VERIFIED + 1)}",
                )
            await approve_procedure(pool, procedure_row_id=alt["id"], approved_by="test")
            await pool.execute("UPDATE procedures SET is_engineering_fixture = false WHERE id = $1", alt["id"])

            decision = await decide_child_failure_strategy(
                pool, parent_run_id=root_id, parent_node_id=str(node_row_id), child_run_id=child_id,
            )
            assert decision["strategy"] == "branch", decision
            assert str(alt["procedure_id"]) in decision["reason"]
        finally:
            await _cleanup(pool, name)
            await _cleanup(pool, name_alt)
            await pool.close()

    asyncio.run(_run())


def test_decide_child_failure_strategy_asks_user_with_no_automated_path():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recfailask-{run_id}"
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)
            root_id = await _start_run(pool, procedure, compiled)
            node_row_id = await pool.fetchval(
                "SELECT id FROM execution_run_nodes WHERE execution_run_id = $1 AND node_order = 0", root_id,
            )
            child_id = await _start_run(
                pool, procedure, compiled, parent_run_id=root_id, parent_node_id=str(node_row_id),
            )
            await _fail_a_child_node(pool, child_id, exc=ValueError("non-retryable, no alternative"), max_attempts=1)

            decision = await decide_child_failure_strategy(
                pool, parent_run_id=root_id, parent_node_id=str(node_row_id), child_run_id=child_id,
            )
            assert decision["strategy"] == "ask_user"

            # The root's own node was never claimed/driven (this test
            # only drives the CHILD), so it's still real, honest
            # 'pending' -- the assertion that matters for 'ask_user' is
            # that decide_child_failure_strategy did NOT transition it
            # (unlike fail_parent, which does).
            parent_status = await pool.fetchval("SELECT status FROM execution_run_nodes WHERE id = $1", node_row_id)
            assert parent_status == "pending", "ask_user must not touch the parent's node"
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_decide_child_failure_strategy_fails_parent_when_budget_exhausted(monkeypatch):
    """B11's hard invariant: 'never leave the parent permanently RUNNING
    after a terminal child' -- when no automated path remains AND
    recursion budgets are exhausted, the parent's own node is REALLY
    marked failed, not just recommended."""
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recfailparent-{run_id}"
        monkeypatch.setattr(settings, "procedure_run_max_child_executions", 1)
        try:
            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)
            root_id = await _start_run(pool, procedure, compiled)
            node_row_id = await pool.fetchval(
                "SELECT id FROM execution_run_nodes WHERE execution_run_id = $1 AND node_order = 0", root_id,
            )
            # This child IS the one existing run in the chain besides
            # root -- max_child_executions=1 means the chain (root + this
            # child = 2 runs) is already AT/OVER budget, so a new child
            # cannot be created either.
            child_id = await _start_run(
                pool, procedure, compiled, parent_run_id=root_id, parent_node_id=str(node_row_id),
            )
            await _fail_a_child_node(pool, child_id, exc=ValueError("non-retryable, budget exhausted"), max_attempts=1)

            decision = await decide_child_failure_strategy(
                pool, parent_run_id=root_id, parent_node_id=str(node_row_id), child_run_id=child_id,
            )
            assert decision["strategy"] == "fail_parent"

            parent_row = await pool.fetchrow(
                "SELECT status, error_class FROM execution_run_nodes WHERE id = $1", node_row_id,
            )
            assert parent_row["status"] == "failed"
            assert parent_row["error_class"] == "downstream_child_failed"

            # The real event trail records this too (B7/B8).
            from app.execution.recorder import get_run_events
            events = await get_run_events(pool, root_id)
            assert any(e["event_type"] == "node_failed" and e["node_order"] == 0 for e in events)
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())


def test_get_run_context_surfaces_the_real_child_failure_strategy():
    async def _run():
        pool = await create_pool(DATABASE_URL, min_size=1, max_size=2)
        run_id = uuid4().hex[:8]
        name = f"proc-test-recfailctx-{run_id}"
        try:
            from app.execution.durable_resume import get_run_context

            procedure = await _capture(pool, name, steps=[{"order": 0, "goal": "step"}])
            compiled = await _compiled_plan(pool, procedure, name)
            root_id = await _start_run(pool, procedure, compiled)
            node_row_id = await pool.fetchval(
                "SELECT id FROM execution_run_nodes WHERE execution_run_id = $1 AND node_order = 0", root_id,
            )
            child_id = await _start_run(
                pool, procedure, compiled, parent_run_id=root_id, parent_node_id=str(node_row_id),
            )
            await _fail_a_child_node_once(pool, child_id, error_class="transient", max_attempts=3)

            context = await get_run_context(pool, root_id)
            assert context["child_failure_strategy"] is not None
            assert context["child_failure_strategy"]["strategy"] == "retry"
            assert "child_failed:retry" in context["current_phase_or_node"]
            assert context["next_when_satisfied"] == context["child_failure_strategy"]["reason"]
        finally:
            await _cleanup(pool, name)
            await pool.close()

    asyncio.run(_run())
