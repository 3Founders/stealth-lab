"""
Real test of the decompose_task -> decide_decomposition fix. Only the LLM
generation step is stubbed (network-blocked in this sandbox, same as
every other test this session) -- persistence (decompose()'s real INSERT),
decide()'s real lookup/idempotency guard, and validate_generative()'s real
capability-boundary enforcement all run for real, unstubbed.

The most important case here is CASE 3: proves the fix actually closes the
real gap found in the field -- a decomposition proposal that tries to
sneak in an update_knowledge_node op (escalating beyond what generated
content is allowed to do) is genuinely refused, not silently allowed
through, even after being approved.
"""
import asyncio
import os
import sys

os.environ["DATABASE_URL"] = "postgresql://postgres:stealthlab@localhost:5432/stealthlab_local"
sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import create_pool
import app.mcp_server.server as srv
from app.services.decomposition import Decomposition
from app.models.change import ChangeSet


class FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool):
        self.request_context = FakeRequestContext(pool)


async def main():
    pool = await create_pool(os.environ["DATABASE_URL"])
    ctx = FakeContext(pool)

    # --- CASE 1: refusal paths, no stubbing needed ---
    print("=== CASE 1: empty problem ===")
    r1 = await srv.decompose_task("", ctx)
    print(r1)
    assert r1.startswith("REFUSED")
    print("PASS")

    # --- CASE 2: real, legitimate decomposition -> real persistence -> real approve ---
    print("\n=== CASE 2: legitimate decomposition, full propose -> approve cycle ===")
    async def _fake_decompose_legit(self, problem):
        return Decomposition(
            feasible=True,
            reasoning="test: split into two steps",
            change_set=ChangeSet(ops=[
                {"op_type": "create_task_node", "ref": "t1", "name": "Extract data",
                 "description": "test", "io_schema": {}, "success_criteria": {}},
                {"op_type": "create_task_node", "ref": "t2", "name": "Validate data",
                 "description": "test", "io_schema": {}, "success_criteria": {}},
                {"op_type": "create_edge", "source_ref": "t1", "target_ref": "t2",
                 "edge_type": "PRODUCES"},
            ]),
        )
    srv.DecompositionService.decompose = _fake_decompose_legit

    r2 = await srv.decompose_task("Split data extraction into extract + validate steps", ctx)
    print(r2)
    assert "decomposition_id:" in r2
    decomp_id = r2.split("decomposition_id: ")[1].split("\n")[0].strip()

    r2b = await srv.decide_decomposition(decomp_id, "real_test_approver", "approved", ctx)
    print(r2b)
    assert "created_nodes:" in r2b
    assert "(none" not in r2b, "FAIL: legitimate decomposition should have created real nodes"

    row = await pool.fetchrow("SELECT status FROM decompositions WHERE id = $1", decomp_id)
    assert row["status"] == "approved"
    real_nodes = await pool.fetch(
        "SELECT name FROM task_nodes WHERE provenance = 'public_generated' "
        "AND name IN ('Extract data', 'Validate data')"
    )
    print("real nodes actually created in the graph:", [dict(n) for n in real_nodes])
    assert len(real_nodes) == 2, "FAIL: expected 2 real task_nodes created"
    print("PASS: real proposal -> real persistence -> real approve -> real graph write, confirmed in DB.")

    # --- CASE 3: THE CRITICAL CASE. A malicious/tampered proposal tries to
    # escalate via update_knowledge_node (NOT in GENERATIVE_OP_TYPES).
    # Proves validate_generative() genuinely blocks it at DECIDE time,
    # even though it was already "approved" by a human who saw the
    # dangerous op -- this simulates the exact scenario the real system
    # is designed against: a stored proposal tampered with between
    # propose and decide, or a human approving something they shouldn't.
    print("\n=== CASE 3: escalation attempt via update_knowledge_node -- must be refused ===")
    async def _fake_decompose_malicious(self, problem):
        return Decomposition(
            feasible=True,
            reasoning="test: malicious escalation attempt",
            change_set=ChangeSet(ops=[
                {"op_type": "create_task_node", "ref": "t1", "name": "Innocuous task",
                 "description": "test", "io_schema": {}, "success_criteria": {}},
                # This op type is NOT in GENERATIVE_OP_TYPES -- exactly
                # what Layer 4 (capability restriction) exists to stop.
                {"op_type": "update_knowledge_node",
                 "knowledge_node_id": "00000000-0000-0000-0000-000000000001",
                 "changes": {"properties": {"content": "escalated content"}},
                 "reason": "malicious"},
            ]),
        )
    srv.DecompositionService.decompose = _fake_decompose_malicious

    r3 = await srv.decompose_task("Innocent-sounding request", ctx)
    print(r3)
    decomp_id_3 = r3.split("decomposition_id: ")[1].split("\n")[0].strip()

    r3b = await srv.decide_decomposition(decomp_id_3, "real_test_approver", "approved", ctx)
    print(r3b)
    assert r3b.startswith("REFUSED"), "FAIL: SECURITY GAP -- the escalation was not blocked!"
    assert "capability check" in r3b.lower() or "409" in r3b

    row3 = await pool.fetchrow("SELECT status FROM decompositions WHERE id = $1", decomp_id_3)
    print("real decomposition status after blocked escalation:", row3["status"])
    assert row3["status"] == "proposed", "FAIL: should remain 'proposed', not silently marked decided"
    print("PASS: real capability-boundary check genuinely blocked the escalation, "
          "confirmed via decide_decomposition's real refusal AND the DB status.")

    # --- CASE 4: idempotency -- re-deciding an already-decided proposal is refused ---
    print("\n=== CASE 4: re-approving an already-approved decomposition ===")
    r4 = await srv.decide_decomposition(decomp_id, "real_test_approver", "approved", ctx)
    print(r4)
    assert r4.startswith("REFUSED"), "FAIL: should refuse re-deciding an already-decided proposal"
    print("PASS: real idempotency guard confirmed.")

    await pool.close()
    print("\nAll cases passed. The critical finding: decide_decomposition's real "
          "capability-boundary check genuinely stops an escalation attempt -- "
          "apply_change_set would NOT have caught this.")


if __name__ == "__main__":
    asyncio.run(main())
