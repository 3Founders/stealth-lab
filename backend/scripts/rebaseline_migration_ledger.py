"""
One-time, idempotent `schema_migrations` ledger correction.

Rewrites every `schema_migrations.checksum` to the LF-normalised sha256 of
the migration file's current content (`migrate.py._checksum`). Needed once
after `migrate.py._checksum` became line-ending-agnostic: rows applied
from a CRLF checkout stored a CRLF-byte hash and would otherwise read as
`MISMATCH` forever against an LF checkout (and vice-versa).

This is the "deliberate ledger correction" `migrate.py`'s own MISMATCH
message points at. It does NOT run any migration and does NOT touch schema
-- only the checksum column, only where it differs, only for filenames
that still exist in `backend/db/`.

    cd backend && DATABASE_URL=<dsn> python scripts/rebaseline_migration_ledger.py [--dry-run]

Safe to run repeatedly: a second run is a no-op (0 rewritten).
"""
from __future__ import annotations

import argparse
import asyncio
import os
import sys

import asyncpg

sys.path.insert(0, os.path.dirname(__file__))
from migrate import DB_DIR, _checksum, _real_files  # noqa: E402


async def run(dsn: str, dry_run: bool) -> int:
    conn = await asyncpg.connect(dsn)
    try:
        ledger = {
            r["filename"]: r["checksum"]
            for r in await conn.fetch("SELECT filename, checksum FROM schema_migrations")
        }
        files = _real_files(DB_DIR)
        rewritten = matched = 0
        for path in files:
            want = _checksum(path)
            have = ledger.get(path.name)
            if have is None:
                print(f"  skip (not in ledger): {path.name}")
                continue
            if have == want:
                matched += 1
                continue
            print(f"  {'would rewrite' if dry_run else 'rewrite'}: {path.name}  "
                  f"{have[:12]}... -> {want[:12]}...")
            if not dry_run:
                await conn.execute(
                    "UPDATE schema_migrations SET checksum = $1 WHERE filename = $2",
                    want, path.name,
                )
            rewritten += 1
        orphans = sorted(set(ledger) - {p.name for p in files})
        for o in orphans:
            print(f"  ledger row with no file (left untouched): {o}")
        print(f"\n{matched} already normalised, {rewritten} "
              f"{'to rewrite' if dry_run else 'rewritten'}, {len(orphans)} orphan rows.")
        return 0
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"))
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    if not args.dsn:
        print("no DSN (pass --dsn or set DATABASE_URL)", file=sys.stderr)
        return 2
    return asyncio.run(run(args.dsn, args.dry_run))


if __name__ == "__main__":
    raise SystemExit(main())
