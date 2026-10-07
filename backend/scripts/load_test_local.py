"""Local load test of the MCP server's own overhead (securityp1.md P1-B §6.2 items 2-3), against a THROWAWAY loopback
database, with the embedding provider and the semantic judges STUBBED -- no model provider is called, so the numbers
are the server + database cost of a request, NOT what a production find_ways costs (there, the judge calls dominate).

    python scripts/load_test_local.py --dsn postgresql://user@127.0.0.1:PORT/scratch_db [--seed-goals 300]
        [--levels 1,5,10,25,50,100] [--seconds 20]

Every *_DATABASE_URL is pointed at --dsn (the server loads .neon_shards.env itself); a non-loopback --dsn is refused.
Requests go through the real HTTP stack (anonymous-read middleware, auth, MCP JSON-RPC over Streamable HTTP, the tool,
its SQL). Each level runs N concurrent clients in a closed loop for --seconds. Reports per level: requests, throughput,
error rate, p50 / p95 / p99. The breaking point is the first level whose error rate exceeds 1% or whose p95 exceeds
--slo-ms.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import socket
import statistics
import subprocess
import sys
import threading
import time
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parents[1]

STUBS = """
import hashlib, math, random, sys
def _vec(text, dim=1024):
    rng = random.Random(hashlib.sha256(text.encode()).digest())
    v = [rng.uniform(-1, 1) for _ in range(dim)]
    n = math.sqrt(sum(x * x for x in v)) or 1.0
    return [x / n for x in v]
from app.services.embeddings import Embedder
async def _embed(self, texts, input_type="document"): return [_vec(t) for t in texts]
async def _embed_one(self, text, input_type="document"): return _vec(text)
Embedder.embed, Embedder.embed_one = _embed, _embed_one
from app.services.semantic.chain import SemanticJudge
from app.services.semantic.policy import RetryPolicy
SemanticJudge.from_settings = classmethod(lambda cls, settings=None, **kw: cls([], kw.get("policy") or RetryPolicy()))
"""

SERVER = STUBS + """
import uvicorn
from app.mcp_server import health
health.RATE_PER_S = health.RATE_BURST = 1e9
uvicorn.run("app.mcp_server.server:app", host="127.0.0.1", port=int(sys.argv[1]), workers=1, log_level="warning")
"""

SEED = STUBS + """
import asyncio, os, uuid
from app.db.session import create_pool
from app.services.goals import find_or_create_goal
from app.services.search_projection import drain_outbox
from app.services.shards import pools_for
VERBS = ["add", "fix", "migrate", "speed up", "deflake", "rotate", "export", "validate", "cache", "paginate"]
NOUNS = ["the billing webhook", "CSV import", "auth tokens", "the search index", "image thumbnails", "the job queue",
         "PDF export", "rate limiting", "the payments retry", "user sessions", "the docs site", "feature flags"]
async def main(n):
    pool = await create_pool(os.environ["DATABASE_URL"], min_size=1, max_size=4)
    try:
        have = await pool.fetchval("SELECT count(*) FROM goals WHERE created_by = 'load-test-seed'")
        for i in range(max(0, n - have)):
            name = f"{VERBS[i % len(VERBS)]} {NOUNS[(i // len(VERBS)) % len(NOUNS)]} lt{uuid.uuid4().hex[:6]}"
            await find_or_create_goal(pool, canonical_name=name, scope_type="global", provenance="system_pending_review",
                                      rationale="load test", owner_id=None, visibility="public",
                                      created_by="load-test-seed", embedder=Embedder())
        await drain_outbox(pool, batch=500, pools=pools_for(pool), max_batches=50)
        print("seeded", await pool.fetchval("SELECT count(*) FROM goals WHERE created_by = 'load-test-seed'"))
    finally:
        await pool.close()
