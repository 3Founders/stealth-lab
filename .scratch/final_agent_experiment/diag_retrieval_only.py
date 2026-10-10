"""
Retrieval-only diagnostic (no Agent/model execution). Connects to the
ALREADY-RUNNING MCP server on 127.0.0.1:8811 (the same server the real
B_default scored trials used) and calls the real `search_procedures` tool
with the exact T1-v3 / T7-v2 task statements, under BOTH require_verified
settings:
  - True  (the real B_default production default) -- expected to reproduce
    matched=false and show WHY (empty result / hard gate).
  - False (diagnostic only, same call the calibration script already used)
    -- to see the actual ranked candidates + similarity scores that exist
    behind the gate, without changing any code or writing answer.md.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "backend"))

from mcp import ClientSession  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
import httpx  # noqa: E402

REPO_ROOT = Path(__file__).resolve().parents[2]

TASKS = {
    "T1-v3": (
        "In this repository at commit bd768e62a887b13a94fdd118693a5c671df1cf95, "
        "examine backend/app/mcp_server/server.py and answer ONE question: which "
        "single function is the SHARED resolver that get_procedure, "
        "check_procedure, check_applicability, report_execution, and "
        "decide_procedure all use to accept either a procedures.procedure_id "
        "family handle or a procedures.id row key? Write only the function name "
        "(with a one-sentence justification) to answer.md. Do not attempt to "
        "enumerate every @server.tool()-decorated function in the file -- that "
        "is not part of this task and is not checked."
    ),
    "T7-v2": (
        "Across every top-level .py file directly under backend/app/services/ "
        "(NOT subdirectories), find the single function or method (including "
        "methods defined inside a class) with the largest number of physical "
        "lines in its body, counted from its `def` line through its last body "
        "line inclusive. Report in answer.md: the function/method name, its "
        "containing file (relative path), and its exact line count. Use as few "
        "full-file reads as you reasonably can; prefer targeted search/outline "
        "tools over reading every file in full."
    ),
}


async def main():
    env_path = REPO_ROOT / "backend" / ".env"
    env = os.environ.copy()
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    token = env["STEALTHLAB_MCP_TOKEN"]
    server_url = "http://127.0.0.1:8811/mcp"

    http_client = httpx.AsyncClient(headers={"Authorization": f"Bearer {token}"}, timeout=300)
    out = {}
    async with streamable_http_client(server_url, http_client=http_client) as (read, write):
        async with ClientSession(read, write, read_timeout_seconds=300) as session:
            await session.initialize()
            for task_id, task_text in TASKS.items():
                out[task_id] = {}
                for rv in (True, False):
                    resp = await session.call_tool(
                        "search_procedures",
                        {"task": task_text, "require_verified": rv, "limit": 10,
                         "invariant_bindings": "{}"},
                    )
                    text = resp.content[0].text if resp.content else "[]"
                    try:
                        candidates = json.loads(text)
                    except Exception:
                        candidates = {"raw_text": text}
                    key = "require_verified_True_(B_default)" if rv else "require_verified_False_(diagnostic)"
                    out[task_id][key] = candidates
                    n = len(candidates) if isinstance(candidates, list) else "ERR"
                    print(f"[{task_id}] require_verified={rv} -> {n} candidates", flush=True)
                    await asyncio.sleep(3.0)

    out_path = Path(__file__).resolve().parent / "diag_retrieval_only_results.json"
    out_path.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"wrote {out_path}", flush=True)


if __name__ == "__main__":
    asyncio.run(main())
