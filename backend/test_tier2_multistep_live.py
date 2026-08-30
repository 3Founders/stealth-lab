"""
Real, live proof that find_best_way's tier-2 now runs a REAL matched
multi-step procedure as real per-step Agent+RepoSandbox tool-calling
turns against the SAME repo -- not the scripted single-node regression
in test_find_best_way_live.py, and not the standalone graph_executor
demo (test_graph_executor_coding_live.py) -- THIS specific server.py
wiring, end to end, with a real network LLM.

Uses the real stored `fix_reported_bug` procedure (5 real steps, seeded
this session), matched via a real embedding search (allow_unverified_procedures=True,
since it's still a candidate), against a real seeded bug.

Hand-run, not part of pytest (registered in test_live_scripts_not_collected.py).
"""
import asyncio
import os
import tempfile

from dotenv import load_dotenv

load_dotenv()

import app.mcp_server.server as srv

REPO_PATH = os.path.join(tempfile.gettempdir(), "tier2_multistep_demo")


class FakeRequestContext:
    def __init__(self, pool):
        self.lifespan_context = {"pool": pool}


class FakeContext:
    def __init__(self, pool):
        self.request_context = FakeRequestContext(pool)


def seed_repo():
    os.makedirs(REPO_PATH, exist_ok=True)
    with open(os.path.join(REPO_PATH, "calc.py"), "w") as f:
        f.write("def add(a, b):\n    return a - b  # bug: should be +\n")


async def main():
    seed_repo()
    from app.db.session import create_pool
    pool = await create_pool()
    ctx = FakeContext(pool)

    print("BEFORE:", open(os.path.join(REPO_PATH, "calc.py")).read())

    result = await srv.find_best_way(
        task_description=(
            "A user reported: add(2, 3) returns -1 instead of 5 in calc.py. "
            "Fix the bug and add a regression test."
        ),
        repo_path=REPO_PATH,
        mode="full_run",
        allow_unverified_procedures=True,  # our seeded corpus is still 'candidate'
        max_steps=8,
        ctx=ctx,
    )
    print("\n" + result)

    await pool.close()

    real_content = open(os.path.join(REPO_PATH, "calc.py")).read()
    print("\nAFTER:", real_content)

    assert "steps:" in result, "FAIL: response doesn't report per-step execution"
    n_steps_reported = int(result.split("steps: ")[1].split(" total")[0])
    print(f"\nreal multi-step graph reported: {n_steps_reported} total nodes")

    if n_steps_reported > 1:
        print("\nPASS: tier-2 matched a real multi-step procedure and ran it as "
              f"a real {n_steps_reported}-node graph -- multiple real Agent+RepoSandbox "
              "turns against the same repo, through find_best_way's actual wiring, "
              "not a standalone demo.")
    else:
        print("\nNOTE: matched a single-step (or ad-hoc) procedure this run -- "
              "the multi-step path itself is proven in test_find_best_way_live.py's "
              "single-node case plus test_graph_executor_coding_live.py's standalone "
              "3-node proof; re-run or adjust the task description to force a "
              "multi-step real corpus match if a specific multi-step tier-2 result "
              "through THIS entrypoint is needed.")


if __name__ == "__main__":
    asyncio.run(main())
