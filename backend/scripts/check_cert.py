"""Check whether one call is inside an intent certificate (P1-06).

    python scripts/check_cert.py --cert ic_... --owner dev-local --task demo-1 \
        --server ai.exa/exa --tool web_search_exa

    python scripts/check_cert.py --cert ic_... --owner dev-local --task demo-1 \
        --server hinoki --tool explain_finding

Prints `ok` or `REFUSED: <reason>`; exits 0 or 1. The reason codes are stable (`cert:expired`,
`cert:owner`, `cert:tool`, `cert:argument:repo_url:pattern`, ...).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values, load_dotenv

load_dotenv(Path(__file__).resolve().parents[1] / ".env")


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return env.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


def parse_arg(text: str) -> tuple[str, str]:
    key, sep, value = text.partition("=")
    if not sep or not key.strip():
        raise SystemExit(f"--arg needs key=value, got {text!r}")
    return key.strip(), value


async def run(args: argparse.Namespace) -> int:
    dsn = dsn_from_env()
    if not dsn:
        raise SystemExit("no DATABASE_URL in backend/.env or the environment")
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1, timeout=30)
    try:
        from app.tool_routing import certificates

        call = SimpleNamespace(
            owner_id=args.owner, task_id=args.task, goal_id=args.goal,
            server=args.server, tool=args.tool, arguments=dict(args.arg or []),
            data_class=args.data_class,
        )
        ok, reason = await certificates.verify(pool, args.cert, call)
        print("ok" if ok else f"REFUSED: {reason}")
        return 0 if ok else 1
    finally:
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Check a call against an intent certificate.")
    ap.add_argument("--cert", required=True)
    ap.add_argument("--owner", default=os.environ.get("KELL_DEV_OWNER") or "dev-local")
    ap.add_argument("--task", default="")
    ap.add_argument("--goal", default=None)
    ap.add_argument("--server", default="")
    ap.add_argument("--tool", default="")
    ap.add_argument("--data-class", default="PUBLIC_SOURCE")
    ap.add_argument("--arg", action="append", metavar="KEY=VALUE")
    args = ap.parse_args()
    args.arg = dict(parse_arg(a) for a in (args.arg or []))
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
