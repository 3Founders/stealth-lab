import asyncio
import os
import sys

os.environ["DATABASE_URL"] = "postgresql://postgres:stealthlab@localhost:5432/stealthlab_local"
sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import create_pool
from app.mcp_server.server import propose_synthesis


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
    r1 = await propose_synthesis("not-a-uuid", ctx)
    print(r1)
    assert r1.startswith("REFUSED"), "FAIL"
    print("CASE 1: PASS")

    print("\n=== CASE 2: well-formed but nonexistent trigger id ===")
    r2 = await propose_synthesis("00000000-0000-0000-0000-000000000099", ctx)
    print(r2)
    assert r2.startswith("REFUSED"), "FAIL"
    assert "no trigger" in r2
    print("CASE 2: PASS -- real LookupError from LoopOrchestrator.run surfaced cleanly.")

    await pool.close()
    print("\nBoth real refusal paths verified against a live Postgres. Full success path")
    print("needs a real task_node + trigger row + live LLM panel -- not runnable in this")
    print("network-restricted sandbox; needs to be run on real infra.")


if __name__ == "__main__":
    asyncio.run(main())
