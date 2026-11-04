"""Which tool servers work right now: checked again every day, and learned from real calls.

The registry snapshot in the image carries one liveness probe per server, taken when the snapshot was made (2026-10-30 for the
first): a server that died or started asking for a sign-in since was still offered as runnable until the next deploy with a new
snapshot. Two things keep it current instead (docs: the 2026-11-04 tool reliability fixes):

  * every day one process re-checks the servers kell could run (verified, remote, not a bulk publisher) with the same MCP
    handshake as scripts/probe_mcp_servers.py -- initialize, then tools/list; nothing is called, no credential is sent -- and
    writes the outcome to tool_liveness (db/180_tool_liveness.sql). A server that does not answer is asked once more at the end
    of the run before it counts as dead. Cards read from the database carry the newest outcome (`apply`);
  * a server whose last three real calls through the gateway in the past day all failed on the server's side (not a refusal by
    kell's rules, not a sign-in, not a tool error the arguments may have caused, not a slow answer) is left out as failing until a call succeeds
    or the failures age out (`DOWN`).
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, Callable, Optional

import httpx

from app.tool_routing.catalog import ToolCard

UA = "kell-tool-router/0.1 (liveness probe; https://www.kell.org.in)"
HEADERS = {"content-type": "application/json", "accept": "application/json, text/event-stream", "user-agent": UA}
TOOLS_MAX = 60

CURRENT: dict[str, str] = {}             # server -> live | auth | dead, from the newest daily check
DOWN: set[str] = set()                   # servers whose recent real calls all failed
_LOADED_AT = -1e9
RELOAD_S = 600.0
RECHECK_EVERY_S = 24 * 3600.0
LOCK_KEY = 0x7001_C4D6                   # one re-check at a time across processes and replicas (the catalog sync uses ...C4D5)
CONCURRENCY = int(os.environ.get("TOOL_RECHECK_CONCURRENCY", "16"))
TIMEOUT_S = float(os.environ.get("TOOL_RECHECK_TIMEOUT", "8"))
_task: Optional[asyncio.Task] = None


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
    """The MCP handshake against one server: live (answered and listed its tools), auth (401/403), dead, or unprobed (a template URL)."""
    t0 = time.monotonic()
    if "{" in url:  # a template ("https://host:{PORT}/mcp"): it needs the user's own settings before it is a URL
        return {"status": "unprobed", "ms": 0, "why": "templated url"}
    try:
        init = {"jsonrpc": "2.0", "id": 1, "method": "initialize",
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


def apply(card: ToolCard) -> ToolCard:
    """The card with its newest checked liveness, when the daily check has one (in place; returns it)."""
    status = CURRENT.get(card.name)
    if status and card.source == "registry":
        card.extra["probe"] = status
    return card


# The last three real calls of a server in the past day, counting only failures on the server's side.
_DOWN_SQL = """
SELECT server FROM (
    SELECT server, status, row_number() OVER (PARTITION BY server ORDER BY at DESC) AS n
    FROM tool_gateway_calls
    WHERE at > now() - interval '24 hours' AND status NOT LIKE 'refused:%'
      AND status NOT IN ('failed:signin', 'failed:tool_error', 'failed:timeout')
) recent WHERE n <= 3
GROUP BY server HAVING count(*) = 3 AND bool_and(status LIKE 'failed:%')
"""


async def refresh(pool: Any, now: Optional[float] = None) -> None:
    """Reloads CURRENT and DOWN from the database, at most every RELOAD_S. Without the tables nothing changes."""
    global _LOADED_AT, CURRENT, DOWN
    t = time.monotonic() if now is None else now
    if pool is None or t - _LOADED_AT < RELOAD_S:
        return
    _LOADED_AT = t
    try:
        async with pool.acquire() as conn:
            rows = await conn.fetch("SELECT server, status FROM tool_liveness WHERE status IN ('live', 'auth', 'dead')")
            down = await conn.fetch(_DOWN_SQL)
        CURRENT = {str(r["server"]): str(r["status"]) for r in rows}
        DOWN = {str(r["server"]) for r in down}
    except Exception:  # noqa: BLE001 -- no table yet: the snapshot's probe stays in force
        pass


_TODO_SQL = """
SELECT name, extra->>'connector_url' AS url FROM tool_cards
WHERE source = 'registry' AND extra->>'verified' = 'true' AND coalesce(extra->>'connector_url', '') <> ''
  AND coalesce((extra->>'owner_count')::int, 1) <= 30
