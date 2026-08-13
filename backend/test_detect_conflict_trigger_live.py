import asyncio
import os
import sys

os.environ["DATABASE_URL"] = "postgresql://postgres:stealthlab@localhost:5432/stealthlab_local"
sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import create_pool
from app.mcp_server.server import detect_conflict_trigger


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
    r1 = await detect_conflict_trigger("not-a-uuid", ctx)
    print(r1)
    assert r1.startswith("REFUSED")
    print("PASS")

    print("\n=== CASE 2: nonexistent node ===")
    r2 = await detect_conflict_trigger("00000000-0000-0000-0000-000000000099", ctx)
    print(r2)
    assert r2.startswith("REFUSED")
    print("PASS")

    print("\n=== CASE 3: real node with no embedding -> no conflict (real, honest path) ===")
    node_id = await pool.fetchval(
        "INSERT INTO knowledge_nodes (node_type, name, properties) VALUES "
        "('policy', 'Solo Test Node', '{\"content\": \"unique content\"}'::jsonb) RETURNING id"
    )
    r3 = await detect_conflict_trigger(str(node_id), ctx)
    print(r3)
    assert "No conflict found" in r3
    print("PASS -- real find_conflicting_knowledge query ran against a real DB, "
          "correctly found nothing (no other embedded nodes to conflict with).")

    await pool.close()


if __name__ == "__main__":
    asyncio.run(main())
