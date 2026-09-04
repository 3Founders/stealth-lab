"""
Retrieval abstention calibration (final pre-score remediation, section A).
Real MCP server + real client + real search_procedures tool (require_verified=False,
the same production path the B_unverified opt-in uses), against a real, larger
calibration set: relevant (+paraphrases) per admitted procedure, diverse irrelevant,
and borderline/ambiguous queries. Records every returned candidate's real
similarity score for every query -- this is the evidence the threshold decision
in retrieval-calibration.md is built from. NOT a scored trial.
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
OUT_PATH = Path(__file__).resolve().parent / "retrieval_calibration_results.jsonl"

RELEVANT = [
    {"id": "R_summary_1", "target": "structural-summary", "task": "I need to understand what a large unfamiliar Python source file contains before editing it, without reading the whole thing."},
    {"id": "R_summary_2", "target": "structural-summary", "task": "Before making changes to a file I've never seen, I want a quick outline of its functions and classes rather than the full contents."},
    {"id": "R_summary_3", "target": "structural-summary", "task": "How can an agent orient itself in a big codebase file without spending a huge number of tokens reading every line?"},
    {"id": "R_summary_4", "target": "structural-summary", "task": "Give me a compact summary of a source file's structure instead of dumping the entire file into context."},
    {"id": "R_worktree_1", "target": "git-worktree", "task": "Several coding agents need to work on the same git repository at the same time without their file edits colliding with each other."},
    {"id": "R_worktree_2", "target": "git-worktree", "task": "How do I let multiple automated coding agents make commits to the same repo concurrently without stepping on each other's changes?"},
    {"id": "R_worktree_3", "target": "git-worktree", "task": "I want to run parallel experiments on different branches of the same codebase simultaneously without race conditions in the working directory."},
    {"id": "R_worktree_4", "target": "git-worktree", "task": "What's a safe way to isolate concurrent git operations from multiple agents working on one repository?"},
    {"id": "R_defer_1", "target": "deferred-tools", "task": "My agent's system prompt is too large because every available tool's full schema is listed up front even for tools that are rarely used."},
    {"id": "R_defer_2", "target": "deferred-tools", "task": "How can I reduce the token overhead of advertising dozens of tools to an LLM agent when only a few are used per conversation?"},
    {"id": "R_defer_3", "target": "deferred-tools", "task": "Is there a way to only load a tool's detailed parameters when the agent actually decides to call it, instead of always?"},
    {"id": "R_defer_4", "target": "deferred-tools", "task": "I want to shrink my agent's context footprint from tool definitions without removing any tool's availability."},
]

IRRELEVANT = [
    {"id": "N_irrigation", "task": "Calculate the optimal seasonal irrigation schedule for a commercial almond orchard in a Mediterranean climate."},
    {"id": "N_grilling", "task": "What's the best marinade for grilling salmon over charcoal?"},
    {"id": "N_gymnastics", "task": "Explain the rules of scoring in Olympic gymnastics floor exercise."},
    {"id": "N_trademark", "task": "How do I file a trademark application for a new clothing brand in the United States?"},
    {"id": "N_retirement", "task": "What is the difference between a Roth IRA and a traditional 401(k) for retirement savings?"},
    {"id": "N_marathon", "task": "Design a training plan for running a first marathon in six months."},
    {"id": "N_vitaminD", "task": "What are the symptoms of vitamin D deficiency in adults?"},
    {"id": "N_photosynthesis", "task": "How does photosynthesis convert sunlight into chemical energy in plant cells?"},
    {"id": "N_rosebush", "task": "What's the proper way to prune a rose bush in early spring?"},
    {"id": "N_compound_interest", "task": "Explain how compound interest works for a savings account."},
]

BORDERLINE = [
    {"id": "B_team_efficiency", "task": "How can I make my team more efficient when working on a shared project?"},
    {"id": "B_coordination", "task": "What's a good way to coordinate multiple people working on the same task?"},
    {"id": "B_resources", "task": "I want my software to use fewer resources overall."},
    {"id": "B_workflow_overhead", "task": "How do I reduce unnecessary overhead in my development workflow?"},
]

ALL_QUERIES = (
    [dict(q, category="relevant") for q in RELEVANT]
    + [dict(q, category="irrelevant", target=None) for q in IRRELEVANT]
    + [dict(q, category="borderline", target=None) for q in BORDERLINE]
)


async def main():
    env_path = REPO_ROOT / "backend" / ".env"
    env = os.environ.copy()
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    env["STEALTHLAB_MCP_TOKEN"] = "bDBkrdBsLe7YJCu8Gh-dxUuL9DK_OF6bD2K7RhDu_eA"

    port = 8810
    proc = _start_mcp_server(REPO_ROOT, port, env)
    try:
        await asyncio.sleep(20.0)
        if proc.poll() is not None:
            out = proc.stdout.read() if proc.stdout else ""
            raise RuntimeError(f"MCP server exited early (code {proc.returncode}):\n{out}")
        server_url = f"http://127.0.0.1:{port}/mcp"

        async def _query_once(task_text: str) -> tuple[list | dict, float]:
            """Fresh connect-call-disconnect per query -- a single
            long-lived session degraded mid-run this pass ('transport
            write blocked' after ~10 real calls); a fresh connection per
            query costs ~1-2s extra but avoids that failure mode."""
            http_client = httpx2.AsyncClient(
                headers={"Authorization": f"Bearer {env['STEALTHLAB_MCP_TOKEN']}"}, timeout=100,
            )
            t0 = time.time()
            async with streamable_http_client(server_url, http_client=http_client) as (read, write):
                async with ClientSession(read, write, read_timeout_seconds=100) as session:
                    await session.initialize()
                    resp = await session.call_tool(
                        "search_procedures",
                        {"task": task_text, "require_verified": False, "limit": 10},
                    )
            elapsed = time.time() - t0
            text = resp.content[0].text if resp.content else "[]"
            try:
                candidates = json.loads(text)
            except Exception:
                candidates = {"raw_text": text}
            return candidates, elapsed

        done_ids: set[str] = set()
        if OUT_PATH.exists():
            for line in OUT_PATH.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    try:
                        r = json.loads(line)
                        if r.get("returned_candidates") is not None:
                            done_ids.add(r["query_id"])
                    except Exception:
                        pass
        print(f"resuming: {len(done_ids)} queries already have real results", flush=True)
        with open(OUT_PATH, "a", encoding="utf-8") as outf:
            for i, q in enumerate(ALL_QUERIES):
                if q["id"] in done_ids:
                    print(f"[{i+1}/{len(ALL_QUERIES)}] {q['id']} already done, skipping", flush=True)
                    continue
                if i > 0:
                    await asyncio.sleep(22.0)  # real Voyage 3 RPM sandbox limit
                try:
                    candidates, elapsed = await asyncio.wait_for(_query_once(q["task"]), timeout=110.0)
                except Exception as exc:  # noqa: BLE001 -- record, never hang, never silently drop
                    row = {
                        "query_id": q["id"], "category": q["category"], "target": q.get("target"),
                        "query": q["task"], "elapsed_s": None,
                        "returned_candidates": None, "error": f"{type(exc).__name__}: {exc}",
                    }
                    outf.write(json.dumps(row) + "\n")
                    outf.flush()
                    print(f"[{i+1}/{len(ALL_QUERIES)}] {q['id']} FAILED ({type(exc).__name__}) -- recorded, continuing", flush=True)
                    continue
                row = {
                    "query_id": q["id"], "category": q["category"], "target": q.get("target"),
                    "query": q["task"], "elapsed_s": elapsed,
                    "returned_candidates": candidates,
                }
                outf.write(json.dumps(row) + "\n")
                outf.flush()
                n = len(candidates) if isinstance(candidates, list) else "ERR"
                top_sim = None
                if isinstance(candidates, list) and candidates:
                    top_sim = candidates[0].get("similarity") or candidates[0].get("_similarity_score")
                print(f"[{i+1}/{len(ALL_QUERIES)}] {q['id']} ({q['category']}) -> "
                      f"{n} candidates, top_sim={top_sim} in {elapsed:.2f}s", flush=True)
        print(f"wrote {OUT_PATH}", flush=True)
    finally:
        proc.terminate()
        try:
            proc.wait(timeout=10)
        except Exception:
            proc.kill()


if __name__ == "__main__":
    asyncio.run(main())
