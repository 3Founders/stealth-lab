"""
Sandboxed execution (stage 6), verified against real behavior, and its
wiring into decide_agent's runnable computation.

Read app/services/sandbox.py's own docstring for exactly what is and
isn't verified here -- network isolation, resource limits, and a real
(denylist, not full-allowlist) filesystem restriction are all verified
against actual behavior; non-root production behavior is not confirmed
(this environment runs as root).

Run:
    DATABASE_URL=postgresql://... python integration_check_v2_sandbox.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from app.db.session import create_pool
from app.services.agent_decision import decide_agent
from app.services.execution import default_registry
from app.services.sandbox import run_sandboxed

CLEAN_CODE = "def run(input_data):\n    return {'doubled': input_data.get('x', 0) * 2}"
CRASHING_CODE = "def run(input_data):\n    raise ValueError('boom')"
NETWORK_ATTEMPT_CODE = """
def run(input_data):
    import urllib.request
    try:
        urllib.request.urlopen("https://pypi.org", timeout=3)
        return {"leaked": True}
    except Exception:
        return {"leaked": False}
"""
INFINITE_LOOP_CODE = "def run(input_data):\n    x = 0\n    while True:\n        x += 1"
OVER_ALLOCATE_CODE = "def run(input_data):\n    x = bytearray(500*1024*1024)\n    return {'allocated': True}"
ENV_LEAK_CODE = """
def run(input_data):
    import os
    return {"secret_present": "SANDBOX_TEST_SECRET" in os.environ}
"""


async def main():
    os.environ["SANDBOX_TEST_SECRET"] = "should-never-leak-into-the-sandbox"
    failures = []

    def check(name, condition, detail=""):
        status = "PASS" if condition else "FAIL"
        print(f"  [{status}] {name}" + (f" -- {detail}" if detail and not condition else ""))
        if not condition:
            failures.append(name)

    print("-- module-level: every specific claim, tested against real behavior --")

    r1 = run_sandboxed(CLEAN_CODE, {"x": 21})
    check("real code executes and returns real output", r1.exit_code == 0 and "42" in r1.stdout)

    r2 = run_sandboxed(NETWORK_ATTEMPT_CODE)
    check("network genuinely blocked (real attempt, real block)", "leaked\": false" in r2.stdout.lower())

    t0 = time.monotonic()
    r3 = run_sandboxed(INFINITE_LOOP_CODE, cpu_seconds=1, wall_clock_seconds=6)
    check("CPU limit kills a genuine infinite loop, not just slows it",
          time.monotonic() - t0 < 5)

    r4 = run_sandboxed(OVER_ALLOCATE_CODE, memory_bytes=64 * 1024 * 1024)
    check("memory limit genuinely enforced, over-allocation fails", "allocated" not in r4.stdout)

    r5 = run_sandboxed(ENV_LEAK_CODE)
    check("a real secret in the parent process does not leak in",
          "secret_present\": false" in r5.stdout.lower())

    r6 = run_sandboxed("def run(input_data):\n    import time\n    time.sleep(20)\n    return {}",
                        wall_clock_seconds=2)
    check("wall-clock timeout correctly flagged", r6.timed_out is True)

    r7 = run_sandboxed(
        'def run(input_data):\n'
        '    try:\n'
        '        with open("/etc/passwd") as f: f.read()\n'
        '        return {"read_passwd": True}\n'
        '    except Exception:\n'
        '        return {"read_passwd": False}\n'
    )
    check("real /etc/passwd is genuinely unreachable from inside the sandbox",
          '"read_passwd": false' in r7.stdout.lower())

    print()
    print("-- wired into decide_agent: the actual gate, not just the module in isolation --")

    pool = await create_pool(os.environ["DATABASE_URL"])
    registry = default_registry()

    async def make_pending(code=None, source="external_marketplace"):
        detail = {"code": code} if code else {}
        row = await pool.fetchrow(
            "INSERT INTO agents (name, description, source, execution_mode, skill_ref, "
            "review_state, source_detail) VALUES ('t','d',$1,'local_skill','x',"
            "'pending_human_approval',$2) RETURNING id",
            source, detail,
        )
        return row["id"]

    a1 = await make_pending(CLEAN_CODE)
    d1 = await decide_agent(pool, a1, "approved", registry, acknowledge_sandbox_limitations=False)
    check("no acknowledgment -- runnable stays False even with clean code", d1["runnable"] is False)

    a2 = await make_pending(CLEAN_CODE)
    d2 = await decide_agent(pool, a2, "approved", registry, acknowledge_sandbox_limitations=True)
    check("acknowledged, clean code -- runnable=True", d2["runnable"] is True)

    a3 = await make_pending(CRASHING_CODE)
    d3 = await decide_agent(pool, a3, "approved", registry, acknowledge_sandbox_limitations=True)
    check("acknowledged, but code genuinely crashes in the sandbox -- runnable stays False",
          d3["runnable"] is False)

    a4 = await make_pending(code=None, source="user_submitted")
    d4 = await decide_agent(pool, a4, "approved", registry, acknowledge_sandbox_limitations=True)
    check("user_submitted with no code -- nothing to test, runnable stays False", d4["runnable"] is False)

    await pool.close()
    print(f"\n{'=' * 55}")
    if failures:
        print(f"{len(failures)} FAILED: {failures}")
        sys.exit(1)
    print("SANDBOX VERIFIED, module and wiring both, against real behavior.")


if __name__ == "__main__":
    asyncio.run(main())
