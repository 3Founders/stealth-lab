"""Live graceful-shutdown check for the MCP server (securityp1.md P1-C), against a THROWAWAY loopback database.

    python scripts/live_shutdown_check.py --dsn postgresql://user@127.0.0.1:PORT/scratch_db

What it proves: a request that is in flight when the server is told to stop still completes, the lifespan shutdown
finishes (pool closed, Sentry / OpenTelemetry flushed), and the process ends within uvicorn's graceful timeout.

Exit status: uvicorn (>= 0.29) re-raises the captured stop signal after a CLEAN shutdown so the parent sees which
signal ended it: 143 after SIGTERM on Linux, 3 after CTRL_BREAK on Windows. That is the expected status of a clean
stop, so the check passes on 0 or on that re-raised signal, and only when the shutdown log is complete.

Safety: it refuses a non-loopback DSN, and it points EVERY database variable the server could read (DATABASE_URL,
SEARCH_DATABASE_URL, CONTROL_DATABASE_URL and every *_DATABASE_URL named in backend/.env and backend/.neon_shards.env)
at the scratch DSN before the server starts -- the server loads .neon_shards.env itself, and load_dotenv never
overrides a variable that is already set. No connection string is printed.

How the request is held in flight: a second session takes an exclusive lock on schema_migrations, so /readyz's
migration check waits on it (the launcher raises the readiness timeout and disables its cache). The stop signal is
sent while it waits, then the lock is released.

Signals: POSIX sends SIGTERM. Windows has no SIGTERM delivery to another process; it sends CTRL_BREAK_EVENT, which
uvicorn handles through the same shutdown path (should_exit -> drain -> lifespan shutdown). Run it on Linux for SIGTERM.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

BACKEND = Path(__file__).resolve().parents[1]
LOOPBACK = {"127.0.0.1", "localhost", "::1"}

LAUNCHER = """
import sys, uvicorn
from app.mcp_server import health
health.READY_DB_TIMEOUT_S = 20.0      # hold the in-flight request on the lock instead of timing out
health.MIGRATION_CACHE_S = 0.0        # always run the migration query (the one that waits on the lock)
health.RATE_BURST = health.RATE_PER_S = 1000.0
uvicorn.run("app.mcp_server.server:app", host="127.0.0.1", port=int(sys.argv[1]), workers=1,
            timeout_graceful_shutdown=int(sys.argv[2]), log_level="info")
"""


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _db_var_names() -> set[str]:
    names = {"DATABASE_URL", "SEARCH_DATABASE_URL", "CONTROL_DATABASE_URL", "TEST_DATABASE_URL", "DATABASE_URL_DIRECT"}
    for f in (BACKEND / ".env", BACKEND / ".neon_shards.env"):
        if f.is_file():
            for line in f.read_text(encoding="utf-8").splitlines():
                m = re.match(r"^\s*([A-Z0-9_]*DATABASE_URL[A-Z0-9_]*)\s*=", line)
                if m:
                    names.add(m.group(1))
    return names


def _get(url: str, timeout: float) -> tuple[int, dict]:
    try:
        with urllib.request.urlopen(url, timeout=timeout) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


async def _lock(dsn: str, hold: asyncio.Event, release: asyncio.Event) -> None:
    import asyncpg

    conn = await asyncpg.connect(dsn)
    try:
        tx = conn.transaction()
        await tx.start()
        await conn.execute("LOCK TABLE schema_migrations IN ACCESS EXCLUSIVE MODE")
        hold.set()
        await release.wait()
        await tx.rollback()
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", required=True, help="a THROWAWAY loopback database")
    ap.add_argument("--graceful", type=int, default=25)
    ap.add_argument("--hold-s", type=float, default=4.0, help="how long the request stays in flight after the signal")
    a = ap.parse_args()
    if (urlparse(a.dsn).hostname or "").lower() not in LOOPBACK:
        print("refusing: --dsn is not a loopback database")
        return 2
    env = {**os.environ, "STEALTHLAB_ENV": "TEST", "DEPLOYMENT_MODE": "single_user", "PYTHONUTF8": "1"}
    for name in _db_var_names():
        env[name] = a.dsn
    port = _free_port()
    flags = subprocess.CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0
    proc = subprocess.Popen([sys.executable, "-c", LAUNCHER, str(port), str(a.graceful)], cwd=BACKEND, env=env,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, creationflags=flags)
    log_lines: list[str] = []
    threading.Thread(target=lambda: [log_lines.append(ln.rstrip()) for ln in proc.stdout], daemon=True).start()
    base = f"http://127.0.0.1:{port}"
    result: dict = {"signal": "SIGTERM" if os.name != "nt" else "CTRL_BREAK_EVENT"}
    try:
        deadline = time.time() + 120
        while time.time() < deadline:
            try:
                if _get(base + "/healthz", 2)[0] == 200:
                    break
            except Exception:  # noqa: BLE001 -- not up yet
                time.sleep(0.5)
        else:
            result["error"] = "server did not come up"
            return 1
        result["readyz_before"] = _get(base + "/readyz", 10)

        loop = asyncio.new_event_loop()
        hold, release = asyncio.Event(), asyncio.Event()
        locker = threading.Thread(target=lambda: loop.run_until_complete(_lock(a.dsn, hold, release)), daemon=True)
        locker.start()
        for _ in range(100):
            if hold.is_set():
                break
            time.sleep(0.1)
        inflight: dict = {}

        def call() -> None:
            t0 = time.time()
            try:
                inflight["response"] = _get(base + "/readyz", 60)
            except Exception as exc:  # noqa: BLE001
                inflight["error"] = type(exc).__name__
            inflight["seconds"] = round(time.time() - t0, 2)
        caller = threading.Thread(target=call)
        caller.start()
        time.sleep(1.0)                                     # the request is now waiting on the lock
        t_signal = time.time()
        proc.send_signal(signal.CTRL_BREAK_EVENT if os.name == "nt" else signal.SIGTERM)
        time.sleep(a.hold_s)
        loop.call_soon_threadsafe(release.set)              # let the in-flight request finish
        caller.join(60)
        try:
            code = proc.wait(timeout=a.graceful + 15)
        except subprocess.TimeoutExpired:
            proc.kill()
            code = "killed after timeout"
        result.update(inflight=inflight, exit_code=code, seconds_signal_to_exit=round(time.time() - t_signal, 2),
                      shutdown_log=[ln for ln in log_lines if re.search(r"(?i)shutdown|draining|finished|waiting", ln)][-8:])
        clean_log = (any("Application shutdown complete" in ln for ln in log_lines)
                     and any("Finished server process" in ln for ln in log_lines))
        signal_exit = {0, 143, -15} if os.name != "nt" else {0, 3, 0xC000013A}
        result["clean_shutdown_log"] = clean_log
        ok = inflight.get("response", (0,))[0] in (200, 503) and code in signal_exit and clean_log
        result["passed"] = bool(ok)
        return 0 if ok else 1
    finally:
        if proc.poll() is None:
            proc.kill()
        print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    raise SystemExit(main())
