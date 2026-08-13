"""
Real, live test of the apply_change_set MCP tool -- calls the actual
function from app.mcp_server.server, against a real (local, this-session-
only) Postgres+pgvector instance with the real schema applied, not a
reimplementation or a hand-mocked pool.

This is NOT a substitute for running it against the real Supabase graph
-- this sandbox has no network path to Supabase (confirmed: raw TCP to
the pooler host times out). This proves the tool's actual code path --
JSON parse -> auto_preserve_missing_keys -> preflight_validate ->
ChangeSet.model_validate -> KnowledgeUpdater.apply -- runs correctly
against a real transactional Postgres with the real schema, using the
exact SQL KnowledgeUpdater issues. Anuj should still run this same
change_set shape against the real Supabase DB before calling this tool
production-verified.
"""
import asyncio
import json
import os
import sys

os.environ["DATABASE_URL"] = "postgresql://postgres:stealthlab@localhost:5432/stealthlab_local"

sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import create_pool
from app.mcp_server.server import apply_change_set

# HONEST STUB: this sandbox cannot reach api.voyageai.com (confirmed via a
# real 403 "Host not in allowlist" on the first unstubbed run -- see run
# log). Real embedding correctness is NOT verified by this script. Only
# the transactional write/invalidate-and-append/auto-preserve/preflight
# logic is verified here, against a real Postgres. Anuj: rerun this same
# script (or the change_set shape below) on a machine with real network
# to both Supabase and Voyage before trusting the embedding step itself.
import app.services.knowledge_update as _ku


async def _fake_embed_one(self, text, input_type="document"):
    return [0.0] * 1024


_ku.Embedder.embed_one = _fake_embed_one


class FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    """Only exposes what apply_change_set actually touches
    (ctx.request_context.lifespan_context['pool']) -- not a reimplementation
    of MCP transport, just enough to call the real tool function directly."""
    def __init__(self, pool):
        self.request_context = FakeRequestContext(pool)


async def main():
    pool = await create_pool(os.environ["DATABASE_URL"])

    node_id = await pool.fetchval(
        "SELECT id FROM knowledge_nodes WHERE name = 'Test Merge Node' AND t_invalid IS NULL"
    )
    if node_id is None:
        print("Seed node not found -- run the seed INSERT first.")
        return 1
    print(f"Seed node id: {node_id}")

    ctx = FakeContext(pool)

    # --- Case 1: valid change set, deliberately omits 'postconditions' to
    # real-exercise auto_preserve_missing_keys, exactly the bug class that
    # was found for real earlier this project.
    change_set = {
        "ops": [
            {
                "op_type": "update_knowledge_node",
                "knowledge_node_id": str(node_id),
                "changes": {
                    "properties": {
                        "content": "Merged content after real debate resolution -- this is the new canonical text.",
                    }
                },
                "reason": "live test: real merge via apply_change_set",
            }
        ]
    }

    print("\n=== CASE 1: valid change set (postconditions omitted, should be auto-preserved) ===")
    result = await apply_change_set(json.dumps(change_set), ctx)
    print(result)

    # Verify against real DB state directly -- not trusting the tool's own report.
    new_row = await pool.fetchrow(
        "SELECT id, properties, t_invalid FROM knowledge_nodes "
        "WHERE name = 'Test Merge Node' AND t_invalid IS NULL"
    )
    old_row = await pool.fetchrow(
        "SELECT t_invalid FROM knowledge_nodes WHERE id = $1", node_id
    )
    print(f"\nOld node ({node_id}) t_invalid now: {old_row['t_invalid']}")
    print(f"New live node: {new_row['id']}")
    print(f"New node properties: {dict(new_row['properties'])}")
    assert old_row["t_invalid"] is not None, "FAIL: old node was not invalidated"
    assert new_row["id"] != node_id, "FAIL: no new row created"
    assert new_row["properties"].get("content", "").startswith("Merged content"), "FAIL: content not applied"
    assert new_row["properties"].get("postconditions") == ["status=closed"], (
        "FAIL: postconditions was NOT auto-preserved -- this is the exact real bug class"
    )
    print("CASE 1: PASS -- real invalidate-and-append occurred, postconditions auto-preserved.")

    # --- Case 2: malformed JSON should be REFUSED, not crash.
    print("\n=== CASE 2: malformed JSON ===")
    result2 = await apply_change_set("{not valid json", ctx)
    print(result2)
    assert result2.startswith("REFUSED"), "FAIL: malformed JSON did not produce a REFUSED response"
    print("CASE 2: PASS")

    # --- Case 3: proposal referencing a nonexistent node should be REFUSED
    # by preflight_validate, independent of the proposal's own claims.
    print("\n=== CASE 3: nonexistent node id ===")
    fake_change_set = {
        "ops": [
            {
                "op_type": "update_knowledge_node",
                "knowledge_node_id": "00000000-0000-0000-0000-000000000099",
                "changes": {"properties": {"content": "x" * 250}},
                "reason": "should be refused",
            }
        ]
    }
    result3 = await apply_change_set(json.dumps(fake_change_set), ctx)
    print(result3)
    assert result3.startswith("REFUSED"), "FAIL: nonexistent node did not produce a REFUSED response"
    print("CASE 3: PASS")

    # --- Case 4: the real wrong-key bug this project found for real --
    # long text under an invented key instead of 'content'.
    print("\n=== CASE 4: wrong property key (the real historical bug) ===")
    node_id2 = await pool.fetchval(
        "INSERT INTO knowledge_nodes (node_type, name, properties) VALUES "
        "('policy', 'Second Test Node', "
        "'{\"content\": \"real content\", \"postconditions\": [\"x\"]}'::jsonb) RETURNING id"
    )
    wrong_key_change_set = {
        "ops": [
            {
                "op_type": "update_knowledge_node",
                "knowledge_node_id": str(node_id2),
                "changes": {"properties": {"statement": "y" * 250}},
                "reason": "should be refused -- wrong key",
            }
        ]
    }
    result4 = await apply_change_set(json.dumps(wrong_key_change_set), ctx)
    print(result4)
    assert result4.startswith("REFUSED"), "FAIL: wrong-key proposal was not refused"
    print("CASE 4: PASS")

    await pool.close()
    print("\nAll 4 real cases passed against a real, live (local) Postgres instance.")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
