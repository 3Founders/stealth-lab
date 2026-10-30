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
UA = "kell-tool-router/0.1 (liveness probe; https://www.kell.org.in)"
HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream", "user-agent": UA}
TOOLS_MAX = 60


def parse(r: httpx.Response) -> dict:
    text = r.text
    if "text/event-stream" in r.headers.get("content-type", ""):
        for line in text.splitlines():
            if line.startswith("data:"):
                try:
                    return json.loads(line[5:].strip())
                except ValueError:
                    continue
        return {}
    try:
        return r.json()
    except ValueError:
        return {}


async def probe(client: httpx.AsyncClient, url: str, timeout: float) -> dict:
    t0 = time.monotonic()
    if "{" in url:  # a template ("https://host:{PORT}/mcp"): it needs the user's own settings before it is a URL
        return {"status": "unprobed", "ms": 0, "why": "templated url"}
    try:
        init ={"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {}, "clientInfo": {"name": "kell-probe", "version": "0.1"}}}
        r = await client.post(url, json=init, headers=HEADERS, timeout=timeout)
        ms = int((time.monotonic() - t0) * 1000)
        if r.status_code in (401, 403):
            return {"status": "auth", "ms": ms, "http": r.status_code}
        j = parse(r)
        if r.status_code >= 400 or "result" not in j:
            return {"status": "dead", "ms": ms, "http": r.status_code, "why": "no MCP initialize result"}
        info = (j["result"] or {}).get("serverInfo") or {}
        sid = r.headers.get("mcp-session-id")
        h = {**HEADERS, **({"mcp-session-id": sid} if sid else {})}
        await client.post(url, json={"jsonrpc": "2.0", "method": "notifications/initialized"}, headers=h, timeout=timeout)
        r2 = await client.post(url, json={"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}, headers=h, timeout=timeout)
        if r2.status_code in (401, 403):
            return {"status": "auth", "ms": ms, "http": r2.status_code, "server": str(info.get("name", ""))[:80]}
        tools = (parse(r2).get("result") or {}).get("tools") or []
        return {"status": "live", "ms": ms, "server": str(info.get("name", ""))[:80],
                "tools": [{"name": str(t.get("name", ""))[:80], "description": " ".join(str(t.get("description") or "").split())[:300]}
                          for t in tools[:TOOLS_MAX] if isinstance(t, dict) and t.get("name")],
                "tool_count": len(tools)}
    except Exception as exc:  # noqa: BLE001 -- one odd server (httpx.InvalidURL is not an HTTPError) must not end the run
        return {"status": "dead", "ms": int((time.monotonic() - t0) * 1000), "why": type(exc).__name__}


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
