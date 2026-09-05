"""
Transport-risk gate: 2 real, non-scored, BOUNDED retrieval attempts over the
real MCP path used by B_default trials.

Reuses verify_b_default_embedding_fix.py's own real functions (same
session/store/probe setup, same _retrieval_only helper) unchanged -- the
only difference here is that each real call is wrapped in
`asyncio.wait_for(..., timeout=90)`, mirroring the SAME bound
orchestrator.py's run_one_trial already applies to a real scored trial.
Point: if the transport stalls again the way it did in the earlier
non-scored check (333.9s, MCPError: SSE stream ended without a response),
THIS script proves that bound actually cuts it off at ~90s rather than
sitting for 300+s again -- the exact "cannot hang indefinitely" property
this gate needs to characterize, using the real flaky network rather than
a simulation.

2 attempts only (relevant control, then the negative control that hit the
real issue last time), each independently bounded. No agent execution, no
GENERAL_COMPUTE tokens, nothing scored.

Run: python verify_transport_bounded.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

from verify_b_default_embedding_fix import (  # noqa: E402
    PORT, REPO_ROOT, _load_env, _make_worktree, _remove_worktree, _retrieval_only,
    _start_mcp_server,
)

CALL_TIMEOUT_S = 90.0


async def main() -> int:
    env = _load_env()
    from app.local_agent.runner import _open_client_session
    from app.local_agent.local_store import LocalProcedureStore
    from app.services.environment_facts import (
        invariant_bindings_from_facts, probe_environment, probe_python_version,
    )

    token = os.environ.get("STEALTHLAB_MCP_TOKEN")
    proc = _start_mcp_server(REPO_ROOT, PORT, env)
    wt_path = _make_worktree()
    results = []
    try:
        await asyncio.sleep(15.0)
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            print("ABORT: MCP server exited early:\n" + out[-3000:])
            return 1
        server_url = f"http://127.0.0.1:{PORT}/mcp"

        async with _open_client_session(server_url, token) as session:
            await session.initialize()

            local_facts = await asyncio.to_thread(probe_environment, str(wt_path))
            local_facts = local_facts + [probe_python_version()]
            invariant_bindings = invariant_bindings_from_facts(local_facts)
            store = LocalProcedureStore(str(wt_path))

            attempts = [
                ("relevant_control",
                 "Several coding agents need to work on the same git repository at the "
                 "same time without their file edits colliding with each other."),
                ("negative_control",
                 "Calculate the optimal seasonal irrigation schedule for a commercial "
                 "almond orchard in a Mediterranean climate."),
            ]
            for i, (label, task_description) in enumerate(attempts):
                if i > 0:
                    print(f"=== waiting 25s before attempt {i+1}/2 ===", flush=True)
                    await asyncio.sleep(25.0)
                print(f"=== [{i+1}/2] {label} (bounded to {CALL_TIMEOUT_S:.0f}s) ===", flush=True)
                t0 = time.time()
                try:
                    r = await asyncio.wait_for(
                        _retrieval_only(
                            session, store, label=label, task_description=task_description,
                            allow_unverified=True, local_facts=local_facts,
                            invariant_bindings=invariant_bindings,
                        ),
                        timeout=CALL_TIMEOUT_S,
                    )
                    r["bounded_timeout_fired"] = False
                except asyncio.TimeoutError:
                    r = {"label": label, "elapsed_s": round(time.time() - t0, 2),
                         "error": f"BOUNDED_TIMEOUT after {CALL_TIMEOUT_S:.0f}s (script-level, "
                                  "same mechanism orchestrator.run_one_trial uses)",
                         "matched": False, "ranked": [], "bounded_timeout_fired": True}
                results.append(r)
                print(json.dumps(r, indent=2), flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()
        _remove_worktree(wt_path)

    print("\n=== SUMMARY ===")
    never_hung = all(r["elapsed_s"] < CALL_TIMEOUT_S + 10 for r in results)
    print("no_attempt_exceeded_the_bound", never_hung)
    for r in results:
        print(f"  {r['label']}: elapsed={r['elapsed_s']}s error={r['error']!r} "
              f"bounded_timeout_fired={r['bounded_timeout_fired']}")

    (HERE / "transport_bounded_check_result.json").write_text(
        json.dumps({"call_timeout_s": CALL_TIMEOUT_S, "no_attempt_exceeded_the_bound": never_hung,
                    "results": results}, indent=2),
        encoding="utf-8",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
