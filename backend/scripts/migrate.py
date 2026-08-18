#!/usr/bin/env python3
"""
The single, documented entry point for applying this project's database
schema (ticket 17, memory-substrate map).

Replaces the previously-documented `for f in db/0*.sql` loop, which is a
real, live bug: that glob only matches filenames starting with the digit
'0', so it silently skips db/10_code_sourced_agents.sql -- the migration
that creates the tables app/services/code_review.py inserts into. This
script lists real files in real lexical order instead.

Adds a real ledger (schema_migrations: filename, checksum, applied_at,
kind) -- there was none before. Every migration file is idempotent DDL
(CREATE TABLE IF NOT EXISTS, ALTER TABLE ADD COLUMN IF NOT EXISTS, etc, a
convention already followed throughout db/*.sql), so running this against
an already-provisioned database is safe: files already applied via the old
loop will simply be re-run once (a no-op) and recorded in the ledger for
the first time. From that point forward, the ledger -- not "did I already
run this by hand" -- governs what runs.

All files in db/*.sql are treated uniformly (kind='schema' for every
file, including 09_seed_internal_agents.sql). Ticket 17's own answer
flagged seed data mixed into the DDL sequence as a real smell and
suggested a separate db/seeds/ folder -- deliberately not adopted here on
request: the distinction is organizational, not load-bearing (nothing
downstream depends on it), and the file works fine tracked exactly like
every other migration. Revisit later if it starts to matter.

Usage:
    python3 scripts/migrate.py                # apply all pending migrations
    python3 scripts/migrate.py --dry-run       # show what would run, apply nothing
    python3 scripts/migrate.py --status        # show ledger state, apply nothing
    python3 scripts/migrate.py --dsn <url>     # override $DATABASE_URL
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import os
import sys
from pathlib import Path

import asyncpg

DB_DIR = Path(__file__).resolve().parent.parent / "db"

LEDGER_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    filename    TEXT PRIMARY KEY,
    checksum    TEXT NOT NULL,
    applied_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    kind        TEXT NOT NULL DEFAULT 'schema' CHECK (kind IN ('schema', 'seed'))
);
"""
# 'kind' stays in the ledger even though every real file today is
# 'schema' -- cheap to keep, and a real db/seeds/ split remains a valid
# option later without a ledger migration of its own if it ever matters.


def _checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _real_files(directory: Path) -> list[Path]:
    """
    Real files, in real lexical order -- deliberately not the buggy
    `sorted(directory.glob("0*.sql"))` pattern this replaces. Lexical sort
    on the current, real, 2-digit zero-padded filenames (01..12, with 08a/
    08b as letter-suffixed variants of 08) sorts correctly as-is; a future
    migration numbered past 99 would need 3-digit padding to keep sorting
    correctly, same caveat any lexically-sorted numbering scheme has.
    """
    if not directory.exists():
        return []
    return sorted(p for p in directory.glob("*.sql") if p.is_file())


async def _ensure_ledger(conn: asyncpg.Connection) -> None:
    await conn.execute(LEDGER_DDL)


async def _ledger_checksum(conn: asyncpg.Connection, filename: str) -> str | None:
    row = await conn.fetchrow(
        "SELECT checksum FROM schema_migrations WHERE filename = $1", filename
    )
    return row["checksum"] if row else None


def _build_plan() -> list[tuple[Path, str]]:
    """Returns (path, kind) pairs in real apply order -- every file in
    db/*.sql, lexical, all tagged 'schema'. No seeds/ subfolder."""
    return [(f, "schema") for f in _real_files(DB_DIR)]


async def run(dsn: str, dry_run: bool = False, status_only: bool = False) -> int:
    conn = await asyncpg.connect(dsn)
    exit_code = 0
    try:
        await _ensure_ledger(conn)

        for path, kind in _build_plan():
            checksum = _checksum(path)
            ledger_checksum = await _ledger_checksum(conn, path.name)

            if ledger_checksum is not None:
                if ledger_checksum != checksum:
                    print(
                        f"MISMATCH  {path.name}: the file on disk does not match "
                        f"what the ledger recorded as applied -- this migration was "
                        f"edited after being applied. Not re-running it silently; "
                        f"resolve by hand (a new migration file, or a deliberate "
                        f"ledger correction)."
                    )
                    exit_code = 1
                elif status_only:
                    print(f"applied   {path.name}")
                continue

            if status_only:
                print(f"pending   {path.name}")
                continue

            if dry_run:
                print(f"would run {path.name}")
                continue

            print(f"applying  {path.name} ...")
            sql = path.read_text()
            async with conn.transaction():
                await conn.execute(sql)
                await conn.execute(
                    "INSERT INTO schema_migrations (filename, checksum, kind) "
                    "VALUES ($1, $2, $3)",
                    path.name, checksum, kind,
                )
            print(f"applied   {path.name}")

        return exit_code
    finally:
        await conn.close()


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--dsn", default=os.environ.get("DATABASE_URL"),
                     help="defaults to $DATABASE_URL")
    ap.add_argument("--dry-run", action="store_true", help="show what would run, apply nothing")
    ap.add_argument("--status", action="store_true", help="show ledger state, apply nothing")
    args = ap.parse_args()

    if not args.dsn:
        print("ERROR: no DATABASE_URL in environment and no --dsn given.")
        return 1
    if args.dry_run and args.status:
        print("ERROR: --dry-run and --status are mutually exclusive.")
        return 1

    return asyncio.run(run(args.dsn, dry_run=args.dry_run, status_only=args.status))


if __name__ == "__main__":
    sys.exit(main())
