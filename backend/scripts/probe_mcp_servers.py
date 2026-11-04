"""Liveness and real tool lists for the registry's remote MCP servers (the tool router's trust signal).

    python scripts/probe_mcp_servers.py [--concurrency 24] [--timeout 10] [--limit N] [--only-missing]

For each server in app/tool_routing/data/mcp_registry.json with an https remote, the MCP handshake: `initialize`, then
`tools/list`. Nothing else -- no tool is called, no credential is sent. The outcome per server:
  live     it answered and listed its tools (their names and descriptions are kept, cut)
  auth     it answered 401/403: alive, but it needs a sign-in to list its tools
  dead     no answer, an error status, or not MCP
Written to app/tool_routing/data/registry_probe.json ({name: {...}}), resumable with --only-missing.

Why: the registry is an open index -- anyone can publish, nothing is reviewed or checked to be alive (35% of servers
link a repository). A server that answers is at least real; one that lists its tools can be ranked tool by tool.
The servers' replies are third-party data: stored and matched, never followed.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

DATA = Path(__file__).resolve().parents[1] / "app" / "tool_routing" / "data"
SNAPSHOT = DATA / "mcp_registry.json"
OUT = DATA / "registry_probe.json"
from app.tool_routing.liveness import probe  # noqa: E402 -- the same handshake the server's daily check uses


async def main(a) -> int:
    servers = json.loads(SNAPSHOT.read_text(encoding="utf-8"))["servers"]
    done: dict = json.loads(OUT.read_text(encoding="utf-8")) if OUT.exists() and a.only_missing else {}
    todo = [s for s in servers if s.get("remote_urls") and s["name"] not in done]
    if a.limit:
        todo = todo[:a.limit]
    print(f"{len(servers)} servers, {len(todo)} remote ones to probe ({len(done)} already)")
    sem = asyncio.Semaphore(a.concurrency)
    counts: dict[str, int] = {}
    t0 = time.monotonic()
    async with httpx.AsyncClient(follow_redirects=True, limits=httpx.Limits(max_connections=a.concurrency * 2)) as client:
        async def one(s):
            async with sem:
                res = await probe(client, s["remote_urls"][0]["url"], a.timeout)
            res["checked_at"] = time.strftime("%Y-%m-%d", time.gmtime())
            done[s["name"]] = res
            counts[res["status"]] = counts.get(res["status"], 0) + 1
            n = sum(counts.values())
            if n % 250 == 0:
                OUT.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
                print(f"{n}/{len(todo)} ({time.monotonic() - t0:.0f} s): {counts}", flush=True)
        await asyncio.gather(*(one(s) for s in todo))
    OUT.write_text(json.dumps(done, ensure_ascii=False), encoding="utf-8")
    print(f"done in {time.monotonic() - t0:.0f} s: {counts} -> {OUT}")
    return 0


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--concurrency", type=int, default=24)
    ap.add_argument("--timeout", type=float, default=10.0)
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--only-missing", action="store_true")
    raise SystemExit(asyncio.run(main(ap.parse_args())))
