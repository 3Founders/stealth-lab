"""
Non-scored retrieval verification (readiness gate section 7/10.D-F).
Starts the REAL MCP server, connects a REAL client, calls the REAL
search_procedures tool (require_verified=True, the production default --
the same tool the B_default arm uses) with genuinely novel queries not
reused from any pilot/calibration task or fixture wording. Records full
detail per query. Includes one negative control.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from orchestrator import _start_mcp_server  # noqa: E402
from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
import httpx2  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]
OUT_PATH = Path(__file__).resolve().parent / "retrieval_verification_results.jsonl"

QUERIES = [
    # Genuinely novel, not the fixture's own "explore repo" wording, not
    # any pilot/calibration task text. Targets the "structural-summary"
    # admitted procedure's real domain (reading an unfamiliar file).
    # require_verified=False -- the 3 admitted procedures are all
    # verification_state='candidate', not 'verified'; this is the real
    # opt-in toggle the B_unverified arm uses (LocalAgentRunner's own
    # allow_unverified param), the correct way to test the hard gate.
    {"id": "Q1_structural_summary_unverified", "task": "I need to understand what a large unfamiliar Python source file contains before editing it, without reading the whole thing.", "expect": "should plausibly match the admitted structural-summary procedure", "require_verified": False},
    {"id": "Q2_worktree_isolation_unverified", "task": "Several coding agents need to work on the same git repository at the same time without their file edits colliding with each other.", "expect": "should plausibly match the admitted git-worktree isolation procedure", "require_verified": False},
    {"id": "Q3_deferred_tools_unverified", "task": "My agent's system prompt is too large because every available tool's full schema is listed up front even for tools that are rarely used.", "expect": "should plausibly match the admitted deferred-tool-loading procedure", "require_verified": False},
    # Same Q1 query, but require_verified=True (the production DEFAULT) --
    # documents the expected, correct contrast: candidate-status admitted
    # knowledge is invisible under the strict default, visible only via
    # explicit opt-in. Not a bug; a fact worth recording plainly.
    {"id": "Q1b_structural_summary_default_strict", "task": "I need to understand what a large unfamiliar Python source file contains before editing it, without reading the whole thing.", "expect": "should return no result under the strict default (candidate, not verified)", "require_verified": True},
    # Negative control: a real, coherent, but genuinely unrelated domain --
    # nothing in the corpus should apply, under either setting.
    {"id": "Q4_negative_control", "task": "Calculate the optimal seasonal irrigation schedule for a commercial almond orchard in a Mediterranean climate.", "expect": "should return no plausible match (abstain)", "require_verified": False},
]


async def main():
    env_path = REPO_ROOT / "backend" / ".env"
    env = os.environ.copy()
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    env["STEALTHLAB_MCP_TOKEN"] = "bDBkrdBsLe7YJCu8Gh-dxUuL9DK_OF6bD2K7RhDu_eA"

    port = 8795
    proc = _start_mcp_server(REPO_ROOT, port, env)
    try:
        await asyncio.sleep(12.0)
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"MCP server exited early (code {proc.returncode}):\n{out}")
        server_url = f"http://127.0.0.1:{port}/mcp"
        http_client = httpx2.AsyncClient(
            headers={"Authorization": f"Bearer {env['STEALTHLAB_MCP_TOKEN']}"}, timeout=300,
        )
        results = []
        with open(OUT_PATH, "w", encoding="utf-8") as outf:
            async with streamable_http_client(server_url, http_client=http_client) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=300) as session:
                    await session.initialize()
                    for i, q in enumerate(QUERIES):
                        if i > 0:
                            # Voyage's real 3 RPM sandbox limit -- pace calls
                            # so this script's own embedding calls don't
                            # queue/timeout the way the first attempt did.
                            await asyncio.sleep(22.0)
                        t0 = time.time()
                        rv = q["require_verified"]
                        resp = await session.call_tool(
                            "search_procedures",
                            {"task": q["task"], "require_verified": rv, "limit": 5},
                        )
                        elapsed = time.time() - t0
                        text = resp.content[0].text if resp.content else "[]"
                        try:
                            candidates = json.loads(text)
                        except Exception:
                            candidates = {"raw_text": text}
                        row = {
                            "query_id": q["id"], "query": q["task"], "expect": q["expect"],
                            "elapsed_s": elapsed, "require_verified": rv,
                            "returned_candidates": candidates,
                        }
                        results.append(row)
                        outf.write(json.dumps(row) + "\n")
                        outf.flush()
                        print(f"[{q['id']}] require_verified={rv} -> "
                              f"{len(candidates) if isinstance(candidates, list) else 'ERR'} "
                              f"candidates in {elapsed:.2f}s", flush=True)
        print(f"wrote {OUT_PATH}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
