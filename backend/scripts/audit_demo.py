"""Write a few demo audit receipts and verify the chain (P1-07).

    python scripts/audit_demo.py
    python scripts/audit_demo.py --count 3

This is a manual-check helper, not production traffic: the rows are labelled `demo` and are
ordinary audit rows, so `verify_audit_chain.py` covers them too.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from types import SimpleNamespace

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from dotenv import dotenv_values


def dsn_from_env() -> str:
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return env.get("DATABASE_URL") or os.environ.get("DATABASE_URL") or ""


async def run(count: int, tamper: bool = False) -> int:
    dsn = dsn_from_env()
    if not dsn:
        raise SystemExit("no DATABASE_URL in backend/.env or the environment")
    import asyncpg

    pool = await asyncpg.create_pool(dsn, min_size=1, max_size=1, timeout=30)
    try:
        from app.tool_routing import audit

        decision = SimpleNamespace(effect="allow", reason="demo", risk="R0",
                                   cert_id=None, approval_id=None)
        ids = []
        for i in range(1, max(1, count) + 1):
            call = SimpleNamespace(
                owner_id="demo", agent="demo", task_id="demo", goal_id=None,
                server="demo", tool="ping", arguments={"i": i},
                data_class="PUBLIC_SOURCE", cert_id=None,
            )
            ids.append(await audit.record(
                pool, call, decision, status="ok", latency_ms=10 * i, cost_usd=0.001 * i))
        print("wrote:", ", ".join(ids))
        ok, message, n = await audit.verify_chain(pool)
        print(f"verify: {'ok' if ok else 'FAILED'} ({message}), {n} receipts")

        if tamper and ids:
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE tool_call_audit SET reason = 'tampered' WHERE receipt_id = $1", ids[0])
            bad, bad_message, _ = await audit.verify_chain(pool)
            print(f"tampered: {'ok (WRONG)' if bad else 'FAILED as expected'} ({bad_message})")
            async with pool.acquire() as conn:
                await conn.execute(
                    "UPDATE tool_call_audit SET reason = 'demo' WHERE receipt_id = $1", ids[0])
            fixed, fixed_message, _ = await audit.verify_chain(pool)
            print(f"restored: {'ok' if fixed else 'FAILED'} ({fixed_message})")
            return 0 if (not bad and fixed) else 1
        return 0 if ok else 1
    finally:
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Write demo audit receipts and verify the chain.")
    ap.add_argument("--count", type=int, default=2)
    ap.add_argument("--tamper", action="store_true",
                    help="edit one receipt, prove the chain fails, then restore it")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.count, args.tamper)))


if __name__ == "__main__":
    main()
