import asyncio
import os
import sys

os.environ["DATABASE_URL"] = "postgresql://postgres:stealthlab@localhost:5432/stealthlab_local"
sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import create_pool
from app.mcp_server.server import submit_approval

# HONEST STUB, same real wall as apply_change_set's own test: this sandbox
# cannot reach api.voyageai.com. Only the approval/audit/state-transition
# logic is verified here -- not the real embedding step.
import app.services.knowledge_update as _ku


async def _fake_embed_one(self, text, input_type="document"):
    return [0.0] * 1024


_ku.Embedder.embed_one = _fake_embed_one


class FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool):
        self.request_context = FakeRequestContext(pool)


async def main():
    pool = await create_pool(os.environ["DATABASE_URL"])
    ctx = FakeContext(pool)

    print("=== CASE 1: malformed UUID ===")
    r1 = await submit_approval("not-a-uuid", "test_approver", "approved", ctx)
    print(r1)
    assert r1.startswith("REFUSED")
    print("PASS")

    print("\n=== CASE 2: invalid decision string ===")
    r2 = await submit_approval("00000000-0000-0000-0000-000000000001", "test_approver", "maybe", ctx)
    print(r2)
    assert r2.startswith("REFUSED")
    print("PASS")

    print("\n=== CASE 3: nonexistent scorecard ===")
    r3 = await submit_approval("00000000-0000-0000-0000-000000000099", "test_approver", "approved", ctx)
    print(r3)
    assert r3.startswith("REFUSED")
    print("PASS -- real 404 from decide() surfaced cleanly, not a crash.")

    print("\n=== CASE 4: real approve flow against a real scorecard ===")
    # Build a real, minimal debate -> candidate -> scorecard chain by hand,
    # matching exactly what LoopOrchestrator._evaluate() would have
    # persisted for real -- so submit_approval exercises the real decide()
    # function against real rows, not a mock.
    task_node_id = await pool.fetchval(
        "INSERT INTO task_nodes (name, description) VALUES "
        "('Approval Test Task', 'testing submit_approval') RETURNING id"
    )
    trigger_id = await pool.fetchval(
        "INSERT INTO triggers (task_node_id, rule_name, metric_name, observed_value, "
        "threshold, sample_size) VALUES ($1, 'test_rule', 'error_rate', 0.3, 0.1, 10) "
        "RETURNING id", task_node_id,
    )
    debate_id = await pool.fetchval(
        "INSERT INTO debates (trigger_id) VALUES ($1) RETURNING id", trigger_id
    )
    await pool.execute("UPDATE triggers SET debate_id = $2 WHERE id = $1", trigger_id, debate_id)
    from app.debate.state_machine import DebateStateMachine
    machine = DebateStateMachine(pool)
    await pool.execute(
        "INSERT INTO debate_events (debate_id, from_state, to_state, reason) "
        "VALUES ($1, NULL, 'OPEN', 'test setup')", debate_id,
    )
    await machine.transition(debate_id, "IN_DEBATE", reason="test setup")
    await machine.transition(debate_id, "PENDING_EVAL", reason="test setup")
    await machine.transition(debate_id, "PENDING_APPROVAL", reason="test setup")

    # Real target node for the change_set to actually touch.
    node_id = await pool.fetchval(
        "INSERT INTO knowledge_nodes (node_type, name, properties) VALUES "
        "('policy', 'Approval Target Node', "
        "'{\"content\": \"before approval\"}'::jsonb) RETURNING id"
    )
    real_change_set = {
        "ops": [{
            "op_type": "update_knowledge_node",
            "knowledge_node_id": str(node_id),
            "changes": {"properties": {"content": "after real approval"}},
            "reason": "test",
        }]
    }
    candidate_id = await pool.fetchval(
        "INSERT INTO candidates (debate_id, summary, rationale, change_set, supporters) "
        "VALUES ($1, $2, $3, $4, $5) RETURNING id",
        debate_id, "test candidate", "test rationale for approval flow", real_change_set, ["panelist_a"],
    )
    scorecard_id = await pool.fetchval(
        "INSERT INTO scorecards (debate_id, candidate_id, layer1_passed, constructive, "
        "groundedness_score, blast_radius, reversible, recommendation) "
        "VALUES ($1, $2, true, true, 1.0, 0, true, 'apply') RETURNING id",
        debate_id, candidate_id,
    )
    print(f"real scorecard: {scorecard_id}")

    r4 = await submit_approval(str(scorecard_id), "real_test_approver", "approved", ctx, note="test approval")
    print(r4)
    assert "approval_id" in r4

    # Verify against the SPECIFIC new node id from applied_ops -- not by
    # name, which earlier failed test runs polluted with duplicates.
    import re
    new_id_match = re.search(r"'new_id': '([\w-]+)'", r4)
    assert new_id_match, "FAIL: could not find new_id in applied_ops output"
    new_node_id = new_id_match.group(1)
    new_row = await pool.fetchrow(
        "SELECT properties, t_invalid FROM knowledge_nodes WHERE id = $1", new_node_id
    )
    print("real new node content after approval:", dict(new_row))
    assert new_row["properties"]["content"] == "after real approval", "FAIL: change was not actually applied"
    assert new_row["t_invalid"] is None, "FAIL: new node should be live"

    old_row = await pool.fetchrow(
        "SELECT t_invalid FROM knowledge_nodes WHERE id = $1", node_id
    )
    assert old_row["t_invalid"] is not None, "FAIL: old node should be invalidated"

    state = await machine.current_state(debate_id)
    print("real debate state after approval:", state)
    assert state == "APPROVED", f"FAIL: expected APPROVED, got {state}"

    approval_row = await pool.fetchrow(
        "SELECT approver_id, decision, note FROM approvals WHERE scorecard_id = $1", scorecard_id
    )
    print("real approvals audit row:", dict(approval_row))
    assert approval_row["decision"] == "approved"

    print("\nPASS: real apply + real audit row + real APPROVED transition, all atomic, all confirmed in DB.")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
