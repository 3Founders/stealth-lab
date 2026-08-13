import asyncio
import os
import sys

os.environ["DATABASE_URL"] = "postgresql://postgres:stealthlab@localhost:5432/stealthlab_local"
sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import create_pool
from app.debate.state_machine import DebateStateMachine
import app.mcp_server.server as srv


class FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool):
        self.request_context = FakeRequestContext(pool)


class _FakeOrchestratorThatGetsCancelled:
    """Simulates the real scenario: run() successfully opens the debate
    (real OPEN -> IN_DEBATE transition happens), then the client
    disconnects mid-flight, injecting a real CancelledError -- exactly
    what happened when the Inspector's request timed out."""

    def __init__(self, pool, panel, judge):
        self._pool = pool
        self._machine = DebateStateMachine(pool)

    async def run(self, trigger_id):
        row = await self._pool.fetchrow(
            "INSERT INTO debates (trigger_id) VALUES ($1) RETURNING id", trigger_id
        )
        debate_id = row["id"]
        await self._pool.execute(
            "UPDATE triggers SET debate_id = $2 WHERE id = $1", trigger_id, debate_id
        )
        await self._pool.execute(
            "INSERT INTO debate_events (debate_id, from_state, to_state, reason) "
            "VALUES ($1, NULL, 'OPEN', 'trigger fired')", debate_id,
        )
        await self._machine.transition(debate_id, "IN_DEBATE", reason="debate started")
        raise asyncio.CancelledError()  # the real disconnect, injected


async def main():
    pool = await create_pool(os.environ["DATABASE_URL"])

    task_node_id = await pool.fetchval(
        "INSERT INTO task_nodes (name, description) VALUES "
        "('Cleanup Fix Test Task', 'testing') RETURNING id"
    )
    trigger_id = await pool.fetchval(
        "INSERT INTO triggers (task_node_id, rule_name, metric_name, observed_value, "
        "threshold, sample_size) VALUES ($1, 'test_rule', 'error_rate', 0.3, 0.1, 10) "
        "RETURNING id", task_node_id,
    )
    print(f"trigger: {trigger_id}")

    srv.LoopOrchestrator = _FakeOrchestratorThatGetsCancelled
    ctx = FakeContext(pool)

    print("\n=== Calling propose_synthesis, simulating a client-cancel mid-debate ===")
    try:
        await srv.propose_synthesis(str(trigger_id), ctx)
        raise AssertionError("FAIL: expected CancelledError to propagate")
    except asyncio.CancelledError:
        print("CancelledError correctly propagated (not swallowed).")

    debate_id = await pool.fetchval("SELECT debate_id FROM triggers WHERE id = $1", trigger_id)
    machine = DebateStateMachine(pool)
    state = await machine.current_state(debate_id)
    print(f"debate state after cancellation: {state}")
    assert state == "REJECTED", f"FAIL: expected REJECTED, got {state}"
    print("PASS: orphaned debate was cleanly closed to REJECTED, not left stuck at IN_DEBATE.")

    events = await pool.fetch(
        "SELECT from_state, to_state, reason FROM debate_events WHERE debate_id = $1 ORDER BY occurred_at",
        debate_id,
    )
    print("\nReal debate_events audit trail:")
    for e in events:
        print(f"  {e['from_state']} -> {e['to_state']}  ({e['reason']})")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