"""


async def recheck(pool: Any, say: Callable[[str], None] = print, client: Optional[httpx.AsyncClient] = None) -> dict[str, int]:
    """Checks every server kell could run and writes the outcomes. Returns the count per status."""
    async with pool.acquire() as conn:
        todo = [(str(r["name"]), str(r["url"])) for r in await conn.fetch(_TODO_SQL)]
    say(f"tool liveness: checking {len(todo)} servers")
    t0 = time.monotonic()
    sem = asyncio.Semaphore(CONCURRENCY)
    results: dict[str, dict] = {}
    own = client is None
    client = client or httpx.AsyncClient(follow_redirects=True, limits=httpx.Limits(max_connections=CONCURRENCY * 2))
    try:
        async def one(name: str, url: str) -> None:
            async with sem:
                results[name] = await probe(client, url, TIMEOUT_S)

        await asyncio.gather(*(one(n, u) for n, u in todo))
        # one more try for the ones that did not answer: a slow minute is not a dead server
        await asyncio.gather(*(one(n, u) for n, u in todo if results.get(n, {}).get("status") == "dead"))
    finally:
        if own:
            await client.aclose()
    rows = [(n, str(r.get("status")), r.get("http"), r.get("tool_count")) for n, r in results.items() if r.get("status") != "unprobed"]
    async with pool.acquire() as conn:
        await conn.executemany("""INSERT INTO tool_liveness (server, status, http, tool_count, checked_at) VALUES ($1, $2, $3, $4, now())
                                  ON CONFLICT (server) DO UPDATE SET status = EXCLUDED.status, http = EXCLUDED.http,
                                      tool_count = EXCLUDED.tool_count, checked_at = now()""", rows)
    counts: dict[str, int] = {}
    for _, s, _, _ in rows:
        counts[s] = counts.get(s, 0) + 1
    say(f"tool liveness: {counts} in {time.monotonic() - t0:.0f} s")
    global _LOADED_AT
    _LOADED_AT = -1e9                    # the next search reads the new outcomes
    return counts


async def due(pool: Any) -> bool:
    """True when the newest check is more than a day old (or there is none)."""
    async with pool.acquire() as conn:
        age = await conn.fetchval("SELECT extract(epoch FROM now() - max(checked_at)) FROM tool_liveness")
    return age is None or float(age) >= RECHECK_EVERY_S


async def run_daily(pool: Any, say: Callable[[str], None] = print, first_wait_s: float = 600.0) -> None:
    """Forever: once an hour, if the last check is a day old and no other process is checking, check again."""
    await asyncio.sleep(first_wait_s)                        # not while a fresh deploy is still warming up
    while True:
        try:
            async with pool.acquire() as lock_conn:
                if await lock_conn.fetchval("SELECT pg_try_advisory_lock($1)", LOCK_KEY):
                    try:
                        if await due(pool):
                            await recheck(pool, say)
                    finally:
                        await lock_conn.execute("SELECT pg_advisory_unlock($1)", LOCK_KEY)
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 -- a failed run is tried again in an hour
            say(f"tool liveness: the check failed ({type(exc).__name__}); trying again in an hour")
        await asyncio.sleep(3600.0)


def start(pool: Any, say: Callable[[str], None] = print) -> None:
    """Starts the daily check in the background, once per process (TOOL_RECHECK=off turns it off)."""
    global _task
    if os.environ.get("TOOL_RECHECK", "on").lower() == "off" or (_task is not None and not _task.done()):
        return
    _task = asyncio.get_running_loop().create_task(run_daily(pool, say), name="tool-liveness")
