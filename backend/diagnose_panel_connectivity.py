"""
Bypasses MCP/Inspector entirely -- calls each real panel agent + judge
directly with a trivial prompt, with an explicit timeout, so a silent hang
or bad key shows up in seconds instead of you waiting on a debate that may
never come back.

default_panel() (used by propose_synthesis) requires THREE separate real
provider keys simultaneously by default: Anthropic, Fireworks, OpenAI. If
any ONE of those is missing/invalid/unreachable, the whole debate can hang
or fail -- and since nothing in the debate code currently logs progress to
the console, that failure is invisible until something times out.

Run this directly:
    python3 diagnose_panel_connectivity.py
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))

from app.debate.panel import default_panel, default_judge


async def _try_one(label: str, agent, timeout_s: float = 20.0):
    t0 = time.time()
    try:
        result = await asyncio.wait_for(
            agent.respond(
                system="You are a test agent. Reply with exactly one word.",
                user="Reply with the single word: OK",
            ),
            timeout=timeout_s,
        )
        elapsed = time.time() - t0
        print(f"[{label}] OK in {elapsed:.1f}s -- response: {result[:80]!r}")
        return True
    except asyncio.TimeoutError:
        elapsed = time.time() - t0
        print(f"[{label}] HUNG -- no response after {elapsed:.1f}s (timeout={timeout_s}s). "
              f"This is very likely your real problem: a missing/invalid key or an "
              f"unreachable provider, with no built-in timeout to fail fast.")
        return False
    except Exception as exc:
        elapsed = time.time() - t0
        print(f"[{label}] FAILED after {elapsed:.1f}s -- real error: {type(exc).__name__}: {exc}")
        return False


async def main():
    print("Testing each real panel agent + judge directly, bypassing MCP entirely.\n")

    panel = default_panel()
    judge = default_judge()

    results = {}
    for agent in panel:
        agent_id = getattr(agent, "agent_id", agent.__class__.__name__)
        results[agent_id] = await _try_one(agent_id, agent)

    results["judge"] = await _try_one("judge", judge)

    print("\n=== Summary ===")
    for label, ok in results.items():
        print(f"  {label}: {'OK' if ok else 'BROKEN -- fix this before testing propose_synthesis'}")

    if not all(results.values()):
        print(
            "\nAt least one real provider is broken. propose_synthesis will hang or "
            "fail until every one of these passes (default_panel() needs all of "
            "them). If you don't want to fix all three providers right now, set "
            "USE_GENERAL_COMPUTE=true in .env instead -- that switches to a single, "
            "cheaper provider path (general_compute_panel()) requiring only "
            "GENERAL_COMPUTE_API_KEY, much easier to get working for a first test."
        )
    else:
        print("\nAll real providers responded -- the hang is NOT provider connectivity. "
              "The debate itself is likely just genuinely slow (multiple real rounds "
              "across 3 models + a judge can legitimately take a couple minutes) or "
              "there's an MCP-layer issue, not an LLM-layer one.")


if __name__ == "__main__":
    asyncio.run(main())
