"""List, approve or deny a tool approval (P1-11).

    python scripts/approve_tool.py --list --owner dev-local
    python scripts/approve_tool.py --show ap_... --owner dev-local
    python scripts/approve_tool.py --approve ap_... --owner dev-local
    python scripts/approve_tool.py --deny ap_... --owner dev-local --note "not this repo"

This is the manual twin of the card in the web and desktop apps. The approval is scoped to one exact
call shape and is single-use; after `--approve`, the next identical gate call consumes it.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values, load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return env.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


async def run(args: argparse.Namespace) -> int:
    dsn = dsn_from_env()
    if not dsn:
        raise SystemExit("no DATABASE_URL in backend/.env or the environment")
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1, timeout=30)
    try:
        from app.tool_routing import approvals

        if args.list:
            rows = await approvals.list_pending(pool, args.owner, limit=args.limit)
            print(json.dumps(rows, indent=2, default=str))
            return 0
        if args.show:
            row = await approvals.get(pool, args.show, args.owner)
            print(json.dumps(row, indent=2, default=str) if row else "not found")
            return 0 if row else 1
        if args.approve:
            ok = await approvals.approve(pool, args.approve, args.owner,
                                         decided_by=args.decided_by, ttl_s=args.ttl)
            print(f"approved: {ok}")
            return 0 if ok else 1
        if args.deny:
            ok = await approvals.deny(pool, args.deny, args.owner,
                                      decided_by=args.decided_by, note=args.note)
            print(f"denied: {ok}")
            return 0 if ok else 1
        print("choose --list, --show, --approve or --deny", file=sys.stderr)
        return 2
    finally:
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="List, approve or deny a tool approval.")
    ap.add_argument("--owner", default=os.environ.get("KELL_DEV_OWNER") or "dev-local")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--show", default="")
    ap.add_argument("--approve", default="")
    ap.add_argument("--deny", default="")
    ap.add_argument("--decided-by", default="dev-local")
    ap.add_argument("--ttl", type=int, default=900)
    ap.add_argument("--note", default="")
    ap.add_argument("--limit", type=int, default=20)
    raise SystemExit(asyncio.run(run(ap.parse_args())))


if __name__ == "__main__":
    main()