asyncio.run(main(int(sys.argv[1])))
"""


def _env(dsn: str) -> dict:
    env = {**os.environ, "STEALTHLAB_ENV": "TEST", "DEPLOYMENT_MODE": "single_user", "PYTHONUTF8": "1"}
    names = {"DATABASE_URL", "SEARCH_DATABASE_URL", "CONTROL_DATABASE_URL", "TEST_DATABASE_URL"}
    for f in (BACKEND / ".env", BACKEND / ".neon_shards.env"):
        if f.is_file():
            names |= {m.group(1) for m in re.finditer(r"(?m)^\s*([A-Z0-9_]*DATABASE_URL[A-Z0-9_]*)\s*=",
                                                       f.read_text(encoding="utf-8"))}
    for n in names:
        env[n] = dsn
    return env


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def pct(values: list[float], q: float) -> float:
    if not values:
        return float("nan")
    v = sorted(values)
    return v[min(len(v) - 1, int(round(q * (len(v) - 1))))]


def _bearer() -> str:
    """The operator token from backend/.env (single_user mode serves it); read in-process, never printed."""
    from dotenv import dotenv_values

    return dotenv_values(BACKEND / ".env").get("STEALTHLAB_MCP_TOKEN") or ""


async def level(base: str, clients: int, seconds: float, tool: str, args_for) -> dict:
    import httpx

    auth = {"authorization": f"Bearer {_bearer()}"}

    lat: list[float] = []
    errors = 0
    deadline = time.monotonic() + seconds
    counter = 0
    async with httpx.AsyncClient(timeout=60, limits=httpx.Limits(max_connections=clients + 5)) as client:
        async def worker(wid: int) -> None:
            nonlocal errors, counter
            while time.monotonic() < deadline:
                counter += 1
                body = {"jsonrpc": "2.0", "id": counter, "method": "tools/call",
                        "params": {"name": tool, "arguments": args_for(wid, counter)}}
                t0 = time.perf_counter()
                try:
                    r = await client.post(base + "/mcp", json=body, headers={
                        "accept": "application/json, text/event-stream", "content-type": "application/json",
                        "mcp-protocol-version": "2025-06-18", **auth})
                    ok = r.status_code == 200 and '"isError":true' not in r.text.replace(" ", "")
                except Exception:  # noqa: BLE001 -- a refused / timed-out request is an error, not a crash
                    ok = False
                dt = (time.perf_counter() - t0) * 1000
                if ok:
                    lat.append(dt)
                else:
                    errors += 1
        await asyncio.gather(*(worker(i) for i in range(clients)))
    total = len(lat) + errors
    return {"clients": clients, "requests": total, "throughput_rps": round(total / seconds, 1),
            "error_rate": round(errors / total, 4) if total else None,
            "p50_ms": round(pct(lat, 0.50), 1), "p95_ms": round(pct(lat, 0.95), 1), "p99_ms": round(pct(lat, 0.99), 1),
            "mean_ms": round(statistics.fmean(lat), 1) if lat else None}


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True)
    ap.add_argument("--seed-goals", type=int, default=300)
    ap.add_argument("--levels", default="1,5,10,25,50,100")
    ap.add_argument("--seconds", type=float, default=20)
    ap.add_argument("--slo-ms", type=float, default=2000)
    ap.add_argument("--json")
    a = ap.parse_args()
    if (urlparse(a.dsn).hostname or "").lower() not in {"127.0.0.1", "localhost", "::1"}:
        print("refusing: --dsn is not a loopback database")
        return 2
    env = _env(a.dsn)
    seeded = subprocess.run([sys.executable, "-c", SEED, str(a.seed_goals)], cwd=BACKEND, env=env,
                            capture_output=True, text=True)
    print((seeded.stdout.strip().splitlines() or ["seed: (no output)"])[-1],
          ("" if seeded.returncode == 0 else seeded.stderr[-800:]))
    port = _free_port()
    proc = subprocess.Popen([sys.executable, "-c", SERVER, str(port)], cwd=BACKEND, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    log: list[str] = []
    threading.Thread(target=lambda: [log.append(x.rstrip()) for x in proc.stdout], daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    results: dict = {"note": "embedding + judges stubbed: server + database overhead only", "levels": {}}
    try:
        import urllib.request

        for _ in range(240):
            try:
                with urllib.request.urlopen(base + "/readyz", timeout=2) as r:
                    if r.status == 200:
                        break
            except Exception:  # noqa: BLE001
                time.sleep(0.5)
        else:
            print("server did not become ready;", "\n".join(log[-20:]))
            return 1
        words = ["billing webhook", "csv import", "auth tokens", "search index", "job queue", "pdf export",
                 "rate limiting", "user sessions", "feature flags", "image thumbnails"]
        plans = {
            "find_ways": lambda w, n: {"query": f"how do I fix the {words[(w + n) % len(words)]} ({w}-{n})",
                                       "use_llm": False},
            "find_ways_summary": lambda w, n: {"query": f"speed up the {words[(w * 3 + n) % len(words)]} ({w}-{n})",
                                               "use_llm": False, "detail": "summary"},
        }
        for name, args_for in plans.items():
            tool = "find_ways"
            results["levels"][name] = []
            for c in [int(x) for x in a.levels.split(",")]:
                r = asyncio.run(level(base, c, a.seconds, tool, args_for))
                results["levels"][name].append(r)
                print(name, json.dumps(r), flush=True)
                if r["error_rate"] and r["error_rate"] > 0.01 or (r["p95_ms"] == r["p95_ms"] and r["p95_ms"] > a.slo_ms):
                    results.setdefault("breaking_point", {})[name] = r
                    break
    finally:
        proc.kill()
    if a.json:
        Path(a.json).write_text(json.dumps(results, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
