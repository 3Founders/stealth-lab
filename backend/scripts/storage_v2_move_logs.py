#!/usr/bin/env python3
"""Storage layout v2 (docs/storage_layout_v2.md): move the log tables of an existing deployment from the control
database onto the search group, once its members are registered.

    DATABASE_URL=<control> S001_DATABASE_URL=... python scripts/storage_v2_move_logs.py [--dry-run]

Each row goes to the member the app itself would write it to, so every reader finds it where it looks:

  identity_decisions   pool_for_log("identity:<type>:<idempotency key or text>")  (replay lookups ask every member)
  llm_spend            pool_for_log(<id>)                                          (budget sums ask every member)
  retrieval_decisions  pool_for_log(<id>)
  routing_observations the Goal's routing member (search_routes 'routing_goal')   (per-Goal reads ask ONE member)
  routing_decisions    the Goal's routing member

Copy first (INSERT .. ON CONFLICT (id) DO NOTHING, row for row, same ids), then delete exactly the copied ids from
the control database, in batches. Idempotent: an interrupted run is resumed by running it again. The search
projections are NOT moved here -- `admin reindex` rebuilds them on the members from the canonical rows.
"""
from __future__ import annotations

import argparse
import asyncio
import sys
from pathlib import Path
from typing import Any

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

TABLES = ("identity_decisions", "llm_spend", "retrieval_decisions", "routing_observations", "routing_decisions")
BATCH = 500


async def _target(pool: Any, table: str, row: Any) -> Any:
    from app.routing.store import _goal_log_pool
    from app.services import search_group

    if table == "identity_decisions":
        key = row["idempotency_key"] or row["candidate_text"]
        return await search_group.pool_for_log(pool, f"identity:{row['object_type']}:{key}")
    if table in ("routing_observations", "routing_decisions"):
        return await _goal_log_pool(pool, str(row["goal_id"]))
    return await search_group.pool_for_log(pool, str(row["id"]))


async def move_table(pool: Any, table: str, *, dry_run: bool) -> dict:
    total = int(await pool.fetchval(f"SELECT count(*) FROM {table}"))
    if dry_run:
        return {"rows": total, "moved": 0}
    if total == 0:                        # nothing (left) to move -- a resumed run still fixes the sequences
        await _advance_sequences(pool, table)
        return {"rows": 0, "moved": 0}
    moved = 0
    while True:
        rows = await pool.fetch(f"SELECT * FROM {table} ORDER BY id LIMIT {BATCH}")
        if not rows:
            break
        cols = list(rows[0].keys())
        sql = (f"INSERT INTO {table} ({', '.join(cols)}) VALUES ({', '.join(f'${i + 1}' for i in range(len(cols)))}) "
               f"ON CONFLICT (id) DO NOTHING")
        by_target: dict[int, tuple[Any, list]] = {}
        for r in rows:
            t = await _target(pool, table, r)
            by_target.setdefault(id(t), (t, []))[1].append(r)
        for t, part in by_target.values():
            await t.executemany(sql, [tuple(r[c] for c in cols) for r in part])
            ids = [r["id"] for r in part]
            present = int(await t.fetchval(f"SELECT count(*) FROM {table} WHERE id = ANY($1)", ids))
            if present != len(ids):
                raise RuntimeError(f"{table}: {len(ids) - present} rows did not arrive on their member; nothing deleted")
            await pool.execute(f"DELETE FROM {table} WHERE id = ANY($1)", ids)
            moved += len(ids)
        print(f"  {table}: {moved}/{total}", flush=True)
    await _advance_sequences(pool, table)
    return {"rows": total, "moved": moved}


async def _advance_sequences(pool: Any, table: str) -> None:
    """A table whose id comes from a sequence (llm_spend): rows arrived with their original ids, so each member's
    sequence must move past its highest id -- otherwise every NEW row collides with a moved one (the spend
    recorder logs and drops such a row: found in the rehearsal, 2026-10-01; the budget cap would not see it)."""
    from app.services import search_group

    for _mid, member in await search_group.member_pools(pool, strict=True):
        seq = await member.fetchval("SELECT pg_get_serial_sequence($1, 'id')", table)
        if seq:
            await member.execute(
                f"SELECT setval($1::regclass, GREATEST((SELECT COALESCE(max(id), 0) FROM {table}), "
                f"(SELECT last_value FROM {seq})), true)", seq)


async def main(argv: list[str]) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    from app.db.session import create_pool
    from app.services import search_group
    from app.services.shards import invalidate_shard_cache

    pool = await create_pool()
    try:
        invalidate_shard_cache()
        if not await search_group.grouped(pool):
            print("ERROR: no search member is registered on this control database", file=sys.stderr)
            return 2
        await search_group.member_pools(pool, strict=True)     # every member reachable before anything moves
        for table in TABLES:
            print(table, await move_table(pool, table, dry_run=args.dry_run), flush=True)
    finally:
        await pool.close()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main(sys.argv[1:])))
