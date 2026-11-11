"""Verify the tool-call audit hash chain (P1-07).

    python scripts/verify_audit_chain.py
    python scripts/verify_audit_chain.py --since 1h
    python scripts/verify_audit_chain.py --since 2026-11-11T00:00:00Z

Prints `ok, N receipts` and exits 0, or `FAILED: ...` and exits 1. The check is over the selected
window; a break before `--since` is not inspected.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return env.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


def parse_since(text: str | None) -> datetime | None:
    if not text:
        return None
    value = text.strip()
    now = datetime.now(timezone.utc)
    try:
        if value.endswith("h"):
            return now - timedelta(hours=float(value[:-1]))
        if value.endswith("d"):
            return now - timedelta(days=float(value[:-1]))
        if value.endswith("m"):
            return now - timedelta(minutes=float(value[:-1]))
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise SystemExit(f"--since must be 1h, 7d, or an ISO timestamp: {exc}") from exc


async def run(since: datetime | None) -> int:
    dsn = dsn_from_env()
    if not dsn:
        raise SystemExit("no DATABASE_URL in backend/.env or the environment")
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1, timeout=30)
    try:
        from app.tool_routing import audit

        ok, message, count = await audit.verify_chain(pool, since)
        if ok:
            print(f"ok, {count} receipts")
            return 0
        print(f"FAILED: {message}")
        return 1
    finally:
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Verify the tool-call audit chain.")
    ap.add_argument("--since", default=None, help="1h, 7d, or an ISO timestamp")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(parse_since(args.since))))


if __name__ == "__main__":
    main()
