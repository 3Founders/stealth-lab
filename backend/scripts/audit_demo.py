"""Write a few demo audit receipts and verify the chain (P1-07).

    python scripts/audit_demo.py
    python scripts/audit_demo.py --count 3 --tamper

This is a manual-check helper, not production traffic: the rows are labelled `demo` and are
ordinary audit rows, so `verify_audit_chain.py` covers them too.

`--tamper` does two things: it shows that `db/197` refuses an UPDATE through the normal connection,
then (inside one transaction, with the trigger disabled) edits a row, proves the hash chain fails at
that receipt, and rolls the transaction back so nothing is left changed.
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
    """The environment wins over backend/.env, so an exported scratch DATABASE_URL is honored."""
    env = dotenv_values(Path(__file__).resolve().parents[1] / ".env")
    return os.environ.get("DATABASE_URL") or env.get("DATABASE_URL") or ""


class _Rollback(Exception):
    """Raised to roll the tamper transaction back once the proof has been read."""


async def _tamper(conn, audit, receipt_id: str) -> int:
    """Prove both halves: the trigger blocks an update, and the chain catches one that gets through."""
    blocked = ""
    try:
        await conn.execute("UPDATE tool_call_audit SET reason = 'tampered' WHERE receipt_id = $1", receipt_id)
    except Exception as exc:  # noqa: BLE001 -- the guard is the expected path
        blocked = str(exc)
    print(f"guard:   {'blocked as expected' if 'append-only' in blocked else 'NOT ENFORCED'} ({blocked or 'update went through'})")

    bad_message, bad = "", False
    try:
        async with conn.transaction():
            try:
                await conn.execute("ALTER TABLE tool_call_audit DISABLE TRIGGER tool_call_audit_no_update")
            except Exception as exc:  # noqa: BLE001 -- not the table owner: the guard still holds
                print(f"tampered: skipped (cannot disable the guard: {type(exc).__name__})")
                return 0 if "append-only" in blocked else 1
            await conn.execute("UPDATE tool_call_audit SET reason = 'tampered' WHERE receipt_id = $1", receipt_id)
            bad, bad_message, _ = await audit.verify_chain(conn)
            raise _Rollback
    except _Rollback:
        pass
    print(f"tampered: {'ok (WRONG)' if bad else 'FAILED as expected'} ({bad_message})")
    ok, message, _ = await audit.verify_chain(conn)
    print(f"restored: {'ok' if ok else 'FAILED'} ({message})")
    return 0 if (not bad and ok and "append-only" in blocked) else 1


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
                return await _tamper(conn, audit, ids[0])
        return 0 if ok else 1
    finally:
        await pool.close()


def main() -> None:
    ap = argparse.ArgumentParser(description="Write demo audit receipts and verify the chain.")
    ap.add_argument("--count", type=int, default=2)
    ap.add_argument("--tamper", action="store_true",
                    help="prove the append-only guard and that the chain fails on a tampered row")
    args = ap.parse_args()
    raise SystemExit(asyncio.run(run(args.count, args.tamper)))


if __name__ == "__main__":
    main()
